"""Cross-process 'panic sell' flag.

The dashboard (a separate process) triggers a sell-all by stamping a file with
the current time; the bot's position monitors check it each loop and dump any
position that was open before the stamp. Positions opened after the stamp are
unaffected, so the flag can linger without killing future trades.
"""

from __future__ import annotations

import time
from pathlib import Path

PANIC_FILE = Path("trades") / "PANIC_SELL"


def trigger() -> float:
    """Stamp the panic file with the current time and return it."""
    PANIC_FILE.parent.mkdir(exist_ok=True)
    now = time.time()
    PANIC_FILE.write_text(str(now), encoding="utf-8")
    return now


def clear() -> None:
    """Remove the panic flag (resume normal operation)."""
    try:
        PANIC_FILE.unlink()
    except FileNotFoundError:
        pass


def panic_time() -> float | None:
    """Return the panic stamp (epoch seconds), or None if not set."""
    try:
        return float(PANIC_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
