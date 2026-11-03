"""Helius history and signer identity fixtures; no credentials or live orders."""

# ruff: noqa: ANN001, ANN201, ANN202

import base64
import copy
import unittest
from unittest.mock import AsyncMock

import test_creator_history_index as account_fixtures
import test_second_block_source as log_fixtures
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from monitoring.creator_history_index import (
    AMM,
    POOL_DISC,
    PUMP,
    WSOL,
    HistoryUnavailableError,
    curve_address,
    read_creator_history,
)
from monitoring.creator_transaction_history import (
    creation_mints,
    migration_proven,
    pool_address,
    verify_migrations,
)
from monitoring.pump_event_logs import IncompletePumpLogsError, pump_events

POLICY = {"history_provider": "helius_transactions", "history_page_size": 100,
          "history_max_pages": 4, "history_max_transactions": 1000,
          "history_max_accounts": 1000, "min_creator_migration_fraction": .01}


def tx(developer, mint, *, fee_creator=None, slot=90):
    return {"slot": slot, "transactionIndex": 0,
        "transaction": {"signatures": [f"signature-{slot}"], "message": {
            "accountKeys": [str(developer), str(PUMP)], "header": {"numRequiredSignatures": 1}}},
        "meta": {"err": None, "logMessages": log_fixtures.logs(log_fixtures.create(
            mint, fee_creator or developer, user=developer))}}


def migration_accounts(developer, mint):
    curve = account_fixtures.curve(developer, mint, complete=True)["account"]
    address, authority = pool_address(str(mint))
    raw = bytearray(243)
    raw[:8], raw[11:43], raw[43:75] = POOL_DISC, bytes(authority), bytes(mint)
    raw[75:107], raw[211:243] = bytes(WSOL), bytes(developer)
    pool = account_fixtures.row(address, AMM, raw)["account"]
    return curve, pool


class TransactionHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.developer, self.fee = Keypair().pubkey(), Keypair().pubkey()
        self.mint, self.other = Pubkey.new_unique(), Pubkey.new_unique()

    async def test_full_pagination_uses_signer_and_verifies_real_pool_with_different_fee_recipient(self):
        pages = [{"data": [], "paginationToken": "empty-page"},
                 {"data": [tx(self.developer, self.mint, fee_creator=self.fee)], "paginationToken": "next-page"},
                 {"data": [tx(self.developer, self.other, fee_creator=self.fee, slot=80)], "paginationToken": None}]
        curve, pool = migration_accounts(self.fee, self.mint)
        addresses = {curve_address(str(self.mint)): curve, pool_address(str(self.mint))[0]: pool}
        async def rpc(method, params):
            if method == "getSlot":
                return 100
            if method == "getTransactionsForAddress":
                self.assertEqual(params[0], str(self.developer))
                self.assertEqual(params[1]["filters"]["slot"], {"lte": 100})
                self.assertEqual(params[1]["commitment"], "finalized")
                return pages.pop(0)
            self.assertEqual(method, "getMultipleAccounts")
            return {"context": {"slot": 100}, "value": [addresses.get(a) for a in params[0]]}
        snapshot = await read_creator_history(str(self.developer), rpc, POLICY)
        self.assertEqual(snapshot.curves, {curve_address(str(self.mint)), curve_address(str(self.other))})
        self.assertEqual(snapshot.migrated_curves, {curve_address(str(self.mint))})
        self.assertEqual(snapshot.source, "helius_creation_signers_and_canonical_pumpswap_pools")
        self.assertTrue(snapshot.migration_count_complete)
        self.assertFalse(pages)

    async def test_partial_cyclic_or_missing_cursor_never_proves_a_denominator(self):
        cases = [[{"data": [], "paginationToken": "same"}]*4,
                 [{"data": []}],
                 [{"data": [], "paginationToken": str(i)} for i in range(4)]]
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaises(HistoryUnavailableError):
                await read_creator_history(str(self.developer), AsyncMock(side_effect=[100, *pages]), POLICY)

    def test_fee_recipient_does_not_receive_credit_for_another_developers_launch(self):
        transaction = tx(self.fee, self.mint, fee_creator=self.developer)
        transaction["transaction"]["message"]["accountKeys"].append(str(self.developer))
        self.assertFalse(creation_mints(transaction, str(self.developer)))

    async def test_missing_logs_signer_and_duplicate_transaction_are_rejected(self):
        good = tx(self.developer, self.mint)
        missing_logs = copy.deepcopy(good)
        missing_logs["meta"]["logMessages"] = None
        unsigned = copy.deepcopy(good)
        unsigned["transaction"]["message"]["accountKeys"] = [str(self.fee), str(self.developer), str(PUMP)]
        pages = [{"data": [missing_logs], "paginationToken": None},
                 {"data": [unsigned], "paginationToken": None},
                 {"data": [good, good], "paginationToken": None}]
        for page in pages:
            with self.subTest(page=page), self.assertRaises(HistoryUnavailableError):
                await read_creator_history(str(self.developer), AsyncMock(side_effect=[100, page]), POLICY)

    def test_curve_graduation_without_a_canonical_pool_is_not_a_migration(self):
        curve, pool = migration_accounts(self.developer, self.mint)
        self.assertFalse(migration_proven(str(self.mint), curve, None))
        self.assertTrue(migration_proven(str(self.mint), curve, pool))
        pool["owner"] = str(PUMP)
        with self.assertRaises(HistoryUnavailableError):
            migration_proven(str(self.mint), curve, pool)

    async def test_partial_migration_verification_remains_a_lower_bound_with_full_launch_denominator(self):
        mints = {str(Pubkey.new_unique()) for _ in range(60)}
        accounts = {}
        for mint in mints:
            curve, pool = migration_accounts(self.developer, Pubkey.from_string(mint))
            accounts[curve_address(mint)] = curve
            accounts[pool_address(mint)[0]] = pool
        async def rpc(_method, params):
            return {"context": {"slot": 100}, "value": [accounts[a] for a in params[0]]}
        migrated, complete = await verify_migrations(mints, rpc, POLICY, 100)
        self.assertFalse(complete)
        self.assertEqual(len(migrated), 50)
        self.assertEqual(len(mints), 60)

    async def test_short_account_batch_and_older_account_context_are_rejected(self):
        for result in [{"context": {"slot": 100}, "value": []},
                       {"context": {"slot": 99}, "value": [None, None]}]:
            with self.subTest(result=result), self.assertRaises(HistoryUnavailableError):
                await verify_migrations({str(self.mint)}, AsyncMock(return_value=result), POLICY, 100)


class InvocationTests(unittest.TestCase):
    def test_rolled_back_child_events_cannot_inflate_a_developer_history(self):
        wrapper, wallet, mint = Pubkey.new_unique(), Keypair().pubkey(), Pubkey.new_unique()
        event = log_fixtures.create(mint, wallet)
        logs = [f"Program {wrapper} invoke [1]", f"Program {PUMP} invoke [2]",
                "Program data: "+base64.b64encode(event).decode(),
                f"Program {PUMP} failed: custom program error: 0x1", f"Program {wrapper} success"]
        self.assertFalse(pump_events(logs, strict=True))

    def test_truncated_history_is_unknown_rather_than_zero_creations(self):
        with self.assertRaises(IncompletePumpLogsError):
            pump_events([f"Program {PUMP} invoke [1]", "Log truncated"], strict=True)
