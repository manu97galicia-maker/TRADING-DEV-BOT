"""Configuration startup with a recording fake trader; never connects or trades."""

# Private flags and positional boolean values are the regression cases under test.
# ruff: noqa: SLF001, FBT003

import copy
import inspect
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

import bot_runner
from interfaces.core import Platform
from monitoring import notifier
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

            def configure_four(self, settings: dict, *, no_volume_exit_seconds: float = 0) -> None:
                captured["settings"] = settings
                captured["inactivity"] = no_volume_exit_seconds

        with (
            patch.object(bot_runner, "load_bot_config", return_value=plan),
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
        self.assertEqual(captured["kwargs"]["fraccion_saldo"], 0.40)
        self.assertEqual(captured["settings"], plan["four_strategies"])
        self.assertEqual(captured["inactivity"], 300)
        self.assertIs(captured["kwargs"]["paper_trade"], False)


if __name__ == "__main__":
    unittest.main()
