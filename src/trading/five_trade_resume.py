"""Prepare or record a user's manual dev-batch resumption without trading I/O.

The caller owns the transaction. Importing this module never changes a ledger.
"""

# ruff: noqa: PLR2004, TRY003

from __future__ import annotations

import json
import time

from trading.five_trade_evaluation import (
    FiveTradeEvaluation,
    batch_counts,
    validate_evaluation_policy,
)
from trading.second_block_policy import STRATEGY_ID


class ResumeBlockedError(ValueError):
    """A static, credential-free explanation for the manual operator."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _closed_dev_counts(db: object, epoch: str, number: int) -> dict:
    rows = db.execute("SELECT closed,net,cost FROM five_trade_entries WHERE epoch=? AND strategy=? AND batch=?",
                      (epoch, STRATEGY_ID, number)).fetchall()
    if len(rows) != 5 or any(closed != 1 or type(net) is not int or type(cost) is not int or cost <= 0
                             for closed, net, cost in rows):
        raise ResumeBlockedError("Each previous trial needs an audited closure and positive integer cost")
    return batch_counts(db, epoch, STRATEGY_ID, number)


def _verified_evaluation(db: object, policy: dict) -> FiveTradeEvaluation:
    validate_evaluation_policy(policy)
    if (not isinstance(policy, dict) or policy.get("enabled") is not True
            or policy.get("legacy_loss_guard") != "batch_roi_only"):
        raise ResumeBlockedError("Manual resumption requires the existing per-strategy ROI evaluation")
    stored = db.execute("SELECT policy,epoch FROM five_trade_settings WHERE id=1").fetchone()
    if stored is None:
        raise ResumeBlockedError("An existing evaluation is required; no history may be reset")
    stored_policy = json.loads(stored[0])
    validate_evaluation_policy(stored_policy)
    if (stored_policy != policy or not isinstance(stored[1], str) or not stored[1].strip()):
        raise ResumeBlockedError("Stored evaluation differs; no history may be reset")
    return FiveTradeEvaluation(policy, stored[1])


def resume_dev(  # noqa: PLR0913 -- verify the selected ledger and cohort before writing
    db: object, *, expected_batch: int, apply: bool = False, expected_owner: str,
    expected_policy_hash: str, configured_evaluation: dict,
) -> dict:
    """Open a fresh batch after a complete losing cohort; keep its evidence intact.

    ``apply=True`` requires the caller's explicit write transaction. The CLI
    opens it with BEGIN IMMEDIATE. An already-running trader may admit real
    orders as soon as that transaction is committed by its human operator.
    """
    if type(expected_batch) is not int or expected_batch < 1 or type(apply) is not bool:
        raise ResumeBlockedError("A positive expected batch number and explicit apply boolean are required")
    if apply and not db.in_transaction:
        raise ResumeBlockedError("Manual application requires an explicit write transaction")
    if db.execute("SELECT owner,policy FROM settings WHERE id=1").fetchone() != (
            expected_owner, expected_policy_hash):
        raise ResumeBlockedError("Ledger owner or shared policy differs")
    evaluation = _verified_evaluation(db, configured_evaluation)
    latest = evaluation.latest(db, STRATEGY_ID)
    if latest != (expected_batch, "paused_nonpositive_roi"):
        raise ResumeBlockedError("Expected losing batch is no longer the latest paused dev batch")
    counts = _closed_dev_counts(db, evaluation.epoch, expected_batch)
    if (counts["admitted"] != 5 or counts["closed"] != 5 or counts["missing_cost"]
            or counts["buy_debit_lamports"] <= 0 or counts["net_lamports"] > 0):
        raise ResumeBlockedError("Previous batch must have five audited closures and nonpositive net ROI")
    if (db.execute("SELECT 1 FROM positions WHERE strategy=? LIMIT 1", (STRATEGY_ID,)).fetchone()
            or db.execute("SELECT 1 FROM five_trade_entries WHERE epoch=? AND strategy=? AND closed=0 LIMIT 1",
                          (evaluation.epoch, STRATEGY_ID)).fetchone()):
        raise ResumeBlockedError("A dev position or unresolved evaluation entry still exists")
    report = {
        "strategy": STRATEGY_ID, "epoch": evaluation.epoch,
        "previous_batch": expected_batch, "new_batch": expected_batch+1,
        "previous_net_lamports": counts["net_lamports"],
        "previous_net_roi_percent": counts["net_roi_percent"],
        "applied": apply, "enables_new_entries": apply, "would_enable_new_entries": True,
        "read_only": not apply, "history_preserved": True, "trader_started": False,
        "existing_live_process_may_buy_after_apply": True,
    }
    if apply:
        now = time.time()
        db.execute("CREATE TABLE IF NOT EXISTS five_trade_manual_resumptions ("
                   "epoch TEXT, strategy TEXT, previous_batch INTEGER, new_batch INTEGER, "
                   "previous_net INTEGER, previous_cost INTEGER, reason TEXT, created REAL, "
                   "PRIMARY KEY(epoch,strategy,new_batch))")
        db.execute("INSERT INTO five_trade_batches VALUES (?,?,?,'collecting',?,NULL)",
                   (evaluation.epoch, STRATEGY_ID, expected_batch+1, now))
        db.execute("INSERT INTO five_trade_manual_resumptions VALUES (?,?,?,?,?,?,?,?)",
                   (evaluation.epoch, STRATEGY_ID, expected_batch, expected_batch+1,
                    counts["net_lamports"], counts["buy_debit_lamports"], "manual_user_resume", now))
    return report
