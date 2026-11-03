"""Offline creator-history selection; synthetic Pump creations, no submissions."""

# ruff: noqa: ANN001, ANN002, ANN003, ANN201, ANN202, ARG001, ARG005

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_second_block_source as stream
from solders.pubkey import Pubkey


class HistoryOnlySourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await stream.SourceTests.asyncSetUp(self)
        self.policy["entry_rule"] = "creator_migration_only"
        self.now = 100.0
        clock = SimpleNamespace(monotonic=lambda: self.now, time=lambda: 1800000000+self.now)
        clock_patch = patch("monitoring.second_block_source.time", clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.token.mint = self.mint
        self.reference_age = 1
        self.paused = False
        self.source.reference_age = lambda: self.reference_age
        self.source.allowed = lambda: not self.paused

    async def settle(self):
        for _ in range(10):
            await asyncio.sleep(0)

    async def created(self, *trades, user=None, slot=100):
        self.source.on_slot({"slot": 100, "parent": 99})
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator, user=user), *trades),
                            slot, "create")
        await self.settle()

    async def test_creation_without_trades_buys_immediately_and_copies_metadata(self):
        await self.created(user=self.a)
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["entry_rule"], "creator_migration_only")
        self.assertIsNone(signal["entry_block"])
        self.assertIsNone(signal["target_slot"])
        self.assertEqual(signal["developer"], str(self.a))
        self.assertEqual(signal["buyers"], 0)
        self.assertEqual(signal["volume_usd"], 0)
        self.assertEqual(signal["observed_mc_usd"], 0)
        self.assertEqual(signal["creator_history"]["migration_fraction"], .01)
        delivered = self.callback.await_args.args[0]
        self.assertIsNot(delivered, self.token)
        self.assertIsNot(delivered.additional_data, self.token.additional_data)
        self.assertIsNone(delivered.additional_data["dev_entry_target_slot"])
        self.assertEqual(delivered.additional_data["dev_entry_generation"], signal["source_generation"])
        self.assertNotIn("dev_entry_target_slot", self.token.additional_data)

    async def test_history_can_arrive_after_block_three_without_a_trade(self):
        history = {"migration_fraction": .01, "launches": 100, "migrations": 1}
        self.source.history.evidence = Mock(return_value=None)
        pending = asyncio.get_running_loop().create_future()
        self.source.history.pending = {str(self.creator): pending}
        await self.created()
        self.callback.assert_not_awaited()
        for slot in range(101, 109):
            self.now += 1
            self.source.on_slot({"slot": slot, "parent": slot-1})
        self.source.history.evidence.return_value = history
        pending.set_result(None)
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertIn(str(self.mint), self.source.candidates)
        self.assertEqual(self.source.signal(str(self.mint))["creation_slot"], 100)

    async def test_observed_creation_older_than_current_parent_is_still_considered(self):
        self.source.on_slot({"slot": 110, "parent": 109})
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator)), 100, "late-create")
        await self.settle()
        self.callback.assert_awaited_once()

    async def test_market_cap_volume_buyers_and_stale_price_do_not_select(self):
        self.source.history.evidence = lambda *args: None
        await self.created(stream.trade(self.mint, self.a, sol=.0001, mc=9000000))
        candidate = self.source.candidates[str(self.mint)]
        candidate.invalid = True  # A bounded first-block buyer list is not a selection rule.
        candidate.price_at = 0
        self.source.history.evidence = lambda *args: {"migration_fraction": .01}
        self.now += 10
        self.source.on_slot({"slot": 120, "parent": 119})
        await self.settle()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertAlmostEqual(signal["observed_mc_usd"], 9000000)
        self.assertEqual(signal["buyers"], 1)
        self.assertLess(signal["volume_usd"], 275)

    async def test_unknown_or_below_one_percent_history_blocks(self):
        self.source.history.evidence = Mock(return_value=None)
        await self.created()
        self.callback.assert_not_awaited()
        for evidence in [None, {"migration_fraction": 0}, {"migration_fraction": .009999}]:
            with self.subTest(evidence=evidence):
                self.source.history.evidence.return_value = evidence
                self.assertIsNone(self.source.signal(str(self.mint)))
                self.source.consider(self.source.candidates[str(self.mint)])
                await self.settle()
                self.callback.assert_not_awaited()

    async def test_preparation_retries_none_at_most_once_per_second(self):
        builder = AsyncMock(side_effect=[None, self.token])
        with patch("monitoring.second_block_source.build_pumpfun_token_info", builder):
            await self.created()
            builder.assert_awaited_once()
            self.assertIsNone(self.source.signal(str(self.mint)))
            self.now += .5
            self.source.on_slot({"slot": 101, "parent": 100})
            await self.settle()
            builder.assert_awaited_once()
            self.now += .5
            self.source.on_slot({"slot": 102, "parent": 101})
            await self.settle()
            self.assertEqual(builder.await_count, 2)
            self.callback.assert_awaited_once()

    async def test_preparation_concurrency_is_bounded_to_two(self):
        release = asyncio.Event()
        active = 0
        peak = 0

        async def build(mint, *args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await release.wait()
                return SimpleNamespace(creator=self.creator, additional_data={})
            finally:
                active -= 1

        with patch("monitoring.second_block_source.build_pumpfun_token_info", side_effect=build) as builder:
            self.source.on_slot({"slot": 100, "parent": 99})
            for index in range(3):
                mint = Pubkey.new_unique()
                self.source.on_logs(stream.logs(stream.create(mint, self.creator)), 100, f"create-{index}")
            await self.settle()
            self.assertEqual(builder.call_count, 2)
            self.assertEqual(peak, 2)
            release.set()
            await self.settle()
            self.now += 1
            self.source.on_slot({"slot": 101, "parent": 100})
            await self.settle()
            self.assertEqual(builder.call_count, 3)
            self.assertEqual(peak, 2)

    async def test_duplicate_logs_slots_and_refreshes_cannot_attempt_twice(self):
        await self.created()
        candidate = self.source.candidates[str(self.mint)]
        for slot in range(101, 110):
            self.source.on_slot({"slot": slot, "parent": slot-1})
            self.source.on_logs(stream.logs(stream.create(self.mint, self.creator)), slot, f"duplicate-{slot}")
            self.source.consider(candidate)
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertEqual(candidate.attempted_slots, {100})
        self.assertFalse(self.source.arm_third_block_retry(str(self.mint), 100, self.source.generation))

    async def test_attempt_tombstone_survives_eviction_and_disconnect_for_ten_minutes(self):
        await self.created()
        self.source.candidates.pop(str(self.mint))
        self.source.disconnect()
        self.source.on_slot({"slot": 110, "parent": 109})
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator)), 110, "reconnect-duplicate")
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.now += 600.01
        self.source.on_slot({"slot": 120, "parent": 119})
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator)), 120, "after-tombstone-ttl")
        await self.settle()
        self.assertEqual(self.callback.await_count, 2)

    async def test_full_attempt_cache_waits_until_expired_without_exceeding_bound(self):
        self.source.attempted_mints = {f"prior-{index}": self.now for index in range(4096)}
        await self.created()
        self.callback.assert_not_awaited()
        self.assertEqual(len(self.source.attempted_mints), 4096)
        self.now += 600.01
        # Refresh the observation to isolate the attempt-cache technical limit.
        self.source.candidates[str(self.mint)].observed_at = self.now
        self.source.on_slot({"slot": 110, "parent": 109})
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertEqual(len(self.source.attempted_mints), 1)

    async def test_returned_future_keeps_attempt_inflight_until_cleanup(self):
        completion = asyncio.get_running_loop().create_future()
        self.callback.return_value = completion
        await self.created()
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.inflight_slot, 100)
        self.source.on_slot({"slot": 105, "parent": 104})
        await self.settle()
        self.callback.assert_awaited_once()
        completion.set_result(None)
        await self.settle()
        self.assertIsNone(candidate.inflight_slot)
        self.callback.assert_awaited_once()

    async def test_candidate_capacity_never_evicts_running_delivery(self):
        completion = asyncio.get_running_loop().create_future()
        self.callback.return_value = completion
        await self.created()
        candidate = self.source.candidates[str(self.mint)]
        with patch.object(self.source, "refresh"):
            for index in range(257):
                mint = Pubkey.new_unique()
                self.source.on_logs(stream.logs(stream.create(mint, self.creator)), 100, f"inflight-bound-{index}")
        self.assertIs(self.source.candidates.get(str(self.mint)), candidate)
        self.assertEqual(len(self.source.candidates), 256)
        self.assertFalse(completion.cancelled())
        completion.set_result(None)
        await self.settle()

    async def test_expired_candidate_stays_until_running_delivery_finishes(self):
        completion = asyncio.get_running_loop().create_future()
        self.callback.return_value = completion
        await self.created()
        candidate = self.source.candidates[str(self.mint)]
        self.now += 601
        self.source.on_slot({"slot": 110, "parent": 109})
        self.assertIs(self.source.candidates.get(str(self.mint)), candidate)
        self.assertFalse(completion.cancelled())
        self.assertIsNone(self.source.signal(str(self.mint)))
        completion.set_result(None)
        await self.settle()
        self.source.on_slot({"slot": 111, "parent": 110})
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.callback.assert_awaited_once()

    async def test_technical_freshness_pause_and_disconnect_still_block(self):
        self.source.history.evidence = lambda *args: None
        await self.created()
        self.source.history.evidence = lambda *args: {"migration_fraction": .01}
        self.now += 30.01
        self.source.on_slot({"slot": 100, "parent": 99})
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.reference_age = 30.01
        self.source.on_slot({"slot": 110, "parent": 109})
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.reference_age = 1
        self.paused = True
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.a)), 111, "future-log")
        self.paused = False
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.source.disconnect()
        self.assertIsNone(self.source.signal(str(self.mint)))
        await self.settle()
        self.callback.assert_not_awaited()

    async def test_candidate_lifetime_is_ten_minutes_and_cache_is_bounded(self):
        self.source.history.evidence = lambda *args: None
        await self.created()
        self.now += 599
        self.source.on_slot({"slot": 110, "parent": 109})
        self.assertIn(str(self.mint), self.source.candidates)
        self.now += 1.01
        self.source.on_slot({"slot": 111, "parent": 110})
        self.assertNotIn(str(self.mint), self.source.candidates)
        with patch.object(self.source, "refresh"):
            mints = []
            for index in range(257):
                mint = Pubkey.new_unique()
                mints.append(str(mint))
                self.source.on_logs(stream.logs(stream.create(mint, self.creator)), 111, f"bounded-{index}")
        self.assertEqual(len(self.source.candidates), 256)
        self.assertNotIn(mints[0], self.source.candidates)
        self.assertIn(mints[-1], self.source.candidates)
        self.source.history.evidence = lambda *args: {"migration_fraction": .01}
        qualified = Pubkey.new_unique()
        self.source.on_logs(stream.logs(stream.create(qualified, self.creator)), 111, "qualified-after-full-cache")
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertIn(str(qualified), self.source.candidates)
        self.assertEqual(len(self.source.candidates), 256)

    async def test_disconnected_build_and_history_completion_cannot_deliver_old_candidate(self):
        pending = asyncio.get_running_loop().create_future()
        self.source.history.pending = {str(self.creator): pending}
        release = asyncio.Event()

        async def build(*args, **kwargs):
            await release.wait()
            return self.token

        with patch("monitoring.second_block_source.build_pumpfun_token_info", side_effect=build):
            await self.created()
            self.source.disconnect()
            self.source.on_slot({"slot": 110, "parent": 109})
            release.set()
            pending.set_result(None)
            await self.settle()
        self.callback.assert_not_awaited()
        self.assertFalse(self.source.candidates)

    async def test_disconnect_preserves_an_already_running_completion(self):
        completion = asyncio.get_running_loop().create_future()
        self.callback.return_value = completion
        await self.created()
        self.source.disconnect()
        self.assertFalse(completion.cancelled())
        completion.set_result(None)
        await self.settle()
        self.callback.assert_awaited_once()
