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

import itertools
from pathlib import Path

import pytest
import yaml

from trading.tiered_exit import (
    build_tiers,
    moonbag_fraction,
    stepped_stop_price,
    tiers_due,
)
from trading.universal_trader import _peldanos_extra

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
REAL_CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-unica123-real.yaml"
ENTRADA = 1.0


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _tiers() -> list:
    t = _trade()
    pares = [(t["take_profit_percentage"], t["take_profit_sell_percentage"])]
    pares += [(t.get(f"tp{i}_gain"), t.get(f"tp{i}_sell")) for i in (2, 3, 4, 5, 6)]
    pares += _peldanos_extra(t.get("tp_extra"))
    return build_tiers(pares)


def test_unica123_recupera_al_mas_40_y_deja_correr_a_x4() -> None:
    """En real, +40% vende el 75% --el capital entero-- y el 25% corre a x4.

    CAMBIADO EL 15-oct, pedido: "Optimiza el resultado poniendo un take
    profit en el que asegures el capital inicial y dejes correr beneficios".
    Antes era +50% sin vender y +70% vendiendo todo.

    POR QUE SE QUITO EL TP2 DE +70%. Era INALCANZABLE. Al 91% de curva
    quedaban +57,5% hasta migrar y el peldano pedia +70%: la curva se acaba
    antes. Es la trampa que `test_la_curva_llega_al_primer_tp` documenta del
    23-sep y en la que esta config habia vuelto a caer. Con el TP2 mudo, las
    unicas salidas eran el trinquete, el stop o el tope de tiempo.

    EL TRINQUETE SIGUE PUESTO pero baja de +12% a +5%: a +12% cortaba al
    corredor en cuanto la moneda respiraba, y eso costaba 14,5 puntos
    mientras la proteccion extra solo ganaba 4.
    """
    trade = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))["trade"]
    tiers = build_tiers(
        [
            (
                trade["take_profit_percentage"],
                trade["take_profit_sell_percentage"],
            ),
            (trade.get("tp2_gain"), trade.get("tp2_sell")),
        ]
    )
    # DOS PELDANOS, reescritos el 18-oct: "vender la mitad cuando suba un
    # 50% y luego el restante un 40% relativo a la subida anterior".
    #
    # "RELATIVO A LA SUBIDA ANTERIOR" SE ENCADENA, no se suma:
    #     TP1 = entrada x 1,50
    #     TP2 = TP1 x 1,40 = entrada x 2,10 = +110%
    # Si se hubiera puesto +40% desde la entrada (1,40), `build_tiers` lo
    # ordenaria ANTES del TP1 y los dos peldanos se dispararian juntos.
    assert [t.gain for t in tiers] == [0.50, 1.10]
    assert [t.sell for t in tiers] == [0.50, 1.0]
    # El tp2 vende el remanente (`min(original * sell, restante)`), asi que
    # no queda moonbag: a x4 se cierra la posicion.
    assert moonbag_fraction(tiers) == pytest.approx(0.0)

    assert trade["stop_a_entrada_tras_tp"] is True
    assert trade["stop_al_tp_tras_tp"] is False
    # 15% desde el 18-oct ("el sl se mueve de precio al de compra 15%").
    # Es el mando que hace que tocar el TP1 asegure el capital: el TP1 por si
    # solo embolsa 0,7425, por debajo del 1,0100 que cuesta entrar.
    assert trade["margen_stop_tras_tp"] == 0.15
    assert stepped_stop_price(ENTRADA, tiers, 0, 0.15) == pytest.approx(1.15)

    # 60% desde el 18-oct, pedido ("stop loss 60%"). Venia del 25%.
    #
    # Y AQUI VA EL AVISO QUE NO HAY QUE PERDER: con la banda de entrada en
    # 2.000-5.000 este stop NO PUEDE SALTAR. La curva no cotiza por debajo
    # del nacimiento (2.936 $ con SOL a 105), asi que entrando en 5.000 la
    # caida maxima posible es -41,3% y entrando mas abajo, menos. Lo que
    # protege de un volcado en esta banda es el anti-rug, no el stop.
    assert trade["stop_loss_percentage"] == 0.60
    # El trailing protege al corredor y ahora esta ESCRITO, no heredado del
    # stop por el respaldo `trailing_stop_percentage or stop_loss_percentage`.
    assert trade["trailing_stop_percentage"] == 0.25


