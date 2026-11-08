#!/usr/bin/env bash
# Arranca LAS TRES ESTRATEGIAS en DINERO REAL, con apagado automatico.
# (copia de arranca-dev1-real.sh, 7-nov: "anade ... una estrategia ... y
# tambien anade una que ...")
#
# Pedido el 4-nov: "No consigo, dimelo en un solo comando". Los pegados de
# varias lineas desde el movil se atascaban; esto hace todo en una llamada:
#
#   cd /root/ai-bot && git fetch origin main && git show FETCH_HEAD:deploy/arranca-tres-real.sh > /root/arranca-tres.sh && bash /root/arranca-tres.sh HORAS
#
# Se lee con `git show` en vez de `git pull` porque el pull es justo lo que
# fallaba (cambios a mano en el droplet). Y se guarda en un fichero antes de
# ejecutarlo, en vez de `| bash`, para que el bot no herede como entrada el
# resto del script.
#
# Argumento opcional: horas (por defecto 9, pedido el 4-nov). REGLA 1: nunca
# sin plazo.
set -u
RAIZ="${RAIZ:-/root/ai-bot}"
# MAIN, desde el 6-nov: es donde Manu cambia la configuracion desde GitHub.
# Antes era la rama de trabajo de Claude, y un cambio hecho en main no llegaba.
RAMA=main
# DE DONDE SE BAJA EL CODIGO. Por defecto `origin`, que en el droplet es SSH
# (git@github.com) y necesita llave aunque el repo sea publico. El 4-nov el
# droplet perdio la llave ("Permission denied (publickey)"); con el repo
# publico un momento, se puede pasar la direccion HTTPS:
#   ORIGEN=https://github.com/manu97galicia-maker/Definitivo_bot.git bash /root/arranca-dev1.sh
ORIGEN="${ORIGEN:-origin}"
#: Las estrategias en real (7-nov): la del dev, la de la curva al 92%, la de
#: presion desde 6k y la de presion suave desde 15,3k. Cada una con su log en logs/<nombre>_real.log.
#: 7-nov: "dejamos solo las estrategias de presion compradora": fuera dev1
#: y la curva 92% (sus YAML siguen ahi; para volver, se anaden a esta lista).
#: 7-nov, despues: "elimina lo de 6k y 15k ... una unica estrategia".
#: 7-nov, despues: "hazme una estrategia de mayhem ... arriesgando 4$".
#: 7-nov, despues: "arranca solo las que llegan al 91%" -> solo presion.
#: Despues: "maybheim tambien, pero prueba cinco veces y si ganamos otras cinco".
#: Despues: "compra todas las que rompan los 10.1k ... nueva estrategia".
#: Despues: "mayhem no, solo la que rompe los 10k".
#: 8-nov: "monta una estrategia para los de ultimos" -> copiar a los 27 devs
#: de Dune, en real. Va junto a la de la curva (comparten el tope de 5).
#: Despues: 8 carteras de Manu -> snipe a las 2 "dev" y copia a las otras 6.
ESTRATEGIAS="bots/bot-real-rompe10k.yaml bots/bot-real-devs27.yaml bots/bot-real-snipe2devs.yaml bots/bot-real-copia6.yaml"
#: Nombre corto del log de cada una. dev1 sigue en logs/dev1_real.log, que es
#: el que leen porque-no-compra.sh y los comandos de siempre.
log_de() {
  case "$(basename "$1" .yaml)" in
    bot-paper-dev1-bloque2) echo "dev1_real.log" ;;
    bot-real-curva92) echo "curva92_real.log" ;;
    bot-real-presion) echo "presion_real.log" ;;
    bot-real-mayhem) echo "mayhem_real.log" ;;
    bot-real-rompe10k) echo "rompe10k_real.log" ;;
    bot-real-devs27) echo "devs27_real.log" ;;
    bot-real-snipe2devs) echo "snipe2devs_real.log" ;;
    bot-real-copia6) echo "copia6_real.log" ;;
    *) echo "$(basename "$1" .yaml)_real.log" ;;
  esac
}
HORAS="${1:-9}"

cd "$RAIZ" || { echo "NO ARRANCA: no existe $RAIZ"; exit 1; }

