"""Tests for post-buy callout generation."""

# ruff: noqa: S101

import json
from pathlib import Path

from monitoring.callout_outbox import build_callout, enqueue_callout


def test_build_callout_contains_identity_and_disclosure() -> None:
    message = build_callout("Test Coin", "MintAddress")

    assert "AI entry detected: Test Coin" in message
    assert "Contract address: MintAddress" in message
    assert "holds a position" in message


def test_enqueue_callout_writes_pending_jsonl(tmp_path: Path) -> None:
    outbox = tmp_path / "callouts.jsonl"

    enqueue_callout("Test Coin", "MintAddress", "Signature", outbox)

    entry = json.loads(outbox.read_text(encoding="utf-8"))
    assert entry["mint"] == "MintAddress"
    assert entry["buy_signature"] == "Signature"
    assert entry["status"] == "pending_authorized_publisher"
