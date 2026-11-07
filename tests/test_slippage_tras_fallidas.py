"""7-nov: "si hay tres fallidas sube el slippage a 14% y prioridad de venta 0.006"."""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN201

import pytest
from test_tp_sl_en_vivo import _construir

PRESION = ["bots/bot-real-presion6k.yaml", "bots/bot-real-presion15k.yaml"]


@pytest.mark.parametrize("yaml_rel", PRESION)
def test_tres_fallidas_seguidas_suben_a_14(yaml_rel, tmp_path, monkeypatch):
    t, _ = _construir(yaml_rel, tmp_path, monkeypatch)
    assert t.buy_slippage == 0.09 and t.buyer.slippage == 0.09
    t._anota_compra_para_slippage(ok=False)
    t._anota_compra_para_slippage(ok=False)
    assert t.buy_slippage == 0.09  # dos no bastan
    t._anota_compra_para_slippage(ok=False)
    assert t.buy_slippage == 0.14 and t.buyer.slippage == 0.14
    t._anota_compra_para_slippage(ok=True)
    assert t.buy_slippage == 0.14  # se queda hasta reiniciar


@pytest.mark.parametrize("yaml_rel", PRESION)
def test_una_buena_en_medio_pone_la_cuenta_a_cero(yaml_rel, tmp_path, monkeypatch):
    t, _ = _construir(yaml_rel, tmp_path, monkeypatch)
    for ok in (False, False, True, False, False):
        t._anota_compra_para_slippage(ok=ok)
    assert t.buy_slippage == 0.09


def test_sin_configurar_no_cambia_nada(tmp_path, monkeypatch):
    t, _ = _construir("bots/bot-real-curva92.yaml", tmp_path, monkeypatch)
    antes = t.buy_slippage
    for _ in range(10):
        t._anota_compra_para_slippage(ok=False)
    assert t.buy_slippage == antes


@pytest.mark.parametrize("yaml_rel", PRESION)
def test_prioridad_de_venta_0006(yaml_rel, tmp_path, monkeypatch):
    """60.000 unidades x 100.000.000 microlamports = 0,006 SOL, normal y stop."""
    t, _ = _construir(yaml_rel, tmp_path, monkeypatch)
    assert t.fee_venta_proteccion == 100_000_000
    pfm = t.priority_fee_manager
    fijo = getattr(pfm, "fixed_fee", None) or getattr(pfm, "fixed_priority_fee", None)
    assert fijo == 100_000_000
    assert fijo * 60_000 / 1e6 / 1e9 == pytest.approx(0.006)
    assert t.fee_compra * 100_000 / 1e6 / 1e9 == pytest.approx(0.007)
