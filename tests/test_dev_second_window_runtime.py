"""Exercise strict second-produced-block admission without signing or networking."""

# The shared fixture deliberately supplies duck-typed runtime hooks.
# ruff: noqa: ANN001, ARG001

import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_four_strategy_bundle as bundle
import test_four_strategy_inclusive_entry as inherited

from trading.base import TradeResult
from trading.four_strategy_runtime import SharedBuyer
from trading.second_block_policy import STRATEGY_ID
from trading.transaction_observer import (
    BUY_PRICE_CEILING,
    ENTRY_WINDOW,
    OBSERVER,
    PRIORITY_CAP,
    observe,
)

SOL = 1_000_000_000
TARGET_SLOT = 102


class StrictSecondWindowRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await bundle.BuyerTests.asyncSetUp(self)
        self.runtime.only_dev = True
        self.runtime.compute_units = {"buy": 200000}
        self.runtime.second_policy = {
            **copy.deepcopy(inherited.SECOND_POLICY),
            "enabled": True,
            "entry_rule": "creator_migration_only",
            "entry_timing": "second_produced_block",
            "require_dev_initial_buy": True,
            "third_block_retry_on_sharp_rise": False,
            "max_entry_budget_usd": 20.0,
            "balance_fraction": .80,
        }
        self.signal = {
            "strategy": STRATEGY_ID,
            "creator": "fake-creator",
            "developer": "fake-dev",
            "creation_slot": 100,
            "target_slot": TARGET_SLOT,
            "entry_block": 2,
            "dev_initial_buy_verified": True,
            "source_generation": 1,
            "sol_usd": 100,
        }
        self.runtime.current_signal = lambda _token: self.signal
        self.token.additional_data["dev_entry_target_slot"] = TARGET_SLOT
        self.runtime.raw_holdings.return_value = (0, TARGET_SLOT)
        self.rpc_slot = TARGET_SLOT
        self.runtime.rpc.side_effect = self.rpc_response
        self.curve = self.runtime.platform_implementations.curve_manager.get_pool_state
        self.state = {
            "price_per_token": 1e-7,
            "complete": False,
            "creator": "fake-creator",
        }
        self.curve.return_value = self.state
        self.runtime.second_source = SimpleNamespace(arm_third_block_retry=Mock())

    async def rpc_response(self, method: str, params: list) -> object:
        if method == "getBalance":
            return {"value": SOL, "context": {"slot": 100}}
        self.assertEqual(method, "getSlot")
        self.assertEqual(params, [{"commitment": "processed"}])
        return self.rpc_slot

    def assert_contexts_reset(self) -> None:
        for variable in (OBSERVER, PRIORITY_CAP, BUY_PRICE_CEILING, ENTRY_WINDOW):
            with self.subTest(context=variable.name):
                self.assertIsNone(variable.get())

    async def assert_rejected_without_broker(self) -> None:
        broker = SimpleNamespace(execute=AsyncMock())
        with patch.object(self.book, "submitting", wraps=self.book.submitting) as submitting:
            result = await SharedBuyer(self.runtime, broker).execute(self.token)
        self.assertFalse(result.success)
        broker.execute.assert_not_awaited()
        submitting.assert_not_called()
        self.assertFalse(self.book.positions())
        self.runtime.second_source.arm_third_block_retry.assert_not_called()
        self.assert_contexts_reset()

    async def test_second_block_and_concrete_integer_target_are_required(self) -> None:
        for field, value in (
            ("entry_block", None), ("entry_block", 1), ("entry_block", 3),
            ("entry_block", 2.0), ("entry_block", True),
            ("target_slot", None), ("target_slot", True),
            ("target_slot", 102.0), ("target_slot", "102"),
        ):
            with self.subTest(field=field, value=value):
                original = self.signal[field]
                self.signal[field] = value
                await self.assert_rejected_without_broker()
                self.signal[field] = original

    async def test_initial_developer_buy_must_be_verified(self) -> None:
        for value in (None, False, 1, "true"):
            with self.subTest(proof=value):
                self.signal["dev_initial_buy_verified"] = value
                await self.assert_rejected_without_broker()

    async def test_token_target_must_match_signal_target(self) -> None:
        self.token.additional_data["dev_entry_target_slot"] = TARGET_SLOT + 1
        await self.assert_rejected_without_broker()

    async def test_source_target_cannot_move_during_balance_read(self) -> None:
        async def move_target(method: str, params: list) -> object:
            response = await self.rpc_response(method, params)
            if method == "getBalance":
                # Mutating the original signal also detects accidental aliasing.
                self.signal["target_slot"] = TARGET_SLOT + 1
                self.token.additional_data["dev_entry_target_slot"] = TARGET_SLOT + 1
            return response

        self.runtime.rpc.side_effect = move_target
        await self.assert_rejected_without_broker()

    async def test_source_target_cannot_move_during_curve_read(self) -> None:
        async def move_target(*args: object, **kwargs: object) -> dict:
            self.signal["target_slot"] = TARGET_SLOT + 1
            self.token.additional_data["dev_entry_target_slot"] = TARGET_SLOT + 1
            return self.state

        self.curve.side_effect = move_target
        await self.assert_rejected_without_broker()

    async def test_source_disappearing_during_curve_read_cancels_unsubmitted_entry(self) -> None:
        async def lose_signal(*args: object, **kwargs: object) -> dict:
            self.runtime.current_signal = lambda _token: None
            return self.state

        self.curve.side_effect = lose_signal
        await self.assert_rejected_without_broker()

    async def assert_cancelled_without_broker(self) -> None:
        broker = SimpleNamespace(execute=AsyncMock())
        with patch.object(self.book, "submitting", wraps=self.book.submitting) as submitting:
            with self.assertRaises(asyncio.CancelledError):
                await SharedBuyer(self.runtime, broker).execute(self.token)
        broker.execute.assert_not_awaited()
        submitting.assert_not_called()
        self.assertFalse(self.book.positions())
        self.runtime.second_source.arm_third_block_retry.assert_not_called()
        self.assert_contexts_reset()

    async def test_cancellation_during_curve_read_releases_unsubmitted_reservation(self) -> None:
        self.curve.side_effect = asyncio.CancelledError
        await self.assert_cancelled_without_broker()

    async def test_cancellation_during_slot_read_releases_unsubmitted_reservation(self) -> None:
        async def cancelled_slot(method: str, params: list) -> object:
            if method == "getSlot":
                raise asyncio.CancelledError
            return await self.rpc_response(method, params)

        self.runtime.rpc.side_effect = cancelled_slot
        await self.assert_cancelled_without_broker()

    async def test_processed_rpc_slot_must_equal_target_before_submitting(self) -> None:
        for value in (101, 103, None, True, 102.0, "102"):
            with self.subTest(slot=value):
                self.rpc_slot = value
                await self.assert_rejected_without_broker()

    async def test_processed_holdings_context_cannot_be_older_than_creation_or_newer_than_target(self) -> None:
        for context in (99, 103, None, True, 102.0):
            with self.subTest(context=context):
                self.runtime.raw_holdings.return_value = (0, context)
                await self.assert_rejected_without_broker()

    async def test_existing_processed_holdings_block_the_entry(self) -> None:
        self.runtime.raw_holdings.return_value = (1, TARGET_SLOT)
        await self.assert_rejected_without_broker()

    async def test_fresh_second_block_uses_processed_holdings_and_preserves_usd_cap(self) -> None:
        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            position = self.book.positions()[0]
            details = json.loads(position["details"])
            self.assertEqual(position["state"], "uncertain")
            self.assertEqual(details["target_slot"], TARGET_SLOT)
            self.assertEqual(details["entry_block"], 2)
            self.assertEqual(details["policy_fraction"], .80)
            self.assertEqual(details["budget_cap_lamports"], 200_000_000)
            self.assertLessEqual(position["budget"], 200_000_000)
            self.assertGreater(position["budget"], 199_999_997)
            self.assertLess(kwargs["amount_sol"], .2)
            self.assertEqual(PRIORITY_CAP.get(), 700_000)
            self.assertTrue(ENTRY_WINDOW.get()())
            self.assertFalse(self.book.signatures(position["nonce"]))
            return TradeResult(success=False, error_message="Unsigned fake broker")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        result = await SharedBuyer(self.runtime, broker).execute(self.token)
        self.assertFalse(result.success)
        broker.execute.assert_awaited_once()
        self.runtime.raw_holdings.assert_awaited_once_with("mint", commitment="processed")
        self.runtime.rpc.assert_any_await("getBalance", ["owner", {"commitment": "finalized"}])
        self.runtime.rpc.assert_any_await("getSlot", [{"commitment": "processed"}])
        self.curve.assert_awaited_once_with("curve", commitment="processed")
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_strict_timing_does_not_restore_4200_mc_or_sharp_rise_filters(self) -> None:
        self.state["price_per_token"] = 5e-7  # $50k MC at the fixture SOL/USD quote.

        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            self.assertIsNone(BUY_PRICE_CEILING.get())
            self.assertTrue(ENTRY_WINDOW.get()())
            return TradeResult(success=False, error_message="Unsigned fake broker")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_awaited_once()
        self.runtime.check_entry_risk.assert_not_awaited()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_presign_guard_rejects_changed_target_even_in_history_only_mode(self) -> None:
        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            guard = ENTRY_WINDOW.get()
            self.assertTrue(guard())
            self.signal["target_slot"] = TARGET_SLOT + 1
            self.token.additional_data["dev_entry_target_slot"] = TARGET_SLOT + 1
            self.assertFalse(guard())
            return TradeResult(success=False, error_message="Unsigned fake broker")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_presign_guard_requires_second_block_and_developer_buy_proof(self) -> None:
        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            guard = ENTRY_WINDOW.get()
            self.assertTrue(guard())
            for field in ("entry_block", "dev_initial_buy_verified"):
                with self.subTest(missing=field):
                    original = self.signal.pop(field)
                    self.assertFalse(guard())
                    self.signal[field] = original
                    self.assertTrue(guard())
            self.runtime.current_signal = lambda _token: None
            self.assertFalse(guard())
            return TradeResult(success=False, error_message="Unsigned fake broker")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_unknown_acknowledgement_keeps_fake_signed_identity_and_reservation(self) -> None:
        async def unknown_acknowledgement(token, **kwargs: object) -> TradeResult:
            self.assertTrue(ENTRY_WINDOW.get()())
            observe("signed", "offline-test-signature")
            raise TimeoutError

        broker = SimpleNamespace(execute=AsyncMock(side_effect=unknown_acknowledgement))
        with self.assertRaises(TimeoutError):
            await SharedBuyer(self.runtime, broker).execute(self.token)
        broker.execute.assert_awaited_once()
        position = self.book.positions()[0]
        self.assertEqual(position["state"], "uncertain")
        self.assertEqual(self.book.signatures(position["nonce"]), [("offline-test-signature", "buy")])
        self.assertEqual(json.loads(position["details"])["target_slot"], TARGET_SLOT)
        self.assert_contexts_reset()


if __name__ == "__main__":
    unittest.main()
