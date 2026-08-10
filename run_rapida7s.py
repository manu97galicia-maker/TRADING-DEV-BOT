"""Lanza RAPIDA-7S: compra la moneda que explota en sus primeros 7 segundos.

Comparte wallet con las demas estrategias. Es la mas agresiva de las tres: no
sabe quien lanza, solo ve la explosion.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-rapida7s.yaml"


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
