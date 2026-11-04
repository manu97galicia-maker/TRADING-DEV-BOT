"""Reuse only task-local, recent dev admission reads; never sign or send."""

# The inherited fake trader exercises both real handlers with unsigned brokers.
# ruff: noqa: ANN001, ANN201, ARG001, SLF001

import asyncio
import copy
import json
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_four_strategy_inclusive_entry as inherited
from solders.pubkey import Pubkey

import trading.universal_trader as legacy
from monitoring.creator_history_index import curve_address
from trading.base import TradeResult
from trading.dev_entry_reads import (
    DEV_ENTRY_READ_VIEW,
    consume_dev_read_view,
    remember_mint_program,
)
from trading.four_strategy_runtime import SharedBuyer
from trading.second_block_policy import STRATEGY_ID
from trading.transaction_observer import ENTRY_WINDOW, OBSERVER, PRIORITY_CAP

SOL = 1_000_000_000
TARGET_SLOT = 102


class DevReadHandoffTests(unittest.IsolatedAsyncioTestCase):
    setUp = inherited.InheritedInclusiveEntryTests.setUp
    fixture = inherited.InheritedInclusiveEntryTests.fixture
    handle = inherited.InheritedInclusiveEntryTests.handle

    async def asyncSetUp(self) -> None:
        self.runtime, self.token, self.broker = self.fixture(STRATEGY_ID)
        self.runtime.only_dev = True
        self.runtime.second_policy.update(
            entry_timing="second_produced_block", require_dev_initial_buy=True,
            third_block_retry_on_sharp_rise=False, max_entry_budget_usd=30.0,
        )
        self.token.mint = Pubkey.new_unique()
        self.token.creator = Pubkey.new_unique()
        self.token.user = Pubkey.new_unique()
        self.token.bonding_curve = Pubkey.from_string(curve_address(str(self.token.mint)))
        self.token.additional_data.update(
            acquisition_kind="second-block-dev", dev_entry_target_slot=TARGET_SLOT,
            dev_entry_generation=1,
        )
        self.signal = {
            "strategy": STRATEGY_ID, "creator": str(self.token.creator),
            "creation_fee_creator": str(self.token.creator), "developer": str(self.token.user),
            "creation_slot": 100, "target_slot": TARGET_SLOT, "source_generation": 1,
            "entry_block": 2, "dev_initial_buy_verified": True, "sol_usd": 100,
        }
        self.runtime.current_signal = lambda _token: self.signal
        self.runtime.raw_holdings.return_value = (0, TARGET_SLOT)
        self.balance = SOL
        self.chain_slot = TARGET_SLOT
        self.runtime.rpc.side_effect = self.rpc_response
        self.curve = self.runtime.platform_implementations.curve_manager.get_pool_state
        self.state = {"price_per_token": 1e-7, "complete": False,
                      "creator": str(self.token.creator)}
        self.curve.return_value = self.state
        context = DEV_ENTRY_READ_VIEW.set(None)
        self.addCleanup(DEV_ENTRY_READ_VIEW.reset, context)

    async def rpc_response(self, method, params):
        if method == "getBalance":
            return {"value": self.balance, "context": {"slot": 100}}
        self.assertEqual(method, "getSlot")
        self.assertEqual(params, [{"commitment": "processed"}])
        return self.chain_slot

    def calls_to(self, method):
        return [call for call in self.runtime.rpc.await_args_list if call.args[0] == method]

    def assert_contexts_clean(self):
        self.assertIsNone(DEV_ENTRY_READ_VIEW.get())
        for variable in (OBSERVER, PRIORITY_CAP, ENTRY_WINDOW):
            with self.subTest(variable=variable.name):
                self.assertIsNone(variable.get())

    def assert_unsigned_budget(self, token, kwargs):
        self.assertEqual(token.mint, self.token.mint)
        position = self.book.positions()[0]
        details = json.loads(position["details"])
        self.assertEqual(details["policy_fraction"], .80)
        self.assertEqual(details["budget_cap_lamports"], 300_000_000)
        self.assertLessEqual(position["budget"], 300_000_000)
        self.assertLess(kwargs["amount_sol"], .3)
        self.assertLessEqual(position["budget"], self.balance-30_000_000)
        self.assertFalse(self.book.signatures(position["nonce"]))
        self.assertTrue(ENTRY_WINDOW.get()())

    def use_unsigned_budget_broker(self):
        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            self.assert_unsigned_budget(token, kwargs)
            return TradeResult(success=False, error_message="Unsigned read-handoff fixture")

        self.broker.execute.side_effect = reject_unsigned

    async def test_real_inherited_handler_avoids_duplicate_mint_balance_and_holdings_reads(self):
        remember_mint_program(self.token.mint, legacy.TOKEN_PROGRAM)
        self.use_unsigned_budget_broker()
        await self.handle(self.runtime, self.token)
        self.broker.execute.assert_awaited_once()
        self.runtime.solana_client.get_account_info.assert_not_awaited()
        self.runtime.solana_client.get_sol_balance.assert_not_awaited()
        self.runtime.raw_holdings.assert_awaited_once_with(str(self.token.mint), commitment="processed")
        self.assertEqual(len(self.calls_to("getBalance")), 1)
        self.assertEqual(len(self.calls_to("getSlot")), 1)
        self.curve.assert_awaited_once_with(self.token.bonding_curve, commitment="processed")
        self.assertFalse(self.book.positions())
        self.assertEqual(self.runtime._reserved_lamports, 0)
        self.assertEqual(self.runtime._reserved_real_buy_slots, 0)
        self.assert_contexts_clean()

    async def test_missing_mint_proof_falls_back_to_the_existing_owner_read(self):
        self.use_unsigned_budget_broker()
        await self.handle(self.runtime, self.token)
        self.broker.execute.assert_awaited_once()
        self.runtime.solana_client.get_account_info.assert_awaited_once_with(
            self.token.mint, commitment="processed")
        self.assertEqual(len(self.calls_to("getBalance")), 1)
        self.runtime.raw_holdings.assert_awaited_once()
        self.assert_contexts_clean()

    async def test_a_cached_mint_program_cannot_override_a_different_prepared_program(self):
        remember_mint_program(self.token.mint, legacy.TOKEN_PROGRAM)
        self.token.token_program_id = legacy.TOKEN_2022_PROGRAM
        self.assertIsNone(self.runtime._prepared_entry_token_program(self.token))

    async def test_the_mint_proof_hook_does_not_apply_to_other_entry_modes(self):
        remember_mint_program(self.token.mint, legacy.TOKEN_PROGRAM)
        self.runtime.second_policy["entry_timing"] = "any_verified_time"
        self.assertIsNone(self.runtime._prepared_entry_token_program(self.token))

    async def test_wallet_view_is_reused_once_but_curve_and_chain_slot_are_still_read(self):
        self.assertEqual(await self.runtime._entry_wallet_balance(self.token), SOL)
        self.assertIsNotNone(DEV_ENTRY_READ_VIEW.get())
        self.use_unsigned_budget_broker()
        await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.broker.execute.assert_awaited_once()
        self.assertEqual(len(self.calls_to("getBalance")), 1)
        self.runtime.raw_holdings.assert_awaited_once()
        self.curve.assert_awaited_once()
        self.assertEqual(len(self.calls_to("getSlot")), 1)
        self.assert_contexts_clean()

    async def test_stale_wallet_view_is_discarded_and_fresh_reads_are_required(self):
        await self.runtime._entry_wallet_balance(self.token)
        frame = DEV_ENTRY_READ_VIEW.get()
        DEV_ENTRY_READ_VIEW.set(replace(frame, created_at=time.monotonic()-1))
        self.use_unsigned_budget_broker()
        await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.broker.execute.assert_awaited_once()
        self.assertEqual(len(self.calls_to("getBalance")), 2)
        self.assertEqual(self.runtime.raw_holdings.await_count, 2)
        self.assert_contexts_clean()

    async def test_a_wallet_view_for_another_target_cannot_validate_this_entry(self):
        await self.runtime._entry_wallet_balance(self.token)
        frame = DEV_ENTRY_READ_VIEW.get()
        DEV_ENTRY_READ_VIEW.set(replace(frame, target_slot=TARGET_SLOT+1))
        self.use_unsigned_budget_broker()
        await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.broker.execute.assert_awaited_once()
        self.assertEqual(len(self.calls_to("getBalance")), 2)
        self.assertEqual(self.runtime.raw_holdings.await_count, 2)
        self.assert_contexts_clean()

    async def test_existing_holdings_from_the_shared_read_still_block_the_broker(self):
        self.runtime.raw_holdings.return_value = (1, TARGET_SLOT)
        await self.runtime._entry_wallet_balance(self.token)
        result = await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.assertFalse(result.success)
        self.broker.execute.assert_not_awaited()
        self.curve.assert_not_awaited()
        self.assertEqual(len(self.calls_to("getBalance")), 1)
        self.runtime.raw_holdings.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assert_contexts_clean()

    async def test_a_shared_read_cannot_bypass_the_minimum_sol_reserve(self):
        self.balance = 50_000_000
        await self.runtime._entry_wallet_balance(self.token)
        result = await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.assertFalse(result.success)
        self.broker.execute.assert_not_awaited()
        self.curve.assert_not_awaited()
        self.assertFalse(self.book.positions())
        self.assert_contexts_clean()

    async def test_a_shared_read_cannot_bypass_the_processed_slot_recheck(self):
        await self.runtime._entry_wallet_balance(self.token)
        self.chain_slot = TARGET_SLOT+1
        result = await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.assertFalse(result.success)
        self.broker.execute.assert_not_awaited()
        self.assertEqual(len(self.calls_to("getSlot")), 1)
        self.assertFalse(self.book.positions())
        self.assert_contexts_clean()

    async def test_a_price_reference_change_still_rechecks_the_usd_cap(self):
        await self.runtime._entry_wallet_balance(self.token)

        async def increase_quote(*args: object, **kwargs: object) -> dict:
            self.runtime.sol_usd = 200
            return self.state

        self.curve.side_effect = increase_quote
        result = await SharedBuyer(self.runtime, self.broker).execute(self.token)
        self.assertFalse(result.success)
        self.broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())
        self.assert_contexts_clean()

    async def test_window_expiry_during_wallet_read_never_reaches_the_broker(self):
        remember_mint_program(self.token.mint, legacy.TOKEN_PROGRAM)

        async def expire(method, params) -> object:
            response = await self.rpc_response(method, params)
            if method == "getBalance":
                self.signal = None
            return response

        self.runtime.rpc.side_effect = expire
        await self.handle(self.runtime, self.token)
        self.broker.execute.assert_not_awaited()
        self.curve.assert_not_awaited()
        self.assertFalse(self.book.positions())
        self.assert_contexts_clean()

    async def test_other_profiles_keep_the_original_balance_path(self):
        self.runtime.second_policy["entry_timing"] = "any_verified_time"
        balance = await self.runtime._entry_wallet_balance(self.token)
        self.assertEqual(balance, SOL)
        self.runtime.solana_client.get_sol_balance.assert_awaited_once_with(self.runtime.wallet.pubkey)
        self.runtime.raw_holdings.assert_not_awaited()
        self.runtime.rpc.assert_not_awaited()
        self.assert_contexts_clean()

    async def test_wallet_view_age_starts_before_the_rpc_await(self):
        rpc_started_at = []

        async def response(method, params) -> object:
            rpc_started_at.append(time.monotonic())
            await asyncio.sleep(0)
            return await self.rpc_response(method, params)

        self.runtime.rpc.side_effect = response
        await self.runtime._entry_wallet_balance(self.token)
        frame = DEV_ENTRY_READ_VIEW.get()
        self.assertEqual(len(rpc_started_at), 1)
        self.assertLessEqual(frame.created_at, rpc_started_at[0])
        DEV_ENTRY_READ_VIEW.set(None)
        self.assert_contexts_clean()

    async def test_handler_restores_outer_context_when_the_legacy_handler_raises(self):
        await self.runtime._entry_wallet_balance(self.token)
        outer_view = DEV_ENTRY_READ_VIEW.get()

        async def fail_in_legacy(_runtime, token) -> None:
            self.assertIsNone(DEV_ENTRY_READ_VIEW.get())
            await self.runtime._entry_wallet_balance(token)
            self.assertIsNotNone(DEV_ENTRY_READ_VIEW.get())
            raise RuntimeError("Synthetic legacy handler failure")  # noqa: TRY003 -- deliberate unsigned failure

        with patch.object(legacy.UniversalTrader, "_handle_token", fail_in_legacy):
            with self.assertRaisesRegex(RuntimeError, "Synthetic legacy handler failure"):
                await self.runtime._handle_token(self.token)
        self.assertIs(DEV_ENTRY_READ_VIEW.get(), outer_view)
        DEV_ENTRY_READ_VIEW.set(None)
        self.assert_contexts_clean()

    async def test_concurrent_read_views_cannot_cross_mints_or_tasks(self):
        token_a = self.token
        token_b = copy.copy(self.token)
        token_b.mint = Pubkey.new_unique()
        token_b.additional_data = {**self.token.additional_data,
                                   "dev_entry_target_slot": TARGET_SLOT+2, "dev_entry_generation": 2}
        signal_b = {**self.signal, "target_slot": TARGET_SLOT+2, "source_generation": 2}
        self.runtime.current_signal = lambda token: self.signal if token is token_a else signal_b

        async def one(token, signal) -> str:
            await self.runtime._entry_wallet_balance(token)
            frame = DEV_ENTRY_READ_VIEW.get()
            self.assertEqual(frame.mint, str(token.mint))
            self.assertEqual(frame.target_slot, signal["target_slot"])
            consumed = consume_dev_read_view(token, signal, self.runtime.wallet.pubkey)
            self.assertEqual(consumed, frame)
            self.assertIsNot(consumed.balance, frame.balance)
            self.assertIsNone(DEV_ENTRY_READ_VIEW.get())
            return frame.mint

        results = await asyncio.gather(one(token_a, self.signal), one(token_b, signal_b))
        self.assertEqual(set(results), {str(token_a.mint), str(token_b.mint)})
        self.assertEqual(len(self.calls_to("getBalance")), 2)
        self.assertEqual(self.runtime.raw_holdings.await_count, 2)
        self.assert_contexts_clean()


if __name__ == "__main__":
    unittest.main()
