"""9-nov: "misma estrategia pero compra en el bloque dos de este tipo de
inicios" (variante de la mayhem rapida)."""

# ruff: noqa: S101, PLR2004, ANN001, ANN201, ANN202

from pathlib import Path

import test_tp_sl_en_vivo as tv
import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

RAIZ = Path(__file__).resolve().parents[1]
YAML = "bots/bot-real-mayhembloque2.yaml"


def _cfg(f=YAML):
    return yaml.safe_load((RAIZ / f).read_text(encoding="utf-8"))


def test_entra_en_el_bloque_dos():
    fl = _cfg()["filters"]
    assert fl["require_mayhem"] is True and fl["bro_address"] is None
    assert fl["buy_from_second_block"] is True and fl["max_buy_age"] == 3
    assert fl["min_first_block_buys"] == 1 and fl["max_first_block_buys"] == 5
    assert fl["min_first_block_sol"] == 0.4  # "las que el dev compre 0.4 sol"
    # En el bloque dos no hay volumen, subida ni presion que medir.
    for k in ("min_buy_age", "min_volumen_sol", "min_ops", "ultimas_tx_compra"):
        assert not fl.get(k), k
    assert not fl.get("min_presion_ratio") and fl["min_entry_mc"] == 0
    assert fl["max_dev_hold_pct"] == 0.10


def test_mismo_tamano_y_stop_que_la_rapida():
    a, b = _cfg()["trade"], _cfg("bots/bot-real-mayhemrapida.yaml")["trade"]
    for k in (
        "buy_amount",
        "tope_compra_sol",
        "stop_loss_percentage",
        "margen_stop_tras_tp",
        "max_hold_time",
        "max_posiciones",
        "max_perdidas_seguidas",
        "min_reserve_sol",
    ):
        assert a[k] == b[k], k
    assert a["nombre_estrategia"] == "real-mayhembloque2"
    # 9-nov: "toma iniciales 35% y luego en dos ventas objetivo 150% roi".
    assert [a["take_profit_percentage"], a["tp2_gain"], a["tp3_gain"]] == [
        0.35,
        0.9,
        1.5,
    ]
    assert not a.get("tp4_gain")
    assert abs(a["take_profit_sell_percentage"] * 1.35 - 1) < 1e-6
    total = a["take_profit_sell_percentage"] + a["tp2_sell"] + a["tp3_sell"]
    assert abs(total - 1) < 1e-9


def test_escalera(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "MC_ENTRADA", 2500.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.36, 1.91, 2.51])
    assert [round(v[1], 4) for v in ventas] == [0.7407, 0.1296, 0.1296]
    # Tras el +35% el stop esta en compra +20%.
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.36, 1.25, 1.19])
    assert len(ventas) == 2 and ventas[1][2] == 1.19


def test_el_trader_recibe_el_minimo_en_sol(tmp_path, monkeypatch):
    t, _ut = _construir(YAML, tmp_path, monkeypatch)
    assert t.min_first_block_sol == 0.4 and t.buy_from_second_block is True
