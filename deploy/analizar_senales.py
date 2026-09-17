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

import yaml

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


# --------------------------------------------------------- LA CUENTA


def _escalera() -> tuple[list[tuple[float, float]], float]:
    """La escalera de venta que hay puesta ahora mismo en el bot."""
    t = yaml.safe_load(
        (Path("bots") / "bot-paper-ai-entry.yaml").read_text(encoding="utf-8")
    )["trade"]
    pares = [(t["take_profit_percentage"], t["take_profit_sell_percentage"])]
    n = 2
    while t.get(f"tp{n}_gain") and t.get(f"tp{n}_sell"):
        pares.append((float(t[f"tp{n}_gain"]), float(t[f"tp{n}_sell"])))
        n += 1
    for e in t.get("tp_extra") or []:
        pares.append((float(e["gain"]), float(e["sell"])))
    pares.sort()
    return pares, float(t["stop_loss_percentage"])


def _cuenta(filas: list[dict]) -> tuple[float, float]:
    """Cuanto se gana o se pierde por operacion, en tanto por uno.

    POR QUE HAY DOS NUMEROS Y NO UNO. De cada moneda se apunto hasta donde
    subio (`pico`) y hasta donde cayo (`suelo`), pero NO en que orden pasaron
    las dos cosas. Sin el orden no se puede saber si el stop salto antes o
    despues de cobrar los peldanos, asi que se dan las dos puntas:

      PEOR  -- si toco el stop, se supone que salto ANTES de cobrar nada.
      MEJOR -- se supone que cobro todos los peldanos que alcanzo y el stop
               salto despues.

    La verdad esta entre las dos. Si la PEOR ya sale positiva, se gana dinero
    seguro; si la MEJOR sale negativa, se pierde seguro. Cuando una es
    positiva y la otra negativa, hace falta medir el orden antes de arriesgar.
    """
    if not filas:
        return 0.0, 0.0
    escalones, stop = _escalera()
    peor = mejor = 0.0
    for f in filas:
        pico = float(f.get("pico") or 0.0)
        suelo = float(f.get("suelo") or 0.0)
        toco_stop = suelo <= -stop
        # Lo que la escalera habria cobrado llegando hasta el pico.
        cobrado = sum(v * g for g, v in escalones if pico >= g)
        vendido = sum(v for g, v in escalones if pico >= g)
        if toco_stop:
            # PEOR: el stop primero, no se cobra ningun peldano.
            peor += -stop
            # MEJOR: se cobran los peldanos y el resto sale en el stop.
            mejor += cobrado + (1 - vendido) * -stop
        else:
            # Sin stop, lo que no se vendio sigue dentro: se valora al
            # ULTIMO precio visto, no al pico, que seria hacer trampa.
            ultimo = f.get("ultimo")
            resto = float(ultimo) if ultimo is not None else 0.0
            peor += cobrado + (1 - vendido) * min(resto, 0.0)
            mejor += cobrado + (1 - vendido) * resto
    n = len(filas)
    return peor / n, mejor / n


def _rentabilidad(filas: list[dict]) -> None:
    """Lo unico que decide de verdad: si la cuenta sale o no sale."""
    print(f"\n{'=' * 72}")
    print("LA CUENTA: cuanto deja cada operacion, con la escalera de hoy")
    print(f"{'=' * 72}")
    escalones, stop = _escalera()
    print(f"Escalera: {', '.join(f'+{g * 100:.0f}%/{v * 100:.0f}%' for g, v in escalones[:3])}"
          f" ...   Stop: -{stop * 100:.0f}%")
    base_peor, base_mejor = _cuenta(filas)
    print(f"\nComprando A CIEGAS:  entre {base_peor * 100:+.1f}% y "
          f"{base_mejor * 100:+.1f}% por operacion")
    print("\nCon cada filtro:")
    print(f"{'filtro':<30}{'monedas':>8}{'peor':>9}{'mejor':>9}")
    print("-" * 72)

    filtros = []
    for senal, (cortes, sentido) in CORTES.items():
        for corte in cortes:
            if sentido == "mayor":
                pasan = [f for f in filas if (f.get(senal) or 0) >= corte]
            else:
                pasan = [f for f in filas if (f.get(senal) or 0) <= corte]
            if len(pasan) < MINIMO_FIABLE:
                continue
            peor, mejor = _cuenta(pasan)
            filtros.append((mejor, peor, senal, corte, len(pasan), sentido))

    filtros.sort(reverse=True)
    for mejor, peor, senal, corte, n, sentido in filtros[:15]:
        signo = ">=" if sentido == "mayor" else "<="
        print(f"{senal} {signo} {corte:<{28 - len(senal) - len(signo)}g}"
              f"{n:>8}{peor * 100:>8.1f}%{mejor * 100:>8.1f}%")

    seguros = [f for f in filtros if f[1] > 0]
    print()
    if seguros:
        mejor, peor, senal, corte, n, sentido = seguros[0]
        signo = ">=" if sentido == "mayor" else "<="
        print(f"GANA SEGURO: {senal} {signo} {corte:g} deja al menos "
              f"{peor * 100:+.1f}% por operacion ({n} monedas).")
    else:
        print("NINGUN filtro gana seguro: en todos, la punta PEOR sale negativa.")
        print("Traducido: con esta escalera, acertar mas no basta -- lo que")
        print("cobra el TP1 (+5% de la posicion) no cubre un stop de -18%.")
        print("Antes de comprar hay que cambiar la escalera, no el filtro.")


