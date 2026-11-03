"""Offline policy, shared-budget and broadcast-deadline regressions."""

# ruff: noqa: ANN001, ANN002, ANN003, ANN201, ANN202, ARG001

import copy
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_four_strategy_bundle as bundle
import yaml
from solders.hash import Hash
from solders.keypair import Keypair

from core.client import SolanaClient
from interfaces.core import Platform, TokenInfo
from monitoring.four_strategy_notifications import purchase_message
from trading.base import TradeResult
from trading.four_strategy_runtime import SharedBuyer
from trading.platform_aware import PlatformAwareBuyer
from trading.second_block_policy import (
    STRATEGY_ID,
    load_second_policy,
    position_exit_plan,
    recovery_allocations,
    second_block_ladder,
    validate_second_policy,
)
from trading.transaction_observer import (
    BUY_PRICE_CEILING,
    ENTRY_WINDOW,
    OBSERVER,
    PRIORITY_CAP,
    EntryWindowExpiredError,
    capped_buy_cost_lamports,
    check_entry_window,
    observe,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = yaml.safe_load((ROOT / "configs/second-block-dev.yaml").read_text())


def enabled_policy():
    policy = copy.deepcopy(POLICY)
    policy["enabled"] = True
    policy["exit"].update(first_sale_mode="recover_capital", recovery_profit_fraction=None,
                          stop_loss_fraction=.12)
    return policy


class PolicyTests(unittest.TestCase):
    def test_pending_exit_cannot_be_enabled(self):
        validate_second_policy(POLICY)
        pending = copy.deepcopy(POLICY)
        pending["enabled"] = True
        pending["exit"]["first_sale_mode"] = "pending"
        with self.assertRaises(ValueError):
            validate_second_policy(pending)
        validate_second_policy(enabled_policy())

    def test_saved_user_selection_recovers_capital_plus_12_at_35_with_22_stop(self):
        selected = copy.deepcopy(POLICY)
        self.assertFalse(selected["enabled"])
        selected["enabled"] = True  # Pure validation of a fixture; no trader is started.
        validate_second_policy(selected)
        self.assertEqual(selected["exit"]["first_sale_mode"], "recover_capital_plus_profit")
        self.assertEqual(selected["exit"]["recovery_profit_fraction"], .12)
        self.assertEqual(selected["exit"]["stop_loss_fraction"], .22)
        fractions = recovery_allocations(selected["exit"], 1, 1.35)
        self.assertAlmostEqual(fractions[0]*1.35, 1.12)
        self.assertAlmostEqual(fractions[0], 1.12/1.35)
        self.assertAlmostEqual(sum(fractions), 1)
        for fraction in fractions[1:]:
            self.assertAlmostEqual(fraction, (1-1.12/1.35)/3)

    def test_compounded_targets_and_capital_recovery(self):
        policy = enabled_policy()["exit"]
        tiers = second_block_ladder(4000, policy)
        for tier, expected in zip(tiers, [1.35, 1.89, 2.646, 3.7044], strict=True):
            self.assertAlmostEqual(tier.target_mc_usd, 4000*expected)
        self.assertAlmostEqual(tiers[0].sell_original_fraction, 1/1.35)
        self.assertAlmostEqual(sum(t.sell_original_fraction for t in tiers), 1)
        allocation = recovery_allocations(policy, 1, 1.5)
        self.assertAlmostEqual(allocation[0], 2/3)
        persisted = {"entry_price": 4e-8, "entry_sol_usd": 100,
                     "entry_exit_policy": policy, "exit_allocations": allocation}
        restored, stop = position_exit_plan(persisted, bundle.CONFIG["four_strategies"]["exit"])
        self.assertEqual(stop, .12)
        self.assertEqual([t.sell_original_fraction for t in restored], allocation)

    def test_alternative_meanings_of_twelve_stay_explicit(self):
        policy = enabled_policy()["exit"]
        policy.update(first_sale_mode="recover_capital_plus_profit", recovery_profit_fraction=.12)
        self.assertAlmostEqual(second_block_ladder(4000, policy)[0].sell_original_fraction, 1.12/1.35)
        policy.update(first_sale_mode="original_fraction", first_sale_original_fraction=.12)
        self.assertEqual(second_block_ladder(4000, policy)[0].sell_original_fraction, .12)

    def test_default_four_settings_and_ledger_identity_are_unchanged(self):
        plan = copy.deepcopy(bundle.CONFIG)
        before = json.dumps(plan["four_strategies"], sort_keys=True)
        loaded = load_second_policy(ROOT / "configs/four-strategies.yaml", plan)
        self.assertFalse(loaded["enabled"])
        self.assertEqual(json.dumps(plan["four_strategies"], sort_keys=True), before)
        old, stop = position_exit_plan({"entry_price": 1e-7, "entry_sol_usd": 100}, plan["four_strategies"]["exit"])
        self.assertEqual([t.sell_original_fraction for t in old], [.5, .25, .25])
        self.assertEqual(stop, .25)

    def test_notification_uses_the_new_stop_and_four_targets(self):
        msg = purchase_message(SimpleNamespace(mint="mint", name="Token", symbol="TK"),
            SimpleNamespace(price=4e-8, amount=1e6),
            {"entry_sol_usd": 100, "entry_exit_policy": enabled_policy()["exit"],
             "features": {"strategy": STRATEGY_ID, "buyers": 2, "volume_usd": 275,
                 "creator_history": {"migrations": 2, "launches": 100}}},
            bundle.CONFIG["four_strategies"]["exit"])
        for expected in ["Segundo bloque", "2 wallets", "275", "2/100", "TP4", "12%"]:
            self.assertIn(expected, msg)

    def test_older_position_stop_is_tightened_to_22_without_loosening_a_stricter_stop(self):
        defaults = {**bundle.CONFIG["four_strategies"]["exit"], "stop_loss_fraction": .22}
        saved = {"entry_price": 1e-7, "entry_sol_usd": 100,
                 "entry_exit_policy": {**enabled_policy()["exit"], "stop_loss_fraction": .25}}
        self.assertEqual(position_exit_plan(saved, defaults)[1], .22)
        saved["entry_exit_policy"]["stop_loss_fraction"] = .12
        self.assertEqual(position_exit_plan(saved, defaults)[1], .12)

    def test_average_buy_cost_ceiling_is_strict_and_never_enlarges_a_budget(self):
        token = BUY_PRICE_CEILING.set(4.2e-8)
        try:
            self.assertEqual(capped_buy_cost_lamports(250000000, 5000000000000), 209999999)
            self.assertEqual(capped_buy_cost_lamports(100000000, 5000000000000), 100000000)
        finally:
            BUY_PRICE_CEILING.reset(token)
        self.assertEqual(capped_buy_cost_lamports(250000000, 5000000000000), 250000000)


class SharedEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await bundle.BuyerTests.asyncSetUp(self)
        self.runtime.second_policy = enabled_policy()
        self.runtime.compute_units = {"buy": 200000}
        self.features = {"strategy": STRATEGY_ID, "sol_usd": 100, "creator": "dev", "target_slot": 101}
        self.runtime.current_signal = lambda _token: self.features
        self.runtime.check_entry_risk.return_value = ({"price_per_token": 4e-8, "complete": False, "creator": "dev"}, {})

    async def test_new_entry_has_90_percent_inclusive_budget_and_persisted_exits(self):
        async def buy(token, **kwargs):
            self.assertGreater(kwargs["amount_sol"], .70)
            self.assertLess(kwargs["amount_sol"], .90)
            self.assertEqual(PRIORITY_CAP.get(), 600000)
            self.assertEqual(BUY_PRICE_CEILING.get(), 4.2e-8)
            self.assertLessEqual((kwargs["priority_fee_override"]*200000+999999)//1000000, 600000)
            check_entry_window()
            details = json.loads(self.book.positions()[0]["details"])
            self.assertEqual(details["entry_exit_policy"]["stop_loss_fraction"], .12)
            observe("signed", "new-buy")
            return TradeResult(success=True, tx_signature="new-buy", price=4e-8, amount=1e6)
        await SharedBuyer(self.runtime, SimpleNamespace(execute=AsyncMock(side_effect=buy))).execute(self.token)
        position = self.book.positions()[0]
        self.assertLessEqual(position["budget"], 900000000)
        self.assertGreater(position["budget"], 899999997)
        self.assertEqual(json.loads(position["details"])["policy_fraction"], .90)
        self.assertIsNone(PRIORITY_CAP.get())
        self.assertIsNone(ENTRY_WINDOW.get())
        self.assertIsNone(BUY_PRICE_CEILING.get())

    async def test_exact_4200_and_changed_creator_are_rejected_before_broker(self):
        broker = SimpleNamespace(execute=AsyncMock())
        for state in [
            {"price_per_token": 4.2e-8, "creator": "dev"},
            {"price_per_token": 4e-8, "creator": "other"},
        ]:
            self.runtime.check_entry_risk.return_value = (state, {})
            self.assertFalse((await SharedBuyer(self.runtime, broker).execute(self.token)).success)
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())

    async def test_expiry_without_broadcast_releases_only_its_reservation(self):
        async def buy(*args, **kwargs):
            self.features = None
            check_entry_window()
        with self.assertRaises(EntryWindowExpiredError):
            await SharedBuyer(self.runtime, SimpleNamespace(execute=AsyncMock(side_effect=buy))).execute(self.token)
        self.assertFalse(self.book.positions())
        self.assertIsNone(ENTRY_WINDOW.get())

    async def test_unacknowledged_signature_keeps_new_policy_for_restart(self):
        async def buy(*args, **kwargs):
            observe("signed", "unknown-buy")
            raise TimeoutError
        with self.assertRaises(TimeoutError):
            await SharedBuyer(self.runtime, SimpleNamespace(execute=AsyncMock(side_effect=buy))).execute(self.token)
        position = self.book.positions()[0]
        self.assertEqual(position["state"], "uncertain")
        self.assertEqual(json.loads(position["details"])["entry_policy_id"], STRATEGY_ID)
        self.assertEqual(self.book.signatures(position["nonce"]), [("unknown-buy", "buy")])


class ExitMonitorTests(unittest.IsolatedAsyncioTestCase):
    setUp = bundle.BookTests.setUp
    reserve = bundle.BookTests.reserve
    exercise = bundle.MonitorTests.exercise

    async def test_four_sales_and_final_dust_exit_use_the_persisted_policy(self):
        sales = await self.exercise(entry_overrides={"entry_exit_policy": enabled_policy()["exit"]},
                                    live_price=3.8e-7)
        self.assertEqual(len(sales), 4)
        self.assertAlmostEqual(sum(s[0] for s in sales), 100, delta=1e-6)
        self.assertAlmostEqual(sales[0][0]*3.8, 100)  # Nominal capital recovered at the observed overshoot.
        self.assertEqual([s[2] for s in sales], [False, False, False, True])

    async def test_new_stop_is_independent_of_the_older_25_percent_stop(self):
        sales = await self.exercise(entry_overrides={"entry_exit_policy": enabled_policy()["exit"]},
                                    live_price=8.7e-8)
        self.assertEqual(sales, [(100, "stop_loss", True)])

    async def test_restart_after_partial_first_sale_uses_frozen_allocation_without_reselling(self):
        policy = enabled_policy()["exit"]
        allocations = recovery_allocations(policy, 1, 1.35)
        sales = await self.exercise(restart_partial=True,
            entry_overrides={"entry_exit_policy": policy, "exit_allocations": allocations},
            live_price=3.8e-7, initial_remaining=100*(1-allocations[0]))
        self.assertEqual(len(sales), 3)
        for amount, _reason, _full in sales:
            self.assertAlmostEqual(amount, 100*allocations[1], delta=1e-6)


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_token_buy_instruction_contains_the_strict_cost_ceiling(self):
        mint, curve, owner = [Keypair().pubkey() for _ in range(3)]
        token = TokenInfo(name="Fake", symbol="FK", uri="", mint=mint,
            platform=Platform.PUMP_FUN, bonding_curve=curve,
            additional_data={"precio_curva": 4e-8, "precio_curva_en": time.monotonic()})
        builder = SimpleNamespace(build_buy_instruction=AsyncMock(return_value=[]),
            get_required_accounts_for_buy=lambda *_args: [],
            get_buy_compute_unit_limit=lambda override: override)
        client = SimpleNamespace(build_and_send_transaction=AsyncMock(return_value="fake-signature"),
                                 confirm_transaction=AsyncMock(return_value=False))
        buyer = PlatformAwareBuyer(client, SimpleNamespace(pubkey=owner, keypair=object()),
            SimpleNamespace(), .2, slippage=.15, max_retries=1, compute_units={"buy": 200000})
        context = BUY_PRICE_CEILING.set(4.2e-8)
        try:
            with patch("trading.platform_aware.get_platform_implementations", return_value=SimpleNamespace(
                instruction_builder=builder, address_provider=object(), curve_manager=object())):
                await buyer.execute(token, priority_fee_override=10000)
        finally:
            BUY_PRICE_CEILING.reset(context)
        self.assertEqual(builder.build_buy_instruction.await_args.args[2:4], (209999999, 5000000000000))
        client.build_and_send_transaction.assert_awaited_once()

    async def exercise(self, stage):
        events, valid = [], True
        async def rate_limit():
            nonlocal valid
            if stage == "rate_limit":
                valid = False
        async def blockhash():
            nonlocal valid
            if stage == "blockhash":
                valid = False
            return Hash.default()
        async def send(transaction, opts):
            self.assertEqual(opts.max_retries, 0)
            self.assertEqual(len(events), 1)
            return SimpleNamespace(value="fake-signature")
        rpc = SimpleNamespace(send_transaction=AsyncMock(side_effect=send))
        client = SimpleNamespace(get_client=AsyncMock(return_value=rpc),
            get_cached_blockhash=blockhash, _rate_limiter=SimpleNamespace(acquire=rate_limit))
        guard = ENTRY_WINDOW.set(lambda: valid)
        observer = OBSERVER.set(lambda event, sig: events.append((event, sig)))
        try:
            if stage:
                with self.assertRaises(EntryWindowExpiredError):
                    await SolanaClient.build_and_send_transaction(client, [], Keypair(), max_retries=3)
                rpc.send_transaction.assert_not_awaited()
                self.assertFalse(events)
            else:
                await SolanaClient.build_and_send_transaction(client, [], Keypair(), max_retries=3)
                rpc.send_transaction.assert_awaited_once()
        finally:
            ENTRY_WINDOW.reset(guard)
            OBSERVER.reset(observer)

    async def test_deadline_after_blockhash_prevents_signing_and_send(self):
        await self.exercise("blockhash")

    async def test_deadline_after_rate_limiter_prevents_broadcast_and_receipt(self):
        await self.exercise("rate_limit")

    async def test_current_window_records_receipt_before_single_send_with_rpc_retries_zero(self):
        await self.exercise(None)
