"""Mantiene el bot vivo sin nadie delante.

Manu se va ocho horas con la orden de no pararlo. Sin esto, cualquier caida
(websocket que muere y no reconecta, excepcion no capturada, un reinicio de
Windows que mate el proceso) dejaria el bot apagado durante horas y con
posiciones abiertas sin nadie que las cierre -- que es como se pierde una
posicion entera.

Lo que hace es simple a proposito: relanza y avisa. Nada de decidir por su
cuenta si conviene seguir operando; esa decision es de Manu y ya la tomo.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404
import sys
import time
import urllib.request
from pathlib import Path

D = Path(__file__).parent
ESPERA_TRAS_CAIDA = 20      # segundos antes de relanzar
AVISO_RESUMEN = 60 * 60     # parte de estado cada hora

for linea in (D / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    linea = linea.strip()
    if linea and not linea.startswith("#") and "=" in linea:
        k, v = linea.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

RPC = os.environ["SOLANA_NODE_RPC_ENDPOINT"]
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


def avisar(texto: str) -> None:
    if TG_TOKEN and TG_CHAT:
        try:
            datos = json.dumps({
                "chat_id": TG_CHAT, "text": texto,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            }).encode()
            urllib.request.urlopen(  # noqa: S310
                urllib.request.Request(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    data=datos, headers={"Content-Type": "application/json"}),
                timeout=15)
        except Exception as e:  # noqa: BLE001
            print(f"telegram fallo: {e}")
    print(texto.replace("<b>", "").replace("</b>", "")
          .encode("ascii", "replace").decode())


def saldo() -> float:
    """SOL en la cartera, o -1 si no se puede consultar."""
    import base58
    from solders.keypair import Keypair

    try:
        kp = Keypair.from_bytes(base58.b58decode(os.environ["SOLANA_PRIVATE_KEY"]))
        cuerpo = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "getBalance",
            "params": [str(kp.pubkey()), {"commitment": "processed"}],
        }).encode()
        r = urllib.request.Request(  # noqa: S310
            RPC, data=cuerpo, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=20) as resp:  # noqa: S310
            return json.load(resp)["result"]["value"] / 1e9
    except Exception:  # noqa: BLE001
        return -1.0


def main() -> None:
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8:replace")
    reinicios = 0
    inicio = time.time()
    ultimo_resumen = time.time()
    avisar(
        "🛡️ <b>GUARDIAN ACTIVO</b>\n"
        "Si el bot se cae, lo relanzo solo y te aviso.\n"
        f"Saldo al empezar: <b>{saldo():.4f} SOL</b>"
    )

    while True:
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, str(D / "src" / "bot_runner.py")],
            cwd=str(D), env=entorno,
            stdout=open(D / "guardian_bot.log", "a", encoding="utf-8"),  # noqa: SIM115
            stderr=subprocess.STDOUT,
        )
        # Mientras el bot vive, se manda un parte por hora para que Manu vea
        # desde el movil que sigue en pie sin tener que abrir nada.
        while proc.poll() is None:
            time.sleep(15)
            if time.time() - ultimo_resumen > AVISO_RESUMEN:
                ultimo_resumen = time.time()
                s = saldo()
                avisar(
                    f"⏱️ <b>Sigo vivo</b> ({(time.time() - inicio) / 3600:.1f} h)\n"
                    f"Saldo: <b>{s:.4f} SOL</b> · reinicios: {reinicios}"
                )

        reinicios += 1
        s = saldo()
        avisar(
            f"⚠️ <b>El bot se ha caido</b> (código {proc.returncode}).\n"
            f"Saldo: <b>{s:.4f} SOL</b>\n"
            f"Relanzando en {ESPERA_TRAS_CAIDA}s… (caída nº {reinicios})"
        )
        time.sleep(ESPERA_TRAS_CAIDA)


if __name__ == "__main__":
    main()
