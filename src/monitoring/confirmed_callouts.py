"""Prepare one durable callout per confirmed buy without delaying TP/SL.

This module does not publish to pump.fun or mark an entry as published.
An authenticated publisher must verify a current position before posting.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from monitoring.callout_outbox import build_callout, choose_intro
from utils.logger import get_logger

logger = get_logger(__name__)
DEFAULT_OUTBOX = Path("trades/pumpfun_confirmed_callouts.jsonl")
_pending: set[asyncio.Task[str]] = set()


def queue_confirmed_callout(
    name: str,
    mint: str,
    signature: str,
    *,
    outbox: Path = DEFAULT_OUTBOX,
    strategy_name: str = "",
    publisher_account: str = "",
) -> str:
    """Record a confirmed-buy event once, even across processes and restarts."""
    if not signature or not signature.strip():
        message = "A confirmed buy signature is required"
        raise ValueError(message)
    outbox = Path(outbox)
    outbox.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(outbox) + ".lock.sqlite3", timeout=2)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            used_intros: set[str] = set()
            if outbox.exists():
                with outbox.open(encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(row, dict):
                            continue
                        used_intros.add(
                            row.get("intro")
                            or str(row.get("message", "")).split(":", 1)[0]
                        )
                        if (
                            isinstance(row, dict)
                            and row.get("mint") == mint
                            and row.get("buy_signature") == signature
                        ):
                            return str(row["message"])
            intro = choose_intro(used_intros)
            message = build_callout(name, mint, intro=intro)
            entry = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "mint": mint,
                "name": name,
                "buy_signature": signature,
                "strategy_name": strategy_name,
                "publisher_account": publisher_account,
                "coin_url": f"https://pump.fun/coin/{mint}",
                "message": message,
                "intro": intro,
                "published_at": None,
                "status": "pending_authenticated_publisher",
            }
            with outbox.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            logger.info("Confirmed-buy callout prepared, not published: %s", mint)
            return message
    finally:
        db.close()


def schedule_confirmed_callout(
    name: str,
    mint: str,
    signature: str,
    *,
    outbox: Path = DEFAULT_OUTBOX,
    strategy_name: str = "",
    publisher_account: str = "",
) -> asyncio.Task[str]:
    """Offload disk locking and writing so price monitoring starts immediately."""
    task = asyncio.create_task(
        asyncio.to_thread(
            queue_confirmed_callout,
            name,
            mint,
            signature,
            outbox=outbox,
            strategy_name=strategy_name,
            publisher_account=publisher_account,
        )
    )
    _pending.add(task)

    def finished(done: asyncio.Task[str]) -> None:
        _pending.discard(done)
        if not done.cancelled() and (error := done.exception()) is not None:
            logger.error(
                "Could not prepare confirmed-buy callout for %s",
                mint,
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(finished)
    return task
