"""Paper and guarded-live brokers for the BSC coordinator."""

# Safety diagnostics are intentionally explicit.
# ruff: noqa: TRY003

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from web3 import Web3

from bsc.coordinator import Fill, PaperBroker
from bsc.flap import FlapAdapter
from bsc.four_meme import FourMemeAdapter
from bsc.runtime import BscRuntime, BscSafetyError

if TYPE_CHECKING:
    from bsc.gmgn_client import GmgnClient, LaunchCandidate
    from bsc.position_store import BscPosition

WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
BALANCE_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


class OnChainPaperBroker(PaperBroker):
    """Paper fills with taxes read from the canonical launchpad contract."""

    def __init__(self, runtime: BscRuntime, client: GmgnClient) -> None:
        self.runtime = runtime
        self.client = client
        self.adapters = {
            "four_meme": FourMemeAdapter(runtime),
            "fourmeme": FourMemeAdapter(runtime),
            "flap": FlapAdapter(runtime),
        }

    async def token_taxes_bps(self, candidate: LaunchCandidate) -> tuple[int, int]:
        """Read tax fields on chain and reject unknown platforms."""
        adapter = self.adapters.get(candidate.platform)
        if adapter is None:
            raise BscSafetyError("unsupported BSC launchpad")
        return adapter.assert_tax_limit(candidate.address)

    async def buy(self, candidate: LaunchCandidate, amount_usd: Decimal) -> Fill:
        """Require a successful canonical on-chain quote before a paper fill."""
        bnb_price, _ = await self.client.token_market(WBNB)
        budget_wei = int(
            (amount_usd / bnb_price * Decimal(10**18)).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        adapter = self.adapters.get(candidate.platform)
        if adapter is None:
            raise BscSafetyError("unsupported BSC launchpad")
        quote = adapter.quote_buy(candidate.address, budget_wei)
        quoted_amount = (
            quote.expected_tokens if isinstance(adapter, FourMemeAdapter) else quote
        )
        if quoted_amount <= 0:
            raise BscSafetyError("canonical launchpad returned an empty buy quote")
        return await super().buy(candidate, amount_usd)


class LiveBroker:
    """Guarded real broker that reconciles every fill from wallet balances."""

    def __init__(
        self,
        runtime: BscRuntime,
        client: GmgnClient,
        *,
        reserve_usd: Decimal = Decimal("5"),
        max_buy_gas_usd: Decimal = Decimal("1.30"),
    ) -> None:
        if runtime.settings.paper_trade or not runtime.settings.live_transactions:
            raise BscSafetyError("live broker requires explicitly unlocked settings")
        if reserve_usd < 0:
            raise ValueError("reserve_usd cannot be negative")
        if max_buy_gas_usd < 0:
            raise ValueError("max_buy_gas_usd cannot be negative")
        runtime.unlock_live_signer()
        self.runtime = runtime
        self.client = client
        self.reserve_usd = reserve_usd
        self.max_buy_gas_usd = max_buy_gas_usd
        self.adapters = {
            "four_meme": FourMemeAdapter(runtime),
            "fourmeme": FourMemeAdapter(runtime),
            "flap": FlapAdapter(runtime),
        }

    @property
    def paper(self) -> bool:
        """Live broker can spend funds."""
        return False

    async def token_taxes_bps(self, candidate: LaunchCandidate) -> tuple[int, int]:
        """Read current token taxes from the canonical platform contract."""
        return self._adapter(candidate.platform).assert_tax_limit(candidate.address)

    async def buy(self, candidate: LaunchCandidate, amount_usd: Decimal) -> Fill:
        """Buy, wait for success and derive acquired quantity from balance delta."""
        bnb_price, _ = await self.client.token_market(WBNB)
        budget_wei = int(
            (amount_usd / bnb_price * Decimal(10**18)).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        if budget_wei <= 0:
            raise BscSafetyError("calculated BNB budget is zero")
        balance = self.runtime.web3.eth.get_balance(self.runtime.wallet_address)
        protected_usd = self.reserve_usd + self.max_buy_gas_usd
        protected_wei = int(
            (protected_usd / bnb_price * Decimal(10**18)).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        if balance - budget_wei < protected_wei:
            raise BscSafetyError(
                "entry would consume the $5 BNB reserve after maximum buy gas"
            )
        before = self._token_balance(candidate.address)
        receipt = self._adapter(candidate.platform).buy(
            candidate.address, budget_wei, bnb_price
        )
        acquired = self._token_balance(candidate.address) - before
        if acquired <= 0:
            raise BscSafetyError("buy receipt succeeded but token balance did not rise")
        return Fill(
            quantity=Decimal(acquired),
            price_usd=candidate.price_usd,
            reference=receipt.tx_hash,
        )

    async def sell(self, position: BscPosition, quantity: Decimal) -> Fill:
        """Sell an integral raw-token quantity and reconcile the balance decrease."""
        raw_quantity = int(quantity.to_integral_value(rounding=ROUND_DOWN))
        if raw_quantity <= 0:
            raise BscSafetyError("sell quantity rounds to zero")
        bnb_price, _ = await self.client.token_market(WBNB)
        before = self._token_balance(position.address)
        _approval, sale = self._adapter(position.platform).sell(
            position.address, raw_quantity, bnb_price
        )
        sold = before - self._token_balance(position.address)
        if sold <= 0 or sold > raw_quantity:
            raise BscSafetyError("sell receipt and token balance are inconsistent")
        return Fill(
            quantity=Decimal(sold),
            price_usd=position.entry_price_usd,
            reference=sale.tx_hash,
        )

    def _adapter(self, platform: str) -> FourMemeAdapter | FlapAdapter:
        adapter = self.adapters.get(platform)
        if adapter is None:
            raise BscSafetyError("unsupported BSC launchpad")
        return adapter

    def _token_balance(self, token: str) -> int:
        contract = self.runtime.web3.eth.contract(
            address=Web3.to_checksum_address(token), abi=BALANCE_ABI
        )
        return int(contract.functions.balanceOf(self.runtime.wallet_address).call())
