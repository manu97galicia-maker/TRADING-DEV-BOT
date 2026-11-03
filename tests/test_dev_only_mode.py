"""Dev-only admission and recovery with fake I/O; no wallets or live startup."""

import asyncio
import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import yaml

import bot_runner
import trading.four_strategy_preflight as preflight
import trading.four_strategy_runtime as runtime_module
from interfaces.core import Platform
from trading.four_strategy_runtime import FourStrategyTrader, SharedBuyer
from trading.second_block_policy import STRATEGY_ID

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SOURCES = (
    "strong-pressure-8k", "strength-21k", "first-block-four-buyers", "telegram-immediate",
)
PLAN = yaml.safe_load((ROOT / "configs/four-strategies.yaml").read_text(encoding="utf-8"))
SECOND_POLICY = {
    **yaml.safe_load((ROOT / "configs/second-block-dev.yaml").read_text(encoding="utf-8")),
    "enabled": True,
}


class DevOnlySignalTests(unittest.TestCase):
    def test_queued_telegram_and_onchain_tokens_are_rejected_before_selection(self) -> None:
        trader = object.__new__(FourStrategyTrader)
        trader.only_dev = True
        trader.signal_for = Mock(return_value={"strategy": "strong-pressure-8k"})
        trader.second_source = SimpleNamespace(signal=Mock())
        for data in (
            {},
            {"four_strategy_id": "strong-pressure-8k"},
            {"four_strategy_id": STRATEGY_ID},
            {"acquisition_kind": "telegram", "signal_received_at": time.time()},
        ):
            with self.subTest(data=data):
                token = SimpleNamespace(mint="fake-mint", additional_data=data)
                self.assertIsNone(trader.current_signal(token))
        trader.signal_for.assert_not_called()
        trader.second_source.signal.assert_not_called()

    def test_dev_token_uses_the_live_second_block_signal(self) -> None:
        trader = object.__new__(FourStrategyTrader)
        trader.only_dev = True
        signal = {"strategy": STRATEGY_ID, "target_slot": 101}
        trader.second_source = SimpleNamespace(signal=Mock(return_value=signal))
        trader.signal_for = Mock()
        token = SimpleNamespace(
            mint="fake-mint", additional_data={"acquisition_kind": "second-block-dev"},
        )
        self.assertIs(trader.current_signal(token), signal)
        trader.second_source.signal.assert_called_once_with("fake-mint")
        trader.signal_for.assert_not_called()

    def test_onchain_selector_is_disabled_without_reading_market_state(self) -> None:
        trader = object.__new__(FourStrategyTrader)
        trader.only_dev = True
        with patch.object(runtime_module, "choose_signal") as choose:
            self.assertIsNone(trader.signal_for("fake-mint"))
        choose.assert_not_called()

    def test_normal_mode_still_accepts_a_fresh_telegram_signal(self) -> None:
        trader = object.__new__(FourStrategyTrader)
        trader.only_dev = False
        trader.sol_usd = 100
        token = SimpleNamespace(mint="fake-mint", additional_data={
            "acquisition_kind": "telegram", "signal_received_at": time.time(),
            "acquisition_source": "fake-channel",
        })
        self.assertEqual(trader.current_signal(token)["strategy"], "telegram-immediate")


class DevOnlyBuyerTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_other_source_signals_never_reach_holdings_rpc_or_broker(self) -> None:
        for strategy in ORIGINAL_SOURCES:
            with self.subTest(strategy=strategy):
                trader = SimpleNamespace(
                    only_dev=True,
                    current_signal=Mock(return_value={"strategy": strategy}),
                    reference_age=Mock(return_value=0),
                    raw_holdings=AsyncMock(),
                    rpc=AsyncMock(),
                    book=SimpleNamespace(reserve=Mock()),
                )
                broker = SimpleNamespace(execute=AsyncMock())
                token = SimpleNamespace(mint="fake-mint", additional_data={})
                result = await SharedBuyer(trader, broker).execute(token, amount_sol=999)
                self.assertFalse(result.success)
                trader.raw_holdings.assert_not_awaited()
                trader.rpc.assert_not_awaited()
                trader.book.reserve.assert_not_called()
                broker.execute.assert_not_awaited()

    async def test_dev_signal_retains_the_existing_holdings_safeguard(self) -> None:
        trader = SimpleNamespace(
            only_dev=True,
            current_signal=Mock(return_value={"strategy": STRATEGY_ID}),
            reference_age=Mock(return_value=0),
            raw_holdings=AsyncMock(return_value=(10, 100)),
            rpc=AsyncMock(),
        )
        broker = SimpleNamespace(execute=AsyncMock())
        token = SimpleNamespace(mint="fake-mint", additional_data={})
        result = await SharedBuyer(trader, broker).execute(token)
        self.assertFalse(result.success)
        self.assertIn("Existing holdings", result.error_message)
        trader.raw_holdings.assert_awaited_once_with("fake-mint")
        trader.rpc.assert_not_awaited()
        broker.execute.assert_not_awaited()


class DevOnlyFeedAndRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_dev_collector_and_audit_run_without_constructing_telegram(self) -> None:
        trader = object.__new__(FourStrategyTrader)
        trader.only_dev = True
        trader.second_policy = copy.deepcopy(SECOND_POLICY)
        trader.four = copy.deepcopy(PLAN["four_strategies"])
        trader.rpc = AsyncMock()
        trader.platform_implementations = object()
        trader.solana_client = object()
        trader.sol_usd = 100
        trader.sol_quote_at = time.time()
        trader.book = SimpleNamespace(paused=Mock(return_value=False))
        trader._on_dexpaid_token = AsyncMock()
        trader.wss_endpoint = "wss://fake.invalid"
        trader.signal_for = Mock(return_value=None)
        trader.bought_mints = set()
        trader.telegram_calls = {}
        trader.platform = Platform.PUMP_FUN
        trader.audit_loop = AsyncMock()
        collector = SimpleNamespace(run=AsyncMock())
        observer = object()
        with (
            patch.object(runtime_module, "PressureTrialSource", return_value=collector) as source,
            patch("monitoring.telegram_source.TelegramSource") as telegram,
            patch("monitoring.second_block_source.SecondBlockSource", return_value=observer),
        ):
            await trader._run_curva96_mode()
        self.assertIs(source.call_args.kwargs["event_observer"], observer)
        collector.run.assert_awaited_once_with(trader._on_dexpaid_token)
        trader.audit_loop.assert_awaited_once()
        telegram.assert_not_called()

    async def test_existing_positions_from_every_original_source_resume_exits(self) -> None:
        positions = [
            {"nonce": f"nonce-{index}", "mint": f"fake-{index}", "strategy": strategy,
             "details": json.dumps({"entry_price": 1e-7, "original_qty": 10})}
            for index, strategy in enumerate(ORIGINAL_SOURCES)
        ]
        trader = object.__new__(FourStrategyTrader)
        trader.only_dev = True
        trader.book = SimpleNamespace(positions=Mock(return_value=positions))
        trader._four_monitored = set()
        trader._four_tasks = set()
        trader.platform_implementations = object()
        trader.solana_client = object()
        trader._monitor_tiered_exit = AsyncMock()
        tokens = [SimpleNamespace(mint=position["mint"], additional_data={}) for position in positions]
        with patch("trading.token_builder.build_pumpfun_token_info", new_callable=AsyncMock) as build:
            build.side_effect = tokens
            await trader._reanudar_posiciones()
            await asyncio.gather(*list(trader._four_tasks))
        self.assertEqual(trader._monitor_tiered_exit.await_count, len(ORIGINAL_SOURCES))
        self.assertEqual(
            [entry.args[0].additional_data["four_strategy_id"]
             for entry in trader._monitor_tiered_exit.await_args_list],
            list(ORIGINAL_SOURCES),
        )
        build.assert_has_awaits([
            call(position["mint"], trader.platform_implementations, trader.solana_client,
                 permitir_migradas=True)
            for position in positions
        ])


