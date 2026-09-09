"""Tests for the periodic DexScreener monitor status message."""

# ruff: noqa: S101

from monitoring.dexpaid_source import build_monitor_heartbeat


def test_heartbeat_explains_monitor_status() -> None:
    message = build_monitor_heartbeat(
        poll_interval=30,
        fresh_signals=2,
        max_payment_age_minutes=10,
        max_token_age_minutes=30,
        paper_mode=True,
    )

    assert "MONITOR ACTIVO" in message
    assert "PAPER" in message
    assert "2" in message
    assert "30s" in message
    assert "pago ≤10m" in message
