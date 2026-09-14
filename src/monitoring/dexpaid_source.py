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
from time import monotonic

from interfaces.core import TokenInfo
from trading.token_builder import build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)


def build_monitor_heartbeat(
    poll_interval: float,
    fresh_signals: int,
    max_payment_age_minutes: float,
    max_token_age_minutes: float,
    *,
    paper_mode: bool,
) -> str:
    """Build the periodic Telegram health message for the API monitor."""
    mode = "PAPER" if paper_mode else "REAL"
    token_age = (
        f"moneda ≤{max_token_age_minutes:g}m"
        if max_token_age_minutes > 0
        else "edad de moneda sin límite"
    )
    return (
        "🛰 <b>MONITOR ACTIVO</b>\n"
        f"DexScreener: última revisión correcta · cada {poll_interval:g}s\n"
        f"Señales frescas desde el último aviso: <b>{fresh_signals}</b>\n"
        f"Filtros: pago ≤{max_payment_age_minutes:g}m · {token_age}\n"
        f"Modo: <b>{mode}</b>"
    )


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


class DexPaidFreshnessTracker:
    """Accept only events appearing after startup or a real boost increase."""

    def __init__(self) -> None:
        self._boost_totals: dict[str, float] = {}
        self._paid_mints: set[str] = set()

    def prime(self, candidates: list[dict]) -> None:
        """Record the startup snapshot without treating it as a signal."""
        for candidate in candidates:
            self._remember(candidate)

    def accept(self, candidate: dict) -> bool:
        """Return true once for a new paid mint or increased/new boost."""
        mint = str(candidate.get("mint") or "")
        if not mint:
            return False
        if candidate.get("signal_type") == "boost":
            total = float(candidate.get("boosts") or 0)
            previous = self._boost_totals.get(mint)
            self._boost_totals[mint] = max(total, previous or 0)
            return previous is None or total > previous
        is_new = mint not in self._paid_mints
        self._paid_mints.add(mint)
        return is_new

    def _remember(self, candidate: dict) -> None:
        mint = str(candidate.get("mint") or "")
        if not mint:
            return
        if candidate.get("signal_type") == "boost":
            self._boost_totals[mint] = float(candidate.get("boosts") or 0)
        else:
            self._paid_mints.add(mint)


class DexPaidSource:
    """Polls dex-paid tokens and emits TokenInfos for buying."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        platform_impls: object,
        client: object,
        poll_interval: float = 30.0,
        max_age_minutes: float = 180.0,
        max_token_age_minutes: float = 30.0,
        normal_buy_amount: float = 0.15,
        small_boost_buy_amount: float = 0.17,
        boost_threshold: float = 500.0,
        boost_buy_amount: float = 0.20,
        *,
        require_volume_h24: bool = False,
        heartbeat_interval: float = 300.0,
        paper_mode: bool = True,
    ) -> None:
        self._impls = platform_impls
        self._client = client
        self.poll_interval = poll_interval
        self.max_age_minutes = max_age_minutes
        self.max_token_age_minutes = max_token_age_minutes
        self.normal_buy_amount = normal_buy_amount
        self.small_boost_buy_amount = small_boost_buy_amount
        self.boost_threshold = boost_threshold
        self.boost_buy_amount = boost_buy_amount
        self.require_volume_h24 = require_volume_h24
        self.heartbeat_interval = heartbeat_interval
        self.paper_mode = paper_mode
        self._seen: set[str] = set()
        self._freshness = DexPaidFreshnessTracker()
        self._primed = False
        self._last_heartbeat = 0.0
        self._fresh_signals = 0

    def _send_heartbeat_if_due(self) -> None:
        """Send one best-effort status message at the configured interval."""
        now = monotonic()
        if self.heartbeat_interval <= 0:
            return
        if (
            self._last_heartbeat
            and now - self._last_heartbeat < self.heartbeat_interval
        ):
            return
        from monitoring.notifier import notify

        notify(
            build_monitor_heartbeat(
                self.poll_interval,
                self._fresh_signals,
                self.max_age_minutes,
                self.max_token_age_minutes,
                paper_mode=self.paper_mode,
            )
        )
        self._last_heartbeat = now
        self._fresh_signals = 0

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
                    limit=10,
                    max_age_minutes=self.max_age_minutes,
                    max_token_age_minutes=self.max_token_age_minutes,
                    check_locked=False,
                )
                if not self._primed:
                    self._freshness.prime(candidates)
                    self._primed = True
                    logger.info(
                        "DexPaid startup baseline recorded (%d entries, no buys)",
                        len(candidates),
                    )
                    self._send_heartbeat_if_due()
                    await asyncio.sleep(self.poll_interval)
                    continue
                for c in candidates:
                    if (
                        self.require_volume_h24
                        and float(c.get("volume_h24") or 0) <= 0
                    ):
                        continue
                    mint = c.get("mint")
                    if not mint or mint in self._seen:
                        continue
                    if not self._freshness.accept(c):
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
                        self._fresh_signals += 1
                        logger.info(
                            "Fresh DexPaid %s -> %s (%s), token age %s min",
                            c.get("signal_type"),
                            token_info.symbol,
                            mint,
                            c.get("token_age_min"),
                        )
                        await token_callback(token_info)
                self._send_heartbeat_if_due()
            except asyncio.CancelledError:
                logger.info("DexPaid source cancelled")
                break
            except Exception:
                logger.exception("DexPaid source error")
            await asyncio.sleep(self.poll_interval)
