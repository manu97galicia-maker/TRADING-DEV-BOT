"""GMGN fallback preserves the indexed denominator and verified migration floor."""

# Synthetic snapshots and mocked read providers never construct a trading runtime.
# ruff: noqa: ARG001

import asyncio
import copy
import time
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from monitoring.creator_history_index import (
    CreatorHistoryCache,
    CreatorSnapshot,
    HistoryUnavailableError,
    curve_address,
    read_creator_history,
)
from monitoring.gmgn_creator_history import parse_creator_index

GMGN_SOURCE = "gmgn_indexed_launches_verified_pumpswap_pools"
PRIMARY_PATH = "monitoring.creator_transaction_history.read_transaction_history"
FALLBACK_PATH = "monitoring.gmgn_creator_history.read_gmgn_creator_history"
VISIBLE_MIGRATIONS = 2
POLICY = {
    "history_provider": "helius_transactions",
    "history_fallback_provider": "gmgn",
    "history_primary_timeout_seconds": 4,
    "history_timeout_seconds": 12,
    "creator_cache_seconds": 600,
    "history_max_pages": 64,
    "history_page_size": 100,
    "history_max_accounts": 10000,
    "history_max_transactions": 10000,
    "min_creator_migration_fraction": .01,
}


class GmgnCreatorIndexParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.creator = str(Keypair().pubkey())
        self.mints = [str(Pubkey.new_unique()) for _ in range(101)]
        self.data = {
            "inner_count": 113, "open_count": 3, "open_ratio": "0.026",
            "creator_ath_info": {"creator": self.creator},
            "tokens": [{"chain": "sol", "token_address": mint,
                        "is_open": index < VISIBLE_MIGRATIONS, "is_pump": True,
                        "launchpad_platform": "Pump.fun"}
                       for index, mint in enumerate(self.mints)],
        }

    def test_rounded_ratio_and_101_rows_keep_the_global_116_count(self) -> None:
        total, reported, known, candidates = parse_creator_index(self.data, self.creator, POLICY)
        self.assertEqual((total, reported), (116, 3))
        self.assertEqual(known, set(self.mints))
        self.assertEqual(candidates, set(self.mints[:2]))

    def test_invalid_counts_and_inconsistent_rounded_ratios_are_rejected(self) -> None:
        for field, value in (
            ("inner_count", True), ("inner_count", -1), ("open_count", 1.5),
            ("open_count", None), ("open_ratio", float("nan")),
            ("open_ratio", float("inf")), ("open_ratio", "0.5"), ("open_ratio", None),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(HistoryUnavailableError):
                parse_creator_index({**self.data, field: value}, self.creator, POLICY)

    def test_wrong_wallet_cross_chain_and_untyped_migration_flags_are_rejected(self) -> None:
        wrong_wallet = {**self.data, "creator_ath_info": {"creator": str(Keypair().pubkey())}}
        for field, value in (("chain", "bsc"), ("is_open", "true")):
            data = copy.deepcopy(self.data)
            data["tokens"][0][field] = value
            with self.subTest(field=field), self.assertRaises(HistoryUnavailableError):
                parse_creator_index(data, self.creator, POLICY)
        with self.assertRaises(HistoryUnavailableError):
            parse_creator_index(wrong_wallet, self.creator, POLICY)

    def test_duplicate_rows_or_more_open_rows_than_the_aggregate_are_rejected(self) -> None:
        duplicate = copy.deepcopy(self.data)
        duplicate["tokens"].append(copy.deepcopy(duplicate["tokens"][0]))
        with self.assertRaises(HistoryUnavailableError):
            parse_creator_index(duplicate, self.creator, POLICY)
        contradictory = copy.deepcopy(self.data)
        for row in contradictory["tokens"][:4]:
            row["is_open"] = True
        with self.assertRaises(HistoryUnavailableError):
            parse_creator_index(contradictory, self.creator, POLICY)

    def test_nonpump_open_tokens_do_not_become_pumpswap_proof_candidates(self) -> None:
        self.data["tokens"][0]["is_pump"] = False
        total, reported, known, candidates = parse_creator_index(self.data, self.creator, POLICY)
        self.assertEqual((total, reported, len(known)), (116, 3, 101))
        self.assertEqual(candidates, {self.mints[1]})

    def test_aggregate_size_bound_and_invalid_token_address_are_enforced(self) -> None:
        with self.assertRaises(HistoryUnavailableError):
            parse_creator_index(self.data, self.creator, {**POLICY, "history_max_accounts": 100})
        self.data["tokens"][0]["token_address"] = "not-a-solana-pubkey"  # noqa: S105 -- invalid public address fixture
        with self.assertRaises(HistoryUnavailableError):
            parse_creator_index(self.data, self.creator, POLICY)


class GmgnIndexedEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.creator = str(Keypair().pubkey())
        self.current = str(Pubkey.new_unique())
        self.prior = [str(Pubkey.new_unique()) for _ in range(100)]
        self.cache = CreatorHistoryCache(AsyncMock(), POLICY)
        self.cache.observe(self.creator, self.current)
        # The aggregate index does not expose a chain slot. A live source may
        # already have a much newer slot without invalidating fresh index data.
        self.cache.coverage_start_slot = 1000
        self.snapshot = CreatorSnapshot(
            self.creator,
            {curve_address(mint) for mint in [self.current, *self.prior]},
            {curve_address(mint) for mint in self.prior[:2]},
            0,
            time.time(),
            source=GMGN_SOURCE,
            migration_count_complete=False,
            indexed_launch_count=116,
            indexed_reported_migrations=3,
        )
        self.cache.snapshots[self.creator] = self.snapshot

    def evidence(self) -> dict | None:
        return self.cache.evidence(self.creator, self.current)

    async def test_global_116_count_is_used_instead_of_the_101_returned_rows(self) -> None:
        evidence = self.evidence()
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["launches"], 115)  # Exclude the listed current mint.
        self.assertEqual(evidence["migrations"], 2)  # GMGN reports 3; only 2 were proved.
        self.assertAlmostEqual(evidence["migration_fraction"], 2 / 115)
        self.assertNotAlmostEqual(evidence["migration_fraction"], 3 / 100)
        self.assertEqual(evidence["source"], GMGN_SOURCE)
        self.assertIsNone(evidence["slot"])
        self.assertTrue(evidence["migration_fraction_is_lower_bound"])

    async def test_unlisted_current_mint_is_not_assumed_to_be_inside_the_global_count(self) -> None:
        self.snapshot.curves.remove(curve_address(self.current))
        evidence = self.evidence()
        self.assertEqual(evidence["launches"], 116)
        self.assertAlmostEqual(evidence["migration_fraction"], 2 / 116)

    async def test_new_live_launches_increase_the_denominator_but_known_rows_do_not(self) -> None:
        self.cache.observe(self.creator, self.prior[0])
        self.assertEqual(self.evidence()["launches"], 115)
        new_live_mint = str(Pubkey.new_unique())
        self.cache.observe(self.creator, new_live_mint)
        self.assertEqual(self.evidence()["launches"], 116)
        self.assertAlmostEqual(self.evidence()["migration_fraction"], 2 / 116)

    async def test_new_migration_from_second_index_read_remains_in_denominator_after_refresh_prunes_it(self) -> None:
        new_migration = str(Pubkey.new_unique())
        self.snapshot.indexed_launch_count = 100
        self.snapshot.indexed_reported_migrations = 1
        self.snapshot.indexed_extra_launch_count = 1
        # The first response's list was truncated; a second response introduces
        # a new migration that was not included in the original global count.
        self.snapshot.curves = {curve_address(mint) for mint in [*self.prior[:99], new_migration]}
        self.snapshot.migrated_curves = {curve_address(new_migration)}
        self.cache.observe(self.creator, new_migration)
        with patch("monitoring.creator_history_index.read_creator_history", new=AsyncMock(return_value=self.snapshot)):
            await self.cache.refresh(self.creator)
        self.assertEqual(self.cache.observed[self.creator], {curve_address(self.current)})
        evidence = self.evidence()
        self.assertEqual(evidence["launches"], 101)
        self.assertEqual(evidence["migrations"], 1)
        self.assertAlmostEqual(evidence["migration_fraction"], 1 / 101)
        self.assertLess(evidence["migration_fraction"], POLICY["min_creator_migration_fraction"])
        self.assertEqual(evidence["indexed_extra_launches"], 1)

    async def test_two_second_read_additions_preserve_current_exclusion_without_false_one_percent(self) -> None:
        extra_a, extra_b = (str(Pubkey.new_unique()) for _ in range(2))
        self.snapshot.indexed_launch_count = 100
        self.snapshot.indexed_reported_migrations = 3
        self.snapshot.indexed_extra_launch_count = 2
        self.snapshot.curves = {
            curve_address(mint) for mint in [self.current, *self.prior[:98], extra_a, extra_b]
        }
        self.snapshot.migrated_curves = {curve_address(extra_a)}
        self.cache.observe(self.creator, extra_a)
        self.cache.observe(self.creator, extra_b)
        with patch("monitoring.creator_history_index.read_creator_history", new=AsyncMock(return_value=self.snapshot)):
            await self.cache.refresh(self.creator)
        evidence = self.evidence()
        self.assertEqual(evidence["launches"], 101)  # 100 base + 2 extra - listed current.
        self.assertAlmostEqual(evidence["migration_fraction"], 1 / 101)
        self.assertLess(evidence["migration_fraction"], POLICY["min_creator_migration_fraction"])
        self.assertEqual(evidence["indexed_extra_launches"], 2)

    async def test_current_token_cannot_inflate_the_verified_migration_numerator(self) -> None:
        self.snapshot.migrated_curves.add(curve_address(self.current))
        evidence = self.evidence()
        self.assertEqual(evidence["migrations"], 2)
        self.assertAlmostEqual(evidence["migration_fraction"], 2 / 115)

    async def test_zero_verified_migrations_remain_zero_despite_gmgn_reported_successes(self) -> None:
        self.snapshot.migrated_curves.clear()
        evidence = self.evidence()
        self.assertEqual(evidence["migrations"], 0)
        self.assertEqual(evidence["migration_fraction"], 0)

    async def test_stale_or_future_index_snapshot_never_proves_history(self) -> None:
        for checked_at in (time.time() - 601, time.time() + 60):
            with self.subTest(checked_at=checked_at):
                self.snapshot.checked_at = checked_at
                self.assertIsNone(self.evidence())

    async def test_invalid_aggregate_counts_cannot_understate_the_denominator(self) -> None:
        for value in (0, -1, True, 1.5, "116", float("nan"), 50):
            with self.subTest(indexed_launch_count=value):
                self.snapshot.indexed_launch_count = value
                self.assertIsNone(self.evidence())

    async def test_inconsistent_reported_migrations_or_unlisted_proofs_are_rejected(self) -> None:
        for value in (-1, True, None, 1, 117):
            with self.subTest(indexed_reported_migrations=value):
                self.snapshot.indexed_reported_migrations = value
                self.assertIsNone(self.evidence())
        self.snapshot.indexed_reported_migrations = 3
        self.snapshot.migrated_curves.add(curve_address(str(Pubkey.new_unique())))
        self.assertIsNone(self.evidence())

    async def test_primary_chain_snapshot_still_requires_live_coverage(self) -> None:
        self.snapshot.indexed_launch_count = None
        self.snapshot.indexed_reported_migrations = None
        self.snapshot.source = "helius_creation_signers_and_canonical_pumpswap_pools"
        self.snapshot.slot = 999
        self.assertIsNone(self.evidence())
        self.snapshot.slot = 1000
        evidence = self.evidence()
        self.assertEqual(evidence["launches"], 100)
        self.assertEqual(evidence["slot"], 1000)

    async def test_fresh_index_snapshot_is_reused_without_restarting_its_provider(self) -> None:
        with patch("monitoring.creator_history_index.read_creator_history", new=AsyncMock()) as reader:
            self.cache.warm(self.creator)
            await asyncio.sleep(0)
        reader.assert_not_awaited()
        self.assertFalse(self.cache.pending)

    async def test_disconnect_does_not_allow_an_inflight_index_refresh_to_repopulate_cache(self) -> None:
        started, finish = asyncio.Event(), asyncio.Event()

        async def delayed_index(*args: object) -> CreatorSnapshot:
            started.set()
            await finish.wait()
            return self.snapshot

        self.cache.snapshots.clear()
        with patch("monitoring.creator_history_index.read_creator_history", side_effect=delayed_index):
            refresh = asyncio.create_task(self.cache.refresh(self.creator))
            await started.wait()
            self.cache.invalidate()
            finish.set()
            await refresh
        self.assertFalse(self.cache.snapshots)
        self.assertIsNone(self.evidence())


class CreatorProviderFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.creator = str(Keypair().pubkey())
        self.current, self.old = (str(Pubkey.new_unique()) for _ in range(2))
        self.rpc = AsyncMock()
        self.primary = CreatorSnapshot(
            self.creator, {curve_address(self.old)}, set(), 1000, time.time(),
        )
        self.index = CreatorSnapshot(
            self.creator, {curve_address(self.old)}, {curve_address(self.old)},
            0, time.time(), source=GMGN_SOURCE, migration_count_complete=False,
            indexed_launch_count=116, indexed_reported_migrations=3,
        )

    async def test_valid_primary_history_below_one_percent_does_not_trigger_fallback(self) -> None:
        with (
            patch(PRIMARY_PATH, new=AsyncMock(return_value=self.primary)) as primary,
            patch(FALLBACK_PATH, new=AsyncMock(return_value=self.index)) as fallback,
        ):
            result = await read_creator_history(self.creator, self.rpc, POLICY)
        self.assertIs(result, self.primary)
        primary.assert_awaited_once_with(self.creator, self.rpc, POLICY)
        fallback.assert_not_awaited()
        self.rpc.assert_not_awaited()

    async def test_unavailable_primary_uses_indexed_provider_without_reclassifying_unknown_as_zero(self) -> None:
        errors = (
            HistoryUnavailableError("Incomplete history"),
            RuntimeError("Read RPC unavailable"),
            ValueError("Invalid history"),
            aiohttp.ClientError("Transport unavailable"),
        )
        for error in errors:
            with (
                self.subTest(error=type(error).__name__),
                patch(PRIMARY_PATH, new=AsyncMock(side_effect=error)),
                patch(FALLBACK_PATH, new=AsyncMock(return_value=self.index)) as fallback,
            ):
                result = await read_creator_history(self.creator, self.rpc, POLICY)
                self.assertIs(result, self.index)
                fallback.assert_awaited_once_with(self.creator, self.rpc, POLICY)
        self.rpc.assert_not_awaited()

    async def test_primary_timeout_cancels_its_work_and_then_uses_fallback(self) -> None:
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def unresponsive_primary(*args: object) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        policy = {**POLICY, "history_primary_timeout_seconds": .01}
        with (
            patch(PRIMARY_PATH, side_effect=unresponsive_primary),
            patch(FALLBACK_PATH, new=AsyncMock(return_value=self.index)) as fallback,
        ):
            result = await asyncio.wait_for(read_creator_history(self.creator, self.rpc, policy), timeout=1)
        self.assertTrue(started.is_set())
        self.assertTrue(cancelled.is_set())
        self.assertIs(result, self.index)
        fallback.assert_awaited_once()

    async def test_without_opt_in_the_existing_primary_failure_is_preserved(self) -> None:
        policy = {key: value for key, value in POLICY.items() if key not in {
            "history_fallback_provider", "history_primary_timeout_seconds",
        }}
        with (
            patch(PRIMARY_PATH, new=AsyncMock(side_effect=HistoryUnavailableError("Missing history"))),
            patch(FALLBACK_PATH, new=AsyncMock()) as fallback,
            self.assertRaises(HistoryUnavailableError),
        ):
            await read_creator_history(self.creator, self.rpc, policy)
        fallback.assert_not_awaited()

    async def test_primary_cancellation_propagates_without_starting_fallback(self) -> None:
        with (
            patch(PRIMARY_PATH, new=AsyncMock(side_effect=asyncio.CancelledError)),
            patch(FALLBACK_PATH, new=AsyncMock()) as fallback,
            self.assertRaises(asyncio.CancelledError),
        ):
            await read_creator_history(self.creator, self.rpc, POLICY)
        fallback.assert_not_awaited()

    async def test_fallback_cancellation_also_propagates(self) -> None:
        with (
            patch(PRIMARY_PATH, new=AsyncMock(side_effect=HistoryUnavailableError("Missing history"))),
            patch(FALLBACK_PATH, new=AsyncMock(side_effect=asyncio.CancelledError)),
            self.assertRaises(asyncio.CancelledError),
        ):
            await read_creator_history(self.creator, self.rpc, POLICY)

    async def test_both_unavailable_providers_leave_no_snapshot_or_positive_evidence(self) -> None:
        cache = CreatorHistoryCache(self.rpc, POLICY)
        cache.observe(self.creator, self.current)
        with (
            patch(PRIMARY_PATH, new=AsyncMock(side_effect=HistoryUnavailableError("Incomplete history"))),
            patch(FALLBACK_PATH, new=AsyncMock(side_effect=HistoryUnavailableError("Index unavailable"))),
        ):
            await cache.refresh(self.creator)
        self.assertFalse(cache.snapshots)
        self.assertIsNone(cache.evidence(self.creator, self.current))
        self.assertIsNotNone(cache.last_error)
        self.rpc.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
