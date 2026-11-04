"""Universal sell via Jupiter — sell ANY SPL token to SOL, any DEX.

Used as a fallback when the pump.fun bonding-curve sell fails (e.g. the token
migrated to PumpSwap/Raydium): Jupiter routes through whatever DEX has the
liquidity, so a held position can always be exited.

Public Jupiter API (no key). Signs the returned VersionedTransaction with the
bot wallet and sends it via the configured RPC.

Three failure modes this used to hit, all found while rescuing a real stuck
position (2026-07-31). Together they meant the fallback silently did nothing and
tokens piled up in the wallet with no position to claim them:

1. **Oversized transaction.** An unconstrained quote happily routes through
   three hops (Pump.fun -> GoonFi -> PancakeSwap), which serialises to ~1400
   bytes. Solana's hard limit is 1232, so the RPC rejected it outright.
   ``restrictIntermediateTokens`` keeps the route to sane intermediates and
   brings it back under the limit.
2. **Selling 100% overflows the curve.** pump.fun's program throws
   ``Custom 6024`` (Overflow) when asked to sell an entire balance on a thin
   curve. Backing off to 90% goes through.
3. **``skip_preflight=True`` hid both.** The RPC returned a signature, this
   function returned ``ok=True``, and the transaction failed on-chain a slot
   later. The caller logged a successful sale that never happened.

So now: constrain the route, simulate before sending, and if the simulation
fails, retry with a smaller slice instead of reporting a phantom success.
"""

from __future__ import annotations

import base64
import json
import os

import aiohttp

from trading.sell_precision import raw_sell_amount
from trading.jupiter_access import API_BASE, jupiter_headers, wait_jupiter_slot
from utils.logger import get_logger

logger = get_logger(__name__)

WSOL = "So11111111111111111111111111111111111111112"
# Official unified endpoint; optional API key and keyless spacing are shared.
QUOTE_URL = f"{API_BASE}/swap/v1/quote"
SWAP_URL = f"{API_BASE}/swap/v1/swap"

#: Hard protocol limit for a serialised Solana transaction.
MAX_TX_BYTES = 1232

#: Fractions of the balance to try, in order. 100% first because that is what
#: we actually want; the rest are the escape hatches for curve overflow and for
#: routes that only fit when the trade is smaller.
FRACCIONES = (1.0, 0.9, 0.5, 0.25)


async def _cotizar(
    s: aiohttp.ClientSession, mint: str, amount: int, slippage_bps: int
) -> dict | None:
    params = {
        "inputMint": mint,
        "outputMint": WSOL,
        "amount": str(amount),
        "slippageBps": str(slippage_bps),
        # Sin esto Jupiter encadena 3 saltos y la tx no cabe en 1232 bytes.
        "restrictIntermediateTokens": "true",
    }
    await wait_jupiter_slot()
    async with s.get(QUOTE_URL, params=params, headers=jupiter_headers()) as r:
        if r.status != 200:
            return None
        quote = await r.json()
    return quote if quote and quote.get("outAmount") else None


async def _cotizar_compra(
    s: aiohttp.ClientSession, mint: str, lamports: int, slippage_bps: int
) -> dict | None:
    """Cotiza SOL -> token. Es el espejo de `_cotizar`, con los mints al reves."""
    params = {
        "inputMint": WSOL,
        "outputMint": mint,
        "amount": str(lamports),
        "slippageBps": str(slippage_bps),
        "restrictIntermediateTokens": "true",
    }
    await wait_jupiter_slot()
    async with s.get(QUOTE_URL, params=params, headers=jupiter_headers()) as r:
        if r.status != 200:
            return None
        quote = await r.json()
    return quote if quote and quote.get("outAmount") else None


