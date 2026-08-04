"""Espera a que llegue el ingreso y arranca el bot solo cuando hay con que.

Arrancar antes de tener saldo no es inofensivo: el bot detecta monedas, pasa
los filtros, intenta comprar y falla por fondos. Cada uno de esos intentos es
ruido en el log y, si el preflight no lo parase, dinero. Asi que se espera.

El umbral son 0,28 SOL: 0,23 que bloquea la compra (0,20 mas el 15% de
slippage) + 0,05 de reserva intocable. Sin esa
reserva el bot compra pero luego NO PUEDE FIRMAR LA VENTA, que es exactamente
como se pierde una posicion entera.

OJO si se toca `min_reserve_sol` en el YAML: este numero tiene que bajar con
el. El 3-ago la reserva se bajo a 0,05 y aqui se quedo el 0,30 antiguo, asi que
con 0,2904 en la cartera el vigilante se quedo esperando un ingreso que ya
habia llegado y el bot NUNCA arranco -- vivo, callado y sin comprar.
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
MINIMO = 0.34   # 0,25 x 1,15 de slippage (=0,2875) + 0,05 de reserva.
                # OJO: NO es buy_amount + reserva. El bot bloquea la compra
                # con el slippage incluido, asi que 0,35 se quedaba corto y
                # arrancaba un bot que no podia comprar nada.
SONDEO = 20          # segundos entre consultas
AVISO_CADA = 15 * 60  # recordatorio si tarda

for linea in (D / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    linea = linea.strip()
    if linea and not linea.startswith("#") and "=" in linea:
        k, v = linea.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

RPC = os.environ["SOLANA_NODE_RPC_ENDPOINT"]
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


def avisar(texto: str) -> None:
    if not (TG_TOKEN and TG_CHAT):
        print(texto)
        return
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
    # La consola de Windows va en cp1252 y un emoji la revienta. Sin esto, el
    # aviso salia por Telegram y el proceso moria en el print de la linea
    # siguiente: el mensaje decia "ARRANCANDO" y el bot no llegaba a arrancar.
    limpio = texto.replace("<b>", "").replace("</b>", "")
    print(limpio.encode("ascii", "replace").decode())


def saldo(pubkey: str) -> float:
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1,
                         "method": "getBalance", "params": [pubkey]}).encode()
    r = urllib.request.Request(  # noqa: S310
        RPC, data=cuerpo, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=20) as resp:  # noqa: S310
        return json.load(resp)["result"]["value"] / 1e9


def main() -> None:
    import base58
    from solders.keypair import Keypair

    kp = Keypair.from_bytes(base58.b58decode(os.environ["SOLANA_PRIVATE_KEY"]))
    pk = str(kp.pubkey())

    inicio = time.time()
    ultimo_aviso = 0.0
    print(f"vigilando {pk} · umbral {MINIMO} SOL")

    while True:
        try:
            s = saldo(pk)
        except Exception as e:  # noqa: BLE001
            print(f"consulta fallo: {e}")
            time.sleep(SONDEO)
            continue

        if s >= MINIMO:
            pos = int((s - 0.05) / 0.2875)
            avisar(
                "💰 <b>FONDOS RECIBIDOS</b>\n"
                f"Saldo: <b>{s:.4f} SOL</b> · hasta {pos} posición(es) a la vez\n\n"
                "🟢 <b>ARRANCANDO EN REAL</b>\n"
                "• edad &lt; 7 s al comprar (mira a los 5)\n"
                "• market cap $2.700–$14.000\n"
                "• volumen ≥ 28 SOL negociados (~$2.000)\n"
                "• curva ≥ 4 SOL · titulares ≥ 8\n"
                "• nombre no repetido en 5 min · sin mayhem\n\n"
                "<b>Salida</b>\n"
                "+50% vende 50% · +90% vende 12,5%\n"
                "+150% vende 12,5% · +300% vende 12,5%\n"
                "moonbag 12,5% · stop −45%\n\n"
                "<b>0,25 SOL por entrada.</b>\n"
                "Te aviso de cada compra y cada venta."
            )
            break

        espera = time.time() - inicio
        if espera - ultimo_aviso > AVISO_CADA:
            ultimo_aviso = espera
            avisar(
                f"⏳ Sigo esperando el ingreso ({espera / 60:.0f} min).\n"
                f"Saldo actual <b>{s:.4f} SOL</b>, hacen falta {MINIMO}."
            )
        time.sleep(SONDEO)

    # subprocess y NO os.execv: en Windows execv rearma la linea de comandos
    # como texto plano y la ruta "Definitivo bot" se parte por el espacio
    # ("can't open file ...\\Definitivo"). Con una lista, el quoting lo hace
    # Python. Se espera al hijo para que este proceso viva lo que viva el bot.
    # PYTHONIOENCODING por ENTORNO, no reconfigurando sys.stdout: el bot lanza
    # cada estrategia en un proceso aparte (separate_process) y en Windows ese
    # hijo se re-crea de cero, con la codificacion cp1252 por defecto otra vez.
    # La variable de entorno si la hereda, y con ella los emojis del log dejan
    # de lanzar una excepcion por linea (550 en un arranque).
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8:replace")
    sys.exit(subprocess.run(  # noqa: S603, PLW1510
        [sys.executable, str(D / "src" / "bot_runner.py")],
        cwd=str(D), env=entorno,
    ).returncode)


if __name__ == "__main__":
    main()
