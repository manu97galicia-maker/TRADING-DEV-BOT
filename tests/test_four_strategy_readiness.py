"""Readiness checks do not disclose credentials or start a trading runtime."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import base58
import yaml
from solders.keypair import Keypair

from trading.four_strategy_preflight import (
    MAINNET_GENESIS,
    READ_METHODS,
    _finish,
    inspect_local,
)
from trading.jupiter_access import jupiter_headers

ROOT = Path(__file__).resolve().parents[1]


class ReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = yaml.safe_load(
            (ROOT / "configs/four-strategies.yaml").read_text(encoding="utf-8")
        )
        self.path = ROOT / "configs/four-strategies.yaml"

    def test_bad_key_and_secret_endpoints_are_never_echoed(self) -> None:
        secret = "secret-do-not-echo"  # noqa: S105 -- deliberate dummy redaction fixture
        env = {
            "SOLANA_PRIVATE_KEY": secret,
            "SOLANA_NODE_RPC_ENDPOINT": "https://rpc.example/?api-key=" + secret,
            "SOLANA_NODE_WSS_ENDPOINT": "wss://rpc.example/?api-key=" + secret,
        }
        with patch("trading.four_strategy_preflight._environment", return_value=env):
            report = inspect_local(self.plan, self.path)
        self.assertNotIn(secret, json.dumps(report))
        self.assertFalse(report["ready_for_manual_start"])
        self.assertFalse(report["live_started"])
        self.assertEqual(report["status"], "blocked")

    def test_valid_key_for_another_wallet_is_a_hard_block(self) -> None:

        keypair = Keypair()
        env = {"SOLANA_PRIVATE_KEY": base58.b58encode(bytes(keypair)).decode()}
        with patch("trading.four_strategy_preflight._environment", return_value=env):
            report = inspect_local(self.plan, self.path)
        self.assertEqual(report["configured_public_wallet"], str(keypair.pubkey()))
        identity = next(c for c in report["checks"] if c["name"] == "wallet_identity")
        self.assertEqual(identity["status"], "fail")

    def test_local_checks_never_claim_that_network_access_was_verified(self) -> None:

        keypair = Keypair()
        self.plan["four_strategies"]["expected_wallet"] = str(keypair.pubkey())
        env = {
            "SOLANA_PRIVATE_KEY": base58.b58encode(bytes(keypair)).decode(),
            "SOLANA_NODE_RPC_ENDPOINT": "https://rpc.example",
            "SOLANA_NODE_WSS_ENDPOINT": "wss://rpc.example",
        }
        with patch("trading.four_strategy_preflight._environment", return_value=env):
            report = inspect_local(self.plan, self.path)
        self.assertEqual(report["status"], "offline_only")
        self.assertFalse(report["ready_for_manual_start"])

    def test_online_readiness_does_not_override_a_failed_check(self) -> None:
        report = {"online": True, "checks": [{"status": "fail"}]}
        self.assertFalse(_finish(report)["ready_for_manual_start"])

    def test_preflight_method_allowlist_cannot_sign_or_send(self) -> None:
        self.assertEqual(READ_METHODS, {
            "getHealth", "getGenesisHash", "getBalance", "getTokenLargestAccounts",
            "getMultipleAccounts", "getTokenAccountsByOwner",
        })

    def test_mainnet_identifier_is_a_complete_32_byte_hash(self) -> None:
        self.assertEqual(len(base58.b58decode(MAINNET_GENESIS)), 32)

    def test_jupiter_key_uses_an_authentication_header(self) -> None:
        with patch.dict("os.environ", {"JUPITER_API_KEY": "dummy-test-key"}):
            self.assertEqual(jupiter_headers(), {"x-api-key": "dummy-test-key"})
        with patch.dict("os.environ", {"JUPITER_API_KEY": ""}):
            self.assertEqual(jupiter_headers(), {})


if __name__ == "__main__":
    unittest.main()
