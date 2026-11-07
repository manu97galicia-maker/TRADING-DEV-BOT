"""7-nov: "si hay tres fallidas sube el slippage a 14%" y las prioridades."""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN201

import pytest
from test_tp_sl_en_vivo import _construir

PRESION = ["bots/bot-real-presion.yaml"]


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
def test_prioridades_bajas(yaml_rel, tmp_path, monkeypatch):
    """7-nov "reducir mis costes enormemente asegurando que se ejecuten":
    compra 0,002 SOL, venta de TP 0,001 SOL y venta del stop 0,002 SOL."""
    t, _ = _construir(yaml_rel, tmp_path, monkeypatch)
    pfm = t.priority_fee_manager
    fijo = getattr(pfm, "fixed_fee", None) or getattr(pfm, "fixed_priority_fee", None)
    assert fijo * 60_000 / 1e6 / 1e9 == pytest.approx(0.001, rel=1e-6)
    assert t.fee_venta_proteccion * 60_000 / 1e6 / 1e9 == pytest.approx(0.002, rel=1e-6)
    assert t.fee_compra * 100_000 / 1e6 / 1e9 == pytest.approx(0.002)


@pytest.mark.parametrize("yaml_rel", PRESION)
def test_tope_comun_de_3(yaml_rel, tmp_path, monkeypatch):
    """7-nov "maximo de posiciones abiertas 4" y despues "maximo 3": cuenta
    las posiciones de TODAS las estrategias de la carpeta."""
    t, _ = _construir(yaml_rel, tmp_path, monkeypatch)
    assert t.max_posiciones == 3 and t.max_posiciones_total == 3
    for i, quien in enumerate(["bot-real-presion", "bot-real-presion"]):
        (tmp_path / f"open_position_{quien}__Mint{i}.json").write_text("{}")
    assert t._abiertas_entre_todos() == 2 and not t._tope_comun_lleno()
    (tmp_path / "open_position_bot-otro__Mint9.json").write_text("{}")
    assert t._tope_comun_lleno()
