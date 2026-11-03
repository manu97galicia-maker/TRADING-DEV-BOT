"""Exit simulations use recording sellers and RPC fixtures only."""

# Private integration hooks and literal configured thresholds are intentional.
# ruff: noqa: SLF001

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from platforms.pumpfun.address_provider import PumpFunAddressProvider
from trading.four_strategy_inactivity import (
    fresh_inactivity,
    inactive_curve_evidence,
    quiet_since,
)
from trading.four_strategy_runtime import FourStrategyTrader


def history(stamp: int | None = 699, **changes: object) -> list[dict]:
    return [{"blockTime": stamp, "signature": "fixture", "slot": 50,
             "err": None, "confirmationStatus": "confirmed", **changes}]


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_history_requires_proven_quiet_time(self) -> None:
        self.assertEqual(quiet_since(history(700), 1000, 300), 700)
        cases = [None, [], {}, history(701), history(None), history(1001),
                 history(err={"error": "failed"}), history(confirmationStatus=None),
                 history(slot=0), history(signature="")]
        for rows in cases:
            with self.subTest(rows=rows):
                self.assertIsNone(quiet_since(rows, 1000, 300))

    async def test_only_underwater_unmigrated_curves_can_be_inactive(self) -> None:
        rpc = AsyncMock(return_value=history())
        manager = SimpleNamespace(get_pool_state=AsyncMock())
        for state, eligible in [
            ({"complete": False, "price_per_token": 0.9}, True),
            ({"complete": False, "price_per_token": 1.0}, False),
            ({"complete": False, "price_per_token": 1.1}, False),
            ({"complete": True, "price_per_token": 0.9}, False),
            ({"price_per_token": 0.9}, False),
            ({"complete": False, "price_per_token": float("nan")}, False),
        ]:
            manager.get_pool_state.return_value = state
            with patch("trading.four_strategy_inactivity.time.time", return_value=1000):
                evidence = await inactive_curve_evidence("curve", 1, 300, rpc, manager)
            self.assertEqual(evidence is not None, eligible)
        rpc.assert_awaited_with(
            "getSignaturesForAddress", ["curve", {"commitment": "confirmed", "limit": 1}]
        )

    def test_cached_quiet_evidence_expires_and_rejects_future_clock(self) -> None:
        evidence = {"checked_at": 1000, "last_activity": 699, "seconds": 300}
        self.assertTrue(fresh_inactivity(evidence, 1001, 300))
        self.assertFalse(fresh_inactivity(evidence, 1003, 300))
        self.assertFalse(fresh_inactivity(evidence, 999, 300))
        self.assertFalse(fresh_inactivity(evidence, 1001, 0))

    async def test_refresh_uses_actual_platform_address_and_discards_old_success_on_failure(self) -> None:
        mint = "9mFNn7N6SScpipcKzsTEbHQRB2yZtjbtXG1EsKKZpump"
        provider = PumpFunAddressProvider()
        runtime = SimpleNamespace(
            no_volume_exit_seconds=300, _inactive_exit_checked={},
            _inactive_exit_candidates={},
            platform_implementations=SimpleNamespace(
                address_provider=provider,
                curve_manager=SimpleNamespace(get_pool_state=AsyncMock(
                    return_value={"complete": False, "price_per_token": 0.9}
                )),
            ), rpc=AsyncMock(return_value=history()),
        )
        position = {"mint": mint, "created": 100,
                    "details": json.dumps({"entry_price": 1})}
        with patch("time.time", return_value=1000):
            await FourStrategyTrader.refresh_inactivity(runtime, position)
        self.assertIn(mint, runtime._inactive_exit_candidates)
        runtime.rpc.side_effect = TimeoutError
        with patch("time.time", return_value=1016):
            await FourStrategyTrader.refresh_inactivity(runtime, position)
        self.assertNotIn(mint, runtime._inactive_exit_candidates)

    async def test_monitor_sells_remaining_only_and_preserves_reason_for_retry(self) -> None:
        position = {"mint": "mint", "nonce": "nonce", "details": json.dumps({
            "entry_price": 1e-7, "entry_sol_usd": 100,
            "original_qty": 100, "executed": [0], "stopping": False,
        })}
        book = SimpleNamespace(positions=lambda: [position], signatures=lambda _nonce: [])
        runtime = SimpleNamespace(
            book=book, four={"exit": {
                "stop_loss_fraction": .25,
                "initial_tp_gain": .5, "last_target_market_cap_usd": 28000,
                "fallback_last_gain": 1, "proposed_original_quantity_fractions": [.5,.25,.25],
                "ladder_policy": "keep_first_tp_raise_later_targets_when_needed",
            }}, anti_rug_ratio=0, no_volume_exit_seconds=300,
            _inactive_exit_candidates={"mint": {"checked_at": time.time(),
                "last_activity": time.time()-301, "seconds": 300}},
            market_price=AsyncMock(return_value=9e-8),
            raw_holdings=AsyncMock(return_value=(50_000_000, 100)),
            _sell_amount=AsyncMock(side_effect=asyncio.CancelledError),
        )

        def update(_nonce: str, changes: dict) -> None:
            position["details"] = json.dumps({**json.loads(position["details"]), **changes})

        runtime.update_details = update
        with self.assertRaises(asyncio.CancelledError):
            await FourStrategyTrader.monitor_position(runtime, SimpleNamespace(mint="mint"))
        runtime._sell_amount.assert_awaited_once_with(
            unittest.mock.ANY, 50, 9e-8, "muerta_sin_volumen", full_exit=True
        )
        self.assertEqual(json.loads(position["details"])["stopping_reason"], "muerta_sin_volumen")


if __name__ == "__main__":
    unittest.main()
