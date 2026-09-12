"""Tests for the alert sent the moment a TP or the stop triggers."""

# ruff: noqa: S101

import pytest

from monitoring.notifier import (
    build_ladder_event_notification,
    build_trade_alert,
    token_label,
)
from trading.tiered_exit import Tier, ladder_progress

LADDER = [Tier(gain=0.25, sell=0.70), Tier(gain=0.50, sell=0.10)]
ENTRY = 0.001


def test_label_shows_name_and_ticker() -> None:
    assert token_label("Barron Trump", "BARRON") == "Barron Trump ($BARRON)"


def test_label_does_not_repeat_a_name_equal_to_the_ticker() -> None:
    assert token_label("BARRON", "BARRON") == "$BARRON"
    assert token_label("", "BARRON") == "$BARRON"


def test_label_escapes_html_in_a_hostile_name() -> None:
    assert "<b>" not in token_label("<b>pwn</b>", "X")


def test_nothing_sold_yet_is_pure_open_profit() -> None:
    sold, realized, total = ladder_progress(
        ENTRY, ENTRY * 1.2, LADDER, [False, False], [None, None]
    )

    assert sold == 0.0
    assert realized == 0.0
    assert total == pytest.approx(0.2)


def test_first_rung_locks_in_its_share() -> None:
    sold, realized, total = ladder_progress(
        ENTRY, ENTRY * 1.25, LADDER, [True, False], [ENTRY * 1.25, None]
    )

    assert sold == pytest.approx(0.70)
    # 70% vendido a +25% = +17.5% realizado; el 30% abierto tambien va a +25%.
    assert realized == pytest.approx(0.175)
    assert total == pytest.approx(0.25)


def test_profit_survives_a_price_collapse_after_taking_profit() -> None:
    """Tras TP1 el 70% ya esta cobrado: un desplome no lo borra."""
    _sold, realized, total = ladder_progress(
        ENTRY, ENTRY * 0.5, LADDER, [True, False], [ENTRY * 1.25, None]
    )

    assert realized == pytest.approx(0.175)
    assert total == pytest.approx(0.175 + 0.30 * -0.5)
    assert total > -0.5


def test_take_profit_alert_carries_reason_pnl_and_gmgn() -> None:
    message = build_ladder_event_notification(
        "TP1",
        "Barron Trump",
        "BARRON",
        "MintAbc",
        {"acquisition_source": "dexscreener", "dexscreener_boosts": 750},
        ENTRY * 1.25,
        ENTRY,
        0.20,
        70.0,
        30.0,
        0.25,
        new_stop_price_sol=ENTRY,
    )

    assert "TP1" in message
    assert "Barron Trump ($BARRON)" in message
    assert "DexScreener boost 750 (caro)" in message
    assert "+25.0%" in message
    assert "+0.0500 SOL" in message
    assert "gmgn.ai/sol/token/MintAbc" in message
    assert "SL protegido sube a" in message


def test_stop_alert_reports_the_loss_and_no_new_stop() -> None:
    message = build_ladder_event_notification(
        "STOP",
        "",
        "BARRON",
        "MintAbc",
        {"acquisition_source": "dextools_nitro", "dextools_nitro_score": 600},
        ENTRY * 0.82,
        ENTRY,
        0.20,
        0.0,
        100.0,
        -0.18,
    )

    assert "STOP" in message
    assert "DEXTools NITRO 600 (caro)" in message
    assert "-18.0%" in message
    assert "-0.0360 SOL" in message
    assert "Cierra el 100% que quedaba" in message
    assert "SL protegido" not in message


# --- Avisos de dinero REAL (alert_trade), que no pasan por _paper_watch ---


def test_real_sell_alert_carries_reason_move_and_pnl() -> None:
    message = build_trade_alert(
        "sell",
        "BARRON",
        0.25,  # SOL cobrados en esta venta
        "MintAbc",
        "tp_tier_+25%",
        price_sol=ENTRY * 1.25,
        name="Barron Trump",
        source_data={"acquisition_source": "dexscreener", "dexscreener_boosts": 750},
        entry_price_sol=ENTRY,
    )

    assert "Barron Trump ($BARRON)" in message
    assert "TP +25%" in message
    assert "DexScreener boost 750 (caro)" in message
    assert "(+25.0%)" in message
    # 0.25 SOL a 1.25x -> se vendieron 0.25/(E*1.25) tokens, con beneficio
    # unitario de 0.25*E: el P&L es exactamente 0.25 * (1 - 1/1.25) = 0.05.
    assert "+0.0500 SOL" in message
    assert "gmgn.ai/sol/token/MintAbc" in message


def test_real_stop_alert_shows_a_loss() -> None:
    message = build_trade_alert(
        "sell",
        "BARRON",
        0.164,
        "MintAbc",
        "stop_loss",
        price_sol=ENTRY * 0.82,
        entry_price_sol=ENTRY,
    )

    assert "STOP LOSS" in message
    assert "(-18.0%)" in message
    assert "-0.0360 SOL" in message


def test_real_buy_alert_has_no_pnl_line() -> None:
    message = build_trade_alert(
        "buy", "BARRON", 0.20, "MintAbc", price_sol=ENTRY, entry_price_sol=ENTRY
    )

    assert "COMPRA" in message
    assert "P&L" not in message


def test_sell_without_a_known_entry_omits_the_pnl() -> None:
    """Si no se apuntó el precio de entrada, mejor callar que inventar."""
    message = build_trade_alert(
        "sell", "BARRON", 0.25, "MintAbc", "stop_loss", price_sol=ENTRY
    )

    assert "P&L" not in message
    assert "STOP LOSS" in message
