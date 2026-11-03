"""Manual-sale reconciliation fixtures never contact a node or start trading."""

# Literal balances and private book paths are intentional audit regression inputs.

import copy
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from trading.four_strategy_book import FourStrategyBook
from trading.manual_sale_audit import IncompleteManualAuditError, audit_manual_sale


def transaction(signature: str, pre: int, post: int, native_before: int, native_after: int, slot: int) -> dict:  # noqa: PLR0913, PLR0917 -- explicit chain receipt fixture
    def token(amount: int) -> dict:
        return {"accountIndex": 1, "owner": "owner", "mint": "mint",
                "uiTokenAmount": {"amount": str(amount)}}
    return {"slot": slot, "transaction": {"signatures": [signature], "message": {
        "accountKeys": [{"pubkey": "owner", "signer": True}, {"pubkey": "ata"}]}},
        "meta": {"err": None, "preBalances": [native_before, 0],
                 "postBalances": [native_after, 0],
                 "preTokenBalances": [token(pre)], "postTokenBalances": [token(post)]}}


class ManualAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transactions = {
            "buy": transaction("buy", 0, 100, 1_000_000, 900_000, 100),
            "manual-sell": transaction("manual-sell", 100, 0, 900_000, 1_020_000, 101),
        }
        self.history = [{"signature": signature, "slot": slot, "err": None,
                         "confirmationStatus": "finalized"}
                        for signature, slot in [("manual-sell", 101), ("buy", 100)]]
        self.calls = []

    def rpc(self, method: str, params: list) -> object:
        self.calls.append((method, params))
        if method == "getTokenAccountsByOwner":
            return {"context": {"slot": 200}, "value": []}
        if method == "getTransaction":
            return copy.deepcopy(self.transactions.get(params[0]))
        if method == "getSignaturesForAddress":
            self.assertEqual(params[0], "ata")
            return self.history
        self.fail("Unexpected RPC method")

    def audit(self) -> dict:
        return audit_manual_sale({"mint": "mint", "nonce": "n"}, [("buy", "buy")], "owner", self.rpc)

    def test_finalized_manual_sale_has_exact_net_and_supports_current_receipt_version(self) -> None:
        result = self.audit()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["net_lamports"], 20_000)
        self.assertEqual(result["buy_debit_lamports"], 100_000)
        self.assertTrue(result["buy_debit_verified"])
        self.assertEqual(result["additional_receipts"], [("manual-sell", "sell")])
        self.assertTrue(all(p[1]["maxSupportedTransactionVersion"] == 1
                            for method, p in self.calls if method == "getTransaction"))

    def test_missing_receipt_or_incomplete_history_cannot_free_a_position(self) -> None:
        self.transactions["manual-sell"] = None
        with self.assertRaises(IncompleteManualAuditError):
            self.audit()
        self.history = self.history[:1]
        with self.assertRaises(IncompleteManualAuditError):
            self.audit()

    def test_plain_token_transfer_and_multi_asset_bundles_are_not_valued_as_sales(self) -> None:
        sell = self.transactions["manual-sell"]
        sell["meta"]["postBalances"][0] = 899_999
        with self.assertRaises(IncompleteManualAuditError):
            self.audit()
        sell["meta"]["postBalances"][0] = 1_020_000
        sell["meta"]["postTokenBalances"].append({"owner": "owner", "mint": "other",
            "uiTokenAmount": {"amount": "2"}})
        with self.assertRaises(IncompleteManualAuditError):
            self.audit()

    def test_extra_acquisition_or_token_mismatch_prevents_false_pnl(self) -> None:
        self.transactions["extra-buy"] = transaction("extra-buy", 100, 150, 900_000, 850_000, 101)
        self.history.insert(1, {"signature": "extra-buy", "slot": 101, "err": None,
                                "confirmationStatus": "finalized"})
        with self.assertRaises(IncompleteManualAuditError):
            self.audit()
        self.history.pop(1)
        self.transactions["manual-sell"]["meta"]["preTokenBalances"][0]["uiTokenAmount"]["amount"] = "99"
        with self.assertRaises(IncompleteManualAuditError):
            self.audit()

    def test_slot_disagreement_and_unfinalized_receipt_are_rejected(self) -> None:
        self.history[0]["slot"] = 102
        with self.assertRaises(IncompleteManualAuditError):
            self.audit()
        self.history[0]["slot"] = 101
        self.history[0]["confirmationStatus"] = "confirmed"
        with self.assertRaises(IncompleteManualAuditError):
            self.audit()


class AtomicManualClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.book = FourStrategyBook(Path(self.directory.name) / "ledger.sqlite3", "owner", "policy")
        self.position = self.book.reserve("mint", "strategy", 1_000_000_000, 100, 0, .15, 0, {})
        self.nonce = self.position["nonce"]
        self.book.submitting(self.nonce)
        self.book.receipt(self.nonce, "buy", "buy")
        self.book.opened(self.nonce, 100, {"original_qty": 100, "entry_price": .1})

    def evidence(self) -> dict:
        return {"verified_zero_holdings": True, "previous_signatures": ["buy"],
                "signatures": ["buy", "manual-sell"], "manual_reconciliation": True}

    def test_manual_receipt_and_outcome_commit_together(self) -> None:
        self.book.settle(self.nonce, -20_000, 200, self.evidence(),
                         additional_receipts=[("manual-sell", "sell")])
        self.assertFalse(self.book.positions())
        self.assertEqual(self.book.research()["strategies"]["strategy"]["net_lamports"], -20_000)

    def test_invalid_settlement_rolls_back_the_new_receipt(self) -> None:
        evidence = self.evidence()
        evidence["signatures"] = ["buy", "different"]
        with self.assertRaises(ValueError):
            self.book.settle(self.nonce, 0, 200, evidence,
                             additional_receipts=[("manual-sell", "sell")])
        self.assertEqual(self.book.signatures(self.nonce), [("buy", "buy")])
        self.assertEqual(len(self.book.positions()), 1)

    def test_conflicting_receipt_does_not_steal_another_position(self) -> None:
        other = self.book.reserve("other", "strategy", 1_000_000_000, 100, 0, .15, 0, {})
        self.book.receipt(other["nonce"], "manual-sell", "sell")
        with self.assertRaises(sqlite3.IntegrityError):
            self.book.settle(self.nonce, 0, 200, self.evidence(),
                             additional_receipts=[("manual-sell", "sell")])
        self.assertEqual(self.book.signatures(self.nonce), [("buy", "buy")])


class ApplyLockTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "The droplet uses Linux flock")
    def test_apply_refuses_while_the_trading_process_lock_is_held(self) -> None:
        import fcntl  # noqa: PLC0415 -- Linux-only integration fixture

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "configs").mkdir()
            (root / "configs/four-strategies.yaml").write_text(
                'four_strategies:\n  database: ledger.sqlite3\n  expected_wallet: owner\n'
            )
            with (root / "ledger.process.lock").open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                code = (
                    "from pathlib import Path; import sys; "
                    "import reconcile_four_manual_sales as cli; "
                    "cli.ROOT=Path(sys.argv[1]); sys.argv=['fixture','--apply']; cli.main()"
                )
                result = subprocess.run(  # noqa: S603 -- fixed fixture program, no shell or user input
                    [sys.executable, "-c", code, str(root)],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True, text=True, check=False, timeout=5,
                )
            self.assertEqual(result.returncode, 2)
            self.assertIn("BlockingIOError", result.stderr)
            self.assertFalse((root / "ledger.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
