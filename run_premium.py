"""Lanza PREMIUM: solo compra a los 15 devs que no dumpean.

Comparte wallet con CORREDORES. Con 0,30 SOL por operacion hacen falta al
menos ~1,5 SOL para que las dos convivan sin quedarse sin gasolina.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-premium.yaml"


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
