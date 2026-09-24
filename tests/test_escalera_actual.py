"""La escalera de ventas configurada, fijada para que no cambie por descuido.

Los numeros van a pelo a proposito: quien los toque ve el test en rojo y
tiene que mirar que ganancia gana o pierde antes de seguir.
"""

# ruff: noqa: S101, PLR2004

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _peldanos() -> list[tuple[float, float]]:
    t = _trade()
    pares = [
        (t["take_profit_percentage"], t["take_profit_sell_percentage"]),
        (t["tp2_gain"], t["tp2_sell"]),
        (t["tp3_gain"], t["tp3_sell"]),
        # CON `.get`, no con corchetes: desde el 24-sep los peldanos lejanos
        # van por market cap y estas claves ya NO estan en el yaml. Con
        # corchetes esto reventaba con KeyError, que es un rojo que no dice
        # que ha cambiado sino solo que falta una clave.
        (t.get("tp4_gain"), t.get("tp4_sell")),
        (t.get("tp5_gain"), t.get("tp5_sell")),
        (t.get("tp6_gain"), t.get("tp6_sell")),
        *((e["gain"], e["sell"]) for e in t.get("tp_extra") or []),
    ]
    # SE ACEPTA `v == 0`, igual que `build_tiers`. Este helper filtraba
    # `v > 0` y con el TP1 a 0% se dejaba fuera el primer escalon, asi que los
    # tests miraban una escalera distinta de la que corre. Un escalon que no
    # vende SI existe: dispara la subida del stop.
    return [(g, v) for g, v in pares if g is not None and v is not None and g > 0 and v >= 0]


def test_los_peldanos_son_los_pedidos() -> None:
    """LA ESCALERA ENTERA, fijada para que cualquier cambio sea deliberado.

    Desde el 24-sep son TRES por porcentaje y el resto por MARKET CAP
    (`mc_take_profit_tiers`). Los peldanos lejanos por porcentaje --x2,5 a
    x20-- se quitaron enteros: un x10 sobre la entrada significa cosas
    distintas segun donde se entre, mientras que 100k es 100k siempre.
    """
    assert _peldanos() == [(0.25, 0.15), (0.55, 0.35), (0.90, 0.30), (1.25, 0.05)]


def test_los_escalones_de_market_cap_son_los_pedidos() -> None:
    """De 100k a 500k, repartiendo el 19% que queda tras los tres peldanos.

    VAN EN PROGRESION GEOMETRICA y no de 100 en 100 mil: de 100k a 150k hay
    un 50% de subida y de 400k a 500k solo un 25%. A partes iguales, casi
    todos los escalones caerian en el tramo mas facil y la moneda que de
    verdad vuela se vendera entera demasiado pronto.
    """
    mc = _trade()["mc_take_profit_tiers"]
    assert [(d["target_mc"], d["sell"]) for d in mc] == [
        (100_000, 0.05), (140_000, 0.04), (200_000, 0.03),
    ]
    # Ascendentes, o el bot los cobraria en desorden.
    objetivos = [d["target_mc"] for d in mc]
    assert objetivos == sorted(objetivos)
    # Y de mas a menos: llegar a 500k es mucho menos probable que a 100k.
    ventas = [d["sell"] for d in mc]
    assert ventas == sorted(ventas, reverse=True)


def test_las_ganancias_van_en_orden_creciente() -> None:
    """Un peldano por debajo del anterior no llegaria a dispararse nunca."""
    ganancias = [g for g, _ in _peldanos()]
    assert ganancias == sorted(ganancias)


def test_nunca_se_vende_mas_del_100() -> None:
    """Vender mas de lo que se tiene es una orden imposible."""
    assert sum(v for _, v in _peldanos()) <= 1.0


def test_queda_moonbag_corriendo() -> None:
    """Ese 3% es el que Manu vende a mano: el bot no lo toca.

    HAY QUE SUMAR LAS DOS ESCALERAS, y es el error facil desde el 24-sep. Los
    peldanos de porcentaje venden el 78% y los escalones de market cap el
    19%. Mirando solo los primeros saldria un moonbag del 22%, que no es el
    moonbag: es lo que aun no se ha repartido.
    """
    t = _trade()
    por_ciento = sum(v for _, v in _peldanos())
    por_mc = sum(d["sell"] for d in t.get("mc_take_profit_tiers") or [])
    assert round(1 - por_ciento - por_mc, 6) == 0.03


def test_el_stop_sube_a_la_entrada_tras_el_primer_tp() -> None:
    """Es lo que hace que despues del primer TP ya no se pueda perder."""
    assert _trade()["stop_a_entrada_tras_tp"] is True


