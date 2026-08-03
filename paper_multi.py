"""Tres estrategias en papel a la vez, sobre el MISMO flujo de lanzamientos.

Por que juntas y no por separado
--------------------------------
Comparten listener y lecturas de curva, asi que la comparacion es limpia: las
tres ven exactamente las mismas monedas en el mismo instante. Si se corrieran
por separado, cada una vería una ventana distinta del mercado y no se podrian
comparar -- que es justo el error que hacía irreales las medidas de ayer.

Las tres
--------
A  MC $5K     no-mayhem, volumen >=$1.100 en los primeros 30s, y se entra
              cuando el market cap toca $5.000. Es la unica configuracion que
              hoy ha sobrevivido a las pruebas de robustez (+3,25 SOL en 19
              operaciones, sin depender de un solo pelotazo).
B  VOL $3K    se entra en cuanto el volumen acumulado pasa de $3.000, si
              ocurre dentro del primer minuto.
C  50 HOLDERS se entra si a los 30 segundos hay 50 titulares o mas.

La SALIDA es la misma en las tres (TP x5, stop -40%, aguante 10 min) a
proposito: asi lo unico que se compara es la ENTRADA. Si cada una llevara su
propia salida no se sabria cual de las dos cosas marca la diferencia.

No toca la wallet. Avisa por Telegram de cada compra y cada venta.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
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

#: Salida comun a las tres. x5 salio del barrido con el recorrido real del
#: precio: por debajo de x2.5 todas las combinaciones daban negativo, y el
#: orden se mantuvo al partir la muestra en dos mitades.
TP, SL, AGUANTE = 4.0, 0.40, 600

GMGN = shutil.which("gmgn-cli") or shutil.which("gmgn-cli.cmd")


def _load_dotenv() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def limpio(t: object, n: int = 12) -> str:
    """Los nombres llevan emojis y caracteres invisibles que tumban la consola."""
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
                log(f"telegram {r.status}")
    except Exception as exc:  # noqa: BLE001
        log(f"telegram fallo: {exc}")


async def holders(mint: str) -> int:
    """Cuantos titulares tiene. GMGN devuelve hasta 100, de sobra para un corte
    en 50. Se llama en un hilo aparte para no bloquear el bucle de eventos."""
    if not GMGN:
        return -1
    def _run() -> int:
        try:
            r = subprocess.run(  # noqa: S603
                [GMGN, "token", "holders", "--chain", "sol", "--address", mint,
                 "--limit", "100", "--raw"],
                capture_output=True, text=True, timeout=25,
                encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                return -1
            return len(json.loads(r.stdout).get("list") or [])
        except Exception:  # noqa: BLE001
            return -1
    return await asyncio.to_thread(_run)


class Multi:
    def __init__(self, args: argparse.Namespace, sol_usd: float) -> None:
        self.args = args
        self.sol = sol_usd
        self.client = SolanaClient(os.environ["SOLANA_NODE_RPC_ENDPOINT"], max_rps=18.0)
        self.impl = platform_factory.create_for_platform(Platform.PUMP_FUN, self.client)
        self.vistos: set[str] = set()
        self.abiertas: dict[str, dict] = {}
        self.cerradas: list[dict] = []
        self.n_lanz = 0
        self.tareas: set[asyncio.Task] = set()
        self.saldo: dict[str, float] = {"A": 0.0, "B": 0.0, "C": 0.0}

    # ---------------------------------------------------------------- curva #
    async def _curva(self, bc: object) -> dict | None:
        try:
            # "processed" o la cuenta no existe todavia en los primeros segundos
            return await self.impl.curve_manager.get_pool_state(bc, commitment="processed")
        except Exception:  # noqa: BLE001
            return None

    def _liquidez_sol(self, st: dict) -> float:
        """SOL que hay REALMENTE en la curva para pagarte al vender.

        Sin esto, el precio miente. TNOS marcaba $27 millones de market cap con
        0,198 SOL en el pool: alguien saco los 85 SOL iniciales y dejo la
        cascara, y el precio se disparo porque se divide por casi cero. Vender
        100 tokens ahi daba 1.755 lamports -- nada.

        El backtest de hoy contaba esas monedas como aciertos de x5. Cuatro de
        los siete "aciertos" eran pools vaciados: quitandolas, +3,25 SOL se
        quedaban en -0,02.
        """
        return float(st.get("real_sol_reserves", 0)) / LAMPORTS

    def _vendible(self, st: dict, tam: float) -> bool:
        """¿Hay SOL suficiente para pagar una salida de este tamaño?

        Se exige un multiplo de la posicion, no un valor fijo: sacar 0,15 SOL
        de un pool con 0,2 SOL mueve el precio tanto que lo que cobras no se
        parece al precio marcado.
        """
        return self._liquidez_sol(st) >= tam * self.args.liquidez_min_x

    # -------------------------------------------------------------- entradas #
    async def al_nacer(self, t: TokenInfo) -> None:
        m = str(t.mint)
        if m in self.vistos:
            return
        self.vistos.add(m)
        self.n_lanz += 1
        tarea = asyncio.create_task(self.evaluar(t))
        self.tareas.add(tarea)
        tarea.add_done_callback(self.tareas.discard)

    async def evaluar(self, t: TokenInfo) -> None:
        """Vigila el primer minuto y decide si alguna estrategia entra."""
        mint = str(t.mint)
        t0 = time.time()
        vol30 = 0.0
        entradas: dict[str, bool] = {}
        holders_hecho = False

        while time.time() - t0 < 65:
            await asyncio.sleep(self.args.sondeo)
            st = await self._curva(t.bonding_curve)
            if st is None:
                continue
            edad = time.time() - t0
            vol = float(st.get("real_sol_reserves", 0)) / LAMPORTS * self.sol
            precio = float(st.get("price_per_token") or 0)
            if precio <= 0:
                continue
            mc = precio * SUPPLY * self.sol
            if edad <= 32:
                vol30 = max(vol30, vol)

            # --- B: volumen $3.000 dentro del primer minuto ---
            if "B" not in entradas and vol >= self.args.vol_b and edad <= 60:
                entradas["B"] = True
                await self._abrir("B", t, precio, mc, f"volumen ${vol:,.0f} a los {edad:.0f}s")

            # --- C: 50 titulares a los 30 segundos ---
            if (not holders_hecho and 28 <= edad <= 40 and vol >= 100):
                holders_hecho = True
                n = await holders(mint)
                if n >= self.args.holders_c:
                    entradas["C"] = True
                    await self._abrir("C", t, precio, mc, f"{n} titulares a los {edad:.0f}s")

            # --- A: no-mayhem + vol30 y esperar a que el MC toque $5.000 ---
            if ("A" not in entradas and not t.is_mayhem_mode
                    and vol30 >= self.args.vol_a and mc >= self.args.mc_a):
                entradas["A"] = True
                await self._abrir("A", t, precio, mc, f"MC ${mc:,.0f} con vol30 ${vol30:,.0f}")

            if len(entradas) == 3:
                break

    async def _abrir(self, est: str, t: TokenInfo, precio: float, mc: float,
                     motivo: str) -> None:
        clave = f"{est}:{t.mint}"
        if clave in self.abiertas:
            return
        self.abiertas[clave] = {
            "est": est, "sym": limpio(t.symbol, 16), "mint": str(t.mint),
            "entrada": precio, "mc": mc, "t0": time.time(),
            "curva": t.bonding_curve, "max": precio, "min": precio,
            "mayhem": bool(t.is_mayhem_mode),
        }
        log(f"  [{est}] COMPRA {limpio(t.symbol)} · {motivo}")
        await avisar(
            f"🟢 <b>COMPRA [{est}]</b> · <b>{limpio(t.symbol, 16)}</b> · "
            f"{self.args.posicion} SOL\n"
            f"📌 {motivo}\n"
            f"📈 MC entrada: ${mc:,.0f}\n"
            f"🎯 TP x{1+TP:.0f} · 🛑 SL -{SL*100:.0f}% · ⏱️ {AGUANTE//60} min\n"
            f"CA: <code>{t.mint}</code>"
        )

    # --------------------------------------------------------------- salidas #
    async def seguir(self) -> None:
        buy = self.args.posicion
        coste = buy * (1 + VAR) + POR_TX
        while True:
            await asyncio.sleep(self.args.sondeo)
            ahora = time.time()
            for clave, p in list(self.abiertas.items()):
                st = await self._curva(p["curva"])
                precio = float(st.get("price_per_token") or 0) if st else 0.0
                vencido = ahora - p["t0"] >= AGUANTE
                if precio <= 0 and not vencido:
                    continue
                if precio > 0:
                    p["max"] = max(p["max"], precio)
                    p["min"] = min(p["min"], precio)
                r = (precio / p["entrada"]) if precio > 0 else (p["min"] / p["entrada"])
                motivo = None
                if r <= 1 - SL:
                    r, motivo = 1 - SL, "SL"
                elif r >= 1 + TP:
                    r, motivo = 1 + TP, "TP"
                elif vencido:
                    motivo = "TIEMPO"
                if not motivo:
                    continue
                pnl = buy * r * (1 - VAR) - POR_TX - coste
                p.update(salida=r, motivo=motivo, pnl=pnl)
                self.saldo[p["est"]] += pnl
                self.cerradas.append(p)
                del self.abiertas[clave]
                emoji = {"TP": "✅", "SL": "🛑", "TIEMPO": "⏱️"}[motivo]
                await avisar(
                    f"{emoji} <b>{motivo} [{p['est']}]</b> · <b>{p['sym']}</b>\n"
                    f"Salida x{r:.2f} → <b>{pnl:+.4f} SOL</b>\n"
                    f"📊 [{p['est']}] acumulado: <b>{self.saldo[p['est']]:+.4f} SOL</b>"
                )

    # -------------------------------------------------------------- informe #
    def resumen(self) -> str:
        p = ["🧪 <b>RESULTADO · 3 estrategias</b>",
             f"Lanzamientos vistos: {self.n_lanz}", ""]
        nombres = {"A": f"A · MC ${self.args.mc_a:,.0f} (no-mayhem, vol30≥${self.args.vol_a:,.0f})",
                   "B": f"B · volumen ≥${self.args.vol_b:,.0f} en el primer minuto",
                   "C": f"C · ≥{self.args.holders_c} titulares a los 30s"}
        for est in ("A", "B", "C"):
            ops = [x for x in self.cerradas if x["est"] == est]
            p.append(f"<b>{nombres[est]}</b>")
            if not ops:
                p.append("  sin operaciones\n")
                continue
            tp = sum(1 for x in ops if x["motivo"] == "TP")
            tot = sum(x["pnl"] for x in ops)
            p.append(f"  {len(ops)} ops · {tp} aciertos ({tp/len(ops)*100:.0f}%) · "
                     f"<b>{tot:+.4f} SOL</b> ({tot/len(ops)/self.args.posicion*100:+.0f}%/op)\n")
        return "\n".join(p)


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


async def main() -> None:
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=90)
    ap.add_argument("--posicion", type=float, default=0.15)
    ap.add_argument("--sondeo", type=float, default=5)
    ap.add_argument("--mc-a", type=float, default=5000)
    ap.add_argument("--vol-a", type=float, default=1100)
    ap.add_argument("--vol-b", type=float, default=3000)
    ap.add_argument("--holders-c", type=int, default=50)
    args = ap.parse_args()

    sol = await precio_sol()
    print(f"SOL ${sol:,.2f} · posicion {args.posicion} SOL · {args.minutos:.0f} min")
    print(f"  A: MC ${args.mc_a:,.0f}, no-mayhem, vol30 >= ${args.vol_a:,.0f}")
    print(f"  B: volumen >= ${args.vol_b:,.0f} en el primer minuto")
    print(f"  C: >= {args.holders_c} titulares a los 30s")
    print(f"  salida comun: TP x{1+TP:.0f} · SL -{SL*100:.0f}% · {AGUANTE//60} min\n")

    m = Multi(args, sol)
    await avisar(
        f"🧪 <b>3 estrategias en papel</b>\n"
        f"A: MC ${args.mc_a:,.0f} (no-mayhem, vol30≥${args.vol_a:,.0f})\n"
        f"B: volumen ≥${args.vol_b:,.0f} en el primer minuto\n"
        f"C: ≥{args.holders_c} titulares a los 30s\n"
        f"Salida comun: TP x{1+TP:.0f} · SL -{SL*100:.0f}%\n"
        f"Posicion {args.posicion} SOL · no toca la wallet."
    )
    lis = UniversalLogsListener(os.environ["SOLANA_NODE_WSS_ENDPOINT"],
                                platforms=[Platform.PUMP_FUN])
    esc = asyncio.create_task(lis.listen_for_tokens(m.al_nacer))
    seg = asyncio.create_task(m.seguir())
    await asyncio.sleep(args.minutos * 60)
    esc.cancel()
    log(f"fin de la busqueda · {m.n_lanz} lanzamientos · {len(m.abiertas)} abiertas")
    await asyncio.sleep(AGUANTE + 20)
    seg.cancel()

    Path("paper_multi_resultado.json").write_text(
        json.dumps([{k: (str(v) if k == "curva" else v) for k, v in x.items()}
                    for x in m.cerradas], indent=1), encoding="utf-8")
    print("\n" + m.resumen().replace("<b>", "").replace("</b>", ""))
    await avisar(m.resumen())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("cortado")
