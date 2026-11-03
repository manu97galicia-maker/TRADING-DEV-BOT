"""Bundle integration with fake chain/buyer only; never signs or sends funds."""

# Dynamic fake objects and explicit boundary values keep financial tests readable.
# ruff: noqa: ANN001, ANN003, ANN201, ANN202, ARG001, ARG005, FBT003, PLR2004, PLR0913, PLR0917

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml

from monitoring.four_strategy_signals import choose_signal
from trading.base import TradeResult
from trading.four_strategy_book import FourStrategyBook
from trading.four_strategy_entry_guard import EntryRiskRejected
from trading.four_strategy_runtime import FourStrategyTrader, SharedBuyer
from trading.transaction_observer import OBSERVER, PRIORITY_CAP, SUBMISSIONS, observe

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load(
    (ROOT / "configs/four-strategies.yaml").read_text(encoding="utf-8")
)
POLICIES = CONFIG["four_strategies"]["strategies"]
SOL = 1_000_000_000


class SignalTests(unittest.TestCase):
    def signal(self, mc=10000, buyers=13, buy=20, sell=3, block=None, history_mc=9000):
        return choose_signal(
            {"ola_compra_sol": buy, "ola_venta_sol": sell, "ola_compradores": buyers},
            [(98.5, history_mc / 1e11), (100, mc / 1e11)],
            block,
            mc / 1e11,
            100,
            100,
            POLICIES,
        )

    def test_pressure_accepts_relaxed_demand_but_keeps_8k_and_flow_checks(self):
        self.assertEqual(
            self.signal(buyers=12, buy=10, sell=3)["strategy"], "strong-pressure-8k"
        )
        self.assertIsNone(self.signal(buyers=11))
        self.assertIsNone(self.signal(mc=7999))
        self.assertIsNone(self.signal(buy=3, sell=1))
        self.assertIsNone(self.signal(buy=9.99, sell=3))
        self.assertIsNone(self.signal(buy=13, sell=7))

    def test_21k_requires_observed_crossing_and_demand(self):
        self.assertEqual(
            self.signal(mc=21500, history_mc=20500)["strategy"], "strength-21k"
        )
        self.assertIsNone(self.signal(mc=21500, history_mc=21200))
        self.assertIsNone(self.signal(mc=21500, history_mc=22000))
        self.assertIsNone(self.signal(mc=21500, history_mc=20500, buyers=12))

    def test_four_buyers_need_the_real_creation_slot_and_450_usd(self):
        block = {"creation_seen": True, "age_seconds": 3, "buyers": 4, "buy_sol": 4.5}
        self.assertEqual(
            self.signal(buyers=4, buy=5, sell=0, block=block)["strategy"],
            "first-block-four-buyers",
        )
        for change in (
            {"buyers": 3},
            {"buy_sol": 4.49},
            {"creation_seen": False},
            {"age_seconds": 9},
            {"age_seconds": None},
        ):
            self.assertIsNone(
                self.signal(buyers=4, buy=5, sell=0, block={**block, **change})
            )

    def test_missing_fresh_price_or_invalid_data_cannot_create_a_signal(self):
        for price in (None, 0, float("nan"), float("inf")):
            self.assertIsNone(choose_signal({}, [], None, price, 100, 100, POLICIES))


class BookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "book.sqlite3"
        self.book = FourStrategyBook(self.path, "owner", "policy")

    def reserve(self, mint, strategy="pressure", balance=SOL, slot=100):
        return self.book.reserve(
            mint, strategy, balance, slot, 30_000_000, 0.15, 3_000_000, {"sol_usd": 100}
        )

    def close(self, mint, strategy, net, slot=100):
        r = self.reserve(mint, strategy, slot=slot)
        self.assertIsNotNone(r)
        self.book.submitting(r["nonce"])
        self.book.receipt(r["nonce"], mint + "-buy", "buy")
        self.book.settle(
            r["nonce"],
            net,
            slot,
            {"verified_zero_holdings": True, "signatures": [mint + "-buy"]},
        )

    def test_legacy_default_preserves_exactly_three_slots(self):
        def reserve(index):
            other = FourStrategyBook(self.path, "owner", "policy")
            return other.reserve(
                str(index), str(index % 4), SOL, 100, 30_000_000, 0.15, 3_000_000, {}
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            entries = [r for r in pool.map(reserve, range(8)) if r]
        self.assertEqual(len(entries), 3)
        self.assertTrue(all(r["budget"] <= 200_000_000 for r in entries))
        self.assertLessEqual(sum(r["budget"] for r in entries), SOL - 30_000_000)

    def test_same_mint_cannot_be_bought_by_two_strategies(self):
        self.assertIsNotNone(self.reserve("same", "telegram"))
        self.assertIsNone(self.reserve("same", "pressure"))

    def test_pending_submissions_cannot_disappear_on_restart(self):
        first = self.reserve("one")
        self.book.submitting(first["nonce"])
        self.book.receipt(first["nonce"], "lost-ack", "buy")
        self.book = FourStrategyBook(self.path, "owner", "policy")
        self.assertIsNone(self.reserve("two"))
        self.book.release_unbroadcast(first["nonce"])
        self.assertEqual(len(self.book.positions()), 1)

    def test_preflight_proof_releases_a_never_broadcast_attempt(self):
        first = self.reserve("one")
        self.book.submitting(first["nonce"])
        self.book.receipt(first["nonce"], "rejected", "buy")
        self.book.rejected(first["nonce"], "rejected")
        self.book.release_unbroadcast(first["nonce"])
        self.assertFalse(self.book.positions())

    def test_20_percent_recalculates_from_the_current_liquid_balance(self):
        one = self.reserve("one")
        self.assertLessEqual(one["budget"], 200_000_000)
        self.assertGreater(one["budget"], 199_999_997)
        self.book.submitting(one["nonce"])
        self.book.opened(one["nonce"], 100, {})
        two = self.reserve("two", balance=800_000_000)
        self.assertLessEqual(two["budget"], int(800_000_000 * 0.20))
        self.assertGreater(two["budget"], int(800_000_000 * 0.20) - 3)
        self.assertLess(two["amount_sol"], 0.20 * 0.80)  # Inclusive costs.

    def test_full_budget_must_fit_reserve_and_other_pending_spends(self):
        self.assertIsNone(self.reserve("empty", balance=31_000_000))
        self.assertIsNone(self.reserve("dust", balance=1))

    def test_three_losses_in_five_pause_only_that_strategy_and_persist(self):
        for i, net in enumerate((-1, 10, -1, 10, -1)):
            self.close(str(i), "pressure", net)
        self.assertTrue(
            FourStrategyBook(self.path, "owner", "policy").paused("pressure")
        )
        self.assertIsNone(self.reserve("six", "pressure"))
        self.assertIsNotNone(self.reserve("other", "telegram"))
        self.assertEqual(self.book.research()["strategies"]["pressure"]["wins"], 2)

    def test_receipt_race_cannot_release_the_slot_or_count_a_result(self):
        r = self.reserve("race")
        self.book.submitting(r["nonce"])
        self.book.receipt(r["nonce"], "buy", "buy")
        self.book.receipt(r["nonce"], "late-sell", "sell")
        with self.assertRaises(ValueError):
            self.book.settle(
                r["nonce"],
                1,
                100,
                {"verified_zero_holdings": True, "signatures": ["buy"]},
            )
        self.assertEqual(len(self.book.positions()), 1)

    def test_closed_mints_and_old_cash_snapshots_are_blocked(self):
        self.close("old", "pressure", 1, slot=200)
        self.assertIsNone(self.reserve("old", slot=200))
        self.assertIsNone(self.reserve("new", slot=199))

    def test_different_owner_or_policy_cannot_reuse_the_ledger(self):
        for owner, policy in (("wrong", "policy"), ("owner", "wrong")):
            with self.assertRaises(ValueError):
                FourStrategyBook(self.path, owner, policy)


class BuyerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.book = FourStrategyBook(
            Path(self.tmp.name) / "book.sqlite3", "owner", "policy"
        )
        self.token = SimpleNamespace(
            mint="mint", bonding_curve="curve", symbol="Coin", additional_data={}
        )
        self.runtime = SimpleNamespace(
            book=self.book,
            wallet=SimpleNamespace(pubkey="owner"),
            four={**CONFIG["four_strategies"], "database": str(self.book.path)},
            min_reserve_lamports=30_000_000,
            buy_slippage=0.15,
            _fee_headroom_lamports=3_000_000,
            fraccion_saldo=0.30,
            sol_usd=100,
            current_signal=lambda token: {
                "strategy": "strong-pressure-8k",
                "sol_usd": 100,
            },
            reference_age=lambda: 1,
            raw_holdings=AsyncMock(return_value=(0, 100)),
            rpc=AsyncMock(return_value={"value": SOL, "context": {"slot": 100}}),
            platform_implementations=SimpleNamespace(
                curve_manager=SimpleNamespace(
                    get_pool_state=AsyncMock(
                        return_value={"price_per_token": 1e-7, "complete": False}
                    )
                )
            ),
        )
        self.runtime.receipt_writer = lambda nonce, kind: (
            FourStrategyTrader.receipt_writer(self.runtime, nonce, kind)
        )
        state = {"price_per_token": 1e-7, "complete": False}
        self.runtime.check_entry_risk = AsyncMock(return_value=(state, {"checked": True}))
        self.runtime.update_details = lambda nonce, changes: (
            FourStrategyTrader.update_details(self.runtime, nonce, changes)
        )

    async def test_entry_risk_rejection_never_reaches_broker_or_keeps_reservation(self):
        for strategy in ("strong-pressure-8k", "strength-21k", "first-block-four-buyers", "telegram-immediate"):
            with self.subTest(strategy=strategy):
                self.runtime.current_signal = lambda token, _strategy=strategy: {"strategy": _strategy, "sol_usd": 100}
                self.runtime.check_entry_risk.side_effect = EntryRiskRejected("concentrated_creator")
                broker = SimpleNamespace(execute=AsyncMock())
                result = await SharedBuyer(self.runtime, broker).execute(self.token)
                self.assertFalse(result.success)
                broker.execute.assert_not_awaited()
                self.assertFalse(self.book.positions())

    async def test_real_buy_path_is_sized_by_the_shared_book_and_receipt_is_durable(
        self,
    ):
        async def execute(token, **kwargs):
            observe("signed", "actual-signature")
            self.assertGreater(kwargs["amount_sol"], 0.20)
            self.assertLess(kwargs["amount_sol"], 0.30)
            return TradeResult(
                True, tx_signature="actual-signature", amount=1000000, price=1e-7
            )

        broker = SimpleNamespace(execute=AsyncMock(side_effect=execute))
        result = await SharedBuyer(self.runtime, broker).execute(
            self.token, amount_sol=999
        )
        self.assertTrue(result.success)
        position = self.book.positions()[0]
        self.assertEqual(position["state"], "open")
        self.assertEqual(json.loads(position["details"])["policy_fraction"], 0.30)
        self.assertEqual(
            self.book.signatures(position["nonce"]), [("actual-signature", "buy")]
        )

    async def test_lost_acknowledgement_retains_signed_identity_and_blocks_other_entries(
        self,
    ):
        async def execute(token, **kwargs):
            observe("signed", "lost-ack")
            raise TimeoutError

        broker = SimpleNamespace(execute=AsyncMock(side_effect=execute))
        with self.assertRaises(TimeoutError):
            await SharedBuyer(self.runtime, broker).execute(self.token)
        position = self.book.positions()[0]
        self.assertEqual(position["state"], "uncertain")
        self.assertEqual(self.book.signatures(position["nonce"]), [("lost-ack", "buy")])
        self.assertIsNone(OBSERVER.get())

    async def test_rejected_preflight_does_not_consume_a_slot(self):
        async def execute(token, **kwargs):
            observe("signed", "rejected")
            observe("preflight_rejected", "rejected")
            return TradeResult(False)

        broker = SimpleNamespace(execute=AsyncMock(side_effect=execute))
        await SharedBuyer(self.runtime, broker).execute(self.token)
        self.assertFalse(self.book.positions())

    async def test_curve_recheck_cancels_before_signing_when_mc_moves_outside_band(
        self,
    ):
        self.runtime.platform_implementations.curve_manager.get_pool_state.return_value[
            "price_per_token"
        ] = 1e-8
        self.runtime.check_entry_risk.return_value = ({"price_per_token": 1e-8, "complete": False}, {})
        broker = SimpleNamespace(execute=AsyncMock())
        result = await SharedBuyer(self.runtime, broker).execute(self.token)
        self.assertFalse(result.success)
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())

    async def test_existing_holdings_and_stale_quotes_never_reach_the_broker(self):
        broker = SimpleNamespace(execute=AsyncMock())
        self.runtime.raw_holdings.return_value = (1, 100)
        self.assertFalse(
            (await SharedBuyer(self.runtime, broker).execute(self.token)).success
        )
        self.runtime.reference_age = lambda: 31
        self.assertFalse(
            (await SharedBuyer(self.runtime, broker).execute(self.token)).success
        )
        broker.execute.assert_not_awaited()


class AuditTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = BuyerTests.asyncSetUp

    async def test_only_finalized_zero_holdings_can_release_the_slot_and_record_net_pnl(
        self,
    ):
        r = self.book.reserve(
            "mint", "pressure", SOL, 100, 30_000_000, 0.15, 3_000_000, {}
        )
        self.book.submitting(r["nonce"])
        self.book.receipt(r["nonce"], "buy", "buy")
        self.book.receipt(r["nonce"], "sell", "sell")
        position = self.book.positions()[0]
        transactions = {
            "buy": {
                "slot": 100,
                "meta": {"err": None, "preBalances": [1000], "postBalances": [800]},
                "transaction": {"message": {"accountKeys": ["owner"]}},
            },
            "sell": {
                "slot": 101,
                "meta": {"err": None, "preBalances": [800], "postBalances": [1100]},
                "transaction": {"message": {"accountKeys": ["owner"]}},
            },
        }
        statuses = [
            {"slot": 100, "err": None, "confirmationStatus": "finalized"},
            {"slot": 101, "err": None, "confirmationStatus": "confirmed"},
        ]

        async def rpc(method, params):
            return (
                {"value": statuses}
                if method == "getSignatureStatuses"
                else transactions[params[0]]
            )

        self.runtime.rpc = rpc
        self.runtime.raw_holdings.return_value = (0, 200)
        await FourStrategyTrader.audit_position(self.runtime, position)
        self.assertEqual(len(self.book.positions()), 1)
        statuses[1]["confirmationStatus"] = "finalized"
        await FourStrategyTrader.audit_position(self.runtime, position)
        self.assertFalse(self.book.positions())
        self.assertEqual(
            self.book.research()["strategies"]["pressure"]["net_lamports"], 100
        )
        observation = self.book.research()["strategies"]["pressure"]["observations"][0]
        self.assertEqual(observation["buy_debit_lamports"], 200)
        self.assertTrue(observation["buy_debit_verified"])


