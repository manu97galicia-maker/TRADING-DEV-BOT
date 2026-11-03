"""Synthetic creation/trade logs and a fake slot clock; no live transactions."""

# ruff: noqa: ANN001, ANN002, ANN201, ARG005

import asyncio
import base64
import struct
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import yaml
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from monitoring.creator_history_index import PUMP, curve_address
from monitoring.pressure_trial_source import PressureTrialSource
from monitoring.second_block_source import CREATE, TRADE, SecondBlockSource, pump_events
from utils.idl_parser import IDLParser

ROOT = Path(__file__).resolve().parents[1]


def logs(*events, program=PUMP):
    return [f"Program {program} invoke [1]",
            *("Program data: "+base64.b64encode(e).decode() for e in events),
            f"Program {program} success"]


def create(mint, creator, *, user=None):
    strings = b"".join(struct.pack("<I", len(s))+s for s in [b"Test", b"TK", b"https://example.test"])
    return (CREATE+strings+bytes(mint)+bytes(Pubkey.from_string(curve_address(str(mint))))
            +bytes(user or creator)+bytes(creator)+struct.pack("<qQQQQ", 1, 1000000000000000,
                30000000000, 793100000000000, 1000000000000000)
            +bytes(Pubkey.new_unique())+b"\0\0")


def trade(mint, wallet, *, sol=1.4, buy=True, mc=4000):
    # 100 USD/SOL, 1B supply, virtual reserves encode the desired MC.
    return (TRADE+bytes(mint)+struct.pack("<QQB", int(sol*1e9), 1000000, buy)
            +bytes(wallet)+struct.pack("<qQQ", 1, round(mc*10000000), 1000000000000000))


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.policy = yaml.safe_load((ROOT / "configs/second-block-dev.yaml").read_text())
        self.impl = SimpleNamespace(event_parser=SimpleNamespace(_idl_parser=IDLParser(str(ROOT / "idl/pump_fun_idl.json"))))
        self.callback = AsyncMock()
        self.source = SecondBlockSource(self.policy, AsyncMock(), self.impl, None,
                                       lambda: 100, lambda: 1, lambda: True, self.callback)
        self.source.history = SimpleNamespace(observe=lambda *a: None, warm=lambda *a: None,
            evidence=lambda *a: {"migration_fraction": .01, "launches": 100, "migrations": 1},
            invalidate=lambda: None, close=AsyncMock())
        self.mint = Pubkey.new_unique()
        self.creator, self.a, self.b = [Keypair().pubkey() for _ in range(3)]
        self.token = SimpleNamespace(creator=self.creator, additional_data={})
        self.builder = patch("monitoring.second_block_source.build_pumpfun_token_info", AsyncMock(return_value=self.token))
        self.builder.start()
        self.addCleanup(self.builder.stop)
        self.addAsyncCleanup(self.source.close)

    async def created(self, *trades, user=None):
        self.source.on_slot({"slot": 100, "parent": 99})
        self.source.on_logs(logs(create(self.mint, self.creator, user=user), *trades), 100, "create")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_two_distinct_buyers_and_275_volume_only_trigger_next_produced_slot(self):
        await self.created(trade(self.mint, self.a), trade(self.mint, self.b))
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.source.on_slot({"slot": 102, "parent": 100})  # A skipped empty slot is permitted.
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["buyers"], 2)
        self.assertAlmostEqual(signal["volume_usd"], 280)
        self.assertEqual(signal["target_slot"], 102)
        self.assertFalse(signal["inclusion_guaranteed"])
        await asyncio.gather(*self.source.tasks)
        self.callback.assert_awaited_once()
        delivered = self.callback.await_args.args[0]
        self.assertIsNot(delivered, self.token)
        self.assertEqual(delivered.creator, self.token.creator)
        self.assertEqual(delivered.additional_data["dev_entry_target_slot"], 102)
        self.source.on_slot({"slot": 103, "parent": 102})
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_repeated_same_wallet_and_trades_in_second_block_do_not_qualify(self):
        await self.created(trade(self.mint, self.a), trade(self.mint, self.a))
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.on_logs(logs(trade(self.mint, self.b)), 101, "second-buy")
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.callback.assert_not_awaited()

    async def test_volume_includes_sells_but_still_requires_two_buyers(self):
        await self.created(trade(self.mint, self.a, sol=.5), trade(self.mint, self.b, sol=.5),
                           trade(self.mint, self.a, sol=1.75, buy=False))
        self.source.on_slot({"slot": 101, "parent": 100})
        self.assertAlmostEqual(self.source.signal(str(self.mint))["volume_usd"], 275)

    async def test_history_uses_creation_signer_when_royalty_creator_differs(self):
        self.source.history.observe = Mock()
        self.source.history.evidence = Mock(return_value={"migration_fraction": .01})
        await self.created(trade(self.mint, self.a), trade(self.mint, self.b), user=self.a)
        self.source.on_slot({"slot": 101, "parent": 100})
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["creator"], str(self.creator))
        self.assertEqual(signal["developer"], str(self.a))
        self.source.history.observe.assert_called_once_with(str(self.a), str(self.mint))
        self.source.history.evidence.assert_called_with(str(self.a), str(self.mint))

    async def test_pda_creation_user_cannot_borrow_an_external_wallet_history(self):
        off_curve = Pubkey.from_string(curve_address(str(self.mint)))
        await self.created(trade(self.mint, self.a), trade(self.mint, self.b), user=off_curve)
        self.source.on_slot({"slot": 101, "parent": 100})
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.assertFalse(self.source.candidates)

    async def test_expired_clock_duplicate_slot_missing_history_and_exact_mc_are_rejected(self):
        await self.created(trade(self.mint, self.a), trade(self.mint, self.b))
        self.source.on_slot({"slot": 101, "parent": 100})
        self.source.slot_at = time.monotonic()-1
        self.source.on_slot({"slot": 101, "parent": 100})
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.source.slot_at = time.monotonic()
        candidate = self.source.candidates[str(self.mint)]
        candidate.price = 4.2e-8
        self.assertIsNone(self.source.signal(str(self.mint)))
        candidate.price = 4e-8
        self.source.history.evidence = lambda *a: None
        self.assertIsNone(self.source.signal(str(self.mint)))
        self.source.disconnect()
        self.assertIsNone(self.source.signal(str(self.mint)))

    def test_events_from_other_programs_or_truncated_invocations_are_untrusted(self):
        event = trade(self.mint, self.a)
        self.assertEqual(pump_events(logs(event)), [event])
        self.assertEqual(pump_events(logs(event, program=self.a)), [])
        self.assertEqual(pump_events(logs(event)[:-1]), [])
        self.assertEqual(pump_events(["Program log: "+logs(event)[0], *logs(event)[1:]]), [])

    def test_optional_observer_failure_does_not_escape_into_existing_four_sources(self):
        collector = object.__new__(PressureTrialSource)
        collector.event_observer = SimpleNamespace(on_logs=Mock(side_effect=ValueError), disconnect=Mock())
        self.assertFalse(collector.notify_observer("on_logs", [], 100, "signature"))
        collector.event_observer.disconnect.assert_called_once()
