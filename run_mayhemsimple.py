"""Lanza MAYHEMSIMPLE: solo edad<5s y MC>=3,4K, sin filtro de creador."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-mayhemsimple.yaml"


if __name__ == "__main__":
    notify(
        "🔴 <b>MAYHEMSIMPLE ACTIVA</b>\n"
        "Dinero real · solo mayhem\n"
        "Sin filtro de creador: edad &lt;5s y MC ≥3.400 $, nada mas\n"
        "0,03 SOL — sin dato validado todavia\n"
        "Salida x2/x3/x6/x7/x10 · moonbag 3% · trinquete tras cada TP"
    )
    asyncio.run(start_bot(CONFIG))
