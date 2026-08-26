"""Lanza DIP: compra el retroceso (pico 5-6K, ahora 1-1,4K), vende al x2."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-dip.yaml"


if __name__ == "__main__":
    notify(
        "🔴 <b>DIP ACTIVA</b>\n"
        "Dinero real · solo mayhem\n"
        "Sin filtro de creador: compra si tuvo pico 5-6K$ y ahora esta en 1-1,4K$\n"
        "0,01 SOL — sin dato validado todavia\n"
        "Vende el 100% al x2 desde la compra · sin stop · tope 10 min"
    )
    asyncio.run(start_bot(CONFIG))
