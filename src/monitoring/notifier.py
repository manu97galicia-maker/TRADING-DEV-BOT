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
_MIN_LIQUID_CURVE_SOL = 7


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
    take_profit_tiers: list[tuple[float, float]] | None = None,
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
        price_line.append(
            f"🛡️ SL -{stop_loss_pct:.0f}%: "
            f"<code>{entry_price_sol * (1 - stop_loss_pct / 100):.12f}</code>"
        )
        for index, (gain, sell) in enumerate(take_profit_tiers or [], 1):
            price_line.append(
                f"🎯 TP{index} +{gain * 100:.0f}% (vende {sell * 100:.0f}%): "
                f"<code>{entry_price_sol * (1 + gain):.12f}</code>"
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
    *,
    take_profit_tiers: list[tuple[float, float]] | None = None,
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
        lines.append(
            f"🛡️ SL -{stop_loss_pct:.0f}%: "
            f"<code>{entry_price_sol * (1 - stop_loss_pct / 100):.12f}</code>"
        )
        for index, (gain, sell) in enumerate(take_profit_tiers or [], 1):
            lines.append(
                f"🎯 TP{index} +{gain * 100:.0f}% (vende {sell * 100:.0f}%): "
                f"<code>{entry_price_sol * (1 + gain):.12f}</code>"
            )
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


def build_paper_result_notification(
    record: dict,
    source_data: dict | None,
    stats: dict,
) -> str:
    """Build a complete paper exit report from the persisted result record."""
    safe_symbol = escape(str(record.get("symbol") or "SIN SIMBOLO"))
    safe_mint = escape(str(record.get("mint") or ""))
    exit_reason = str(record.get("exit_reason") or "open")
    if exit_reason == "stop":
        heading = "🛑 STOP PAPER"
    elif bool(record.get("strategy_closed")):
        heading = "✅ CIERRE TP PAPER"
    else:
        heading = "⏳ OBSERVACIÓN PAPER TERMINADA"

    lines = [
        f"{heading} · <b>{safe_symbol}</b>",
        f"🔎 Motivo de entrada: <b>{purchase_reason_label(source_data)}</b>",
        f"🏷️ Compra: <code>{float(record.get('entry_price_sol') or 0):.12f} "
        "SOL/token</code>",
    ]
    for tier in list(record.get("take_profit_tiers") or []):
        mark = "✅" if tier.get("hit") else "⬜"
        lines.append(
            f"{mark} {escape(str(tier.get('reason') or 'TP'))} "
            f"+{float(tier.get('gain_pct') or 0):.0f}% "
            f"(vende {float(tier.get('sell_pct') or 0):.0f}%): "
            f"<code>{float(tier.get('price_sol') or 0):.12f}</code>"
        )
    sales = list(record.get("sales") or [])
    if sales:
        for sale in sales:
            lines.append(
                f"💸 {escape(str(sale.get('reason') or 'VENTA'))}: "
                f"{float(sale.get('position_pct') or 0):.0f}% a "
                f"<code>{float(sale.get('price_sol') or 0):.12f}</code>"
            )
        lines.append(
            "💰 Precio medio vendido: "
            f"<code>{float(record.get('average_sale_price_sol') or 0):.12f} "
            "SOL/token</code>"
        )
    else:
        lines.append("💸 Venta: <b>sin ejecutar</b> durante la observación")

    curve_sol = float(record.get("curve_sol") or 0)
    liquidity_warning = (
        " · ⚠️ puede no ser vendible"
        if curve_sol < _MIN_LIQUID_CURVE_SOL
        else ""
    )
    lines.extend(
        [
            f"📍 Último precio: <code>{float(record.get('last_price_sol') or 0):.12f}</code>",
            f"🛡️ SL inicial: <code>{float(record.get('initial_stop_price_sol') or 0):.12f}</code>"
            f" · SL final: <code>{float(record.get('final_stop_price_sol') or 0):.12f}</code>",
            f"📦 Vendido {float(record.get('position_sold_pct') or 0):.0f}% · "
            f"restante {float(record.get('position_remaining_pct') or 0):.0f}%",
            f"📊 ROI <b>{float(record.get('roi_pct') or 0):+.1f}%</b> · "
            f"pico {float(record.get('peak_pct') or 0):+.1f}% · "
            f"mín {float(record.get('trough_pct') or 0):+.1f}%",
            f"💧 curva {curve_sol:.1f} SOL{liquidity_warning}",
            f"💵 {float(record.get('amount_sol') or 0):.3f} SOL → "
            f"<b>{float(record.get('value_sol') or 0):.4f} SOL</b> "
            f"({float(record.get('pnl_sol') or 0):+.4f})",
            "━━━━━━━━━━━━",
            f"📈 <b>WIN RATE: {float(stats.get('winrate') or 0):.1f}%</b> · "
            f"{int(stats.get('wins') or 0)}✅/{int(stats.get('losses') or 0)}❌ · "
            f"{int(stats.get('n') or 0)} trades",
            f"💼 P&L paper acumulado: <b>{float(stats.get('pnl') or 0):+.4f} SOL</b>",
            f'<a href="https://dexscreener.com/solana/{safe_mint}">DexScreener</a> · '
            f'<a href="https://gmgn.ai/sol/token/{safe_mint}">GMGN</a> · '
            f'<a href="https://pump.fun/{safe_mint}">pump.fun</a> · '
            f'<a href="https://t.me/MaestroSniperBot?start={safe_mint}">Maestro</a>',
            f"CA: <code>{safe_mint}</code>",
        ]
    )
    return "\n".join(lines)


def configurar_avisos(solo: list[str] | None) -> None:
    """Limita los avisos a los tipos indicados. None o lista vacia = todos."""
    global _TIPOS_PERMITIDOS  # noqa: PLW0603
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
        # The URL is a fixed Telegram HTTPS endpoint, never caller-controlled.
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310  # nosec B310
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
        import winsound  # noqa: PLC0415

        tune = (880, 1320, 1760) if action == "buy" else (660, 440)
        for freq in tune:
            winsound.Beep(freq, 160)
    except Exception:  # noqa: BLE001, S110 - alerts must never crash trading
        pass


def _reason_label(reason: str | None) -> str:  # noqa: PLR0911
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


def alert_trade(  # noqa: PLR0913
    action: str,
    symbol: str,
    sol: float,
    mint: str | None = None,
    reason: str | None = None,
    *,
    price_sol: float = 0.0,
) -> None:
    """Fire-and-forget trade alert: local beep + optional Telegram. Never raises.

    Includes a GMGN link (and Maestro) when the mint is provided, and a friendly
    reason label (TP/SL/max-hold/manual) for sells.
    """
    try:
        threading.Thread(target=_beep, args=(action,), daemon=True).start()
        emoji = "🟢 COMPRA" if action == "buy" else "🔴 VENTA"
        safe_symbol = escape(symbol or "SIN SIMBOLO")
        msg = f"{emoji}{_reason_label(reason)} · <b>{safe_symbol}</b> · {sol:.3f} SOL"
        if price_sol > 0:
            label = "compra" if action == "buy" else "venta"
            msg += f"\n🏷️ Precio {label}: <code>{price_sol:.12f} SOL/token</code>"
        if mint:
            safe_mint = escape(mint)
            msg += (
                f'\n📊 <a href="https://dexscreener.com/solana/{safe_mint}">DexScreener</a> · '
                f'<a href="https://gmgn.ai/sol/token/{safe_mint}">GMGN</a> · '
                f'🤖 <a href="https://t.me/MaestroSniperBot?start={safe_mint}">Maestro</a>\n'
                f"CA: <code>{safe_mint}</code>"
            )
        notify(msg)
    except Exception:  # noqa: BLE001, S110
        pass
