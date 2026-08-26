"""Pump.fun creator-migration filter.

Computes a token creator's track record from pump.fun's own public API
(https://frontend-api-v3.pump.fun/coins?creator=<wallet>): how many tokens
they've launched and how many graduated (``complete: true`` = migrated).

This is the on-chain-truth source RugCheck lacks: RugCheck often returns no
data for fresh pump.fun tokens, so a dev-reputation filter built on it blocks
almost everything. pump.fun's API returns the creator's full coin list with the
migration flag in one call, cached per creator.
"""

from __future__ import annotations

import asyncio
import time

import aiohttp

from utils.logger import get_logger

logger = get_logger(__name__)

_API = "https://frontend-api-v3.pump.fun/coins"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_TTL = 600.0  # cache a creator's stats for 10 min
_cache: dict[str, tuple[float, int, int, float]] = {}  # creator -> (ts, launches, migrations, rate)

#: Picos de las monedas del creador que SI migraron, por creador.
#:
#: POR QUE SOLO LAS MIGRADAS. El campo ``ath_market_cap`` de pump.fun se infla
#: solo: un creador llego a ensenar 89.950.686 $ de maximo con 1.387 $ de
#: liquidez y 5 $ de volumen diario. Ordenar devs por ese numero premia a las
#: granjas. Migrar, en cambio, exige que entren 85 SOL de verdad en la curva y
#: eso no se puede fingir -- asi que aqui solo cuenta el pico de una moneda que
#: ademas migro.
_picos: dict[str, list[float]] = {}

#: Consultas en vuelo, por creador. Sin esto, pedir las estadisticas nada mas
#: ver la moneda y volver a pedirlas al ir a comprar lanza DOS llamadas a la
#: API para el mismo dev: la primera aun no ha terminado, asi que la cache
#: todavia esta vacia. Con esto la segunda se engancha a la primera.
_en_vuelo: dict[str, "asyncio.Task"] = {}


async def creator_migration_stats(creator: str) -> tuple[int, int, float]:
    """Return ``(launches, migrations, rate)`` for a pump.fun creator.

    ``migrations`` counts the creator's coins with ``complete == True``.
    Returns ``(0, 0, 0.0)`` on any error (fail-closed for a reputation filter).
    """
    now = time.time()
    hit = _cache.get(creator)
    if hit and now - hit[0] < _TTL:
        return hit[1], hit[2], hit[3]

    tarea = _en_vuelo.get(creator)
    if tarea is not None and not tarea.done():
        return await asyncio.shield(tarea)
    tarea = asyncio.ensure_future(_consultar(creator))
    _en_vuelo[creator] = tarea
    try:
        return await asyncio.shield(tarea)
    finally:
        if _en_vuelo.get(creator) is tarea and tarea.done():
            _en_vuelo.pop(creator, None)


async def _consultar(creator: str) -> tuple[int, int, float]:
    now = time.time()
    url = f"{_API}?creator={creator}&limit=200&offset=0"
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=_HEADERS) as resp:
                if resp.status != 200:
                    return 0, 0, 0.0
                data = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001
        logger.warning("pumpfun creator stats failed for %s", creator[:8])
        return 0, 0, 0.0

    coins = data if isinstance(data, list) else []
    launches = len(coins)
    migrations = sum(1 for c in coins if c.get("complete") is True)
    rate = migrations / launches if launches else 0.0
    picos = []
    for c in coins:
        if c.get("complete") is not True:
            continue
        for campo in ("ath_market_cap", "ath_market_cap_usd", "usd_market_cap"):
            v = c.get(campo)
            if isinstance(v, (int, float)) and v > 0:
                picos.append(float(v))
                break
    _picos[creator] = sorted(picos, reverse=True)
    _cache[creator] = (now, launches, migrations, rate)
    return launches, migrations, rate


def monedas_por_encima(creator: str, min_mc: float) -> int:
    """Cuantas monedas MIGRADAS del creador superaron ``min_mc`` dolares.

    Se lee de lo que dejo ``creator_migration_stats``; no hace red. Devuelve 0
    si de ese creador no se ha consultado nada, que es el fallo correcto para
    un filtro de reputacion: sin dato, no es elite.
    """
    return sum(1 for p in _picos.get(creator, ()) if p >= min_mc)


def mejor_pico(creator: str) -> float:
    """El pico mas alto de una moneda migrada del creador. 0 si no hay dato."""
    picos = _picos.get(creator) or []
    return picos[0] if picos else 0.0


def mediana_pico(creator: str, min_monedas: int = 2) -> float:
    """Mediana de picos de las monedas MIGRADAS del creador.

    0 si hay menos de ``min_monedas`` migradas: con una sola no es mediana,
    es la suerte de esa moneda. Igual que en ``mejor_pico``, solo cuentan las
    migradas -- el resto se filtra ya en ``_consultar``.
    """
    picos = _picos.get(creator) or []
    if len(picos) < max(1, min_monedas):
        return 0.0
    picos_ordenados = sorted(picos)
    n = len(picos_ordenados)
    mitad = n // 2
    if n % 2:
        return picos_ordenados[mitad]
    return (picos_ordenados[mitad - 1] + picos_ordenados[mitad]) / 2
