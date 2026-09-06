"""Run the measured MAYHEM40 strategy in paper-trading mode only."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from bot_runner import start_bot
from monitoring.notifier import notify

CONFIG = "bots/bot-paper-mayhem40.yaml"


if __name__ == "__main__":
    notify(
        "🧪 <b>PAPER MAYHEM40 ACTIVO</b>\n"
        "Simulación: no usa fondos reales\n"
        "0,025 SOL nocionales · vende todo al +40% · stop teórico -22%\n"
        "Solo mayhem · 12 compradores distintos · entrada MC $2.600-$3.400"
    )
    asyncio.run(start_bot(CONFIG))
