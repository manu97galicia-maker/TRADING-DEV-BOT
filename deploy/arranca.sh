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

echo "== 1/4 TRAYENDO LO NUEVO =="
git pull --no-edit || {
  echo
  echo "El pull fallo. Si habla de un merge sin terminar:"
  echo "  cd $RAIZ && git commit --no-edit && bash deploy/arranca.sh"
  exit 1
}

echo
echo "== 2/4 COMANDOS CORTOS =="
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
echo "== QUE FUENTE ESTA COMPRANDO =="
# Lo que de verdad hay que ver: UNA sola fuente activa, y que sea la de
# presion. Mas de una linea aqui significa que el pull no entro.
journalctl -u pump-bot --since "-2 min" --no-pager 2>/dev/null \
  | grep -iE "acquisition ENABLED|PRESIÓN COMPRADORA ACTIVA" \
  | sed 's/^/  /' \
  || echo "  (aun sin lineas; espera un minuto y escribe:  bot)"
