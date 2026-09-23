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
        (t["tp4_gain"], t["tp4_sell"]),
        (t["tp5_gain"], t["tp5_sell"]),
        (t["tp6_gain"], t["tp6_sell"]),
        *((e["gain"], e["sell"]) for e in t.get("tp_extra") or []),
    ]
    # SE ACEPTA `v == 0`, igual que `build_tiers`. Este helper filtraba
    # `v > 0` y con el TP1 a 0% se dejaba fuera el primer escalon, asi que los
    # tests miraban una escalera distinta de la que corre. Un escalon que no
    # vende SI existe: dispara la subida del stop.
    return [(g, v) for g, v in pares if g > 0 and v >= 0]


def test_los_peldanos_son_los_pedidos() -> None:
    assert _peldanos() == [
        (0.30, 0.20),  # 23-sep: el primero pasa a +30% y VENDE un 20%
        (0.50, 0.199),
        (0.80, 0.182),  # 23-sep: pierde 12 puntos que suben a los lejanos
        (1.50, 0.101),  # x2,5
        (2.30, 0.085),  # x3,3
        (4.00, 0.069),  # x5
        (6.00, 0.056),  # x7
        (9.00, 0.040),  # x10
        (14.00, 0.024),  # x15
        (19.00, 0.024),  # x20
    ]


def test_las_ganancias_van_en_orden_creciente() -> None:
    """Un peldano por debajo del anterior no llegaria a dispararse nunca."""
    ganancias = [g for g, _ in _peldanos()]
    assert ganancias == sorted(ganancias)


def test_nunca_se_vende_mas_del_100() -> None:
    """Vender mas de lo que se tiene es una orden imposible."""
    assert sum(v for _, v in _peldanos()) <= 1.0


def test_queda_moonbag_corriendo() -> None:
    """Ese 2% es el que Manu vende a mano: el bot no lo toca."""
    vendido = sum(v for _, v in _peldanos())
    assert round(1 - vendido, 6) == 0.02


def test_el_stop_sube_a_la_entrada_tras_el_primer_tp() -> None:
    """Es lo que hace que despues del primer TP ya no se pueda perder."""
    assert _trade()["stop_a_entrada_tras_tp"] is True


def test_no_se_cierra_ninguna_posicion_por_reloj() -> None:
    """0 = sin reloj. Con 7200 los peldanos de x10 a x20 no se disparaban
    nunca: el reloj vendia antes de que la moneda llegara."""
    assert _trade()["max_hold_time"] == 0


def test_los_peldanos_lejanos_cobran_una_salida_de_verdad() -> None:
    """Los de arriba son los que cobran cuando una sale de verdad."""
    lejanos = [(g, v) for g, v in _peldanos() if g >= 1.5]
    assert len(lejanos) == 7
    assert round(sum(v for _, v in lejanos), 6) == 0.399


def test_la_escalera_termina_en_x20() -> None:
    """Lo pedido: el resto repartido en escalones HASTA x20.

    Antes subia hasta x100. La consecuencia de cortar ahi hay que tenerla
    escrita: una moneda que pase de x20 ya no tiene peldanos por encima, y
    lo unico que sigue dentro es el 2% del moonbag.
    """
    ganancias = [g for g, _ in _peldanos()]
    assert max(ganancias) == 19.00


def test_los_peldanos_altos_tienen_tiempo_de_llegar() -> None:
    """Si vuelve a ponerse un reloj corto, los peldanos de x10 a x20 dejan
    de tener sentido y este test avisa."""
    t = _trade()
    hay_altos = any(g >= 9.0 for g, _ in _peldanos())
    assert hay_altos
    assert t["max_hold_time"] == 0 or t["max_hold_time"] >= 43200


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
    """23-sep: 20% al +30%, 19,9% al +50% y 18,2% al +80%.

    El primer peldano deja de ser un disparador puro y pasa a cobrar. Los 20
    puntos que se lleva salen EN PROPORCION de todos los demas --elegido asi
    al preguntarlo-- y ademas el de +80% cede 12 puntos a los lejanos.
    """
    cerca = [(g, v) for g, v in _peldanos() if g < 1.5]
    assert cerca == [(0.30, 0.20), (0.50, 0.199), (0.80, 0.182)]
    assert round(sum(v for _, v in cerca), 6) == 0.581


def test_el_tercer_peldano_sigue_siendo_casi_la_mitad_de_lo_que_queda() -> None:
    """LA TRADUCCION QUE SE PUEDE EQUIVOCAR, y por eso esta fijada aqui.

    El tercer peldano nacio de "al +80% vende la mitad de lo que queda". El bot
    no sabe vender fracciones de lo que queda: TODOS sus peldanos son fraccion
    de la compra ORIGINAL. Entonces quedaba el 55% y la mitad era 27,5%. Poner
    0.50 ahi venderia el 50% de la compra, casi el doble de lo pedido -- ese es
    el error que este test existe para impedir.

    YA NO ES LA MITAD, y es deliberado. El 19-sep se repartieron diez puntos
    del primer peldano; el 23-sep el primero paso a cobrar un 20% y al tercero
    se le quitaron doce puntos mas para subirlos a los lejanos. A esa altura
    queda un 60,1% y el tercero vende el 18,2%, o sea el 30,3% de lo que
    queda.

    Lo que este test sigue impidiendo es el error de traduccion original:
    poner 0.50 ahi creyendo que es "la mitad de lo que queda" venderia el 50%
    de la compra entera, mas del triple de lo que toca.
    """
    cerca = _peldanos()[:3]
    queda_antes_del_tercero = 1 - cerca[0][1] - cerca[1][1]
    proporcion = cerca[2][1] / queda_antes_del_tercero
    assert 0.25 <= proporcion <= 0.35, (
        f"el tercer peldano vende el {proporcion * 100:.1f}% de lo que queda"
    )
