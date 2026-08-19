#!/usr/bin/env bash
# Lanza la tanda de prueba de TRES compras reales. Pensado para escribirse
# desde el movil, en la consola web de DigitalOcean:
#
#     cd /root/ai-bot && bash tanda.sh
#
# Comprueba antes de tocar nada y ABORTA si algo no cuadra. Nunca reinicia a
# ciegas: el bot NO reanuda posiciones al reiniciar (detecta la marca, avisa de
# "bolsa huerfana" y la deja sin vigilancia), asi que reiniciar con una posicion
# abierta es abandonarla. Paso el 11-ago con Parmesan y wCAT.
#
# No usa -e a proposito: queremos llegar al resumen aunque algo falle, no morir
# a la mitad dejando medio sistema arrancado sin saber cual mitad.
set -uo pipefail

RAMA="${RAMA:-claude/metricas-cuando-que-ppdrnl}"
SERVICIOS="definitiva premium dev10"
CFGS="bots/bot-sniper-definitiva.yaml bots/bot-sniper-premium.yaml bots/bot-sniper-dev10.yaml"

rojo()  { printf '\033[31m%s\033[0m\n' "$*"; }
verde() { printf '\033[32m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }

echo
echo "=== TANDA DE PRUEBA: 3 compras reales de 0,06 SOL ==="
echo

# --- 1. Estamos en la carpeta correcta? --------------------------------------
# Hay DOS copias del repo en el droplet: los servicios de los bots corren desde
# /root/ai-bot y el scout desde /root/Definitivo_bot. Hacer el pull en la que no
# es deja la tanda corriendo con la configuracion VIEJA sin avisar de nada.
if [ ! -f "run_dev10.py" ] || [ ! -d "bots" ]; then
  rojo "ABORTA: esto no parece la carpeta de los bots."
  info "Estas en: $(pwd)"
  info "Prueba:   cd /root/ai-bot && bash tanda.sh"
  exit 1
fi

ESPERADA=$(systemctl show -p WorkingDirectory --value dev10 2>/dev/null | sed 's/^WorkingDirectory=//')
if [ -n "$ESPERADA" ] && [ "$ESPERADA" != "$(pwd)" ]; then
  rojo "ABORTA: el servicio dev10 corre desde otra carpeta."
  info "servicio: $ESPERADA"
  info "tu:       $(pwd)"
  exit 1
fi
verde "OK  carpeta correcta: $(pwd)"

# --- 2. Hay posiciones abiertas? ---------------------------------------------
ABIERTAS=$(ls open_position*.json 2>/dev/null | wc -l)
if [ "$ABIERTAS" -gt 0 ]; then
  rojo "ABORTA: hay $ABIERTAS posicion(es) abierta(s). Reiniciar las abandona."
  for f in open_position*.json; do
    info "$(python3 -c "
import json,sys
try:
    r=json.load(open('$f'))
    print(f\"{r.get('symbol','?'):10} {r.get('mint','')[:12]}...  bot={r.get('suffix','?')}\")
except Exception: print('$f (ilegible)')
" 2>/dev/null)"
  done
  echo
  info "Vendelas desde el panel de Telegram (botones 100%) y vuelve a lanzar esto."
  exit 1
fi
verde "OK  no hay posiciones abiertas"

# --- 3. Traer la configuracion de la tanda -----------------------------------
git fetch origin "$RAMA" --quiet 2>/dev/null || { rojo "ABORTA: git fetch fallo"; exit 1; }
git checkout "$RAMA" --quiet 2>/dev/null
if ! git pull origin "$RAMA" --quiet; then
  rojo "ABORTA: git pull fallo (cambios locales sin guardar?)"
  info "Mira:  git status"
  exit 1
fi
verde "OK  rama $RAMA al dia ($(git rev-parse --short HEAD))"

# --- 4. La configuracion que va a cargar es la de la tanda? ------------------
# Comprobar el fichero y no fiarse del pull: si el pull trajo otra cosa, o la
# carpeta tenia cambios locales, aqui es donde se ve. Mejor no arrancar que
# arrancar creyendo que corre una cosa cuando corre otra.
MAL=0
for c in $CFGS; do
  LECTURA=$(python3 -c "
import yaml
d = yaml.safe_load(open('$c')); t = d['trade']
print(t.get('max_real_buys'), t.get('tope_compra_sol'), t.get('buy_amount'),
      t.get('coordinar_mints'), t.get('venta_muerta_repetir_seg'),
      d['filters'].get('max_tx_por_compra'))
" 2>/dev/null)
  read -r NBUY TOPE IMPORTE COORD MUERTA TXC <<<"$LECTURA"
  if [ "$NBUY" != "1" ] || [ "$TOPE" != "0.06" ] || [ "$COORD" != "True" ]; then
    rojo "MAL $c -> compras=$NBUY tope=$TOPE coord=$COORD"
    MAL=1
  else
    info "$(basename "$c" .yaml): 1 compra x $IMPORTE SOL · tope $TOPE · tx/compra $TXC · muerta cada ${MUERTA}s"
  fi
done
[ "$MAL" -eq 1 ] && { rojo "ABORTA: la configuracion no es la de la tanda."; exit 1; }
verde "OK  configuracion de tanda verificada -> 3 compras, 0,18 SOL en total"

# --- 5. Arrancar -------------------------------------------------------------
echo
systemctl restart $SERVICIOS || { rojo "ABORTA: el restart fallo"; exit 1; }
sleep 3
for s in $SERVICIOS; do
  EST=$(systemctl is-active "$s" 2>/dev/null)
  [ "$EST" = "active" ] && verde "OK  $s activo" || rojo "FALLO  $s -> $EST"
done

echo
echo "=== EN MARCHA. Para ver el log: ==="
echo "  journalctl -u definitiva -u premium -u dev10 -f"
echo
echo "Cada bot hace UNA compra y para. Vender: botones del panel de Telegram."
echo "Al cerrar, mira solo la linea 'P&L REAL' -- el teorico se equivoca de signo."
