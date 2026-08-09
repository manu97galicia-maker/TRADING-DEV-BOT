"""Deterministic safety and analytics tests for paper trading."""

import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from paper_trading.analytics import simulate_pair, summarize
from paper_trading.engine import PaperTradingEngine
from paper_trading.history import DevHistory
from paper_trading.models import Candidate, DevStats, Strategy
from paper_trading.store import PaperStore


class PaperTradingTests(unittest.TestCase):
    def test_real_trading_flag_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "forbidden"):
            PaperTradingEngine({"enabled": True, "real_trading": True})

    def test_capital_defaults_match_requested_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperTradingEngine(
                {"database": str(Path(directory) / "paper.db")}
            )
            self.assertEqual(engine.starting_capital, 2.0)
            self.assertEqual(engine.trade_size, 0.25)

    def test_start_announces_and_starts_reporter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = PaperTradingEngine(
                {
                    "enabled": True,
                    "database": str(Path(directory) / "paper.db"),
                }
            )
            with patch("paper_trading.engine.notify") as mocked_notify:
                async def exercise() -> None:
                    engine.start()
                    self.assertIsNotNone(engine._report_task)
                    engine._report_task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await engine._report_task

                asyncio.run(exercise())
                mocked_notify.assert_called_once()

    def test_base_signal_is_strict(self) -> None:
        strategy = Strategy()
        candidate = Candidate(100, "mint", "ABC", "dev", 3_999)
        self.assertTrue(strategy.accepts(candidate, DevStats(5, 12_001, 30_000)))
        self.assertFalse(strategy.accepts(candidate, DevStats(5, 12_000, 30_000)))
        self.assertFalse(
            strategy.accepts(
                Candidate(100, "m", "A", "d", 4_000), DevStats(5, 12_001, 30_000)
            )
        )

    def test_history_excludes_future_and_current_mint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            rows = [
                {
                    "dev": "d",
                    "mint": "old",
                    "symbol": "OLD",
                    "launched_at": 10,
                    "peak_mc": 20_000,
                },
                {
                    "dev": "d",
                    "mint": "current",
                    "symbol": "NOW",
                    "launched_at": 20,
                    "peak_mc": 99_000,
                },
                {
                    "dev": "d",
                    "mint": "future",
                    "symbol": "F",
                    "launched_at": 30,
                    "peak_mc": 999_000,
                },
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
            )
            stats = DevHistory(path).before("d", 25, "current")
            self.assertEqual(stats.launches, 1)
            self.assertEqual(stats.mean_peak_mc, 20_000)

    def test_frozen_aggregate_profile_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(
                json.dumps(
                    {
                        "wallets": [
                            {"wallet": "d", "launches_7d": 8, "median_peak": 15_000}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stats = DevHistory(path, "median_peak").before("d", 100)
            self.assertEqual(stats.launches, 8)
            self.assertEqual(stats.mean_peak_mc, 15_000)

    def test_first_tp_sl_touch_wins(self) -> None:
        result = simulate_pair([(0, 0), (5, -0.2), (10, 0.8)], 0.5, 0.15)
        self.assertEqual(result["exit"], "sl")
        self.assertEqual(result["time"], 5)

    def test_store_deduplicates_mint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(Path(directory) / "paper.db")
            row = {
                "mint": "m",
                "signal_ts": 1,
                "symbol": "A",
                "dev": "d",
                "entry_mc": 3000,
                "dev_mean_mc": 13000,
                "dev_launches": 5,
                "best_peak_mc": 20000,
                "best_symbol": "B",
                "features": {},
            }
            self.assertTrue(store.create(row))
            self.assertFalse(store.create(row))

    def test_summary_drawdown(self) -> None:
        rows = [
            {"result": {"strategies": {"primary": {"return": value}}}}
            for value in (0.2, -0.5, 0.1)
        ]
        self.assertAlmostEqual(summarize(rows)["max_drawdown"], 0.5)


if __name__ == "__main__":
    unittest.main()
