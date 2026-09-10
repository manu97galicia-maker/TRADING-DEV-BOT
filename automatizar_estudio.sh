#!/bin/bash
# Manda por Telegram el estudio de operaciones cuando hay 8 nuevas.
#
# Va por cron cada 15 minutos, pero solo hace algo si el contador ha subido de
# 8: analizar con menos muestra es leer ruido y tomar decisiones con el.
#
# Cuenta las dos fuentes, porque el bot que corre ahora (bot-paper-ai-entry)
# opera en papel y NO escribe las lineas "P&L REAL" de los logs: sus resultados
# van a paper_trades/results.jsonl. Gatillar solo con las reales dejaba el
# contador congelado y el estudio no salia nunca.
set -u
cd /root/ai-bot || exit 1

ESTADO=estudio_contador.json

REALES=$(grep -h "P&L REAL" logs/*.log 2>/dev/null | wc -l)
# El "no existe" lo emite el shell al abrir la redireccion, no wc, asi que
# hay que silenciar el bloque entero o cron manda un correo de error.
PAPEL=$( { wc -l < paper_trades/results.jsonl; } 2>/dev/null || echo 0 )
TOTAL=$((REALES + PAPEL))

ULTIMO=0
[ -f "$ESTADO" ] && ULTIMO=$(grep -o '[0-9]\+' "$ESTADO" | head -1)
[ -z "$ULTIMO" ] && ULTIMO=0

if [ "$((TOTAL - ULTIMO))" -lt 8 ]; then
  exit 0
fi

PY=/root/ai-bot/.venv/bin/python
export PYTHONPATH=/root/ai-bot/src

SALIDA=$("$PY" /root/ai-bot/estudiar_operaciones.py 2>&1)
PAPEL_TXT=$("$PY" /root/ai-bot/learning-examples/summarize_signal_results.py 2>&1)

mkdir -p /root/ai-bot/estudios
{
  echo "=== OPERACIONES REALES ==="; echo "$SALIDA"
  echo; echo "=== PAPER ==="; echo "$PAPEL_TXT"
} > "/root/ai-bot/estudios/estudio_$(date +%Y%m%d_%H%M).txt"

# Las lineas que importan. Si el estudio no encontro nada que resumir, se manda
# su primera linea tal cual: un aviso vacio no dice si el bot va mal o si
# simplemente no hay operaciones todavia, y esa diferencia es justo la que hay
# que ver de un vistazo.
RESUMEN=$(echo "$SALIDA" | grep -E "ganadoras|perdedoras|RESULTADO|hacen falta" | head -4)
[ -z "$RESUMEN" ] && RESUMEN=$(echo "$SALIDA" | grep -v '^=*$' | head -3)
[ -z "$RESUMEN" ] && RESUMEN="(el estudio de operaciones reales no devolvio nada)"

RESUMEN_PAPEL=$(echo "$PAPEL_TXT" | head -8)
[ -z "$RESUMEN_PAPEL" ] && RESUMEN_PAPEL="(sin resultados de paper todavia)"

# El texto viaja por el entorno, NO incrustado en el fuente de Python: una
# comilla o una barra en el resumen rompia el heredoc y el aviso no salia.
export TG_REALES="$RESUMEN"
export TG_PAPEL="$RESUMEN_PAPEL"
export TG_TOTAL="$TOTAL"

"$PY" - <<'PY'
import os
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv("/root/ai-bot/.env")
token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
if not token or not chat:
    raise SystemExit("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el .env")

texto = (
    f"ESTUDIO DE OPERACIONES ({os.getenv('TG_TOTAL', '?')} en total)\n\n"
    f"--- REALES ---\n{os.getenv('TG_REALES', '')}\n\n"
    f"--- PAPER ---\n{os.getenv('TG_PAPEL', '')}"
)
datos = urllib.parse.urlencode({"chat_id": chat, "text": texto}).encode()
peticion = urllib.request.Request(
    f"https://api.telegram.org/bot{token}/sendMessage", data=datos
)
with urllib.request.urlopen(peticion, timeout=15) as respuesta:  # noqa: S310
    respuesta.read()
PY

# El contador se guarda AQUI, pase lo que pase con el estudio. Antes lo escribia
# estudiar_operaciones.py, que retorna antes de escribirlo cuando no encuentra
# operaciones: el contador no subia nunca y el mismo aviso vacio se repetia cada
# 15 minutos para siempre.
echo "{\"operaciones_estudiadas\": $TOTAL}" > "$ESTADO"
