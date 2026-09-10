"""Pure logic for a tiered take-profit ladder.

A tier is ``(gain, sell)`` where ``gain`` is the fraction above entry that
triggers it (x2 = 1.0, x5 = 4.0) and ``sell`` is the fraction of the ORIGINAL
position to sell when it triggers. Whatever is left after all tiers fire is
held (moonbag). Stop-loss handling lives in the trader; this module only
decides which take-profit tiers are due at a given price.

Kept dependency-free so it can be unit-tested without RPC or a wallet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Tier:
    """One take-profit rung."""

    gain: float  # fraction above entry that triggers the sell (0.4 = +40%)
    sell: float  # fraction of the ORIGINAL quantity to sell


def stepped_stop_price(
    entry_price: float, tiers: list[Tier], completed_tier: int
) -> float | None:
    """Return the protected stop one rung below a completed TP.

    TP1 protects the entry price, TP2 protects TP1, TP3 protects TP2, and so
    on. ``completed_tier`` is zero-based.
    """
    if entry_price <= 0 or not tiers or completed_tier < 0:
        return None
    previous_tier = completed_tier - 1
    if previous_tier < 0:
        return entry_price
    previous_tier = min(previous_tier, len(tiers) - 1)
    return entry_price * (1 + tiers[previous_tier].gain)


def build_tiers(pairs: list[tuple[float | None, float | None]]) -> list[Tier]:
    """Build a validated, ascending list of tiers from (gain, sell) pairs.

    Drops pairs with a missing/zero sell fraction, clamps sell to (0, 1],
    and sorts by trigger gain so lower targets fire first.

    Args:
        pairs: ``[(gain, sell), ...]`` possibly containing ``None``/zero.

    Returns:
        Cleaned tiers sorted by ascending gain.
    """
    tiers: list[Tier] = []
    for gain, sell in pairs:
        if gain is None or sell is None:
            continue
        if gain < 0 or sell <= 0:
            continue
        tiers.append(Tier(gain=float(gain), sell=min(float(sell), 1.0)))
    return sorted(tiers, key=lambda t: t.gain)


def tiers_due(
    entry_price: float,
    current_price: float,
    tiers: list[Tier],
    executed: list[bool],
) -> list[int]:
    """Return indices of tiers whose target price is reached and not yet sold.

    Args:
        entry_price: Position entry price (SOL per token).
        current_price: Current price (SOL per token).
        tiers: Tiers from :func:`build_tiers`.
        executed: Parallel list; True where a tier already sold.

    Returns:
        Indices (into ``tiers``) to execute now, in ascending order.
    """
    if entry_price <= 0:
        return []
    due: list[int] = []
    for i, tier in enumerate(tiers):
        if executed[i]:
            continue
        if current_price >= entry_price * (1.0 + tier.gain):
            due.append(i)
    return due


def ladder_progress(
    entry_price: float,
    current_price: float,
    tiers: list[Tier],
    executed: list[bool],
    sale_prices: list[float | None],
) -> tuple[float, float, float]:
    """Return ``(sold_fraction, realized_roi, total_roi)`` right now.

    ``realized_roi`` counts only the rungs already sold; ``total_roi`` adds
    what the unsold remainder is worth at ``current_price``. Both are
    fractions of the ORIGINAL position, so they can be shown as the running
    profit of the whole trade while it is still open.

    Args:
        entry_price: Position entry price (SOL per token).
        current_price: Current price (SOL per token).
        tiers: Tiers from :func:`build_tiers`.
        executed: Parallel list; True where a tier already sold.
        sale_prices: Parallel list of the price each executed tier sold at;
            ``None`` falls back to that tier's target price.

    Returns:
        ``(sold_fraction, realized_roi, total_roi)``.
    """
    if entry_price <= 0:
        return 0.0, 0.0, 0.0
    remaining = 1.0
    realized = 0.0
    for i, tier in enumerate(tiers):
        if not executed[i] or remaining <= 0:
            continue
        sold = min(float(tier.sell), remaining)
        price = sale_prices[i]
        sale_price = (
            float(price) if price is not None else entry_price * (1 + tier.gain)
        )
        realized += sold * (sale_price / entry_price - 1)
        remaining -= sold
    open_roi = remaining * (current_price / entry_price - 1)
    return max(0.0, 1.0 - remaining), realized, realized + open_roi


def moonbag_fraction(tiers: list[Tier]) -> float:
    """Fraction of the original position left held after all tiers fire."""
    return max(0.0, 1.0 - sum(t.sell for t in tiers))
