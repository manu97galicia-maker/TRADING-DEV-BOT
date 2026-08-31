"""Lanza RELAMPAGO-PAPEL: mide el criterio de 4K sin gastar un duro.

Mismo criterio que RELAMPAGO pero con `paper_trade: true`: apunta lo que
habria comprado y sigue su precio, sin firmar nada. Los datos salen en
paper_trades/entries.jsonl y paper_trades/results.jsonl.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from bot_runner import start_bot  # noqa: E402
from monitoring.notifier import notify  # noqa: E402

CONFIG = "bots/bot-sniper-relampago-papel.yaml"


if __name__ == "__main__":
    notify(
        "RELAMPAGO-PAPEL ACTIVO (NO gasta dinero)\n"
        "Mide cuantas compras de las 3 wallets siguen bajo $4.000 al llegar\n"
        "Resultados en paper_trades/entries.jsonl y results.jsonl"
    )
    asyncio.run(start_bot(CONFIG))
