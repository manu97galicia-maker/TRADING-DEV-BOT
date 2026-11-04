"""Synthetic WebSocket collection with no RPC, SQL, signing, or trading."""

# The stream fixtures expose only the callbacks needed by this collector.
# ruff: noqa: ANN201, ARG001, SLF001

import asyncio
import base64
import copy
import json
import struct
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import test_dev_only_mode as mode
import test_dev_second_window_source as strict
import test_second_block_source as events
from solders.pubkey import Pubkey

import trading.four_strategy_runtime as runtime_module
from monitoring import volumen
from monitoring.pressure_trial_source import PressureTrialSource
from trading.four_strategy_runtime import FourStrategyTrader


def trade_log(mint: Pubkey, wallet: Pubkey, *, buy: bool = True, lamports: int = 1_000_000_000) -> str:
    body = (bytes(mint) + struct.pack("<QQB", lamports, 1_000_000, buy)
            + bytes(wallet) + struct.pack("<qQQQQ", int(time.time()), 30_000_000_000,
                                         793_100_000_000_000, 1_000_000_000, 793_100_000_000_000))
    return "Program data: " + base64.b64encode(volumen.DISC_TRADE + body).decode()


def notification(logs: list[str], *, slot: int = 100, signature: str = "synthetic") -> dict:
    return {"method": "logsNotification", "params": {"result": {
        "context": {"slot": slot}, "value": {"err": None, "signature": signature, "logs": logs}}}}


class SyntheticSocket:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = deque(messages)
        self.send = AsyncMock()

    async def recv(self) -> str:
        # Let synthetic preparation/delivery tasks complete between frames.
        for _ in range(10):
            await asyncio.sleep(0)
        if not self.messages:
            raise asyncio.CancelledError
        return json.dumps(self.messages.popleft())


