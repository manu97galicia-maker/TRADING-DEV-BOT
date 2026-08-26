"""Lanza DEVBUY: compra si el creador se juega SOL de verdad en su propia moneda."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-devbuy.yaml"


if __name__ == "__main__":
    notify(
        "🔴 <b>DEVBUY ACTIVA</b>\n"
        "Dinero real · solo pump.fun\n"
        "Creador se juega 2+ SOL en su propia creacion\n"
        "0,05 SOL — sin dato validado todavia\n"
        "Entrada por debajo de ~3.400 $\n"
        "Salida 20% al +35% · 40% al +82% · 40% al +146% · sin moonbag"
    )
    asyncio.run(start_bot(CONFIG))
