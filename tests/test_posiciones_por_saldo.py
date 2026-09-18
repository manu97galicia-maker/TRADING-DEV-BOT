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


def test_ahora_el_tamano_sale_del_saldo_no_del_tope() -> None:
    """EL CAMBIO DEL 19-SEP: se compra el 25% de lo libre, no una cantidad fija.

    Los dos tests que habia aqui modelaban posiciones de tamano FIJO
    (`tope_compra_sol`) y contaban cuantas cabian. Ese modelo ya no describe al
    bot: `tope_compra_sol` paso a ser un freno contra fallos y quien decide el
    tamano es `fraccion_saldo`. Mantenerlos habria sido peor que borrarlos --
    verde sobre un comportamiento que el bot ya no tiene.
    """
    t = _trade()
    assert t["fraccion_saldo"] > 0, "si esto es 0, vuelve a mandar el importe fijo"
    # Y el tope tiene que quedar por ENCIMA del 25% en saldos normales, o
    # recortaria sin avisar y volveriamos al tamano fijo por la puerta de atras.
    libre = 1.0 - t["min_reserve_sol"]
    assert libre * t["fraccion_saldo"] <= t["tope_compra_sol"]


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


def test_con_saldo_pequeno_manda_el_saldo_y_no_el_techo() -> None:
    """Sigue habiendo un saldo por debajo del cual manda la cartera.

    Con 0,495 por compra ese punto estaba en 1 SOL. Con 0,07 hay que bajar
    mucho mas para encontrarlo: con 0,25 SOL de saldo (menos la reserva de
    0,03) solo caben tres. El freno existe, esta mas abajo.
    """
    cfg = _trade()
    reserva, compra = cfg["min_reserve_sol"], cfg["tope_compra_sol"]
    assert caben(0.25, reserva, compra) < TECHO


def test_el_techo_muerde_cuando_sobra_saldo() -> None:
    """Con 5 SOL cabrian 12 por saldo, pero el techo las deja en 5."""
    cfg = _trade()
    reserva, compra = cfg["min_reserve_sol"], cfg["tope_compra_sol"]
    libre = 5.0 - reserva
    assert int(libre // (compra + COLCHON_POR_POSICION)) > TECHO
    assert caben(5.0, reserva, compra) == TECHO


def test_saldo_a_partir_del_cual_compra_algo() -> None:
    """El numero que se pregunta de verdad: "por que no compra nada?".

    Con el 25%, por debajo de cierto saldo la compra sale por debajo del minimo
    que compensa y el bot se queda quieto. NO es un fallo -- esa compra naceria
    perdiendo, porque las comisiones fijas no bajan con el tamano.

        umbral = reserva + minimo / fraccion
    """
    t = _trade()
    umbral = t["min_reserve_sol"] + t["compra_minima_sol"] / t["fraccion_saldo"]
    assert round(umbral, 3) == 0.110

    # Justo por debajo no compra; justo por encima, si.
    def primera(saldo: float) -> float:
        return min((saldo - t["min_reserve_sol"]) * t["fraccion_saldo"],
                   t["tope_compra_sol"])

    assert primera(umbral - 0.001) < t["compra_minima_sol"]
    assert primera(umbral + 0.001) >= t["compra_minima_sol"]


