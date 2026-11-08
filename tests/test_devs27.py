"""8-nov: "monta una estrategia para los de ultimos" -> copiar a los 27 devs
del estudio de Dune (commit 8b90317), en real."""

# ruff: noqa: S101, PLR2004, ANN001, ANN201, ANN202

from pathlib import Path

import test_tp_sl_en_vivo as tv
import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

from trading.universal_trader import _normalize_creators

RAIZ = Path(__file__).resolve().parents[1]
YAML = "bots/bot-real-devs27.yaml"


def _cfg():
    return yaml.safe_load((RAIZ / YAML).read_text(encoding="utf-8"))


def test_config_devs27():
    c = _cfg()
    fl, t = c["filters"], c["trade"]
    assert fl["paper_trade"] is False and fl["listener_type"] == "logs"
    assert fl["bro_address"] == "devs_dune_27.json"
    assert fl["curva_96"]["enabled"] is False
    assert fl["max_buy_age"] == 0  # compra al nacer
    # Frenos anti-rug como la otra.
    assert fl["max_dev_hold_pct"] == 0.10 and fl["allow_mayhem"] is False
    # Stop en %: el de 7.400 $ venderia al instante una moneda recien nacida.
    assert t["stop_loss_percentage"] == 0.40 and t["mc_stop_loss"] == 0
    # 8-nov: 60% del portfolio, prioridad de compra 0,02 SOL, sin trailing.
    assert t["fraccion_saldo"] == 0.60 and t["fraccion_base"] == "cartera"
    assert t["trailing_stop_percentage"] == 0
    pf = c["priority_fees"]
    assert pf["fixed_amount_compra"] * 100_000 / 1e15 == 0.02
    assert pf["hard_cap"] >= pf["fixed_amount_compra"]
    # x3 recupera lo invertido; 30k/50k/75k/100k; 5% a 240k; 10% a los suelos.
    assert t["take_profit_percentage"] == 2.0
    assert abs(t["take_profit_sell_percentage"] * 3 - 1) < 1e-6
    mc = [(x["target_mc"], x["sell"]) for x in t["mc_take_profit_tiers"]]
    assert [m for m, _ in mc] == [30000, 50000, 75000, 100000, 240000]
    vendido = t["take_profit_sell_percentage"] + sum(v for _, v in mc)
    assert abs(vendido - 0.90) < 1e-9
    assert t["suelos_por_mc"] == [
        {"alcanza": 240000, "suelo": 200000},
        {"alcanza": 1000000, "suelo": 450000},
    ]
    assert t["max_posiciones_total"] == 2 and t["max_perdidas_seguidas"] == 3
    assert t["nombre_estrategia"] == "real-devs27"


def test_la_lista_son_los_27_devs(monkeypatch):
    monkeypatch.chdir(RAIZ)
    devs = _normalize_creators("devs_dune_27.json")
    assert devs is not None and len(devs) == 27
    assert "HnCotNeUiHYqztXzvHrH4HhxHSvrdYyytWQv7FtsZLHs" in devs
    assert "34L8WzCzgsr6NagqU3tUKLWetg1JGaENF1NYmdYxqkHX" in devs  # 100 boosts


# Entrada de moneda recien nacida a 4.000 $: x3 = 12k, 30k = x7,5, 50k = x12,5,
# 75k = x18,75, 100k = x25, 240k = x60, 1M = x250, 450k = x112,5, 200k = x50.
_ESCALERA = [1.0, 3.01, 7.6, 12.6, 18.8, 25.1, 60.1]
_MOTIVOS = [
    "tp_tier1_+200%",
    "mc_tier_$30000",
    "mc_tier_$50000",
    "mc_tier_$75000",
    "mc_tier_$100000",
    "mc_tier_$240000",
]


def _con_escalera(tmp_path, monkeypatch, camino):
    monkeypatch.setattr(tv, "MC_ENTRADA", 4000.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, camino, max_s=60)
    return ventas


def test_pasa_el_millon_y_el_ultimo_10_sale_en_450k(tmp_path, monkeypatch):
    ventas = _con_escalera(
        tmp_path, monkeypatch, [*_ESCALERA, 150.0, 250.1, 200.0, 112.0]
    )
    assert [v[0] for v in ventas] == [*_MOTIVOS, "suelo_por_mc"]
    assert abs(ventas[0][1] - 1 / 3) < 1e-3  # recupera lo invertido
    assert abs(ventas[-1][1] - 0.10) < 1e-3 and ventas[-1][2] == 112.0
    assert abs(sum(v[1] for v in ventas) - 1.0) < 1e-3


def test_sin_millon_el_ultimo_10_sale_antes_de_200k(tmp_path, monkeypatch):
    ventas = _con_escalera(tmp_path, monkeypatch, [*_ESCALERA, 70.0, 49.0])
    assert [v[0] for v in ventas] == [*_MOTIVOS, "suelo_por_mc"]
    assert abs(ventas[-1][1] - 0.10) < 1e-3 and ventas[-1][2] == 49.0


def test_tras_x3_el_stop_esta_en_compra_mas_5(tmp_path, monkeypatch):
    ventas = _con_escalera(tmp_path, monkeypatch, [1.0, 3.01, 2.0, 1.04])
    assert ventas[0][0] == "tp_tier1_+200%" and len(ventas) == 2
    assert ventas[1][2] == 1.04 and abs(ventas[1][1] - 2 / 3) < 1e-3


def test_antes_de_x3_stop_a_menos_40(tmp_path, monkeypatch):
    """Con el stop de 7.400 $ de la otra estrategia venderia nada mas comprar."""
    ventas = _con_escalera(tmp_path, monkeypatch, [1.0, 1.0, 1.5, 0.7, 0.59])
    assert ventas and ventas[0][0] == "stop_loss" and ventas[0][2] == 0.59
    assert abs(ventas[0][1] - 1.0) < 1e-3
