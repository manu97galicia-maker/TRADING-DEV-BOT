# Vigía: avisar por Telegram cuando el bot se para

El 12-sep el bot llevaba **ocho días parado** y no lo supimos hasta mirarlo
a mano. `Restart=always` cubre que el proceso reviente, pero **no** cubre:

- que alguien lo pare (`systemctl stop`, un Ctrl+C, un `kill`)
- que se quede caído tras agotar los reintentos
- que no arranque al reiniciar la máquina

En ninguno de esos casos llegaba ningún aviso.

## Instalación (una sola vez, en el droplet)

```bash
cd /root/ai-bot

# 1. Copiar las unidades
cp deploy/pump-bot-vigia.service /etc/systemd/system/
cp deploy/pump-bot-vigia.timer   /etc/systemd/system/

# 2. Que pump-bot avise al vigía cuando entre en fallo
mkdir -p /etc/systemd/system/pump-bot.service.d
cat > /etc/systemd/system/pump-bot.service.d/aviso.conf <<'CONF'
[Unit]
OnFailure=pump-bot-vigia.service
CONF

# 3. Activar
systemctl daemon-reload
systemctl enable --now pump-bot-vigia.timer
```

## Comprobar que funciona

```bash
# ¿Está armado el timer?
systemctl list-timers pump-bot-vigia --no-pager

# Prueba de verdad: parar el bot y esperar el mensaje en Telegram
systemctl stop pump-bot
systemctl start pump-bot-vigia.service    # fuerza la comprobación ya
systemctl start pump-bot                  # volver a arrancarlo
```

Debe llegar un mensaje con el estado y las últimas líneas del log.

Si no llega, mira por qué:

```bash
journalctl -u pump-bot-vigia -n 20 --no-pager
```

Lo más probable es que falten `TELEGRAM_BOT_TOKEN` o `TELEGRAM_CHAT_ID`
en el `.env`; el vigía lo dice con esas palabras.

## Detalles que importan

- **Avisa una sola vez por caída.** Mientras siga parado no repite: una
  alarma cada 5 minutos se acaba silenciando, y entonces no sirve de nada.
  Se rearma solo cuando el bot vuelve a estar vivo.
- **El testigo** vive en `/tmp/pump-bot-caida-avisada`. Si reinicias la
  máquina se borra solo, que es lo correcto: tras un reinicio quieres saber
  si el bot volvió.
- **Dos disparadores a propósito.** `OnFailure=` salta en el instante en
  que el servicio falla; el timer cada 5 min pilla lo que aquél no ve.
