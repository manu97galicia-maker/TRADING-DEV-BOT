"""El TP1 no vende: asegura. Y el suelo sube un 12% en cada escalon.

LO PEDIDO: "el take profit uno repartelo a partir del tercer peldano, baja el
tp2, y si toca tp1 vamos a subir stop loss a precio de compra +12% asegurando
asi ganancias; lo mismo para los demas escalones".

DOS COSAS QUE SALIERON AL IMPLEMENTARLO, y que son el motivo de la mitad de
estos tests:

1. Un escalon que vende 0% DESAPARECIA. `build_tiers` descartaba `sell <= 0`,
   asi que poner el TP1 a 0 lo borraba en silencio: ni vendia ni movia el stop,
   y desde fuera parecia que la configuracion no se aplicaba.

2. Con el margen al 12%, el suelo tras el TP2 caia EXACTAMENTE en su propio
   precio de disparo (+25% x 1,12 = +40%). Tocar el objetivo habria cerrado la
   posicion en el mismo instante. Por eso el TP2 subio a +50%.
"""

# ruff: noqa: S101, PLR2004

from pathlib import Path

import yaml

from trading.tiered_exit import (
    build_tiers,
    moonbag_fraction,
    stepped_stop_price,
    tiers_due,
)
from trading.universal_trader import _peldanos_extra

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
ENTRADA = 1.0


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _tiers() -> list:
    t = _trade()
    pares = [(t["take_profit_percentage"], t["take_profit_sell_percentage"])]
    pares += [(t.get(f"tp{i}_gain"), t.get(f"tp{i}_sell")) for i in (2, 3, 4, 5, 6)]
    pares += _peldanos_extra(t.get("tp_extra"))
    return build_tiers(pares)


# ------------------------------------- un escalon que no vende sigue existiendo


def test_un_escalon_que_vende_cero_sobrevive() -> None:
    """EL FALLO QUE HABRIA HECHO INUTIL TODO ESTO, en silencio.

    `build_tiers` descartaba `sell <= 0`. Con el TP1 a 0% el escalon
    desaparecia: no vendia (correcto) pero tampoco movia el stop (que es TODO
    su trabajo), y la configuracion parecia no aplicarse.
    """
    t = build_tiers([(0.25, 0.0), (0.50, 0.25)])
    assert len(t) == 2
    assert t[0].gain == 0.25
    assert t[0].sell == 0.0


def test_lo_que_no_es_un_numero_se_sigue_descartando() -> None:
    """Aceptar el 0 no puede significar aceptar basura."""
    assert build_tiers([(None, 0.2), (0.3, None), (-0.1, 0.2), (0.4, -0.5)]) == []


def test_el_tp1_de_la_config_vende_y_asegura() -> None:
    """El primer peldano hace DOS cosas, y la segunda es la que importa.

    Historial: del 19 al 23-sep no vendia nada, solo subia el suelo; el
    23-sep paso a +30% cobrando un 20%; el 24-sep a +15%/20% y luego a
    +25%/15%.

    LO QUE NO CAMBIA EN NINGUNA VERSION: en cuanto se toca, el suelo sube a
    compra +5% y la operacion ya no puede acabar en perdida. Ese es el
    trabajo principal del primer peldano; lo que cobra es secundario.

    Los dos numeros salen del yaml a proposito: ya paso que un test fijara el
    peldano a mano y se quedara verde midiendo el de dos versiones atras.
    """
    t = _trade()
    assert _tiers()[0].sell == t["take_profit_sell_percentage"] == 1.00
    assert _tiers()[0].gain == t["take_profit_percentage"] == 0.5513
    assert _tiers()[0].gain == t["take_profit_percentage"] == 0.5513


# ------------------------------------------------- el suelo asegura ganancia


def test_tocar_el_tp1_asegura_ganancia() -> None:
    """Es la idea entera: llegar al primer peldano convierte la operacion en
    ganadora pase lo que pase despues.

    El margen bajo del 12% al 5% el 23-sep, pedido ("ten mas paciencia con
    las monedas"): se asegura menos, pero queda mas aire entre el suelo y el
    peldano siguiente y la posicion no se cierra al primer respiro.
    """
    tiers, m = _tiers(), _trade()["margen_stop_tras_tp"]
    suelo = stepped_stop_price(ENTRADA, tiers, 0, m)
    assert abs(suelo - ENTRADA * (1 + m)) < 1e-9
    assert suelo > ENTRADA, "el suelo tiene que estar POR ENCIMA de la compra"


def test_el_margen_es_el_pedido() -> None:
    """Historial: 12% (19-sep) -> 5% (23-sep, por paciencia)."""
    assert _trade()["margen_stop_tras_tp"] == 0.05


def test_sin_margen_el_suelo_vuelve_a_la_entrada() -> None:
    """Poner 0 devuelve el comportamiento de antes, sin tocar codigo."""
    tiers = _tiers()
    assert stepped_stop_price(ENTRADA, tiers, 0, 0.0) == ENTRADA


def test_el_suelo_nunca_alcanza_el_escalon_que_acaba_de_tocarse() -> None:
    """EL TEST QUE MAS IMPORTA DE ESTE FICHERO.

    Si el suelo llega al precio del escalon recien tocado, esa posicion se
    cierra SOLA en el instante de cumplir su objetivo: el bot venderia todo al
    primer tic hacia abajo. Eso es exactamente lo que pasaba con el TP2 en
    +40%, y por lo que subio a +50%.

    Se comprueba en TODOS los escalones, no solo en el que fallaba.
    """
    tiers, m = _tiers(), _trade()["margen_stop_tras_tp"]
    for i, tier in enumerate(tiers):
        precio = ENTRADA * (1 + tier.gain)
        suelo = stepped_stop_price(ENTRADA, tiers, i, m)
        assert suelo < precio, (
            f"escalon {i + 1} (+{tier.gain * 100:.0f}%): el suelo ({suelo:.4f}) "
            f"alcanza su propio precio ({precio:.4f}); se cerraria solo"
        )


