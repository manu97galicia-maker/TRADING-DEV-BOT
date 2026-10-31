"""Guardas de configuración para la instancia real UNICA123."""

# ruff: noqa: I001, PLR2004, S101

from pathlib import Path
from math import isclose

import yaml

from trading.tiered_exit import build_tiers, stepped_stop_price


CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-unica123-real.yaml"


def test_unica123_real_dispara_al_93_por_ciento() -> None:
    """La instancia queda activa y escucha únicamente la ventana acordada."""
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    curva = config["filters"]["curva_96"]

    assert config["enabled"] is True
    assert config["filters"]["paper_trade"] is False
    assert curva["enabled"] is True
    assert curva["umbral"] == 0.93
    assert curva["tope"] == 0.995


def test_unica123_real_reparte_la_salida_completa() -> None:
    """Vende 40/50/7/1,5/1,5 y mueve el stop como se acordó."""
    trade = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]
    porcentaje = [
        (trade["take_profit_percentage"], trade["take_profit_sell_percentage"]),
        (trade["tp2_gain"], trade["tp2_sell"]),
    ]
    market_cap = trade["mc_take_profit_tiers"]

    assert porcentaje == [(0.35, 0.40), (0.73, 0.50)]
    assert market_cap == [
        {"target_mc": 200000, "sell": 0.07},
        {"target_mc": 600000, "sell": 0.015},
        {"target_mc": 900000, "sell": 0.015},
    ]
    assert isclose(
        sum(sell for _, sell in porcentaje) + sum(tier["sell"] for tier in market_cap),
        1.0,
    )
    assert trade["stop_a_entrada_tras_tp"] is True
    assert trade["stop_loss_percentage"] == 0.30
    assert trade["margen_stop_tras_tp"] == 0
    assert trade["stop_al_tp_tras_tp"] is False

    tiers = build_tiers(porcentaje)
    assert stepped_stop_price(1.0, tiers, 0, 0) == 1.0
    assert stepped_stop_price(1.0, tiers, 1, 0) == 1.35


def test_unica123_real_compra_el_20_por_ciento_del_sol_libre() -> None:
    """El tamaño se recalcula en cada compra sin agotar la reserva."""
    trade = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]

    assert trade["fraccion_saldo"] == 0.20
    assert trade["fraccion_base"] == "libre"
    assert trade["tope_compra_sol"] == 0.20
    assert trade["max_real_buys"] == 0
    assert trade["max_posiciones"] == 2


def test_unica123_real_exige_holders_presion_y_sin_mecha() -> None:
    """La curva sola no basta para autorizar una entrada real."""
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    filters = config["filters"]
    curva = filters["curva_96"]

    assert filters["min_holders"] == 5
    assert curva["min_compradores"] == 8
    assert curva["min_compradores_ola"] == 3
    assert curva["ventana_presion"] == 5.0
    assert curva["min_presion_ratio"] == 1.5
    assert curva["min_compra_sol"] == 0.20
    assert curva["vela_seg"] == 1.0
    assert curva["max_subida_vela"] == 0.35
