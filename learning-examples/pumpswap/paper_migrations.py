"""Observe fresh PumpSwap migrations and simulate the configured TP/SL ladder."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import sys
import time
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from solders.pubkey import Pubkey

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dexscreener import DexScreenerClient  # noqa: E402
from dashboard.dexpaid_feed import get_candidates  # noqa: E402
from monitoring.notifier import _send_blocking  # noqa: E402

EVENT_DISCRIMINATOR = bytes([189, 233, 93, 185, 92, 148, 234, 148])
AMOUNT_SOL = 0.35
STOP_LOSS = 0.18
MAX_HOLD_SECONDS = 0  # Zero keeps the position until TP or SL closes it.
TIERS = (
    (0.25, 0.30),
    (0.50, 0.25),
    (0.75, 0.25),
    (1.00, 0.04),
    (2.00, 0.04),
    (4.00, 0.04),
    (9.00, 0.04),
    (19.00, 0.04),
)
MAX_POSITIONS = 0  # Zero means no configured paper-position cap.
STATS_PATH = Path("paper_stats_pumpswap.json")
RESULTS_PATH = Path("paper_trades/pumpswap_results.jsonl")


def parse_migration_event(encoded: str) -> dict[str, object] | None:
    """Decode a CompletePumpAmmMigrationEvent emitted by pump.fun."""
    try:
        raw = base64.b64decode(encoded)
    except (ValueError, TypeError):
        return None
    if len(raw) < 168 or raw[:8] != EVENT_DISCRIMINATOR:
        return None
    offset = 8
    user = Pubkey.from_bytes(raw[offset : offset + 32])
    offset += 32
    mint = Pubkey.from_bytes(raw[offset : offset + 32])
    offset += 32
    mint_amount, sol_amount, fee = struct.unpack_from("<QQQ", raw, offset)
    offset += 24
    curve = Pubkey.from_bytes(raw[offset : offset + 32])
    offset += 32
    timestamp = struct.unpack_from("<q", raw, offset)[0]
    offset += 8
    pool = Pubkey.from_bytes(raw[offset : offset + 32])
    return {
        "user": str(user),
        "mint": str(mint),
        "mint_amount": mint_amount,
        "sol_amount": sol_amount,
        "fee": fee,
        "bonding_curve": str(curve),
        "timestamp": timestamp,
        "pool": str(pool),
    }


async def send(text: str) -> None:
    """Send Telegram without blocking the event loop."""
    await asyncio.to_thread(_send_blocking, text)


async def market(mint: str) -> tuple[float, str, float]:
    """Return PumpSwap price in SOL, symbol and liquidity USD."""
    async with DexScreenerClient() as client:
        data = await client.get_market_data(mint)
    if data is None or str(data.dex_id or "").lower() != "pumpswap":
        return 0.0, "?", 0.0
    symbol = str((data.raw.get("baseToken") or {}).get("symbol") or "?")
    return float(data.price_native or 0), symbol, float(data.liquidity_usd or 0)


def bump_stats(pnl: float) -> dict[str, float | int]:
    """Persist cumulative PumpSwap paper performance."""
    try:
        stats = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stats = {}
    stats["n"] = int(stats.get("n", 0)) + 1
    stats["wins"] = int(stats.get("wins", 0)) + int(pnl > 0)
    stats["losses"] = int(stats["n"]) - int(stats["wins"])
    stats["pnl"] = float(stats.get("pnl", 0)) + pnl
    stats["winrate"] = 100 * int(stats["wins"]) / int(stats["n"])
    STATS_PATH.write_text(json.dumps(stats), encoding="utf-8")
    return stats


async def watch(event: dict[str, object]) -> None:
    """Open and close one simulated PumpSwap position."""
    mint = str(event["mint"])
    price = 0.0
    symbol = "?"
    liquidity = 0.0
    for _ in range(60):
        price, symbol, liquidity = await market(mint)
        if price > 0:
            break
        await asyncio.sleep(1)
    if price <= 0:
        await send(f"⚠️ Migración PumpSwap sin precio tras 60 s\nCA: <code>{mint}</code>")
        return

    entry = price
    remaining = 1.0
    proceeds = 0.0
    hits = [False] * len(TIERS)
    stop = entry * (1 - STOP_LOSS)
    started = time.monotonic()
    safe_mint = escape(mint)
    signal_type = escape(str(event.get("signal_type") or "paid"))
    boosts = float(event.get("boosts") or 0)
    await send(
        f"🧪 <b>ENTRADA PAPER PUMPSWAP</b> · {escape(symbol)}\n"
        f"🔎 Motivo: <b>DexScreener {signal_type}</b> · boost {boosts:g}\n"
        f"💵 Simulación: <b>{AMOUNT_SOL:.2f} SOL</b>\n"
        f"🏷️ Entrada: <code>{entry:.12f} SOL/token</code>\n"
        f"💧 Liquidez: ${liquidity:,.0f}\n"
        f"🛡️ SL -18% · TP +25%/+50%/+75% y escalones hasta x20\n"
        f'<a href="https://gmgn.ai/sol/token/{safe_mint}">GMGN</a> · '
        f'<a href="https://dexscreener.com/solana/{safe_mint}">DexScreener</a> · '
        f'<a href="https://pump.fun/{safe_mint}">pump.fun</a>\n'
        f"CA: <code>{safe_mint}</code>"
    )

    reason = "open"
    while (
        (MAX_HOLD_SECONDS <= 0 or time.monotonic() - started < MAX_HOLD_SECONDS)
        and remaining > 1e-9
    ):
        current, _, _ = await market(mint)
        if current <= 0:
            await asyncio.sleep(1)
            continue
        for index, (gain, sell_fraction) in enumerate(TIERS):
            if not hits[index] and current >= entry * (1 + gain):
                sold = min(sell_fraction, remaining)
                proceeds += sold * current / entry
                remaining -= sold
                hits[index] = True
                stop = entry if index == 0 else entry * (1 + TIERS[index - 1][0])
        if current <= stop and remaining > 0:
            proceeds += remaining * current / entry
            remaining = 0
            reason = "stop"
        elif remaining <= 1e-9:
            reason = "tp_closed"
        await asyncio.sleep(1)

    if remaining > 0:
        reason = "max_hold"
        current, _, _ = await market(mint)
        proceeds += remaining * max(current, 0) / entry
    value = AMOUNT_SOL * proceeds
    pnl = value - AMOUNT_SOL
    stats = bump_stats(pnl)
    record = {
        "timestamp": time.time(),
        "mint": mint,
        "symbol": symbol,
        "entry_price_sol": entry,
        "value_sol": value,
        "pnl_sol": pnl,
        "roi_pct": 100 * pnl / AMOUNT_SOL,
        "tp_hits": sum(hits),
        "exit_reason": reason,
    }
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    await send(
        f"📊 <b>CIERRE PAPER PUMPSWAP</b> · {escape(symbol)}\n"
        f"Motivo: {reason} · TP alcanzados: {sum(hits)}\n"
        f"ROI: <b>{record['roi_pct']:+.1f}%</b> · P&L: <b>{pnl:+.4f} SOL</b>\n"
        f"WR PumpSwap: <b>{stats['winrate']:.1f}%</b> · "
        f"{stats['wins']}✅/{stats['losses']}❌\n"
        f"P&L acumulado: <b>{stats['pnl']:+.4f} SOL</b>\n"
        f'<a href="https://gmgn.ai/sol/token/{safe_mint}">GMGN</a> · '
        f'<a href="https://dexscreener.com/solana/{safe_mint}">DexScreener</a>'
    )


async def main() -> None:
    """Poll for fresh Dex Paid/boost signals whose token is on PumpSwap."""
    load_dotenv()
    active: set[asyncio.Task[None]] = set()
    seen: set[tuple[str, str, float]] = set()
    primed = False
    await send(
        "🧪 <b>PumpSwap Dex Paid/Boost paper activo</b> · "
        "0,35 SOL · mismo TP/SL"
    )
    while True:
        try:
            rows = await get_candidates(
                limit=10,
                max_age_minutes=10,
                max_token_age_minutes=0,
                check_locked=False,
            )
            migrated = [
                row
                for row in rows
                if row.get("migrated") and float(row.get("volume_h24") or 0) > 0
            ]
            current = {
                (
                    str(row.get("mint") or ""),
                    str(row.get("signal_type") or "paid"),
                    float(row.get("boosts") or 0),
                )
                for row in migrated
                if row.get("mint")
            }
            if not primed:
                seen.update(current)
                primed = True
            else:
                by_key = {
                    (
                        str(row.get("mint") or ""),
                        str(row.get("signal_type") or "paid"),
                        float(row.get("boosts") or 0),
                    ): row
                    for row in migrated
                }
                for key in current - seen:
                    active = {task for task in active if not task.done()}
                    if MAX_POSITIONS <= 0 or len(active) < MAX_POSITIONS:
                        active.add(asyncio.create_task(watch(by_key[key])))
                seen.update(current)
        except (TimeoutError, OSError):
            await asyncio.sleep(2)
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
