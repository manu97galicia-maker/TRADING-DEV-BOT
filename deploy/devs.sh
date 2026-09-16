#!/usr/bin/env bash
# Mide los devs sin que importe que se caiga el SSH, y lo manda a GitHub.
#
# POR QUE EXISTE. Medir 180 devs son varios minutos hablando con pump.fun y
# DexScreener, y una sesion de SSH desde el movil se corta sola en cuanto se
# bloquea la pantalla: el 16-sep la medicion murio a mitad por eso. Ademas los
# comandos largos en un teclado de telefono salen mal (esa vez, "rcd" en vez
# de "cd"), asi que aqui todo cabe en una palabra.
#
#   bash deploy/devs.sh medir     lo lanza en segundo plano (puedes cerrar)
#   bash deploy/devs.sh ver       como va / los mejores
#   bash deploy/devs.sh subir     lo manda a GitHub para que Claude lo lea
set -euo pipefail

RAIZ=/root/ai-bot
UV=/root/.local/bin/uv
LOG="$RAIZ/logs/devs.log"
SALIDA="$RAIZ/devs_medidos.json"

cd "$RAIZ"

case "${1:-ver}" in
  medir)
    mkdir -p "$RAIZ/logs"
    if pgrep -f "medir_devs.py" >/dev/null; then
      echo "Ya se esta midiendo. Miralo con:  bash deploy/devs.sh ver"
      exit 0
    fi
    # setsid + nohup: sigue vivo aunque se cierre la sesion de SSH.
    setsid nohup "$UV" run deploy/medir_devs.py > "$LOG" 2>&1 < /dev/null &
    echo "Midiendo en segundo plano. YA PUEDES CERRAR LA APP."
    echo "Tarda unos minutos. Luego:  bash deploy/devs.sh ver"
    ;;
  ver)
    if pgrep -f "medir_devs.py" >/dev/null; then
      echo "MIDIENDO todavia. Ultimo avance:"
      tail -3 "$LOG" 2>/dev/null || echo "  (aun sin salida)"
      exit 0
    fi
    if [ ! -f "$SALIDA" ]; then
      echo "Todavia no hay medicion. Lanzala con:  bash deploy/devs.sh medir"
      [ -f "$LOG" ] && { echo; echo "Ultimo intento:"; tail -5 "$LOG"; }
      exit 0
    fi
    echo "Medicion TERMINADA."
    tail -25 "$LOG" 2>/dev/null
    echo
    echo "Para que Claude la lea:  bash deploy/devs.sh subir"
    ;;
  subir)
    if [ ! -f "$SALIDA" ]; then
      echo "No hay nada que subir: falta $SALIDA"
      exit 1
    fi
    git add devs_medidos.json devs_unificados.json
    git commit -m "Medicion de devs desde el droplet" || echo "(sin cambios)"
    git push
    echo
    echo "Subido. Dile a Claude: 'ya'."
    ;;
  *)
    echo "Uso: bash deploy/devs.sh [medir|ver|subir]"
    exit 1
    ;;
esac
