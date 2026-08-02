"""Prueba en papel: comprar si en el PRIMER SEGUNDO ya hay volumen.

Por que hay que medirlo en vivo
-------------------------------
Con datos historicos esto no se puede responder. GMGN da velas de 30 segundos
y la API de pump.fun solo el estado actual: para cuando cualquiera de las dos
te deja mirar, el precio ya se movio. Y ese es justo el punto -- midiendo a los
30s salio que las monedas con mucho volumen ya habian subido ANTES de que
pudieras entrar (TNOS ya valia 26 millones a los 30 segundos).

A un segundo el precio todavia no ha corrido. Si hay senal, esta ahi.

Que hace
--------
1. Escucha los lanzamientos por el WSS de Helius (mismo listener que el bot).
2. Al segundo 1 lee la curva: los SOL depositados son el volumen real.
3. Si supera el umbral, apunta la entrada (NO compra) y sigue el precio.
4. Al final evalua VARIAS reglas de salida sobre las mismas entradas, que es
   la ventaja de hacerlo en papel: no hay que elegir el TP antes de saber.

Comisiones reales incluidas: 2,25% por lado + 0,0018 SOL por transaccion
(medido en la cadena el 1-ago).

Uso
---
    python paper_1s.py                       # 30 min, umbral $800
    python paper_1s.py --umbral 800 --minutos 60
    python paper_1s.py --incluir-mayhem
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import aiohttp

from core.client import SolanaClient
from interfaces.core import Platform, TokenInfo
from monitoring.universal_logs_listener import UniversalLogsListener
from platforms import platform_factory

LAMPORTS = 1_000_000_000
BUY, VAR, POR_TX = 0.15, 0.0225, 0.0018


def _load_dotenv() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def log(m: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {m}", flush=True)


async def avisar(texto: str) -> None:
    """Manda el texto a Telegram. Un fallo aqui nunca corta la prueba."""
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        return
    try:
        async with aiohttp.ClientSession() as s, s.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data={"chat_id": chat, "text": texto[:4000], "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                log(f"telegram {r.status}: {(await r.text())[:120]}")
    except Exception as exc:  # noqa: BLE001
        log(f"telegram fallo: {exc}")


async def precio_sol(session: aiohttp.ClientSession) -> float:
    """SOL en dolares, sacado de la propia API de pump.fun (mc usd / mc sol)."""
    try:
        u = ("https://frontend-api-v3.pump.fun/coins?offset=0&limit=50"
             "&sort=created_timestamp&order=DESC&includeNsfw=true")
        async with session.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=20) as r:
            d = await r.json()
        v = [c["usd_market_cap"] / c["market_cap"] for c in d if c.get("market_cap")]
        return statistics.median(v) if v else 73.0
    except Exception:  # noqa: BLE001
        return 73.0


class Prueba:
    def __init__(self, args: argparse.Namespace, sol_usd: float) -> None:
        self.args = args
        self.sol_usd = sol_usd
        self.client = SolanaClient(os.environ["SOLANA_NODE_RPC_ENDPOINT"])
        self.impl = platform_factory.create_for_platform(Platform.PUMP_FUN, self.client)
        self.abiertas: dict[str, dict] = {}
        self.cerradas: list[dict] = []
        self.vistos: set[str] = set()
        self.n_lanz = 0
        self.ilegibles = 0
        self.volumenes: list[float] = []
        self.tareas: set[asyncio.Task] = set()

    async def al_nacer(self, t: TokenInfo) -> None:
        mint = str(t.mint)
        if mint in self.vistos:
            return
        self.vistos.add(mint)
        self.n_lanz += 1
        if t.is_mayhem_mode and not self.args.incluir_mayhem:
            return
        tarea = asyncio.create_task(self.mirar_al_segundo(t))
        self.tareas.add(tarea)
        tarea.add_done_callback(self.tareas.discard)

    async def mirar_al_segundo(self, t: TokenInfo) -> None:
        """El corazon de la prueba: leer la curva 1 segundo despues de nacer."""
        await asyncio.sleep(self.args.segundo)
        # Al segundo 1 la cuenta de la curva a veces todavia no ha propagado al
        # RPC ("Account not found"). Se reintenta un par de veces con 300ms:
        # sin esto se pierden lanzamientos y el conteo sale sesgado.
        st = None
        for _ in range(3):
            try:
                st = await self.impl.curve_manager.get_pool_state(t.bonding_curve)
                break
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.3)
        if st is None:
            self.ilegibles += 1
            return
        vol_sol = float(st.get("real_sol_reserves", 0)) / LAMPORTS
        vol_usd = vol_sol * self.sol_usd
        # se apunta SIEMPRE, pase o no el umbral: sin el reparto completo no se
        # sabe si el umbral es alcanzable o si simplemente no existe
        self.volumenes.append(vol_usd)
        if vol_usd < self.args.umbral:
            return
        precio = float(st.get("price_per_token") or 0)
        if precio <= 0:
            return
        mc = precio * 1_000_000_000 * self.sol_usd
        mint = str(t.mint)
        self.abiertas[mint] = {
            "sym": t.symbol, "mint": mint, "creator": str(t.creator),
            "mayhem": bool(t.is_mayhem_mode), "vol_usd": vol_usd,
            "entrada": precio, "mc_entrada": mc, "t0": time.time(),
            "max": precio, "min": precio, "camino": [(0.0, precio)],
            "curva": t.bonding_curve,
        }
        log(f"  + {t.symbol[:12]:12} vol1s ${vol_usd:>7,.0f} · MC ${mc:>8,.0f}"
            + ("  [mayhem]" if t.is_mayhem_mode else ""))
        await avisar(
            f"🎯 <b>ENTRADA (papel)</b> · <b>{t.symbol[:16]}</b>\n"
            f"💵 volumen al segundo {self.args.segundo:.0f}: <b>${vol_usd:,.0f}</b>\n"
            f"📈 MC de entrada: ${mc:,.0f}\n"
            f'📊 <a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
            f'<a href="https://pump.fun/coin/{mint}">pump.fun</a>\n'
            f"CA: <code>{mint}</code>"
        )

    async def seguir(self) -> None:
        """Sigue el precio de las abiertas hasta agotar el tiempo de vigilancia."""
        while True:
            await asyncio.sleep(self.args.sondeo)
            ahora = time.time()
            for mint, p in list(self.abiertas.items()):
                try:
                    st = await self.impl.curve_manager.get_pool_state(p["curva"])
                    precio = float(st.get("price_per_token") or 0)
                except Exception:  # noqa: BLE001
                    # una lectura fallida no cierra la posicion: se reintenta al
                    # siguiente sondeo. Solo el tiempo la cierra.
                    precio = 0.0
                if precio <= 0:
                    if ahora - p["t0"] >= self.args.vigilar:
                        p["salida"] = p["camino"][-1][1]
                        self.cerradas.append(p)
                        del self.abiertas[mint]
                    continue
                p["max"] = max(p["max"], precio)
                p["min"] = min(p["min"], precio)
                p["camino"].append((round(ahora - p["t0"], 1), precio))
                if ahora - p["t0"] >= self.args.vigilar:
                    p["salida"] = precio
                    self.cerradas.append(p)
                    del self.abiertas[mint]


#: Escalera que pidio Manu (2-ago): stop -35%; a +40% vende el 60%; a +120%
#: vende la mitad de lo que queda (20% del total); el 20% final sale cuando el
#: market cap toca $14.000.
ESCALERA = [(0.40, 0.60), (1.20, 0.20)]
SL_ESCALERA = 0.35
MC_FINAL = 14_000.0


def simular_escalera(p: dict, tramos: list[tuple[float, float]] = ESCALERA,
                     sl: float = SL_ESCALERA, mc_final: float = MC_FINAL) -> tuple[float, str]:
    """Recorre el camino REAL del precio y aplica la escalera en orden.

    Usar el camino y no el maximo/minimo importa aqui: con una escalera, lo que
    cobras depende de si el precio toco +40% ANTES de desplomarse o al reves.
    En cada muestra se mira primero el stop (pesimista) y luego los objetivos.

    Devuelve (multiplo cobrado sobre la entrada, motivo).
    """
    e = p["entrada"]
    mc_e = p.get("mc_entrada") or 0
    camino = p.get("camino") or [(0.0, e)]
    restante = 1.0
    cobrado = 0.0
    pendientes = list(tramos)
    final_hecho = False
    motivo = "TIEMPO"

    for _t, precio in camino:
        if restante <= 1e-9:
            break
        r = precio / e
        if r <= 1 - sl:                       # stop: se va todo lo que quede
            cobrado += restante * (1 - sl)
            restante = 0.0
            motivo = "SL" if cobrado <= (1 - sl) + 1e-9 else "SL parcial"
            break
        while pendientes and r >= 1 + pendientes[0][0]:
            _g, frac = pendientes.pop(0)
            frac = min(frac, restante)
            cobrado += frac * r               # se cobra al precio observado
            restante -= frac
            motivo = "TP"
        mc = (mc_e * r) if mc_e else 0
        if not final_hecho and mc >= mc_final and restante > 0 and not pendientes:
            cobrado += restante * r
            restante = 0.0
            final_hecho = True
            motivo = "MC $14K"
    if restante > 1e-9:                        # lo que sobre sale al final
        cobrado += restante * (camino[-1][1] / e)
    return cobrado, motivo


def pnl_escalera(ops: list[dict]) -> tuple[float, dict[str, int]]:
    coste = BUY * (1 + VAR) + POR_TX
    total = 0.0
    motivos: dict[str, int] = {}
    for p in ops:
        r, mot = simular_escalera(p)
        motivos[mot] = motivos.get(mot, 0) + 1
        # una escalera de 3 tramos son hasta 3 ventas: 3 cuotas fijas
        n_ventas = 3 if mot in ("TP", "MC $14K") else 1
        total += BUY * r * (1 - VAR) - POR_TX * n_ventas - coste
    return total, motivos


def reglas_pnl(ops: list[dict]) -> list[tuple[str, int, float]]:
    """(nombre, aciertos, PnL en SOL) de cada regla de salida sobre las MISMAS
    entradas. Convencion pesimista: si entre dos sondeos toco el TP y el SL, se
    cuenta el stop."""
    coste = BUY * (1 + VAR) + POR_TX
    out = []
    for tp, sl in ((0.35, 0.30), (0.50, 0.30), (1.00, 0.30), (1.00, 0.50),
                   (2.00, 0.50), (0.35, 0.18)):
        total, aciertos = 0.0, 0
        for p in ops:
            e = p["entrada"]
            alto, bajo = p["max"] / e, p["min"] / e
            if bajo <= 1 - sl:
                r = 1 - sl
            elif alto >= 1 + tp:
                r = 1 + tp
                aciertos += 1
            else:
                r = p.get("salida", e) / e
            total += BUY * r * (1 - VAR) - POR_TX - coste
        out.append((f"TP +{tp*100:.0f}% / SL -{sl*100:.0f}%", aciertos, total))
    return out


async def informe_telegram(ops: list[dict], pr: Prueba, args: argparse.Namespace) -> None:
    """El PnL supuesto, al Telegram."""
    p = [f"🧪 <b>PRUEBA EN PAPEL · volumen al segundo {args.segundo:.0f}</b>",
         f"Umbral ${args.umbral:,.0f} · {'con' if args.incluir_mayhem else 'sin'} mayhem",
         "",
         f"Lanzamientos vistos: <b>{pr.n_lanz}</b>",
         f"Curva leida: {len(pr.volumenes)} · ilegibles: {pr.ilegibles}",
         f"Pasaron el umbral: <b>{len(ops)}</b>", ""]
    if pr.volumenes:
        v = sorted(pr.volumenes)
        def q(x: float) -> float:
            return v[min(int(len(v) * x), len(v) - 1)]
        p.append("💵 <b>Volumen al segundo 1</b>")
        p.append(f"mediana ${q(.5):,.0f} · p90 ${q(.9):,.0f} · max ${v[-1]:,.0f}")
        for u in (200, 400, 800, 1500, 3000):
            n = sum(1 for x in v if x >= u)
            p.append(f"  ≥ ${u:,}: {n} ({n/len(v)*100:.1f}%)")
        p.append("")
    if ops:
        tot_e, motivos = pnl_escalera(ops)
        p.append("🪜 <b>TU ESCALERA</b> (SL -35% · +40% vende 60% · "
                 "+120% vende 20% · resto a MC $14K)")
        p.append(f"  <b>{tot_e:+.4f} SOL</b> en {len(ops)} operaciones "
                 f"({tot_e/len(ops)/BUY*100:+.1f}% por operacion)")
        p.append("  " + " · ".join(f"{k}: {v}" for k, v in sorted(motivos.items())))
        p.append("")
        p.append("📊 <b>Otras salidas, mismas entradas</b>")
        for nom, ac, tot in reglas_pnl(ops):
            p.append(f"  {nom}: {ac}/{len(ops)} · <b>{tot:+.4f} SOL</b> "
                     f"({tot/len(ops)/BUY*100:+.0f}%/op)")
    else:
        p.append("⚠️ Ninguna moneda supero el umbral: sin operaciones que evaluar.")
    await avisar("\n".join(p))


def evaluar(ops: list[dict]) -> None:
    if not ops:
        print("\nsin operaciones: nada supero el umbral")
        return
    print(f"\n{'='*72}\nRESULTADO · {len(ops)} entradas\n{'='*72}")
    print(f"{'simbolo':12} {'vol 1s':>9} {'MC entrada':>11} {'maximo':>8} {'minimo':>8} {'final':>8}")
    print("-" * 62)
    for p in sorted(ops, key=lambda x: -x["max"] / x["entrada"]):
        e = p["entrada"]
        print(f"{str(p['sym'])[:12]:12} ${p['vol_usd']:>8,.0f} ${p['mc_entrada']:>10,.0f} "
              f"x{p['max']/e:>7.2f} x{p['min']/e:>7.2f} x{p.get('salida', e)/e:>7.2f}")

    print(f"\n{'REGLA DE SALIDA':28} {'acierta':>8} {'PnL supuesto':>14} {'por op':>9}")
    print("-" * 62)
    coste = BUY * (1 + VAR) + POR_TX
    for tp, sl in ((0.35, 0.30), (0.50, 0.30), (1.00, 0.30), (1.00, 0.50),
                   (2.00, 0.50), (0.35, 0.18)):
        total = 0.0
        aciertos = 0
        for p in ops:
            e = p["entrada"]
            alto, bajo = p["max"] / e, p["min"] / e
            # peor caso: si toco los dos niveles, se asume primero el stop
            if bajo <= 1 - sl:
                r = 1 - sl
            elif alto >= 1 + tp:
                r = 1 + tp
                aciertos += 1
            else:
                r = p.get("salida", e) / e
            total += BUY * r * (1 - VAR) - POR_TX - coste
        print(f"TP +{tp*100:>3.0f}% / SL -{sl*100:>2.0f}%{'':10} {aciertos/len(ops)*100:>7.0f}% "
              f"{total:>+13.4f} SOL {total/len(ops)/BUY*100:>+8.1f}%")


async def main() -> None:
    _load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--umbral", type=float, default=800, help="volumen minimo en USD al segundo 1")
    p.add_argument("--segundo", type=float, default=1.0, help="cuando mirar la curva")
    p.add_argument("--minutos", type=float, default=30, help="cuanto tiempo buscar entradas")
    p.add_argument("--vigilar", type=float, default=300, help="segundos siguiendo cada moneda")
    p.add_argument("--sondeo", type=float, default=5)
    p.add_argument("--incluir-mayhem", action="store_true")
    args = p.parse_args()

    async with aiohttp.ClientSession() as session:
        sol = await precio_sol(session)
    await avisar(
        "🧪 <b>Prueba en papel arrancada</b>\n"
        f"Compra si el volumen al segundo {args.segundo:.0f} supera "
        f"<b>${args.umbral:,.0f}</b>\n"
        f"{'Con' if args.incluir_mayhem else 'Sin'} mayhem · "
        f"buscando {args.minutos:.0f} min\n"
        "No toca la wallet."
    )
    print(f"SOL = ${sol:,.2f} · umbral ${args.umbral:,.0f} al segundo {args.segundo:.0f} · "
          f"{'con' if args.incluir_mayhem else 'SIN'} mayhem")
    print(f"buscando entradas {args.minutos:.0f} min · siguiendo cada moneda {args.vigilar:.0f}s\n")

    pr = Prueba(args, sol)
    listener = UniversalLogsListener(os.environ["SOLANA_NODE_WSS_ENDPOINT"],
                                     platforms=[Platform.PUMP_FUN])
    escucha = asyncio.create_task(listener.listen_for_tokens(pr.al_nacer))
    seguidor = asyncio.create_task(pr.seguir())
    await asyncio.sleep(args.minutos * 60)
    escucha.cancel()
    log(f"fin de la busqueda · {pr.n_lanz} lanzamientos vistos · "
        f"{len(pr.abiertas) + len(pr.cerradas)} pasaron el umbral")
    await asyncio.sleep(args.vigilar + 10)
    seguidor.cancel()

    todas = pr.cerradas + list(pr.abiertas.values())
    # El camino del precio SE GUARDA: sin el no se puede reevaluar una escalera
    # despues (lo que cobras depende del orden de los sucesos, no del maximo).
    # Solo se recorta el objeto Pubkey de la curva, que no es serializable.
    Path("paper_1s_resultado.json").write_text(
        json.dumps([{k: (str(v) if k == "curva" else v) for k, v in p.items()}
                    for p in todas], indent=1),
        encoding="utf-8")
    evaluar(todas)
    await informe_telegram(todas, pr, args)
    print(f"\nlanzamientos vistos: {pr.n_lanz} · pasaron el umbral: {len(todas)} "
          f"({len(todas)/max(pr.n_lanz,1)*100:.1f}%)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("cortado")
