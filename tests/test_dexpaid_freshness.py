"""Tests ensuring DexScreener signals are new events, not old volume."""

# ruff: noqa: S101

from monitoring.dexpaid_source import DexPaidFreshnessTracker


def _candidate(mint: str, boosts: float = 0, signal_type: str = "paid") -> dict:
    return {"mint": mint, "boosts": boosts, "signal_type": signal_type}


def test_initial_snapshot_is_only_a_baseline() -> None:
    tracker = DexPaidFreshnessTracker()
    tracker.prime([_candidate("old", 100, "boost")])

    assert not tracker.accept(_candidate("old", 100, "boost"))


def test_new_boost_mint_is_accepted_once() -> None:
    tracker = DexPaidFreshnessTracker()
    tracker.prime([_candidate("old", 100, "boost")])

    assert tracker.accept(_candidate("new", 10, "boost"))
    assert not tracker.accept(_candidate("new", 10, "boost"))


def test_boost_increase_is_a_new_signal() -> None:
    tracker = DexPaidFreshnessTracker()
    tracker.prime([_candidate("mint", 10, "boost")])

    assert tracker.accept(_candidate("mint", 50, "boost"))


def test_new_paid_profile_is_accepted_once() -> None:
    tracker = DexPaidFreshnessTracker()
    tracker.prime([_candidate("old")])

    assert tracker.accept(_candidate("new"))
    assert not tracker.accept(_candidate("new"))
