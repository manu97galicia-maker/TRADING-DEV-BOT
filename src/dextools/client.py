"""Small async client for DEXTools API v2 token data."""

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

    async def _get(self, suffix: str) -> object:
        if not self.configured:
            logger.warning("DEXTools skipped: DEXTOOLS_API_KEY is not configured")
            return None
        await self._limiter.acquire()
        session = await self._get_session()
        path = f"/{self._tier}/v2/token/{self._chain}/{suffix}"
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
            self._get(token_address),
            self._get(f"{token_address}/price"),
            self._get(f"{token_address}/score"),
        )
        data = [_unwrap(response) for response in responses]
        if not any(data):
            return None
        merged: dict[str, Any] = {}
        for item in data:
            merged.update(item)
        return _parse_token_data(merged)


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
