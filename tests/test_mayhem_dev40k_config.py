"""Safety and strategy assertions for the Mayhem DEV40K paper config."""

import unittest
from pathlib import Path

import yaml


class MayhemDev40KConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = yaml.safe_load(
            Path("bots/bot-paper-mayhem-dev40k.yaml").read_text(encoding="utf-8")
        )

    def test_is_paper_only_with_requested_sizes(self) -> None:
        self.assertTrue(self.config["filters"]["paper_trade"])
        self.assertEqual(self.config["trade"]["max_real_buys"], 0)
        self.assertEqual(self.config["trade"]["buy_amount"], 0.03)
        self.assertEqual(self.config["trade"]["mayhem_buy_amount"], 0.04)

    def test_requires_frequent_40k_median_mayhem_devs(self) -> None:
        filters = self.config["filters"]
        median = filters["dev_mediana"]
        self.assertTrue(filters["require_mayhem"])
        self.assertEqual(median["min_mediana_mc"], 40_000)
        self.assertGreaterEqual(median["min_monedas"], 3)
        self.assertGreaterEqual(median["min_lanzamientos"], 5)

    def test_profit_ladder_raises_stop_and_keeps_trailing(self) -> None:
        trade = self.config["trade"]
        self.assertTrue(trade["stop_a_entrada_tras_tp"])
        self.assertFalse(trade["stop_al_tp_tras_tp"])
        self.assertEqual(trade["trailing_stop_percentage"], 0.25)
        sold = sum(
            trade[key]
            for key in (
                "take_profit_sell_percentage",
                "tp2_sell",
                "tp3_sell",
                "tp4_sell",
            )
        )
        self.assertAlmostEqual(sold, 0.8)


if __name__ == "__main__":
    unittest.main()
