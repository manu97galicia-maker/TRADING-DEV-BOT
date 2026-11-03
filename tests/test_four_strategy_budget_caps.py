"""Verify shared admission caps against a fake broker and temporary ledger."""

# Fake runtime fields and explicit financial boundaries keep these tests readable.
# ruff: noqa: ANN001, ANN201, SLF001

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_four_strategy_bundle as bundle_tests

from trading.base import TradeResult
from trading.four_strategy_runtime import FourStrategyTrader, SharedBuyer

LAMPORTS_PER_SOL = 1_000_000_000
BALANCE_LAMPORTS = 362_187_680


class SharedBuyerBudgetCapTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = bundle_tests.BuyerTests.asyncSetUp

    async def buy_with_caps(self, nominal_cap=0, total_cap=0):
        self.runtime.tope_compra_sol = nominal_cap
        self.runtime.tope_compra_total_sol = total_cap
        self.runtime.rpc.return_value = {
            "value": BALANCE_LAMPORTS,
            "context": {"slot": 100},
        }
        broker = SimpleNamespace(
            execute=AsyncMock(
                return_value=TradeResult(
                    success=True,
                    tx_signature="capped-buy",
                    amount=1_000_000,
                    price=1e-7,
                )
            )
        )
        result = await SharedBuyer(self.runtime, broker).execute(
            self.token, amount_sol=999
        )
        self.assertTrue(result.success)
        broker.execute.assert_awaited_once()
        amount = broker.execute.await_args.kwargs["amount_sol"]
        position = self.book.positions()[0]
        self.assertEqual(position["state"], "open")
        expected_cost = int(
            Decimal(str(amount))
            * LAMPORTS_PER_SOL
            * (1 + Decimal(str(self.runtime.buy_slippage)))
        )
        self.assertEqual(
            position["budget"], expected_cost + self.runtime._fee_headroom_lamports
        )
        self.assertLessEqual(
            position["budget"],
            BALANCE_LAMPORTS - self.runtime.min_reserve_lamports,
        )
        hook_amount, hook_budget = FourStrategyTrader._inclusive_entry_budget(
            self.runtime, self.token, BALANCE_LAMPORTS
        )
        self.assertEqual((amount, position["budget"]), (hook_amount, hook_budget))
        return amount, position["budget"]

    async def test_nominal_cap_reaches_final_broker_and_durable_reservation(self):
        amount, budget = await self.buy_with_caps(nominal_cap=0.05)
        self.assertEqual(amount, 0.05)
        self.assertLessEqual(budget, 57_500_000 + 3_000_000)

    async def test_total_cap_limits_slippage_inclusive_cost_before_fees(self):
        amount, budget = await self.buy_with_caps(total_cap=0.06)
        self.assertGreater(amount, 0.05)
        self.assertLessEqual(amount, 0.06 / 1.15)
        self.assertLessEqual(budget, 60_000_000 + 3_000_000)

    async def test_stricter_of_nominal_and_total_caps_wins(self):
        amount, budget = await self.buy_with_caps(nominal_cap=0.05, total_cap=0.06)
        self.assertEqual(amount, 0.05)
        self.assertLessEqual(budget, 57_500_000 + 3_000_000)

    async def test_nonfinite_or_negative_caps_never_reach_broker(self):
        broker = SimpleNamespace(execute=AsyncMock())
        for field in ("tope_compra_sol", "tope_compra_total_sol"):
            for invalid in (float("nan"), float("inf"), -0.01):
                with self.subTest(field=field, cap=invalid):
                    self.runtime.tope_compra_sol = 0
                    self.runtime.tope_compra_total_sol = 0
                    setattr(self.runtime, field, invalid)
                    with self.assertRaises(ValueError):
                        await SharedBuyer(self.runtime, broker).execute(self.token)
                    self.assertFalse(self.book.positions())
        broker.execute.assert_not_awaited()


class BookBudgetCapTests(unittest.TestCase):
    setUp = bundle_tests.BookTests.setUp

    def reserve_capped(self, mint="mint", cap_sol=0.05):
        return self.book.reserve(
            mint,
            "pressure",
            BALANCE_LAMPORTS,
            100,
            30_000_000,
            0.15,
            3_000_000,
            {},
            fraction=0.90,
            cap_sol=cap_sol,
        )

    def test_cap_is_persisted_and_unsubmitted_reservation_can_be_released(self):
        reservation = self.reserve_capped()
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation["amount_sol"], 0.05)
        self.assertEqual(reservation["budget"], 60_500_000)
        self.assertEqual(self.book.positions()[0]["budget"], 60_500_000)
        self.book.cancel_unsubmitted(reservation["nonce"])
        self.assertFalse(self.book.positions())
        self.assertIsNotNone(self.reserve_capped())

    def test_cap_does_not_allow_releasing_a_signed_uncertain_reservation(self):
        reservation = self.reserve_capped()
        self.assertIsNotNone(reservation)
        self.assertTrue(self.book.submitting(reservation["nonce"]))
        self.book.receipt(reservation["nonce"], "signed-buy", "buy")
        self.book.cancel_unsubmitted(reservation["nonce"])
        self.book.release_unbroadcast(reservation["nonce"])
        position = self.book.positions()[0]
        self.assertEqual(position["state"], "uncertain")
        self.assertEqual(position["budget"], 60_500_000)
        self.assertIsNone(self.reserve_capped(mint="other"))

    def test_invalid_cap_rolls_back_without_consuming_a_slot(self):
        for invalid in (float("nan"), float("inf"), -0.01):
            with self.subTest(cap=invalid):
                with self.assertRaises(ValueError):
                    self.reserve_capped(cap_sol=invalid)
                self.assertFalse(self.book.positions())
