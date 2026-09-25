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
    return [
        (g, v) for g, v in pares if g is not None and v is not None and g > 0 and v >= 0
    ]


def test_los_peldanos_son_los_pedidos() -> None:
    """LA ESCALERA ENTERA, fijada para que cualquier cambio sea deliberado.

    25-sep: TRES peldanos de porcentaje y una cola de market cap. Pedido asi:
    "+25 vendes un 20, sube un 60 vendes un 40%, 100% 25, y el restante lo
    repartes hasta los 80k".

    EL TERCER PELDANO CABE POR PRIMERA VEZ. Con la entrada al 87% el +100%
    quedaba 12,8 puntos POR ENCIMA de la migracion y solo cobraba si la
    moneda migraba y seguia subiendo en PumpSwap. Entrando al 71% cae al
    89,8% de curva. Lo comprueba `test_los_peldanos_caben_dentro_de_la_curva`.
    """
    assert _peldanos() == [(0.25, 0.20), (0.60, 0.40), (1.00, 0.25)]


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
    """La escalera llega hasta el tercer peldano y ahi se acaba.

    Historial: siete peldanos con market cap hasta 400k (24-sep tarde); UN
    SOLO take profit al +55,13% (esa noche, para pagar menos comisiones); dos
    peldanos (+30%/+60%); y desde el 25-sep tres, con cola de market cap.

    EL TP3 VOLVIO A PROPOSITO y por eso ya no se prohibe aqui. Las dos cosas
    que su vuelta obligaba a comprobar estan comprobadas en este mismo
    fichero, que es lo que pedia el texto anterior:
      - que cabe en la curva -> test_los_peldanos_caben_dentro_de_la_curva
      - que el suelo no lo alcanza -> test_el_suelo_nunca_alcanza_al_peldano...

    LO QUE SIGUE PROHIBIDO es `tp_extra` y del cuarto en adelante. Con cuatro
    o mas peldanos de porcentaje la cola de market cap se vuelve inalcanzable
    en la practica --solo se arma cuando se han cobrado TODOS los de
    porcentaje-- y quedaria puesta sin hacer nada.
    """
    assert _trade().get("tp_extra") in (None, []), (
        "han vuelto los peldanos lejanos: hay que recuperar la comprobacion "
        "de que caen en las cifras de market cap pedidas"
    )
    for clave in ("tp4_gain", "tp5_gain", "tp6_gain"):
        assert clave not in _trade(), (
            f"{clave} ha vuelto al yaml. Con cuatro peldanos de porcentaje la "
            f"cola de market cap deja de armarse en la practica"
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


def test_los_escalones_de_market_cap_son_los_pedidos() -> None:
    """LA COLA DE LA ESCALERA (25-sep): "el restante lo repartes hasta 80k".

    Cuatro escalones que reparten el 15% que sobra de los tres peldanos de
    porcentaje, de mas a menos: cuanto mas arriba, menos monedas llegan.

    ESTE TEST SUSTITUYE A `test_no_quedan_escalones_de_market_cap`, que
    exigia la lista vacia. No se ha relajado nada: la via vuelve a estar
    encendida a peticion y ahora se fija lo que tiene dentro.

    SE ARMAN SOLO TRAS LOS TRES PELDANOS DE PORCENTAJE (en el codigo:
    `if tiers and all(executed) and mc_tiers`). O sea que una moneda a 80k de
    market cap que solo haya subido un 80% desde la compra no vende nada por
    aqui. Son una COLA, no una via paralela.
    """
    assert _trade()["mc_take_profit_tiers"] == [
        {"target_mc": 50000, "sell": 0.05},
        {"target_mc": 60000, "sell": 0.04},
        {"target_mc": 70000, "sell": 0.03},
        {"target_mc": 80000, "sell": 0.03},
    ]


def test_los_escalones_de_market_cap_van_de_mas_a_menos() -> None:
    """De mas a menos porque cuanto mas arriba, menos monedas llegan.

    El trozo gordo se cobra pronto. Un reparto creciente estaria apostando a
    que la mayoria llega al ultimo escalon, que es justo lo contrario de lo
    que pasa.
    """
    mc = _trade()["mc_take_profit_tiers"]
    objetivos = [d["target_mc"] for d in mc]
    ventas = [d["sell"] for d in mc]
    assert objetivos == sorted(objetivos), f"tienen que ir subiendo: {objetivos}"
    assert len(set(objetivos)) == len(objetivos), f"hay dos iguales: {objetivos}"
    for antes, despues in pairwise(ventas):
        assert despues <= antes, f"de mas a menos: {ventas}"


def test_la_cola_de_market_cap_recoge_todo_lo_que_sobra() -> None:
    """Lo pedido fue repartir EL RESTANTE, o sea que no puede quedar nada.

    Si los escalones sumaran menos que el resto, quedaria un moonbag que
    nadie pidio; si sumaran mas, se estaria ordenando vender mas de lo que
    hay. Los dos fallos son silenciosos, y por eso van fijados aqui.
    """
    t = _trade()
    porcentaje = sum(v for _, v in _peldanos())
    cola = sum(d["sell"] for d in t["mc_take_profit_tiers"])
    resto = round(1.0 - porcentaje, 6)
    assert round(cola, 6) == resto, (
        f"los peldanos venden el {porcentaje:.0%} y sobra el {resto:.0%}, "
        f"pero la cola de market cap reparte el {cola:.0%}"
    )


def test_las_ganancias_van_en_orden_creciente() -> None:
    """Un peldano por debajo del anterior no llegaria a dispararse nunca."""
    ganancias = [g for g, _ in _peldanos()]
    assert ganancias == sorted(ganancias)


def test_nunca_se_vende_mas_del_100() -> None:
    """Vender mas de lo que se tiene es una orden imposible."""
    assert sum(v for _, v in _peldanos()) <= 1.0


def test_ya_no_queda_moonbag_perpetuo() -> None:
    """LO QUE SOBRA DE LOS PELDANOS ESTA TODO ASIGNADO (25-sep).

    Los tres peldanos de porcentaje venden el 85% (20 + 40 + 25) y el 15%
    que queda se lo reparten los cuatro escalones de market cap. O sea que
    una moneda que llegue a 80k cierra ENTERA y no queda nada corriendo.

    ESTE TEST HA IDO Y VENIDO TRES VECES y por eso conviene el historial:
      - `test_ya_no_queda_moonbag` cuando el take profit unico vendia el 100%
      - `test_el_moonbag_es_lo_que_sobra_de_la_escalera` con la escalera de
        dos peldanos, que dejaba un 10% sin vender nunca
      - este, que vuelve a cero pero por otro motivo: no es que se venda todo
        de golpe, es que la cola de market cap recoge el resto.

    LO QUE SE GANA: no quedan cuentas de token abiertas con su renta dentro
    ni posiciones casi vacias vigilandose. LO QUE CUESTA: si una moneda hace
    x10 por encima de los 80k, ya no queda nada dentro para cogerlo.

    OJO: esto vale MIENTRAS LOS ESCALONES SE ALCANCEN. Si el market cap no
    llega (ver el aviso de los 80k en el yaml), el 15% se queda dentro
    protegido por el suelo. "Sin moonbag" es una propiedad de la
    configuracion, no una promesa de lo que pasara en cada moneda.
    """
    t = _trade()
    porcentaje = sum(v for _, v in _peldanos())
    cola = sum(d["sell"] for d in t.get("mc_take_profit_tiers") or [])
    assert round(porcentaje + cola, 6) == 1.0, (
        f"peldanos {porcentaje:.0%} + cola {cola:.0%} tienen que sumar el "
        f"100% de la compra para que no quede moonbag perpetuo"
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
