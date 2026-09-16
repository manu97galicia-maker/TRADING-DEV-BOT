"""Puntua a cada dev por lo que importa: que no hunda, que pague, que siga vivo.

QUE LE FALTA A LAS LISTAS QUE YA HABIA. Las seis listas sueltas del repo se
hicieron en julio y agosto y ordenan por pico o por numero de lanzamientos.
Ninguna mide si el dev HUNDE sus monedas, si suele pagar DexScreener, si les
pone redes, ni --lo mas importante-- si sigue lanzando HOY. Un dev bueno en
julio puede llevar dos meses parado, y comprarle seria comprarle a un fantasma.

LO QUE SE MIDE, Y POR QUE ESE Y NO OTRO
---------------------------------------
* MIGRACION (peso alto). De todas sus monedas, cuantas llegaron a migrar.
  Es el mejor sustituto que hay de "no hace rug", y la razon es que NO SE
  PUEDE FALSEAR: migrar exige que entren ~85 SOL de verdad en la curva. El
  campo `ath_market_cap` de pump.fun, en cambio, se infla solo -- hay
  creadores ensenando 89 millones de maximo con 1.387 $ de liquidez. Por eso
  aqui no se ordena por picos.
* ACTIVIDAD RECIENTE. Lanzamientos en los ultimos 7 y 30 dias. Un dev sin
  lanzamientos en 30 dias no es un dev bueno: es un dev que no esta.
* PAGA DEXSCREENER / BOOSTS / REDES. Se mira en DexScreener sobre una muestra
  de sus monedas recientes. Es lo que pediste: los que suelen pagar dex y
  boost, y les ponen redes a las dos.
* MUERTAS. Cuantas de sus monedas recientes estan hoy practicamente a cero.
  Es el reverso de la migracion y el sintoma directo del pump and dump.

    uv run deploy/medir_devs.py                 # mide devs_unificados.json
    uv run deploy/medir_devs.py 20              # solo los 20 primeros (prueba)

Tarda: son dos llamadas por dev mas una por cada tanda de monedas. Con 180
devs son unos minutos. Escribe devs_medidos.json y no borra nada.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

ENTRADA = Path("devs_unificados.json")
SALIDA = Path("devs_medidos.json")

API_PUMP = "https://frontend-api-v3.pump.fun/coins"
API_DEX = "https://api.dexscreener.com/latest/dex/tokens"
CABECERAS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

#: Monedas recientes que se miran por dev en DexScreener. Mas no cambia el
#: resultado y multiplica el tiempo.
MUESTRA = 20
#: DexScreener acepta varias direcciones por llamada.
POR_TANDA = 25
#: Pausa entre llamadas. Ni pump.fun ni DexScreener publican su limite, asi
#: que se va despacio a proposito: que tarde cinco minutos mas da igual,
#: que te corten a mitad no.
PAUSA = 0.35
DIA = 86400.0
#: Por debajo de esto una moneda esta muerta, en dolares de market cap.
MUERTA_USD = 2000.0
#: Un dev sin lanzamientos en este plazo no se considera activo.
DIAS_ACTIVO = 30


async def _json(
    sesion: aiohttp.ClientSession, url: str, params: dict | None = None
) -> object:
    try:
        async with sesion.get(url, params=params, timeout=20) as r:
            if r.status != 200:  # noqa: PLR2004
                return None
            return await r.json(content_type=None)
    except Exception:  # noqa: BLE001
        return None


async def _monedas(sesion: aiohttp.ClientSession, wallet: str) -> list[dict]:
    """Todas las monedas de un creador, de la API de pump.fun."""
    datos = await _json(sesion, API_PUMP, {"creator": wallet, "limit": 200})
    return datos if isinstance(datos, list) else []


async def _dex(sesion: aiohttp.ClientSession, mints: list[str]) -> dict[str, dict]:
    """Estado en DexScreener de una tanda de monedas."""
    fuera: dict[str, dict] = {}
    for i in range(0, len(mints), POR_TANDA):
        tanda = mints[i : i + POR_TANDA]
        datos = await _json(sesion, f"{API_DEX}/{','.join(tanda)}")
        for par in (datos or {}).get("pairs") or []:
            mint = (par.get("baseToken") or {}).get("address")
            if not mint:
                continue
            # Se queda el par con mas liquidez de cada moneda.
            previo = fuera.get(mint)
            liq = float((par.get("liquidity") or {}).get("usd") or 0)
            if previo is None or liq > previo["_liq"]:
                info = par.get("info") or {}
                fuera[mint] = {
                    "_liq": liq,
                    "mc": float(par.get("marketCap") or 0),
                    # `info` con websites/socials solo aparece cuando alguien
                    # ha PAGADO el perfil ampliado. Es la senal de "dex pagado".
                    "pago_dex": bool(info.get("websites") or info.get("socials")),
                    "redes": len(info.get("socials") or []),
                    "boosts": int((par.get("boosts") or {}).get("active") or 0),
                }
        await asyncio.sleep(PAUSA)
    return fuera


def _puntuar(m: dict) -> float:
    """Una nota de 0 a 100. El peso esta en no hundir y en seguir vivo.

    No es una formula magica: es un ORDEN para mirar primero a los que mas
    cumplen lo pedido. Los numeros de cada dev van al lado para poder
    discutirla.
    """
    if not m["activo"]:
        return 0.0
    nota = 0.0
    nota += 40 * min(1.0, m["tasa_migracion"] / 0.30)   # no hunde
    nota += 20 * min(1.0, m["pct_pago_dex"] / 0.50)     # suele pagar dex
    nota += 10 * min(1.0, m["pct_boost"] / 0.30)        # y boostea
    nota += 10 * min(1.0, m["pct_redes"] / 0.70)        # con redes
    nota += 10 * min(1.0, m["lanz_30d"] / 10.0)         # lanza a menudo
    nota += 10 * (1.0 - min(1.0, m["pct_muertas"]))     # no deja cadaveres
    return round(nota, 1)


async def _medir_uno(
    sesion: aiohttp.ClientSession, dev: dict, ahora: float
) -> dict | None:
    wallet = dev["wallet"]
    monedas = await _monedas(sesion, wallet)
    await asyncio.sleep(PAUSA)
    if not monedas:
        return {"wallet": wallet, "lanzamientos": 0, "nota": 0.0,
                "activo": False, "nota_texto": "sin datos en la API de pump.fun"}

    def _cuando(c: dict) -> float:
        return float(c.get("created_timestamp") or 0) / 1000.0

    migradas = sum(1 for c in monedas if c.get("complete"))
    lanz_7d = sum(1 for c in monedas if ahora - _cuando(c) <= 7 * DIA)
    lanz_30d = sum(1 for c in monedas if ahora - _cuando(c) <= DIAS_ACTIVO * DIA)
    ultima = max((_cuando(c) for c in monedas), default=0.0)

    recientes = sorted(monedas, key=_cuando, reverse=True)[:MUESTRA]
    mints = [str(c.get("mint")) for c in recientes if c.get("mint")]
    estado = await _dex(sesion, mints)
    vistas = [estado[m] for m in mints if m in estado]

    n = len(vistas) or 1
    m = {
        "wallet": wallet,
        "listas": dev.get("listas", []),
        "lanzamientos": len(monedas),
        "migradas": migradas,
        "tasa_migracion": migradas / len(monedas),
        "lanz_7d": lanz_7d,
        "lanz_30d": lanz_30d,
        "dias_desde_ultima": round((ahora - ultima) / DIA, 1) if ultima else 999.0,
        "activo": lanz_30d > 0,
        "muestra_en_dex": len(vistas),
        "pct_pago_dex": sum(1 for v in vistas if v["pago_dex"]) / n,
        "pct_boost": sum(1 for v in vistas if v["boosts"] > 0) / n,
        "pct_redes": sum(1 for v in vistas if v["redes"] > 0) / n,
        "pct_muertas": sum(1 for v in vistas if v["mc"] < MUERTA_USD) / n,
    }
    m["nota"] = _puntuar(m)
    return m


async def _todo(devs: list[dict]) -> list[dict]:
    ahora = time.time()
    fuera = []
    async with aiohttp.ClientSession(headers=CABECERAS) as sesion:
        for i, dev in enumerate(devs, 1):
            medido = await _medir_uno(sesion, dev, ahora)
            if medido:
                fuera.append(medido)
            if i % 10 == 0 or i == len(devs):
                print(f"  {i}/{len(devs)} devs medidos", flush=True)
    return fuera


def main() -> None:
    """Mide cada dev de devs_unificados.json y los ordena por nota."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not ENTRADA.exists():
        print(f"Falta {ENTRADA}. Primero: uv run deploy/unir_listas_devs.py")
        return
    devs = json.loads(ENTRADA.read_text(encoding="utf-8")).get("wallets") or []
    tope = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if tope:
        devs = devs[:tope]
    print(f"Midiendo {len(devs)} devs. Esto tarda unos minutos.\n")

    medidos = asyncio.run(_todo(devs))
    medidos.sort(key=lambda d: d.get("nota", 0), reverse=True)

    activos = [d for d in medidos if d.get("activo")]
    print(f"\n{'=' * 78}")
    print(f"{len(medidos)} medidos · {len(activos)} siguen lanzando "
          f"(ultimos {DIAS_ACTIVO} dias)")
    print(f"{'=' * 78}")
    print(f"{'nota':>5} {'wallet':<46}{'migr':>6}{'lanz30':>7}{'dex':>6}{'muert':>7}")
    print("-" * 78)
    for d in activos[:30]:
        print(f"{d['nota']:>5.1f} {d['wallet']:<46}"
              f"{d['tasa_migracion'] * 100:>5.0f}%{d['lanz_30d']:>7}"
              f"{d['pct_pago_dex'] * 100:>5.0f}%{d['pct_muertas'] * 100:>6.0f}%")

    parados = len(medidos) - len(activos)
    if parados:
        print(f"\n{parados} devs SIN lanzamientos en {DIAS_ACTIVO} dias: nota 0.")
        print("Son la mayor trampa de las listas viejas -- buenos en julio, hoy no estan.")

    SALIDA.write_text(json.dumps({
        "_README": (
            "Devs medidos contra pump.fun y DexScreener. `nota` pesa: no "
            "hundir (migracion 40), pagar dex (20), boost (10), redes (10), "
            "lanzar a menudo (10), no dejar cadaveres (10). Un dev sin "
            "lanzamientos en 30 dias saca 0 aunque fuera bueno antes."
        ),
        "_medido": time.strftime("%Y-%m-%d %H:%M"),
        "_count": len(medidos),
        "wallets": medidos,
    }, indent=2), encoding="utf-8")
    print(f"\nEscrito en {SALIDA}")


if __name__ == "__main__":
    main()
