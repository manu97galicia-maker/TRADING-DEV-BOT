"""Size a native SOL entry within a fraction that includes execution costs."""

from decimal import Decimal

LAMPORTS_PER_SOL = 1_000_000_000


def inclusive_balance_entry(
    balance_lamports: int,
    fraction: float,
    slippage: float,
    fee_lamports: int,
    cap_sol: float = 0,
) -> tuple[float, int]:
    """Return nominal SOL and total reserved lamports, never exceeding the budget."""
    share, margin = Decimal(str(fraction)), Decimal(str(slippage))
    cap = Decimal(str(cap_sol))
    if (
        balance_lamports < 0 or fee_lamports < 0
        or not share.is_finite() or not 0 <= share <= 1
        or not margin.is_finite() or margin < 0
        or not cap.is_finite() or cap < 0
    ):
        message = "Invalid inclusive entry budget"
        raise ValueError(message)
    budget = int(Decimal(balance_lamports) * share)
    if budget <= fee_lamports:
        return 0.0, 0
    nominal = int(Decimal(budget - fee_lamports) / (1 + margin))
    if cap > 0:
        nominal = min(nominal, int(cap * LAMPORTS_PER_SOL))
    if nominal <= 0:
        return 0.0, 0
    # Match the buyer's maximum SOL cost, then include rent/fee headroom.
    reserved = int(Decimal(nominal) * (1 + margin)) + fee_lamports
    return nominal / LAMPORTS_PER_SOL, reserved
