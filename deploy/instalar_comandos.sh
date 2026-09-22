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
_poner porque porque.sh

# `bot` no envuelve un guion: resume de un vistazo el estado de todo, que es
# lo que de verdad se quiere saber al entrar.
# `bot` se escribe con un heredoc CITADO ('BOT') y la ruta se mete despues
# con sed. Con el heredoc sin citar habia que escapar cada $ y cada backtick
# del guion de dentro, y una escapada mal puesta generaba un fichero roto que
# solo se veia al ejecutarlo. Asi lo de dentro se lee tal cual se va a correr.
cat > "$DESTINO/bot" <<'BOT'
#!/usr/bin/env bash
set -uo pipefail
cd __RAIZ__
echo "== BOT =="
if systemctl is-active pump-bot >/dev/null 2>&1; then
  echo "  pump-bot: ENCENDIDO"
else
  echo "  pump-bot: PARADO"
fi
if grep -q "pausar_compras: true" bots/bot-paper-ai-entry.yaml; then
  echo "  compras:  EN PAUSA (modo estudio)"
else
  echo "  compras:  ACTIVAS (configurado para comprar de verdad)"
fi

# EL COMMIT Y SI ESTA AL DIA. Sin esto no habia forma de saber si el droplet
# tiene los ultimos arreglos: estuvo tres dias con codigo viejo pareciendo
# que funcionaba.
#
# OJO CON COMPARAR COMMITS A PELO, que es lo que hacia y costo cuatro vueltas
# desde el movil el 21-sep. En cuanto una traida deja un commit de merge en la
# maquina, el commit de aqui ya no es igual al de GitHub NUNCA, aunque el
# codigo este entero: esto decia "AUN NO HA LLEGADO" para siempre y mandaba a
# reintentar una traida que ya no hacia nada. La pregunta es "tengo todo lo de
# GitHub", no "es el mismo numero", y se contesta por antepasados.
#
# (Aqui no se nombra el comando de traer a proposito: un test comprueba que
# este instalador no lo lleva escrito ni en los comentarios, porque no le
# toca traer nada.)
git fetch -q origin 2>/dev/null || true
_l="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
_ref="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null \
        || echo origin/main)"
_r="$(git rev-parse --short "$_ref" 2>/dev/null || echo "$_l")"
if [ "$_l" = "$_r" ]; then
  echo "  codigo:   $_l (al dia)"
elif git merge-base --is-ancestor "$_ref" HEAD 2>/dev/null; then
  echo "  codigo:   $_l (al dia; tiene todo lo de GitHub $_r y algo propio)"
else
  echo "  codigo:   $_l  ·  GitHub en $_r -- FALTA CODIGO"
  echo "            arreglalo con:  bash /root/ai-bot/deploy/arranca.sh"
fi

# HA COMPRADO O NO. Es la pregunta de verdad, y hasta ahora no la contestaba
# nadie: "ACTIVAS" solo dice como esta CONFIGURADO, no lo que ha hecho.
echo
echo "== HA COMPRADO? =="
_c="$(journalctl -u pump-bot --since '-6 hours' --no-pager 2>/dev/null | grep -c 'Buying .* SOL worth' || true)"
echo "  intentos de compra (6 h): ${_c:-0}"
if [ "${_c:-0}" -eq 0 ]; then
  # LAS TRES FUENTES QUE PUEDEN MANDAR CANDIDATAS, no solo una. Esto buscaba
  # unicamente "PRESIÓN —", de cuando esa era la fuente encendida: desde que
  # se cambio a titulares el contador salia 0 SIEMPRE, pasara lo que pasara.
  # El 21-sep eso hizo creer que la fuente no encontraba nada.
  _p="$(journalctl -u pump-bot --since '-30 min' --no-pager 2>/dev/null | grep -cE 'TITULARES —|PRESIÓN —|Telegram call' || true)"
  echo "  candidatas de la fuente (30 min): ${_p:-0}"
  echo "  -> para saber que las para, escribe:  porque"
fi

echo
echo "== MEDICIONES =="
if systemctl is-active pump-bot-cazador >/dev/null 2>&1; then
  echo "  cazador:  ENCENDIDO"
else
  echo "  cazador:  apagado"
fi
if [ -f trades/senales.jsonl ]; then
  echo "  monedas apuntadas: $(wc -l < trades/senales.jsonl)"
fi
if [ -f devs_medidos.json ]; then
  echo "  devs medidos: si"
else
  echo "  devs medidos: no"
fi
echo
echo "Comandos:  bot  ·  porque  ·  cazador ver  ·  devs ver"
BOT
sed -i "s|__RAIZ__|$RAIZ|" "$DESTINO/bot"
chmod +x "$DESTINO/bot"
echo "  bot     ->  resumen de estado"

echo
echo "Listo. Pruebalo ahora mismo escribiendo:  bot"
