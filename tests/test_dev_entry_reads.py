"""Recent-entry read proofs use synthetic accounts and no network or signer."""

# ruff: noqa: ANN001, ANN201, SLF001

import asyncio
import dataclasses
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from solders.pubkey import Pubkey

from trading import dev_entry_reads as reads
from trading.dev_entry_reads import (
    DEV_ENTRY_READ_VIEW,
    DevEntryReadView,
    cached_mint_program,
    consume_dev_read_view,
    remember_mint_program,
)
from trading.token_builder import build_pumpfun_token_info

TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")


class MintReadProofTests(unittest.TestCase):
    def setUp(self):
        cache_patch = patch.object(reads, "_MINT_PROGRAM_READS", OrderedDict())
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        self.mint = Pubkey.new_unique()

    def test_both_supported_owner_programs_can_be_reused_for_the_exact_mint(self):
        for owner in (TOKEN, TOKEN_2022):
            with self.subTest(owner=owner):
                remember_mint_program(str(self.mint), str(owner), now=100)
                self.assertEqual(cached_mint_program(self.mint, owner, now=100.5), owner)
                self.assertIsNone(cached_mint_program(Pubkey.new_unique(), owner, now=100.5))

    def test_a_json_proof_or_wrong_requested_program_is_not_a_process_read(self):
        self.assertIsNone(cached_mint_program(self.mint, TOKEN, now=100))
        remember_mint_program(self.mint, TOKEN, now=100)
        self.assertIsNone(cached_mint_program(self.mint, TOKEN_2022, now=100.1))
        self.assertEqual(cached_mint_program(self.mint, TOKEN, now=100.1), TOKEN)

    def test_proof_expires_after_one_second_and_backward_clock_is_rejected(self):
        remember_mint_program(self.mint, TOKEN, now=100)
        self.assertEqual(cached_mint_program(self.mint, TOKEN, now=101), TOKEN)
        self.assertIsNone(cached_mint_program(self.mint, TOKEN, now=101.001))
        remember_mint_program(self.mint, TOKEN, now=100)
        self.assertIsNone(cached_mint_program(self.mint, TOKEN, now=99.9))
        self.assertIsNone(cached_mint_program(self.mint, TOKEN, now=100.1))

    def test_invalid_account_owner_cannot_keep_an_older_positive_proof(self):
        remember_mint_program(self.mint, TOKEN, now=100)
        remember_mint_program(self.mint, Pubkey.new_unique(), now=100.1)
        self.assertIsNone(cached_mint_program(self.mint, TOKEN, now=100.2))
        for invalid in (None, True, "invalid", 1, {}):
            with self.subTest(invalid=invalid):
                remember_mint_program(invalid, invalid, now=100)
                self.assertIsNone(cached_mint_program(invalid, invalid, now=100))

    def test_bad_timestamps_never_create_or_return_a_proof(self):
        for invalid in (True, float("nan"), float("inf"), -1, 10**1000):
            with self.subTest(invalid=invalid):
                remember_mint_program(self.mint, TOKEN, now=invalid)
                self.assertIsNone(cached_mint_program(self.mint, TOKEN, now=100))
                self.assertIsNone(cached_mint_program(self.mint, TOKEN, now=invalid))

    def test_cache_is_bounded_and_recently_used_mints_survive_lru_eviction(self):
        mints = [Pubkey.new_unique() for _ in range(257)]
        for mint in mints[:-1]:
            remember_mint_program(mint, TOKEN, now=100)
        self.assertEqual(len(reads._MINT_PROGRAM_READS), 256)
        self.assertEqual(cached_mint_program(mints[0], TOKEN, now=100.1), TOKEN)
        remember_mint_program(mints[-1], TOKEN_2022, now=100.2)
        self.assertEqual(len(reads._MINT_PROGRAM_READS), 256)
        self.assertEqual(cached_mint_program(mints[0], TOKEN, now=100.3), TOKEN)
        self.assertIsNone(cached_mint_program(mints[1], TOKEN, now=100.3))
        self.assertEqual(cached_mint_program(mints[-1], TOKEN_2022, now=100.3), TOKEN_2022)

    def test_new_read_refreshes_a_mints_owner_timestamp_and_recency(self):
        remember_mint_program(self.mint, TOKEN, now=100)
        remember_mint_program(self.mint, TOKEN_2022, now=101)
        self.assertIsNone(cached_mint_program(self.mint, TOKEN, now=101.5))
        self.assertEqual(cached_mint_program(self.mint, TOKEN_2022, now=101.5), TOKEN_2022)


