#!/bin/bash
# Pasa la estrategia de senales a dinero real, paso a paso y preguntando.
#
# Existe porque copiar y pegar en una consola web falla a menudo: asi solo hay
# que teclear una linea corta en vez de seis bloques largos.
#
#   cd /root/ai-bot && git pull && bash arrancar_real.sh
#
# No hace nada irreversible sin confirmacion explicita.
set -u
cd /root/ai-bot 2>/dev/null || { echo "No encuentro /root/ai-bot"; exit 1; }

YAML=bots/bot-paper-ai-entry.yaml
PY=.venv/bin/python
export PYTHONPATH=/root/ai-bot/src

echo "==========================================================="
echo " ARRANCAR EN REAL - estrategia de senales"
echo "==========================================================="

# --- 1. Dependencias ---
echo
echo "[1/5] Sincronizando dependencias..."
uv sync 2>&1 | tail -2

# --- 2. Wallet ---
echo
echo "[2/5] Wallet"
CLAVE=$(grep -E "^SOLANA_PRIVATE_KEY=." .env 2>/dev/null | head -1)
if [ -z "$CLAVE" ]; then
  echo "  No hay clave en el .env."
  read -rsp "  Pega las 12/24 palabras de la frase secreta y pulsa Enter: " SEED
  echo
  export SEED
  "$PY" -c "
import os
from dashboard.wallet_import import derive_from_mnemonic
w = derive_from_mnemonic(os.environ['SEED'], 0)
with open('.env','a') as f: f.write('SOLANA_PRIVATE_KEY=' + w['private_key_b58'] + chr(10))
print('  Guardada la cuenta 0:', w['pubkey'])
" || { echo "  La frase no es valida. Abortado."; unset SEED; exit 1; }
  unset SEED
else
  echo "  Ya hay una clave configurada."
fi

echo
"$PY" learning-examples/comprobar_wallet.py || exit 1

# --- 3. Confirmacion ---
echo
echo "[3/5] Confirmacion"
echo "  A partir de aqui el bot comprara con SOL DE VERDAD."
echo "  Si el saldo de arriba es 0, manda SOL a esa direccion ANTES de seguir."
echo
read -rp "  Escribe REAL en mayusculas para continuar (cualquier otra cosa cancela): " OK
if [ "$OK" != "REAL" ]; then
  echo "  Cancelado. Todo sigue en papel."
  exit 0
fi

read -rp "  Cuantas compras reales como maximo? [3]: " N
N=${N:-3}
case "$N" in ''|*[!0-9]*) echo "  '$N' no es un numero. Abortado."; exit 1;; esac

# --- 4. Cambiar la configuracion ---
echo
echo "[4/5] Pasando a real..."
cp "$YAML" "$YAML.bak"
sed -i 's/^  paper_trade: true/  paper_trade: false/' "$YAML"
sed -i "s/^  max_real_buys: [0-9]\+/  max_real_buys: $N/" "$YAML"

"$PY" -c "
import sys, yaml
c = yaml.safe_load(open('$YAML'))
t = c['trade']
if c['filters']['paper_trade'] or t['max_real_buys'] < 1:
    print('  ERROR: la configuracion no quedo en real. Restaurando.'); sys.exit(1)
print(f\"  paper_trade: {c['filters']['paper_trade']}  |  max_real_buys: {t['max_real_buys']}\")
print(f\"  Exposicion maxima: {t['max_real_buys']} x {t['tope_compra_sol']} = \"
      f\"{t['max_real_buys'] * t['tope_compra_sol']:.2f} SOL\")
" || { mv "$YAML.bak" "$YAML"; echo "  Restaurado a papel."; exit 1; }
rm -f "$YAML.bak"

# --- 5. Arrancar ---
echo
echo "[5/5] Arrancando..."
systemctl restart paper-ai-entry
sleep 3
systemctl is-active --quiet paper-ai-entry \
  && echo "  Servicio ACTIVO." \
  || { echo "  El servicio NO arranco. Mira: journalctl -u paper-ai-entry -n 50"; exit 1; }

echo
echo "==========================================================="
echo " Para parar en seco:     systemctl stop paper-ai-entry"
echo " Para volver a papel:    bash volver_a_papel.sh"
echo "==========================================================="
echo
echo "Mostrando logs en vivo. Ctrl+C para dejar de mirar (el bot sigue)."
sleep 2
journalctl -u paper-ai-entry -f
