"""Tests for detailed post-buy Telegram notifications."""

from monitoring.notifier import (
    build_paper_entry_notification,
    build_paper_result_notification,
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
        take_profit_tiers=[
            (0.25, 0.70),
            (0.50, 0.10),
            (0.75, 0.10),
            (2.00, 0.10),
        ],
        name="Paper Coin",
        holders_seen=7,
        distinct_buyers=9,
        observed_volume_sol=11.25,
        observed_operations=14,
        curve_progress_pct=48.5,
    )
    assert "ENTRADA PAPER" in message
    assert "DexScreener boost 500" in message
    assert "0.200 SOL" in message
    assert "0.000000123456 SOL/token" in message
    assert "+25%/70%" in message
    assert "SL -18%" in message
    assert "TP1 +25%" in message
    assert "0.000000154320" in message
    assert "https://dexscreener.com/solana/PaperMint" in message
    assert "https://gmgn.ai/sol/token/PaperMint" in message
    assert "Paper Coin" in message
    assert "$PAPER" in message
    assert "Holders vistos por Helius: <b>7" in message
    assert "Compradores distintos: <b>9" in message
    assert "11.25 SOL" in message
    assert "48.5%" in message
    assert "https://pump.fun/coin/PaperMint" in message


def test_paper_result_includes_prices_stops_winrate_and_links() -> None:
    message = build_paper_result_notification(
        record={
            "symbol": "RESULT",
            "mint": "ResultMint",
            "entry_price_sol": 0.0000001,
            "last_price_sol": 0.00000013,
            "initial_stop_price_sol": 0.000000082,
            "final_stop_price_sol": 0.0000001,
            "take_profit_tiers": [
                {
                    "reason": "TP1",
                    "gain_pct": 25,
                    "sell_pct": 70,
                    "price_sol": 0.000000125,
                    "hit": True,
                },
                {
                    "reason": "TP2",
                    "gain_pct": 50,
                    "sell_pct": 10,
                    "price_sol": 0.00000015,
                    "hit": False,
                },
            ],
            "sales": [
                {
                    "reason": "TP1",
                    "position_pct": 70,
                    "price_sol": 0.000000125,
                },
                {
                    "reason": "STOP",
                    "position_pct": 30,
                    "price_sol": 0.0000001,
                },
            ],
            "average_sale_price_sol": 0.0000001175,
            "position_sold_pct": 100,
            "position_remaining_pct": 0,
            "roi_pct": 17.5,
            "peak_pct": 31,
            "trough_pct": -4,
            "curve_sol": 8,
            "amount_sol": 0.2,
            "value_sol": 0.235,
            "pnl_sol": 0.035,
            "exit_reason": "stop",
            "strategy_closed": True,
        },
        source_data={"acquisition_source": "dexscreener"},
        stats={"n": 5, "wins": 3, "losses": 2, "winrate": 60, "pnl": 0.1},
    )

    assert "Compra: <code>0.000000100000" in message
    assert "TP1: 70% a <code>0.000000125000" in message
    assert "✅ TP1 +25%" in message
    assert "⬜ TP2 +50%" in message
    assert "STOP: 30% a <code>0.000000100000" in message
    assert "SL inicial" in message
    assert "SL final" in message
    assert "WIN RATE: 60.0%" in message
    assert "https://gmgn.ai/sol/token/ResultMint" in message
