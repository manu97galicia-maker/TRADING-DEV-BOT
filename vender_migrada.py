"""Vende un token migrado (ya no en curva) via Jupiter.

Uso:
    python vender_migrada.py <MINT_ADDRESS> [cantidad]

Si no pasas cantidad, vende TODO lo que haya en la wallet.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv(".env")

import urllib.request
from solders.keypair import Keypair
from trading.jupiter_seller import jupiter_sell


def get_token_balance(rpc: str, owner: str, mint: str) -> float:
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [owner, {"mint": mint}, {"encoding": "jsonParsed"}]
    }).encode()
    req = urllib.request.Request(rpc, data=payload, headers={"Content-Type": "application/json"})
    data = json.load(urllib.request.urlopen(req, timeout=15))
    accounts = (data.get("result") or {}).get("value") or []
    total = 0.0
    for a in accounts:
        info = ((a.get("account") or {}).get("data") or {}).get("parsed", {}).get("info") or {}
        total += float((info.get("tokenAmount") or {}).get("uiAmount") or 0)
    return total


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    mint = sys.argv[1]
    rpc = os.getenv("SOLANA_NODE_RPC_ENDPOINT")
    kp = Keypair.from_base58_string(os.getenv("SOLANA_PRIVATE_KEY"))

    if len(sys.argv) >= 3:
        amount = float(sys.argv[2])
    else:
        amount = get_token_balance(rpc, str(kp.pubkey()), mint)
        if amount <= 0:
            print(f"No tienes tokens de {mint} en la wallet.")
            return
        print(f"Saldo encontrado: {amount} tokens")

    print(f"Vendiendo {amount} tokens de {mint[:20]}... via Jupiter")
    ok, sig, err = await jupiter_sell(kp, mint, amount)
    if ok:
        print(f"VENDIDO — tx: {sig}")
    else:
        print(f"FALLO: {err}")


if __name__ == "__main__":
    asyncio.run(main())
