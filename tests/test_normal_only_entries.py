"""Normal Pump entries only, with unsigned brokers and synthetic stream events."""

# The borrowed fixtures use temporary ledgers and duck-typed runtime hooks.
# ruff: noqa: ANN001, ANN201, ARG001, FBT003, SLF001

import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_dev_fee_sharing_runtime as sharing
import test_dev_history_fees as history
import test_dev_second_window_source as stream
import test_second_block_source as events
from solders.pubkey import Pubkey

from trading.base import TradeResult
from trading.four_strategy_runtime import SharedBuyer, _configure_normal_only_assets
from trading.pump_fee_identity import sharing_config_address
from trading.transaction_observer import (
    ENTRY_WINDOW,
    EntryWindowExpiredError,
    check_entry_window,
)


class NormalOnlyEntryTests(unittest.IsolatedAsyncioTestCase):
    rpc_response = sharing.DevFeeSharingRuntimeTests.rpc_response
    assert_contexts_reset = sharing.DevFeeSharingRuntimeTests.assert_contexts_reset

    async def asyncSetUp(self):
        await sharing.DevFeeSharingRuntimeTests.asyncSetUp(self)
        self.runtime.second_policy["max_entry_budget_usd"] = 30.0
        self.token.is_mayhem_mode = False
        self.token.is_cashback_coin = False
        self.state.update(is_mayhem_mode=False, is_cashback_coin=False)
        self.broker = SimpleNamespace(execute=AsyncMock(return_value=TradeResult(False)))

    async def test_prepared_mayhem_cannot_reach_rpc_reservation_or_any_entry_broker(self):
        self.token.is_mayhem_mode = True
        self.runtime.only_dev = False
        with patch.object(self.book, "reserve", wraps=self.book.reserve) as reserve:
            for strategy in ("dev-second-block", "strong-pressure-8k", "strength-21k",
                             "first-block-four-buyers", "telegram-immediate"):
                with self.subTest(strategy=strategy):
                    self.signal["strategy"] = strategy
                    result = await SharedBuyer(self.runtime, self.broker).execute(self.token)
                    self.assertFalse(result.success)
                    self.assertEqual(result.error_message, "mayhem_mode")
            reserve.assert_not_called()
        self.runtime.rpc.assert_not_awaited()
        self.runtime.raw_holdings.assert_not_awaited()
        self.curve.assert_not_awaited()
        self.broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_authoritative_curve_mayhem_cancels_unsubmitted_reservation(self):
        self.state["is_mayhem_mode"] = True
        with (patch.object(self.book, "reserve", wraps=self.book.reserve) as reserve,
              patch.object(self.book, "cancel_unsubmitted", wraps=self.book.cancel_unsubmitted) as cancel,
              patch.object(self.book, "submitting", wraps=self.book.submitting) as submitting):
            result = await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "mayhem_mode")
        reserve.assert_called_once()
        cancel.assert_called_once()
        submitting.assert_not_called()
        self.curve.assert_awaited_once_with(self.token.bonding_curve, commitment="processed")
        self.broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_last_curve_returned_by_entry_risk_check_cannot_enable_mayhem(self):
        self.runtime.only_dev = False
        self.signal = {"strategy": "strong-pressure-8k", "sol_usd": 100}
        self.runtime.four = copy.deepcopy(self.runtime.four)
        self.runtime.four["entry_protection"] = {"enabled": True}
        self.runtime.check_entry_risk = AsyncMock(return_value=(
            {**self.state, "is_mayhem_mode": True}, {"offline": True}))
        with patch.object(self.book, "submitting", wraps=self.book.submitting) as submitting:
            result = await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.assertFalse(result.success)
        self.runtime.check_entry_risk.assert_awaited_once()
        submitting.assert_not_called()
        self.broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_normal_cashback_with_official_fee_sharing_stays_eligible(self):
        self.state.update(creator=str(self.shared), is_cashback_coin=True)

        async def decline_unsigned(token, **kwargs: object) -> TradeResult:
            self.assertFalse(token.is_mayhem_mode)
            self.assertTrue(token.is_cashback_coin)
            self.assertEqual(token.creator, self.shared)
            self.assertEqual(token.creator_vault, sharing.creator_vault(self.shared))
            self.assertEqual(token.user, sharing.DEVELOPER)
            self.assertEqual(self.signal["creator_history"]["migration_fraction"], .01)
            position = self.book.positions()[0]
            self.assertLessEqual(position["budget"], 300_000_000)
            self.assertLess(kwargs["amount_sol"], .3)
            self.assertFalse(self.book.signatures(position["nonce"]))
            self.assertTrue(ENTRY_WINDOW.get()())
            return TradeResult(False, error_message="Unsigned broker")

        self.broker.execute.side_effect = decline_unsigned
        self.assertFalse((await SharedBuyer(self.runtime, self.broker).execute(self.token)).success)
        self.broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_presign_window_rejects_a_late_mayhem_flag_without_signing(self):
        async def decline_unsigned(token, **kwargs: object) -> TradeResult:
            guard = ENTRY_WINDOW.get()
            self.assertTrue(guard())
            check_entry_window()
            token.is_mayhem_mode = True
            self.assertFalse(guard())
            with self.assertRaises(EntryWindowExpiredError):
                check_entry_window()
            return TradeResult(False, error_message="Unsigned broker")

        self.broker.execute.side_effect = decline_unsigned
        self.assertFalse((await SharedBuyer(self.runtime, self.broker).execute(self.token)).success)
        self.broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()


