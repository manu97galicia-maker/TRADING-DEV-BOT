"""Entrypoint for the isolated Four.meme + Flap coordinator."""

# Operator-facing configuration errors are intentionally explicit.
# ruff: noqa: TRY003

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from web3 import Web3

from bsc.brokers import LiveBroker, OnChainPaperBroker
from bsc.coordinator import BscCoordinator
from bsc.flap import PORTAL
from bsc.four_meme import HELPER3, TOKEN_MANAGER2
from bsc.gmgn_client import GmgnClient
from bsc.name_guard import PersistentNameGuard
from bsc.position_store import PositionStore
from bsc.runtime import BscRuntime, RuntimeSettings
from utils.logger import get_logger

LOGGER = get_logger(__name__)


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"config section {name} is missing")
    return value


def _load_config(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("BSC config could not be read") from exc
    return _mapping(raw, "root")


def _preflight(runtime: BscRuntime) -> None:
    runtime.healthcheck()
    for address in (HELPER3, TOKEN_MANAGER2, PORTAL):
        code = runtime.web3.eth.get_code(Web3.to_checksum_address(address))
        if not code:
            raise RuntimeError(f"canonical BSC contract has no bytecode: {address}")


async def _run(config_path: Path, *, once: bool, preflight: bool) -> None:
    load_dotenv()
    config = _load_config(config_path)
    mode = _mapping(config.get("mode"), "mode")
    entry = _mapping(config.get("entry"), "entry")
    positions = _mapping(config.get("positions"), "positions")
    wallet = _mapping(config.get("wallet", {}), "wallet")
    gas = _mapping(config.get("gas"), "gas")
    paper_trade = mode.get("paper_trade") is True
    live_transactions = mode.get("live_transactions") is True
    if paper_trade == live_transactions:
        raise ValueError("exactly one of paper_trade/live_transactions must be true")

    settings = RuntimeSettings.from_env(
        paper_trade=paper_trade, live_transactions=live_transactions
    )
    runtime = BscRuntime(settings)
    _preflight(runtime)
    client = GmgnClient()
    broker = (
        OnChainPaperBroker(runtime, client)
        if paper_trade
        else LiveBroker(
            runtime,
            client,
            reserve_usd=Decimal(str(wallet.get("reserve_usd", 5))),
            max_buy_gas_usd=Decimal(str(gas["max_buy_gas_usd"])),
        )
    )
    if preflight:
        candidates = await client.launch_candidates()
        LOGGER.warning(
            "BSC preflight passed in %s mode; GMGN returned %d candidates",
            "PAPER" if paper_trade else "REAL",
            len(candidates),
        )
        return
    store = PositionStore(positions.get("state_file", "reservas/bsc_positions.json"))
    guard = PersistentNameGuard(
        Path(entry.get("repeated_name_state", "reservas/bsc_recent_names.json")),
        cooldown_seconds=int(entry["reject_repeated_name_within_seconds"]),
    )
    coordinator = BscCoordinator(
        client=client,
        broker=broker,
        store=store,
        name_guard=guard,
        max_open_positions=int(positions["max_open"]),
    )
    LOGGER.warning(
        "BSC coordinator started in %s mode; no total trade cap, max 2 open",
        "PAPER" if paper_trade else "REAL",
    )
    if once:
        await coordinator.run_once()
    else:
        await coordinator.run_forever(float(config.get("poll_seconds", 2)))


def main() -> None:
    """Validate configuration and start the BSC coordinator."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("bots/bot-bsc-four-flap-paper.yaml"),
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="validate RPC, contracts, credentials and GMGN without trading",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.config, once=args.once, preflight=args.preflight))


if __name__ == "__main__":
    main()
