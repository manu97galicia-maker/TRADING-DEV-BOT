"""Offline-tested durable loss-window component; not wired to a trading runner.

Only verified full closures count. A pause blocks admission, not exits, and
cannot be cleared by restarting a process or appending a later winner.
"""

from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from pathlib import Path


class StrategyLossGuard:
    """Track the last five full closures separately for each strategy."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS loss_guard_outcomes ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                "strategy TEXT NOT NULL, trade_id TEXT NOT NULL, "
                "net_lamports INTEGER NOT NULL, "
                "UNIQUE(strategy, trade_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS loss_guard_pauses ("
                "strategy TEXT PRIMARY KEY, reason TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def record_closed(
        self, strategy: str, trade_id: str, net_lamports: int
    ) -> bool:
        """Record a verified closure once; return whether this strategy is paused.

        Caller must reconcile the entire position and its fees first. Unknown
        outcomes and partial exits must never be passed here. The observation
        order must follow confirmed closure order, not original buy timestamps.
        """
        if not strategy.strip() or not trade_id.strip():
            raise ValueError("Strategy and trade identity are required")
        if type(net_lamports) is not int:
            raise TypeError("Net outcome must be integer lamports")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT net_lamports FROM loss_guard_outcomes "
                "WHERE strategy=? AND trade_id=?", (strategy, trade_id)
            ).fetchone()
            if previous and previous[0] != net_lamports:
                raise ValueError("A verified outcome cannot be silently revised")
            db.execute(
                "INSERT OR IGNORE INTO loss_guard_outcomes "
                "(strategy,trade_id,net_lamports) VALUES (?,?,?)",
                (strategy, trade_id, net_lamports),
            )
            recent = db.execute(
                "SELECT net_lamports FROM loss_guard_outcomes "
                "WHERE strategy=? ORDER BY sequence DESC LIMIT 5", (strategy,)
            ).fetchall()
            if len(recent) == 5 and sum(row[0] < 0 for row in recent) >= 3:
                db.execute(
                    "INSERT OR IGNORE INTO loss_guard_pauses VALUES (?,?)",
                    (strategy, "three_losses_in_last_five_closures"),
                )
            return bool(db.execute(
                "SELECT 1 FROM loss_guard_pauses WHERE strategy=?", (strategy,)
            ).fetchone())

    def is_paused(self, strategy: str) -> bool:
        """Return the durable pause, independently of any other strategy."""
        with closing(self._connect()) as db:
            return bool(db.execute(
                "SELECT 1 FROM loss_guard_pauses WHERE strategy=?", (strategy,)
            ).fetchone())


def priority_fee_cap_lamports(
    sol_usd: float, *, quote_age_seconds: float, cap_usd: float = 0.08
) -> int:
    """Convert an emergency priority-fee cap using a fresh SOL/USD observation.

    This is only the priority component, not transaction rent, slippage, base
    signature fees, or a cumulative allowance across retries.
    """
    if (
        not all(math.isfinite(x) for x in (sol_usd, quote_age_seconds, cap_usd))
        or sol_usd <= 0 or cap_usd <= 0 or not 0 <= quote_age_seconds <= 30
    ):
        raise ValueError("A positive SOL price and a quote at most 30s old are required")
    return math.floor(cap_usd / sol_usd * 1_000_000_000)


def priority_price_micro_lamports(cap_lamports: int, compute_units: int) -> int:
    """Round down the CU price so its rounded fee cannot exceed the cap."""
    if type(cap_lamports) is not int or type(compute_units) is not int:
        raise TypeError("Integer fee and compute-unit limit are required")
    if cap_lamports < 0 or compute_units <= 0:
        raise ValueError("Invalid fee cap or compute-unit limit")
    return cap_lamports * 1_000_000 // compute_units
