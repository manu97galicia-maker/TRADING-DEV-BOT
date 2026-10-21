"""Strict schema tests for the read-only GMGN client."""

# Pytest relies on plain assertions for rich failure output.
# ruff: noqa: S101

from decimal import Decimal

import pytest

from bsc.gmgn_client import GmgnClient, GmgnError


def test_parses_documented_trenches_fields() -> None:
    client = GmgnClient("test-key")
    item = client._parse_candidate(  # noqa: SLF001
        {
            "address": "0x" + "1" * 40,
            "creator": "0x" + "2" * 40,
            "name": "Token",
            "symbol": "TOK",
            "launchpad_platform": "fourmeme",
            "usd_market_cap": "8000",
            "total_supply": "1000000",
            "created_timestamp": 1_700_000_000,
        }
    )
    assert item.price_usd == Decimal("0.008")
    assert item.market_cap_usd == Decimal("8000")


@pytest.mark.parametrize("market_cap", ["9000", "10000", "", None])
def test_rejects_missing_or_excess_market_cap(market_cap: object) -> None:
    client = GmgnClient("test-key")
    with pytest.raises(GmgnError):
        client._parse_candidate(  # noqa: SLF001
            {
                "address": "0x" + "1" * 40,
                "creator": "0x" + "2" * 40,
                "name": "Token",
                "symbol": "TOK",
                "launchpad_platform": "flap",
                "usd_market_cap": market_cap,
                "price": "0.01",
                "created_timestamp": 1_700_000_000,
            }
        )
