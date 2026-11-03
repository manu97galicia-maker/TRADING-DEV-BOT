"""Durable, independent batches of five verified outcomes per strategy.

This module never signs, submits, buys or sells. The caller owns the SQLite
transaction so admission and settlement stay atomic with the position book.
"""

# Exact cohort size and policy strings implement the user's evaluation rule.
# ruff: noqa: PLR2004, TRY003

from __future__ import annotations

import json
import time
import uuid

STRATEGIES = ("strong-pressure-8k", "strength-21k", "first-block-four-buyers",
              "telegram-immediate", "dev-second-block")


def validate_evaluation_policy(policy: dict | None) -> None:
    if policy is None:
        return
    if not isinstance(policy, dict) or type(policy.get("enabled")) is not bool:
        raise ValueError("Evaluation needs an explicit enabled boolean")
    if not policy["enabled"]:
        return
    if (type(policy.get("batch_size")) is not int or policy.get("batch_size") != 5 or policy.get("scope") != "per_strategy"
            or policy.get("roi_currency") != "SOL"
            or policy.get("continue_when") != "strictly_positive_net_roi"
            or policy.get("legacy_loss_guard") not in {"keep_three_in_five", "batch_roi_only"}):
        raise ValueError("Expected per-strategy batches of five and strictly positive net SOL ROI")


class FiveTradeEvaluation:
    def __init__(self, policy: dict, epoch: str) -> None:
        self.policy, self.epoch = policy, epoch

    @classmethod
    def configure(cls, db: object, policy: dict | None) -> FiveTradeEvaluation | None:
        validate_evaluation_policy(policy)
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='five_trade_settings'").fetchone()
        old = db.execute("SELECT policy,epoch FROM five_trade_settings WHERE id=1").fetchone() if exists else None
        if old:
            stored = json.loads(old[0])
            validate_evaluation_policy(stored)
            if policy is not None and json.dumps(policy, sort_keys=True) != json.dumps(stored, sort_keys=True):
                raise ValueError("Evaluation policy changed; preserve its history and review an explicit migration")
            return cls(stored, old[1])
        if not policy or not policy["enabled"]:
            return None
        db.execute("CREATE TABLE IF NOT EXISTS five_trade_settings (id INTEGER PRIMARY KEY, policy TEXT NOT NULL, epoch TEXT NOT NULL, created REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS five_trade_batches (epoch TEXT, strategy TEXT, batch INTEGER, state TEXT NOT NULL, created REAL NOT NULL, closed_at REAL, PRIMARY KEY(epoch,strategy,batch))")
        db.execute("CREATE TABLE IF NOT EXISTS five_trade_entries (nonce TEXT PRIMARY KEY, epoch TEXT NOT NULL, strategy TEXT NOT NULL, batch INTEGER NOT NULL, closed INTEGER NOT NULL DEFAULT 0, net INTEGER, cost INTEGER, created REAL NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS five_trade_entries_batch ON five_trade_entries(epoch,strategy,batch)")
        epoch = uuid.uuid4().hex
        db.execute("INSERT INTO five_trade_settings VALUES (1,?,?,?)", (json.dumps(policy, sort_keys=True), epoch, time.time()))
        return cls(policy, epoch)

    @property
    def keep_legacy_loss_guard(self) -> bool:
        return self.policy["legacy_loss_guard"] == "keep_three_in_five"

    def latest(self, db: object, strategy: str) -> tuple | None:
        return db.execute("SELECT batch,state FROM five_trade_batches WHERE epoch=? AND strategy=? ORDER BY batch DESC LIMIT 1", (self.epoch, strategy)).fetchone()

    def paused(self, db: object, strategy: str) -> bool:
        row = self.latest(db, strategy)
        return bool(row and row[1] in {"paused_nonpositive_roi", "blocked_missing_cost"})

    def can_admit(self, db: object, strategy: str) -> bool:
        if strategy not in STRATEGIES:
            return False
        row = self.latest(db, strategy)
        return row is None or row[1] in {"collecting", "passed"}

    def admit(self, db: object, strategy: str, nonce: str) -> dict | None:
        if not self.can_admit(db, strategy):
            return None
        row = self.latest(db, strategy)
        if row is None or row[1] == "passed":
            number = row[0]+1 if row else 1
            db.execute("INSERT INTO five_trade_batches VALUES (?,?,?,'collecting',?,NULL)",
                       (self.epoch, strategy, number, time.time()))
        else:
            number = row[0]
        admitted = db.execute("SELECT COUNT(*) FROM five_trade_entries WHERE epoch=? AND strategy=? AND batch=?", (self.epoch, strategy, number)).fetchone()[0]
        if admitted >= 5:
            return None
        db.execute("INSERT INTO five_trade_entries(nonce,epoch,strategy,batch,created) VALUES (?,?,?,?,?)",
                   (nonce, self.epoch, strategy, number, time.time()))
        if admitted+1 == 5:
            db.execute("UPDATE five_trade_batches SET state='waiting_for_closures' WHERE epoch=? AND strategy=? AND batch=?", (self.epoch, strategy, number))
        return {"epoch": self.epoch, "strategy": strategy, "batch": number, "batch_size": 5}

    def release_unbroadcast(self, db: object, nonce: str) -> None:
        row = db.execute("SELECT strategy,batch FROM five_trade_entries WHERE nonce=? AND epoch=? AND closed=0", (nonce, self.epoch)).fetchone()
        if row:
            db.execute("DELETE FROM five_trade_entries WHERE nonce=? AND closed=0", (nonce,))
            db.execute("UPDATE five_trade_batches SET state='collecting' WHERE epoch=? AND strategy=? AND batch=? AND state='waiting_for_closures'", (self.epoch, row[0], row[1]))

    def closed(self, db: object, nonce: str, net: int, cost: int | None) -> None:
        row = db.execute("SELECT strategy,batch,closed FROM five_trade_entries WHERE nonce=? AND epoch=?", (nonce, self.epoch)).fetchone()
        if row is None:
            return  # Pre-existing positions cannot become new evaluation trials.
        if row[2]:
            raise ValueError("Evaluation closure already recorded")
        valid_cost = type(cost) is int and cost > 0
        db.execute("UPDATE five_trade_entries SET closed=1,net=?,cost=? WHERE nonce=?",
                   (net, cost if valid_cost else None, nonce))
        counts = batch_counts(db, self.epoch, row[0], row[1])
        state = "blocked_missing_cost" if counts["missing_cost"] else None
        if not state and counts["closed"] == 5:
            state = "passed" if counts["net_lamports"] > 0 else "paused_nonpositive_roi"
        if state:
            db.execute("UPDATE five_trade_batches SET state=?,closed_at=? WHERE epoch=? AND strategy=? AND batch=?",
                       (state, time.time() if counts["closed"] == 5 else None, self.epoch, row[0], row[1]))


