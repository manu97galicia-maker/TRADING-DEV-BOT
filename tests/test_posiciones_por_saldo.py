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


def test_un_saldo_mayor_abre_mas_posiciones() -> None:
    """Numeros a pelo a proposito: si alguien cambia tope_compra_sol, este
    test se pone rojo y le obliga a mirar cuantas posiciones pierde.

    Historial de esa cuenta, que es lo que hay que ver de un vistazo:
        0,20 -> 2/4/9   con 0,5 / 1 / 2 SOL
        0,28 -> 1/3/6   (+40%)
        0,38 -> 1/2/5   (+36% mas)
    """
    cfg = _trade()
    reserva, compra = cfg["min_reserve_sol"], cfg["tope_compra_sol"]
    assert caben(0.5, reserva, compra) == 1
    assert caben(1.0, reserva, compra) == 2
    assert caben(2.0, reserva, compra) == 5


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
    """Con 1 SOL el freno es la cartera, no el 5: caben 2."""
    cfg = _trade()
    reserva, compra = cfg["min_reserve_sol"], cfg["tope_compra_sol"]
    assert caben(1.0, reserva, compra) < TECHO


def test_el_techo_muerde_cuando_sobra_saldo() -> None:
    """Con 5 SOL cabrian 12 por saldo, pero el techo las deja en 5."""
    cfg = _trade()
    reserva, compra = cfg["min_reserve_sol"], cfg["tope_compra_sol"]
    libre = 5.0 - reserva
    assert int(libre // (compra + COLCHON_POR_POSICION)) > TECHO
    assert caben(5.0, reserva, compra) == TECHO


def test_saldo_necesario_para_las_cinco() -> None:
    """El numero que hay que tener en la cartera para que quepan las 5.

    Se fija aqui porque es la pregunta que se hace de verdad: "por que solo
    abre dos si puse 5?". La respuesta es esta cuenta, no un fallo.

    OJO AL FILO: en el saldo EXACTO el resultado NO es de fiar. La division
    da 5,000000 justo, y que el suelo caiga en 4 o en 5 depende de como
    redondee el binario esa compra en concreto: con 0,3808 y 0,2856 salen 4,
    con 0,20 salen 5. No es una regla, es suerte. Un pelo por encima del
    minimo salen 5 siempre, asi que hay que dejar margen y no fiarse del
    numero justo. Redondear hacia arriba seria peor: abriria una posicion
    sin SOL para cerrarla.
    """
    cfg = _trade()
    reserva = cfg["min_reserve_sol"]
    for compra, hace_falta in (
        (cfg["tope_compra_sol"], 1.949),
        (cfg["buy_amount"], 1.473),
        (0.20, 1.045),
    ):
        justo = reserva + TECHO * (compra + COLCHON_POR_POSICION)
        assert round(justo, 3) == hace_falta
        # En el filo puede dar 4 o 5; lo que NO puede es pasarse.
        assert caben(justo, reserva, compra) in (TECHO - 1, TECHO)
        # Con un margen minimo, las 5 siempre.
        assert caben(justo + 0.01, reserva, compra) == TECHO
