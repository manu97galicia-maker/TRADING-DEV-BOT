"""Universal sell via Jupiter — sell ANY SPL token to SOL, any DEX.

Used as a fallback when the pump.fun bonding-curve sell fails (e.g. the token
migrated to PumpSwap/Raydium): Jupiter routes through whatever DEX has the
liquidity, so a held position can always be exited.

Public Jupiter API (no key). Signs the returned VersionedTransaction with the
bot wallet and sends it via the configured RPC.
"""

from __future__ import annotations

import base64
import os

import aiohttp

from utils.logger import get_logger

logger = get_logger(__name__)

WSOL = "So11111111111111111111111111111111111111112"
# Jupiter's current free ("lite") endpoints (v6 quote-api is deprecated).
QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
SWAP_URL = "https://lite-api.jup.ag/swap/v1/swap"


async def jupiter_sell(
    keypair: object,
    mint: str,
    ui_amount: float,
    decimals: int = 6,
    slippage_bps: int = 2500,
    rpc: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Sell ``ui_amount`` of ``mint`` to SOL via Jupiter.

    Args:
        keypair: solders Keypair to sign with.
        mint: Token mint to sell.
        ui_amount: Human amount (decimals applied here).
        decimals: Token decimals (pump.fun = 6).
        slippage_bps: Slippage in basis points (2500 = 25%).
        rpc: RPC endpoint (defaults to SOLANA_NODE_RPC_ENDPOINT).

    Returns:
        ``(ok, signature, error)``.
    """
    rpc = rpc or os.getenv("SOLANA_NODE_RPC_ENDPOINT")
    if not rpc:
        return False, None, "RPC no configurado"
    amount = int(ui_amount * (10 ** decimals))
    if amount <= 0:
        return False, None, "cantidad cero"

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20)
        ) as s:
            params = {
                "inputMint": mint,
                "outputMint": WSOL,
                "amount": str(amount),
                "slippageBps": str(slippage_bps),
            }
            async with s.get(QUOTE_URL, params=params) as r:
                quote = await r.json()
            if not quote or not quote.get("outAmount"):
                return False, None, "sin ruta en Jupiter (¿sin liquidez?)"

            body = {
                "quoteResponse": quote,
                "userPublicKey": str(keypair.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
            }
            async with s.post(SWAP_URL, json=body) as r:
                swap = await r.json()
            tx_b64 = swap.get("swapTransaction")
            if not tx_b64:
                return False, None, f"Jupiter swap sin tx: {swap.get('error')}"
    except (aiohttp.ClientError, TimeoutError) as exc:
        return False, None, f"Jupiter API: {exc}"

    try:
        from solders.transaction import VersionedTransaction

        unsigned = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        signed = VersionedTransaction(unsigned.message, [keypair])

        from solana.rpc.async_api import AsyncClient
        from solana.rpc.types import TxOpts

        async with AsyncClient(rpc) as c:
            resp = await c.send_raw_transaction(
                bytes(signed), opts=TxOpts(skip_preflight=True, max_retries=3)
            )
            return True, str(resp.value), None
    except Exception as exc:  # noqa: BLE001 -- surface any signing/send failure
        return False, None, f"firma/envío: {exc}"
