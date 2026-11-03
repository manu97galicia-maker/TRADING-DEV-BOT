"""Canonical Pump fee sharing reaches only an unsigned, synthetic broker."""

# These fixtures deliberately provide only the runtime hooks exercised here.
# ruff: noqa: ANN001, ARG001

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_dev_second_window_runtime as strict
from solders.pubkey import Pubkey

from interfaces.core import Platform
from trading.base import TradeResult
from trading.four_strategy_runtime import SharedBuyer
from trading.platform_aware import PlatformAwareBuyer
from trading.pump_fee_identity import sharing_config_address
from trading.transaction_observer import ENTRY_WINDOW, PRIORITY_CAP, observe

MINT = Pubkey.from_string("7UjCFv9FF4nWAB63vr6WdwAsmFr66hKK3RnLqvKapump")
DEVELOPER = Pubkey.from_string("866SrsMfwKsErATZDCLAPbxH3NuqjKn7zQ1xXMqixoz1")
PUMP = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")


def creator_vault(creator: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"creator-vault", bytes(creator)], PUMP)[0]


class DevFeeSharingRuntimeTests(unittest.IsolatedAsyncioTestCase):
    rpc_response = strict.StrictSecondWindowRuntimeTests.rpc_response
    assert_contexts_reset = strict.StrictSecondWindowRuntimeTests.assert_contexts_reset

    async def asyncSetUp(self) -> None:
        await strict.StrictSecondWindowRuntimeTests.asyncSetUp(self)
        self.original = Pubkey.new_unique()
        self.shared = Pubkey.from_string(sharing_config_address(str(MINT)))
        self.token.mint = MINT
        self.token.creator = self.original
        self.token.creator_vault = creator_vault(self.original)
        self.token.user = DEVELOPER
        self.history = {"migration_fraction": .01, "launches": 100, "migrations": 1}
        self.signal.update(
            creator=str(self.original), creation_fee_creator=str(self.original),
            prepared_fee_creator=str(self.original), developer=str(DEVELOPER),
            creator_history=self.history,
        )
        self.state["creator"] = str(self.original)

    async def assert_rejected_without_broker(self) -> None:
        broker = SimpleNamespace(execute=AsyncMock(return_value=TradeResult(success=False)))
        with patch.object(self.book, "submitting", wraps=self.book.submitting) as submitting:
            result = await SharedBuyer(self.runtime, broker).execute(self.token)
        self.assertFalse(result.success)
        broker.execute.assert_not_awaited()
        submitting.assert_not_called()
        self.assertFalse(self.book.positions())
        self.runtime.second_source.arm_third_block_retry.assert_not_called()
        self.assert_contexts_reset()

    def assert_bound_accounts_and_budget(self, token, kwargs: dict, expected: Pubkey) -> None:
        self.assertEqual(token.creator, expected)
        self.assertEqual(token.creator_vault, creator_vault(expected))
        self.assertEqual(token.user, DEVELOPER)
        self.assertEqual(token.additional_data["dev_creation_fee_creator"], str(self.original))
        self.assertEqual(token.additional_data["dev_current_fee_creator"], str(expected))
        self.assertEqual(self.signal["creator"], str(self.original))
        self.assertEqual(self.signal["creation_fee_creator"], str(self.original))
        self.assertEqual(self.signal["developer"], str(DEVELOPER))
        self.assertEqual(self.signal["creator_history"], self.history)
        position = self.book.positions()[0]
        details = json.loads(position["details"])
        self.assertEqual(position["state"], "uncertain")
        self.assertEqual(details["creation_fee_creator"], str(self.original))
        self.assertEqual(details["entry_fee_creator"], str(expected))
        self.assertEqual(details["policy_fraction"], .80)
        self.assertEqual(details["budget_cap_lamports"], 200_000_000)
        self.assertLessEqual(position["budget"], 200_000_000)
        self.assertLess(kwargs["amount_sol"], .2)
        self.assertEqual(PRIORITY_CAP.get(), 700_000)
        self.assertTrue(ENTRY_WINDOW.get()())
        self.assertFalse(self.book.signatures(position["nonce"]))

    async def exercise_unsigned(self, expected: Pubkey) -> None:
        async def decline_unsigned(token, **kwargs: object) -> TradeResult:
            self.assert_bound_accounts_and_budget(token, kwargs, expected)
            return TradeResult(success=False, error_message="Unsigned fixture")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=decline_unsigned))
        result = await SharedBuyer(self.runtime, broker).execute(self.token)
        self.assertFalse(result.success)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_original_recipient_remains_accepted_with_audited_identity(self) -> None:
        await self.exercise_unsigned(self.original)

    async def test_already_prepared_sharing_recipient_is_accepted(self) -> None:
        self.token.creator = self.shared
        self.token.creator_vault = creator_vault(self.shared)
        self.signal["prepared_fee_creator"] = str(self.shared)
        self.state["creator"] = str(self.shared)
        await self.exercise_unsigned(self.shared)

    async def test_switch_to_official_sharing_pda_rebuilds_stale_creator_vault(self) -> None:
        old_vault = self.token.creator_vault
        self.state["creator"] = str(self.shared)
        await self.exercise_unsigned(self.shared)
        self.assertNotEqual(self.token.creator_vault, old_vault)
        self.assertEqual(self.token.creator_vault, creator_vault(self.shared))

    async def test_arbitrary_changed_fee_recipient_is_rejected_before_submission(self) -> None:
        self.state["creator"] = str(Pubkey.new_unique())
        await self.assert_rejected_without_broker()

    async def test_sharing_pda_of_a_different_mint_is_rejected(self) -> None:
        self.state["creator"] = sharing_config_address(str(Pubkey.new_unique()))
        self.assertNotEqual(self.state["creator"], str(self.shared))
        await self.assert_rejected_without_broker()

    async def test_missing_or_malformed_creator_is_not_accepted_as_sharing(self) -> None:
        for observed in (None, "", "invalid-recipient"):
            with self.subTest(observed=observed):
                self.state["creator"] = observed
                await self.assert_rejected_without_broker()

    async def test_origin_cannot_change_during_the_balance_read(self) -> None:
        async def replace_origin(method: str, params: list) -> object:
            response = await self.rpc_response(method, params)
            if method == "getBalance":
                changed = str(Pubkey.new_unique())
                self.signal["creation_fee_creator"] = changed
                self.signal["creator"] = changed
                self.state["creator"] = changed
            return response

        self.runtime.rpc.side_effect = replace_origin
        await self.assert_rejected_without_broker()

    async def test_origin_cannot_change_during_the_curve_read(self) -> None:
        async def replace_origin(*args: object, **kwargs: object) -> dict:
            changed = str(Pubkey.new_unique())
            self.signal["creation_fee_creator"] = changed
            self.signal["creator"] = changed
            self.state["creator"] = changed
            return self.state

        self.curve.side_effect = replace_origin
        await self.assert_rejected_without_broker()

    async def test_presign_window_rejects_an_origin_change(self) -> None:
        self.state["creator"] = str(self.shared)

        async def decline_unsigned(token, **kwargs: object) -> TradeResult:
            guard = ENTRY_WINDOW.get()
            self.assertTrue(guard())
            self.signal["creation_fee_creator"] = str(Pubkey.new_unique())
            self.assertFalse(guard())
            return TradeResult(success=False, error_message="Unsigned fixture")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=decline_unsigned))
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_presign_window_rejects_an_arbitrary_token_creator_change(self) -> None:
        self.state["creator"] = str(self.shared)

        async def decline_unsigned(token, **kwargs: object) -> TradeResult:
            guard = ENTRY_WINDOW.get()
            self.assertTrue(guard())
            token.creator = Pubkey.new_unique()
            self.assertFalse(guard())
            return TradeResult(success=False, error_message="Unsigned fixture")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=decline_unsigned))
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assert_contexts_reset()

    async def test_official_sharing_does_not_extend_the_second_block_window(self) -> None:
        self.state["creator"] = str(self.shared)
        self.rpc_slot = strict.TARGET_SLOT + 1
        await self.assert_rejected_without_broker()

    async def test_disappearing_signal_prevents_fee_sharing_entry(self) -> None:
        async def expire(*args: object, **kwargs: object) -> dict:
            self.runtime.current_signal = lambda _token: None
            return {**self.state, "creator": str(self.shared)}

        self.curve.side_effect = expire
        await self.assert_rejected_without_broker()

    async def test_unknown_signed_acknowledgement_preserves_fee_identity_and_slot(self) -> None:
        self.state["creator"] = str(self.shared)

        async def lose_acknowledgement(token, **kwargs: object) -> TradeResult:
            self.assert_bound_accounts_and_budget(token, kwargs, self.shared)
            observe("signed", "offline-sharing-signature")
            raise TimeoutError

        broker = SimpleNamespace(execute=AsyncMock(side_effect=lose_acknowledgement))
        with self.assertRaises(TimeoutError):
            await SharedBuyer(self.runtime, broker).execute(self.token)
        position = self.book.positions()[0]
        details = json.loads(position["details"])
        self.assertEqual(position["state"], "uncertain")
        self.assertEqual(details["creation_fee_creator"], str(self.original))
        self.assertEqual(details["entry_fee_creator"], str(self.shared))
        self.assertEqual(self.book.signatures(position["nonce"]), [("offline-sharing-signature", "buy")])
        self.assert_contexts_reset()

    async def test_legacy_signal_without_origin_keeps_existing_exact_match_requirement(self) -> None:
        self.signal.pop("creation_fee_creator")
        self.state["creator"] = str(self.shared)
        await self.assert_rejected_without_broker()


class DevFeeSharingBeforeBuildTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.original = Pubkey.new_unique()
        self.shared = Pubkey.from_string(sharing_config_address(str(MINT)))
        self.client = SimpleNamespace(build_and_send_transaction=AsyncMock())
        self.buyer = PlatformAwareBuyer(
            self.client, SimpleNamespace(pubkey=Pubkey.new_unique(), keypair=None),
            SimpleNamespace(), .1, max_retries=0, allow_mayhem=True,
        )
        self.token = SimpleNamespace(
            platform=Platform.PUMP_FUN, mint=MINT, bonding_curve=Pubkey.new_unique(),
            creator=self.original, creator_vault=creator_vault(self.original),
            user=DEVELOPER, symbol="SI", is_mayhem_mode=False, is_cashback_coin=False,
            additional_data={"dev_creation_fee_creator": str(self.original)},
        )
        self.curve_read = AsyncMock(return_value={
            "price_per_token": 1e-7, "creator": str(self.shared),
            "is_mayhem_mode": False, "is_cashback_coin": False,
        })
        # Stop before building an instruction, accessing a signer, or sending.
        self.build = AsyncMock(side_effect=RuntimeError("Offline fixture stops before instruction creation"))
        self.implementations = SimpleNamespace(
            address_provider=SimpleNamespace(derive_creator_vault=creator_vault),
            instruction_builder=SimpleNamespace(build_buy_instruction=self.build),
            curve_manager=SimpleNamespace(get_pool_state=self.curve_read),
        )

    async def execute_without_network(self) -> TradeResult:
        with patch("trading.platform_aware.get_platform_implementations", return_value=self.implementations):
            result = await self.buyer.execute(self.token)
        self.client.build_and_send_transaction.assert_not_awaited()
        return result

    def assert_updated_sharing_accounts(self) -> None:
        self.build.assert_awaited_once()
        self.assertEqual(self.token.creator, self.shared)
        self.assertEqual(self.token.creator_vault, creator_vault(self.shared))
        self.assertEqual(self.token.user, DEVELOPER)
        self.assertEqual(self.token.additional_data["dev_creation_fee_creator"], str(self.original))
        self.assertEqual(self.token.additional_data["dev_current_fee_creator"], str(self.shared))

    async def test_regular_reread_binds_sharing_recipient_before_instruction_builder(self) -> None:
        await self.execute_without_network()
        self.assert_updated_sharing_accounts()
        self.curve_read.assert_awaited_once()

    async def test_regular_cached_state_keeps_official_sharing_accounts(self) -> None:
        self.token.creator = self.shared
        self.token.creator_vault = creator_vault(self.shared)
        self.token.additional_data.update(precio_curva=1e-7, precio_curva_en=time.monotonic())
        await self.execute_without_network()
        self.assert_updated_sharing_accounts()
        self.curve_read.assert_not_awaited()

    async def test_regular_reread_blocks_arbitrary_recipient_before_builder(self) -> None:
        self.curve_read.return_value["creator"] = str(Pubkey.new_unique())
        result = await self.execute_without_network()
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "Unexpected dev fee recipient")
        self.build.assert_not_awaited()

    async def test_fast_reread_binds_sharing_recipient_before_builder(self) -> None:
        self.buyer.extreme_fast_mode = True
        self.buyer.extreme_fast_token_amount = 1_000_000
        await self.execute_without_network()
        self.assert_updated_sharing_accounts()
        self.curve_read.assert_awaited_once()

    async def test_fast_reread_blocks_arbitrary_recipient_before_builder(self) -> None:
        self.buyer.extreme_fast_mode = True
        self.buyer.extreme_fast_token_amount = 1_000_000
        self.curve_read.return_value["creator"] = str(Pubkey.new_unique())
        result = await self.execute_without_network()
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, "Unexpected dev fee recipient")
        self.build.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
