"""Tests for DexScreener paid/boost acquisition sizing."""

# ruff: noqa: PLR2004, S101

from monitoring.dexpaid_source import _dexpaid_signal_amount


def test_plain_paid_listing_uses_normal_amount() -> None:
    assert _dexpaid_signal_amount(0, 0.15, 0.17, 500, 0.20) == 0.15


def test_small_boost_uses_middle_amount() -> None:
    assert _dexpaid_signal_amount(100, 0.15, 0.17, 500, 0.20) == 0.17


def test_expensive_boost_uses_high_amount() -> None:
    assert _dexpaid_signal_amount(500, 0.15, 0.17, 500, 0.20) == 0.20
