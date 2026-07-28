"""Turn plain-language orders into concrete config changes.

Rule-based (no LLM): the dashboard command box sends text like "market cap
5k-250k, liquidez bloqueada, dev migró 24%, vende 80% al 40%, 10% al x2,
10% al x5, sl 18%, slippage 20%, compra 0.05 sol" and this maps it to config
actions the editor applies live. Anything not understood is returned in
``note`` so the UI can say so.

Each action is ``{scope, field, value, human}`` where ``scope`` is
``"trade"`` (edits ``trade.*``) or ``"dexscreener"`` (edits
``filters.dexscreener.*``).
"""

from __future__ import annotations

import re
from typing import Any


def _num(text: str) -> float:
    """Parse a number, honoring k/m suffixes (5k -> 5000, 1.2m -> 1_200_000)."""
    s = text.strip().lower().replace(",", ".")
    mult = 1.0
    if s.endswith("k"):
        mult, s = 1_000.0, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000.0, s[:-1]
    return float(s) * mult


_N = r"(\d[\d.,]*[km]?)"  # a number with optional k/m suffix


def interpret(text: str) -> dict[str, Any]:
    """Parse an order string into config actions (Spanish or English)."""
    t = " " + text.lower().strip() + " "
    actions: list[dict[str, Any]] = []

    def add(scope: str, field: str, value: Any, human: str) -> None:
        actions.append({"scope": scope, "field": field, "value": value, "human": human})

    # ---- Action commands (not config edits) ----
    if re.search(r"vend\w*\s+(todo|todas|all)|cierra\w*\s+(todo|todas|posici)|p[aá]nico|panic", t):
        add("action", "sell_all", True, "🔴 Vender TODAS las posiciones")
    if re.search(r"\b(arranca|arrancar|activa el bot|enciende el bot|start|inicia)\b", t):
        add("action", "start_bot", True, "▶ Arrancar el bot")
    if re.search(r"\b(para el bot|parar|det[eé]n|stop|apaga el bot)\b", t):
        add("action", "stop_bot", True, "■ Parar el bot")

    # ---- Mayhem mode ----
    # "solo mayhem" / "solo modo mayhem" -> comprar ÚNICAMENTE tokens mayhem.
    # require_mayhem implica allow_mayhem (si no, el guard los rechazaría).
    if re.search(r"\b(sin|no|quita\w*)\b[^.]*mayhem", t):
        add("flat", "require_mayhem", False, "Sin exigir mayhem")
        add("flat", "allow_mayhem", False, "Evitar tokens mayhem")
    elif re.search(r"\b(solo|s[oó]lo|only|[uú]nicamente)\b[^.]*mayhem", t):
        add("flat", "allow_mayhem", True, "Permitir mayhem")
        add("flat", "require_mayhem", True, "Comprar SOLO tokens mayhem")
    elif re.search(r"\bmayhem\b", t):
        add("flat", "allow_mayhem", True, "Permitir tokens mayhem")

    # ---- DexScreener "Dex Paid" gate ----
    if re.search(r"dex\s*paid", t):
        if re.search(r"\b(no|sin|quita)\b[^.]*dex\s*paid", t):
            add("dexscreener", "require_dex_paid", False, "Sin exigir Dex Paid")
        else:
            add("dexscreener", "enabled", True, "Filtro DexScreener ON")
            add("dexscreener", "require_dex_paid", True, "Comprar solo si Dex Paid")

    if re.search(r"(desactiv\w*|quita\w*|apaga\w*)\s+(el\s+)?filtro", t):
        add("dexscreener", "enabled", False, "Filtro DexScreener OFF")

    # ---- Locked liquidity ----
    if re.search(r"liquidez\s*bloquead|locked\s*liquidity|liq\.?\s*bloquead", t):
        add("dexscreener", "enabled", True, "Filtro DexScreener ON")
        add("dexscreener", "require_liquidity_locked", True, "Exigir liquidez bloqueada")
    elif re.search(r"(sin|no|quita)\b[^.]*liquidez\s*bloquead", t):
        add("dexscreener", "require_liquidity_locked", False, "Sin exigir liquidez bloqueada")

    # ---- Market cap (range / min / max) ----
    if re.search(r"market\s*cap|mcap|\bmc\b", t):
        add("dexscreener", "enabled", True, "Filtro DexScreener ON")
        mn = re.search(rf"(?:min\w*|desde|mayor(?:\s*que)?|>)\s*\$?{_N}", t)
        mx = re.search(rf"(?:max\w*|hasta|menor(?:\s*que)?|<)\s*\$?{_N}", t)
        rng = re.search(rf"\$?{_N}\s*(?:-|–|a|y)\s*\$?{_N}\b", t)
        if rng and not (mn or mx):
            lo, hi = _num(rng.group(1)), _num(rng.group(2))
            add("dexscreener", "min_market_cap", lo, f"Market cap mín ${lo:,.0f}")
            add("dexscreener", "max_market_cap", hi, f"Market cap máx ${hi:,.0f}")
        else:
            if mn:
                v = _num(mn.group(1))
                add("dexscreener", "min_market_cap", v, f"Market cap mín ${v:,.0f}")
            if mx:
                v = _num(mx.group(1))
                add("dexscreener", "max_market_cap", v, f"Market cap máx ${v:,.0f}")

    # ---- Liquidity USD / boosts ----
    m = re.search(rf"liquidez\s*(?:min\w*\s*)?\$?{_N}", t)
    if m and "bloquead" not in m.group(0):
        v = _num(m.group(1))
        add("dexscreener", "enabled", True, "Filtro DexScreener ON")
        add("dexscreener", "min_liquidity_usd", v, f"Liquidez mín ${v:,.0f}")
    m = re.search(rf"boost\w*\s*(?:min\w*\s*)?{_N}", t)
    if m:
        add("dexscreener", "enabled", True, "Filtro DexScreener ON")
        add("dexscreener", "min_boosts", int(_num(m.group(1))), f"Boosts mín {int(_num(m.group(1)))}")

    # ---- Dev migration rate ----
    m = re.search(r"(?:dev|developer|creador)\s*(?:migr\w*\s*)?(\d+)\s*%", t)
    if m:
        rate = int(m.group(1)) / 100
        add("dexscreener", "enabled", True, "Filtro DexScreener ON")
        add("dexscreener", "min_dev_migration_rate", rate, f"Dev migró ≥{m.group(1)}%")

    # ---- Take-profit tiers ----
    # "vende todo al 40%" / "vende al 40% ... todo" -> TP1 +40%, vender 100%.
    m = re.search(r"vend\w*\s*(?:todo|todas?)\s*(?:al|a|en)\s*\+?(\d+)\s*%", t) or (
        re.search(r"vend\w*\s*(?:al|a|en)\s*\+?(\d+)\s*%[^.]*\btodo\b", t)
    )
    if m:
        add("trade", "exit_strategy", "tp_sl", "Estrategia TP/SL")
        add("trade", "take_profit_percentage", int(m.group(1)) / 100, f"TP1 +{m.group(1)}%")
        add("trade", "take_profit_sell_percentage", 1.0, "vender 100% en TP1")
    # Tier 1: "vende 80% al 40%" or "tp 40% vende 80%"
    m = re.search(r"vend\w*\s*(\d+)\s*%\s*(?:al|a|en|cuando suba)\s*\+?(\d+)\s*%", t)
    if m:
        add("trade", "exit_strategy", "tp_sl", "Estrategia TP/SL")
        add("trade", "take_profit_percentage", int(m.group(2)) / 100, f"TP1 +{m.group(2)}%")
        add("trade", "take_profit_sell_percentage", int(m.group(1)) / 100, f"vender {m.group(1)}% en TP1")
    else:
        m = re.search(r"(?:tp|take\s*profit)\s*(\d+)\s*%(?:[^%]*vend\w*\s*(\d+)\s*%)?", t)
        if m:
            add("trade", "exit_strategy", "tp_sl", "Estrategia TP/SL")
            add("trade", "take_profit_percentage", int(m.group(1)) / 100, f"TP1 +{m.group(1)}%")
            if m.group(2):
                add("trade", "take_profit_sell_percentage", int(m.group(2)) / 100, f"vender {m.group(2)}% en TP1")

    # Tiers by multiplier: "10% al x2", "vende 10% al x2", "x5 vende 10%"
    def _add_tier(mult: int, sell: int) -> None:
        if mult == 2:
            add("trade", "tp2_gain", 1.0, "TP2 = x2")
            add("trade", "tp2_sell", sell / 100, f"vender {sell}% en x2")
        elif mult >= 3:
            add("trade", "tp3_gain", float(mult - 1), f"TP3 = x{mult}")
            add("trade", "tp3_sell", sell / 100, f"vender {sell}% en x{mult}")

    for mm in re.finditer(r"(\d+)\s*%\s*(?:al|a|en|cuando)\s*x\s?(\d+)", t):
        _add_tier(int(mm.group(2)), int(mm.group(1)))
    for mm in re.finditer(r"x\s?(\d+)\s*(?:vend\w*\s*)?(\d+)\s*%", t):
        _add_tier(int(mm.group(1)), int(mm.group(2)))

    # ---- Stop loss ----
    m = re.search(r"(?:sl|stop\s*loss)\s*(\d+)\s*%", t)
    if m:
        add("trade", "exit_strategy", "tp_sl", "Estrategia TP/SL")
        add("trade", "stop_loss_percentage", int(m.group(1)) / 100, f"SL -{m.group(1)}%")

    # ---- Slippage ----
    m = re.search(r"slippage\s*(\d+)\s*%", t)
    if m:
        val = int(m.group(1)) / 100
        add("trade", "buy_slippage", val, f"Slippage {m.group(1)}%")
        add("trade", "sell_slippage", val, f"Slippage venta {m.group(1)}%")

    # ---- Buy amount ----
    m = re.search(rf"compr\w*\s*(?:con\s*)?{_N}\s*sol", t)
    if m:
        v = _num(m.group(1))
        add("trade", "buy_amount", v, f"Comprar {v:g} SOL por token")

    # ---- Sniper params (scope 'flat') ----
    # Priority fee = landing speed. "fee 2000000" or "prioridad 2000000".
    m = re.search(r"(?:fee|prioridad|priority)\s*(\d[\d_]*)", t)
    if m:
        fee = int(m.group(1).replace("_", ""))
        add("flat", "fixed_amount", fee, f"Fee de prioridad {fee:,}")
        add("flat", "hard_cap", fee, f"Tope de fee {fee:,}")
    # Max token age (seconds): "edad max 2", "antiguedad 2s", "token age 2"
    m = re.search(r"(?:edad|antig\w*|token\s*age)\s*(?:max\w*\s*)?(\d+(?:[.,]\d+)?)\s*s?", t)
    if m:
        add("flat", "max_token_age", _num(m.group(1)), f"Antigüedad máx {m.group(1)}s")
    # Continuous vs one-shot
    if re.search(r"\b(continuo|sin parar|yolo|no parar)\b", t):
        add("flat", "yolo_mode", True, "Modo continuo (yolo)")
    elif re.search(r"\b(una compra|un solo|una sola|para tras)\b", t):
        add("flat", "yolo_mode", False, "Una sola compra")
    # Name + creator use the ORIGINAL text (case-sensitive; addresses break if
    # lowercased since base58 is case-sensitive).
    m = re.search(
        r"(?:nombre\s*(?:contiene|con)|tokens?\s*con|match)\s*[\"']?([A-Za-z0-9áéíóúñ]+)[\"']?",
        text, re.IGNORECASE,
    )
    if m and m.group(1).lower() not in ("dex", "boost", "boosts", "liquidez"):
        add("flat", "match_string", m.group(1), f"Solo tokens con '{m.group(1)}'")
    m = re.search(
        r"(?:creador|creator|dev)\s*[:=]?\s*([1-9A-HJ-NP-Za-km-z]{32,44})",
        text, re.IGNORECASE,
    )
    if m:
        add("flat", "bro_address", m.group(1), f"Solo del creador {m.group(1)[:6]}…")

    # De-duplicate (keep last value per scope+field)
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for a in actions:
        dedup[(a["scope"], a["field"])] = a
    final = list(dedup.values())

    return {
        "actions": final,
        "matched": bool(final),
        "note": "" if final else "No entendí. Ejemplos: 'market cap 5k-250k', "
        "'liquidez bloqueada', 'dev migró 24%', 'vende 80% al 40%, 10% al x2, "
        "10% al x5', 'sl 18%', 'slippage 20%', 'compra 0.05 sol'.",
    }
