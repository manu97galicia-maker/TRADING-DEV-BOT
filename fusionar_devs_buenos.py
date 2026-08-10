"""Junta las dos listas de devs buenos en la que usa PREMIUM.

Son dos formas distintas de medir lo mismo y por eso se suman en vez de elegir:

  top_devs_premium.json  (de Dune, 27-jul) — mide COMPORTAMIENTO: dev que en sus
      lanzamientos fuertes CONSERVA >=60% del precio a los 2-4 min, o sea que no
      dumpea encima de quien le compra. 15 devs, pico mediano $19.392.

  top_devs_200k.json  (medido hoy) — mide RESULTADO: dev con monedas que han
      pasado de $200K de ATH, entre 0,5 y 2,5 lanzamientos al dia y sin dias de
      rafaga. 17 devs.

Un dev puede no dumpear y no haber hecho nunca un $200K, y al reves. Las dos
señales valen y la union cubre mas lanzamientos al dia, que es el cuello de
botella real: con 15 devs solo habia 13,7 oportunidades diarias.

Se descarta a los que esten en las listas negras del repo, igual que en
construir_devs_premium.py: lo que ha pasado con dinero real manda sobre
cualquier analisis.
"""

from __future__ import annotations

import json
from pathlib import Path

AQUI = Path(__file__).parent
FUENTES = ["top_devs_premium.json", "top_devs_200k.json"]
DESTINO = AQUI / "top_devs_buenos.json"


def vetados() -> set[str]:
    fuera: set[str] = set()
    for f in AQUI.glob("blacklist_devs*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        fuera |= set(d.get("wallets", []) if isinstance(d, dict) else d)
    for f in AQUI.glob("blocked_devs_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict):
            fuera |= set(d.keys())
    return {x for x in fuera if isinstance(x, str)}


def main() -> None:
    fuera = vetados()
    union: dict[str, list[str]] = {}
    for nombre in FUENTES:
        p = AQUI / nombre
        if not p.is_file():
            print(f"  aviso: no encuentro {nombre}, sigo sin el")
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        for w in d.get("wallets", []):
            union.setdefault(w, []).append(nombre.replace("top_devs_", "").replace(".json", ""))
        print(f"  {nombre}: {len(d.get('wallets', []))} devs")

    limpias = {w: v for w, v in union.items() if w not in fuera}
    quitados = len(union) - len(limpias)
    en_ambas = [w for w, v in limpias.items() if len(v) > 1]

    print()
    print(f"union            : {len(union)} devs")
    print(f"  -{quitados} vetados o auto-baneados por rugear")
    print(f"  = {len(limpias)} en la lista final")
    print(f"  ({len(en_ambas)} aparecen en las DOS listas: son los mas fiables)")
    for w in en_ambas:
        print(f"     {w}")

    DESTINO.write_text(json.dumps({
        "criterio": ("union de top_devs_premium (no dumpean, medido a los 2-4 min) "
                     "y top_devs_200k (han pasado de $200K sin ser granjas), "
                     "menos los vetados y auto-baneados"),
        "en_ambas_listas": en_ambas,
        "wallets": sorted(limpias),
        "origen": {w: v for w, v in sorted(limpias.items())},
    }, indent=1), encoding="utf-8")
    print(f"\nguardado en {DESTINO.name}")


if __name__ == "__main__":
    main()
