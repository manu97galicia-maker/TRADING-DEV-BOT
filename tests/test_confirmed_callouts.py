"""Confirmed-buy queue deduplication and nonblocking price monitoring."""

import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from monitoring.confirmed_callouts import (
    queue_confirmed_callout,
    schedule_confirmed_callout,
)


class ConfirmedCalloutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.outbox = Path(self.directory.name) / "outbox.jsonl"

    def entries(self) -> list[dict]:
        return [json.loads(line) for line in self.outbox.read_text().splitlines()]

    async def test_real_signature_and_disclosure_are_stored_without_publishing(
        self,
    ) -> None:
        queue_confirmed_callout(
            "Coin", "mint", "sig", outbox=self.outbox,
            publisher_account="AImemebot",
        )
        entry = self.entries()[0]
        self.assertEqual(entry["buy_signature"], "sig")
        self.assertEqual(entry["publisher_account"], "AImemebot")
        self.assertIn("holds a position", entry["message"])
        self.assertIsNone(entry["published_at"])
        self.assertEqual(entry["status"], "pending_authenticated_publisher")

    async def test_same_purchase_is_not_queued_again_after_reload(self) -> None:
        first = queue_confirmed_callout("Coin", "mint", "sig", outbox=self.outbox)
        again = queue_confirmed_callout("Coin", "mint", "sig", outbox=self.outbox)
        self.assertEqual(first, again)
        self.assertEqual(len(self.entries()), 1)

    async def test_a_new_purchase_of_the_same_mint_is_a_new_event(self) -> None:
        queue_confirmed_callout("Coin", "mint", "first", outbox=self.outbox)
        queue_confirmed_callout("Coin", "mint", "second", outbox=self.outbox)
        self.assertEqual(
            {r["buy_signature"] for r in self.entries()}, {"first", "second"}
        )

    async def test_missing_purchase_signature_cannot_create_a_callout(self) -> None:
        with self.assertRaises(ValueError):
            queue_confirmed_callout("Coin", "mint", "", outbox=self.outbox)
        self.assertFalse(self.outbox.exists())

    async def test_intros_do_not_repeat_across_strategies_or_restarts(self) -> None:
        for i in range(80):
            queue_confirmed_callout(
                "Coin", f"mint-{i}", f"sig-{i}", outbox=self.outbox,
                strategy_name=f"strategy-{i % 4}",
            )
        entries = self.entries()
        self.assertEqual(len({entry["intro"] for entry in entries}), 80)
        self.assertEqual(len({entry["strategy_name"] for entry in entries}), 4)

    async def test_legacy_intro_and_malformed_records_are_handled(self) -> None:
        self.outbox.write_text(
            'null\n[]\nmalformed\n' + json.dumps({
                "mint": "old", "buy_signature": "old-sig",
                "message": "AI opportunity detected: Old",
            }) + "\n", encoding="utf-8",
        )
        result = queue_confirmed_callout("New", "new", "new-sig", outbox=self.outbox)
        self.assertFalse(result.startswith("AI opportunity detected:"))

    async def test_concurrent_writers_do_not_duplicate_confirmed_buys(self) -> None:
        def enqueue(i: int) -> str:
            return queue_confirmed_callout(
                "Coin", "mint", f"sig-{i % 3}", outbox=self.outbox
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(enqueue, range(12)))
        self.assertEqual(
            {r["buy_signature"] for r in self.entries()}, {"sig-0", "sig-1", "sig-2"}
        )
        self.assertEqual(len({r["intro"] for r in self.entries()}), 3)

    async def test_a_locked_outbox_does_not_block_price_monitoring(self) -> None:
        started = threading.Event()
        finish = threading.Event()

        def slow_write(*_args: object, **_kwargs: object) -> str:
            started.set()
            finish.wait(timeout=3)
            return "prepared"

        with patch("monitoring.confirmed_callouts.queue_confirmed_callout", slow_write):
            task = schedule_confirmed_callout("Coin", "mint", "sig", outbox=self.outbox)
            try:
                await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
                await asyncio.sleep(0)
                self.assertFalse(task.done())
            finally:
                finish.set()
            self.assertEqual(await task, "prepared")


if __name__ == "__main__":
    unittest.main()
