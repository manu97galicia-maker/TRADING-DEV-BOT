"""Read-only Helius developer-history check; never loads a wallet or trader."""

# ruff: noqa: PLC0415, TRY003, TRY301

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import aiohttp
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/"src"))
HTTP_OK = 200


async def inspect(developer: str, config: Path) -> dict:
    from monitoring.creator_history_index import read_creator_history
    from trading.second_block_policy import load_second_policy, validate_second_policy

    start, methods = time.monotonic(), Counter()
    report = {"read_only": True, "developer": developer, "status": "unavailable"}
    try:
        plan = yaml.safe_load(config.read_text(encoding="utf-8"))
        policy = load_second_policy(config, plan)
        validate_second_policy({**policy, "enabled": True})
        env_file = (config.resolve().parent/plan["env_file"]).resolve()
        values = dotenv_values(env_file) if env_file.is_file() else {}
        endpoint = values.get("SOLANA_NODE_RPC_ENDPOINT") or os.environ.get("SOLANA_NODE_RPC_ENDPOINT")
        if not endpoint:
            raise ValueError("RPC endpoint is not configured")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async def rpc(method: str, params: list) -> object:
                if method not in {"getSlot", "getTransactionsForAddress", "getMultipleAccounts"}:
                    raise ValueError("Only history read methods are allowed")
                methods[method] += 1
                async with session.post(endpoint, json={"jsonrpc": "2.0", "id": 1,
                                                       "method": method, "params": params}) as response:
                    body = await response.json(content_type=None)
                    if response.status != HTTP_OK or body.get("error") or body.get("result") is None:
                        raise ValueError("History read unavailable")
                    return body["result"]
            snapshot = await asyncio.wait_for(read_creator_history(developer, rpc, policy),
                                              timeout=policy["history_timeout_seconds"])
        ratio = len(snapshot.migrated_curves)/len(snapshot.curves)
        report.update(status="available", source=snapshot.source, launches=len(snapshot.curves),
            verified_canonical_migrations=len(snapshot.migrated_curves), migration_fraction_lower_bound=ratio,
            history_anchor_slot=snapshot.slot, all_migration_accounts_checked=snapshot.migration_count_complete,
            meets_migration_threshold=ratio >= policy["min_creator_migration_fraction"])
    except Exception as error:  # noqa: BLE001 -- sanitized read-only diagnostic; never print a credential-bearing exception URL
        report["error_type"] = type(error).__name__
    return {**report, "read_methods": dict(methods), "elapsed_ms": round((time.monotonic()-start)*1000)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--developer", required=True)
    parser.add_argument("--config", type=Path, default=ROOT/"configs/four-strategies.yaml")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = asyncio.run(inspect(args.developer, args.config))
    rendered = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered+"\n", encoding="utf-8")
    print(rendered)
    if report["status"] != "available":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
