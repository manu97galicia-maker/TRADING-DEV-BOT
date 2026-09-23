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

# DESDE QUE ARRANCO EL BOT, no los ultimos ${MIN} minutos. Hay lineas --como la
# de que fuente esta escuchando-- que se escriben UNA SOLA VEZ, al arrancar.
# Buscarlas en una ventana de veinte minutos hacia que este diagnostico dijera
# "la fuente no arranco: el bot no esta escuchando nada" en un bot que llevaba
# CUATRO HORAS funcionando. Paso el 22-sep y mando a buscar una averia que no
# existia.
log_entero() { journalctl -u pump-bot --no-pager 2>/dev/null; }

cuenta() { log | grep -ciE "$1" || true; }

# CON QUE LINEA ANUNCIA UNA CANDIDATA CADA FUENTE. Tienen que estar todas las
# que puedan estar encendidas, o el embudo entero sale a cero: esto miraba solo
# "PRESIÓN —", de cuando esa era la fuente, y desde que se cambio a titulares
# daba 0 SIEMPRE, pasara lo que pasara. Cero por no mirar y cero por no haber
# se leen igual, y llevan a sitios opuestos.
CANDIDATAS='CURVA AL |TITULARES —|PRESIÓN —|Telegram call'

echo "== ULTIMOS ${MIN} MINUTOS =="
echo

echo "1. ARRANCO LA FUENTE?"
linea="$(log_entero | grep -E "acquisition ENABLED|TITULARES ACTIVA|CURVA AL|PRESIÓN COMPRADORA ACTIVA|Telegram watcher connecting" | tail -5)"
if [ -n "$linea" ]; then
  echo "$linea" | sed 's/.*]: /  /'
else
  echo "  NO. La fuente no arranco: el bot no esta escuchando nada."
  echo "  Mira:  journalctl -u pump-bot --no-pager | tail -30"
  exit 0
fi

echo
# ANTES QUE EL EMBUDO: SI LLEGAN DATOS.
#
# Un embudo lleno de ceros se lee como "la estrategia es muy exigente" y lleva
# a aflojar filtros. Pero si el nodo esta rechazando la conexion, esos ceros no
# significan nada: el bot no ha visto NI UNA moneda. Los dos dias del 21 y 22
# de septiembre se fueron en eso -- se movieron el corte de titulares, la edad
# y el anti-dip mientras el problema era un 429.
echo "1b. LLEGAN DATOS DE LA CADENA?"
_429="$(cuenta '429|se cayo el websocket')"
if [ "${_429:-0}" -gt 0 ]; then
  echo "  NO. El nodo rechaza la conexion: ${_429} avisos en ${MIN} min."
  echo "  429 = demasiadas peticiones. Es la cuota del nodo, no los filtros."
  echo "  Con el stream caido el embudo de abajo sale a cero SIEMPRE y no"
  echo "  significa nada. Mira el plan de Helius o baja node.max_rps."
  echo
else
  echo "  Si: sin rechazos del nodo en los ultimos ${MIN} min."
fi

echo
# 1c. EL PRECIO DE SOL, que desde el 23-sep PUEDE PARAR LAS COMPRAS.
#
# El importe es de 10 $ por operacion y los SOL salen de dividir por el precio
# de SOL. Mientras ese precio siga en el valor por defecto del codigo (91,24 $,
# de agosto) el bot NO COMPRA a proposito: gastar "10 $" a ese cambio con SOL
# a 220 serian 24 $ de verdad en cada compra.
#
# Se reintenta cada 10 segundos, asi que lo normal es que esto no salga nunca.
# Si sale y no se va, el bot no llega a DexScreener y hay una averia -- y sin
# esta seccion se veria igual que cualquier otro embudo a cero.
echo "1c. SABE EL BOT CUANTO VALE SOL?"
_sinsol="$(cuenta 'precio de SOL sigue en el valor por defecto|PRECIO DE SOL SIN')"
if [ "${_sinsol:-0}" -gt 0 ]; then
  echo "  NO. ${_sinsol} avisos en ${MIN} min: el precio de SOL no se ha"
  echo "  confirmado y las compras estan PARADAS a proposito (los importes en"
  echo "  dolares estarian 2,4 veces por encima de lo pedido)."
  echo "  Se reintenta solo cada 10 s. Si no se va, el bot no llega a"
  echo "  DexScreener: mira la salida a internet del droplet."
else
  _cambio="$(log | grep -oE 'IMPORTE: [0-9.]+ \$ a [0-9.]+ \$/SOL' | tail -1)"
  if [ -n "$_cambio" ]; then
    echo "  Si: $_cambio"
  else
    echo "  Sin avisos. (El cambio exacto sale en la primera compra.)"
  fi
fi

echo
echo "2. EL EMBUDO (cuantas monedas se caen en cada paso)"
printf '  %-42s %s\n' "candidatas que mando la fuente" "$(cuenta "$CANDIDATAS")"
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
printf '  %-42s %s\n' "sin precio de SOL confirmado" "$(cuenta 'precio de SOL sigue en el valor')"

echo
echo "5. EL PARTE DEL BOT (el embudo con sus propios numeros)"
# El mismo recuento que llega al movil cada 5 minutos, por si el aviso se
# perdio o Telegram no esta configurado.
# El parte de titulares sale por el LOG desde el 19-sep, precisamente porque
# los avisos de Telegram estan apagados a peticion: era el unico diagnostico
# que habia y se estaba calculando solo para tirarlo.
parte="$(log | grep -A 16 -E "Parte de titulares|Parte de la curva" | tail -18)"
if [ -z "$parte" ]; then
  parte="$(log | grep -A 8 "POR QUÉ NO COMPRA" | tail -12)"
fi
if [ -n "$parte" ]; then
  echo "$parte" | sed 's/.*]: /  /; s/<[^>]*>//g'
else
  echo "  (todavia no ha salido ninguno; sale cada 5 min)"
fi

echo
echo "== VEREDICTO =="
c=$(cuenta "$CANDIDATAS"); b=$(cuenta 'Buying .* SOL worth')
if [ "$c" -eq 0 ]; then
  echo "  La fuente no ha mandado NI UNA candidata. El corte esta demasiado"
  echo "  alto, o no llegan operaciones. Mira el punto 1."
elif [ "$b" -eq 0 ]; then
  echo "  La fuente manda candidatas ($c) pero NINGUNA llega a comprarse."
  echo "  El motivo esta en el punto 3: eso es lo que hay que aflojar."
else
  echo "  Esta comprando: $b intentos de $c candidatas."
fi
