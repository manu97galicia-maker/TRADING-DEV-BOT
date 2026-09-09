"""Acquisition source from Telegram calls.

Wraps TelegramWatcher: every contract address posted in your channels is
(1) logged for the dashboard "Telegram calls" panel, and (2) if it matches
this bot's platform, rebuilt into a TokenInfo and handed to the buy path,
where the DexScreener filter (dex paid / locked liquidity / not migrated)
still applies. Non-buyable calls (wrong platform, e.g. moon/bags on a
pump.fun bot) are alert-only.

WARNING: the existing-mint buy path is UNVERIFIED against a live buy here.
Telegram calls are also high-risk (scams/rugs) — rely on the filter and a
tiny buy_amount.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from interfaces.core import Platform, TokenInfo
from monitoring.telegram_watcher import TelegramWatcher
from trading.token_builder import build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)

CALLS_LOG = Path("trades") / "telegram_calls.log"
_NITRO_RE = re.compile(r"\bnitro(?:\s+score)?\D{0,12}(\d{3,5})\b", re.IGNORECASE)


def _telegram_signal_amount(
    text: str, config: dict, source: str = ""
) -> tuple[float, float]:
    """Return (buy amount, detected NITRO score) for a listing message."""
    normal = float(config.get("normal_buy_amount", 0) or 0)
    nitro_amount = float(config.get("nitro_buy_amount", normal) or normal)
    promoted = float(config.get("promoted_buy_amount", normal) or normal)
    threshold = float(config.get("promotion_threshold", 500) or 500)
    scores = [float(match) for match in _NITRO_RE.findall(text or "")]
    score = max(scores, default=0.0)
    source_key = source.lower().lstrip("@").replace(" ", "")
    if score >= threshold:
        amount = promoted
    elif score > 0 or source_key == "dextools_nitro":
        amount = nitro_amount
    else:
        amount = normal
    return amount, score


def _log_call(call: dict, bought: bool) -> None:
    """Append a detected call to the dashboard-readable log."""
    try:
        CALLS_LOG.parent.mkdir(exist_ok=True)
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "address": call.get("address"),
            "platform": call.get("platform"),
            "chat": call.get("chat"),
            "text": call.get("text"),
            "bought_attempt": bought,
        }
        with CALLS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        logger.exception("Failed to log Telegram call")


class TelegramSource:
    """Stream Telegram token calls into alerts + the buy path."""

    def __init__(
        self,
        config: dict,
        platform_impls: object,
        client: object,
        platform: Platform,
    ) -> None:
        self._config = config
        self._impls = platform_impls
        self._client = client
        self._platform = platform

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Connect to Telegram and route calls to alerts + buys."""
        import os

        # Prefer config, fall back to environment (.env). Env is the safer
        # place for these credentials.
        api_id = self._config.get("api_id") or os.getenv("TELEGRAM_API_ID")
        api_hash = self._config.get("api_hash") or os.getenv("TELEGRAM_API_HASH")
        if not api_id or not api_hash:
            logger.error(
                "Telegram source: set TELEGRAM_API_ID/TELEGRAM_API_HASH in .env"
            )
            return

        suffixes = tuple(
            self._config.get("suffixes") or ("pump", "bonk", "moon", "bags")
        )
        watcher = TelegramWatcher(
            api_id=int(api_id),
            api_hash=str(api_hash),
            session_name=self._config.get("session_name", "tg_watcher"),
            suffixes=suffixes,
            channels=self._config.get("channels"),
        )

        async def on_call(call: dict) -> None:
            # Only buy calls that match THIS bot's platform; others alert only.
            buyable = call.get("platform") == self._platform.value
            token_info = None
            if buyable and self._platform == Platform.PUMP_FUN:
                token_info = await build_pumpfun_token_info(
                    call["address"], self._impls, self._client
                )
            if token_info is not None:
                source = call.get("chat_username") or call.get("chat") or "telegram"
                signal_amount, nitro_score = _telegram_signal_amount(
                    call.get("text", ""), self._config, source=source
                )
                token_info.additional_data = {
                    **(token_info.additional_data or {}),
                    "acquisition_source": source,
                    "dextools_nitro_score": nitro_score,
                    "signal_buy_amount": signal_amount,
                }
            _log_call(call, bought=token_info is not None)
            if token_info is not None:
                logger.info(
                    "Telegram call -> buying %s (from %s)",
                    call["address"],
                    call.get("chat"),
                )
                await token_callback(token_info)

        await watcher.run(on_call)
