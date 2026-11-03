"""Offline creator-history admission and task-local priority-fee regressions."""

# Fake brokers exercise the shipped admission and sale paths without a network.
# ruff: noqa: ANN001, ANN002, ANN003, ANN201, ANN202, ARG001, FBT003, PLR2004, SLF001

import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_four_strategy_bundle as bundle
import yaml

import trading.four_strategy_runtime as runtime_module
from trading.base import TradeResult
from trading.four_strategy_runtime import FourStrategyTrader, SharedBuyer
from trading.second_block_policy import STRATEGY_ID, position_exit_plan
from trading.transaction_observer import (
    BUY_PRICE_CEILING,
    ENTRY_WINDOW,
    OBSERVER,
    PRIORITY_CAP,
    SUBMISSIONS,
    EntryWindowExpiredError,
    check_entry_window,
    observe,
)

POLICY = yaml.safe_load(
    (bundle.ROOT / "configs/second-block-dev.yaml").read_text(encoding="utf-8")
)


def enabled_history_policy():
    policy = copy.deepcopy(POLICY)
    policy["enabled"] = True  # Fixture only; no runner or wallet is constructed.
    policy["entry_timing"] = "any_verified_time"
    policy["require_dev_initial_buy"] = False
    return policy


def rounded_priority_fee(price, units):
    return (price * units + 999_999) // 1_000_000


class HistoryAssetSelectionTests(unittest.TestCase):
    def test_mayhem_flags_open_only_for_the_dev_only_creator_history_rule(self):
        for only_dev, legacy_rule in ((True, False), (True, True), (False, False)):
            with self.subTest(only_dev=only_dev, legacy_rule=legacy_rule):
                policy = enabled_history_policy()
                if legacy_rule:
                    policy["entry_rule"] = "second_block"
                broker = SimpleNamespace(allow_mayhem=False, require_mayhem=True)
                trader = SimpleNamespace(
                    only_dev=only_dev, second_policy=policy, buyer=broker,
                    allow_mayhem=False, require_mayhem=True,
                )
                runtime_module._configure_history_only_assets(trader)
                expected = (True, False) if only_dev and not legacy_rule else (False, True)
                self.assertEqual((trader.allow_mayhem, trader.require_mayhem), expected)
                self.assertEqual((broker.allow_mayhem, broker.require_mayhem), expected)


class HistoryEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await bundle.BuyerTests.asyncSetUp(self)
        self.runtime.second_policy = enabled_history_policy()
        self.runtime.compute_units = {"buy": 200_000, "sell": 120_000}
        self.features = {
            "strategy": STRATEGY_ID,
            "sol_usd": 100,
            "creator": "dev",
            "target_slot": 101,
            "creator_history": {"migrations": 1, "launches": 100},
        }
        self.runtime.current_signal = lambda _token: self.features
        self.state = {
            "price_per_token": 5e-7,
            "complete": False,
            "creator": "dev",
        }
        self.runtime.platform_implementations.curve_manager.get_pool_state.return_value = self.state

    async def test_history_mode_uses_007_buy_cap_and_persists_the_selected_exits(self):
        policy = self.runtime.second_policy
        self.assertEqual(policy["entry_rule"], "creator_migration_only")
        self.assertEqual(policy["buy_priority_fee_cap_usd"], .07)
        self.assertEqual(policy["sell_priority_fee_cap_usd"], .03)

        async def buy(token, **kwargs):
            self.assertEqual(PRIORITY_CAP.get(), 700_000)
            self.assertEqual(kwargs["priority_fee_override"], 3_500_000)
            self.assertEqual(
                rounded_priority_fee(kwargs["priority_fee_override"], 200_000),
                700_000,
            )
            self.assertIsNone(BUY_PRICE_CEILING.get())
            self.assertIsNotNone(ENTRY_WINDOW.get())
            self.features["target_slot"] = 999  # No second/third-block deadline.
            check_entry_window()
            reservation = self.book.positions()[0]
            details = json.loads(reservation["details"])
            self.assertEqual(details["entry_exit_policy"], policy["exit"])
            self.assertEqual(details["policy_fraction"], .80)
            inclusive = round(kwargs["amount_sol"] * 1e9 * 1.15) + 3_700_000
            self.assertLessEqual(inclusive, 200_000_000)  # $20 inclusive at $100/SOL.
            self.assertGreater(inclusive, 199_999_990)
            observe("signed", "history-buy")
            return TradeResult(True, tx_signature="history-buy", price=5e-7, amount=1e6)

        result = await SharedBuyer(
            self.runtime, SimpleNamespace(execute=AsyncMock(side_effect=buy))
        ).execute(self.token, priority_fee_override=999_999_999)
        self.assertTrue(result.success)
        self.runtime.check_entry_risk.assert_not_awaited()
        details = json.loads(self.book.positions()[0]["details"])
        tiers, stop = position_exit_plan(details, bundle.CONFIG["four_strategies"]["exit"])
        self.assertEqual(stop, .22)
        for tier, multiplier in zip(tiers, [1.35, 1.89, 2.646, 3.7044], strict=True):
            self.assertAlmostEqual(tier.target_mc_usd, 50_000 * multiplier)
        self.assertAlmostEqual(tiers[0].sell_original_fraction, 1.12 / 1.35)
        self.assertIsNone(PRIORITY_CAP.get())
        self.assertIsNone(OBSERVER.get())
        self.assertIsNone(BUY_PRICE_CEILING.get())
        self.assertIsNone(ENTRY_WINDOW.get())

    async def test_exact_old_ceiling_and_higher_market_caps_reach_the_broker(self):
        broker = SimpleNamespace(execute=AsyncMock(return_value=TradeResult(False)))
        for market_cap in (4_200, 50_000, 1_000_000):
            with self.subTest(market_cap=market_cap):
                self.state["price_per_token"] = market_cap / 1e11
                await SharedBuyer(self.runtime, broker).execute(self.token)
                self.assertFalse(self.book.positions())
        self.assertEqual(broker.execute.await_count, 3)
        self.runtime.check_entry_risk.assert_not_awaited()

    async def test_verified_creator_unmigrated_curve_and_valid_price_still_required(self):
        broker = SimpleNamespace(execute=AsyncMock())
        invalid_states = [
            {**self.state, "creator": "other"},
            {key: value for key, value in self.state.items() if key != "creator"},
            {**self.state, "complete": True},
            *({**self.state, "price_per_token": price} for price in (0, -1, float("nan"), float("inf"))),
        ]
        for state in invalid_states:
            with self.subTest(state=state):
                self.runtime.platform_implementations.curve_manager.get_pool_state.return_value = state
                result = await SharedBuyer(self.runtime, broker).execute(self.token)
                self.assertFalse(result.success)
                self.assertFalse(self.book.positions())
        broker.execute.assert_not_awaited()
        self.runtime.check_entry_risk.assert_not_awaited()

    async def test_fresh_reference_holdings_and_source_history_signal_still_required(self):
        broker = SimpleNamespace(execute=AsyncMock())
        buyer = SharedBuyer(self.runtime, broker)
        self.runtime.reference_age = lambda: 31
        self.assertFalse((await buyer.execute(self.token)).success)
        self.runtime.reference_age = lambda: 1
        self.runtime.raw_holdings.return_value = (1, 100)
        self.assertFalse((await buyer.execute(self.token)).success)
        self.runtime.raw_holdings.return_value = (0, 100)
        self.token.additional_data = {"migrated": True}
        self.assertFalse((await buyer.execute(self.token)).success)
        self.token.additional_data = {}
        self.runtime.current_signal = Mock(side_effect=[self.features, None])
        self.assertFalse((await buyer.execute(self.token)).success)
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())

    async def test_duplicate_mint_is_rejected_even_when_both_budgets_would_fit(self):
        reserved = self.book.reserve(
            "mint", "other", bundle.SOL, 100, 30_000_000, .15, 3_000_000, {},
            fraction=.01,
        )
        self.assertIsNotNone(reserved)
        broker = SimpleNamespace(execute=AsyncMock())
        self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_not_awaited()
        self.assertEqual(len(self.book.positions()), 1)

    async def test_sol_quote_is_rechecked_before_the_final_buy_priority_cap(self):
        self.runtime.tope_compra_sol = .05  # Remains below $20 even after the quote doubles.
        async def curve(*args, **kwargs):
            self.runtime.sol_usd = 200
            return self.state

        async def buy(*args, **kwargs):
            self.assertEqual(PRIORITY_CAP.get(), 350_000)
            self.assertEqual(kwargs["priority_fee_override"], 1_750_000)
            return TradeResult(False)

        self.runtime.platform_implementations.curve_manager.get_pool_state.side_effect = curve
        broker = SimpleNamespace(execute=AsyncMock(side_effect=buy))
        await SharedBuyer(self.runtime, broker).execute(self.token)
        broker.execute.assert_awaited_once()
        self.assertFalse(self.book.positions())
        self.assertIsNone(PRIORITY_CAP.get())

    async def test_pending_90_percent_buy_cannot_share_its_sol_with_another_mint(self):
        self.runtime.rpc.return_value = {"value": 250_000_000, "context": {"slot": 100}}
        entered, release = asyncio.Event(), asyncio.Event()

        async def buy(*args, **kwargs):
            entered.set()
            await release.wait()
            return TradeResult(False)

        broker = SimpleNamespace(execute=AsyncMock(side_effect=buy))
        buyer = SharedBuyer(self.runtime, broker)
        first = asyncio.create_task(buyer.execute(self.token))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            other = SimpleNamespace(mint="other", bonding_curve="curve", symbol="Other", additional_data={})
            self.assertFalse((await buyer.execute(other)).success)
            self.assertEqual(broker.execute.await_count, 1)
            self.assertEqual(len(self.book.positions()), 1)
        finally:
            release.set()
            await first
        self.assertFalse(self.book.positions())

    async def test_failed_broker_restores_buy_context_and_releases_unbroadcast_budget(self):
        async def buy(*args, **kwargs):
            self.assertEqual(PRIORITY_CAP.get(), 700_000)
            raise TimeoutError

        with self.assertRaises(TimeoutError):
            await SharedBuyer(
                self.runtime, SimpleNamespace(execute=AsyncMock(side_effect=buy))
            ).execute(self.token)
        self.assertFalse(self.book.positions())
        self.assertIsNone(PRIORITY_CAP.get())
        self.assertIsNone(OBSERVER.get())
        self.assertIsNone(BUY_PRICE_CEILING.get())
        self.assertIsNone(ENTRY_WINDOW.get())

    async def test_source_history_eligibility_is_checked_again_before_broadcast(self):
        async def buy(*args, **kwargs):
            self.features = None
            check_entry_window()

        with self.assertRaises(EntryWindowExpiredError):
            await SharedBuyer(
                self.runtime, SimpleNamespace(execute=AsyncMock(side_effect=buy))
            ).execute(self.token)
        self.assertFalse(self.book.positions())
        self.assertIsNone(PRIORITY_CAP.get())
        self.assertIsNone(OBSERVER.get())
        self.assertIsNone(ENTRY_WINDOW.get())


class HistorySaleFeeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bundle.BookTests.setUp(self)
        self.trader = object.__new__(FourStrategyTrader)
        self.trader.book = self.book
        self.trader.second_policy = enabled_history_policy()
        self.trader.sol_usd = 100
        self.trader.reference_age = lambda: 1
        self.trader.compute_units = {"sell": 120_000}
        self.trader._bloqueada_hasta = {}
        self.trader._fallos_venta = {}
        self.trader._saldo_real_tokens = AsyncMock(return_value=100)
        self.trader._log_trade = Mock()
        self.trader._anota_firma = Mock()
        self.seller = SimpleNamespace(execute=AsyncMock(side_effect=self.successful_sale))
        self.trader._vendedor_para = Mock(return_value=self.seller)
        self.token = self.add_position("dev", STRATEGY_ID)

    def add_position(self, mint, strategy):
        reservation = self.book.reserve(
            mint, strategy, bundle.SOL, 100, 30_000_000, .15, 3_000_000, {}
        )
        self.assertIsNotNone(reservation)
        nonce = reservation["nonce"]
        self.book.submitting(nonce)
        self.book.receipt(nonce, mint + "-buy", "buy")
        self.book.opened(nonce, 100, {"entry_policy_id": strategy})
        return SimpleNamespace(mint=mint, symbol=mint, additional_data={})

    async def successful_sale(self, token, **kwargs):
        signature = token.mint + "-sale-" + str(self.seller.execute.await_count)
        observe("signed", signature)
        return TradeResult(True, tx_signature=signature, amount=kwargs["token_amount"], price=1e-7)

    def assert_clean_context(self):
        self.assertIsNone(PRIORITY_CAP.get())
        self.assertIsNone(OBSERVER.get())
        self.assertIsNone(SUBMISSIONS.get())
        self.assertIsNone(runtime_module._DEV_EXIT_CAP.get())

    async def test_every_dev_exit_reason_uses_the_003_priority_budget(self):
        reasons = (
            "take_profit_1", "take_profit_2", "take_profit_3", "take_profit_4",
            "stop_loss", "muerta_sin_volumen", "anti_rug", "manual_sell",
            "panic_sell", "max_hold_time", "trailing_stop", "other_exit",
        )

        async def sale(token, **kwargs):
            self.assertEqual(PRIORITY_CAP.get(), 300_000)
            self.assertEqual(kwargs["priority_fee_override"], 2_500_000)
            self.assertEqual(rounded_priority_fee(kwargs["priority_fee_override"], 120_000), 300_000)
            return await self.successful_sale(token, **kwargs)

        self.seller.execute.side_effect = sale
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertTrue(await self.trader._sell_amount(self.token, 25, 1e-7, reason))
                self.assert_clean_context()
        self.assertEqual(self.seller.execute.await_count, len(reasons))

    async def test_disabling_new_entries_does_not_remove_cap_from_a_dev_position(self):
        self.trader.second_policy["enabled"] = False

        async def sale(token, **kwargs):
            self.assertEqual(PRIORITY_CAP.get(), 300_000)
            self.assertEqual(kwargs["priority_fee_override"], 2_500_000)
            return await self.successful_sale(token, **kwargs)

        self.seller.execute.side_effect = sale
        self.assertTrue(await self.trader._sell_amount(self.token, 100, 1e-7, "take_profit_4", full_exit=True))
        self.assert_clean_context()

    async def test_stale_sol_quote_allows_dev_exit_with_zero_priority(self):
        self.trader.reference_age = lambda: 31

        async def sale(token, **kwargs):
            self.assertEqual(PRIORITY_CAP.get(), 0)
            self.assertEqual(kwargs["priority_fee_override"], 0)
            return await self.successful_sale(token, **kwargs)

        self.seller.execute.side_effect = sale
        for reason in ("take_profit_1", "stop_loss", "muerta_sin_volumen"):
            self.assertTrue(await self.trader._sell_amount(self.token, 25, 1e-7, reason))
        self.assert_clean_context()

    async def test_other_strategy_keeps_legacy_tp_fee_and_008_protective_cap(self):
        token = self.add_position("pressure", "strong-pressure-8k")

        async def sale(token, **kwargs):
            if self.expected_reason == "take_profit_1":
                self.assertIsNone(PRIORITY_CAP.get())
                self.assertIsNone(kwargs["priority_fee_override"])
            else:
                self.assertEqual(PRIORITY_CAP.get(), 800_000)
                self.assertEqual(kwargs["priority_fee_override"], 6_666_666)
            return await self.successful_sale(token, **kwargs)

        self.seller.execute.side_effect = sale
        for reason in ("take_profit_1", "stop_loss"):
            self.expected_reason = reason
            self.assertTrue(await self.trader._sell_amount(token, 25, 1e-7, reason))
            self.assert_clean_context()

    async def test_jupiter_fallback_retains_the_dev_priority_cap(self):
        self.trader.wallet = SimpleNamespace(keypair=object())
        self.seller.execute.side_effect = None
        self.seller.execute.return_value = TradeResult(False, error_message="fake curve failure")

        async def fallback(*args, **kwargs):
            self.assertEqual(PRIORITY_CAP.get(), 300_000)
            self.assertEqual(self.trader._fee_de_venta_para("take_profit_1"), 2_500_000)
            observe("signed", "fallback-sale")
            return True, "fallback-sale", None, .01

        with patch("trading.jupiter_seller.jupiter_sell", side_effect=fallback) as routed:
            self.assertTrue(await self.trader._sell_amount(self.token, 25, 1e-7, "take_profit_1"))
        routed.assert_awaited_once()
        self.assert_clean_context()

    async def test_concurrent_dev_and_other_exits_keep_their_own_caps_across_await(self):
        other = self.add_position("pressure", "strong-pressure-8k")
        ready, started = asyncio.Event(), set()

        async def sale(token, **kwargs):
            cap = 300_000 if token.mint == "dev" else 800_000
            fee = 2_500_000 if token.mint == "dev" else 6_666_666
            self.assertEqual(PRIORITY_CAP.get(), cap)
            self.assertEqual(kwargs["priority_fee_override"], fee)
            started.add(token.mint)
            if len(started) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=1)
            self.assertEqual(PRIORITY_CAP.get(), cap)
            self.assertEqual(self.trader._fee_de_venta_para("stop_loss"), fee)
            return await self.successful_sale(token, **kwargs)

        self.seller.execute.side_effect = sale
        results = await asyncio.gather(
            self.trader._sell_amount(self.token, 25, 1e-7, "take_profit_1"),
            self.trader._sell_amount(other, 25, 1e-7, "stop_loss"),
        )
        self.assertEqual(results, [True, True])
        self.assert_clean_context()

    async def test_seller_exception_and_cancellation_restore_all_context(self):
        for exception in (TimeoutError(), asyncio.CancelledError()):
            with self.subTest(exception=type(exception).__name__):
                self.seller.execute.side_effect = exception
                with self.assertRaises(type(exception)):
                    await self.trader._sell_amount(self.token, 25, 1e-7, "stop_loss")
                self.assert_clean_context()


if __name__ == "__main__":
    unittest.main()
