"""Cierra las cuentas de token vacias y recupera el rent inmovilizado.

Cada vez que el bot compra un token, Solana abre una cuenta para guardarlo y
cobra ~0,00204 SOL de rent. Ese SOL no se gasta: queda depositado y se
devuelve al cerrar la cuenta. El bot deberia cerrarlas solo (cleanup
post_session), pero si se queda a medias -- una caida, un reinicio, una tanda
de ventas fallidas -- las cuentas se acumulan y el rent se queda ahi parado.

El 1-ago habia 19 cuentas abiertas con 0,039 SOL dentro, once veces el saldo
que quedaba en la wallet.

Solo toca cuentas con saldo CERO. Una cuenta con tokens dentro no se puede
cerrar (la red lo rechaza), asi que no hay riesgo de tirar una posicion.

Uso:
    ./.venv/bin/python recuperar_rent.py           # solo mira y te dice cuanto hay
    ./.venv/bin/python recuperar_rent.py --hazlo   # cierra de verdad
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TokenAccountOpts, TxOpts
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from spl.token.instructions import CloseAccountParams, close_account

TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")

#: Cierres por transaccion. Cada uno son ~30 bytes y una transaccion cabe en
#: 1232: 20 deja margen de sobra y evita quedarse a medias por tamano.
POR_TANDA = 20


def _load_dotenv() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def main() -> None:
    _load_dotenv()
    hazlo = "--hazlo" in sys.argv

    kp = Keypair.from_base58_string(os.environ["SOLANA_PRIVATE_KEY"])
    dueno = kp.pubkey()
    rpc = (
        os.environ.get("SOLANA_NODE_RPC_ENDPOINT")
        or "https://api.mainnet-beta.solana.com"
    )
    client = AsyncClient(rpc)

    try:
        saldo0 = (await client.get_balance(dueno)).value / 1e9
        print(f"wallet {dueno}")
        print(f"saldo actual: {saldo0:.6f} SOL\n")

        vacias: list[tuple[Pubkey, Pubkey, int]] = []  # (cuenta, programa, lamports)
        con_tokens = 0
        for prog in (TOKEN, TOKEN_2022):
            resp = await client.get_token_accounts_by_owner_json_parsed(
                dueno, TokenAccountOpts(program_id=prog)
            )
            for acc in resp.value:
                info = acc.account.data.parsed["info"]
                cantidad = int(info["tokenAmount"]["amount"])
                if cantidad == 0:
                    vacias.append((acc.pubkey, prog, acc.account.lamports))
                else:
                    con_tokens += 1
                    print(
                        f"  se queda (tiene tokens): {info['mint']} "
                        f"{float(info['tokenAmount']['uiAmount'] or 0):,.0f}"
                    )

        recuperable = sum(lamports for _, _, lamports in vacias) / 1e9
        print(
            f"\ncuentas vacias: {len(vacias)}  ->  {recuperable:.6f} SOL recuperables"
        )
        if con_tokens:
            print(f"cuentas con tokens: {con_tokens} (no se tocan)")

        if not vacias:
            print("\nno hay nada que cerrar")
            return
        if not hazlo:
            print("\n(simulacion; nada se ha tocado)")
            print(
                "para hacerlo de verdad:  ./.venv/bin/python recuperar_rent.py --hazlo"
            )
            return

        cerradas = 0
        for i in range(0, len(vacias), POR_TANDA):
            tanda = vacias[i : i + POR_TANDA]
            ixs = [
                close_account(
                    CloseAccountParams(
                        program_id=prog, account=cuenta, dest=dueno, owner=dueno
                    )
                )
                for cuenta, prog, _ in tanda
            ]
            bh = (await client.get_latest_blockhash()).value.blockhash
            tx = Transaction([kp], Message(ixs, dueno), bh)
            try:
                sig = (
                    await client.send_transaction(tx, opts=TxOpts(skip_preflight=False))
                ).value
                await client.confirm_transaction(sig, commitment="confirmed")
                cerradas += len(tanda)
                print(
                    f"  tanda {i // POR_TANDA + 1}: {len(tanda)} cuentas cerradas · {sig}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  tanda {i // POR_TANDA + 1} FALLO: {exc}")

        saldo1 = (await client.get_balance(dueno)).value / 1e9
        print(f"\ncerradas {cerradas} cuentas")
        print(f"saldo: {saldo0:.6f} -> {saldo1:.6f} SOL  ({saldo1 - saldo0:+.6f})")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
