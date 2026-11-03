"""Pump.fun order-flow entries without creation-time or first-block filters."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import TYPE_CHECKING

import websockets

from monitoring import volumen
from trading.token_builder import build_pumpfun_token_info

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from interfaces.core import TokenInfo

logger = logging.getLogger(__name__)
WINDOW_SECONDS = 10.0
MIN_VOLUME_USD = 1000.0
MIN_BUYERS = 5
MIN_BUY_SELL_RATIO = 2.0
MOMENTUM_SECONDS = 1.0
MAX_REFERENCE_GAP_SECONDS = 2.0
MAX_PRICE_AGE_SECONDS = 1.0
MAX_PARALLEL_READS = 4
MAX_NOTIFICATIONS = 20000
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def pressure_signal(  # noqa: PLR0911 -- each signal rejection is explicit
    flow: dict, points: list, price: float | None, sol_usd: float, now: float
) -> tuple[dict | None, str]:
    """Use only observed recent flow; absence of sells is not sufficient."""
    buy = float(flow.get("ola_compra_sol", 0))
    sell = float(flow.get("ola_venta_sol", 0))
    buyers = int(flow.get("ola_compradores", 0))
    if price is None or not all(math.isfinite(v) for v in (buy, sell, price, sol_usd)):
        return None, "missing_or_invalid_live_data"
    if min(buy, sell) < 0 or price <= 0 or sol_usd <= 0:
        return None, "invalid_live_data"
    total_usd = (buy + sell) * sol_usd
    if total_usd < MIN_VOLUME_USD:
        return None, "volume_below_1000_usd_in_10s"
    if buyers < MIN_BUYERS:
        return None, "fewer_than_five_recent_buyers"
    if buy <= sell or buy < MIN_BUY_SELL_RATIO * sell:
        return None, "buy_pressure_below_two_to_one"
    history = [
        (t, p)
        for t, p in points
        if now - MAX_REFERENCE_GAP_SECONDS <= t <= now and math.isfinite(p) and p > 0
    ]
    base = [p for t, p in history if t <= now - MOMENTUM_SECONDS]
    recent = [p for t, p in history if t > now - MOMENTUM_SECONDS]
    if not base or not recent:
        return None, "insufficient_price_history"
    previous = history[-2][1] if len(history) > 1 else price
    if price < base[-1] or price < previous:
        return None, "price_is_falling"
    return {
        "observed_at": now,
        "window_seconds": WINDOW_SECONDS,
        "volume_usd": total_usd,
        "buy_sol": buy,
        "sell_sol": sell,
        "buyers_seen": buyers,
        "buy_sell_ratio": buy / sell if sell else None,
        "price_gain_1s": price / base[-1] - 1,
        "observed_mc_usd": price * 1e9 * sol_usd,
    }, "ok"


class PressureTrialSource:
    def __init__(  # noqa: PLR0913, PLR0917 -- stream I/O and policy callbacks
        self,
        wss: str,
        implementations: object,
        client: object,
        signal_reader: Callable[[str], dict | None],
        is_consumed: Callable[[str], bool],
        is_allowed: Callable[[], bool],
    ) -> None:
        self.wss, self.implementations, self.client = wss, implementations, client
        self.signal_reader = signal_reader
        self.is_consumed = is_consumed
        self.is_allowed = is_allowed
        self.tasks = set()
        self.in_flight = set()
        self.completed = set()
        self.retry_after = {}
        self.notifications = {}
        self.evaluations = 0

    async def evaluate(
        self, mint: str, callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        try:
            token = await build_pumpfun_token_info(
                mint, self.implementations, self.client, permitir_migradas=False
            )
            if token is None:
                return
            signal = self.signal_reader(mint)
            if signal is None:
                return
            token.additional_data = {
                **(token.additional_data or {}),
                "acquisition_source": "pressure_volume",
                "origen": "pressure_volume",
                "estrategia": "presion_volumen",
                "trial_entry_features": signal,
            }
            logger.info(
                "PRESSURE candidate %s: %.0f USD/10s, %d buyers, buy %.3f SOL / sell %.3f SOL",
                mint,
                signal["volume_usd"],
                signal["buyers_seen"],
                signal["buy_sol"],
                signal["sell_sol"],
            )
            await callback(token)
            if self.is_consumed(mint):
                self.completed.add(mint)
            if len(self.completed) > MAX_NOTIFICATIONS:
                self.completed.pop()
        except Exception:
            logger.exception("Pressure candidate evaluation failed")
        finally:
            self.in_flight.discard(mint)
            self.retry_after[mint] = time.monotonic() + MAX_REFERENCE_GAP_SECONDS
            if len(self.retry_after) > MAX_NOTIFICATIONS:
                self.retry_after.clear()

    def consider(
        self, mints: list[str], callback: Callable[[TokenInfo], Awaitable[None]]
    ) -> None:
        if not self.is_allowed():
            return
        for mint in mints:
            if len(self.in_flight) >= MAX_PARALLEL_READS:
                return
            if mint in self.in_flight or mint in self.completed:
                continue
            if time.monotonic() < self.retry_after.get(mint, 0):
                continue
            if self.signal_reader(mint) is None:
                continue
            self.evaluations += 1
            self.in_flight.add(mint)
            task = asyncio.create_task(self.evaluate(mint, callback))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def run(self, callback: Callable[[TokenInfo], Awaitable[None]]) -> None:  # noqa: C901 -- subscription and reconnect boundary
        logger.info(getattr(self, "description", "PRESSURE source active: >=1000 USD/10s, >=5 buyers, buy/sell >=2, no recent price fall"))
        while True:
            try:
                async with websockets.connect(self.wss) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "logsSubscribe",
                                "params": [
                                    {"mentions": [PUMP_PROGRAM]},
                                    {"commitment": "processed"},
                                ],
                            }
                        )
                    )
                    reply = json.loads(await ws.recv())
                    if "result" not in reply:
                        message = "Pump.fun subscription rejected"
                        raise RuntimeError(message)  # noqa: TRY301 -- subscription response validation
                    logger.info("PRESSURE subscribed to Pump.fun trades")
                    while True:
                        data = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                        if data.get("method") != "logsNotification":
                            continue
                        result = data["params"]["result"]
                        value = result["value"]
                        if value.get("err") is not None:
                            continue
                        slot = result.get("context", {}).get("slot")
                        identity = (slot, value.get("signature"))
                        if identity in self.notifications:
                            continue
                        self.notifications[identity] = None
                        if len(self.notifications) > MAX_NOTIFICATIONS:
                            self.notifications.pop(next(iter(self.notifications)))
                        mints = volumen.registrar_logs(value.get("logs", []), slot=slot)
                        self.consider(mints, callback)
            except asyncio.CancelledError:
                for task in list(self.tasks):
                    task.cancel()
                await asyncio.gather(*self.tasks, return_exceptions=True)
                raise
            except Exception:
                logger.warning(
                    "Pressure stream interrupted; reconnecting", exc_info=True
                )
                await asyncio.sleep(MAX_REFERENCE_GAP_SECONDS)
