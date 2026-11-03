"""Publication evidence, duplicate suppression and RPC checks; no real posts."""

# Dynamic fake UI/RPC objects and fixed boundary values document the contract.
# ruff: noqa: ANN201

import copy
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from monitoring.callout_publisher import (
    CalloutPublisher,
    PositionVerifier,
    PublicationJournal,
    pending_entries,
)

OWNER = "HGUXwmjHmqY75q4j9v5wfGZnzFQESnq2wZ2UPJrkg3Uk"
MINT = "So11111111111111111111111111111111111111112"
SIGNATURE = "1" * 64
URL = f"https://pump.fun/coin/{MINT}?highlight_callout=abc-123"


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.journal = PublicationJournal(self.path / "publication.sqlite3")
        self.entry = {
            "publisher_account": "AImemebot",
            "mint": MINT,
            "buy_signature": SIGNATURE,
            "message": "Observed signal. Holds a position.",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.settings = {
            "callout_account": "AImemebot",
            "callout_publication": {"max_age_seconds": 120},
        }
        self.button = SimpleNamespace(click=AsyncMock())
        self.verifier = SimpleNamespace(held=AsyncMock(return_value=True))
        self.form = SimpleNamespace(
            prepare=AsyncMock(return_value=self.button),
            confirm=AsyncMock(return_value=URL),
            page=SimpleNamespace(
                get_by_role=lambda *_: SimpleNamespace(wait_for=AsyncMock())
            ),
        )
        self.publisher = CalloutPublisher(
            self.settings, self.journal, self.verifier, self.form
        )

    async def test_success_requires_visible_permalink_and_persists(self):
        self.assertEqual(await self.publisher.publish_one(self.entry), "published")
        self.assertEqual(self.journal.status(self.entry), "published")
        self.assertEqual(
            await self.publisher.publish_one(self.entry), "already_handled"
        )
        self.assertEqual(
            PublicationJournal(self.journal.path).status(self.entry), "published"
        )
        self.button.click.assert_awaited_once()
        self.assertEqual(self.verifier.held.await_count, 2)

    async def test_lost_ack_never_repeats_click_after_restart(self):
        self.button.click.side_effect = TimeoutError("unknown result")
        with self.assertRaises(TimeoutError):
            await self.publisher.publish_one(self.entry)
        self.assertEqual(self.journal.status(self.entry), "sending")
        restarted = CalloutPublisher(
            self.settings,
            PublicationJournal(self.journal.path),
            self.verifier,
            self.form,
        )
        self.assertEqual(await restarted.publish_one(self.entry), "already_handled")
        self.button.click.assert_awaited_once()

    async def test_no_visible_publication_is_not_marked_published(self):
        self.form.confirm.return_value = None
        self.assertEqual(
            await self.publisher.publish_one(self.entry), "submission_unverified"
        )
        self.assertEqual(self.journal.status(self.entry), "sending")
        self.assertEqual(
            await self.publisher.publish_one(self.entry), "already_handled"
        )

    async def test_expired_entries_never_reach_browser(self):
        self.entry["created_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=3)
        ).isoformat()
        self.assertEqual(await self.publisher.publish_one(self.entry), "expired")
        self.form.prepare.assert_not_awaited()

    async def test_other_account_is_rejected(self):
        self.entry["publisher_account"] = "another-account"
        self.assertEqual(
            await self.publisher.publish_one(self.entry), "different_account"
        )
        self.form.prepare.assert_not_awaited()

    async def test_no_holdings_or_unverified_purchase_never_opens_composer(self):
        self.verifier.held.return_value = False
        self.assertEqual(
            await self.publisher.publish_one(self.entry), "no_verified_position"
        )
        self.form.prepare.assert_not_awaited()

    async def test_position_closed_while_preparing_prevents_submission(self):
        self.verifier.held.side_effect = [True, False]
        self.assertEqual(
            await self.publisher.publish_one(self.entry), "position_or_signal_changed"
        )
        self.button.click.assert_not_awaited()
        self.assertIsNone(self.journal.status(self.entry))

    async def test_under_minimum_position_does_not_send_or_buy(self):
        self.form.prepare.return_value = None
        self.assertEqual(
            await self.publisher.publish_one(self.entry), "form_not_eligible"
        )
        self.button.click.assert_not_awaited()

    async def test_long_message_is_rejected_without_truncating_disclosure(self):
        self.entry["message"] = "x" * 2001
        self.assertEqual(
            await self.publisher.publish_one(self.entry), "message_too_long"
        )
        self.form.prepare.assert_not_awaited()

    async def test_concurrent_journals_only_claim_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(
                pool.map(
                    lambda _: PublicationJournal(self.journal.path).claim(self.entry),
                    range(8),
                )
            )
        self.assertEqual(sum(claims), 1)

    async def test_arbitrary_url_cannot_mark_published(self):
        self.journal.claim(self.entry)
        with self.assertRaises(ValueError):
            self.journal.published(
                self.entry, "https://other.example/coin/123?highlight_callout=abc"
            )
        self.assertEqual(self.journal.status(self.entry), "sending")

    async def test_incomplete_queue_records_are_ignored(self):
        path = self.path / "queue.jsonl"
        path.write_text(
            "null\n[]\ninvalid\n" + json.dumps(self.entry) + '\n{"mint":',
            encoding="utf-8",
        )
        self.assertEqual(pending_entries(path), [self.entry])

    async def test_receipt_and_current_holdings_are_both_required(self):
        ledger = self.path / "runtime.sqlite3"
        with closing(sqlite3.connect(ledger)) as db, db:
            db.execute("CREATE TABLE settings(id INTEGER,owner TEXT)")
            db.execute("INSERT INTO settings VALUES (1,?)", (OWNER,))
            db.execute("CREATE TABLE positions(nonce TEXT,mint TEXT,state TEXT)")
            db.execute("INSERT INTO positions VALUES ('n',?,'open')", (MINT,))
            db.execute("CREATE TABLE receipts(nonce TEXT,signature TEXT,kind TEXT)")
            db.execute("INSERT INTO receipts VALUES ('n',?,'buy')", (SIGNATURE,))
        tx = {
            "slot": 10,
            "transaction": {
                "message": {"accountKeys": [{"pubkey": OWNER, "signer": True}]}
            },
            "meta": {
                "err": None,
                "preTokenBalances": [],
                "postTokenBalances": [
                    {"mint": MINT, "owner": OWNER, "uiTokenAmount": {"amount": "10"}}
                ],
            },
        }
        holdings = {
            "context": {"slot": 10},
            "value": [
                {
                    "account": {
                        "data": {"parsed": {"info": {"tokenAmount": {"amount": "10"}}}}
                    }
                }
            ],
        }
        rpc = AsyncMock(side_effect=[tx, holdings])
        verifier = PositionVerifier(ledger, OWNER, rpc)
        self.assertTrue(await verifier.held(self.entry))
        self.assertEqual(
            [call.args[0] for call in rpc.await_args_list],
            ["getTransaction", "getTokenAccountsByOwner"],
        )
        for label in (
            "failed",
            "not_signer",
            "no_buy_delta",
            "zero_holdings",
            "stale_holdings",
        ):
            bad_tx, bad_holdings = copy.deepcopy(tx), copy.deepcopy(holdings)
            if label == "failed":
                bad_tx["meta"]["err"] = {"failure": True}
            elif label == "not_signer":
                bad_tx["transaction"]["message"]["accountKeys"][0]["signer"] = False
            elif label == "no_buy_delta":
                bad_tx["meta"]["preTokenBalances"] = bad_tx["meta"]["postTokenBalances"]
            elif label == "zero_holdings":
                bad_holdings["value"] = []
            else:
                bad_holdings["context"]["slot"] = 9
            with self.subTest(label=label):
                verifier.rpc = AsyncMock(side_effect=[bad_tx, bad_holdings])
                self.assertFalse(await verifier.held(self.entry))

    async def test_missing_ledger_is_not_created(self):
        path = self.path / "missing.sqlite3"
        rpc = AsyncMock()
        self.assertFalse(await PositionVerifier(path, OWNER, rpc).held(self.entry))
        self.assertFalse(path.exists())
        rpc.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
