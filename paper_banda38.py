"""Prueba en papel de la estrategia BANDA 3-3.8K, con avisos por Telegram.

Es el equivalente exacto de bots/bot-sniper-banda38.yaml, pero sin tocar la
wallet: apunta lo que habria comprado y vendido y va contando el P&L.

  ENTRADA  moneda de menos de 60 segundos
           market cap entre $3.000 y $3.800
           al menos 10 SOL en la curva (el "volumen" comprobable)
  SALIDA   +50%  -> vende el 50%
           +125% -> vende el 25%
           el 25% restante lo sigue un trailing stop al 80% del maximo
           stop -35% SOLO antes del primer cobro · aguante 10 minutos

Comisiones reales: 2,25% por lado + 0,0018 SOL por transaccion (medido en la
cadena el 1-ago). Cada tramo de la escalera es una venta, o sea otra cuota.
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
SUPPLY = 1_000_000_000
VAR, POR_TX = 0.0225, 0.0018

MC_MIN, MC_MAX = 3000.0, 3800.0
#: 10 SOL (~$730 de volumen equivalente). Medido el 3-ago: exigir volumen sube
#: el porcentaje de ganadoras COBRABLES del 15% al 22%, pero solo hasta ~$1.000
#: -- a partir de $3.000 vuelve a bajar al 17% y encima quita un tercio de las
#: oportunidades. Asi que se sube un poco, no mucho.
CURVA_MIN_SOL = 10.0
TRAMOS = [(0.50, 0.50), (1.25, 0.25)]

#: El 25% restante ya NO espera a un objetivo fijo: lo sigue un trailing stop.
#:
#: En la tanda del 3-ago, TRES de las seis ganadoras devolvieron el resto al
#: stop: subian por encima de x1.5, cobraban el primer tramo, y el 25-50% que
#: quedaba acababa saliendo a x0.65. Pasaba porque el stop se mide contra el
#: precio de ENTRADA y no se mueve nunca.
#:
#: Midiendo las tres reglas posibles sobre esas mismas operaciones:
#:   stop -35% desde la entrada        +0,4752 SOL en las ganadoras
#:   stop a break-even tras el 1er TP  +0,5394
#:   trailing al 80% del maximo        +0,6083   <-- el mejor
#:
#: Se quita tambien el objetivo fijo de $14.000: solo lo alcanzo 1 de 6, y
#: mientras esperaba, las otras se desangraban. El trailing hace ese trabajo
#: mejor -- si llega, lo cobra; si se da la vuelta antes, tambien.
TRAILING = 0.80

SL = 0.35
AGUANTE = 600
EDAD_MAX = 60

#: Ventanas de edad (segundos desde el nacimiento) que se comparan.
#:
#: La tanda del 3-ago 08:10-08:30 dio un resultado sin excepciones sobre 22
#: operaciones: de las que entraron a los 5 SEGUNDOS, 0 de 8 tocaron el TP.
#: A los 10s acertaron 3 de 8 (38%) y a partir de 15s, 3 de 6 (50%).
#:
#: Y las otras dos cosas que se saben al comprar NO separan nada: el market cap
#: de entrada fue $3.486 en las ganadoras y $3.521 en las perdedoras; el SOL en
#: curva, 9,23 contra 9,44. Solo la edad discrimina.
#:
#: El motivo: una moneda que llega a $3.500 en cinco segundos es una donde el
#: creador metio 9-10 SOL el solo -- nadie mas ha tenido tiempo de verla. Eso
#: es un bundle y detras viene el volcado. Si tarda 12-36 segundos es porque
#: esta entrando gente, y esa demanda puede seguir empujando.
#:
#: Asi que se elimina la ventana de 5s y se pasa a preguntar hasta donde llega
#: la mejora: (edad minima, edad maxima).
VENTANAS = {"MEDIA": (8.0, 15.0), "TARDIA": (15.0, 60.0)}


def _load_dotenv() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def limpio(t: object, n: int = 14) -> str:
    """Los nombres de las memecoins llevan emojis y caracteres invisibles que
    tumban la consola de Windows (cp1252). Ya paso dos veces."""
    return str(t or "?")[:n].encode("ascii", "replace").decode()


def log(m: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {m}", flush=True)


async def avisar(texto: str) -> None:
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
                log(f"telegram {r.status}: {(await r.text())[:100]}")
    except Exception as exc:  # noqa: BLE001
        log(f"telegram fallo: {exc}")


async def precio_sol() -> float:
    try:
        u = ("https://frontend-api-v3.pump.fun/coins?offset=0&limit=50"
             "&sort=created_timestamp&order=DESC&includeNsfw=true")
        async with aiohttp.ClientSession() as s, s.get(
                u, headers={"User-Agent": "Mozilla/5.0"}, timeout=20) as r:
            d = await r.json()
        v = [c["usd_market_cap"] / c["market_cap"] for c in d if c.get("market_cap")]
        return statistics.median(v) if v else 73.0
    except Exception:  # noqa: BLE001
        return 73.0


class Prueba:
    def __init__(self, args: argparse.Namespace, sol: float) -> None:
        self.args = args
        self.sol = sol
        self.client = SolanaClient(os.environ["SOLANA_NODE_RPC_ENDPOINT"], max_rps=10.0)
        self.impl = platform_factory.create_for_platform(Platform.PUMP_FUN, self.client)
        self.vistos: set[str] = set()
        self.abiertas: dict[str, dict] = {}
        self.cerradas: list[dict] = []
        self.n_lanz = 0
        self.n_banda = 0        # llegaron a la banda pero sin curva suficiente
        self.invertido = dict.fromkeys(VENTANAS, 0.0)
        self.realizado = dict.fromkeys(VENTANAS, 0.0)
        self.tareas: set[asyncio.Task] = set()

    async def _curva(self, bc: object) -> dict | None:
        try:
            # "processed" es obligatorio: con "confirmed" la cuenta todavia no
            # existe en los primeros segundos y fallan TODAS las lecturas
            return await self.impl.curve_manager.get_pool_state(bc, commitment="processed")
        except Exception:  # noqa: BLE001
            return None

    async def al_nacer(self, t: TokenInfo) -> None:
        m = str(t.mint)
        if m in self.vistos:
            return
        self.vistos.add(m)
        self.n_lanz += 1
        if t.is_mayhem_mode:
            return                      # allow_mayhem: false
        tarea = asyncio.create_task(self.vigilar_entrada(t))
        self.tareas.add(tarea)
        tarea.add_done_callback(self.tareas.discard)

    async def vigilar_entrada(self, t: TokenInfo) -> None:
        """Mira el primer minuto a ver si entra en la banda con curva suficiente.

        Se evaluan DOS ventanas de edad sobre la MISMA moneda:

          RAPIDA  entra solo si cumple dentro de los primeros 10 segundos. Eso
                  selecciona los lanzamientos donde el creador mete el dinero de
                  golpe al nacer -- no da tiempo a que entre nadie mas.
          NORMAL  hasta 60 segundos, que da margen a que la moneda la empuje
                  gente de fuera.

        Una moneda que califica a los 8s abre posicion en las dos: asi la
        comparacion aisla la ventana y no el momento del mercado.
        """
        t0 = time.time()
        aviso_banda = False
        pendientes = set(VENTANAS)          # {"RAPIDA", "NORMAL"}
        while time.time() - t0 < EDAD_MAX and pendientes:
            await asyncio.sleep(self.args.sondeo)
            st = await self._curva(t.bonding_curve)
            if st is None:
                continue
            precio = float(st.get("price_per_token") or 0)
            if precio <= 0:
                continue
            edad = time.time() - t0
            mc = precio * SUPPLY * self.sol
            curva = float(st.get("real_sol_reserves", 0)) / LAMPORTS
            if not (MC_MIN <= mc <= MC_MAX):
                continue
            if not aviso_banda:
                aviso_banda = True
                self.n_banda += 1
            if curva < CURVA_MIN_SOL:
                continue                # en la banda pero sin dinero detras
            for v in list(pendientes):
                lo, hi = VENTANAS[v]
                if lo <= edad <= hi:
                    pendientes.discard(v)
                    await self._abrir(t, precio, mc, curva, v, edad)
                elif edad > hi:
                    pendientes.discard(v)   # esa ventana ya se paso

    async def _abrir(self, t: TokenInfo, precio: float, mc: float, curva: float,
                     variante: str, edad: float) -> None:
        clave = f"{variante}:{t.mint}"
        if clave in self.abiertas:
            return
        coste = self.args.posicion * (1 + VAR) + POR_TX
        self.abiertas[clave] = {
            "var": variante, "sym": limpio(t.symbol), "mint": str(t.mint),
            "entrada": precio, "mc": mc, "curva": curva, "edad": edad,
            "bc": t.bonding_curve, "t0": time.time(),
            "tramos": list(TRAMOS), "restante": 1.0, "cobrado": 0.0, "coste": coste,
            "max": precio,
        }
        self.invertido[variante] += coste
        log(f"  [{variante}] COMPRA {limpio(t.symbol)} · MC ${mc:,.0f} · "
            f"curva {curva:.1f} SOL · a los {edad:.0f}s")
        await avisar(
            f"🟢 <b>COMPRA [{variante}]</b> · <b>{limpio(t.symbol, 16)}</b> · "
            f"{self.args.posicion} SOL\n"
            f"⏱️ a los <b>{edad:.0f} segundos</b> de nacer\n"
            f"📈 MC entrada: <b>${mc:,.0f}</b>\n"
            f"💧 Curva: {curva:.2f} SOL\n"
            f"🎯 +50% vende 50% · +125% vende 25% · resto con trailing\n"
            f"🛑 SL -{SL*100:.0f}% · ⏱️ {AGUANTE//60} min\n"
            f"CA: <code>{t.mint}</code>"
        )

    async def _vender(self, p: dict, frac: float, r: float, etq: str, emoji: str) -> None:
        frac = min(frac, p["restante"])
        if frac <= 1e-9:
            return
        neto = self.args.posicion * frac * r * (1 - VAR) - POR_TX
        p["cobrado"] += neto
        p["restante"] -= frac
        self.realizado[p["var"]] += neto
        log(f"  [{p['var']}] {etq} {p['sym']} · x{r:.2f} · {neto:+.4f} SOL · "
            f"acumulado {self.realizado[p['var']] - self.invertido[p['var']]:+.4f}")
        await avisar(
            f"{emoji} <b>{etq}</b> · <b>{p['sym']}</b>\n"
            f"Vendido {frac*100:.0f}% a x{r:.2f} → <b>{neto:+.4f} SOL</b>\n"
            f"Queda {p['restante']*100:.0f}%\n"
            f"📊 P&L [{p['var']}]: "
            f"<b>{self.realizado[p['var']] - self.invertido[p['var']]:+.4f} SOL</b>"
        )

    async def seguir(self) -> None:
        while True:
            await asyncio.sleep(self.args.sondeo)
            ahora = time.time()
            for clave, p in list(self.abiertas.items()):
                st = await self._curva(p["bc"])
                precio = float(st.get("price_per_token") or 0) if st else 0.0
                liq = (float(st.get("real_sol_reserves", 0)) / LAMPORTS) if st else None
                vencido = ahora - p["t0"] >= AGUANTE
                if precio <= 0 and not vencido:
                    continue
                if precio > 0:
                    p["max"] = max(p["max"], precio)
                r = (precio / p["entrada"]) if precio > 0 else 1 - SL
                r_max = p["max"] / p["entrada"]
                cobro_hecho = p["restante"] < 0.999
                cerrar = None

                # El stop cambia de sitio en cuanto se cobra el primer tramo:
                # antes protege la entrada, despues protege lo ganado.
                if not cobro_hecho and r <= 1 - SL:
                    await self._vender(p, p["restante"], 1 - SL,
                                       f"STOP -{SL*100:.0f}%", "🛑", liq)
                    cerrar = "SL"
                elif cobro_hecho and r <= r_max * TRAILING:
                    await self._vender(p, p["restante"], r_max * TRAILING,
                                       f"TRAILING (-{(1-TRAILING)*100:.0f}% del maximo)",
                                       "📉", liq)
                    cerrar = "TRAILING"
                else:
                    while p["tramos"] and r >= 1 + p["tramos"][0][0]:
                        g, fr = p["tramos"].pop(0)
                        await self._vender(p, fr, r, f"TP +{g*100:.0f}%", "✅", liq)
                    if p["restante"] <= 1e-9:
                        cerrar = "COMPLETA"
                    elif vencido:
                        await self._vender(p, p["restante"], r, "CIERRE POR TIEMPO", "⏱️", liq)
                        cerrar = "TIEMPO"
                if cerrar:
                    p["motivo"] = cerrar
                    p["pnl"] = p["cobrado"] - p["coste"]
                    self.cerradas.append(p)
                    del self.abiertas[clave]

    def resumen(self) -> str:
        p = ["📊 <b>BANDA 3-3.8K · resultado</b>", "",
             f"Lanzamientos vistos: {self.n_lanz}",
             f"Llegaron a la banda: {self.n_banda}", ""]
        for v, seg in VENTANAS.items():
            ops = [x for x in self.cerradas if x["var"] == v]
            p.append(f"<b>{v}</b> (entrada en los primeros {seg:.0f}s)")
            if not ops:
                p.append("  sin operaciones cerradas\n")
                continue
            tot = sum(x["pnl"] for x in ops)
            gan = sum(1 for x in ops if x["pnl"] > 0)
            mot: dict[str, int] = {}
            for x in ops:
                mot[x["motivo"]] = mot.get(x["motivo"], 0) + 1
            edad_med = statistics.median([x["edad"] for x in ops])
            p.append(f"  <b>{tot:+.4f} SOL</b> en {len(ops)} ops · {gan} en verde")
            p.append(f"  {tot/len(ops)/self.args.posicion*100:+.1f}% por operación · "
                     f"entrada mediana a los {edad_med:.0f}s")
            p.append("  " + " · ".join(f"{k}: {n}" for k, n in sorted(mot.items())) + "\n")
        return "\n".join(p)


async def main() -> None:
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=120)
    ap.add_argument("--posicion", type=float, default=0.15)
    ap.add_argument("--sondeo", type=float, default=10)
    args = ap.parse_args()

    sol = await precio_sol()
    print(f"SOL ${sol:,.2f} · posicion {args.posicion} SOL · {args.minutos:.0f} min")
    print(f"entrada: MC ${MC_MIN:,.0f}-${MC_MAX:,.0f} · curva >= {CURVA_MIN_SOL} SOL "
          f"· edad < {EDAD_MAX}s · sin mayhem\n")

    pr = Prueba(args, sol)
    await avisar(
        f"🧪 <b>BANDA 3-3.8K arrancada</b> (papel)\n"
        f"Entrada: MC ${MC_MIN:,.0f}-${MC_MAX:,.0f} · curva ≥{CURVA_MIN_SOL:.0f} SOL "
        f"· menos de {EDAD_MAX}s · sin mayhem\n"
        f"Salida: +50% (50%) · +125% (25%) · resto con trailing al "
        f"{TRAILING*100:.0f}% del máximo\n"
        f"SL -{SL*100:.0f}% solo antes del primer cobro\n"
        f"Posición {args.posicion} SOL · no toca la wallet."
    )
    lis = UniversalLogsListener(os.environ["SOLANA_NODE_WSS_ENDPOINT"],
                                platforms=[Platform.PUMP_FUN])
    esc = asyncio.create_task(lis.listen_for_tokens(pr.al_nacer))
    seg = asyncio.create_task(pr.seguir())
    await asyncio.sleep(args.minutos * 60)
    esc.cancel()
    log(f"fin · {pr.n_lanz} lanzamientos · {pr.n_banda} en la banda · "
        f"{len(pr.abiertas)} abiertas")
    await asyncio.sleep(AGUANTE + 20)
    seg.cancel()

    Path("paper_banda38_resultado.json").write_text(
        json.dumps([{k: (str(v) if k == "bc" else v) for k, v in x.items()}
                    for x in pr.cerradas], indent=1), encoding="utf-8")
    print("\n" + pr.resumen().replace("<b>", "").replace("</b>", ""))
    await avisar(pr.resumen())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("cortado")
