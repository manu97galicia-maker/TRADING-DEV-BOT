"""Tests for automatic developer blocking thresholds."""

# ruff: noqa: S101

from monitoring.dev_blocklist import any_dev_blocked


def test_one_rug_blocks_when_threshold_is_one() -> None:
    assert any_dev_blocked({"dev": 1}, ["dev"], 1)


def test_clean_developer_is_not_blocked() -> None:
    assert not any_dev_blocked({"dev": 0}, ["dev"], 1)


def test_creator_identity_also_blocks() -> None:
    assert any_dev_blocked({"creator": 1}, ["user", "creator"], 1)
