"""GMGN launch counts with signer and canonical migration proofs from Solana.

Only public reads are issued. The indexed denominator is labeled separately
from verified PumpSwap migrations; the capped GMGN token list is never a count.
"""

# Validation labels are deliberately fixed and contain no upstream error text.
# ruff: noqa: TRY003, TRY301, BLE001, PLR2004

from __future__ import annotations

import asyncio
import os
import re
import socket
import time
import uuid
from decimal import Decimal, InvalidOperation

import aiohttp
from solders.pubkey import Pubkey

from monitoring.creator_history_index import (
    CreatorSnapshot,
    HistoryUnavailableError,
    curve_address,
)
from monitoring.creator_transaction_history import (
    CREATE,
    event_parser,
    migration_proven,
    pool_address,
)
from monitoring.pump_event_logs import IncompletePumpLogsError, pump_events
from trading.second_block_policy import required_migrations

GMGN_URL = "https://openapi.gmgn.ai/v1/user/created_tokens"
_RATE_LIMIT_STATE = {"until": 0.0, "next_at": 0.0}


def _count(value: object) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
        return int(value)
    raise HistoryUnavailableError("GMGN creator counts are invalid")


def _token_rows(data: dict, policy: dict) -> tuple[set[str], set[str], int]:
    rows = data.get("tokens")
    if not isinstance(rows, list) or len(rows) > policy["history_max_accounts"]:
        raise HistoryUnavailableError("GMGN creator token list is unavailable or oversized")
    known, candidates = set(), set()
    open_rows = 0
    for row in rows:
        if not isinstance(row, dict) or row.get("chain") != "sol" or type(row.get("is_open")) is not bool:
            raise HistoryUnavailableError("GMGN creator token metadata is invalid")
        address = row.get("token_address")
        try:
            if not isinstance(address, str):
                raise TypeError
            Pubkey.from_string(address)
        except (TypeError, ValueError):
            raise HistoryUnavailableError("GMGN creator token address is invalid") from None
        if address in known:
            raise HistoryUnavailableError("GMGN creator token list contains duplicates")
        known.add(address)
        open_rows += int(row["is_open"])
        if row["is_open"] and row.get("is_pump") is True:
            candidates.add(address)
    return known, candidates, open_rows


def _validate_identity(data: dict, developer: str) -> None:
    identity = data.get("creator_ath_info")
    if identity is not None and (not isinstance(identity, dict)
                                or identity.get("creator") not in (None, developer)):
        raise HistoryUnavailableError("GMGN creator index identifies a different wallet")
    if "last_create_timestamp" in data and data["last_create_timestamp"] is not None:
        _count(data["last_create_timestamp"])


def parse_creator_index(data: object, developer: str, policy: dict) -> tuple[int, int, set[str], set[str]]:
    """Return global launch/migration counts, known mints and proof candidates."""
    if not isinstance(data, dict):
        raise HistoryUnavailableError("GMGN creator index is not an object")
    _validate_identity(data, developer)
    inner, migrated = _count(data.get("inner_count")), _count(data.get("open_count"))
    total = inner + migrated
    if not 0 < total <= policy["history_max_accounts"]:
        raise HistoryUnavailableError("GMGN creator launch count is empty or oversized")
    ratio = data.get("open_ratio")
    if type(ratio) not in (str, int, float) or isinstance(ratio, bool):
        raise HistoryUnavailableError("GMGN creator graduation ratio is invalid")
    try:
        reported = Decimal(str(ratio))
        if not reported.is_finite() or not 0 <= reported <= 1:
            raise InvalidOperation
        quantum = Decimal(1).scaleb(reported.as_tuple().exponent)
        tolerance = max(Decimal("0.000001"), quantum / 2) if 0 < reported < 1 else Decimal("0.000001")
        if abs(reported - Decimal(migrated) / Decimal(total)) > tolerance:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise HistoryUnavailableError("GMGN creator aggregates are inconsistent") from None
    known, candidates, open_rows = _token_rows(data, policy)
    if len(known) > total or open_rows > migrated:
        raise HistoryUnavailableError("GMGN creator rows contradict the aggregate counts")
    return total, migrated, known, candidates


