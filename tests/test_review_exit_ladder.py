"""Check ordered exits for low-cap, high-cap and Telegram entry examples."""

import json
import unittest
from pathlib import Path

from trading.review_exit_ladder import build_review_ladder


class ReviewLadderTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.plan = json.loads(
            (root / "estudios/four_strategies/request.json").read_text(encoding="utf-8")
        )
        self.settings = self.plan["exit"]

    def test_8k_keeps_28k_final_target(self) -> None:
        tiers = build_review_ladder(8000, self.settings)
        self.assertEqual([tier.target_mc_usd for tier in tiers], [12000, 20000, 28000])
        self.assertEqual([tier.sell_original_fraction for tier in tiers], [.5, .25, .25])

    def test_21k_retains_50pct_and_raises_later_targets(self) -> None:
        tiers = build_review_ladder(21000, self.settings)
        self.assertEqual([tier.target_mc_usd for tier in tiers], [31500, 36750, 42000])
        self.assertEqual([tier.gain_from_entry for tier in tiers], [.5, .75, 1])

    def test_equal_first_and_final_are_not_duplicate_exits(self) -> None:
        tiers = build_review_ladder(28000 / 1.5, self.settings)
        self.assertGreater(tiers[2].target_mc_usd, tiers[0].target_mc_usd)

    def test_all_entry_examples_have_ordered_exits_and_no_unallocated_tokens(self) -> None:
        for entry in (1000, 6000, 13000, 18000, 19000, 21000, 28000, 100000):
            tiers = build_review_ladder(entry, self.settings)
            self.assertEqual(tiers[0].gain_from_entry, .5)
            self.assertEqual(sum(t.sell_original_fraction for t in tiers), 1)
            self.assertTrue(entry < tiers[0].target_mc_usd < tiers[1].target_mc_usd < tiers[2].target_mc_usd)

    def test_invalid_entry_or_configuration_cannot_build_a_ladder(self) -> None:
        for entry in (0, -1, float("nan"), float("inf"), 1e308):
            with self.assertRaises(ValueError):
                build_review_ladder(entry, self.settings)
        for changes in (
            {"proposed_original_quantity_fractions": [.5, .5, .5]},
            {"fallback_last_gain": .25},
            {"last_target_market_cap_usd": float("nan")},
            {"ladder_policy": "unknown"},
        ):
            with self.assertRaises(ValueError):
                build_review_ladder(21000, {**self.settings, **changes})

    def test_plan_remains_offline_and_four_strategies_share_the_exit_rule(self) -> None:
        self.assertEqual(self.plan["execution"], "offline_review_only")
        self.assertEqual(len(self.plan["strategies"]), 4)
        self.assertEqual(self.plan["exit"]["stop_loss_fraction"], .25)


if __name__ == "__main__":
    unittest.main()