class EntryReadViewTests(unittest.TestCase):
    def setUp(self):
        self.context = DEV_ENTRY_READ_VIEW.set(None)
        self.addCleanup(DEV_ENTRY_READ_VIEW.reset, self.context)
        self.mint, self.wallet, self.origin = (str(Pubkey.new_unique()) for _ in range(3))
        self.token = SimpleNamespace(mint=Pubkey.from_string(self.mint), additional_data={
            "dev_entry_target_slot": 102, "dev_entry_generation": 0,
        })
        self.signal = {"target_slot": 102, "source_generation": 0, "creation_fee_creator": self.origin}
        self.view = DevEntryReadView(self.mint, self.wallet, 102, 0, self.origin, 100,
                                     (0, 101), {"value": 1_000_000_000, "context": {"slot": 80}})

    def consume(self, view=None, *, token=None, signal=None, wallet=None, now=100.5):
        DEV_ENTRY_READ_VIEW.set(self.view if view is None else view)
        return consume_dev_read_view(self.token if token is None else token,
                                     self.signal if signal is None else signal,
                                     self.wallet if wallet is None else wallet, now=now)

    def assert_rejected_and_consumed(self, view):
        self.assertIsNone(self.consume(view))
        self.assertIsNone(DEV_ENTRY_READ_VIEW.get())

    def test_valid_zero_generation_view_is_consumed_once(self):
        actual = self.consume()
        self.assertEqual(actual, self.view)
        self.assertIsNot(actual, self.view)
        self.assertIsNone(consume_dev_read_view(self.token, self.signal, self.wallet, now=100.5))

    def test_result_balance_is_copied_and_view_fields_are_frozen(self):
        actual = self.consume()
        self.view.balance["value"] = 0
        self.view.balance["context"]["slot"] = 0
        self.assertEqual(actual.balance["value"], 1_000_000_000)
        self.assertEqual(actual.balance["context"]["slot"], 80)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            actual.target_slot = 103

    def test_exact_wallet_mint_generation_target_and_origin_are_required(self):
        for changes in ({"mint": str(Pubkey.new_unique())}, {"wallet": str(Pubkey.new_unique())},
                        {"target_slot": 103}, {"source_generation": 1},
                        {"creation_fee_creator": str(Pubkey.new_unique())}):
            with self.subTest(changes=changes):
                self.assert_rejected_and_consumed(dataclasses.replace(self.view, **changes))

    def test_signal_identity_requires_concrete_integers_and_matching_origin(self):
        for field, values in (("target_slot", (True, None, 102.0, "102", 103)),
                              ("source_generation", (True, None, 0.0, "0", 1)),
                              ("creation_fee_creator", (None, "different"))):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertIsNone(self.consume(signal={**self.signal, field: value}))
                    self.assertIsNone(DEV_ENTRY_READ_VIEW.get())

    def test_delivered_token_target_and_generation_cannot_disagree(self):
        for field, value in (("dev_entry_target_slot", 103), ("dev_entry_target_slot", True),
                             ("dev_entry_generation", 1), ("dev_entry_generation", False)):
            with self.subTest(field=field, value=value):
                token = SimpleNamespace(mint=self.token.mint,
                                        additional_data={**self.token.additional_data, field: value})
                self.assertIsNone(self.consume(token=token))

    def test_stale_backward_or_invalid_view_clocks_are_rejected(self):
        for value in (99, 100.6001, float("inf"), float("nan"), True, 10**1000):
            with self.subTest(now=value):
                self.assertIsNone(self.consume(now=value))
        for value in (None, True, -1, float("inf"), float("nan"), 10**1000):
            with self.subTest(created_at=value):
                self.assert_rejected_and_consumed(dataclasses.replace(self.view, created_at=value))
        self.assertIsNotNone(self.consume(now=100.6))

    def test_malformed_holdings_and_balances_fall_back_without_throwing(self):
        for holdings in (None, [0, 101], (0,), (0, 101, 9), (False, 101), (-1, 101), (0, True)):
            with self.subTest(holdings=holdings):
                self.assert_rejected_and_consumed(dataclasses.replace(self.view, holdings=holdings))
        for balance in (None, {}, {"value": True, "context": {"slot": 80}},
                        {"value": -1, "context": {"slot": 80}}, {"value": 1, "context": None},
                        {"value": 1, "context": {"slot": True}}, {"value": 1, "context": {"slot": -1}}):
            with self.subTest(balance=balance):
                self.assert_rejected_and_consumed(dataclasses.replace(self.view, balance=balance))
        self.assertIsNone(self.consume(view={"mint": self.mint}))

    def test_no_origin_is_valid_only_when_both_snapshot_and_signal_omit_it(self):
        legacy = dataclasses.replace(self.view, creation_fee_creator=None)
        signal = {key: value for key, value in self.signal.items() if key != "creation_fee_creator"}
        self.assertIsNotNone(self.consume(legacy, signal=signal))