async def _wait_read_slot() -> None:
    """Weight-two reads share the free tier's five-units/second allowance."""
    while True:
        now = time.monotonic()
        if now < _RATE_LIMIT_STATE["until"]:
            raise HistoryUnavailableError("GMGN creator index rate limit cooldown")
        delay = _RATE_LIMIT_STATE["next_at"] - now
        if delay <= 0:
            # No await between checking and claiming; concurrent tasks cannot
            # claim the same slot. Recheck after sleeps rather than compressing
            # delayed reservations into a burst when the loop is overloaded.
            _RATE_LIMIT_STATE["next_at"] = now + 0.4
            return
        await asyncio.sleep(delay)


async def _fetch_index(session: object, developer: str, api_key: str, *, migrated: bool = False) -> dict:
    await _wait_read_slot()
    params = {"chain": "sol", "wallet_address": developer,
              "timestamp": str(int(time.time())), "client_id": str(uuid.uuid4())}
    if migrated:
        params["migrate_state"] = "migrated"
    try:
        async with session.get(GMGN_URL, params=params, allow_redirects=False, headers={
            "X-APIKEY": api_key, "Content-Type": "application/json",
            "User-Agent": "definitivo-sol-history/1.0",
        }) as response:
            if response.status == 429:
                _RATE_LIMIT_STATE["until"] = time.monotonic() + 300
                raise HistoryUnavailableError("GMGN creator index rate limited")
            if response.status != 200:
                raise HistoryUnavailableError("GMGN creator index request unavailable")
            payload = await response.json(content_type=None)
        if (not isinstance(payload, dict) or type(payload.get("code")) not in (str, int)
                or str(payload["code"]) != "0" or not isinstance(payload.get("data"), dict)):
            raise HistoryUnavailableError("GMGN creator index response is invalid")
        return payload["data"]
    except HistoryUnavailableError:
        raise
    except Exception:
        raise HistoryUnavailableError("GMGN creator index request failed") from None


def _signed_creation(transaction: object, mint: str, developer: str, anchor: int) -> bool:  # noqa: PLR0911 -- each missing proof rejects independently
    """Unrelated truncated transactions cannot invalidate a mint's own proof."""
    try:
        if not isinstance(transaction, dict):
            return False
        slot = transaction.get("slot")
        if type(slot) is not int or not 0 < slot <= anchor:
            return False
        meta, body = transaction["meta"], transaction["transaction"]
        if "err" not in meta or meta["err"] is not None:
            return False
        signatures, message = body["signatures"], body["message"]
        keys, required = message["accountKeys"], message["header"]["numRequiredSignatures"]
        if (not isinstance(signatures, list) or not signatures
                or not all(isinstance(value, str) and value for value in signatures)
                or not isinstance(keys, list) or not all(isinstance(key, str) for key in keys)
                or type(required) is not int or not 1 <= required <= len(keys)
                or developer not in keys[:required]):
            return False
        for raw in pump_events(meta["logMessages"], strict=True):
            if raw[:8] != CREATE:
                continue
            event = event_parser().decode_event_data(raw, "CreateEvent")
            fields = event["fields"] if event and event.get("event_name") == "CreateEvent" else {}
            if (fields.get("mint") == mint and fields.get("user") == developer
                    and fields.get("bonding_curve") == curve_address(mint)):
                return True
    except (KeyError, TypeError, ValueError, IncompletePumpLogsError):
        return False
    return False


async def _verify_creator(mint: str, developer: str, rpc: object, anchor: int, gate: asyncio.Semaphore) -> bool:
    async with gate:
        page = await rpc("getTransactionsForAddress", [mint, {
            "transactionDetails": "full", "sortOrder": "asc", "encoding": "json", "limit": 10,
            "maxSupportedTransactionVersion": 1, "commitment": "finalized",
            "filters": {"status": "succeeded", "tokenAccounts": "none", "slot": {"lte": anchor}},
        }])
    rows = page.get("data") if isinstance(page, dict) else None
    if not isinstance(rows, list) or len(rows) > 10:
        return False
    return any(_signed_creation(row, mint, developer, anchor) for row in rows)


