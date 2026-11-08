"""8-nov: "estrategia mayhem agresiva pero con alto roi tipo entre 400-2000%
... arriesgando 0.04 sol" -> "57 devs" y stop "-70%"."""

# ruff: noqa: S101, PLR2004, ANN001, ANN201, ANN202

import csv
from pathlib import Path

import test_tp_sl_en_vivo as tv
import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

from trading.universal_trader import _normalize_creators

RAIZ = Path(__file__).resolve().parents[1]
YAML = "bots/bot-real-mayhem2000.yaml"
SCRIPT = (RAIZ / "deploy/arranca-tres-real.sh").read_text(encoding="utf-8")


def _cfg():
    return yaml.safe_load((RAIZ / YAML).read_text(encoding="utf-8"))


def test_config():
    c = _cfg()
    t, fl = c["trade"], c["filters"]
    assert fl["paper_trade"] is False and fl["listener_type"] == "logs"
    assert fl["allow_mayhem"] is True and fl["require_mayhem"] is True
    assert fl["bro_address"] == "devs_mayhem57.json"
    assert fl["curva_96"]["enabled"] is False and fl["max_buy_age"] == 0
    # 0,04 SOL fijos, con tope duro.
    assert t["buy_amount"] == 0.04 and t["tope_compra_sol"] == 0.04
    assert t["fraccion_saldo"] == 0 and t["buy_amount_usd"] == 0
    # x5 / x10 / x15 / x21, un 25% cada una; stop -70%; sin trailing.
    escalera = [
        (t["take_profit_percentage"], t["take_profit_sell_percentage"]),
        (t["tp2_gain"], t["tp2_sell"]),
        (t["tp3_gain"], t["tp3_sell"]),
        (t["tp4_gain"], t["tp4_sell"]),
    ]
    assert escalera == [(4.0, 0.25), (9.0, 0.25), (14.0, 0.25), (20.0, 0.25)]
    assert t["stop_loss_percentage"] == 0.70 and t["mc_stop_loss"] == 0
    assert t["trailing_stop_percentage"] == 0 and t["venta_muerta_seg"] == 0
    assert not t.get("suelos_por_mc") and not t.get("mc_take_profit_tiers")
    assert t["vender_antes_de_edad_seg"] == 82800  # antes de que acabe
    assert not t.get("barrido_restos")  # lo hace la de los 27 devs
    # Prioridad de compra 0,002 SOL (no la de 0,02 de los 27 devs).
    assert c["priority_fees"]["fixed_amount_compra"] == 20_000_000


def test_los_57_devs(monkeypatch):
    filas = list(csv.DictReader((RAIZ / "mayhem_v2_raw.csv").open()))
    esperados = {
        f["wallet"]
        for f in filas
        if float(f["mayhem_launches"]) >= 2 and float(f["pct_pump_100k"]) >= 30
    }
    monkeypatch.chdir(RAIZ)
    devs = _normalize_creators("devs_mayhem57.json")
    assert devs == esperados and len(devs) == 57


def test_escalera_y_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "MC_ENTRADA", 3000.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 5.01, 10.01, 15.01, 21.01])
    assert [round(v[1], 2) for v in ventas] == [0.25, 0.25, 0.25, 0.25]
    assert [v[2] for v in ventas] == [5.01, 10.01, 15.01, 21.01]
    # Antes del x5: solo vende si cae mas de un 70%.
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 0.6, 0.35, 0.29])
    assert ventas and ventas[0][0] == "stop_loss" and ventas[0][2] == 0.29
    # Tras el x5: el resto sale en compra +5% si se da la vuelta.
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 5.01, 3.0, 1.04])
    assert len(ventas) == 2 and ventas[1][2] == 1.04


def test_se_puede_arrancar_sola():
    assert 'ESTRATEGIAS="$*"' in SCRIPT
    assert 'bot-real-mayhem2000) echo "mayhem2000_real.log"' in SCRIPT
