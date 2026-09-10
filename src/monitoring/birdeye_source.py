"""Acquisition source: buy pump.fun tokens entering Birdeye's trending list.

Polls the trending endpoint and emits a candidate the first time a mint
appears, so the signal is "it just became trending", not "it is trending".
The startup snapshot is recorded without buying, the same way the dex-paid
source primes itself.

Reality check: trending is dominated by established tokens, and
``build_pumpfun_token_info`` rejects anything whose bonding curve is complete.
Most entrants are therefore discarded as migrated — that is expected, and the
counters in the heartbeat show it rather than hiding it.

WARNING: the buy path for existing mints is UNVERIFIED against a live buy in
this workspace — enable with a tiny buy_amount and watch the logs first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic

from birdeye.client import BirdeyeClient, BirdeyeTrendingToken
from interfaces.core import TokenInfo
from trading.token_builder import build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)


def build_trending_heartbeat(
    poll_interval: float,
    entrants: int,
    buyable: int,
    *,
    configured: bool,
    paper_mode: bool,
) -> str:
    """Build the periodic Telegram health message for the trending monitor."""
    if not configured:
        return (
            "🐦 <b>BIRDEYE SIN CLAVE</b>\n"
            "Falta BIRDEYE_API_KEY en el .env: la fuente no emite señales."
        )
    mode = "PAPER" if paper_mode else "REAL"
    return (
        "🐦 <b>BIRDEYE TENDENCIAS</b>\n"
        f"Lista revisada · cada {poll_interval:g}s\n"
        f"Nuevas entradas desde el último aviso: <b>{entrants}</b>\n"
        f"De ellas comprables en curva: <b>{buyable}</b>\n"
        f"Modo: <b>{mode}</b>"
    )


def passes_market_floor(
    token: BirdeyeTrendingToken,
    min_liquidity_usd: float,
    min_volume_h24: float,
) -> bool:
    """Return whether an entrant clears the configured liquidity/volume floors.

    A missing value fails a floor that is set: the point of the floor is to
    require evidence, and absent data is not evidence.
    """
    if min_liquidity_usd > 0 and (
        token.liquidity_usd is None or token.liquidity_usd < min_liquidity_usd
    ):
        return False
    return not (
        min_volume_h24 > 0
        and (token.volume_h24 is None or token.volume_h24 < min_volume_h24)
    )


class TrendingEntrantTracker:
    """Accept a mint once: the first time it appears after startup."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def prime(self, tokens: list[BirdeyeTrendingToken]) -> None:
        """Record the startup snapshot without treating it as a signal."""
        for token in tokens:
            self._seen.add(token.address)

    def accept(self, token: BirdeyeTrendingToken) -> bool:
        """Return true only the first time this mint is offered."""
        if not token.address or token.address in self._seen:
            return False
        self._seen.add(token.address)
        return True


class BirdeyeTrendingSource:
    """Polls Birdeye trending and emits TokenInfos for buying."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        platform_impls: object,
        client: object,
        poll_interval: float = 30.0,
        limit: int = 20,
        buy_amount: float = 0.15,
        min_liquidity_usd: float = 0.0,
        min_volume_h24: float = 0.0,
        *,
        heartbeat_interval: float = 300.0,
        paper_mode: bool = True,
        birdeye: BirdeyeClient | None = None,
    ) -> None:
        self._impls = platform_impls
        self._client = client
        self._birdeye = birdeye or BirdeyeClient()
        self.poll_interval = poll_interval
        self.limit = limit
        self.buy_amount = buy_amount
        self.min_liquidity_usd = min_liquidity_usd
        self.min_volume_h24 = min_volume_h24
        self.heartbeat_interval = heartbeat_interval
        self.paper_mode = paper_mode
        self._tracker = TrendingEntrantTracker()
        self._primed = False
        self._last_heartbeat = 0.0
        self._entrants = 0
        self._buyable = 0

    def _send_heartbeat_if_due(self) -> None:
        """Send one best-effort status message at the configured interval."""
        now = monotonic()
        if self.heartbeat_interval <= 0:
            return
        if self._last_heartbeat and now - self._last_heartbeat < (
            self.heartbeat_interval
        ):
            return
        from monitoring.notifier import notify

        notify(
            build_trending_heartbeat(
                self.poll_interval,
                self._entrants,
                self._buyable,
                configured=self._birdeye.configured,
                paper_mode=self.paper_mode,
            )
        )
        self._last_heartbeat = now
        self._entrants = 0
        self._buyable = 0

    async def _emit(
        self,
        token: BirdeyeTrendingToken,
        token_callback: Callable[[TokenInfo], Awaitable[None]],
    ) -> None:
        """Build and hand over one entrant, if it is still on its curve."""
        token_info = await build_pumpfun_token_info(
            token.address,
            self._impls,
            self._client,
            symbol=token.symbol or "",
            name=token.name or "",
        )
        if token_info is None:
            return
        token_info.additional_data = {
            **(token_info.additional_data or {}),
            "acquisition_source": "birdeye_trending",
            "birdeye_rank": token.rank,
            "birdeye_volume_h24": token.volume_h24,
            "signal_buy_amount": self.buy_amount,
        }
        self._buyable += 1
        logger.info(
            "Birdeye trending entrant -> %s (%s), rank %s, vol24h %s",
            token_info.symbol,
            token.address,
            token.rank,
            token.volume_h24,
        )
        await token_callback(token_info)

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Continuously poll trending and emit new, non-migrated entrants."""
        if not self._birdeye.configured:
            logger.error(
                "Birdeye trending source: set BIRDEYE_API_KEY in .env; not starting"
            )
            return
        logger.info(
            "Birdeye trending source active: poll every %ss, top %s",
            self.poll_interval,
            self.limit,
        )
        try:
            while True:
                try:
                    tokens = await self._birdeye.get_trending_tokens(limit=self.limit)
                    if not self._primed:
                        self._tracker.prime(tokens)
                        self._primed = True
                        logger.info(
                            "Birdeye trending baseline recorded (%d entries, no buys)",
                            len(tokens),
                        )
                    else:
                        for token in tokens:
                            if not self._tracker.accept(token):
                                continue
                            self._entrants += 1
                            if not passes_market_floor(
                                token, self.min_liquidity_usd, self.min_volume_h24
                            ):
                                continue
                            await self._emit(token, token_callback)
                    self._send_heartbeat_if_due()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Birdeye trending source error")
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("Birdeye trending source cancelled")
        finally:
            await self._birdeye.close()
