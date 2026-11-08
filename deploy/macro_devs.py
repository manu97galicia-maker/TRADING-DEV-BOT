"""MACRO LISTA de devs: todos los de nuestras listas, con lo que se sabe hoy.

Pedido el 7-nov: "haz una macro lista con todos ellos y los que siempre pagan
dex o dextools o ambas, y los mejores segun GMGN".

Para cada dev de todas las listas del repo (unos 800):
  1. GMGN (si hay GMGN_API_KEY en el .env): cuantas monedas ha lanzado,
     cuantas migraron, su porcentaje, cuando lanzo la ultima y su ATH.
  2. Helius (el del bot): si se ha movido en 30 dias y, a los que si, cuantas
     monedas de pump.fun lanzo en 7 y 30 dias y cuales (ver devs_activos.py),
     y su % de migradas de TODO su historial, con el mismo lector con el que
     el bot decide si compra. Se marca quien cumple la regla del bot
     (>= 1 migrada y >= 10%).
  3. DexScreener (gratis, sin clave): de sus ultimas 5 monedas, cuantas tienen
     el perfil PAGADO (web/redes en DexScreener) y cuantas tienen boost.
  4. DEXTools (si hay DEXTOOLS_API_KEY): de sus ultimas 3 monedas, cuantas
     tienen la ficha con redes en DEXTools. Es una SENAL de pago, no el
     recibo: la API de DEXTools no dice quien pago.
"Siempre paga" = todas sus monedas medidas (minimo 2) lo cumplen.

No compra, no vende, no toca el bot ni su configuracion. Las claves van en
cabeceras y nunca se escriben. Escribe /root/macro_devs.json (fuera del repo).

    cd /root/ai-bot && PYTHONPATH=src .venv/bin/python deploy/macro_devs.py
    ... deploy/macro_devs.py 30     # solo los 30 primeros (prueba)

Tarda unos 15-20 minutos con todos: va despacio a proposito para no quitarle
Helius al bot ni pasarse de los limites de GMGN, DexScreener y DEXTools.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import devs_activos as da

SALIDA = Path("/root/macro_devs.json")
GMGN_URL = "https://openapi.gmgn.ai/v1/user/created_tokens"
DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"
DEXT_URL = "https://public-api.dextools.io/{tier}/v2/token/solana/{mint}"
#: Lo que exige el bot para comprar (bots/bot-paper-dev1-bloque2.yaml,
#: pumpfun_dev): al menos 2 migradas y el 10% (8-nov; antes 1 migrada).
MIN_MIGRADAS = 2
MIN_PCT = 10.0
#: Tiempo maximo para leer el historial entero de un dev.
ESPERA_HISTORIAL = 40.0
MUESTRA_DEX = 5
MUESTRA_DEXT = 3
MIN_MUESTRA = 2


def _get(url: str, cabeceras: dict | None = None, reintentos: int = 2) -> object:
    """GET que devuelve JSON o None. Nunca lanza ni escribe la URL."""
    peticion = urllib.request.Request(  # noqa: S310 -- URLs fijas de arriba
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                      **(cabeceras or {})},
    )
    for intento in range(reintentos + 1):
        try:
            with urllib.request.urlopen(peticion, timeout=20) as r:  # noqa: S310
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and intento < reintentos:  # noqa: PLR2004
                time.sleep(5 * (intento + 1))
                continue
            return None
        except Exception:  # noqa: BLE001
            return None
    return None


# --- GMGN ------------------------------------------------------------------


def leer_gmgn(data: object) -> dict | None:
    """Lo util de la respuesta de GMGN para un creador."""
    if not isinstance(data, dict):
        return None
    try:
        dentro = int(data.get("inner_count") or 0)
        migradas = int(data.get("open_count") or 0)
    except (TypeError, ValueError):
        return None
    total = dentro + migradas
    out: dict = {
        "gmgn_lanzadas": total,
        "gmgn_migradas": migradas,
        "gmgn_pct_migradas": round(100 * migradas / total, 1) if total else 0.0,
    }
    ultima = data.get("last_create_timestamp")
    if isinstance(ultima, int | float | str) and str(ultima).isdigit():
        out["gmgn_ultima_lanzada_hace_dias"] = round(
            (time.time() - int(ultima)) / da.DIA, 1
        )
    ath = data.get("creator_ath_info")
    if isinstance(ath, dict):
        for k, v in ath.items():
            if re.search(r"ath|market|mc|cap", k, re.IGNORECASE):
                try:
                    out[f"gmgn_{k}"] = float(v)
                except (TypeError, ValueError):
                    continue
    return out


def gmgn(wallet: str, clave: str) -> dict | None:
    params = urllib.parse.urlencode({
        "chain": "sol", "wallet_address": wallet,
        "timestamp": str(int(time.time())), "client_id": str(uuid.uuid4()),
    })
    datos = _get(f"{GMGN_URL}?{params}", {"X-APIKEY": clave})
    if not isinstance(datos, dict) or str(datos.get("code")) != "0":
        return None
    return leer_gmgn(datos.get("data"))


# --- % de migradas, con el mismo lector que usa el bot ----------------------


def historial_con_helius(carteras: list[str], url: str) -> dict[str, dict]:
    """(lanzadas, migradas, %) de cada dev, leidos de la cadena con Helius.

    Es EXACTAMENTE el lector del bot (monitoring/pumpfun_dev.py,
    `_historial_tolerante`): cuenta tambien las monedas minteadas a traves de
    una plataforma, y lo ilegible cuenta como lanzada y no migrada. Asi el %
    que sale aqui es el mismo con el que el bot decide si compra.
    """
    import asyncio  # noqa: PLC0415

    import aiohttp  # noqa: PLC0415

    from monitoring import pumpfun_dev  # noqa: PLC0415

    fuera: dict[str, dict] = {}

    async def todo() -> None:
        async with aiohttp.ClientSession() as sesion:

            async def post(cuerpo: dict) -> dict | None:
                try:
                    async with sesion.post(url, json=cuerpo, timeout=30) as r:
                        return await r.json(content_type=None)
                except Exception:  # noqa: BLE001
                    return None

            pumpfun_dev.usar_rpc_de_respaldo(post)
            for n, w in enumerate(carteras, 1):
                try:
                    lanz, migr = await asyncio.wait_for(
                        pumpfun_dev._historial_tolerante(w),  # noqa: SLF001
                        timeout=ESPERA_HISTORIAL,
                    )
                    fuera[w] = {
                        "lanzadas_total": lanz,
                        "migradas_total": migr,
                        "pct_migradas": round(100 * migr / lanz, 1) if lanz else 0.0,
                    }
                except Exception as e:  # noqa: BLE001
                    texto = str(e) if type(e).__name__ in {
                        "HistoryUnavailableError", "RuntimeError"} else ""
                    fuera[w] = {"historial_error": da._tapa(  # noqa: SLF001
                        f"{type(e).__name__}: {texto}")[:100]}
                if n % 25 == 0:
                    print(f"  {n}/{len(carteras)}")
                await asyncio.sleep(da.PAUSA)

    asyncio.run(todo())
    return fuera


def cumple_la_regla(h: dict | None) -> bool:
    """Lo que el bot pide para comprar: >= 1 migrada y >= 10%."""
    return bool(
        h
        and (h.get("migradas_total") or 0) >= MIN_MIGRADAS
        and (h.get("pct_migradas") or 0) >= MIN_PCT
    )


# --- DexScreener -----------------------------------------------------------


def leer_dex(datos: object) -> dict[str, dict]:
    """Por moneda: si tiene el perfil pagado en DexScreener y si tiene boost.

    `info` con web o redes solo aparece cuando alguien ha PAGADO el perfil
    ampliado (mismo criterio que medir_devs.py). Se queda el par con mas
    liquidez de cada moneda.
    """
    fuera: dict[str, dict] = {}
    for par in (datos or {}).get("pairs") or [] if isinstance(datos, dict) else []:
        mint = (par.get("baseToken") or {}).get("address")
        if not mint:
            continue
        liq = float((par.get("liquidity") or {}).get("usd") or 0)
        if mint in fuera and fuera[mint]["_liq"] >= liq:
            continue
        info = par.get("info") or {}
        fuera[mint] = {
            "_liq": liq,
            "pago_dex": bool(info.get("websites") or info.get("socials")),
            "boost": int((par.get("boosts") or {}).get("active") or 0) > 0,
        }
    return fuera


def resumen_pago(marcas: list[bool]) -> dict:
    """Cuantas de las monedas medidas cumplen, y si son TODAS."""
    n = len(marcas)
    si = sum(marcas)
    return {"medidas": n, "con_pago": si,
            "siempre": n >= MIN_MUESTRA and si == n}


# --- DEXTools --------------------------------------------------------------


def leer_dextools(datos: object) -> bool | None:
    """True si la ficha del token en DEXTools tiene web o redes."""
    if not isinstance(datos, dict):
        return None
    d = datos.get("data") if isinstance(datos.get("data"), dict) else datos
    social = d.get("socialInfo") or d.get("social_info") or {}
    if not isinstance(social, dict):
        return None
    return any(isinstance(v, str) and v.strip() for v in social.values())


# --- todo junto ------------------------------------------------------------


def main() -> None:  # noqa: C901, PLR0912, PLR0915
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(da.RAIZ / ".env")
    rpc = os.environ.get("SOLANA_NODE_RPC_ENDPOINT", "")
    clave_gmgn = os.environ.get("GMGN_API_KEY", "").strip()
    clave_dext = os.environ.get("DEXTOOLS_API_KEY", "").strip()
    tier = (os.environ.get("DEXTOOLS_API_TIER") or "trial").strip()
    devs = da.cargar_devs()
    carteras = list(devs)
    if len(sys.argv) > 1:
        carteras = carteras[: int(sys.argv[1])]
    ahora = time.time()
    print(f"{len(carteras)} devs. GMGN: {'SI' if clave_gmgn else 'NO (sin GMGN_API_KEY)'}"
          f" | DEXTools: {'SI' if clave_dext else 'NO (sin DEXTOOLS_API_KEY)'}"
          f" | Helius: {'SI' if rpc else 'NO'}")

    if clave_gmgn:
        print("1/4 GMGN...")
        for n, w in enumerate(carteras, 1):
            devs[w]["gmgn"] = gmgn(w, clave_gmgn)
            if n % 100 == 0:
                print(f"  {n}/{len(carteras)}")
            time.sleep(0.45)

    if rpc:
        print("2/4 Helius: quien se mueve y que lanza...")
        for n, w in enumerate(carteras, 1):
            try:
                firmas = da._rpc(rpc, "getSignaturesForAddress", [w, {"limit": 1}]) or []  # noqa: SLF001
                bt = firmas[0].get("blockTime") if firmas else None
                devs[w]["ultima_tx_hace_dias"] = round((ahora - bt) / da.DIA, 1) if bt else None
            except Exception:  # noqa: BLE001
                devs[w]["ultima_tx_hace_dias"] = None
            if n % 100 == 0:
                print(f"  {n}/{len(carteras)}")
            time.sleep(da.PAUSA)
        config = {
            "transactionDetails": "full", "limit": 100, "sortOrder": "desc",
            "encoding": "json", "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
            "filters": {"status": "succeeded", "tokenAccounts": "none"},
        }
        vivos = [w for w in carteras
                 if (devs[w].get("ultima_tx_hace_dias") or 999) <= 30]  # noqa: PLR2004
        print(f"   {len(vivos)} se movieron en 30 dias")
        for w in vivos:
            try:
                pagina = da._rpc(rpc, "getTransactionsForAddress", [w, config]) or {}  # noqa: SLF001
                txs = pagina.get("data") if isinstance(pagina, dict) else None
                devs[w]["lanz"] = da.lanzamientos(txs or [], w, ahora)
            except Exception:  # noqa: BLE001
                devs[w]["lanz"] = None
            time.sleep(da.PAUSA)

        print(f"2b) % de migradas de los {len(vivos)} que se mueven (como el bot)...")
        for w, h in historial_con_helius(vivos, rpc).items():
            devs[w]["historial"] = h

    print("3/4 DexScreener: quien paga perfil y boost...")
    pendientes = {w: (devs[w].get("lanz") or {}).get("mints_recientes", [])[:MUESTRA_DEX]
                  for w in carteras}
    todos = [m for ms in pendientes.values() for m in ms]
    dex: dict[str, dict] = {}
    for i in range(0, len(todos), 30):
        dex.update(leer_dex(_get(DEX_URL + ",".join(todos[i : i + 30]))))
        time.sleep(0.4)

    dext: dict[str, bool | None] = {}
    if clave_dext:
        print("4/4 DEXTools: quien tiene la ficha con redes...")
        for ms in pendientes.values():
            for m in ms[:MUESTRA_DEXT]:
                dext[m] = leer_dextools(_get(DEXT_URL.format(tier=tier, mint=m),
                                             {"X-API-KEY": clave_dext}))
                time.sleep(1.1)

    filas = []
    for w in carteras:
        d = devs[w]
        lanz = d.get("lanz") or {}
        ms = pendientes.get(w) or []
        p_dex = resumen_pago([dex[m]["pago_dex"] for m in ms if m in dex])
        p_boost = resumen_pago([dex[m]["boost"] for m in ms if m in dex])
        p_dext = resumen_pago([v for m in ms[:MUESTRA_DEXT] if (v := dext.get(m)) is not None])
        filas.append({
            "wallet": w,
            "estado": da.estado(d.get("ultima_tx_hace_dias"), lanz or None),
            "lanzadas_7d": lanz.get("lanzadas_7d"),
            "lanzadas_30d": lanz.get("lanzadas_30d"),
            **(d.get("historial") or {}),
            "cumple_regla_del_bot": cumple_la_regla(d.get("historial")),
            **(d.get("gmgn") or {}),
            "mediana_pico_de_las_listas": d.get("mediana_pico"),
            "mejor_pico_de_las_listas": d.get("mejor_pico"),
            "dex_perfil_pagado": p_dex,
            "dex_boost": p_boost,
            "dextools_con_redes": p_dext,
            "siempre_paga_dex": p_dex["siempre"],
            "siempre_dextools": p_dext["siempre"],
            "siempre_ambos": p_dex["siempre"] and p_dext["siempre"],
            "en_blacklist": d.get("en_blacklist", False),
            "listas": d["listas"],
        })
    SALIDA.write_text(json.dumps({
        "generado": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ahora)),
        "que_es": __doc__.split("\n\n")[1],
        "devs": filas,
    }, indent=1, ensure_ascii=False), encoding="utf-8")

    def corto(w: str) -> str:
        return f"{w[:6]}...{w[-4:]}"

    activos = [f for f in filas if f["estado"].lower().startswith("lanza")]
    print("\n== ACTIVOS")
    for k in ("LANZA (7 dias)", "lanza (30 dias)", "se mueve, sin lanzar", "PARADO", "sin datos"):
        print(f"{sum(1 for f in filas if f['estado'] == k):6}  {k}")
    regla = [f for f in filas if f["cumple_regla_del_bot"] and not f["en_blacklist"]]
    regla.sort(key=lambda f: (-(f.get("pct_migradas") or 0), -(f.get("migradas_total") or 0)))
    print(f"\n== ACTIVOS QUE CUMPLEN LA REGLA DEL BOT (>= {MIN_MIGRADAS} migrada y "
          f">= {MIN_PCT:.0f}%): {len(regla)}")
    for f in regla[:30]:
        print(f"{f['wallet']}  {f['migradas_total']}/{f['lanzadas_total']} "
              f"({f['pct_migradas']:.0f}%)  {f['estado']}  "
              f"dex {f['dex_perfil_pagado']['con_pago']}/{f['dex_perfil_pagado']['medidas']}")
    if clave_gmgn:
        print("\n== MEJORES SEGUN GMGN (3+ lanzadas, por % migradas)")
        buenos = [f for f in filas if (f.get("gmgn_lanzadas") or 0) >= 3 and not f["en_blacklist"]]  # noqa: PLR2004
        buenos.sort(key=lambda f: (-f["gmgn_pct_migradas"], -f["gmgn_migradas"]))
        for f in buenos[:25]:
            print(f"{f['wallet']}  {f['gmgn_migradas']}/{f['gmgn_lanzadas']} "
                  f"({f['gmgn_pct_migradas']:.0f}%)  {f['estado']}")
    for titulo, clave in (("SIEMPRE PAGAN DEX", "siempre_paga_dex"),
                          ("SIEMPRE DEXTOOLS", "siempre_dextools"),
                          ("SIEMPRE LAS DOS", "siempre_ambos")):
        lista = [f for f in filas if f[clave]]
        print(f"\n== {titulo}: {len(lista)}")
        for f in lista[:15]:
            print(f"{corto(f['wallet'])}  {f['estado']}  "
                  f"dex {f['dex_perfil_pagado']['con_pago']}/{f['dex_perfil_pagado']['medidas']}")
    print(f"\n{len(activos)} devs activos. Todo en {SALIDA}")


if __name__ == "__main__":
    main()
