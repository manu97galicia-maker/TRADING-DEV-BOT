#!/usr/bin/env bash
# POR QUE NO COMPRA. Cuenta en que punto se cae cada moneda.
#
# El bot escribe el motivo exacto de cada descarte, pero estan mezclados con
# miles de lineas y desde el movil no hay forma de leerlos. Esto los agrupa y
# los cuenta: el embudo entero en media pantalla.
#
#   porque          ultimos 20 minutos
#   porque 120      ultimas 2 horas
set -uo pipefail

RAIZ=/root/ai-bot
MIN="${1:-20}"
cd "$RAIZ" || exit 1

log() { journalctl -u pump-bot --since "-${MIN} min" --no-pager 2>/dev/null; }
cuenta() { log | grep -ciE "$1" || true; }

echo "== ULTIMOS ${MIN} MINUTOS =="
echo

echo "1. ARRANCO LA FUENTE?"
linea="$(log | grep -E "PRESIÓN COMPRADORA ACTIVA|acquisition ENABLED" | tail -3)"
if [ -n "$linea" ]; then
  echo "$linea" | sed 's/.*]: /  /'
else
  echo "  NO. La fuente no arranco: el bot no esta escuchando nada."
  echo "  Mira:  journalctl -u pump-bot --since \"-${MIN} min\" | tail -30"
  exit 0
fi

echo
echo "2. EL EMBUDO (cuantas monedas se caen en cada paso)"
printf '  %-42s %s\n' "candidatas que mando la fuente" "$(cuenta 'PRESIÓN —')"
printf '  %-42s %s\n' "  descartadas por bundle"        "$(cuenta 'BUNDLE descartado')"
printf '  %-42s %s\n' "  sin haber visto nacer"         "$(cuenta 'no se vio nacer')"
printf '  %-42s %s\n' "COMPRAS INTENTADAS"              "$(cuenta 'Buying .* SOL worth')"
printf '  %-42s %s\n' "  compras que entraron"          "$(cuenta 'Buy successful|compra confirmada')"

echo
echo "3. QUE LAS PARA JUSTO ANTES DE COMPRAR"
# El motivo va escrito despues de "Evitado SIMBOLO: ", asi que se recorta el
# simbolo --que cambia en cada moneda-- y se agrupan por el motivo de verdad.
motivos="$(log | grep -oE "Evitado [^:]+: .*" \
  | sed -E 's/Evitado [^:]+: //; s/[0-9]+([.,][0-9]+)?/N/g' \
  | cut -c1-60 | sort | uniq -c | sort -rn | head -8)"
if [ -n "$motivos" ]; then
  echo "$motivos" | sed 's/^/  /'
else
  echo "  (ninguna llego tan lejos)"
fi

echo
echo "4. FRENOS DE CARTERA"
printf '  %-42s %s\n' "max posiciones abiertas" "$(cuenta 'Max posiciones abiertas|max posiciones')"
printf '  %-42s %s\n' "SOL insuficiente"        "$(cuenta 'SOL insuficiente')"
printf '  %-42s %s\n' "en PAUSA (modo estudio)" "$(cuenta 'PAUSA ·')"

echo
echo "== VEREDICTO =="
c=$(cuenta 'PRESIÓN —'); b=$(cuenta 'Buying .* SOL worth')
if [ "$c" -eq 0 ]; then
  echo "  La fuente no ha mandado NI UNA candidata. El corte esta demasiado"
  echo "  alto, o no llegan operaciones. Mira el punto 1."
elif [ "$b" -eq 0 ]; then
  echo "  La fuente manda candidatas ($c) pero NINGUNA llega a comprarse."
  echo "  El motivo esta en el punto 3: eso es lo que hay que aflojar."
else
  echo "  Esta comprando: $b intentos de $c candidatas."
fi