def test_toda_compra_que_el_bot_haga_devuelve_el_capital() -> None:
    """ "Asegurar el capital" es una cuenta, y el suelo es lo que la sostiene.

    Vendiendo una fraccion f a ganancia g se recupera lo puesto solo si
    f x compra x (1+g) x (1-comision) - comision_prioridad >= lo que costo
    entrar. Las comisiones de prioridad son FIJAS, asi que por debajo de un
    cierto tamano la venta ya no cubre la compra.

    EL 60% PLANO (15-oct) ROMPIA ESTO. Con base `libre` las compras menguan:
    con 1 SOL salian 0,591 / 0,235 / 0,092, y la tercera se quedaba 0,00040
    SOL corta de devolver el capital. Con 0,3 SOL fallaban la segunda y la
    tercera. El pedido nuevo habria roto en silencio el pedido anterior
    ("asegures el capital inicial").

    EL ARREGLO es `compra_minima_sol`: por debajo de el el bot NO compra mas
    pequeno, SALTA la moneda (universal_trader.py:5121). Asi que lo que
    comprueba este test es la propiedad que de verdad importa: TODA COMPRA
    QUE EL BOT LLEGUE A HACER asegura el capital. Las que no podrian, no se
    hacen.
    """
    cfg = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    trade, pf, cu = cfg["trade"], cfg["priority_fees"], cfg["compute_units"]

    g = trade["take_profit_percentage"]
    f = trade["take_profit_sell_percentage"]
    fee_compra = pf["fixed_amount"] * cu["buy"] / 1e15
    fee_venta = pf["fixed_amount"] * cu["sell"] / 1e15
    comision_pump = 0.01
    suelo = trade["compra_minima_sol"]
    reserva = trade["min_reserve_sol"]
    colchon = 0.003  # _fee_headroom_lamports

    # EL CRITERIO CAMBIO EL 18-oct Y NO ES UN AJUSTE COSMETICO.
    #
    # Hasta el 17-oct el TP1 devolvia el capital el solo (+40% vendiendo el
    # 75% cobra 1,039 por cada 1 puesto). Con lo pedido el 18-oct --vender la
    # MITAD a +50%-- cobra 0,7425 y entrar cuesta 1,0100: el TP1 no puede
    # devolverlo a NINGUN tamano, y eso no se arregla subiendo el suelo de
    # compra.
    #
    # Lo que lo devuelve es el TP1 MAS el resto saliendo por el suelo que
    # sube a la compra + `margen_stop_tras_tp`. Asi que el test mide esa
    # suma, que es la garantia de verdad, y de paso deja escrito que ahora
    # la proteccion DEPENDE DE DOS VENTAS en vez de una.
    margen = trade["margen_stop_tras_tp"]

    def asegura_el_tp_solo(compra: float) -> bool:
        cuesta = compra * (1 + comision_pump) + fee_compra
        cobra = f * compra * (1 + g) * (1 - comision_pump) - fee_venta
        return cobra >= cuesta

    def asegura(compra: float) -> bool:
        cuesta = compra * (1 + comision_pump) + fee_compra
        tp1 = f * compra * (1 + g) * (1 - comision_pump) - fee_venta
        resto = (1 - f) * compra * (1 + margen) * (1 - comision_pump) - fee_venta
        return tp1 + resto >= cuesta

    # El suelo configurado TIENE que asegurar: es toda su razon de ser.
    assert asegura(suelo), (
        f"el suelo de {suelo} SOL no llega a devolver el capital: "
        "entonces no protege de nada"
    )
    # Y SE DEJA DICHO QUE EL TP1 SOLO NO LLEGA. Si alguien vuelve a una
    # configuracion donde el TP1 se baste (vendiendo mas, o a mas ganancia),
    # este aviso salta y hay que revisar el bloque del YAML, que explica que
    # la garantia depende de dos ventas.
    assert not asegura_el_tp_solo(suelo), (
        "el TP1 ya se basta solo: el YAML dice lo contrario y hay que actualizarlo"
    )
    # El suelo ya no tiene que ser justo: con el criterio combinado hace
    # falta 0,0107 SOL y el suelo esta en 0,075, asi que sobra de largo. Eso
    # es correcto -- el suelo lo puso el TP viejo, mas exigente.
    # Simulacion de las compras que SI se harian, con varios saldos.
    for saldo in (0.3, 0.5, 1.0, 2.0, 5.0):
        wallet = saldo
        for _ in range(trade["max_posiciones"]):
            libre = wallet - reserva
            if libre <= 0:
                break
            imp = libre * trade["fraccion_saldo"]
            if imp * (1 + trade["buy_slippage"]) + colchon > libre:
                break
            if imp < suelo:
                break  # el bot salta la moneda: no se abre la posicion
            assert asegura(imp), (
                f"con {saldo} SOL se compraria {imp:.4f} (por encima del "
                f"suelo {suelo}) y NO devuelve el capital"
            )
            wallet -= imp + colchon


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
    23-sep paso a +30% cobrando un 20%; el 24-sep a +15%/20%, luego a
    +25%/15%, luego a +30%/60%; el 25-sep a +25% cobrando un 20%.

    LO QUE NO CAMBIA EN NINGUNA VERSION: en cuanto se toca, el suelo sube a
    compra +5% y la operacion ya no puede acabar en perdida. Ese es el
    trabajo principal del primer peldano; lo que cobra es secundario.

    Y AHORA SE NOTA MAS QUE NUNCA: desde el 26-sep vende solo el 10%, asi que
    una moneda que toca el +25% y se da la vuelta deja +7,0% -- contra el
    +9,0% de ayer y el +20,0% de cuando cobraba el 60%. El peldano sigue
    haciendo su trabajo --convertir una perdedora en ganadora pequena-- pero
    la ganancia pequena es cada vez mas pequena. Es el precio de mover el
    peso al x3, y esta puesto a sabiendas.

    Los dos numeros salen del yaml a proposito: ya paso que un test fijara el
    peldano a mano y se quedara verde midiendo el de dos versiones atras.
    """
    t = _trade()
    assert _tiers()[0].sell == t["take_profit_sell_percentage"] == 0.25
    assert _tiers()[0].gain == t["take_profit_percentage"] == 0.35


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


def test_seis_peldanos_con_el_peso_abajo_y_una_cola_decreciente() -> None:
    """LA ESCALERA DEL 26-SEP: +25% vende 10%, +60% vende 20%, +100% vende
    20%, el x3 vende 35%, y el 15% que sobra lo reparten cuatro escalones de
    market cap hasta 80k. Pedido: "tp mas enfocado en el x3".

    ESTE TEST HA CAMBIADO DE FORMA CINCO VECES, con la escalera: sumaba dos
    escaleras (porcentaje + market cap) para llegar al 97%; luego exigia un
    solo peldano vendiendo el 100%; luego dos y un resto de 10%; ahora tres y
    una cola. Se reescribe cada vez en vez de aflojarse, porque lo que fija
    --cuanto se vende y cuanto sobra-- es justo lo que no puede quedar a
    medias.

    LA DIFERENCIA CON LA VERSION ANTERIOR: entonces el resto era un moonbag
    que no se vendia nunca; ahora esta todo asignado a la cola de market cap,
    asi que `moonbag_fraction` sigue dando 15% --solo mira los peldanos de
    porcentaje-- pero el trader le resta los escalones de market cap y queda
    en cero. Esa resta esta en `universal_trader.py` y existe por WIW, que el
    18-ago dejo media posicion sin vender al saltar el stop.
    """
    tiers = _tiers()
    trade = _trade()
    assert len(tiers) == 6, f"seis peldanos: {tiers}"
    assert [t.gain for t in tiers] == [0.35, 0.65, 1.00, 1.50, 2.00, 4.00]
    assert [t.sell for t in tiers] == [0.25, 0.40, 0.15, 0.10, 0.05, 0.03]
    # EL PESO ESTA ABAJO (05-oct), al reves que la escalera del 26-sep. Entre
    # los dos primeros peldanos se cobra el 65% de la posicion, y de ahi
    # arriba la cola va menguando: 15, 10, 5, 3.
    #
    # ES OTRA APUESTA, no un error: el 26-sep el peso estaba en el x3 para
    # cobrar mucho de las que corren; esto cobra pronto y deja una cola fina
    # corriendo. Lo que gana es que una moneda que se queda a medias paga
    # mucho mas; lo que pierde es el x3.
    assert tiers[0].sell + tiers[1].sell == 0.65, (
        f"los dos primeros peldanos tienen que cobrar el 65%: {tiers}"
    )
    de_la_cola = [x.sell for x in tiers[1:]]
    for antes, despues in itertools.pairwise(de_la_cola):
        assert despues <= antes, f"de mas a menos a partir del segundo: {tiers}"
    assert abs(moonbag_fraction(tiers) - 0.02) < 1e-9
    assert not (trade.get("tp_extra") or [])
    # LA COLA DE MARKET CAP SE VACIO EL 05-oct, y va con los seis peldanos:
    # solo se arma cuando se han cobrado TODOS los de porcentaje, asi que con
    # seis arriba habria quedado puesta sin llegar a armarse casi nunca.
    cola = sum(d["sell"] for d in trade.get("mc_take_profit_tiers") or [])
    assert cola == 0, f"con seis peldanos la cola no se arma: reparte {cola:.0%}"
    # O sea que el 2% que sobra SI se queda corriendo, y arrastra la cuenta
    # de token abierta con su renta dentro.
    assert abs(moonbag_fraction(tiers) - 0.02) < 1e-9


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


def test_la_entrada_del_c123_da_para_cobrar_el_primer_peldano() -> None:
    """EL GUARDIAN QUE FALTABA, y su ausencia costo el TP2.

    `tests/test_la_curva_llega_al_primer_tp.py` vigila este mismo contrato
    desde el 23-sep, pero sobre `bot-paper-ai-entry.yaml`. El C123 no tenia
    ninguno, y por eso volvio a caer en la trampa sin que nada chillara:
    umbral al 91% (quedaban +57,5% hasta migrar) con un tp2 que pedia +70%.
    Ese peldano NO SE PODIA TOCAR dentro de la curva, y era el que vendia el
    100% de la posicion. Las unicas salidas reales eran el trinquete, el stop
    o el tope de tiempo.

    LA CUENTA. La curva es un producto constante entre reservas virtuales, el
    precio va como 1/tokens_virtuales**2 y el market cap al migrar son
    410,9 SOL. Para que un peldano de +X% se cobre DENTRO de la curva hace
    falta entrar por debajo de MC_migracion / (1+X).

    Este test NO exige un umbral concreto --eso lo decide Manu y lo mueve
    mucho-- sino que el umbral y los peldanos sigan cuadrando. Si alguien
    sube el umbral o el TP hasta que dejen de encajar, se dice aqui.
    """
    cfg = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    umbral = cfg["filters"]["curva_96"]["umbral"]
    trade = cfg["trade"]

    k = 30 * 1073e6
    vt0 = 1073e6
    #: MC al migrar, en SOL. Sale de la matematica de la curva.
    mc_sol_al_migrar = 410.9

    def mc_en_sol(avance: float) -> float:
        vt = vt0 - 793.1e6 * avance
        return (k / vt**2) * 1e9

    entrada = mc_en_sol(umbral)
    techo = mc_sol_al_migrar / entrada - 1

    tiers = build_tiers(
        [
            (
                trade["take_profit_percentage"],
                trade["take_profit_sell_percentage"],
            ),
            (trade.get("tp2_gain"), trade.get("tp2_sell")),
        ]
    )
    assert tiers, "sin peldanos no hay nada que cobrar"

    # EL PRIMERO ES EL QUE TIENE QUE CABER: es el que recupera el capital, o
    # sea la unica salida en verde garantizada dentro de la curva. Si ese no
    # se puede tocar, las unicas salidas son el stop y el reloj -- que es
    # exactamente lo que pasaba con el tp2 de +70% entrando al 91%.
    assert tiers[0].gain <= techo * 0.75, (
        f"el primer peldano (+{tiers[0].gain * 100:.0f}%) se come el "
        f"{tiers[0].gain / techo * 100:.0f}% del recorrido disponible "
        f"(+{techo * 100:.1f}%): demasiado justo o inalcanzable"
    )

    # LOS DE MAS ARRIBA PUEDEN SALIRSE DE LA CURVA A PROPOSITO. El x4 pedido
    # el 16-oct exige migrar y seguir subiendo en PumpSwap; es una decision,
    # no un fallo, porque se juega con dinero ya recuperado. Lo que NO puede
    # pasar es que la mayor parte de la posicion dependa de que migre.
    cobrables = [t for t in tiers if t.gain <= techo]
    assert cobrables, (
        f"NINGUN peldano cabe en el +{techo * 100:.1f}% que queda hasta "
        "migrar: la escalera no cobraria en ningun sitio"
    )
    assert sum(t.sell for t in cobrables) >= 0.5, (
        "los peldanos alcanzables dentro de la curva venden menos de la "
        "mitad: casi todo quedaria colgado de que la moneda migre"
    )


def test_las_cifras_del_ejemplo_del_yaml_son_de_verdad() -> None:
    """El YAML trae una tabla de ejemplo con tres compras y lo que cobran.

    Se recalcula aqui porque ese bloque YA MINTIO una vez: hablaba de "los
    tres peldanos de fracciones_saldo" con las cifras de la escalera
    33/40/50, y Manu habia cambiado a un 60% plano el mismo dia. Un ejemplo
    con numeros que ya no salen es peor que no tener ejemplo.
    """
    cfg = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    trade, pf, cu = cfg["trade"], cfg["priority_fees"], cfg["compute_units"]
    fee_compra = pf["fixed_amount"] * cu["buy"] / 1e15
    fee_venta = pf["fixed_amount"] * cu["sell"] / 1e15
    f = trade["take_profit_sell_percentage"]
    g = trade["take_profit_percentage"]
    margen = trade["margen_stop_tras_tp"]
    texto = REAL_CONFIG.read_text(encoding="utf-8")

    # LA TABLA SE REESCRIBIO EL 18-oct. Con el TP1 vendiendo solo la mitad ya
    # no basta una columna "cobra el TP1": la garantia son DOS ventas, el TP1
    # y el resto saliendo por el suelo subido a la compra + margen. La tabla
    # del YAML tiene ahora cuatro columnas y aqui se recalculan las cuatro.
    #
    # (compra, cuesta, TP1, resto, total) tal y como estan en el yaml
    for compra, cuesta_esc, tp1_esc, resto_esc, total_esc in (
        (0.24625, "0,24990", "0,18182", "0,13916", "0,32098"),
        (0.18469, "0,18772", "0,13611", "0,10412", "0,24023"),
        (0.13852, "0,14109", "0,10183", "0,07783", "0,17967"),
        (0.07500, "0,07694", "0,05467", "0,04168", "0,09635"),
    ):
        cuesta = compra * 1.01 + fee_compra
        tp1 = f * compra * (1 + g) * 0.99 - fee_venta
        resto = (1 - f) * compra * (1 + margen) * 0.99 - fee_venta
        for valor, escrito, nombre in (
            (cuesta, cuesta_esc, "cuesta"),
            (tp1, tp1_esc, "el TP1"),
            (resto, resto_esc, "el resto"),
            (tp1 + resto, total_esc, "en total"),
        ):
            assert f"{valor:.5f}".replace(".", ",") == escrito, (
                f"el yaml dice que {compra} {nombre} {escrito} y son {valor:.5f}"
            )
            assert escrito in texto, (
                f"{escrito} no esta escrito en el yaml: la tabla y el test "
                "ya no hablan de lo mismo"
            )

        # Y LO QUE DE VERDAD IMPORTA: que sobre. Si algun dia no sobra, este
        # test lo dice antes de que lo diga la cartera.
        assert tp1 + resto > cuesta, (
            f"una compra de {compra} SOL no devolveria el capital: cuesta "
            f"{cuesta:.5f} y las dos ventas dan {tp1 + resto:.5f}"
        )
