"""Regression tests for the take-profit stop ratchet."""

import pytest

from trading.tiered_exit import Tier, stepped_stop_price

TIERS = [
    Tier(gain=0.25, sell=0.70),
    Tier(gain=0.50, sell=0.10),
    Tier(gain=0.75, sell=0.10),
    Tier(gain=2.00, sell=0.10),
]


@pytest.mark.parametrize(
    ("completed_tier", "expected_stop"),
    [
        (0, 1.00),
        (1, 1.25),
        (2, 1.50),
        (3, 1.75),
    ],
)
def test_stop_ratchets_one_rung_behind(
    completed_tier: int, expected_stop: float
) -> None:
    assert stepped_stop_price(1.0, TIERS, completed_tier) == pytest.approx(
        expected_stop
    )


def test_stop_ratchet_rejects_invalid_input() -> None:
    assert stepped_stop_price(0.0, TIERS, 0) is None
    assert stepped_stop_price(1.0, TIERS, -1) is None
    assert stepped_stop_price(1.0, [], 0) is None
