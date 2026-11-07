"""Listas de devs del ultimo mes sacadas de Dune (pedido el 7-nov).

Manu: "extraeme todos los devs activos de este mes que minimo saquen un
proyecto por semana, su tasa de migracion sea superior al 60% y su mediana,
moda y frecuencia sean mas de 20k, asi como todos los devs que pagan
DexScreener; ninguno podra hacer rugs. Lista de devs que superaron los 300k
este mes y otra de un millon, todos activos; devs que suelan superar los 80k.
Descarta compras de una misma wallet o mechas que en menos de diez segundos
se disparan: tienen que ser organicos. Filtra tambien por devs que paguen
boosts de DexScreener o NITRO de DEXTools."

COMO. Una sola consulta a Dune (deploy/dune/devs_mes.sql) saca, por dev, todo
lo que hace falta; los cortes se aplican aqui. El resultado crudo se guarda en
/root/devs_mes_dune.json: con --sin-dune se vuelven a aplicar los cortes sin
gastar creditos. Lo de DexScreener (si paga, boosts) no esta en Dune: se mira
token a token en su API. DEXTools NITRO no tiene historico en su API: no se
puede filtrar por eso.

No compra, no vende, no toca el bot. La clave de Dune se lee del .env y no se
escribe nunca.

    cd /root/ai-bot && .venv/bin/python deploy/dune_devs_mes.py
    cd /root/ai-bot && .venv/bin/python deploy/dune_devs_mes.py --sin-dune
"""

# ruff: noqa: TRY003, PLR2004, S310

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SQL = Path(__file__).resolve().parent / "dune" / "devs_mes.sql"
CRUDO = Path("/root/devs_mes_dune.json")
SALIDA = Path("/root/devs_mes.json")
DUNE = "https://api.dune.com/api/v1"

#: Los cortes de Manu. Se pueden cambiar sin volver a llamar a Dune.
CORTES = {
    "semanas_activas": 4,  # "minimo sacan un proyecto por semana" (4 semanas)
    "pct_migra": 60.0,  # "tasa de migracion superior al 60%"
    "mediana_usd": 20000,  # "mediana ... mas de 20k"
    "moda_usd": 20000,  # "moda ... mas de 20k"
    "frec_20k": 60.0,  # "frecuencia mas de 20k": el 60%+ de sus monedas
    "frec_80k": 50.0,  # "suelan superar los 80k": la mitad o mas
}


# --- Clasificar (sin red) ---
def limpio(f: dict) -> bool:
    """Sin rugs, sin mechas de 10 s y sin compras de una misma cartera."""
    return (
        not int(f.get("rugs") or 0)
        and not int(f.get("mechas") or 0)
        and not int(f.get("misma_wallet") or 0)
    )


def activo(f: dict) -> bool:
    """Ha lanzado algo en la ultima semana."""
    return int(f.get("lanz_7d") or 0) >= 1


def clasifica(filas: list[dict], cortes: dict | None = None) -> dict[str, list[dict]]:
    """Las listas que pidio Manu. Todas sin rugs, sin mechas y organicas."""
    c = {**CORTES, **(cortes or {})}
    buenos = [f for f in filas if limpio(f)]
    return {
        "activos_calidad": [
            f
            for f in buenos
            if activo(f)
            and int(f.get("semanas_activas") or 0) >= c["semanas_activas"]
            and float(f.get("pct_migra") or 0) > c["pct_migra"]
            and float(f.get("mediana_usd") or 0) > c["mediana_usd"]
            and float(f.get("moda_usd") or 0) > c["moda_usd"]
            and float(f.get("frec_20k") or 0) >= c["frec_20k"]
        ],
        "superaron_300k": [
            f for f in buenos if activo(f) and int(f.get("n_300k") or 0) >= 1
        ],
        "superaron_1m": [
            f for f in buenos if activo(f) and int(f.get("n_1m") or 0) >= 1
        ],
        "suelen_80k": [
            f
            for f in buenos
            if int(f.get("lanzamientos") or 0) >= 2
            and float(f.get("frec_80k") or 0) >= c["frec_80k"]
        ],
    }


def trades_al_dia(devs: list[dict]) -> int:
    return int(sum(float(f.get("trades_dia") or 0) for f in devs))


def lanzamientos_al_dia(devs: list[dict]) -> float:
    return round(sum(float(f.get("lanzamientos") or 0) for f in devs) / 30.0, 1)


# ------------------------------------------------------------------- red
def _clave() -> str | None:
    """La clave de Dune del .env, se llame como se llame (DUNE_API_KEY...)."""
    for k, v in os.environ.items():
        if "DUNE" in k.upper() and v.strip():
            return v.strip()
    return None


