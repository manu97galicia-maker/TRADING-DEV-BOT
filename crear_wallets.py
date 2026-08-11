"""Crea N wallets de Solana y reparte SOL entre ellas al azar.

Uso:
    python crear_wallets.py crear 20              # genera y guarda las claves
    python crear_wallets.py repartir 1.0          # reparte 1 SOL desde la principal
    python crear_wallets.py saldos                # que tiene cada una

DONDE SE GUARDAN LAS CLAVES. En `wallets_generadas.json`, en la carpeta del
repo, con permisos restringidos. Ese fichero es EQUIVALENTE AL DINERO: quien lo
tenga puede vaciar las veinte carteras. Esta en .gitignore para que no acabe en
GitHub, pero eso no lo protege de nada mas -- si el disco es accesible, las
claves lo son.

AVISO SOBRE ESTE ORDENADOR EN CONCRETO: ya hay claves privadas en texto plano
en el Escritorio, en Documentos y en la raiz del perfil, y un fichero cuyo
NOMBRE es una frase semilla BIP39. Anadir veinte claves mas empeora ese
problema, no lo mejora. Si esto va a llevar dinero de verdad, merece la pena
mover todo eso a un gestor de contraseñas antes.

EL REPARTO es aleatorio pero no absurdo: cada wallet recibe entre el 60% y el
140% de la parte igual, y la suma cuadra exactamente con lo que se pide enviar.
Un reparto perfectamente igual seria la firma mas obvia de que las veinte son
del mismo dueño.
"""

from __future__ import annotations

import json
import os
import random
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from solders.keypair import Keypair  # noqa: E402

FICHERO = Path(__file__).parent / "wallets_generadas.json"
RPC = os.getenv("SOLANA_NODE_RPC_ENDPOINT")
#: Minimo que Solana exige para que una cuenta no se borre sola.
RENTA_MINIMA = 0.00089


def _rpc(metodo: str, params: list):
    import urllib.request

    req = urllib.request.Request(
        RPC,
        data=json.dumps({"jsonrpc": "2.0", "id": 1,
                         "method": metodo, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=30))


def crear(n: int) -> None:
    if FICHERO.exists():
        print(f"Ya existe {FICHERO.name}. Bórralo a mano si quieres empezar de")
        print("cero -- no lo sobrescribo solo porque dentro puede haber dinero.")
        return
    wallets = []
    for i in range(n):
        kp = Keypair()
        wallets.append({
            "n": i + 1,
            "address": str(kp.pubkey()),
            "private_key": str(kp),  # base58, el formato que usa el bot
        })
    FICHERO.write_text(json.dumps({
        "creadas": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "aviso": "ESTO ES DINERO. Quien tenga este fichero puede vaciarlas todas.",
        "wallets": wallets,
    }, indent=1), encoding="utf-8")
    try:  # en Windows no siempre aplica, pero no cuesta intentarlo
        FICHERO.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:  # noqa: BLE001
        pass
    print(f"{n} wallets creadas en {FICHERO.name}")
    print("\nDirecciones (las publicas, se pueden compartir):")
    for w in wallets:
        print(f"  {w['n']:2d}. {w['address']}")
    print("\nLas claves privadas estan en el fichero. NO las pego aqui.")


def repartir(total: float) -> None:
    if not FICHERO.exists():
        print("No hay wallets creadas. Lanza primero: crear_wallets.py crear 20")
        return
    datos = json.loads(FICHERO.read_text(encoding="utf-8"))
    wallets = datos["wallets"]
    n = len(wallets)

    parte = total / n
    if parte < RENTA_MINIMA * 2:
        print(f"AVISO: {total} SOL entre {n} salen a {parte:.5f} cada una, y la")
        print(f"renta minima de una cuenta de Solana es {RENTA_MINIMA}. Con eso")
        print("las cuentas existen pero no pueden operar.")

    # Reparto aleatorio entre el 60% y el 140% de la parte igual, ajustado
    # al final para que la suma cuadre exactamente con lo pedido.
    pesos = [random.uniform(0.6, 1.4) for _ in range(n)]
    suma = sum(pesos)
    montos = [round(total * p / suma, 6) for p in pesos]
    montos[-1] = round(total - sum(montos[:-1]), 6)

    origen = Keypair.from_base58_string(os.getenv("SOLANA_PRIVATE_KEY"))
    saldo = _rpc("getBalance", [str(origen.pubkey())])["result"]["value"] / 1e9
    print(f"origen : {origen.pubkey()}")
    print(f"saldo  : {saldo:.4f} SOL")
    print(f"a enviar: {total} SOL en {n} transferencias\n")
    if saldo < total + 0.01:
        print("No hay saldo suficiente (hace falta el total mas las comisiones).")
        return

    for w, m in zip(wallets, montos):
        print(f"  {w['n']:2d}. {w['address'][:20]}...  {m:.5f} SOL")
    print("\nEsto MUEVE DINERO DE VERDAD. Para ejecutarlo:")
    print("    python crear_wallets.py repartir", total, "--confirmar")


def saldos() -> None:
    if not FICHERO.exists():
        print("No hay wallets creadas.")
        return
    datos = json.loads(FICHERO.read_text(encoding="utf-8"))
    total = 0.0
    for w in datos["wallets"]:
        try:
            b = _rpc("getBalance", [w["address"]])["result"]["value"] / 1e9
        except Exception:  # noqa: BLE001
            b = -1
        total += max(0, b)
        print(f"  {w['n']:2d}. {w['address'][:24]}...  {b:.5f} SOL")
        time.sleep(0.2)
    print(f"\n  TOTAL: {total:.4f} SOL")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    elif sys.argv[1] == "crear":
        crear(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    elif sys.argv[1] == "repartir":
        repartir(float(sys.argv[2]) if len(sys.argv) > 2 else 1.0)
    elif sys.argv[1] == "saldos":
        saldos()
    else:
        print(__doc__)
