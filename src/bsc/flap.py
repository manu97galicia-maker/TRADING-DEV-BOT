"""Flap Portal adapter for BNB-native exact-input swaps."""

# The validation messages are intentionally explicit operator diagnostics.
# ruff: noqa: TRY003

from __future__ import annotations

from typing import TYPE_CHECKING

from web3 import Web3

from bsc.four_meme import (
    ERC20_ABI,
    MAX_COMBINED_TAX_BPS,
    MAX_SLIPPAGE_BPS,
    ZERO_ADDRESS,
)
from bsc.runtime import BscSafetyError

if TYPE_CHECKING:
    from decimal import Decimal

    from bsc.runtime import BscRuntime, TransactionResult


PORTAL = "0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0"

PARAM_COMPONENTS = [
    {"name": "inputToken", "type": "address"},
    {"name": "outputToken", "type": "address"},
    {"name": "inputAmount", "type": "uint256"},
    {"name": "minOutputAmount", "type": "uint256"},
    {"name": "permitData", "type": "bytes"},
]

PORTAL_ABI = [
    {
        "inputs": [
            {
                "components": PARAM_COMPONENTS,
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInput",
        "outputs": [{"name": "outputAmount", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": PARAM_COMPONENTS,
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "swapExactInput",
        "outputs": [{"name": "outputAmount", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [{"name": "token", "type": "address"}],
        "name": "getTokenV8Safe",
        "outputs": [
            {
                "components": [
                    {"name": "status", "type": "uint8"},
                    {"name": "reserve", "type": "uint256"},
                    {"name": "circulatingSupply", "type": "uint256"},
                    {"name": "price", "type": "uint256"},
                    {"name": "tokenVersion", "type": "uint8"},
                    {"name": "r", "type": "uint256"},
                    {"name": "h", "type": "uint256"},
                    {"name": "k", "type": "uint256"},
                    {"name": "dexSupplyThresh", "type": "uint256"},
                    {"name": "quoteTokenAddress", "type": "address"},
                    {"name": "nativeToQuoteSwapEnabled", "type": "bool"},
                    {"name": "extensionID", "type": "bytes32"},
                    {"name": "buyTaxRate", "type": "uint256"},
                    {"name": "sellTaxRate", "type": "uint256"},
                    {"name": "pool", "type": "address"},
                    {"name": "progress", "type": "uint256"},
                    {"name": "lpFeeProfile", "type": "uint8"},
                    {"name": "dexId", "type": "uint8"},
                ],
                "name": "state",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


class FlapAdapter:
    """Quote and execute Flap Portal swaps with slippage protection."""

    def __init__(self, runtime: BscRuntime, slippage_bps: int = 500) -> None:
        if not 0 <= slippage_bps <= MAX_SLIPPAGE_BPS:
            raise ValueError("slippage_bps must be between 0 and 2000")
        self.runtime = runtime
        self.slippage_bps = slippage_bps
        self.portal = runtime.web3.eth.contract(
            address=Web3.to_checksum_address(PORTAL), abi=PORTAL_ABI
        )

    def token_taxes_bps(self, token: str) -> tuple[int, int]:
        """Read current asymmetric tax rates from the canonical Portal."""
        state = self.portal.functions.getTokenV8Safe(
            Web3.to_checksum_address(token)
        ).call()
        return int(state[12]), int(state[13])

    def assert_tax_limit(self, token: str) -> tuple[int, int]:
        """Reject a token when its buy and sell taxes exceed 6% combined."""
        buy_rate, sell_rate = self.token_taxes_bps(token)
        if buy_rate + sell_rate > MAX_COMBINED_TAX_BPS:
            raise BscSafetyError("combined token tax exceeds 6%")
        return buy_rate, sell_rate

    def _quote(self, input_token: str, output_token: str, amount: int) -> int:
        if amount <= 0:
            raise ValueError("swap input must be positive")
        params = (
            Web3.to_checksum_address(input_token),
            Web3.to_checksum_address(output_token),
            amount,
            0,
            b"",
        )
        return int(self.portal.functions.quoteExactInput(params).call())

    def _swap_params(
        self, input_token: str, output_token: str, amount: int
    ) -> tuple[str, str, int, int, bytes]:
        quoted = self._quote(input_token, output_token, amount)
        minimum = quoted * (10_000 - self.slippage_bps) // 10_000
        return (
            Web3.to_checksum_address(input_token),
            Web3.to_checksum_address(output_token),
            amount,
            minimum,
            b"",
        )

    def buy(
        self, token: str, budget_wei: int, bnb_price_usd: Decimal
    ) -> TransactionResult:
        """Buy a Flap token with native BNB through the canonical Portal."""
        self.assert_tax_limit(token)
        params = self._swap_params(ZERO_ADDRESS, token, budget_wei)
        return self.runtime.send_transaction(
            self.portal.functions.swapExactInput(params),
            value_wei=budget_wei,
            selling=False,
            bnb_price_usd=bnb_price_usd,
        )

    def quote_buy(self, token: str, budget_wei: int) -> int:
        """Return the current output for a native-BNB buy without transacting."""
        self.assert_tax_limit(token)
        return self._quote(ZERO_ADDRESS, token, budget_wei)

    def sell(
        self, token: str, amount_wei: int, bnb_price_usd: Decimal
    ) -> tuple[TransactionResult, TransactionResult]:
        """Approve the canonical Portal and sell a Flap token for BNB."""
        token_address = Web3.to_checksum_address(token)
        erc20 = self.runtime.web3.eth.contract(address=token_address, abi=ERC20_ABI)
        approval = self.runtime.send_transaction(
            erc20.functions.approve(Web3.to_checksum_address(PORTAL), amount_wei),
            selling=True,
            bnb_price_usd=bnb_price_usd,
        )
        params = self._swap_params(token, ZERO_ADDRESS, amount_wei)
        sale = self.runtime.send_transaction(
            self.portal.functions.swapExactInput(params),
            selling=True,
            bnb_price_usd=bnb_price_usd,
        )
        return approval, sale