class NormalOnlySourceTests(unittest.IsolatedAsyncioTestCase):
    settle = stream.DevSecondWindowSourceTests.settle
    created = stream.DevSecondWindowSourceTests.created

    async def asyncSetUp(self):
        await stream.DevSecondWindowSourceTests.asyncSetUp(self)
        self.policy["require_first_block_activity"] = False
        self.token.is_mayhem_mode = False
        self.token.is_cashback_coin = False

    async def test_mayhem_is_excluded_from_delivery_but_creation_stays_in_dev_history(self):
        self.token.is_mayhem_mode = True
        await self.created(events.trade(self.mint, self.a), user=self.a)
        candidate = self.source.candidates[str(self.mint)]
        self.assertIsNone(candidate.token)
        self.source.history.observe.assert_called_once_with(str(self.a), str(self.mint))
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.callback.assert_not_awaited()

    async def test_normal_cashback_sharing_recipient_still_delivers_with_one_percent_history(self):
        self.token.creator = Pubkey.from_string(sharing_config_address(str(self.mint)))
        self.token.is_cashback_coin = True
        await self.created(events.trade(self.mint, self.a), user=self.a)
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_awaited_once()
        delivered = self.callback.await_args.args[0]
        self.assertFalse(delivered.is_mayhem_mode)
        self.assertTrue(delivered.is_cashback_coin)
        self.assertEqual(delivered.creator, self.token.creator)
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["developer"], str(self.a))
        self.assertEqual(signal["creator"], str(self.creator))
        self.assertEqual(signal["creator_history"]["migration_fraction"], .01)
        self.source.history.observe.assert_called_once_with(str(self.a), str(self.mint))


class NormalOnlyFinalBuyerGuardTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = sharing.DevFeeSharingBeforeBuildTests.asyncSetUp
    execute_without_network = sharing.DevFeeSharingBeforeBuildTests.execute_without_network

    async def test_regular_and_fast_fresh_mayhem_stop_before_instruction_builder(self):
        self.buyer.allow_mayhem = False
        self.curve_read.return_value["is_mayhem_mode"] = True
        for fast in (False, True):
            with self.subTest(fast=fast):
                self.buyer.extreme_fast_mode = fast
                self.buyer.extreme_fast_token_amount = 1_000_000
                result = await self.execute_without_network()
                self.assertFalse(result.success)
                self.assertEqual(result.error_message, "mayhem_mode")
                self.build.assert_not_awaited()

    async def test_cached_mayhem_also_stops_before_instruction_builder(self):
        self.buyer.allow_mayhem = False
        self.token.is_mayhem_mode = True
        self.token.additional_data.update(precio_curva=1e-7, precio_curva_en=time.monotonic())
        result = await self.execute_without_network()
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "mayhem_mode")
        self.curve_read.assert_not_awaited()
        self.build.assert_not_awaited()


class NormalOnlyExistingExitTests(unittest.IsolatedAsyncioTestCase):
    setUp = history.HistorySaleFeeTests.setUp
    add_position = history.HistorySaleFeeTests.add_position
    successful_sale = history.HistorySaleFeeTests.successful_sale
    assert_clean_context = history.HistorySaleFeeTests.assert_clean_context

    async def test_existing_mayhem_position_can_still_reach_its_fake_seller(self):
        self.token.is_mayhem_mode = True
        self.trader.buyer = SimpleNamespace(allow_mayhem=True, require_mayhem=True)
        _configure_normal_only_assets(self.trader)
        self.seller.execute.side_effect = None
        self.seller.execute.return_value = TradeResult(False, error_message="Unsigned seller")
        self.assertFalse(await self.trader._sell_amount(self.token, 100, 1e-7, "stop_loss", full_exit=True))
        self.seller.execute.assert_awaited_once()
        self.assertTrue(self.seller.execute.await_args.args[0].is_mayhem_mode)
        self.assertFalse(self.trader.allow_mayhem)
        self.assertEqual(len(self.book.positions()), 1)
        self.assert_clean_context()


if __name__ == "__main__":
    unittest.main()
