"""Cuantas posiciones caben: techo de 5, y por debajo manda el SALDO.

Son dos frenos y gana el que se quede corto. El techo son 5; el otro es el
guardian del saldo, que antes de cada compra exige:

    saldo - reserva_global - lo_ya_comprometido >= compra + colchon

El colchon por posicion (0,003 SOL) cubre sus comisiones y la renta de la
cuenta de token. Sin el, con varias posiciones abiertas las rentas se
comerian la reserva global y una venta podria quedarse sin gas.
"""

# ruff: noqa: S101, PLR2004

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
# Lo que el codigo reserva por posicion: comisiones + renta de la cuenta de
# token (_fee_headroom_lamports = 3_000_000 lamports).
COLCHON_POR_POSICION = 0.003
# Techo configurado de posiciones simultaneas (max_posiciones).
TECHO = 5


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def importe_de_compra(libre: float, t: dict) -> float:
    """Lo que compraria el bot con ese saldo libre, en el modo que este puesto.

    Hay DOS modos y se excluyen: importe fijo (`buy_amount`) o porcentaje del
    saldo (`fraccion_saldo`); el porcentaje, si es mayor que cero, sustituye al
    fijo dentro del cerrojo de gasto. Esta funcion existe para que los tests no
    tengan que saber cual esta activo -- cada vez que se cambiaba de modo habia
    que rehacer media docena de tests.

    El tope y el suelo solo se aplican si son MAYORES QUE CERO: en esta
    configuracion, cero significa "sin limite".
    """
    frac = t.get("fraccion_saldo", 0)
    imp = libre * frac if frac > 0 else float(t["buy_amount"])
    tope = t.get("tope_compra_sol", 0)
    if tope > 0 and imp > tope:
        imp = tope
    return max(0.0, imp)


def tamanos(saldo: float) -> list[float]:
    """Los importes de las compras que se abrirían con ese saldo.

    Cada una es el 25% de lo que queda LIBRE en ese momento, no del saldo
    inicial. El tope y el suelo solo se aplican si son mayores que cero: en
    esta configuracion, cero significa "sin limite".
    """
    t = _trade()
    libre = saldo - t["min_reserve_sol"]
    salen: list[float] = []
    for _ in range(TECHO):
        if libre <= 0:
            break
        imp = importe_de_compra(libre, t)
        if imp <= 0 or (t["compra_minima_sol"] > 0 and imp < t["compra_minima_sol"]):
            break
        # EL GUARDIAN DEL SALDO, igual que en el codigo: no basta con que el
        # 25% "quepa", tiene que caber la compra MAS el slippage MAS las
        # comisiones. Con saldos muy pequenos el colchon fijo (0,003) pesa mas
        # que la propia compra, y ahi el bot se para en vez de pasarse.
        necesita = imp * (1 + t["buy_slippage"]) + COLCHON_POR_POSICION
        # Y si no cabe, la regla del 75%: el bot no se salta la moneda, compra
        # con esa fraccion de lo que quede. Es justo lo que hace que la ULTIMA
        # plaza salga mas pequena en vez de no salir.
        _f = t.get("fraccion_si_no_llega", 0)
        if necesita > libre and _f > 0 and libre > 0:
            recorte = libre * _f
            if t["tope_compra_sol"] > 0:
                recorte = min(recorte, t["tope_compra_sol"])
            nec = recorte * (1 + t["buy_slippage"]) + COLCHON_POR_POSICION
            bajo = t["compra_minima_sol"] > 0 and recorte < t["compra_minima_sol"]
            if recorte > 0 and nec <= libre and not bajo:
                imp, necesita = recorte, nec
        if necesita > libre:
            break
        salen.append(imp)
        libre -= necesita
    return salen


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
    libre = 1.0 - t["min_reserve_sol"]
    pedido = libre * t["fraccion_saldo"] if t["fraccion_saldo"] > 0 else t["buy_amount"]
    tope = t["tope_compra_sol"]
    assert tope == 0 or pedido <= tope, (
        f"el tope ({tope}) recortaria la compra ({pedido:.3f}) sin avisar"
    )


def test_con_el_25_por_ciento_nunca_se_llena_el_saldo() -> None:
    """La propiedad que de verdad protege: cada compra sale de lo que QUEDA,
    asi que por muchas plazas que haya nunca se gasta todo.

    Con tamano fijo esto no era cierto -- por eso hacia falta el guardian del
    saldo con su colchon. Ahora el propio tamano se encoge solo.
    """
    t = _trade()
    libre = 1.0 - t["min_reserve_sol"]
    gastado = 0.0
    for _ in range(t["max_posiciones"]):
        imp = min(libre * t["fraccion_saldo"], t["tope_compra_sol"])
        if imp < t["compra_minima_sol"]:
            break
        gastado += imp
        libre -= imp * (1 + t["buy_slippage"]) + COLCHON_POR_POSICION
    assert libre > 0, "se ha gastado hasta el ultimo lamport"
    assert gastado < 1.0 - t["min_reserve_sol"]


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


def test_con_saldo_justo_la_ultima_plaza_sale_mas_pequena() -> None:
    """Con importe fijo, un saldo corto se nota en la ULTIMA compra.

    Con 0,25 SOL entran cuatro de 0,04 y para la quinta ya no queda; ahi actua
    la regla del 75%, que compra con lo que reste en vez de saltarse la
    moneda. Por eso salen cinco y la ultima es mas chica.
    """
    tam = tamanos(0.25)
    assert len(tam) == TECHO
    assert tam[-1] < tam[0], f"la ultima deberia ser mas pequena: {tam}"
    assert len(tamanos(0.05)) >= 1, "con poco saldo compra poco, pero compra"


def test_el_techo_de_cinco_sigue_mandando() -> None:
    """Por mucho saldo que haya, nunca mas de cinco a la vez: cada posicion
    abierta consulta su precio cada segundo y el presupuesto del nodo no es
    infinito."""
    assert len(tamanos(50.0)) == TECHO


def test_nunca_se_gasta_la_reserva() -> None:
    """Lo unico que queda protegiendo el saldo, ahora que no hay tope ni suelo.

    Sin reserva no habria gas para VENDER lo comprado, y una posicion sin
    salida vale cero.
    """
    t = _trade()
    for saldo in (0.05, 0.25, 1.0, 5.0):
        gastado = sum(x * (1 + t["buy_slippage"]) + COLCHON_POR_POSICION
                      for x in tamanos(saldo))
        assert gastado <= saldo - t["min_reserve_sol"] + 1e-9, (
            f"con {saldo} SOL se gastarian {gastado:.4f} y hay que dejar "
            f"{t['min_reserve_sol']} de reserva"
        )
