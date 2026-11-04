"""Notifications use fake metadata and mocked transport; never send messages."""

# Integration tests exercise the bundle private hooks without constructing a wallet.
# ruff: noqa: SLF001

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import yaml

from monitoring.four_strategy_notifications import (
    entry_reason,
    purchase_message,
    token_labels,
)
from trading.four_strategy_runtime import FourStrategyTrader
from trading.universal_trader import UniversalTrader

ROOT = Path(__file__).resolve().parents[1]


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.token = SimpleNamespace(name="mint", symbol="?", mint="mint",
            additional_data={"four_strategy_id": "strong-pressure-8k"})
        self.features = {"strategy": "strong-pressure-8k", "buyers": 12,
            "volume_usd": 1400, "buy_sol": 3, "sell_sol": 1}
        self.details = {"features": self.features, "entry_sol_usd": 100}
        self.plan = yaml.safe_load((ROOT / "configs/four-strategies.yaml").read_text())
        self.result = SimpleNamespace(price=1e-7, amount=1e6, tx_signature="signature")

    async def test_metadata_is_optional_and_must_match_the_mint(self) -> None:
        rpc = AsyncMock(return_value={"id": "mint", "content": {"metadata": {
            "name": "A <token>", "symbol": "TKN"}}})
        self.assertEqual(await token_labels(self.token, rpc), ("A <token>", "TKN"))
        rpc.return_value["id"] = "different-mint"
        self.assertEqual(await token_labels(self.token, rpc), ("Nombre no disponible", ""))
        rpc.side_effect = TimeoutError
        self.assertEqual(await token_labels(self.token, rpc), ("Nombre no disponible", ""))

    def test_message_has_name_ca_evidence_and_actual_ladder(self) -> None:
        self.token.name, self.token.symbol = "A <token>", "TKN"
        msg = purchase_message(self.token, self.result, self.details, self.plan["four_strategies"]["exit"])
        for expected in ["A &lt;token&gt;", "TKN", "CA: <code>mint</code>",
                         "12 compradores", "$1,400/8s", "3.000 SOL / ventas 1.000 SOL",
                         "TP1 +50%", "0.100 SOL"]:
            self.assertIn(expected, msg)

    def test_each_source_uses_its_actual_evidence(self) -> None:
        self.assertIn("21K", entry_reason({**self.features, "strategy": "strength-21k"}))
        self.assertIn("4 compradores", entry_reason({"strategy": "first-block-four-buyers",
            "first_block": {"buyers": 4, "buy_sol": 5}}))
        self.assertIn("@channel", entry_reason({"strategy": "telegram-immediate", "source": "@channel"}))

    async def test_one_transport_call_then_callout_queue_after_labels(self) -> None:
        runtime = SimpleNamespace(
            rpc=AsyncMock(return_value={"id": "mint", "content": {"metadata": {"name": "Token", "symbol": "TK"}}}),
            book=SimpleNamespace(positions=lambda: [{"mint": "mint", "nonce": "n", "details": json.dumps(self.details)}]),
            four=self.plan["four_strategies"], update_details=Mock(),
        )
        with patch("monitoring.notifier.notify") as notify, patch(
            "monitoring.confirmed_callouts.schedule_confirmed_callout"
        ) as queue:
            await FourStrategyTrader._notify_four_purchase(runtime, self.token, self.result)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs, {"tipo": "compra"})
        self.assertIn("Token", notify.call_args.args[0])
        queue.assert_called_once()

    async def test_slow_metadata_does_not_delay_parent_position_monitor(self) -> None:
        runtime = object.__new__(FourStrategyTrader)
        runtime._purchase_notices = set()
        runtime._four_tasks = set()
        runtime._notify_four_purchase = AsyncMock()
        async def parent(*_args: object, **_kwargs: object) -> None:
            self.assertTrue(self.token.additional_data["four_purchase_notice"])
            self.assertEqual(len(runtime._four_tasks), 1)
        with patch("trading.universal_trader.UniversalTrader._handle_successful_buy", side_effect=parent):
            await FourStrategyTrader._handle_successful_buy(runtime, self.token, self.result)
            await asyncio.gather(*runtime._four_tasks)
        runtime._notify_four_purchase.assert_awaited_once()

    async def test_legacy_path_keeps_monitoring_and_skips_its_duplicate_buy_notice(self) -> None:
        runtime = object.__new__(UniversalTrader)
        runtime._compras_de_la_tanda = 0
        runtime.traded_mints, runtime.bought_mints = set(), set()
        runtime.callouts = {"enabled": False}
        runtime.marry_mode, runtime.exit_strategy = False, "tp_sl"
        for method in ["_apunta_compra_por_origen", "_cuenta_compra_para_el_resumen",
                       "_anota_firma", "_persist_bought_mint", "_log_trade"]:
            setattr(runtime, method, Mock())
        runtime._handle_tp_sl_exit = AsyncMock()
        self.token.platform = SimpleNamespace(value="pump_fun")
        self.token.token_program_id = None
        self.token.additional_data["four_purchase_notice"] = True
        with patch("monitoring.notifier.notify") as notify:
            await UniversalTrader._handle_successful_buy(runtime, self.token, self.result)
        notify.assert_not_called()
        runtime._handle_tp_sl_exit.assert_awaited_once_with(self.token, self.result)
        self.assertIn("mint", runtime.bought_mints)


if __name__ == "__main__":
    unittest.main()
