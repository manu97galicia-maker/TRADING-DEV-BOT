"""8-nov: las 8 carteras de Manu. Snipe a las 2 "dev" al lanzar y copia de
las compras de las otras 6; las dos con 25% del portfolio, solo por debajo de
4k de market cap, +38% recupera lo invertido y el resto en 3 tandas, cada una
un 40% por encima de la anterior."""

# ruff: noqa: S101, PLR2004, ANN001, ANN201, ANN202

from itertools import pairwise
from pathlib import Path

import pytest
import test_tp_sl_en_vivo as tv
import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

from trading.universal_trader import _normalize_creators

RAIZ = Path(__file__).resolve().parents[1]
SNIPE = "bots/bot-real-snipe2devs.yaml"
COPIA = "bots/bot-real-copia6.yaml"
DEVS = {
    "5FqUo9aBjsp7QeeyN6Vi2ZmF2fjS4H5EU7wnAQwPy17z",
    "JDd3hy3gQn2V982mi1zqhNqUw1GfV2UL6g76STojCJPN",
}


def _cfg(f):
    return yaml.safe_load((RAIZ / f).read_text(encoding="utf-8"))


@pytest.mark.parametrize("f", [SNIPE, COPIA])
def test_comun(f):
    c = _cfg(f)
    t, fl = c["trade"], c["filters"]
    assert fl["paper_trade"] is False and fl["curva_96"]["enabled"] is False
    assert fl["max_entry_mc"] == 4000
    assert t["fraccion_saldo"] == 0.25 and t["fraccion_base"] == "cartera"
    assert t["stop_loss_percentage"] == 0.40 and t["mc_stop_loss"] == 0
    assert t["stop_a_entrada_tras_tp"] is True
    assert t["take_profit_percentage"] == 0.38
    assert abs(t["take_profit_sell_percentage"] * 1.38 - 1) < 1e-6
    ganancias = [1.38, 1 + t["tp2_gain"], 1 + t["tp3_gain"], 1 + t["tp4_gain"]]
    for antes, despues in pairwise(ganancias):
        assert abs(despues / antes - 1.40) < 1e-6
    resto = [t["tp2_sell"], t["tp3_sell"], t["tp4_sell"]]
    assert max(resto) - min(resto) < 1e-9
    assert abs(t["take_profit_sell_percentage"] + sum(resto) - 1) < 1e-9
    assert not t.get("suelos_por_mc") and not t.get("mc_take_profit_tiers")
    assert c["priority_fees"]["fixed_amount_compra"] == 20_000_000
    assert t["max_posiciones_total"] == 5


def test_snipe_solo_los_2_devs(monkeypatch):
    c = _cfg(SNIPE)
    assert c["filters"]["bro_address"] == "devs_snipe_2.json"
    assert not (c["filters"].get("copy_wallets") or {}).get("enabled")
    monkeypatch.chdir(RAIZ)
    assert set(_normalize_creators("devs_snipe_2.json")) == DEVS


def test_copia_las_otras_6():
    c = _cfg(COPIA)
    cw = c["filters"]["copy_wallets"]
    assert cw["enabled"] is True and cw["follow_funded"] is False
    assert len(set(cw["wallets"])) == 6 and not set(cw["wallets"]) & DEVS
    assert not c["filters"].get("bro_address")


@pytest.mark.parametrize("f", [SNIPE, COPIA])
def test_escalera_y_stop(f, tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "MC_ENTRADA", 3500.0)
    t, ut = _construir(f, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.39, 1.94, 2.71, 3.79])
    assert [v[0] for v in ventas] == [
        "tp_tier1_+38%",
        "tp_tier2_+93%",
        "tp_tier3_+170%",
        "tp_tier4_+279%",
    ]
    assert abs(sum(v[1] for v in ventas) - 1.0) < 1e-3
    t, ut = _construir(f, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.0, 0.7, 0.59])
    assert ventas and ventas[0][0] == "stop_loss" and ventas[0][2] == 0.59
