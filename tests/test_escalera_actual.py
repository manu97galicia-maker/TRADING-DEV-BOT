"""La escalera de ventas configurada, fijada para que no cambie por descuido.

Los numeros van a pelo a proposito: quien los toque ve el test en rojo y
tiene que mirar que ganancia gana o pierde antes de seguir.
"""

# ruff: noqa: S101, PLR2004

from itertools import pairwise
from pathlib import Path

import yaml
from modelo_tamano import SOL_USD_REF

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _peldanos() -> list[tuple[float, float]]:
    t = _trade()
    pares = [
        (t["take_profit_percentage"], t["take_profit_sell_percentage"]),
        # CON `.get` EN TODOS, no con corchetes. Cada vez que un peldano se
        # quita del yaml, leerlo con corchetes revienta con KeyError -- un
        # rojo que solo dice "falta una clave", no "la escalera ha cambiado".
        # Paso con tp4/tp5/tp6 el 24-sep por la manana y con tp2/tp3 esa
        # misma noche, al quedarse la configuracion en UN SOLO take profit.
        (t.get("tp2_gain"), t.get("tp2_sell")),
        (t.get("tp3_gain"), t.get("tp3_sell")),
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

    Tarde del 24-sep: OCHO peldanos, todos por porcentaje. Tres de cerca
    (+18%, +38%, +53%) y cinco lejanos en `tp_extra`, cada uno un 35% mas
    caro que el ANTERIOR --no que la entrada-- empezando donde acaba el
    tercero.

    LOS ESCALONES DE MARKET CAP SE FUERON esa misma tarde, tras unas horas
    puestos. Una cifra fija de market cap significa cosas distintas segun
    donde se entre: 100k es un x2,2 entrando al 86% de la curva y un x1,4
    entrando al 96%. Los multiplos no dependen de eso.
    """
    assert _peldanos() == [(0.30, 0.60), (0.60, 0.30)]


def _mc_de_compra() -> float:
    """El market cap al que se compra, con la curva del yaml y SOL a 220 $.

    La curva de pump.fun es un producto constante: con `avance` vendido, los
    tokens virtuales valen 1073e6 - 793,1e6 x avance, y el precio es
    k/vt^2 con k = 30 x 1073e6. El market cap es precio x supply x precio
    del SOL.

    EL PRECIO DEL SOL ES FIJO AQUI (220 $) y el bot usa el de verdad, que
    refresca desde DexScreener. Un test que saliera a internet daria
    distinto cada dia. Lo que se comprueba es que los peldanos APUNTAN a
    donde se penso, no el dolar exacto.
    """
    avance = yaml.safe_load(CONFIG.read_text())["filters"]["curva_96"]["umbral"]
    vt = 1073e6 - 793.1e6 * avance
    precio = (30 * 1073e6 / vt) / vt
    return precio * 1e9 * SOL_USD_REF


def test_no_quedan_peldanos_lejanos() -> None:
    """La escalera es corta: dos peldanos de cerca y nada mas.

    Historial del dia: siete peldanos con escalones de market cap hasta 400k
    por la tarde; UN SOLO take profit al +55,13% por la noche, para pagar
    menos comisiones; y ahora dos (+30% vendiendo 60%, +60% vendiendo 30%).

    NO HAY PELDANOS LEJANOS en ninguna de las dos ultimas. Si vuelven, lo que
    hay que recuperar es el test que comprobaba que caen en las cifras de
    market cap pedidas --calculandolas desde la curva-- y no solo meter
    numeros en el yaml. Queda en el historial de git.
    """
    assert _trade().get("tp_extra") in (None, []), (
        "han vuelto los peldanos lejanos: hay que recuperar la comprobacion "
        "de que caen en las cifras de market cap pedidas"
    )
    for clave in ("tp3_gain", "tp4_gain", "tp5_gain", "tp6_gain"):
        assert clave not in _trade(), (
            f"{clave} ha vuelto al yaml. Si es a proposito, hay que volver a "
            f"comprobar que cabe en la curva y que el suelo no lo alcanza"
        )


def test_el_suelo_nunca_alcanza_al_peldano_que_acaba_de_cobrarse() -> None:
    """EL FALLO DE AGOSTO, que ahora vigila su propio test.

    Si dos peldanos quedan a menos de un `margen_stop_tras_tp` uno de otro,
    el suelo que sube tras cobrar el primero cae ENCIMA del segundo y la
    posicion se cierra sola en el instante de tocar su objetivo. Paso con el
    margen al 12% y obligo a subir el TP2 de +40% a +50%.

    Estaba metido dentro del test de la cadena geometrica --que daba un 35%
    de separacion de regalo-- y al cambiar los lejanos a cifras de market cap
    esa garantia desaparecio. Por eso se saca aparte: es una propiedad que
    tiene que cumplirse siempre, venga la escalera de donde venga.
    """
    margen = float(_trade()["margen_stop_tras_tp"])
    peldanos = _peldanos()
    for (g_antes, _), (g_ahora, _) in pairwise(peldanos):
        suelo = (1 + g_antes) * (1 + margen)
        assert suelo < 1 + g_ahora, (
            f"tras cobrar el peldano de x{1 + g_ahora:.3f} el suelo queda en "
            f"x{suelo:.3f}, por encima: la posicion se cerraria sola"
        )


def test_no_quedan_escalones_de_market_cap() -> None:
    """Estuvieron puestos unas horas el 24-sep y los sustituyo `tp_extra`.

    La lista se deja VACIA en vez de borrar la clave, para que se vea que la
    via existe y esta apagada. Y hay un motivo para que no convivan: la
    escalera de market cap solo se arma cuando se han cobrado TODOS los
    peldanos de porcentaje, y con ocho arriba seria inalcanzable en la
    practica -- estaria puesta sin hacer nada, que es la peor de las dos
    cosas.
    """
    assert _trade()["mc_take_profit_tiers"] == []


def test_las_ganancias_van_en_orden_creciente() -> None:
    """Un peldano por debajo del anterior no llegaria a dispararse nunca."""
    ganancias = [g for g, _ in _peldanos()]
    assert ganancias == sorted(ganancias)


def test_nunca_se_vende_mas_del_100() -> None:
    """Vender mas de lo que se tiene es una orden imposible."""
    assert sum(v for _, v in _peldanos()) <= 1.0


def test_el_moonbag_es_lo_que_sobra_de_la_escalera() -> None:
    """VUELVE EL MOONBAG (24-sep, noche): "lo que sobre".

    Los dos peldanos venden el 90% (60 + 30) y el 10% restante se queda en la
    cartera. El bot NO lo vende nunca -- solo sale si salta el suelo.

    ESTE TEST SUSTITUYE A `test_ya_no_queda_moonbag`, que exigia lo
    contrario. Durante unas horas el take profit unico vendia el 100% y no
    sobraba nada; al volver a una escalera corta, sobra.

    LO QUE ARRASTRA, Y POR ESO IMPORTA QUE ESTE ESCRITO: mientras quede
    moonbag la cuenta de token sigue abierta con su renta dentro, y el bot
    sigue vigilando una posicion casi vacia. Es el precio de dejar algo
    corriendo, y es una decision, no un descuido.
    """
    peldanos = _peldanos()
    vendido = round(sum(v for _, v in peldanos), 6)
    assert vendido < 1.0, "si se vende el 100% no hay moonbag que comprobar"
    assert round(1 - vendido, 6) == 0.10, (
        f"el moonbag tiene que ser el 10% que sobra de 60 + 30, y es "
        f"{(1 - vendido) * 100:.1f}%"
    )

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


def test_el_primer_peldano_apaga_el_stop_antes_que_el_stop_muerda() -> None:
    """LO QUE VUELVE CON LA ESCALERA CORTA, y es su razon de ser.

    El primer peldano (+30%) no esta solo para cobrar: al tocarlo el suelo
    sube a compra + margen y la operacion deja de poder acabar en perdida.
    Con el take profit unico eso no existia y una moneda que subia un 45% y
    se daba la vuelta costaba el stop ENTERO.

    LO QUE SE COMPRUEBA: que el primer peldano quede por debajo del segundo
    (o no apagaria nada antes), y que vender en el no deje la operacion en
    rojo si la moneda se da la vuelta justo despues.
    """
    t = _trade()
    peldanos = _peldanos()
    assert len(peldanos) >= 2, f"esta escalera necesita al menos dos: {peldanos}"
    (g1, v1), (g2, _) = peldanos[0], peldanos[1]
    assert g1 < g2
    margen = float(t["margen_stop_tras_tp"])
    # Cobrado el primero, el resto sale en compra + margen si se cae.
    resultado = v1 * (1 + g1) + (1 - v1) * (1 + margen) - 1
    assert resultado > 0, (
        f"tocar el primer peldano y darse la vuelta deja {resultado * 100:+.1f}%: "
        f"el peldano no esta cumpliendo su trabajo de apagar el stop"
    )

def test_los_peldanos_caben_dentro_de_la_curva() -> None:
    """Lo unico que el take profit tiene que cumplir ahora.

    Si el objetivo cae por encima del recorrido que queda hasta migrar, no
    se cobra mientras la moneda esta en pump.fun: hace falta que migre Y siga
    subiendo. Con un solo peldano eso no es "cobrar menos", es no cobrar.

    Entrando al 90% quedan +64,7% y el objetivo esta en +55,1%: entra por
    9,6 puntos.
    """
    c = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]["curva_96"]
    vt = 1073e6 - 793.1e6 * c["umbral"]
    recorrido = (vt / (1073e6 - 793.1e6)) ** 2 - 1
    objetivo = max(g for g, _ in _peldanos())
    assert objetivo < recorrido, (
        f"el take profit (+{objetivo * 100:.1f}%) no cabe en lo que queda de "
        f"curva (+{recorrido * 100:.1f}%): solo cobraria despues de migrar"
    )
    margen = recorrido - objetivo
    assert margen > 0.05, (
        f"entra por solo {margen * 100:.1f} puntos; con tan poco margen "
        f"depende de que la moneda llegue practicamente a migrar"
    )

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
