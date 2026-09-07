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
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import base58

from utils.logger import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

DEFAULT_SUFFIXES = ("pump", "bonk", "moon", "bags")

# Base58 alphabet (no 0, O, I, l). Solana addresses are ~32-44 chars.
_BASE58 = r"[1-9A-HJ-NP-Za-km-z]{32,44}"
_CA_RE = re.compile(_BASE58)
_SOLANA_PUBLIC_KEY_BYTES = 32


def is_solana_address(token: str) -> bool:
    """True if ``token`` decodes to a 32-byte Solana public key (Solana-only)."""
    try:
        return len(base58.b58decode(token)) == _SOLANA_PUBLIC_KEY_BYTES
    except Exception:  # noqa: BLE001 -- any decode failure -> not a Solana pubkey
        return False


def extract_contract_addresses(
    text: str,
    suffixes: tuple[str, ...] = DEFAULT_SUFFIXES,
    *,
    accept_all_solana: bool = False,
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
        has_known_suffix = any(token.endswith(sfx) for sfx in suffixes)
        if (accept_all_solana or has_known_suffix) and is_solana_address(token):
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

    def __init__(  # noqa: PLR0913 -- explicit connection/filter options
        self,
        api_id: int,
        api_hash: str,
        *,
        session_name: str = "tg_watcher",
        suffixes: tuple[str, ...] = DEFAULT_SUFFIXES,
        channels: list[str | int] | None = None,
        accept_all_solana: bool = False,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.suffixes = tuple(suffixes)
        self.channels = channels
        self.accept_all_solana = accept_all_solana
        self._seen: set[str] = set()

    async def run(self, on_call: Callable[[dict], Awaitable[None]]) -> None:
        """Connect and stream token calls to ``on_call``.

        ``on_call`` receives ``{address, platform, chat, text}`` per new,
        not-yet-seen contract address.
        """
        try:
            from telethon import TelegramClient, events  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(  # noqa: TRY003
                "Telethon not installed. Add 'telethon' and run `uv sync`."
            ) from exc

        client = TelegramClient(self.session_name, self.api_id, self.api_hash)

        @client.on(events.NewMessage(chats=self.channels))
        async def _handler(event: object) -> None:
            text = getattr(event, "raw_text", "") or ""
            for address in extract_contract_addresses(
                text,
                self.suffixes,
                accept_all_solana=self.accept_all_solana,
            ):
                if address in self._seen:
                    continue
                self._seen.add(address)
                chat = getattr(getattr(event, "chat", None), "title", None) or "?"
                logger.info("Telegram call: %s from %s", address, chat)
                await on_call(
                    {
                        "address": address,
                        "platform": platform_for_suffix(address),
                        "chat": chat,
                        "text": text[:280],
                        "message_date": (
                            getattr(event, "date", None) or datetime.now(timezone.utc)  # noqa: UP017 -- Python 3.10
                        ).isoformat(),
                    }
                )

        logger.info(
            "Telegram watcher connecting (suffixes=%s, channels=%s, all_solana=%s)",
            self.suffixes,
            self.channels or "all",
            self.accept_all_solana,
        )
        await client.start()
        await client.run_until_disconnected()
