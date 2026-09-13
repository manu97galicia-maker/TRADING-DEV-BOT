#!/bin/bash
# Diagnostico de una sola pasada: por que el bot no esta comprando.
#
#   cd /root/ai-bot && bash revisar.sh
#
# Solo lee. No toca configuracion, no reinicia nada, no gasta.
set -u
cd /root/ai-bot 2>/dev/null || { echo "No encuentro /root/ai-bot"; exit 1; }
DESDE="${1:-2 hours ago}"

echo "=========================================================="
echo " REVISION  ($DESDE)"
echo "=========================================================="

echo
echo "--- 1. SERVICIO ---"
systemctl is-active pump-bot
systemctl show pump-bot -p ActiveEnterTimestamp --value

echo
echo "--- 2. CONFIGURACION ---"
grep -nE "^enabled:|^  (paper_trade|max_real_buys|tope_compra_sol|min_reserve_sol):" \
  bots/bot-paper-ai-entry.yaml
grep -nE "^    (incluir_migradas|enabled|poll_interval|max_age_minutes):" \
  bots/bot-paper-ai-entry.yaml

echo
echo "--- 3. WALLET ---"
PYTHONPATH=src .venv/bin/python learning-examples/comprobar_wallet.py 2>&1 | tail -4

echo
echo "--- 4. CUANTAS SENALES HA VISTO ---"
L=$(journalctl -u pump-bot --since "$DESDE" --no-pager 2>/dev/null)
printf "  senales frescas   : %s\n" "$(echo "$L" | grep -c 'Fresh DexPaid')"
printf "  descartadas migrad: %s\n" "$(echo "$L" | grep -ci 'curve complete')"
printf "  compras intentadas: %s\n" "$(echo "$L" | grep -c 'Buying ')"
printf "  compras OK        : %s\n" "$(echo "$L" | grep -ci 'buy successful\|Sold ')"
printf "  errores           : %s\n" "$(echo "$L" | grep -ci 'error\|traceback')"

echo
echo "--- 5. POR QUE NO COMPRO (ultimas 15 razones) ---"
echo "$L" | grep -iE "se omite|skip|omitida|no se compra|Evitado|rechaz" \
  | sed 's/.*python\[[0-9]*\]: //' | tail -15

echo
echo "--- 6. ULTIMOS ERRORES (5) ---"
echo "$L" | grep -iE "error|exception" | sed 's/.*python\[[0-9]*\]: //' | tail -5

echo
echo "--- 7. ULTIMAS 10 LINEAS ---"
echo "$L" | sed 's/.*python\[[0-9]*\]: //' | tail -10

echo
echo "=========================================================="
