"""Monedas de pump.fun que pasaron de 100k, segun DEXTools, y sus devs.

Pedido el 7-nov: "mira las APIs que tenemos, empieza por DEXTools: los que
estaban en bonding curve y superaron los 100k".

LO QUE DEXTOOLS DA Y LO QUE NO. Su API v2 no tiene "monedas que salieron de
pump.fun y pasaron de 100k". Lo que si tiene: los rankings del momento
(hotpools y gainers) y la ficha de cada token (con su market cap). Asi que
esto saca las monedas DE PUMP.FUN (la direccion acaba en "pump") que estan
hoy en esos rankings con 100k o mas, y busca quien las minteo. Es una foto
del momento: corriendolo cada dia se va juntando historia.

Ademas, la primera vez ensena que campos devuelve DEXTools con tu plan, para
saber que mas se le puede sacar.

EL DEV de cada moneda: la cartera que pago y firmo la transaccion que la creo
(su PRIMERA transaccion, leida con el Helius del bot). Igual que el bot:
"el dev es el que mintea".

No compra, no vende, no toca el bot. Las claves van en cabeceras y nunca se
escriben. Escribe /root/devs_100k.json y AÑADE a lo que ya hubiera (cada
pasada suma monedas nuevas).

    cd /root/ai-bot && PYTHONPATH=src .venv/bin/python deploy/devs_100k_dextools.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import devs_activos as da
import macro_devs as md

SALIDA = Path("/root/devs_100k.json")
MIN_MC = 100_000.0
RUTA = "https://public-api.dextools.io/{tier}/v2{ruta}"


def lista_de(payload: object) -> list[dict]:
    """Las filas de una respuesta de DEXTools, venga como venga."""
    if isinstance(payload, dict):
        datos = payload.get("data", payload.get("results"))
        if isinstance(datos, dict):
            datos = datos.get("results") or datos.get("tokens") or datos.get("pools")
        payload = datos
    return [x for x in payload or [] if isinstance(x, dict)] if isinstance(payload, list) else []


def mint_de(fila: dict) -> str | None:
    for k in ("mainToken", "token", "baseToken"):
        t = fila.get(k)
        if isinstance(t, dict) and t.get("address"):
            return str(t["address"])
    return str(fila["address"]) if fila.get("address") else None


def market_cap(ficha: object) -> float | None:
    """Market cap de la ficha de un token (o su FDV si no hay otro)."""
    d = ficha.get("data") if isinstance(ficha, dict) else None
    if not isinstance(d, dict):
        return None
    for k in ("mcap", "marketCap", "market_cap", "fdv"):
        try:
            v = float(d.get(k) or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return None


def es_de_pumpfun(mint: str) -> bool:
    return mint.endswith("pump")


def dev_de(tx: dict) -> str | None:
    """La cartera que pago y firmo la creacion (primera cuenta), si lo es."""
    try:
        pagador = str(tx["transaction"]["message"]["accountKeys"][0])
    except (KeyError, IndexError, TypeError):
        return None
    from monitoring.pumpfun_dev import es_cartera  # noqa: PLC0415

    return pagador if es_cartera(pagador) else None


def main() -> None:  # noqa: C901, PLR0912, PLR0915
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(da.RAIZ / ".env")
    print("== CLAVES DE API EN EL .env (solo SI/NO)")
    for k in ("SOLANA_NODE_RPC_ENDPOINT", "DEXTOOLS_API_KEY", "GMGN_API_KEY",
              "BIRDEYE_API_KEY", "JUPITER_API_KEY", "GEYSER_API_TOKEN"):
        print(f"  {k}: {'SI' if os.environ.get(k, '').strip() else 'NO'}")
    clave = os.environ.get("DEXTOOLS_API_KEY", "").strip()
    rpc = os.environ.get("SOLANA_NODE_RPC_ENDPOINT", "").strip()
    if not clave:
        print("Sin DEXTOOLS_API_KEY no se puede seguir.")
        sys.exit(1)
    tier = (os.environ.get("DEXTOOLS_API_TIER") or "trial").strip().lower()
    cab = {"X-API-Key": clave}

    def dext(ruta: str) -> object:
        r = md._get(RUTA.format(tier=tier, ruta=ruta), cab)  # noqa: SLF001
        time.sleep(1.1)  # plan trial: 1 por segundo
        return r

    print(f"\n== DEXTOOLS (plan {tier}): que devuelve")
    candidatas: dict[str, dict] = {}
    for nombre in ("hotpools", "gainers"):
        filas = lista_de(dext(f"/ranking/solana/{nombre}"))
        campos = sorted({k for f in filas for k in f})
        print(f"  {nombre}: {len(filas)} filas; campos: {', '.join(campos[:15]) or '(nada)'}")
        for f in filas:
            m = mint_de(f)
            if m and es_de_pumpfun(m):
                candidatas.setdefault(m, {"ranking": nombre})
    print(f"  monedas de pump.fun en los rankings: {len(candidatas)}")

    primera = True
    for m, c in candidatas.items():
        ficha = dext(f"/token/solana/{m}/info")
        if primera:
            d = ficha.get("data") if isinstance(ficha, dict) else None
            print(f"  ficha /info, campos: {', '.join(sorted(d)) if isinstance(d, dict) else '(nada)'}")
            primera = False
        c["market_cap"] = market_cap(ficha)
    grandes = {m: c for m, c in candidatas.items() if (c.get("market_cap") or 0) >= MIN_MC}
    print(f"  con {MIN_MC / 1000:.0f}k o mas: {len(grandes)}")

    if rpc:
        print("\n== QUIEN LAS MINTEO (Helius, primera transaccion de cada una)")
        config = {"transactionDetails": "full", "limit": 1, "sortOrder": "asc",
                  "encoding": "json", "maxSupportedTransactionVersion": 0,
                  "commitment": "confirmed"}
        for m, c in grandes.items():
            try:
                pag = da._rpc(rpc, "getTransactionsForAddress", [m, config]) or {}  # noqa: SLF001
                txs = pag.get("data") if isinstance(pag, dict) else None
                c["dev"] = dev_de(txs[0]) if txs else None
            except Exception as e:  # noqa: BLE001
                c["dev"] = None
                c["error"] = da._tapa(f"{type(e).__name__}: {e}")[:100]  # noqa: SLF001
            time.sleep(da.PAUSA)

    previo = {}
    if SALIDA.is_file():
        try:
            previo = json.loads(SALIDA.read_text(encoding="utf-8")).get("monedas", {})
        except (OSError, json.JSONDecodeError):
            previo = {}
    ahora = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    for m, c in grandes.items():
        previo[m] = {**previo.get(m, {}), **c, "visto": ahora}
    nuestros = set(da.cargar_devs())
    devs: dict[str, list[str]] = {}
    for m, c in previo.items():
        if c.get("dev"):
            devs.setdefault(c["dev"], []).append(m)
    SALIDA.write_text(json.dumps({
        "que_es": __doc__.split("\n\n")[0],
        "actualizado": ahora,
        "monedas": previo,
        "devs": {d: {"monedas_100k": ms, "ya_en_nuestras_listas": d in nuestros}
                 for d, ms in sorted(devs.items(), key=lambda x: -len(x[1]))},
    }, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\n== DEVS CON MONEDAS DE {MIN_MC / 1000:.0f}k+ (acumulado)")
    for d, ms in sorted(devs.items(), key=lambda x: -len(x[1]))[:25]:
        print(f"{d}  {len(ms)} moneda(s)  {'(ya en nuestras listas)' if d in nuestros else 'NUEVO'}")
    print(f"\nTodo en {SALIDA}")


if __name__ == "__main__":
    main()
