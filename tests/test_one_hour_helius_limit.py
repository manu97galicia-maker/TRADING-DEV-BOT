"""The live session deadline cancels the full trader task."""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_four_strategies as runner


class OneHourHeliusLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_cancels_trader(self) -> None:
        cancelled = asyncio.Event()

        async def trader(_path: str, *, only_dev: bool) -> None:
            self.assertTrue(only_dev)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with (patch.dict(sys.modules, {"bot_runner": SimpleNamespace(start_bot=trader)}),
              patch.object(runner, "MAX_RUN_SECONDS", 0.01)):
            await runner.run_with_helius_limit(Path("unused.yaml"), only_dev=True)
        self.assertTrue(cancelled.is_set())

    async def test_early_shutdown_returns_cleanly(self) -> None:
        called = []

        async def trader(path: str, *, only_dev: bool) -> None:
            called.append((path, only_dev))

        with patch.dict(sys.modules, {"bot_runner": SimpleNamespace(start_bot=trader)}):
            await runner.run_with_helius_limit(Path("unused.yaml"), only_dev=True)
        self.assertEqual(called, [("unused.yaml", True)])

    async def test_inner_timeout_is_not_reported_as_session_limit(self) -> None:
        async def trader(_path: str, *, only_dev: bool) -> None:
            raise TimeoutError("RPC timed out")

        with patch.dict(sys.modules, {"bot_runner": SimpleNamespace(start_bot=trader)}):
            with self.assertRaisesRegex(TimeoutError, "RPC timed out"):
                await runner.run_with_helius_limit(Path("unused.yaml"), only_dev=True)


if __name__ == "__main__":
    unittest.main()
