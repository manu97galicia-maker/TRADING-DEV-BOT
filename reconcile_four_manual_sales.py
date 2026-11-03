"""Default: audit manual sales only. --apply requires the trading process lock."""

# No signer is imported. RPC errors are reported without endpoint/credential text.
# ruff: noqa: PLC0415, TRY003, TRY301, BLE001, S310

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
READ_METHODS = {"getGenesisHash", "getTokenAccountsByOwner", "getTransaction", "getSignaturesForAddress"}
GENESIS = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"


def main() -> None:  # noqa: C901 -- separate read-only and process-locked apply paths
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cfg = yaml.safe_load((ROOT / "configs/four-strategies.yaml").read_text())
    settings = cfg["four_strategies"]
    path = ROOT / settings["database"]
    owner = settings["expected_wallet"]
    policy = hashlib.sha256(json.dumps(settings, sort_keys=True, allow_nan=False).encode()).hexdigest()
    lock = None
    try:
        if args.apply:
            import fcntl

            lock = path.with_suffix(".process.lock").open("a+b")
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("SELECT owner,policy FROM settings WHERE id=1").fetchone() != (owner, policy):
                raise ValueError("Ledger owner or policy differs")
            positions = [{"nonce": n, "mint": m} for n, m in db.execute("SELECT nonce,mint FROM positions ORDER BY created")]
            receipts = {p["nonce"]: db.execute(
                "SELECT signature,kind FROM receipts WHERE nonce=? AND kind!='rejected' ORDER BY rowid", (p["nonce"],)
            ).fetchall() for p in positions}
        values = dotenv_values(ROOT / "configs" / cfg.get("env_file", "../.env"), interpolate=False)
        endpoint = values.get("SOLANA_NODE_RPC_ENDPOINT", "")
        if not endpoint.startswith("https://"):
            raise ValueError("HTTPS RPC required")

        def rpc(method: str, params: list) -> object:
            if method not in READ_METHODS:
                raise ValueError("Only public RPC reads are allowed")
            time.sleep(1.1)
            request = urllib.request.Request(endpoint, json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            ).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read())
            if data.get("error") or data.get("result") is None:
                raise ValueError("RPC evidence unavailable")
            return data["result"]

        if rpc("getGenesisHash", []) != GENESIS:
            raise ValueError("Mainnet evidence required")
        from trading.manual_sale_audit import audit_manual_sale

        audits = [audit_manual_sale(p, receipts[p["nonce"]], owner, rpc) for p in positions]
        # All audits finish successfully before the first database mutation.
        if args.apply:
            from trading.four_strategy_book import FourStrategyBook

            book = FourStrategyBook(path, owner, policy,
                max_positions=cfg.get("trade", {}).get("max_posiciones", 3),
                evaluation_policy=cfg.get("five_trade_evaluation"))
            for audit in audits:
                if audit["state"] != "ready":
                    continue
                book.settle(audit["nonce"], audit["net_lamports"], audit["context_slot"], {
                    "verified_zero_holdings": True, "commitment": "finalized",
                    "signatures": audit["signatures"],
                    "previous_signatures": [s for s, _ in audit["known_receipts"]],
                    "manual_reconciliation": True,
                    "buy_debit_lamports": audit["buy_debit_lamports"],
                    "buy_debit_verified": audit["buy_debit_verified"],
                }, additional_receipts=audit["additional_receipts"])
        print(json.dumps({"mode": "apply" if args.apply else "read_only_plan", "wallet": owner,
            "positions": audits, "trader_started": False}, ensure_ascii=False, indent=2))
    except Exception as error:
        print(f"Conciliación detenida ({type(error).__name__}); no se inicia trading.", file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    main()
