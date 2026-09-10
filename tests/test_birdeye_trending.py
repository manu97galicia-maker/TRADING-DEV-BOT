"""Tests for the Birdeye trending acquisition source."""

# ruff: noqa: S101

import asyncio

from birdeye.client import BirdeyeClient, BirdeyeTrendingToken
from monitoring.birdeye_source import (
    TrendingEntrantTracker,
    build_trending_heartbeat,
    passes_market_floor,
)


def _token(
    address: str,
    liquidity: float | None = 1000.0,
    volume: float | None = 5000.0,
) -> BirdeyeTrendingToken:
    return BirdeyeTrendingToken(
        address=address, liquidity_usd=liquidity, volume_h24=volume, rank=1
    )


def test_startup_snapshot_is_only_a_baseline() -> None:
    tracker = TrendingEntrantTracker()
    tracker.prime([_token("already-trending")])

    assert not tracker.accept(_token("already-trending"))


def test_new_entrant_is_accepted_once() -> None:
    tracker = TrendingEntrantTracker()
    tracker.prime([_token("old")])

    assert tracker.accept(_token("new"))
    assert not tracker.accept(_token("new"))


def test_entry_without_mint_is_rejected() -> None:
    assert not TrendingEntrantTracker().accept(_token(""))


def test_floors_off_accept_everything() -> None:
    assert passes_market_floor(_token("m", liquidity=1.0, volume=1.0), 0, 0)


def test_floor_rejects_below_threshold() -> None:
    assert not passes_market_floor(_token("m", liquidity=100.0), 500, 0)
    assert not passes_market_floor(_token("m", volume=100.0), 0, 500)


def test_missing_value_fails_a_configured_floor() -> None:
    assert not passes_market_floor(_token("m", liquidity=None), 500, 0)
    assert not passes_market_floor(_token("m", volume=None), 0, 500)


def test_heartbeat_reports_a_missing_api_key() -> None:
    message = build_trending_heartbeat(30, 0, 0, configured=False, paper_mode=True)

    assert "BIRDEYE_API_KEY" in message


def test_heartbeat_separates_entrants_from_buyable() -> None:
    message = build_trending_heartbeat(30, 7, 2, configured=True, paper_mode=True)

    assert "<b>7</b>" in message
    assert "<b>2</b>" in message
    assert "PAPER" in message


def test_trending_response_is_parsed() -> None:
    client = BirdeyeClient(api_key="test-key")
    payload = {
        "success": True,
        "data": {
            "tokens": [
                {
                    "address": "MintOne",
                    "name": "One",
                    "symbol": "ONE",
                    "liquidity": 1234.5,
                    "volume24hUSD": 6789.0,
                    "rank": 3,
                },
                {"name": "no address"},
            ]
        },
    }

    async def fake_get(_path: str, _params: dict[str, str]) -> object:
        return payload

    client._get = fake_get  # noqa: SLF001
    tokens = asyncio.run(client.get_trending_tokens())

    assert len(tokens) == 1
    assert tokens[0].address == "MintOne"
    assert tokens[0].liquidity_usd == 1234.5
    assert tokens[0].volume_h24 == 6789.0
    assert tokens[0].rank == 3


def test_unsuccessful_trending_payload_yields_nothing() -> None:
    client = BirdeyeClient(api_key="test-key")

    async def fake_get(_path: str, _params: dict[str, str]) -> object:
        return {"success": False}

    client._get = fake_get  # noqa: SLF001

    assert asyncio.run(client.get_trending_tokens()) == []
