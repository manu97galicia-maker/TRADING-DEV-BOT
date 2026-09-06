"""Safety and strategy assertions for the MAYHEM40 paper configuration."""

import unittest
from pathlib import Path

import yaml


class Mayhem40ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = yaml.safe_load(
            Path("bots/bot-paper-mayhem40.yaml").read_text(encoding="utf-8")
        )

    def test_cannot_trade_real_funds(self) -> None:
        self.assertTrue(self.config["filters"]["paper_trade"])
        self.assertEqual(self.config["trade"]["buy_amount"], 0.025)
        self.assertEqual(self.config["trade"]["mayhem_buy_amount"], 0.025)

    def test_exit_is_full_sale_at_forty_percent(self) -> None:
        trade = self.config["trade"]
        self.assertEqual(trade["take_profit_percentage"], 0.40)
        self.assertEqual(trade["take_profit_sell_percentage"], 1.0)
        self.assertEqual(trade["stop_loss_percentage"], 0.22)

    def test_requires_distributed_mayhem_demand(self) -> None:
        filters = self.config["filters"]
        self.assertTrue(filters["require_mayhem"])
        self.assertGreaterEqual(filters["min_titulares"], 12)
        self.assertGreaterEqual(filters["min_buys"], 12)
        self.assertLessEqual(filters["max_entry_mc"], 3400)
        self.assertFalse(filters["pumpfun_dev"]["enabled"])


if __name__ == "__main__":
    unittest.main()
