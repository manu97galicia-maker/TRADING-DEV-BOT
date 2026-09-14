"""Observa la caja donde cobra DexScreener y apunta QUE forma tiene cada pago.

POR QUE ESTO ANTES DE COMPRAR. Pagar DexScreener es una transaccion en la
cadena, asi que se ve en el mismo segundo -- por eso los indexadores rapidos
lo saben al momento y la API de DexScreener va minutos por detras.

Pero saber que alguien ha pagado NO dice de que moneda es el pago. Para poder
comprar hace falta atar cada pago a un mint, y eso depende de como venga
montada la transaccion: un memo con la direccion del token, el propio mint
entre las cuentas, o solo el pagador (y entonces habria que mirar que monedas
ha creado). No se puede adivinar: hay que ver una de verdad.

Esto NO compra ni toca el bot. Solo mira y apunta. Se deja corriendo un rato
y luego se mira el fichero para decidir como se construye la fuente.

    uv run deploy/espiar_pagos_dex.py

Sale con Ctrl+C. Lo visto queda en trades/pagos_dex_observados.jsonl
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import websockets
from dotenv import load_dotenv

#: La caja de DexScreener. Es una cuenta fuera de curva (controlada por un
#: programa), no una wallet normal.
CAJA = "ARu4n5mFdZogZAravu7CcizaojWnS6oqka37gdLT5SZn"
SALIDA = Path("trades") / "pagos_dex_observados.jsonl"


async def _detalle(rpc: str, firma: str) -> dict:
    """Trae la transaccion entera para ver de que esta hecha."""
    cuerpo = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            firma,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        ],
    }
    async with aiohttp.ClientSession() as s, s.post(rpc, json=cuerpo) as r:
        return (await r.json()).get("result") or {}


def _resumir(tx: dict) -> dict:
    """Saca lo que importa para decidir si el pago identifica una moneda."""
    msg = (tx.get("transaction") or {}).get("message") or {}
    meta = tx.get("meta") or {}
    cuentas = [
        c.get("pubkey") if isinstance(c, dict) else c
        for c in (msg.get("accountKeys") or [])
    ]
    # Un memo, si lo hay, suele ser donde se mete la referencia del pedido.
    memos = []
    for ix in msg.get("instructions") or []:
        if isinstance(ix, dict):
            if ix.get("program") == "spl-memo":
                memos.append(ix.get("parsed"))
            elif "memo" in str(ix.get("programId", "")).lower():
                memos.append(ix.get("data"))
    # Mints que aparezcan en los saldos de token de la transaccion: si el pago
    # menciona el token, saldra aqui.
    mints = sorted(
        {
            b.get("mint")
            for b in (meta.get("preTokenBalances") or [])
            + (meta.get("postTokenBalances") or [])
            if b.get("mint")
        }
    )
    cambio_sol = 0.0
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if pre and post and cuentas:
        try:
            i = cuentas.index(CAJA)
            cambio_sol = (post[i] - pre[i]) / 1e9
        except (ValueError, IndexError):
            cambio_sol = 0.0
    return {
        "pagador": cuentas[0] if cuentas else None,
        "sol_recibidos": round(cambio_sol, 6),
        "mints_en_la_tx": mints,
        "memos": memos,
        "n_cuentas": len(cuentas),
        "cuentas": cuentas[:20],
        "programas": sorted(
            {
                ix.get("programId")
                for ix in (msg.get("instructions") or [])
                if isinstance(ix, dict) and ix.get("programId")
            }
        ),
    }


def _pinta(n: int, firma: str, d: dict) -> None:
    print(f"\n--- PAGO {n} ---")
    print(f"  firma        {firma}")
    print(f"  pagador      {d['pagador']}")
    print(f"  SOL a la caja {d['sol_recibidos']:+.6f}")
    print(f"  mints en la tx {d['mints_en_la_tx'] or 'NINGUNO'}")
    print(f"  memos        {d['memos'] or 'ninguno'}")
    print(f"  programas    {d['programas']}")
    if d["mints_en_la_tx"]:
        print("  --> El pago SI menciona un token: se puede atar a una moneda.")
    else:
        print("  --> El pago NO menciona token. Habria que ir por el pagador:")
        print("      ver que monedas ha creado esa wallet.")


async def main() -> None:
    load_dotenv(str(Path(".env").resolve()))
    wss = os.getenv("SOLANA_NODE_WSS_ENDPOINT", "").strip()
    rpc = os.getenv("SOLANA_NODE_RPC_ENDPOINT", "").strip()
    if not wss or not rpc:
        print("Faltan SOLANA_NODE_WSS_ENDPOINT o SOLANA_NODE_RPC_ENDPOINT en .env")
        return

    print(f"Vigilando la caja {CAJA}")
    print("Esto NO compra nada. Ctrl+C para salir.\n")
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    vistos: set[str] = set()
    n = 0

    async with websockets.connect(wss) as ws:
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [{"mentions": [CAJA]}, {"commitment": "confirmed"}],
                }
            )
        )
        print(json.loads(await ws.recv()))
        while True:
            datos = json.loads(await ws.recv())
            if datos.get("method") != "logsNotification":
                continue
            val = datos["params"]["result"]["value"]
            firma = val.get("signature")
            if not firma or firma in vistos or val.get("err"):
                continue
            vistos.add(firma)
            try:
                tx = await _detalle(rpc, firma)
                d = _resumir(tx) if tx else {"error": "sin detalle"}
            except Exception as exc:  # noqa: BLE001
                d = {"error": str(exc)}
            n += 1
            if "error" not in d:
                _pinta(n, firma, d)
            else:
                print(f"\n--- PAGO {n} --- {firma}: {d['error']}")
            with SALIDA.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "cuando": datetime.now(timezone.utc).isoformat(),
                            "firma": firma,
                            **d,
                        }
                    )
                    + "\n"
                )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\nParado. Lo visto esta en {SALIDA}")
