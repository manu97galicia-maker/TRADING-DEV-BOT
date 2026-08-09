import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from trading.universal_trader import UniversalTrader


class OpenPositionMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.trader = UniversalTrader.__new__(UniversalTrader)
        self.trader._open_pos_path = Path(self.temp_dir.name) / "open_position_test.json"
        self.trader._state_suffix = "_test"

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def token(mint):
        return SimpleNamespace(
            mint=mint,
            symbol=mint,
            platform=SimpleNamespace(value="pump_fun"),
        )

    @staticmethod
    def buy():
        return SimpleNamespace(price=0.000001, amount=1000)

    def test_concurrent_positions_have_independent_markers(self):
        first = self.token("Mint111")
        second = self.token("Mint222")

        self.trader._record_open_position(first, self.buy())
        self.trader._record_open_position(second, self.buy())

        first_path = self.trader._open_position_path("Mint111")
        second_path = self.trader._open_position_path("Mint222")
        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())

        self.trader._write_live_status(first, 0.000001, 750, 0.0000015, 1000)
        first_status = json.loads(first_path.read_text(encoding="utf-8"))
        self.assertEqual(first_status["remaining"], 750)

        self.trader._clear_open_position("Mint111")
        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.exists())

    def test_clear_all_removes_legacy_and_mint_markers(self):
        self.trader._open_pos_path.write_text(
            json.dumps({"mint": "LegacyMint"}), encoding="utf-8"
        )
        self.trader._record_open_position(self.token("Mint333"), self.buy())

        self.trader._clear_open_position()

        self.assertEqual(self.trader._open_position_paths(), [])


if __name__ == "__main__":
    unittest.main()
