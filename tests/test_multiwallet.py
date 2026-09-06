import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from server import multiwallet


class FakeKeypair:
    def __init__(self, public: str = "PublicWallet") -> None:
        self.public = public

    def pubkey(self) -> str:
        return self.public


class MultiWalletTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.state_patch = patch("server.multiwallet.config.STATE_DIR", self.state)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def write_wallets(self, count: int = 2) -> None:
        rows = [{"address": f"address-{index}", "secret": f"secret-{index}"} for index in range(count)]
        (self.state / "wallets.json").write_text(json.dumps(rows), encoding="utf-8")

    async def test_list_never_returns_private_keys(self) -> None:
        self.write_wallets()
        with (
            patch("server.multiwallet.wallet.keypair_from_secret", side_effect=FakeKeypair),
            patch("server.multiwallet.wallet.sol_balance", new=AsyncMock(return_value=0.25)),
        ):
            rows = await multiwallet.list_wallets()
        self.assertEqual(2, len(rows))
        self.assertTrue(all("secret" not in row for row in rows))
        self.assertEqual("address-0", rows[0]["address"])

    async def test_create_persists_keys_but_returns_only_public_data(self) -> None:
        with (
            patch("server.multiwallet.wallet._require_hot"),
            patch("server.multiwallet.wallet.sol_balance", new=AsyncMock(return_value=0.0)),
        ):
            response = await multiwallet.create_wallets(2)
        stored = json.loads((self.state / "wallets.json").read_text(encoding="utf-8"))
        self.assertEqual(2, len(stored))
        self.assertTrue(all(row.get("secret") for row in stored))
        self.assertTrue(all("secret" not in row for row in response))

    async def test_spread_enforces_total_limit(self) -> None:
        self.write_wallets()
        with (
            patch("server.multiwallet.wallet._require_hot"),
            patch("server.multiwallet.wallet.keypair_from_secret", side_effect=FakeKeypair),
            patch("server.multiwallet.config.MAX_SPREAD_SOL", 1.0),
            self.assertRaises(multiwallet.MultiWalletError),
        ):
            await multiwallet.spread(1.01, 2)

    async def test_spread_reports_partial_failures_without_secrets(self) -> None:
        self.write_wallets()
        successful = {"sol": 0.1, "signature": "ok", "to": "address-0"}
        send = AsyncMock(side_effect=[successful, multiwallet.wallet.WalletError("rpc caído")])
        with (
            patch("server.multiwallet.wallet._require_hot"),
            patch("server.multiwallet.wallet.keypair_from_secret", side_effect=FakeKeypair),
            patch("server.multiwallet.wallet.primary_keypair", return_value=FakeKeypair("main")),
            patch("server.multiwallet.wallet.sol_balance", new=AsyncMock(return_value=1.0)),
            patch("server.multiwallet.wallet.send_sol_with_keypair", new=send),
        ):
            result = await multiwallet.spread(0.2, 2)
        self.assertEqual(1, result["ok"])
        self.assertEqual(1, result["fallos"])
        self.assertNotIn("secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
