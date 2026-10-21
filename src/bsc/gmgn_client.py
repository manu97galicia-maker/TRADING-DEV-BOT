"""Read-only GMGN OpenAPI client used by the BSC launch coordinator."""

# API validation errors are intentionally explicit operator diagnostics.
# ruff: noqa: TRY003

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from bsc.strategy import DeveloperHistory

GMGN_HOST = "https://openapi.gmgn.ai"
GMGN_BSC_QUOTE_TYPES = (6, 7, 1, 16, 8, 3, 9, 10, 2, 17, 18, 0)
SUPPORTED_GMGN_PLATFORMS = frozenset({"fourmeme", "flap"})
HTTP_RATE_LIMITED = 429
HTTP_CLIENT_ERROR = 400
EVM_ADDRESS_LENGTH = 42


class GmgnError(RuntimeError):
    """Raised when GMGN data is unavailable or structurally unsafe to use."""


@dataclass(frozen=True)
class LaunchCandidate:
    """A strictly parsed BSC launch candidate."""

    address: str
    name: str
    symbol: str
    creator: str
    platform: str
    market_cap_usd: Decimal
    price_usd: Decimal
    created_at: int


def _decimal(value: object, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GmgnError(f"GMGN field {field} is not numeric") from exc
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        raise GmgnError(f"GMGN field {field} is not positive")
    return parsed


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise GmgnError(f"GMGN field {field} is not an integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise GmgnError(f"GMGN field {field} is not an integer") from exc
    if parsed < 0:
        raise GmgnError(f"GMGN field {field} is negative")
    return parsed


def _required_text(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GmgnError(f"GMGN field {field} is missing")
    return value.strip()


class GmgnClient:
    """Small authenticated client limited to read-only OpenAPI routes."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        host: str = GMGN_HOST,
        timeout_seconds: int = 15,
    ) -> None:
        self.api_key = (api_key or os.environ.get("GMGN_API_KEY", "")).strip()
        if not self.api_key:
            raise GmgnError("GMGN_API_KEY is required")
        self.host = host.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> object:
        params = dict(query or {})
        params.update(
            {
                "timestamp": str(int(time.time())),
                "client_id": str(uuid.uuid4()),
            }
        )
        headers = {
            "X-APIKEY": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "definitivo-bsc/1.0",
        }
        for attempt in range(2):
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.request(
                    method,
                    f"{self.host}{path}",
                    params=params,
                    json=body,
                    headers=headers,
                ) as response:
                    try:
                        payload = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError) as exc:
                        raise GmgnError("GMGN returned a non-JSON response") from exc
                    if not isinstance(payload, dict):
                        raise GmgnError("GMGN returned an invalid response envelope")
                    if response.status == HTTP_RATE_LIMITED and attempt == 0:
                        reset = response.headers.get("X-RateLimit-Reset", "")
                        try:
                            delay = max(float(reset) - time.time(), 0)
                        except ValueError:
                            delay = 1.0
                        await asyncio.sleep(min(delay + 0.25, 5.0))
                        continue
                    if response.status >= HTTP_CLIENT_ERROR or payload.get("code") != 0:
                        message = payload.get("message") or payload.get("error")
                        raise GmgnError(
                            f"GMGN {path} failed (HTTP {response.status}): {message}"
                        )
                    return payload.get("data")
        raise GmgnError("GMGN rate limit did not clear")

    async def launch_candidates(self) -> tuple[LaunchCandidate, ...]:
        """Return Four.meme/Flap candidates after server and local validation."""
        section: dict[str, object] = {
            "filters": ["offchain", "onchain"],
            "launchpad_platform_v2": True,
            "limit": 80,
            "launchpad_platform": sorted(SUPPORTED_GMGN_PLATFORMS),
            "quote_address_type": list(GMGN_BSC_QUOTE_TYPES),
            "max_marketcap": 9000,
            "min_creator_created_open_count": 2,
            "min_creator_created_open_ratio": 0.08,
        }
        data = await self._request(
            "POST",
            "/v1/trenches",
            query={"chain": "bsc"},
            body={
                "version": "v2",
                "new_creation": dict(section),
                "near_completion": dict(section),
            },
        )
        if not isinstance(data, dict):
            raise GmgnError("GMGN trenches data is not an object")

        parsed: dict[str, LaunchCandidate] = {}
        for category in ("new_creation", "pump"):
            items = data.get(category, [])
            if not isinstance(items, list):
                raise GmgnError(f"GMGN trenches category {category} is invalid")
            for raw in items:
                try:
                    candidate = self._parse_candidate(raw)
                except GmgnError:
                    continue
                parsed[candidate.address.lower()] = candidate
        return tuple(parsed.values())

    def _parse_candidate(self, raw: object) -> LaunchCandidate:
        if not isinstance(raw, dict):
            raise GmgnError("GMGN candidate is not an object")
        platform = _required_text(raw, "launchpad_platform").lower()
        if platform not in SUPPORTED_GMGN_PLATFORMS:
            raise GmgnError("candidate is not from Four.meme or Flap")
        address = _required_text(raw, "address")
        creator = _required_text(raw, "creator")
        if not address.startswith("0x") or len(address) != EVM_ADDRESS_LENGTH:
            raise GmgnError("candidate address is invalid")
        if not creator.startswith("0x") or len(creator) != EVM_ADDRESS_LENGTH:
            raise GmgnError("candidate creator is invalid")
        market_cap = _decimal(raw.get("usd_market_cap"), "usd_market_cap")
        if market_cap >= Decimal("9000"):
            raise GmgnError("candidate market cap is not below $9,000")
        price_value = raw.get("price")
        if price_value in (None, "", 0, "0"):
            total_supply = _decimal(raw.get("total_supply"), "total_supply")
            price = market_cap / total_supply
        else:
            price = _decimal(price_value, "price")
        return LaunchCandidate(
            address=address,
            name=_required_text(raw, "name"),
            symbol=_required_text(raw, "symbol"),
            creator=creator,
            platform=platform,
            market_cap_usd=market_cap,
            price_usd=price,
            created_at=_integer(
                raw.get("created_timestamp", raw.get("creation_timestamp")),
                "created_timestamp",
            ),
        )

    async def creator_history(self, wallet: str) -> DeveloperHistory:
        """Fetch authoritative aggregate launch and migration counts."""
        data = await self._request(
            "GET",
            "/v1/user/created_tokens",
            query={"chain": "bsc", "wallet_address": wallet},
        )
        if not isinstance(data, dict):
            raise GmgnError("GMGN creator history is not an object")
        inner = _integer(data.get("inner_count"), "inner_count")
        migrated = _integer(data.get("open_count"), "open_count")
        launches = inner + migrated
        if launches <= 0:
            raise GmgnError("GMGN creator history is empty")
        reported_ratio = _decimal(data.get("open_ratio"), "open_ratio", allow_zero=True)
        calculated_ratio = Decimal(migrated) / Decimal(launches)
        if abs(reported_ratio - calculated_ratio) > Decimal("0.000001"):
            raise GmgnError("GMGN creator history aggregates are inconsistent")
        return DeveloperHistory(launches=launches, migrations=migrated)

    async def token_market(self, address: str) -> tuple[Decimal, Decimal]:
        """Return current USD price and market cap for an open position."""
        data = await self._request(
            "GET",
            "/v1/token/info",
            query={"chain": "bsc", "address": address},
        )
        if not isinstance(data, dict):
            raise GmgnError("GMGN token info is not an object")
        return (
            _decimal(data.get("price"), "price"),
            _decimal(
                data.get("market_cap", data.get("usd_market_cap")),
                "market_cap",
            ),
        )
