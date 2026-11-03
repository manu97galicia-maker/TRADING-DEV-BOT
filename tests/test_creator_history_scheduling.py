"""Keep the active dev history reader free of a second stale queued lookup."""

# Synthetic public keys and patched provider reads avoid network or wallet I/O.
# ruff: noqa: ANN001

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from solders.pubkey import Pubkey

from monitoring.creator_history_index import (
    CreatorHistoryCache,
    CreatorSnapshot,
    curve_address,
)

PROVIDER = "monitoring.creator_history_index.read_creator_history"
POLICY = {
    "entry_rule": "creator_migration_only",
    "creator_cache_seconds": 600,
    "history_timeout_seconds": 5,
}


class CreatorHistorySchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.creators = [str(Pubkey.new_unique()) for _ in range(3)]
        self.current = {creator: str(Pubkey.new_unique()) for creator in self.creators}
        self.old = {creator: str(Pubkey.new_unique()) for creator in self.creators}
        self.cache = CreatorHistoryCache(AsyncMock(), dict(POLICY))
        self.addAsyncCleanup(self.cache.close)
        for creator in self.creators:
            self.cache.observe(creator, self.current[creator])

    def snapshot(self, creator: str) -> CreatorSnapshot:
        old = curve_address(self.old[creator])
        return CreatorSnapshot(creator, {old}, {old}, 100, time.time())

    async def settle_until(self, predicate) -> None:
        for _ in range(30):
            if predicate():
                return
            await asyncio.sleep(0)
        self.assertTrue(predicate(), "The bounded in-memory task did not settle")

    async def test_active_history_has_no_second_request_waiting_behind_the_gate(self) -> None:
        first, stale, newest = self.creators
        release = {creator: asyncio.Event() for creator in self.creators}
        order = []

        async def read(creator, _rpc, _policy) -> CreatorSnapshot:
            order.append(creator)
            await release[creator].wait()
            return self.snapshot(creator)

        with patch(PROVIDER, new=AsyncMock(side_effect=read)) as provider:
            self.cache.warm(first)
            await self.settle_until(lambda: order == [first])
            self.cache.warm(stale)
            self.assertEqual(set(self.cache.pending), {first})
            provider.assert_awaited_once()

            release[first].set()
            await self.settle_until(lambda: not self.cache.pending)
            # The source can select a newer candidate after capacity returns;
            # the earlier refused lookup did not reserve the next network read.
            self.cache.warm(newest)
            await self.settle_until(lambda: order == [first, newest])
            self.assertNotIn(stale, self.cache.pending)
            self.assertEqual(provider.await_count, 2)
            release[newest].set()
            await self.settle_until(lambda: not self.cache.pending)
        self.assertEqual(self.cache.observed[stale], {curve_address(self.current[stale])})
        self.assertNotIn(stale, self.cache.snapshots)

    async def test_refusing_background_work_never_discards_observed_launches(self) -> None:
        busy, waiting, _unused = self.creators
        started, finish = asyncio.Event(), asyncio.Event()

        async def read(creator, _rpc, _policy) -> CreatorSnapshot:
            started.set()
            await finish.wait()
            return self.snapshot(creator)

        self.cache.snapshots[waiting] = self.snapshot(waiting)
        extra = str(Pubkey.new_unique())
        self.cache.observe(waiting, extra)
        before = self.cache.evidence(waiting, self.current[waiting])
        self.assertEqual(before["launches"], 2)
        with patch(PROVIDER, new=AsyncMock(side_effect=read)) as provider:
            self.cache.warm(busy)
            await self.settle_until(started.is_set)
            self.cache.warm(waiting)
            self.assertEqual(self.cache.observed[waiting], {
                curve_address(self.current[waiting]), curve_address(extra),
            })
            self.assertEqual(self.cache.evidence(waiting, self.current[waiting]), before)
            provider.assert_awaited_once()
            finish.set()
            await self.settle_until(lambda: not self.cache.pending)

    async def test_fresh_cached_creator_does_not_take_background_capacity(self) -> None:
        creator = self.creators[0]
        snapshot = self.snapshot(creator)
        self.cache.snapshots[creator] = snapshot
        with patch(PROVIDER, new=AsyncMock()) as provider:
            self.cache.warm(creator)
            await asyncio.sleep(0)
            provider.assert_not_called()
        self.assertFalse(self.cache.pending)
        self.assertIs(self.cache.snapshots[creator], snapshot)

    async def test_close_cancels_active_history_and_clears_its_pending_slot(self) -> None:
        creator = self.creators[0]
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def read(_creator, _rpc, _policy) -> CreatorSnapshot:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            message = "The fake blocked provider must be cancelled"
            raise AssertionError(message)

        with patch(PROVIDER, new=AsyncMock(side_effect=read)):
            self.cache.warm(creator)
            await self.settle_until(started.is_set)
            task = self.cache.pending[creator]
            await self.cache.close()
            await self.settle_until(lambda: not self.cache.pending)
        self.assertTrue(cancelled.is_set())
        self.assertTrue(task.cancelled())
        self.assertFalse(self.cache.snapshots)

    async def test_classic_profile_keeps_two_pending_reads_but_executes_one_at_a_time(self) -> None:
        self.cache.policy["entry_rule"] = "second_block"
        first, second, third = self.creators
        finish = {creator: asyncio.Event() for creator in self.creators}
        order = []

        async def read(creator, _rpc, _policy) -> CreatorSnapshot:
            order.append(creator)
            await finish[creator].wait()
            return self.snapshot(creator)

        with patch(PROVIDER, new=AsyncMock(side_effect=read)) as provider:
            self.cache.warm(first)
            self.cache.warm(second)
            self.cache.warm(third)
            await self.settle_until(lambda: order == [first])
            self.assertEqual(set(self.cache.pending), {first, second})
            provider.assert_awaited_once()
            finish[first].set()
            await self.settle_until(lambda: order == [first, second])
            self.assertNotIn(third, self.cache.pending)
            self.assertEqual(provider.await_count, 2)
            finish[second].set()
            await self.settle_until(lambda: not self.cache.pending)


if __name__ == "__main__":
    unittest.main()
