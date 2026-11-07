"""¿Siguen lanzando los devs de nuestras listas? Medido con Helius.

Pedido el 7-nov: "revisa todas las listas de devs que tenemos si siguen
activos, los de mediana etc".

POR QUE NO VALE `medir_devs.py`. Pregunta a la API de pump.fun, y pump.fun
contesta 403 al droplet. Este lee la cadena con el mismo Helius del bot.

QUE HACE, EN DOS PASOS (para gastar poco Helius):
  1. A TODOS: la ultima transaccion de su cartera (getSignaturesForAddress,
     1 consulta por dev). Sin nada en 30 dias, el dev esta parado.
  2. SOLO A LOS QUE SE MOVIERON EN 30 DIAS: sus ultimas 100 transacciones
     (getTransactionsForAddress, 1 consulta), para contar cuantas MONEDAS DE
     PUMP.FUN ha creado en 7 y en 30 dias y cuando fue la ultima. Cuentan las
     que creo con su cartera y las que minteo a traves de una plataforma.

La mediana y el pico de cada dev NO se vuelven a medir (haria falta leer cada
moneda): se copian de las listas, que son de julio-septiembre.

No compra, no vende, no cambia ninguna configuracion. No ensena la clave.
Escribe /root/devs_activos.json (fuera del repo, para que el arrancador no lo
aparte al limpiar).

    cd /root/ai-bot && PYTHONPATH=src .venv/bin/python deploy/devs_activos.py
    ... deploy/devs_activos.py 30      # solo los 30 primeros (prueba)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = Path("/root/devs_activos.json")
DIA = 86400.0
#: Pausa entre consultas: el bot comparte este Helius y no se le puede quitar
#: el sitio. ~4 por segundo.
PAUSA = 0.25
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def _tapa(texto: str) -> str:
    """Quita cualquier api-key de un texto antes de escribirlo."""
    return re.sub(r"(api[-_]?key=)[^&\s\"']+", r"\1***", texto, flags=re.IGNORECASE)


def _numero(v: object) -> float | None:
    try:
        if isinstance(v, str):
            v = v.replace("$", "").replace(",", "").strip()
            if v.upper().endswith("K"):
                return float(v[:-1]) * 1000
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def cargar_devs(raiz: Path = RAIZ) -> dict[str, dict]:  # noqa: C901
    """Todas las carteras de todas las listas, con lo que dicen de cada una."""
    devs: dict[str, dict] = {}

    def apunta(x: dict, lista: str) -> None:
        w = str(x.get("wallet") or "").strip()
        if len(w) < 32:  # noqa: PLR2004
            return
        d = devs.setdefault(w, {"listas": [], "mediana_pico": None, "mejor_pico": None})
        if lista not in d["listas"]:
            d["listas"].append(lista)
        for k in ("median_peak", "median_usd", "med", "mediana_pico_usd"):
            v = _numero(x.get(k))
            if v and v < 5e8 and (d["mediana_pico"] or 0) < v:  # noqa: PLR2004
                d["mediana_pico"] = v
        for k in ("best_peak_usd", "mejor_pico_usd"):
            v = _numero(x.get(k))
            if v and v < 5e8 and (d["mejor_pico"] or 0) < v:  # noqa: PLR2004
                d["mejor_pico"] = v

    def recorre(x: object, lista: str) -> None:
        if isinstance(x, dict):
            if "wallet" in x:
                apunta(x, lista)
            for v in x.values():
                recorre(v, lista)
        elif isinstance(x, list):
            for v in x:
                recorre(v, lista)

    ficheros = [*sorted(raiz.glob("top_devs*.json")), raiz / "devs_unificados.json"]
    for f in ficheros:
        if f.is_file():
            try:
                recorre(json.loads(f.read_text(encoding="utf-8")), f.stem)
            except (OSError, json.JSONDecodeError):
                continue
    crudo = raiz / "mayhem_v2_raw.csv"
    if crudo.is_file():
        with crudo.open(encoding="utf-8") as fh:
            for fila in csv.DictReader(fh):
                apunta(fila, crudo.stem)
    vetados: set[str] = set()
    negra = raiz / "blacklist_devs.json"
    if negra.is_file():
        texto = negra.read_text(encoding="utf-8")
        vetados = set(re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", texto))
    for w, d in devs.items():
        d["en_blacklist"] = w in vetados
    return devs


def lanzamientos(txs: list[dict], wallet: str, ahora: float) -> dict:
    """Monedas de pump.fun que `wallet` creo o minteo, en estas transacciones.

    Se reconoce la creacion por la INSTRUCCION de pump.fun (create o
    create_v2) dentro de una transaccion que `wallet` firmo, incluidas las
    lanzadas a traves de una plataforma. No hace falta leer los logs.
    """
    import base58  # noqa: PLC0415

    crear = {bytes.fromhex("181ec828051c0777"), bytes.fromhex("d6904cec5f8b31b4")}
    tiempos: list[float] = []
    mints: list[str] = []
    for tx in txs:
        try:
            msg = tx["transaction"]["message"]
            meta = tx.get("meta") or {}
            if meta.get("err") is not None:
                continue
            claves = list(msg["accountKeys"])
            firmantes = claves[: int(msg["header"]["numRequiredSignatures"])]
            if wallet not in firmantes:
                continue
            cargadas = meta.get("loadedAddresses") or {}
            todas = claves + list(cargadas.get("writable") or []) + list(
                cargadas.get("readonly") or []
            )
            ixs = list(msg.get("instructions") or [])
            for interna in meta.get("innerInstructions") or []:
                ixs.extend(interna.get("instructions") or [])
            creaciones = [
                ix for ix in ixs
                if isinstance(ix.get("programIdIndex"), int)
                and 0 <= ix["programIdIndex"] < len(todas)
                and todas[ix["programIdIndex"]] == PUMP
                and base58.b58decode(ix.get("data") or "")[:8] in crear
            ]
            if creaciones and tx.get("blockTime"):
                tiempos.append(float(tx["blockTime"]))
                # La primera cuenta de create/create_v2 es el mint.
                cuentas = creaciones[0].get("accounts") or []
                if cuentas and isinstance(cuentas[0], int) and cuentas[0] < len(todas):
                    mints.append(str(todas[cuentas[0]]))
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "lanzadas_7d": sum(1 for t in tiempos if ahora - t <= 7 * DIA),
        "lanzadas_30d": sum(1 for t in tiempos if ahora - t <= 30 * DIA),
        "ultima_lanzada_hace_dias": round((ahora - max(tiempos)) / DIA, 1)
        if tiempos
        else None,
        # Las mas recientes primero (las transacciones vienen de nueva a vieja).
        "mints_recientes": mints[:10],
    }


def estado(dias_ultima_tx: float | None, lanz: dict | None) -> str:
    """Una palabra para cada dev."""
    if dias_ultima_tx is None:
        return "sin datos"
    if lanz and lanz.get("lanzadas_7d"):
        return "LANZA (7 dias)"
    if lanz and lanz.get("lanzadas_30d"):
        return "lanza (30 dias)"
    if dias_ultima_tx <= 30:  # noqa: PLR2004
        return "se mueve, sin lanzar"
    return "PARADO"


def _rpc(url: str, metodo: str, params: list) -> object:
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params})
    peticion = urllib.request.Request(  # noqa: S310 -- la URL es la del .env
        url, data=cuerpo.encode(), headers={"Content-Type": "application/json"}
    )
    for intento in range(3):
        try:
            with urllib.request.urlopen(peticion, timeout=20) as r:  # noqa: S310
                datos = json.load(r)
            if datos.get("error"):
                raise RuntimeError(str(datos["error"].get("message", ""))[:80])
            return datos.get("result")
        except urllib.error.HTTPError as e:
            if e.code == 429 and intento < 2:  # noqa: PLR2004
                time.sleep(2 + intento * 2)
                continue
            raise RuntimeError(f"HTTP {e.code}") from None  # noqa: TRY003
    return None


def main() -> None:  # noqa: C901, PLR0915
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(RAIZ / ".env")
    url = os.environ.get("SOLANA_NODE_RPC_ENDPOINT", "")
    if not url:
        print("Falta SOLANA_NODE_RPC_ENDPOINT en el .env")
        sys.exit(1)
    devs = cargar_devs()
    carteras = list(devs)
    if len(sys.argv) > 1:
        carteras = carteras[: int(sys.argv[1])]
    ahora = time.time()
    n_listas = len({lista for d in devs.values() for lista in d["listas"]})
    print(f"{len(carteras)} devs de {n_listas} listas. Paso 1...")

    for n, w in enumerate(carteras, 1):
        d = devs[w]
        try:
            firmas = _rpc(url, "getSignaturesForAddress", [w, {"limit": 1}]) or []
            bt = firmas[0].get("blockTime") if firmas else None
            d["ultima_tx_hace_dias"] = round((ahora - bt) / DIA, 1) if bt else None
        except Exception as e:  # noqa: BLE001
            d["ultima_tx_hace_dias"] = None
            d["error"] = _tapa(f"{type(e).__name__}: {e}")[:100]
        if n % 100 == 0:
            print(f"  {n}/{len(carteras)}")
        time.sleep(PAUSA)

    vivos = [
        w for w in carteras
        if devs[w].get("ultima_tx_hace_dias") is not None
        and devs[w]["ultima_tx_hace_dias"] <= 30  # noqa: PLR2004
    ]
    print(f"Paso 2: {len(vivos)} se han movido en 30 dias; se cuentan sus lanzamientos...")
    config = {
        "transactionDetails": "full", "limit": 100, "sortOrder": "desc",
        "encoding": "json", "maxSupportedTransactionVersion": 0,
        "commitment": "confirmed",
        "filters": {"status": "succeeded", "tokenAccounts": "none"},
    }
    for n, w in enumerate(vivos, 1):
        try:
            pagina = _rpc(url, "getTransactionsForAddress", [w, config]) or {}
            txs = pagina.get("data") if isinstance(pagina, dict) else None
            devs[w]["lanzamientos"] = lanzamientos(txs or [], w, ahora)
        except Exception as e:  # noqa: BLE001
            devs[w]["error"] = _tapa(f"{type(e).__name__}: {e}")[:100]
        if n % 25 == 0:
            print(f"  {n}/{len(vivos)}")
        time.sleep(PAUSA)

    filas = []
    for w in carteras:
        d = devs[w]
        lanz = d.get("lanzamientos")
        filas.append({
            "wallet": w,
            "estado": estado(d.get("ultima_tx_hace_dias"), lanz),
            "ultima_tx_hace_dias": d.get("ultima_tx_hace_dias"),
            **(lanz or {}),
            "mediana_pico_de_las_listas": d.get("mediana_pico"),
            "mejor_pico_de_las_listas": d.get("mejor_pico"),
            "en_blacklist": d.get("en_blacklist", False),
            "listas": d["listas"],
            **({"error": d["error"]} if d.get("error") else {}),
        })
    orden = {"LANZA (7 dias)": 0, "lanza (30 dias)": 1, "se mueve, sin lanzar": 2,
             "PARADO": 3, "sin datos": 4}
    filas.sort(key=lambda f: (orden.get(f["estado"], 9), -(f["mediana_pico_de_las_listas"] or 0)))
    SALIDA.write_text(json.dumps({
        "generado": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ahora)),
        "que_es": "devs de todas las listas del repo, mirados en la cadena con Helius",
        "devs": filas,
    }, indent=1, ensure_ascii=False), encoding="utf-8")

    cuenta: dict[str, int] = {}
    for f in filas:
        cuenta[f["estado"]] = cuenta.get(f["estado"], 0) + 1
    print("\n== RESUMEN")
    for k in orden:
        print(f"{cuenta.get(k, 0):6}  {k}")
    print("\n== LOS QUE LANZAN, por mediana de pico (de las listas)")
    for f in [f for f in filas if f["estado"].lower().startswith("lanza")][:25]:
        med = f["mediana_pico_de_las_listas"]
        print(
            f"{f['wallet']}  {f.get('lanzadas_7d', 0):3} en 7d  "
            f"{f.get('lanzadas_30d', 0):3} en 30d  mediana "
            f"{(f'{med / 1000:.0f}k' if med else '?'):>6}"
            f"{'  BLACKLIST' if f['en_blacklist'] else ''}"
        )
    print(f"\nTodo en {SALIDA}")


if __name__ == "__main__":
    main()
