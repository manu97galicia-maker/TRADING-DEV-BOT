"""UN SOLO SITIO donde esta escrito de que tamano compra el bot.

POR QUE EXISTE. Esta cuenta estaba COPIADA en tres ficheros de test
(`test_tamano_de_compra`, `test_posiciones_por_saldo`, `test_posicion_polvo`).
Cada vez que se cambiaba la forma de decidir el importe habia que acordarse de
las tres, y el 23-sep se vio lo que pasa cuando no: se cambio la base del
porcentaje del saldo libre a la cartera entera, una copia se actualizo y las
otras dos siguieron VERDES midiendo el comportamiento viejo. Un test verde
sobre algo que el bot ya no hace es peor que no tener test, porque se confia
en el.

QUE MODELA. El calculo del importe dentro del cerrojo de gasto de
`universal_trader.py`. No es el codigo de verdad --es una copia-- pero al
menos ahora es UNA copia, y cuando se separe del original se separa en un
sitio y no en tres.
"""

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
#: Lo que el codigo reserva por posicion: comisiones + renta de la cuenta de
#: token (`_fee_headroom_lamports` = 3_000_000 lamports).
COLCHON_POR_POSICION = 0.003
#: El tope de posiciones que aplica el trader cuando `max_posiciones` es 0.
#: Tiene que coincidir con `TOPE_DURO_POSICIONES` en universal_trader.py: lo
#: comprueba `test_el_tope_duro_del_modelo_es_el_del_codigo`.
TOPE_DURO_POSICIONES = 40


def plazas_efectivas(t: dict | None = None) -> int:
    """Las plazas que aplica el trader de verdad.

    `max_posiciones: 0` NO significa cero: en el codigo es
    `int(max_posiciones or TOPE_DURO_POSICIONES)`, o sea 40. El modelo hacia
    `range(0)` y simulaba CERO compras, asi que los tests que lo usan se
    quedaban verdes sin comprobar nada -- incluido el de la reserva, que es
    el que avisa de quedarse sin gas para vender. Se destapo el 09-oct al
    mezclar el commit del 05-oct, que puso `max_posiciones: 0`.
    """
    cfg = t if t is not None else trade()
    return int(cfg.get("max_posiciones") or TOPE_DURO_POSICIONES)
#: PRECIO DE SOL DE REFERENCIA PARA LOS TESTS, y solo para eso.
#:
#: El bot NO usa este numero: divide por `_SOL_USD`, que refresca solo desde
#: DexScreener cada hora, y si no lo ha podido confirmar no compra. Aqui hace
#: falta uno fijo porque un test que sale a internet no es un test: daria
#: distinto cada dia y se pondria rojo por el mercado, no por el codigo.
#:
#: Lo que se comprueba con el es la MECANICA --que 10 $ se convierten en los
#: SOL que toca y que el resto de frenos siguen puestos-- no el numero exacto
#: de SOL, que cambia cada hora por definicion.
SOL_USD_REF = 220.0


def trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def base_es_libre(t: dict | None = None) -> bool:
    """True si el porcentaje se calcula sobre el saldo LIBRE.

    La base ha ido y venido tres veces en dos dias, asi que se lee del yaml
    en vez de darse por supuesta: darla por supuesta es como dos copias de
    esta misma cuenta se quedaron verdes midiendo el modo viejo.
    """
    t = t or trade()
    return str(t.get("fraccion_base", "cartera")).strip().lower() == "libre"


def importe(cartera: float, t: dict | None = None) -> float:
    """El importe de UNA compra con esa CARTERA, en el modo que este puesto.

    Hay TRES modos y se excluyen. El orden es el mismo que en el codigo:

    1. `buy_amount_usd` -- un importe en DOLARES, que se pasa a SOL con el
       precio de SOL en vivo. Es el que manda hoy: 10 $ por operacion.
    2. `fraccion_saldo` -- un porcentaje de la cartera.
    3. `buy_amount` -- un numero de SOL fijo.

    Esta funcion existe para que los tests no tengan que saber cual esta
    activo: se han alternado varias veces en un mismo dia.

    OJO CON EL ARGUMENTO. Desde el 23-sep el porcentaje se calcula sobre la
    CARTERA entera --el saldo de la wallet mas lo que ya esta metido en las
    monedas abiertas-- y no sobre el saldo libre. Pasarle aqui el saldo libre
    devuelve el numero viejo sin que nada se queje.

    El tope y el suelo solo se aplican si son MAYORES QUE CERO. Cero significa
    "sin limite" en toda esta configuracion, y escribir `min(x, tope)` a secas
    daria 0.
    """
    t = t or trade()
    usd = t.get("buy_amount_usd", 0)
    frac = t.get("fraccion_saldo", 0)
    if usd > 0:
        imp = float(usd) / SOL_USD_REF
    elif frac > 0:
        # `cartera` es el nombre historico del argumento; con
        # `fraccion_base: libre` quien llama pasa aqui el saldo LIBRE. La
        # cuenta es la misma, lo que cambia es sobre que se hace.
        imp = cartera * frac
    else:
        imp = float(t["buy_amount"])
    tope = t.get("tope_compra_sol", 0)
    if tope > 0 and imp > tope:
        imp = tope
    return max(0.0, imp)


