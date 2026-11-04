"""Strict >1% admission and bounded proof counts using public-data fixtures."""

# Fake providers exercise reads only, without any network or persistent ledger.
# ruff: noqa: ANN201

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_creator_transaction_history as accounts
import test_dev_second_window_source as strict
import test_gmgn_creator_history as indexed
import test_second_block_source as stream
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from monitoring import creator_transaction_history as helius
from monitoring import gmgn_creator_history as gmgn
from monitoring.creator_history_index import curve_address
from trading.second_block_policy import (
    creator_migration_allowed,
    required_migrations,
    validate_second_policy,
)


class ThresholdPolicyTests(unittest.TestCase):
    def test_equality_is_rejected_only_when_strict_operator_is_selected(self):
        policy = {"min_creator_migration_fraction": .01}
        self.assertTrue(creator_migration_allowed({"migration_fraction": .01}, policy))
        policy["creator_migration_strictly_above"] = False
        self.assertTrue(creator_migration_allowed({"migration_fraction": .01}, policy))
        policy["creator_migration_strictly_above"] = True
        for ratio, expected in ((.009999, False), (.01, False), (.010001, True)):
            with self.subTest(ratio=ratio):
                self.assertIs(creator_migration_allowed({"migration_fraction": ratio}, policy), expected)

    def test_missing_or_nonfinite_history_cannot_satisfy_either_comparison(self):
        for strict_operator in (False, True):
            policy = {"min_creator_migration_fraction": .01,
                      "creator_migration_strictly_above": strict_operator}
            self.assertFalse(creator_migration_allowed(None, policy))
            for ratio in (None, True, "0.02", float("nan"), float("inf")):
                with self.subTest(strict=strict_operator, ratio=ratio):
                    self.assertFalse(creator_migration_allowed({"migration_fraction": ratio}, policy))

    def test_proof_count_uses_decimal_floor_plus_one_or_ceiling(self):
        inclusive = {"min_creator_migration_fraction": .01}
        strict_policy = {**inclusive, "creator_migration_strictly_above": True}
        for denominator, inclusive_count, strict_count in ((300, 3, 4), (301, 4, 4),
                                                           (400, 4, 5), (401, 5, 5)):
            with self.subTest(denominator=denominator):
                self.assertEqual(required_migrations(denominator, inclusive), inclusive_count)
                self.assertEqual(required_migrations(denominator, strict_policy), strict_count)
        # Binary floating point puts 7 * .01 above .07; the policy is decimal.
        self.assertEqual(required_migrations(7, {"min_creator_migration_fraction": .01}), 1)
        self.assertEqual(required_migrations(100, {"min_creator_migration_fraction": .07}), 7)
        self.assertEqual(required_migrations(100, {"min_creator_migration_fraction": .07,
                                                  "creator_migration_strictly_above": True}), 8)

    def test_strict_operator_requires_a_boolean_even_while_strategy_is_disabled(self):
        for value in (None, 0, 1, "false", "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_second_policy({"enabled": False, "creator_migration_strictly_above": value})
        validate_second_policy({"enabled": False})
        validate_second_policy({"enabled": False, "creator_migration_strictly_above": True})


class StrictSourceThresholdTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = strict.DevSecondWindowSourceTests.asyncSetUp
    created = strict.DevSecondWindowSourceTests.created
    settle = strict.DevSecondWindowSourceTests.settle

    async def test_exactly_one_percent_never_prepares_or_delivers_a_strict_entry(self):
        self.policy.update(creator_migration_strictly_above=True, require_first_block_activity=False)
        self.source.history.evidence = Mock(return_value={"migration_fraction": .01})
        with patch("monitoring.second_block_source.build_pumpfun_token_info", return_value=self.token) as builder:
            await self.created(stream.trade(self.mint, self.a), user=self.a)
            self.source.on_slot({"slot": 105, "parent": 100})
            await self.settle()
        builder.assert_not_awaited()
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.source.history.observe.assert_called_once_with(str(self.a), str(self.mint))

    async def test_above_one_percent_keeps_dev_initial_buy_and_produced_second_block(self):
        self.policy.update(creator_migration_strictly_above=True, require_first_block_activity=False)
        self.source.history.evidence = Mock(return_value={"migration_fraction": .010001})
        await self.created(stream.trade(self.mint, self.a), user=self.a)
        self.callback.assert_not_awaited()
        self.source.on_slot({"slot": 105, "parent": 100})
        await self.settle()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertTrue(signal["dev_initial_buy_verified"])
        self.assertEqual(signal["entry_block"], 2)
        self.assertEqual(signal["target_slot"], 105)
        self.assertEqual(signal["creator_history"]["migration_fraction"], .010001)

    async def test_missing_flag_preserves_historical_inclusive_source_selection(self):
        self.policy.pop("creator_migration_strictly_above", None)
        self.policy["require_first_block_activity"] = False
        await self.created(stream.trade(self.mint, self.a), user=self.a)
        self.source.on_slot({"slot": 101, "parent": 100})
        await self.settle()
        self.callback.assert_awaited_once()
        self.assertEqual(self.source.signal(str(self.mint))["creator_history"]["migration_fraction"], .01)

    async def test_prepared_entry_is_rejected_if_current_history_drops_to_exactly_one_percent(self):
        self.policy.update(creator_migration_strictly_above=True, require_first_block_activity=False)
        self.source.history.evidence = Mock(return_value={"migration_fraction": .010001})
        await self.created(stream.trade(self.mint, self.a), user=self.a)
        self.assertIsNotNone(self.source.candidates[str(self.mint)].token)
        self.source.history.evidence.return_value = {"migration_fraction": .01}
        self.source.on_slot({"slot": 105, "parent": 100})
        await self.settle()
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.callback.assert_not_awaited()


class ProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.developer = Keypair().pubkey()

    def fixtures(self, count: int) -> None:
        self.mints = sorted(str(Pubkey.new_unique()) for _ in range(count))
        self.proven = {*self.mints[:4], self.mints[50]}
        self.accounts = {}
        self.calls = []
        for mint in self.proven:
            curve, pool = accounts.migration_accounts(self.developer, Pubkey.from_string(mint))
            self.accounts[curve_address(mint)] = curve
            self.accounts[helius.pool_address(mint)[0]] = pool

    async def rpc(self, method: str, params: list) -> object:
        self.calls.append((method, params))
        if method == "getSlot":
            return 100
        if method == "getMultipleAccounts":
            self.assertEqual(params[1]["commitment"], "finalized")
            self.assertEqual(params[1]["minContextSlot"], 100)
            return {"context": {"slot": 100}, "value": [self.accounts.get(address) for address in params[0]]}
        self.assertEqual(method, "getTransactionsForAddress")
        self.assertEqual(params[1]["filters"]["slot"], {"lte": 100})
        return {"data": [accounts.tx(self.developer, Pubkey.from_string(params[0]))], "paginationToken": None}

    async def test_helius_integer_boundary_requires_one_more_proof_without_changing_denominator(self):
        self.fixtures(144)  # 144 finalized launches + 256 live buffer = D400.
        for strict_operator, proofs, batches in ((False, 4, 1), (True, 5, 2)):
            with self.subTest(strict=strict_operator):
                self.calls.clear()
                policy = {**accounts.POLICY, "creator_migration_strictly_above": strict_operator}
                with patch.object(helius, "required_migrations", wraps=required_migrations) as threshold:
                    migrated, complete = await helius.verify_migrations(set(self.mints), self.rpc, policy, 100)
                threshold.assert_called_once_with(400, policy)
                self.assertEqual(len(migrated), proofs)
                self.assertFalse(complete)
                self.assertEqual(len(self.calls), batches)

    async def test_helius_noninteger_boundary_has_same_count_in_both_modes(self):
        self.fixtures(145)  # D401 * 1% = 4.01, so both modes need five proofs.
        for strict_operator in (False, True):
            with self.subTest(strict=strict_operator):
                self.calls.clear()
                policy = {**accounts.POLICY, "creator_migration_strictly_above": strict_operator}
                migrated, complete = await helius.verify_migrations(set(self.mints), self.rpc, policy, 100)
                self.assertEqual(len(migrated), 5)
                self.assertFalse(complete)
                self.assertEqual(len(self.calls), 2)

    async def gmgn_snapshot(self, *, strict_operator: bool):
        claimed = set(self.mints[:51])
        data = {"inner_count": len(self.mints) - len(claimed), "open_count": len(claimed),
                "open_ratio": str(len(claimed) / len(self.mints)),
                "creator_ath_info": {"creator": str(self.developer)},
                "tokens": [indexed.row(mint, migrated=mint in claimed) for mint in self.mints]}
        session = indexed.Session([indexed.Response({"code": 0, "data": data})])
        policy = {**indexed.POLICY, "creator_migration_strictly_above": strict_operator}
        with (patch.dict(os.environ, {"GMGN_API_KEY": "fixture-not-a-real-key"}),
              patch.object(gmgn.aiohttp, "TCPConnector", return_value=SimpleNamespace()),
              patch.object(gmgn.aiohttp, "ClientSession", return_value=session),
              patch.object(gmgn, "required_migrations", wraps=required_migrations) as threshold):
            snapshot = await gmgn.read_gmgn_creator_history(str(self.developer), self.rpc, policy)
        threshold.assert_called_once_with(len(self.mints) + 256, policy)
        self.assertEqual(snapshot.indexed_launch_count, len(self.mints))
        self.assertEqual(snapshot.indexed_extra_launch_count, 0)
        self.assertEqual(snapshot.curves, {curve_address(mint) for mint in self.mints})
        return snapshot

    async def test_gmgn_integer_boundary_requires_one_more_canonical_signed_creation_proof(self):
        self.fixtures(144)
        for strict_operator, proofs, batches in ((False, 4, 1), (True, 5, 2)):
            with self.subTest(strict=strict_operator):
                self.calls.clear()
                snapshot = await self.gmgn_snapshot(strict_operator=strict_operator)
                self.assertEqual(len(snapshot.migrated_curves), proofs)
                self.assertEqual(sum(method == "getMultipleAccounts" for method, _ in self.calls), batches)
                self.assertEqual(sum(method == "getTransactionsForAddress" for method, _ in self.calls), proofs)

    async def test_gmgn_noninteger_boundary_uses_five_proofs_in_both_modes(self):
        self.fixtures(145)
        for strict_operator in (False, True):
            with self.subTest(strict=strict_operator):
                self.calls.clear()
                snapshot = await self.gmgn_snapshot(strict_operator=strict_operator)
                self.assertEqual(len(snapshot.migrated_curves), 5)
                self.assertEqual(sum(method == "getMultipleAccounts" for method, _ in self.calls), 2)


if __name__ == "__main__":
    unittest.main()
