"""Lanza RELAMPAGO como estrategia unica.

Un YAML en bots/ no arranca solo: cada estrategia viva en el droplet tiene su
lanzador y su unidad de systemd (ver relampago.service). Sin este fichero, el
config estaba puesto y no lo ejecutaba nadie.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-relampago.yaml"


if __name__ == "__main__":
    notify(
        "RELAMPAGO ACTIVO\n"
        "Dinero real: 0,025 SOL por compra (tope total 0,0325)\n"
        "Copia 3 wallets (J4iit8, 9YzsAD, vv4FfL) si la moneda sigue bajo $4.000\n"        "Maximo DOS operaciones abiertas a la vez\n"
        "Salida: 70% al +80%, 25% al +250%, moonbag del 5%\n"
        "Trailing 25%, stop 35%, cierre a los 20 s si no se mueve"
    )
    asyncio.run(start_bot(CONFIG))
