"""Fire-and-forget Telegram notifications for trade events.

Sends a message to a Telegram chat via a bot token. Everything is optional and
non-blocking: if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set, calls are a
silent no-op, and any network error is swallowed so trading is never affected.

Env vars:
  TELEGRAM_BOT_TOKEN  - bot token from @BotFather (e.g. 123456:ABC-DEF...)
  TELEGRAM_CHAT_ID    - numeric chat id of the recipient (get via getUpdates)
"""

from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request

from utils.logger import get_logger

logger = get_logger(__name__)


def _send_blocking(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            resp.read()
    except Exception as exc:  # noqa: BLE001 - notifications must never crash trading
        logger.debug(f"Telegram notify failed: {exc}")


def notify(text: str) -> None:
    """Send a Telegram message without blocking the caller (best-effort)."""
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        return
    threading.Thread(target=_send_blocking, args=(text,), daemon=True).start()


def _beep(action: str) -> None:
    """Audible local alert on Windows (rising tune for buy, low tone for sell)."""
    try:
        import winsound

        tune = (880, 1320, 1760) if action == "buy" else (660, 440)
        for freq in tune:
            winsound.Beep(freq, 160)
    except Exception:  # noqa: BLE001 - alerts must never crash trading
        pass


def _reason_label(reason: str | None) -> str:
    """Etiqueta legible del motivo de una venta (TP/SL/max-hold/manual/panic)."""
    if not reason:
        return ""
    r = reason.lower()
    if r.startswith("tp_tier"):
        gain = reason.split("_")[-1]  # p.ej. "+50%"
        return f" · ✅ TP {gain}"
    if r == "stop_loss":
        return " · 🛑 STOP LOSS"
    if r == "max_hold_time":
        return " · ⏱️ Max-hold (rojo)"
    if r.startswith("manual"):
        pct = r.replace("manual_", "").replace("pct", "")
        return f" · 👆 Manual {pct}%"
    if r == "panic_sell":
        return " · 🚨 Panic"
    return f" · {reason}"


def alert_trade(
    action: str,
    symbol: str,
    sol: float,
    mint: str | None = None,
    reason: str | None = None,
) -> None:
    """Fire-and-forget trade alert: local beep + optional Telegram. Never raises.

    Includes a GMGN link (and Maestro) when the mint is provided, and a friendly
    reason label (TP/SL/max-hold/manual) for sells.
    """
    try:
        threading.Thread(target=_beep, args=(action,), daemon=True).start()
        emoji = "🟢 COMPRA" if action == "buy" else "🔴 VENTA"
        msg = f"{emoji}{_reason_label(reason)} · <b>{symbol}</b> · {sol:.3f} SOL"
        if mint:
            msg += (
                f'\n📊 <a href="https://dexscreener.com/solana/{mint}">DexScreener</a> · '
                f'<a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
                f'🤖 <a href="https://t.me/MaestroSniperBot?start={mint}">Maestro</a>\n'
                f"CA: <code>{mint}</code>"
            )
        notify(msg)
    except Exception:  # noqa: BLE001
        pass