async def _construir(
    s: aiohttp.ClientSession, quote: dict, keypair: object
) -> tuple[bytes, int] | None:
    """Devuelve la transaccion firmada y su tamano, o None si Jupiter falla."""
    body = {
        "quoteResponse": quote,
        "userPublicKey": str(keypair.pubkey()),
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
    }
    from trading.transaction_observer import PRIORITY_CAP

    cap = PRIORITY_CAP.get()
    if cap is not None:
        body["prioritizationFeeLamports"] = cap
    await wait_jupiter_slot()
    async with s.post(SWAP_URL, json=body, headers=jupiter_headers()) as r:
        swap = await r.json()
    tx_b64 = swap.get("swapTransaction")
    if not tx_b64:
        return None

    from solders.transaction import VersionedTransaction

    unsigned = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    if cap is not None:
        units, rate = None, 0
        for instruction in unsigned.message.instructions:
            program = str(unsigned.message.account_keys[instruction.program_id_index])
            data = bytes(instruction.data)
            if program != "ComputeBudget111111111111111111111111111111":
                continue
            if len(data) == 5 and data[0] == 2:
                units = int.from_bytes(data[1:], "little")
            elif len(data) == 9 and data[0] == 3:
                rate = int.from_bytes(data[1:], "little")
        if rate and (units is None or (units * rate + 999999) // 1000000 > cap):
            return None
    signed = VersionedTransaction(unsigned.message, [keypair])
    raw = bytes(signed)
    return raw, len(raw)


async def _rpc(s: aiohttp.ClientSession, rpc: str, metodo: str, params: list) -> dict:
    async with s.post(
        rpc,
        json={"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as r:
        return await r.json()


async def _simular(s: aiohttp.ClientSession, rpc: str, raw: bytes) -> str | None:
    """None si la transaccion pasaria; el error si no. Cuesta nada y evita
    dar por buena una venta que va a fallar."""
    res = await _rpc(
        s,
        rpc,
        "simulateTransaction",
        [
            base64.b64encode(raw).decode(),
            {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True},
        ],
    )
    valor = (res.get("result") or {}).get("value") or {}
    err = valor.get("err")
    return json.dumps(err) if err else None


def _sol_de(quote: dict) -> float:
    """Los SOL que la cotizacion dice que entran, o 0 si no se puede leer.

    Cero significa "no se sabe", y quien llama lo trata como tal: se queda
    con el precio de referencia en vez de apuntar un cobro de cero, que
    falsearia el P&L en la direccion contraria.
    """
    try:
        return int(quote.get("outAmount") or 0) / 1e9
    except (TypeError, ValueError, AttributeError):
        return 0.0


async def jupiter_sell(
    keypair: object,
    mint: str,
    ui_amount: float,
    decimals: int = 6,
    slippage_bps: int = 2500,
    rpc: str | None = None,
) -> tuple[bool, str | None, str | None, float]:
    """Sell ``ui_amount`` of ``mint`` to SOL via Jupiter.

    Tries the full amount first and falls back to smaller slices when the route
    does not fit in a transaction or the curve overflows. Every candidate is
    simulated before it is sent, so a returned ``ok=True`` means the transaction
    was accepted by the cluster, not merely submitted.

    Args:
        keypair: solders Keypair to sign with.
        mint: Token mint to sell.
        ui_amount: Human amount (decimals applied here).
        decimals: Token decimals (pump.fun = 6).
        slippage_bps: Slippage in basis points (2500 = 25%).
        rpc: RPC endpoint (defaults to SOLANA_NODE_RPC_ENDPOINT).

    Returns:
        ``(ok, signature, error, sol_out)``. ``sol_out`` son los SOL que la
        cotizacion de Jupiter dice que entran en la wallet -- NO el precio de
        referencia de quien llama. Se devuelve porque sin el, el registro de
        operaciones apuntaba el precio de ANTES de vender, que en un desplome
        es mucho mejor que el real: el 24-sep una venta ejecutada a -92% quedo
        apuntada como -60%, y con esos numeros la estrategia parece mejor de
        lo que es. Es la cotizacion, no la liquidacion definitiva, asi que
        puede quedarse corta por el slippage -- pero se equivoca por
        muchisimo menos.

        On success the signature is confirmed to
        have simulated cleanly; the caller should still treat a partial sale as
        possible, since a fallback slice sells less than requested.
    """
    rpc = rpc or os.getenv("SOLANA_NODE_RPC_ENDPOINT")
    from trading.transaction_observer import OBSERVER, SUBMISSIONS, observe

    if OBSERVER.get() is not None and SUBMISSIONS.get():
        return False, None, "A curve submission is unresolved; no duplicate routed exit", 0.0
    if not rpc:
        return False, None, "RPC no configurado", 0.0
    total = raw_sell_amount(ui_amount, decimals)
    if total <= 0:
        return False, None, "cantidad cero", 0.0

    motivos: list[str] = []
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            for frac in FRACCIONES:
                amount = int(total * frac)
                if amount <= 0:
                    continue
                etiqueta = f"{frac * 100:.0f}%"

                quote = await _cotizar(s, mint, amount, slippage_bps)
                if not quote:
                    motivos.append(f"{etiqueta}: sin ruta")
                    continue

                construido = await _construir(s, quote, keypair)
                if not construido:
                    motivos.append(f"{etiqueta}: Jupiter no devolvio tx")
                    continue
                raw, tam = construido

                if tam > MAX_TX_BYTES:
                    motivos.append(f"{etiqueta}: tx {tam}B > {MAX_TX_BYTES}")
                    continue

                err = await _simular(s, rpc, raw)
                if err:
                    motivos.append(f"{etiqueta}: simulacion {err[:60]}")
                    continue

                from solders.transaction import VersionedTransaction

                observed_signature = str(VersionedTransaction.from_bytes(raw).signatures[0])
                observe("signed", observed_signature)
                res = await _rpc(
                    s,
                    rpc,
                    "sendTransaction",
                    [
                        base64.b64encode(raw).decode(),
                        {"encoding": "base64", "maxRetries": 3},
                    ],
                )
                if "error" in res:
                    error = res["error"]
                    if isinstance(error, dict) and error.get("code") == -32002 and (error.get("data") or {}).get("err") is not None:
                        observe("preflight_rejected", observed_signature)
                    elif OBSERVER.get() is not None:
                        return False, observed_signature, "Routed exit submission unresolved", 0.0
                    motivos.append(f"{etiqueta}: envio {json.dumps(res['error'])[:60]}")
                    continue

                sig = res.get("result")
                if frac < 1.0:
                    logger.warning(
                        "Jupiter: vendido solo el %s de %s (%s)",
                        etiqueta, mint, "; ".join(motivos),
                    )
                # LOS SOL QUE ENTRAN, segun la cotizacion recien firmada.
                # Sin esto quien llama apuntaba el precio de ANTES de vender,
                # y el registro salia optimista justo en las ventas que peor
                # van -- que son las unicas que hay que mirar.
                return True, str(sig), None, _sol_de(quote)
    except (aiohttp.ClientError, TimeoutError) as exc:
        return False, None, f"Jupiter API: {exc}", 0.0
    except Exception as exc:  # noqa: BLE001 -- surface any signing/send failure
        return False, None, f"firma/envio: {exc}", 0.0

    return False, None, "sin variante viable -> " + "; ".join(motivos), 0.0


async def jupiter_buy(
    keypair: object,
    mint: str,
    sol_amount: float,
    slippage_bps: int = 2500,
    rpc: str | None = None,
) -> tuple[bool, str | None, str | None, float]:
    """Compra ``mint`` gastando ``sol_amount`` SOL, via Jupiter.

    Hace falta porque el comprador normal del bot entra por la curva de
    pump.fun, y una moneda que YA HA MIGRADO no tiene curva: su liquidez esta
    en un pool. Para snipear migraciones no hay otra via.

    A diferencia de la venta, aqui NO se prueban fracciones: si no cabe la
    compra que se pide, se cancela. Comprar menos de lo decidido cambia la
    apuesta sin avisar, y en una entrada por migracion el tamaño es justamente
    lo que se ha calculado que se puede arriesgar.

    Se simula antes de enviar, asi que un ``ok=True`` significa que la red la
    acepto, no solo que se mando.

    Returns:
        ``(ok, firma, error, tokens_estimados)``.
    """
    rpc = rpc or os.getenv("SOLANA_NODE_RPC_ENDPOINT")
    if not rpc:
        return False, None, "RPC no configurado", 0.0
    lamports = int(sol_amount * 1_000_000_000)
    if lamports <= 0:
        return False, None, "cantidad cero", 0.0

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            quote = await _cotizar_compra(s, mint, lamports, slippage_bps)
            if not quote:
                return False, None, "sin ruta de compra", 0.0
            tokens = int(quote.get("outAmount") or 0) / 1_000_000

            construido = await _construir(s, quote, keypair)
            if not construido:
                return False, None, "Jupiter no devolvio tx", 0.0
            raw, tam = construido
            if tam > MAX_TX_BYTES:
                return False, None, f"tx {tam}B > {MAX_TX_BYTES}", 0.0

            err = await _simular(s, rpc, raw)
            if err:
                return False, None, f"simulacion: {err[:80]}", 0.0

            res = await _rpc(
                s, rpc, "sendTransaction",
                [base64.b64encode(raw).decode(),
                 {"encoding": "base64", "skipPreflight": True,
                  "maxRetries": 3}],
            )
            if "error" in res:
                return False, None, str(res["error"])[:120], 0.0
            return True, res.get("result"), None, tokens
    except Exception as e:  # noqa: BLE001
        return False, None, f"{type(e).__name__}: {e}", 0.0
