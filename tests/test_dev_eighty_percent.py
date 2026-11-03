"""Dev-only 80% admission with real coordinators and unsigned fake execution."""

# Fixtures deliberately expose the inherited private spend-coordination hooks.
# ruff: noqa: ANN001, ARG001, SLF001

import copy
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_four_strategy_bundle as bundle
import test_four_strategy_inclusive_entry as inherited

from trading import second_block_policy
from trading.base import TradeResult
from trading.buy_budget import inclusive_balance_entry
from trading.four_strategy_runtime import SharedBuyer
from trading.second_block_policy import STRATEGY_ID
from trading.transaction_observer import OBSERVER, PRIORITY_CAP

SOL = 1_000_000_000
ACTUAL_BALANCE = 228_667_072
MIN_RESERVE = 30_000_000


class DevEightyPercentPolicySelectionTests(unittest.TestCase):
    def test_only_dev_preflight_selects_child_fraction_and_preserves_shared_default(self) -> None:
        path = bundle.ROOT / "configs/four-strategies.yaml"
        plan = copy.deepcopy(bundle.CONFIG)
        dev_fraction = second_block_policy.entry_balance_fraction(path, plan, only_dev=True)
        shared_fraction = second_block_policy.entry_balance_fraction(path, plan)
        self.assertEqual(dev_fraction, .80)
        self.assertEqual(shared_fraction, .90)
        _, dev_budget = inclusive_balance_entry(ACTUAL_BALANCE, dev_fraction, .15, 3_700_000)
        _, shared_budget = inclusive_balance_entry(ACTUAL_BALANCE, shared_fraction, .15, 3_700_000)
        self.assertLessEqual(dev_budget, ACTUAL_BALANCE - MIN_RESERVE)
        self.assertGreater(shared_budget, ACTUAL_BALANCE - MIN_RESERVE)
        self.assertEqual(plan, bundle.CONFIG)


class DevEightyPercentBuyerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await bundle.BuyerTests.asyncSetUp(self)
        self.runtime.only_dev = True
        self.runtime.compute_units = {"buy": 200000}
        self.runtime.second_policy = {
            **copy.deepcopy(inherited.SECOND_POLICY), "enabled": True,
        }
        self.runtime.current_signal = lambda _token: {
            "strategy": STRATEGY_ID, "creator": "fake-creator",
            "target_slot": None, "sol_usd": 100,
        }
        self.runtime.platform_implementations.curve_manager.get_pool_state.return_value[
            "creator"
        ] = "fake-creator"

    async def test_actual_balance_reaches_unsigned_broker_with_inclusive_eighty_percent_budget(self) -> None:
        self.runtime.rpc.return_value = {
            "value": ACTUAL_BALANCE, "context": {"slot": 100},
        }
        captured = {}

        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            captured.update(kwargs)
            position = self.book.positions()[0]
            details = json.loads(position["details"])
            self.assertEqual(details["policy_fraction"], .80)
            self.assertEqual(details["balance_lamports"], ACTUAL_BALANCE)
            self.assertEqual(details["entry_rule"], "creator_migration_only")
            self.assertEqual(details["entry_exit_policy"], self.runtime.second_policy["exit"])
            self.assertIsNone(details["target_slot"])
            self.assertEqual(PRIORITY_CAP.get(), 700_000)  # $0.07 at $100/SOL.
            nominal_lamports = int(Decimal(str(kwargs["amount_sol"])) * SOL)
            total_cost = int(Decimal(nominal_lamports) * Decimal("1.15"))
            total_cost += self.runtime._fee_headroom_lamports + PRIORITY_CAP.get()
            self.assertEqual(position["budget"], total_cost)
            self.assertLessEqual(total_cost, ACTUAL_BALANCE * 4 // 5)
            self.assertGreater(total_cost, ACTUAL_BALANCE * 4 // 5 - 3)
            self.assertGreaterEqual(ACTUAL_BALANCE - total_cost, MIN_RESERVE)
            self.assertFalse(self.book.signatures(position["nonce"]))
            return TradeResult(success=False, error_message="Fake broker: never signed")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        result = await SharedBuyer(self.runtime, broker).execute(self.token, amount_sol=999)
        self.assertFalse(result.success)
        broker.execute.assert_awaited_once()
        self.assertGreater(captured["amount_sol"], 0)
        self.assertLess(captured["amount_sol"], ACTUAL_BALANCE / SOL * .80)
        self.assertFalse(self.book.positions())
        self.assertIsNone(OBSERVER.get())
        self.assertIsNone(PRIORITY_CAP.get())

    async def test_eighty_percent_still_rejects_014_sol_that_would_breach_reserve(self) -> None:
        self.runtime.rpc.return_value = {
            "value": 140_000_000, "context": {"slot": 100},
        }
        broker = SimpleNamespace(execute=AsyncMock())
        result = await SharedBuyer(self.runtime, broker).execute(self.token)
        self.assertFalse(result.success)
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())

    async def test_original_four_sources_keep_their_ninety_percent_budgets(self) -> None:
        self.runtime.only_dev = False
        for strategy in (
            "strong-pressure-8k", "strength-21k",
            "first-block-four-buyers", "telegram-immediate",
        ):
            with self.subTest(strategy=strategy):
                self.runtime.current_signal = lambda _token, source=strategy: {
                    "strategy": source, "sol_usd": 100,
                }
                price = 2.2e-7 if strategy == "strength-21k" else 1e-7
                self.runtime.platform_implementations.curve_manager.get_pool_state.return_value[
                    "price_per_token"
                ] = price
                self.runtime.check_entry_risk.return_value = (
                    {"price_per_token": price, "complete": False}, {"checked": True},
                )

                async def reject_unsigned(token, **kwargs: object) -> TradeResult:
                    position = self.book.positions()[0]
                    self.assertEqual(json.loads(position["details"])["policy_fraction"], .90)
                    self.assertLessEqual(position["budget"], SOL * 9 // 10)
                    self.assertGreater(position["budget"], SOL * 9 // 10 - 3)
                    return TradeResult(success=False, error_message="Fake broker: never signed")

                broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
                self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
                broker.execute.assert_awaited_once()
                self.assertFalse(self.book.positions())

    async def test_new_dev_budget_does_not_rewrite_open_historical_positions(self) -> None:
        for mint, strategy, fraction in (
            ("old-dev", STRATEGY_ID, .90),
            ("old-four", "strong-pressure-8k", .30),
        ):
            reservation = self.book.reserve(
                mint, strategy, SOL, 100, MIN_RESERVE, .15, 3_000_000,
                {}, fraction=fraction,
            )
            self.assertIsNotNone(reservation)
            self.book.submitting(reservation["nonce"])
            self.book.opened(reservation["nonce"], 100, {"historical": True})
        historical = self.book.positions()
        self.runtime.rpc.return_value = {
            "value": ACTUAL_BALANCE, "context": {"slot": 101},
        }

        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            positions = self.book.positions()
            self.assertEqual(positions[:2], historical)
            self.assertEqual(json.loads(positions[2]["details"])["policy_fraction"], .80)
            return TradeResult(success=False, error_message="Fake broker: never signed")

        broker = SimpleNamespace(execute=AsyncMock(side_effect=reject_unsigned))
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_awaited_once()
        self.assertEqual(self.book.positions(), historical)


class DevEightyPercentInheritedHandlerTests(unittest.IsolatedAsyncioTestCase):
    setUp = inherited.InheritedInclusiveEntryTests.setUp
    fixture = inherited.InheritedInclusiveEntryTests.fixture
    handle = inherited.InheritedInclusiveEntryTests.handle

    async def test_inherited_handlers_admit_actual_balance_and_release_unsigned_failure(self) -> None:
        trader, token, broker = self.fixture(STRATEGY_ID, ACTUAL_BALANCE)
        trader.only_dev = True
        trader.current_signal.return_value["target_slot"] = None
        captured = {}

        async def reject_unsigned(token, **kwargs: object) -> TradeResult:
            position = self.book.positions()[0]
            captured.update(
                nominal=kwargs["amount_sol"], budget=position["budget"],
                reserved=trader._reserved_lamports,
            )
            self.assertEqual(json.loads(position["details"])["policy_fraction"], .80)
            self.assertEqual(trader._reserved_lamports, position["budget"])
            self.assertEqual(trader._reserved_real_buy_slots, 1)
            self.assertFalse(self.book.signatures(position["nonce"]))
            return TradeResult(success=False, error_message="Fake broker: never signed")

        broker.execute.side_effect = reject_unsigned
        logger, release_mint = await self.handle(trader, token)
        broker.execute.assert_awaited_once()
        trader.rpc.assert_awaited_once()
        self.assertGreater(captured["nominal"], ACTUAL_BALANCE / SOL * .65)
        self.assertLessEqual(captured["budget"], ACTUAL_BALANCE * 4 // 5)
        self.assertGreaterEqual(ACTUAL_BALANCE - captured["budget"], MIN_RESERVE)
        self.assertEqual(trader._reserved_lamports, 0)
        self.assertEqual(trader._reserved_real_buy_slots, 0)
        self.assertFalse(self.book.positions())
        release_mint.assert_called_once_with("fake-mint")
        trader._handle_failed_buy.assert_awaited_once()
        trader._handle_successful_buy.assert_not_awaited()
        self.assertFalse(any("SOL insuficiente" in str(call) for call in logger.warning.call_args_list))

    async def test_inherited_guard_rejects_014_sol_before_durable_admission(self) -> None:
        trader, token, broker = self.fixture(STRATEGY_ID, 140_000_000)
        trader.only_dev = True
        logger, release_mint = await self.handle(trader, token)
        broker.execute.assert_not_awaited()
        trader.rpc.assert_not_awaited()
        self.assertEqual(trader._reserved_lamports, 0)
        self.assertEqual(trader._reserved_real_buy_slots, 0)
        self.assertFalse(self.book.positions())
        release_mint.assert_called_once_with("fake-mint")
        self.assertTrue(any("SOL insuficiente" in str(call) for call in logger.warning.call_args_list))

    async def test_fresh_lower_balance_rejects_and_releases_both_coordinators(self) -> None:
        trader, token, broker = self.fixture(STRATEGY_ID, ACTUAL_BALANCE)
        trader.only_dev = True
        trader.rpc.return_value = {"value": 140_000_000, "context": {"slot": 101}}
        _, release_mint = await self.handle(trader, token)
        trader.rpc.assert_awaited_once()
        broker.execute.assert_not_awaited()
        self.assertEqual(trader._reserved_lamports, 0)
        self.assertEqual(trader._reserved_real_buy_slots, 0)
        self.assertFalse(self.book.positions())
        release_mint.assert_called_once_with("fake-mint")


if __name__ == "__main__":
    unittest.main()
