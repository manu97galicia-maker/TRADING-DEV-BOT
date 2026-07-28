"""Vigía LOCAL: avisa (pitido + pantalla) cuando uno de tus 63 devs lanza una
moneda en pump.fun — compre el bot o no. No opera, solo avisa. 100% local.

Uso (en el PC, dentro de la carpeta del bot):
    .venv/Scripts/python.exe watch_devs.py
Salir: Ctrl+C
"""
import asyncio
import datetime
import json
import sys
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Falta 'websockets'. Instala:  .venv/Scripts/pip install websockets")
    sys.exit(1)

try:
    import winsound

    def beep() -> None:
        for f in (880, 1320, 1760):
            winsound.Beep(f, 150)
except Exception:  # noqa: BLE001 - sin sonido en no-Windows
    def beep() -> None:
        print("\a", end="", flush=True)

DEVS_FILE = Path(__file__).parent / "top_devs_final.json"
data = json.loads(DEVS_FILE.read_text(encoding="utf-8"))
DEVS = {w["wallet"]: w for w in data["wallets"]}
WS = "wss://pumpportal.fun/api/data"

print(f"👀 Vigilando {len(DEVS)} devs (feed pumpportal). Ctrl+C para salir.\n")


def alerta(msg: dict) -> None:
    dev = msg.get("traderPublicKey", "")
    d = DEVS.get(dev, {})
    sym = msg.get("symbol", "?")
    name = msg.get("name", "")
    mint = msg.get("mint", "")
    mcap = msg.get("marketCapSol")
    mp = d.get("median_peak")
    mp_s = f"${mp:,}" if isinstance(mp, (int, float)) else "?"
    t = datetime.datetime.now().strftime("%H:%M:%S")
    beep()
    print("=" * 72)
    print(f"🚨 {t}  ¡LANZAMIENTO DE TU DEV!  ->  {sym}  ({name})")
    print(f"   Dev: {dev[:10]}…  · mediana {mp_s} · lanz/7d {d.get('launches_7d','?')} · paga DEX")
    if mcap:
        print(f"   MC inicial: {mcap} SOL")
    print(f"   pump.fun:    https://pump.fun/{mint}")
    print(f"   DexScreener: https://dexscreener.com/solana/{mint}")
    print(f"   Maestro:     https://t.me/MaestroSniperBot?start={mint}")
    print(f"   CA: {mint}")
    print("=" * 72 + "\n", flush=True)


async def run() -> None:
    while True:
        try:
            async with websockets.connect(WS, ping_interval=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                print("[conectado a pumpportal — escuchando…]\n", flush=True)
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if msg.get("traderPublicKey") in DEVS:
                        alerta(msg)
        except Exception as e:  # noqa: BLE001 - reconectar siempre
            print(f"[reconectando en 3s… {type(e).__name__}]", flush=True)
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n👋 Vigía detenido.")
