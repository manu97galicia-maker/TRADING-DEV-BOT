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
            cuando = datetime.fromisoformat(d["timestamp"]).replace(tzinfo=timezone.utc)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if cuando >= corte:
            d["_cuando"] = cuando
            fuera.append(d)
    return fuera


def _acumular_por_fuente(
    ops: list[dict],
) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    """Suma puesto y sacado por fuente, contando SOLO lo ya cerrado."""
    fuente_de: dict[str, str] = {}
    vendidas: set[str] = set()
    for o in ops:
        sim = o.get("symbol") or o.get("token_address", "?")[:6]
        if o.get("action") == "buy":
            fuente_de[sim] = o.get("origen") or "listener"
        elif o.get("action") == "sell":
            vendidas.add(sim)

    gastado: dict[str, float] = defaultdict(float)
    cobrado: dict[str, float] = defaultdict(float)
    n: dict[str, int] = defaultdict(int)
    for o in ops:
        sim = o.get("symbol") or o.get("token_address", "?")[:6]
        if sim not in vendidas:
            continue  # abierta: todavia no dice nada
        f = fuente_de.get(sim, "?")
        sol = float(o.get("price") or 0) * float(o.get("amount") or 0)
        if o.get("action") == "buy":
            gastado[f] += sol
            n[f] += 1
        else:
            cobrado[f] += sol
    return gastado, cobrado, n


#: Como se agrupa el motivo de salida. El orden importa: es de peor a mejor.
_ESCALONES = (
    ("stop", "stop loss", ("stop_loss", "panic_sell")),
    ("muerta", "muerta / bajo entrada", ("muerta", "bajo_entrada", "abandono")),
    ("tp1", "solo TP1 (+30%)", ("TP1",)),
    ("tp2", "hasta TP2 (+50%)", ("TP2",)),
    (
        "tp3",
        "cruzó TP3 (+70%)",
        ("TP3", "TP4", "TP5", "TP6", "TP7", "TP8", "TP9", "TP10"),
    ),
)


def _escalon(motivo: str) -> str:
    """En que escalon murio esta venta. '?' si no se apunto el motivo."""
    if not motivo:
        return "?"
    texto = str(motivo).strip()
    # De mas alto a mas bajo: lo que importa de una moneda es HASTA DONDE
    # llego, no por donde salio al final.
    for clave, _, marcas in reversed(_ESCALONES):
        if any(m.lower() in texto.lower() for m in marcas):
            return clave
    return "?"