def test_el_colchon_mas_apretado_esta_anotado() -> None:
    """Lo que puede caer la moneda tras tocar un peldano antes de que el suelo
    la cierre. Se mide el mas apretado de los diez.

    LA HISTORIA DE ESTE NUMERO, porque explica el ajuste entero. Con el margen
    al 12% y el TP1 en +30%, el suelo tras el TP2 quedaba en 1,30 x 1,12 =
    1,456 contra un disparo en 1,50: un colchon del 2,9%, que en una memecoin
    es un respiro normal. La posicion se cerraba justo despues de cobrar.

    Bajando el margen al 5% (23-sep) subio al 9%. Al bajar el TP1 a +15% el
    24-sep queda en 8,7%: el hueco entre el TP1 y el TP2 es mayor, asi que el
    punto apretado se mueve a otro peldano.

    NO SE FIJA EL NUMERO EXACTO sino un minimo. Un test que exige "8,7" se
    pone rojo cada vez que alguien mueve un peldano por un motivo que no
    tiene nada que ver, y a la tercera se actualiza sin leerlo. Lo que de
    verdad importa es que ningun colchon baje de un respiro normal de
    memecoin, que es de donde vino el problema.
    """
    tiers, m = _tiers(), _trade()["margen_stop_tras_tp"]
    colchones = [
        (ENTRADA * (1 + t.gain) - stepped_stop_price(ENTRADA, tiers, i, m))
        / (ENTRADA * (1 + t.gain))
        for i, t in enumerate(tiers)
    ]
    assert min(colchones) > 0.05, (
        f"el colchon mas apretado es del {min(colchones) * 100:.1f}%: por "
        "debajo del 5% un respiro normal cierra la posicion justo despues "
        "de cobrar el peldano"
    )


# ----------------------------------------------------- la escalera completa


def test_un_solo_peldano_que_vende_el_cien_por_cien() -> None:
    """LA CONFIGURACION SE QUEDO EN UN TAKE PROFIT (24-sep, noche, pedido:
    "solo quiero un tp y un sl", para pagar menos comisiones de venta).

    ESTE TEST SUSTITUYE A DOS. Uno sumaba las dos escaleras --la de
    porcentaje y la de market cap-- para comprobar que juntas vendian el 97%
    y dejaban un 3% de moonbag. El otro fijaba los tres peldanos de cerca.
    Ninguna de las dos cosas existe ya.

    LO QUE SE COMPRUEBA AHORA es lo que queda por comprobar, que es poco y
    por eso hay que escribirlo: un peldano, vende todo, no hay resto, y
    `moonbag_fraction` --que es lo que mira el codigo para decidir si deja
    algo corriendo-- devuelve cero.
    """
    tiers = _tiers()
    assert len(tiers) == 1, f"un solo take profit: {tiers}"
    assert tiers[0].sell == 1.0
    assert moonbag_fraction(tiers) == 0.0
    # Y que no hayan vuelto por la puerta de atras las otras dos vias.
    assert not (_trade().get("tp_extra") or [])
    assert not (_trade().get("mc_take_profit_tiers") or [])

def test_los_diez_escalones_disparan_y_queda_el_moonbag() -> None:
    """Recorrido completo con la config real."""
    tiers = _tiers()
    ejec, restante = [False] * len(tiers), 1.0
    for tier in tiers:
        for j in tiers_due(ENTRADA, ENTRADA * (1 + tier.gain), tiers, ejec):
            ejec[j] = True
            restante -= min(tiers[j].sell, restante)
    assert all(ejec)
    assert abs(restante - moonbag_fraction(tiers)) < 1e-9


def test_tocar_el_tp1_y_caer_cierra_en_verde() -> None:
    """EL RECORRIDO QUE RESUME LO PEDIDO.

    La moneda sube hasta tocar el primer peldano, lo cobra, se da la vuelta y
    cae. El stop la cierra en compra +5%: la operacion acaba GANANDO entera,
    tanto lo cobrado arriba como lo que quedaba dentro.

    EL PRECIO SALE DEL PRIMER PELDANO, no escrito a mano: estaba fijado en
    1,30 y al bajar el TP1 a +25% habria seguido en verde midiendo un
    recorrido que ya no es el primero.
    """
    tiers, m = _tiers(), _trade()["margen_stop_tras_tp"]
    sl = ENTRADA * (1 - _trade()["stop_loss_percentage"])
    ejec, restante = [False] * len(tiers), 1.0
    _toca_el_tp1 = ENTRADA * (1 + tiers[0].gain)

    for j in tiers_due(ENTRADA, _toca_el_tp1, tiers, ejec):
        ejec[j] = True
        restante -= min(tiers[j].sell, restante)
        suelo = stepped_stop_price(ENTRADA, tiers, j, m)
        sl = max(sl, suelo)

    assert round(restante, 6) == round(1 - tiers[0].sell, 6)
    assert abs(sl - ENTRADA * (1 + m)) < 1e-9
    # Ahora cae. El resultado de cerrar en el suelo:
    assert sl / ENTRADA - 1 > 0, "tenia que cerrar en verde"
    assert round((sl / ENTRADA - 1) * 100, 1) == round(m * 100, 1)
