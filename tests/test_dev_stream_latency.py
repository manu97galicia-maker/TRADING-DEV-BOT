"""Keep observed developer slots free of SQLite without bypassing admission."""

# Fake client hooks exercise real source/runtime/ledger with unsigned brokers.
# ruff: noqa: ANN001, ANN201, SLF001

import asyncio
import copy
import time
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_dev_only_mode as only_dev
import test_four_strategy_inclusive_entry as inherited
import test_second_block_source as stream
from solders.pubkey import Pubkey

from monitoring.second_block_source import Candidate
from trading import four_strategy_runtime as runtime_module
from trading.four_strategy_runtime import SharedBuyer
from trading.second_block_policy import STRATEGY_ID


class DeveloperStreamLatencyTests(unittest.IsolatedAsyncioTestCase):
    setUp = inherited.InheritedInclusiveEntryTests.setUp
    fixture = inherited.InheritedInclusiveEntryTests.fixture

    async def construct_source(self, *, classic=False):
        trader, token, broker = self.fixture(STRATEGY_ID)
        trader.only_dev = True
        trader.second_policy = copy.deepcopy(only_dev.SECOND_POLICY)
        trader.second_policy["entry_timing"] = "any_verified_time"
        trader.second_policy["require_dev_initial_buy"] = False
        if classic:
            trader.second_policy["entry_rule"] = "second_block"
        trader.four = copy.deepcopy(only_dev.PLAN["four_strategies"])
        trader.wss_endpoint = "wss://unused.invalid"
        trader._four_tasks = set()
        trader.audit_loop = AsyncMock()
        trader.book.paused = Mock(wraps=trader.book.paused)
        collector = SimpleNamespace(run=AsyncMock())
        with patch.object(runtime_module, "PressureTrialSource", return_value=collector):
            await trader._run_curva96_mode()
        source = trader.second_source  # The real observer installed by the runtime.
        self.addAsyncCleanup(source.close)
        source.history = SimpleNamespace(
            warm=Mock(), evidence=Mock(return_value=None),
            invalidate=Mock(), close=AsyncMock(),
        )
        return trader, source, token, broker

    async def test_full_candidate_cache_handles_slots_and_trade_without_sqlite_pause_reads(self):
        trader, source, _token, _broker = await self.construct_source()
        trader.book.paused.side_effect = AssertionError("SQLite on the receive callback")
        source.callback = AsyncMock()
        now = time.monotonic()
        for _ in range(256):
            mint = Pubkey.new_unique()
            source.candidates[str(mint)] = Candidate(
                str(mint), "creator", 100, "Fake", "FK", developer="developer",
                token=SimpleNamespace(additional_data={}), observed_at=now,
            )
        source.on_slot({"slot": 101, "parent": 100})
        mint = Pubkey.from_string(next(iter(source.candidates)))
        source.on_logs(stream.logs(stream.trade(mint, Pubkey.new_unique())), 100, "trade")
        self.assertEqual(len(source.candidates), 256)
        # With activity disabled, preparation and signal selection may each
        # consult the in-memory history. Keep that bounded without requiring
        # exactly one implementation-specific lookup per observed candidate.
        self.assertGreaterEqual(source.history.evidence.call_count, 257)
        self.assertLessEqual(source.history.evidence.call_count, 514)
        trader.book.paused.assert_not_called()
        source.callback.assert_not_awaited()  # Unknown history remains ineligible.

    async def test_classic_observer_preserves_its_pause_callback(self):
        trader, source, _token, _broker = await self.construct_source(classic=True)
        self.assertTrue(source.allowed())
        trader.book.paused.assert_called_once_with(STRATEGY_ID)
        with closing(self.book.connect()) as db, db:
            db.execute("INSERT INTO pauses VALUES (?)", (STRATEGY_ID,))
        self.assertFalse(source.allowed())

    async def test_history_delivery_still_stops_at_real_handler_when_ledger_is_paused(self):
        trader, source, token, broker = await self.construct_source()
        # Restore the real selection method after installing the unsigned fixture.
        del trader.current_signal
        token.additional_data = {"acquisition_kind": "second-block-dev"}
        candidate = Candidate(
            str(token.mint), str(token.creator), 100, "Fake", "FK", developer="developer",
            buyers={"buyer-a", "buyer-b"}, buy_sol=3, token=token,
            observed_at=time.monotonic(),
        )
        source.candidates[candidate.mint] = candidate
        source.history.evidence.return_value = {"migration_fraction": .01}
        trader.book.can_admit = Mock(wraps=trader.book.can_admit)
        with closing(self.book.connect()) as db, db:
            db.execute("INSERT INTO pauses VALUES (?)", (STRATEGY_ID,))
        # Reception can observe a qualifying token; the actual handler refuses it.
        self.assertTrue(source.allowed())
        source.on_slot({"slot": 101, "parent": 100})
        for _ in range(8):
            await asyncio.sleep(0)
        self.assertTrue(candidate.attempted)
        trader.book.paused.assert_not_called()
        trader.book.can_admit.assert_called_once_with(STRATEGY_ID)
        trader.solana_client.get_sol_balance.assert_not_awaited()
        trader.rpc.assert_not_awaited()
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())

    async def test_atomic_reservation_rejects_pause_added_after_handler_check(self):
        trader, _source, token, broker = await self.construct_source()
        self.assertTrue(self.book.can_admit(STRATEGY_ID))

        async def pause_during_balance(*_args: object, **_kwargs: object) -> dict:
            with closing(self.book.connect()) as db, db:
                db.execute("INSERT INTO pauses VALUES (?)", (STRATEGY_ID,))
            return {"value": inherited.SOL, "context": {"slot": 100}}

        trader.rpc.side_effect = pause_during_balance
        result = await SharedBuyer(trader, broker).execute(token)
        self.assertFalse(result.success)
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())


if __name__ == "__main__":
    unittest.main()
