"""Run the explicitly budget-limited zero-holder live strategy."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402


if __name__ == "__main__":
    asyncio.run(start_bot("bots/bot-sniper-zero-holders-live2.yaml"))
