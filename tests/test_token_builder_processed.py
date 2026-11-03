"""Fresh-token preparation reads both accounts promptly without signing or RPC."""

# Test doubles expose the async RPC interface and its independent reads.
# ruff: noqa: ANN001, ARG001

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from solders.pubkey import Pubkey

from interfaces.core import Platform, TokenInfo
from trading.token_builder import build_pumpfun_token_info

MINT = Pubkey.from_string("DBDqhnAi5MjaHsk1JyUonAtUUrmRBJPjJi3GdiBLpump")
CREATOR = Pubkey.from_string("11111111111111111111111111111111")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
CURVE = Pubkey.from_bytes(bytes([2]) * 32)
ASSOCIATED_CURVE = Pubkey.from_bytes(bytes([3]) * 32)
CREATOR_VAULT = Pubkey.from_bytes(bytes([4]) * 32)


class ProcessedTokenPreparationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.state = {
            "complete": False,
            "creator": str(CREATOR),
            "price_per_token": 3e-8,
            "is_mayhem_mode": False,
            "is_cashback_coin": True,
        }
        self.curve_manager = SimpleNamespace(
            get_pool_state=AsyncMock(return_value=self.state),
        )
        self.address_provider = SimpleNamespace(
            derive_pool_address=Mock(return_value=CURVE),
            get_additional_accounts=Mock(return_value={
                "associated_bonding_curve": ASSOCIATED_CURVE,
                "creator_vault": CREATOR_VAULT,
            }),
        )
        self.implementations = SimpleNamespace(
            curve_manager=self.curve_manager, address_provider=self.address_provider,
        )
        self.client = SimpleNamespace(
            get_account_info=AsyncMock(return_value=SimpleNamespace(owner=TOKEN_PROGRAM)),
        )

    async def prepare(self, **kwargs: object) -> TokenInfo | None:
        return await build_pumpfun_token_info(
            str(MINT), self.implementations, self.client,
            symbol="FRESH", name="Fresh token", **kwargs,
        )

    async def test_fresh_mint_missing_at_finalized_is_prepared_from_processed_accounts(self) -> None:
        async def processed_mint(mint, commitment=None) -> SimpleNamespace:
            if commitment != "processed":
                message = "Fresh mint is not available at finalized yet"
                raise ValueError(message)
            self.assertEqual(mint, MINT)
            return SimpleNamespace(owner=TOKEN_PROGRAM)

        self.client.get_account_info.side_effect = processed_mint
        token = await self.prepare()
        self.assertIsInstance(token, TokenInfo)
        self.assertEqual(token.mint, MINT)
        self.assertEqual(token.creator, CREATOR)
        self.assertEqual(token.token_program_id, TOKEN_PROGRAM)
        self.assertEqual(token.platform, Platform.PUMP_FUN)
        self.assertEqual(token.associated_bonding_curve, ASSOCIATED_CURVE)
        self.assertEqual(token.creator_vault, CREATOR_VAULT)
        self.assertTrue(token.is_cashback_coin)
        self.assertEqual(token.additional_data["precio_curva"], self.state["price_per_token"])
        self.assertGreater(token.additional_data["precio_curva_en"], 0)
        self.client.get_account_info.assert_awaited_once_with(MINT, commitment="processed")
        self.curve_manager.get_pool_state.assert_awaited_once_with(
            CURVE, commitment="processed", exigir_precio=False,
        )

    async def test_both_reads_start_before_either_completes(self) -> None:
        curve_started, mint_started = asyncio.Event(), asyncio.Event()

        async def curve_read(*args: object, **kwargs: object) -> dict:
            curve_started.set()
            await mint_started.wait()
            return self.state

        async def mint_read(*args: object, **kwargs: object) -> SimpleNamespace:
            mint_started.set()
            await curve_started.wait()
            return SimpleNamespace(owner=TOKEN_PROGRAM)

        self.curve_manager.get_pool_state.side_effect = curve_read
        self.client.get_account_info.side_effect = mint_read
        token = await asyncio.wait_for(self.prepare(), timeout=2)
        self.assertIsInstance(token, TokenInfo)
        self.curve_manager.get_pool_state.assert_awaited_once()
        self.client.get_account_info.assert_awaited_once()

    async def test_either_read_failure_keeps_the_candidate_unprepared(self) -> None:
        for failed in (self.curve_manager.get_pool_state, self.client.get_account_info):
            with self.subTest(failed=failed):
                failed.side_effect = ValueError("Account unavailable")
                self.assertIsNone(await self.prepare())
                failed.side_effect = None
        self.address_provider.get_additional_accounts.assert_not_called()

    async def test_missing_mint_owner_is_never_guessed(self) -> None:
        for account in (None, SimpleNamespace(), SimpleNamespace(owner=None)):
            with self.subTest(account=account):
                self.client.get_account_info.return_value = account
                self.assertIsNone(await self.prepare())
        self.address_provider.get_additional_accounts.assert_not_called()

    async def test_unreadable_unmigrated_curve_is_still_rejected(self) -> None:
        self.state["price_per_token"] = 0
        self.assertIsNone(await self.prepare())
        self.address_provider.get_additional_accounts.assert_not_called()

    async def test_migrated_curve_remains_excluded_from_normal_preparation(self) -> None:
        self.state.update(complete=True, price_per_token=0)
        self.assertIsNone(await self.prepare())
        self.address_provider.get_additional_accounts.assert_not_called()

    async def test_explicit_migrated_preparation_preserves_existing_routing_flag(self) -> None:
        self.state.update(complete=True, price_per_token=0)
        token = await self.prepare(permitir_migradas=True)
        self.assertIsInstance(token, TokenInfo)
        self.assertTrue(token.additional_data["migrated"])
        self.client.get_account_info.assert_awaited_once_with(MINT, commitment="processed")

    async def test_missing_creator_or_associated_curve_still_fails_closed(self) -> None:
        self.state["creator"] = None
        self.assertIsNone(await self.prepare())
        self.state["creator"] = str(CREATOR)
        self.address_provider.get_additional_accounts.return_value = {}
        self.assertIsNone(await self.prepare())


if __name__ == "__main__":
    unittest.main()
