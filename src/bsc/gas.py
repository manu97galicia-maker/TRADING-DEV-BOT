"""Gas bidding and fee estimates for BNB Smart Chain paper trading."""

# The validation messages are intentionally explicit operator diagnostics.
# ruff: noqa: TRY003

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

WEI_PER_BNB = Decimal(10**18)
GWEI_IN_WEI = Decimal(10**9)


@dataclass(frozen=True)
class RapidGasPolicy:
    """Adaptive gas policy with a strict ceiling against fee spikes."""

    buy_multiplier: Decimal = Decimal("2.5")
    sell_multiplier: Decimal = Decimal("1.25")
    max_gwei: Decimal = Decimal("5")
    max_buy_gas_usd: Decimal = Decimal("1.30")

    def bid_gwei(self, network_gwei: Decimal, *, selling: bool) -> Decimal:
        """Return a rapid bid, capped to prevent an unbounded fee."""
        if network_gwei <= 0:
            raise ValueError("network_gwei must be positive")
        multiplier = self.sell_multiplier if selling else self.buy_multiplier
        return min(network_gwei * multiplier, self.max_gwei)

    def buy_is_affordable(
        self,
        gas_units: int,
        gas_price_gwei: Decimal,
        bnb_price_usd: Decimal,
    ) -> bool:
        """Return whether the estimated buy gas stays within its USD budget."""
        return (
            gas_cost_usd(gas_units, gas_price_gwei, bnb_price_usd)
            <= self.max_buy_gas_usd
        )


def gas_cost_bnb(gas_units: int, gas_price_gwei: Decimal) -> Decimal:
    """Estimate transaction gas cost in BNB."""
    if gas_units <= 0 or gas_price_gwei <= 0:
        raise ValueError("gas units and gas price must be positive")
    return Decimal(gas_units) * gas_price_gwei * GWEI_IN_WEI / WEI_PER_BNB


def gas_cost_usd(
    gas_units: int, gas_price_gwei: Decimal, bnb_price_usd: Decimal
) -> Decimal:
    """Estimate transaction gas cost in US dollars."""
    if bnb_price_usd <= 0:
        raise ValueError("bnb_price_usd must be positive")
    return gas_cost_bnb(gas_units, gas_price_gwei) * bnb_price_usd
