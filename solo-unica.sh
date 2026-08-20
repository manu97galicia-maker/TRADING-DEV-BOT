#!/usr/bin/env bash
# Deja el droplet con UNA sola estrategia viva: `unica`.
#
# Para y DESHABILITA cualquier otro bot. Lo de deshabilitar importa tanto como
# lo de parar: un servicio "inactive pero enabled" vuelve solo en el siguiente
# reinicio del servidor, y entonces hay dos bots comprando con la misma wallet
# sin que nadie se entere. Asi aparecieron compras que no salian en ningun log.
#
#   bash /root/ai-bot/solo-unica.sh
#
set -uo pipefail
VIVA="unica"

echo "== posiciones abiertas =="
N=$(ls /root/ai-bot/open_position_*.json 2>/dev/null | wc -l)
if [ "$N" -ne 0 ]; then
  echo "  HAY $N POSICION(ES) ABIERTA(S). No toco nada."
  echo "  El bot no reanuda posiciones al reiniciar: se quedarian sin vigilancia."
  exit 1
fi
echo "  ninguna"

echo
echo "== apagando todo lo que no sea $VIVA =="
for f in /etc/systemd/system/*.service; do
  n=$(basename "$f" .service)
  case "$n" in
    dbus-*|multipath*|droplet-agent*|systemd-*|snap.*|sshd|syslog|sudo|vpc-peering) continue;;
    "$VIVA"|sniper-deck) continue;;
  esac
  a=$(systemctl is-active "$n" 2>/dev/null)
  e=$(systemctl is-enabled "$n" 2>/dev/null)
  if [ "$a" = "active" ] || [ "$e" = "enabled" ]; then
    systemctl stop "$n" 2>/dev/null
    systemctl disable "$n" 2>/dev/null >/dev/null
    echo "  apagado y deshabilitado: $n"
  fi
done

echo
echo "== limpiando restos =="
rm -f /root/ai-bot/reservas/claim_*.json 2>/dev/null
mkdir -p /root/ai-bot/posiciones_fantasma
mv /root/ai-bot/open_position_*.json /root/ai-bot/posiciones_fantasma/ 2>/dev/null
echo "  reservas y marcas viejas fuera"

echo
echo "== arrancando $VIVA =="
systemctl enable --now "$VIVA" >/dev/null 2>&1
systemctl restart "$VIVA"
sleep 20

echo
echo "== como queda =="
for f in /etc/systemd/system/*.service; do
  n=$(basename "$f" .service)
  case "$n" in dbus-*|multipath*|droplet-agent*|systemd-*|snap.*|sudo) continue;; esac
  a=$(systemctl is-active "$n" 2>/dev/null)
  e=$(systemctl is-enabled "$n" 2>/dev/null)
  [ "$a" = "active" ] || [ "$e" = "enabled" ] && printf "  %-16s %-9s %s\n" "$n" "$a" "$e"
done
echo
echo "  procesos de bot corriendo:"
ps -eo pid,cmd | grep -E "run_.*\.py" | grep -v grep | sed "s/^/    /"
