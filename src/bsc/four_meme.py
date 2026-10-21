"""Four.meme V2 curve adapter for native-BNB pairs."""

# The validation messages are intentionally explicit operator diagnostics.
# ruff: noqa: TRY003

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from bsc.runtime import BscRuntime, BscSafetyError, TransactionResult

if TYPE_CHECKING:
    from decimal import Decimal

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
HELPER3 = "0xF251F83e40a78868FcfA3FA4599Dad6494E46034"
TOKEN_MANAGER2 = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"  # noqa: S105
MAX_SLIPPAGE_BPS = 2_000
MAX_COMBINED_TAX_BPS = 600

HELPER_ABI = [
    {
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "funds", "type": "uint256"},
        ],
        "name": "tryBuy",
        "outputs": [
            {"name": "tokenManager", "type": "address"},
            {"name": "quote", "type": "address"},
            {"name": "estimatedAmount", "type": "uint256"},
            {"name": "estimatedCost", "type": "uint256"},
            {"name": "estimatedFee", "type": "uint256"},
            {"name": "amountMsgValue", "type": "uint256"},
            {"name": "amountApproval", "type": "uint256"},
            {"name": "amountFunds", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "trySell",
        "outputs": [
            {"name": "tokenManager", "type": "address"},
            {"name": "quote", "type": "address"},
            {"name": "funds", "type": "uint256"},
            {"name": "fee", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

MANAGER_ABI = [
    {
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "funds", "type": "uint256"},
            {"name": "minAmount", "type": "uint256"},
        ],
        "name": "buyTokenAMAP",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "origin", "type": "uint256"},
            {"name": "token", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "minFunds", "type": "uint256"},
            {"name": "feeRate", "type": "uint256"},
            {"name": "feeRecipient", "type": "address"},
        ],
        "name": "sellToken",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

ERC20_ABI = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

TAX_ABI = [
    {
        "inputs": [],
        "name": name,
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
    for name in ("feeRate", "buyFeeRate", "sellFeeRate")
]


@dataclass(frozen=True)
class BuyQuote:
    """A Four.meme quote ready for a slippage-protected buy."""

    expected_tokens: int
    minimum_tokens: int
    message_value: int
    funds_argument: int
    fee_wei: int


class FourMemeAdapter:
    """Quote and execute Four.meme V2 native-BNB curve trades."""

    def __init__(self, runtime: BscRuntime, slippage_bps: int = 500) -> None:
        if not 0 <= slippage_bps <= MAX_SLIPPAGE_BPS:
            raise ValueError("slippage_bps must be between 0 and 2000")
        self.runtime = runtime
        self.slippage_bps = slippage_bps
        self.helper = runtime.web3.eth.contract(
            address=Web3.to_checksum_address(HELPER3), abi=HELPER_ABI
        )
        self.manager = runtime.web3.eth.contract(
            address=Web3.to_checksum_address(TOKEN_MANAGER2), abi=MANAGER_ABI
        )

    def token_taxes_bps(self, token: str) -> tuple[int, int]:
        """Read Four.meme token taxes, treating standard tokens as zero-tax."""
        contract = self.runtime.web3.eth.contract(
            address=Web3.to_checksum_address(token), abi=TAX_ABI
        )

        def optional_rate(name: str) -> int | None:
            try:
                return int(getattr(contract.functions, name)().call())
            except (BadFunctionCallOutput, ContractLogicError, ValueError):
                return None

        buy_rate = optional_rate("buyFeeRate")
        sell_rate = optional_rate("sellFeeRate")
        if buy_rate is not None and sell_rate is not None:
            return buy_rate, sell_rate
        symmetric = optional_rate("feeRate")
        if symmetric is not None:
            return symmetric, symmetric
        return 0, 0

    def assert_tax_limit(self, token: str) -> tuple[int, int]:
        """Reject a token when its buy and sell taxes exceed 6% combined."""
        buy_rate, sell_rate = self.token_taxes_bps(token)
        if buy_rate < 0 or sell_rate < 0:
            raise BscSafetyError("invalid negative token tax")
        if buy_rate + sell_rate > MAX_COMBINED_TAX_BPS:
            raise BscSafetyError("combined token tax exceeds 6%")
        return buy_rate, sell_rate

    def quote_buy(self, token: str, budget_wei: int) -> BuyQuote:
        """Quote a native-BNB budget buy and enforce the canonical V2 manager."""
        if budget_wei <= 0:
            raise ValueError("budget_wei must be positive")
        self.assert_tax_limit(token)
        token_address = Web3.to_checksum_address(token)
        result = self.helper.functions.tryBuy(token_address, 0, budget_wei).call()
        manager, quote = result[0], result[1]
        if Web3.to_checksum_address(manager) != Web3.to_checksum_address(
            TOKEN_MANAGER2
        ):
            raise BscSafetyError("token is not routed to Four.meme V2")
        if Web3.to_checksum_address(quote) != Web3.to_checksum_address(ZERO_ADDRESS):
            raise BscSafetyError("only native-BNB Four.meme pairs are enabled")
        expected = int(result[2])
        minimum = expected * (10_000 - self.slippage_bps) // 10_000
        minimum -= minimum % 1_000_000_000
        return BuyQuote(
            expected_tokens=expected,
            minimum_tokens=minimum,
            message_value=int(result[5]),
            funds_argument=int(result[7]),
            fee_wei=int(result[4]),
        )

    def buy(
        self, token: str, budget_wei: int, bnb_price_usd: Decimal
    ) -> TransactionResult:
        """Execute a quoted, simulated and gas-bounded curve buy."""
        quote = self.quote_buy(token, budget_wei)
        function = self.manager.functions.buyTokenAMAP(
            Web3.to_checksum_address(token),
            quote.funds_argument,
            quote.minimum_tokens,
        )
        return self.runtime.send_transaction(
            function,
            value_wei=quote.message_value,
            selling=False,
            bnb_price_usd=bnb_price_usd,
        )

    def sell(
        self, token: str, amount_wei: int, bnb_price_usd: Decimal
    ) -> tuple[TransactionResult, TransactionResult]:
        """Approve and sell tokens with a conservative minimum-funds bound."""
        if amount_wei <= 0:
            raise ValueError("amount_wei must be positive")
        token_address = Web3.to_checksum_address(token)
        manager, quote_asset, funds, _fee = self.helper.functions.trySell(
            token_address, amount_wei
        ).call()
        if Web3.to_checksum_address(manager) != Web3.to_checksum_address(
            TOKEN_MANAGER2
        ):
            raise BscSafetyError("token is not routed to Four.meme V2")
        if Web3.to_checksum_address(quote_asset) != Web3.to_checksum_address(
            ZERO_ADDRESS
        ):
            raise BscSafetyError("only native-BNB Four.meme pairs are enabled")

        erc20 = self.runtime.web3.eth.contract(address=token_address, abi=ERC20_ABI)
        approval = self.runtime.send_transaction(
            erc20.functions.approve(
                Web3.to_checksum_address(TOKEN_MANAGER2), amount_wei
            ),
            selling=True,
            bnb_price_usd=bnb_price_usd,
        )
        minimum_funds = int(funds) * (10_000 - self.slippage_bps) // 10_000
        sale = self.runtime.send_transaction(
            self.manager.functions.sellToken(
                0,
                token_address,
                amount_wei,
                minimum_funds,
                0,
                Web3.to_checksum_address(ZERO_ADDRESS),
            ),
            selling=True,
            bnb_price_usd=bnb_price_usd,
        )
        return approval, sale
