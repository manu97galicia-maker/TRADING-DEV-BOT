"""Tests for detailed post-buy Telegram notifications."""

from monitoring.notifier import (
    build_paper_entry_notification,
    build_purchase_notification,
)


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
    assert "DexScreener boost 500" in message
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
    assert "DEXTools NITRO 1000" in message


def test_legends_alpha_signal_is_identified() -> None:
    message = build_purchase_notification(
        "ALPHA",
        "AlphaMint",
        0.15,
        {"acquisition_source": "LegendsAlphas"},
        0,
        "+25%/70%",
        18,
    )
    assert "Legends Alpha signal" in message


def test_confirmed_purchase_includes_exact_entry_price() -> None:
    message = build_purchase_notification(
        "PRICE",
        "PriceMint",
        0.17,
        {"acquisition_source": "dextools_nitro", "dextools_nitro_score": 500},
        2500,
        "+25%/70%",
        18,
        entry_price_sol=0.000002345678,
    )
    assert "DEXTools NITRO 500" in message
    assert "0.000002345678 SOL/token" in message
    assert "https://dexscreener.com/solana/PriceMint" in message


def test_paper_entry_explains_reason_amount_price_and_ladder() -> None:
    message = build_paper_entry_notification(
        symbol="PAPER",
        mint="PaperMint",
        amount_sol=0.20,
        source_data={
            "acquisition_source": "dexscreener",
            "dexscreener_boosts": 500,
        },
        entry_price_sol=0.000000123456,
        market_cap_usd=11263,
        curve_sol=8.4,
        strategy="+25%/70% · +50%/10% · +75%/10% · +200%/10%",
        stop_loss_pct=18,
    )
    assert "ENTRADA PAPER" in message
    assert "DexScreener boost 500" in message
    assert "0.200 SOL" in message
    assert "0.000000123456 SOL/token" in message
    assert "+25%/70%" in message
    assert "SL -18%" in message
    assert "https://dexscreener.com/solana/PaperMint" in message