def _ritmo(filas: list[dict]) -> None:
    """Cuantas monedas por minuto pasarian cada filtro.

    POR QUE ESTO IMPORTA TANTO COMO EL ACIERTO. Un filtro se juzga por dos
    cosas y casi siempre se mira solo una. La otra es el RITMO: de nada sirve
    el mejor filtro del mundo si deja pasar tres monedas por minuto y solo
    caben cinco posiciones a la vez -- el bot se llena en dos minutos con las
    tres primeras que aparezcan, que no son las mejores, son las primeras. A
    partir de ahi el filtro no elige nada: elige el reloj.

    Al reves tambien duele: un filtro que deja pasar una moneda cada dos horas
    tiene las cinco plazas vacias casi todo el dia.

    El ritmo sale de las marcas de tiempo apuntadas, no de una estimacion.
    """
    tiempos = sorted(float(f["t"]) for f in filas if f.get("t"))
    if len(tiempos) < 2:  # noqa: PLR2004
        return
    minutos = (tiempos[-1] - tiempos[0]) / 60.0
    if minutos <= 0:
        return
    print(f"\n{'=' * 72}")
    print(f"EL RITMO: cuantas caerian por minuto  ({minutos / 60:.1f} horas medidas)")
    print(f"{'=' * 72}")
    print(f"Monedas vistas: {len(filas)}  ->  {len(filas) / minutos:.1f} por minuto\n")
    print(f"{'filtro':<30}{'pasan':>8}{'por minuto':>12}{'una cada':>12}")
    print("-" * 72)

    ritmos = []
    for senal, (cortes, sentido) in CORTES.items():
        for corte in cortes:
            if sentido == "mayor":
                pasan = [f for f in filas if (f.get(senal) or 0) >= corte]
            else:
                pasan = [f for f in filas if (f.get(senal) or 0) <= corte]
            if len(pasan) < MINIMO_FIABLE:
                continue
            ritmos.append((len(pasan) / minutos, senal, corte, len(pasan), sentido))
    ritmos.sort(reverse=True)
    for por_min, senal, corte, n, sentido in ritmos[:12]:
        signo = ">=" if sentido == "mayor" else "<="
        cada = f"{60 / por_min:.0f} s" if por_min >= 1 else f"{1 / por_min:.0f} min"
        etiqueta = f"{senal} {signo} {corte:g}"
        print(f"{etiqueta:<30}{n:>8}{por_min:>11.1f}{cada:>12}")

    print("\nCOMO SE LEE ESTO. Con 5 plazas y posiciones que duran minutos, un")
    print("filtro que deja caer mas de ~1 por minuto NO esta eligiendo: el bot")
    print("se llena con las primeras que pasen y el resto ni las mira. Si el")
    print("ritmo sale alto, hay que APRETAR el corte aunque baje el acierto --")
    print("porque el acierto medido aqui es el de TODAS las que pasan, y el bot")
    print("solo va a poder comprar las primeras.")


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
    _rentabilidad(filas)
    _ritmo(filas)
    print(f"\n{'=' * 72}")
    if len(filas) < MINIMO_FIABLE:
        print(f"OJO: solo {len(filas)} monedas. Por debajo de {MINIMO_FIABLE} esto")
        print("no decide nada -- deja el cazador corriendo mas rato.")
    else:
        print("Con estos umbrales se puede montar el filtro. Nunca antes.")


if __name__ == "__main__":
    main()
