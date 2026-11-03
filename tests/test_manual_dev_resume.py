"""Manual-resume checks use only a synthetic ledger, never a live trader."""

# ruff: noqa: ANN001, ANN003, ANN201

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import yaml

from resume_dev_batch import run
from trading.five_trade_resume import resume_dev
from trading.four_strategy_book import FourStrategyBook

ROOT = Path(__file__).resolve().parents[1]
POLICY = yaml.safe_load((ROOT / "configs/four-strategies.yaml").read_text())["five_trade_evaluation"]
DEV = "dev-second-block"
OTHER = "strength-21k"


class ManualDevResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic-ledger.sqlite3"
        self.book = FourStrategyBook(self.path, "fake-owner", "fake-policy-hash", max_positions=4,
                                     evaluation_policy=POLICY)
        self.evaluation = self.book.evaluation
        self.db = self.book.connect()
        self.addCleanup(self.db.close)
        self.epoch = self.evaluation.epoch
        for index in range(5):
            nonce = f"old-dev-{index}"
            self.evaluation.admit(self.db, DEV, nonce)
            self.evaluation.closed(self.db, nonce, -10, 100)
            self.db.execute("INSERT INTO outcomes(nonce,strategy,net,details) VALUES (?,?,?,?)",
                            (nonce, DEV, -10, '{}'))
        # Independent historical state must survive the manual dev override.
        self.evaluation.admit(self.db, OTHER, "other-pending")
        self.db.execute("INSERT INTO pauses VALUES (?)", (DEV,))
        self.db.execute("INSERT INTO pauses VALUES (?)", (OTHER,))
        self.db.execute("INSERT INTO receipts VALUES ('old-public-signature','old-dev-0','buy')")
        self.db.commit()

    def invoke(self, *, apply=False, expected_batch=1, owner="fake-owner", policy_hash="fake-policy-hash",
               policy=None):
        return resume_dev(self.db, expected_batch=expected_batch, apply=apply,
                          expected_owner=owner, expected_policy_hash=policy_hash,
                          configured_evaluation=POLICY if policy is None else policy)

    def contents(self, table):
        # The caller supplies only fixed test-owned schema names.
        return self.db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()  # noqa: S608

    def snapshot(self):
        return list(self.db.iterdump())

    def assert_rejected_without_changes(self, **kwargs):
        before = self.snapshot()
        changes = self.db.total_changes
        with self.assertRaises(ValueError):
            self.invoke(**kwargs)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.db.total_changes, changes)

    def cli_config(self):
        directory = Path(self.temp.name) / "configs"
        directory.mkdir()
        cfg = yaml.safe_load((ROOT / "configs/four-strategies.yaml").read_text())
        cfg["enabled"] = True
        cfg["four_strategies"]["database"] = self.path.name
        cfg["four_strategies"]["expected_wallet"] = "fake-owner"
        expected_hash = hashlib.sha256(
            json.dumps(cfg["four_strategies"], sort_keys=True, allow_nan=False).encode()).hexdigest()
        self.db.execute("UPDATE settings SET policy=? WHERE id=1", (expected_hash,))
        self.db.commit()
        config = directory / "four-strategies.yaml"
        config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        dev = yaml.safe_load((ROOT / "configs/second-block-dev.yaml").read_text())
        dev["enabled"] = True
        (directory / "second-block-dev.yaml").write_text(yaml.safe_dump(dev), encoding="utf-8")
        return config

    def test_cli_defaults_to_preview_on_a_synthetic_config_and_read_only_ledger(self):
        config = self.cli_config()
        before = self.snapshot()
        report = run(config, expected_batch=1)
        self.assertFalse(report["applied"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["trader_started"])
        self.assertEqual(report["new_batch"], 2)
        self.assertEqual(self.snapshot(), before)

    def test_cli_does_not_create_a_missing_ledger(self):
        config = self.cli_config()
        cfg = yaml.safe_load(config.read_text())
        cfg["four_strategies"]["database"] = "does-not-exist.sqlite3"
        config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        missing = Path(self.temp.name) / "does-not-exist.sqlite3"
        before = self.snapshot()
        with self.assertRaises(ValueError):
            run(config, expected_batch=1)
        self.assertFalse(missing.exists())
        self.assertEqual(self.snapshot(), before)

    def test_preview_is_read_only_and_describes_a_new_batch_without_erasing_losses(self):
        before = self.snapshot()
        changes = self.db.total_changes
        report = self.invoke()
        self.assertFalse(report["applied"])
        self.assertEqual(report["strategy"], DEV)
        self.assertEqual(report["previous_batch"], 1)
        self.assertEqual(report["new_batch"], 2)
        self.assertAlmostEqual(report["previous_net_roi_percent"], -10)
        self.assertFalse(report["enables_new_entries"])
        self.assertTrue(report["would_enable_new_entries"])
        self.assertFalse(report["trader_started"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.db.total_changes, changes)
        self.assertTrue(self.evaluation.paused(self.db, DEV))
        self.assertIsNone(self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='five_trade_manual_resumptions'").fetchone())

    def test_preview_works_with_a_sqlite_read_only_connection(self):
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as readonly:
            report = resume_dev(readonly, expected_batch=1, expected_owner="fake-owner",
                                expected_policy_hash="fake-policy-hash", configured_evaluation=POLICY)
        self.assertFalse(report["applied"])
        self.assertEqual(report["new_batch"], 2)

    def test_apply_requires_a_callers_transaction(self):
        self.assertFalse(self.db.in_transaction)
        self.assert_rejected_without_changes(apply=True)

    def test_apply_preserves_results_pauses_receipts_policy_and_other_strategy(self):
        before = {table: self.contents(table) for table in (
            "settings", "five_trade_settings", "five_trade_entries", "outcomes", "positions", "receipts", "pauses")}
        old_batches = self.contents("five_trade_batches")
        self.db.execute("BEGIN IMMEDIATE")
        report = self.invoke(apply=True)
        self.assertTrue(self.db.in_transaction)
        self.assertTrue(report["applied"])
        self.assertFalse(report["trader_started"])
        self.assertEqual(self.contents("five_trade_batches")[:-1], old_batches)
        latest = self.db.execute(
            "SELECT batch,state,closed_at FROM five_trade_batches WHERE epoch=? AND strategy=? ORDER BY batch DESC LIMIT 1",
            (self.epoch, DEV)).fetchone()
        self.assertEqual(latest, (2, "collecting", None))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM five_trade_entries WHERE epoch=? AND strategy=? AND batch=2",
            (self.epoch, DEV)).fetchone()[0], 0)
        for table, rows in before.items():
            with self.subTest(table=table):
                self.assertEqual(self.contents(table), rows)
        self.assertFalse(self.evaluation.paused(self.db, DEV))
        self.assertTrue(self.evaluation.can_admit(self.db, DEV))
        audit = self.db.execute(
            "SELECT epoch,strategy,previous_batch,new_batch,previous_net,previous_cost,reason "
            "FROM five_trade_manual_resumptions").fetchone()
        self.assertEqual(audit, (self.epoch, DEV, 1, 2, -50, 500, "manual_user_resume"))
        self.db.commit()
        # Existing Book readers see the explicit new batch without reconfiguration.
        self.assertTrue(self.book.can_admit(DEV))

    def test_caller_rollback_undoes_both_new_batch_and_audit_schema(self):
        before = self.snapshot()
        self.db.execute("BEGIN IMMEDIATE")
        self.invoke(apply=True)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.evaluation.paused(self.db, DEV))

    def test_retry_of_the_old_expected_batch_cannot_open_another_batch(self):
        self.db.execute("BEGIN IMMEDIATE")
        self.invoke(apply=True)
        self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        self.assert_rejected_without_changes(apply=True)
        self.db.rollback()
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM five_trade_manual_resumptions").fetchone()[0], 1)

    def test_five_new_negative_closures_pause_again_without_changing_the_old_batch(self):
        old = self.contents("five_trade_batches")[0]
        self.db.execute("BEGIN IMMEDIATE")
        self.invoke(apply=True)
        self.db.commit()
        for index in range(5):
            nonce = f"new-dev-{index}"
            trial = self.evaluation.admit(self.db, DEV, nonce)
            self.assertEqual(trial["batch"], 2)
            self.evaluation.closed(self.db, nonce, -1, 100)
        self.db.commit()
        self.assertTrue(self.evaluation.paused(self.db, DEV))
        self.assertFalse(self.evaluation.can_admit(self.db, DEV))
        self.assertIsNone(self.evaluation.admit(self.db, DEV, "sixth-new-dev"))
        self.assertEqual(self.contents("five_trade_batches")[0], old)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM outcomes WHERE strategy=?", (DEV,)).fetchone()[0], 5)

    def test_expected_batch_requires_a_matching_positive_integer(self):
        for value in (None, True, False, 0, -1, 1.0, "1", 2):
            with self.subTest(value=value):
                self.assert_rejected_without_changes(expected_batch=value)

    def test_wallet_and_policy_hash_must_match_durable_identity(self):
        self.assert_rejected_without_changes(owner="different-owner")
        self.assert_rejected_without_changes(policy_hash="different-policy-hash")

    def test_configured_evaluation_cannot_disable_or_change_the_roi_guard(self):
        for changes in ({"enabled": False}, {"legacy_loss_guard": "keep_three_in_five"},
                        {"batch_size": 6}, {"scope": "all"}, {"roi_currency": "USD"}):
            with self.subTest(changes=changes):
                self.assert_rejected_without_changes(policy={**copy.deepcopy(POLICY), **changes})

    def test_stored_policy_must_match_configured_evaluation(self):
        self.db.execute("UPDATE five_trade_settings SET policy=? WHERE id=1", ('{}',))
        self.db.commit()
        self.assert_rejected_without_changes()

    def test_equal_but_invalid_stored_boolean_type_is_rejected(self):
        stored = {**POLICY, "enabled": 1}
        self.assertEqual(stored, POLICY)  # Python equality alone cannot validate this field.
        self.db.execute("UPDATE five_trade_settings SET policy=? WHERE id=1", (json.dumps(stored),))
        self.db.commit()
        self.assert_rejected_without_changes()

    def test_empty_epoch_is_rejected_without_creating_a_new_evaluation(self):
        self.db.execute("UPDATE five_trade_settings SET epoch='' WHERE id=1")
        self.db.commit()
        self.assert_rejected_without_changes()

    def test_only_a_complete_nonpositive_roi_pause_is_eligible(self):
        for state in ("passed", "collecting", "waiting_for_closures", "blocked_missing_cost"):
            with self.subTest(state=state):
                self.db.execute("UPDATE five_trade_batches SET state=? WHERE strategy=?", (state, DEV))
                self.db.commit()
                self.assert_rejected_without_changes()

    def test_positive_results_cannot_be_relabelled_as_an_eligible_pause(self):
        self.db.execute("UPDATE five_trade_entries SET net=1 WHERE strategy=?", (DEV,))
        self.db.commit()
        self.assert_rejected_without_changes()

    def test_zero_net_completed_batch_can_be_explicitly_resumed(self):
        self.db.execute("UPDATE five_trade_entries SET net=0 WHERE strategy=?", (DEV,))
        self.db.commit()
        report = self.invoke()
        self.assertEqual(report["previous_net_roi_percent"], 0)
        self.assertEqual(report["new_batch"], 2)

    def test_missing_or_invalid_recorded_costs_are_not_bypassed(self):
        for cost in (None, 0, -1, 1.5, "bad"):
            with self.subTest(cost=cost):
                self.db.execute("UPDATE five_trade_entries SET cost=? WHERE nonce='old-dev-0'", (cost,))
                self.db.commit()
                self.assert_rejected_without_changes()

    def test_missing_or_noninteger_results_are_not_bypassed(self):
        for net in (None, 1.5, "bad"):
            with self.subTest(net=net):
                self.db.execute("UPDATE five_trade_entries SET net=? WHERE nonce='old-dev-0'", (net,))
                self.db.commit()
                self.assert_rejected_without_changes()

    def test_an_incomplete_or_overfull_paused_batch_is_rejected(self):
        self.db.execute("DELETE FROM five_trade_entries WHERE nonce='old-dev-0'")
        self.db.commit()
        self.assert_rejected_without_changes()
        self.db.execute("INSERT INTO five_trade_entries VALUES ('extra-1',?,?,1,1,-1,100,1)",
                        (self.epoch, DEV))
        self.db.execute("INSERT INTO five_trade_entries VALUES ('extra-2',?,?,1,1,-1,100,1)",
                        (self.epoch, DEV))
        self.db.commit()
        self.assert_rejected_without_changes()

    def test_open_reserved_or_signed_uncertain_dev_positions_block_resume(self):
        for state in ("open", "reserved", "uncertain"):
            with self.subTest(state=state):
                self.db.execute("INSERT INTO positions VALUES ('target-position','target-mint',?,?,1,1,'{}',1)",
                                (DEV, state))
                self.db.commit()
                self.assert_rejected_without_changes()
                self.db.execute("DELETE FROM positions WHERE nonce='target-position'")
                self.db.commit()

    def test_other_unclosed_dev_entries_in_the_epoch_block_resume(self):
        self.db.execute("INSERT INTO five_trade_entries VALUES ('unresolved-old',?,?,0,0,NULL,NULL,1)",
                        (self.epoch, DEV))
        self.db.commit()
        self.assert_rejected_without_changes()


if __name__ == "__main__":
    unittest.main()
