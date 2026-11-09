"""9-nov: "mayhem rapida" (montarla ya y probarla en real): cualquier mayhem
entre 5 y 20 s, 0,03 SOL, +120% recupera, x3 y x5, stop -50%, a los 3 min
vende todo."""

# ruff: noqa: S101, PLR2004, ANN001, ANN201, ANN202

from pathlib import Path

import test_tp_sl_en_vivo as tv
import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

RAIZ = Path(__file__).resolve().parents[1]
YAML = "bots/bot-real-mayhemrapida.yaml"


def _cfg():
    return yaml.safe_load((RAIZ / YAML).read_text(encoding="utf-8"))


def test_config():
    c = _cfg()
    t, fl = c["trade"], c["filters"]
    assert fl["paper_trade"] is False and fl["require_mayhem"] is True
    assert fl["bro_address"] is None  # sin devs
    assert fl["curva_96"]["enabled"] is False
    assert fl["min_buy_age"] == 5 and fl["max_buy_age"] == 60
    # 9-nov "volumen, tendencia alcista y muchas operaciones" -> "media".
    assert fl["min_volumen_sol"] == 1.5 and fl["min_ops"] == 8
    assert fl["min_entry_mc"] == 4600 and fl["ultimas_tx_compra"] == 2
    assert fl["min_presion_ratio"] == 1.3 and fl["presion_seg"] == 5
    assert fl["min_first_block_buys"] == 1 and fl["max_first_block_buys"] == 5
    assert fl["max_dev_hold_pct"] == 0.10
    assert t["buy_amount"] == 0.035 and t["tope_compra_sol"] == 0.035
    assert t["fraccion_saldo"] == 0 and t["min_reserve_sol"] == 0.015
    assert t["stop_loss_percentage"] == 0.50 and t["trailing_stop_percentage"] == 0
    gan = [t["take_profit_percentage"], t["tp2_gain"], t["tp3_gain"], t["tp4_gain"]]
    assert gan == [1.2, 2.0, 4.0, 9.0]  # +120%, x3, x5, x10
    assert abs(t["take_profit_sell_percentage"] * 2.2 - 1) < 1e-6
    total = t["take_profit_sell_percentage"] + t["tp2_sell"] + t["tp3_sell"]
    total += t["tp4_sell"]
    assert abs(total - 1) < 1e-9
    assert t["margen_stop_tras_tp"] == 0.20 and t["stop_a_entrada_tras_tp"] is True
    assert t["max_hold_time"] == 120 and t["max_perdidas_seguidas"] == 0
    assert t["max_posiciones"] == 7 and t["max_real_buys"] == 0


def test_escalera(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "MC_ENTRADA", 3000.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 2.21, 3.01, 5.01, 10.01])
    assert [round(v[1], 4) for v in ventas] == [0.4545, 0.1818, 0.1818, 0.1818]


def test_stop_a_menos_50(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "MC_ENTRADA", 3000.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 0.8, 0.49])
    assert ventas and ventas[0][0] == "stop_loss" and ventas[0][2] == 0.49


def test_a_los_2_minutos_vende_todo(tmp_path, monkeypatch):
    """Ni TP ni stop: a los 120 s se vende lo que quede."""
    monkeypatch.setattr(tv, "MC_ENTRADA", 3000.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0] * 5 + [1.3] * 900, max_s=60)
    assert ventas and ventas[0][0] == "max_hold_time"
    assert abs(ventas[0][1] - 1.0) < 1e-3
