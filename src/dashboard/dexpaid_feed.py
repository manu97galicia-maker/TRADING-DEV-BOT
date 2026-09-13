"""Live feed of Solana tokens that recently paid DexScreener (dex paid / boosts).

Powers the dashboard's "Pagaron Dex" panel and is the detection source for the
buy-on-dex-paid strategy. Measured reality: a paid order is visible on the API
minutes after payment (not seconds), so this polls the latest-boosts feed and
surfaces recently-paid tokens with the data needed to decide a buy:
boost amount, payment age, price/liquidity, and RugCheck locked-liquidity %.

Bounded on purpose (a handful of tokens per refresh) to stay within the 60/300
rpm DexScreener limits and RugCheck's gentle limit.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from dexscreener.client import DexScreenerClient
from dexscreener.rugcheck import RugCheckClient
from utils.logger import get_logger

logger = get_logger(__name__)


def edad_de_pago_aceptable(age_min: float | None, tope_minutos: float) -> bool:
    """Decide si un pago sirve como senal por su antiguedad.

    Que EXISTA una orden de pago aprobada no es negociable: sin ella no hay
    senal. Su antiguedad si lo es -- con ``tope_minutos`` a cero deja de
    filtrar, porque el feed que se consulta ya es el de "ultimos" y la
    frescura la garantizan el propio feed y el registro de lo ya visto.

    Args:
        age_min: Minutos desde el pago, o None si no hay orden aprobada.
        tope_minutos: Tope en minutos; cero o menos desactiva el filtro.

    Returns:
        True si el candidato pasa.
    """
    if age_min is None:
        return False
    return not (tope_minutos > 0 and age_min > tope_minutos)


async def _paid_age_minutes(
    dex: DexScreenerClient, mint: str, chain: str, now_ms: float
) -> float | None:
    """Return minutes since the newest approved paid order, or None.

    Runs on the 60 rpm limiter, so it is the slowest step of a cycle and is
    only called for the paid path (a boost is its own proof of payment).
    """
    orders = await dex.get_orders(mint, chain)
    paid_ts = [
        order.get("paymentTimestamp")
        for order in orders
        if order.get("status") == "approved" and order.get("paymentTimestamp")
    ]
    if not paid_ts:
        return None
    return (now_ms - max(paid_ts)) / 60000.0


async def get_candidates(  # noqa: PLR0913
    limit: int = 10,
    max_age_minutes: float = 120.0,
    max_token_age_minutes: float = 0.0,
    chain: str = "solana",
    check_locked: bool = True,
    dex: DexScreenerClient | None = None,
    interesa: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """Return recently dex-paid tokens with buy-decision data.

    Args:
        limit: Max tokens to return (kept small for rate limits).
        max_age_minutes: Only include tokens whose newest approved paid order
            is younger than this; zero disables the age check (an approved
            order is still required).
        max_token_age_minutes: Reject pairs older than this; zero disables it.
        chain: Chain slug.
        check_locked: Also query RugCheck for locked-liquidity % (slower).

    Returns:
        List of dicts sorted by most-recent payment first, each with mint,
        symbol, boosts, payment_age_min, price_usd, liquidity_usd, dex_id,
        migrated flag, and (if check_locked) lp_locked_pct.
    """
    now_ms = time.time() * 1000.0

    # Un cliente NUEVO en cada vuelta traia un limitador NUEVO, con el cubo
    # de golpes lleno: el limitador no limitaba nada entre vueltas y cada
    # sondeo empezaba con permiso para disparar a discrecion. Con
    # poll_interval a 5s eso son 12 arranques limpios por minuto contra un
    # tope de 60 peticiones, y de ahi los 429 en cadena. Quien llama en
    # bucle debe pasar SIEMPRE el mismo cliente.
    propio = dex is None
    cliente = dex if dex is not None else DexScreenerClient(chain=chain)
    try:
        rows = await _recoger(
            cliente,
            limit=limit,
            max_age_minutes=max_age_minutes,
            max_token_age_minutes=max_token_age_minutes,
            chain=chain,
            check_locked=check_locked,
            now_ms=now_ms,
            interesa=interesa,
        )
    finally:
        if propio:
            await cliente.close()
    rows.sort(
        key=lambda r: (
            r["signal_type"] != "boost",
            r["payment_age_min"] if r["payment_age_min"] is not None else 0,
        )
    )
    return rows


def _fusionar_feeds(entradas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Junta por moneda lo que llega de los dos feeds (pagos y boosts).

    Una misma moneda puede salir en los dos. Se queda con el mayor numero
    de boosts visto y recuerda de cual de los dos vino, que es lo que luego
    decide si hace falta consultar las ordenes.
    """
    por_mint: dict[str, dict[str, Any]] = {}
    for entrada in entradas:
        mint = entrada.get("tokenAddress")
        if not mint:
            continue
        actual = por_mint.get(mint, {})
        por_mint[mint] = {
            **actual,
            **entrada,
            "totalAmount": max(
                float(actual.get("totalAmount") or actual.get("amount") or 0),
                float(entrada.get("totalAmount") or entrada.get("amount") or 0),
            ),
            "_from_boosts": bool(
                actual.get("_from_boosts") or entrada.get("_from_boosts")
            ),
            "_from_profiles": bool(
                actual.get("_from_profiles") or entrada.get("_from_profiles")
            ),
        }
    return por_mint


