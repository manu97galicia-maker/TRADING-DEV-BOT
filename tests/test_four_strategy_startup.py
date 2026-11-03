"""Configuration startup with a recording fake trader; never connects or trades."""

# Private flags and positional boolean values are the regression cases under test.
# ruff: noqa: SLF001, FBT003

import copy
import hashlib
import inspect
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml

import bot_runner
from interfaces.core import Platform
from monitoring import notifier
from trading.four_strategy_runtime import FourStrategyTrader
from trading.universal_trader import UniversalTrader

ROOT = Path(__file__).resolve().parents[1]


class NotificationConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        notifier.configurar_avisos(None)

    def test_true_accepts_all_types_without_iterating_a_boolean(self) -> None:
        notifier.configurar_avisos(False)
        notifier.configurar_avisos(True)
        self.assertFalse(notifier._SILENCIO)
        self.assertEqual(notifier._TIPOS_PERMITIDOS, set())

    def test_false_and_type_lists_keep_their_existing_meaning(self) -> None:
        notifier.configurar_avisos(False)
        self.assertTrue(notifier._SILENCIO)
        notifier.configurar_avisos(["compra"])
        self.assertFalse(notifier._SILENCIO)
        self.assertEqual(notifier._TIPOS_PERMITIDOS, {"compra"})
        for value in (None, []):
            notifier.configurar_avisos(value)
            self.assertFalse(notifier._SILENCIO)
            self.assertEqual(notifier._TIPOS_PERMITIDOS, set())


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_yaml_reaches_the_configured_trader_without_loading_a_wallet(
        self,
    ) -> None:
        plan = copy.deepcopy(
            yaml.safe_load(
                (ROOT / "configs/four-strategies.yaml").read_text(encoding="utf-8")
            )
        )
        # In-memory fixture only. Do not edit or start the actual configuration.
        plan["enabled"] = True
        signature = inspect.signature(UniversalTrader.__init__)
        captured = {}

        class FakeTrader:
            def __init__(self, **kwargs: object) -> None:
                signature.bind(self, **kwargs)
                captured["kwargs"] = kwargs
                self.start = AsyncMock()
                captured["trader"] = self

            def configure_four(self, settings: dict, *, no_volume_exit_seconds: float = 0,
                               second_block_policy: dict | None = None,
                               evaluation_policy: dict | None = None) -> None:
                captured["settings"] = settings
                captured["inactivity"] = no_volume_exit_seconds
                captured["second_policy"] = second_block_policy
                captured["evaluation_policy"] = evaluation_policy

        with (
            patch.object(bot_runner, "load_bot_config", return_value=plan),
            patch("trading.second_block_policy.load_second_policy", return_value={"enabled": False}),
            patch.object(bot_runner, "setup_logging"),
            patch.object(bot_runner, "print_config_summary"),
            patch.object(
                bot_runner, "get_platform_from_config", return_value=Platform.PUMP_FUN
            ),
            patch.object(
                bot_runner, "validate_platform_listener_combination", return_value=True
            ),
            patch(
                "platforms.platform_factory.registry.is_platform_supported",
                return_value=True,
            ),
            patch("trading.four_strategy_runtime.FourStrategyTrader", FakeTrader),
            patch.object(bot_runner, "_auto_scale", new_callable=AsyncMock) as scale,
            patch.object(
                notifier,
                "_send_blocking",
                side_effect=AssertionError("No messages may be sent"),
            ),
        ):
            try:
                await bot_runner.start_bot("unused-test-configuration.yaml")
                scale.assert_not_called()
            finally:
                notifier.configurar_avisos(None)
        captured["trader"].start.assert_awaited_once()
        self.assertEqual(captured["kwargs"]["fraccion_saldo"], 0.90)
        self.assertEqual(captured["kwargs"]["max_posiciones"], 4)
        self.assertEqual(captured["kwargs"]["stop_loss_percentage"], .22)
        self.assertEqual(captured["settings"], plan["four_strategies"])
        self.assertEqual(captured["inactivity"], 300)
        self.assertEqual(captured["evaluation_policy"]["batch_size"], 5)
        self.assertEqual(captured["evaluation_policy"]["scope"], "per_strategy")
        self.assertEqual(captured["evaluation_policy"]["legacy_loss_guard"], "batch_roi_only")
        self.assertIs(captured["kwargs"]["paper_trade"], False)

    def test_22_percent_stop_keeps_the_existing_ledger_identity_and_input(self) -> None:
        plan = yaml.safe_load((ROOT / "configs/four-strategies.yaml").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            settings = copy.deepcopy(plan["four_strategies"])
            settings["database"] = str(Path(directory) / "book.sqlite3")
            original = copy.deepcopy(settings)
            identity = hashlib.sha256(json.dumps(settings, sort_keys=True, allow_nan=False).encode()).hexdigest()
            trader = object.__new__(FourStrategyTrader)
            trader.wallet = SimpleNamespace(pubkey=settings["expected_wallet"])
            trader.wallet_pool = None
            trader.paper_trade = False
            trader.max_posiciones = 4
            trader.fraccion_saldo = .90
            trader.stop_loss_percentage = .22
            trader.buyer = object()
            trader.configure_four(settings, evaluation_policy=plan["five_trade_evaluation"])
            self.assertEqual(trader.four["exit"]["stop_loss_fraction"], .22)
            self.assertEqual(settings, original)
            with closing(trader.book.connect()) as db:
                self.assertEqual(db.execute("SELECT policy FROM settings WHERE id=1").fetchone()[0], identity)
            self.assertIn("SL -22%", trader._cartel_de_salidas())
            self.assertIn("ROI neto SOL >0", trader._cartel_de_salidas())
            self.assertNotIn("3 perdidas", trader._cartel_de_salidas())


if __name__ == "__main__":
    unittest.main()
