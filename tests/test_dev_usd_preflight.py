"""Exercise online USD readiness with fake HTTP, websocket and public identity."""

# Fake transport fixtures deliberately accept the shape of external clients.
# ruff: noqa: ANN001, ANN201

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from trading import four_strategy_preflight as preflight
from trading.buy_budget import inclusive_balance_entry

SOL = 1_000_000_000


class FakeResponse:
    def __init__(self, data) -> None:
        self.data = data

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return copy.deepcopy(self.data)


class FakeSession:
    def __init__(self, balance, price) -> None:
        self.balance, self.price = balance, price
        self.methods = []
        self.urls = []

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    def post(self, _url, *, json):
        method = json["method"]
        self.methods.append(method)
        results = {
            "getHealth": "ok",
            "getGenesisHash": preflight.MAINNET_GENESIS,
            "getBalance": {"value": self.balance, "context": {"slot": 123}},
        }
        return FakeResponse({"result": results[method]})

    def get(self, url, **_kwargs: object):
        self.urls.append(url)
        if url.startswith("https://api.dexscreener.com/"):
            return FakeResponse({"pairs": [
                {"baseToken": {"address": preflight.WSOL}, "priceUsd": self.price,
                 "liquidity": {"usd": 1_000_000}},
                {"baseToken": {"address": preflight.WSOL}, "priceUsd": .01,
                 "liquidity": {"usd": 1}},
            ]})
        return FakeResponse({"outAmount": "1000"})


class FakeWebsocket:
    def __init__(self) -> None:
        self.sent = []

    async def __aenter__(self) -> "FakeWebsocket":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def recv(self):
        return json.dumps({"result": 456})


class DevUsdOnlinePreflightTests(unittest.IsolatedAsyncioTestCase):
    async def inspect(self, *, balance=140_000_000, price=200, only_dev=True, stale=False):
        plan = {
            "trade": {"fraccion_saldo": .90, "buy_slippage": .15,
                      "min_reserve_sol": .03, "sin_volumen_bajo_entrada_seg": 0},
            "four_strategies": {"expected_wallet": "public-test-owner",
                                "entry_protection": {"enabled": False}},
        }
        second = {"enabled": True, "balance_fraction": .80,
                  "max_entry_budget_usd": 20.0, "buy_priority_fee_cap_usd": .07}
        local_report = {"checks": [], "online": False, "live_started": False,
                        "configured_public_wallet": "public-test-owner"}
        env = {"SOLANA_NODE_RPC_ENDPOINT": "https://rpc.invalid",
               "SOLANA_NODE_WSS_ENDPOINT": "wss://rpc.invalid"}
        session, websocket = FakeSession(balance, price), FakeWebsocket()
        clock = Mock(side_effect=[100, 131] if stale else None, return_value=100)
        with (
            patch.object(preflight, "inspect_local", return_value=local_report),
            patch.object(preflight, "_environment", return_value=env),
            patch.object(preflight, "time", SimpleNamespace(monotonic=clock)),
            patch("aiohttp.ClientSession", return_value=session),
            patch("websockets.connect", return_value=websocket),
            patch("trading.second_block_policy.load_second_policy", return_value=second),
            patch.object(preflight, "_telegram_check", new=AsyncMock()) as telegram,
        ):
            report = await preflight.inspect_online(plan, Path("unused.yaml"), only_dev=only_dev)
        self.assertFalse(report["live_started"])
        self.assertEqual(session.methods, ["getHealth", "getGenesisHash", "getBalance"])
        self.assertEqual([item["method"] for item in websocket.sent], ["slotSubscribe", "slotUnsubscribe"])
        self.assertTrue(set(session.methods) <= preflight.READ_METHODS)
        if only_dev:
            telegram.assert_not_awaited()
        else:
            telegram.assert_awaited_once()
        return report

    async def test_twenty_usd_cap_allows_reserve_that_uncapped_eighty_percent_would_breach(self):
        balance = 140_000_000
        _, uncapped = inclusive_balance_entry(balance, .80, .15, 3_350_000)
        self.assertGreater(uncapped, balance - 30_000_000)
        report = await self.inspect(balance=balance, price=200)
        self.assertTrue(report["ready_for_manual_start"])
        self.assertEqual(report["entry_budget_cap_usd"], 20)
        budget = round(report["prepared_entry_budget_sol"] * SOL)
        self.assertLessEqual(budget, 100_000_000)
        self.assertGreater(budget, 99_999_990)
        self.assertGreaterEqual(balance - budget, 30_000_000)
        native = next(item for item in report["checks"] if item["name"] == "native_reserve")
        self.assertEqual(native["status"], "pass")

    async def test_invalid_quote_cannot_produce_positive_usd_readiness(self):
        for price in (0, -1, float("nan"), float("inf"), "invalid"):
            with self.subTest(price=price):
                report = await self.inspect(price=price)
                self.assertFalse(report["ready_for_manual_start"])
                self.assertEqual(report["status"], "blocked")
                quote = next(item for item in report["checks"] if item["name"] == "sol_usd_reference")
                self.assertEqual(quote["status"], "fail")
                self.assertFalse(any(item["name"] == "native_reserve" and item["status"] == "pass"
                                     for item in report["checks"]))

    async def test_quote_older_than_thirty_seconds_blocks_native_cap_check(self):
        report = await self.inspect(stale=True)
        self.assertFalse(report["ready_for_manual_start"])
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any(item["name"] == "rpc_online" and item["status"] == "fail"
                            for item in report["checks"]))
        self.assertNotIn("prepared_entry_budget_sol", report)

    async def test_all_sources_keeps_shared_budget_without_applying_child_usd_cap(self):
        report = await self.inspect(balance=SOL, price=200, only_dev=False)
        self.assertTrue(report["ready_for_manual_start"])
        self.assertNotIn("entry_budget_cap_usd", report)
        self.assertGreater(report["prepared_entry_budget_sol"], .89)
        self.assertLessEqual(report["prepared_entry_budget_sol"], .90)


if __name__ == "__main__":
    unittest.main()
