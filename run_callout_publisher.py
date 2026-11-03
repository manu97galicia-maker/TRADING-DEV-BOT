"""Run only the callout publisher. This entry point cannot start a trader."""

from __future__ import annotations

# Optional services load only for the requested mode; errors are redacted.
# ruff: noqa: PLC0415, BLE001
import argparse
import asyncio
import importlib.util
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--setup",
        action="store_true",
        help="Open a dedicated Chrome profile; sign in manually",
    )
    modes.add_argument(
        "--run",
        action="store_true",
        help="Publish eligible queued buys; does not trade",
    )
    args = parser.parse_args()
    plan = yaml.safe_load(
        (ROOT / "configs/four-strategies.yaml").read_text(encoding="utf-8")
    )
    settings = plan["four_strategies"]
    publication = settings["callout_publication"]
    if not args.setup and not args.run:
        from monitoring.callout_publisher import pending_entries

        path = ROOT / publication["state_database"]
        counts = {}
        if path.exists():
            with closing(
                sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            ) as db:
                counts = dict(
                    db.execute(
                        "SELECT status,COUNT(*) FROM publications GROUP BY status"
                    ).fetchall()
                )
        print(
            json.dumps(
                {
                    "account": settings["callout_account"],
                    "wallet": settings["expected_wallet"],
                    "publication_configured": publication["enabled"],
                    "browser_dependency_installed": importlib.util.find_spec(
                        "playwright"
                    )
                    is not None,
                    "dedicated_session_prepared": (
                        ROOT / publication["profile_directory"] / "publisher-ready.json"
                    ).exists(),
                    "queued_entries": len(
                        pending_entries(ROOT / settings["callout_outbox"])
                    ),
                    "publication_results": counts,
                    "trader_started": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if Path.cwd().resolve() != ROOT:
        parser.error("Run from the repository root")
    if args.run and not publication["enabled"]:
        parser.error("Callout publication is disabled")
    if importlib.util.find_spec("playwright") is None:
        parser.error(
            "Install the optional publisher dependency: pip install playwright"
        )
    if (
        args.run
        and not (
            ROOT / publication["profile_directory"] / "publisher-ready.json"
        ).exists()
    ):
        parser.error(
            "Prepare the dedicated session first: run_callout_publisher.py --setup"
        )
    from dotenv import dotenv_values

    from monitoring.callout_publisher import browser_service

    values = dotenv_values(ROOT / ".env", interpolate=False)
    endpoint = values.get("SOLANA_NODE_RPC_ENDPOINT") or os.getenv(
        "SOLANA_NODE_RPC_ENDPOINT", ""
    )
    if not args.setup and not endpoint.startswith("https://"):
        parser.error("A configured HTTPS RPC endpoint is required")
    try:
        asyncio.run(browser_service(settings, endpoint, setup=args.setup))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(
            f"Publicador detenido ({type(error).__name__}). No se ha iniciado trading."
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
