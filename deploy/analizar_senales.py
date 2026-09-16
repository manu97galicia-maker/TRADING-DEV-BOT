"""Que senal predice un pump, y con que umbral. Sale de trades/senales.jsonl.

COMO SE LEE ESTO. Por cada senal se prueban varios cortes y se compara el
grupo que pasa el corte con el que no. Lo que importa no es el acierto a
secas, sino la DIFERENCIA: si el 40% de las que pasan llegan al TP1 y
tambien el 40% de las que no, esa senal no sirve para nada por muy sensata
que suene.

La columna que decide es VENTAJA: cuantos puntos de acierto gana el grupo
que pasa el corte. Por debajo de unos 10 puntos, con pocas monedas, es
ruido.

    uv run deploy/analizar_senales.py           # todo lo apuntado
    uv run deploy/analizar_senales.py 500       # las 500 ultimas

Antes hay que tener datos:  uv run deploy/cazar_senales.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ENTRADA = Path("trades") / "senales.jsonl"
#: Monedas por debajo de las cuales no se decide nada.
MINIMO_FIABLE = 50
#: Puntos de acierto a partir de los cuales una senal merece mirarse.
VENTAJA_UTIL = 10.0
#: Cortes a probar por senal. "mayor" = pasa quien esta POR ENCIMA.
CORTES: dict[str, tuple[list[float], str]] = {
    "presion": ([1.5, 2.0, 3.0, 5.0], "mayor"),
    "ola_compradores": ([3, 5, 8, 12], "mayor"),
    "compradores": ([10, 20, 40, 80], "mayor"),
    "velocidad_sol_s": ([0.2, 0.5, 1.0, 2.0], "mayor"),
    "ola_neto_sol": ([1.0, 3.0, 6.0, 12.0], "mayor"),
    "avance_curva": ([0.10, 0.25, 0.50, 0.75], "mayor"),
    "titulares": ([15, 30, 60, 120], "mayor"),
    # De aqui abajo, pasa quien esta POR DEBAJO: son banderas de rug.
    "carteras_primer_bloque": ([2, 4, 8], "menor"),
    "sol_primer_bloque": ([1.0, 3.0, 8.0], "menor"),
    "venta_creador_sol": ([0.01, 0.5, 2.0], "menor"),
    "mayor_compra_sol": ([1.0, 3.0, 8.0], "menor"),
}


def _cargar(limite: int) -> list[dict]:
    if not ENTRADA.exists():
        return []
    filas = []
    for linea in ENTRADA.read_text(encoding="utf-8").splitlines():
        try:
            filas.append(json.loads(linea))
        except json.JSONDecodeError:
            continue
    return filas[-limite:] if limite else filas


def _pct(filas: list[dict], clave: str) -> float:
    return 100.0 * sum(1 for f in filas if f.get(clave)) / len(filas) if filas else 0.0


def _resumen(filas: list[dict]) -> None:
    print(f"\nMONEDAS APUNTADAS: {len(filas)}")
    if not filas:
        print("Nada que analizar. Primero: uv run deploy/cazar_senales.py")
        return
    print(f"  llegan al TP1 (+25%)   {_pct(filas, 'llego_tp1'):5.1f}%")
    print(f"  llegan al TP2 (+48%)   {_pct(filas, 'llego_tp2'):5.1f}%")
    print(f"  llegan al TP3 (+80%)   {_pct(filas, 'llego_tp3'):5.1f}%")
    print(f"  tocan el stop (-18%)   {_pct(filas, 'toco_stop'):5.1f}%")
    picos = sorted(f.get("pico", 0.0) for f in filas)
    mediana = picos[len(picos) // 2]
    print(f"  pico MEDIANO           {mediana * 100:+.1f}%")
    print("\nEsta es la linea base: comprando A CIEGAS saldria esto. Una senal")
    print("solo vale si mejora estos numeros de forma clara.")


def _analizar(filas: list[dict], objetivo: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"QUE SENAL PREDICE '{objetivo}'")
    print(f"{'=' * 72}")
    base = _pct(filas, objetivo)
    print(f"Linea base (todas): {base:.1f}%\n")
    print(f"{'senal':<24} {'corte':>8} {'pasan':>6} {'aciertan':>9} {'ventaja':>8}")
    print("-" * 72)

    hallazgos = []
    for senal, (cortes, sentido) in CORTES.items():
        for corte in cortes:
            if sentido == "mayor":
                pasan = [f for f in filas if (f.get(senal) or 0) >= corte]
            else:
                pasan = [f for f in filas if (f.get(senal) or 0) <= corte]
            if len(pasan) < MINIMO_FIABLE / 5:
                continue
            acierto = _pct(pasan, objetivo)
            ventaja = acierto - base
            marca = " <<<" if ventaja >= VENTAJA_UTIL else ""
            print(f"{senal:<24} {corte:>8g} {len(pasan):>6} "
                  f"{acierto:>8.1f}% {ventaja:>+7.1f}{marca}")
            if ventaja >= VENTAJA_UTIL:
                hallazgos.append((ventaja, senal, corte, len(pasan), acierto))
    if not hallazgos:
        print("\nNinguna senal saca ventaja clara todavia. O hacen falta mas")
        print("monedas, o estas senales no predicen esto.")
        return
    hallazgos.sort(reverse=True)
    print("\nLO QUE MERECE LA PENA, de mejor a peor:")
    for ventaja, senal, corte, n, acierto in hallazgos[:8]:
        print(f"  {senal} >= {corte:g}  ->  {acierto:.1f}% aciertan "
              f"(+{ventaja:.1f} puntos, sobre {n} monedas)")


def main() -> None:
    """Lee lo apuntado y dice que senal predice que."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    limite = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    filas = _cargar(limite)
    _resumen(filas)
    if not filas:
        return
    for objetivo in ("llego_tp1", "llego_tp2", "llego_tp3"):
        _analizar(filas, objetivo)
    print(f"\n{'=' * 72}")
    if len(filas) < MINIMO_FIABLE:
        print(f"OJO: solo {len(filas)} monedas. Por debajo de {MINIMO_FIABLE} esto")
        print("no decide nada -- deja el cazador corriendo mas rato.")
    else:
        print("Con estos umbrales se puede montar el filtro. Nunca antes.")


if __name__ == "__main__":
    main()
