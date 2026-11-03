"""Offline risk-component tests; no wallet, network, orders or live runner."""

import math
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from trading.strategy_loss_guard import (
    StrategyLossGuard,
    priority_fee_cap_lamports,
    priority_price_micro_lamports,
)


class LossGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "risk.sqlite3"
        self.guard = StrategyLossGuard(self.path)

    def test_three_of_five_pauses_only_the_affected_strategy(self) -> None:
        for i, net in enumerate((-1, 2, -1, 2, -1)):
            paused = self.guard.record_closed("pressure", str(i), net)
            self.assertEqual(paused, i == 4)
        self.assertFalse(self.guard.is_paused("telegram"))
        self.assertTrue(StrategyLossGuard(self.path).is_paused("pressure"))
        self.assertTrue(self.guard.record_closed("pressure", "later-win", 999))

    def test_window_slides_and_zero_is_not_a_loss(self) -> None:
        for i, net in enumerate((-1, 2, 0, 2, -1, -1)):
            self.assertFalse(self.guard.record_closed("s", str(i), net))
        self.assertTrue(self.guard.record_closed("s", "seventh", -1))

    def test_duplicate_receipts_neither_count_twice_nor_change_results(self) -> None:
        for _ in range(10):
            self.assertFalse(self.guard.record_closed("s", "same", -1))
        with self.assertRaises(ValueError):
            self.guard.record_closed("s", "same", 1)
        for invalid in (None, float("nan"), True, 1.5):
            with self.assertRaises(TypeError):
                self.guard.record_closed("s", "invalid", invalid)

    def test_concurrent_results_preserve_pause(self) -> None:
        def record(index: int) -> bool:
            return StrategyLossGuard(self.path).record_closed("s", str(index), -1)
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(record, range(5)))
        self.assertTrue(self.guard.is_paused("s"))


class EmergencyFeeTests(unittest.TestCase):
    def test_eight_cents_uses_current_price_and_rounds_down(self) -> None:
        for price in (75.0, 95.32, 150.0, 200.0):
            cap = priority_fee_cap_lamports(price, quote_age_seconds=2)
            self.assertLessEqual(cap / 1e9 * price, 0.08)
            for units in (120000, 200000, 1400000):
                cu_price = priority_price_micro_lamports(cap, units)
                self.assertLessEqual(math.ceil(cu_price * units / 1e6), cap)

    def test_unknown_or_stale_quotes_cannot_set_a_dollar_cap(self) -> None:
        for price, age in ((0, 0), (95, 31), (95, -1), (float("nan"), 0)):
            with self.assertRaises(ValueError):
                priority_fee_cap_lamports(price, quote_age_seconds=age)


if __name__ == "__main__":
    unittest.main()
