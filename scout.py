"""Explorador 24/7 de devs: encuentra quien pumpea de verdad, sin rugs.

El problema que resuelve
------------------------
La lista de devs del bot se calculo una vez y se quedo quieta: de 106 wallets
vigiladas, 103 no dispararon ni una sola compra, y todo el beneficio historico
vino de creadores que NO estaban en ninguna lista. Una lista fija envejece mal
porque los devs que lanzaban esta semana dejan de lanzar la siguiente.

Esto lo arregla escuchando el feed de lanzamientos 24/7, siguiendo que pasa con
cada moneda, y manteniendo un marcador vivo de quien merece la pena.

Como decide que un dev es bueno
-------------------------------
Tres condiciones, y las tres tienen que cumplirse:

1. ACIERTA      un % minimo de sus monedas supera el objetivo de market cap.
2. NO RUGEA     ninguna (o casi) de sus monedas se desploma tras el pico ni
                acaba con el creador vaciando su bolsa.
3. ES ORGANICO  sus monedas las compra gente distinta, no tres carteras
                haciendose el volumen entre ellas.

Una tension que conviene tener presente
---------------------------------------
"Organico" y "lo que mas pumpea" no son lo mismo. El propio bot tiene un
detector de *bundles* (lanzamientos donde el operador se autocompra varios SOL
de golpe) porque, historicamente, esos son los que se mueven. Este explorador
los penaliza a proposito, porque se le pidio organico: el dinero rapido de un
bundle suele salir del bolsillo del que entra despues. Si algun dia se prefiere
lo contrario, es cambiar ORGANIC_MIN_MAKERS y el peso del bundle.

Que hace ademas
---------------
Vigila el dinero: si el saldo cae por debajo del suelo, PARA el bot. Si detecta
tokens huerfanos (en la wallet pero sin posicion abierta que los reclame), los
vende por Jupiter. Y manda un informe extendido cada 4 horas por Telegram.

Arranque
--------
    cd /root/Definitivo_bot
    ./.venv/bin/python scout.py

o como servicio (ver deploy/scout.service en el repo del panel).
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent / "src"))

import aiohttp
import websockets

# --------------------------------------------------------------------------- #
# Configuracion                                                               #
# --------------------------------------------------------------------------- #


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _load_dotenv() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
PUMPFUN_API = "https://frontend-api-v3.pump.fun/coins"
DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens"
SOL_MINT = "So11111111111111111111111111111111111111112"

STATE_DIR = Path("scout_state")
LANZAMIENTOS = STATE_DIR / "lanzamientos.jsonl"   # cada moneda vista
VEREDICTOS = STATE_DIR / "veredictos.jsonl"       # como acabo cada una
MARCADOR = STATE_DIR / "devs.json"                # marcador vivo por dev

#: Market cap que una moneda tiene que superar para contar como acierto. 8K es
#: el corte que usa la consulta de Dune, donde el 11,3% de los lanzamientos lo
#: consiguen: suficientemente raro para significar algo, suficientemente comun
#: para acumular muestra en dias y no en meses.
OBJETIVO_MC = float(_env("SCOUT_OBJETIVO_MC", "8000"))

#: Se evalua una moneda cuando tiene esta edad. Antes es pronto (no ha dado
#: tiempo a pumpear); mucho despues, la API deja de ser fiable para las que
#: murieron.
EVALUAR_A_MIN = float(_env("SCOUT_EVALUAR_MIN", "45"))
VENTANA_DIAS = float(_env("SCOUT_VENTANA_DIAS", "7"))

# --- criterios para entrar en la lista buena ---
MIN_LANZAMIENTOS = int(_env("SCOUT_MIN_LANZAMIENTOS", "3"))
MIN_ACIERTO = float(_env("SCOUT_MIN_ACIERTO", "60"))       # %
MAX_RUGS_PCT = float(_env("SCOUT_MAX_RUGS", "0"))          # % tolerado
ORGANIC_MIN_MAKERS = int(_env("SCOUT_MIN_MAKERS", "15"))   # compradores distintos

#: Caida desde el pico que se considera desplome. Una moneda que hace pico y se
#: deja mas de esto en la primera hora no es volatilidad: es que alguien vacio.
RUG_CAIDA_PCT = float(_env("SCOUT_RUG_CAIDA", "85"))

# --- guardian del dinero ---
SUELO_SOL = float(_env("SCOUT_SUELO_SOL", "0.15"))
VENDER_HUERFANOS = _env("SCOUT_VENDER_HUERFANOS", "true").lower() in ("1", "true", "yes")
#: Un token solo es huerfano si lleva mas de esto sin que ninguna posicion lo
#: reclame. max_hold_time del bot son 3 minutos, asi que 15 deja margen de sobra
#: para no vender por debajo de una operacion que sigue viva.
HUERFANO_MIN = float(_env("SCOUT_HUERFANO_MIN", "15"))
HUERFANO_MIN_USD = float(_env("SCOUT_HUERFANO_MIN_USD", "1.0"))

INFORME_HORAS = float(_env("SCOUT_INFORME_HORAS", "4"))
SECO = _env("SCOUT_DRY_RUN", "false").lower() in ("1", "true", "yes")

RPC = _env("SOLANA_NODE_RPC_ENDPOINT") or "https://api.mainnet-beta.solana.com"


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Telegram                                                                    #
# --------------------------------------------------------------------------- #


async def avisar(session: aiohttp.ClientSession, texto: str) -> None:
    """Manda un mensaje a Telegram. Nunca revienta el bucle si falla."""
    token, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("(sin Telegram configurado) " + texto[:120])
        return
    try:
        async with session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat,
                "text": texto[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                log(f"telegram {r.status}: {(await r.text())[:120]}")
    except Exception as exc:  # noqa: BLE001 -- avisar nunca debe tumbar el scout
        log(f"telegram fallo: {exc}")


# --------------------------------------------------------------------------- #
# Almacen                                                                     #
# --------------------------------------------------------------------------- #


def _anexar(path: Path, rec: dict[str, Any]) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _leer(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# --------------------------------------------------------------------------- #
# 1. Escucha: cada moneda nueva que sale                                      #
# --------------------------------------------------------------------------- #


async def escuchar(pendientes: dict[str, dict[str, Any]]) -> None:
    """Se engancha al feed de PumpPortal y apunta cada lanzamiento.

    Reconecta sola: este proceso tiene que aguantar dias, y una caida del
    websocket no puede dejarnos ciegos hasta que alguien lo mire.
    """
    espera = 5
    while True:
        try:
            async with websockets.connect(PUMPPORTAL_WS, ping_interval=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken", "params": []}))
                log("feed conectado")
                espera = 5
                async for raw in ws:
                    try:
                        d = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if "method" in d and d.get("method") == "newToken":
                        params = d.get("params") or []
                        d = params[0] if params else {}
                    mint = d.get("mint")
                    creator = d.get("traderPublicKey") or d.get("creator")
                    if not mint or not creator:
                        continue
                    if mint in pendientes:
                        continue
                    rec = {
                        "mint": mint,
                        "creator": creator,
                        "symbol": (d.get("symbol") or "?")[:16],
                        "visto": time.time(),
                    }
                    pendientes[mint] = rec
                    _anexar(LANZAMIENTOS, rec)
        except Exception as exc:  # noqa: BLE001
            log(f"feed caido ({type(exc).__name__}); reconecto en {espera}s")
            await asyncio.sleep(espera)
            espera = min(espera * 2, 120)


# --------------------------------------------------------------------------- #
# 2. Veredicto: que fue de cada moneda                                        #
# --------------------------------------------------------------------------- #


async def _pumpfun(session: aiohttp.ClientSession, mint: str) -> dict[str, Any] | None:
    try:
        async with session.get(
            f"{PUMPFUN_API}/{mint}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            return await r.json() if r.status == 200 else None
    except Exception:  # noqa: BLE001
        return None


async def _dexscreener(session: aiohttp.ClientSession, mint: str) -> dict[str, Any] | None:
    try:
        async with session.get(
            f"{DEXSCREENER}/{mint}", timeout=aiohttp.ClientTimeout(total=20)
        ) as r:
            if r.status != 200:
                return None
            pares = (await r.json()).get("pairs") or []
            return pares[0] if pares else None
    except Exception:  # noqa: BLE001
        return None


async def juzgar(session: aiohttp.ClientSession, rec: dict[str, Any]) -> dict[str, Any]:
    """Decide como acabo una moneda: acierto, fallo o rug, y si fue organica.

    Se cruzan dos fuentes porque ninguna basta sola: pump.fun sabe del market
    cap y de la curva, DexScreener sabe de compradores distintos, que es lo que
    de verdad distingue el volumen real del que se hacen tres carteras.
    """
    mint = rec["mint"]
    pf, dx = await asyncio.gather(_pumpfun(session, mint), _dexscreener(session, mint))

    mc_ahora = float((pf or {}).get("usd_market_cap") or 0)
    mc_pico = max(mc_ahora, float((pf or {}).get("ath_market_cap") or 0))
    if dx:
        try:
            mc_ahora = max(mc_ahora, float(dx.get("marketCap") or 0))
            mc_pico = max(mc_pico, mc_ahora)
        except (TypeError, ValueError):
            pass

    # Actividad de la primera hora. OJO: DexScreener da NUMERO DE OPERACIONES,
    # no compradores distintos -- no existe forma gratis de contar carteras
    # unicas sin recorrer la cadena. Es un proxy peor, pero sirve: un lanzamiento
    # farmeado hace pocas operaciones muy grandes, y uno organico muchas
    # pequenas. Si algun dia se quiere el dato bueno, hay que sacar los holders
    # de la API de pump.fun por moneda, que es mucho mas lento.
    compras = ventas = 0
    if dx:
        tx = (dx.get("txns") or {}).get("h1") or {}
        compras, ventas = int(tx.get("buys") or 0), int(tx.get("sells") or 0)

    acierto = mc_pico >= OBJETIVO_MC

    # RUG: hizo pico y se lo dejo casi entero. No es volatilidad, es vaciado.
    caida = ((mc_pico - mc_ahora) / mc_pico * 100) if mc_pico > 0 else 0.0
    rug = bool(mc_pico >= OBJETIVO_MC * 0.5 and caida >= RUG_CAIDA_PCT)

    # ORGANICO: gente distinta comprando, no un puñado de operaciones enormes.
    organico = compras >= ORGANIC_MIN_MAKERS and (ventas == 0 or compras / max(ventas, 1) < 8)

    veredicto = {
        **rec,
        "juzgado": time.time(),
        "mc_pico": round(mc_pico),
        "mc_ahora": round(mc_ahora),
        "caida_pct": round(caida, 1),
        "compras_1h": compras,
        "ventas_1h": ventas,
        "acierto": acierto,
        "rug": rug,
        "organico": organico,
    }
    _anexar(VEREDICTOS, veredicto)
    return veredicto


async def evaluar_pendientes(
    session: aiohttp.ClientSession, pendientes: dict[str, dict[str, Any]]
) -> int:
    """Juzga las monedas que ya tienen edad suficiente."""
    ahora = time.time()
    maduras = [r for r in pendientes.values() if ahora - r["visto"] >= EVALUAR_A_MIN * 60]
    n = 0
    for rec in maduras[:40]:  # tanda acotada: la API tiene limites
        await juzgar(session, rec)
        pendientes.pop(rec["mint"], None)
        n += 1
        await asyncio.sleep(0.4)
    return n


# --------------------------------------------------------------------------- #
# 3. Marcador de devs                                                         #
# --------------------------------------------------------------------------- #


def marcador() -> dict[str, dict[str, Any]]:
    """Agrega los veredictos por creador, dentro de la ventana temporal."""
    corte = time.time() - VENTANA_DIAS * 86400
    por_dev: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in _leer(VEREDICTOS):
        if v.get("juzgado", 0) >= corte:
            por_dev[v["creator"]].append(v)

    out: dict[str, dict[str, Any]] = {}
    for dev, vs in por_dev.items():
        n = len(vs)
        aciertos = sum(1 for v in vs if v["acierto"])
        rugs = sum(1 for v in vs if v["rug"])
        organicas = sum(1 for v in vs if v["organico"])
        picos = [v["mc_pico"] for v in vs]
        out[dev] = {
            "lanzamientos": n,
            "aciertos": aciertos,
            "rugs": rugs,
            "organicas": organicas,
            "acierto_pct": round(aciertos / n * 100, 1),
            "rug_pct": round(rugs / n * 100, 1),
            "organico_pct": round(organicas / n * 100, 1),
            "pico_mediano": round(statistics.median(picos)) if picos else 0,
            "mejor_pico": max(picos) if picos else 0,
            "ultimo": max(v["juzgado"] for v in vs),
            "simbolos": [v["symbol"] for v in vs[-4:]],
        }
    return out


def aprobados(m: dict[str, dict[str, Any]], vetados: set[str]) -> list[tuple[str, dict]]:
    """Devs que cumplen las tres condiciones: acierta, no rugea, es organico."""
    ok = []
    for dev, s in m.items():
        if dev in vetados:
            continue
        if s["lanzamientos"] < MIN_LANZAMIENTOS:
            continue
        if s["acierto_pct"] < MIN_ACIERTO:
            continue
        if s["rug_pct"] > MAX_RUGS_PCT:
            continue
        if s["organico_pct"] < 50:
            continue
        ok.append((dev, s))
    ok.sort(key=lambda x: (-x[1]["acierto_pct"], -x[1]["lanzamientos"]))
    return ok


def _vetados() -> set[str]:
    try:
        return set(json.loads(Path("blacklist_devs.json").read_text(encoding="utf-8"))["wallets"])
    except Exception:  # noqa: BLE001
        return set()


# --------------------------------------------------------------------------- #
# 4. Guardian del dinero                                                      #
# --------------------------------------------------------------------------- #


async def _rpc(session: aiohttp.ClientSession, metodo: str, params: list) -> Any:
    async with session.post(
        RPC,
        json={"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params},
        timeout=aiohttp.ClientTimeout(total=25),
    ) as r:
        return (await r.json()).get("result")


def _wallet() -> str | None:
    """Direccion publica del bot, derivada sin tocar la mitad secreta."""
    raw = _env("SOLANA_PRIVATE_KEY")
    if not raw:
        return None
    try:
        from solders.keypair import Keypair

        return str(Keypair.from_base58_string(raw).pubkey())
    except Exception:  # noqa: BLE001
        return None


def _posiciones_abiertas() -> set[str]:
    """Mints que algun bot esta vigilando ahora mismo."""
    mints = set()
    for p in Path(".").glob("open_position*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("mint"):
                mints.add(d["mint"])
        except Exception:  # noqa: BLE001
            continue
    return mints


def _comprados_recientes(minutos: float) -> set[str]:
    """Mints comprados hace poco: aunque no haya posicion, no son huerfanos."""
    corte = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutos)
    mints = set()
    p = Path("trades") / "trades.log"
    if not p.exists():
        return mints
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
        try:
            e = json.loads(line)
            ts = datetime.fromisoformat(str(e.get("timestamp", "")).replace("Z", ""))
            if ts >= corte and e.get("token_address"):
                mints.add(e["token_address"])
        except Exception:  # noqa: BLE001
            continue
    return mints


async def parar_bot(session: aiohttp.ClientSession, motivo: str) -> None:
    """Corta el trading. Parar nunca arruina: lo peor es perderse operaciones."""
    import signal
    import subprocess

    matados = []
    try:
        ps = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True)
        for linea in ps.stdout.splitlines():
            if "bot_runner" in linea or "watchdog.py" in linea:
                pid = int(linea.split()[0])
                if pid != os.getpid():
                    os.kill(pid, signal.SIGTERM)
                    matados.append(pid)
    except Exception as exc:  # noqa: BLE001
        log(f"no pude parar el bot: {exc}")

    await avisar(
        session,
        f"🛑 <b>BOT PARADO</b>\n{motivo}\n"
        f"Procesos terminados: {matados or 'ninguno encontrado'}\n\n"
        f"No se reanuda solo. Cuando quieras volver:\n"
        f"<code>cd /root/Definitivo_bot && nohup ./.venv/bin/python watchdog.py &</code>",
    )


async def revisar_dinero(session: aiohttp.ClientSession, estado: dict) -> list[str]:
    """Suelo de saldo y venta de huerfanos. Devuelve lineas para el informe."""
    lineas: list[str] = []
    addr = _wallet()
    if not addr:
        return ["(sin wallet legible)"]

    bal = (await _rpc(session, "getBalance", [addr]) or {}).get("value", 0) / 1e9
    lineas.append(f"Saldo: <b>{bal:.4f} SOL</b>")

    if bal < SUELO_SOL and not estado.get("parado"):
        estado["parado"] = True
        await parar_bot(session, f"Saldo {bal:.4f} SOL por debajo del suelo de {SUELO_SOL}.")
        lineas.append(f"⛔ suelo de {SUELO_SOL} SOL tocado: bot parado")

    if not VENDER_HUERFANOS:
        return lineas

    # tokens en la wallet que nadie reclama
    protegidos = _posiciones_abiertas() | _comprados_recientes(HUERFANO_MIN)
    res = await _rpc(
        session,
        "getTokenAccountsByOwner",
        [addr, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding": "jsonParsed"}],
    )
    huerfanos = []
    for acc in (res or {}).get("value", []):
        info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        mint = info.get("mint")
        cant = (info.get("tokenAmount") or {}).get("uiAmount") or 0
        if cant > 0 and mint and mint not in protegidos:
            huerfanos.append((mint, float(cant)))

    for mint, cant in huerfanos[:5]:
        valor = await _valor_en_sol(session, mint, cant)
        if valor is None or valor * 150 < HUERFANO_MIN_USD:  # ~150 $/SOL
            continue
        if SECO:
            lineas.append(f"🧹 (seco) venderia {mint[:8]}… por ~{valor:.4f} SOL")
            continue
        ok, sig, err = await _vender(mint, cant)
        if ok:
            lineas.append(f"🧹 huerfano vendido {mint[:8]}… ~{valor:.4f} SOL")
            await avisar(session, f"🧹 Huerfano vendido: <code>{mint}</code> ~{valor:.4f} SOL")
        else:
            lineas.append(f"⚠️ no pude vender {mint[:8]}…: {str(err)[:60]}")
    return lineas


async def _valor_en_sol(session: aiohttp.ClientSession, mint: str, cant: float) -> float | None:
    try:
        url = (
            f"https://lite-api.jup.ag/swap/v1/quote?inputMint={mint}"
            f"&outputMint={SOL_MINT}&amount={int(cant * 1e6)}&slippageBps=3000"
        )
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return None
            return int((await r.json()).get("outAmount", 0)) / 1e9
    except Exception:  # noqa: BLE001
        return None


async def _vender(mint: str, cant: float) -> tuple[bool, str | None, str | None]:
    """Vende por Jupiter usando el mismo vendedor que ya usa el bot."""
    try:
        from solders.keypair import Keypair

        from trading.jupiter_seller import jupiter_sell

        kp = Keypair.from_base58_string(_env("SOLANA_PRIVATE_KEY"))
        return await jupiter_sell(kp, mint, cant, slippage_bps=3000, rpc=RPC)
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)


# --------------------------------------------------------------------------- #
# 5. Informe cada 4 horas                                                     #
# --------------------------------------------------------------------------- #


async def informe(session: aiohttp.ClientSession, estado: dict, pendientes: dict) -> None:
    m = marcador()
    vet = _vetados()
    buenos = aprobados(m, vet)
    conocidos = set(estado.get("conocidos", []))
    nuevos = [(d, s) for d, s in buenos if d not in conocidos]

    total_juzgadas = sum(s["lanzamientos"] for s in m.values())
    total_aciertos = sum(s["aciertos"] for s in m.values())
    total_rugs = sum(s["rugs"] for s in m.values())

    p = [f"📡 <b>INFORME SCOUT</b> · {datetime.now():%d/%m %H:%M}", ""]
    p.append(f"Vigilando {len(m)} devs · {total_juzgadas} monedas juzgadas ({VENTANA_DIAS:.0f}d)")
    if total_juzgadas:
        p.append(
            f"De esas: {total_aciertos} pasaron ${OBJETIVO_MC:,.0f} "
            f"({total_aciertos/total_juzgadas*100:.1f}%) · {total_rugs} rugs"
        )
    p.append(f"En cola sin juzgar: {len(pendientes)}")
    p.append("")

    if nuevos:
        p.append(f"🆕 <b>NUEVOS APROBADOS ({len(nuevos)})</b>")
        for dev, s in nuevos[:8]:
            p.append(
                f"<code>{dev}</code>\n"
                f"   {s['acierto_pct']:.0f}% acierto · {s['lanzamientos']} lanz · "
                f"0 rugs · {s['organico_pct']:.0f}% organicas · pico ~${s['pico_mediano']:,}"
            )
        p.append("")

    if buenos:
        p.append(f"🏆 <b>TOP APROBADOS</b> (acierto ≥{MIN_ACIERTO:.0f}%, sin rugs, organicos)")
        for dev, s in buenos[:10]:
            p.append(
                f"  {dev[:6]}…{dev[-4:]} · {s['acierto_pct']:>3.0f}% · "
                f"{s['lanzamientos']:>2} lanz · ${s['pico_mediano']:,} · {', '.join(s['simbolos'][:2])}"
            )
    else:
        p.append("Todavia ningun dev cumple las tres condiciones. Hace falta mas muestra.")
    p.append("")

    # los que rugean, para vetarlos
    rugueros = sorted(
        [(d, s) for d, s in m.items() if s["rugs"] > 0 and d not in vet],
        key=lambda x: -x[1]["rug_pct"],
    )
    if rugueros:
        p.append(f"☠️ <b>RUGUEAN</b> ({len(rugueros)}) — candidatos a lista negra")
        for dev, s in rugueros[:5]:
            p.append(f"  {dev[:6]}…{dev[-4:]} · {s['rugs']}/{s['lanzamientos']} rugs")
        p.append("")

    p.append("💰 <b>DINERO</b>")
    p.extend("  " + x for x in await revisar_dinero(session, estado))

    estado["conocidos"] = [d for d, _ in buenos]
    STATE_DIR.mkdir(exist_ok=True)
    MARCADOR.write_text(
        json.dumps({"actualizado": time.time(), "devs": m}, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    await avisar(session, "\n".join(p))


# --------------------------------------------------------------------------- #
# Bucle principal                                                             #
# --------------------------------------------------------------------------- #


async def main() -> None:
    STATE_DIR.mkdir(exist_ok=True)
    pendientes: dict[str, dict[str, Any]] = {}
    # recupera lo que quedo sin juzgar de una ejecucion anterior
    juzgados = {v["mint"] for v in _leer(VEREDICTOS)}
    for r in _leer(LANZAMIENTOS)[-5000:]:
        if r["mint"] not in juzgados:
            pendientes[r["mint"]] = r
    log(f"arrancando · {len(pendientes)} monedas pendientes de juzgar")

    estado: dict[str, Any] = {"conocidos": [], "parado": False}
    async with aiohttp.ClientSession() as session:
        await avisar(
            session,
            f"📡 <b>Scout arrancado</b>\n"
            f"Objetivo ${OBJETIVO_MC:,.0f} · informe cada {INFORME_HORAS:.0f}h\n"
            f"Suelo {SUELO_SOL} SOL · vender huerfanos: {'si' if VENDER_HUERFANOS else 'no'}"
            + ("\n⚠️ MODO SECO: no vende de verdad" if SECO else ""),
        )
        asyncio.create_task(escuchar(pendientes))

        prox_informe = time.time() + INFORME_HORAS * 3600
        while True:
            try:
                n = await evaluar_pendientes(session, pendientes)
                if n:
                    log(f"juzgadas {n} monedas · {len(pendientes)} en cola")
                if time.time() >= prox_informe:
                    await informe(session, estado, pendientes)
                    prox_informe = time.time() + INFORME_HORAS * 3600
                else:
                    await revisar_dinero(session, estado)  # el guardian no espera 4h
            except Exception as exc:  # noqa: BLE001 -- el scout no se muere nunca
                log(f"error en el bucle: {type(exc).__name__}: {exc}")
            await asyncio.sleep(120)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("adios")