def _resumen(mint: str, entrada: dict[str, Any]) -> dict[str, Any]:
    """Lo minimo para decidir si una candidata merece gastar peticiones.

    Sale entero de la respuesta de los feeds, sin una sola llamada extra.
    """
    return {
        "mint": mint,
        "signal_type": "boost" if entrada.get("_from_boosts") else "paid",
        "boosts": entrada.get("totalAmount") or entrada.get("amount") or 0,
    }


async def _recoger(  # noqa: PLR0913
    dex: DexScreenerClient,
    *,
    limit: int,
    max_age_minutes: float,
    max_token_age_minutes: float,
    chain: str,
    check_locked: bool,
    now_ms: float,
    interesa: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """Trae y arma las candidatas con el cliente que se le da."""
    boosts, profiles = await asyncio.gather(
        dex.get_latest_boosts(), dex.get_latest_token_profiles()
    )
    combined = [
        {**entry, "_from_boosts": True}
        for entry in boosts
        if entry.get("chainId") == chain
    ] + [
        {**entry, "_from_profiles": True}
        for entry in profiles
        if entry.get("chainId") == chain
    ]
    by_mint = _fusionar_feeds(combined)
    # CRIBA BARATA ANTES DE GASTAR PETICIONES. Lo caro de cada candidata
    # --la consulta de ordenes y la de mercado-- se hacia sobre las 90, y
    # quien llama descartaba despues ~85 por repetidas o sin senal nueva.
    # Es decir: se pagaba el precio completo por monedas que ya se sabia que
    # no se iban a comprar, y una vuelta tardaba 25-80s en vez de 5.
    #
    # Lo que necesita esa criba (mint, si es boost, cuantos boosts) ya esta
    # en la respuesta de los feeds, asi que puede decidirse gratis.
    if interesa is not None:
        by_mint = {
            mint: entrada
            for mint, entrada in by_mint.items()
            if interesa(_resumen(mint, entrada))
        }
    toks = list(by_mint.values())[: limit * 3]

    async def build(entry: dict[str, Any]) -> dict[str, Any] | None:
        mint = entry.get("tokenAddress")
        if not mint:
            return None
        # A boost is its own proof of payment, so the orders lookup is
        # skipped for it: that is what keeps detection near-instant.
        is_boost = bool(entry.get("_from_boosts"))
        age_min: float | None = None
        if not is_boost:
            age_min = await _paid_age_minutes(dex, mint, chain, now_ms)
            if not edad_de_pago_aceptable(age_min, max_age_minutes):
                return None
        market = await dex.get_market_data(mint, chain)
        pair_created_at = market.pair_created_at if market else None
        token_age_min = (
            (now_ms - float(pair_created_at)) / 60000.0 if pair_created_at else None
        )
        if max_token_age_minutes > 0 and (
            token_age_min is None or token_age_min > max_token_age_minutes
        ):
            return None
        dex_id = market.dex_id if market else None
        migrated = dex_id in ("pumpswap", "raydium") if dex_id else None
        return {
            "mint": mint,
            "symbol": (
                market.raw.get("baseToken", {}).get("symbol") if market else None
            )
            or "?",
            "name": (market.raw.get("baseToken", {}).get("name") if market else None)
            or "",
            "boosts": entry.get("totalAmount") or entry.get("amount") or 0,
            "payment_age_min": round(age_min, 1) if age_min is not None else None,
            "token_age_min": (
                round(token_age_min, 1) if token_age_min is not None else None
            ),
            "signal_type": "boost" if is_boost else "paid",
            # Salio en las DOS listas: pago el perfil Y ademas boosts. Son
            # dos gastos distintos, asi que cuenta como senal mas fuerte
            # que cualquiera de los dos por separado.
            "pago_y_boost": bool(
                entry.get("_from_boosts") and entry.get("_from_profiles")
            ),
            "price_usd": market.price_usd if market else None,
            "liquidity_usd": market.liquidity_usd if market else None,
            "dex_id": dex_id,
            "migrated": migrated,
        }

    built = await asyncio.gather(*[build(t) for t in toks])
    rows = [r for r in built if r][:limit]

    if check_locked and rows:
        async with RugCheckClient() as rc:
            reports = await asyncio.gather(*[rc.get_report(r["mint"]) for r in rows])
            for r, rep in zip(rows, reports):  # noqa: B905 - Python 3.9
                r["lp_locked_pct"] = rep.lp_locked_pct if rep.found else None
                r["rugged"] = rep.rugged if rep.found else None

    return rows
