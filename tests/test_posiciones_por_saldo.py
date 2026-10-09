"""Cuantas posiciones caben: un techo de 2, y por debajo manda el SALDO.

Son dos frenos y gana el que se quede corto. El techo son 2 (25-sep, pedido:
"maximo dos operaciones simultaneas"); el otro es el guardian del saldo, que
antes de cada compra exige:

    saldo - reserva_global - lo_ya_comprometido >= compra + colchon

El colchon por posicion (0,003 SOL) cubre sus comisiones y la renta de la
cuenta de token. Sin el, con varias posiciones abiertas las rentas se
comerian la reserva global y una venta podria quedarse sin gas.

CUAL DE LOS DOS MANDA SE HA INVERTIDO, y es lo que este fichero fija ahora.
Con techo de 5 y el 50% de la cartera por compra mandaba el saldo: se abrian
dos compras de verdad y dos migas, y el techo era decorativo. Con el techo en
2 manda el TECHO: se abren dos compras enteras y se para, con cartera de
sobra sin comprometer. Ver cuatro posiciones ya no es posible, y ver dos con
saldo de sobra no es un fallo -- es el techo haciendo su trabajo.
"""

# ruff: noqa: S101, PLR2004, PLC0415

import yaml
from modelo_tamano import COLCHON_POR_POSICION, CONFIG, plazas_efectivas, tamanos
from modelo_tamano import importe as importe_de_compra
from modelo_tamano import trade as _trade

# Techo EFECTIVO de posiciones simultaneas. `max_posiciones: 0` en el yaml
# no es cero: el trader aplica `max_posiciones or TOPE_DURO_POSICIONES`.
TECHO = plazas_efectivas()


def _cfg_fees() -> dict:
    """El bloque `priority_fees` del yaml, que decide lo que cuesta vender."""
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["priority_fees"]


