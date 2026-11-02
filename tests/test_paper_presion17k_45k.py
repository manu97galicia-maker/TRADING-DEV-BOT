"""Contrato de seguridad y parametros del experimento PRESION 17K."""

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "bots" / "bot-paper-presion17k-45k.yaml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_es_exclusivamente_paper_y_nace_desactivado(cfg: dict) -> None:
    assert cfg["enabled"] is False
    assert cfg["filters"]["paper_trade"] is True
    assert cfg["trade"]["max_real_buys"] == 0


def test_entrada_exige_banda_fuerza_y_volumen(cfg: dict) -> None:
    filters = cfg["filters"]
    curve = filters["curva_96"]
    assert (filters["min_entry_mc"], filters["max_entry_mc"]) == (17000, 18000)
    assert filters["min_holders"] >= 8
    assert filters["min_volumen_sol"] >= 8
    assert filters["min_buys"] >= 12
    assert filters["min_ops"] >= 16
    assert filters["min_presion_ratio"] >= 1.6
    assert curve["min_compradores"] >= 8
    assert curve["min_presion_ratio"] >= 1.6
    assert curve["max_subida_vela"] <= 0.35


def test_stop_y_salidas_suman_toda_la_posicion(cfg: dict) -> None:
    trade = cfg["trade"]
    assert trade["stop_loss_percentage"] == 0
    assert trade["mc_stop_loss"] == 13680
    assert trade["take_profit_percentage"] == pytest.approx(24 / 17 - 1)
    assert trade["take_profit_sell_percentage"] == pytest.approx(0.73)
    assert trade["mc_take_profit_tiers"] == [
        {"target_mc": 45000, "sell": pytest.approx(0.27)}
    ]
    assert trade["take_profit_sell_percentage"] + sum(
        tier["sell"] for tier in trade["mc_take_profit_tiers"]
    ) == pytest.approx(1.0)
    assert trade["stop_a_entrada_tras_tp"] is True
