"""Dice con que wallet operara el bot, sin enseñar nunca la clave privada.

El bot no configura una direccion: configura una CLAVE, en SOLANA_PRIVATE_KEY,
y la direccion sale de ella. Este script hace ese calculo para poder confirmar
que la clave que hay puesta es la de la wallet que se cree, antes de que el bot
gaste nada.

    uv run learning-examples/comprobar_wallet.py
    uv run learning-examples/comprobar_wallet.py --esperada <direccion>

Con --esperada compara y dice si coincide. La clave privada no se imprime, ni
entera ni en trozos.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair

if TYPE_CHECKING:
    from solders.pubkey import Pubkey

CONFIG = Path("bots") / "bot-paper-ai-entry.yaml"
LAMPORTS = 1_000_000_000


def _esperada() -> str | None:
    """Devuelve la direccion pasada con --esperada, si la hay."""
    if "--esperada" not in sys.argv:
        return None
    i = sys.argv.index("--esperada")
    return sys.argv[i + 1] if i + 1 < len(sys.argv) else None


def _keypair(clave: str) -> Keypair | None:
    """Interpreta la clave en base58 o como lista de bytes JSON."""
    clave = clave.strip()
    try:
        if clave.startswith("["):
            return Keypair.from_bytes(bytes(json.loads(clave)))
        return Keypair.from_base58_string(clave)
    except Exception as exc:  # noqa: BLE001 -- el formato lo decide el usuario
        print(f"  La clave no se pudo interpretar: {exc}")
        print("  Debe ser base58 (lo que exporta Phantom, ~88 caracteres)")
        print("  o una lista de bytes tipo [12,34,...] de solana-keygen.")
        return None


async def _saldo(direccion: Pubkey) -> float | None:
    """Consulta el saldo, o None si no hay RPC o no responde."""
    rpc = os.getenv("SOLANA_NODE_RPC_ENDPOINT", "").strip()
    if not rpc:
        return None
    try:
        client = AsyncClient(rpc)
        try:
            return (await client.get_balance(direccion)).value / LAMPORTS
        finally:
            await client.close()
    except Exception:  # noqa: BLE001 -- sin red esto es informativo, no critico
        return None


def _reserva() -> float:
    """Lee del YAML el SOL que el bot nunca debe gastar."""
    try:
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        return float(cfg.get("trade", {}).get("min_reserve_sol") or 0)
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        return 0.0


def main() -> None:
    """Deriva la direccion de la clave del .env y la compara si procede."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(str(Path(".env").resolve()))

    clave = os.getenv("SOLANA_PRIVATE_KEY", "").strip()
    if not clave:
        print("SOLANA_PRIVATE_KEY esta vacia en el .env: el bot no puede operar.")
        return

    par = _keypair(clave)
    if par is None:
        return
    direccion = par.pubkey()

    print("=" * 58)
    print(f"El bot operara con:  {direccion}")
    print("=" * 58)

    saldo = asyncio.run(_saldo(direccion))
    if saldo is None:
        print("Saldo: no consultado (sin RPC configurado o sin respuesta)")
    else:
        reserva = _reserva()
        print(f"Saldo: {saldo:.4f} SOL")
        if reserva:
            print(
                f"Reserva intocable: {reserva:.4f} SOL  ->  "
                f"disponible para operar: {max(0.0, saldo - reserva):.4f} SOL"
            )

    esperada = _esperada()
    if esperada:
        print()
        if str(direccion) == esperada.strip():
            print(f"COINCIDE con la que esperabas: {esperada}")
        else:
            print("NO COINCIDE.")
            print(f"  esperabas: {esperada}")
            print(f"  la clave del .env es de: {direccion}")
            print("  El bot usaria la segunda. Cambia SOLANA_PRIVATE_KEY.")


if __name__ == "__main__":
    main()
