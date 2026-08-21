#!/bin/bash
# Despliega al droplet SIN abandonar posiciones abiertas.
#
# POR QUE EXISTE. El bot NO reanuda posiciones al reiniciar: al arrancar detecta
# la marca de una posicion viva, avisa de "bolsa huerfana", la deja para vender a
# mano y limpia la marca. Es decir, quien reinicia con una posicion abierta la
# abandona -- deja de vigilarse el take-profit, el stop-loss y el anti-rug.
#
# Paso el 11-ago: se reinicio para aplicar un stop-loss nuevo con dos posiciones
# abiertas (Parmesan y wCAT) y las dos se quedaron sin vigilancia. Aplicar una
# proteccion no puede costar las posiciones que se estaban protegiendo.
#
# Este script comprueba primero y espera. Nunca reinicia a ciegas.
#
# Uso:
#   bash desplegar.sh                 # espera a que no haya posiciones (max 15 min)
#   bash desplegar.sh --ya            # reinicia igual, avisando de lo que abandona
#   bash desplegar.sh --solo-subir    # sube los ficheros y NO reinicia

set -u
SERVIDOR="root@159.89.19.12"
CLAVE="$HOME/.ssh/id_droplet_bot"
REMOTO="/root/ai-bot"
SERVICIO="devmig"
ESPERA_MAX=900   # 15 minutos

cd "$(dirname "$0")" || exit 1

posiciones() {
  ssh -i "$CLAVE" -o BatchMode=yes "$SERVIDOR" \
    "ls $REMOTO/open_position_*.json 2>/dev/null | wc -l"
}

detalle() {
  ssh -i "$CLAVE" -o BatchMode=yes "$SERVIDOR" \
    "cat $REMOTO/open_position_*.json 2>/dev/null"
}

echo "== Subiendo ficheros =="
tar czf - src/ bots/ run_*.py *.service 2>/dev/null \
  | ssh -i "$CLAVE" -o BatchMode=yes "$SERVIDOR" "cd $REMOTO && tar xzf -" \
  || { echo "FALLO al subir"; exit 1; }
echo "   subidos"

ssh -i "$CLAVE" -o BatchMode=yes "$SERVIDOR" \
  "cd $REMOTO && .venv/bin/python -m py_compile src/trading/universal_trader.py src/bot_runner.py" \
  || { echo "FALLO: el codigo no compila. NO se reinicia."; exit 1; }
echo "   compila"

if [ "${1:-}" = "--solo-subir" ]; then
  echo "== Subido sin reiniciar. Los cambios NO estan activos hasta reiniciar. =="
  exit 0
fi

if [ "${1:-}" = "--ya" ]; then
  N=$(posiciones)
  [ "$N" -gt 0 ] && { echo "AVISO: se abandonan $N posiciones abiertas:"; detalle; }
else
  echo "== Esperando a que no haya posiciones abiertas =="
  ESPERADO=0
  while true; do
    N=$(posiciones)
    [ "$N" -eq 0 ] && { echo "   sin posiciones abiertas"; break; }
    if [ "$ESPERADO" -ge "$ESPERA_MAX" ]; then
      echo
      echo "   Siguen abiertas $N posiciones tras $((ESPERA_MAX/60)) minutos:"
      detalle
      echo
      echo "   NO se reinicia. Opciones:"
      echo "     - esperar a que cierren y volver a lanzar esto"
      echo "     - bash desplegar.sh --ya   (abandona esas posiciones a sabiendas)"
      exit 2
    fi
    [ $((ESPERADO % 60)) -eq 0 ] && echo "   $N abierta(s), esperando... (${ESPERADO}s)"
    sleep 15
    ESPERADO=$((ESPERADO + 15))
  done
fi

echo "== Reiniciando $SERVICIO =="
ssh -i "$CLAVE" -o BatchMode=yes "$SERVIDOR" "systemctl restart $SERVICIO.service"
sleep 12
ssh -i "$CLAVE" -o BatchMode=yes "$SERVIDOR" "
  echo \"   estado: \$(systemctl is-active $SERVICIO.service) · PID \$(systemctl show $SERVICIO.service -p MainPID --value)\"
  echo \"   errores: \$(journalctl -u $SERVICIO.service -p err --no-pager | grep -cv 'No entries')\"
"
