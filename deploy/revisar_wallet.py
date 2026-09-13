"""Revision de la wallet: saldo, cuantas posiciones caben y renta atrapada.

Contesta las tres preguntas que de verdad se hacen mirando una wallet de este
bot, y que no salen de mirar el saldo a secas:

  1. Con este saldo, cuantas posiciones puede abrir de verdad. El tope
     configurado no manda si el dinero no llega.
  2. Cuanta RENTA hay parada. Cada cuenta de token de Solana retiene ~0,00204
     SOL de deposito y no vuelve hasta que se cierra la cuenta. Una wallet que
     lleva semanas operando puede tener decimas de SOL ahi.
  3. Que queda en la wallet: moonbags de verdad y polvo de monedas muertas.

NO toca nada: solo lee. Cerrar cuentas es cosa de `cleanup`, y solo cierra las
que estan a cero para no quemar tokens que valgan algo.

    uv run deploy/revisar_wallet.py

La clave privada no se imprime nunca, ni entera ni en trozos.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey

LAMPORTS = 1_000_000_000
CONFIG = Path("bots") / "bot-paper-ai-entry.yaml"
#: Deposito que retiene una cuenta de token SPL. Vuelve entero al cerrarla.
RENTA_POR_CUENTA = 0.00203928
#: Lo que el bot aparta por posicion para comisiones y renta.
COLCHON = 0.003
#: IDs de los dos programas de token de Solana. No son secretos: son
#: direcciones publicas y fijas de la cadena.
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # noqa: S105
#: Cuantas cuentas con tokens se listan antes de resumir.
MAX_LISTADAS = 15


def _cfg() -> dict:
    try:
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _keypair(clave: str) -> Keypair | None:
    try:
        if clave.startswith("["):
            return Keypair.from_bytes(bytes(json.loads(clave)))
        return Keypair.from_base58_string(clave)
    except Exception as exc:  # noqa: BLE001
        print(f"  La clave del .env no se pudo interpretar: {exc}")
        return None


async def _mirar(direccion: Pubkey, rpc: str) -> tuple[float, list[dict]]:
    """Devuelve (saldo en SOL, lista de cuentas de token)."""
    client = AsyncClient(rpc)
    try:
        saldo = (await client.get_balance(direccion)).value / LAMPORTS
        cuentas: list[dict] = []
        for programa in (TOKEN_PROGRAM, TOKEN_2022):
            try:
                resp = await client.get_token_accounts_by_owner_json_parsed(
                    direccion, {"programId": Pubkey.from_string(programa)}
                )
            except Exception:  # noqa: BLE001, S112
                # Token-2022 puede no estar presente en la wallet; no es un
                # error, simplemente no hay cuentas de ese programa.
                continue
            for c in resp.value:
                info = c.account.data.parsed["info"]
                cuentas.append(
                    {
                        "mint": info["mint"],
                        "cantidad": float(info["tokenAmount"].get("uiAmount") or 0),
                    }
                )
        return saldo, cuentas
    finally:
        await client.close()


def _caben(saldo: float, reserva: float, compra: float, techo: int) -> int:
    libre = saldo - reserva
    if libre <= 0 or compra <= 0:
        return 0
    return min(int(libre // (compra + COLCHON)), techo or 99)


def _informe_saldo(
    direccion: Pubkey, saldo: float, reserva: float, compra: float, techo: int
) -> None:
    """Saldo y cuantas posiciones permite de verdad."""
    print("=" * 60)
    print(f"WALLET  {direccion}")
    print("=" * 60)
    print(f"\nSaldo                {saldo:.4f} SOL")
    print(f"Reserva intocable   -{reserva:.4f} SOL")
    print(f"Disponible           {max(0.0, saldo - reserva):.4f} SOL")

    caben = _caben(saldo, reserva, compra, techo)
    print(f"\nPOSICIONES: caben {caben} (el tope configurado es {techo})")
    if caben >= techo:
        print("  Da para el tope entero.")
        return
    falta = reserva + techo * (compra + COLCHON) - saldo
    print(f"  Manda el saldo, no el tope. Para las {techo} faltan {falta:.4f} SOL mas,")
    print(f"  o bajar tope_compra_sol por debajo de {compra:.4f}.")


def _informe_cuentas(cuentas: list[dict]) -> None:
    """Cuentas de token abiertas y cuanta renta retienen."""
    con_saldo = [c for c in cuentas if c["cantidad"] > 0]
    vacias = [c for c in cuentas if c["cantidad"] <= 0]
    print(f"\nCUENTAS DE TOKEN: {len(cuentas)}")
    print(f"  con tokens dentro   {len(con_saldo)}")
    print(f"  vacias              {len(vacias)}")
    print(f"\nRENTA ATRAPADA      {len(cuentas) * RENTA_POR_CUENTA:.4f} SOL en total")

    if vacias:
        print(
            f"  {len(vacias) * RENTA_POR_CUENTA:.4f} SOL son de cuentas VACIAS: "
            f"se recuperan cerrandolas."
        )
        print("  Con cleanup.mode: after_sell el bot ya las cierra al vender.")
        print(
            "  Las de antes de ese cambio: uv run learning-examples/cleanup_accounts.py"
        )
    if not con_saldo:
        return
    print(
        f"  {len(con_saldo) * RENTA_POR_CUENTA:.4f} SOL estan en cuentas CON "
        f"tokens: no se pueden cerrar sin vender o quemar."
    )
    print("\n  Lo que queda dentro (moonbags y polvo):")
    for c in sorted(con_saldo, key=lambda x: -x["cantidad"])[:MAX_LISTADAS]:
        print(f"    {c['mint']}  {c['cantidad']:,.0f}")
    if len(con_saldo) > MAX_LISTADAS:
        print(f"    ... y {len(con_saldo) - MAX_LISTADAS} mas")


def main() -> None:
    """Revisa la wallet del .env y explica que significa cada numero."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(str(Path(".env").resolve()))

    clave = os.getenv("SOLANA_PRIVATE_KEY", "").strip()
    if not clave:
        print("SOLANA_PRIVATE_KEY esta vacia en el .env.")
        return
    par = _keypair(clave)
    if par is None:
        return
    direccion = par.pubkey()

    rpc = os.getenv("SOLANA_NODE_RPC_ENDPOINT", "").strip()
    if not rpc:
        print(f"Wallet: {direccion}")
        print("Sin SOLANA_NODE_RPC_ENDPOINT en el .env: no puedo consultar nada.")
        return

    try:
        saldo, cuentas = asyncio.run(_mirar(direccion, rpc))
    except Exception as exc:  # noqa: BLE001
        print(f"Wallet: {direccion}")
        print(f"No pude consultar el nodo: {exc}")
        return

    t = _cfg().get("trade", {})
    reserva = float(t.get("min_reserve_sol") or 0)
    compra = float(t.get("tope_compra_sol") or 0)
    techo = int(t.get("max_posiciones") or 0)

    _informe_saldo(direccion, saldo, reserva, compra, techo)
    _informe_cuentas(cuentas)
    print("\nEsto solo LEE. No cierra cuentas ni vende nada.")


if __name__ == "__main__":
    main()
