"""Cuantas posiciones caben: techo de 5, y por debajo manda el SALDO.

Son dos frenos y gana el que se quede corto. El techo son 5; el otro es el
guardian del saldo, que antes de cada compra exige:

    saldo - reserva_global - lo_ya_comprometido >= compra + colchon

El colchon por posicion (0,003 SOL) cubre sus comisiones y la renta de la
cuenta de token. Sin el, con varias posiciones abiertas las rentas se
comerian la reserva global y una venta podria quedarse sin gas.
"""

# ruff: noqa: S101, PLR2004, PLC0415

from modelo_tamano import COLCHON_POR_POSICION, tamanos
from modelo_tamano import importe as importe_de_compra
from modelo_tamano import trade as _trade

# Techo configurado de posiciones simultaneas (max_posiciones).
TECHO = 5


def caben(saldo: float, reserva: float, compra: float) -> int:
    """Cuantas posiciones caben en un saldo, dejando la reserva intacta."""
    libre = saldo - reserva
    if libre <= 0:
        return 0
    return min(int(libre // (compra + COLCHON_POR_POSICION)), TECHO)


def test_el_techo_son_cinco_posiciones() -> None:
    """Lo pedido: maximo 5 a la vez, si da el capital."""
    assert _trade()["max_posiciones"] == TECHO


def test_la_reserva_sigue_siendo_la_pedida() -> None:
    assert _trade()["min_reserve_sol"] == 0.03


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
        k
        for k in ("buy_amount_usd", "fraccion_saldo", "buy_amount")
        if t.get(k, 0) > 0
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
        # Y la caida, cuando llega, es de golpe y no gradual.
        assert tam[-1] < 0.5 * tam[0], (
            f"con base cartera las ultimas son recortes del saldo libre: {tam}"
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
    """CUANTAS SE ABREN DE VERDAD, que no es lo mismo que `max_posiciones`.

    El techo son cinco y lo pedido fue "puede haber multiples posiciones".
    Pero quien decide de verdad es el DINERO, y con el 30% de la cartera
    (24-sep, tarde) no llega para cinco: con 1 SOL salen 0,300 · 0,299 ·
    0,298 · 0,048 y se acabo. Tres del 30% ya son el 90% del saldo.

    ESTE TEST SE LLAMABA `test_caben_las_cinco_plazas` y exigia cinco. Se
    puso rojo al pasar del 25% al 30%, que era su trabajo. No se ha bajado
    la exigencia: se ha cambiado la PREGUNTA, porque "caben cinco" dejo de
    ser cierto y seguir afirmandolo era el tipo de test verde que miente.
    Ahora fija cuantas caben en cada modo, y cualquier cambio de tamano
    vuelve a moverlo.

    EL TECHO DEJA DE MANDAR, y conviene saberlo al leer un dia de
    operaciones: ver cuatro posiciones y no cinco NO es un fallo del bot.
    Si se quiere que el techo diga la verdad, la palanca es bajar
    `max_posiciones` a 3; si se quiere que no haya migas,
    `compra_minima_sol`. Ninguna se toca sin pedirlo.
    """
    from modelo_tamano import base_es_libre

    tam = tamanos(1.0)
    enteras = [x for x in tam if x > 0.9 * tam[0]]
    if base_es_libre():
        # Menguan suaves: caben las cinco y la ultima sigue siendo decente.
        assert len(tam) == TECHO, f"con base libre caben las cinco: {tam}"
        assert tam[-1] > 0.4 * tam[0], f"la ultima no puede ser una miga: {tam}"
    else:
        # Base "cartera": iguales hasta que se agota el saldo libre.
        assert len(tam) < TECHO, (
            f"con el 30% de la cartera el dinero no da para las cinco: {tam}"
        )
        assert len(tam) == 4, f"da para cuatro: {tam}"
        # CUANTAS SON COMPRAS DE VERDAD SALE DE LA FRACCION, no escrito a
        # mano. El saldo libre da para ~0,95 del total (el resto es reserva y
        # colchones), asi que caben `0,95 / fraccion` compras enteras y el
        # resto son recortes. Con el 25% y el 30% salian tres; con el 50%
        # (24-sep) sale UNA. El numero a mano fallaba en cada cambio sin
        # decir por que.
        frac = float(_trade()["fraccion_saldo"])
        esperadas = int(0.95 / frac)
        assert len(enteras) == esperadas, (
            f"con el {frac * 100:.0f}% de la cartera salen {esperadas} compras "
            f"enteras, no {len(enteras)}: {tam}"
        )
        assert tam[-1] < 0.2 * tam[0], f"la ultima es calderilla: {tam}"
    # Y con poco saldo sigue comprando, solo que diminuto: es lo pedido.
    assert len(tamanos(0.05)) >= 1, "con poco saldo compra poco, pero compra"


def test_el_techo_de_cinco_sigue_mandando() -> None:
    """El techo existe por el NODO, no por el dinero: cada posicion abierta
    consulta su precio cada segundo y el presupuesto de peticiones no es
    infinito. Por mucho saldo que haya, nunca mas de cinco a la vez.
    """
    assert _trade()["max_posiciones"] == TECHO
    assert len(tamanos(50.0)) == TECHO, "el techo no se puede pasar nunca"


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