class BuilderMintProofTests(unittest.IsolatedAsyncioTestCase):
    async def test_builder_records_the_actual_read_start_not_the_later_curve_completion(self):
        mint, creator, curve, associated, vault = (Pubkey.new_unique() for _ in range(5))
        implementations = SimpleNamespace(
            address_provider=SimpleNamespace(derive_pool_address=lambda _mint: curve,
                get_additional_accounts=lambda _token: {"associated_bonding_curve": associated, "creator_vault": vault}),
            curve_manager=SimpleNamespace(get_pool_state=AsyncMock(return_value={
                "complete": False, "price_per_token": 1e-7, "creator": creator,
            })),
        )
        client = SimpleNamespace(get_account_info=AsyncMock(return_value=SimpleNamespace(owner=TOKEN_2022)))
        with patch("trading.token_builder.monotonic", side_effect=(100, 102)), \
             patch("trading.token_builder.remember_mint_program") as remember:
            token = await build_pumpfun_token_info(str(mint), implementations, client)
        self.assertIsNotNone(token)
        remember.assert_called_once_with(mint, TOKEN_2022, now=100)
        client.get_account_info.assert_awaited_once_with(mint, commitment="processed")
        implementations.curve_manager.get_pool_state.assert_awaited_once_with(
            curve, commitment="processed", exigir_precio=False)


class EntryReadTaskOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_inherited_child_context_cannot_consume_the_parents_view(self):
        mint, wallet = str(Pubkey.new_unique()), str(Pubkey.new_unique())
        token = SimpleNamespace(mint=mint, additional_data={"dev_entry_target_slot": 102,
                                                          "dev_entry_generation": 0})
        signal = {"target_slot": 102, "source_generation": 0}
        view = DevEntryReadView(mint, wallet, 102, 0, None, 100,
                                (0, 102), {"value": 1_000_000_000, "context": {"slot": 80}})
        context = DEV_ENTRY_READ_VIEW.set(view)
        try:
            async def consume_as_child() -> None:
                self.assertIs(DEV_ENTRY_READ_VIEW.get(), view)
                self.assertIsNone(consume_dev_read_view(token, signal, wallet, now=100.1))
                self.assertIsNone(DEV_ENTRY_READ_VIEW.get())

            await asyncio.create_task(consume_as_child())
            self.assertIs(DEV_ENTRY_READ_VIEW.get(), view)
            self.assertIsNotNone(consume_dev_read_view(token, signal, wallet, now=100.1))
            self.assertIsNone(consume_dev_read_view(token, signal, wallet, now=100.1))
        finally:
            DEV_ENTRY_READ_VIEW.reset(context)


if __name__ == "__main__":
    unittest.main()
