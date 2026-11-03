"""Adversarial snapshots and entry rejects; no real wallet, sends or swaps."""

# Dynamic RPC fixtures and precise boundary values make the safety checks explicit.
# ruff: noqa: ANN001, ANN201

import asyncio
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml
from solders.pubkey import Pubkey
from spl.token.constants import TOKEN_2022_PROGRAM_ID, TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address

from trading.four_strategy_entry_guard import (
    PUMP_PROGRAM,
    EntryRiskRejected,
    check_price,
    concentration_snapshot,
    validate_policy,
)
from trading.four_strategy_runtime import FourStrategyTrader

CONFIG = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "configs/four-strategies.yaml").read_text(
        encoding="utf-8"
    )
)
POLICY = CONFIG["four_strategies"]["entry_protection"]
SUPPLY = 10**15


class PriceTests(unittest.TestCase):
    def test_measured_price_and_partial_creation_window_are_allowed(self):
        report = check_price([(99.9, 100), (99.95, 102)], 105, 100, 100, POLICY)
        self.assertAlmostEqual(report["rise_fraction"], 0.05)
        self.assertAlmostEqual(report["observed_window_seconds"], 0.1)

    def test_spike_dump_drift_and_missing_history_are_rejected(self):
        cases = [
            ([(99, 100), (99.9, 140)], 140, 140, "sharp_price_rise"),
            ([(99, 100), (99.9, 110)], 98, 100, "price_dump_from_recent_peak"),
            ([(99, 110), (99.9, 111)], 111, 100, "price_ran_during_validation"),
            ([(99.9, 100)], 100, 100, "missing_fresh_price_history"),
            ([(98.1, 100), (98.2, 100)], 100, 100, "missing_fresh_price_history"),
        ]
        for points, price, anchor, reason in cases:
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(EntryRiskRejected, reason),
            ):
                check_price(points, price, anchor, 100, POLICY)

    def test_exact_boundaries_are_allowed(self):
        check_price([(99, 100), (99.9, 135)], 135, 135, 100, POLICY)
        check_price([(99, 100), (99.9, 110)], 110, 100, 100, POLICY)
        check_price([(99, 100), (99.9, 90)], 90, 100, 100, POLICY)

    def test_invalid_policy_and_non_finite_prices_are_rejected(self):
        validate_policy(POLICY)
        for field in (
            "max_rise_fraction",
            "max_creator_fraction",
            "rpc_timeout_seconds",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_policy({**POLICY, field: float("nan")})
        with self.assertRaises(ValueError):
            validate_policy({**POLICY, "max_creator_fraction": 0.50})
        with self.assertRaises(EntryRiskRejected):
            check_price([(99, 100), (99.9, 101)], float("nan"), 100, 100, POLICY)


class ConcentrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mint, self.creator = Pubkey.new_unique(), Pubkey.new_unique()
        self.program = TOKEN_PROGRAM_ID
        self.curve, _ = Pubkey.find_program_address(
            [b"bonding-curve", bytes(self.mint)], PUMP_PROGRAM
        )
        self.vault = get_associated_token_address(self.curve, self.mint, self.program)
        self.token = SimpleNamespace(
            mint=self.mint,
            creator=self.creator,
            token_program_id=self.program,
            bonding_curve=self.curve,
            associated_bonding_curve=self.vault,
            additional_data={},
        )
        self.accounts = {
            str(self.mint): {
                "owner": str(self.program),
                "data": {
                    "parsed": {
                        "type": "mint",
                        "info": {
                            "supply": str(SUPPLY),
                            "decimals": 6,
                            "isInitialized": True,
                        },
                    }
                },
            },
            str(self.curve): {
                "owner": str(PUMP_PROGRAM),
                "data": ["opaque-curve-data", "base64"],
            },
        }
        self.top, self.creator_addresses = [], []
        self.add(str(self.curve), 0.80, str(self.vault))
        self.add(str(self.creator), 0.015)
        self.add(str(self.creator), 0.015)
        owner_a, owner_b = str(Pubkey.new_unique()), str(Pubkey.new_unique())
        self.a1 = self.add(owner_a, 0.02)
        self.a2 = self.add(owner_a, 0.02)
        self.add(owner_b, 0.04)
        self.snapshot_slot = 101
        self.rpc = AsyncMock(side_effect=self.read_rpc)

    def add(self, owner, fraction, address=None):
        address = address or str(Pubkey.new_unique())
        self.accounts[address] = {
            "owner": str(self.program),
            "data": {
                "parsed": {
                    "type": "account",
                    "info": {
                        "mint": str(self.mint),
                        "owner": owner,
                        "state": "initialized",
                        "tokenAmount": {
                            "amount": str(round(fraction * SUPPLY)),
                            "decimals": 6,
                        },
                    },
                }
            },
        }
        self.top.append(address)
        if owner == str(self.creator):
            self.creator_addresses.append(address)
        return address

    async def read_rpc(self, method, params):
        if method == "getTokenLargestAccounts":
            return {
                "context": {"slot": 100},
                "value": [{"address": a} for a in self.top],
            }
        if method == "getTokenAccountsByOwner":
            return {
                "context": {"slot": 101},
                "value": [
                    {"pubkey": a, "account": self.accounts[a]}
                    for a in self.creator_addresses
                ],
            }
        if method == "getMultipleAccounts":
            self.assertEqual(params[1]["minContextSlot"], 101)
            return {
                "context": {"slot": self.snapshot_slot},
                "value": [self.accounts.get(a) for a in params[0]],
            }
        self.fail(f"Unexpected RPC operation: {method}")

    async def check(self):
        return await concentration_snapshot(
            self.token, str(self.creator), self.rpc, POLICY
        )

    async def test_curve_reserve_is_excluded_and_same_owner_accounts_are_grouped(self):
        report = await self.check()
        self.assertEqual(report["curve_reserve_excluded_raw"], int(0.80 * SUPPLY))
        self.assertAlmostEqual(report["max_observed_owner_fraction"], 0.04)
        self.assertAlmostEqual(report["creator_fraction"], 0.03)
        self.assertAlmostEqual(report["top10_observed_owners_fraction"], 0.11)
        self.assertIn("related_wallets_not_identified", report["coverage"])

    async def test_one_atom_burn_is_tolerated_but_different_supply_is_rejected(self):
        info = self.accounts[str(self.mint)]["data"]["parsed"]["info"]
        info["supply"] = str(SUPPLY - 1)
        await self.check()
        info["supply"] = str(SUPPLY // 2)
        with self.assertRaisesRegex(
            EntryRiskRejected, "unsupported_mint_supply_or_decimals"
        ):
            await self.check()

    async def test_owner_cannot_evade_limit_with_multiple_observed_accounts(self):
        for address in (self.a1, self.a2):
            self.accounts[address]["data"]["parsed"]["info"]["tokenAmount"][
                "amount"
            ] = str(int(0.041 * SUPPLY))
        with self.assertRaisesRegex(EntryRiskRejected, "concentrated_observed_owner"):
            await self.check()

    async def test_creator_accounts_outside_top20_are_still_counted(self):
        self.top = [a for a in self.top if a not in self.creator_addresses]
        for address in self.creator_addresses:
            self.accounts[address]["data"]["parsed"]["info"]["tokenAmount"][
                "amount"
            ] = str(int(0.026 * SUPPLY))
        with self.assertRaisesRegex(EntryRiskRejected, "concentrated_creator"):
            await self.check()

    async def test_top10_concentration_is_checked_even_when_each_owner_passes(self):
        self.accounts[str(self.vault)]["data"]["parsed"]["info"]["tokenAmount"][
            "amount"
        ] = str(int(0.5 * SUPPLY))
        for _ in range(4):
            self.add(str(Pubkey.new_unique()), 0.06)
        with self.assertRaisesRegex(
            EntryRiskRejected, "concentrated_top10_observed_owners"
        ):
            await self.check()

    async def test_wrong_reserve_address_cannot_exempt_a_holder(self):
        self.token.associated_bonding_curve = Pubkey.new_unique()
        with self.assertRaisesRegex(EntryRiskRejected, "wrong_curve_reserve_address"):
            await self.check()
        self.rpc.assert_not_awaited()

    async def test_wrong_curve_owner_and_reserve_owner_are_rejected(self):
        original = copy.deepcopy(self.accounts)
        self.accounts[str(self.curve)]["owner"] = str(Pubkey.new_unique())
        with self.assertRaisesRegex(EntryRiskRejected, "wrong_curve_program"):
            await self.check()
        self.accounts = original
        self.accounts[str(self.vault)]["data"]["parsed"]["info"]["owner"] = str(
            self.creator
        )
        with self.assertRaisesRegex(EntryRiskRejected, "wrong_curve_reserve_owner"):
            await self.check()

    async def test_missing_stale_and_wrong_mint_evidence_cannot_authorize_entry(self):
        self.snapshot_slot = 100
        with self.assertRaisesRegex(EntryRiskRejected, "incomplete_or_stale"):
            await self.check()
        self.snapshot_slot = 101
        account = self.accounts.pop(self.a1)
        with self.assertRaisesRegex(EntryRiskRejected, "missing_account_snapshot"):
            await self.check()
        self.accounts[self.a1] = account
        account["data"]["parsed"]["info"]["mint"] = str(Pubkey.new_unique())
        with self.assertRaisesRegex(EntryRiskRejected, "invalid_token_account_state"):
            await self.check()

    async def test_token_2022_reserve_uses_the_actual_token_program(self):
        old_vault = str(self.vault)
        self.program = TOKEN_2022_PROGRAM_ID
        self.token.token_program_id = self.program
        self.vault = get_associated_token_address(self.curve, self.mint, self.program)
        self.token.associated_bonding_curve = self.vault
        self.accounts[str(self.vault)] = self.accounts.pop(old_vault)
        self.top = [str(self.vault) if a == old_vault else a for a in self.top]
        for address, account in self.accounts.items():
            if address != str(self.curve):
                account["owner"] = str(self.program)
        await self.check()

    async def test_runtime_rejects_bad_price_evidence_after_concentration(self):
        runtime = SimpleNamespace(
            four={"entry_protection": POLICY},
            rpc=self.rpc,
            platform_implementations=SimpleNamespace(
                curve_manager=SimpleNamespace(
                    get_pool_state=AsyncMock(
                        return_value={
                            "price_per_token": 1.4e-7,
                            "complete": False,
                            "creator": str(self.creator),
                        }
                    )
                )
            ),
        )
        with (
            patch("trading.four_strategy_runtime.time.time", return_value=100),
            patch(
                "trading.four_strategy_runtime.volumen._historia_precio",
                {str(self.mint): [(99, 1e-7), (99.9, 1.4e-7)]},
            ),
            self.assertRaisesRegex(EntryRiskRejected, "sharp_price_rise"),
        ):
            await FourStrategyTrader.check_entry_risk(
                runtime,
                self.token,
                {
                    "price_per_token": 1.4e-7,
                    "complete": False,
                    "creator": str(self.creator),
                },
            )

    async def test_runtime_timeout_fails_closed(self):
        async def slow(*_args: object) -> None:
            await asyncio.sleep(1)

        runtime = SimpleNamespace(
            four={"entry_protection": {**POLICY, "rpc_timeout_seconds": 0.001}},
            rpc=slow,
        )
        with self.assertRaisesRegex(EntryRiskRejected, "entry_risk_check_timeout"):
            await FourStrategyTrader.check_entry_risk(
                runtime,
                self.token,
                {
                    "price_per_token": 1e-7,
                    "complete": False,
                    "creator": str(self.creator),
                },
            )


if __name__ == "__main__":
    unittest.main()
