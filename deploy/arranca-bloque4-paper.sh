#!/usr/bin/env bash
# Arranca el experimento de 4 compras del primer bloque, siempre en PAPER.
# En una terminal pregunta la duración; Enter conserva el valor seguro de 1 h.
set -euo pipefail

SERVICIO="pump-bloque4-paper.service"
MINUTOS_PREDETERMINADOS=60

if [[ -t 0 ]]; then
  read -r -p \
    "¿Cuántos minutos debe estar activo? [${MINUTOS_PREDETERMINADOS}]: " \
    minutos
else
  minutos="${MINUTOS_PREDETERMINADOS}"
  echo "Sin terminal interactiva: duración predeterminada de ${minutos} min."
fi

minutos="${minutos:-$MINUTOS_PREDETERMINADOS}"
if [[ ! "$minutos" =~ ^[0-9]+$ ]] || (( minutos < 1 || minutos > 1440 )); then
  echo "Duración inválida: usa un número entero entre 1 y 1440 minutos." >&2
  exit 2
fi

segundos=$((minutos * 60))

# No se toca ningún servicio real. Esta propiedad solo vive hasta el próximo
# reinicio del sistema; el fichero de unidad conserva 60 min como respaldo.
systemctl set-property --runtime "$SERVICIO" RuntimeMaxSec="${segundos}s"
systemctl restart "$SERVICIO"

if [[ "$(systemctl is-active "$SERVICIO")" != "active" ]]; then
  echo "El servicio paper no quedó activo." >&2
  systemctl status "$SERVICIO" --no-pager -l || true
  exit 1
fi

echo "PAPER activo durante un máximo de ${minutos} min."
echo "Se detendrá automáticamente al finalizar ese tiempo."
systemctl show "$SERVICIO" \
  -p ActiveEnterTimestamp -p RuntimeMaxUSec -p MainPID --no-pager
