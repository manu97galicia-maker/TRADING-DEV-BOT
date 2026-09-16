#!/usr/bin/env bash
# Enciende, mira o apaga el cazador de senales, desde el movil y con una
# palabra. Hace falta porque medir bien son HORAS de escucha, y una sesion
# de SSH desde el telefono se cierra en cuanto se bloquea la pantalla.
#
#   bash deploy/cazador.sh on     instala el servicio y lo arranca
#   bash deploy/cazador.sh ver    cuantas monedas lleva y que dicen los datos
#   bash deploy/cazador.sh off    lo para (lo apuntado NO se borra)
set -euo pipefail

RAIZ=/root/ai-bot
UNIDAD=pump-bot-cazador.service
DATOS="$RAIZ/trades/senales.jsonl"

case "${1:-ver}" in
  on)
    mkdir -p "$RAIZ/logs" "$RAIZ/trades"
    cp "$RAIZ/deploy/$UNIDAD" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now "$UNIDAD"
    echo "Cazador ENCENDIDO. No compra nada, solo mide."
    echo "Deja pasar unas horas y luego:  bash deploy/cazador.sh ver"
    ;;
  off)
    systemctl disable --now "$UNIDAD" 2>/dev/null || true
    echo "Cazador APAGADO. Lo apuntado sigue en $DATOS"
    ;;
  ver)
    if systemctl is-active --quiet "$UNIDAD"; then
      echo "Cazador: ENCENDIDO"
    else
      echo "Cazador: apagado  (arrancalo con: bash deploy/cazador.sh on)"
    fi
    if [ -f "$DATOS" ]; then
      echo "Monedas apuntadas: $(wc -l < "$DATOS")"
    else
      echo "Monedas apuntadas: 0 (todavia no ha cerrado ninguna)"
    fi
    echo
    cd "$RAIZ" && /root/.local/bin/uv run deploy/analizar_senales.py
    ;;
  *)
    echo "Uso: bash deploy/cazador.sh [on|ver|off]"
    exit 1
    ;;
esac
