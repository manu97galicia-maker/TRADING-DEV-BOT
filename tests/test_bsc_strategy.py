"""Tests for the BSC paper strategy and gas accounting."""

# Pytest relies on plain assertions for rich failure output.
# ruff: noqa: S101

from decimal import Decimal

import pytest

from bsc.gas import RapidGasPolicy, gas_cost_bnb, gas_cost_usd
from bsc.strategy import BscLaunchStrategy, DeveloperHistory, allocated_fraction


def test_creator_requires_two_migrations_and_eight_percent() -> None:
    strategy = BscLaunchStrategy()

    assert strategy.creator_qualifies(DeveloperHistory(25, 2))
    assert not strategy.creator_qualifies(DeveloperHistory(26, 2))
    assert not strategy.creator_qualifies(DeveloperHistory(10, 1))
    assert not strategy.creator_qualifies(DeveloperHistory(1, 2))


def test_only_supported_launchpads_are_eligible() -> None:
    strategy = BscLaunchStrategy()
    history = DeveloperHistory(10, 2)

    assert strategy.should_enter("four_meme", history, Decimal("8999.99"))
    assert strategy.should_enter("flap", history, Decimal("5000"))
    assert not strategy.should_enter("other", history, Decimal("5000"))
    assert not strategy.should_enter("four_meme", history, Decimal("9000"))
    assert not strategy.should_enter("four_meme", history, Decimal("0"))


def test_combined_buy_and_sell_tax_cannot_exceed_six_percent() -> None:
    strategy = BscLaunchStrategy()
    history = DeveloperHistory(10, 2)

    assert strategy.should_enter(
        "four_meme", history, Decimal("5000"), buy_tax_bps=300, sell_tax_bps=300
    )
    assert not strategy.should_enter(
        "flap", history, Decimal("5000"), buy_tax_bps=301, sell_tax_bps=300
    )
    assert not strategy.taxes_qualify(-1, 0)


def test_exit_ladder_allocates_the_whole_original_position() -> None:
    strategy = BscLaunchStrategy()

    assert allocated_fraction(strategy.exit_orders()) == Decimal("1.000")
    assert strategy.stop_price(Decimal("100")) == Decimal("62.00")


def test_initial_recovery_is_exact_before_fees() -> None:
    strategy = BscLaunchStrategy()

    recovered = strategy.recover_fraction * (1 + strategy.recover_at_gain)
    assert recovered == Decimal("1.00000")


def test_rapid_gas_is_adaptive_and_capped() -> None:
    policy = RapidGasPolicy()

    assert policy.bid_gwei(Decimal("2"), selling=False) == Decimal("5.0")
    assert policy.bid_gwei(Decimal("2"), selling=True) == Decimal("2.50")
    assert policy.bid_gwei(Decimal("10"), selling=True) == Decimal("5")
    assert policy.buy_is_affordable(350_000, Decimal("5"), Decimal("742"))
    assert not policy.buy_is_affordable(350_000, Decimal("5"), Decimal("743"))


def test_gas_estimate() -> None:
    assert gas_cost_bnb(150_000, Decimal("2")) == Decimal("0.0003")
    assert gas_cost_usd(150_000, Decimal("2"), Decimal("743")) == Decimal("0.2229")


def test_invalid_gas_input_is_rejected() -> None:
    with pytest.raises(ValueError):
        gas_cost_bnb(0, Decimal("2"))
