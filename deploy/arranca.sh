#!/usr/bin/env bash
# TODO EL ARRANQUE EN UN COMANDO CORTO.
#
# POR QUE EXISTE. Desde el movil, un comando largo no llega: o se cae el SSH a
# mitad, o la sesion vuelve a ~ y las rutas relativas fallan, o sale una
# errata en el teclado. Ha pasado cuatro veces seguidas con la misma linea
# ("cd /root/ai-bot && git pull && ... && bot"). Esto es esa linea entera,
# metida en un fichero con ruta absoluta:
#
#     bash /root/ai-bot/deploy/arranca.sh
#
# Trae lo nuevo, instala los comandos cortos, reinicia el bot y enseña el
# estado. Es idempotente: se puede repetir sin miedo.
set -uo pipefail

RAIZ=/root/ai-bot
cd "$RAIZ" || { echo "No existe $RAIZ"; exit 1; }

echo "== 1/5 TRAYENDO LO NUEVO =="
# APARTA LO QUE BLOQUEA EL PULL, y solo eso.
#
# EL ATASCO DEL 21-SEP, cuatro vueltas desde el movil con el bot corriendo
# codigo viejo: el pull no entraba porque `bots/bot-paper-ai-entry.yaml`
# estaba tocado a mano en la maquina, y el guion contestaba "reintenta git
# pull" -- que era justo lo que no podia funcionar.
#
# Lo que se hace: copiar esos ficheros a /root/respaldo-<fecha> y devolverlos
# a como estan en el repo. NADA MAS. Solo se toca lo que git ya sabe que
# esta modificado; el .env con la clave de la wallet y los *.session de
# Telegram estan en .gitignore y no entran aqui ni pueden entrar.
git fetch -q origin || { echo "No se pudo hablar con GitHub."; exit 1; }

estorban="$(git diff --name-only HEAD 2>/dev/null | sort -u)"
if [ -n "$estorban" ]; then
  RESPALDO="/root/respaldo-$(date +%Y%m%d-%H%M%S)"
  echo "  hay ficheros tocados a mano; se guardan en $RESPALDO"
  printf '%s\n' "$estorban" | while read -r f; do
    [ -f "$f" ] || continue
    mkdir -p "$RESPALDO/$(dirname "$f")"
    cp "$f" "$RESPALDO/$f"
    echo "    $f"
  done
  printf '%s\n' "$estorban" | while read -r f; do
    git checkout -- "$f" 2>/dev/null
  done
fi

# --no-rebase EXPLICITO, y no es un adorno. Desde git 2.34, cuando el
# historial se ha separado `git pull` no elige por ti: se NIEGA con "Need to
# specify how to reconcile divergent branches" y no trae nada. Es literalmente
# el atasco del 21-sep. Aqui se dice que mezcle, que es lo unico que no pierde
# nada: los commits de esta maquina se quedan donde estan.
git pull --no-rebase --no-edit || {
  echo
  echo "El pull fallo. Si habla de un merge sin terminar:"
  echo "  cd $RAIZ && git commit --no-edit && bash deploy/arranca.sh"
  exit 1
}
echo "  codigo: $(git rev-parse --short HEAD)"

echo
echo "== 2/5 COMANDOS CORTOS =="
bash deploy/instalar_comandos.sh

echo
echo "== 3/5 QUE SE ACTUALICE SOLO =="
# A partir de aqui no hace falta volver a escribir nada: cada cambio llega
# solo en menos de diez minutos.
cp deploy/pump-bot-autoactualiza.service /etc/systemd/system/
cp deploy/pump-bot-autoactualiza.timer   /etc/systemd/system/
mkdir -p "$RAIZ/logs"
systemctl daemon-reload
systemctl enable --now pump-bot-autoactualiza.timer \
  && echo "  temporizador activo: se trae los cambios cada 10 min"

echo
echo "== 4/5 REINICIANDO EL BOT =="
systemctl restart pump-bot && echo "  reiniciado"

# Un momento para que arranque y escriba sus primeras lineas.
sleep 3

echo
echo "== 5/5 ESTADO =="
bot

echo
echo "== LLEGO EL CAMBIO? =="
# La ambiguedad que costo tres dias: el guion decia "reiniciado" aunque el
# pull no hubiera traido nada, y el bot seguia con la configuracion de hace
# tres dias pareciendo que estaba al dia.
#
# Y LA FALSA ALARMA QUE COSTO OTRAS CUATRO. Esto exigia que el commit del
# droplet fuera IDENTICO al de GitHub. Pero en cuanto un `git pull` deja un
# commit de merge en la maquina --lo que hacia el actualizador automatico al
# separarse el historial-- los dos numeros ya no coinciden NUNCA, aunque el
# codigo si estuviera entero. El droplet decia "AUN NO HA LLEGADO" para
# siempre y mandaba a reintentar un pull que ya no hacia nada.
#
# La pregunta de verdad no es "es el mismo commit" sino "tengo ya todo lo que
# hay en GitHub", y eso se contesta mirando si el commit de GitHub es
# antepasado del que corre aqui.
git fetch -q origin 2>/dev/null
local_c="$(git rev-parse --short HEAD 2>/dev/null)"
remoto_ref="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' \
              2>/dev/null || echo origin/main)"
remoto_c="$(git rev-parse --short "$remoto_ref" 2>/dev/null || echo "$local_c")"
if git merge-base --is-ancestor "$remoto_ref" HEAD 2>/dev/null; then
  if [ "$local_c" = "$remoto_c" ]; then
    echo "  OK: al dia con GitHub ($local_c)"
  else
    echo "  OK: tiene todo lo de GitHub ($remoto_c), mas commits de esta"
    echo "      maquina encima ($local_c). El codigo nuevo SI esta."
  fi
else
  echo "  OJO: falta codigo. El droplet esta en $local_c y GitHub en $remoto_c."
  # Ya NO se dice "reintenta git pull": era el consejo que fallaba en bucle.
  # El paso 1 aparta lo que bloquea el pull, asi que lo que hay que hacer es
  # volver a lanzar este mismo guion.
  echo "  Vuelve a lanzar:  bash $RAIZ/deploy/arranca.sh"
fi

echo
echo "== QUE CONFIGURACION ESTA CORRIENDO =="
journalctl -u pump-bot --since "-2 min" --no-pager 2>/dev/null \
  | grep "CONFIG ACTIVA" | tail -1 | sed 's/^/  /' \
  || echo "  (aun sin linea; espera un minuto y escribe:  bot)"

echo
echo "== QUE FUENTE ESTA COMPRANDO =="
# Lo que de verdad hay que ver: UNA linea, la de "curva_96". Desde el 24-sep
# es la unica fuente encendida ("solo esta estrategia"): Telegram se apago
# porque no miraba ningun filtro y se repartia las plazas con la curva, asi
# que ningun resultado del dia se podia atribuir a ninguna de las dos.
#
# Ninguna linea significa que el pull no entro y el bot sigue con la
# configuracion de antes. DOS O MAS lineas tambien es un aviso ahora: hay una
# estrategia encendida que nadie pidio.
journalctl -u pump-bot --since "-2 min" --no-pager 2>/dev/null \
  | grep -iE "acquisition ENABLED|TITULARES ACTIVA|CURVA AL" \
  | sed 's/^/  /' \
  || echo "  (aun sin lineas; espera un minuto y escribe:  bot)"
