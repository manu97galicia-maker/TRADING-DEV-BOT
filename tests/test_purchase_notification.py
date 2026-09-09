"""Tests for detailed post-buy Telegram notifications."""

from monitoring.notifier import build_purchase_notification


def test_moontok_purchase_explains_source_and_amount() -> None:
    message = build_purchase_notification(
        "MOON",
        "MintAddress",
        0.15,
        {"acquisition_source": "CryptoListings"},
        1234,
        "+25%/70% · +50%/10%",
        18,
    )
    assert "MoonTok listing" in message
    assert "0.150 SOL" in message
    assert "<code>MintAddress</code>" in message


def test_expensive_dexscreener_boost_is_explicit() -> None:
    message = build_purchase_notification(
        "BOOST",
        "BoostMint",
        0.20,
        {"acquisition_source": "dexscreener", "dexscreener_boosts": 500},
        0,
        "+25%/70%",
        18,
    )
    assert "DexScreener boost caro" in message
    assert "500" in message
    assert "0.200 SOL" in message


def test_dextools_nitro_level_is_included() -> None:
    message = build_purchase_notification(
        "NITRO",
        "NitroMint",
        0.20,
        {"acquisition_source": "dextools_nitro", "dextools_nitro_score": 1000},
        0,
        "+25%/70%",
        18,
    )
    assert "DEXTools NITRO caro" in message
    assert "1000" in message
