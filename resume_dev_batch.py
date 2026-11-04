"""Manual dev-batch resumption. Default is read-only; --apply enables new entries.

An existing live trader may start buying immediately after manual --apply.
This program never loads wallet keys, calls an RPC or starts a trader.
"""

# ruff: noqa: PLC0415, TRY003

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/"src"))


def run(config: Path, *, expected_batch: int, apply: bool = False) -> dict:
    from trading.five_trade_resume import resume_dev
    from trading.second_block_policy import load_second_policy

    config = config.resolve()
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    dev = load_second_policy(config, cfg)
    if cfg.get("enabled") is not True or dev.get("enabled") is not True:
        raise ValueError("Both the shared coordinator and dev strategy must already be enabled")
    settings = cfg["four_strategies"]
    directory = config.parent.parent
    ledger = (directory/settings["database"]).resolve()
    if not ledger.is_relative_to(directory) or not ledger.is_file():
        raise ValueError("Existing ledger must be inside the selected project")
    expected_hash = hashlib.sha256(json.dumps(settings, sort_keys=True, allow_nan=False).encode()).hexdigest()
    mode = "rw" if apply else "ro"
    with closing(sqlite3.connect(ledger.as_uri()+"?mode="+mode, uri=True, timeout=3)) as db:
        try:
            db.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            report = resume_dev(db, expected_batch=expected_batch, apply=apply,
                expected_owner=settings["expected_wallet"], expected_policy_hash=expected_hash,
                configured_evaluation=cfg.get("five_trade_evaluation"))
            if apply:
                db.commit()
            else:
                db.rollback()
        except BaseException:
            db.rollback()
            raise
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT/"configs/four-strategies.yaml")
    parser.add_argument("--expected-batch", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="Manually enable a fresh five-trade dev batch")
    args = parser.parse_args()
    try:
        report = run(args.config, expected_batch=args.expected_batch, apply=args.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({"applied": False, "state": "blocked", "error_type": type(error).__name__,
                          "reason": getattr(error, "reason", "Ledger or configuration verification failed"),
                          "trader_started": False}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
