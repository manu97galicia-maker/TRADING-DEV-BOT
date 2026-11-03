"""Read-only batch status. Never creates a ledger, starts a trader or sends orders."""

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


def inspect(config: Path) -> dict:
    from trading.five_trade_evaluation import (
        STRATEGIES,
        evaluation_report,
        validate_evaluation_policy,
    )

    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    policy = cfg.get("five_trade_evaluation")
    validate_evaluation_policy(policy)
    settings = cfg["four_strategies"]
    ledger = (config.resolve().parent.parent/settings["database"]).resolve()
    legacy_pauses = []
    if ledger.exists():
        with closing(sqlite3.connect(ledger.as_uri()+"?mode=ro", uri=True, timeout=2)) as db:
            expected = hashlib.sha256(json.dumps(settings, sort_keys=True, allow_nan=False).encode()).hexdigest()
            if db.execute("SELECT owner,policy FROM settings WHERE id=1").fetchone() != (settings["expected_wallet"], expected):
                raise ValueError("Ledger owner or policy differs")
            batches = evaluation_report(db)
            legacy_pauses = [r[0] for r in db.execute("SELECT strategy FROM pauses ORDER BY strategy")]
    else:
        batches = {"state": "not_started", "strategies": {strategy: {"current": {
            "state": "not_started", "admitted": 0, "closed": 0, "net_roi_percent": None}}
            for strategy in STRATEGIES}}
    stored = batches.get("policy") or policy or {}
    legacy_active = stored.get("legacy_loss_guard", "keep_three_in_five") == "keep_three_in_five"
    for strategy, data in batches["strategies"].items():
        data["blocked_by_legacy_loss_guard"] = legacy_active and strategy in legacy_pauses
    return {"read_only": True, "prepared_max_positions": cfg["trade"]["max_posiciones"],
        "prepared_stop_loss_fraction": cfg["trade"]["stop_loss_percentage"],
        "configured_evaluation": policy, "legacy_loss_guard_active": legacy_active,
        "recorded_legacy_pauses": legacy_pauses, "evaluation": batches, "trader_started": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT/"configs/four-strategies.yaml")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.config), indent=2, ensure_ascii=False, allow_nan=False))
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"read_only": True, "state": "unavailable", "error_type": type(error).__name__}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
