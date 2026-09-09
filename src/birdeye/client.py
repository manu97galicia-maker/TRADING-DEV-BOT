"""Small async client for Birdeye token market data."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from core.rpc_rate_limiter import TokenBucketRateLimiter
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://public-api.birdeye.so"
DEFAULT_CHAIN = "solana"
HTTP_ERROR_MIN = 400


@dataclass(slots=True)
class BirdeyeTokenOverview:
    """Normalized fields returned by Birdeye's token-overview endpoint."""

    address: str | None = None
    name: str | None = None
    symbol: str | None = None
    price_usd: float | None = None
    liquidity_usd: float | None = None
    volume_h24: float | None = None
    market_cap: float | None = None
    fdv: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class BirdeyeClient:
    """Async Birdeye client whose key defaults to ``BIRDEYE_API_KEY``."""

    def __init__(
        self,
        api_key: str | None = None,
        chain: str = DEFAULT_CHAIN,
        timeout: float = 10.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._api_key = (api_key or os.getenv("BIRDEYE_API_KEY", "")).strip()
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

    async def __aenter__(self) -> BirdeyeClient:
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

    async def _get(self, path: str, params: dict[str, str]) -> object:
        if not self.configured:
            logger.warning("Birdeye skipped: BIRDEYE_API_KEY is not configured")
            return None
        await self._limiter.acquire()
        session = await self._get_session()
        headers = {
            "accept": "application/json",
            "X-API-KEY": self._api_key,
            "x-chain": self._chain,
        }
        try:
            async with session.get(
                f"{BASE_URL}{path}", params=params, headers=headers
            ) as response:
                if response.status >= HTTP_ERROR_MIN:
                    logger.warning("Birdeye %s returned HTTP %s", path, response.status)
                    return None
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("Birdeye request to %s failed: %s", path, exc)
            return None

    async def get_token_overview(
        self, token_address: str
    ) -> BirdeyeTokenOverview | None:
        """Fetch normalized market data for a Solana token mint."""
        payload = await self._get("/defi/token_overview", {"address": token_address})
        if not isinstance(payload, dict) or payload.get("success") is False:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        return _parse_token_overview(data)


def _parse_token_overview(data: dict[str, Any]) -> BirdeyeTokenOverview:
    """Normalize the subset of Birdeye fields used by the bot."""
    return BirdeyeTokenOverview(
        address=data.get("address"),
        name=data.get("name"),
        symbol=data.get("symbol"),
        price_usd=_num(data.get("price")),
        liquidity_usd=_num(data.get("liquidity")),
        volume_h24=_num(data.get("v24hUSD") or data.get("volume24hUSD")),
        market_cap=_num(data.get("marketCap") or data.get("mc")),
        fdv=_num(data.get("fdv")),
        raw=data,
    )


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
