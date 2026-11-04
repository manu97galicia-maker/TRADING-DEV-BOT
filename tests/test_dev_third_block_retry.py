"""Offline regressions for the single, unsubmitted DEV third-block retry."""

# Synthetic stream events and fake brokers never sign or send transactions.
# ruff: noqa: ANN001, ANN003, ANN201, ANN202, ARG001, ARG005, FBT003, PLR2004, SLF001

import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_four_strategy_bundle as bundle
import test_second_block_policy as policy_fixture
import test_second_block_source as stream

from trading.base import TradeResult
from trading.four_strategy_entry_guard import EntryRiskRejected
from trading.four_strategy_runtime import FourStrategyTrader, SharedBuyer
from trading.second_block_policy import STRATEGY_ID
from trading.transaction_observer import (
    BUY_PRICE_CEILING,
    PRIORITY_CAP,
    check_entry_window,
    observe,
)


class RetrySourceTests(unittest.IsolatedAsyncioTestCase):
    created = stream.SourceTests.created

    async def asyncSetUp(self):
        await stream.SourceTests.asyncSetUp(self)
        self.policy["third_block_retry_on_sharp_rise"] = True
        self.token.mint = self.mint
        self.now = 100.0
        clock = SimpleNamespace(monotonic=lambda: self.now, time=lambda: 1800000000 + self.now)
        clock_patch = patch("monitoring.second_block_source.time", clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.attempts = []

    async def settle(self):
        for _ in range(8):
            await asyncio.sleep(0)

    async def drain(self):
        while self.source.tasks:
            await asyncio.gather(*list(self.source.tasks))

    async def second(self):
        await self.created(stream.trade(self.mint, self.a), stream.trade(self.mint, self.b))
        self.source.on_slot({"slot": 102, "parent": 100})
        await self.settle()

    def third(self, *, mc=3900):
        # Actual parentage, rather than slot + 1, defines produced block three.
        self.source.on_slot({"slot": 108, "parent": 102})
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.a, mc=mc)), 108, "third-price")

    async def record_and_arm(self, token):
        signal = self.source.signal(str(self.mint))
        self.attempts.append((token, signal))
        if signal["entry_block"] == 2:
            self.assertTrue(self.source.arm_third_block_retry(
                str(self.mint), signal["target_slot"], signal["source_generation"]))

    async def test_only_one_attempt_per_produced_block_and_never_a_fourth(self):
        self.source.callback = self.record_and_arm
        await self.second()
        await self.drain()
        self.assertEqual([signal["entry_block"] for _, signal in self.attempts], [2])
        self.source.on_slot({"slot": 102, "parent": 100})
        await self.settle()
        self.assertEqual(len(self.attempts), 1)
        self.third()
        await self.drain()
        self.assertEqual([signal["entry_block"] for _, signal in self.attempts], [2, 3])
        self.assertEqual([signal["target_slot"] for _, signal in self.attempts], [102, 108])
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.attempted_slots, {102, 108})
        self.source.on_slot({"slot": 108, "parent": 102})
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.b)), 108, "more-third-trades")
        await self.settle()
        self.assertEqual(len(self.attempts), 2)
        self.source.on_slot({"slot": 111, "parent": 108})
        await self.settle()
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.assertEqual(len(self.attempts), 2)

    async def test_no_armed_sharp_rejection_means_no_third_attempt(self):
        await self.second()
        await self.drain()
        self.third()
        await self.drain()
        self.callback.assert_awaited_once()
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_returned_handler_task_keeps_third_block_pending_until_cleanup_finishes(self):
        cleanup = asyncio.Event()
        arm_results = []

        async def handler(signal):
            await cleanup.wait()
            arm_results.append(self.source.arm_third_block_retry(
                str(self.mint), signal["target_slot"], signal["source_generation"]))

        async def callback(token):
            signal = self.source.signal(str(self.mint))
            self.attempts.append((token, signal))
            if signal["entry_block"] == 2:
                return asyncio.create_task(handler(signal))
            return None

        self.source.callback = callback
        await self.second()
        self.third()
        await self.settle()
        self.assertEqual(len(self.attempts), 1)
        self.assertEqual(self.source.candidates[str(self.mint)].inflight_slot, 102)
        cleanup.set()
        await self.drain()
        self.assertEqual(arm_results, [True])
        self.assertEqual([signal["entry_block"] for _, signal in self.attempts], [2, 3])

    async def test_already_armed_retry_waits_for_second_handler_cleanup(self):
        cleanup = asyncio.get_running_loop().create_future()

        async def callback(token):
            await self.record_and_arm(token)
            return cleanup if len(self.attempts) == 1 else None

        self.source.callback = callback
        await self.second()
        self.third()
        await self.settle()
        self.assertEqual(self.source.signal(str(self.mint))["entry_block"], 3)
        self.assertEqual(len(self.attempts), 1)
        cleanup.set_result(None)
        await self.drain()
        self.assertEqual([signal["entry_block"] for _, signal in self.attempts], [2, 3])

    async def test_attempt_tokens_and_metadata_are_separate_and_bound_to_their_window(self):
        runtime = SimpleNamespace(second_source=self.source)

        async def callback(token):
            signal = FourStrategyTrader.current_signal(runtime, token)
            self.attempts.append((token, signal))
            self.assertNotIn("four_strategy_nonce", token.additional_data)
            token.additional_data["four_strategy_nonce"] = f"attempt-{signal['entry_block']}"
            token.additional_data["learning_entry"] = copy.deepcopy(signal)
            if signal["entry_block"] == 2:
                self.assertTrue(self.source.arm_third_block_retry(
                    str(self.mint), signal["target_slot"], signal["source_generation"]))

        self.source.callback = callback
        await self.second()
        await self.drain()
        first = self.attempts[0][0]
        self.third()
        await self.drain()
        third = self.attempts[1][0]
        self.assertIsNot(first, third)
        self.assertIsNot(first, self.token)
        self.assertIsNot(first.additional_data, third.additional_data)
        self.assertNotIn("four_strategy_nonce", self.token.additional_data)
        self.assertEqual(first.additional_data["learning_entry"]["target_slot"], 102)
        self.assertEqual(third.additional_data["learning_entry"]["target_slot"], 108)
        self.assertEqual(first.additional_data["dev_entry_target_slot"], 102)
        self.assertEqual(third.additional_data["dev_entry_target_slot"], 108)
        self.assertIsNone(FourStrategyTrader.current_signal(runtime, first))
        self.assertEqual(FourStrategyTrader.current_signal(runtime, third)["entry_block"], 3)

    async def test_later_block_trades_refresh_price_without_changing_first_block_counts(self):
        self.source.callback = self.record_and_arm
        await self.second()
        await self.drain()
        frozen = self.attempts[0][1]
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.creator, sol=100, mc=4100)), 102, "second-buy")
        self.third(mc=3900)
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.creator, sol=200, buy=False, mc=3800)), 108, "third-sell")
        await self.drain()
        latest = self.attempts[1][1]
        self.assertEqual(latest["buyers"], frozen["buyers"])
        self.assertAlmostEqual(latest["buy_sol"], frozen["buy_sol"])
        self.assertAlmostEqual(latest["sell_sol"], frozen["sell_sol"])
        self.assertAlmostEqual(latest["volume_usd"], frozen["volume_usd"])
        self.assertAlmostEqual(latest["observed_mc_usd"], 3800)

    async def test_expired_price_cannot_be_reused_in_a_fresh_third_slot(self):
        self.source.callback = self.record_and_arm
        await self.second()
        await self.drain()
        self.now += 1.01
        self.source.on_slot({"slot": 108, "parent": 102})
        await self.drain()
        self.assertEqual(len(self.attempts), 1)
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_missing_history_blocks_third_attempt(self):
        self.source.callback = self.record_and_arm
        await self.second()
        await self.drain()
        self.source.history.evidence = lambda *args: None
        self.third()
        await self.drain()
        self.assertEqual(len(self.attempts), 1)
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_exact_4200_blocks_third_attempt(self):
        self.source.callback = self.record_and_arm
        await self.second()
        await self.drain()
        self.third(mc=4200)
        await self.drain()
        self.assertEqual(len(self.attempts), 1)
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_stale_third_slot_and_duplicate_notification_do_not_renew_deadline(self):
        self.source.callback = self.record_and_arm
        await self.second()
        await self.drain()
        self.third()
        self.now += self.policy["max_slot_age_seconds"] + 0.01
        self.source.on_slot({"slot": 108, "parent": 102})
        await self.drain()
        self.assertEqual(len(self.attempts), 1)
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_unknown_parent_continuity_and_disconnect_prevent_retry(self):
        self.source.callback = self.record_and_arm
        await self.second()
        await self.drain()
        self.source.on_slot({"slot": 108, "parent": 107})
        await self.drain()
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.assertEqual(len(self.attempts), 1)
        self.source.disconnect()
        self.assertFalse(self.source.arm_third_block_retry(str(self.mint), 102, 0))
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_old_generation_proof_cannot_arm_a_reconnected_same_mint(self):
        proofs = []

        async def callback(token):
            signal = self.source.signal(str(self.mint))
            proofs.append((token, signal))
            if len(proofs) == 2:
                self.assertFalse(self.source.arm_third_block_retry(
                    str(self.mint), 102, proofs[0][1]["source_generation"]))

        self.source.callback = callback
        await self.second()
        await self.drain()
        self.source.disconnect()
        await self.second()
        await self.drain()
        self.assertNotEqual(proofs[0][1]["source_generation"], proofs[1][1]["source_generation"])
        runtime = SimpleNamespace(second_source=self.source)
        self.assertIsNone(FourStrategyTrader.current_signal(runtime, proofs[0][0]))
        self.assertIsNotNone(FourStrategyTrader.current_signal(runtime, proofs[1][0]))
        self.third()
        await self.drain()
        self.assertEqual(len(proofs), 2)


class RetryAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await bundle.BuyerTests.asyncSetUp(self)
        self.runtime.second_policy = policy_fixture.enabled_policy()
        self.runtime.second_policy["third_block_retry_on_sharp_rise"] = True
        self.runtime.compute_units = {"buy": 200000}
        self.features = {"strategy": STRATEGY_ID, "sol_usd": 100, "creator": "dev",
                         "entry_block": 2, "target_slot": 102, "source_generation": 7}
        self.runtime.current_signal = lambda token: self.features
        self.runtime.check_entry_risk.side_effect = EntryRiskRejected("sharp_price_rise")
        self.arm = Mock(return_value=True)
        self.runtime.second_source = SimpleNamespace(arm_third_block_retry=self.arm)
        self.broker = SimpleNamespace(execute=AsyncMock())

    async def test_exact_sharp_second_block_rejection_arms_only_after_reservation_is_removed(self):
        def arm(mint, slot, generation):
            self.assertFalse(self.book.positions())
            self.assertEqual((mint, slot, generation), ("mint", 102, 7))
            return True

        self.arm.side_effect = arm
        result = await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.assertFalse(result.success)
        self.arm.assert_called_once_with("mint", 102, 7)
        self.broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())

    async def test_all_other_guard_labels_and_third_block_sharp_rejection_cannot_arm(self):
        for reason, block in [
            ("concentrated_creator", 2), ("missing_fresh_price_history", 2),
            ("price_ran_during_validation", 2), ("price_dump_from_recent_peak", 2),
            ("sharp_price_rise_extra", 2), ("sharp_price_rise", 3),
        ]:
            with self.subTest(reason=reason, block=block):
                self.features["entry_block"] = block
                self.runtime.check_entry_risk.side_effect = EntryRiskRejected(reason)
                self.assertFalse((await SharedBuyer(self.runtime, self.broker).execute(self.token)).success)
                self.assertFalse(self.book.positions())
        self.arm.assert_not_called()
        self.broker.execute.assert_not_awaited()

    async def test_other_entry_source_sharp_rejection_cannot_arm_dev_retry(self):
        self.features["strategy"] = "strong-pressure-8k"
        self.assertFalse((await SharedBuyer(self.runtime, self.broker).execute(self.token)).success)
        self.arm.assert_not_called()
        self.broker.execute.assert_not_awaited()

    async def test_failed_cancellation_is_not_proof_of_an_unsubmitted_attempt(self):
        with patch.object(self.book, "cancel_unsubmitted"):
            self.assertFalse((await SharedBuyer(self.runtime, self.broker).execute(self.token)).success)
        self.assertEqual(len(self.book.positions()), 1)
        self.arm.assert_not_called()
        self.broker.execute.assert_not_awaited()

    async def test_existing_unknown_signature_prevents_retry_and_keeps_its_position(self):
        async def risk(token, state):
            position = self.book.positions()[0]
            self.book.submitting(position["nonce"])
            self.book.receipt(position["nonce"], "unknown-signature", "buy")
            raise EntryRiskRejected("sharp_price_rise")

        self.runtime.check_entry_risk.side_effect = risk
        self.assertFalse((await SharedBuyer(self.runtime, self.broker).execute(self.token)).success)
        position = self.book.positions()[0]
        self.assertEqual(position["state"], "uncertain")
        self.assertEqual(self.book.signatures(position["nonce"]), [("unknown-signature", "buy")])
        self.arm.assert_not_called()
        self.broker.execute.assert_not_awaited()

    async def test_third_block_revalidates_mc_and_retains_shared_budget_exit_and_fee_caps(self):
        self.features.update(entry_block=3, target_slot=108)
        self.runtime.check_entry_risk.side_effect = None
        self.runtime.check_entry_risk.return_value = ({"price_per_token": 4.2e-8, "creator": "dev"}, {})
        self.assertFalse((await SharedBuyer(self.runtime, self.broker).execute(self.token)).success)
        self.assertFalse(self.book.positions())
        self.arm.assert_not_called()
        self.broker.execute.assert_not_awaited()

        async def buy(token, **kwargs):
            self.assertGreater(kwargs["amount_sol"], 0.70)
            self.assertLess(kwargs["amount_sol"], 0.90)
            self.assertEqual(PRIORITY_CAP.get(), 600000)
            self.assertEqual(BUY_PRICE_CEILING.get(), 4.2e-8)
            self.assertLessEqual((kwargs["priority_fee_override"] * 200000 + 999999) // 1000000, 600000)
            check_entry_window()
            details = json.loads(self.book.positions()[0]["details"])
            self.assertEqual(details["features"]["entry_block"], 3)
            self.assertEqual(details["target_slot"], 108)
            self.assertEqual(details["entry_exit_policy"], self.runtime.second_policy["exit"])
            self.assertEqual(details["policy_fraction"], 0.90)
            observe("signed", "fake-third-signature")
            return TradeResult(True, tx_signature="fake-third-signature", price=4e-8, amount=1000000)

        self.runtime.check_entry_risk.return_value = ({"price_per_token": 4e-8, "creator": "dev"}, {})
        self.broker.execute.side_effect = buy
        self.assertTrue((await SharedBuyer(self.runtime, self.broker).execute(self.token)).success)
        self.runtime.check_entry_risk.assert_awaited()
        self.broker.execute.assert_awaited_once()
        self.arm.assert_not_called()

    async def test_runtime_callback_returns_the_handler_task_for_source_cleanup_tracking(self):
        cleanup = asyncio.Event()

        async def handle(token):
            await cleanup.wait()

        runtime = SimpleNamespace(_four_tasks=set(), _handle_token=AsyncMock(side_effect=handle))
        task = await FourStrategyTrader._on_dexpaid_token(runtime, self.token)
        self.assertIsInstance(task, asyncio.Future)
        self.assertIn(task, runtime._four_tasks)
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        cleanup.set()
        await task
        await asyncio.sleep(0)
        self.assertFalse(runtime._four_tasks)
