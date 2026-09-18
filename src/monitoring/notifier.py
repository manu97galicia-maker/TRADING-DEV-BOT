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
#: Silencio total. Lo pone `configurar_avisos(False)` y lo miran LAS DOS
#: puertas de salida: `notify` y `notify_ahora`. La segunda se salta el
#: filtro por tipo a proposito (avisos de caida), asi que sin esta bandera
#: seguiria escribiendo con todo lo demas apagado.
_SILENCIO = False
_EXPENSIVE_PROMOTION_THRESHOLD = 500
_MIN_LIQUID_CURVE_SOL = 7


def token_label(name: str | None, symbol: str | None) -> str:
    """Return ``Nombre ($TICKER)``, or just one of them when the other is
    missing or is a copy of it.

    The dex feeds often carry only a symbol, and ``build_pumpfun_token_info``
    falls back to the symbol (or the mint) for the name, so the two are equal
    more often than not. Printing both in that case is noise.
    """
    safe_name = escape((name or "").strip())
    safe_symbol = escape((symbol or "").strip())
    if safe_symbol and safe_name and safe_name.lower() != safe_symbol.lower():
        return f"{safe_name} (${safe_symbol})"
    if safe_symbol:
        return f"${safe_symbol}"
    return safe_name or "SIN SIMBOLO"


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


