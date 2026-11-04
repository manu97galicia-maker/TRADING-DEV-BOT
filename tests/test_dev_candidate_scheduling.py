"""Prepare eligible recent developer candidates without wasting bounded workers."""

# Synthetic stream events and fake async builders never sign or access a node.
# ruff: noqa: ANN001, ANN201, SLF001

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_dev_history_only_source as history_tests
import test_second_block_source as stream
from solders.pubkey import Pubkey

from monitoring.creator_history_index import CreatorHistoryCache, curve_address
from monitoring.second_block_source import Candidate


class DevCandidateSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await history_tests.HistoryOnlySourceTests.asyncSetUp(self)
        self.policy.update(require_first_block_activity=True,
                           min_distinct_first_block_buyers=2, min_first_block_volume_usd=250)
        self.source.history.observe = Mock()
        self.source.history.warm = Mock()
        self.source.history.evidence = Mock(return_value={"migration_fraction": .01})

    settle = history_tests.HistoryOnlySourceTests.settle
    created = history_tests.HistoryOnlySourceTests.created

    async def test_all_creations_remain_in_denominator_without_scheduling_ineligible_work(self):
        cache = CreatorHistoryCache(AsyncMock(), self.policy)
        cache.warm = Mock()
        self.source.history = cache
        mints = [Pubkey.new_unique() for _ in range(3)]
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock) as builder:
            self.source.on_slot({"slot": 100, "parent": 99})
            for index, mint in enumerate(mints):
                self.source.on_logs(stream.logs(stream.create(mint, self.creator)), 100, f"creation-{index}")
            self.source.on_logs(stream.logs(stream.trade(mints[0], self.a, sol=10)), 100, "one-buyer")
            self.source.on_logs(stream.logs(stream.trade(mints[1], self.a, sol=1),
                                            stream.trade(mints[1], self.b, sol=1)), 100, "below-volume")
            await self.settle()
            self.assertEqual(cache.observed[str(self.creator)], {curve_address(str(mint)) for mint in mints})
            cache.warm.assert_not_called()
            builder.assert_not_awaited()
            self.assertFalse(self.source.building)
            self.callback.assert_not_awaited()

    async def test_second_buyer_starts_preparation_in_the_same_log_event_without_next_slot(self):
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   return_value=self.token) as builder:
            await self.created(stream.trade(self.mint, self.a, sol=1))
            builder.assert_not_awaited()
            self.source.history.warm.assert_not_called()
            self.source.on_logs(stream.logs(stream.trade(self.mint, self.b, sol=1.5)), 100, "qualifying-buy")
            self.assertIn(str(self.mint), self.source.building)
            self.assertEqual(self.source.slot, 100)
            await self.settle()
            builder.assert_awaited_once()
            self.callback.assert_awaited_once()
            self.source.history.warm.assert_called_once_with(str(self.creator))

    async def test_unknown_and_negative_histories_do_not_occupy_prepare_workers(self):
        mints = [Pubkey.new_unique() for _ in range(4)]
        proofs = {str(mints[0]): None, str(mints[1]): {"migration_fraction": 0},
                  str(mints[2]): {"migration_fraction": .009999}, str(mints[3]): {"migration_fraction": .01}}
        self.source.history.evidence.side_effect = lambda _creator, mint: proofs[mint]

        async def build(mint, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(mint=Pubkey.from_string(mint), creator=self.creator, additional_data={})

        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   side_effect=build) as builder:
            self.source.on_slot({"slot": 100, "parent": 99})
            for index, mint in enumerate(mints):
                self.source.on_logs(stream.logs(stream.create(mint, self.creator),
                    stream.trade(mint, self.a, sol=1), stream.trade(mint, self.b, sol=1.5)), 100, f"creation-{index}")
            await self.settle()
            builder.assert_awaited_once()
            self.assertEqual(builder.await_args.args[0], str(mints[3]))
            self.assertTrue(all(self.source.candidates[str(mint)].token is None for mint in mints[:3]))
            self.callback.assert_awaited_once()

    async def test_recent_eligible_candidates_use_bounded_workers_before_older_or_ineligible(self):
        mints = [Pubkey.new_unique() for _ in range(4)]
        candidates = []
        for index, mint in enumerate(mints):
            candidate = Candidate(str(mint), str(self.creator), 100, "Fake", "FK", developer=str(self.creator),
                                  buyers={str(self.a), str(self.b)}, buy_sol=2.5, observed_at=self.now-4+index)
            self.source.candidates[candidate.mint] = candidate
            candidates.append(candidate)
        candidates[-1].buyers = {str(self.a)}  # Most recent, but ineligible.
        release = asyncio.Event()

        async def build(mint, *_args: object, **_kwargs: object) -> SimpleNamespace:
            await release.wait()
            return SimpleNamespace(mint=Pubkey.from_string(mint), creator=self.creator, additional_data={})

        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   side_effect=build) as builder:
            self.source.on_slot({"slot": 101, "parent": 100})
            await self.settle()
            self.assertEqual([call.args[0] for call in builder.await_args_list], [str(mints[2]), str(mints[1])])
            self.assertEqual(len(self.source.building), 2)
            self.assertIsNone(candidates[0].token)
            self.assertIsNone(candidates[3].token)
            release.set()
            await self.settle()

    async def test_history_completion_prepares_immediately_and_does_not_rearm_done_future(self):
        self.source.history.evidence.return_value = None
        pending = asyncio.get_running_loop().create_future()
        self.source.history.pending = {str(self.creator): pending}
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   return_value=self.token) as builder:
            await self.created(stream.trade(self.mint, self.a, sol=1), stream.trade(self.mint, self.b, sol=1.5))
            builder.assert_not_awaited()
            self.assertEqual(self.source.history_refreshes, {pending})
            self.source.history.evidence.return_value = {"migration_fraction": .01}
            pending.set_result(None)  # The fake keeps this done Future in pending.
            await self.settle()
            builder.assert_awaited_once()
            self.callback.assert_awaited_once()
            self.assertFalse(self.source.history_refreshes)
            await self.settle()
            builder.assert_awaited_once()

    async def test_completed_shared_history_prioritizes_creation_slots_over_late_log_arrival(self):
        self.source.history.evidence.return_value = None
        pending = asyncio.get_running_loop().create_future()
        self.source.history.pending = {str(self.creator): pending}
        mints = [Pubkey.new_unique() for _ in range(3)]
        release = asyncio.Event()

        async def build(mint, *_args: object, **_kwargs: object) -> SimpleNamespace:
            await release.wait()
            return SimpleNamespace(mint=Pubkey.from_string(mint), creator=self.creator, additional_data={})

        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   side_effect=build) as builder:
            self.source.on_slot({"slot": 500, "parent": 499})
            for index, (mint, creation_slot) in enumerate(zip(mints, (300, 200, 100), strict=True)):
                self.now += 1
                self.source.on_logs(stream.logs(stream.create(mint, self.creator),
                    stream.trade(mint, self.a, sol=1), stream.trade(mint, self.b, sol=1.5)),
                    creation_slot, f"delayed-creation-{index}")
            await self.settle()
            builder.assert_not_awaited()
            self.assertGreater(self.source.candidates[str(mints[-1])].observed_at,
                               self.source.candidates[str(mints[0])].observed_at)
            self.source.history.evidence.return_value = {"migration_fraction": .01}
            pending.set_result(None)
            await self.settle()
            self.assertEqual([call.args[0] for call in builder.await_args_list], [str(mints[0]), str(mints[1])])
            self.assertEqual(self.source.building, {str(mints[0]), str(mints[1])})
            release.set()
            await self.settle()
            self.assertEqual(self.callback.await_count, 2)
            self.assertFalse(self.source.history_refreshes)
            await self.settle()
            self.assertEqual(builder.await_count, 2)

    async def test_done_unknown_history_future_does_not_create_callback_loop(self):
        self.source.history.evidence.return_value = None
        pending = asyncio.get_running_loop().create_future()
        self.source.history.pending = {str(self.creator): pending}
        await self.created(stream.trade(self.mint, self.a, sol=1), stream.trade(self.mint, self.b, sol=1.5))
        pending.set_result(None)
        await self.settle()
        warm_calls = self.source.history.warm.call_count
        await self.settle()
        self.assertEqual(self.source.history.warm.call_count, warm_calls)
        self.assertFalse(self.source.history_refreshes)
        self.assertFalse(self.source.building)
        self.callback.assert_not_awaited()

    async def test_activity_optional_profile_keeps_immediate_creation_preparation(self):
        self.policy["require_first_block_activity"] = False
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   return_value=self.token) as builder:
            await self.created()
            builder.assert_awaited_once()
            self.callback.assert_awaited_once()

    async def test_classic_profile_still_prebuilds_and_warms_before_activity(self):
        self.policy["entry_rule"] = "second_block"
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   return_value=self.token) as builder:
            await self.created()
            builder.assert_awaited_once()
            self.source.history.warm.assert_called_once_with(str(self.creator))
            self.callback.assert_not_awaited()

    async def test_signal_reports_creation_reception_preparation_and_stable_first_ready_times(self):
        raw = stream.create(self.mint, self.creator)
        parser = self.impl.event_parser._idl_parser
        decoded = parser.decode_event_data(raw, "CreateEvent")
        decoded["fields"]["timestamp"] = 1800000097

        async def build(*_args: object, **_kwargs: object) -> SimpleNamespace:
            self.now += .2
            return self.token

        with (patch.object(parser, "decode_event_data", return_value=decoded),
              patch("monitoring.second_block_source.build_pumpfun_token_info", side_effect=build)):
            await self.created(stream.trade(self.mint, self.a, sol=1), stream.trade(self.mint, self.b, sol=1.5))
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["creation_timestamp"], 1800000097)
        self.assertEqual(signal["source_seen_at"], 1800000100)
        self.assertAlmostEqual(signal["token_prepared_at"], 1800000100.2)
        self.assertEqual(signal["signal_ready_at"], signal["token_prepared_at"])
        self.assertAlmostEqual(signal["signal_age_seconds"], 3.2)
        self.now += 1
        later = self.source.signal(str(self.mint))
        self.assertEqual(later["signal_ready_at"], signal["signal_ready_at"])
        self.assertAlmostEqual(later["signal_age_seconds"], 4.2)


if __name__ == "__main__":
    unittest.main()
