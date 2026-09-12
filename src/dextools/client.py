"""Small async client for DEXTools API v2.

Cubre tres cosas: los datos de un token, el ranking de hotpools y los
endpoints de bloqueo/auditoria.

Sobre el ranking: la API v2 NO tiene endpoint de "quien acaba de pagar
NITRO" -- sus endpoints son blockchain/exchange/pool/token/rankings y
ninguno expone la compra de promocion. Lo que si se puede leer es su
efecto: un pack NITRO compra posicion en el DEXTboard, y el DEXTboard es
``/ranking/{chain}/hotpools``. No es el recibo, es la consecuencia, con la
ventaja de llegar ya validada por el mercado.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from core.rpc_rate_limiter import TokenBucketRateLimiter
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://public-api.dextools.io"
DEFAULT_CHAIN = "solana"
VALID_TIERS = frozenset({"trial", "standard", "advanced", "pro"})
HTTP_ERROR_MIN = 400


@dataclass(slots=True)
class DextoolsTokenData:
    """Normalized DEXTools token information and metrics."""

    address: str | None = None
    name: str | None = None
    symbol: str | None = None
    price_usd: float | None = None
    liquidity_usd: float | None = None
    volume_h24: float | None = None
    market_cap: float | None = None
    fdv: float | None = None
    dext_score: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DextoolsHotPool:
    """Una entrada del ranking de hotpools (el DEXTboard)."""

    mint: str
    pool: str | None = None
    name: str | None = None
    symbol: str | None = None
    rank: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DextoolsTokenSafety:
    """Bloqueo de liquidez y autoridades de un token.

    ``lp_locked_pct`` a None significa "no se ha podido saber", que NO es lo
    mismo que cero: quien decide descarta ante la duda, no este parseo.
    """

    lp_locked_pct: float | None = None
    is_honeypot: bool | None = None
    mint_authority_active: bool | None = None
    freeze_authority_active: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class DextoolsClient:
    """Async DEXTools client configured through environment variables.

    ``DEXTOOLS_API_KEY`` provides authentication and ``DEXTOOLS_API_TIER``
    selects the URL tier associated with the key (``trial`` by default).
    """

    def __init__(
        self,
        api_key: str | None = None,
        tier: str | None = None,
        chain: str = DEFAULT_CHAIN,
        timeout: float = 10.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._api_key = (api_key or os.getenv("DEXTOOLS_API_KEY", "")).strip()
        selected_tier = (tier or os.getenv("DEXTOOLS_API_TIER", "trial")).lower()
        if selected_tier not in VALID_TIERS:
            msg = f"Unsupported DEXTools API tier: {selected_tier}"
            raise ValueError(msg)
        self._tier = selected_tier
        self._chain = chain
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = session
        self._owns_session = session is None
        self._session_lock = asyncio.Lock()
        self._limiter = TokenBucketRateLimiter(max_rps=1.0, burst_size=2)

    @property
    def configured(self) -> bool:
        """Whether an API key is available."""
        return bool(self._api_key)

    async def __aenter__(self) -> DextoolsClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the internally owned HTTP session."""
        if (
            self._owns_session
            and self._session is not None
            and not self._session.closed
        ):
            await self._session.close()
            self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession(timeout=self._timeout)
                    self._owns_session = True
        return self._session

    @property
    def tier(self) -> str:
        """Plan contra el que se construyen las URLs."""
        return self._tier

    async def _get_token(self, suffix: str) -> object:
        """GET bajo ``/token/{chain}/``, que es donde vive casi todo."""
        return await self._get(f"/token/{self._chain}/{suffix}")

    async def _get(self, ruta: str) -> object:
        """GET a cualquier ruta de la v2, ya con plan, clave y limitador.

        El prefijo ``/token/`` estaba antes metido aqui dentro, lo que dejaba
        ``/ranking/`` fuera de alcance; ahora lo pone quien llama.
        """
        if not self.configured:
            logger.warning("DEXTools skipped: DEXTOOLS_API_KEY is not configured")
            return None
        await self._limiter.acquire()
        session = await self._get_session()
        path = f"/{self._tier}/v2{ruta}"
        try:
            async with session.get(
                f"{BASE_URL}{path}",
                headers={"accept": "application/json", "X-API-Key": self._api_key},
            ) as response:
                if response.status >= HTTP_ERROR_MIN:
                    logger.warning(
                        "DEXTools %s returned HTTP %s", path, response.status
                    )
                    return None
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("DEXTools request to %s failed: %s", path, exc)
            return None

    async def get_token(self, token_address: str) -> DextoolsTokenData | None:
        """Fetch and combine the token, price, and score v2 endpoints."""
        responses = await asyncio.gather(
            self._get_token(token_address),
            self._get_token(f"{token_address}/price"),
            self._get_token(f"{token_address}/score"),
        )
        data = [_unwrap(response) for response in responses]
        if not any(data):
            return None
        merged: dict[str, Any] = {}
        for item in data:
            merged.update(item)
        return _parse_token_data(merged)

    async def get_hotpools(self, limit: int = 0) -> list[DextoolsHotPool]:
        """Ranking de hotpools de la cadena: el DEXTboard que compra NITRO."""
        payload = await self._get(f"/ranking/{self._chain}/hotpools")
        return _parse_hotpools(payload, limit=limit)

    async def get_gainers(self, limit: int = 0) -> list[DextoolsHotPool]:
        """Ranking de mayores subidas, con la misma forma que hotpools."""
        payload = await self._get(f"/ranking/{self._chain}/gainers")
        return _parse_hotpools(payload, limit=limit)

    async def get_token_safety(self, token_address: str) -> DextoolsTokenSafety | None:
        """Bloqueo de liquidez y auditoria de un token.

        Devuelve None si la API no contesta nada, para que quien llama pueda
        distinguir "no lo se" de "lo se y esta mal".
        """
        locks, audit = await asyncio.gather(
            self._get_token(f"{token_address}/locks"),
            self._get_token(f"{token_address}/audit"),
        )
        if locks is None and audit is None:
            return None
        return _parse_token_safety(locks, audit)


