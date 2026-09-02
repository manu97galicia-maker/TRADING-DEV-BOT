"""Vende TODOS los tokens de la wallet del bot y deja solo SOL.

Uso:
    python vender_todo.py [--dry-run]

Con --dry-run solo lista los tokens sin vender.
"""
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv(".env")

from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TokenAccountOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from trading.jupiter_seller import jupiter_sell

SPL = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
T22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Tokens conocidos que NO son pump.fun y deben saltarse
SKIP_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC (salta si es pequeño)
}


def get_sol_price_usd() -> float:
    try:
        url = "https://price.jup.ag/v6/price?ids=So11111111111111111111111111111111111111112"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        data = json.load(urllib.request.urlopen(req, timeout=5))
        return float(data["data"]["So11111111111111111111111111111111111111112"]["price"])
    except Exception:
        return 150.0


async def get_all_tokens(client: AsyncClient, owner: Pubkey) -> list[dict]:
    tokens = []
    for prog_str, name in [(SPL, "SPL"), (T22, "T22")]:
        prog = Pubkey.from_string(prog_str)
        opts = TokenAccountOpts(program_id=prog)
        resp = await client.get_token_accounts_by_owner_json_parsed(owner, opts)
        for acc in resp.value:
            info = acc.account.data.parsed["info"]
            mint = info["mint"]
            ta = info["tokenAmount"]
            amt = float(ta.get("uiAmount") or 0)
            decimals = int(ta.get("decimals", 6))
            if amt > 0:
                tokens.append({
                    "mint": mint,
                    "amount": amt,
                    "decimals": decimals,
                    "program": name,
                    "ata": str(acc.pubkey),
                })
    return tokens


async def main():
    dry_run = "--dry-run" in sys.argv

    rpc_url = os.getenv("SOLANA_NODE_RPC_ENDPOINT")
    pk = os.getenv("SOLANA_PRIVATE_KEY")
    if not rpc_url or not pk:
        print("ERROR: falta SOLANA_NODE_RPC_ENDPOINT o SOLANA_PRIVATE_KEY en .env")
        return

    kp = Keypair.from_base58_string(pk)
    owner = kp.pubkey()
    client = AsyncClient(rpc_url)

    print(f"\nWallet: {owner}")

    sol_balance = (await client.get_balance(owner)).value
    print(f"SOL: {sol_balance / 1e9:.6f}")

    if sol_balance < 5_000_000:  # < 0.005 SOL
        print("\nATENCION: Menos de 0.005 SOL — puede que no alcance para fees de todas las ventas.")
        print("Recarga al menos 0.05 SOL antes de continuar.")
        if not dry_run:
            resp = input("¿Continuar de todas formas? [s/N]: ").strip().lower()
            if resp != "s":
                await client.close()
                return

    print("\nBuscando tokens...")
    tokens = await get_all_tokens(client, owner)
    await client.close()

    # Filtrar los que están en SKIP_MINTS con saldo pequeño
    tokens_to_sell = []
    for t in tokens:
        if t["mint"] in SKIP_MINTS and t["amount"] < 1.0:
            print(f"  SKIP  {t['mint'][:20]}...  ({t['amount']:.4f} — muy pequeño)")
            continue
        tokens_to_sell.append(t)

    # Ordenar de mayor a menor (en cantidad — no en SOL, pero es lo mejor que tenemos)
    tokens_to_sell.sort(key=lambda x: x["amount"], reverse=True)

    print(f"\n{len(tokens_to_sell)} tokens para vender:\n")
    for i, t in enumerate(tokens_to_sell, 1):
        print(f"  {i:2}. {t['mint'][:20]}...  {t['amount']:,.0f} tokens  ({t['program']})")

    if dry_run:
        print("\n[DRY RUN] No se vendió nada.")
        return

    if not tokens_to_sell:
        print("Nada que vender.")
        return

    print("\n" + "-" * 60)
    ok_count = 0
    fail_count = 0

    for i, t in enumerate(tokens_to_sell, 1):
        mint = t["mint"]
        amount = t["amount"]
        decimals = t["decimals"]

        print(f"\n[{i}/{len(tokens_to_sell)}] Vendiendo {amount:,.0f} de {mint[:20]}...")

        success, sig, err = await jupiter_sell(kp, mint, amount, decimals=decimals)

        if success:
            print(f"  OK  sig: {sig}")
            ok_count += 1
        else:
            print(f"  FALLO: {err}")
            fail_count += 1

        # Pequeña pausa para no saturar el RPC
        if i < len(tokens_to_sell):
            await asyncio.sleep(2)

    print("\n" + "-" * 60)
    print(f"Resultado: {ok_count} vendidos, {fail_count} fallidos")

    # Balance final
    client2 = AsyncClient(rpc_url)
    sol_final = (await client2.get_balance(Pubkey.from_string(str(owner)))).value
    await client2.close()
    print(f"SOL final: {sol_final / 1e9:.6f}")


if __name__ == "__main__":
    asyncio.run(main())
