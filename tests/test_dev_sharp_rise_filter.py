"""Offline regressions for the DEV-only sharp-rise filter exception."""

# Synthetic prices and mocked read-only RPC checks never start a trader.
# ruff: noqa: ANN201, FBT003

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml
from solders.pubkey import Pubkey

from trading.four_strategy_entry_guard import EntryRiskRejected, check_price
from trading.four_strategy_runtime import FourStrategyTrader
from trading.second_block_policy import STRATEGY_ID, validate_second_policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = yaml.safe_load(
    (ROOT / "configs/four-strategies.yaml").read_text(encoding="utf-8")
)["four_strategies"]["entry_protection"]
SECOND_POLICY = yaml.safe_load(
    (ROOT / "configs/second-block-dev.yaml").read_text(encoding="utf-8")
)


class SharpRisePolicyTests(unittest.TestCase):
    def test_saved_dev_policy_disables_only_the_sharp_rise_filter(self):
        self.assertIs(SECOND_POLICY["reject_sharp_price_rise"], False)
        self.assertIs(SECOND_POLICY["enabled"], False)
        self.assertEqual(POLICY["max_rise_fraction"], 0.35)
        self.assertEqual(POLICY["max_drawdown_fraction"], 0.10)
        self.assertEqual(POLICY["max_quote_increase_fraction"], 0.10)
        self.assertEqual(POLICY["max_price_age_seconds"], 1.0)

    def test_explicit_boolean_and_legacy_omission_are_valid(self):
        for setting in (None, False, True):
            with self.subTest(setting=setting):
                policy = copy.deepcopy(SECOND_POLICY)
                policy["enabled"] = True
                if setting is None:
                    policy.pop("reject_sharp_price_rise", None)
                else:
                    policy["reject_sharp_price_rise"] = setting
                validate_second_policy(policy)

    def test_non_boolean_filter_setting_is_rejected(self):
        for value in (None, "false", "true", 0, 1, [], {}):
            with self.subTest(value=value):
                policy = copy.deepcopy(SECOND_POLICY)
                policy.update(enabled=True, reject_sharp_price_rise=value)
                with self.assertRaises(ValueError):
                    validate_second_policy(policy)


class SharpRisePriceTests(unittest.TestCase):
    def test_default_and_explicit_true_reject_the_same_spike(self):
        for options in ({}, {"reject_sharp_rise": True}):
            with (
                self.subTest(options=options),
                self.assertRaisesRegex(EntryRiskRejected, "sharp_price_rise"),
            ):
                check_price([(99, 100), (99.9, 140)], 140, 140, 100, POLICY, **options)

    def test_false_permits_spike_and_preserves_measured_evidence(self):
        report = check_price(
            [(99, 100), (99.9, 140)],
            140,
            140,
            100,
            POLICY,
            reject_sharp_rise=False,
        )
        self.assertAlmostEqual(report["rise_fraction"], 0.40)
        self.assertAlmostEqual(report["drawdown_fraction"], 0)
        self.assertAlmostEqual(report["quote_increase_fraction"], 0)
        self.assertAlmostEqual(report["observed_window_seconds"], 1)

    def test_non_boolean_exception_cannot_disable_the_filter(self):
        for value in (None, "false", "true", 0, 1, [], {}):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(EntryRiskRejected, "invalid_sharp_rise_policy"),
            ):
                check_price(
                    [(99, 100), (99.9, 140)],
                    140,
                    140,
                    100,
                    POLICY,
                    reject_sharp_rise=value,
                )

    def test_false_retains_dump_drift_history_and_finite_price_guards(self):
        cases = [
            ([(99, 100), (99.9, 160)], 140, 140, "price_dump_from_recent_peak"),
            ([(99, 100), (99.9, 140)], 140, 120, "price_ran_during_validation"),
            ([(99.9, 100)], 140, 140, "missing_fresh_price_history"),
            ([(98.9, 100), (98.99, 140)], 140, 140, "missing_fresh_price_history"),
            ([(97, 100), (99.9, 140)], 140, 140, "missing_fresh_price_history"),
            ([(99, 100), (99.9, float("nan"))], 140, 140, "invalid_price_history"),
            ([(99, 100), (99.9, 140)], float("inf"), 140, "invalid_entry_price"),
            ([(99, 100), (99.9, 140)], 140, float("nan"), "invalid_entry_price"),
        ]
        for points, price, anchor, reason in cases:
            with (
                self.subTest(points=points, reason=reason),
                self.assertRaisesRegex(EntryRiskRejected, reason),
            ):
                check_price(points, price, anchor, 100, POLICY, reject_sharp_rise=False)

    def test_false_retains_exact_drawdown_drift_and_freshness_boundaries(self):
        report = check_price(
            [(98.5, 100), (99, 150)],
            135,
            135,
            100,
            POLICY,
            reject_sharp_rise=False,
        )
        self.assertAlmostEqual(report["drawdown_fraction"], 0.10)
        report = check_price(
            [(99, 100), (99.9, 143)],
            143,
            130,
            100,
            POLICY,
            reject_sharp_rise=False,
        )
        self.assertAlmostEqual(report["quote_increase_fraction"], 0.10)


class SharpRiseRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.creator = str(Pubkey.new_unique())
        self.developer = str(Pubkey.new_unique())
        self.token = SimpleNamespace(
            mint=Pubkey.new_unique(),
            creator=self.creator,
            bonding_curve=Pubkey.new_unique(),
            additional_data={
                "learning_entry": {
                    "strategy": STRATEGY_ID,
                    "developer": self.developer,
                }
            },
        )
        self.initial = {
            "price_per_token": 140,
            "creator": self.creator,
            "complete": False,
        }
        self.current = {**self.initial, "price_per_token": 145}
        self.order = []
        self.concentration_evidence = {"slot": 123, "creator_fraction": 0.03}

        async def concentration(*_args: object, **_kwargs: object) -> dict:
            self.order.append("concentration")
            return self.concentration_evidence

        async def reread(*_args: object, **_kwargs: object) -> dict:
            self.order.append("reread")
            return self.current

        self.snapshot = AsyncMock(side_effect=concentration)
        self.pool_state = AsyncMock(side_effect=reread)
        self.runtime = SimpleNamespace(
            four={"entry_protection": POLICY},
            second_policy={"reject_sharp_price_rise": False},
            rpc=AsyncMock(side_effect=AssertionError("Unexpected real RPC access")),
            platform_implementations=SimpleNamespace(
                curve_manager=SimpleNamespace(get_pool_state=self.pool_state)
            ),
        )
        self.points = [(99, 100), (99.9, 140)]
        patches = [
            patch(
                "trading.four_strategy_runtime.concentration_snapshot", self.snapshot
            ),
            patch("trading.four_strategy_runtime.check_price", wraps=check_price),
            patch("trading.four_strategy_runtime.time.time", return_value=100),
            patch(
                "trading.four_strategy_runtime.volumen._historia_precio",
                {
                    str(self.token.mint): self.points,
                },
            ),
        ]
        started = [p.start() for p in patches]
        self.price_check = started[1]
        for item in patches:
            self.addCleanup(item.stop)

    async def test_dev_spike_passes_using_reread_price_and_developer_concentration(
        self,
    ):
        current, evidence = await FourStrategyTrader.check_entry_risk(
            self.runtime,
            self.token,
            self.initial,
        )
        self.assertIs(current, self.current)
        self.assertIs(evidence["concentration"], self.concentration_evidence)
        self.assertAlmostEqual(evidence["price"]["rise_fraction"], 0.45)
        self.assertEqual(self.order, ["concentration", "reread"])
        self.snapshot.assert_awaited_once_with(
            self.token,
            self.creator,
            self.runtime.rpc,
            POLICY,
            developer=self.developer,
        )
        self.pool_state.assert_awaited_once_with(
            self.token.bonding_curve,
            commitment="processed",
        )
        self.price_check.assert_called_once_with(
            self.points,
            145,
            140,
            100,
            POLICY,
            reject_sharp_rise=False,
        )
        self.assertEqual(self.token.additional_data["precio_curva"], 145)
        self.assertGreater(self.token.additional_data["precio_curva_en"], 0)
        self.runtime.rpc.assert_not_awaited()

    async def test_dev_default_and_true_keep_the_spike_rejection(self):
        for second_policy in ({}, {"reject_sharp_price_rise": True}, None):
            with self.subTest(second_policy=second_policy):
                if second_policy is None:
                    del self.runtime.second_policy
                else:
                    self.runtime.second_policy = second_policy
                self.price_check.reset_mock()
                with self.assertRaisesRegex(EntryRiskRejected, "sharp_price_rise"):
                    await FourStrategyTrader.check_entry_risk(
                        self.runtime,
                        self.token,
                        self.initial,
                    )
                self.assertIs(
                    self.price_check.call_args.kwargs["reject_sharp_rise"], True
                )
                self.assertNotIn("precio_curva", self.token.additional_data)

    async def test_other_and_unlabelled_entries_keep_the_spike_rejection(self):
        for strategy in (
            "strong-pressure-8k",
            "telegram-immediate",
            "first-block-four-buyers",
            "strength-21k",
            None,
        ):
            with self.subTest(strategy=strategy):
                self.token.additional_data = (
                    {"learning_entry": {"strategy": strategy}} if strategy else {}
                )
                self.snapshot.reset_mock()
                self.price_check.reset_mock()
                with self.assertRaisesRegex(EntryRiskRejected, "sharp_price_rise"):
                    await FourStrategyTrader.check_entry_risk(
                        self.runtime,
                        self.token,
                        self.initial,
                    )
                self.assertIs(
                    self.price_check.call_args.kwargs["reject_sharp_rise"], True
                )
                self.snapshot.assert_awaited_once_with(
                    self.token,
                    self.creator,
                    self.runtime.rpc,
                    POLICY,
                )
                self.assertNotIn("precio_curva", self.token.additional_data)

    async def test_dev_exception_still_rejects_concentration_before_price_validation(
        self,
    ):
        self.snapshot.side_effect = EntryRiskRejected("concentrated_developer")
        with self.assertRaisesRegex(EntryRiskRejected, "concentrated_developer"):
            await FourStrategyTrader.check_entry_risk(
                self.runtime,
                self.token,
                self.initial,
            )
        self.snapshot.assert_awaited_once()
        self.pool_state.assert_not_awaited()
        self.price_check.assert_not_called()

    async def test_dev_exception_still_rejects_a_curve_changed_during_reread(self):
        for change in ({"complete": True}, {"creator": str(Pubkey.new_unique())}):
            with self.subTest(change=change):
                self.current = {**self.initial, **change}
                with self.assertRaisesRegex(
                    EntryRiskRejected, "curve_changed_during_risk_check"
                ):
                    await FourStrategyTrader.check_entry_risk(
                        self.runtime,
                        self.token,
                        self.initial,
                    )
                self.price_check.assert_not_called()
                self.assertNotIn("precio_curva", self.token.additional_data)

    async def test_dev_exception_still_rejects_a_quote_that_ran_during_reread(self):
        self.current["price_per_token"] = 155
        with self.assertRaisesRegex(EntryRiskRejected, "price_ran_during_validation"):
            await FourStrategyTrader.check_entry_risk(
                self.runtime,
                self.token,
                self.initial,
            )
        self.assertEqual(self.order, ["concentration", "reread"])
        self.assertIs(self.price_check.call_args.kwargs["reject_sharp_rise"], False)
        self.assertNotIn("precio_curva", self.token.additional_data)


if __name__ == "__main__":
    unittest.main()
