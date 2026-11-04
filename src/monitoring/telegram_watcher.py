"""Watch Telegram channels/groups for Solana token calls (contract addresses).

Detects base58 mint addresses posted in messages that end in a launchpad
suffix — ``pump`` (pump.fun), ``bonk`` (letsbonk), ``moon``, ``bags`` — and
hands each new address to a callback (alert and/or the buy pipeline).

The message scanning is pure and unit-testable (`extract_contract_addresses`).
The live watcher uses Telethon and needs YOUR Telegram API credentials
(api_id + api_hash from https://my.telegram.org) and a one-time phone login;
it only reads the channels/groups your account is already in.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import base58

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_SUFFIXES = ("pump", "bonk", "moon", "bags")

# Base58 alphabet (no 0, O, I, l). Solana addresses are ~32-44 chars.
_BASE58 = r"[1-9A-HJ-NP-Za-km-z]{32,44}"
_CA_RE = re.compile(_BASE58)


async def resolve_channels(
    resolver: Callable[[str | int], Awaitable[object]],
    channels: list[str | int] | None,
) -> tuple[list[object], list[str | int]]:
    """Resuelve los canales configurados y devuelve (resueltos, fallidos).

    Telethon revienta al arrancar si UNO de los nombres de ``chats=`` no se
    puede resolver -- porque el @usuario no existe, cambio, o la cuenta no
    esta dentro. Antes eso se llevaba por delante la fuente entera: anadir
    un canal nuevo mal escrito dejaba de escuchar tambien los que si
    funcionaban. Se resuelven de uno en uno para que un nombre malo cueste
    solo ese canal.
    """
    if not channels:
        return [], []
    resueltos: list[object] = []
    fallidos: list[str | int] = []
    for canal in channels:
        try:
            entidad = await resolver(canal)
        except Exception:  # noqa: BLE001 -- cualquier fallo = canal inservible
            logger.warning(
                "Canal de Telegram '%s' no se pudo resolver: se ignora. "
                "Comprueba el @usuario exacto y que tu cuenta este dentro.",
                canal,
            )
            fallidos.append(canal)
            continue
        if entidad is None:
            fallidos.append(canal)
            continue
        resueltos.append(entidad)
    return resueltos, fallidos


def is_solana_address(token: str) -> bool:
    """True if ``token`` decodes to a 32-byte Solana public key (Solana-only)."""
    try:
        return len(base58.b58decode(token)) == 32
    except Exception:  # noqa: BLE001 -- any decode failure -> not a Solana pubkey
        return False


def extract_contract_addresses(
    text: str, suffixes: tuple[str, ...] = DEFAULT_SUFFIXES
) -> list[str]:
    """Return unique base58 addresses in ``text`` ending in any suffix.

    Suffix match is case-sensitive because launchpad vanity suffixes are
    lowercase in the real mint (e.g. ``...pump``). Order preserved.

    Args:
        text: The message text to scan.
        suffixes: Accepted address endings.

    Returns:
        Unique contract addresses (first occurrence order).
    """
    if not text:
        return []
    seen: list[str] = []
    for token in _CA_RE.findall(text):
        if token in seen:
            continue
        # Must end in a launchpad suffix AND be a valid 32-byte Solana pubkey.
        if any(token.endswith(sfx) for sfx in suffixes) and is_solana_address(token):
            seen.append(token)
    return seen


def platform_for_suffix(address: str) -> str | None:
    """Best-effort launchpad label from a mint's suffix."""
    if address.endswith("pump"):
        return "pump_fun"
    if address.endswith("bonk"):
        return "lets_bonk"
    if address.endswith("moon"):
        return "moonshot"
    if address.endswith("bags"):
        return "bags"
    return None


class TelegramWatcher:
    """Live Telegram listener (Telethon) that emits new token-call addresses.

    Args:
        api_id / api_hash: Telegram API credentials (my.telegram.org).
        session_name: Telethon session file name (stores your login).
        suffixes: Address suffixes to accept.
        channels: Optional list of channel/group ids or usernames to restrict
            to; None = every chat your account receives.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str = "tg_watcher",
        suffixes: tuple[str, ...] = DEFAULT_SUFFIXES,
        channels: list[str | int] | None = None,
        non_interactive: bool = False,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.suffixes = tuple(suffixes)
        self.channels = channels
        self.non_interactive = non_interactive
        self._seen: set[str] = set()

    async def run(self, on_call: Callable[[dict], Awaitable[None]]) -> None:
        """Connect and stream token calls to ``on_call``.

        ``on_call`` receives ``{address, platform, chat, text}`` per new,
        not-yet-seen contract address.
        """
        try:
            from telethon import TelegramClient, events
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "Telethon not installed. Add 'telethon' and run `uv sync`."
            ) from exc

        client = TelegramClient(self.session_name, self.api_id, self.api_hash)

        async def _handler(event: object) -> None:
            text = getattr(event, "raw_text", "") or ""
            for address in extract_contract_addresses(text, self.suffixes):
                if address in self._seen:
                    continue
                self._seen.add(address)
                event_chat = getattr(event, "chat", None)
                chat = getattr(event_chat, "title", None) or "?"
                chat_username = getattr(event_chat, "username", None)
                logger.info("Telegram call: %s from %s", address, chat)
                await on_call(
                    {
                        "address": address,
                        "platform": platform_for_suffix(address),
                        "chat": chat,
                        "chat_username": chat_username,
                        "text": text[:280],
                        "message_date": getattr(
                            getattr(event, "message", None), "date", None
                        ),
                    }
                )

        logger.info(
            "Telegram watcher connecting (suffixes=%s, channels=%s)",
            self.suffixes,
            self.channels or "all",
        )
        try:
            if self.non_interactive:
                await client.connect()
                if not await client.is_user_authorized():
                    logger.warning("Telegram session is not authorized; keeping other sources active")
                    await client.disconnect()
                    return
            else:
                await client.start()
        except EOFError:
            logger.warning(
                "Telegram session is not authorized and no interactive input is "
                "available; other acquisition sources will keep running"
            )
            await client.disconnect()
            return

        resueltos, fallidos = await resolve_channels(
            client.get_entity, self.channels
        )
        if self.channels and not resueltos:
            # Sin esto caeria en chats=None, que NO es "no escuchar" sino
            # "escuchar TODOS los chats de la cuenta": compraria cualquier
            # direccion pegada en cualquier grupo. Mejor no escuchar nada.
            logger.error(
                "Ningun canal de Telegram se pudo resolver (%s): la fuente no "
                "escucha nada. No se cae a 'todos los chats' a proposito.",
                fallidos,
            )
            await client.disconnect()
            return
        if fallidos:
            logger.warning(
                "Escuchando %d de %d canales; ignorados: %s",
                len(resueltos),
                len(self.channels or []),
                fallidos,
            )
        client.add_event_handler(
            _handler, events.NewMessage(chats=resueltos or None)
        )
        try:
            await client.run_until_disconnected()
        finally:
            await client.disconnect()
