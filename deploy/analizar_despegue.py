"""Que paso en el instante en que una moneda cruzo un market cap dado.

PEDIDO POR MANU (15-oct-2026): "Analiza la fuerza explosiva de estos tres
contratos [...] en torno a los 20.000 de market cap y analiza que paso para
que se despegasen y igualasen hasta casi un por 10 desde los 20.000. Y que
caracteristicas tienen en comun para poder comprar en ese momento en torno a
los 18.000 o 20.000? [...] presion compradora debe poner."

    uv run deploy/analizar_despegue.py <mint> [<mint> ...]
    uv run deploy/analizar_despegue.py --mc 18000 --sol 150 <mint>
    uv run deploy/analizar_despegue.py --json trades/despegue.json <mint>

QUE HACE. Baja TODAS las operaciones de la bonding curve de cada moneda y
reconstruye su market cap operacion a operacion desde `virtual_sol_reserves` /
`virtual_token_reserves` del `TradeEvent`. Eso da el MC EXACTO en cada trade,
no una estimacion. Localiza el instante del primer cruce del MC objetivo y
mide, en ventanas de 3/10/30 segundos ANTERIORES a ese instante, lo que se
puede usar como senal de compra: presion compradora (SOL que entra / SOL que
sale), compradores distintos, volumen, tamano de la compra mayor y
concentracion de la cartera mas gorda.

Ademas mide LA PEOR CAIDA por debajo del punto de cruce antes del pico. Ese
es el numero que dice si un stop loss te habria sacado antes del despegue.

TRES COSAS QUE ESTE SCRIPT NO PUEDE CONTESTAR, Y HAY QUE SABERLO:

1. Solo ve la BONDING CURVE. Si la moneda migro, lo de despues (PumpSwap /
   Raydium) no esta aqui. Y con SOL a $150 la curva se acaba en ~$61.600,
   asi que un x10 desde $20.000 ($200.000) ocurre en su mayor parte FUERA de
   lo que mide esto. El informe lo dice en cada moneda.
2. El MC en dolares depende del precio de SOL (`--sol`, por defecto 150). El
   MC en SOL es exacto; el de dolares vale lo que valga ese supuesto. Si
   estas monedas corrieron con SOL a otro precio, hay que pasarlo.
3. El historico depende de lo que guarde el nodo. Un plan sin historico
   completo devuelve solo lo reciente; entonces el informe avisa de que no
   vio el nacimiento y las ventanas de presion pueden estar incompletas.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from solders.pubkey import Pubkey

#: sha256("event:TradeEvent")[:8] -- convencion de eventos de Anchor.
DISCRIMINADOR_TRADE = hashlib.sha256(b"event:TradeEvent").digest()[:8]
PROGRAMA_PUMP = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")

DECIMALES_TOKEN = 6
DECIMALES_SOL = 9
SUPPLY_TOTAL = 1_000_000_000

#: MC en SOL cuando la curva se completa y la moneda migra.
MC_SOL_AL_MIGRAR = 410.9

#: Por debajo de esto se da por visto el nacimiento (el MC al nacer son
#: ~28 SOL; con SOL entre $100 y $200 eso es $2.800-$5.600).
MC_USD_DE_NACIMIENTO = 6_000

#: Tope de firmas por pagina que admite getSignaturesForAddress.
PAGINA_FIRMAS = 1_000
#: Transacciones por lote JSON-RPC. Mas alto y los nodos empiezan a cortar.
LOTE_TX = 100
VENTANAS_SEG = (3, 10, 30)
HTTP_DEMASIADAS = 429
HTTP_ERROR_SERVIDOR = 500

#: Con una sola moneda no hay nada que comparar entre ellas.
MINIMO_PARA_COMPARAR = 2


# ------------------------------------------------------------------ RPC
class Nodo:
    """Cliente JSON-RPC con reintentos y espera creciente."""

    def __init__(self, url: str, sesion: aiohttp.ClientSession) -> None:
        self.url = url
        self.sesion = sesion
        self.llamadas = 0

    async def pide(self, cuerpo: list | dict, intentos: int = 5) -> list | dict | None:
        espera = 1.0
        for intento in range(intentos):
            try:
                async with self.sesion.post(self.url, json=cuerpo) as r:
                    if (
                        r.status in (HTTP_DEMASIADAS,)
                        or r.status >= HTTP_ERROR_SERVIDOR
                    ):
                        raise aiohttp.ClientResponseError(
                            r.request_info, r.history, status=r.status
                        )
                    self.llamadas += 1
                    return await r.json()
            except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError):
                if intento == intentos - 1:
                    raise
                await asyncio.sleep(espera)
                espera = min(espera * 2, 16.0)
        return None

    async def firmas(self, direccion: str) -> list[dict]:
        """Todas las firmas de una cuenta, de la mas nueva a la mas vieja."""
        fuera: list[dict] = []
        antes: str | None = None
        while True:
            params: dict = {"limit": PAGINA_FIRMAS}
            if antes:
                params["before"] = antes
            r = await self.pide(
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
            print(f"      ... {len(fuera)} firmas", file=sys.stderr)
            if len(lote) < PAGINA_FIRMAS:
                break
        return fuera

    async def transacciones(self, firmas: list[str]) -> list[dict]:
        """Baja transacciones en lotes JSON-RPC."""
        fuera: list[dict] = []
        for i in range(0, len(firmas), LOTE_TX):
            trozo = firmas[i : i + LOTE_TX]
            cuerpo = [
                {
                    "jsonrpc": "2.0",
                    "id": j,
                    "method": "getTransaction",
                    "params": [
                        f,
                        {
                            "encoding": "json",
                            "maxSupportedTransactionVersion": 0,
                            "commitment": "confirmed",
                        },
                    ],
                }
                for j, f in enumerate(trozo)
            ]
            r = await self.pide(cuerpo)
            if isinstance(r, list):
                fuera.extend(
                    x["result"] for x in r if isinstance(x, dict) and x.get("result")
                )
            print(
                f"      ... {min(i + LOTE_TX, len(firmas))}/{len(firmas)} tx",
                file=sys.stderr,
            )
        return fuera


def pda_curva(mint: str) -> str:
    """PDA de la bonding curve. Semilla ["bonding-curve", mint]."""
    direccion, _bump = Pubkey.find_program_address(
        [b"bonding-curve", bytes(Pubkey.from_string(mint))], PROGRAMA_PUMP
    )
    return str(direccion)


# ------------------------------------------------------- parseo del TradeEvent
class _Lector:
    """Lector secuencial que devuelve None cuando se acaban los bytes.

    Hace falta porque el `TradeEvent` ha ido CRECIENDO con los upgrades del
    programa: una moneda antigua emite un evento mas corto. Si se exigieran
    todos los campos, se descartarian las operaciones viejas enteras -- justo
    las que hacen falta para medir presion compradora.
    """

    def __init__(self, datos: bytes) -> None:
        self.b = datos
        self.o = 0

    def u64(self) -> int | None:
        if self.o + 8 > len(self.b):
            return None
        v: int = struct.unpack_from("<Q", self.b, self.o)[0]
        self.o += 8
        return v

    def i64(self) -> int | None:
        if self.o + 8 > len(self.b):
            return None
        v: int = struct.unpack_from("<q", self.b, self.o)[0]
        self.o += 8
        return v

    def pubkey(self) -> str | None:
        if self.o + 32 > len(self.b):
            return None
        v = str(Pubkey(self.b[self.o : self.o + 32]))
        self.o += 32
        return v

    def booleano(self) -> bool | None:
        if self.o + 1 > len(self.b):
            return None
        v = self.b[self.o] != 0
        self.o += 1
        return v

    def cadena(self, tope: int = 64) -> str | None:
        if self.o + 4 > len(self.b):
            return None
        largo: int = struct.unpack_from("<I", self.b, self.o)[0]
        if largo > tope or self.o + 4 + largo > len(self.b):
            return None
        self.o += 4
        v = self.b[self.o : self.o + largo].decode("utf-8", "replace")
        self.o += largo
        return v


#: Campos del TradeEvent en orden, tal y como los lista `idl/pump_fun_idl.json`.
_CAMPOS: tuple[tuple[str, str], ...] = (
    ("mint", "pubkey"),
    ("sol_amount", "u64"),
    ("token_amount", "u64"),
    ("is_buy", "bool"),
    ("user", "pubkey"),
    ("timestamp", "i64"),
    ("virtual_sol_reserves", "u64"),
    ("virtual_token_reserves", "u64"),
    ("real_sol_reserves", "u64"),
    ("real_token_reserves", "u64"),
    ("fee_recipient", "pubkey"),
    ("fee_basis_points", "u64"),
    ("fee", "u64"),
    ("creator", "pubkey"),
    ("creator_fee_basis_points", "u64"),
    ("creator_fee", "u64"),
    ("track_volume", "bool"),
    ("total_unclaimed_tokens", "u64"),
    ("total_claimed_tokens", "u64"),
    ("current_sol_volume", "u64"),
    ("last_update_timestamp", "i64"),
    ("ix_name", "string"),
    ("mayhem_mode", "bool"),
    ("cashback_fee_basis_points", "u64"),
    ("cashback", "u64"),
)


def parsea_trade(crudo: bytes) -> dict | None:
    """Decodifica un TradeEvent; tolera eventos viejos mas cortos."""
    if not crudo.startswith(DISCRIMINADOR_TRADE):
        return None
    lector = _Lector(crudo[8:])
    lectores = {
        "pubkey": lector.pubkey,
        "u64": lector.u64,
        "i64": lector.i64,
        "bool": lector.booleano,
        "string": lector.cadena,
    }
    ev: dict = {nombre: lectores[tipo]() for nombre, tipo in _CAMPOS}
    if not ev["virtual_token_reserves"] or not ev["mint"]:
        return None
    return ev


def eventos_de_tx(tx: dict) -> list[dict]:
    """Saca los TradeEvent de los logs de una transaccion."""
    fuera = []
    meta = tx.get("meta") or {}
    for linea in meta.get("logMessages") or []:
        if not linea.startswith("Program data: "):
            continue
        try:
            crudo = base64.b64decode(linea[len("Program data: ") :])
        except (ValueError, TypeError):
            continue
        ev = parsea_trade(crudo)
        if ev:
            ev["_slot"] = tx.get("slot")
            fuera.append(ev)
    return fuera


# ------------------------------------------------------------------ analisis
def mc_usd(ev: dict, precio_sol: float) -> float:
    """Market cap en dolares a partir de las reservas virtuales."""
    vsol = ev["virtual_sol_reserves"] / 10**DECIMALES_SOL
    vtok = ev["virtual_token_reserves"] / 10**DECIMALES_TOKEN
    return (vsol / vtok) * SUPPLY_TOTAL * precio_sol


def ventana(ops: list[dict], hasta: int, segundos: float) -> dict:
    """Presion compradora en los `segundos` previos a la operacion `hasta`."""
    t_fin = ops[hasta]["timestamp"]
    comprado = vendido = mayor = 0.0
    compradores: set[str] = set()
    n_compras = n_ventas = 0
    for op in ops[: hasta + 1]:
        if t_fin - op["timestamp"] > segundos:
            continue
        sol = (op["sol_amount"] or 0) / 10**DECIMALES_SOL
        if op["is_buy"]:
            comprado += sol
            n_compras += 1
            mayor = max(mayor, sol)
            if op["user"]:
                compradores.add(op["user"])
        else:
            vendido += sol
            n_ventas += 1
    if vendido > 0:
        ratio = comprado / vendido
    else:
        ratio = float("inf") if comprado > 0 else 0.0
    return {
        "segundos": segundos,
        "comprado_sol": comprado,
        "vendido_sol": vendido,
        "ratio": ratio,
        "compradores": len(compradores),
        "n_compras": n_compras,
        "n_ventas": n_ventas,
        "mayor_compra_sol": mayor,
    }


def _concentracion(ops: list[dict], hasta: int) -> dict:
    """Cuanto del SOL comprado hasta `hasta` viene de una sola cartera."""
    por_cartera: dict[str, float] = {}
    total = 0.0
    for op in ops[: hasta + 1]:
        if op["is_buy"] and op["user"]:
            sol = (op["sol_amount"] or 0) / 10**DECIMALES_SOL
            por_cartera[op["user"]] = por_cartera.get(op["user"], 0.0) + sol
            total += sol
    if total <= 0:
        return {}
    return {
        "carteras_distintas": len(por_cartera),
        "concentracion_mayor_pct": max(por_cartera.values()) / total * 100,
        "sol_comprado_total": total,
    }


def _caidas(mcs: list[float], desde: int, hasta: int) -> dict:
    """Peor caida bajo el punto de cruce y peor caida desde un maximo."""
    base = mcs[desde]
    tramo = mcs[desde : hasta + 1]
    maximo = base
    peor_desde_maximo = 0.0
    for m in tramo:
        maximo = max(maximo, m)
        peor_desde_maximo = min(peor_desde_maximo, (m - maximo) / maximo)
    return {
        "peor_caida_bajo_el_cruce_pct": (min(tramo) / base - 1) * 100,
        "peor_caida_desde_maximo_pct": peor_desde_maximo * 100,
    }


def analiza(mint: str, ops: list[dict], mc_objetivo: float, precio_sol: float) -> dict:
    """Informe de una moneda."""
    ops = sorted(ops, key=lambda e: (e["timestamp"], e.get("_slot") or 0))
    if not ops:
        return {"mint": mint, "error": "sin operaciones"}

    mcs = [mc_usd(o, precio_sol) for o in ops]
    pico = max(mcs)
    idx_pico = mcs.index(pico)
    t0 = ops[0]["timestamp"]
    banderas = [o["mayhem_mode"] for o in ops if o.get("mayhem_mode") is not None]
    mc_migrar = MC_SOL_AL_MIGRAR * precio_sol

    r: dict = {
        "mint": mint,
        "n_ops": len(ops),
        "mc_primera_op": mcs[0],
        "vio_el_nacimiento": mcs[0] < MC_USD_DE_NACIMIENTO,
        "mc_pico_en_curva": pico,
        "segundos_hasta_pico": ops[idx_pico]["timestamp"] - t0,
        "mayhem": (any(banderas) if banderas else None),
        "creador": next((o["creator"] for o in ops if o.get("creator")), None),
        "mc_al_migrar": mc_migrar,
        "migro": pico >= mc_migrar * 0.985,
    }

    idx = next((i for i, m in enumerate(mcs) if m >= mc_objetivo), None)
    if idx is None:
        r["cruzo_objetivo"] = False
        return r

    r["cruzo_objetivo"] = True
    r["cruce"] = {
        "mc": mcs[idx],
        "segundos_desde_primera_op": ops[idx]["timestamp"] - t0,
        "ops_antes": idx,
        "ventanas": [ventana(ops, idx, s) for s in VENTANAS_SEG],
        **_concentracion(ops, idx),
    }
    base = mcs[idx]
    for mult in (2, 3, 5):
        j = next((k for k in range(idx, len(mcs)) if mcs[k] >= base * mult), None)
        r[f"segundos_hasta_x{mult}"] = (
            ops[j]["timestamp"] - ops[idx]["timestamp"] if j is not None else None
        )
    if idx_pico > idx:
        r.update(_caidas(mcs, idx, idx_pico))
    return r


# -------------------------------------------------------------------- informe
def _fmt_ratio(v: float) -> str:
    return "inf (nadie vendio)" if v == float("inf") else f"{v:.2f}"


def imprime(r: dict, mc_objetivo: float) -> None:
    print("=" * 74)
    print(r["mint"])
    if r.get("error"):
        print("   ERROR:", r["error"])
        return
    print(f"   operaciones en la curva  : {r['n_ops']}")
    aviso = "" if r["vio_el_nacimiento"] else "   <-- NO se vio el nacimiento"
    print(f"   MC en la primera op vista: ${r['mc_primera_op']:,.0f}{aviso}")
    print(f"   MC pico EN LA CURVA      : ${r['mc_pico_en_curva']:,.0f}")
    print(f"   mayhem                   : {r['mayhem']}")
    print(
        f"   migro                    : {r['migro']}"
        f"   (la curva acaba en ${r['mc_al_migrar']:,.0f})"
    )
    if r["migro"]:
        print("   -> lo que subio DESPUES de migrar no lo ve este script.")
    if not r.get("cruzo_objetivo"):
        print(f"   NO cruzo ${mc_objetivo:,.0f} dentro de la bonding curve.")
        return

    c = r["cruce"]
    print()
    print(f"   --- EN EL INSTANTE DE CRUZAR ${mc_objetivo:,.0f} ---")
    print(f"   MC exacto                : ${c['mc']:,.0f}")
    print(f"   edad                     : {c['segundos_desde_primera_op']:,.0f} s")
    print(f"   operaciones previas      : {c['ops_antes']}")
    if "carteras_distintas" in c:
        print(f"   carteras compradoras     : {c['carteras_distintas']}")
        print(
            f"   la mayor concentra       : {c['concentracion_mayor_pct']:.1f}%"
            " del SOL comprado"
        )
        print(f"   SOL comprado acumulado   : {c['sol_comprado_total']:.2f}")
    print()
    print("   PRESION COMPRADORA (SOL que entra / SOL que sale):")
    for v in c["ventanas"]:
        print(
            f"     {v['segundos']:>2.0f}s antes: ratio {_fmt_ratio(v['ratio']):>18s}"
            f" | compro {v['comprado_sol']:>7.2f} SOL"
            f" ({v['n_compras']} ops, {v['compradores']} carteras)"
            f" | vendio {v['vendido_sol']:>7.2f} SOL ({v['n_ventas']} ops)"
            f" | mayor {v['mayor_compra_sol']:.2f} SOL"
        )
    print()
    print("   DESPUES DEL CRUCE, DENTRO DE LA CURVA:")
    for mult in (2, 3, 5):
        s = r.get(f"segundos_hasta_x{mult}")
        print(f"     x{mult}: " + (f"{s:,.0f} s" if s is not None else "no llego"))
    if "peor_caida_bajo_el_cruce_pct" in r:
        print(
            f"     peor caida POR DEBAJO del cruce: "
            f"{r['peor_caida_bajo_el_cruce_pct']:.1f}%"
        )
        print(
            f"     peor caida desde un maximo     : "
            f"{r['peor_caida_desde_maximo_pct']:.1f}%"
        )


def _rango(nombre: str, valores: list, fmt: str = "{:.2f}") -> None:
    finitos = [v for v in valores if v is not None and v != float("inf")]
    infinitos = sum(1 for v in valores if v == float("inf"))
    if not finitos:
        cola = f" ({infinitos} con ratio infinito)" if infinitos else ""
        print(f"   {nombre:34s} sin dato{cola}")
        return
    cola = f"   (+{infinitos} con ratio infinito)" if infinitos else ""
    print(
        f"   {nombre:34s} min {fmt.format(min(finitos)):>12s}"
        f"  ·  max {fmt.format(max(finitos)):>12s}{cola}"
    )


def comun(rs: list[dict], mc_objetivo: float) -> None:
    """Que comparten las monedas que cruzaron el objetivo."""
    buenos = [r for r in rs if r.get("cruzo_objetivo")]
    if len(buenos) < MINIMO_PARA_COMPARAR:
        print("\nMenos de dos monedas cruzaron el objetivo: no hay nada que comparar.")
        return
    print("\n" + "=" * 74)
    print(f"QUE TIENEN EN COMUN AL CRUZAR ${mc_objetivo:,.0f}  ({len(buenos)} monedas)")
    print("=" * 74)
    for i, seg in enumerate(VENTANAS_SEG):
        _rango(
            f"presion compradora a {seg}s",
            [r["cruce"]["ventanas"][i]["ratio"] for r in buenos],
        )
    for i, seg in enumerate(VENTANAS_SEG):
        _rango(
            f"compradores distintos a {seg}s",
            [float(r["cruce"]["ventanas"][i]["compradores"]) for r in buenos],
            "{:.0f}",
        )
    for i, seg in enumerate(VENTANAS_SEG):
        _rango(
            f"SOL comprado a {seg}s",
            [r["cruce"]["ventanas"][i]["comprado_sol"] for r in buenos],
        )
    _rango(
        "edad al cruzar (s)",
        [float(r["cruce"]["segundos_desde_primera_op"]) for r in buenos],
        "{:,.0f}",
    )
    _rango(
        "carteras compradoras",
        [r["cruce"].get("carteras_distintas") for r in buenos],
        "{:.0f}",
    )
    _rango(
        "concentracion de la mayor (%)",
        [r["cruce"].get("concentracion_mayor_pct") for r in buenos],
    )
    _rango(
        "peor caida bajo el cruce (%)",
        [r.get("peor_caida_bajo_el_cruce_pct") for r in buenos],
    )
    print()
    print(f"   mayhem : {[r['mayhem'] for r in buenos]}")
    print(f"   migro  : {[r['migro'] for r in buenos]}")
    creadores = {r["creador"] for r in buenos if r.get("creador")}
    print(f"   creadores distintos: {len(creadores)} de {len(buenos)}")
    print()
    print("   COMO SE LEE ESTO PARA DECIDIR UN FILTRO:")
    print("   - La presion compradora minima de la columna 'min' es el techo")
    print("     de lo que se puede exigir: por encima de eso, alguna de estas")
    print("     tres NO se habria comprado.")
    print("   - 'peor caida bajo el cruce' dice si un stop habria saltado antes")
    print("     del despegue. Si es mas negativa que tu stop, comprar ahi con")
    print("     ese stop te saca ANTES del x10.")
    print("   - Tres monedas no son una muestra. Esto describe tres casos; no")
    print("     mide con que frecuencia se repiten ni cuantas fallaron igual.")


async def _una(nodo: Nodo, mint: str, mc: float, sol: float) -> dict:
    print(f"--- {mint}", file=sys.stderr)
    curva = pda_curva(mint)
    print(f"    curva: {curva}", file=sys.stderr)
    firmas = await nodo.firmas(curva)
    if not firmas:
        print("    sin firmas (nodo sin historico, o mint equivocado)", file=sys.stderr)
        return {"mint": mint, "error": "sin firmas"}
    buenas = [f["signature"] for f in firmas if not f.get("err")]
    buenas.reverse()
    txs = await nodo.transacciones(buenas)
    ops = [e for t in txs for e in eventos_de_tx(t) if e["mint"] == mint]
    print(f"    {len(ops)} operaciones parseadas", file=sys.stderr)
    return analiza(mint, ops, mc, sol)


async def _corre(a: argparse.Namespace, url: str) -> None:
    tiempo = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=tiempo) as sesion:
        nodo = Nodo(url, sesion)
        resultados = [await _una(nodo, m, a.mc, a.sol) for m in a.mints]
    for r in resultados:
        imprime(r, a.mc)
    comun(resultados, a.mc)
    print(f"\n({nodo.llamadas} llamadas al nodo)")
    if a.json:
        destino = Path(a.json)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(resultados, indent=2, default=str), encoding="utf-8"
        )
        print(f"Crudo guardado en {destino}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mints", nargs="+", help="direcciones de los tokens")
    p.add_argument("--mc", type=float, default=20_000, help="MC objetivo en USD")
    p.add_argument("--sol", type=float, default=150.0, help="precio de SOL en USD")
    p.add_argument("--json", default="", help="guarda el crudo en este fichero")
    a = p.parse_args()

    load_dotenv()
    url = os.environ.get("SOLANA_NODE_RPC_ENDPOINT", "")
    if not url:
        print("Falta SOLANA_NODE_RPC_ENDPOINT (ni en el entorno ni en .env).")
        raise SystemExit(2)

    print(f"Objetivo: ${a.mc:,.0f} de market cap   ·   SOL a ${a.sol:,.2f}")
    print(f"Con ese precio de SOL la curva migra a ${MC_SOL_AL_MIGRAR * a.sol:,.0f}.")
    print(
        f"Un x10 desde ${a.mc:,.0f} son ${a.mc * 10:,.0f}: lo que pase por encima de la"
    )
    print("migracion NO lo ve este script (eso ya es PumpSwap/Raydium).")
    print()
    asyncio.run(_corre(a, url))


if __name__ == "__main__":
    main()
