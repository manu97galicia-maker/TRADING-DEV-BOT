"""History proof uses synthetic owned account prefixes, never a live endpoint."""

# ruff: noqa: ANN001, ANN002, ANN201, ANN202, ARG001

import asyncio
import base64
import time
import unittest
from unittest.mock import AsyncMock, patch

from solders.pubkey import Pubkey

from monitoring.creator_history_index import (
    AMM,
    CURVE_DISC,
    POOL_DISC,
    PUMP,
    WSOL,
    CreatorHistoryCache,
    CreatorSnapshot,
    HistoryUnavailableError,
    curve_address,
    read_creator_history,
    scan_pages,
)

POLICY = {"history_max_pages": 4, "history_page_size": 10,
          "history_max_accounts": 100, "creator_cache_seconds": 600,
          "history_timeout_seconds": 1}


def row(address, program, raw):
    return {"pubkey": str(address), "account": {"owner": str(program), "executable": False,
            "data": [base64.b64encode(raw).decode(), "base64"]}}


def curve(creator, mint, *, complete=False):
    return row(curve_address(str(mint)), PUMP, CURVE_DISC+b"\0"*40+bytes([complete])+bytes(creator))


def page(rows, cursor=None, slot=100):
    return {"context": {"slot": slot}, "value": {"accounts": rows, "paginationKey": cursor}}


class PageTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_filtered_page_with_cursor_does_not_mean_complete_history(self):
        creator, mint = Pubkey.new_unique(), Pubkey.new_unique()
        rpc = AsyncMock(side_effect=[page([], "next"), page([curve(creator, mint)], slot=101)])
        rows, slot = await scan_pages(rpc, PUMP, str(creator), 49, CURVE_DISC, 81, POLICY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(slot, 101)
        self.assertEqual(rpc.await_args_list[1].args[1][1]["paginationKey"], "next")

    async def test_partial_cyclic_wrong_owner_and_wrong_creator_are_unknown(self):
        creator, other, mint = Pubkey.new_unique(), Pubkey.new_unique(), Pubkey.new_unique()
        wrong_owner = curve(creator, mint)
        wrong_owner["account"]["owner"] = str(AMM)
        cases = [
            [page([], "same")]*4,
            [page([], "1"), page([], "2"), page([], "3"), page([], "4")],
            [page([wrong_owner])], [page([curve(other, mint)])],
            [{"context": {"slot": 100}, "value": {"accounts": []}}],
        ]
        for responses in cases:
            with self.subTest(responses=len(responses)), self.assertRaises(HistoryUnavailableError):
                await scan_pages(AsyncMock(side_effect=responses), PUMP, str(creator),
                                 49, CURVE_DISC, 81, POLICY)

    async def test_only_verified_canonical_migration_pools_count(self):
        creator, mint, fresh = Pubkey.new_unique(), Pubkey.new_unique(), Pubkey.new_unique()
        authority = Pubkey.find_program_address([b"pool-authority", bytes(mint)], PUMP)[0]
        canonical = Pubkey.find_program_address([b"pool", b"\0\0", bytes(authority), bytes(mint), bytes(WSOL)], AMM)[0]
        raw = bytearray(243)
        raw[:8], raw[11:43], raw[43:75] = POOL_DISC, bytes(authority), bytes(mint)
        raw[75:107], raw[211:243] = bytes(WSOL), bytes(creator)
        pools = [row(canonical, AMM, raw), row(Pubkey.new_unique(), AMM, raw)]
        old, new = curve(creator, mint, complete=True), curve(creator, fresh)
        rpc = AsyncMock(side_effect=[page([old]), page(pools, slot=101), page([old, new], slot=102)])
        snapshot = await read_creator_history(str(creator), rpc, POLICY)
        self.assertEqual(len(snapshot.curves), 2)
        self.assertEqual(snapshot.migrated_curves, {str(old["pubkey"])})
        self.assertEqual(snapshot.slot, 102)


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_candidate_is_excluded_and_live_launches_increase_denominator(self):
        creator = str(Pubkey.new_unique())
        old, migrated, current, later = [str(Pubkey.new_unique()) for _ in range(4)]
        cache = CreatorHistoryCache(AsyncMock(), POLICY)
        cache.observe(creator, current)
        cache.snapshots[creator] = CreatorSnapshot(creator,
            {curve_address(x) for x in [old, migrated, current]}, {curve_address(migrated)}, 100, time.time())
        self.assertEqual(cache.evidence(creator, current)["migration_fraction"], .5)
        cache.observe(creator, later)
        self.assertEqual(cache.evidence(creator, current)["launches"], 3)
        cache.snapshots[creator].checked_at -= 601
        self.assertIsNone(cache.evidence(creator, current))
        cache.invalidate()
        self.assertIsNone(cache.evidence(creator, current))

    async def test_history_anchor_must_cover_the_start_of_live_observation(self):
        creator = str(Pubkey.new_unique())
        old, current = str(Pubkey.new_unique()), str(Pubkey.new_unique())
        cache = CreatorHistoryCache(AsyncMock(), POLICY)
        cache.observe(creator, current)
        cache.coverage_start_slot = 101
        snapshot = CreatorSnapshot(creator, {curve_address(old)}, {curve_address(old)}, 100, time.time())
        cache.snapshots[creator] = snapshot
        self.assertIsNone(cache.evidence(creator, current))
        snapshot.slot = 101
        self.assertIsNotNone(cache.evidence(creator, current))
        cache.invalidate()
        self.assertIsNone(cache.coverage_start_slot)

    async def test_refresh_started_before_disconnect_cannot_repopulate_history(self):
        creator, mint = str(Pubkey.new_unique()), str(Pubkey.new_unique())
        cache = CreatorHistoryCache(AsyncMock(), POLICY)
        cache.observe(creator, mint)
        started, finish = asyncio.Event(), asyncio.Event()
        async def read(*args):
            started.set()
            await finish.wait()
            return CreatorSnapshot(creator, {curve_address(mint)}, set(), 100, time.time())
        with patch("monitoring.creator_history_index.read_creator_history", side_effect=read):
            task = asyncio.create_task(cache.refresh(creator))
            await started.wait()
            cache.invalidate()
            finish.set()
            await task
        self.assertFalse(cache.snapshots)

    async def test_observation_overflow_cannot_understate_launch_count(self):
        creator = str(Pubkey.new_unique())
        cache = CreatorHistoryCache(AsyncMock(), POLICY)
        for _ in range(300):
            mint = str(Pubkey.new_unique())
            cache.observe(creator, mint)
        self.assertEqual(len(cache.observed[creator]), 256)
        self.assertIn(creator, cache.overflow)
        self.assertIsNone(cache.evidence(creator, mint))

    async def test_refresh_prunes_indexed_observations_but_keeps_new_unfinalized_launches(self):
        creator = str(Pubkey.new_unique())
        old, current = str(Pubkey.new_unique()), str(Pubkey.new_unique())
        cache = CreatorHistoryCache(AsyncMock(), POLICY)
        cache.observe(creator, old)
        cache.observe(creator, current)
        snapshot = CreatorSnapshot(creator, {curve_address(old)}, {curve_address(old)}, 100, time.time())
        with patch("monitoring.creator_history_index.read_creator_history", return_value=snapshot):
            await cache.refresh(creator)
        self.assertEqual(cache.observed[creator], {curve_address(current)})
        self.assertEqual(cache.evidence(creator, current)["migration_fraction"], 1)

    async def test_evicted_creator_refresh_cannot_reuse_a_new_observation_session(self):
        creator, mint = str(Pubkey.new_unique()), str(Pubkey.new_unique())
        cache = CreatorHistoryCache(AsyncMock(), POLICY)
        cache.observe(creator, mint)
        started, finish = asyncio.Event(), asyncio.Event()
        async def read(*args):
            started.set()
            await finish.wait()
            return CreatorSnapshot(creator, {curve_address(mint)}, set(), 100, time.time())
        with patch("monitoring.creator_history_index.read_creator_history", side_effect=read):
            task = asyncio.create_task(cache.refresh(creator))
            await started.wait()
            cache.observed.pop(creator)
            cache.observe(creator, mint)
            finish.set()
            await task
        self.assertFalse(cache.snapshots)
