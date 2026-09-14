"""Compra a partir de las llamadas de un canal de Discord.

Gemelo de ``telegram_source``: cada contrato que aparece en los canales
vigilados se apunta en un log y, si es de la plataforma de este bot, se
reconstruye como TokenInfo y se manda al camino de compra, donde siguen
aplicando TODOS los filtros del YAML (holders, bundle, dev, market cap).

Que el filtro siga en medio no es un detalle: un canal de calls es la via mas
directa que existe para que alguien te meta un rug en la cartera. La senal
dice "compra esto", los filtros dicen si es comprable.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from interfaces.core import Platform, TokenInfo
from monitoring.discord_watcher import DiscordWatcher
from trading.token_builder import build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)

CALLS_LOG = Path("trades") / "discord_calls.log"


def _log_call(call: dict, bought: bool) -> None:
    """Apunta la llamada detectada, se compre o no."""
    try:
        CALLS_LOG.parent.mkdir(exist_ok=True)
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "address": call.get("address"),
            "platform": call.get("platform"),
            "chat": call.get("chat"),
            "text": call.get("text"),
            "bought_attempt": bought,
        }
        with CALLS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        logger.exception("No pude escribir el log de calls de Discord")


class DiscordSource:
    """Lleva las calls de Discord a los avisos y al camino de compra."""

    def __init__(
        self,
        config: dict,
        platform_impls: object,
        client: object,
        platform: Platform,
    ) -> None:
        self._config = config
        self._impls = platform_impls
        self._client = client
        self._platform = platform

    async def run(self, token_callback: Callable[[TokenInfo], Awaitable[None]]) -> None:
        """Escucha Discord y encamina las llamadas."""
        # El token sale del entorno, no del YAML: los YAML se commitean.
        token = self._config.get("token") or os.getenv("DISCORD_TOKEN", "")
        if not token:
            logger.error("Discord: pon DISCORD_TOKEN en el .env")
            return

        channels = self._config.get("channels") or []
        if not channels:
            logger.error("Discord: no hay 'channels' en la config del bot")
            return

        suffixes = tuple(self._config.get("suffixes") or ("pump", "bonk", "moon", "bags"))
        watcher = DiscordWatcher(
            token=str(token),
            channels=[str(c) for c in channels],
            suffixes=suffixes,
            poll_seconds=float(self._config.get("poll_seconds", 2.0)),
            is_bot_token=bool(self._config.get("is_bot_token", False)),
        )

        async def on_call(call: dict) -> None:
            # Solo se compra lo que sea de la plataforma de ESTE bot; el resto
            # queda como aviso en el log.
            buyable = call.get("platform") == self._platform.value
            token_info = None
            if buyable and self._platform == Platform.PUMP_FUN:
                token_info = await build_pumpfun_token_info(
                    call["address"], self._impls, self._client
                )
            _log_call(call, bought=token_info is not None)
            if token_info is not None:
                token_info.additional_data = {
                    **(token_info.additional_data or {}),
                    "acquisition_source": f"discord/{call.get('chat') or 'unknown'}",
                    "signal_buy_amount": float(
                        self._config.get("buy_amount", 0) or 0
                    ),
                }
                logger.info(
                    "Call de Discord -> a comprar %s (de %s)",
                    call["address"],
                    call.get("chat"),
                )
                await token_callback(token_info)

        await watcher.run(on_call)
