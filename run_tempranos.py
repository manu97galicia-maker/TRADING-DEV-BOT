"""Lanza TEMPRANOS: copia a las carteras que entran primero en las que se disparan.

La lista de carteras sale de analizar quien compro primero en las monedas que
llegaron a 12.000 $ de market cap en menos de dos minutos, y quedarse solo con
las que aparecen en VARIAS monedas distintas. El 1,6% de las monedas llega a
esa marca, asi que acertar una vez no distingue a nadie del azar.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-tempranos.yaml"


if __name__ == "__main__":
    notify(
        "🔵 <b>TEMPRANOS ACTIVO</b>\n"
        "Copia a las carteras que entran primero en las monedas que se disparan\n"
        "0,06 SOL por compra · solo por debajo de 3.400 $ de market cap\n"
        "Salida: 50% al +50% · 20% al +102,5% · 20% al +173,4% · 10% corriendo"
    )
    asyncio.run(start_bot(CONFIG))