class SyntheticConnection:
    def __init__(self, socket: SyntheticSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> SyntheticSocket:
        return self.socket

    async def __aexit__(self, *args: object) -> bool:
        return False


async def receive_frames(test: unittest.TestCase, source: PressureTrialSource,
                         frames: list[dict], callback: object) -> SyntheticSocket:
    socket = SyntheticSocket([{"result": 1}, {"id": 2, "result": 2}, *frames])
    with (patch("monitoring.pressure_trial_source.websockets.connect", return_value=SyntheticConnection(socket)),
          patch("sqlite3.connect", side_effect=AssertionError("No SQL in the receive loop")),
          test.assertRaises(asyncio.CancelledError)):
        await source.run(callback)
    return socket


class FilteredVolumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.held, self.other, self.wallet = (Pubkey.new_unique() for _ in range(3))
        cleaner = patch.object(volumen, "_limpiar")
        cleaner.start()
        self.addCleanup(cleaner.stop)
        for mint in (self.held, self.other):
            self.addCleanup(volumen._olvidar, str(mint))
            self.addCleanup(volumen._nacimiento.pop, str(mint), None)

    def assert_other_not_aggregated(self) -> None:
        for name in ("_ops", "_precio", "_historia_precio", "_nacimiento", "_curva", "_first_slot",
                     "_last_slot", "_first_slot_wallets", "_first_slot_sol", "_second_slot"):
            with self.subTest(cache=name):
                self.assertNotIn(str(self.other), getattr(volumen, name))

    def test_allowlist_preserves_held_price_flow_and_block_activity_only(self) -> None:
        allowed = {str(self.held)}
        touched = volumen.registrar_logs([
            trade_log(self.other, self.wallet), trade_log(self.held, self.wallet)], slot=100, allowed_mints=allowed)
        volumen.registrar_logs([trade_log(self.held, self.wallet, buy=False, lamports=200_000_000)],
                               slot=101, allowed_mints=allowed)
        self.assertEqual(touched, [str(self.held)])
        self.assertAlmostEqual(volumen.precio_en_vivo(str(self.held), 1), 30 / 793_100_000)
        self.assertEqual(volumen.presion(str(self.held)), (1, .2))
        self.assertAlmostEqual(volumen.volumen_sol(str(self.held)), 1.2)
        self.assertEqual(volumen.compras_primer_bloque(str(self.held)), 1)
        self.assertAlmostEqual(volumen.metricas_segundo_bloque(str(self.held))["venta_sol"], .2)
        self.assertEqual(len(volumen._historia_precio[str(self.held)]), 2)
        self.assert_other_not_aggregated()

    def test_empty_allowlist_rejects_before_decoding_wallet_or_mutating_caches(self) -> None:
        with patch.object(volumen, "_b58", wraps=volumen._b58) as decode:
            self.assertEqual(volumen.registrar_logs([trade_log(self.other, self.wallet)],
                                                   slot=100, allowed_mints=set()), [])
        decode.assert_called_once_with(bytes(self.other))
        self.assert_other_not_aggregated()

    def test_omitted_allowlist_keeps_the_existing_all_mint_behavior(self) -> None:
        touched = volumen.registrar_logs([
            trade_log(self.held, self.wallet), trade_log(self.other, self.wallet)], slot=100)
        self.assertEqual(touched, [str(self.held), str(self.other)])
        self.assertEqual(volumen.volumen_sol(str(self.other)), 1)


class CollectorOnlySocketTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.held, self.other, self.wallet = (Pubkey.new_unique() for _ in range(3))
        for mint in (self.held, self.other):
            self.addCleanup(volumen._olvidar, str(mint))
            self.addCleanup(volumen._nacimiento.pop, str(mint), None)
        self.client = Mock()
        self.callback = AsyncMock()
        self.observer = SimpleNamespace(on_slot=Mock(), on_logs=Mock(), disconnect=Mock(), close=AsyncMock())

    def source(self, **kwargs: object) -> PressureTrialSource:
        return PressureTrialSource("wss://synthetic.invalid", object(), self.client,
                                   Mock(), Mock(), Mock(return_value=True),
                                   event_observer=self.observer, **kwargs)

    async def test_empty_tracked_set_skips_aggregation_and_pressure_selection_without_rpc_or_sql(self):
        tracked = Mock(return_value=set())
        source = self.source(observer_only=True, tracked_mints=tracked)
        source.consider = Mock()
        logs = [trade_log(self.other, self.wallet)]
        with patch.object(volumen, "registrar_logs") as aggregate:
            await receive_frames(self, source, [notification(logs)], self.callback)
        self.observer.on_logs.assert_called_once_with(logs, 100, "synthetic")
        tracked.assert_called_once_with()
        aggregate.assert_not_called()
        source.consider.assert_not_called()
        source.signal_reader.assert_not_called()
        self.client.assert_not_called()
        self.assertEqual(self.client.mock_calls, [])
        self.callback.assert_not_awaited()

    async def test_observer_runs_first_and_only_held_mints_retain_live_exit_data(self):
        order = []
        self.observer.on_logs.side_effect = lambda *_args: order.append("observer")
        tracked = {str(self.held)}
        source = self.source(observer_only=True, tracked_mints=lambda: tracked)
        source.consider = Mock()
        logs = [trade_log(self.other, self.wallet), trade_log(self.held, self.wallet)]
        register = volumen.registrar_logs

        def aggregate(*args: object, **kwargs: object) -> list[str]:
            order.append("aggregate")
            return register(*args, **kwargs)

        with patch.object(volumen, "registrar_logs", side_effect=aggregate) as aggregate_mock:
            await receive_frames(self, source, [notification(logs)], self.callback)
        self.assertEqual(order, ["observer", "aggregate"])
        aggregate_mock.assert_called_once_with(logs, slot=100, allowed_mints=tracked)
        source.consider.assert_not_called()
        source.signal_reader.assert_not_called()
        self.assertEqual(self.client.mock_calls, [])
        self.assertIsNotNone(volumen.precio_en_vivo(str(self.held), 1))
        self.assertEqual(volumen.presion(str(self.held)), (1, 0))
        self.assertNotIn(str(self.other), volumen._ops)

    async def test_default_multi_source_keeps_original_order_and_aggregation_arguments(self):
        order = []
        tracked = Mock(side_effect=AssertionError("Tracking is only needed in observer-only mode"))
        source = self.source(tracked_mints=tracked)
        source.consider = Mock(side_effect=lambda *_args: order.append("consider"))
        self.observer.on_logs.side_effect = lambda *_args: order.append("observer")
        logs = [trade_log(self.other, self.wallet)]

        def aggregate(*args: object, **kwargs: object) -> list[str]:
            order.append("aggregate")
            return [str(self.other)]

        with patch.object(volumen, "registrar_logs", side_effect=aggregate) as aggregate_mock:
            await receive_frames(self, source, [notification(logs)], self.callback)
        self.assertFalse(source.observer_only)
        self.assertEqual(order, ["aggregate", "observer", "consider"])
        aggregate_mock.assert_called_once_with(logs, slot=100)
        source.consider.assert_called_once_with([str(self.other)], self.callback)
        tracked.assert_not_called()


class DevProofCollectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = strict.DevSecondWindowSourceTests.asyncSetUp

    async def test_all_creations_still_count_and_dev_proof_delivers_only_in_next_produced_block(self):
        self.policy["require_first_block_activity"] = False
        other = Pubkey.new_unique()
        creation = events.logs(events.create(self.mint, self.creator, user=self.a),
                               events.trade(self.mint, self.a), events.create(other, self.creator, user=self.b))
        collector = PressureTrialSource("wss://synthetic.invalid", object(), Mock(), Mock(), Mock(), Mock(),
                                        event_observer=self.source, observer_only=True, tracked_mints=set)
        frames = [
            {"method": "slotNotification", "params": {"result": {"slot": 100, "parent": 99}}},
            notification(creation, signature="creation-signature"),
            {"method": "slotNotification", "params": {"result": {"slot": 105, "parent": 100}}},
        ]
        with patch.object(volumen, "registrar_logs") as aggregate:
            await receive_frames(self, collector, frames, AsyncMock())
        aggregate.assert_not_called()
        self.source.history.observe.assert_has_calls([
            call(str(self.a), str(self.mint)), call(str(self.b), str(other))])
        self.assertEqual(self.source.history.observe.call_count, 2)
        self.callback.assert_awaited_once()
        delivered = self.callback.await_args.args[0]
        self.assertEqual(delivered.additional_data["dev_entry_target_slot"], 105)
        self.assertEqual(delivered.additional_data["acquisition_kind"], "second-block-dev")


class RuntimeCollectorSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_dev_history_profile_uses_held_set_and_classic_profiles_keep_general_data(self):
        for only_dev, entry_rule, expected in (
            (True, "creator_migration_only", True),
            (True, "second_block", False),
            (False, "creator_migration_only", False),
        ):
            with self.subTest(only_dev=only_dev, entry_rule=entry_rule):
                trader = object.__new__(FourStrategyTrader)
                trader.only_dev = only_dev
                trader.second_policy = {**copy.deepcopy(mode.SECOND_POLICY), "entry_rule": entry_rule}
                trader.four = copy.deepcopy(mode.PLAN["four_strategies"])
                trader.wss_endpoint = "wss://synthetic.invalid"
                trader.platform_implementations = object()
                trader.solana_client = Mock()
                trader.sol_usd = 100
                trader.sol_quote_at = time.time()
                trader.rpc = AsyncMock()
                trader.bought_mints = set()
                trader.telegram_calls = {}
                trader.platform = mode.Platform.PUMP_FUN
                trader._on_dexpaid_token = AsyncMock()
                trader.audit_loop = AsyncMock()
                trader.book = Mock()
                collector = SimpleNamespace(run=AsyncMock())
                with (patch.object(runtime_module, "PressureTrialSource", return_value=collector) as factory,
                      patch("monitoring.second_block_source.SecondBlockSource", return_value=object()),
                      patch("monitoring.telegram_source.TelegramSource", return_value=SimpleNamespace(run=AsyncMock()))):
                    await trader._run_curva96_mode()
                self.assertIs(factory.call_args.kwargs["observer_only"], expected)
                tracked = factory.call_args.kwargs["tracked_mints"]
                self.assertEqual(tracked(), set())  # Fixtures need not set this before collection.
                trader._four_monitored = {"held"}
                self.assertIs(tracked(), trader._four_monitored)
                trader._four_monitored.add("another-held")
                self.assertEqual(tracked(), {"held", "another-held"})
                self.assertEqual(trader.book.mock_calls, [])
                self.assertEqual(trader.solana_client.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
