"""Unit tests for external market-data response normalization."""

# ruff: noqa: PLR2004, S101

from birdeye.client import _parse_token_overview
from dextools.client import _parse_token_data, _unwrap


def test_birdeye_token_overview_parsing() -> None:
    result = _parse_token_overview(
        {
            "address": "mint",
            "symbol": "TKN",
            "price": "1.25",
            "liquidity": 5000,
            "v24hUSD": "900",
            "mc": 10000,
        }
    )

    assert result.address == "mint"
    assert result.price_usd == 1.25
    assert result.liquidity_usd == 5000.0
    assert result.volume_h24 == 900.0
    assert result.market_cap == 10000.0


def test_dextools_unwrap_and_parsing() -> None:
    data = _unwrap(
        {
            "data": {
                "address": "mint",
                "price": "2.5",
                "score": 87,
                "metrics": {"liquidity": "1000", "volume24h": 300},
            }
        }
    )
    result = _parse_token_data(data)

    assert result.address == "mint"
    assert result.price_usd == 2.5
    assert result.dext_score == 87.0
    assert result.liquidity_usd == 1000.0
    assert result.volume_h24 == 300.0
