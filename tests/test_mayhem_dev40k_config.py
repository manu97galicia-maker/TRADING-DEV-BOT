"""Safety and strategy assertions for the Mayhem DEV40K paper config."""

import json
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

    def test_requires_dataset_derived_40k_mayhem_devs(self) -> None:
        filters = self.config["filters"]
        self.assertTrue(filters["require_mayhem"])
        self.assertEqual(filters["bro_address"], "top_devs_mayhem40k.json")
        data = json.loads(Path(filters["bro_address"]).read_text(encoding="utf-8"))
        wallets = data["wallets"]
        self.assertEqual(len(wallets), 20)
        self.assertEqual(len({row["wallet"] for row in wallets}), 20)
        self.assertTrue(all(row["median_peak"] >= 40_000 for row in wallets))
        self.assertTrue(all(row["mayhem_launches"] >= 3 for row in wallets))

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
