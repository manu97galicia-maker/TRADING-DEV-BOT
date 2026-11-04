"""A mocked RPC account must authenticate as a Pump curve before decoding."""

from __future__ import annotations

import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from solders.pubkey import Pubkey

from platforms.pumpfun.address_provider import PumpFunAddresses
from platforms.pumpfun.curve_manager import PumpFunCurveManager

DISC = hashlib.sha256(b"account:BondingCurve").digest()[:8]


class PumpCurveIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.pool = Pubkey.new_unique()
        self.account = SimpleNamespace(owner=PumpFunAddresses.PROGRAM, executable=False, data=DISC+b"payload")
        self.client = SimpleNamespace(get_account_info=AsyncMock(return_value=self.account))
        self.manager = PumpFunCurveManager(self.client, Mock())
        self.decoded = {"creator": "authenticated fee recipient", "price_per_token": 1e-7}
        self.manager._decode_curve_state_with_idl = Mock(return_value=self.decoded)  # noqa: SLF001

    async def test_authenticates_owner_and_discriminator_from_the_existing_read(self) -> None:
        result = await self.manager.get_pool_state(self.pool, commitment="processed", exigir_precio=False)
        self.assertIs(result, self.decoded)
        self.client.get_account_info.assert_awaited_once_with(self.pool, commitment="processed")
        self.manager._decode_curve_state_with_idl.assert_called_once_with(self.account.data, exigir_precio=False)  # noqa: SLF001

    async def test_wrong_or_missing_program_owner_is_rejected_before_decode(self) -> None:
        for owner in (None, Pubkey.new_unique(), "not-a-public-key"):
            self.account.owner = owner
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                await self.manager.get_pool_state(self.pool)
        del self.account.owner
        with self.assertRaises(ValueError):
            await self.manager.get_pool_state(self.pool)
        self.manager._decode_curve_state_with_idl.assert_not_called()  # noqa: SLF001

    async def test_executable_or_invalid_execution_flag_is_rejected(self) -> None:
        for value in (True, 0, 1, None, "false"):
            self.account.executable = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.manager.get_pool_state(self.pool)
        self.manager._decode_curve_state_with_idl.assert_not_called()  # noqa: SLF001

    async def test_legacy_wrapper_without_executable_attribute_still_authenticates_identity(self) -> None:
        del self.account.executable
        self.assertIs(await self.manager.get_pool_state(self.pool), self.decoded)

    async def test_empty_truncated_or_wrong_discriminator_never_reaches_idl(self) -> None:
        for data in (None, b"", DISC[:7], b"wrongdis"+b"payload", "not-binary"):
            self.account.data = data
            with self.subTest(data=data), self.assertRaises(ValueError):
                await self.manager.get_pool_state(self.pool)
        self.manager._decode_curve_state_with_idl.assert_not_called()  # noqa: SLF001

    async def test_missing_account_is_rejected_without_a_second_rpc(self) -> None:
        self.client.get_account_info.return_value = None
        with self.assertRaises(ValueError):
            await self.manager.get_pool_state(self.pool, commitment="processed")
        self.client.get_account_info.assert_awaited_once_with(self.pool, commitment="processed")
        self.manager._decode_curve_state_with_idl.assert_not_called()  # noqa: SLF001

    async def test_rpc_failure_is_not_replaced_by_untrusted_defaults(self) -> None:
        self.client.get_account_info.side_effect = TimeoutError
        with self.assertRaises(ValueError):
            await self.manager.get_pool_state(self.pool)
        self.manager._decode_curve_state_with_idl.assert_not_called()  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
