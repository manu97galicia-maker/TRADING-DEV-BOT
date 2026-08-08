"""Run only the safe developer-history paper strategy."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402


if __name__ == "__main__":
    asyncio.run(start_bot("bots/bot-paper-dev-history.yaml"))
