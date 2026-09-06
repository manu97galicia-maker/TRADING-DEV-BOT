"""Run the high-median Mayhem developer strategy in paper mode."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from bot_runner import start_bot
from monitoring.notifier import notify

CONFIG = "bots/bot-paper-mayhem-dev40k.yaml"


if __name__ == "__main__":
    notify(
        "🧪 <b>PAPER MAYHEM DEV40K ACTIVO</b>\n"
        "Simulación: no usa fondos reales\n"
        "0,03 SOL base · 0,04 SOL Mayhem · dev mediana ≥$40K y 5+ lanzamientos\n"
        "Cobro escalonado · stop ascendente · trailing 25%"
    )
    asyncio.run(start_bot(CONFIG))
