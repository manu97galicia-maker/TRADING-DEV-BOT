#!/usr/bin/env bash
# Arranca TODO en el droplet: los 3 bots, el scout y el panel web.
#
#   cd /root/Definitivo_bot && git pull && bash arrancar.sh
#
# Es idempotente: se puede volver a lanzar tantas veces como haga falta. Para
# cada cosa dice OK o FALLO con el motivo, y al final resume que quedo vivo.
#
# No usa -e a proposito: si algo falla queremos llegar al resumen igualmente,
# no morir a la mitad dejando medio sistema arrancado sin saber cual mitad.
set -uo pipefail

BOT="${BOT:-/root/Definitivo_bot}"
PANEL="${PANEL:-/root/sniper-deck}"

ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFALLO\033[0m %s\n' "$1"; FALLOS=$((FALLOS+1)); }
warn() { printf '  \033[33mAVISO\033[0m %s\n' "$1"; }
paso() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
FALLOS=0

# --------------------------------------------------------------------------- #
paso "1/5  Codigo y dependencias del bot"
# --------------------------------------------------------------------------- #
cd "$BOT" || { bad "no existe $BOT"; exit 1; }

git pull --ff-only 2>&1 | sed 's/^/        /'
[ -d .venv ] || python3 -m venv .venv
# Encontrar el interprete en vez de suponerlo. Este repo puede estar montado con
# un .venv, con uv (hay uv.lock), o directamente sobre el python del sistema, y
# dar por hecho ./.venv/bin/python hacia fallar el arranque entero.
PY=""
for cand in "$PWD/.venv/bin/python" "$PWD/.venv/bin/python3" "$HOME/.venv/bin/python"; do
  [ -x "$cand" ] && { PY="$cand"; break; }
done
if [ -z "$PY" ] && command -v uv >/dev/null 2>&1 && [ -f uv.lock ]; then
  uv sync --quiet 2>/dev/null && [ -x "$PWD/.venv/bin/python" ] && PY="$PWD/.venv/bin/python"
fi
[ -z "$PY" ] && PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  bad "no encuentro ningun Python en este servidor"
  exit 1
fi
ok "python: $PY"

# Instalar dependencias. OJO: los entornos creados por uv NO traen pip, asi que
# "$PY -m pip" falla ahi aunque el entorno este perfecto. Se prueba uv primero.
DEPS="aiohttp PyYAML websockets solders solana base58"
INSTALADO=0
if command -v uv >/dev/null 2>&1 && [ -f uv.lock ]; then
  uv sync --quiet >/dev/null 2>&1 && INSTALADO=1
  VIRTUAL_ENV="$PWD/.venv" uv pip install --quiet $DEPS >/dev/null 2>&1 && INSTALADO=1
fi
if [ "$INSTALADO" = 0 ]; then
  "$PY" -m pip install --quiet $DEPS >/dev/null 2>&1 && INSTALADO=1
fi
# Lo que manda es si los modulos se pueden importar, no si el instalador dijo
# que si: en un entorno gestionado el install falla aunque no hiciera falta.
if "$PY" -c "import aiohttp, yaml, websockets" >/dev/null 2>&1; then
  ok "dependencias listas"
else
  bad "faltan dependencias. Prueba a mano:  uv pip install $DEPS"
fi

# Los ficheros que este arranque necesita. Si faltan, es que el push desde el PC
# no se hizo y todo lo demas seria trabajar sobre la version vieja.
FALTA=0
for f in scout.py scout.service bots/bot-sniper-velocidad.yaml bots/bot-sniper-nextbig-vol.yaml; do
  [ -f "$f" ] || { bad "falta $f"; FALTA=1; }
done
if [ "$FALTA" = 1 ]; then
  echo
  echo "  Los cambios no han llegado al droplet. En tu PC (Windows):"
  echo '     cd "C:\Users\ManuS\Desktop\Definitivo bot"'
  echo "     git push"
  echo "  y vuelve a lanzar esto."
  exit 1
fi
ok "ficheros nuevos presentes"

# --------------------------------------------------------------------------- #
paso "2/5  Que va a arrancar"
# --------------------------------------------------------------------------- #
"$PY" - <<'PY'
import yaml, pathlib
reales = []
for f in sorted(pathlib.Path("bots").glob("*.yaml")):
    c = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    if not c.get("enabled"):
        continue
    fl = c.get("filters", {}) or {}
    papel = fl.get("paper_trade")
    modo = "PAPEL" if papel else "DINERO REAL"
    print(f"        {f.stem:<28} {modo:<12} compra {c.get('trade',{}).get('buy_amount')} SOL")
    if not papel:
        reales.append(f.stem)
print()
print(f"        Tocando dinero real: {reales or 'ninguno'}")
PY

# --------------------------------------------------------------------------- #
paso "3/5  Bots"
# --------------------------------------------------------------------------- #
# Se mata primero el watchdog: si no, relanzaria el bot en cuanto lo matemos.
pkill -f "watchdog.py" 2>/dev/null && sleep 1
pkill -f "bot_runner"  2>/dev/null && sleep 2
pkill -9 -f "bot_runner" 2>/dev/null
rm -f .bot.pid 2>/dev/null

nohup "$PY" watchdog.py > logs/watchdog.out 2>&1 &
sleep 8

VIVOS=$(pgrep -fc "bot_runner" 2>/dev/null || echo 0)
if [ "$VIVOS" -gt 0 ]; then
  ok "$VIVOS proceso(s) de bot corriendo"
else
  bad "el bot no arranco. Mira el motivo con:  cat $BOT/logs/watchdog.out"
fi

# --------------------------------------------------------------------------- #
paso "4/5  Scout (vigilante 24/7)"
# --------------------------------------------------------------------------- #
install -m 644 scout.service /etc/systemd/system/scout.service
sed -i "s#^ExecStart=.*#ExecStart=$PY scout.py#" /etc/systemd/system/scout.service
sed -i "s#/root/Definitivo_bot#${BOT}#g" /etc/systemd/system/scout.service
systemctl daemon-reload
systemctl enable scout >/dev/null 2>&1
systemctl restart scout
sleep 4
if [ "$(systemctl is-active scout)" = "active" ]; then
  ok "scout activo (informe cada 4h por Telegram)"
else
  bad "scout no arranca. Mira: journalctl -u scout -n 30 --no-pager"
fi

# --------------------------------------------------------------------------- #
paso "5/5  Panel web"
# --------------------------------------------------------------------------- #
if [ -d "$PANEL" ]; then
  cd "$PANEL"
  git pull --ff-only >/dev/null 2>&1
  # Vercel proxya desde fuera: el panel tiene que escuchar en 0.0.0.0.
  if grep -q '^PANEL_HOST=' .env 2>/dev/null; then
    sed -i 's/^PANEL_HOST=.*/PANEL_HOST=0.0.0.0/' .env
  else
    echo 'PANEL_HOST=0.0.0.0' >> .env
  fi
  # Sin usuarios no hay login posible: es lo que daba "contrasena incorrecta".
  if grep -q '^PANEL_SEED_PASSWORD=.\+' .env 2>/dev/null; then
    "$PY" -m server.users sync >/dev/null 2>&1 && ok "usuarios del panel listos"
  else
    warn "PANEL_SEED_PASSWORD vacio en $PANEL/.env -> el login seguira fallando"
  fi
  systemctl restart sniper-deck 2>/dev/null
  command -v ufw >/dev/null && ufw allow 8080/tcp >/dev/null 2>&1
  sleep 3
  if curl -fsS --max-time 8 http://127.0.0.1:8080/api/ping >/dev/null 2>&1; then
    ok "panel respondiendo en el puerto 8080"
  else
    bad "panel no responde. Mira: journalctl -u sniper-deck -n 30 --no-pager"
  fi
  cd "$BOT"
else
  warn "no encuentro el panel en $PANEL (se salta)"
fi

# --------------------------------------------------------------------------- #
printf '\n\033[1m========================= RESUMEN =========================\033[0m\n\n'
# --------------------------------------------------------------------------- #
printf '  bots corriendo : %s\n' "$(pgrep -fc 'bot_runner' 2>/dev/null || echo 0)"
printf '  scout          : %s\n' "$(systemctl is-active scout 2>/dev/null)"
printf '  panel          : %s\n' "$(systemctl is-active sniper-deck 2>/dev/null)"
IP=$(curl -fsS --max-time 8 https://api.ipify.org 2>/dev/null || echo '?')
printf '  memoria libre  : %s\n' "$(free -m | awk '/^Mem:/{print $7" MB de "$2" MB"}')"
echo
if [ "$FALLOS" = 0 ]; then
  cat <<EOF
  Todo arriba.

    Web:      https://web-app-datos-franc.vercel.app
    Telegram: te llega el informe del scout dentro de 4h
              (y avisos al momento si el saldo baja del suelo)

  Para mirar en vivo:
    journalctl -u scout -f          <- el vigilante
    cat $BOT/logs/watchdog.out      <- el arranque de los bots

EOF
else
  cat <<EOF
  Quedaron $FALLOS cosas sin arrancar (mira los FALLO de arriba).

  Lo mas habitual:
    - falta hacer git push desde el PC
    - PANEL_SEED_PASSWORD vacio en $PANEL/.env
    - el droplet es de 1GB: si algo murio, mira si fue por memoria con
        dmesg | grep -i "killed process"

EOF
fi
echo "  El panel escucha sin cifrar en $IP:8080. Cuando tengas un rato,"
echo "  cierralo con:  cd $PANEL && API_HOST=<tu-subdominio> bash deploy/listo.sh"
echo
exit "$FALLOS"
