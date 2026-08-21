"""Apunta el resultado REAL del dia y lo acumula desde el 21-ago-2026.

Se lee de los logs, que traen el P&L leido de la cadena, y NO del contador
teorico del bot: ese ha llegado a equivocarse de signo (+229 SOL en una
operacion que dio -0,02).

    .venv/bin/python medir_resultados.py          # dia de hoy
    .venv/bin/python medir_resultados.py --todo   # historico completo

El resultado se guarda en resultados_diarios.json, que es lo que lee el panel.
"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

INICIO = "2026-08-21"
FICHERO = "/root/ai-bot/resultados_diarios.json"
PNL = re.compile(
    r"^(\d{4}-\d\d-\d\d) [\d:]+ .*?- ([^:]+): P&L REAL ([+-][0-9.]+) SOL cobrado"
    r"(?: \+ ([0-9.]+) en moonbag)?")

def recoge(dias_atras=2):
    corte = os.path.getmtime  # se filtran ficheros por fecha de escritura
    ahora = datetime.now(timezone.utc).timestamp()
    ops = {}
    for f in glob.glob("/root/ai-bot/logs/*.log"):
        try:
            if ahora - corte(f) > dias_atras*86400 + 3600:
                continue
            for l in open(f, encoding="utf-8", errors="replace"):
                m = PNL.search(l)
                if not m:
                    continue
                dia, sym, real, bag = m.groups()
                if dia < INICIO:
                    continue
                # La misma operacion puede salir en varios ficheros de log.
                ops[(dia, sym, real)] = (dia, float(real) + float(bag or 0))
        except OSError:
            continue
    return list(ops.values())

def main() -> None:
    todo = "--todo" in sys.argv
    ops = recoge(30 if todo else 2)
    if not ops:
        print("  todavia no hay operaciones cerradas desde el %s" % INICIO)
        return
    por_dia = {}
    for dia, sol in ops:
        d = por_dia.setdefault(dia, {"n": 0, "sol": 0.0, "ganadas": 0, "peor": 0.0})
        d["n"] += 1
        d["sol"] += sol
        if sol > 0:
            d["ganadas"] += 1
        d["peor"] = min(d["peor"], sol)
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print("  RESULTADOS REALES (leidos de la cadena) desde el %s" % INICIO)
    print()
    print("  %-12s %6s %9s %10s %10s %10s" % (
        "dia", "ops", "aciertos", "SOL", "media/op", "peor"))
    acum, nacum, gacum = 0.0, 0, 0
    for dia in sorted(por_dia):
        d = por_dia[dia]
        acum += d["sol"]; nacum += d["n"]; gacum += d["ganadas"]
        marca = "  <- hoy" if dia == hoy else ""
        print("  %-12s %6d %8.1f%% %+10.4f %+10.4f %+10.4f%s" % (
            dia, d["n"], 100*d["ganadas"]/d["n"], d["sol"],
            d["sol"]/d["n"], d["peor"], marca))
    print()
    print("  ACUMULADO: %d operaciones · %.1f%% aciertos · %+.4f SOL · %+.4f por operacion" % (
        nacum, 100*gacum/max(1, nacum), acum, acum/max(1, nacum)))
    json.dump({"desde": INICIO, "dias": por_dia,
               "total": {"n": nacum, "sol": acum, "ganadas": gacum}},
              open(FICHERO, "w"), indent=1)
    print("  guardado en %s" % FICHERO)

main()
