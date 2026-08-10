"""Que tenian en comun las que ganaron y las que perdieron.

Cruza, para cada operacion, las CONDICIONES DE ENTRADA que el bot registro al
comprar con el P&L REAL leido de la cadena al cerrar. Si una condicion aparece
sistematicamente en las perdedoras y no en las ganadoras, ahi hay un filtro que
poner; si no aparece ninguna diferencia, hay que buscar la causa en otro sitio
en vez de seguir moviendo numeros a ojo.

Se usa el P&L REAL y nunca el que calcula el bot. El teorico es `tokens x
precio observado` y no descuenta comision, impacto ni slippage: el 9-ago sumaba
+0,16 SOL mientras la cartera perdia 0,75, y en una operacion concreta llego a
equivocarse de SIGNO (S'MORE decia +0,0906 y la cadena dijo -0,0099).

Se lanza solo cada 8 operaciones nuevas (ver `automatizar_estudio.sh`), que es
cuando hay muestra suficiente para que la comparacion signifique algo.
"""

from __future__ import annotations

import glob
import json
import re
import statistics
import sys
from pathlib import Path

LOGS = "/root/ai-bot/logs/*.log"
ESTADO = Path("/root/ai-bot/estudio_ultimo.json")

# "CUMPLE twg: 43.7 SOL en 32 compras, 5 en primer bloque, 44 tx de 25 wallets, MC $4,215 a los 13.0s"
RE_ENTRADA = re.compile(
    r"CUMPLE ([^:]+): ([\d.]+) SOL en (\d+) compras, (\d+) en primer bloque, "
    r"(\d+) tx de (\d+) wallets, MC \$([\d,]+) a los ([\d.]+)s"
)
# "twg: P&L REAL -0.0520 SOL (cadena)"
RE_PNL = re.compile(r"^(.+?): P&L REAL ([+-][\d.]+) SOL \(cadena\)")
RE_DEV = re.compile(r"DEV OK ([^:]+): (\d+) migraciones de (\d+)")


def recoger() -> list[dict]:
    entradas: dict[str, dict] = {}
    devs: dict[str, tuple[int, int]] = {}
    ops: list[dict] = []

    for fichero in sorted(glob.glob(LOGS)):
        try:
            texto = Path(fichero).read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for linea in texto.splitlines():
            if (m := RE_DEV.search(linea)):
                devs[m.group(1).strip()] = (int(m.group(2)), int(m.group(3)))
            if (m := RE_ENTRADA.search(linea)):
                entradas[m.group(1).strip()] = {
                    "volumen_sol": float(m.group(2)),
                    "compras": int(m.group(3)),
                    "primer_bloque": int(m.group(4)),
                    "tx": int(m.group(5)),
                    "wallets": int(m.group(6)),
                    "mc": float(m.group(7).replace(",", "")),
                    "segundos": float(m.group(8)),
                }
            if (m := RE_PNL.search(linea.split(" - ")[-1])):
                sym = m.group(1).strip()
                op = {"moneda": sym, "pnl": float(m.group(2))}
                op.update(entradas.get(sym, {}))
                if sym in devs:
                    migs, lanz = devs[sym]
                    op["dev_migradas"] = migs
                    op["dev_tasa"] = migs / lanz if lanz else 0
                ops.append(op)
    return ops


def resumen(grupo: list[dict], campo: str) -> str:
    vals = [g[campo] for g in grupo if campo in g]
    if not vals:
        return "sin dato"
    return "mediana %.1f  (min %.1f / max %.1f, n=%d)" % (
        statistics.median(vals), min(vals), max(vals), len(vals))


def main() -> None:
    ops = recoger()
    if not ops:
        print("Todavia no hay operaciones cerradas con P&L real.")
        return

    ganadoras = [o for o in ops if o["pnl"] > 0]
    perdedoras = [o for o in ops if o["pnl"] <= 0]
    total = sum(o["pnl"] for o in ops)

    print("=" * 70)
    print("ESTUDIO DE %d OPERACIONES CERRADAS" % len(ops))
    print("=" * 70)
    print("  ganadoras : %d  (%.0f%%)   suman %+.4f SOL"
          % (len(ganadoras), len(ganadoras) / len(ops) * 100,
             sum(o["pnl"] for o in ganadoras)))
    print("  perdedoras: %d  (%.0f%%)   suman %+.4f SOL"
          % (len(perdedoras), len(perdedoras) / len(ops) * 100,
             sum(o["pnl"] for o in perdedoras)))
    print("  RESULTADO : %+.4f SOL" % total)
    if ganadoras and perdedoras:
        gm = statistics.mean(o["pnl"] for o in ganadoras)
        pm = statistics.mean(o["pnl"] for o in perdedoras)
        print("  gana de media %+.4f  ·  pierde de media %+.4f" % (gm, pm))
        print("  >> hacen falta %.1f ganadoras por cada perdedora para empatar"
              % (abs(pm) / gm if gm else 0))

    print()
    print("--- QUE LAS DIFERENCIA EN LA ENTRADA ---")
    campos = [
        ("volumen_sol", "volumen al comprar (SOL)"),
        ("mc", "market cap de entrada ($)"),
        ("wallets", "wallets distintas"),
        ("compras", "compras acumuladas"),
        ("primer_bloque", "compras en el primer bloque"),
        ("segundos", "segundos de vida al comprar"),
        ("dev_tasa", "tasa de migracion del dev"),
    ]
    print("%-32s %-34s %s" % ("", "GANADORAS", "PERDEDORAS"))
    for campo, nombre in campos:
        g = resumen(ganadoras, campo)
        p = resumen(perdedoras, campo)
        if g == "sin dato" and p == "sin dato":
            continue
        print("%-32s %-34s %s" % (nombre, g, p))

    print()
    print("--- OPERACION A OPERACION ---")
    for o in sorted(ops, key=lambda x: -x["pnl"]):
        print("  %-14s %+8.4f SOL   vol %-6s MC %-9s %s wallets  %ss" % (
            o["moneda"][:14], o["pnl"],
            ("%.1f" % o["volumen_sol"]) if "volumen_sol" in o else "?",
            ("$%.0f" % o["mc"]) if "mc" in o else "?",
            o.get("wallets", "?"), o.get("segundos", "?")))

    print()
    if len(ops) < 8:
        print("AVISO: con %d operaciones esto no da para concluir nada. Hacen "
              "falta 8 como minimo, y aun asi es poca muestra." % len(ops))
    ESTADO.write_text(json.dumps({"operaciones_estudiadas": len(ops)}), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
