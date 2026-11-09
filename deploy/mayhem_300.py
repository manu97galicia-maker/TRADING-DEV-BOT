"""Monedas MAYHEM que suben un 300% o mas: que tenian en comun al principio.

Pedido el 7-nov: "puedes analizar las de mayhem que se hagan un 300% o mas,
que caracteristicas tienen. Sin parar el bot".

No compra, no vende, no toca el bot ni su configuracion. Solo lee la cadena
con el Helius del .env y escribe /root/mayhem_300.json.

    cd /root/ai-bot && nice -n 19 .venv/bin/python deploy/mayhem_300.py
    ... --horas 12 --max-monedas 120 --decision 10 --sol 91

COMO LO HACE
1. Busca monedas mayhem de las ultimas --horas: las operaciones de una moneda
   mayhem pagan la comision a una cuenta propia de pump.fun (MAYHEM_FEE); de
   ahi salen los mints, y cada uno se confirma con la marca `mayhem_mode` que
   lleva el propio TradeEvent.
2. De cada moneda baja sus operaciones de la bonding curve: las primeras
   enteras (de ahi salen las senales) y del resto una de cada pocas (de ahi
   sale el pico).
3. EL MOMENTO DE DECISION (--decision, 10 s por defecto) es cuando el bot
   podria comprar. GANADORA = desde ese momento su market cap llego a x4 o
   mas (un +300%). Las senales se miden SOLO con lo que ya habia pasado en ese
   momento; si no, la cuenta haria trampa (una moneda que ya ha subido x4 a
   los 60 s "predice" que sube).
4. Compara ganadoras con el resto, senal por senal.

LIMITES, PARA LEERLO BIEN
- Solo ve la bonding curve. Con SOL a 91 $ la curva migra hacia los 37k: un
  x4 desde ~3-5k cabe de sobra dentro, pero lo que pase despues de migrar no
  se ve.
- El pico sale de una muestra de operaciones: puede quedarse un poco corto
  del maximo real, nunca pasarse.
- El agente de mayhem compra y vende en muchas monedas. Las carteras que
  aparecen en muchisimas monedas a la vez se apartan de "compradores
  distintos" y se listan aparte.
- Con pocas ganadoras (menos de ~15) esto describe casos, no demuestra
  nada.

GASTO DE HELIUS: lo dice al final (llamadas y transacciones leidas). Con los
valores por defecto, unas pocas decenas de miles de transacciones.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analizar_despegue as ad

#: Cuenta que cobra la comision de las operaciones mayhem (address_provider).
MAYHEM_FEE = "GesfTA3X2arioaHp8bbKdjG9vJtskViWACZoYvxp4twS"
#: Programa de mayhem: plan B si la cuenta de comision no diera nada.
MAYHEM_PROGRAMA = "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e"
SALIDA = Path("/root/mayhem_300.json")
SUPPLY_UNIDADES = ad.SUPPLY_TOTAL * 10**ad.DECIMALES_TOKEN
#: Operaciones del principio que se bajan enteras.
PRIMERAS = 300
#: Del resto, una de cada tantas (para el pico).
SALTO = 8
#: Una cartera que aparece en esta fraccion de monedas o mas no es un
#: comprador de esa moneda: es un bot que esta en todas (el agente mayhem).
FRACCION_COMUN = 0.4
MIN_PARA_COMUNES = 10
#: Un corte con menos monedas a un lado no dice nada.
MIN_POR_LADO = 3


# ------------------------------------------------------------- calculo puro
def mc_sol(ev: dict) -> float:
    """Market cap en SOL a partir de las reservas virtuales."""
    vsol = ev["virtual_sol_reserves"] / 10**ad.DECIMALES_SOL
    vtok = ev["virtual_token_reserves"] / 10**ad.DECIMALES_TOKEN
    return vsol / vtok * ad.SUPPLY_TOTAL


def _ultima_hasta(ops: list[dict], t: float) -> int:
    i = 0
    for j, e in enumerate(ops):
        if e["timestamp"] <= t:
            i = j
        else:
            break
    return i


def senales(ops: list[dict], decision: float, comunes: set[str]) -> dict | None:
    """Lo que se sabia de la moneda en el momento de decision, y su desenlace.

    `ops` en orden de la cadena. None si no se vio el nacimiento o no hay
    nada despues del momento de decision.
    """
    if not ops:
        return None
    t0 = ops[0]["timestamp"]
    slot0 = ops[0].get("_slot")
    creador = ops[0].get("creator") or ""
    td = t0 + decision
    i_dec = _ultima_hasta(ops, td)
    antes, despues = ops[: i_dec + 1], ops[i_dec + 1 :]
    if not despues:
        return None
    mc_nacer = mc_sol(ops[0])
    mc_dec = mc_sol(ops[i_dec])
    mcs_despues = [mc_sol(e) for e in despues]
    pico = max(mcs_despues)
    i_pico = mcs_despues.index(pico)

    bloque1 = [e for e in antes if e.get("_slot") == slot0]
    dev_compra = (
        sum(
            e["sol_amount"] for e in bloque1 if e["is_buy"] and e.get("user") == creador
        )
        / 1e9
    )
    b1_otros = [e for e in bloque1 if e["is_buy"] and e.get("user") != creador]

    def _ajenas(es: list[dict]) -> list[dict]:
        return [
            e for e in es if e.get("user") != creador and e.get("user") not in comunes
        ]

    compras = [e for e in _ajenas(antes) if e["is_buy"]]
    ventas = [e for e in _ajenas(antes) if not e["is_buy"]]
    sol_in = sum(e["sol_amount"] for e in compras) / 1e9
    sol_out = sum(e["sol_amount"] for e in ventas) / 1e9
    neto: Counter = Counter()
    for e in antes:
        neto[e.get("user")] += e["token_amount"] * (1 if e["is_buy"] else -1)
    gorda = max((v for u, v in neto.items() if u != creador), default=0)

    return {
        "mult_desde_decision": pico / mc_dec if mc_dec else 0.0,
        "seg_hasta_pico": despues[i_pico]["timestamp"] - t0,
        "migro": any(e.get("real_token_reserves") == 0 for e in ops),
        "dev_vende_antes_del_pico": any(
            not e["is_buy"] and e.get("user") == creador
            for e in ops[: i_dec + 2 + i_pico]
        ),
        "hora_utc": time.gmtime(t0).tm_hour,
        # --- senales: SOLO lo que ya se veia en el momento de decision
        "dev_compra_sol": round(dev_compra, 3),
        "bloque1_compradores": len({e.get("user") for e in b1_otros}),
        "bloque1_sol": round(sum(e["sol_amount"] for e in b1_otros) / 1e9, 3),
        "compradores": len({e.get("user") for e in compras}),
        "vendedores": len({e.get("user") for e in ventas}),
        "sol_comprado": round(sol_in, 3),
        "ratio_compra_venta": round(sol_in / sol_out, 2) if sol_out else 99.0,
        "subida_hasta_decision": round(mc_dec / mc_nacer, 3) if mc_nacer else 0.0,
        "dev_ya_vendio": any(
            not e["is_buy"] and e.get("user") == creador for e in antes
        ),
        "cartera_mas_gorda_pct": round(100 * gorda / SUPPLY_UNIDADES, 2),
        "cashback": any((e.get("cashback_fee_basis_points") or 0) > 0 for e in ops),
    }


SENALES = (
    "dev_compra_sol",
    "bloque1_compradores",
    "bloque1_sol",
    "compradores",
    "vendedores",
    "sol_comprado",
    "ratio_compra_venta",
    "subida_hasta_decision",
    "cartera_mas_gorda_pct",
)
SI_NO = ("dev_ya_vendio", "cashback")


def compara(filas: list[dict], umbral: float) -> list[dict]:
    """Por cada senal: mediana de ganadoras y resto, y el mejor corte.

    VENTAJA = puntos de % de ganadoras que hay de mas entre las que pasan el
    corte frente a las que no. Es lo que dice si la senal sirve.
    """
    gan = [f for f in filas if f["mult_desde_decision"] >= umbral]
    res = [f for f in filas if f["mult_desde_decision"] < umbral]
    fuera = []
    for s in SENALES:
        vals = sorted(f[s] for f in filas)
        mejor = None
        for q in (0.25, 0.5, 0.75):
            corte = vals[int(q * (len(vals) - 1))]
            pasan = [f for f in filas if f[s] >= corte]
            no = [f for f in filas if f[s] < corte]
            if len(pasan) < MIN_POR_LADO or len(no) < MIN_POR_LADO:
                continue
            p1 = (
                100
                * sum(f["mult_desde_decision"] >= umbral for f in pasan)
                / len(pasan)
            )
            p0 = 100 * sum(f["mult_desde_decision"] >= umbral for f in no) / len(no)
            if mejor is None or abs(p1 - p0) > abs(mejor["ventaja"]):
                mejor = {
                    "corte": corte,
                    "pasan": len(pasan),
                    "pct_pasan": round(p1),
                    "pct_no": round(p0),
                    "ventaja": round(p1 - p0),
                }
        fuera.append(
            {
                "senal": s,
                "mediana_ganadoras": statistics.median([f[s] for f in gan])
                if gan
                else None,
                "mediana_resto": statistics.median([f[s] for f in res])
                if res
                else None,
                "mejor_corte": mejor,
            }
        )
    for s in SI_NO:
        si = [f for f in filas if f[s]]
        no = [f for f in filas if not f[s]]
        fuera.append(
            {
                "senal": s,
                "si": len(si),
                "pct_ganan_si": round(
                    100 * sum(f["mult_desde_decision"] >= umbral for f in si) / len(si)
                )
                if si
                else None,
                "no": len(no),
                "pct_ganan_no": round(
                    100 * sum(f["mult_desde_decision"] >= umbral for f in no) / len(no)
                )
                if no
                else None,
            }
        )
    return fuera


def carteras_comunes(ops_por_moneda: dict[str, list[dict]]) -> set[str]:
    """Carteras que estan en muchisimas monedas a la vez (el agente, bots)."""
    if len(ops_por_moneda) < MIN_PARA_COMUNES:
        return set()
    cuenta: Counter = Counter()
    for ops in ops_por_moneda.values():
        cuenta.update({e.get("user") for e in ops})
    tope = FRACCION_COMUN * len(ops_por_moneda)
    return {u for u, n in cuenta.items() if n >= tope}


# --------------------------------------------------------------- lectura red
class Lector:
    def __init__(self, nodo: ad.Nodo) -> None:
        self.nodo = nodo
        self.tx_leidas = 0

    async def firmas_desde(
        self, direccion: str, desde: float, paginas: int
    ) -> list[dict]:
        fuera: list[dict] = []
        antes = None
        for _ in range(paginas):
            params: dict = {"limit": ad.PAGINA_FIRMAS}
            if antes:
                params["before"] = antes
            r = await self.nodo.pide(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [direccion, params],
                }
            )
            lote = (r or {}).get("result") or []
            if not lote:
                break
            fuera.extend(lote)
            antes = lote[-1]["signature"]
            if (lote[-1].get("blockTime") or 0) < desde or len(lote) < ad.PAGINA_FIRMAS:
                break
        return fuera

    async def eventos(self, firmas: list[str]) -> list[dict]:
        self.tx_leidas += len(firmas)
        txs = await self.nodo.transacciones(firmas)
        return [e for t in txs for e in ad.eventos_de_tx(t)]


async def descubre(lec: Lector, horas: float, muestra_tx: int) -> set[str]:
    """Mints mayhem con operaciones en las ultimas `horas`."""
    desde = time.time() - horas * 3600
    for origen in (MAYHEM_FEE, MAYHEM_PROGRAMA):
        sig = [
            f
            for f in await lec.firmas_desde(origen, desde, 60)
            if not f.get("err") and (f.get("blockTime") or 0) >= desde
        ]
        print(
            f"  {origen[:6]}...: {len(sig)} operaciones en {horas:g} h", file=sys.stderr
        )
        if not sig:
            continue
        paso = max(1, len(sig) // muestra_tx)
        evs = await lec.eventos([f["signature"] for f in sig[::paso]])
        mints = {e["mint"] for e in evs if e.get("mayhem_mode")}
        print(f"  monedas mayhem encontradas: {len(mints)}", file=sys.stderr)
        if mints:
            return mints
    return set()


async def ops_de(lec: Lector, mint: str) -> list[dict]:
    firmas = await lec.firmas_desde(ad.pda_curva(mint), 0, 30)
    buenas = [f["signature"] for f in firmas if not f.get("err")]
    buenas.reverse()  # de la mas vieja a la mas nueva
    elegidas = buenas[:PRIMERAS] + buenas[PRIMERAS::SALTO]
    if buenas and buenas[-1] not in elegidas:
        elegidas.append(buenas[-1])
    evs = [e for e in await lec.eventos(elegidas) if e["mint"] == mint]
    evs.sort(key=lambda e: (e.get("_slot") or 0, e["timestamp"]))
    return evs


# ------------------------------------------------------------------ informe
def imprime(
    filas: list[dict], comparacion: list[dict], a: argparse.Namespace, comunes: set[str]
) -> None:
    gan = [f for f in filas if f["mult_desde_decision"] >= a.x]
    print(f"\n== MONEDAS MAYHEM ANALIZADAS: {len(filas)}")
    print(
        f"   suben x{a.x:g} (+{(a.x - 1) * 100:.0f}%) o mas desde los {a.decision:g} s: "
        f"{len(gan)} ({100 * len(gan) / max(1, len(filas)):.0f}%)"
    )
    if comunes:
        print(
            f"   carteras apartadas por estar en casi todas (agente/bots): {len(comunes)}"
        )
    if gan:
        seg = statistics.median(f["seg_hasta_pico"] for f in gan)
        print(
            f"   las ganadoras tardan en hacer pico (mediana): {seg / 60:.1f} min; "
            f"migran {sum(f['migro'] for f in gan)} de {len(gan)}; "
            f"el dev vende antes del pico en {sum(f['dev_vende_antes_del_pico'] for f in gan)}"
        )
    print(f"\n== QUE TENIAN A LOS {a.decision:g} s (ganadoras vs resto)")
    print(f"{'senal':<24}{'ganadoras':>10}{'resto':>9}   mejor corte")
    for c in comparacion:
        if "mejor_corte" in c:
            m = c["mejor_corte"]
            corte = (
                (
                    f">= {m['corte']:g}: ganan {m['pct_pasan']}% vs {m['pct_no']}% "
                    f"(VENTAJA {m['ventaja']:+d}, {m['pasan']} pasan)"
                )
                if m
                else "-"
            )
            g, r = c["mediana_ganadoras"], c["mediana_resto"]
            print(
                f"{c['senal']:<24}{'' if g is None else f'{g:g}':>10}"
                f"{'' if r is None else f'{r:g}':>9}   {corte}"
            )
        else:
            print(
                f"{c['senal']:<24} si: {c['si']} (ganan {c['pct_ganan_si']}%)  "
                f"no: {c['no']} (ganan {c['pct_ganan_no']}%)"
            )
    print("\nCOMO LEERLO: VENTAJA es cuantos puntos mas de ganadoras hay entre las")
    print("que pasan el corte. Por debajo de ~10 puntos, o con menos de ~15")
    print("ganadoras, es ruido.")


#: Momentos (segundos desde que nace) en los que se mira cada moneda pasada
#: con --mints: asi se ve como iba construyendose antes de dispararse.
MOMENTOS_DETALLE = (5, 10, 20, 30, 60)


def detalle_por_moneda(ops_por: dict[str, list[dict]], comunes: set[str]) -> None:
    """Moneda a moneda: que tenia a los 5/10/20/30/60 s y hasta donde llego."""
    for m, ops in ops_por.items():
        print(f"\n== {m}")
        if not ops:
            print("   sin operaciones en la curva (o no se pudieron leer)")
            continue
        nacer = mc_sol(ops[0])
        pico = max(mc_sol(e) for e in ops)
        i_pico = max(range(len(ops)), key=lambda i: mc_sol(ops[i]))
        seg_pico = ops[i_pico]["timestamp"] - ops[0]["timestamp"]
        print(
            f"   mayhem: {ops[0].get('mayhem_mode')} · pico x{pico / nacer:.1f} desde "
            f"que nacio, a los {seg_pico / 60:.1f} min · migro: "
            f"{any(e.get('real_token_reserves') == 0 for e in ops)}"
        )
        for seg in MOMENTOS_DETALLE:
            f = senales(ops, seg, comunes)
            if not f:
                continue
            print(
                f"   a los {seg:>2} s: compradores {f['compradores']:>3} · vendedores "
                f"{f['vendedores']:>3} · {f['sol_comprado']:>6} SOL comprados · ratio "
                f"{f['ratio_compra_venta']:>5} · ya x{f['subida_hasta_decision']} · "
                f"desde aqui x{f['mult_desde_decision']:.1f} · bloque1 "
                f"{f['bloque1_compradores']} carteras {f['bloque1_sol']} SOL · dev "
                f"{f['dev_compra_sol']} SOL · cartera gorda {f['cartera_mas_gorda_pct']}%"
            )


async def _corre(a: argparse.Namespace, url: str) -> None:  # noqa: C901
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
        lec = Lector(ad.Nodo(url, s))
        if a.mints:
            # 9-nov: "que paso para que se disparasen ... solo en estas": se
            # miran SOLO las monedas que se pasan, sean o no mayhem.
            mints = [m for m in a.mints.replace(",", " ").split() if m]
        else:
            print("== BUSCANDO MONEDAS MAYHEM", file=sys.stderr)
            mints = sorted(await descubre(lec, a.horas, a.muestra))[: a.max_monedas]
        ops_por: dict[str, list[dict]] = {}
        for i, m in enumerate(mints, 1):
            print(f"  [{i}/{len(mints)}] {m}", file=sys.stderr)
            try:
                ops_por[m] = await ops_de(lec, m)
            except Exception as e:  # noqa: BLE001
                print(f"     fallo: {type(e).__name__}", file=sys.stderr)
        llamadas = lec.nodo.llamadas
    comunes = carteras_comunes(ops_por)
    filas = []
    corte_edad = time.time() - a.min_edad_min * 60
    for m, ops in ops_por.items():
        if not ops or ops[0]["timestamp"] > corte_edad:
            continue  # recien nacida: aun no se sabe como acaba
        if ops[0].get("mayhem_mode") is False and not a.mints:
            continue
        f = senales(ops, a.decision, comunes)
        if f:
            filas.append({"mint": m, **f})
    if a.mints:
        detalle_por_moneda(ops_por, comunes)
    comparacion = compara(filas, a.x) if filas else []
    imprime(filas, comparacion, a, comunes)
    gan = sorted(
        (f for f in filas if f["mult_desde_decision"] >= a.x),
        key=lambda f: -f["mult_desde_decision"],
    )
    if gan:
        print("\n== LAS GANADORAS")
        for f in gan[:25]:
            print(
                f"{f['mint']}  x{f['mult_desde_decision']:.1f}  "
                f"compradores {f['compradores']}  {f['sol_comprado']} SOL  "
                f"dev {f['dev_compra_sol']} SOL"
            )
    SALIDA.write_text(
        json.dumps(
            {
                "que_es": __doc__.split("\n\n")[0],
                "parametros": vars(a),
                "monedas": filas,
                "comparacion": comparacion,
                "carteras_comunes": sorted(comunes),
            },
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\nHelius: {llamadas} llamadas, {lec.tx_leidas} transacciones leidas.")
    print(f"Todo en {SALIDA}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--horas", type=float, default=6, help="ventana hacia atras")
    p.add_argument("--max-monedas", type=int, default=80)
    p.add_argument(
        "--muestra", type=int, default=600, help="transacciones para descubrir monedas"
    )
    p.add_argument(
        "--decision",
        type=float,
        default=10,
        help="segundos desde que nace: cuando se compraria",
    )
    p.add_argument("--x", type=float, default=4.0, help="4 = +300%%")
    p.add_argument(
        "--mints",
        default="",
        help="solo estas monedas (separadas por espacio o coma), mayhem o no",
    )
    p.add_argument(
        "--min-edad-min",
        type=float,
        default=60,
        help="ignora monedas mas jovenes que esto (aun no han acabado)",
    )
    a = p.parse_args()
    # El .env del bot: junto al repo, o en /root/ai-bot si esto se copio fuera.
    for env in (
        Path(__file__).resolve().parents[1] / ".env",
        Path("/root/ai-bot/.env"),
    ):
        if env.is_file():
            load_dotenv(env)
            break
    url = os.environ.get("SOLANA_NODE_RPC_ENDPOINT", "").strip()
    if not url:
        print("Falta SOLANA_NODE_RPC_ENDPOINT en el .env")
        raise SystemExit(2)
    try:
        asyncio.run(_corre(a, url))
    except Exception as e:  # noqa: BLE001
        # Sin el mensaje: el de aiohttp lleva la URL del nodo, con la clave.
        print(f"Fallo leyendo la cadena: {type(e).__name__}. No se escribio nada.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
