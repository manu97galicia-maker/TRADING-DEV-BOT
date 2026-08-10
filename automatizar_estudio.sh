#!/bin/bash
# Lanza el estudio de operaciones cuando hay 8 nuevas desde el ultimo.
#
# Va por cron cada 15 minutos, pero solo hace algo si el contador ha subido de
# 8: analizar con menos muestra es leer ruido y tomar decisiones con el.
# El resultado se guarda y se manda por Telegram para que Manu lo vea sin
# tener que entrar al servidor.
set -u
cd /root/ai-bot || exit 1

CERRADAS=$(grep -h "P&L REAL" logs/*.log 2>/dev/null | wc -l)
ULTIMO=0
[ -f estudio_ultimo.json ] && ULTIMO=$(grep -o '[0-9]\+' estudio_ultimo.json | head -1)
[ -z "$ULTIMO" ] && ULTIMO=0

if [ "$((CERRADAS - ULTIMO))" -lt 8 ]; then
  exit 0
fi

SALIDA=$(PYTHONPATH=/root/ai-bot/src /root/ai-bot/.venv/bin/python \
         /root/ai-bot/estudiar_operaciones.py 2>&1)
echo "$SALIDA" > /root/ai-bot/estudios/estudio_$(date +%Y%m%d_%H%M).txt

# Resumen corto a Telegram: las tres lineas que importan.
RESUMEN=$(echo "$SALIDA" | grep -E "ganadoras|perdedoras|RESULTADO|hacen falta" | head -4)
/root/ai-bot/.venv/bin/python - <<PY
import os, urllib.parse, urllib.request
from dotenv import load_dotenv
load_dotenv("/root/ai-bot/.env")
t = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
c = os.getenv("TELEGRAM_CHAT_ID", "").strip()
if t and c:
    texto = "ESTUDIO DE OPERACIONES\n\n" + """$RESUMEN"""
    d = urllib.parse.urlencode({"chat_id": c, "text": texto}).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{t}/sendMessage", data=d),
            timeout=15)
    except Exception:
        pass
PY
