"""Live feed of Solana tokens that recently paid DexScreener (dex paid / boosts).

Powers the dashboard's "Pagaron Dex" panel and is the detection source for the
buy-on-dex-paid strategy. Measured reality: a paid order is visible on the API
minutes after payment (not seconds), so this polls the latest-boosts feed and
surfaces recently-paid tokens with the data needed to decide a buy:
boost amount, payment age, price/liquidity, and RugCheck locked-liquidity %.

Bounded on purpose (a handful of tokens per refresh) to stay within the 60/300
rpm DexScreener limits and RugCheck's gentle limit.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from dexscreener.client import DexScreenerClient
from dexscreener.rugcheck import RugCheckClient
from utils.logger import get_logger

logger = get_logger(__name__)


async def _paid_age_minutes(
    dex: DexScreenerClient, mint: str, chain: str, now_ms: float
) -> float | None:
    """Return minutes since the newest approved paid order, or None.

    Runs on the 60 rpm limiter, so it is the slowest step of a cycle and is
    only called for the paid path (a boost is its own proof of payment).
    """
    orders = await dex.get_orders(mint, chain)
    paid_ts = [
        order.get("paymentTimestamp")
        for order in orders
        if order.get("status") == "approved" and order.get("paymentTimestamp")
    ]
    if not paid_ts:
        return None
    return (now_ms - max(paid_ts)) / 60000.0


async def get_candidates(
    limit: int = 10,
    max_age_minutes: float = 120.0,
    max_token_age_minutes: float = 0.0,
    chain: str = "solana",
    check_locked: bool = True,
) -> list[dict[str, Any]]:
    """Return recently dex-paid tokens with buy-decision data.

    Args:
        limit: Max tokens to return (kept small for rate limits).
        max_age_minutes: Only include tokens whose newest approved paid order
            is younger than this.
        max_token_age_minutes: Reject pairs older than this; zero disables it.
        chain: Chain slug.
        check_locked: Also query RugCheck for locked-liquidity % (slower).

    Returns:
        List of dicts sorted by most-recent payment first, each with mint,
        symbol, boosts, payment_age_min, price_usd, liquidity_usd, dex_id,
        migrated flag, and (if check_locked) lp_locked_pct.
    """
    now_ms = time.time() * 1000.0

    async with DexScreenerClient(chain=chain) as dex:
        boosts, profiles = await asyncio.gather(
            dex.get_latest_boosts(), dex.get_latest_token_profiles()
        )
        combined = [
            {**entry, "_from_boosts": True}
            for entry in boosts
            if entry.get("chainId") == chain
        ] + [
            {**entry, "_from_profiles": True}
            for entry in profiles
            if entry.get("chainId") == chain
        ]
        by_mint: dict[str, dict[str, Any]] = {}
        for entry in combined:
            mint = entry.get("tokenAddress")
            if not mint:
                continue
            current = by_mint.get(mint, {})
            by_mint[mint] = {
                **current,
                **entry,
                "totalAmount": max(
                    float(current.get("totalAmount") or current.get("amount") or 0),
                    float(entry.get("totalAmount") or entry.get("amount") or 0),
                ),
                "_from_boosts": bool(
                    current.get("_from_boosts") or entry.get("_from_boosts")
                ),
                "_from_profiles": bool(
                    current.get("_from_profiles") or entry.get("_from_profiles")
                ),
            }
        toks = list(by_mint.values())[: limit * 3]

        async def build(entry: dict[str, Any]) -> dict[str, Any] | None:
            mint = entry.get("tokenAddress")
            if not mint:
                return None
            # A boost is its own proof of payment, so the orders lookup is
            # skipped for it: that is what keeps detection near-instant.
            is_boost = bool(entry.get("_from_boosts"))
            age_min: float | None = None
            if not is_boost:
                age_min = await _paid_age_minutes(dex, mint, chain, now_ms)
                if age_min is None or age_min > max_age_minutes:
                    return None
            market = await dex.get_market_data(mint, chain)
            pair_created_at = market.pair_created_at if market else None
            token_age_min = (
                (now_ms - float(pair_created_at)) / 60000.0 if pair_created_at else None
            )
            if max_token_age_minutes > 0 and (
                token_age_min is None or token_age_min > max_token_age_minutes
            ):
                return None
            dex_id = market.dex_id if market else None
            migrated = dex_id in ("pumpswap", "raydium") if dex_id else None
            return {
                "mint": mint,
                "symbol": (
                    market.raw.get("baseToken", {}).get("symbol") if market else None
                )
                or "?",
                "name": (
                    market.raw.get("baseToken", {}).get("name") if market else None
                )
                or "",
                "boosts": entry.get("totalAmount") or entry.get("amount") or 0,
                "payment_age_min": round(age_min, 1) if age_min is not None else None,
                "token_age_min": (
                    round(token_age_min, 1) if token_age_min is not None else None
                ),
                "signal_type": "boost" if is_boost else "paid",
                "price_usd": market.price_usd if market else None,
                "liquidity_usd": market.liquidity_usd if market else None,
                "dex_id": dex_id,
                "migrated": migrated,
            }

        built = await asyncio.gather(*[build(t) for t in toks])
        rows = [r for r in built if r][:limit]

        if check_locked and rows:
            async with RugCheckClient() as rc:
                reports = await asyncio.gather(
                    *[rc.get_report(r["mint"]) for r in rows]
                )
                for r, rep in zip(rows, reports):  # noqa: B905 - Python 3.9
                    r["lp_locked_pct"] = rep.lp_locked_pct if rep.found else None
                    r["rugged"] = rep.rugged if rep.found else None

    rows.sort(
        key=lambda r: (
            r["signal_type"] != "boost",
            r["payment_age_min"] if r["payment_age_min"] is not None else 0,
        )
    )
    return rows
