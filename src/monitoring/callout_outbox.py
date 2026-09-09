"""Durable Pump.fun callout outbox populated after confirmed buys.

Pump.fun does not currently document a public write API for callouts.  This
module therefore prepares compliant messages after a confirmed purchase and
stores them durably.  A future authorised publisher can consume this outbox
without coupling authentication or browser cookies to the trading loop.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_OUTBOX = Path("trades") / "pumpfun_callout_outbox.jsonl"


def build_callout(name: str, mint: str) -> str:
    """Build the configured callout with the required position disclosure."""
    display_name = name.strip() or "Unknown token"
    return (
        f"AI entry detected: {display_name}\n"
        f"Contract address: {mint}\n"
        "Disclosure: this automated account holds a position in this token. "
        "Not financial advice."
    )


def enqueue_callout(
    name: str,
    mint: str,
    tx_signature: str | None,
    outbox: Path = DEFAULT_OUTBOX,
) -> str:
    """Append one confirmed-buy callout to the durable JSONL outbox."""
    message = build_callout(name, mint)
    entry = {
        "created_at": datetime.now(UTC).isoformat(),
        "mint": mint,
        "name": name,
        "buy_signature": tx_signature,
        "message": message,
        "status": "pending_authorized_publisher",
    }
    try:
        outbox.parent.mkdir(parents=True, exist_ok=True)
        with outbox.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("Could not persist Pump.fun callout for %s", mint)
        raise
    logger.info("Pump.fun callout queued after confirmed buy: %s", mint)
    return message
