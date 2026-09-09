"""Check a Telethon session without prompting for login or starting the bot."""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient


async def main() -> None:
    """Print whether the configured read-only Telegram session is authorized."""
    load_dotenv()
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        print("Telegram API credentials are not configured")
        return
    client = TelegramClient("moontok_watcher", int(api_id), api_hash)
    await client.connect()
    try:
        print(f"Telegram session authorized: {await client.is_user_authorized()}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