def batch_counts(db: object, epoch: str, strategy: str, number: int) -> dict:
    admitted, closed, net, cost, missing = db.execute(
        "SELECT COUNT(*),COALESCE(SUM(closed),0),COALESCE(SUM(net),0),COALESCE(SUM(cost),0),"
        "COALESCE(SUM(CASE WHEN closed=1 AND cost IS NULL THEN 1 ELSE 0 END),0) "
        "FROM five_trade_entries WHERE epoch=? AND strategy=? AND batch=?", (epoch, strategy, number)).fetchone()
    return {"admitted": admitted, "closed": closed, "net_lamports": net, "buy_debit_lamports": cost,
            "missing_cost": missing, "net_roi_percent": net/cost*100 if closed == 5 and cost > 0 and not missing else None}


def evaluation_report(db: object) -> dict:
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='five_trade_settings'").fetchone()
    row = db.execute("SELECT policy,epoch FROM five_trade_settings WHERE id=1").fetchone() if exists else None
    if not row:
        return {"state": "not_started", "realized_evaluation_roi": None,
            "strategies": {strategy: {"current": {"state": "not_started", "admitted": 0,
                "closed": 0, "net_roi_percent": None}, "recent_batches": []} for strategy in STRATEGIES}}
    policy, epoch = json.loads(row[0]), row[1]
    result = {"state": "configured", "epoch": epoch, "policy": policy, "strategies": {}}
    for strategy in STRATEGIES:
        rows = db.execute("SELECT batch,state,created,closed_at FROM five_trade_batches WHERE epoch=? AND strategy=? ORDER BY batch DESC LIMIT 20", (epoch, strategy)).fetchall()
        batches = [{"batch": number, "state": state, "created": created, "closed_at": closed_at,
                    **batch_counts(db, epoch, strategy, number)} for number, state, created, closed_at in rows]
        result["strategies"][strategy] = {"current": batches[0] if batches else {"state": "not_started", "admitted": 0, "closed": 0, "net_roi_percent": None}, "recent_batches": batches}
    return result