class ThirtyPercentSizingTests(unittest.TestCase):
    setUp = BookTests.setUp

    def reserve(self, mint, balance=SOL, slot=100):
        return self.book.reserve(mint, "pressure", balance, slot, 30_000_000,
                                 0.15, 3_000_000, {}, fraction=0.30)

    def test_concurrent_pending_entries_cannot_overdraw_the_wallet(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            reservations = list(pool.map(lambda i: self.reserve(str(i)), range(4)))
        filled = [r for r in reservations if r]
        self.assertEqual(len(filled), 3)
        self.assertLessEqual(sum(r["budget"] for r in filled), SOL - 30_000_000)
        for row in self.book.positions():
            self.assertEqual(json.loads(row["details"])["policy_fraction"], .30)
            self.assertLessEqual(row["budget"], 300_000_000)

    def test_each_new_budget_tracks_the_fresh_actual_balance_and_three_slot_limit(self):
        for index, balance in enumerate([SOL, 700_000_000, 490_000_000]):
            reservation = self.reserve(str(index), balance=balance, slot=100+index)
            self.assertIsNotNone(reservation)
            self.assertLessEqual(reservation["budget"], int(balance * .30))
            self.assertGreater(reservation["budget"], int(balance * .30) - 3)
            self.book.submitting(reservation["nonce"])
            self.book.opened(reservation["nonce"], 100+index, {})
        self.assertIsNone(self.reserve("fourth", balance=343_000_000, slot=104))

    def test_historical_twenty_and_forty_percent_positions_keep_their_recorded_size(self):
        old = self.book.reserve("old", "pressure", SOL, 100, 30_000_000,
                                .15, 3_000_000, {})
        old_forty = self.book.reserve("old-forty", "telegram", SOL, 100, 30_000_000,
                                      .15, 3_000_000, {}, fraction=.40)
        self.assertIsNotNone(old_forty)
        new = self.reserve("new")
        self.assertIsNotNone(new)
        rows = {p["mint"]: p for p in self.book.positions()}
        self.assertEqual(json.loads(rows["old"]["details"])["policy_fraction"], .20)
        self.assertEqual(rows["old"]["budget"], old["budget"])
        self.assertEqual(json.loads(rows["old-forty"]["details"])["policy_fraction"], .40)
        self.assertEqual(rows["old-forty"]["budget"], old_forty["budget"])
        self.assertEqual(json.loads(rows["new"]["details"])["policy_fraction"], .30)

    def test_native_sol_reserve_still_blocks_small_balances(self):
        self.assertIsNone(self.reserve("too-small", balance=40_000_000))
        self.assertFalse(self.book.positions())

    def test_four_shared_positions_fit_after_confirmed_debits_but_a_fifth_is_rejected(self):
        self.book = FourStrategyBook(self.path, "owner", "policy", max_positions=4)
        for index, balance in enumerate([SOL, 700_000_000, 490_000_000, 343_000_000]):
            reservation = self.reserve(str(index), balance=balance, slot=100+index)
            self.assertIsNotNone(reservation)
            self.book.submitting(reservation["nonce"])
            self.book.opened(reservation["nonce"], 100+index, {})
        self.assertEqual(len(self.book.positions()), 4)
        self.assertIsNone(self.reserve("fifth", balance=240_100_000, slot=105))

    def test_four_slot_limit_still_rejects_pending_spends_that_exceed_cash(self):
        self.book = FourStrategyBook(self.path, "owner", "policy", max_positions=4)
        with ThreadPoolExecutor(max_workers=8) as pool:
            reservations = list(pool.map(lambda i: self.reserve(str(i)), range(8)))
        self.assertEqual(len([r for r in reservations if r]), 3)
        self.assertLessEqual(sum(r["budget"] for r in reservations if r), SOL-30_000_000)


class ObserverTests(unittest.TestCase):
    def test_task_local_observer_tracks_rejections_and_restores_scope(self):
        events = []
        observer = OBSERVER.set(
            lambda event, signature: events.append((event, signature))
        )
        submissions = SUBMISSIONS.set(set())
        try:
            observe("signed", "a")
            self.assertEqual(SUBMISSIONS.get(), {"a"})
            observe("preflight_rejected", "a")
            self.assertFalse(SUBMISSIONS.get())
        finally:
            OBSERVER.reset(observer)
            SUBMISSIONS.reset(submissions)
        self.assertEqual(len(events), 2)
        self.assertIsNone(PRIORITY_CAP.get())


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    setUp = BookTests.setUp
    reserve = BookTests.reserve

    async def exercise(
        self, *, stop=False, restart_partial=False, commitment="finalized", pressure=(1, 0),
        entry_overrides=None, live_price=None, initial_remaining=None,
    ):
        reservation = self.reserve("mint")
        nonce = reservation["nonce"]
        self.book.submitting(nonce)
        self.book.receipt(nonce, "buy", "buy")
        self.book.opened(
            nonce,
            100,
            {
                "entry_price": 1e-7,
                "original_qty": 100,
                "entry_sol_usd": 100,
                "executed": [],
                "pending_tier": 0 if restart_partial else None,
                **(entry_overrides or {}),
            },
        )
        remaining = initial_remaining if initial_remaining is not None else 50.0 if restart_partial else 100.0
        sales = []
        runtime = SimpleNamespace(
            book=self.book,
            four=CONFIG["four_strategies"],
            anti_rug_seg=CONFIG["trade"]["anti_rug_seg"],
            anti_rug_min_sol=CONFIG["trade"]["anti_rug_min_sol"],
            anti_rug_ratio=CONFIG["trade"]["anti_rug_ratio"],
            _clear_open_position=lambda mint: None,
        )
        runtime.update_details = lambda nonce, changes: (
            FourStrategyTrader.update_details(runtime, nonce, changes)
        )
        runtime.market_price = AsyncMock(return_value=live_price if live_price is not None else 7.4e-8 if stop else 2.8e-7)

        async def holdings(mint, *, commitment="finalized"):
            return round(remaining * 1e6), 200

        runtime.raw_holdings = holdings

        async def rpc(method, params):
            return {
                "value": [
                    {"slot": 100, "err": None, "confirmationStatus": commitment}
                    for _ in params[0]
                ]
            }

        runtime.rpc = rpc

        async def sell(token, amount, price, reason, *, full_exit):
            nonlocal remaining
            sales.append((amount, reason, full_exit))
            remaining -= amount
            self.book.receipt(nonce, "sell" + str(len(sales)), "sell")
            return True

        runtime._sell_amount = sell  # noqa: SLF001 -- injected legacy sell hook

        async def audit(position):
            self.book.settle(
                nonce,
                1,
                200,
                {
                    "verified_zero_holdings": True,
                    "signatures": [s for s, _ in self.book.signatures(nonce)],
                },
            )

        runtime.audit_position = audit
        real_sleep = asyncio.sleep

        async def fast_sleep(delay):
            await real_sleep(0)

        with (
            patch("trading.four_strategy_runtime.asyncio.sleep", fast_sleep),
            patch("trading.four_strategy_runtime.volumen.presion", return_value=pressure) as pressure_reader,
        ):
            await asyncio.wait_for(
                FourStrategyTrader.monitor_position(
                    runtime, SimpleNamespace(mint="mint")
                ),
                timeout=2,
            )
            pressure_reader.assert_not_called()
        return sales

    async def test_all_three_targets_sell_the_original_50_25_25_without_duplicates(
        self,
    ):
        sales = await self.exercise()
        self.assertEqual([s[0] for s in sales], [50, 25, 25])
        self.assertEqual([s[2] for s in sales], [False, False, True])

    async def test_stop_dumps_all_remaining_tokens(self):
        sales = await self.exercise(stop=True)
        self.assertEqual(sales, [(100, "stop_loss", True)])

    async def test_sell_pressure_does_not_trigger_the_disabled_protective_exit(self):
        sales = await self.exercise(pressure=(0, 100))
        self.assertEqual([s[0] for s in sales], [50, 25, 25])
        self.assertTrue(all(s[1].startswith("take_profit_") for s in sales))

    async def test_protective_exit_does_not_wait_for_buy_finalization(self):
        sales = await self.exercise(stop=True, commitment="confirmed")
        self.assertEqual(sales, [(100, "stop_loss", True)])

    async def test_restart_after_first_sale_does_not_sell_that_half_again(self):
        sales = await self.exercise(restart_partial=True)
        self.assertEqual([s[0] for s in sales], [25, 25])


if __name__ == "__main__":
    unittest.main()
