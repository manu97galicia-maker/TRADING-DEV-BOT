"""Panel de Telegram para los bots: PnL en vivo + botones de venta (25/50/100%).

- Lee los open_position*.json que cada bot escribe cada 2s (mint, symbol, PnL,
  restante, valor en SOL).
- Manda/edita un mensaje por posicion en Telegram con el PnL refrescandose y
  botones inline [Vender 25% | 50% | 100%].
- Al pulsar un boton, escribe sell_cmd<suffix>.json = {mint, frac}; el monitor
  del bot lo detecta y vende esa fraccion del RESTANTE.

Cada bot mantiene 1 posicion a la vez (position_semaphore=1), asi que el
'suffix' del bot identifica la posicion de forma unica.

Arranca aparte (junto a los bots), NO toca el trading:
    .venv/Scripts/python.exe tg_panel.py
"""
import glob
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path


def _load_env():
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if (not tok or not chat) and Path(".env").exists():
        for ln in Path(".env").read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln.startswith("TELEGRAM_BOT_TOKEN") and "=" in ln:
                tok = tok or ln.split("=", 1)[1].strip().strip('"').strip("'")
            elif ln.startswith("TELEGRAM_CHAT_ID") and "=" in ln:
                chat = chat or ln.split("=", 1)[1].strip().strip('"').strip("'")
    return tok, chat


TOKEN, CHAT_ID = _load_env()
API = f"https://api.telegram.org/bot{TOKEN}"
POLL = 3  # segundos entre refrescos


def _api(method, params, timeout=15):
    try:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(f"{API}/{method}", data=data)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def _bot_name(suffix):
    # "_bot-sniper-gemas" -> "GEMAS"
    s = suffix.lstrip("_").replace("bot-sniper-", "")
    return s.upper() or "BOT"


def _keyboard(suffix):
    row = [
        {"text": "Vender 25%", "callback_data": f"s|{suffix}|0.25"},
        {"text": "Vender 50%", "callback_data": f"s|{suffix}|0.5"},
        {"text": "Vender 100%", "callback_data": f"s|{suffix}|1"},
    ]
    return json.dumps({"inline_keyboard": [row]})


def _fmt(rec):
    sym = rec.get("symbol") or "?"
    mint = rec.get("mint", "")
    pnl = rec.get("pnl_pct", 0.0)
    val = rec.get("value_sol", 0.0)
    entry = rec.get("entry_price", 0.0)
    price = rec.get("price", 0.0)
    bot = _bot_name(rec.get("suffix", ""))
    face = "🟢" if pnl >= 0 else "🔴"
    sign = "+" if pnl >= 0 else ""
    return (
        f"{face} <b>{sym}</b>  ({bot})\n"
        f"PnL: <b>{sign}{pnl:.1f}%</b>  ·  valor ~{val:.3f} SOL\n"
        f"entrada {entry:.8f} → ahora {price:.8f}\n"
        f'<a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · <code>{mint}</code>'
    )


def _read_positions():
    out = {}
    for f in glob.glob("open_position*.json"):
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
            suf = rec.get("suffix")
            # ignora marcadores viejos sin datos en vivo
            if suf and "pnl_pct" in rec:
                out[suf] = rec
        except Exception:
            continue
    return out


def _handle_callbacks(offset):
    """Procesa pulsaciones de botones. Devuelve el nuevo offset."""
    res = _api("getUpdates", {"offset": offset, "timeout": 0}, timeout=10)
    if not res.get("ok"):
        return offset
    for upd in res.get("result", []):
        offset = upd["update_id"] + 1
        cq = upd.get("callback_query")
        if not cq:
            continue
        # Seguridad: SOLO el chat autorizado puede ejecutar ventas. Ignora (y
        # avisa) cualquier pulsación que no venga de CHAT_ID.
        from_chat = str(cq.get("message", {}).get("chat", {}).get("id", ""))
        from_user = str(cq.get("from", {}).get("id", ""))
        if str(CHAT_ID) not in (from_chat, from_user):
            _api("answerCallbackQuery",
                 {"callback_query_id": cq.get("id"), "text": "No autorizado"})
            continue
        data = cq.get("data", "")
        cq_id = cq.get("id")
        parts = data.split("|")
        if len(parts) != 3 or parts[0] != "s":
            _api("answerCallbackQuery", {"callback_query_id": cq_id})
            continue
        _, suffix, frac = parts
        pos = _read_positions().get(suffix)
        if not pos:
            _api("answerCallbackQuery",
                 {"callback_query_id": cq_id, "text": "Ya no hay posicion abierta"})
            continue
        cmd = {"mint": pos["mint"], "frac": float(frac), "ts": time.time()}
        try:
            Path(f"sell_cmd{suffix}.json").write_text(json.dumps(cmd), encoding="utf-8")
            pct = int(round(float(frac) * 100))
            _api("answerCallbackQuery",
                 {"callback_query_id": cq_id,
                  "text": f"Orden enviada: vender {pct}% de {pos.get('symbol','?')}"})
        except Exception as e:
            _api("answerCallbackQuery",
                 {"callback_query_id": cq_id, "text": f"Error: {str(e)[:80]}"})
    return offset


def main():
    if not TOKEN or not CHAT_ID:
        print("FALTA TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en .env")
        return
    print("Panel TG arrancado. Refrescando cada", POLL, "s.")
    _api("sendMessage", {"chat_id": CHAT_ID,
                         "text": "📟 Panel de posiciones ACTIVO. Verás aquí cada "
                                 "posición con su PnL y botones de venta."})
    msgs = {}          # suffix -> message_id
    last_text = {}     # suffix -> ultimo texto (evita edits redundantes)
    offset = 0
    # arranca el offset al final para no procesar pulsaciones viejas
    r0 = _api("getUpdates", {"timeout": 0})
    if r0.get("ok") and r0.get("result"):
        offset = r0["result"][-1]["update_id"] + 1

    while True:
        try:
            offset = _handle_callbacks(offset)
            positions = _read_positions()

            # posiciones nuevas / actualizadas
            for suf, rec in positions.items():
                text = _fmt(rec)
                if suf not in msgs:
                    resp = _api("sendMessage", {
                        "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                        "disable_web_page_preview": "true",
                        "reply_markup": _keyboard(suf)})
                    if resp.get("ok"):
                        msgs[suf] = resp["result"]["message_id"]
                        last_text[suf] = text
                elif text != last_text.get(suf):
                    _api("editMessageText", {
                        "chat_id": CHAT_ID, "message_id": msgs[suf], "text": text,
                        "parse_mode": "HTML", "disable_web_page_preview": "true",
                        "reply_markup": _keyboard(suf)})
                    last_text[suf] = text

            # posiciones cerradas -> marca el mensaje y quita botones
            for suf in list(msgs.keys()):
                if suf not in positions:
                    _api("editMessageText", {
                        "chat_id": CHAT_ID, "message_id": msgs[suf],
                        "text": (last_text.get(suf, "") + "\n\n✅ <b>POSICIÓN CERRADA</b>"),
                        "parse_mode": "HTML", "disable_web_page_preview": "true"})
                    msgs.pop(suf, None)
                    last_text.pop(suf, None)

            time.sleep(POLL)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("loop err:", str(e)[:150])
            time.sleep(POLL)


if __name__ == "__main__":
    main()
