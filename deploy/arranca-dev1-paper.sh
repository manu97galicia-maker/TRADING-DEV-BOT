#!/usr/bin/env bash
# Arranca exclusivamente la prueba DEV 1% + edad 1-2 s en paper.
set -euo pipefail

RAIZ="${RAIZ:-/root/ai-bot}"
YAML="$RAIZ/bots/bot-paper-dev1-bloque2.yaml"
HORAS="${1:-1}"
LOG="$RAIZ/logs/dev1_paper.log"

cd "$RAIZ"
case "$HORAS" in
  ''|*[!0-9]*) echo "NO ARRANCA: las horas deben ser un entero"; exit 1 ;;
esac
if [ "$HORAS" -lt 1 ] || [ "$HORAS" -gt 24 ]; then
  echo "NO ARRANCA: duración permitida 1-24 horas"
  exit 1
fi

# Puerta fail-closed: este lanzador nunca acepta una configuración real.
grep -q '^  paper_trade: true$' "$YAML" || {
  echo "NO ARRANCA: esta configuración no está en paper"; exit 1;
}
grep -q '^    min_edad_seg: 1$' "$YAML" || {
  echo "NO ARRANCA: falta edad mínima de 1 s"; exit 1;
}
grep -q '^    max_edad_seg: 2$' "$YAML" || {
  echo "NO ARRANCA: falta edad máxima de 2 s"; exit 1;
}
grep -q '^    min_migrations: 1$' "$YAML" || {
  echo "NO ARRANCA: falta una migración mínima"; exit 1;
}
grep -q '^    min_rate: 0.01$' "$YAML" || {
  echo "NO ARRANCA: falta tasa mínima del 1%"; exit 1;
}
grep -q '^    strict_only: true$' "$YAML" || {
  echo "NO ARRANCA: el filtro del dev no es estricto"; exit 1;
}

if pgrep -f "run_bot_process.*bot-paper-dev1-bloque2" >/dev/null; then
  echo "YA ESTÁ CORRIENDO; no se inicia un duplicado"
  exit 0
fi

test -f "$RAIZ/.env" || { echo "NO ARRANCA: falta $RAIZ/.env"; exit 1; }
test -x "$RAIZ/.venv/bin/python" || {
  echo "NO ARRANCA: falta el Python del entorno virtual"; exit 1;
}
mkdir -p "$RAIZ/logs"

SEGUNDOS=$((HORAS * 3600))
setsid nohup timeout "$SEGUNDOS" env PYTHONPATH="$RAIZ/src" \
  "$RAIZ/.venv/bin/python" -u -c \
  "from bot_runner import run_bot_process; run_bot_process('$YAML')" \
  > "$LOG" 2>&1 < /dev/null &

sleep 3
if pgrep -f "run_bot_process.*bot-paper-dev1-bloque2" >/dev/null; then
  echo "PAPER ARRANCADO durante $HORAS hora(s). Log: $LOG"
else
  echo "SE CAYÓ AL ARRANCAR"
  tail -30 "$LOG" || true
  exit 1
fi
