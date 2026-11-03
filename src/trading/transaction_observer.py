"""Optional task-local durable receipt tracking before network submission."""

from collections.abc import Callable
from contextvars import ContextVar

OBSERVER: ContextVar[Callable[[str, str], None] | None] = ContextVar(
    "transaction_observer", default=None
)
PRIORITY_CAP: ContextVar[int | None] = ContextVar(
    "transaction_priority_cap", default=None
)
SUBMISSIONS: ContextVar[set[str] | None] = ContextVar(
    "observed_submissions", default=None
)


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
