#!/usr/bin/env bash
# POR QUE NO COMPRA dev1-bloque2: servidor, Helius, pump.fun y el codigo.
#
# Pedido el 5-nov: "no compro nada, es culpa del server de Digital Ocean, de
# Helius, de alguna api, del codigo?". Mira las cuatro cosas y lo dice. No
# cambia nada, no compra nada y no ensena claves (todo lo que imprime pasa
# por `tapa`, que borra api-key/token de cualquier URL).
#
#   bash /root/porque.sh                 -> resumen general
#   bash /root/porque.sh <MINT>          -> y ademas que paso con esa moneda
set -u
RAIZ=/root/ai-bot
LOG="$RAIZ/logs/dev1_real.log"
M="${1:-}"
tapa() { sed -E 's/(api[-_]?key=|token=)[^&" ]+/\1***/Ig'; }
ok()  { echo "  OK    $*"; }
mal() { echo "  MAL   $*"; }
ojo() { echo "  OJO   $*"; }
n()   { local c; c=$(grep -c -E "$1" "$LOG" 2>/dev/null); echo "${c:-0}"; }

echo "=== 1. SERVIDOR (DigitalOcean) ==="
if pgrep -f "run_bot_process.*dev1-bloque2" >/dev/null; then ok "el bot esta vivo"
else mal "el bot NO esta corriendo"; fi
mem=$(free -m | awk '/^Mem:/{print $7}')
if [ "${mem:-0}" -ge 100 ]; then ok "memoria libre: ${mem} MB"; else mal "memoria libre: solo ${mem} MB"; fi
disco=$(df -P / | awk 'NR==2{gsub("%","",$5); print $5}')
if [ "${disco:-0}" -lt 95 ]; then ok "disco al ${disco}%"; else mal "disco casi lleno (${disco}%)"; fi
echo "  ..    carga del procesador: $(cut -d' ' -f1 /proc/loadavg) (1 nucleo: por encima de 2 va justo)"
if [ ! -f "$LOG" ]; then mal "no existe $LOG: el bot no ha arrancado nunca"; exit 1; fi
edad=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
if [ "$edad" -lt 120 ]; then ok "el log se ha movido hace ${edad} s"
else mal "el log lleva ${edad} s sin moverse: bot colgado o sin datos de Helius"; fi

echo "=== 2. HELIUS ==="
r429=$(n "429")
cortes=$(n "WebSocket connection closed")
auth=$(n "401|[Uu]nauthorized|[Ii]nvalid api")
tarde=$(n "no se vio nacer")
vistas=$(n "CURVA AL|no se vio nacer|compras en el primer bloque|DEV filtro|DEV OK")
if [ "$auth" -gt 0 ]; then mal "Helius rechaza la clave ($auth veces): revisa el .env o el plan"; else ok "Helius acepta la clave"; fi
if [ "$r429" -gt 20 ]; then mal "Helius ha frenado al bot $r429 veces (429): el plan se queda corto"
elif [ "$r429" -gt 0 ]; then ojo "Helius ha frenado al bot $r429 veces (429)"; else ok "ningun 429 de Helius"; fi
if [ "$cortes" -gt 5 ]; then mal "la conexion con Helius se ha cortado $cortes veces"
else ok "cortes de conexion: $cortes"; fi
echo "  ..    monedas con rastro en el log: $vistas; vistas tarde (sin su nacimiento): $tarde"

echo "=== 3. PUMP.FUN (historial del dev) ==="
cod=$(curl -s -o /dev/null -m 10 -w "%{http_code}" -H "User-Agent: Mozilla/5.0" \
  "https://frontend-api-v3.pump.fun/coins?creator=11111111111111111111111111111111&limit=1&offset=0")
if [ "$cod" = "200" ]; then ok "pump.fun contesta al droplet (codigo 200)"
else mal "pump.fun NO contesta bien al droplet (codigo $cod): el filtro del 1% no puede funcionar"; fi
devok=$(n "DEV OK"); devno=$(n "DEV filtro BLOCK"); cero=$(n "migr / 0 lanz"); desc=$(n "dev desconocido")
echo "  ..    devs que pasan: $devok | no llegan al 1%: $devno (de ellos con 0 lanzadas: $cero) | desconocidos: $desc"
# El respaldo por la cadena (4ed81dd, 5-nov): si pump.fun da 403, Helius.
respaldo=$(n "se usa la cadena")
cadena_mal=$(n "por la cadena no disponible")
# "No verified creation history" = el dev no ha creado ninguna moneda antes.
# No es una averia: es lo normal en pump.fun, y descartarlo es lo correcto.
nuevos=$(n "No verified creation history")
averias=$(( cadena_mal - nuevos ))
if [ "$respaldo" -eq 0 ] && [ "$cod" != "200" ]; then
  mal "el bot NO esta usando el respaldo por Helius: arranco con codigo viejo (reinicialo)"
elif [ "$averias" -gt 0 ]; then
  mal "el respaldo por Helius falla ($averias veces). Motivos:"
  grep -h "por la cadena no disponible" "$LOG" | grep -v "No verified creation history" \
    | sed -E 's/.*\((.*)\)$/\1/' | sort | uniq -c | sort -rn | head -4
else
  ok "el respaldo por Helius esta funcionando"
fi
if [ "$nuevos" -gt 0 ]; then
  echo "  ..    devs SIN ninguna moneda antes: $nuevos (lo normal; se descartan bien)"
fi
if [ "$devno" -gt 0 ] && [ "$cero" -eq "$devno" ] && [ "$devok" -eq 0 ]; then
  mal "TODOS los rechazados salen con 0 lanzadas: el dato del dev no esta llegando"
fi

echo "=== 4. EL EMBUDO: por que se descartan ==="
echo "  compradas: $(n 'Successfully bought')"
grep -h "Evitado" "$LOG" 2>/dev/null | sed -E 's/^.*Evitado [^:]*: //; s/[0-9][0-9.,]*/N/g' \
  | sort | uniq -c | sort -rn | head -8 | tapa

if [ -n "$M" ]; then
  echo "=== 5. LA MONEDA $M ==="
  if ! grep -q "$M" "$LOG" 2>/dev/null; then
    mal "el bot NO la ha visto en ningun momento."
    echo "        O no estaba corriendo entonces, o no le llego por Helius, o no es de pump.fun."
  else
    if grep "$M" "$LOG" | grep -q "no se vio nacer"; then
      mal "la vio TARDE: no vio su nacimiento, asi que no puede mirar su primer bloque."
    fi
    grep -h "$M" "$LOG" | tail -4 | tapa | cut -c1-200
    sym=$(grep -h -oE "[^ :(]+ \($M\)" "$LOG" | head -1 | cut -d' ' -f1)
    if [ -n "$sym" ] && [ "$sym" != "?" ]; then
      echo "  ..    decisiones con su nombre ($sym):"
      grep -h -E "(Evitado|DEV OK|DEV filtro BLOCK|Successfully bought) $sym[ :]" "$LOG" \
        | tail -4 | tapa | cut -c1-200
    fi
  fi
fi
