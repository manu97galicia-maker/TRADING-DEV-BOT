"""Fee and pre-send receipt integration using local signed dummy transactions."""

# Explicit small fake limits and local request helpers exercise fee boundaries.
# ruff: noqa: ARG001

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from core.client import SolanaClient
from trading.jupiter_seller import _construir
from trading.transaction_observer import OBSERVER, PRIORITY_CAP


class FeeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_fee_uses_actual_cu_limit_and_persists_before_send(self) -> None:
        events = []
        signer = Keypair()

        async def send(transaction: object, options: object) -> object:
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][0], "signed")
            instructions = transaction.message.instructions
            rate = next(
                int.from_bytes(bytes(i.data)[1:], "little")
                for i in instructions
                if bytes(i.data)[:1] == b"\x03"
            )
            self.assertLessEqual((rate * 200000 + 999999) // 1000000, 1000)
            return SimpleNamespace(value="dummy-signature")

        rpc = SimpleNamespace(send_transaction=AsyncMock(side_effect=send))
        client = SimpleNamespace(
            get_client=AsyncMock(return_value=rpc),
            get_cached_blockhash=AsyncMock(return_value=Hash.default()),
            _rate_limiter=SimpleNamespace(acquire=AsyncMock()),
        )
        cap_token = PRIORITY_CAP.set(1000)
        observer_token = OBSERVER.set(
            lambda event, signature: events.append((event, signature))
        )
        try:
            result = await SolanaClient.build_and_send_transaction(
                client,
                [],
                signer,
                skip_preflight=False,
                max_retries=1,
                priority_fee=1000000,
                compute_unit_limit=200000,
            )
        finally:
            PRIORITY_CAP.reset(cap_token)
            OBSERVER.reset(observer_token)
        self.assertEqual(result, "dummy-signature")

    async def test_receipt_write_failure_prevents_network_send(self) -> None:
        rpc = SimpleNamespace(send_transaction=AsyncMock())
        client = SimpleNamespace(
            get_client=AsyncMock(return_value=rpc),
            get_cached_blockhash=AsyncMock(return_value=Hash.default()),
            _rate_limiter=SimpleNamespace(acquire=AsyncMock()),
        )

        def refuse(event: str, signature: str) -> None:
            message = "dummy ledger unavailable"
            raise OSError(message)

        observer_token = OBSERVER.set(refuse)
        try:
            with self.assertRaises(OSError):
                await SolanaClient.build_and_send_transaction(
                    client, [], Keypair(), max_retries=1
                )
        finally:
            OBSERVER.reset(observer_token)
        rpc.send_transaction.assert_not_awaited()

    async def build_jupiter(self, rate: int, cap: int) -> tuple:
        signer = Keypair()
        message = MessageV0.try_compile(
            signer.pubkey(),
            [set_compute_unit_limit(200000), set_compute_unit_price(rate)],
            [],
            Hash.default(),
        )
        raw = bytes(VersionedTransaction(message, [signer]))
        response = SimpleNamespace(
            json=AsyncMock(
                return_value={"swapTransaction": base64.b64encode(raw).decode()}
            )
        )

        class Request:
            async def __aenter__(self) -> object:
                return response

            async def __aexit__(self, *args: object) -> None:
                return None

        bodies = []

        def post(url: str, *, json: dict, headers: dict) -> Request:
            bodies.append(json)
            return Request()

        session = SimpleNamespace(post=post)
        cap_token = PRIORITY_CAP.set(cap)
        try:
            with patch("trading.jupiter_seller.wait_jupiter_slot", AsyncMock()):
                result = await _construir(session, {}, signer)
        finally:
            PRIORITY_CAP.reset(cap_token)
        return result, bodies

    async def test_routed_exit_requests_and_checks_the_same_cap(self) -> None:
        result, bodies = await self.build_jupiter(5000, 1000)
        self.assertIsNotNone(result)
        self.assertEqual(bodies[0]["prioritizationFeeLamports"], 1000)

    async def test_overpriced_routed_transaction_is_rejected_before_signing(
        self,
    ) -> None:
        result, _ = await self.build_jupiter(5001, 1000)
        self.assertIsNone(result)

    async def test_stale_reference_zero_priority_does_not_select_dynamic_fees(
        self,
    ) -> None:
        result, bodies = await self.build_jupiter(0, 0)
        self.assertIsNotNone(result)
        self.assertEqual(bodies[0]["prioritizationFeeLamports"], 0)


if __name__ == "__main__":
    unittest.main()