# FUERA pump-bot Y SU AUTO-ACTUALIZADOR (6-nov). pump-bot arranca con
# bot_runner todo lo que tenga `enabled: true` -- esta estrategia incluida --
# y el auto-actualizador lo reinicia cada vez que baja algo de GitHub. Con
# los dos encendidos habria DOS bots en real sobre la misma wallet. Para
# volver a encender el auto-actualizador:
#   systemctl start pump-bot-autoactualiza.timer
systemctl stop pump-bot-autoactualiza.timer pump-bot 2>/dev/null
pkill -f "bot_runner.py" 2>/dev/null && sleep 3

# SE PARAN LAS TRES ANTES DE NADA (7-nov). Este arrancador las vuelve a
# lanzar todas con el codigo nuevo; dos procesos con el mismo fichero
# comprarian dos veces sobre la misma wallet.
# Se paran TODAS, no solo las de la lista (7-nov): al quitar dev1 y la curva
# 92% de la lista, parando solo las de la lista esas dos seguian comprando.
pkill -f "run_bot_process" 2>/dev/null
sleep 3

# UNA ACTUALIZACION A MEDIAS (6-nov): si un pull anterior choco, git se
# queda "a mitad de mezcla" y no deja hacer nada. Se deshace la mezcla; lo
# que hubiera a mano vuelve a estar como antes de ella y se aparta abajo.
if [ -f .git/MERGE_HEAD ]; then
  git merge --abort 2>/dev/null || git reset --merge
  echo "Habia una actualizacion a medias: deshecha"
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

# IGUAL QUE GITHUB, SIN MEZCLAR (6-nov). Antes se hacia `git pull`, que
# MEZCLA lo del droplet con lo de GitHub: el droplet acumulo mezclas propias
# (6c5ef692) y se quedo con 4 ficheros distintos de GitHub sin que nada lo
# dijera. Ahora se baja GitHub y se pone TAL CUAL. Lo que el droplet tuviera
# de su cosecha no se pierde: queda en la rama respaldo-<fecha>.
git branch -f "respaldo-$F" HEAD 2>/dev/null \
  && echo "Version anterior del servidor guardada en la rama respaldo-$F"
git fetch "$ORIGEN" "$RAMA" \
  || { echo "NO ARRANCA: no pude bajar $RAMA de GitHub"; exit 1; }
git checkout -B "$RAMA" FETCH_HEAD \
  || { echo "NO ARRANCA: fallo el checkout de $RAMA"; exit 1; }
git branch --set-upstream-to="origin/$RAMA" "$RAMA" >/dev/null 2>&1
if [ -n "$(git status --porcelain --untracked-files=no)" ] \
   || ! git diff --quiet FETCH_HEAD; then
  echo "NO ARRANCA: el servidor no ha quedado igual que GitHub"
  git status --short | head -10
  exit 1
fi
echo "Servidor igual que GitHub: $(git log -1 --format='%h %s')"

# LA PUERTA: solo arranca las que esten en real. Una en papel no se lanza
# desde aqui (este arrancador es el de dinero real).
SEG=$((HORAS * 3600))
LANZADAS=""
for Y in $ESTRATEGIAS; do
  NOMBRE=$(basename "$Y" .yaml)
  if [ ! -f "$Y" ]; then
    echo "NO ESTA $Y: no se arranca"
    continue
  fi
  if ! grep -q "^  paper_trade: false" "$Y"; then
    echo "$NOMBRE no esta en real: no se arranca"
    continue
  fi
  setsid nohup timeout "$SEG" env PYTHONPATH="$RAIZ/src" \
    "$RAIZ/.venv/bin/python" -u -c \
    "from bot_runner import run_bot_process; run_bot_process('$RAIZ/$Y')" \
    > "$RAIZ/logs/$(log_de "$Y")" 2>&1 < /dev/null &
  LANZADAS="$LANZADAS $Y"
  sleep 2
done

sleep 5
FALLOS=0
for Y in $LANZADAS; do
  NOMBRE=$(basename "$Y" .yaml)
  if pgrep -f "run_bot_process.*$NOMBRE" >/dev/null; then
    echo "ARRANCADA EN REAL: $NOMBRE (log: logs/$(log_de "$Y"))"
  else
    FALLOS=1
    echo "NO SE MANTUVO: $NOMBRE. Ultimas lineas:"
    tail -8 "$RAIZ/logs/$(log_de "$Y")" 2>/dev/null \
      | sed -E 's/(api[-_]?key=)[^&" ]+/\1***/Ig'
  fi
done
echo "Se apagan solas en $HORAS horas."
exit $FALLOS