def _unwrap(payload: object) -> dict[str, Any]:
    """Extract DEXTools' data object while tolerating response variants."""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _parse_token_data(data: dict[str, Any]) -> DextoolsTokenData:
    """Normalize the subset of DEXTools fields used by the bot."""
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    return DextoolsTokenData(
        address=data.get("address"),
        name=data.get("name"),
        symbol=data.get("symbol"),
        price_usd=_num(data.get("price") or data.get("priceUSD")),
        liquidity_usd=_num(metrics.get("liquidity") or data.get("liquidity")),
        volume_h24=_num(metrics.get("volume24h") or data.get("volume24h")),
        market_cap=_num(metrics.get("marketCap") or data.get("marketCap")),
        fdv=_num(metrics.get("fdv") or data.get("fdv")),
        dext_score=_num(data.get("dextScore") or data.get("score")),
        raw=data,
    )


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(data: dict[str, Any], *keys: str) -> object:
    """Devuelve el primer valor no nulo de entre varias claves posibles."""
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _flag(value: object) -> bool | None:
    """Interpreta los si/no de la auditoria, que llegan de tres formas.

    Unas veces son booleanos, otras las cadenas "yes"/"no" y otras
    "unknown". "unknown" tiene que seguir siendo None: es el caso que debe
    hacer que se descarte la moneda, y convertirlo en False lo volveria un
    aprobado.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"yes", "true", "1"}:
            return True
        if text in {"no", "false", "0"}:
            return False
    return None


def _parse_hotpools(payload: object, limit: int = 0) -> list[DextoolsHotPool]:
    """Normaliza la respuesta del ranking a una lista manejable.

    Llega a veces como ``{"data": [...]}`` y a veces como lista pelada, y el
    token principal del par unas veces es ``mainToken`` y otras ``token``. Se
    aceptan ambas: es mas barato que quedarse sin senal el dia que cambien
    el nombre.
    """
    if isinstance(payload, dict):
        entries = payload.get("data", payload.get("results", []))
    else:
        entries = payload
    if not isinstance(entries, list):
        return []
    pools: list[DextoolsHotPool] = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        main = _first(entry, "mainToken", "token", "baseToken")
        if not isinstance(main, dict):
            continue
        mint = _first(main, "address", "tokenAddress", "id")
        if not mint:
            continue
        rank = _num(_first(entry, "rank", "position"))
        pool = _first(entry, "address", "pairAddress")
        pools.append(
            DextoolsHotPool(
                mint=str(mint),
                pool=str(pool) if pool is not None else None,
                name=main.get("name"),
                symbol=main.get("symbol"),
                rank=int(rank) if rank is not None else position,
                raw=entry,
            )
        )
        if limit > 0 and len(pools) >= limit:
            break
    return pools


def _parse_token_safety(locks: object, audit: object) -> DextoolsTokenSafety:
    """Junta los dos endpoints de seguridad en una sola respuesta."""
    lock_data = _unwrap(locks)
    audit_data = _unwrap(audit)
    pct = _num(
        _first(
            lock_data,
            "lockedPercentage",
            "percentageLocked",
            "lockedLiquidityPercentage",
        )
    )
    return DextoolsTokenSafety(
        lp_locked_pct=pct,
        is_honeypot=_flag(_first(audit_data, "isHoneypot", "honeypot")),
        mint_authority_active=_flag(
            _first(audit_data, "isMintable", "mintable", "mintAuthority")
        ),
        freeze_authority_active=_flag(
            _first(audit_data, "isBlacklisted", "freezeAuthority", "isFreezable")
        ),
        raw={"locks": lock_data, "audit": audit_data},
    )
