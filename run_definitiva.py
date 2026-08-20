"""Lanza la estrategia DEFINITIVA como proceso independiente."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402

CONFIG = "bots/bot-sniper-definitiva.yaml"


if __name__ == "__main__":
    asyncio.run(start_bot(CONFIG))
