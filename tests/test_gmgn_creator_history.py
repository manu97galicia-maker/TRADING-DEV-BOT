"""GMGN's capped index supplies counts; unsigned RPC fixtures prove successes."""

# Fakes intentionally match the asynchronous HTTP and public-chain interfaces.
# ruff: noqa: ANN001, ANN201, SLF001

import asyncio
import copy
import os
import unittest
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_creator_transaction_history as fixtures
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from monitoring import gmgn_creator_history as gmgn
from monitoring.creator_history_index import (
    CreatorHistoryCache,
    HistoryUnavailableError,
    curve_address,
)
from monitoring.creator_transaction_history import pool_address

POLICY = {**fixtures.POLICY, "history_max_accounts": 10000}


def row(mint, *, migrated=False, pump=True):
    return {"token_address": str(mint), "chain": "sol", "is_open": migrated,
            "is_pump": pump, "launchpad_platform": "Pump.fun"}


class Response:
    def __init__(self, data, status=200) -> None:
        self.data, self.status = data, status

    async def __aenter__(self) -> "Response":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def json(self, **_kwargs: object):
        return copy.deepcopy(self.data)


class Session:
    def __init__(self, responses) -> None:
        self.responses, self.requests = list(responses), []

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    def get(self, url, **kwargs: object):
        self.requests.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class GmgnCreatorProofTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.developer, self.other = Keypair().pubkey(), Keypair().pubkey()
        self.mint, self.current = Pubkey.new_unique(), Pubkey.new_unique()
        self.accounts = {}
        self.transactions = {}
        self.calls = []
        self.add_migration(self.mint)
        self.index = {"inner_count": 9, "open_count": 1, "open_ratio": "0.1",
                      "creator_ath_info": {"creator": str(self.developer)},
                      "tokens": [row(self.mint, migrated=True), row(self.current)]}
        gmgn._RATE_LIMIT_STATE.update(until=0, next_at=0)
        self.addCleanup(gmgn._RATE_LIMIT_STATE.update, until=0, next_at=0)

    def add_migration(self, mint):
        curve, pool = fixtures.migration_accounts(self.developer, mint)
        self.accounts[curve_address(str(mint))] = curve
        self.accounts[pool_address(str(mint))[0]] = pool
        self.transactions[str(mint)] = [fixtures.tx(self.developer, mint, slot=90)]

    async def rpc(self, method, params):
        self.calls.append((method, params))
        if method == "getSlot":
            self.assertEqual(params, [{"commitment": "finalized"}])
            return 100
        if method == "getMultipleAccounts":
            self.assertEqual(params[1]["commitment"], "finalized")
            self.assertEqual(params[1]["minContextSlot"], 100)
            return {"context": {"slot": 100}, "value": [self.accounts.get(address) for address in params[0]]}
        self.assertEqual(method, "getTransactionsForAddress")
        self.assertEqual(params[1]["sortOrder"], "asc")
        self.assertEqual(params[1]["transactionDetails"], "full")
        self.assertEqual(params[1]["limit"], 10)
        self.assertEqual(params[1]["filters"]["slot"], {"lte": 100})
        return {"data": self.transactions[params[0]], "paginationToken": None}

    async def read(self, responses=None, rpc=None):
        responses = responses or [Response({"code": 0, "data": self.index})]
        session = Session(responses)
        with (
            patch.dict(os.environ, {"GMGN_API_KEY": "test-secret-api-key"}),
            patch.object(gmgn.aiohttp, "TCPConnector", return_value=SimpleNamespace()),
            patch.object(gmgn.aiohttp, "ClientSession", return_value=session),
        ):
            result = await gmgn.read_gmgn_creator_history(str(self.developer), rpc or self.rpc, POLICY)
        for url, kwargs in session.requests:
            self.assertEqual(url, gmgn.GMGN_URL)
            self.assertEqual(kwargs["params"]["chain"], "sol")
            self.assertEqual(kwargs["params"]["wallet_address"], str(self.developer))
            self.assertIn("timestamp", kwargs["params"])
            self.assertIn("client_id", kwargs["params"])
            self.assertEqual(kwargs["headers"]["X-APIKEY"], "test-secret-api-key")
            self.assertNotIn("X-Signature", kwargs["headers"])
            self.assertFalse(kwargs["allow_redirects"])
        return result, session

    async def test_canonical_pool_and_real_creation_signer_form_a_labeled_snapshot(self):
        snapshot, session = await self.read()
        self.assertEqual(snapshot.creator, str(self.developer))
        self.assertEqual(snapshot.indexed_launch_count, 10)
        self.assertEqual(snapshot.indexed_reported_migrations, 1)
        self.assertEqual(snapshot.indexed_extra_launch_count, 0)
        self.assertEqual(snapshot.slot, 0)  # An HTTP index has no observed chain slot.
        self.assertEqual(snapshot.source, "gmgn_indexed_launches_verified_pumpswap_pools")
        self.assertFalse(snapshot.migration_count_complete)
        self.assertEqual(snapshot.migrated_curves, {curve_address(str(self.mint))})
        self.assertTrue(snapshot.migrated_curves <= snapshot.curves)
        self.assertEqual(len(session.requests), 1)

    async def test_second_filtered_request_keeps_the_first_global_denominator(self):
        third = Pubkey.new_unique()
        self.add_migration(third)
        known = [self.mint, *[Pubkey.new_unique() for _ in range(100)]]
        self.index.update(inner_count=113, open_count=3, open_ratio="0.026",
                          tokens=[row(mint, migrated=mint == self.mint) for mint in known])
        filtered = {"inner_count": 0, "open_count": 9999, "open_ratio": "invalid-and-ignored",
                    "tokens": [row(third, migrated=True)]}
        snapshot, session = await self.read([
            Response({"code": 0, "data": self.index}), Response({"code": 0, "data": filtered}),
        ])
        self.assertEqual(snapshot.indexed_launch_count, 116)
        self.assertEqual(snapshot.indexed_reported_migrations, 3)
        self.assertEqual(snapshot.indexed_extra_launch_count, 1)
        self.assertEqual(len(snapshot.curves), 102)
        self.assertEqual(snapshot.migrated_curves, {curve_address(str(self.mint)), curve_address(str(third))})
        self.assertNotIn("migrate_state", session.requests[0][1]["params"])
        self.assertEqual(session.requests[1][1]["params"]["migrate_state"], "migrated")

    async def test_new_mint_from_second_read_cannot_inflate_the_one_percent_boundary(self):
        # The first global count may not include the graduated token returned
        # moments later. Counting its proof against only 100 would falsely pass.
        self.index.update(inner_count=99, open_count=1, open_ratio="0.01",
                          tokens=[row(self.current)])
        filtered = {"tokens": [row(self.mint, migrated=True)]}
        snapshot, _session = await self.read([
            Response({"code": 0, "data": self.index}), Response({"code": 0, "data": filtered}),
        ])
        self.assertEqual(snapshot.indexed_launch_count, 100)
        self.assertEqual(snapshot.indexed_extra_launch_count, 1)
        self.assertEqual(snapshot.migrated_curves, {curve_address(str(self.mint))})
        candidate = str(Pubkey.new_unique())
        cache = CreatorHistoryCache(AsyncMock(), {**POLICY, "creator_cache_seconds": 600})
        cache.observe(str(self.developer), candidate)
        cache.snapshots[str(self.developer)] = snapshot
        evidence = cache.evidence(str(self.developer), candidate)
        self.assertEqual(evidence["launches"], 101)
        self.assertEqual(evidence["migrations"], 1)
        self.assertLess(evidence["migration_fraction"], POLICY["min_creator_migration_fraction"])

    async def test_fee_recipient_is_not_credited_with_another_signers_launch(self):
        self.transactions[str(self.mint)] = [fixtures.tx(self.other, self.mint, fee_creator=self.developer)]
        snapshot, _session = await self.read()
        self.assertFalse(snapshot.migrated_curves)

    async def test_missing_pool_never_counts_as_verified_migration(self):
        self.accounts.pop(pool_address(str(self.mint))[0])
        snapshot, _session = await self.read()
        self.assertFalse(snapshot.migrated_curves)
        self.assertFalse(any(method == "getTransactionsForAddress" for method, _ in self.calls))

    async def test_unrelated_truncated_transaction_does_not_hide_a_complete_creation(self):
        truncated = copy.deepcopy(self.transactions[str(self.mint)][0])
        truncated["meta"]["logMessages"] = ["Log truncated"]
        self.transactions[str(self.mint)].insert(0, truncated)
        snapshot, _session = await self.read()
        self.assertEqual(snapshot.migrated_curves, {curve_address(str(self.mint))})

    async def test_truncated_creation_itself_never_proves_the_signer(self):
        self.transactions[str(self.mint)][0]["meta"]["logMessages"].append("Log truncated")
        snapshot, _session = await self.read()
        self.assertFalse(snapshot.migrated_curves)

    async def test_wrong_canonical_pool_owner_fails_closed(self):
        self.accounts[pool_address(str(self.mint))[0]]["owner"] = str(self.other)
        with self.assertRaises(HistoryUnavailableError):
            await self.read()

    async def test_without_api_key_no_http_or_chain_request_is_issued(self):
        rpc = AsyncMock()
        with (
            patch.dict(os.environ, {"GMGN_API_KEY": ""}),
            patch.object(gmgn.aiohttp, "ClientSession") as session,
            self.assertRaises(HistoryUnavailableError),
        ):
            await gmgn.read_gmgn_creator_history(str(self.developer), rpc, POLICY)
        session.assert_not_called()
        rpc.assert_not_awaited()

    async def test_auth_rate_limit_and_transport_errors_do_not_expose_secrets(self):
        for response in (
            Response({"code": 401, "message": "test-secret-api-key"}, 401),
            Response({"code": 429, "message": "test-secret-api-key"}, 429),
            RuntimeError("https://api.invalid?api-key=test-secret-api-key"),
        ):
            with self.subTest(response=response):
                gmgn._RATE_LIMIT_STATE["until"] = 0
                with self.assertRaises(HistoryUnavailableError) as caught:
                    await self.read([response])
                self.assertNotIn("test-secret", str(caught.exception))
                self.assertNotIn("https://", str(caught.exception))
        gmgn._RATE_LIMIT_STATE["until"] = 0
        rpc = AsyncMock(side_effect=RuntimeError("https://rpc.invalid?api-key=test-secret-api-key"))
        with self.assertRaises(HistoryUnavailableError) as caught:
            await self.read(rpc=rpc)
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))

    async def test_rate_limit_cooldown_prevents_another_http_request(self):
        session = Session([Response({}, 429)])
        with self.assertRaises(HistoryUnavailableError):
            await gmgn._fetch_index(session, str(self.developer), "test-secret-api-key")
        with self.assertRaises(HistoryUnavailableError):
            await gmgn._fetch_index(session, str(self.developer), "test-secret-api-key")
        self.assertEqual(len(session.requests), 1)

    async def test_concurrent_reads_are_paced_without_compressing_waiting_requests(self):
        clock, starts, waits = {"now": 100.0}, [], []
        real_sleep = asyncio.sleep

        async def sleep(delay) -> None:
            waits.append(delay)
            clock["now"] += delay
            await real_sleep(0)

        session = Session([Response({"code": 0, "data": {}}) for _ in range(4)])
        original_get = session.get

        def timed_get(url, **kwargs: object) -> Response:
            starts.append(clock["now"])
            return original_get(url, **kwargs)

        session.get = timed_get
        with (
            patch.object(gmgn.time, "monotonic", side_effect=lambda: clock["now"]),
            patch.object(gmgn.asyncio, "sleep", side_effect=sleep),
        ):
            await asyncio.gather(*(gmgn._fetch_index(session, str(self.developer), "test-key")
                                   for _ in range(4)))
        self.assertEqual(len(starts), 4)
        self.assertTrue(waits)
        for earlier, later in pairwise(starts):
            self.assertGreaterEqual(round(later - earlier, 9), .4)


if __name__ == "__main__":
    unittest.main()
