"""Tests for the persistent BSC duplicate-name guard."""

# Pytest relies on plain assertions for rich failure output.
# ruff: noqa: S101

from datetime import datetime, timedelta, timezone
from pathlib import Path

from bsc.name_guard import RecentNameRegistry, normalize_token_name


def test_name_normalization_catches_cosmetic_duplicates() -> None:
    assert normalize_token_name("  TILLY  Coin ") == "tilly coin"
    fullwidth_tilly = "\uff34\uff29\uff2c\uff2c\uff39"
    assert normalize_token_name(fullwidth_tilly) == "tilly"


def test_repeated_name_inside_two_hours_is_rejected(tmp_path: Path) -> None:
    registry = RecentNameRegistry(tmp_path / "names.json")
    start = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)

    assert registry.observe("TILLY", now=start).allowed
    assert not registry.observe(" tilly ", now=start + timedelta(minutes=119)).allowed


def test_name_is_allowed_after_two_hours_and_survives_restart(tmp_path: Path) -> None:
    state = tmp_path / "names.json"
    start = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    RecentNameRegistry(state).observe("TILLY", now=start)

    restarted = RecentNameRegistry(state)
    assert restarted.observe("tilly", now=start + timedelta(hours=2)).allowed
