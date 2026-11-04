"""Keep partial sales rounded down and final sales aligned to chain balances."""

from decimal import Decimal


def raw_sell_amount(amount: float, decimals: int) -> int:
    """Avoid losing an atom when a six-decimal chain balance becomes a float."""
    return int(Decimal(str(amount)) * (10**decimals))


def align_full_sell(requested: float, actual: float, decimals: int, *, full_exit: bool = False) -> tuple[float, bool]:
    """Use the verified full balance on closure; otherwise sweep at most one atom."""
    gap = Decimal(str(actual)) - Decimal(str(requested))
    if actual > 0 and (full_exit or gap <= Decimal(1) / (10**decimals)):
        return actual, True
    return requested, False