def _pide(url: str, clave: str | None = None, cuerpo: dict | None = None) -> dict:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method="POST" if datos else "GET")
    req.add_header("User-Agent", "pump-bot")
    req.add_header("Content-Type", "application/json")
    if clave:
        req.add_header("X-Dune-API-Key", clave)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def _error(e: Exception) -> str:
    """El error de Dune, sin la clave."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            cuerpo = e.read().decode()[:600]
        except Exception:  # noqa: BLE001
            cuerpo = ""
        return f"HTTP {e.code}: {cuerpo}"
    return type(e).__name__


def _lanza(sql: str, clave: str, rendimiento: str) -> dict:
    """Manda la consulta a Dune por la primera via que acepte.

    7-nov: la primera prueba en el droplet dio "HTTP 400" en /sql/execute.
    Se prueban, en orden: /sql/execute con y sin `performance`, y crear una
    consulta (privada; si el plan no deja, publica: solo contiene el SQL) y
    ejecutarla. Cada rechazo se imprime entero (sin la clave) para saber por
    que.
    """
    intentos = [
        (
            "sql/execute",
            f"{DUNE}/sql/execute",
            {"sql": sql, "performance": rendimiento},
        ),
        ("sql/execute sin performance", f"{DUNE}/sql/execute", {"sql": sql}),
    ]
    for nombre, url, cuerpo in intentos:
        try:
            return _pide(url, clave, cuerpo)
        except urllib.error.HTTPError as e:
            print(f"  {nombre}: {_error(e)}")
    for privada in (True, False):
        try:
            q = _pide(
                f"{DUNE}/query",
                clave,
                {
                    "name": "pump-bot devs del mes",
                    "query_sql": sql,
                    "is_private": privada,
                },
            )
        except urllib.error.HTTPError as e:
            print(
                f"  crear consulta {'privada' if privada else 'publica'}: {_error(e)}"
            )
            continue
        qid = q.get("query_id")
        print(f"  consulta {'privada' if privada else 'publica'} creada en Dune: {qid}")
        try:
            return _pide(
                f"{DUNE}/query/{qid}/execute", clave, {"performance": rendimiento}
            )
        except urllib.error.HTTPError as e:
            print(f"  ejecutar consulta {qid}: {_error(e)}")
            return _pide(f"{DUNE}/query/{qid}/execute", clave, {})
    raise RuntimeError("Dune no acepto la consulta por ninguna via (ver arriba)")


def dune_sql(sql: str, clave: str, rendimiento: str = "medium") -> list[dict]:
    """Ejecuta SQL en Dune y devuelve todas las filas."""
    ej = _lanza(sql, clave, rendimiento)
    eid = ej.get("execution_id")
    if not eid:
        raise RuntimeError(f"Dune no devolvio ejecucion: {str(ej)[:200]}")
    inicio = time.time()
    while True:
        st = _pide(f"{DUNE}/execution/{eid}/status", clave)
        estado = st.get("state", "")
        if estado == "QUERY_STATE_COMPLETED":
            break
        if estado in (
            "QUERY_STATE_FAILED",
            "QUERY_STATE_CANCELLED",
            "QUERY_STATE_EXPIRED",
        ):
            raise RuntimeError(f"Dune: {estado} {str(st.get('error'))[:400]}")
        if time.time() - inicio > 1800:
            raise RuntimeError("Dune tardo mas de 30 min")
        print(f"  ... {estado or 'esperando'} ({int(time.time() - inicio)} s)")
        time.sleep(10)
    filas: list[dict] = []
    offset = 0
    while True:
        r = _pide(f"{DUNE}/execution/{eid}/results?limit=5000&offset={offset}", clave)
        lote = ((r.get("result") or {}).get("rows")) or []
        filas += lote
        if not r.get("next_offset") or not lote:
            break
        offset = int(r["next_offset"])
    coste = (
        (st.get("execution_cost_credits") or st.get("cost"))
        if isinstance(st, dict)
        else None
    )
    print(f"  Dune: {len(filas)} devs" + (f" · {coste} creditos" if coste else ""))
    return filas


def precio_sol() -> float:
    try:
        d = _pide(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        )
        return float(d["solana"]["usd"])
    except Exception:  # noqa: BLE001
        print("  (no pude leer el precio de SOL; uso 150 $)")
        return 150.0


def dexscreener(mints: list[str], max_consultas: int) -> dict[str, dict]:
    """Por moneda: si tiene un pago aprobado en DexScreener y sus boosts activos."""
    fuera: dict[str, dict] = {}
    for i in range(0, min(len(mints), max_consultas), 30):
        lote = mints[i : i + 30]
        try:
            pares = _pide(
                "https://api.dexscreener.com/tokens/v1/solana/" + ",".join(lote)
            )
        except Exception:  # noqa: BLE001
            pares = []
        for p in pares if isinstance(pares, list) else []:
            m = ((p.get("baseToken") or {}).get("address")) or ""
            b = int(((p.get("boosts") or {}).get("active")) or 0)
            fuera.setdefault(m, {"boosts": 0, "paga": False})
            fuera[m]["boosts"] = max(fuera[m]["boosts"], b)
            fuera[m]["paga"] = fuera[m]["paga"] or bool(p.get("info"))
        time.sleep(0.25)
    return fuera


# ------------------------------------------------------------------ informe
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--sin-dune",
        action="store_true",
        help="reaplica los cortes a lo ya bajado, sin gastar creditos",
    )
    p.add_argument("--rendimiento", default="medium", choices=["medium", "large"])
    p.add_argument(
        "--max-dex", type=int, default=900, help="monedas maximo a mirar en DexScreener"
    )
    a = p.parse_args()

    if a.sin_dune:
        filas = json.loads(CRUDO.read_text(encoding="utf-8"))["filas"]
    else:
        from dotenv import load_dotenv  # noqa: PLC0415

        load_dotenv(RAIZ / ".env")
        clave = _clave()
        if not clave:
            print("No encuentro la clave de Dune en el .env (una variable con DUNE).")
            raise SystemExit(2)
        sol = precio_sol()
        print(f"== DUNE (SOL a {sol:.2f} $). Puede tardar varios minutos.")
        sql = SQL.read_text(encoding="utf-8").replace("{SOL_USD}", f"{sol:.4f}")
        try:
            filas = dune_sql(sql, clave, a.rendimiento)
        except Exception as e:  # noqa: BLE001
            print(f"FALLO: {e if isinstance(e, RuntimeError) else _error(e)}")
            raise SystemExit(1) from None
        CRUDO.write_text(
            json.dumps({"ts": time.time(), "sol": sol, "filas": filas}),
            encoding="utf-8",
        )

    listas = clasifica(filas)
    sucios = [f for f in filas if not limpio(f)]
    print(f"\n== {len(filas)} devs con 4+ lanzamientos o algun pico de 80k+")
    print(f"   descartados por rug / mecha / misma wallet: {len(sucios)}")

    # DexScreener: quien paga y quien tiene 100+ boosts, entre los activos y limpios.
    candidatos = [f for f in filas if limpio(f) and activo(f)]
    mints = [m for f in candidatos for m in (f.get("mints") or [])]
    print(
        f"\n== DEXSCREENER: miro {min(len(mints), a.max_dex)} de {len(mints)} monedas"
    )
    dx = dexscreener(mints, a.max_dex)
    for f in candidatos:
        datos = [dx[m] for m in (f.get("mints") or []) if m in dx]
        f["paga_dex"] = any(d["paga"] for d in datos)
        f["max_boosts"] = max((d["boosts"] for d in datos), default=0)
    listas["pagan_dexscreener"] = [f for f in candidatos if f.get("paga_dex")]
    listas["boost_100"] = [f for f in candidatos if f.get("max_boosts", 0) >= 100]

    for nombre, devs in listas.items():
        print(
            f"\n== {nombre}: {len(devs)} devs · {lanzamientos_al_dia(devs)} "
            f"lanzamientos/dia · {trades_al_dia(devs)} trades/dia"
        )
        for f in sorted(devs, key=lambda x: -float(x.get("mediana_usd") or 0))[:10]:
            print(
                f"   {f['dev']}  lanz {f.get('lanzamientos')} · migra "
                f"{f.get('pct_migra')}% · mediana {float(f.get('mediana_usd') or 0):,.0f} $"
                f" · max {float(f.get('max_pico_usd') or 0):,.0f} $"
            )
    union = {f["dev"]: f for devs in listas.values() for f in devs}
    print(
        f"\n== TODAS JUNTAS: {len(union)} devs · "
        f"{lanzamientos_al_dia(list(union.values()))} lanzamientos al dia"
    )
    print("   (DEXTools NITRO: su API no da historico de pagos; no se puede filtrar)")
    SALIDA.write_text(
        json.dumps(
            {
                "que_es": __doc__.split("\n\n")[0],
                "cortes": CORTES,
                "listas": {k: [f["dev"] for f in v] for k, v in listas.items()},
                "detalle": union,
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nTodo en {SALIDA}  (crudo de Dune en {CRUDO})")


if __name__ == "__main__":
    sys.exit(main())
