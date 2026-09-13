#!/bin/bash
# Devuelve la estrategia a papel y reinicia. Sin preguntas: es la salida rapida.
set -u
cd /root/ai-bot 2>/dev/null || { echo "No encuentro /root/ai-bot"; exit 1; }
sed -i 's/^  paper_trade: false/  paper_trade: true/' bots/bot-paper-ai-entry.yaml
sed -i 's/^  max_real_buys: [0-9]\+/  max_real_buys: 0/' bots/bot-paper-ai-entry.yaml
systemctl restart pump-bot
grep -nE "^  (paper_trade|max_real_buys):" bots/bot-paper-ai-entry.yaml
echo "De vuelta en papel. El bot ya no puede gastar SOL."
