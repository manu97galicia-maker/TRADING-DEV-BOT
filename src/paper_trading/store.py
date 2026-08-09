"""SQLite persistence for reproducible paper-trading observations."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


class PaperStore:
    """Small durable store with mint-level deduplication."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (
                  mint TEXT PRIMARY KEY, signal_ts REAL NOT NULL, closed_ts REAL,
                  symbol TEXT NOT NULL, dev TEXT NOT NULL, entry_mc REAL NOT NULL,
                  dev_mean_mc REAL NOT NULL, dev_launches INTEGER NOT NULL,
                  best_peak_mc REAL NOT NULL, best_symbol TEXT NOT NULL,
                  features_json TEXT NOT NULL, result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS positions_signal_ts
                  ON positions(signal_ts);
                """
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection and always close its Windows file handle."""
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, row: dict[str, Any]) -> bool:
        """Insert once; return false when the mint was already observed."""
        try:
            with self.connect() as db:
                db.execute(
                    """INSERT INTO positions VALUES
                    (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                    (
                        row["mint"],
                        row["signal_ts"],
                        row["symbol"],
                        row["dev"],
                        row["entry_mc"],
                        row["dev_mean_mc"],
                        row["dev_launches"],
                        row["best_peak_mc"],
                        row["best_symbol"],
                        json.dumps(row.get("features", {}), sort_keys=True),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        else:
            return True

    def close(self, mint: str, closed_ts: float, result: dict[str, Any]) -> None:
        """Persist a completed observation."""
        with self.connect() as db:
            db.execute(
                "UPDATE positions SET closed_ts=?, result_json=? WHERE mint=?",
                (closed_ts, json.dumps(result, sort_keys=True), mint),
            )

    def rows(self, since: float = 0.0) -> list[dict[str, Any]]:
        """Return records for reports and optimization."""
        with self.connect() as db:
            records = db.execute(
                "SELECT * FROM positions WHERE signal_ts >= ? ORDER BY signal_ts",
                (since,),
            ).fetchall()
        out = []
        for record in records:
            row = dict(record)
            row["features"] = json.loads(row.pop("features_json") or "{}")
            row["result"] = json.loads(row.pop("result_json") or "null")
            out.append(row)
        return out
