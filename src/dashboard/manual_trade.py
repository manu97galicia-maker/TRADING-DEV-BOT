"""Manual buy/sell from the dashboard (paste a CA to buy; sell % of a position).

Builds the same trading stack the bot uses (SolanaClient + Wallet +
PriorityFeeManager + platform buyer/seller) from the .env RPC + private key, so
the panel can trade on demand. pump.fun only for now.

SAFETY: localhost-only endpoints (see server.py). Real-money execution is
UNVERIFIED in this workspace — test with a tiny amount. Requires the full deps
(`uv sync`) and a working RPC.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

TRADES_LOG = Path("trades") / "trades.log"
DEFAULT_SLIPPAGE = 0.25
DEFAULT_FIXED_FEE = 500_000


def _log_trade(action: str, mint: str, symbol: str, price: float, amount: float, sig: str) -> None:
    try:
        TRADES_LOG.parent.mkdir(exist_ok=True)
        with TRADES_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": datetime.utcnow().isoformat(),
                "action": action, "platform": "pump_fun",
                "token_address": mint, "symbol": symbol,
                "price": price, "amount": amount, "tx_hash": sig,
            }) + "\n")
    except OSError:
        logger.exception("manual trade log failed")


class ManualTrader:
    """Lazily-built trading stack for on-demand manual buys/sells."""

    def __init__(self) -> None:
        self._impls = None
        self._client = None
        self._wallet = None
        self._pfm = None

    async def _ensure(self) -> None:
        if self._impls is not None:
            return
        rpc = os.getenv("SOLANA_NODE_RPC_ENDPOINT")
        key = os.getenv("SOLANA_PRIVATE_KEY")
        if not rpc or not key:
            raise ValueError("Falta SOLANA_NODE_RPC_ENDPOINT o SOLANA_PRIVATE_KEY en .env")
        from core.client import SolanaClient
        from core.priority_fee.manager import PriorityFeeManager
        from core.wallet import Wallet
        from interfaces.core import Platform
        from platforms import get_platform_implementations

        self._client = SolanaClient(rpc, max_rps=25)
        self._wallet = Wallet(key)
        self._pfm = PriorityFeeManager(
            client=self._client, enable_dynamic_fee=False, enable_fixed_fee=True,
            fixed_fee=DEFAULT_FIXED_FEE, extra_fee=0.0, hard_cap=DEFAULT_FIXED_FEE,
        )
        self._impls = get_platform_implementations(Platform.PUMP_FUN, self._client)

    async def _token_info(self, mint: str):
        from trading.token_builder import build_pumpfun_token_info

        return await build_pumpfun_token_info(mint, self._impls, self._client)

    async def buy(self, mint: str, amount_sol: float) -> dict[str, Any]:
        """Buy ``amount_sol`` of a pump.fun token now."""
        await self._ensure()
        if amount_sol <= 0:
            return {"ok": False, "error": "cantidad inválida"}
        ti = await self._token_info(mint)
        if ti is None:
            return {"ok": False, "error": "token no válido / migrado / no legible"}
        from trading.platform_aware import PlatformAwareBuyer

        buyer = PlatformAwareBuyer(
            self._client, self._wallet, self._pfm, amount_sol, DEFAULT_SLIPPAGE,
            2, 0, False, compute_units={},
        )
        res = await buyer.execute(ti)
        if res.success:
            _log_trade("buy", str(ti.mint), ti.symbol, res.price, res.amount, str(res.tx_signature))
            return {"ok": True, "signature": str(res.tx_signature), "amount": res.amount, "price": res.price}
        return {"ok": False, "error": res.error_message or "compra fallida"}

    async def sell(self, mint: str, pct: float) -> dict[str, Any]:
        """Sell ``pct`` percent (25/50/75/100) of the held position at market."""
        await self._ensure()
        if not (0 < pct <= 100):
            return {"ok": False, "error": "porcentaje inválido"}
        ti = await self._token_info(mint)
        if ti is None:
            return {"ok": False, "error": "token no legible"}

        from solana.rpc.async_api import AsyncClient
        from spl.token.instructions import get_associated_token_address

        ata = get_associated_token_address(self._wallet.pubkey, ti.mint, ti.token_program_id)
        rpc = os.getenv("SOLANA_NODE_RPC_ENDPOINT")
        async with AsyncClient(rpc) as c:
            bal = await c.get_token_account_balance(ata)
            held = float(bal.value.ui_amount or 0)
        if held <= 0:
            return {"ok": False, "error": "no tienes saldo de ese token"}

        amount = held * pct / 100.0
        curve = self._impls.curve_manager
        try:
            price = await curve.calculate_price(ti.bonding_curve)
        except Exception:  # noqa: BLE001
            price = 0.0
        from trading.platform_aware import PlatformAwareSeller

        seller = PlatformAwareSeller(
            self._client, self._wallet, self._pfm, DEFAULT_SLIPPAGE, 2, compute_units={},
        )
        res = await seller.execute(ti, token_amount=amount, token_price=price)
        if res.success:
            _log_trade("sell", str(ti.mint), ti.symbol, res.price, res.amount, str(res.tx_signature))
            return {"ok": True, "signature": str(res.tx_signature), "sold": amount, "pct": pct}

        # Fallback: migrated token -> sell via Jupiter (any DEX).
        from trading.jupiter_seller import jupiter_sell

        ok, sig, err = await jupiter_sell(
            self._wallet.keypair, str(ti.mint), amount,
            slippage_bps=int(DEFAULT_SLIPPAGE * 10000),
        )
        if ok:
            _log_trade("sell", str(ti.mint), ti.symbol, price, amount, sig)
            return {"ok": True, "signature": sig, "sold": amount, "pct": pct, "via": "jupiter"}
        return {"ok": False, "error": f"curva: {res.error_message} · jupiter: {err}"}
