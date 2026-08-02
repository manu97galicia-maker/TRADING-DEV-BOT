"""La pestaña Discover de GMGN, en consola y con filtros tuyos.

Para que sirve
--------------
Las listas de devs (`top_devs_*.json`) se calcularon una vez con Dune y
envejecen en dias: el 1-ago los 93 devs de mayhem y los 121 de next-big no
lanzaron NI UNA moneda en dos horas de medicion. Esto no usa listas: pregunta
a GMGN que hay AHORA y filtra por lo que de verdad distingue una moneda de una
granja.

Los datos que trae GMGN y la API de pump.fun no
-----------------------------------------------
  creator_created_count   cuantas monedas ha creado ese dev (una granja hace
                          miles; el 1-ago salio una con 6.212)
  bundler_mhr             % de operaciones de bots de bundle
  rug_ratio               riesgo de rug estimado (0-1)
  smart_degen_count       carteras "smart money" que ya han entrado
  holder_count            titulares reales
  top_10_holder_rate      concentracion del top 10
  volume_24h / swaps      volumen y operaciones de verdad, en dolares

Uso
---
    python descubrir.py                      # filtros por defecto
    python descubrir.py --max-mc 3700 --min-vol 5000
    python descubrir.py --preset smart-money
    python descubrir.py --json               # para encadenar con otra cosa
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

BIN = shutil.which("gmgn-cli") or shutil.which("gmgn-cli.cmd")

#: Un dev que ha creado mas de esto es una granja de spam, no un creador. El
#: corte es generoso a proposito: hay creadores legitimos prolificos, pero
#: nadie honesto llega a mil.
MAX_MONEDAS_DEL_DEV = 1000


def pedir(args: argparse.Namespace) -> dict:
    cmd = [
        BIN, "market", "trenches", "--chain", "sol",
        "--type", args.tipo, "--limit", str(args.limite), "--raw",
    ]
    if args.max_mc:
        cmd += ["--max-marketcap", str(args.max_mc)]
    if args.min_mc:
        cmd += ["--min-marketcap", str(args.min_mc)]
    if args.min_vol:
        cmd += ["--min-volume-24h", str(args.min_vol)]
    if args.max_edad:
        cmd += ["--max-created", args.max_edad]
    if args.max_rug is not None:
        cmd += ["--max-rug-ratio", str(args.max_rug)]
    if args.min_holders:
        cmd += ["--min-holder-count", str(args.min_holders)]
    if args.preset:
        cmd += ["--filter-preset", args.preset]
    if args.orden:
        cmd += ["--sort-by", args.orden]
    # encoding explicito: los nombres de las memecoins llevan emojis y kanji, y
    # en Windows subprocess decodifica por defecto en cp1252 y revienta.
    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        sys.exit(f"gmgn-cli fallo: {(r.stderr or r.stdout)[:300]}")
    return json.loads(r.stdout or "{}")


def limpio(texto: object, n: int = 12) -> str:
    """Recorta y quita lo que la consola de Windows no sabe escribir.

    Sin esto, una moneda con emoji o kanji tumba el listado entero al
    imprimirlo (cp1252 no puede codificar esos caracteres).
    """
    s = str(texto or "?")[:n]
    return s.encode(sys.stdout.encoding or "utf-8", "replace").decode(
        sys.stdout.encoding or "utf-8", "replace"
    )


def main() -> None:
    if not BIN:
        sys.exit("gmgn-cli no esta instalado o no esta en el PATH")

    p = argparse.ArgumentParser(description="Discover de GMGN con tus filtros")
    p.add_argument("--tipo", default="new_creation",
                   choices=["new_creation", "near_completion", "completed"])
    p.add_argument("--max-mc", type=float, default=3700)
    p.add_argument("--min-mc", type=float, default=0)
    p.add_argument("--min-vol", type=float, default=0)
    p.add_argument("--max-edad", default="10m")
    p.add_argument("--max-rug", type=float, default=None)
    p.add_argument("--min-holders", type=int, default=0)
    p.add_argument("--preset", default=None, choices=["safe", "smart-money", "strict"])
    p.add_argument("--orden", default="volume_24h")
    p.add_argument("--limite", type=int, default=80)
    p.add_argument("--todas", action="store_true", help="no descartar granjas")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    datos = pedir(args)
    filas = datos.get(args.tipo) or []
    if not filas and isinstance(datos, list):
        filas = datos

    granjas = 0
    limpias = []
    for t in filas:
        if not args.todas and int(t.get("creator_created_count") or 0) > MAX_MONEDAS_DEL_DEV:
            granjas += 1
            continue
        limpias.append(t)

    if args.json:
        print(json.dumps(limpias, indent=1)[:200000])
        return

    print(f"{len(filas)} monedas de GMGN · {granjas} descartadas por granja "
          f"(dev con >{MAX_MONEDAS_DEL_DEV} monedas) · quedan {len(limpias)}\n")
    if not limpias:
        print("  nada pasa los filtros ahora mismo")
        return

    cab = (f"{'simbolo':12} {'MC':>8} {'vol 24h':>9} {'ops':>5} {'hold':>5} "
           f"{'rug':>5} {'bundle':>7} {'smart':>6} {'del dev':>8}")
    print(cab)
    print("-" * len(cab))
    for t in limpias:
        print(
            f"{str(t.get('symbol'))[:12]:12} "
            f"${float(t.get('market_cap') or 0):>7,.0f} "
            f"${float(t.get('volume_24h') or 0):>8,.0f} "
            f"{int(t.get('swaps_24h') or 0):>5} "
            f"{int(t.get('holder_count') or 0):>5} "
            f"{float(t.get('rug_ratio') or 0):>5.2f} "
            f"{float(t.get('bundler_mhr') or 0)*100:>6.0f}% "
            f"{int(t.get('smart_degen_count') or 0):>6} "
            f"{int(t.get('creator_created_count') or 0):>8,}"
        )
    print("\nCA de los candidatos:")
    for t in limpias[:10]:
        print(f"  {t.get('symbol')}: {t.get('address')}")


if __name__ == "__main__":
    main()
