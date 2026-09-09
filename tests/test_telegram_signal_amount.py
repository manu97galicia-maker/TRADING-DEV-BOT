"""Tests for Telegram acquisition sizing."""

# ruff: noqa: PLR2004, S101

from monitoring.telegram_source import _telegram_signal_amount

CONFIG = {
    "normal_buy_amount": 0.15,
    "nitro_buy_amount": 0.17,
    "promoted_buy_amount": 0.20,
    "promotion_threshold": 500,
}


def test_normal_listing_uses_normal_amount() -> None:
    amount, score = _telegram_signal_amount("New Solana listing", CONFIG)

    assert amount == 0.15
    assert score == 0


def test_nitro_500_uses_promoted_amount() -> None:
    amount, score = _telegram_signal_amount("NITRO score: 500", CONFIG)

    assert amount == 0.20
    assert score == 500


def test_nitro_200_uses_small_promotion_amount() -> None:
    amount, score = _telegram_signal_amount("NITRO 200", CONFIG)

    assert amount == 0.17
    assert score == 200


def test_nitro_channel_without_score_uses_small_promotion_amount() -> None:
    amount, score = _telegram_signal_amount(
        "New promotion", CONFIG, source="dextools_nitro"
    )

    assert amount == 0.17
    assert score == 0
