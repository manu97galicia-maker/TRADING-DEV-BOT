"""Atomic wallet-wide admission, receipts and per-strategy verified outcomes."""

# Policy literals mirror the explicit bundle contract; errors retain useful context.
# ruff: noqa: PLR2004, TRY003

from __future__ import annotations

import json
import sqlite3
import statistics
import time
import uuid
from contextlib import closing
from itertools import pairwise
from pathlib import Path

from trading.buy_budget import inclusive_balance_entry


class FourStrategyBook:
    """Keep three positions, including pending submissions and unsold runners."""

    def __init__(self, path: Path, owner: str, policy_id: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, owner TEXT, policy TEXT, watermark INTEGER)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS positions (nonce TEXT PRIMARY KEY, mint TEXT UNIQUE, strategy TEXT, state TEXT, budget INTEGER, debit_slot INTEGER, details TEXT, created REAL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS receipts (signature TEXT PRIMARY KEY, nonce TEXT, kind TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS outcomes (sequence INTEGER PRIMARY KEY AUTOINCREMENT, nonce TEXT UNIQUE, strategy TEXT, net INTEGER, details TEXT)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS pauses (strategy TEXT PRIMARY KEY)")
            old = db.execute("SELECT owner,policy FROM settings WHERE id=1").fetchone()
            if old and old != (owner, policy_id):
                raise ValueError(
                    "Wallet or strategy policy differs from the durable ledger"
                )
            db.execute(
                "INSERT OR IGNORE INTO settings VALUES (1,?,?,0)", (owner, policy_id)
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def positions(self) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT nonce,mint,strategy,state,budget,debit_slot,details FROM positions ORDER BY created"
            ).fetchall()
        return [
            dict(
                zip(
                    (
                        "nonce",
                        "mint",
                        "strategy",
                        "state",
                        "budget",
                        "debit_slot",
                        "details",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in rows
        ]

    def paused(self, strategy: str) -> bool:
        with closing(self.connect()) as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM pauses WHERE strategy=?", (strategy,)
                ).fetchone()
            )

    def reserve(  # noqa: PLR0913, PLR0917 -- all inputs belong to one atomic admission
        self,
        mint: str,
        strategy: str,
        balance: int,
        slot: int,
        reserve: int,
        slippage: float,
        fees: int,
        features: dict,
    ) -> dict | None:
        """Reserve 20% of current native SOL inclusive of slippage and fees."""
        if not mint or not strategy or min(balance, slot, reserve, fees) < 0:
            raise ValueError("Invalid reservation inputs")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM pauses WHERE strategy=?", (strategy,)
            ).fetchone():
                return None
            watermark = db.execute(
                "SELECT watermark FROM settings WHERE id=1"
            ).fetchone()[0]
            rows = db.execute(
                "SELECT state,budget,debit_slot FROM positions"
            ).fetchall()
            if (
                slot < watermark
                or len(rows) >= 3
                or any(r[0] == "uncertain" for r in rows)
                or db.execute(
                    "SELECT 1 FROM positions WHERE mint=?", (mint,)
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM outcomes WHERE json_extract(details,'$.mint')=?",
                    (mint,),
                ).fetchone()
            ):
                return None
            pending = sum(
                budget
                for state, budget, debit in rows
                if state == "reserved" or debit is None or debit > slot
            )
            amount, budget = inclusive_balance_entry(balance, 0.20, slippage, fees)
            if amount <= 0 or budget > balance - reserve - pending:
                return None
            nonce = uuid.uuid4().hex
            details = {
                "mint": mint,
                "features": features,
                "amount_sol": amount,
                "policy_fraction": 0.20,
                "balance_lamports": balance,
            }
            db.execute(
                "INSERT INTO positions VALUES (?,?,?,'reserved',?,NULL,?,?)",
                (
                    nonce,
                    mint,
                    strategy,
                    budget,
                    json.dumps(details, allow_nan=False),
                    time.time(),
                ),
            )
            return {"nonce": nonce, "amount_sol": amount, "budget": budget}

    def submitting(self, nonce: str) -> bool:
        """Persist uncertainty before any possible network submission."""
        with closing(self.connect()) as db, db:
            return (
                db.execute(
                    "UPDATE positions SET state='uncertain' WHERE nonce=? AND state='reserved'",
                    (nonce,),
                ).rowcount
                == 1
            )

    def cancel_unsubmitted(self, nonce: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute(
                "DELETE FROM positions WHERE nonce=? AND state='reserved'", (nonce,)
            )

    def receipt(self, nonce: str, signature: str, kind: str) -> None:
        if not signature or kind not in {"buy", "sell", "failed"}:
            raise ValueError("An identified receipt and its kind are required")
        with closing(self.connect()) as db, db:
            if not db.execute(
                "SELECT 1 FROM positions WHERE nonce=?", (nonce,)
            ).fetchone():
                raise ValueError(
                    "Cannot attach a receipt to a settled or missing position"
                )
            existing = db.execute(
                "SELECT nonce,kind FROM receipts WHERE signature=?", (signature,)
            ).fetchone()
            if existing == (nonce, "rejected"):
                db.execute(
                    "UPDATE receipts SET kind=? WHERE signature=?", (kind, signature)
                )
                return
            if existing and existing != (nonce, kind):
                raise ValueError("Receipt already belongs to another position")
            db.execute(
                "INSERT OR IGNORE INTO receipts VALUES (?,?,?)",
                (signature, nonce, kind),
            )

    def rejected(self, nonce: str, signature: str) -> None:
        """A typed preflight rejection is evidence that this tx was not sent."""
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE receipts SET kind='rejected' WHERE nonce=? AND signature=?",
                (nonce, signature),
            )

    def release_unbroadcast(self, nonce: str) -> None:
        """Release only if every recorded attempt is proven unbroadcast."""
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM receipts WHERE nonce=? AND kind!='rejected'", (nonce,)
            ).fetchone():
                db.execute(
                    "DELETE FROM positions WHERE nonce=? AND state IN ('reserved','uncertain')",
                    (nonce,),
                )

    def opened(self, nonce: str, slot: int, entry: dict) -> None:
        with closing(self.connect()) as db, db:
            old = db.execute(
                "SELECT details FROM positions WHERE nonce=?", (nonce,)
            ).fetchone()
            if old is None:
                raise ValueError("Missing durable buy reservation")
            details = {**json.loads(old[0]), **entry}
            db.execute(
                "UPDATE positions SET state='open',debit_slot=?,details=? WHERE nonce=?",
                (slot, json.dumps(details, allow_nan=False), nonce),
            )

    def signatures(self, nonce: str) -> list[tuple[str, str]]:
        with closing(self.connect()) as db:
            return db.execute(
                "SELECT signature,kind FROM receipts WHERE nonce=? AND kind!='rejected' ORDER BY rowid",
                (nonce,),
            ).fetchall()

    def settle(
        self, nonce: str, net: int, context_slot: int, details: dict,
        *, additional_receipts: list[tuple[str, str]] | None = None,
    ) -> None:
        """Called only after finalized receipts and zero on-chain holdings."""
        if type(net) is not int or not details.get("verified_zero_holdings"):
            raise ValueError("A complete integer-lamport audit is required")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT strategy,details,state FROM positions WHERE nonce=?", (nonce,)
            ).fetchone()
            if row is None:
                return
            if row[2] == "reserved":
                raise ValueError("Cannot settle an unsubmitted reservation")
            if additional_receipts:
                previous = [r[0] for r in db.execute(
                    "SELECT signature FROM receipts WHERE nonce=? AND kind!='rejected' ORDER BY rowid",
                    (nonce,),
                )]
                if previous != details.get("previous_signatures"):
                    raise ValueError("Manual reconciliation snapshot changed")
                for signature, kind in additional_receipts:
                    if not signature or kind not in {"sell", "failed"}:
                        raise ValueError("Invalid manual disposal receipt")
                    # A conflicting signature fails and rolls back this entire
                    # transaction, including all receipts inserted above it.
                    db.execute("INSERT INTO receipts VALUES (?,?,?)", (signature, nonce, kind))
            expected = details.get("signatures")
            actual = [
                r[0]
                for r in db.execute(
                    "SELECT signature FROM receipts WHERE nonce=? AND kind!='rejected' ORDER BY rowid",
                    (nonce,),
                )
            ]
            if not expected or expected != actual:
                raise ValueError("Settlement receipts changed; repeat the chain audit")
            evidence = {**json.loads(row[1]), **details, "closed_at": time.time()}
            db.execute(
                "INSERT INTO outcomes(nonce,strategy,net,details) VALUES (?,?,?,?)",
                (nonce, row[0], net, json.dumps(evidence, allow_nan=False)),
            )
            recent = db.execute(
                "SELECT net FROM outcomes WHERE strategy=? ORDER BY sequence DESC LIMIT 5",
                (row[0],),
            ).fetchall()
            if len(recent) == 5 and sum(r[0] < 0 for r in recent) >= 3:
                db.execute("INSERT OR IGNORE INTO pauses VALUES (?)", (row[0],))
            db.execute(
                "UPDATE settings SET watermark=MAX(watermark,?) WHERE id=1",
                (context_slot,),
            )
            db.execute("DELETE FROM positions WHERE nonce=?", (nonce,))

    def research(self) -> dict:
        """Compare all verified winners and losers, separated by strategy."""
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT strategy,net,details FROM outcomes ORDER BY sequence"
            ).fetchall()
        groups: dict[str, dict] = {}
        for strategy, net, raw in rows:
            group = groups.setdefault(
                strategy,
                {"closed": 0, "wins": 0, "net_lamports": 0, "observations": []},
            )
            group["closed"] += 1
            group["wins"] += net > 0
            group["net_lamports"] += net
            group["observations"].append({"net_lamports": net, **json.loads(raw)})
        for group in groups.values():
            observations = group["observations"]
            timestamps = sorted(
                r.get("features", {}).get("observed_at")
                for r in observations
                if isinstance(r.get("features", {}).get("observed_at"), (int, float))
            )
            intervals = [later - earlier for earlier, later in pairwise(timestamps)]
            group["observed_inter_entry_seconds_median"] = (
                statistics.median(intervals) if intervals else None
            )
            for label, subset in (
                ("winners", [r for r in observations if r["net_lamports"] > 0]),
                ("non_winners", [r for r in observations if r["net_lamports"] <= 0]),
            ):
                medians = {}
                for feature in (
                    "entry_mc",
                    "buy_sol",
                    "sell_sol",
                    "volume_usd",
                    "buyers",
                    "pressure",
                ):
                    samples = [r.get("features", {}).get(feature) for r in subset]
                    samples = [x for x in samples if isinstance(x, (int, float))]
                    medians[feature] = statistics.median(samples) if samples else None
                group[label] = {"n": len(subset), "median_features": medians}
            group["validation"] = (
                "insufficient_sample"
                if group["closed"] < 30
                else "descriptive_only_needs_forward_validation"
            )
        return {
            "strategies": groups,
            "open_or_uncertain": len(self.positions()),
            "automatic_parameter_changes": False,
        }
