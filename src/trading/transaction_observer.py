"""Optional task-local durable receipt tracking before network submission."""

from collections.abc import Callable
from contextvars import ContextVar
from decimal import ROUND_CEILING, Decimal

OBSERVER: ContextVar[Callable[[str, str], None] | None] = ContextVar(
    "transaction_observer", default=None
)
PRIORITY_CAP: ContextVar[int | None] = ContextVar(
    "transaction_priority_cap", default=None
)
SUBMISSIONS: ContextVar[set[str] | None] = ContextVar(
    "observed_submissions", default=None
)
ENTRY_WINDOW: ContextVar[Callable[[], bool] | None] = ContextVar(
    "transaction_entry_window", default=None
)
BUY_PRICE_CEILING: ContextVar[float | None] = ContextVar(
    "buy_price_ceiling_sol", default=None
)


def capped_buy_cost_lamports(maximum: int, token_raw: int) -> int:
    """Cap the exact-token Pump buy cost below a SOL/token ceiling.

    Pump tokens use six decimals and SOL nine. This caps the average cost
    enforced by max_sol_cost; it does not promise the post-buy market cap.
    """
    ceiling = BUY_PRICE_CEILING.get()
    if ceiling is None:
        return maximum
    value = Decimal(str(ceiling))
    if not value.is_finite() or value <= 0 or token_raw <= 0 or maximum <= 0:
        raise ValueError("Invalid buy price ceiling")  # noqa: TRY003 -- invalid task-local policy
    strict_cap = int((value*token_raw*1000).to_integral_value(rounding=ROUND_CEILING))-1
    if strict_cap <= 0:
        raise ValueError("Buy price ceiling is below one lamport")  # noqa: TRY003
    return min(maximum, strict_cap)


class EntryWindowExpiredError(RuntimeError):
    """A timed entry must be abandoned before signing/broadcasting."""


def check_entry_window() -> None:
    guard = ENTRY_WINDOW.get()
    if guard is not None and not guard():
        raise EntryWindowExpiredError("Second-block submission window expired")  # noqa: TRY003 -- guard error


def observe(event: str, signature: str) -> None:
    """Fail closed if a configured receipt writer cannot persist a signature."""
    callback = OBSERVER.get()
    if callback is not None:
        callback(event, signature)
    submissions = SUBMISSIONS.get()
    if submissions is not None:
        if event == "signed":
            submissions.add(signature)
        elif event == "preflight_rejected":
            submissions.discard(signature)
