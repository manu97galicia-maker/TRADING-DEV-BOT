"""Acquisition source: buy tokens that recently paid DexScreener.

Polls the dex-paid feed, rebuilds a TokenInfo for each fresh, non-migrated
pump.fun candidate, and hands it to the trader's normal buy path (where the
DexScreener filter still applies). This is the "comprar al pagar dex" trigger.

Measured reality: a paid order is visible minutes after payment, so this polls
on an interval rather than reacting in milliseconds.

WARNING: the buy path for existing mints is UNVERIFIED against a live buy in
this workspace — enable with a tiny buy_amount and watch the logs first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from interfaces.core import TokenInfo
from trading.token_builder import build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)


def _dexpaid_signal_amount(
    boosts: float,
    normal_amount: float,
    small_boost_amount: float,
    expensive_threshold: float,
    expensive_amount: float,
) -> float:
    """Choose the configured buy size for a paid listing or boost tier."""
    if boosts >= expensive_threshold:
        return expensive_amount
    if boosts > 0:
        return small_boost_amount
    return normal_amount


class DexPaidSource:
    """Polls dex-paid tokens and emits TokenInfos for buying."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        platform_impls: object,
        client: object,
        poll_interval: float = 30.0,
        max_age_minutes: float = 180.0,
        normal_buy_amount: float = 0.15,
        small_boost_buy_amount: float = 0.17,
        boost_threshold: float = 500.0,
        boost_buy_amount: float = 0.20,
    ) -> None:
        self._impls = platform_impls
        self._client = client
        self.poll_interval = poll_interval
        self.max_age_minutes = max_age_minutes
        self.normal_buy_amount = normal_buy_amount
        self.small_boost_buy_amount = small_boost_buy_amount
        self.boost_threshold = boost_threshold
        self.boost_buy_amount = boost_buy_amount
        self._seen: set[str] = set()

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Continuously poll and emit new, non-migrated dex-paid candidates."""
        from dashboard.dexpaid_feed import get_candidates

        logger.info(
            "DexPaid source active: poll every %ss, max age %s min",
            self.poll_interval,
            self.max_age_minutes,
        )
        while True:
            try:
                candidates = await get_candidates(
                    limit=10, max_age_minutes=self.max_age_minutes, check_locked=False
                )
                for c in candidates:
                    mint = c.get("mint")
                    if not mint or mint in self._seen:
                        continue
                    if c.get("migrated"):
                        continue  # only bonding-curve buys
                    token_info = await build_pumpfun_token_info(
                        mint, self._impls, self._client, symbol=c.get("symbol", "")
                    )
                    if token_info is not None:
                        boosts = float(c.get("boosts") or 0)
                        signal_amount = _dexpaid_signal_amount(
                            boosts,
                            self.normal_buy_amount,
                            self.small_boost_buy_amount,
                            self.boost_threshold,
                            self.boost_buy_amount,
                        )
                        token_info.additional_data = {
                            **(token_info.additional_data or {}),
                            "acquisition_source": "dexscreener",
                            "dexscreener_boosts": boosts,
                            "signal_buy_amount": signal_amount,
                        }
                        self._seen.add(mint)
                        logger.info(
                            "DexPaid candidate -> %s (%s), paid %s min ago",
                            token_info.symbol,
                            mint,
                            c.get("payment_age_min"),
                        )
                        await token_callback(token_info)
            except asyncio.CancelledError:
                logger.info("DexPaid source cancelled")
                break
            except Exception:
                logger.exception("DexPaid source error")
            await asyncio.sleep(self.poll_interval)
