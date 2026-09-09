"""Fire-and-forget Telegram notifications for trade events.

Sends a message to a Telegram chat via a bot token. Everything is optional and
non-blocking: if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set, calls are a
silent no-op, and any network error is swallowed so trading is never affected.

Env vars:
  TELEGRAM_BOT_TOKEN  - bot token from @BotFather (e.g. 123456:ABC-DEF...)
  TELEGRAM_CHAT_ID    - numeric chat id of the recipient (get via getUpdates)
"""

from __future__ import annotations

import os
import threading
import urllib.parse
import urllib.request
from html import escape

from utils.logger import get_logger

logger = get_logger(__name__)

#: Tipos de aviso que SI se mandan. Vacio = se manda todo (comportamiento de
#: siempre). Manu pidio el 10-ago recibir solo las compras: el bot avisaba
#: tambien de senales, moonbags cada 5 minutos, cierres, bolsas huerfanas y
#: arranques, y eso convierte el canal en ruido que se acaba ignorando --
#: justo el que no hay que ignorar es el de "acabo de gastar tu dinero".
_TIPOS_PERMITIDOS: set[str] = set()
_EXPENSIVE_PROMOTION_THRESHOLD = 500


def purchase_reason_label(source_data: dict | None) -> str:
    """Return a readable acquisition reason from signal metadata."""
    data = source_data or {}
    source = str(data.get("acquisition_source") or "deteccion directa")
    source_key = source.lower().lstrip("@").replace(" ", "")
    boosts = float(data.get("dexscreener_boosts") or 0)
    nitro = float(data.get("dextools_nitro_score") or 0)
    reason = escape(source)
    if source_key == "cryptolistings":
        reason = "MoonTok listing"
    elif source_key == "legendsalphas":
        reason = "Legends Alpha signal"
    elif source_key == "dextnewpairsbot":
        reason = "DEXTools nuevo listing"
    elif source_key == "dextools_nitro":
        level = f" {nitro:g}" if nitro else ""
        expensive = " (caro)" if nitro >= _EXPENSIVE_PROMOTION_THRESHOLD else ""
        reason = f"DEXTools NITRO{level}{expensive}"
    elif source_key == "dexscreener":
        if boosts >= _EXPENSIVE_PROMOTION_THRESHOLD:
            reason = f"DexScreener boost {boosts:g} (caro)"
        elif boosts > 0:
            reason = f"DexScreener boost {boosts:g}"
        else:
            reason = "DexScreener listing/paid"
    return reason


def build_purchase_notification(  # noqa: PLR0913, PLR0917
    symbol: str,
    mint: str,
    amount_sol: float,
    source_data: dict | None,
    market_cap_usd: float,
    strategy: str,
    stop_loss_pct: float,
    extra_lines: list[str] | None = None,
    *,
    entry_price_sol: float = 0.0,
) -> str:
    """Build the Telegram message sent after a confirmed purchase."""
    safe_symbol = escape(symbol or "SIN SIMBOLO")
    safe_mint = escape(mint)
    lines = [
        f"🟢 <b>COMPRA CONFIRMADA</b> · {safe_symbol}",
        f"🔎 Motivo: <b>{purchase_reason_label(source_data)}</b>",
    ]
    lines.extend(extra_lines or [])
    entry = f"💵 Compra: <b>{amount_sol:.3f} SOL</b>"
    if market_cap_usd > 0:
        entry += f" · MC entrada ~${market_cap_usd:,.0f}"
    price_line = []
    if entry_price_sol > 0:
        price_line.append(
            f"🏷️ Precio entrada: <code>{entry_price_sol:.12f} SOL/token</code>"
        )
    lines.extend(
        [
            entry,
            *price_line,
            f"🎯 {strategy} · SL -{stop_loss_pct:.0f}%",
            f'<a href="https://dexscreener.com/solana/{safe_mint}">DexScreener</a> · '
            f'<a href="https://gmgn.ai/sol/token/{safe_mint}">GMGN</a>',
            f"CA: <code>{safe_mint}</code>",
        ]
    )
    return "\n".join(lines)


def build_paper_entry_notification(  # noqa: PLR0913, PLR0917
    symbol: str,
    mint: str,
    amount_sol: float,
    source_data: dict | None,
    entry_price_sol: float,
    market_cap_usd: float,
    curve_sol: float,
    strategy: str,
    stop_loss_pct: float,
) -> str:
    """Build a paper-entry alert without implying that real funds were spent."""
    safe_symbol = escape(symbol or "SIN SIMBOLO")
    safe_mint = escape(mint)
    lines = [
        f"🧪 <b>ENTRADA PAPER</b> · {safe_symbol}",
        f"🔎 Motivo: <b>{purchase_reason_label(source_data)}</b>",
        f"💵 Simulación: <b>{amount_sol:.3f} SOL</b>",
    ]
    if entry_price_sol > 0:
        lines.append(f"🏷️ Precio entrada: <code>{entry_price_sol:.12f} SOL/token</code>")
    if market_cap_usd > 0:
        lines.append(f"📈 MC entrada: ~${market_cap_usd:,.0f}")
    lines.extend(
        [
            f"💧 Curva: {curve_sol:.1f} SOL",
            f"🎯 {strategy} · SL -{stop_loss_pct:.0f}%",
            f'<a href="https://dexscreener.com/solana/{safe_mint}">DexScreener</a> · '
            f'<a href="https://gmgn.ai/sol/token/{safe_mint}">GMGN</a> · '
            f'<a href="https://pump.fun/{safe_mint}">pump.fun</a>',
            f"CA: <code>{safe_mint}</code>",
        ]
    )
    return "\n".join(lines)


def configurar_avisos(solo: list[str] | None) -> None:
    """Limita los avisos a los tipos indicados. None o lista vacia = todos."""
    global _TIPOS_PERMITIDOS
    _TIPOS_PERMITIDOS = set(solo or [])
    if _TIPOS_PERMITIDOS:
        logger.info("Telegram: solo se avisara de %s", sorted(_TIPOS_PERMITIDOS))


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


def notify(text: str, tipo: str = "otro") -> None:
    """Send a Telegram message without blocking the caller (best-effort).

    Args:
        text: el mensaje, en HTML de Telegram.
        tipo: para que el filtro de `configurar_avisos` pueda dejarlo pasar o
            no. Los que importan van marcados ("compra"); todo lo demas se
            queda con el valor por defecto y cae cuando hay filtro puesto.
    """
    if _TIPOS_PERMITIDOS and tipo not in _TIPOS_PERMITIDOS:
        return
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
