"""Que take profit y que stop loss habrian ganado mas, sobre los datos propios.

    uv run deploy/optimizar_tp_sl.py              # todo lo apuntado
    uv run deploy/optimizar_tp_sl.py 0.25         # con compras de 0,25 SOL
    uv run deploy/optimizar_tp_sl.py 0.25 5000    # y solo las 5.000 ultimas

QUE HACE. Lee `trades/senales.jsonl` --lo que lleva apuntando el cazador-- y
prueba cada combinacion de TP y SL sobre TODAS las monedas, descontando las
comisiones de verdad. Despues repite la cuenta filtrando por cada senal
(presion, compradores distintos, velocidad...) para ver cual sube el
beneficio esperado y cual no sirve de nada.

LO QUE DEVUELVE NO ES UNA PROMESA. Es lo que habria pasado con lo apuntado,
que es lo unico honesto que se puede decir.

====================================================================
LO QUE ESTOS DATOS NO PUEDEN CONTESTAR. Leer esto antes que los numeros.
====================================================================

1. NO SE SABE EL ORDEN. De cada moneda se guarda el maximo (`pico`) y el
   minimo (`suelo`) alcanzados, pero NO cual ocurrio antes. Una moneda que
   bajo un 20% y luego subio un 80% y otra que subio un 80% y luego bajo un
   20% se guardan igual, y con un stop del 20% una gana y la otra pierde.

   Por eso cada combinacion se da en HORQUILLA:
     OPTIMISTA  = si toco el TP, se cobra (se supone que el TP fue primero)
     PESIMISTA  = si toco el stop, se pierde (se supone que el stop fue
                  primero)
   La verdad esta entre las dos. Una combinacion solo es fiable si gana
   TAMBIEN en la pesimista.

2. LA VENTANA SON 5 MINUTOS. El cazador sigue cada moneda `SEGUIMIENTO`
   segundos (300). Lo que pase despues no esta apuntado. Asi que esto
   optimiza para operaciones de minutos, no para `max_hold_time: 3600`.

3. NO SE APUNTA SI LA MONEDA ES MAYHEM. La foto que toma el cazador
   (`_foto` en cazar_senales.py) no guarda ese dato, asi que NO se puede
   separar mayhem de lo demas. Para poder, hay que anadirlo primero y
   esperar a que se acumulen datos nuevos.

4. EL PRECIO DE ENTRADA ES EL DE LA FOTO, no el de una compra real. No
   incluye el deslizamiento de la compra ni que la transaccion pueda no
   confirmar.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ENTRADA = Path("trades") / "senales.jsonl"

#: Por debajo de esto no se decide nada: con pocas monedas cualquier
#: combinacion parece buena por azar.
MINIMO_FIABLE = 200

#: Comisiones de prioridad del bot, en microlamports por unidad de computo,
#: y las unidades de cada operacion. Salen del yaml que corre.
FEE_COMPRA_ULAM = 8_480_000
FEE_VENTA_ULAM = 8_480_000
CU_COMPRA, CU_VENTA = 140_000, 120_000
#: Renta de la cuenta de token. Se recupera al cerrarla, asi que no es
#: coste; se deja escrito para que nadie la sume dos veces.
RENTA_RECUPERABLE_SOL = 0.00204

#: Combinaciones a probar.
TPS = [0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.70, 1.00, 1.50, 2.00]
SLS = [0.15, 0.20, 0.25, 0.30, 0.40, 0.50]

#: Cortes por senal, para ver cual mejora el beneficio.
CORTES: dict[str, list[float]] = {
    "presion": [1.5, 2.0, 3.0, 5.0],
    "ola_compradores": [3, 5, 8, 12],
    "compradores": [10, 20, 40, 80],
    "velocidad_sol_s": [0.2, 0.5, 1.0, 2.0],
    "titulares": [15, 30, 60, 120],
    "avance_curva": [0.25, 0.50, 0.75, 0.90],
}


def _comision_sol() -> float:
    """Lo que cuesta el viaje de ida y vuelta en comisiones de prioridad."""
    return (FEE_COMPRA_ULAM * CU_COMPRA + FEE_VENTA_ULAM * CU_VENTA) / 1e15


def _leer(limite: int) -> list[dict]:
    if not ENTRADA.is_file():
        print(f"No existe {ENTRADA}.")
        print("Primero hay que acumular datos:  uv run deploy/cazar_senales.py")
        raise SystemExit(1)
    filas = []
    with ENTRADA.open(encoding="utf-8") as f:
        for linea in f:
            try:
                d = json.loads(linea)
            except (json.JSONDecodeError, ValueError):
                continue
            if "pico" in d and "suelo" in d:
                filas.append(d)
    return filas[-limite:] if limite > 0 else filas


def _resultado(fila: dict, tp: float, sl: float, *, optimista: bool) -> float:
    """Cuanto habria rendido esa moneda con ese TP y ese SL, en tanto por uno.

    `optimista` decide quien va primero cuando se tocaron los dos.
    """
    pico = float(fila.get("pico") or 0.0)
    suelo = float(fila.get("suelo") or 0.0)
    toca_tp = pico >= tp
    toca_sl = suelo <= -sl
    if toca_tp and toca_sl:
        return tp if optimista else -sl
    if toca_tp:
        return tp
    if toca_sl:
        return -sl
    # Ni uno ni otro: se cierra por tiempo con lo que valiera al final.
    ultimo = fila.get("ultimo")
    return float(ultimo) if ultimo is not None else 0.0


def _evalua(
    filas: list[dict], tp: float, sl: float, compra_sol: float
) -> tuple[float, float, float]:
    """(media optimista, media pesimista, % que cobran el TP), en SOL netos."""
    coste = _comision_sol()
    opt = pes = 0.0
    cobran = 0
    for fila in filas:
        opt += _resultado(fila, tp, sl, optimista=True) * compra_sol - coste
        pes += _resultado(fila, tp, sl, optimista=False) * compra_sol - coste
        if float(fila.get("pico") or 0.0) >= tp:
            cobran += 1
    n = len(filas)
    return opt / n, pes / n, cobran / n * 100


def _tabla(filas: list[dict], compra_sol: float, titulo: str) -> None:
    print()
    print(f"=== {titulo} ({len(filas):,} monedas) ===".replace(",", "."))
    if len(filas) < MINIMO_FIABLE:
        print(f"  Menos de {MINIMO_FIABLE}: no se decide nada con esto.")
        return
    resultados = []
    for tp in TPS:
        for sl in SLS:
            opt, pes, pct = _evalua(filas, tp, sl, compra_sol)
            resultados.append((pes, opt, tp, sl, pct))
    # Se ordena por la PESIMISTA: lo que aguanta el peor supuesto.
    resultados.sort(reverse=True)
    print(f"  {'TP':>6}{'SL':>6}{'cobran TP':>11}{'pesimista':>12}{'optimista':>12}")
    for pes, opt, tp, sl, pct in resultados[:8]:
        print(
            f"  {tp * 100:>5.0f}%{sl * 100:>5.0f}%{pct:>10.1f}%"
            f"{pes:>11.5f}{opt:>12.5f}"
        )
    mejor = resultados[0]
    print()
    print(f"  Mejor en el supuesto PESIMISTA: TP +{mejor[2] * 100:.0f}% / "
          f"SL -{mejor[3] * 100:.0f}%")
    if mejor[0] <= 0:
        print("  OJO: ni la mejor combinacion gana dinero en el supuesto")
        print("  pesimista. Con estos datos NO hay una configuracion segura.")


def main() -> None:
    args = sys.argv[1:]
    compra_sol = float(args[0]) if args else 0.25
    limite = int(args[1]) if len(args) > 1 else 0

    filas = _leer(limite)
    print()
    print(f"Leidas {len(filas):,} monedas de {ENTRADA}".replace(",", "."))
    print(f"Compra simulada: {compra_sol} SOL")
    print(f"Comisiones por viaje: {_comision_sol():.5f} SOL "
          f"({_comision_sol() / compra_sol * 100:.2f}% de la compra)")
    print(f"Renta de la cuenta ({RENTA_RECUPERABLE_SOL} SOL) NO se cuenta: "
          f"se recupera al cerrarla.")
    if not filas:
        return

    _tabla(filas, compra_sol, "TODAS LAS MONEDAS")

    print()
    print("=== Y AHORA FILTRANDO POR CADA SENAL ===")
    print("Solo se muestran los cortes que MEJORAN la media pesimista.")
    base_pes = max(
        _evalua(filas, tp, sl, compra_sol)[1] for tp in TPS for sl in SLS
    )
    for senal, cortes in CORTES.items():
        for corte in cortes:
            sub = [f for f in filas if float(f.get(senal) or 0) >= corte]
            if len(sub) < MINIMO_FIABLE:
                continue
            mejor = max(
                _evalua(sub, tp, sl, compra_sol)[1] for tp in TPS for sl in SLS
            )
            if mejor > base_pes:
                ventaja = (mejor - base_pes) / compra_sol * 100
                print(
                    f"  {senal} >= {corte:<6} {len(sub):>7,} monedas  "
                    f"mejor pesimista {mejor:+.5f} SOL  "
                    f"(+{ventaja:.2f} puntos sobre no filtrar)".replace(",", ".")
                )

    print()
    print("RECORDATORIO: esto es lo que HABRIA pasado con lo apuntado, en una")
    print("ventana de 5 minutos, sin saber el orden entre pico y suelo, y sin")
    print("poder separar las monedas mayhem porque ese dato no se guarda.")


if __name__ == "__main__":
    main()
