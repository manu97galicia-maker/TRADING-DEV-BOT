"""Bounded, fully paginated creator history from owned on-chain accounts.

Only canonical PumpSwap migration pools count. Older Raydium migrations are
not counted, so the numerator is conservative. No price/ATH proxy is used.
"""

# Every prefix offset below is from the shipped Pump/PumpSwap account IDLs.
# ruff: noqa: PLR2004, TRY003, BLE001

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass

import base58
from solders.pubkey import Pubkey

PUMP = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
AMM = Pubkey.from_string("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")
WSOL = Pubkey.from_string("So11111111111111111111111111111111111111112")
CURVE_DISC = hashlib.sha256(b"account:BondingCurve").digest()[:8]
POOL_DISC = hashlib.sha256(b"account:Pool").digest()[:8]


class HistoryUnavailableError(ValueError):
    """Never treat unavailable, incomplete or cyclic pages as zero launches."""


def curve_address(mint: str) -> str:
    return str(Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(mint))], PUMP)[0])


def account_prefix(row: dict, program: Pubkey, discriminator: bytes, length: int) -> bytes:
    account = row["account"]
    if account.get("owner") != str(program) or account.get("executable") is not False:
        raise HistoryUnavailableError("Wrong history account owner")
    if (not isinstance(account.get("data"), list) or len(account["data"]) != 2
            or account["data"][1] != "base64"):
        raise HistoryUnavailableError("Unexpected account encoding")
    raw = base64.b64decode(account["data"][0], validate=True)
    if len(raw) != length or raw[:8] != discriminator:
        raise HistoryUnavailableError("Unexpected history account prefix")
    Pubkey.from_string(row["pubkey"])
    return raw


async def scan_pages(rpc: object, program: Pubkey, creator: str, offset: int,  # noqa: PLR0913, PLR0917 -- IDL filter contract
                     discriminator: bytes, length: int, policy: dict) -> tuple[list[dict], int]:
    cursor = None
    cursors, addresses = set(), set()
    output = []
    slot = 0
    for _ in range(policy["history_max_pages"]):
        config = {"encoding": "base64", "commitment": "confirmed", "withContext": True,
            "limit": policy["history_page_size"], "dataSlice": {"offset": 0, "length": length},
            "filters": [{"memcmp": {"offset": 0, "bytes": base58.b58encode(discriminator).decode()}},
                        {"memcmp": {"offset": offset, "bytes": creator}}]}
        if cursor is not None:
            config["paginationKey"] = cursor
        response = await rpc("getProgramAccountsV2", [str(program), config])
        body = response.get("value")
        current_slot = response.get("context", {}).get("slot")
        if (not isinstance(body, dict) or "paginationKey" not in body
                or type(current_slot) is not int or current_slot < slot
                or not isinstance(body.get("accounts"), list)):
            raise HistoryUnavailableError("Incomplete paginated history")
        slot = current_slot
        for row in body["accounts"]:
            raw = account_prefix(row, program, discriminator, length)
            if str(Pubkey.from_bytes(raw[offset:offset+32])) != creator or row["pubkey"] in addresses:
                raise HistoryUnavailableError("Duplicate or unfiltered creator history")
            addresses.add(row["pubkey"])
            output.append(row)
            if len(output) > policy["history_max_accounts"]:
                raise HistoryUnavailableError("Creator history exceeds the memory bound")
        cursor = body["paginationKey"]
        if cursor is None:
            return output, slot
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise HistoryUnavailableError("Cyclic history cursor")
        cursors.add(cursor)
    raise HistoryUnavailableError("History pagination limit reached")


@dataclass
class CreatorSnapshot:
    creator: str
    curves: set[str]
    migrated_curves: set[str]
    slot: int
    checked_at: float


async def read_creator_history(creator: str, rpc: object, policy: dict) -> CreatorSnapshot:
    Pubkey.from_string(creator)
    curves, slot = await scan_pages(rpc, PUMP, creator, 49, CURVE_DISC, 81, policy)
    if not curves:
        raise HistoryUnavailableError("No complete creator history available")
    complete = {}
    for row in curves:
        raw = account_prefix(row, PUMP, CURVE_DISC, 81)
        if raw[48] not in {0, 1}:
            raise HistoryUnavailableError("Invalid curve completion flag")
        complete[row["pubkey"]] = bool(raw[48])
    pools, pool_slot = await scan_pages(rpc, AMM, creator, 211, POOL_DISC, 243, policy)
    migrated = set()
    for row in pools:
        raw = account_prefix(row, AMM, POOL_DISC, 243)
        mint = Pubkey.from_bytes(raw[43:75])
        authority = Pubkey.find_program_address([b"pool-authority", bytes(mint)], PUMP)[0]
        canonical = Pubkey.find_program_address([b"pool", b"\0\0", bytes(authority), bytes(mint), bytes(WSOL)], AMM)[0]
        curve = curve_address(str(mint))
        if (raw[9:11] == b"\0\0" and raw[11:43] == bytes(authority)
                and raw[75:107] == bytes(WSOL) and row["pubkey"] == str(canonical)
                and complete.get(curve)):
            migrated.add(curve)
    # Repeat the denominator after reading pools: a pool query must never add
    # newer successes against an older, smaller launch count. Pages are not an
    # atomic snapshot; missing/closed/reassigned accounts remain a limitation.
    newer, newer_slot = await scan_pages(rpc, PUMP, creator, 49, CURVE_DISC, 81, policy)
    current = {row["pubkey"] for row in newer}
    if newer_slot < max(slot, pool_slot) or not set(complete) <= current:
        raise HistoryUnavailableError("Creator history changed inconsistently")
    return CreatorSnapshot(creator, current, migrated, newer_slot, time.time())


