"""Pure strategy rules for Four.meme and Flap launches on BNB Chain.

This module deliberately contains no wallet or transaction-signing code.  It is
safe to exercise in unit tests and in the paper engine before an EVM executor is
introduced.
"""

# The validation message is intentionally an explicit operator diagnostic.
# ruff: noqa: TRY003

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

SUPPORTED_PLATFORMS = frozenset({"four_meme", "fourmeme", "flap"})


@dataclass(frozen=True)
class DeveloperHistory:
    """Observed launch history for one token creator."""

    launches: int
    migrations: int

    @property
    def migration_rate(self) -> Decimal:
        """Return successful migrations divided by all observed launches."""
        if self.launches <= 0:
            return Decimal("0")
        return Decimal(self.migrations) / Decimal(self.launches)


@dataclass(frozen=True)
class ExitOrder:
    """One planned sale, expressed as a fraction of the original token amount."""

    trigger: str
    value: Decimal
    sell_fraction: Decimal


@dataclass(frozen=True)
class BscLaunchStrategy:
    """Validated paper strategy requested for BSC launchpads."""

    entry_usd: Decimal = Decimal("25")
    max_entry_market_cap_usd: Decimal = Decimal("9000")
    max_combined_tax_bps: int = 600
    min_migrations: int = 2
    min_migration_rate: Decimal = Decimal("0.08")
    stop_loss: Decimal = Decimal("0.38")
    recover_at_gain: Decimal = Decimal("0.60")
    recover_fraction: Decimal = Decimal("0.625")

    def creator_qualifies(self, history: DeveloperHistory) -> bool:
        """Return whether the creator passes both historical requirements."""
        return (
            history.migrations >= self.min_migrations
            and history.migrations <= history.launches
            and history.migration_rate >= self.min_migration_rate
        )

    def should_enter(
        self,
        platform: str,
        history: DeveloperHistory,
        market_cap_usd: Decimal,
        buy_tax_bps: int = 0,
        sell_tax_bps: int = 0,
    ) -> bool:
        """Return whether a paper candidate is eligible for entry."""
        return (
            platform in SUPPORTED_PLATFORMS
            and self.creator_qualifies(history)
            and Decimal("0") < market_cap_usd < self.max_entry_market_cap_usd
            and self.taxes_qualify(buy_tax_bps, sell_tax_bps)
        )

    def taxes_qualify(self, buy_tax_bps: int, sell_tax_bps: int) -> bool:
        """Require known non-negative taxes whose combined rate is at most 6%."""
        return (
            buy_tax_bps >= 0
            and sell_tax_bps >= 0
            and buy_tax_bps + sell_tax_bps <= self.max_combined_tax_bps
        )

    def exit_orders(self) -> tuple[ExitOrder, ...]:
        """Return the complete exit ladder.

        Selling 62.5% after a 60% gain returns exactly the initial notional
        before fees: 0.625 * 1.60 == 1.00.  The remaining 37.5% is allocated
        fully, leaving no accidental dust.
        """
        distributed = Decimal("0.0458333333333333333333333333")
        final_distributed = Decimal("0.275") - (distributed * 5)
        return (
            ExitOrder("gain", self.recover_at_gain, self.recover_fraction),
            *(
                ExitOrder("market_cap_usd", Decimal(mc), distributed)
                for mc in range(10_000, 30_001, 5_000)
            ),
            ExitOrder("market_cap_usd", Decimal("35000"), final_distributed),
            ExitOrder("market_cap_usd", Decimal("180000"), Decimal("0.05")),
            ExitOrder("market_cap_usd", Decimal("400000"), Decimal("0.025")),
            ExitOrder("market_cap_usd", Decimal("900000"), Decimal("0.025")),
        )

    def stop_price(self, entry_price: Decimal) -> Decimal:
        """Return the hard stop price, 38% below the executed entry price."""
        if entry_price <= 0:
            raise ValueError("entry_price must be positive")
        return entry_price * (Decimal("1") - self.stop_loss)


def allocated_fraction(orders: tuple[ExitOrder, ...]) -> Decimal:
    """Return the fraction of the original position allocated to exits."""
    return sum((order.sell_fraction for order in orders), start=Decimal("0"))
