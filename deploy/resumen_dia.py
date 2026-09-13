"""Resumen de las operaciones del dia: que se compro, que se vendio y a cuanto.

Se lee de `trades/trades.log`, que es lo unico que se escribe con cada
operacion confirmada. No consulta nada a la red: es el registro de lo que de
verdad paso, no una estimacion.

Uso:
    uv run deploy/resumen_dia.py            # hoy
    uv run deploy/resumen_dia.py 2          # los ultimos 2 dias
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REGISTRO = Path("trades") / "trades.log"


def _lineas(dias: int) -> list[dict]:
    if not REGISTRO.is_file():
        return []
    corte = datetime.now(timezone.utc) - timedelta(days=dias)
    fuera = []
    for linea in REGISTRO.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(linea)
            cuando = datetime.fromisoformat(d["timestamp"]).replace(
                tzinfo=timezone.utc
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if cuando >= corte:
            d["_cuando"] = cuando
            fuera.append(d)
    return fuera


def main() -> None:
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    ops = _lineas(dias)
    if not ops:
        print(f"Sin operaciones en los ultimos {dias} dia(s).")
        print(f"(se lee {REGISTRO.resolve()})")
        return

    # Por moneda: lo gastado comprando y lo cobrado vendiendo.
    gastado: dict[str, float] = defaultdict(float)
    cobrado: dict[str, float] = defaultdict(float)
    compras: dict[str, int] = defaultdict(int)
    ventas: dict[str, int] = defaultdict(int)
    entrada: dict[str, float] = {}
    ultima: dict[str, float] = {}

    for o in ops:
        sim = o.get("symbol") or o.get("token_address", "?")[:6]
        sol = float(o.get("price") or 0) * float(o.get("amount") or 0)
        if o.get("action") == "buy":
            gastado[sim] += sol
            compras[sim] += 1
            entrada.setdefault(sim, float(o.get("price") or 0))
        elif o.get("action") == "sell":
            cobrado[sim] += sol
            ventas[sim] += 1
            ultima[sim] = float(o.get("price") or 0)

    print(f"OPERACIONES DE LOS ULTIMOS {dias} DIA(S)\n")
    print(f"{'moneda':<12}{'compras':>8}{'ventas':>7}{'puesto':>9}{'sacado':>9}{'balance':>10}")
    print("-" * 55)
    total_g = total_c = 0.0
    cerradas = abiertas = 0
    for sim in sorted(gastado, key=lambda s: cobrado[s] - gastado[s]):
        g, c = gastado[sim], cobrado[sim]
        total_g += g
        total_c += c
        if ventas[sim]:
            cerradas += 1
        else:
            abiertas += 1
        marca = "" if ventas[sim] else "  (abierta)"
        print(
            f"{sim:<12}{compras[sim]:>8}{ventas[sim]:>7}"
            f"{g:>9.4f}{c:>9.4f}{c - g:>+10.4f}{marca}"
        )

    print("-" * 55)
    print(f"{'TOTAL':<12}{'':>8}{'':>7}{total_g:>9.4f}{total_c:>9.4f}{total_c - total_g:>+10.4f} SOL")
    print(f"\n{cerradas} con ventas, {abiertas} sin vender todavia.")
    if total_g > 0:
        print(f"Rendimiento sobre lo puesto: {(total_c / total_g - 1) * 100:+.1f}%")
    print(
        "\nOJO: 'sacado' solo cuenta lo YA vendido. Una posicion abierta sale en "
        "negativo aunque vaya ganando, porque todavia no ha cobrado nada."
    )


if __name__ == "__main__":
    main()
