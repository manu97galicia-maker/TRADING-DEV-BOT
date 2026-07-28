"""GMGN signals: copy what Smart Money / KOLs are buying.

Reads GMGN's on-chain tracking via the authenticated ``gmgn-cli`` (configured
with your API key). Surfaces pump.fun tokens that smart money / KOLs just
BOUGHT, so YOUR bot can buy them with YOUR wallet through the normal filter +
buy path. GMGN is used for READING only — execution stays local.

``gmgn-cli`` must be installed and configured (``gmgn-cli config --apply``).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from interfaces.core import TokenInfo
from trading.token_builder import build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)


def _gmgn_bin() -> str | None:
    return shutil.which("gmgn-cli") or shutil.which("gmgn-cli.cmd")


async def fetch_signals(
    source: str = "smartmoney",
    chain: str = "sol",
    min_usd: float = 0.0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch recent BUY trades from GMGN (smartmoney or kol).

    Returns pump.fun buys as ``{mint, symbol, wallet, usd, tags, launchpad}``,
    newest first, deduped by mint.
    """
    binary = _gmgn_bin()
    if binary is None:
        logger.warning("gmgn-cli not found on PATH")
        return []
    cmd = [binary, "track", source, "--chain", chain, "--side", "buy",
           "--limit", str(limit), "--raw"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=25)
    except (OSError, asyncio.TimeoutError) as exc:
        logger.warning("gmgn-cli %s failed: %s", source, exc)
        return []
    try:
        data = json.loads(out.decode("utf-8", "ignore"))
    except json.JSONDecodeError:
        return []

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in data.get("list", []) or []:
        if it.get("side") != "buy":
            continue
        mint = it.get("base_address")
        if not mint or mint in seen:
            continue
        usd = float(it.get("amount_usd") or 0)
        if usd < min_usd:
            continue
        bt = it.get("base_token") or {}
        seen.add(mint)
        rows.append({
            "mint": mint,
            "symbol": bt.get("symbol") or "?",
            "launchpad": bt.get("launchpad"),
            "wallet": it.get("maker"),
            "usd": round(usd, 2),
            "tags": (it.get("maker_info") or {}).get("tags") or [],
            "source": source,
            "timestamp": it.get("timestamp"),
        })
    return rows


class GmgnSource:
    """Poll GMGN smart-money/KOL buys and emit pump.fun buy candidates."""

    def __init__(
        self,
        config: dict,
        platform_impls: object,
        client: object,
    ) -> None:
        self._config = config
        self._impls = platform_impls
        self._client = client
        self._seen: set[str] = set()

    async def run(
        self, token_callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        sources = self._config.get("sources") or ["smartmoney", "kol"]
        min_usd = float(self._config.get("min_usd", 0) or 0)
        poll = float(self._config.get("poll_interval", 20) or 20)
        logger.info("GMGN source active: %s, min $%s, every %ss", sources, min_usd, poll)
        while True:
            try:
                for src in sources:
                    for sig in await fetch_signals(src, min_usd=min_usd):
                        mint = sig["mint"]
                        if mint in self._seen or sig.get("launchpad") != "pump":
                            continue
                        token_info = await build_pumpfun_token_info(
                            mint, self._impls, self._client, symbol=sig["symbol"]
                        )
                        if token_info is not None:
                            self._seen.add(mint)
                            logger.info(
                                "GMGN %s buy -> %s (%s, $%s)",
                                src, sig["symbol"], mint, sig["usd"],
                            )
                            await token_callback(token_info)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("GMGN source error")
            await asyncio.sleep(poll)
