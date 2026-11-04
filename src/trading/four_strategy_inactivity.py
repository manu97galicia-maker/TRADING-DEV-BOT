"""Read-only evidence for an inactive, underwater Pump.fun curve."""

from __future__ import annotations

import math
import time

MAX_EVIDENCE_AGE_SECONDS = 2


def quiet_since(rows: object, now: float, seconds: float) -> float | None:
    """Missing/error history is unknown, never evidence of zero activity.

    Any successful curve transaction counts as activity, even a non-trade.
    This deliberately makes the rule more conservative than counting trades.
    """
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    latest = rows[0]
    stamp = latest.get("blockTime")
    if (
        type(stamp) is not int
        or stamp <= 0
        or not isinstance(latest.get("signature"), str)
        or not latest["signature"]
        or type(latest.get("slot")) is not int
        or latest["slot"] <= 0
        or "err" not in latest
        or latest.get("err") is not None
        or latest.get("confirmationStatus") not in {"confirmed", "finalized"}
        or not math.isfinite(now)
        or not math.isfinite(seconds)
        or seconds <= 0
        or now - stamp < seconds
    ):
        return None
    return float(stamp)


async def inactive_curve_evidence(
    curve: object, entry: float, seconds: float, rpc: object, curve_manager: object
) -> dict | None:
    """Only an unmigrated curve can prove inactivity through its own address."""
    state = await curve_manager.get_pool_state(curve, commitment="confirmed")
    price = float(state.get("price_per_token") or 0)
    # Once migrated, the former curve no longer represents market activity.
    if state.get("complete") is not False or not math.isfinite(price) or not 0 < price < entry:
        return None
    rows = await rpc(
        "getSignaturesForAddress",
        [str(curve), {"commitment": "confirmed", "limit": 1}],
    )
    last_activity = quiet_since(rows, time.time(), seconds)
    if last_activity is None:
        return None
    return {
        "checked_at": time.time(),
        "last_activity": last_activity,
        "seconds": seconds,
        "curve_price": price,
        "signature": rows[0]["signature"],
    }


def fresh_inactivity(evidence: dict | None, now: float, seconds: float) -> bool:
    """Never act on a cached quiet result after it has become stale."""
    if not evidence:
        return False
    return (
        seconds > 0
        and evidence.get("seconds") == seconds
        and 0 <= now - evidence.get("checked_at", 0) <= MAX_EVIDENCE_AGE_SECONDS
        and now - evidence.get("last_activity", now) >= seconds
    )
