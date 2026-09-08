"""Win rate y ROI mediano REALES de una wallet que COMPRA (un caller/KOL).

    ./.venv/bin/python analizar_caller.py 94ZSpiQKTZAwy8C8jvuE4kvjsUuHUmp3XDQA1pAzYcU3

POR QUE EXISTE
--------------
Los perfiles de las apps de callers ordenan las operaciones por GANANCIA, asi
que solo se ven las ganadoras y cualquier win rate sacado de ahi da 100%. Las
perdedoras estan al final de una lista que nadie captura. Esto lee la CADENA,
que no ordena nada y no esconde nada.

COMO MIDE
---------
No intenta entender las instrucciones de pump.fun. Para cada transaccion mira
dos cosas del propio firmante:

  1. cuanto SOL le entra o le sale (post - pre, sumando la comision pagada),
  2. cuanto cambia su saldo de cada token (pre/postTokenBalances).

Token que SUBE + SOL que baja = compra. Token que BAJA + SOL que sube = venta.
Se agrupa por mint y una posicion cuenta como CERRADA cuando el saldo del token
vuelve a cero (con margen del 1% por polvo). El ROI es entonces
(SOL_recibido - SOL_gastado) / SOL_gastado.

LO QUE NO MIDE, Y HAY QUE SABERLO
---------------------------------
  - Tokens que entran o salen por TRANSFERENCIA (airdrops, mover a otra wallet
    propia) ensucian el saldo y la posicion se marca `dudosa`, no se cuenta.
  - Solo mira las ultimas `--max` firmas: es una foto reciente, no la vida
    entera de la wallet.
  - Las posiciones todavia ABIERTAS no cuentan. Eso sesga a favor del caller si
    tiene perdedoras sin cerrar ("no es perdida hasta que vendes"), asi que el
    script las cuenta aparte y avisa.
"""

import json
import statistics
import sys
import time
import urllib.request

LAMPORTS = 1_000_000_000
POLVO = 0.01  # 1% del maximo tenido: por debajo se considera cerrada


def _rpc_url() -> str:
    """RPC del .env, que es donde ya vive el de Helius."""
    for linea in open(".env", encoding="utf-8"):
        if linea.startswith("SOLANA_NODE_RPC_ENDPOINT"):
            return linea.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("No hay SOLANA_NODE_RPC_ENDPOINT en .env")


def _rpc(url: str, metodo: str, params: list, reintentos: int = 4):
    cuerpo = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params}
    ).encode()
    for intento in range(reintentos):
        try:
            req = urllib.request.Request(
                url, data=cuerpo, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r).get("result")
        except Exception:
            if intento == reintentos - 1:
                return None
            time.sleep(2**intento)  # 429 y cortes: no insistir a ciegas
    return None


def firmas(url: str, wallet: str, maximo: int) -> list[str]:
    """Firmas de la wallet, de la mas nueva a la mas vieja."""
    out, antes = [], None
    while len(out) < maximo:
        params = [wallet, {"limit": min(1000, maximo - len(out))}]
        if antes:
            params[1]["before"] = antes
        lote = _rpc(url, "getSignaturesForAddress", params) or []
        if not lote:
            break
        out += [f["signature"] for f in lote if not f.get("err")]
        antes = lote[-1]["signature"]
        if len(lote) < 1000:
            break
    return out


def movimiento(tx: dict, wallet: str) -> tuple[str, float, float] | None:
    """Devuelve (mint, delta_tokens, delta_sol) de ESTA tx para la wallet."""
    meta, msg = tx.get("meta"), tx["transaction"]["message"]
    if not meta:
        return None
    claves = [
        k["pubkey"] if isinstance(k, dict) else k for k in msg.get("accountKeys", [])
    ]
    if wallet not in claves:
        return None
    i = claves.index(wallet)

    # SOL: se suma la comision porque la paga el firmante y no es parte del trade.
    d_sol = (meta["postBalances"][i] - meta["preBalances"][i]) / LAMPORTS
    if i == 0:
        d_sol += meta.get("fee", 0) / LAMPORTS

    pre = {
        b["mint"]: float(b["uiTokenAmount"]["uiAmount"] or 0)
        for b in meta.get("preTokenBalances", [])
        if b.get("owner") == wallet
    }
    post = {
        b["mint"]: float(b["uiTokenAmount"]["uiAmount"] or 0)
        for b in meta.get("postTokenBalances", [])
        if b.get("owner") == wallet
    }
    for mint in set(pre) | set(post):
        d_tok = post.get(mint, 0) - pre.get(mint, 0)
        if abs(d_tok) > 0:
            return mint, d_tok, d_sol
    return None


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    wallet = sys.argv[1]
    maximo = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    url = _rpc_url()

    print(f"Wallet   : {wallet}")
    print(f"Leyendo hasta {maximo} firmas...", flush=True)
    sigs = firmas(url, wallet, maximo)
    print(f"Firmas OK: {len(sigs)}\n", flush=True)

    pos: dict[str, dict] = {}
    for n, s in enumerate(sigs, 1):
        if n % 100 == 0:
            print(f"  {n}/{len(sigs)}", flush=True)
        tx = _rpc(
            url,
            "getTransaction",
            [s, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        if not tx:
            continue
        mv = movimiento(tx, wallet)
        if not mv:
            continue
        mint, d_tok, d_sol = mv
        p = pos.setdefault(
            mint, {"tok": 0.0, "pico": 0.0, "gastado": 0.0, "recibido": 0.0, "ops": 0}
        )
        p["tok"] += d_tok
        p["pico"] = max(p["pico"], p["tok"])
        p["ops"] += 1
        if d_tok > 0 and d_sol < 0:
            p["gastado"] += -d_sol
        elif d_tok < 0 and d_sol > 0:
            p["recibido"] += d_sol

    cerradas, abiertas, dudosas = [], 0, 0
    for mint, p in pos.items():
        if p["gastado"] <= 0:
            dudosas += 1  # llego por transferencia, no por compra
            continue
        if p["tok"] > p["pico"] * POLVO:
            abiertas += 1
            continue
        cerradas.append(((p["recibido"] - p["gastado"]) / p["gastado"] * 100, mint))

    print("\n" + "=" * 58)
    if not cerradas:
        print("Sin posiciones cerradas medibles.")
        print(f"  abiertas {abiertas} · dudosas {dudosas}")
        return

    rois = sorted(r for r, _ in cerradas)
    ganadoras = sum(1 for r in rois if r > 0)
    wr = ganadoras / len(rois) * 100
    print(f"POSICIONES CERRADAS : {len(rois)}")
    print(f"WIN RATE            : {wr:.1f}%  ({ganadoras} de {len(rois)})")
    print(f"ROI MEDIANO         : {statistics.median(rois):+.1f}%")
    print(f"ROI MEDIO           : {statistics.mean(rois):+.1f}%")
    print(f"  peor {rois[0]:+.1f}%   mejor {rois[-1]:+.1f}%")
    perd = [r for r in rois if r <= 0]
    if perd:
        print(f"  perdida mediana cuando falla: {statistics.median(perd):+.1f}%")
    print(f"\nNo contadas: {abiertas} abiertas · {dudosas} dudosas")
    print("=" * 58)
    print("\nTU FILTRO (win rate > 60% y ROI mediano > 250%):")
    print(f"  win rate    {'PASA' if wr > 60 else 'NO'}")
    print(f"  ROI mediano {'PASA' if statistics.median(rois) > 250 else 'NO'}")


if __name__ == "__main__":
    main()
