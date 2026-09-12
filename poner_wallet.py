"""Pone la wallet en el .env a partir de la frase secreta, pensado para movil.

Teclear una clave privada en base58 (88 caracteres aleatorios) desde un movil
es inviable: un solo caracter mal y no hay forma de saber cual. La frase de
12 o 24 palabras si se puede escribir, y ademas trae checksum, asi que una
palabra mal se detecta ANTES de escribir nada.

    uv run poner_wallet.py

A diferencia de leerla oculta, aqui la frase se ve mientras se escribe: en un
movil con autocorrector, escribir a ciegas es como se cuelan los fallos.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dashboard.wallet_import import derive_from_mnemonic

ENV = Path(".env")
CUENTAS = 5


def _pedir_frase() -> str | None:
    """Pide la frase y la valida antes de seguir."""
    print("Escribe las 12 o 24 palabras separadas por espacios y pulsa Enter.")
    print("(se ven mientras escribes, para que puedas revisarlas)")
    print()
    frase = input("> ").strip()
    palabras = frase.split()
    if len(palabras) not in (12, 24):
        print(f"\nHas escrito {len(palabras)} palabras. Tienen que ser 12 o 24.")
        return None
    try:
        derive_from_mnemonic(frase, 0)
    except ValueError as exc:
        print(f"\nLa frase no es valida: {exc}")
        print("Suele ser una palabra mal escrita. Revisa y vuelve a lanzarlo.")
        print("Consejo: apaga el autocorrector del movil antes de escribirla.")
        return None
    return frase


def main() -> None:
    """Deriva las primeras cuentas, deja elegir una y la escribe en el .env."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 55)
    print(" PONER LA WALLET EN EL .ENV")
    print("=" * 55)
    print()

    frase = _pedir_frase()
    if frase is None:
        return

    print("\nEstas son las primeras cuentas de esa frase:\n")
    cuentas = [derive_from_mnemonic(frase, i) for i in range(CUENTAS)]
    for i, w in enumerate(cuentas):
        print(f"  {i}: {w['pubkey']}")

    print("\nBusca en Phantom la que uses y escribe su numero.")
    elegido = input("Numero de cuenta [0]: ").strip() or "0"
    if not elegido.isdigit() or int(elegido) >= CUENTAS:
        print(f"'{elegido}' no vale. Tiene que ser de 0 a {CUENTAS - 1}.")
        return
    wallet = cuentas[int(elegido)]

    # Se quita cualquier SOLANA_PRIVATE_KEY anterior en vez de anadir otra: con
    # dos lineas iguales gana la ultima, y eso es justo el tipo de sorpresa que
    # hace operar desde una wallet que no era.
    lineas = []
    if ENV.exists():
        lineas = [
            ln
            for ln in ENV.read_text(encoding="utf-8").splitlines()
            if not ln.startswith("SOLANA_PRIVATE_KEY=")
        ]
    lineas.append(f"SOLANA_PRIVATE_KEY={wallet['private_key_b58']}")
    ENV.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    print()
    print("=" * 55)
    print(f" Guardada. El bot operara con:\n   {wallet['pubkey']}")
    print("=" * 55)
    print("\nComprueba el saldo con:")
    print("  uv run learning-examples/comprobar_wallet.py")
    print("\nY reinicia:")
    print("  systemctl restart paper-ai-entry")


if __name__ == "__main__":
    main()