async def _verified_migrations(candidates: set[str], developer: str, rpc: object,
                               anchor: int, sufficient: int) -> set[str]:
    verified = set()
    gate = asyncio.Semaphore(2)
    # A verified lower bound is enough; do not issue a lookup per indexed launch.
    ordered = sorted(candidates)
    for start in range(0, len(ordered), 50):
        batch = ordered[start:start + 50]
        addresses = [address for mint in batch for address in (curve_address(mint), pool_address(mint)[0])]
        result = await rpc("getMultipleAccounts", [addresses, {
            "encoding": "base64", "commitment": "finalized", "minContextSlot": anchor,
        }])
        slot = result.get("context", {}).get("slot") if isinstance(result, dict) else None
        accounts = result.get("value") if isinstance(result, dict) else None
        if type(slot) is not int or slot < anchor or not isinstance(accounts, list) or len(accounts) != len(addresses):
            raise HistoryUnavailableError("GMGN migration proof account batch is incomplete")
        canonical = [mint for index, mint in enumerate(batch)
                     if migration_proven(mint, accounts[index * 2], accounts[index * 2 + 1])]
        # Only two identity lookups run at once; each has at most ten transactions.
        identities = await asyncio.gather(*(_verify_creator(mint, developer, rpc, anchor, gate)
                                            for mint in canonical))
        verified.update(curve_address(mint) for mint, valid in zip(canonical, identities, strict=True) if valid)
        if len(verified) >= sufficient:
            break
    return verified


async def read_gmgn_creator_history(developer: str, rpc: object, policy: dict) -> CreatorSnapshot:
    """Use GMGN's labeled denominator and only chain-verified migration successes."""
    api_key = os.environ.get("GMGN_API_KEY", "").strip()
    if not api_key:
        raise HistoryUnavailableError("GMGN_API_KEY is unavailable for creator history")
    try:
        if not Pubkey.from_string(developer).is_on_curve():
            raise HistoryUnavailableError("GMGN creator must be a signer wallet")
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4), connector=connector) as session:
            data = await _fetch_index(session, developer, api_key)
            total, reported, known, candidates = parse_creator_index(data, developer, policy)
            extra_launches = 0
            if len(candidates) < reported:
                extra = await _fetch_index(session, developer, api_key, migrated=True)
                _validate_identity(extra, developer)
                more, additional, _open_rows = _token_rows(extra, policy)
                # The two index reads are not an atomic snapshot. Any newly
                # listed mint may postdate the first global count; charge it
                # conservatively to the denominator even when it is older.
                extra_launches = len(more - known)
                known.update(more)
                candidates.update(additional)
                if (len(known) > total + extra_launches or len(known) > policy["history_max_accounts"]
                        or len(candidates) > reported):
                    raise HistoryUnavailableError("GMGN creator index changed inconsistently")
        anchor = await rpc("getSlot", [{"commitment": "finalized"}])
        if type(anchor) is not int or anchor <= 0:
            raise HistoryUnavailableError("GMGN migration proof anchor is unavailable")
        sufficient = required_migrations(total + extra_launches + 256, policy)
        verified = await _verified_migrations(candidates, developer, rpc, anchor, sufficient)
        return CreatorSnapshot(
            developer, {curve_address(mint) for mint in known}, verified, 0, time.time(),
            source="gmgn_indexed_launches_verified_pumpswap_pools", migration_count_complete=False,
            indexed_launch_count=total, indexed_reported_migrations=reported,
            indexed_extra_launch_count=extra_launches,
        )
    except asyncio.CancelledError:
        raise
    except HistoryUnavailableError:
        raise
    except Exception:
        raise HistoryUnavailableError("GMGN creator history proof is unavailable") from None