def caben(saldo: float, reserva: float, compra: float) -> int:
    """Cuantas posiciones caben en un saldo, dejando la reserva intacta."""
    libre = saldo - reserva
    if libre <= 0:
        return 0
    return min(int(libre // (compra + COLCHON_POR_POSICION)), TECHO)


def test_el_techo_de_posiciones_es_el_que_aplica_el_trader() -> None:
    """`max_posiciones: 0` NO es cero plazas: son 40.

    Historial: 5 -> 2 (25-sep, "maximo dos operaciones simultaneas") -> 0
    (05-oct, commit "Restrict paper strategy to Telegram alpha channels").

    EL 0 ES UNA TRAMPA, y por eso este test mira el numero EFECTIVO. En el
    trader la linea es `int(max_posiciones or TOPE_DURO_POSICIONES)`, asi que
    un 0 en el yaml se convierte en 40. El modelo de estos tests hacia
    `range(0)` y simulaba cero compras: todos los tests que lo usan se
    quedaban verdes sin comprobar nada. Se destapo el 09-oct.

    QUIEN FRENA DE VERDAD CON 40 PLAZAS es el dinero: con el 30% de la
    cartera por compra se abren cuatro o cinco y se acaba el saldo libre.
    """
    configurado = _trade()["max_posiciones"]
    assert plazas_efectivas() == TECHO
    if configurado == 0:
        assert TECHO == 40, "el tope duro del trader es 40"
    else:
        assert configurado == TECHO


def test_la_reserva_sigue_siendo_la_pedida() -> None:
    """0,03 SOL. Bajo a 0,01 el 25-sep y el commit del 05-oct la devolvio."""
    assert _trade()["min_reserve_sol"] == 0.03


def test_la_reserva_cubre_vender_todo_lo_abierto() -> None:
    """LO QUE DE VERDAD TIENE QUE CUMPLIR LA RESERVA, y no es ser un numero.

    La reserva es el SOL que el bot nunca gasta comprando, para que siempre
    quede gas con el que VENDER. Quedarse corto no es "compro menos": es
    tener una moneda cayendose y no poder soltarla.

    EL PEOR CASO es que TODAS las posiciones abiertas salten el stop a la vez,
    porque la venta de proteccion lleva su propia comision de prioridad, que
    es ~24 veces la de un take profit normal (0,005091 contra 0,000211 SOL).

    LO DISPONIBLE no es solo la reserva: cada posicion aparta ademas su
    colchon de 0,003 (`_fee_headroom_lamports`) para sus comisiones y la renta
    de la cuenta de token.

    POR QUE ESTE TEST Y NO SOLO EL NUMERO. El 25-sep se pidio bajar la reserva
    y el numero llego con un emoji comiendose un caracter: 0,001 o 0,01. El
    primero no cubre ni UNA venta de proteccion. Un test que solo fije "0,01"
    se quedaria verde si manana suben las plazas a 4 --el peor caso se
    duplica a 0,0204 y 0,01 deja de llegar-- o si sube la comision de venta.
    Este ata los tres numeros: reserva, plazas y comision.
    """
    t = _trade()
    fees = _cfg_fees()
    # Comision de una venta de proteccion, en SOL: microlamports/CU x CU / 1e15.
    cu_venta = 120_000
    venta_proteccion = fees["fixed_amount_venta_proteccion"] * cu_venta / 1e15
    plazas = int(t["max_posiciones"])
    peor_caso = plazas * venta_proteccion
    disponible = float(t["min_reserve_sol"]) + plazas * COLCHON_POR_POSICION
    assert disponible > peor_caso, (
        f"con {plazas} plazas saltando el stop a la vez hacen falta "
        f"{peor_caso:.4f} SOL de comision y solo hay {disponible:.4f} "
        f"(reserva {t['min_reserve_sol']} + {plazas} x {COLCHON_POR_POSICION}): "
        f"el bot podria quedarse sin gas para vender lo que tiene abierto"
    )


def test_la_reserva_cubre_al_menos_una_venta_de_proteccion_ella_sola() -> None:
    """La reserva SOLA, sin contar los colchones, cubre cerrar una posicion.

    Los colchones se apartan al abrir cada posicion, asi que dependen de que
    todo haya ido bien. La reserva es la ultima red y tiene que valer por si
    misma para al menos una salida de emergencia.
    """
    t = _trade()
    fees = _cfg_fees()
    venta_proteccion = fees["fixed_amount_venta_proteccion"] * 120_000 / 1e15
    assert float(t["min_reserve_sol"]) > venta_proteccion, (
        f"la reserva ({t['min_reserve_sol']} SOL) no cubre ni una venta de "
        f"proteccion ({venta_proteccion:.6f} SOL)"
    )


def test_el_modo_de_tamano_esta_claro() -> None:
    """EL CAMBIO DEL 19-SEP: se compra el 25% de lo libre, no una cantidad fija.

    Los dos tests que habia aqui modelaban posiciones de tamano FIJO
    (`tope_compra_sol`) y contaban cuantas cabian. Ese modelo ya no describe al
    bot: `tope_compra_sol` paso a ser un freno contra fallos y quien decide el
    tamano es `fraccion_saldo`. Mantenerlos habria sido peor que borrarlos --
    verde sobre un comportamiento que el bot ya no tiene.
    """
    t = _trade()
    # Uno de los dos manda, nunca los dos: el porcentaje, si es >0, SUSTITUYE
    # al importe fijo dentro del cerrojo de gasto. Hoy manda el porcentaje.
    encendidos = [
        k for k in ("buy_amount_usd", "fraccion_saldo", "buy_amount") if t.get(k, 0) > 0
    ]
    assert len(encendidos) == 1, (
        f"manda UNO de los tres modos, y hoy mandan: {encendidos or 'ninguno'}"
    )
    # Y el tope no puede recortar lo configurado, o compraria menos de lo
    # pedido sin dejar rastro en el log. Hoy esta a 0 --sin tope-- pero la
    # comprobacion vale igual para el dia que se vuelva a poner.
    pedido = importe_de_compra(1.0, t)
    tope = t["tope_compra_sol"]
    assert tope == 0 or pedido <= tope, (
        f"el tope ({tope}) recortaria la compra ({pedido:.3f}) sin avisar"
    )


def test_el_tamano_vuelve_a_encogerse_solo() -> None:
    """QUE ES LO QUE FRENA, que ha cambiado tres veces y conviene tenerlo fijo.

    HASTA EL 23-SEP frenaba el propio tamano: cada compra era un porcentaje de
    lo que QUEDABA libre. El 23-sep la base paso a la cartera y ese freno
    desaparecio -- las compras dejaron de menguar y tres seguidas del 25%
    comprometian el 75% del dinero. El 24-sep se volvio al saldo libre.

    Y EL 24-SEP POR LA TARDE SE VOLVIO A "CARTERA" con el 25%, pedido asi
    ("compra el 25% del total del balance"). O sea que el freno automatico
    NO existe ahora mismo: las compras salen iguales hasta que se acaba el
    saldo libre, y lo que las para es el guardian del saldo, no su tamano.

    ESTE TEST SE ADAPTA A LA BASE QUE HAYA, en vez de exigir una. Lo que fija
    es la FORMA de cada modo, que es lo que de verdad hay que saber al leer
    un dia de operaciones:

      libre    cada compra menor que la anterior, siempre.
      cartera  las primeras IGUALES y las ultimas recortadas de golpe por
               lo que quedaba libre.
    """
    from modelo_tamano import base_es_libre

    tam = tamanos(1.0)
    assert len(tam) >= 2
    if base_es_libre():
        assert tam[1] < tam[0], f"con base libre tienen que menguar: {tam}"
    else:
        # LA PROPIEDAD QUE DE VERDAD DISTINGUE LA BASE "CARTERA": la PRIMERA
        # compra es la fraccion del saldo ENTERO, sin descontar nada. Con
        # base "libre" seria la fraccion de lo gastable (saldo - reserva) y
        # saldria mas pequena.
        #
        # Antes aqui se exigia que la primera y la segunda salieran IGUALES.
        # Era cierto mientras la fraccion era pequena; con el 50% (24-sep) la
        # segunda ya sale recortada --0,500 y 0,350-- porque no queda saldo
        # libre para otra entera, y el test se puso rojo describiendo el modo
        # y no un fallo.
        frac = float(_trade()["fraccion_saldo"])
        assert abs(tam[0] - frac * 1.0) < 1e-6, (
            f"la primera tiene que ser el {frac * 100:.0f}% del saldo entero: {tam}"
        )
        # VUELVEN LAS MIGAS CON `max_posiciones: 0` (05-oct), y conviene
        # tenerlo escrito porque es justo lo que el techo de 2 habia quitado
        # el 25-sep. Con 1 SOL y el 30%: 0,300 / 0,299 / 0,298 / 0,048.
        #
        # LA CUARTA SON ~4,4 $, y la comision de ida y vuelta (~0,93 $) se
        # lleva el 21% antes de que la moneda se mueva. No es un fallo: es la
        # consecuencia de quitar el techo. La palanca para cortarlas sin
        # volver a poner techo es `compra_minima_sol`, que sigue a 0.
        assert tam[-1] < 0.5 * tam[0], (
            f"sin techo, la ultima vuelve a ser un recorte pequeno: {tam}"
        )


def test_la_reserva_nunca_se_toca() -> None:
    """Justo por debajo de la reserva no se abre nada: queda gas para vender."""
    cfg = _trade()
    reserva, compra = cfg["min_reserve_sol"], cfg["tope_compra_sol"]
    assert caben(reserva, reserva, compra) == 0
    assert caben(reserva + compra / 2, reserva, compra) == 0


def test_lo_que_queda_cubre_las_ventas_pendientes() -> None:
    """Con el saldo al limite, la reserva mas los colchones dan para vender.

    Es la comprobacion que importa: no sirve de nada abrir muchas si luego
    no hay SOL para cerrarlas.
    """
    cfg = _trade()
    reserva, compra = cfg["min_reserve_sol"], cfg["tope_compra_sol"]
    saldo = 2.0
    n = caben(saldo, reserva, compra)
    gastado = n * (compra + COLCHON_POR_POSICION)
    assert saldo - gastado >= reserva


def test_cuantas_plazas_caben_de_verdad() -> None:
    """CUANTAS SE ABREN DE VERDAD, y quien de los dos frenos manda.

    HISTORIA DE ESTE TEST, porque es la historia del error que arregla. Se
    llamaba `test_caben_las_cinco_plazas` y exigia cinco. Se puso rojo al
    pasar del 25% al 30%, que era su trabajo: "caben cinco" habia dejado de
    ser cierto. Se cambio entonces la PREGUNTA --cuantas caben en cada
    modo-- en vez de bajar la exigencia, y quedo escrito que el techo era
    decorativo y que ver cuatro posiciones no era un fallo.

    EL 25-sep SE ARREGLO POR EL OTRO LADO: `max_posiciones` bajo de 5 a 2,
    o sea que el techo dejo de ser decorativo y paso a ser EL freno. Con el
    50% de la cartera y 1 SOL salen 0,500 y 0,350 y se acaba -- no porque el
    dinero se agote (queda un 15% sin comprometer) sino porque no hay mas
    plazas.

    LO QUE FIJA AHORA: que manda el techo y no el saldo. Si alguien sube
    `max_posiciones` sin bajar la fraccion, vuelven las migas y esto se pone
    rojo; si baja la fraccion sin tocar el techo, deja de mandar el techo y
    tambien se pone rojo. Los dos numeros son un solo mando.
    """
    from modelo_tamano import base_es_libre

    tam = tamanos(1.0)
    if base_es_libre():
        # Menguan suaves: se llenan las plazas y la ultima sigue siendo decente.
        assert len(tam) == TECHO, f"con base libre se llenan las plazas: {tam}"
        assert tam[-1] > 0.4 * tam[0], f"la ultima no puede ser una miga: {tam}"
    else:
        # Base "cartera": iguales hasta que se agota el saldo libre... salvo
        # que el techo llegue antes, que es lo que pasa ahora.
        # VUELVE A MANDAR EL DINERO (05-oct, `max_posiciones: 0` = 40
        # plazas). Con el 30% el saldo libre se agota en la cuarta, muy lejos
        # de las 40, asi que el techo no ata nada.
        assert len(tam) < TECHO, (
            f"con {TECHO} plazas el techo no puede ser lo que para al bot: "
            f"lo para el dinero. Salen {len(tam)} compras: {tam}"
        )
        # Y SE COMPROMETE CASI TODO, que es la otra cara: sin techo el bot
        # sigue comprando restos hasta quedarse sin saldo libre.
        assert sum(tam) > 0.90, (
            f"sin techo se compromete casi toda la cartera: {tam}"
        )
    # Y con poco saldo sigue comprando, solo que diminuto: es lo pedido.
    assert len(tamanos(0.05)) >= 1, "con poco saldo compra poco, pero compra"


def test_el_techo_sigue_siendo_un_limite_duro() -> None:
    """Por mucho saldo que haya, nunca mas plazas que el techo efectivo.

    El techo existe por el NODO, no por el dinero: cada posicion abierta
    consulta su precio y el presupuesto de peticiones no es infinito.

    CON 40 PLAZAS ESE LIMITE YA NO SE TOCA en la practica --el saldo se
    agota mucho antes-- pero tiene que seguir existiendo: es la ultima red
    si alguien baja mucho el porcentaje de compra.
    """
    assert len(tamanos(50.0)) <= TECHO, "el techo no se puede pasar nunca"
    # Y con un porcentaje diminuto, el que ata es el techo y no el saldo.
    assert len(tamanos(50.0, plazas=TECHO)) <= TECHO


def test_nunca_se_gasta_la_reserva() -> None:
    """Lo unico que queda protegiendo el saldo, ahora que no hay tope ni suelo.

    Sin reserva no habria gas para VENDER lo comprado, y una posicion sin
    salida vale cero.

    OJO CON LO QUE SE SUMA, que es donde esto estuvo mal. Sumar los `need` de
    todas las compras y compararlo con el saldo inicial es una cuenta
    equivocada: el `need` incluye el slippage, que es un TECHO que casi nunca
    se paga, y ademas se reserva y se SUELTA en cuanto la compra entra. Nunca
    estan los cinco reservados a la vez. Lo que hay que comprobar es lo que
    sale de la wallet de verdad, y que aguante tambien en el peor caso.
    """
    t = _trade()
    for saldo in (0.05, 0.25, 1.0, 5.0):
        for pesimista in (False, True):
            gastado = sum(
                x * (1 + t["buy_slippage"]) + COLCHON_POR_POSICION
                if pesimista
                else x + COLCHON_POR_POSICION
                for x in tamanos(saldo, pesimista=pesimista)
            )
            assert saldo - gastado >= t["min_reserve_sol"] - 1e-9, (
                f"con {saldo} SOL se gastarian {gastado:.4f} y hay que dejar "
                f"{t['min_reserve_sol']} de reserva"
            )
