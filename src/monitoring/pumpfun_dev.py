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

#: RESPALDO POR LA CADENA, A TRAVES DE HELIUS (5-nov).
#:
#: Pedido: "Arreglarlo", tras ver con deploy/porque-no-compra.sh que pump.fun
#: contesta 403 al droplet de DigitalOcean: los 12 devs rechazados salian con
#: 0 lanzadas porque el dato no llegaba. Sin pump.fun, el historial se lee de
#: la cadena con la fuente que construyo Codex
#: (monitoring/creator_transaction_history.py): las transacciones que FIRMO el
#: dev (getTransactionsForAddress de Helius), cuales crearon una moneda de
#: pump.fun, y cuales de esas tienen su pool canonico de PumpSwap (migro).
#:
#: Cuesta consultas de Helius por cada dev NUEVO; luego se guarda 10 min como
#: lo de pump.fun. Solo se usa si pump.fun falla.
_rpc_respaldo = None
#: Tras un rechazo de pump.fun no se le vuelve a preguntar en este rato: en
#: el droplet contesta 403 siempre, y preguntar en cada moneda solo es tiempo
#: perdido antes de ir a Helius.
_PAUSA_PUMPFUN = 600.0
_pumpfun_bloqueado_hasta = 0.0
#: Limites prudentes para un droplet de 1 nucleo y una cuota de Helius
#: (Codex usaba 64 paginas). Un dev con mas de 3.000 transacciones da "sin
#: dato" y no se compra: con dinero real, no saber no es que si.
_POLITICA_HELIUS = {
    "history_provider": "helius_transactions",
    "history_page_size": 100,
    "history_max_pages": 30,
    "history_max_transactions": 3000,
    "history_max_accounts": 3000,
    "min_creator_migration_fraction": 0.01,
}
_ESPERA_HELIUS = 8.0


def usar_rpc_de_respaldo(post_rpc: object) -> None:
    """Registra el cliente del nodo (Helius) para leer la cadena si pump.fun falla.

    `post_rpc` es `SolanaClient.post_rpc`: recibe el cuerpo JSON-RPC y devuelve
    la respuesta entera.
    """
    global _rpc_respaldo  # noqa: PLW0603
    _rpc_respaldo = post_rpc


async def _rpc(method: str, params: list) -> object:
    data = await _rpc_respaldo(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    if not data or data.get("error") or data.get("result") is None:
        raise RuntimeError(f"sin respuesta del nodo para {method}")  # noqa: TRY003
    return data["result"]


async def _por_la_cadena(creator: str) -> tuple[int, int] | None:
    """(lanzadas, migradas) leidas de la cadena, o None si no se pudo."""
    if _rpc_respaldo is None:
        return None
    from monitoring.creator_transaction_history import (  # noqa: PLC0415
        read_transaction_history,
    )

    try:
        foto = await asyncio.wait_for(
            read_transaction_history(creator, _rpc, _POLITICA_HELIUS),
            timeout=_ESPERA_HELIUS,
        )
    except Exception as error:  # noqa: BLE001
        # EL MOTIVO, NO SOLO EL TIPO (5-nov): con solo "HistoryUnavailableError"
        # no se distinguia un dev sin monedas previas (lo normal) de una
        # averia. Se escribe el texto SOLO de los errores cuyo texto es fijo
        # (las etiquetas de Codex y el RuntimeError de `_rpc`): un error de
        # red podria llevar la URL del nodo, y en ella la clave de Helius.
        from monitoring.creator_history_index import (  # noqa: PLC0415
            HistoryUnavailableError,
        )

        fijo = isinstance(error, HistoryUnavailableError | RuntimeError)
        logger.warning(
            "Historial del dev %s por la cadena no disponible (%s)",
            creator[:8],
            f"{type(error).__name__}: {error}" if fijo else type(error).__name__,
        )
        return None
    return len(foto.curves), len(foto.migrated_curves)


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


async def _consultar_por_la_cadena(
    creator: str, now: float
) -> tuple[int, int, float]:
    """Respaldo cuando pump.fun no contesta. Sin dato, (0, 0, 0): no compra."""
    leido = await _por_la_cadena(creator)
    if leido is None:
        return 0, 0, 0.0
    launches, migrations = leido
    rate = migrations / launches if launches else 0.0
    _cache[creator] = (now, launches, migrations, rate)
    return launches, migrations, rate


async def _consultar(creator: str) -> tuple[int, int, float]:
    global _pumpfun_bloqueado_hasta  # noqa: PLW0603
    now = time.time()
    if now < _pumpfun_bloqueado_hasta:
        return await _consultar_por_la_cadena(creator, now)
    url = f"{_API}?creator={creator}&limit=200&offset=0"
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=_HEADERS) as resp:
                if resp.status != 200:
                    # ANTES ESTO ERA UN 0 CALLADO: "0 migr / 0 lanz" y descarte,
                    # sin que nada dijera que pump.fun no habia contestado.
                    logger.warning(
                        "pump.fun contesta %s al pedir el historial del dev; "
                        "se usa la cadena (Helius)", resp.status,
                    )
                    _pumpfun_bloqueado_hasta = now + _PAUSA_PUMPFUN
                    return await _consultar_por_la_cadena(creator, now)
                data = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001
        logger.warning("pumpfun creator stats failed for %s", creator[:8])
        return await _consultar_por_la_cadena(creator, now)

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
