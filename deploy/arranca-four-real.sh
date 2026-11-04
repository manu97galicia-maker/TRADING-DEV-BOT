#!/usr/bin/env bash
# Arranca el sistema de Codex (configs/four-strategies.yaml) en DINERO REAL.
#
# Pedido el 4-nov: "Puedes ponerme esto en modo real". Mismo esquema que
# deploy/arranca-dev1-real.sh, que fue lo que funciono desde el movil:
#
#   cd /root/ai-bot && git fetch origin main && git show FETCH_HEAD:deploy/arranca-four-real.sh > /root/arranca-four.sh && bash /root/arranca-four.sh
#
# El apagado lo pone el propio run_four_strategies.py: una hora
# (MAX_RUN_SECONDS). Y antes de comprar nada hace su revision en linea; si
# encuentra un problema, no arranca y lo dice.
set -u
RAIZ=/root/ai-bot
RAMA=main
# Por defecto `origin` (SSH). Sin llave SSH en el droplet, con el repo publico:
#   ORIGEN=https://github.com/manu97galicia-maker/Definitivo_bot.git bash /root/arranca-four.sh
ORIGEN="${ORIGEN:-origin}"
CONFIG=configs/four-strategies.yaml

cd "$RAIZ" || { echo "NO ARRANCA: no existe $RAIZ"; exit 1; }

# Nunca dos a la vez (Codex tiene ademas su propio cerrojo de proceso).
if pgrep -f "run_four_strategies.py --run" >/dev/null; then
  echo "YA ESTA CORRIENDO - no arranco otro"
  exit 0
fi

# Si la mia (dev1-bloque2) esta corriendo, se avisa y NO se para: es una
# decision. Las dos hacen casi lo mismo, y si comparten wallet esta, con el
# 90% por compra, se llevaria casi todo el saldo.
if pgrep -f "run_bot_process.*dev1-bloque2" >/dev/null; then
  echo "AVISO: tambien esta corriendo dev1-bloque2. Para pararla:"
  echo "  pkill -f \"run_bot_process.*dev1-bloque2\""
fi

# Cambios a mano: se copian y se apartan (stash -u; nunca -a).
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

git checkout "$RAMA" || { echo "NO ARRANCA: fallo el checkout de $RAMA"; exit 1; }
git pull --no-rebase --no-edit "$ORIGEN" "$RAMA" \
  || { echo "NO ARRANCA: fallo el pull. Si pone CONFLICT, mandame la pantalla"; exit 1; }

# La puerta: el fichero tiene que estar encendido.
grep -q "^enabled: true" "$CONFIG" \
  || { echo "NO ARRANCA: $CONFIG no esta en enabled: true"; exit 1; }

setsid nohup "$RAIZ/.venv/bin/python" -u run_four_strategies.py --run \
  > "$RAIZ/logs/four_real.log" 2>&1 < /dev/null &

sleep 15
if pgrep -f "run_four_strategies.py --run" >/dev/null; then
  echo "ARRANCADO EN REAL - se apaga solo en 1 hora"
else
  echo "NO ARRANCO. La revision o el arranque han fallado. Mandame esto:"
  tail -30 "$RAIZ/logs/four_real.log"
  exit 1
fi
