"""Lanza DEV10, la unica estrategia en vivo.

Existia como `run_zero_holders_live2.py` con el yaml cambiado a mano en el
droplet: el nombre no decia lo que arrancaba y el parche se perdia en cada
despliegue. Este script es el mismo arranque, pero apuntando a DEV10 desde el
repositorio.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-dev10.yaml"


if __name__ == "__main__":
    notify(
        "🔴 <b>DEV10 ACTIVO</b>\n"
        "Dinero real · 0,30 SOL por compra\n"
        "Solo si el creador ha migrado el 10%+ de sus monedas (min. 3)\n"
        "Entrada entre $2K y $2,8K de MC, en los 3 primeros segundos\n"
        "Salida: 40% al +50% · 20% al +115% · 20% al +170% · 20% al +300%\n"
        "Slippage de compra 30% · 2 intentos por operacion"
    )
    asyncio.run(start_bot(CONFIG))
