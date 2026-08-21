"""Lanza DEVMIG: la unica estrategia en vivo."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-devmig.yaml"


if __name__ == "__main__":
    notify(
        "🔴 <b>DEVMIG ACTIVA</b>\n"
        "Dinero real · solo pump.fun\n"
        "Creador con 5%+ migrado (min 2 migraciones)\n"
        "Arranque con 4 COMPRAS y mas de 400 $ de volumen\n"
        "0,10 SOL — y 0,20 si el creador migra el 20%+\n"
        "Entrada por debajo de 2.600 $ (2.800 si el dev es del 20%+)\n"
        "Salida 55% al +40% · 20% al +100% · 10% en x3 · 15% moonbag\n"
        "Sin stop · fuera si 2 min bajo la compra o 2 min sin volumen"
    )
    asyncio.run(start_bot(CONFIG))
