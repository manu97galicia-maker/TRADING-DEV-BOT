"""Contratos de los dos experimentos nuevos; ambos deben permanecer en paper."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent


def _cfg(name: str) -> dict:
    return yaml.safe_load((ROOT / "bots" / name).read_text(encoding="utf-8"))


def test_mayhem_usa_importe_fijo_y_lista_medida() -> None:
    cfg = _cfg("bot-paper-mayhem-24k.yaml")
    trade, filters = cfg["trade"], cfg["filters"]
    assert filters["paper_trade"] is True
    assert filters["paper_max_samples"] == 5
    assert filters["require_mayhem"] is True
    assert trade["buy_amount"] == trade["mayhem_buy_amount"] == 0.03
    assert trade["fraccion_saldo"] == 0
    assert trade["tope_compra_sol"] == 0.03
    assert filters["bro_address"] == "top_devs_mayhem_66.json"
    assert filters["min_presion_ratio"] == 1.2
    assert trade["stop_loss_percentage"] == 0.70
    assert trade["final_target_mc"] == 24000
    assert "take_profit_percentage" not in trade


def test_lista_mayhem_es_el_corte_de_3_y_66_7() -> None:
    data = yaml.safe_load((ROOT / "top_devs_mayhem_66.json").read_text(encoding="utf-8"))
    assert data["_count"] == len(data["wallets"]) == 11
    assert all(row["mayhem_launches"] >= 3 for row in data["wallets"])
    assert all(row["pct_pump_30k"] >= 66.7 for row in data["wallets"])


def test_presion71_exige_ocho_holders_presion_y_antimecha() -> None:
    cfg = _cfg("bot-paper-presion71-24k.yaml")
    trade, filters = cfg["trade"], cfg["filters"]
    curve = filters["curva_96"]
    assert filters["paper_trade"] is True
    assert filters["min_holders"] == 8
    assert curve["umbral"] == 0.71
    assert curve["min_compradores"] == 8
    assert curve["min_compradores_ola"] == 3
    assert curve["min_presion_ratio"] == 1.5
    assert curve["min_compra_sol"] == 0.20
    assert curve["vela_seg"] == 1.0
    assert curve["max_subida_vela"] == 0.35
    assert trade["fraccion_saldo"] == 0.15
    assert trade["fraccion_base"] == "libre"
    assert trade["stop_loss_percentage"] == 0.70
    assert trade["final_target_mc"] == 24000
    assert "take_profit_percentage" not in trade


def test_ningun_experimento_nuevo_puede_operar_real() -> None:
    for name in ("bot-paper-mayhem-24k.yaml", "bot-paper-presion71-24k.yaml"):
        assert _cfg(name)["filters"]["paper_trade"] is True
