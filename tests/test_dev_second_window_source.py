"""Strict developer entry uses a synthetic second produced block, never live I/O."""

# ruff: noqa: ANN001, ANN002, ANN003, ANN201, ANN202, ARG001, FBT003

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_second_block_policy as historical
import test_second_block_source as stream


class DevSecondWindowSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await stream.SourceTests.asyncSetUp(self)
        self.policy.update(entry_rule="creator_migration_only", entry_timing="second_produced_block",
                           require_dev_initial_buy=True, require_first_block_activity=True,
                           min_first_block_volume_usd=250, min_distinct_first_block_buyers=2,
                           third_block_retry_on_sharp_rise=False)
        self.now = 100.0
        clock = SimpleNamespace(monotonic=lambda: self.now, time=lambda: 1800000000+self.now)
        clock_patch = patch("monitoring.second_block_source.time", clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.token.mint = self.mint
        self.paused = False
        self.source.allowed = lambda: not self.paused
        self.source.history.warm = Mock()
        self.source.history.observe = Mock()

    async def settle(self):
        for _ in range(10):
            await asyncio.sleep(0)

    def qualifying_trades(self, *, user=None, mc=4000):
        return (stream.trade(self.mint, user or self.creator, sol=1, mc=mc),
                stream.trade(self.mint, self.b, sol=1.5, mc=mc))

    async def created(self, *trades, user=None):
        self.source.on_slot({"slot": 100, "parent": 99})
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator, user=user), *trades),
                            100, "creation-signature")
        await self.settle()

    async def test_prepares_in_creation_block_and_delivers_only_in_second_with_public_proof(self):
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   return_value=self.token) as builder:
            await self.created(*self.qualifying_trades(user=self.a, mc=9000000), user=self.a)
            builder.assert_awaited_once()
            self.callback.assert_not_awaited()
            candidate = self.source.candidates[str(self.mint)]
            self.assertIs(candidate.token, self.token)
            self.assertTrue(candidate.dev_initial_buy_verified)
            self.assertIsNone(self.source.signal(str(self.mint)))
            self.source.on_slot({"slot": 101, "parent": 100})
            await self.settle()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["entry_rule"], "creator_migration_only")
        self.assertEqual(signal["entry_timing"], "second_produced_block")
        self.assertEqual(signal["target_slot"], 101)
        self.assertEqual(signal["entry_block"], 2)
        self.assertEqual(signal["developer"], str(self.a))
        self.assertTrue(signal["dev_initial_buy_required"])
        self.assertTrue(signal["dev_initial_buy_verified"])
        self.assertEqual(signal["dev_initial_buy_signature"], "creation-signature")
        self.assertEqual(signal["buyers"], 2)
        self.assertEqual(signal["volume_usd"], 250)
        self.assertAlmostEqual(signal["observed_mc_usd"], 9000000)
        self.assertLessEqual(signal["token_prepared_at"], signal["signal_ready_at"])
        delivered = self.callback.await_args.args[0]
        self.assertIsNot(delivered, self.token)
        self.assertEqual(delivered.additional_data["dev_entry_target_slot"], 101)
        self.assertEqual(delivered.additional_data["dev_entry_generation"], self.source.generation)
        self.assertFalse(signal["inclusion_guaranteed"])

    async def test_actual_parent_selects_second_despite_skipped_numeric_slots_then_expires(self):
        await self.created(*self.qualifying_trades())
        self.source.on_slot({"slot": 105, "parent": 100})
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertEqual(self.source.signal(str(self.mint))["target_slot"], 105)
        self.source.on_slot({"slot": 109, "parent": 105})
        await self.settle()
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.assertFalse(self.source.arm_third_block_retry(str(self.mint), 105, self.source.generation))
        self.callback.assert_awaited_once()

    async def test_second_target_is_immutable_when_another_child_of_creation_arrives(self):
        await self.created(*self.qualifying_trades())
        self.source.on_slot({"slot": 105, "parent": 100})
        self.source.on_slot({"slot": 106, "parent": 100})
        await self.settle()
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.callback.assert_not_awaited()

    async def test_branch_which_does_not_extend_creation_cannot_enter(self):
        await self.created(*self.qualifying_trades())
        self.source.on_slot({"slot": 105, "parent": 99})
        await self.settle()
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.callback.assert_not_awaited()

    async def test_expired_second_slot_cannot_be_renewed_by_duplicate_or_stale_slot(self):
        await self.created(*self.qualifying_trades())
        self.paused = True
        self.source.on_slot({"slot": 101, "parent": 100})
        self.now += self.policy["max_slot_age_seconds"]+.001
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.on_slot({"slot": 100, "parent": 99})
        self.paused = False
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.source.consider(self.source.candidates[str(self.mint)])
        await self.settle()
        self.callback.assert_not_awaited()

    async def test_newer_log_proves_second_window_over_before_next_slot_notification(self):
        await self.created(*self.qualifying_trades())
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.a)), 102, "newer-block")
        self.assertFalse(self.source.available)
        self.assertIsNone(self.source.signal(str(self.mint)))
        await self.settle()
        self.callback.assert_not_awaited()
        self.source.on_slot({"slot": 102, "parent": 101})
        self.assertNotIn(str(self.mint), self.source.candidates)

    async def test_first_other_wallet_trade_cannot_be_replaced_by_later_dev_buy(self):
        await self.created(stream.trade(self.mint, self.a, sol=1),
                           stream.trade(self.mint, self.creator, sol=1.5))
        candidate = self.source.candidates[str(self.mint)]
        self.assertIs(candidate.dev_initial_buy_verified, False)
        self.source.history.warm.assert_not_called()
        self.assertIsNone(candidate.token)
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.creator)), 100, "later-dev")
        await self.settle()
        self.assertIs(candidate.dev_initial_buy_verified, False)
        self.callback.assert_not_awaited()

    async def test_creator_fee_recipient_does_not_substitute_for_creation_signer(self):
        await self.created(stream.trade(self.mint, self.creator, sol=1),
                           stream.trade(self.mint, self.a, sol=1.5), user=self.a)
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.developer, str(self.a))
        self.assertIs(candidate.dev_initial_buy_verified, False)
        self.source.history.observe.assert_called_once_with(str(self.a), str(self.mint))
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_not_awaited()

    async def test_first_sell_is_not_a_dev_initial_buy_even_if_dev_buys_later(self):
        await self.created(stream.trade(self.mint, self.creator, buy=False), *self.qualifying_trades())
        self.assertIs(self.source.candidates[str(self.mint)].dev_initial_buy_verified, False)
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_not_awaited()

    async def test_valid_first_dev_buy_proof_is_not_changed_by_subsequent_sell(self):
        await self.created(*self.qualifying_trades(), stream.trade(self.mint, self.a, buy=False))
        candidate = self.source.candidates[str(self.mint)]
        self.assertIs(candidate.dev_initial_buy_verified, True)
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_awaited_once()

    async def test_first_malformed_trade_cannot_be_skipped_to_prove_a_later_dev_buy(self):
        await self.created(stream.trade(self.mint, self.a)[:80], *self.qualifying_trades())
        self.assertIs(self.source.candidates[str(self.mint)].dev_initial_buy_verified, False)
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_not_awaited()

    async def test_unidentifiable_earlier_trade_fails_closed(self):
        await self.created(stream.TRADE, *self.qualifying_trades())
        self.assertIs(self.source.candidates[str(self.mint)].dev_initial_buy_verified, False)
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_not_awaited()

    async def test_dev_buy_in_separate_transaction_cannot_prove_first_transaction_order(self):
        await self.created()
        self.source.on_logs(stream.logs(*self.qualifying_trades()), 100, "separate-transaction")
        self.assertIsNone(self.source.candidates[str(self.mint)].dev_initial_buy_verified)
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_not_awaited()

    async def test_unsigned_direct_callbacks_cannot_prove_dev_initial_buy(self):
        self.source.on_slot({"slot": 100, "parent": 99})
        self.source.creation(stream.create(self.mint, self.creator), 100)
        for raw in self.qualifying_trades():
            self.source.trade(raw, 100)
        self.source.refresh_candidates()
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.assertIsNone(self.source.candidates[str(self.mint)].dev_initial_buy_verified)
        self.callback.assert_not_awaited()

    async def test_failed_or_truncated_creation_logs_do_not_establish_proof(self):
        valid = stream.logs(stream.create(self.mint, self.creator), *self.qualifying_trades())
        failed = [*valid[:-1], f"Program {stream.PUMP} failed: custom program error: 0x1"]
        truncated = [*valid[:-1], "Log truncated"]
        self.source.on_slot({"slot": 100, "parent": 99})
        self.source.on_logs(failed, 100, "failed")
        self.source.on_logs(truncated, 100, "truncated")
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.assertFalse(self.source.candidates)
        self.source.history.observe.assert_not_called()
        self.callback.assert_not_awaited()

    async def test_unknown_history_which_arrives_after_second_cannot_prepare_or_deliver(self):
        self.source.history.evidence = Mock(return_value=None)
        pending = asyncio.get_running_loop().create_future()
        self.source.history.pending = {str(self.creator): pending}
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   return_value=self.token) as builder:
            await self.created(*self.qualifying_trades())
            self.source.on_slot({"slot": 101, "parent": 100})
            self.source.on_slot({"slot": 102, "parent": 101})
            self.source.history.evidence.return_value = {"migration_fraction": .01}
            pending.set_result(None)
            await self.settle()
            builder.assert_not_awaited()
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.callback.assert_not_awaited()

    async def test_history_ready_inside_second_can_only_deliver_before_deadline(self):
        self.source.history.evidence = Mock(return_value=None)
        pending = asyncio.get_running_loop().create_future()
        self.source.history.pending = {str(self.creator): pending}
        await self.created(*self.qualifying_trades())
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.history.evidence.return_value = {"migration_fraction": .01}
        pending.set_result(None)
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertEqual(self.source.signal(str(self.mint))["target_slot"], 101)

    async def test_cold_history_completion_after_second_deadline_does_not_start_build(self):
        self.source.history.evidence = Mock(return_value=None)
        pending = asyncio.get_running_loop().create_future()
        self.source.history.pending = {str(self.creator): pending}
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock,
                   return_value=self.token) as builder:
            await self.created(*self.qualifying_trades())
            self.source.on_slot({"slot": 101, "parent": 100})
            self.now += self.policy["max_slot_age_seconds"]+.001
            self.source.history.evidence.return_value = {"migration_fraction": .01}
            pending.set_result(None)
            await self.settle()
            builder.assert_not_awaited()
        self.callback.assert_not_awaited()

    async def test_unknown_or_below_one_percent_history_never_prepares(self):
        self.source.history.evidence = Mock(return_value=None)
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock) as builder:
            await self.created(*self.qualifying_trades())
            for evidence in (None, {"migration_fraction": 0}, {"migration_fraction": .009999}):
                self.source.history.evidence.return_value = evidence
                self.source.refresh_candidates()
                await self.settle()
            self.source.on_slot({"slot": 101, "parent": 100})
            await self.settle()
            builder.assert_not_awaited()
        self.callback.assert_not_awaited()

    async def test_build_which_finishes_in_third_does_not_deliver(self):
        release = asyncio.Event()

        async def build(*args, **kwargs):
            await release.wait()
            return self.token

        with patch("monitoring.second_block_source.build_pumpfun_token_info", side_effect=build):
            await self.created(*self.qualifying_trades())
            self.assertIn(str(self.mint), self.source.building)
            self.source.on_slot({"slot": 101, "parent": 100})
            self.source.on_slot({"slot": 102, "parent": 101})
            release.set()
            await self.settle()
        self.assertFalse(self.source.building)
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.callback.assert_not_awaited()

    async def test_disconnect_invalidates_an_old_build_without_cancelling_it(self):
        release = asyncio.Event()

        async def build(*args, **kwargs):
            await release.wait()
            return self.token

        with patch("monitoring.second_block_source.build_pumpfun_token_info", side_effect=build):
            await self.created(*self.qualifying_trades())
            generation = self.source.generation
            self.source.disconnect()
            self.source.on_slot({"slot": 101, "parent": 100})
            release.set()
            await self.settle()
        self.assertGreater(self.source.generation, generation)
        self.assertFalse(self.source.candidates)
        self.callback.assert_not_awaited()

    async def test_creation_logs_already_in_third_are_observed_for_history_but_not_prepared(self):
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock) as builder:
            self.source.on_slot({"slot": 102, "parent": 101})
            self.source.on_logs(stream.logs(stream.create(self.mint, self.creator), *self.qualifying_trades()),
                                100, "too-late")
            await self.settle()
            builder.assert_not_awaited()
        self.source.history.observe.assert_called_once_with(str(self.creator), str(self.mint))
        self.assertFalse(self.source.candidates)
        self.callback.assert_not_awaited()

    async def test_delayed_creation_is_eligible_while_actual_second_window_still_open(self):
        self.source.on_slot({"slot": 105, "parent": 100})
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator), *self.qualifying_trades()),
                            100, "late-inside-second")
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertEqual(self.source.signal(str(self.mint))["target_slot"], 105)

    async def test_dev_only_activity_setting_keeps_dev_proof_but_not_two_buyers_or_volume(self):
        self.policy["require_first_block_activity"] = False
        await self.created(stream.trade(self.mint, self.creator, sol=.001))
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertFalse(signal["first_block_activity_required"])
        self.assertTrue(signal["dev_initial_buy_verified"])
        self.assertEqual(signal["buyers"], 1)
        self.assertAlmostEqual(signal["volume_usd"], .1)

    async def test_shipped_profile_accepts_one_tiny_dev_buy_with_above_one_percent_history(self):
        # Exercise the actual selector, independently of the historical activity fixture.
        self.policy.clear()
        self.policy.update(historical.POLICY)
        self.assertFalse(self.policy["require_first_block_activity"])
        self.assertTrue(self.policy["creator_migration_strictly_above"])
        self.source.history.evidence = Mock(return_value={"migration_fraction": .010001})
        await self.created(stream.trade(self.mint, self.creator, sol=.000001))
        self.callback.assert_not_awaited()
        self.source.on_slot({"slot": 105, "parent": 100})
        await self.settle()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["target_slot"], 105)
        self.assertEqual(signal["entry_block"], 2)
        self.assertIs(signal["dev_initial_buy_verified"], True)
        self.assertFalse(signal["first_block_activity_required"])
        self.assertEqual(signal["buyers"], 1)
        self.assertAlmostEqual(signal["volume_usd"], .0001)

    async def test_shipped_profile_rejects_unknown_or_at_most_one_percent_history(self):
        self.policy.clear()
        self.policy.update(historical.POLICY)
        self.assertFalse(self.policy["require_first_block_activity"])
        self.source.history.evidence = Mock(return_value=None)
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock) as builder:
            await self.created(stream.trade(self.mint, self.creator, sol=.000001))
            self.source.on_slot({"slot": 105, "parent": 100})
            for history in (None, {"migration_fraction": 0}, {"migration_fraction": .009999},
                            {"migration_fraction": .01}):
                with self.subTest(history=history):
                    self.source.history.evidence.return_value = history
                    self.source.refresh_candidates()
                    await self.settle()
                    self.assertIsNone(self.source.signal(str(self.mint)))
            builder.assert_not_awaited()
        self.callback.assert_not_awaited()

    async def test_shipped_profile_keeps_initial_dev_buy_proof_without_activity_minimums(self):
        self.policy.clear()
        self.policy.update(historical.POLICY)
        self.source.history.evidence = Mock(return_value={"migration_fraction": .010001})
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock) as builder:
            await self.created(stream.trade(self.mint, self.a, sol=.000001))
            self.source.on_slot({"slot": 105, "parent": 100})
            await self.settle()
            builder.assert_not_awaited()
        self.assertIs(self.source.candidates[str(self.mint)].dev_initial_buy_verified, False)
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.callback.assert_not_awaited()

    async def test_second_block_trade_cannot_supply_missing_first_block_buyers_or_volume(self):
        await self.created(stream.trade(self.mint, self.creator, sol=1))
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.a, sol=5)), 101, "second-block-buy")
        await self.settle()
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.buyers, {str(self.creator)})
        self.assertEqual(candidate.buy_sol, 1)
        self.assertIsNone(candidate.token)
        self.callback.assert_not_awaited()

    async def test_distinct_wallets_and_minimum_volume_still_apply_when_selected(self):
        await self.created(stream.trade(self.mint, self.creator, sol=1),
                           stream.trade(self.mint, self.creator, sol=1.5))
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.callback.assert_not_awaited()

    async def test_two_wallets_below_selected_minimum_volume_still_block(self):
        await self.created(stream.trade(self.mint, self.creator, sol=1),
                           stream.trade(self.mint, self.a, sol=1.499))
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.callback.assert_not_awaited()

    async def test_duplicate_logs_and_slots_never_repeat_a_second_block_attempt(self):
        await self.created(*self.qualifying_trades())
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_awaited_once()
        candidate = self.source.candidates[str(self.mint)]
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator), *self.qualifying_trades()),
                            100, "creation-signature")
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.refresh_candidates()
        await self.settle()
        self.assertEqual(candidate.attempted_slots, {101})
        self.assertIn(str(self.mint), self.source.attempted_mints)
        self.callback.assert_awaited_once()

    async def test_expiring_source_does_not_cancel_a_completion_that_may_have_broadcast(self):
        completion = asyncio.get_running_loop().create_future()
        self.callback.return_value = completion
        await self.created(*self.qualifying_trades())
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_awaited_once()
        self.source.on_slot({"slot": 102, "parent": 101})
        self.assertFalse(completion.cancelled())
        completion.set_result(None)
        await self.settle()
        self.callback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
