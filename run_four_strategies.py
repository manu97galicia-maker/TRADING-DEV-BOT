"""Validate the opt-in bundle by default. No imports of wallets in check mode."""

# Lazy platform/runtime imports are required so validation never loads wallets.
# Literal risk thresholds below intentionally validate the fixed user contract.
# ruff: noqa: PLC0415, PLR2004, TRY003

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def check_plan(path: Path) -> dict:
    """Validate explicit shared risk settings without loading credentials."""
    from trading.review_exit_ladder import build_review_ladder

    plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    trade = plan["trade"]
    settings = plan["four_strategies"]
    if trade["max_posiciones"] != 3 or trade["fraccion_saldo"] != 0.20:
        raise ValueError("Bundle requires three positions and a 20% fraction")
    if (
        trade["stop_loss_percentage"] != 0.25
        or settings["exit"]["stop_loss_fraction"] != 0.25
    ):
        raise ValueError("Bundle requires a 25% initial stop")
    required = {"strong-pressure-8k", "strength-21k", "first-block-four-buyers"}
    if set(settings["strategies"]) != required:
        raise ValueError(
            "Exactly three on-chain policies and the Telegram source are required"
        )
    if not plan["filters"]["telegram_calls"].get("channels"):
        raise ValueError("Telegram channels must be explicitly restricted")
    if plan["filters"]["telegram_calls"].get("migrated_channels"):
        raise ValueError("Migrated purchases are outside this bundle")
    for key in required:
        policy = settings["strategies"][key]
        if policy["min_buyers"] <= 0 or policy["min_volume_usd"] <= 0:
            raise ValueError("Positive demand thresholds are required")
    build_review_ladder(8000, settings["exit"])
    build_review_ladder(21000, settings["exit"])
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs/four-strategies.yaml"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--run",
        action="store_true",
        help="Explicit execution; configuration must also have enabled: true",
    )
    modes.add_argument(
        "--preflight", action="store_true", help="Read-only local readiness checks"
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Check configured services using read methods only",
    )
    parser.add_argument(
        "--report", type=Path, help="Save the sanitized preflight report"
    )
    args = parser.parse_args()
    plan = check_plan(args.config)
    if (args.online or args.report) and not args.preflight:
        parser.error("--online and --report require --preflight")
    if args.preflight:
        from trading.four_strategy_preflight import inspect_local, inspect_online

        report = (
            asyncio.run(inspect_online(plan, args.config))
            if args.online
            else inspect_local(plan, args.config)
        )
        rendered = json.dumps(report, indent=2, ensure_ascii=False)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered, encoding="utf-8")
        print(rendered)
        if report["status"] == "blocked":
            raise SystemExit(2)
        return
    if not args.run:
        print(
            json.dumps(
                {
                    "valid": True,
                    "mode": "validation_only",
                    "strategies": [
                        *plan["four_strategies"]["strategies"],
                        "telegram-immediate",
                    ],
                    "max_shared_positions": 3,
                    "entry_fraction_inclusive": 0.20,
                    "initial_stop": 0.25,
                    "enabled": plan["enabled"],
                },
                indent=2,
            )
        )
        return
    if not plan.get("enabled"):
        parser.error("The configuration is disabled. Validation does not activate it.")
    if Path.cwd().resolve() != ROOT:
        parser.error(
            "Run from the repository root so state and Telegram session paths agree"
        )
    from trading.four_strategy_preflight import inspect_online

    readiness = asyncio.run(inspect_online(plan, args.config))
    if not readiness["ready_for_manual_start"]:
        print(json.dumps(readiness, indent=2, ensure_ascii=False))
        parser.error("Read-only preflight found blockers; no trader was started")
    import bot_runner

    # A process lock prevents duplicated websocket collectors on this ledger.
    lock_path = Path(plan["four_strategies"]["database"]).with_suffix(".process.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as process_lock:
        if sys.platform == "win32":
            import msvcrt

            process_lock.seek(0)
            process_lock.write(b"0")
            process_lock.flush()
            process_lock.seek(0)
            msvcrt.locking(process_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(bot_runner.start_bot(str(args.config)))


if __name__ == "__main__":
    main()
