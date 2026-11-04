"""Offline creation-block activity gates for developer-history admission."""

# ruff: noqa: ANN201

import asyncio
import unittest
from unittest.mock import Mock

import test_dev_history_only_source as history
import test_second_block_source as stream


class FirstBlockActivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await history.HistoryOnlySourceTests.asyncSetUp(self)
        self.policy["require_first_block_activity"] = True
        self.policy["min_distinct_first_block_buyers"] = 2
        self.policy["min_first_block_volume_usd"] = 250

    settle = history.HistoryOnlySourceTests.settle
    created = history.HistoryOnlySourceTests.created

    async def test_creation_without_activity_is_blocked(self):
        await self.created()
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.assertFalse(self.source.candidates[str(self.mint)].attempted_slots)

    async def test_one_buyer_with_high_volume_is_blocked(self):
        await self.created(stream.trade(self.mint, self.a, sol=10))
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.buyers, {str(self.a)})
        self.assertEqual(candidate.buy_sol, 10)

    async def test_repeated_wallet_is_one_distinct_buyer(self):
        await self.created(stream.trade(self.mint, self.a, sol=2),
                           stream.trade(self.mint, self.a, sol=2))
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.buyers, {str(self.a)})
        self.assertEqual(candidate.buy_sol, 4)

    async def test_seller_does_not_count_as_second_buyer(self):
        await self.created(stream.trade(self.mint, self.a, sol=1),
                           stream.trade(self.mint, self.b, sol=2, buy=False))
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.buyers, {str(self.a)})
        self.assertEqual(candidate.buy_sol+candidate.sell_sol, 3)

    async def test_two_buyers_and_249_99_dollars_are_blocked(self):
        await self.created(stream.trade(self.mint, self.a, sol=1),
                           stream.trade(self.mint, self.b, sol=1.4999))
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(len(candidate.buyers), 2)
        self.assertAlmostEqual((candidate.buy_sol+candidate.sell_sol)*100, 249.99)

    async def test_two_buyers_and_exactly_250_dollars_qualify_in_creation_block(self):
        await self.created(stream.trade(self.mint, self.a, sol=1),
                           stream.trade(self.mint, self.b, sol=1.5))
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["creation_slot"], 100)
        self.assertEqual(signal["buyers"], 2)
        self.assertEqual(signal["volume_usd"], 250)
        self.assertIsNone(signal["target_slot"])
        self.assertIsNone(signal["entry_block"])

    async def test_total_volume_counts_creation_block_buys_and_sells(self):
        await self.created(stream.trade(self.mint, self.a, sol=.5),
                           stream.trade(self.mint, self.b, sol=.5),
                           stream.trade(self.mint, self.a, sol=1.5, buy=False))
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["buyers"], 2)
        self.assertEqual(signal["buy_sol"], 1)
        self.assertEqual(signal["sell_sol"], 1.5)
        self.assertEqual(signal["volume_usd"], 250)

    async def test_later_block_buyer_cannot_complete_creation_block_activity(self):
        await self.created(stream.trade(self.mint, self.a, sol=3))
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.b, sol=10)),
                            101, "later-second-buyer")
        await self.settle()
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.buyers, {str(self.a)})
        self.assertEqual(candidate.buy_sol, 3)

    async def test_later_block_volume_cannot_complete_creation_block_activity(self):
        await self.created(stream.trade(self.mint, self.a, sol=.5),
                           stream.trade(self.mint, self.b, sol=.5))
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.a, sol=2),
                                        stream.trade(self.mint, self.b, sol=2, buy=False)),
                            101, "later-volume")
        await self.settle()
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(len(candidate.buyers), 2)
        self.assertEqual(candidate.buy_sol, 1)
        self.assertEqual(candidate.sell_sol, 0)

    async def test_late_creation_block_event_can_complete_activity_after_block_three(self):
        await self.created(stream.trade(self.mint, self.a, sol=.5),
                           stream.trade(self.mint, self.a, sol=1.5, buy=False))
        for slot in range(101, 106):
            self.now += 1
            self.source.on_slot({"slot": slot, "parent": slot-1})
        self.callback.assert_not_awaited()
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.b, sol=.5)),
                            100, "late-creation-block-buyer")
        await self.settle()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["buyers"], 2)
        self.assertEqual(signal["volume_usd"], 250)
        self.assertEqual(signal["creation_slot"], 100)
        self.assertIsNone(signal["target_slot"])

    async def test_unknown_or_below_one_percent_history_still_blocks(self):
        self.source.history.evidence = Mock(return_value=None)
        await self.created(stream.trade(self.mint, self.a, sol=1),
                           stream.trade(self.mint, self.b, sol=1.5))
        candidate = self.source.candidates[str(self.mint)]
        for evidence in [None, {"migration_fraction": 0}, {"migration_fraction": .009999}]:
            with self.subTest(evidence=evidence):
                self.source.history.evidence.return_value = evidence
                self.assertIsNone(self.source.signal(str(self.mint)))
                self.source.consider(candidate)
                await self.settle()
                self.callback.assert_not_awaited()
        self.assertFalse(candidate.attempted_slots)

    async def test_history_can_finish_after_block_three_when_first_block_qualifies(self):
        self.source.history.evidence = Mock(return_value=None)
        pending = asyncio.get_running_loop().create_future()
        self.source.history.pending = {str(self.creator): pending}
        await self.created(stream.trade(self.mint, self.a, sol=1),
                           stream.trade(self.mint, self.b, sol=1.5))
        for slot in range(101, 109):
            self.now += 1
            self.source.on_slot({"slot": slot, "parent": slot-1})
        self.callback.assert_not_awaited()
        self.source.history.evidence.return_value = {"migration_fraction": .01}
        pending.set_result(None)
        await self.settle()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["creation_slot"], 100)
        self.assertEqual(signal["volume_usd"], 250)
        self.assertIsNone(signal["target_slot"])

    async def test_market_cap_and_stale_price_do_not_add_selection_rules(self):
        self.source.history.evidence = Mock(return_value=None)
        await self.created(stream.trade(self.mint, self.a, sol=1, mc=9000000),
                           stream.trade(self.mint, self.b, sol=1.5, mc=9000000))
        candidate = self.source.candidates[str(self.mint)]
        candidate.price_at = 0
        candidate.invalid = True
        self.source.history.evidence.return_value = {"migration_fraction": .01}
        self.now += 10
        self.source.on_slot({"slot": 120, "parent": 119})
        await self.settle()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertAlmostEqual(signal["observed_mc_usd"], 9000000)
        self.assertEqual(signal["volume_usd"], 250)

    async def test_duplicate_first_block_logs_do_not_supply_missing_volume(self):
        await self.created(stream.trade(self.mint, self.a, sol=.5),
                           stream.trade(self.mint, self.b, sol=.5))
        event = stream.logs(stream.trade(self.mint, self.a, sol=1, buy=False))
        self.source.on_logs(event, 100, "same-first-block-trade")
        self.source.on_logs(event, 100, "same-first-block-trade")
        await self.settle()
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.buy_sol+candidate.sell_sol, 2)

    async def test_qualified_candidate_has_one_attempt_across_logs_slots_and_refreshes(self):
        await self.created(stream.trade(self.mint, self.a, sol=1),
                           stream.trade(self.mint, self.b, sol=1.5))
        candidate = self.source.candidates[str(self.mint)]
        for slot in range(101, 110):
            self.source.on_slot({"slot": slot, "parent": slot-1})
            self.source.on_logs(stream.logs(stream.trade(self.mint, self.a, sol=5)),
                                slot, f"later-trade-{slot}")
            self.source.on_logs(stream.logs(stream.create(self.mint, self.creator)),
                                slot, f"duplicate-create-{slot}")
            self.source.consider(candidate)
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertEqual(candidate.attempted_slots, {100})
        self.assertEqual(candidate.buy_sol, 2.5)
        self.assertFalse(self.source.arm_third_block_retry(str(self.mint), 100,
                                                          self.source.generation))
