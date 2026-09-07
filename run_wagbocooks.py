"""Launch the isolated Wagbocooks Telegram strategy."""

from __future__ import annotations

import asyncio

from bot_runner import start_bot

if __name__ == "__main__":
    asyncio.run(start_bot("bots/bot-sniper-wagbocooks.yaml"))