def _recorrido(ops: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """De que fuente vino cada moneda, y hasta que escalon llego.

    De una moneda importa HASTA DONDE llego, no por donde salio al final: una
    que cobro el TP3 y luego cerro en el stop es una ganadora, no una perdida.
    Por eso se guarda el escalon MAS ALTO de todas sus ventas.
    """
    orden = [c for c, _, _ in _ESCALONES] + ["?"]
    fuente_de: dict[str, str] = {}
    llego: dict[str, str] = {}
    for o in ops:
        sim = o.get("symbol") or o.get("token_address", "?")[:6]
        if o.get("action") == "buy":
            fuente_de[sim] = o.get("origen") or "listener"
        elif o.get("action") == "sell":
            nuevo = _escalon(o.get("motivo") or "")
            previo = llego.get(sim)
            if previo is None or orden.index(nuevo) > orden.index(previo):
                llego[sim] = nuevo
    return fuente_de, llego


def _hasta_donde(ops: list[dict]) -> None:
    """Hasta que peldano llego cada moneda, por fuente.

    ES LA TABLA QUE DECIDE EL REPARTO DE LA ESCALERA. El TP3 (+70%) lleva el
    35% de cada posicion: si casi ninguna moneda lo cruza, ese 35% esta
    esperando un tren que no pasa y hay que bajarlo. Si lo cruzan muchas,
    esta bien donde esta. Sin este recuento, mover el TP3 es adivinar.
    """
    fuente_de, llego = _recorrido(ops)
    if not llego:
        return
    print("\n\nHASTA DONDE LLEGO CADA MONEDA\n")
    fuentes = sorted({fuente_de.get(s, "?") for s in llego})
    etiquetas = {c: e for c, e, _ in _ESCALONES}
    etiquetas["?"] = "sin motivo apuntado"
    print(f"{'':24}" + "".join(f"{c:>8}" for c, _, _ in _ESCALONES) + f"{'?':>8}")
    print("-" * (24 + 8 * (len(_ESCALONES) + 1)))
    for f in fuentes:
        fila = dict.fromkeys(etiquetas, 0)
        for sim, esc in llego.items():
            if fuente_de.get(sim, "?") == f:
                fila[esc] = fila.get(esc, 0) + 1
        print(
            f"{f:<24}"
            + "".join(f"{fila[c]:>8}" for c, _, _ in _ESCALONES)
            + f"{fila['?']:>8}"
        )
    print("\n  " + " · ".join(f"{c}={e}" for c, e in etiquetas.items()))

    total = len(llego)
    cruzaron = sum(1 for e in llego.values() if e == "tp3")
    conocidas = sum(1 for e in llego.values() if e != "?")
    if conocidas:
        print(
            f"\n  CRUZARON EL +70%: {cruzaron} de {conocidas} "
            f"({cruzaron / conocidas * 100:.0f}%)"
        )
        print(
            "  Ahi esta el 35% de cada posicion. Si ese porcentaje es bajo,\n"
            "  conviene bajar el TP3; si es alto, esta bien donde esta."
        )
    if total != conocidas:
        print(
            f"\n  ({total - conocidas} sin motivo: son de antes del 14-sep, "
            "cuando no se apuntaba)"
        )


def _por_fuente(ops: list[dict]) -> None:
    """Que fuente de compra va mejor y cual peor.

    Es la unica comparacion que decide algo: si una fuente pierde en todas
    sus operaciones, se apaga y se acabo. Antes no se podia hacer porque el
    registro no apuntaba de donde venia cada compra.

    Se compara SOLO con posiciones cerradas. Una abierta no ha cobrado nada
    todavia y meterla en la cuenta hunde a la fuente que mas ha comprado hoy,
    que normalmente es la que mejor esta funcionando.
    """
    gastado, cobrado, n = _acumular_por_fuente(ops)
    print("QUE FUENTE VA MEJOR (solo posiciones ya cerradas)\n")
    if not gastado:
        print("  Todavia no hay ninguna posicion cerrada: nada que comparar.")
        return
    print(f"{'fuente':<16}{'ops':>5}{'puesto':>9}{'sacado':>9}{'balance':>10}{'':>4}")
    print("-" * 53)
    orden = sorted(gastado, key=lambda f: -(cobrado[f] - gastado[f]))
    for f in orden:
        g, c = gastado[f], cobrado[f]
        pct = (c / g - 1) * 100 if g else 0
        print(f"{f:<16}{n[f]:>5}{g:>9.4f}{c:>9.4f}{c - g:>+10.4f}{pct:>+7.0f}%")
    if len(orden) > 1:
        print(f"\n  MEJOR: {orden[0]}   ·   PEOR: {orden[-1]}")
    _sin_origen = sum(1 for f in gastado if f == "listener")
    if _sin_origen:
        print(
            "\n  'listener' son compras sin fuente apuntada: o del listener "
            "normal, o anteriores al cambio que empezo a registrarlo."
        )


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

    _por_fuente(ops)
    _hasta_donde(ops)
    print(f"\nPOR MONEDA, ULTIMOS {dias} DIA(S)\n")
    print(
        f"{'moneda':<12}{'compras':>8}{'ventas':>7}{'puesto':>9}{'sacado':>9}{'balance':>10}"
    )
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
    print(
        f"{'TOTAL':<12}{'':>8}{'':>7}{total_g:>9.4f}{total_c:>9.4f}{total_c - total_g:>+10.4f} SOL"
    )
    print(f"\n{cerradas} con ventas, {abiertas} sin vender todavia.")
    if total_g > 0:
        print(f"Rendimiento sobre lo puesto: {(total_c / total_g - 1) * 100:+.1f}%")
    print(
        "\nOJO: 'sacado' solo cuenta lo YA vendido. Una posicion abierta sale en "
        "negativo aunque vaya ganando, porque todavia no ha cobrado nada."
    )


if __name__ == "__main__":
    main()
