"""Tests for the copy-trader weekly quality gate."""

import unittest

from monitoring.trader_quality import QualityRules, TraderCandidate, rank_candidates


class TraderQualityTests(unittest.TestCase):
    def candidate(self, **overrides: int | float | str) -> TraderCandidate:
        row = {
            "wallet": "wallet",
            "median_roi_pct_7d": 170,
            "calls_7d": 5,
            "win_rate_pct_7d": 70,
            "x_followers": 2_500,
            "x_handle": "quiet_trader",
            "source": "audited-rpc-export-2026-09-06",
        }
        row.update(overrides)
        return TraderCandidate.from_dict(row)

    def test_requires_every_threshold(self) -> None:
        rules = QualityRules()
        self.assertEqual(rank_candidates([self.candidate()], rules), [self.candidate()])
        self.assertEqual(
            rank_candidates([self.candidate(median_roi_pct_7d=149.99)], rules), []
        )
        self.assertEqual(rank_candidates([self.candidate(calls_7d=4)], rules), [])
        self.assertEqual(
            rank_candidates([self.candidate(win_rate_pct_7d=59.99)], rules), []
        )
        self.assertEqual(
            rank_candidates([self.candidate(x_followers=15_001)], rules), []
        )

    def test_missing_source_fails_closed(self) -> None:
        self.assertEqual(rank_candidates([self.candidate(source="")]), [])

    def test_under_3k_is_ranked_first(self) -> None:
        quiet = self.candidate(wallet="quiet", x_followers=2_999, median_roi_pct_7d=160)
        loud = self.candidate(wallet="loud", x_followers=8_000, median_roi_pct_7d=900)
        self.assertEqual(rank_candidates([loud, quiet]), [quiet, loud])

    def test_missing_metric_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing verified fields"):
            TraderCandidate.from_dict({"wallet": "unknown"})


if __name__ == "__main__":
    unittest.main()
