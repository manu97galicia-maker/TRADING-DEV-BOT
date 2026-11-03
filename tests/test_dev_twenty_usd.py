"""Bound dev entries in USD with unsigned brokers and a temporary real ledger."""

# Dynamic fake runtime hooks deliberately match the inherited admission path.
# ruff: noqa: ANN001, ARG001, SLF001

import copy
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_four_strategy_bundle as bundle
import test_four_strategy_inclusive_entry as inherited

from trading import four_strategy_runtime as runtime_module
from trading.base import TradeResult
from trading.buy_budget import inclusive_balance_entry
from trading.four_strategy_runtime import FourStrategyTrader, SharedBuyer
from trading.second_block_policy import STRATEGY_ID
from trading.transaction_observer import ENTRY_WINDOW

SOL = 1_000_000_000
USD_LIMIT = Decimal("20")


class InclusiveUsdBudgetTests(unittest.TestCase):
    def test_shipped_dev_configuration_has_twenty_usd_cap_and_eighty_percent(self) -> None:
        self.assertEqual(inherited.SECOND_POLICY["max_entry_budget_usd"], 20.0)
        self.assertEqual(inherited.SECOND_POLICY["balance_fraction"], .80)

    def test_cap_includes_slippage_and_fee_headroom_with_floor_rounding(self) -> None:
        for quote in ("100", "137.123456789", "250.1"):
            with self.subTest(quote=quote):
                cap = int(USD_LIMIT / Decimal(quote) * SOL)
                amount, budget = inclusive_balance_entry(
                    SOL, .80, .15, 3_700_000, budget_cap_lamports=cap,
                )
                nominal = int(Decimal(str(amount)) * SOL)
                self.assertEqual(budget, int(Decimal(nominal) * Decimal("1.15")) + 3_700_000)
                self.assertLessEqual(budget, cap)
                self.assertGreater(budget, cap - 3)
                self.assertLessEqual(Decimal(budget) / SOL * Decimal(quote), USD_LIMIT)

    def test_smaller_balance_fraction_and_nominal_cap_still_win(self) -> None:
        ordinary = inclusive_balance_entry(200_000_000, .80, .15, 3_700_000)
        self.assertEqual(ordinary, inclusive_balance_entry(
            200_000_000, .80, .15, 3_700_000, budget_cap_lamports=200_000_000,
        ))
        amount, budget = inclusive_balance_entry(
            SOL, .80, .15, 3_700_000, .05, budget_cap_lamports=200_000_000,
        )
        self.assertEqual(amount, .05)
        self.assertEqual(budget, 61_200_000)

    def test_zero_cap_or_cap_too_small_for_fees_cannot_mean_unlimited(self) -> None:
        for cap in (0, 3_000_000, 3_700_000):
            with self.subTest(cap=cap):
                self.assertEqual((0.0, 0), inclusive_balance_entry(
                    SOL, .80, .15, 3_700_000, budget_cap_lamports=cap,
                ))

    def test_invalid_lamport_cap_is_rejected(self) -> None:
        for cap in (-1, True, 1.5, float("nan"), float("inf")):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                inclusive_balance_entry(SOL, .80, .15, 3_700_000, budget_cap_lamports=cap)


class DevUsdBuyerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await bundle.BuyerTests.asyncSetUp(self)
        self.runtime.only_dev = True
        self.runtime.compute_units = {"buy": 200000}
        self.runtime.second_policy = {
            **copy.deepcopy(inherited.SECOND_POLICY), "enabled": True,
            "max_entry_budget_usd": 20.0,
        }
        self.runtime.current_signal = lambda _token: {
            "strategy": STRATEGY_ID, "creator": "fake-creator",
            "target_slot": None, "sol_usd": 100,
        }
        self.runtime.platform_implementations.curve_manager.get_pool_state.return_value[
            "creator"
        ] = "fake-creator"

    async def unsigned_buy(self, balance: int = SOL) -> tuple[float, int]:
        self.runtime.rpc.return_value = {"value": balance, "context": {"slot": 100}}
        captured = {}

        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            position = self.book.positions()[0]
            details = json.loads(position["details"])
            captured.update(amount=kwargs["amount_sol"], budget=position["budget"])
            self.assertEqual(details["policy_fraction"], .80)
            cap = runtime_module._entry_budget_cap_lamports(self.runtime, STRATEGY_ID)
            self.assertEqual(details["budget_cap_lamports"], cap)
            self.assertLessEqual(position["budget"], cap)
            self.assertLessEqual(Decimal(position["budget"]) / SOL * Decimal(str(self.runtime.sol_usd)), USD_LIMIT)
            self.assertLessEqual(position["budget"], balance * 4 // 5)
            self.assertGreaterEqual(balance - position["budget"], 30_000_000)
            self.assertFalse(self.book.signatures(position["nonce"]))
            return TradeResult(success=False, error_message="Fake broker: never signed")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token, amount_sol=999)).success)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        return captured["amount"], captured["budget"]

    async def test_twenty_usd_reaches_broker_as_inclusive_budget(self) -> None:
        amount, budget = await self.unsigned_buy()
        self.assertLess(amount, .2)
        self.assertLessEqual(budget, 200_000_000)
        self.assertGreater(budget, 199_999_997)

    async def test_eighty_percent_is_used_when_smaller_than_twenty_usd(self) -> None:
        _, budget = await self.unsigned_buy(200_000_000)
        self.assertLessEqual(budget, 160_000_000)
        self.assertGreater(budget, 159_999_997)

    async def test_existing_nominal_cap_can_further_reduce_usd_sized_entry(self) -> None:
        self.runtime.tope_compra_sol = .05
        amount, budget = await self.unsigned_buy()
        self.assertEqual(amount, .05)
        self.assertEqual(budget, 61_200_000)

    async def test_minimum_sol_reserve_still_blocks_oversized_fraction(self) -> None:
        self.runtime.rpc.return_value = {"value": 140_000_000, "context": {"slot": 100}}
        broker = SimpleNamespace(execute=AsyncMock())
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())

    async def test_quote_increase_before_submission_cancels_unsubmitted_budget(self) -> None:
        state = self.runtime.platform_implementations.curve_manager.get_pool_state.return_value

        async def increase_quote(*args: object, **kwargs: object) -> dict:
            self.runtime.sol_usd = 200
            return state

        self.runtime.platform_implementations.curve_manager.get_pool_state.side_effect = increase_quote
        broker = SimpleNamespace(execute=AsyncMock())
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())

    async def test_presign_window_closes_if_quote_rises_inside_the_broker(self) -> None:
        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            presign_guard = ENTRY_WINDOW.get()
            self.assertTrue(presign_guard())
            self.runtime.sol_usd = 200
            self.assertFalse(presign_guard())
            self.assertFalse(self.book.signatures(self.book.positions()[0]["nonce"]))
            return TradeResult(success=False, error_message="Fake: pre-sign check rejected")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assertIsNone(ENTRY_WINDOW.get())

    async def test_original_four_sources_do_not_inherit_dev_usd_cap(self) -> None:
        self.runtime.only_dev = False
        for strategy in ("strong-pressure-8k", "strength-21k", "first-block-four-buyers", "telegram-immediate"):
            with self.subTest(strategy=strategy):
                self.runtime.current_signal = lambda _token, source=strategy: {"strategy": source, "sol_usd": 100}
                self.assertIsNone(runtime_module._entry_budget_cap_lamports(self.runtime, strategy))
                price = 2.2e-7 if strategy == "strength-21k" else 1e-7
                state = {"price_per_token": price, "complete": False, "creator": "fake-creator"}
                self.runtime.check_entry_risk.return_value = (state, {"checked": True})
                self.runtime.platform_implementations.curve_manager.get_pool_state.return_value = state

                async def reject_unsigned(token, **kwargs: object) -> TradeResult:
                    position = self.book.positions()[0]
                    self.assertGreater(position["budget"], 899_999_997)
                    self.assertLessEqual(position["budget"], 900_000_000)
                    return TradeResult(success=False, error_message="Fake broker: never signed")

                broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
                self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
                broker.execute.assert_awaited_once()
                self.assertFalse(self.book.positions())

    async def test_invalid_or_stale_quote_never_produces_a_budget(self) -> None:
        for quote, age in ((0, 1), (-1, 1), (float("nan"), 1), (float("inf"), 1), (100, 30.1), (100, -1)):
            with self.subTest(quote=quote, age=age):
                self.runtime.sol_usd = quote
                self.runtime.reference_age = lambda current_age=age: current_age
                with self.assertRaises(ValueError):
                    runtime_module._entry_budget_cap_lamports(self.runtime, STRATEGY_ID)
                self.assertEqual((0.0, 0), FourStrategyTrader._inclusive_entry_budget(self.runtime, self.token, SOL))
                broker = SimpleNamespace(execute=AsyncMock())
                result = await SharedBuyer(self.runtime, broker).execute(self.token)
                self.assertFalse(result.success)
                broker.execute.assert_not_awaited()
                self.assertFalse(self.book.positions())


class DevUsdInheritedHandlerTests(unittest.IsolatedAsyncioTestCase):
    setUp = inherited.InheritedInclusiveEntryTests.setUp
    fixture = inherited.InheritedInclusiveEntryTests.fixture
    handle = inherited.InheritedInclusiveEntryTests.handle

    async def test_both_reservations_use_same_twenty_usd_cap_and_release_unsigned_failure(self) -> None:
        trader, token, broker = self.fixture(STRATEGY_ID, SOL)
        trader.only_dev = True
        trader.second_policy["max_entry_budget_usd"] = 20.0
        trader.current_signal.return_value["target_slot"] = None

        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            position = self.book.positions()[0]
            self.assertLessEqual(position["budget"], 200_000_000)
            self.assertGreater(position["budget"], 199_999_997)
            self.assertEqual(trader._reserved_lamports, position["budget"])
            self.assertEqual(trader._reserved_real_buy_slots, 1)
            self.assertEqual(json.loads(position["details"])["budget_cap_lamports"], 200_000_000)
            self.assertLess(kwargs["amount_sol"], .2)
            self.assertFalse(self.book.signatures(position["nonce"]))
            return TradeResult(success=False, error_message="Fake broker: never signed")

        broker.execute.side_effect = reject_unsigned
        logger, release_mint = await self.handle(trader, token)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assertEqual(trader._reserved_lamports, 0)
        self.assertEqual(trader._reserved_real_buy_slots, 0)
        release_mint.assert_called_once_with("fake-mint")
        self.assertFalse(any("SOL insuficiente" in str(call) for call in logger.warning.call_args_list))


if __name__ == "__main__":
    unittest.main()
