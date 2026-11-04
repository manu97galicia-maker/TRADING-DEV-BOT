#!/usr/bin/env bash
# Arranca bot-paper-dev1-bloque2 en DINERO REAL, con apagado automatico.
#
# Pedido el 4-nov: "No consigo, dimelo en un solo comando". Los pegados de
# varias lineas desde el movil se atascaban; esto hace todo en una llamada:
#
#   cd /root/ai-bot && git fetch origin claude/private-key-env-access-cc1er2 && git show FETCH_HEAD:deploy/arranca-dev1-real.sh > /root/arranca-dev1.sh && bash /root/arranca-dev1.sh
#
# Se lee con `git show` en vez de `git pull` porque el pull es justo lo que
# fallaba (cambios a mano en el droplet). Y se guarda en un fichero antes de
# ejecutarlo, en vez de `| bash`, para que el bot no herede como entrada el
# resto del script.
#
# Argumento opcional: horas (por defecto 9, pedido el 4-nov). REGLA 1: nunca
# sin plazo.
set -u
RAIZ=/root/ai-bot
RAMA=claude/private-key-env-access-cc1er2
# DE DONDE SE BAJA EL CODIGO. Por defecto `origin`, que en el droplet es SSH
# (git@github.com) y necesita llave aunque el repo sea publico. El 4-nov el
# droplet perdio la llave ("Permission denied (publickey)"); con el repo
# publico un momento, se puede pasar la direccion HTTPS:
#   ORIGEN=https://github.com/manu97galicia-maker/Definitivo_bot.git bash /root/arranca-dev1.sh
ORIGEN="${ORIGEN:-origin}"
YAML=bots/bot-paper-dev1-bloque2.yaml
HORAS="${1:-9}"

cd "$RAIZ" || { echo "NO ARRANCA: no existe $RAIZ"; exit 1; }

# NUNCA DOS A LA VEZ. Dos procesos con el mismo fichero comprarian las mismas
# monedas dos veces sobre la misma wallet. Se busca el PROCESO DEL BOT, no el
# texto suelto: cualquier comando que nombre el fichero daria un falso "ya
# corriendo". run_bot_process no pasa por el
# cerrojo de .bot.pid, asi que el cerrojo es este.
if pgrep -f "run_bot_process.*dev1-bloque2" >/dev/null; then
  echo "YA ESTA CORRIENDO - no arranco otro"
  exit 0
fi

# CAMBIOS A MANO: se copian (solo esos ficheros, segundos) y se apartan con
# stash -u. Ni el .env, ni los logs, ni los marcadores de posiciones abiertas
# entran: git los ignora, y stash -u no toca lo ignorado.
F=$(date +%Y%m%d-%H%M%S)
if [ -n "$(git status --porcelain)" ]; then
  mkdir -p "/root/respaldo-$F"
  git status --short > "/root/respaldo-$F/LISTA.txt"
  git status --porcelain | cut -c4- | while read -r f; do
    cp -r --parents "$f" "/root/respaldo-$F/" 2>/dev/null
  done
  git stash push -u -m "cambios locales $F" \
    || { echo "NO ARRANCA: no pude apartar los cambios a mano"; exit 1; }
  echo "Cambios a mano guardados en /root/respaldo-$F y en git stash"
fi

git checkout "$RAMA" || { echo "NO ARRANCA: fallo el checkout"; exit 1; }
git pull --no-rebase --no-edit "$ORIGEN" "$RAMA" \
  || { echo "NO ARRANCA: fallo el pull. Si pone CONFLICT, mandame la pantalla"; exit 1; }

# LA PUERTA: solo arranca si el fichero esta en real y con las 7 plazas.
if ! grep -q "^  paper_trade: false" "$YAML" \
   || ! grep -q "^  max_posiciones: 7" "$YAML"; then
  echo "NO ARRANCA: el fichero no esta en real con 7 plazas"
  exit 1
fi

SEG=$((HORAS * 3600))
setsid nohup timeout "$SEG" env PYTHONPATH="$RAIZ/src" \
  "$RAIZ/.venv/bin/python" -u -c \
  "from bot_runner import run_bot_process; run_bot_process('$RAIZ/$YAML')" \
  > "$RAIZ/logs/dev1_real.log" 2>&1 < /dev/null &

sleep 5
if pgrep -f "run_bot_process.*dev1-bloque2" >/dev/null; then
  echo "ARRANCADO EN REAL - se apaga solo en $HORAS horas"
else
  echo "SE CAYO AL ARRANCAR. Mandame esto:"
  tail -20 "$RAIZ/logs/dev1_real.log"
  exit 1
fi