class CreatorHistoryCache:
    """Keep history work away from the block-boundary callback and buy path."""

    def __init__(self, rpc: object, policy: dict) -> None:
        self.rpc, self.policy = rpc, policy
        self.snapshots: dict[str, CreatorSnapshot] = OrderedDict()
        self.observed: dict[str, set[str]] = OrderedDict()
        self.pending: dict[str, asyncio.Task] = {}
        self.retry_after: dict[str, float] = {}
        self.gate = asyncio.Semaphore(1)
        self.last_error: str | None = None
        self.generation = 0
        self.overflow: set[str] = set()

    def observe(self, creator: str, mint: str) -> None:
        if creator not in self.observed and len(self.observed) >= 64:
            old = next(iter(self.observed))
            self.observed.pop(old)
            self.snapshots.pop(old, None)
            self.overflow.discard(old)
            self.retry_after.pop(old, None)
        curves = self.observed.setdefault(creator, set())
        if len(curves) >= 256:
            self.overflow.add(creator)
            self.snapshots.pop(creator, None)
        else:
            curves.add(curve_address(mint))

    def evidence(self, creator: str, mint: str) -> dict | None:
        snapshot = self.snapshots.get(creator)
        if (creator in self.overflow or creator not in self.observed or not snapshot
                or not 0 <= time.time()-snapshot.checked_at <= self.policy["creator_cache_seconds"]):
            return None
        current = curve_address(mint)
        curves = (snapshot.curves | self.observed.get(creator, set())) - {current}
        migrations = snapshot.migrated_curves - {current}
        if not curves or not migrations <= curves:
            return None
        return {"launches": len(curves), "migrations": len(migrations),
            "migration_fraction": len(migrations)/len(curves), "checked_at": snapshot.checked_at,
            "slot": snapshot.slot, "source": "canonical_pumpswap_pools_over_creator_curves",
            "older_raydium_migrations_not_counted": True}

    def warm(self, creator: str) -> None:
        if (creator not in self.observed or creator in self.overflow or creator in self.pending
                or len(self.pending) >= 2 or time.monotonic() < self.retry_after.get(creator, 0)):
            return
        snapshot = self.snapshots.get(creator)
        if snapshot and 0 <= time.time()-snapshot.checked_at <= self.policy["creator_cache_seconds"]:
            return
        task = asyncio.create_task(self.refresh(creator))
        self.pending[creator] = task
        def done(completed: asyncio.Task) -> None:
            if self.pending.get(creator) is completed:
                self.pending.pop(creator, None)
        task.add_done_callback(done)

    async def refresh(self, creator: str) -> None:
        generation = self.generation
        observation = self.observed.get(creator)
        try:
            async with self.gate:
                snapshot = await asyncio.wait_for(read_creator_history(creator, self.rpc, self.policy),
                                                  timeout=self.policy["history_timeout_seconds"])
            if (generation != self.generation or observation is not self.observed.get(creator)
                    or creator not in self.observed or creator in self.overflow):
                return
            if len(self.snapshots) >= 64 and creator not in self.snapshots:
                self.snapshots.pop(next(iter(self.snapshots)))
            while self.snapshots and sum(len(s.curves) for s in self.snapshots.values()) + len(snapshot.curves) > 20000:
                self.snapshots.pop(next(iter(self.snapshots)))
            self.snapshots[creator] = snapshot
            self.last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if generation == self.generation and creator in self.observed:
                self.last_error = type(error).__name__
                self.retry_after[creator] = time.monotonic()+30

    def invalidate(self) -> None:
        self.generation += 1
        self.snapshots.clear()
        self.observed.clear()
        self.overflow.clear()
        self.retry_after.clear()
        for task in self.pending.values():
            task.cancel()

    async def close(self) -> None:
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
