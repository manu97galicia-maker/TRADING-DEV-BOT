"""Cuanto se compra segun los boosts pagados y de donde viene la moneda.

Importes cerrados en SOL, no fracciones:

                         pump.fun/PumpSwap   Raydium y otros
    hasta 50 boosts            0,25               0,18
    mas de 50 boosts           0,30               0,20

Y pagar el perfil Y ADEMAS boosts sube un 20% sobre lo que toque.
"""

# ruff: noqa: S101, PLR2004, PLC0415

from pathlib import Path

from monitoring.dexpaid_source import (
    FACTOR_PAGO_Y_BOOST,
    TRAMOS_IMPORTE,
    importe_por_senal,
)


def test_un_pago_sin_boost_va_en_el_primer_tramo() -> None:
    """Es la senal mas floja que se compra: no puede pagar mas que una con
    boosts."""
    assert importe_por_senal(0) == 0.25


def test_hasta_cincuenta_boosts_es_el_primer_tramo() -> None:
    assert importe_por_senal(1) == 0.25
    assert importe_por_senal(50) == 0.25


def test_por_encima_de_cincuenta_sube() -> None:
    assert importe_por_senal(51) == 0.30
    assert importe_por_senal(5000) == 0.30


def test_una_de_raydium_compra_menos_en_cada_tramo() -> None:
    for boosts in (0, 50, 51, 900):
        assert importe_por_senal(boosts, de_fuera=True) < importe_por_senal(boosts)


def test_los_importes_de_raydium_son_los_pedidos() -> None:
    assert importe_por_senal(0, de_fuera=True) == 0.18
    assert importe_por_senal(51, de_fuera=True) == 0.20


def test_pagar_perfil_y_ademas_boosts_sube_un_veinte_por_ciento() -> None:
    """Son dos gastos distintos: cuenta mas que cualquiera por separado."""
    sin = importe_por_senal(75)
    con = importe_por_senal(75, pago_y_boost=True)
    assert round(con / sin, 4) == FACTOR_PAGO_Y_BOOST


def test_los_tramos_nunca_bajan_al_subir_los_boosts() -> None:
    """Pagar mas no puede hacer que se invierta menos."""
    for clave in ("pumpfun", "otros"):
        valores = [float(t[clave]) for t in TRAMOS_IMPORTE]
        assert valores == sorted(valores)


def test_el_ultimo_tramo_recoge_todo_lo_que_queda() -> None:
    """Con `hasta: 0` no hay boosts tan altos que se queden sin importe."""
    assert TRAMOS_IMPORTE[-1]["hasta"] == 0
    assert importe_por_senal(10**9) == TRAMOS_IMPORTE[-1]["pumpfun"]


def test_el_descuento_de_procedencia_va_solo_en_la_tabla() -> None:
    """Si ademas se aplicara el factor de origen fuera, se descontaria dos
    veces y una de Raydium compraria 0,12 en vez de 0,18."""
    import yaml

    cfg = yaml.safe_load(
        (
            Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
        ).read_text(encoding="utf-8")
    )["filters"]["buy_on_dex_paid"]
    assert cfg["otros_factor_importe"] == 1.0
