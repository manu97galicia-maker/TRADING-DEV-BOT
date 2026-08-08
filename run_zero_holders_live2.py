"""Run the explicitly budget-limited zero-holder live strategy."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402


if __name__ == "__main__":
    notify(
        "🔴 <b>ZERO-HOLDERS ACTIVO</b>\n"
        "Dinero real · sin límite de compras\n"
        "0.35 SOL/operación · suelo intocable 2 SOL\n"
        "Entrada &lt;$3K y ≤5s · mínimo 10 holders externos\n"
        "75% al +40% · 12.5% a MC $50K · 6.25% a $90K · hold 6.25%\n"
        "SL -38% · slippage 30%"
    )
    asyncio.run(start_bot("bots/bot-sniper-zero-holders-live2.yaml"))
