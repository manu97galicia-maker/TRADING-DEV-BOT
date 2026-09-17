#!/usr/bin/env bash
# Deja `devs`, `cazador` y `bot` disponibles DESDE CUALQUIER CARPETA.
#
# POR QUE. Las herramientas se invocan como `bash deploy/devs.sh ver`, que
# solo funciona estando dentro de /root/ai-bot. Y desde el movil no se esta:
# cada vez que se cae el SSH --que es a menudo-- la sesion vuelve a ~ y el
# comando falla con "No such file or directory". Ha pasado tres veces. Con
# esto da igual donde estes y ademas se escribe la mitad, que en un teclado
# de telefono es donde salen las erratas.
#
# Instalar (una vez):  bash /root/ai-bot/deploy/instalar_comandos.sh
# Luego, desde donde sea:
#   devs ver          cazador ver          bot
set -euo pipefail

RAIZ=/root/ai-bot
DESTINO=/usr/local/bin

_poner() {
  local nombre="$1" guion="$2"
  cat > "$DESTINO/$nombre" <<EOF
#!/usr/bin/env bash
exec bash "$RAIZ/deploy/$guion" "\$@"
EOF
  chmod +x "$DESTINO/$nombre"
  echo "  $nombre  ->  deploy/$guion"
}

echo "Instalando comandos globales:"
_poner devs devs.sh
_poner cazador cazador.sh

# `bot` no envuelve un guion: resume de un vistazo el estado de todo, que es
# lo que de verdad se quiere saber al entrar.
cat > "$DESTINO/bot" <<EOF
#!/usr/bin/env bash
set -uo pipefail
cd "$RAIZ"
echo "== BOT =="
systemctl is-active pump-bot >/dev/null 2>&1 \\
  && echo "  pump-bot: ENCENDIDO" || echo "  pump-bot: PARADO"
grep -q "pausar_compras: true" bots/bot-paper-ai-entry.yaml \\
  && echo "  compras:  EN PAUSA (modo estudio)" \\
  || echo "  compras:  ACTIVAS -- esta comprando de verdad"
echo
echo "== MEDICIONES =="
systemctl is-active pump-bot-cazador >/dev/null 2>&1 \\
  && echo "  cazador:  ENCENDIDO" || echo "  cazador:  apagado"
if [ -f trades/senales.jsonl ]; then
  echo "  monedas apuntadas: \$(wc -l < trades/senales.jsonl)"
fi
[ -f devs_medidos.json ] && echo "  devs medidos: si" || echo "  devs medidos: no"
echo
echo "Comandos:  devs ver  ·  cazador ver  ·  bot"
EOF
chmod +x "$DESTINO/bot"
echo "  bot     ->  resumen de estado"

echo
echo "Listo. Pruebalo ahora mismo escribiendo:  bot"
