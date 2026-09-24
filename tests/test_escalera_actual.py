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
    assert _peldanos() == [(0.5513, 1.00)]


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
    """La noche del 24-sep la escalera se quedo en UN SOLO take profit.

    Hasta esa tarde habia cuatro escalones lejanos apuntando a 150k, 200k,
    300k y 400k de market cap. Se quitaron al pedirse "solo quiero un tp y un
    sl" para pagar menos comisiones de venta.

    ESTE TEST SUSTITUYE AL QUE COMPROBABA QUE CAIAN EN ESAS CIFRAS. No se
    borra sin mas: si alguien vuelve a anadir peldanos lejanos, lo que hay
    que recuperar es aquel test --que comprobaba el market cap al que caen
    calculandolo desde la curva-- y no solo meter numeros en el yaml. Queda
    en el historial de git, en el commit que los quito.
    """
    assert _trade().get("tp_extra") in (None, []), (
        "han vuelto los peldanos lejanos: hay que recuperar la comprobacion "
        "de que caen en las cifras de market cap pedidas"
    )
    for clave in ("tp2_gain", "tp3_gain", "tp4_gain", "tp5_gain", "tp6_gain"):
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


def test_ya_no_queda_moonbag() -> None:
    """El TP unico vende el 100%: no queda nada corriendo.

    Hasta la noche del 24-sep se dejaba un 3% que el bot no vendia nunca y
    que Manu vendia a mano. Con un solo take profit que se lleva la posicion
    entera, ese resto desaparece -- y es coherente con lo pedido: menos
    ventas, menos comisiones y nada pendiente de gestionar a mano.
    """
    peldanos = _peldanos()
    assert len(peldanos) == 1, f"un solo take profit: {peldanos}"
    assert round(sum(v for _, v in peldanos), 6) == 1.0, (
        "el TP unico tiene que vender el 100%, o quedaria un resto sin dueno "
        "que ni el bot vende ni nadie sabe que esta ahi"
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


def test_con_un_solo_tp_el_ganador_si_cubre_al_perdedor() -> None:
    """SE DIO LA VUELTA LA PROPIEDAD, y por eso este test cambia de nombre.

    Mientras hubo escalera, el aviso permanente era el contrario: un ganador
    que solo llegaba al primer peldano NO cubria a un perdedor ni de lejos
    (el TP1 cobraba un 6% de la posicion contra un stop del 26%), y hacian
    falta unos tres aciertos por fallo solo para empatar.

    CON UN SOLO TAKE PROFIT ESO DESAPARECE. No hay ganadores a medias: o la
    moneda llega al objetivo y deja la ganancia entera, o no llega y cuesta
    el stop entero. El ganador (+55,1%) cubre al perdedor (-26%) con holgura
    y bastan ~32 aciertos de cada 100 para no perder.

    LO QUE SE PAGA A CAMBIO, Y NO LO MIDE ESTE TEST: los finales intermedios
    dejan de existir. Una moneda que sube un +54% y se da la vuelta valia
    +5,6% con el peldano pequeno puesto y ahora vale -26%. Esta escrito en
    el yaml, en el comentario del take profit.
    """
    t = _trade()
    peldanos = _peldanos()
    assert len(peldanos) == 1, f"este test describe la configuracion de UN tp: {peldanos}"
    g, v = peldanos[0]
    assert v == 1.0, "el tp unico vende la posicion entera"
    assert g > t["stop_loss_percentage"], (
        f"el ganador deja +{g * 100:.1f}% y el perdedor cuesta "
        f"-{t['stop_loss_percentage'] * 100:.0f}%: si esto se invierte, la "
        f"configuracion no puede ganar dinero ni acertando la mitad"
    )


def test_el_tp_unico_cabe_dentro_de_la_curva() -> None:
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
    objetivo = _peldanos()[0][0]
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
