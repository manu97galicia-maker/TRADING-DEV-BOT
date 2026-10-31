"""Contrato de las calls reales de Telegram de UNICA123."""

# ruff: noqa: S101

from pathlib import Path

import yaml


CONFIG = Path(__file__).resolve().parents[1] / "bots" / "bot-unica123-real.yaml"


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_solo_estan_los_dos_canales_pedidos() -> None:
    telegram = _config()["filters"]["telegram_calls"]

    assert telegram["enabled"] is True
    assert telegram["channels"] == ["Wagbocooks", "LegendsAlphas"]
    assert telegram["suffixes"] == ["pump"]


def test_las_calls_usan_quince_por_ciento_sin_importe_fijo() -> None:
    telegram = _config()["filters"]["telegram_calls"]

    assert telegram["balance_fraction"] == 0.15
    assert telegram["normal_buy_amount"] == 0
    assert telegram["nitro_buy_amount"] == 0
    assert telegram["promoted_buy_amount"] == 0


def test_la_curva_conserva_su_veinte_por_ciento_y_los_topes() -> None:
    trade = _config()["trade"]

    assert trade["fraccion_saldo"] == 0.20
    assert trade["tope_compra_sol"] == 0.20
    assert trade["max_posiciones"] == 4
