"""El tope de posiciones abiertas lo pone el SALDO, no un numero fijo.

Con max_posiciones: 0 el semaforo deja de ser el freno y quien decide es
el guardian del saldo, que antes de cada compra exige:

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
# Techo duro del semaforo cuando max_posiciones es 0.
TECHO_DEL_CODIGO = 20


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def caben(saldo: float, reserva: float, compra: float) -> int:
    """Cuantas posiciones caben en un saldo, dejando la reserva intacta."""
    libre = saldo - reserva
    if libre <= 0:
        return 0
    return min(int(libre // (compra + COLCHON_POR_POSICION)), TECHO_DEL_CODIGO)


def test_no_hay_tope_fijo_de_posiciones() -> None:
    """0 = que mande el saldo, que es lo pedido."""
    assert _trade()["max_posiciones"] == 0


def test_la_reserva_sigue_siendo_la_pedida() -> None:
    assert _trade()["min_reserve_sol"] == 0.03


def test_un_saldo_mayor_abre_mas_posiciones() -> None:
    cfg = _trade()
    reserva, compra = cfg["min_reserve_sol"], cfg["tope_compra_sol"]
    assert caben(0.5, reserva, compra) == 2
    assert caben(1.0, reserva, compra) == 4
    assert caben(2.0, reserva, compra) == 9


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


def test_el_techo_del_codigo_no_se_alcanza_con_saldo_pequeno() -> None:
    """Avisa si alguien baja tope_compra_sol y el techo de 20 pasa a morder."""
    cfg = _trade()
    reserva, compra = cfg["min_reserve_sol"], cfg["tope_compra_sol"]
    assert caben(1.0, reserva, compra) < TECHO_DEL_CODIGO
