"""Run the explicitly authorized two-buy real-money trial strategy."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402


if __name__ == "__main__":
    notify(
        "🔴 <b>ORGANIC-LIVE2 ACTIVO</b>\n"
        "Dinero real · máximo 2 compras totales\n"
        "0.25 SOL/operación · reserva 0.04 SOL · entrada &lt;$5.1K\n"
        "Dev ≥2 migradas y ≥20% · lista top ≥40% · ≥5 holders\n"
        "TP: 40% al +50%, 25% al +100%, 25% al +200%, resto a MC $29K\n"
        "SL -38% · slippage 30%"
    )
    asyncio.run(start_bot("bots/bot-sniper-organic-live2.yaml"))
