#!/usr/bin/env bash
# Se trae los cambios de GitHub y reinicia el bot SOLO si algo cambio.
#
# POR QUE EXISTE. Desde el movil, con un SSH que se reconecta solo, escribir
# un comando es fragil: cinco intentos seguidos del mismo no llegaron a
# ejecutarse (una errata, una sesion que murio a mitad, la carpeta equivocada,
# un instalador que nunca corrio, y una pantalla en blanco). Mientras tanto el
# bot seguia con la configuracion vieja y cada cambio se quedaba en GitHub.
# Esto lo trae solo, cada diez minutos, sin que nadie escriba nada.
#
# LO QUE NO HACE, y es lo importante: nada que pueda romper el droplet. Solo
# `git pull`. Ni `add`, ni `reset --hard`, ni `checkout`, ni `rm` -- en ese
# directorio viven el .env con la clave de la wallet y las sesiones de
# Telegram, y un automatismo con permiso para borrar es un accidente
# esperando a las 3 de la manana.
set -uo pipefail

RAIZ=/root/ai-bot
SERVICIO=pump-bot
cd "$RAIZ" || { echo "No existe $RAIZ"; exit 1; }

antes="$(git rev-parse HEAD 2>/dev/null)" || { echo "No es un repo git"; exit 1; }

# --ff-only, Y ES EL ARREGLO DE LA RAIZ. Con `git pull` a secas, en cuanto el
# historial se separa esto CREA un commit de merge en el droplet -- y desde ese
# momento la maquina ya no vuelve a coincidir con GitHub nunca, asi que todos
# los carteles decian "AUN NO HA LLEGADO" en bucle aunque el codigo estuviera.
# El 21-sep eso costo cuatro vueltas desde el movil con el bot corriendo codigo
# viejo. Un automatismo cada diez minutos no debe inventar historia: o avanza
# en linea recta, o se calla y lo arregla una persona con `arranca.sh`.
if ! salida="$(git pull --ff-only 2>&1)"; then
  # NO SE REINICIA. Un bot corriendo con codigo viejo es mejor que un bot
  # reiniciado a medias: el viejo al menos vigila las posiciones abiertas.
  echo "PULL FALLIDO, no se reinicia nada:"
  echo "$salida"
  echo "Si dice 'Not possible to fast-forward' o habla de cambios locales:"
  echo "  bash $RAIZ/deploy/arranca.sh"
  exit 1
fi

despues="$(git rev-parse HEAD)"
if [ "$antes" = "$despues" ]; then
  echo "Sin cambios ($(git rev-parse --short HEAD))."
  exit 0
fi

# SOLO SE REINICIA SI HA CAMBIADO ALGO. Reiniciar por reiniciar corta
# posiciones vivas cada diez minutos sin razon: se retoman, pero cada reinicio
# es una ventana sin vigilancia y no hay que abrirla gratis.
asunto="$(git log -1 --pretty=%s)"
echo "Actualizado: ${antes:0:7} -> ${despues:0:7}  ($asunto)"

# LOS COMANDOS CORTOS TAMBIEN SE REFRESCAN. Sin esto el auto-actualizador
# traia el codigo pero no los comandos nuevos: `porque` llego al droplet el
# 18-sep y siguio dando "command not found" porque nadie volvio a correr el
# instalador. Es idempotente, asi que repetirlo no cuesta nada.
bash deploy/instalar_comandos.sh >/dev/null 2>&1 \
  && echo "comandos cortos al dia"

systemctl restart "$SERVICIO" && echo "$SERVICIO reiniciado"

# Avisar por Telegram, que es donde Manu mira. Si falla, da igual: lo
# importante (traer el cambio y reiniciar) ya esta hecho.
/root/.local/bin/uv run python - <<PY 2>/dev/null || true
import sys
sys.path.insert(0, "src")
from monitoring.notifier import notify

notify(
    "🔄 <b>BOT ACTUALIZADO</b>\n"
    "${antes:0:7} → ${despues:0:7}\n"
    "$asunto\n"
    "Reiniciado y funcionando."
)
PY
