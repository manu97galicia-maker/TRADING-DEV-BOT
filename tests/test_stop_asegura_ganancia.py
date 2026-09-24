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
    assert _tiers()[0].sell == t["take_profit_sell_percentage"] == 0.15
    assert _tiers()[0].gain == t["take_profit_percentage"] == 0.25


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


def test_se_vende_el_97_por_ciento_entre_las_dos_escaleras() -> None:
    """AHORA SE VENDE TODO POR UNA SOLA ESCALERA, y eso simplifica la cuenta.

    Durante unas horas del 24-sep hubo dos: los peldanos de porcentaje
    vendian el 75% y los escalones de market cap el 22% -- y sumar solo la
    primera daba un moonbag del 25%, que no lo era. Desde la tarde los
    escalones de market cap estan vacios y los ocho peldanos venden el 97%
    entre todos, asi que `moonbag_fraction` ya dice la verdad y este test
    comprueba justamente eso.
    """
    tiers = _tiers()
    por_ciento = sum(t.sell for t in tiers)
    por_mc = sum(d["sell"] for d in _trade().get("mc_take_profit_tiers") or [])
    assert por_mc == 0, "si vuelven los escalones de market cap, hay que sumar los dos"
    assert abs(por_ciento - 0.97) < 1e-9
    # Con una sola escalera, lo que ve `moonbag_fraction` SI es el moonbag.
    assert abs(moonbag_fraction(tiers) - 0.03) < 1e-9


def test_la_escalera_de_porcentaje_son_tres() -> None:
    """Los TRES DE CERCA son +25%, +55% y +100%.

    Historial del dia: cuatro peldanos (+25/+55/+90/+125, vendiendo
    15-35-30-5) por la manana; por la tarde se pide "el take profit 2 vende
    un 40%, el siguiente lo ponemos en un 100% pero vende un 20%". Se va el
    cuarto y el tercero sube.

    DETRAS DE ESTOS TRES HAY CINCO MAS, los de `tp_extra`, que van en
    multiplos de x1,35. Aqui se miran solo los de cerca --los que caben por
    debajo de x2,5-- porque son los unicos que una moneda normal recorre;
    los otros cinco tienen su propio test en test_escalera_actual.py.

    EL GRUESO SE COBRA EN MEDIO: 15% - 40% - 20%. El segundo peldano es el
    que de verdad manda, y es el unico de los tres que cae holgadamente
    dentro de la curva (+55% contra un recorrido de +87,2% hasta migrar).

    EL TERCERO YA NO CABE EN LA CURVA. Al +100% la moneda tiene que haber
    migrado: entrando al 87,0% quedan +87,2% hasta migrar, asi que el tercer
    peldano esta 4,9 puntos por encima de ese punto. Esto esta puesto a
    sabiendas --queda la cuenta entera en el yaml-- pero conviene que un
    test lo diga, porque es lo que decide cuanto rinde el caso bueno mas
    frecuente: la moneda que llega a migrar y se muere ahi.
    """
    tiers = [t for t in _tiers() if t.gain <= 1.5]
    assert len(tiers) == 3, f"tres peldanos de cerca: {tiers}"
    assert [t.sell for t in tiers] == [0.15, 0.40, 0.20]
    assert [t.gain for t in tiers] == [0.25, 0.55, 1.00]
    # El recorrido dentro de la curva entrando donde se entra.
    umbral = yaml.safe_load(CONFIG.read_text())["filters"]["curva_96"]["umbral"]
    vt = lambda a: 1073e6 - 793.1e6 * a  # noqa: E731
    recorrido = (vt(umbral) / vt(1.0)) ** 2 - 1
    assert tiers[1].gain < recorrido, "el segundo peldano tiene que caber en la curva"
    assert tiers[2].gain > recorrido, (
        "si el tercero vuelve a caber en la curva, la cuenta del yaml esta vieja"
    )


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
