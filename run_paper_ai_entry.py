"""Run the MoonTok + Dex Paid strategy in paper-trading mode."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from bot_runner import start_bot

CONFIG = "bots/bot-paper-ai-entry.yaml"


if __name__ == "__main__":
    asyncio.run(start_bot(CONFIG))
