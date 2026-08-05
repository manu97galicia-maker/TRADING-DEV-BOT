"""Vigila canales de Discord y saca los contratos que se postean.

Gemelo de ``telegram_watcher`` para Discord. Sondea la API REST en vez de
abrir el gateway: son unas pocas llamadas por segundo, no hace falta ninguna
libreria extra (aiohttp ya esta), y sobrevive a un corte de red sin
reconexiones raras.

AVISO IMPORTANTE SOBRE LA CUENTA
--------------------------------
Para leer un servidor en el que solo eres un miembro mas hay que usar el token
de TU cuenta de usuario. Discord lo prohibe expresamente (los "self-bots" van
contra sus condiciones) y la sancion habitual es cerrarte la cuenta. No es un
riesgo legal, es un riesgo para tu cuenta, y lo asumes tu.

La via limpia, cuando se puede:
  * Si el canal es de tipo "Anuncios", puedes SEGUIRLO desde un servidor tuyo
    y poner ahi un bot propio con token de bot. Eso si cumple.
  * Si conoces al dueno del servidor, que invite a tu bot.

Con token de bot hace falta ademas el intent privilegiado de "Message Content";
sin el, los mensajes llegan con el texto vacio y no se puede extraer nada.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import aiohttp

from monitoring.telegram_watcher import extract_contract_addresses, platform_for_suffix
from utils.logger import get_logger

logger = get_logger(__name__)

API = "https://discord.com/api/v10"
#: Cada cuanto se pregunta por mensajes nuevos. Discord permite bastante mas,
#: pero 2s ya es mas rapido que cualquier humano leyendo el canal.
DEFAULT_POLL = 2.0
#: Cuantos mensajes se piden de golpe al arrancar y en cada sondeo.
BATCH = 50

# Codigos HTTP que hay que distinguir: cada uno significa algo distinto y
# tratarlos igual esconde el motivo real de que no llegue ninguna senal.
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_RATE_LIMITED = 429


class DiscordWatcher:
    """Sondea canales de Discord y avisa de cada contrato nuevo."""

    def __init__(
        self,
        token: str,
        channels: list[str],
        *,
        suffixes: tuple[str, ...] = ("pump", "bonk", "moon", "bags"),
        poll_seconds: float = DEFAULT_POLL,
        is_bot_token: bool = False,
    ) -> None:
        self._token = token.strip()
        self._channels = [str(c).strip() for c in channels if str(c).strip()]
        self._suffixes = suffixes
        self._poll = max(1.0, float(poll_seconds))
        self._is_bot = is_bot_token
        #: Ultimo mensaje visto por canal. Se siembra en el arranque con el
        #: mensaje actual para NO comprar el historial entero al encender.
        self._last: dict[str, str] = {}
        #: Direcciones ya vistas: el mismo contrato se repostea mucho.
        self._seen: set[str] = set()

    @property
    def _headers(self) -> dict[str, str]:
        value = f"Bot {self._token}" if self._is_bot else self._token
        return {"Authorization": value, "User-Agent": "Mozilla/5.0"}

    async def _fetch(self, session: aiohttp.ClientSession, channel: str) -> list[dict]:
        """Mensajes nuevos de un canal, del mas antiguo al mas nuevo."""
        params: dict[str, str | int] = {"limit": BATCH}
        last = self._last.get(channel)
        if last:
            params["after"] = last

        async with session.get(
            f"{API}/channels/{channel}/messages", params=params, headers=self._headers
        ) as response:
            if response.status == HTTP_RATE_LIMITED:
                # Rate limit: Discord dice cuanto esperar. Ignorarlo es como se
                # pasa de un aviso a un bloqueo temporal de la cuenta.
                body = await response.json(content_type=None)
                wait = float((body or {}).get("retry_after", 5))
                logger.warning("Discord rate limit; espero %.1fs", wait)
                await asyncio.sleep(wait)
                return []
            if response.status == HTTP_UNAUTHORIZED:
                raise PermissionError(
                    "Discord ha rechazado el token (401). Revisa DISCORD_TOKEN."
                )
            if response.status == HTTP_FORBIDDEN:
                raise PermissionError(
                    f"Sin acceso al canal {channel} (403). ¿Sigues en ese servidor?"
                )
            if response.status != HTTP_OK:
                logger.warning("Discord devolvio %s en el canal %s", response.status, channel)
                return []
            messages = await response.json(content_type=None)

        if not isinstance(messages, list) or not messages:
            return []
        # Discord los da del mas nuevo al mas viejo; se invierte para procesar
        # en el orden en que se escribieron.
        messages.reverse()
        self._last[channel] = str(messages[-1].get("id"))
        return messages

    @staticmethod
    def _text_of(message: dict) -> str:
        """Todo el texto util del mensaje, incluidos los embeds.

        Los canales de calls suelen postear con embed, no con texto plano: si
        solo se mira ``content``, la mitad de las senales se pierden.
        """
        parts = [str(message.get("content") or "")]
        for embed in message.get("embeds") or []:
            parts.append(str(embed.get("title") or ""))
            parts.append(str(embed.get("description") or ""))
            parts.append(str((embed.get("footer") or {}).get("text") or ""))
            parts.append(str((embed.get("author") or {}).get("name") or ""))
            for field in embed.get("fields") or []:
                parts.append(str(field.get("name") or ""))
                parts.append(str(field.get("value") or ""))
        return "\n".join(p for p in parts if p)

    async def _seed(self, session: aiohttp.ClientSession) -> None:
        """Marca lo que ya hay como visto, sin comprarlo."""
        for channel in self._channels:
            try:
                async with session.get(
                    f"{API}/channels/{channel}/messages",
                    params={"limit": 1},
                    headers=self._headers,
                ) as response:
                    if response.status != HTTP_OK:
                        logger.warning(
                            "No pude leer el canal %s al arrancar (%s)", channel, response.status
                        )
                        continue
                    messages = await response.json(content_type=None)
                if isinstance(messages, list) and messages:
                    self._last[channel] = str(messages[0].get("id"))
                    logger.info("Discord: vigilando el canal %s", channel)
            except aiohttp.ClientError as exc:
                logger.warning("Discord: fallo al arrancar el canal %s: %s", channel, exc)

    async def _process(
        self, message: dict, channel: str, on_call: Callable[[dict], Awaitable[None]]
    ) -> None:
        """Saca los contratos de un mensaje y avisa de los que no se han visto."""
        text = self._text_of(message)
        if not text:
            return
        for address in extract_contract_addresses(text, self._suffixes):
            if address in self._seen:
                continue
            self._seen.add(address)
            author = (message.get("author") or {}).get("username", "?")
            logger.info("Discord call: %s (de %s)", address, author)
            await on_call(
                {
                    "address": address,
                    "platform": platform_for_suffix(address),
                    "chat": f"discord/{channel}",
                    "text": text[:400],
                }
            )

    async def run(self, on_call: Callable[[dict], Awaitable[None]]) -> None:
        """Bucle infinito. ``on_call`` recibe {address, platform, chat, text}."""
        if not self._token:
            logger.error("Discord: falta DISCORD_TOKEN; no arranco")
            return
        if not self._channels:
            logger.error("Discord: no hay canales configurados; no arranco")
            return

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await self._seed(session)
            logger.info(
                "Discord: escuchando %d canal(es) cada %.0fs", len(self._channels), self._poll
            )
            while True:
                for channel in self._channels:
                    try:
                        messages = await self._fetch(session, channel)
                    except PermissionError:
                        raise
                    except (aiohttp.ClientError, TimeoutError) as exc:
                        logger.debug("Discord: sondeo fallido en %s: %s", channel, exc)
                        continue

                    for message in messages:
                        await self._process(message, channel, on_call)
                await asyncio.sleep(self._poll)