class DevOnlyLedgerTests(unittest.TestCase):
    @staticmethod
    def trader(settings: dict) -> FourStrategyTrader:
        trader = object.__new__(FourStrategyTrader)
        trader.wallet = SimpleNamespace(pubkey=settings["expected_wallet"])
        trader.wallet_pool = None
        trader.paper_trade = False
        trader.max_posiciones = 4
        trader.fraccion_saldo = .90
        trader.stop_loss_percentage = .22
        trader.buyer = object()
        return trader

    def test_mode_change_reuses_policy_identity_and_preserves_existing_book_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = copy.deepcopy(PLAN["four_strategies"])
            settings["database"] = str(Path(directory) / "fake-book.sqlite3")
            original = copy.deepcopy(settings)
            initial = self.trader(settings)
            initial.configure_four(settings, second_block_policy=copy.deepcopy(SECOND_POLICY),
                                   evaluation_policy=PLAN["five_trade_evaluation"])
            position = initial.book.reserve("fake-mint", ORIGINAL_SOURCES[0],
                1_000_000_000, 100, 30_000_000, .15, 3_000_000, {}, fraction=.90)
            self.assertIsNotNone(position)
            initial.book.opened(position["nonce"], 100, {"entry_price": 1e-7, "original_qty": 10})
            positions = initial.book.positions()
            with closing(initial.book.connect()) as db, db:
                db.execute("INSERT INTO pauses VALUES (?)", (STRATEGY_ID,))
                db.execute("INSERT INTO outcomes(nonce,strategy,net,details) VALUES (?,?,?,?)",
                           ("old-outcome", ORIGINAL_SOURCES[1], -1, "{}"))
            selected = self.trader(settings)
            selected.configure_four(settings, second_block_policy=copy.deepcopy(SECOND_POLICY),
                                    evaluation_policy=PLAN["five_trade_evaluation"], only_dev=True)
            self.assertTrue(selected.only_dev)
            self.assertEqual(selected.book.positions(), positions)
            self.assertEqual(settings, original)
            self.assertEqual(selected.fraccion_saldo, .90)
            self.assertEqual(selected.max_posiciones, 4)
            self.assertEqual(selected.four["exit"]["stop_loss_fraction"], .22)
            with closing(selected.book.connect()) as db:
                identity = hashlib.sha256(json.dumps(settings, sort_keys=True, allow_nan=False).encode()).hexdigest()
                self.assertEqual(db.execute("SELECT policy FROM settings WHERE id=1").fetchone()[0], identity)
                self.assertEqual(db.execute("SELECT strategy FROM pauses").fetchall(), [(STRATEGY_ID,)])
                self.assertEqual(db.execute("SELECT nonce,strategy,net FROM outcomes").fetchall(),
                                 [("old-outcome", ORIGINAL_SOURCES[1], -1)])


class DevOnlyPreflightTests(unittest.TestCase):
    def test_local_preflight_skips_telegram_but_keeps_wallet_and_rpc_checks(self) -> None:
        plan = copy.deepcopy(PLAN)
        # No Telegram source config or session path is needed for this mode.
        plan["filters"].pop("telegram_calls", None)
        plan["four_strategies"]["callout_publication"] = {"enabled": False}
        public = plan["four_strategies"]["expected_wallet"]
        env = {
            "SOLANA_PRIVATE_KEY": "fake-key-never-decoded",
            "SOLANA_NODE_RPC_ENDPOINT": "https://fake.invalid",
            "SOLANA_NODE_WSS_ENDPOINT": "wss://fake.invalid",
        }
        config_path = Mock(spec=Path)
        config_path.resolve.side_effect = AssertionError("No Telegram session path may be read")
        keypair = SimpleNamespace(pubkey=Mock(return_value=public))

        def dependency(name: str) -> object:
            self.assertNotEqual(name, "telethon", "Dev-only mode must not require telethon")
            return object()

        with (
            patch.object(preflight, "_environment", return_value=env),
            patch.object(preflight.importlib.util, "find_spec", side_effect=dependency) as lookup,
            patch("solders.keypair.Keypair") as keypair_type,
        ):
            keypair_type.from_base58_string.return_value = keypair
            report = preflight.inspect_local(plan, config_path, only_dev=True)
        checks = {check["name"]: check for check in report["checks"]}
        self.assertEqual(report["entry_mode"], "dev_only")
        self.assertNotIn("telegram_local", checks)
        self.assertEqual(lookup.call_args_list, [call("aiohttp"), call("websockets"), call("solders")])
        config_path.resolve.assert_not_called()
        for name in ("dependencies", "wallet_identity", "rpc_config", "wss_config"):
            with self.subTest(check=name):
                self.assertEqual(checks[name]["status"], "pass")
        self.assertEqual(report["configured_public_wallet"], public)
        self.assertFalse(report["online"])
        self.assertFalse(report["live_started"])
        self.assertFalse(report["ready_for_manual_start"])


class DevOnlyStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_or_missing_coordinator_is_rejected_before_construction(self) -> None:
        for plan in ({}, {"four_strategies": {"enabled": False}}):
            with (
                self.subTest(plan=plan),
                patch.object(bot_runner, "load_bot_config", return_value=plan),
                patch.object(bot_runner, "setup_logging") as setup,
                patch.object(bot_runner, "UniversalTrader") as legacy_trader,
                patch.object(runtime_module, "FourStrategyTrader") as four_trader,
            ):
                with self.assertRaisesRegex(ValueError, "shared coordinator"):
                    await bot_runner.start_bot("unused-fake-configuration.yaml", only_dev=True)
                setup.assert_not_called()
                legacy_trader.assert_not_called()
                four_trader.assert_not_called()

    async def test_start_bot_propagates_mode_and_existing_risk_to_a_fake_trader(self) -> None:
        plan = copy.deepcopy(PLAN)
        plan["enabled"] = True
        plan["four_strategies"]["enabled"] = True
        trader = SimpleNamespace(configure_four=Mock(), start=AsyncMock())
        with (
            patch.object(bot_runner, "load_bot_config", return_value=plan),
            patch.object(bot_runner, "setup_logging"),
            patch.object(bot_runner, "print_config_summary"),
            patch.object(bot_runner, "get_platform_from_config", return_value=Platform.PUMP_FUN),
            patch.object(bot_runner, "validate_platform_listener_combination", return_value=True),
            patch("platforms.platform_factory.registry.is_platform_supported", return_value=True),
            patch("monitoring.notifier.configurar_avisos"),
            patch.object(runtime_module, "FourStrategyTrader", return_value=trader) as construct,
            patch("trading.second_block_policy.load_second_policy", return_value=copy.deepcopy(SECOND_POLICY)),
            patch.object(bot_runner, "_auto_scale", new_callable=AsyncMock) as scale,
        ):
            await bot_runner.start_bot("unused-fake-configuration.yaml", only_dev=True)
        trader.configure_four.assert_called_once_with(
            plan["four_strategies"],
            no_volume_exit_seconds=float(plan["trade"]["sin_volumen_bajo_entrada_seg"]),
            second_block_policy=SECOND_POLICY,
            evaluation_policy=plan["five_trade_evaluation"],
            only_dev=True,
        )
        self.assertEqual(construct.call_args.kwargs["fraccion_saldo"], .90)
        self.assertEqual(construct.call_args.kwargs["stop_loss_percentage"], .22)
        self.assertEqual(construct.call_args.kwargs["max_posiciones"], 4)
        trader.start.assert_awaited_once()
        scale.assert_not_awaited()


class DevOnlyCliTests(unittest.TestCase):
    def test_cli_validation_lists_only_dev_without_startup_or_async_execution(self) -> None:
        spec = importlib.util.spec_from_file_location("fake_dev_only_cli", ROOT / "run_four_strategies.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        plan = copy.deepcopy(PLAN)
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["run_four_strategies.py", "--only-dev", "--config", "unused.yaml"]),
            patch.object(cli, "check_plan", return_value=plan) as validate,
            patch("trading.second_block_policy.load_second_policy", return_value=copy.deepcopy(SECOND_POLICY)),
            patch.object(cli.asyncio, "run", side_effect=AssertionError("Validation must not start async work")),
            redirect_stdout(output),
        ):
            cli.main()
        report = json.loads(output.getvalue())
        self.assertEqual(report["mode"], "validation_only")
        self.assertTrue(report["valid"])
        self.assertEqual(report["strategies"], [STRATEGY_ID])
        self.assertEqual(report["entry_fraction_inclusive"], .90)
        self.assertEqual(report["initial_stop"], .22)
        self.assertEqual(report["max_shared_positions"], 4)
        self.assertEqual(report["five_trade_evaluation"]["legacy_loss_guard"], "batch_roi_only")
        validate.assert_called_once_with(Path("unused.yaml"), only_dev=True)


if __name__ == "__main__":
    unittest.main()