def build_purchase_notification(  # noqa: PLR0913
    symbol: str,
    mint: str,
    amount_sol: float,
    source_data: dict | None,
    market_cap_usd: float,
    strategy: str,
    stop_loss_pct: float,
    extra_lines: list[str] | None = None,
    *,
    name: str | None = None,
    entry_price_sol: float = 0.0,
    take_profit_tiers: list[tuple[float, float]] | None = None,
) -> str:
    """Build the Telegram message sent after a confirmed purchase."""
    safe_mint = escape(mint)
    lines = [
        f"🟢 <b>COMPRA CONFIRMADA</b> · {token_label(name, symbol)}",
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


def build_paper_entry_notification(  # noqa: PLR0913
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
    name: str | None = None,
    take_profit_tiers: list[tuple[float, float]] | None = None,
) -> str:
    """Build a paper-entry alert without implying that real funds were spent."""
    safe_mint = escape(mint)
    lines = [
        f"🧪 <b>ENTRADA PAPER</b> · {token_label(name, symbol)}",
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
    label = token_label(str(record.get("name") or ""), str(record.get("symbol") or ""))
    safe_mint = escape(str(record.get("mint") or ""))
    exit_reason = str(record.get("exit_reason") or "open")
    if exit_reason == "stop":
        heading = "🛑 STOP PAPER"
    elif bool(record.get("strategy_closed")):
        heading = "✅ CIERRE TP PAPER"
    else:
        heading = "⏳ OBSERVACIÓN PAPER TERMINADA"

    lines = [
        f"{heading} · <b>{label}</b>",
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
        " · ⚠️ puede no ser vendible" if curve_sol < _MIN_LIQUID_CURVE_SOL else ""
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


def build_ladder_event_notification(  # noqa: PLR0913
    event: str,
    name: str | None,
    symbol: str | None,
    mint: str,
    source_data: dict | None,
    price_sol: float,
    entry_price_sol: float,
    amount_sol: float,
    sold_pct: float,
    remaining_pct: float,
    total_roi: float,
    *,
    paper: bool = True,
    new_stop_price_sol: float | None = None,
) -> str:
    """Build the alert sent the moment a take-profit or the stop triggers.

    Args:
        event: ``TP1``..``TPn`` or ``STOP``.
        name: Token name, when the feed carried one.
        symbol: Token ticker.
        mint: Token mint (contract address).
        source_data: Signal metadata, for the buy reason.
        price_sol: Price the rung triggered at.
        entry_price_sol: Entry price, to show the move from entry.
        amount_sol: Size of the original position, to turn ROI into SOL.
        sold_pct: Percent of the original position this rung sells.
        remaining_pct: Percent still held after it.
        total_roi: Running ROI of the whole position, as a fraction.
        paper: Whether this is a simulation.
        new_stop_price_sol: Where the stop moves to after a take-profit.

    Returns:
        The Telegram HTML message.
    """
    safe_mint = escape(mint)
    is_stop = event.upper() == "STOP"
    tag = "PAPER" if paper else "REAL"
    heading = f"🛑 <b>STOP {tag}</b>" if is_stop else f"🎯 <b>{escape(event)} {tag}</b>"
    move = (price_sol / entry_price_sol - 1) * 100 if entry_price_sol > 0 else 0.0
    pnl_sol = amount_sol * total_roi
    lines = [
        f"{heading} · {token_label(name, symbol)}",
        f"🔎 Motivo de compra: <b>{purchase_reason_label(source_data)}</b>",
        f"🏷️ Precio: <code>{price_sol:.12f} SOL/token</code> ({move:+.1f}%)",
    ]
    if is_stop:
        lines.append(f"📦 Cierra el {remaining_pct:.0f}% que quedaba")
    else:
        lines.append(f"📦 Vende {sold_pct:.0f}% · queda {remaining_pct:.0f}%")
    lines.append(f"💰 P&L: <b>{pnl_sol:+.4f} SOL ({total_roi * 100:+.1f}%)</b>")
    if not is_stop and new_stop_price_sol:
        lines.append(f"🛡️ SL protegido sube a: <code>{new_stop_price_sol:.12f}</code>")
    lines.extend(
        [
            f'<a href="https://dexscreener.com/solana/{safe_mint}">DexScreener</a> · '
            f'<a href="https://gmgn.ai/sol/token/{safe_mint}">GMGN</a> · '
            f'<a href="https://pump.fun/{safe_mint}">pump.fun</a>',
            f"CA: <code>{safe_mint}</code>",
        ]
    )
    return "\n".join(lines)


def configurar_avisos(solo: list[str] | bool | None) -> None:  # noqa: FBT001
    # FBT001 pide que un booleano vaya por palabra clave. Aqui no es una
    # bandera: es UNO de los tres valores que puede traer la misma clave del
    # yaml (lista / false / nada), y partirlo en dos parametros obligaria a
    # quien lee la config a decidir cual usar antes de mirarla.
    """Que avisos de Telegram se mandan.

    Tres formas de llamarla, y la de en medio es nueva:

    * ``None`` (la clave no esta en el yaml) -> se manda TODO, como siempre.
    * ``False`` -> NO se manda NADA. Silencio total, avisos de caida incluidos.
    * una lista de tipos -> solo esos.

    OJO CON LA LISTA VACIA: `[]` significa "todos", no "ninguno". Es
    contraintuitivo y por eso hizo falta el `False`: `set([]) ` es falsy y el
    filtro de abajo lo trata como "sin filtro". Quien quiera silencio tiene que
    decirlo con `false`, no con una lista vacia.
    """
    global _TIPOS_PERMITIDOS, _SILENCIO  # noqa: PLW0603
    _SILENCIO = solo is False
    if _SILENCIO:
        _TIPOS_PERMITIDOS = set()
        logger.info("Telegram: SILENCIADO (avisos_telegram: false)")
        return
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


def notify_ahora(text: str) -> bool:
    """Manda el aviso y ESPERA a que salga. Devuelve si se pudo mandar.

    `notify` lanza un hilo demonio y vuelve enseguida, que es lo que quiere
    el bucle de compra. Un script de una sola pasada no puede usar eso: el
    proceso termina antes de que el hilo llegue a enviar nada y el aviso se
    pierde justo cuando mas falta hace -- avisar de que el bot se ha caido.

    Tampoco filtra por tipo: si algo llama a esto es porque hay que contarlo.
    """
    if _SILENCIO:
        return False
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        return False
    _send_blocking(text)
    return True


def notify(text: str, tipo: str = "otro") -> None:
    """Send a Telegram message without blocking the caller (best-effort).

    Args:
        text: el mensaje, en HTML de Telegram.
        tipo: para que el filtro de `configurar_avisos` pueda dejarlo pasar o
            no. Los que importan van marcados ("compra"); todo lo demas se
            queda con el valor por defecto y cae cuando hay filtro puesto.
    """
    if _SILENCIO:
        return
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


def build_trade_alert(  # noqa: PLR0913
    action: str,
    symbol: str,
    sol: float,
    mint: str | None = None,
    reason: str | None = None,
    *,
    price_sol: float = 0.0,
    name: str | None = None,
    source_data: dict | None = None,
    entry_price_sol: float = 0.0,
) -> str:
    """Build the real-money buy/sell alert. See :func:`alert_trade` for args."""
    emoji = "🟢 COMPRA" if action == "buy" else "🔴 VENTA"
    msg = (
        f"{emoji}{_reason_label(reason)} · "
        f"<b>{token_label(name, symbol)}</b> · {sol:.3f} SOL"
    )
    if source_data:
        msg += f"\n🔎 Motivo de compra: <b>{purchase_reason_label(source_data)}</b>"
    if price_sol > 0:
        etiqueta = "compra" if action == "buy" else "venta"
        msg += f"\n🏷️ Precio {etiqueta}: <code>{price_sol:.12f} SOL/token</code>"
        if action == "sell" and entry_price_sol > 0:
            move = (price_sol / entry_price_sol - 1) * 100
            # `sol` es el valor de ESTA pata, asi que los tokens salen de
            # dividirlo por su precio: con eso el P&L es el de la venta real y
            # no una estimacion sobre la posicion entera.
            pnl = (sol / price_sol) * (price_sol - entry_price_sol)
            msg += f" ({move:+.1f}%)"
            msg += f"\n💰 P&L de esta venta: <b>{pnl:+.4f} SOL</b>"
    if mint:
        safe_mint = escape(mint)
        msg += (
            f'\n📊 <a href="https://dexscreener.com/solana/{safe_mint}">'
            "DexScreener</a> · "
            f'<a href="https://gmgn.ai/sol/token/{safe_mint}">GMGN</a> · '
            f'🤖 <a href="https://t.me/MaestroSniperBot?start={safe_mint}">'
            "Maestro</a>\n"
            f"CA: <code>{safe_mint}</code>"
        )
    return msg


def alert_trade(  # noqa: PLR0913
    action: str,
    symbol: str,
    sol: float,
    mint: str | None = None,
    reason: str | None = None,
    *,
    price_sol: float = 0.0,
    name: str | None = None,
    source_data: dict | None = None,
    entry_price_sol: float = 0.0,
) -> None:
    """Fire-and-forget trade alert: local beep + optional Telegram. Never raises.

    This is the REAL-money path (paper has its own, richer, alerts), so it
    carries the same facts a position is judged by: what fired, the reason the
    coin was bought, the move from entry and the profit or loss of this sale.

    Args:
        action: ``buy`` or ``sell``.
        symbol: Token ticker.
        sol: SOL value of this leg.
        mint: Token mint, for the links.
        reason: Raw sell reason (``tp_tier_+25%``, ``stop_loss``, ...).
        price_sol: Price this leg executed at.
        name: Token name, when known.
        source_data: Signal metadata, for the buy reason.
        entry_price_sol: Entry price, to show the move and the P&L.
    """
    try:
        threading.Thread(target=_beep, args=(action,), daemon=True).start()
        notify(
            build_trade_alert(
                action,
                symbol,
                sol,
                mint,
                reason,
                price_sol=price_sol,
                name=name,
                source_data=source_data,
                entry_price_sol=entry_price_sol,
            )
        )
    except Exception:  # noqa: BLE001, S110
        pass
