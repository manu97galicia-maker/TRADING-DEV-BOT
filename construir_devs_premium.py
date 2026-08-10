"""Saca la lista PREMIUM: devs cuyas monedas corren, y que no son granjas.

La fuente es `top_devs_organico.json`, que estaba en una copia vieja del bot
(`~/pumpfun-bonkfun-bot`) y no se habia traido a este repo. Su criterio ya es
el bueno: dev con >=2 lanzamientos FUERTES por semana, donde fuerte significa
>=10 compradores en 2 min, pico >=$18K y que CONSERVA >=60% del precio a los
2-4 min. Ese ultimo trozo es la clave: mide que el dev NO dumpea.

Aqui se le aplican encima los dos filtros que pidio Manu:

  1. NO SPAM: como mucho dos lanzamientos al dia (<=14 en 7 dias). La lista
     original incluye granjas de 1.637 monedas en una semana; esas hacen ruido,
     no monedas.
  2. QUE CORRAN: mediana de pico >= $8.000. Se usa la MEDIANA y no el maximo
     porque un solo pelotazo no dice nada de la siguiente moneda.

Y se descarta a cualquiera que este en las listas negras del repo: el dev
`5FqUo9aBjsp7...` es el numero 2 de la lista organica y a la vez acumula 3 rugs
en produccion. Entre lo que dice un analisis de hace dias y lo que ha pasado de
verdad con dinero, manda lo segundo.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

ORIGEN = Path.home() / "pumpfun-bonkfun-bot" / "top_devs_organico.json"
DESTINO = Path(__file__).parent / "top_devs_premium.json"

MAX_LANZ_7D = 14      # dos al dia como mucho
MIN_PICO = 8000       # mediana de pico en dolares


def cargar_vetados() -> set[str]:
    """Devs vetados a mano o auto-baneados por rugear en produccion."""
    vetados: set[str] = set()
    aqui = Path(__file__).parent
    for nombre in aqui.glob("blacklist_devs*.json"):
        try:
            d = json.loads(nombre.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        vetados |= set(d.get("wallets", []) if isinstance(d, dict) else d)
    for nombre in aqui.glob("blocked_devs_*.json"):
        try:
            d = json.loads(nombre.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict):
            vetados |= set(d.keys())
    return {str(v) for v in vetados if isinstance(v, str)}


def main() -> None:
    if not ORIGEN.is_file():
        print(f"No encuentro {ORIGEN}")
        return
    origen = json.loads(ORIGEN.read_text(encoding="utf-8"))
    devs = origen.get("wallets", [])
    vetados = cargar_vetados()

    sel = []
    fuera_spam = fuera_pico = fuera_veto = 0
    for d in devs:
        if d.get("wallet") in vetados:
            fuera_veto += 1
            continue
        if (d.get("launches_7d") or 0) > MAX_LANZ_7D:
            fuera_spam += 1
            continue
        if (d.get("median_peak") or 0) < MIN_PICO:
            fuera_pico += 1
            continue
        sel.append(d)

    sel.sort(key=lambda d: -(d.get("median_peak") or 0))

    print(f"{len(devs)} devs en la lista organica")
    print(f"  -{fuera_veto} vetados o auto-baneados por rugear")
    print(f"  -{fuera_spam} spam (mas de {MAX_LANZ_7D} lanzamientos en 7 dias)")
    print(f"  -{fuera_pico} con mediana de pico por debajo de ${MIN_PICO:,}")
    print(f"  = {len(sel)} PREMIUM\n")

    print("%-46s %7s %8s %11s %9s" % (
        "dev", "lanz7d", "al dia", "pico medio", "vol SOL"))
    print("-" * 88)
    for d in sel:
        print("%-46s %7d %8.1f %11s %9.1f" % (
            d["wallet"], d["launches_7d"], d["launches_7d"] / 7,
            "$%s" % format(d["median_peak"], ","), d.get("median_vol_sol", 0)))
    print("-" * 88)

    if sel:
        al_dia = sum(d["launches_7d"] for d in sel) / 7
        pico = statistics.median(d["median_peak"] for d in sel)
        print("%d devs -> %.1f lanzamientos/dia   ·   pico mediano $%s"
              % (len(sel), al_dia, format(round(pico), ",")))

        DESTINO.write_text(json.dumps({
            "generado_de": str(ORIGEN.name),
            "criterio": (f"lanzamientos FUERTES organicos, <= {MAX_LANZ_7D} "
                         f"lanzamientos/7d y mediana de pico >= ${MIN_PICO:,}"),
            "lanzamientos_dia": round(al_dia, 1),
            "pico_mediano": round(pico),
            "wallets": [d["wallet"] for d in sel],
            "detalle": sel,
        }, indent=1), encoding="utf-8")
        print(f"\nguardado en {DESTINO.name}")


if __name__ == "__main__":
    main()
