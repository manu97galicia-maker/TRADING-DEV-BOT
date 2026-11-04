"""Synthetic receipts only: these tests never connect, sign or trade real funds."""

# ruff: noqa: ANN001, ANN201

import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import yaml

from inspect_five_trade_evaluation import inspect
from trading.five_trade_evaluation import STRATEGIES
from trading.four_strategy_book import FourStrategyBook

ROOT = Path(__file__).resolve().parents[1]
POLICY = yaml.safe_load((ROOT/"configs/four-strategies.yaml").read_text())["five_trade_evaluation"]


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"ledger.sqlite3"
        self.book = FourStrategyBook(self.path, "owner", "policy", max_positions=4,
                                     evaluation_policy=POLICY)

    def reserve(self, mint, strategy=STRATEGIES[0]):
        return self.book.reserve(mint, strategy, 1_000_000_000, 1000, 30_000_000,
                                 .15, 3_000_000, {}, fraction=.30)

    def opened(self, mint, strategy=STRATEGIES[0]):
        result = self.reserve(mint, strategy)
        self.assertIsNotNone(result)
        nonce = result["nonce"]
        self.book.submitting(nonce)
        self.book.receipt(nonce, "buy-"+nonce, "buy")
        self.book.opened(nonce, 900, {})
        return nonce

    def close(self, nonce, *, net=10, cost=100, verified=True):
        self.book.receipt(nonce, "sell-"+nonce, "sell")
        self.book.settle(nonce, net, 1000, {"verified_zero_holdings": True,
            "signatures": [sig for sig, _ in self.book.signatures(nonce)],
            "buy_debit_lamports": cost, "buy_debit_verified": verified})

    def current(self, strategy=STRATEGIES[0]):
        return self.book.research()["five_trade_evaluation"]["strategies"][strategy]["current"]

    def finish_batch(self, nets, *, strategy=STRATEGIES[0], prefix="a", costs=None):
        for i, net in enumerate(nets):
            nonce = self.opened(prefix+str(i), strategy)
            self.close(nonce, net=net, cost=costs[i] if costs else 100)

    def test_sixth_waits_for_all_five_closures_and_positive_roi_opens_next_batch(self):
        first_four = [self.opened(str(i)) for i in range(4)]
        self.assertIsNone(self.reserve("fifth-too-early"))  # Shared four-position limit.
        self.close(first_four[0])
        fifth = self.opened("fifth")
        self.assertEqual(self.current()["admitted"], 5)
        self.assertEqual(self.current()["state"], "waiting_for_closures")
        self.assertFalse(self.book.can_admit(STRATEGIES[0]))
        self.assertFalse(self.book.paused(STRATEGIES[0]))  # Fifth reserved entry can still submit.
        self.assertIsNone(self.reserve("sixth"))
        for nonce in [*first_four[1:], fifth]:
            self.close(nonce)
        self.assertEqual(self.current()["state"], "passed")
        self.assertAlmostEqual(self.current()["net_roi_percent"], 10)
        sixth = self.opened("sixth")
        details = json.loads(next(p["details"] for p in self.book.positions() if p["nonce"] == sixth))
        self.assertEqual(details["five_trade_evaluation"]["batch"], 2)

    def test_dev_can_enter_without_batch_gate_while_old_fifth_position_stays_open(self):
        dev = STRATEGIES[-1]
        first_four = [self.opened(f"old-dev-{index}", dev) for index in range(4)]
        for nonce in first_four:
            self.close(nonce, net=10)
        fifth = self.opened("old-dev-fifth", dev)
        self.assertEqual(self.current(dev)["state"], "waiting_for_closures")
        self.assertFalse(self.book.can_admit(dev))
        with closing(self.book.connect()) as db:
            before = list(db.iterdump())

        self.book = FourStrategyBook(self.path, "owner", "policy", max_positions=4,
                                     evaluation_policy=POLICY, dev_batch_gate_enabled=False)
        with closing(self.book.connect()) as db:
            self.assertEqual(list(db.iterdump()), before)
        self.assertTrue(self.book.can_admit(dev))
        self.assertFalse(self.book.paused(dev))
        sixth = self.opened("unbatched-dev", dev)
        details = json.loads(next(p["details"] for p in self.book.positions() if p["nonce"] == sixth))
        self.assertNotIn("five_trade_evaluation", details)
        self.assertEqual(self.current(dev)["admitted"], 5)
        self.assertEqual(self.current(dev)["closed"], 4)

        self.close(sixth, net=-200)
        self.close(fifth, net=-100)
        self.assertEqual(self.current(dev)["state"], "paused_nonpositive_roi")
        self.assertTrue(self.book.can_admit(dev))
        self.assertFalse(self.book.paused(dev))
        self.assertFalse(self.book.research()["dev_batch_gate_enabled"])
        with closing(self.book.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM five_trade_entries WHERE strategy=?", (dev,)).fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM outcomes WHERE strategy=?", (dev,)).fetchone()[0], 6)

    def test_disabling_dev_batch_keeps_other_strategies_batched_and_shared_limit(self):
        path = Path(self.temp.name)/"dev-unbatched.sqlite3"
        self.book = FourStrategyBook(path, "owner", "policy", max_positions=9,
                                     evaluation_policy=POLICY, dev_batch_gate_enabled=False)
        def reserve_many(mint, strategy) -> dict | None:
            return self.book.reserve(mint, strategy, 10_000_000_000, 1000, 30_000_000,
                                     .15, 3_000_000, {}, fraction=.05)
        other = STRATEGIES[0]
        for index in range(5):
            self.assertIsNotNone(reserve_many(f"other-{index}", other))
        self.assertFalse(self.book.can_admit(other))
        self.assertIsNone(reserve_many("other-sixth", other))
        self.assertTrue(self.book.can_admit(STRATEGIES[-1]))
        for index in range(4):
            self.assertIsNotNone(reserve_many(f"dev-{index}", STRATEGIES[-1]))
        self.assertIsNone(reserve_many("dev-fifth", STRATEGIES[-1]))
        self.assertEqual(len(self.book.positions()), 9)

    def test_zero_net_roi_stops_even_with_four_winning_trades(self):
        self.finish_batch([1, 1, 1, 1, -4])
        self.assertEqual(self.current()["state"], "paused_nonpositive_roi")
        self.assertEqual(self.current()["net_roi_percent"], 0)
        self.assertIsNone(self.reserve("sixth"))

    def test_roi_uses_real_cost_weighting_instead_of_mean_trade_percentages(self):
        self.finish_batch([10, 10, 10, 10, -100], costs=[100, 100, 100, 100, 10000])
        self.assertAlmostEqual(self.current()["net_roi_percent"], -60/10400*100)
        self.assertTrue(self.book.paused(STRATEGIES[0]))

    def test_strategies_have_independent_cohorts_and_pauses(self):
        self.finish_batch([1, 1, 1, 1, -10])
        self.finish_batch([5, 5, 5, 5, 5], strategy=STRATEGIES[1], prefix="b")
        self.assertTrue(self.book.paused(STRATEGIES[0]))
        self.assertFalse(self.book.paused(STRATEGIES[1]))
        self.assertEqual(self.current(STRATEGIES[1])["state"], "passed")
        self.assertEqual(self.current()["batch"], 1)

    def test_two_large_winners_outweigh_three_losses_for_every_strategy(self):
        for strategy in STRATEGIES:
            with self.subTest(strategy=strategy):
                self.finish_batch([-10, -10, -10, 20, 20], strategy=strategy, prefix=strategy)
                self.assertEqual(self.current(strategy)["state"], "passed")
                self.assertAlmostEqual(self.current(strategy)["net_roi_percent"], 2)
                self.assertFalse(self.book.paused(strategy))
                next_entry = self.reserve(strategy+"-sixth", strategy)
                self.assertIsNotNone(next_entry)
                self.assertEqual(self.current(strategy)["batch"], 2)
                self.book.cancel_unsubmitted(next_entry["nonce"])

    def test_explicit_legacy_policy_can_still_keep_the_older_guard(self):
        path = Path(self.temp.name)/"legacy-batch.sqlite3"
        legacy_policy = {**POLICY, "legacy_loss_guard": "keep_three_in_five"}
        self.book = FourStrategyBook(path, "owner", "policy", max_positions=4,
                                     evaluation_policy=legacy_policy)
        self.finish_batch([-1, -1, -1, 10, 10])
        self.assertEqual(self.current()["state"], "passed")
        self.assertTrue(self.book.paused(STRATEGIES[0]))
        self.assertIsNone(self.reserve("sixth"))

    def test_unbroadcast_cancellation_frees_the_trial_slot(self):
        result = self.reserve("cancelled")
        self.book.cancel_unsubmitted(result["nonce"])
        self.assertEqual(self.current()["admitted"], 0)
        rejected = self.reserve("preflight-rejected")
        nonce = rejected["nonce"]
        self.book.submitting(nonce)
        self.book.receipt(nonce, "rejected-signature", "buy")
        self.book.rejected(nonce, "rejected-signature")
        self.book.release_unbroadcast(nonce)
        self.assertEqual(self.current()["admitted"], 0)

    def test_unknown_ack_keeps_the_reservation_and_cannot_count_as_a_win(self):
        result = self.reserve("unknown")
        nonce = result["nonce"]
        self.book.submitting(nonce)
        self.book.receipt(nonce, "unknown-signature", "buy")
        self.book.release_unbroadcast(nonce)
        self.assertEqual(self.current()["admitted"], 1)
        self.assertEqual(self.current()["closed"], 0)
        self.assertIsNone(self.current()["net_roi_percent"])
        self.assertEqual(len(self.book.positions()), 1)

    def test_unverified_cost_blocks_evaluation_without_inventing_positive_roi(self):
        self.close(self.opened("missing-cost"), net=999, cost=100, verified=False)
        self.assertEqual(self.current()["state"], "blocked_missing_cost")
        self.assertIsNone(self.current()["net_roi_percent"])
        self.assertIsNone(self.reserve("next"))
        self.assertIsNotNone(self.reserve("other", STRATEGIES[1]))

    def test_roi_pause_and_current_epoch_survive_restart(self):
        self.finish_batch([1, 1, 1, 1, -10])
        epoch = self.book.evaluation.epoch
        self.book = FourStrategyBook(self.path, "owner", "policy", max_positions=4)
        self.assertEqual(self.book.evaluation.epoch, epoch)
        self.assertTrue(self.book.paused(STRATEGIES[0]))
        self.assertIsNone(self.reserve("restart-does-not-reset"))

    def test_duplicate_settlement_does_not_add_a_sixth_result(self):
        nonce = self.opened("one")
        self.close(nonce)
        self.book.settle(nonce, 10, 1000, {"verified_zero_holdings": True})
        self.assertEqual(self.current()["closed"], 1)

    def test_policy_changes_cannot_reset_existing_batch_history(self):
        changed = copy.deepcopy(POLICY)
        changed["legacy_loss_guard"] = "keep_three_in_five"
        with self.assertRaises(ValueError):
            FourStrategyBook(self.path, "owner", "policy", max_positions=4, evaluation_policy=changed)

    def test_paid_failed_attempt_fees_are_losses_in_the_batch(self):
        result = self.reserve("failed-buy")
        nonce = result["nonce"]
        self.book.submitting(nonce)
        self.book.receipt(nonce, "failed-buy-signature", "buy")
        self.book.settle(nonce, -5000, 1000, {"verified_zero_holdings": True,
            "signatures": ["failed-buy-signature"], "buy_debit_lamports": 5000,
            "buy_debit_verified": True})
        self.assertEqual(self.current()["closed"], 1)
        self.assertEqual(self.current()["net_lamports"], -5000)

    def test_preexisting_positions_and_outcomes_do_not_become_new_trials(self):
        path = Path(self.temp.name)/"historical.sqlite3"
        old = FourStrategyBook(path, "owner", "policy")
        result = old.reserve("old", STRATEGIES[0], 1_000_000_000, 100, 0, .15, 0, {})
        nonce = result["nonce"]
        old.submitting(nonce)
        old.receipt(nonce, "old-buy", "buy")
        old.opened(nonce, 100, {})
        self.book = FourStrategyBook(path, "owner", "policy", max_positions=4, evaluation_policy=POLICY)
        self.close(nonce)
        self.assertEqual(self.current()["state"], "not_started")
        self.assertEqual(self.book.research()["strategies"][STRATEGIES[0]]["closed"], 1)

    def test_configured_roi_only_policy_keeps_legacy_records_without_reactivating_them_as_rules(self):
        path = Path(self.temp.name)/"explicit-roi-only.sqlite3"
        old = FourStrategyBook(path, "owner", "policy")
        with closing(old.connect()) as db, db:
            db.execute("INSERT INTO pauses VALUES (?)", (STRATEGIES[0],))
        current = FourStrategyBook(path, "owner", "policy", max_positions=4, evaluation_policy=POLICY)
        self.assertFalse(current.paused(STRATEGIES[0]))
        with closing(current.connect()) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM pauses WHERE strategy=?", (STRATEGIES[0],)).fetchone())

    def test_read_only_inspector_does_not_create_evaluation_tables_or_fabricate_roi(self):
        root = Path(self.temp.name)/"readonly"
        (root/"configs").mkdir(parents=True)
        cfg = yaml.safe_load((ROOT/"configs/four-strategies.yaml").read_text())
        cfg["four_strategies"]["database"] = "ledger.sqlite3"
        policy = hashlib.sha256(json.dumps(cfg["four_strategies"], sort_keys=True, allow_nan=False).encode()).hexdigest()
        book = FourStrategyBook(root/"ledger.sqlite3", cfg["four_strategies"]["expected_wallet"], policy)
        with closing(book.connect()) as db, db:
            db.execute("INSERT INTO pauses VALUES (?)", (STRATEGIES[0],))
        config = root/"configs/four-strategies.yaml"
        config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        (root/"configs/second-block-dev.yaml").write_text(
            (ROOT/"configs/second-block-dev.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        with closing(book.connect()) as db:
            before = db.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()
        report = inspect(config)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["trader_started"])
        self.assertFalse(report["dev_batch_gate_enabled"])
        self.assertEqual(report["evaluation"]["state"], "not_started")
        self.assertFalse(report["legacy_loss_guard_active"])
        self.assertEqual(report["recorded_legacy_pauses"], [STRATEGIES[0]])
        for strategy in report["evaluation"]["strategies"].values():
            self.assertIsNone(strategy["current"]["net_roi_percent"])
            self.assertFalse(strategy["blocked_by_legacy_loss_guard"])
        with closing(book.connect()) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall(), before)
