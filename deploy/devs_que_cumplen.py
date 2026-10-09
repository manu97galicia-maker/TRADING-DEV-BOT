"""Los devs que cumplen TODO a la vez: lanzan a diario, pagan dex y no hunden.

PARA QUE EXISTE. `medir_devs.py` mide y ORDENA por nota, pero la nota mezcla
seis cosas: un dev puede salir alto porque paga mucho aunque hunda, u otro
porque lanza mucho aunque no pague. Esto no ordena: FILTRA. O cumple las tres
condiciones, o no sale.

    uv run deploy/devs_que_cumplen.py            # con los cortes de abajo
    uv run deploy/devs_que_cumplen.py 0.5 0.7 5  # migracion, pago dex, lanz/7d

SOBRE "QUE SUPEREN 40k DE MARKET CAP", que es lo que se pidio y no se puede
medir honestamente
------------------------------------------------------------------------
El `ath_market_cap` que publica pump.fun ESTA INFLADO y se puede fabricar: hay
creadores ensenando 89 millones de maximo con 1.387 $ de liquidez. Filtrar por
ese campo daria una lista llena de humo, y encima la mas convincente.

Lo que SI es imposible de falsear es MIGRAR: para que una moneda migre tienen
que entrar ~85 SOL de verdad en su curva. Y eso, en dolares, es justo la franja
que se pedia:

    SOL a 150 $  ->  migra sobre  61.600 $
    SOL a 220 $  ->  migra sobre  90.400 $

O sea que "pasa de 40k" y "migra" son practicamente la misma pregunta, y una
se puede comprobar. Por eso aqui se filtra por TASA DE MIGRACION.

LO QUE ESTE FICHERO NO HACE: no compra, no toca el bot, no llama a ninguna API.
Solo lee `devs_medidos.json`, que escribe `medir_devs.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ENTRADA = Path("devs_medidos.json")

#: Fraccion de sus monedas que llegan a migrar. 0,30 ya es alto: la mayoria de
#: los devs no pasa del 5%.
MIN_MIGRACION = 0.30
#: Fraccion de sus monedas recientes con pago de DexScreener visto.
MIN_PAGO_DEX = 0.60
#: Lanzamientos en los ultimos 7 dias. 7 = uno al dia.
MIN_LANZ_7D = 5
#: Dias desde su ultimo lanzamiento. Un dev parado no sirve aunque fuera bueno.
MAX_DIAS_PARADO = 3.0
#: Fraccion de sus monedas recientes que estan practicamente a cero.
MAX_MUERTAS = 0.60


def _leer() -> list[dict]:
    if not ENTRADA.is_file():
        print(f"No existe {ENTRADA}. Primero: uv run deploy/medir_devs.py")
        raise SystemExit(1)
    datos = json.loads(ENTRADA.read_text(encoding="utf-8"))
    medido = datos.get("_medido", "?")
    devs = [v for k, v in datos.items() if not k.startswith("_")]
    if not devs and isinstance(datos.get("devs"), list):
        devs = datos["devs"]
    print(f"Leidos {len(devs)} devs, medidos el {medido}.")
    return [d for d in devs if isinstance(d, dict) and "wallet" in d]


def main() -> None:
    args = sys.argv[1:]
    migr = float(args[0]) if len(args) >= 1 else MIN_MIGRACION
    dex = float(args[1]) if len(args) >= 2 else MIN_PAGO_DEX  # noqa: PLR2004
    lanz = int(args[2]) if len(args) >= 3 else MIN_LANZ_7D  # noqa: PLR2004

    devs = _leer()
    print()
    print(
        f"Cortes:  migracion >= {migr:.0%}   pago dex >= {dex:.0%}   "
        f"lanzamientos 7d >= {lanz}   parado <= {MAX_DIAS_PARADO} dias"
    )
    print()

    cumplen = [
        d
        for d in devs
        if d.get("tasa_migracion", 0) >= migr
        and d.get("pct_pago_dex", 0) >= dex
        and d.get("lanz_7d", 0) >= lanz
        and d.get("dias_desde_ultima", 999) <= MAX_DIAS_PARADO
        and d.get("pct_muertas", 1) <= MAX_MUERTAS
    ]
    cumplen.sort(key=lambda d: d.get("tasa_migracion", 0), reverse=True)

    if not cumplen:
        print("NINGUNO cumple los tres cortes a la vez.")
        print()
        print("Cuantos caen en cada uno (sobre el total), para saber cual aflojar:")
        for etiqueta, fn in (
            (f"migran >= {migr:.0%}", lambda d: d.get("tasa_migracion", 0) >= migr),
            (f"pagan dex >= {dex:.0%}", lambda d: d.get("pct_pago_dex", 0) >= dex),
            (f"lanzan >= {lanz} en 7d", lambda d: d.get("lanz_7d", 0) >= lanz),
            (
                f"activos (<= {MAX_DIAS_PARADO}d)",
                lambda d: d.get("dias_desde_ultima", 999) <= MAX_DIAS_PARADO,
            ),
        ):
            print(
                f"    {etiqueta:<24} {sum(1 for d in devs if fn(d)):>4} de {len(devs)}"
            )
        print()
        print("Aflojar el que menos pase:  uv run deploy/devs_que_cumplen.py 0.2 0.4 3")
        return

    print(f"{'migr':>6}{'dex':>6}{'boost':>7}{'7d':>5}{'30d':>6}{'muert':>7}  wallet")
    for d in cumplen:
        print(
            f"{d['tasa_migracion'] * 100:>5.0f}%"
            f"{d.get('pct_pago_dex', 0) * 100:>5.0f}%"
            f"{d.get('pct_boost', 0) * 100:>6.0f}%"
            f"{d.get('lanz_7d', 0):>5}"
            f"{d.get('lanz_30d', 0):>6}"
            f"{d.get('pct_muertas', 0) * 100:>6.0f}%  "
            f"{d['wallet']}"
        )
    print()
    print(f"{len(cumplen)} devs cumplen los tres cortes.")
    print()
    print("PARA COMPRARLES AL LANZAR hace falta encender `filters.pumpfun_dev`,")
    print("que hoy esta en `enabled: false`. Eso es otra decision: esa via compra")
    print("monedas RECIEN NACIDAS, que es lo contrario de la estrategia de ahora.")


if __name__ == "__main__":
    main()
