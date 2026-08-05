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
    _cache[creator] = (now, launches, migrations, rate)
    return launches, migrations, rate
