"""Guarded BNB Chain transaction runtime.

The runtime is intentionally platform-agnostic.  Platform adapters construct a
contract function; this class simulates it, estimates/caps gas, signs locally,
submits it and reconciles the receipt.  Live mode has several independent gates
so changing a YAML boolean cannot spend funds by accident.
"""

# The validation messages are intentionally explicit operator diagnostics.
# ruff: noqa: TRY003

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from web3 import HTTPProvider, Web3

from bsc.gas import RapidGasPolicy, gas_cost_usd

if TYPE_CHECKING:
    from eth_account.signers.local import LocalAccount
    from web3.contract.contract import ContractFunction

EXPECTED_CHAIN_ID = 56
LIVE_CONFIRMATION = "I_UNDERSTAND_BSC_LIVE_TRADING"


class BscSafetyError(RuntimeError):
    """Raised when a BSC safety condition is not satisfied."""


@dataclass(frozen=True)
class RuntimeSettings:
    """Runtime settings containing no secret values."""

    rpc_http: str
    paper_trade: bool = True
    live_transactions: bool = False
    receipt_timeout_seconds: int = 90
    gas_limit_multiplier: Decimal = Decimal("1.20")
    max_transaction_gas: int = 15_000_000

    @classmethod
    def from_env(
        cls, *, paper_trade: bool = True, live_transactions: bool = False
    ) -> RuntimeSettings:
        """Load the RPC URL without ever logging it."""
        rpc_http = os.environ.get("BSC_RPC_HTTP", "").strip()
        if not rpc_http:
            raise BscSafetyError("BSC_RPC_HTTP is required")
        return cls(
            rpc_http=rpc_http,
            paper_trade=paper_trade,
            live_transactions=live_transactions,
        )


@dataclass(frozen=True)
class TransactionResult:
    """Reconciled result of one submitted transaction."""

    tx_hash: str
    block_number: int
    gas_used: int
    effective_gas_price: int


class BscRuntime:
    """Simulate and, only when explicitly unlocked, submit BSC transactions."""

    def __init__(
        self,
        settings: RuntimeSettings,
        gas_policy: RapidGasPolicy | None = None,
    ) -> None:
        self.settings = settings
        self.gas_policy = gas_policy or RapidGasPolicy()
        self.web3 = Web3(
            HTTPProvider(settings.rpc_http, request_kwargs={"timeout": 20})
        )
        self._account: LocalAccount | None = None

    def healthcheck(self) -> None:
        """Verify RPC connectivity and reject every chain except BSC mainnet."""
        if not self.web3.is_connected():
            raise BscSafetyError("BSC RPC is not reachable")
        if self.web3.eth.chain_id != EXPECTED_CHAIN_ID:
            raise BscSafetyError("RPC is not BNB Smart Chain mainnet (chain 56)")

    def unlock_live_signer(self) -> str:
        """Unlock the signer only after all independent live gates pass."""
        if self.settings.paper_trade or not self.settings.live_transactions:
            raise BscSafetyError("live transactions are disabled by configuration")
        if os.environ.get("BSC_ENABLE_LIVE_TRADING") != LIVE_CONFIRMATION:
            raise BscSafetyError("BSC_ENABLE_LIVE_TRADING confirmation is missing")
        private_key = os.environ.get("BSC_PRIVATE_KEY", "").strip()
        if not private_key:
            raise BscSafetyError("BSC_PRIVATE_KEY is missing")
        try:
            account = self.web3.eth.account.from_key(private_key)
        except (TypeError, ValueError) as exc:
            raise BscSafetyError("BSC_PRIVATE_KEY is invalid") from exc
        self._account = account
        return account.address

    @property
    def wallet_address(self) -> str:
        """Return the public wallet address without exposing key material."""
        if self._account is None:
            raise BscSafetyError("live signer is locked")
        return self._account.address

    def build_transaction(
        self,
        function: ContractFunction,
        *,
        value_wei: int,
        selling: bool,
        bnb_price_usd: Decimal,
    ) -> dict[str, Any]:
        """Simulate and build a bounded legacy-gas BSC transaction."""
        account = self.wallet_address
        base = {"from": account, "value": value_wei}
        function.call(base)
        estimated = function.estimate_gas(base)
        gas_limit = int(Decimal(estimated) * self.settings.gas_limit_multiplier)
        if gas_limit > self.settings.max_transaction_gas:
            raise BscSafetyError("estimated transaction gas exceeds safety cap")

        network_gwei = Decimal(self.web3.from_wei(self.web3.eth.gas_price, "gwei"))
        bid_gwei = self.gas_policy.bid_gwei(network_gwei, selling=selling)
        if not selling and not self.gas_policy.buy_is_affordable(
            gas_limit, bid_gwei, bnb_price_usd
        ):
            cost = gas_cost_usd(gas_limit, bid_gwei, bnb_price_usd)
            raise BscSafetyError(f"estimated buy gas ${cost:.2f} exceeds $1.30")

        return function.build_transaction(
            {
                **base,
                "chainId": EXPECTED_CHAIN_ID,
                "nonce": self.web3.eth.get_transaction_count(account, "pending"),
                "gas": gas_limit,
                "gasPrice": self.web3.to_wei(bid_gwei, "gwei"),
            }
        )

    def send_transaction(
        self,
        function: ContractFunction,
        *,
        value_wei: int = 0,
        selling: bool = False,
        bnb_price_usd: Decimal,
    ) -> TransactionResult:
        """Sign, submit and require a successful mined receipt."""
        if self._account is None:
            raise BscSafetyError("live signer is locked")
        transaction = self.build_transaction(
            function,
            value_wei=value_wei,
            selling=selling,
            bnb_price_usd=bnb_price_usd,
        )
        signed = self._account.sign_transaction(transaction)
        tx_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=self.settings.receipt_timeout_seconds
        )
        if receipt.status != 1:
            raise BscSafetyError(f"transaction reverted: {tx_hash.hex()}")
        return TransactionResult(
            tx_hash=tx_hash.hex(),
            block_number=int(receipt.blockNumber),
            gas_used=int(receipt.gasUsed),
            effective_gas_price=int(receipt.effectiveGasPrice),
        )
