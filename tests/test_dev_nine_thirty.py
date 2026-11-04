"""Nine shared slots and thirty-USD dev budgets, using synthetic ledgers only."""

# Fake runtime hooks intentionally match the production admission interfaces.
# ruff: noqa: ANN001, ARG001

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_dev_second_window_runtime as strict
import yaml

from trading.base import TradeResult
from trading.buy_budget import inclusive_balance_entry
from trading.four_strategy_book import FourStrategyBook
from trading.four_strategy_runtime import SharedBuyer
from trading.second_block_policy import STRATEGY_ID, entry_budget_cap_lamports
from trading.transaction_observer import ENTRY_WINDOW

ROOT = Path(__file__).resolve().parents[1]
SOL = 1_000_000_000
USD_CAP_LAMPORTS = 300_000_000
RESERVE = 30_000_000


class NineThirtyBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "synthetic.sqlite3"
        self.book = FourStrategyBook(self.path, "fake-owner", "fake-policy", max_positions=9)

    def reserve(self, mint: str, *, balance: int = 10*SOL, book=None) -> dict | None:
        return (book or self.book).reserve(
            mint, STRATEGY_ID, balance, 1000, RESERVE, .15, 3_700_000, {},
            fraction=.80, budget_cap_lamports=USD_CAP_LAMPORTS,
        )

    def test_shipped_limits_are_nine_slots_and_thirty_usd_with_eighty_percent(self) -> None:
        plan = yaml.safe_load((ROOT / "configs/four-strategies.yaml").read_text())
        dev = yaml.safe_load((ROOT / "configs/second-block-dev.yaml").read_text())
        self.assertEqual(plan["trade"]["max_posiciones"], 9)
        self.assertEqual(plan["trade"]["min_reserve_sol"], .03)
        self.assertEqual(dev["max_entry_budget_usd"], 30.0)
        self.assertEqual(dev["balance_fraction"], .80)
        self.assertEqual(entry_budget_cap_lamports(dev, 100, quote_age_seconds=0), USD_CAP_LAMPORTS)

    def test_atomic_admission_has_exactly_nine_slots_under_concurrency(self) -> None:
        def admit(index: int) -> dict | None:
            connection = FourStrategyBook(self.path, "fake-owner", "fake-policy", max_positions=9)
            return self.reserve(f"mint-{index}", book=connection)

        with ThreadPoolExecutor(max_workers=12) as executor:
            admitted = [entry for entry in executor.map(admit, range(18)) if entry]
        self.assertEqual(len(admitted), 9)
        self.assertEqual(len(self.book.positions()), 9)
        self.assertIsNone(self.reserve("tenth"))
        self.assertTrue(all(USD_CAP_LAMPORTS-3 < entry["budget"] <= USD_CAP_LAMPORTS
                            for entry in admitted))
        self.assertLessEqual(sum(entry["budget"] for entry in admitted), 10*SOL-RESERVE)

    def test_pending_costs_and_native_reserve_can_block_before_nine(self) -> None:
        first = self.reserve("one", balance=600_000_000)
        self.assertIsNotNone(first)
        self.assertIsNone(self.reserve("two", balance=600_000_000))
        self.assertEqual(len(self.book.positions()), 1)
        self.assertGreaterEqual(600_000_000-first["budget"], RESERVE)

    def test_eighty_percent_does_not_expand_to_thirty_usd_when_balance_is_smaller(self) -> None:
        entry = self.reserve("small", balance=200_000_000)
        self.assertIsNotNone(entry)
        self.assertLessEqual(entry["budget"], 160_000_000)
        self.assertGreater(entry["budget"], 159_999_997)
        self.assertGreaterEqual(200_000_000-entry["budget"], RESERVE)
        self.assertIsNone(self.reserve("insufficient", balance=140_000_000))

    def test_an_unknown_signed_submission_blocks_new_entries_even_with_free_slots(self) -> None:
        entry = self.reserve("uncertain")
        self.book.submitting(entry["nonce"])
        self.book.receipt(entry["nonce"], "offline-unknown-buy", "buy")
        self.book.release_unbroadcast(entry["nonce"])
        self.assertIsNone(self.reserve("must-wait"))
        self.assertEqual(self.book.positions()[0]["state"], "uncertain")
        self.assertEqual(self.book.signatures(entry["nonce"]), [("offline-unknown-buy", "buy")])

    def test_an_open_runner_still_occupies_one_of_the_nine_slots(self) -> None:
        runner = self.reserve("runner")
        self.book.submitting(runner["nonce"])
        self.book.receipt(runner["nonce"], "offline-runner-buy", "buy")
        self.book.opened(runner["nonce"], 900, {"remaining_qty": .01, "executed": [0, 1, 2]})
        for index in range(8):
            self.assertIsNotNone(self.reserve(f"reserved-{index}"))
        self.assertIsNone(self.reserve("tenth"))
        self.assertEqual(len(self.book.positions()), 9)

    def test_reopening_with_a_higher_limit_preserves_all_existing_ledger_evidence(self) -> None:
        policy = yaml.safe_load((ROOT / "configs/four-strategies.yaml").read_text())["five_trade_evaluation"]
        path = Path(self.directory.name) / "historical.sqlite3"
        original = FourStrategyBook(path, "fake-owner", "fake-policy", max_positions=4,
                                    evaluation_policy=policy)
        entry = self.reserve("closed", book=original)
        original.submitting(entry["nonce"])
        original.receipt(entry["nonce"], "offline-historical-buy", "buy")
        original.receipt(entry["nonce"], "offline-historical-sell", "sell")
        original.settle(entry["nonce"], -10, 1000, {
            "verified_zero_holdings": True,
            "signatures": ["offline-historical-buy", "offline-historical-sell"],
            "buy_debit_lamports": 100, "buy_debit_verified": True,
        })
        pending = self.reserve("pending", book=original)
        with closing(original.connect()) as db, db:
            db.execute("INSERT INTO pauses VALUES ('strength-21k')")
            before = list(db.iterdump())
        reopened = FourStrategyBook(path, "fake-owner", "fake-policy", max_positions=9,
                                    evaluation_policy=policy)
        with closing(reopened.connect()) as db:
            self.assertEqual(list(db.iterdump()), before)
        self.assertEqual(reopened.positions()[0]["nonce"], pending["nonce"])
        self.assertEqual(reopened.max_positions, 9)

    def test_five_trade_evaluation_still_limits_each_strategy_independently(self) -> None:
        policy = yaml.safe_load((ROOT / "configs/four-strategies.yaml").read_text())["five_trade_evaluation"]
        path = Path(self.directory.name) / "evaluation.sqlite3"
        evaluated = FourStrategyBook(path, "fake-owner", "fake-policy", max_positions=9,
                                     evaluation_policy=policy)
        for index in range(5):
            self.assertIsNotNone(self.reserve(f"trial-{index}", book=evaluated))
        self.assertIsNone(self.reserve("sixth-unclosed", book=evaluated))
        current = evaluated.research()["five_trade_evaluation"]["strategies"][STRATEGY_ID]["current"]
        self.assertEqual(current["admitted"], 5)
        self.assertEqual(current["state"], "waiting_for_closures")
        self.assertFalse(evaluated.can_admit(STRATEGY_ID))

    def test_invalid_slot_limits_do_not_create_a_ledger(self) -> None:
        for index, limit in enumerate((0, 10, -1, True, False, 9.0, "9")):
            path = Path(self.directory.name) / f"invalid-{index}.sqlite3"
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                FourStrategyBook(path, "fake-owner", "fake-policy", max_positions=limit)
            self.assertFalse(path.exists())

    def test_thirty_usd_cap_floors_the_full_slippage_and_fee_budget(self) -> None:
        for quote in ("100", "137.123456789", "250.1"):
            with self.subTest(quote=quote):
                cap = int(Decimal("30")/Decimal(quote)*SOL)
                amount, budget = inclusive_balance_entry(
                    10*SOL, .80, .15, 3_700_000, budget_cap_lamports=cap)
                self.assertGreater(amount, 0)
                self.assertLess(amount, float(Decimal("30")/Decimal(quote)))
                self.assertLessEqual(budget, cap)
                self.assertGreater(budget, cap-3)
                self.assertLessEqual(Decimal(budget)/SOL*Decimal(quote), Decimal("30"))


class DevThirtyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    rpc_response = strict.StrictSecondWindowRuntimeTests.rpc_response

    async def asyncSetUp(self) -> None:
        await strict.StrictSecondWindowRuntimeTests.asyncSetUp(self)
        self.runtime.second_policy["max_entry_budget_usd"] = 30.0
        self.book = FourStrategyBook(self.book.path, "owner", "policy", max_positions=9)
        self.runtime.book = self.book

    async def test_thirty_usd_reaches_unsigned_broker_with_eighty_percent_and_nine_slot_book(self) -> None:
        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            position = self.book.positions()[0]
            details = json.loads(position["details"])
            self.assertEqual(self.book.max_positions, 9)
            self.assertEqual(details["budget_cap_lamports"], USD_CAP_LAMPORTS)
            self.assertEqual(details["policy_fraction"], .80)
            self.assertEqual(details["entry_budget_cap_usd"], 30.0)
            self.assertLessEqual(position["budget"], USD_CAP_LAMPORTS)
            self.assertGreater(position["budget"], USD_CAP_LAMPORTS-3)
            self.assertLess(kwargs["amount_sol"], .3)
            self.assertTrue(ENTRY_WINDOW.get()())
            self.assertFalse(self.book.signatures(position["nonce"]))
            return TradeResult(success=False, error_message="Unsigned fake broker")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        result = await SharedBuyer(self.runtime, broker).execute(self.token)
        self.assertFalse(result.success)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assertIsNone(ENTRY_WINDOW.get())

    async def test_quote_increase_during_curve_read_rechecks_the_thirty_usd_cap(self) -> None:
        async def increase_quote(*args: object, **kwargs: object) -> dict:
            self.runtime.sol_usd = 200
            return self.state

        self.curve.side_effect = increase_quote
        broker = SimpleNamespace(execute=AsyncMock())
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())

    async def test_presign_guard_rechecks_the_thirty_usd_cap_after_broker_preparation(self) -> None:
        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            guard = ENTRY_WINDOW.get()
            self.assertTrue(guard())
            self.runtime.sol_usd = 200
            self.assertFalse(guard())
            return TradeResult(success=False, error_message="Unsigned guard rejection")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assertIsNone(ENTRY_WINDOW.get())


if __name__ == "__main__":
    unittest.main()
