"""Lanza VOLUMEN-2K, la segunda estrategia, en paralelo a DEV10.

Proceso aparte y servicio aparte a proposito: si una casca, la otra sigue
operando y vendiendo lo que tenga abierto.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-volumen2k.yaml"


if __name__ == "__main__":
    notify(
        "🟠 <b>VOLUMEN-2K ACTIVO</b>\n"
        "Dinero real · 0,15 SOL por compra\n"
        "Entra al llegar a ~2.000 $ de volumen (26,7 SOL)\n"
        "…pero solo si entran 3x más compras que ventas en 3 s\n"
        "No compra un nombre repetido en los últimos 10 min\n"
        "Salida: 67% al +50% (recupera el capital) · resto a trailing\n"
        "Stop-loss y trailing al 33%"
    )
    asyncio.run(start_bot(CONFIG))