def tamanos(
    saldo: float, plazas: int | None = None, *, pesimista: bool = False
) -> list[float]:
    """Los importes de las compras que se abririan con ese saldo inicial.

    DOS NUMEROS DISTINTOS Y NO HAY QUE MEZCLARLOS:

    * `cartera` = el SOL que queda en la wallet MAS lo que ya se metio en
      monedas. Es la base del porcentaje. No baja al comprar --el dinero
      cambia de sitio, no desaparece-- solo se encoge por las comisiones.
    * `libre` = el SOL de la wallet menos la reserva intocable. Es lo que se
      puede gastar de verdad, y ese SI baja con cada compra. Es lo que acaba
      frenando: por eso con el 30% no salen cinco posiciones.

    INCLUYE EL GUARDIAN DEL SALDO, que es lo que mas importa: no basta con que
    el importe "quepa", tiene que caber la compra MAS el slippage MAS las
    comisiones. Sin esa guarda la simulacion se pasaba del saldo y el test
    acusaba al bot de algo que el bot no hace -- tiene su `if free < need` y
    se para solo.

    `pesimista=True` supone que TODAS las compras pagan el slippage entero
    (el 10%), que es el peor caso posible. El caso normal supone que no se
    paga casi nada, que es lo habitual. La verdad esta en medio y el bot no
    necesita adivinarla --relee el saldo de la cadena antes de cada compra--
    pero la reserva tiene que aguantar en los DOS extremos, y por eso se
    pueden simular los dos.
    """
    t = trade()
    wallet = saldo
    dentro = 0.0
    salen: list[float] = []
    suelo = t.get("compra_minima_sol", 0)
    sobre_libre = base_es_libre(t)
    for _ in range(plazas or plazas_efectivas(t)):
        libre = wallet - t["min_reserve_sol"]
        if libre <= 0:
            break
        imp = importe(libre if sobre_libre else wallet + dentro, t)
        if imp <= 0 or (suelo > 0 and imp < suelo):
            break
        necesita = imp * (1 + t["buy_slippage"]) + COLCHON_POR_POSICION
        # SI NO CABE, EL RECORTE. El bot no se salta la moneda: compra con esa
        # fraccion de lo que quede LIBRE. Sin esto el modelo decia "no compra"
        # justo donde el bot SI compra, que es el caso de saldo pequeno -- el
        # mas facil de tener mal y el que mas se consulta.
        #
        # OJO: el recorte se calcula sobre el saldo libre, NO sobre la
        # cartera. Son dos cosas distintas con el mismo aire: el porcentaje
        # decide cuanto se quiere gastar, el recorte cuanto se PUEDE.
        _f = t.get("fraccion_si_no_llega", 0)
        if necesita > libre and _f > 0:
            recorte = libre * _f
            if t.get("tope_compra_sol", 0) > 0:
                recorte = min(recorte, t["tope_compra_sol"])
            nec = recorte * (1 + t["buy_slippage"]) + COLCHON_POR_POSICION
            bajo = suelo > 0 and recorte < suelo
            if recorte > 0 and nec <= libre and not bajo:
                imp, necesita = recorte, nec
        if necesita > libre:
            break
        salen.append(round(imp, 6))
        # LO QUE SE RESERVA NO ES LO QUE SE GASTA, y mezclarlo falsea el
        # resultado. `necesita` lleva dentro el slippage, que es un TECHO:
        # casi nunca se paga. Se exige que quepa --eso hace el bot-- pero de
        # la wallet sale el importe mas las comisiones. Restando `necesita`,
        # la cartera parecia encogerse un 3% por compra y las posiciones
        # salian desiguales por culpa del modelo, no del bot.
        wallet -= necesita if pesimista else imp + COLCHON_POR_POSICION
        dentro += imp
    return salen
