"""Pure entry checks for the three on-chain members of the four-source bundle."""

from __future__ import annotations

import math


def choose_signal(  # noqa: PLR0913, PLR0917, PLR0911 -- explicit observed inputs and rejection reasons
    flow: dict,
    points: list,
    block: dict | None,
    price: float | None,
    sol_usd: float,
    now: float,
    policies: dict,
) -> dict | None:
    """Classify once: first-block, 21K crossing, then lower-cap pressure.

    Recent buyers are distinct wallets supplied by the chain collector. The
    first-block check requires the actual creation slot to have been observed.
    No signal is invented to satisfy the desired trade frequency.
    """
    if price is None:
        return None
    buy = float(flow.get("ola_compra_sol", 0))
    sell = float(flow.get("ola_venta_sol", 0))
    buyers = int(flow.get("ola_compradores", 0))
    values = (price, sol_usd, buy, sell, now)
    if (
        not all(math.isfinite(x) for x in values)
        or min(price, sol_usd, now) <= 0
        or min(buy, sell) < 0
    ):
        return None
    mc = price * 1e9 * sol_usd
    if not math.isfinite(mc):
        return None
    features = {
        "observed_at": now,
        "entry_mc": mc,
        "buy_sol": buy,
        "sell_sol": sell,
        "buyers": buyers,
        "volume_usd": (buy + sell) * sol_usd,
        "pressure": buy / sell if sell else None,
    }
    fb = policies["first-block-four-buyers"]
    block_age = (block or {}).get("age_seconds")
    if (
        block
        and block.get("creation_seen")
        and block_age is not None
        and 0 <= block_age <= fb["max_age_seconds"]
        and block.get("buyers", 0) >= fb["min_buyers"]
        and block.get("buy_sol", 0) * sol_usd >= fb["min_volume_usd"]
    ):
        return {**features, "strategy": "first-block-four-buyers", "first_block": block}
    history = [
        (stamp, value)
        for stamp, value in points
        if now - 8 <= stamp <= now and math.isfinite(value) and value > 0
    ]
    old = [p for stamp, p in history if stamp <= now - 1]
    if not old or price < old[-1]:
        return None
    crossing_mc = 21000
    crossed = (
        any(p * 1e9 * sol_usd < crossing_mc for _, p in history) and mc >= crossing_mc
    )
    name = "strength-21k" if crossed else "strong-pressure-8k"
    policy = policies[name]
    if name == "strong-pressure-8k" and not policy["min_mc"] <= mc < crossing_mc:
        return None
    if (
        buyers < policy["min_buyers"]
        or buy < policy["min_buy_sol"]
        or buy <= sell
        or buy < policy["min_ratio"] * sell
        or features["volume_usd"] < policy["min_volume_usd"]
    ):
        return None
    return {**features, "strategy": name}
