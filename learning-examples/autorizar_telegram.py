"""Autoriza la sesion de Telegram que el bot usa para LEER canales.

Son dos conexiones distintas y se confunden: TELEGRAM_BOT_TOKEN sirve para que
el bot te MANDE avisos, y no hace falta autorizar nada. Para LEER canales
(el Nitro de DEXTools) Telegram exige una sesion de usuario de verdad, con tu
numero y el codigo que te envian. Eso es lo que hace este script, una sola vez.

    uv run learning-examples/autorizar_telegram.py

Deja el fichero de sesion en el directorio actual, asi que hay que lanzarlo
desde la raiz del proyecto: es donde el bot lo busca al arrancar.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient

CONFIG = Path("bots") / "bot-paper-ai-entry.yaml"
SESION_POR_DEFECTO = "moontok_watcher"


def _nombre_sesion() -> str:
    """Lee del YAML como se llama la sesion, para no adivinarla."""
    try:
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        canales = cfg.get("filters", {}).get("telegram_calls", {})
        return str(canales.get("session_name") or SESION_POR_DEFECTO)
    except (OSError, yaml.YAMLError, AttributeError):
        return SESION_POR_DEFECTO


async def main() -> None:
    """Conecta, pide el codigo si hace falta y confirma que quedo autorizada."""
    load_dotenv(str(Path(".env").resolve()))
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        print("Faltan TELEGRAM_API_ID y TELEGRAM_API_HASH en el .env.")
        print("Se sacan creando una aplicacion en https://my.telegram.org")
        print("(no son el token de BotFather: eso es otra cosa distinta).")
        return

    sesion = _nombre_sesion()
    print(f"Sesion: {sesion}  (se guardara en {sesion}.session)")
    cliente = TelegramClient(sesion, int(api_id), api_hash)

    await cliente.connect()
    try:
        if await cliente.is_user_authorized():
            yo = await cliente.get_me()
            print(f"Ya estaba autorizada como {yo.first_name} (@{yo.username}).")
            return
        print()
        print("Telegram va a pedirte el numero y un codigo. El numero va con")
        print("prefijo internacional, por ejemplo +34600112233.")
        print()
        await cliente.start()  # pide telefono, codigo y 2FA si la tienes
        yo = await cliente.get_me()
        print()
        print(f"Autorizada como {yo.first_name} (@{yo.username}).")
        print("El bot ya puede leer los canales. Reinicialo:")
        print("  systemctl restart pump-bot")
    finally:
        await cliente.disconnect()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
