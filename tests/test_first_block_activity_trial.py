"""Activity-only second-block trial without creator-history requests or live orders."""

# Test fixtures mirror asynchronous runtime callbacks.
# ruff: noqa: ANN201

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import test_second_block_source as stream
import yaml

from trading.four_strategy_book import FourStrategyBook
from trading.second_block_policy import STRATEGY_ID, validate_second_policy


class ActivitySourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await stream.SourceTests.asyncSetUp(self)
        self.policy.update(entry_rule="first_block_activity_only",
                           entry_timing="second_produced_block",
                           require_dev_initial_buy=True,
                           min_distinct_first_block_buyers=3,
                           min_first_block_volume_usd=350,
                           require_first_block_activity=True)
        self.source.history.observe = Mock(side_effect=AssertionError("history observation"))
        self.source.history.warm = Mock(side_effect=AssertionError("history request"))
        self.source.history.evidence = Mock(side_effect=AssertionError("history evidence"))

    async def test_three_distinct_buyers_and_350_usd_trigger_only_second_block(self):
        self.source.on_slot({"slot": 100, "parent": 99})
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator, user=self.a),
                                        stream.trade(self.mint, self.a, sol=1),
                                        stream.trade(self.mint, self.b, sol=1.2)), 100, "create")
        await asyncio.sleep(0)
        self.callback.assert_not_awaited()
        self.source.on_logs(stream.logs(stream.trade(self.mint, self.creator, sol=1.3)), 100, "third")
        for _ in range(4):
            await asyncio.sleep(0)
        self.source.on_slot({"slot": 101, "parent": 100})
        signal = self.source.signal(str(self.mint))
        self.assertIsNotNone(signal)
        self.assertEqual(signal["entry_rule"], "first_block_activity_only")
        self.assertEqual(signal["buyers"], 3)
        self.assertAlmostEqual(signal["volume_usd"], 350)
        self.assertIsNone(signal["creator_history"])
        self.assertTrue(signal["dev_initial_buy_verified"])
        self.assertEqual(signal["trial_id"], self.policy["trial_id"])
        self.assertEqual(signal["max_trial_buys"], 5)
        for _ in range(4):
            await asyncio.sleep(0)
        self.callback.assert_awaited_once()
        self.source.on_slot({"slot": 102, "parent": 101})
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.source.history.observe.assert_not_called()
        self.source.history.warm.assert_not_called()
        self.source.history.evidence.assert_not_called()

    async def test_two_buyers_or_low_volume_never_qualify(self):
        self.source.on_slot({"slot": 100, "parent": 99})
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator, user=self.a),
                                        stream.trade(self.mint, self.a, sol=1.5),
                                        stream.trade(self.mint, self.b, sol=2.0)), 100, "create")
        self.source.on_slot({"slot": 101, "parent": 100})
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.callback.assert_not_awaited()

    async def test_three_buyers_below_350_never_qualify(self):
        self.source.on_slot({"slot": 100, "parent": 99})
        self.source.on_logs(stream.logs(stream.create(self.mint, self.creator, user=self.a),
                                        stream.trade(self.mint, self.a, sol=1),
                                        stream.trade(self.mint, self.b, sol=1),
                                        stream.trade(self.mint, self.creator, sol=1.49)), 100, "create")
        self.source.on_slot({"slot": 101, "parent": 100})
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.callback.assert_not_awaited()


class DurableTrialTests(unittest.TestCase):
    def test_shipped_policy_requires_three_buyers_and_five_buy_limit(self):
        policy = yaml.safe_load((Path(__file__).resolve().parents[1] / "configs/second-block-dev.yaml").read_text())
        policy["enabled"] = True
        validate_second_policy(policy)
        for key, value in (("min_distinct_first_block_buyers", 2),
                           ("min_first_block_volume_usd", 349),
                           ("max_trial_buys", 6)):
            with self.subTest(key=key):
                changed = {**policy, key: value}
                with self.assertRaises(ValueError):
                    validate_second_policy(changed)

    def test_five_buy_cap_survives_close_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sqlite3"
            book = FourStrategyBook(path, "wallet", "policy", max_positions=9,
                                    dev_batch_gate_enabled=False)
            features = {"trial_id": "trial-1", "max_trial_buys": 5}
            for i in range(5):
                reservation = book.reserve(f"mint-{i}", STRATEGY_ID, 10_000_000_000,
                                           1, 0, 0, 0, features, fraction=.01)
                self.assertIsNotNone(reservation)
                if i == 0:
                    nonce = reservation["nonce"]
                    self.assertTrue(book.submitting(nonce))
                    book.receipt(nonce, "signature-0", "buy")
                    book.settle(nonce, 1, 1, {"verified_zero_holdings": True,
                                               "signatures": ["signature-0"]})
            restarted = FourStrategyBook(path, "wallet", "policy", max_positions=9,
                                         dev_batch_gate_enabled=False)
            self.assertEqual(restarted.trial_count("trial-1"), 5)
            self.assertIsNone(restarted.reserve("mint-extra", STRATEGY_ID, 10_000_000_000,
                                                1, 0, 0, 0, features, fraction=.01))


if __name__ == "__main__":
    unittest.main()
