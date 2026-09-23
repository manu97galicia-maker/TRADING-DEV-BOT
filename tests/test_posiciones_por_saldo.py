"""Cuantas posiciones caben: techo de 5, y por debajo manda el SALDO.

Son dos frenos y gana el que se quede corto. El techo son 5; el otro es el
guardian del saldo, que antes de cada compra exige:

    saldo - reserva_global - lo_ya_comprometido >= compra + colchon

El colchon por posicion (0,003 SOL) cubre sus comisiones y la renta de la
cuenta de token. Sin el, con varias posiciones abiertas las rentas se
comerian la reserva global y una venta podria quedarse sin gas.
"""

# ruff: noqa: S101, PLR2004

from modelo_tamano import COLCHON_POR_POSICION, tamanos
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
    assert (t["fraccion_saldo"] > 0) != (t["buy_amount"] > 0), (
        "o importe fijo o porcentaje, no los dos ni ninguno"
    )
    # Y el tope no puede recortar lo configurado, o compraria menos de lo
    # pedido sin dejar rastro en el log. Hoy esta a 0 --sin tope-- pero la
    # comprobacion vale igual para el dia que se vuelva a poner.
    # SOBRE LA CARTERA (1 SOL), no sobre el saldo libre: desde el 23-sep el
    # porcentaje se calcula sobre el saldo mas lo que ya esta en monedas.
    pedido = 1.0 * t["fraccion_saldo"] if t["fraccion_saldo"] > 0 else t["buy_amount"]
    tope = t["tope_compra_sol"]
    assert tope == 0 or pedido <= tope, (
        f"el tope ({tope}) recortaria la compra ({pedido:.3f}) sin avisar"
    )


def test_el_tamano_ya_no_se_encoge_solo() -> None:
    """QUE ES LO QUE PROTEGE AHORA, porque ha cambiado y es importante.

    HASTA EL 23-SEP protegia el propio tamano: cada compra era un porcentaje
    de lo que QUEDABA libre, asi que se encogia sola y por muchas plazas que
    hubiera nunca se llegaba a gastar todo. Era un freno automatico, y este
    fichero lo daba por hecho.

    DESDE EL 23-SEP ese freno YA NO EXISTE: el 30% se calcula sobre la cartera
    entera, asi que las compras que caben salen IGUALES y tres seguidas
    comprometen el 90%. Lo unico que queda impidiendo que se gaste hasta el
    ultimo lamport es el guardian del saldo (`test_nunca_se_gasta_la_reserva`).

    Este test fija el hecho nuevo para que no se vuelva a dar por supuesto el
    viejo: si alguien devuelve el calculo al saldo libre, las compras vuelven
    a menguar y esto se pone rojo.
    """
    tam = tamanos(1.0)
    assert len(tam) >= 2
    assert abs(tam[1] - tam[0]) < 0.05 * tam[0], (
        f"la segunda compra tiene que valer lo mismo que la primera: {tam}"
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


def test_con_el_30_por_ciento_solo_caben_tres_posiciones_enteras() -> None:
    """EL PRECIO DE QUE LAS POSICIONES SEAN IGUALES, y hay que tenerlo escrito.

    `max_posiciones` dice 5, pero con cada compra pidiendo el 30% de la
    cartera el dinero se acaba antes: caben TRES enteras --el 90% de la
    cartera jugado a la vez-- y la cuarta sale del recorte de lo que quede.
    La quinta plaza no se usa nunca mientras el porcentaje sea el 30%.

    O sea: el techo de cinco ya no es el freno que manda. Manda el saldo. Si
    algun dia vuelven a salir cinco posiciones, es que alguien bajo el
    porcentaje, y este test lo dice.
    """
    t = _trade()
    tam = tamanos(1.0)
    enteras = [x for x in tam if x > 0.9 * t["fraccion_saldo"]]
    assert len(enteras) == 3, f"deberian caber tres del 30%: {tam}"
    assert len(tam) < TECHO, f"con el 30% no pueden salir {TECHO}: {tam}"
    # Y con poco saldo sigue comprando, solo que diminuto: es lo pedido.
    assert len(tamanos(0.05)) >= 1, "con poco saldo compra poco, pero compra"


def test_el_techo_de_cinco_sigue_escrito_aunque_hoy_no_se_alcance() -> None:
    """El techo existe por el NODO, no por el dinero: cada posicion abierta
    consulta su precio cada segundo y el presupuesto de peticiones no es
    infinito. Hoy no se alcanza porque manda el saldo, pero basta con bajar
    el porcentaje para que vuelva a mandar el techo.
    """
    assert _trade()["max_posiciones"] == TECHO
    assert len(tamanos(50.0)) <= TECHO, "el techo no se puede pasar nunca"


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
