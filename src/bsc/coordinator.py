"""Autonomous, restart-safe coordinator for the BSC launch strategy."""

# Validation errors are intentionally explicit operator diagnostics.
# ruff: noqa: TRY003

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from bsc.gmgn_client import GmgnError, LaunchCandidate
from bsc.position_store import BscPosition, PositionStore
from bsc.strategy import BscLaunchStrategy, ExitOrder

if TYPE_CHECKING:
    from bsc.gmgn_client import GmgnClient
    from bsc.name_guard import PersistentNameGuard

MAX_OPEN_POSITIONS = 2
MAX_ERROR_BACKOFF_SECONDS = 30


@dataclass(frozen=True)
class Fill:
    """A reconciled paper or live fill."""

    quantity: Decimal
    price_usd: Decimal
    reference: str


class Broker(Protocol):
    """Execution boundary shared by paper and eventual live operation."""

    @property
    def paper(self) -> bool:
        """Return whether this broker can spend funds."""

    async def token_taxes_bps(self, candidate: LaunchCandidate) -> tuple[int, int]:
        """Return current on-chain buy and sell taxes."""

    async def buy(self, candidate: LaunchCandidate, amount_usd: Decimal) -> Fill:
        """Buy and return the reconciled fill."""

    async def sell(self, position: BscPosition, quantity: Decimal) -> Fill:
        """Sell and return the reconciled fill."""


class PaperBroker:
    """Deterministic paper broker; it never creates or signs a transaction."""

    @property
    def paper(self) -> bool:
        """Paper broker is always non-custodial."""
        return True

    async def token_taxes_bps(self, candidate: LaunchCandidate) -> tuple[int, int]:
        """Use known zero tax only in deterministic tests.

        Production paper runs must replace this broker method with an on-chain
        quote adapter. Returning zero here is explicit and visible in audit data.
        """
        del candidate
        return 0, 0

    async def buy(self, candidate: LaunchCandidate, amount_usd: Decimal) -> Fill:
        """Create a paper fill at the observed GMGN price."""
        return Fill(
            quantity=amount_usd / candidate.price_usd,
            price_usd=candidate.price_usd,
            reference=f"paper-buy-{candidate.address.lower()}",
        )

    async def sell(self, position: BscPosition, quantity: Decimal) -> Fill:
        """Create a paper sale marker; coordinator supplies trigger pricing."""
        return Fill(
            quantity=quantity,
            price_usd=position.entry_price_usd,
            reference=f"paper-sell-{position.address.lower()}",
        )


class BscCoordinator:
    """Discover, validate, enter and monitor at most two open positions."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        client: GmgnClient,
        broker: Broker,
        store: PositionStore,
        name_guard: PersistentNameGuard,
        strategy: BscLaunchStrategy | None = None,
        max_open_positions: int = MAX_OPEN_POSITIONS,
    ) -> None:
        if max_open_positions != MAX_OPEN_POSITIONS:
            raise ValueError("this strategy requires exactly two open-position slots")
        self.client = client
        self.broker = broker
        self.store = store
        self.name_guard = name_guard
        self.strategy = strategy or BscLaunchStrategy()
        self.max_open_positions = max_open_positions
        self._name_decisions: dict[str, bool] = {}

    async def run_once(self) -> None:
        """Reconcile exits first, then evaluate new entries."""
        await self._monitor_positions()
        for candidate in await self.client.launch_candidates():
            if len(self.store.open_positions()) >= self.max_open_positions:
                if candidate.address.lower() not in self.store.seen_tokens:
                    self._observe_candidate_name(candidate)
                continue
            await self._evaluate(candidate)

    async def run_forever(self, poll_seconds: float = 2.0) -> None:
        """Run continuously without allowing one data error to stop monitoring."""
        if poll_seconds < 1:
            raise ValueError("poll_seconds must be at least 1")
        while True:
            try:
                await self.run_once()
            except GmgnError:
                await asyncio.sleep(
                    min(poll_seconds * MAX_OPEN_POSITIONS, MAX_ERROR_BACKOFF_SECONDS)
                )
            else:
                await asyncio.sleep(poll_seconds)

    async def _evaluate(self, candidate: LaunchCandidate) -> None:
        key = candidate.address.lower()
        if key in self.store.seen_tokens:
            return
        if not self._observe_candidate_name(candidate):
            self.store.mark_seen(candidate.address)
            return
        try:
            history = await self.client.creator_history(candidate.creator)
            buy_tax, sell_tax = await self.broker.token_taxes_bps(candidate)
        except (GmgnError, RuntimeError, ValueError):
            return
        if not self.strategy.should_enter(
            candidate.platform,
            history,
            candidate.market_cap_usd,
            buy_tax,
            sell_tax,
        ):
            self.store.mark_seen(candidate.address)
            return
        fill = await self.broker.buy(candidate, self.strategy.entry_usd)
        if fill.quantity <= 0 or fill.price_usd <= 0:
            raise RuntimeError("broker returned an invalid buy fill")
        position = BscPosition(
            address=candidate.address,
            name=candidate.name,
            symbol=candidate.symbol,
            platform=candidate.platform,
            original_quantity=fill.quantity,
            remaining_quantity=fill.quantity,
            entry_price_usd=fill.price_usd,
            entry_market_cap_usd=candidate.market_cap_usd,
            entry_notional_usd=self.strategy.entry_usd,
            opened_at=int(time.time()),
            paper=self.broker.paper,
        )
        self.store.add_position(position)

    def _observe_candidate_name(self, candidate: LaunchCandidate) -> bool:
        key = candidate.address.lower()
        if key not in self._name_decisions:
            self._name_decisions[key] = self.name_guard.observe(candidate.name).allowed
        return self._name_decisions[key]

    async def _monitor_positions(self) -> None:
        for position in self.store.open_positions():
            price, market_cap = await self.client.token_market(position.address)
            if price <= self.strategy.stop_price(position.entry_price_usd):
                await self._sell(position, position.remaining_quantity, "stop_loss")
                continue
            for order in self.strategy.exit_orders():
                trigger_key = self._trigger_key(order)
                if trigger_key in position.completed_exit_triggers:
                    continue
                if not self._triggered(order, position, price, market_cap):
                    continue
                quantity = position.original_quantity * order.sell_fraction
                quantity = min(quantity, position.remaining_quantity)
                await self._sell(position, quantity, trigger_key)

    async def _sell(
        self, position: BscPosition, quantity: Decimal, trigger_key: str
    ) -> None:
        if quantity <= 0:
            return
        fill = await self.broker.sell(position, quantity)
        if fill.quantity <= 0 or fill.quantity > quantity:
            raise RuntimeError("broker returned an invalid sell fill")
        position.remaining_quantity -= fill.quantity
        if position.remaining_quantity < Decimal("0.000000000000000001"):
            position.remaining_quantity = Decimal("0")
        position.completed_exit_triggers.add(trigger_key)
        self.store.update_position(position)

    @staticmethod
    def _trigger_key(order: ExitOrder) -> str:
        return f"{order.trigger}:{order.value}"

    @staticmethod
    def _triggered(
        order: ExitOrder,
        position: BscPosition,
        price_usd: Decimal,
        market_cap_usd: Decimal,
    ) -> bool:
        if order.trigger == "gain":
            return price_usd >= position.entry_price_usd * (Decimal("1") + order.value)
        if order.trigger == "market_cap_usd":
            return market_cap_usd >= order.value
        raise RuntimeError(f"unknown BSC exit trigger: {order.trigger}")
