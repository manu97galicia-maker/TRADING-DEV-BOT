"""Lanza PORTAVIONES como estrategia unica."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-portaviones.yaml"


if __name__ == "__main__":
    notify(
        "PORTAVIONES ACTIVO\n"
        "Dinero real: maximo total 0,07 SOL por compra\n"
        "Copia 9 wallets; solo pump.fun normal con MC menor de $4.000\n"
        "Salida: 50% al +75% y 50% al +200%\n"
        "Una sola compra por mint; stop-loss y anti-rug activos"
    )
    asyncio.run(start_bot(CONFIG))
