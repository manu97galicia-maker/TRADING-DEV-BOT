"""Quema el "polvo" de memecoins sin valor y cierra sus cuentas para recuperar rent.

Pedido por Manu el 9-nov: *"HAZLO"* -- recuperar los ~0,12 SOL de rent que
estaban atrapados en 81 cuentas de la wallet 43w6CJ... que solo guardaban
restos (p. ej. 0,000001 tokens) de memecoins ya vendidas.

`recuperar_rent.py` solo cierra cuentas a CERO, porque Solana no deja cerrar
una cuenta que tenga aunque sea 1 unidad dentro. Este script da el paso que
falta: quema esos restos y cierra la cuenta en la MISMA transaccion.

Seguridad (para no tirar nada que valga):
- Solo toca una cuenta si su contenido vale MENOS DE 0,01 $ segun el precio
  de Jupiter. Si Jupiter no da precio para ese token, la cuenta NO se toca.
- Nunca toca SOL envuelto (wSOL) ni USDC/USDT, valgan lo que valgan.
- Por defecto solo MIRA y lista. Solo actua con --hazlo.

Uso (en el droplet, dentro de la carpeta del bot, donde esta el .env):
    ./.venv/bin/python quemar_polvo.py                   # solo mira
    ./.venv/bin/python quemar_polvo.py --hazlo           # quema y cierra
    ./.venv/bin/python quemar_polvo.py --clave OTRA_KEY  # usa otra variable del .env

Por defecto firma con la clave de SOLANA_PRIVATE_KEY del .env. La primera
linea que imprime es la wallet: comprueba que es la que quieres limpiar.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TokenAccountOpts, TxOpts
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from spl.token.instructions import (
    BurnCheckedParams,
    CloseAccountParams,
    burn_checked,
    close_account,
)

TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")

#: Tokens que nunca se queman, valgan lo que valgan.
NUNCA = {
    "So11111111111111111111111111111111111111112",  # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

#: Por debajo de esto (en dolares) el contenido de una cuenta se considera polvo.
LIMITE_USD = 0.01

#: Cuentas por transaccion. Cada una lleva 2 instrucciones (quemar + cerrar);
#: 8 deja margen de sobra para no pasarse del tamano maximo de transaccion.
POR_TANDA = 8

PRECIOS_URL = "https://lite-api.jup.ag/price/v3?ids="


def _load_dotenv() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _precios(mints: list[str]) -> dict[str, float]:
    """Precio en dolares de cada token segun Jupiter. Sin precio = no aparece."""
    precios: dict[str, float] = {}
    for i in range(0, len(mints), 50):
        url = PRECIOS_URL + ",".join(mints[i : i + 50])
        try:
            with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310
                datos = json.load(r)
        except Exception as exc:  # noqa: BLE001
            print(f"  aviso: no se pudo leer precios de Jupiter ({exc})")
            continue
        for mint, info in (datos or {}).items():
            if isinstance(info, dict) and info.get("usdPrice") is not None:
                precios[mint] = float(info["usdPrice"])
    return precios


async def main() -> None:
    _load_dotenv()
    hazlo = "--hazlo" in sys.argv
    var = "SOLANA_PRIVATE_KEY"
    if "--clave" in sys.argv:
        var = sys.argv[sys.argv.index("--clave") + 1]

    kp = Keypair.from_base58_string(os.environ[var])
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

        cuentas = []  # (cuenta, programa, mint, cantidad, decimales, ui, lamports)
        for prog in (TOKEN, TOKEN_2022):
            resp = await client.get_token_accounts_by_owner_json_parsed(
                dueno, TokenAccountOpts(program_id=prog)
            )
            for acc in resp.value:
                info = acc.account.data.parsed["info"]
                ta = info["tokenAmount"]
                cuentas.append(
                    (
                        acc.pubkey,
                        prog,
                        info["mint"],
                        int(ta["amount"]),
                        int(ta["decimals"]),
                        float(ta["uiAmountString"]),
                        acc.account.lamports,
                    )
                )

        con_restos = [c for c in cuentas if c[3] > 0]
        precios = _precios(sorted({c[2] for c in con_restos}))

        a_limpiar = []
        for c in cuentas:
            cuenta, prog, mint, cantidad, dec, ui, lam = c
            if cantidad == 0:
                a_limpiar.append(c)
                print(f"  vacia          {mint}  rent {lam / 1e9:.5f}")
                continue
            if mint in NUNCA:
                print(f"  SE QUEDA       {mint}  {ui:,.6f} (token protegido)")
                continue
            if mint not in precios:
                print(f"  SE QUEDA       {mint}  {ui:,.6f} (sin precio, no se toca)")
                continue
            usd = ui * precios[mint]
            if usd >= LIMITE_USD:
                print(f"  SE QUEDA       {mint}  {ui:,.6f} = {usd:.2f} $")
                continue
            a_limpiar.append(c)
            print(f"  polvo -> quema {mint}  {ui:,.6f} = {usd:.4f} $  rent {lam / 1e9:.5f}")

        recuperable = sum(c[6] for c in a_limpiar) / 1e9
        print(
            f"\ncuentas a limpiar: {len(a_limpiar)} de {len(cuentas)}"
            f"  ->  {recuperable:.6f} SOL recuperables"
        )

        if not a_limpiar:
            print("\nno hay nada que limpiar")
            return
        if not hazlo:
            print("\n(solo se ha mirado; nada se ha tocado)")
            print("para hacerlo de verdad:  ./.venv/bin/python quemar_polvo.py --hazlo")
            return

        limpiadas = 0
        for i in range(0, len(a_limpiar), POR_TANDA):
            tanda = a_limpiar[i : i + POR_TANDA]
            ixs = []
            for cuenta, prog, mint, cantidad, dec, _ui, _lam in tanda:
                if cantidad > 0:
                    ixs.append(
                        burn_checked(
                            BurnCheckedParams(
                                program_id=prog,
                                account=cuenta,
                                mint=Pubkey.from_string(mint),
                                owner=dueno,
                                amount=cantidad,
                                decimals=dec,
                            )
                        )
                    )
                ixs.append(
                    close_account(
                        CloseAccountParams(
                            program_id=prog, account=cuenta, dest=dueno, owner=dueno
                        )
                    )
                )
            bh = (await client.get_latest_blockhash()).value.blockhash
            tx = Transaction([kp], Message(ixs, dueno), bh)
            n = i // POR_TANDA + 1
            try:
                sig = (
                    await client.send_transaction(tx, opts=TxOpts(skip_preflight=False))
                ).value
                await client.confirm_transaction(sig, commitment="confirmed")
                limpiadas += len(tanda)
                print(f"  tanda {n}: {len(tanda)} cuentas limpiadas · {sig}")
            except Exception as exc:  # noqa: BLE001
                print(f"  tanda {n} FALLO: {exc}")

        saldo1 = (await client.get_balance(dueno)).value / 1e9
        print(f"\nlimpiadas {limpiadas} cuentas")
        print(f"saldo: {saldo0:.6f} -> {saldo1:.6f} SOL  ({saldo1 - saldo0:+.6f})")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