def test_no_se_cierra_ninguna_posicion_por_reloj() -> None:
    """0 = sin reloj. Con 7200 los peldanos de x10 a x20 no se disparaban
    nunca: el reloj vendia antes de que la moneda llegara."""
    assert _trade()["max_hold_time"] == 0


def test_el_abandono_no_depende_del_reloj_de_posicion() -> None:
    """La trampa que casi cuela: `abandon_after` salia de
    `(max_hold_time or 120) * 3 + 120`, asi que poner el reloj a 0 para
    dejar correr las posiciones hacia que el bot ABANDONASE cada una a los
    8 minutos, dejando la bolsa huerfana en la wallet.
    """
    t = _trade()
    assert t["abandonar_tras_seg"] >= 3600
    # Y con la formula vieja habria dado esto:
    assert (t["max_hold_time"] or 120) * 3 + 120 < t["abandonar_tras_seg"]


def test_el_ganador_minimo_no_llega_a_cubrir_al_perdedor() -> None:
    """AVISO PERMANENTE: un ganador que solo llega al primer peldano no cubre
    a un perdedor, ni de lejos.

    LOS NUMEROS DE HOY (23-sep). El TP1 cobra el 20% de un +30%, o sea +6% de
    la posicion. Despues el suelo sube a compra +5%, asi que si la moneda se
    da la vuelta ahi mismo el resto sale en +5%: la operacion entera deja
    alrededor de +10%. Un perdedor cuesta -29,4% (el stop del 30%, menos el
    2% de moonbag que no se vende).

    O SEA QUE HACEN FALTA UNOS TRES ACIERTOS POR CADA FALLO solo para
    empatar, contando que los aciertos se queden en el primer peldano.

    Esto NO es un fallo: el reparto esta echado adelante a proposito y la
    ganancia de verdad esta en los peldanos de +50% para arriba, donde una
    sola moneda paga varias perdidas. El test esta para que nadie lo de por
    supuesto sin verlo escrito, y para que salte el dia que alguien ponga un
    primer peldano tan gordo que parezca que la estrategia se sostiene sola.

    (La cuenta completa --cuanto deja cada final y cuantos aciertos pide--
    esta en el comentario de `stop_loss_percentage` del yaml.)
    """
    t = _trade()
    g, v = _peldanos()[0]
    assert g * v < t["stop_loss_percentage"]


def test_los_peldanos_de_cerca_son_los_pedidos() -> None:
    """24-sep: 15% al +25%, 35% al +55%, 30% al +90% y 5% al +125%.

    Los tres se cobran mientras la moneda todavia se mide en porcentaje sobre
    la entrada. De ahi en adelante manda el market cap.

    TODOS SON FRACCION DE LA COMPRA ORIGINAL, no de lo que queda dentro. Es
    la confusion que ya estuvo a punto de costar dinero con el peldano de "la
    mitad de lo que queda": 0,30 aqui es el 30% de la compra, no el 30% del
    resto.
    """
    cerca = [(g, v) for g, v in _peldanos() if g < 1.5]
    assert cerca == [(0.25, 0.15), (0.55, 0.35), (0.90, 0.30), (1.25, 0.05)]
    assert round(sum(v for _, v in cerca), 6) == 0.85


def test_todos_los_peldanos_son_fraccion_de_la_compra_original() -> None:
    """LA CONFUSION QUE YA ESTUVO A PUNTO DE COSTAR DINERO.

    El bot NO sabe vender fracciones de lo que queda dentro: todos sus
    peldanos son fraccion de la COMPRA ORIGINAL. Cuando el 24-sep se pidio
    "al +85% vende el 60% de lo que queda", eso se tradujo a 0,33 -- poner
    0,60 habria vendido el 60% de la compra entera, casi el doble.

    Ahora los peldanos se piden ya como fraccion del original, asi que no hay
    traduccion que hacer. Lo que este test impide es que vuelva a colarse una:
    si la suma de las ventas pasara de 1, alguien habria escrito fracciones
    del resto creyendo que eran del original.
    """
    suma = sum(v for _, v in _peldanos())
    assert suma <= 1.0, (
        f"los peldanos suman {suma * 100:.0f}% de la compra: alguien ha "
        "escrito fracciones de lo que QUEDA como si fueran del original"
    )
    # Y con los escalones de market cap tampoco puede pasar de 1.
    t = _trade()
    suma += sum(d["sell"] for d in t.get("mc_take_profit_tiers") or [])
    assert suma <= 1.0
