"""Busca la escalera que mas deja, probandolas todas contra lo medido.

DE DONDE VIENE ESTO. Con 27.241 monedas medidas salio que el mejor filtro
--3 compradores distintos o mas-- sube el acierto del 14,4% al 27,4%. Suena
bien y no basta: el TP1 de hoy vende el 20% de un +25%, o sea +5% de la
posicion, contra un stop de -18%. Acertar una de cada cuatro pierde dinero.

Asi que el problema no es el filtro, es la ESCALERA. Y como cada moneda
apuntada trae hasta donde subio y hasta donde cayo, se puede probar cualquier
escalera contra esos mismos recorridos y ver cual habria dejado mas, sin
arriesgar un duro.

QUE SE PRUEBA. Solo las tres palancas que de verdad se pueden mover y se
entienden: donde esta el primer take profit, cuanto vende, y donde esta el
stop. Los peldanos lejanos se dejan como estan -- casi nunca se tocan y
moverlos solo servirian para enganarse a uno mismo.

POR QUE SE ORDENA POR LA PUNTA PESIMISTA. De cada moneda se sabe el pico y el
suelo, pero NO en que orden pasaron. Optimizar contra la punta optimista es
la forma mas rapida de elegir una escalera que solo funciona si la suerte
acompana en el orden. Se elige por la PEOR: la que aguanta incluso si el
stop salto siempre antes de cobrar nada.

    optimizar                       # todo lo medido
    optimizar 3                     # solo con >=3 compradores (el filtro)

Solo LEE y calcula. No cambia la configuracion del bot: al final dice que
habria que tocar, y eso lo decides tu.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ENTRADA = Path("trades") / "senales.jsonl"
#: Donde puede estar el primer take profit, en tanto por uno.
TP1_OBJETIVOS = (0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.70)
#: Cuanto vende ese primer peldano, de la compra original.
TP1_VENTAS = (0.20, 0.30, 0.40, 0.50, 0.60, 0.80, 1.00)
#: Donde puede estar el stop.
STOPS = (0.08, 0.10, 0.12, 0.15, 0.18, 0.22, 0.25, 0.30, 0.35, 0.40, 0.50)
#: Cuantas monedas hacen falta para que una combinacion cuente.
MINIMO = 50


def _analizador():  # noqa: ANN202
    """Reutiliza la cuenta del analizador, para no tener dos versiones."""
    ruta = Path(__file__).resolve().parent / "analizar_senales.py"
    spec = importlib.util.spec_from_file_location("an", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cuenta(filas: list[dict], escalones: list, stop: float) -> tuple[float, float]:
    """Lo que deja cada operacion con ESTA escalera. Dos puntas, como siempre.

    Es la misma cuenta que hace `analizar_senales._cuenta`, pero con la
    escalera como argumento en vez de leerla del YAML: aqui se prueban
    cientos, no la que hay puesta.
    """
    if not filas:
        return 0.0, 0.0
    peor = mejor = 0.0
    for f in filas:
        pico = float(f.get("pico") or 0.0)
        suelo = float(f.get("suelo") or 0.0)
        cobrado = sum(v * g for g, v in escalones if pico >= g)
        vendido = sum(v for g, v in escalones if pico >= g)
        if suelo <= -stop:
            peor += -stop
            mejor += cobrado + (1 - vendido) * -stop
        else:
            ultimo = f.get("ultimo")
            resto = float(ultimo) if ultimo is not None else 0.0
            peor += cobrado + (1 - vendido) * min(resto, 0.0)
            mejor += cobrado + (1 - vendido) * resto
    n = len(filas)
    return peor / n, mejor / n


def _lejanos(tp1_gain: float, tp1_sell: float) -> list[tuple[float, float]]:
    """Los peldanos de arriba, repartiendo lo que sobra del primero.

    Se mantienen los objetivos de la escalera actual y solo se reparte el
    resto proporcionalmente: cambiar tambien estos seria buscar formas en el
    ruido, porque muy pocas monedas llegan tan arriba.
    """
    resto = max(0.0, 1.0 - tp1_sell - 0.02)  # el 2% del moonbag se respeta
    pesos = [(0.48, 25), (0.80, 27.5), (1.50, 6.5), (2.30, 5.5), (4.00, 4.5),
             (6.00, 3.5), (9.00, 2.5), (14.00, 1.5), (19.00, 1.5)]
    # Solo los objetivos por encima del primer peldano.
    pesos = [(g, w) for g, w in pesos if g > tp1_gain]
    total = sum(w for _g, w in pesos) or 1
    return [(tp1_gain, tp1_sell)] + [(g, resto * w / total) for g, w in pesos]


def _cargar() -> list[dict]:
    if not ENTRADA.exists():
        return []
    filas = []
    for linea in ENTRADA.read_text(encoding="utf-8").splitlines():
        try:
            filas.append(json.loads(linea))
        except json.JSONDecodeError:
            continue
    return filas


def _avisar_si_toca_el_borde(g: float, v: float, stop: float) -> None:
    """Un ganador en el extremo de lo probado no es un ganador: es un aviso.

    Si la mejor combinacion sale justo en el valor mas pequeno o mas grande
    que se ha probado, el optimo de verdad esta FUERA y este numero no es el
    mejor, es el ultimo que se miro.

    Y con el stop hay ademas una trampa propia: de cada moneda se sabe el
    pico y el suelo pero no en que orden pasaron, y eso hace que un stop
    apretado parezca gratis -- recorta a las perdedoras sin tocar a las
    ganadoras, porque su suelo se apunto sin saber si ocurrio ANTES del
    pico. En la realidad un stop del 8% en pump.fun salta con el ruido
    normal de la curva y saca de monedas que iban a subir. Por eso el aviso
    es explicito en vez de dejar que el numero hable solo.
    """
    bordes = []
    if stop <= min(STOPS):
        bordes.append(f"el stop mas apretado que se ha probado (-{stop * 100:.0f}%)")
    if stop >= max(STOPS):
        bordes.append(f"el stop mas ancho que se ha probado (-{stop * 100:.0f}%)")
    if g <= min(TP1_OBJETIVOS) or g >= max(TP1_OBJETIVOS):
        bordes.append(f"el TP1 en el extremo probado (+{g * 100:.0f}%)")
    if v >= max(TP1_VENTAS):
        bordes.append("vendiendo la posicion ENTERA en el primer peldano")
    if not bordes:
        return
    print("\nAVISO -- la mejor combinacion esta en el BORDE de lo probado:")
    for b in bordes:
        print(f"  · {b}")
    print("Eso significa que el optimo real queda FUERA de la busqueda, asi")
    print("que este numero no es el mejor: es el ultimo que se miro.")
    if stop <= min(STOPS):
        print("Y con el stop hay trampa: como no se apunto si el suelo pasó")
        print("ANTES o DESPUES del pico, un stop apretado parece gratis aqui y")
        print("en la realidad salta con el ruido normal y te saca de las que")
        print("iban a subir. No lo copies al YAML sin medir el orden.")


def _veredicto(peor: float, mejor: float) -> None:
    """Que significa el resultado, dicho sin adornos."""
    if peor > 0:
        print("La punta PESIMISTA es positiva: esta escalera gana dinero incluso")
        print("suponiendo que el stop saltara siempre antes de cobrar nada.")
    elif mejor > 0:
        print("OJO: solo gana en la punta optimista. Entre las dos puntas esta")
        print("la verdad, y depende del ORDEN en que pasaron pico y suelo, que")
        print("no se apunto. NO es para jugarse dinero todavia.")
    else:
        print("NINGUNA escalera sale positiva con estos datos. Eso NO es un")
        print("fallo del calculo: dice que con estas monedas no se gana")
        print("cambiando la salida, y que hay que apretar mas la ENTRADA.")


def main() -> None:
    """Prueba todas las escaleras y dice cual deja mas."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    filas = _cargar()
    if len(filas) < MINIMO:
        print(f"Hacen falta al menos {MINIMO} monedas medidas y hay "
              f"{len(filas)}. Deja el cazador corriendo: cazador on")
        return

    corte = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    if corte > 0:
        filas = [f for f in filas if (f.get("ola_compradores") or 0) >= corte]
        print(f"Solo monedas con >= {corte:g} compradores distintos: "
              f"{len(filas)}")
    else:
        print(f"Todas las monedas medidas: {len(filas)}")
    if len(filas) < MINIMO:
        print("Con el filtro quedan muy pocas para decidir nada.")
        return

    an = _analizador()
    esc_hoy, stop_hoy = an._escalera()  # noqa: SLF001
    hoy_peor, hoy_mejor = _cuenta(filas, esc_hoy, stop_hoy)
    print(f"\nLA DE HOY (+{esc_hoy[0][0] * 100:.0f}% vende "
          f"{esc_hoy[0][1] * 100:.0f}%, stop -{stop_hoy * 100:.0f}%): "
          f"entre {hoy_peor * 100:+.1f}% y {hoy_mejor * 100:+.1f}% por operacion")

    resultados = []
    for g in TP1_OBJETIVOS:
        for v in TP1_VENTAS:
            escalones = _lejanos(g, v)
            for stop in STOPS:
                peor, mejor = _cuenta(filas, escalones, stop)
                resultados.append((peor, mejor, g, v, stop))
    resultados.sort(reverse=True)

    print(f"\n{'=' * 72}")
    print("LAS 12 MEJORES, ordenadas por la punta PESIMISTA")
    print(f"{'=' * 72}")
    print(f"{'TP1':>8}{'vende':>8}{'stop':>8}{'peor':>10}{'mejor':>10}")
    print("-" * 72)
    for peor, mejor, g, v, stop in resultados[:12]:
        print(f"{g * 100:>7.0f}%{v * 100:>7.0f}%{stop * 100:>7.0f}%"
              f"{peor * 100:>9.1f}%{mejor * 100:>9.1f}%")

    peor, mejor, g, v, stop = resultados[0]
    print(f"\n{'=' * 72}")
    print("LA MEJOR QUE SALE DE TUS DATOS")
    print(f"{'=' * 72}")
    print(f"  TP1 al +{g * 100:.0f}%, vendiendo el {v * 100:.0f}%")
    print(f"  Stop al -{stop * 100:.0f}%")
    print(f"  ROI MEDIO POR OPERACION: entre {peor * 100:+.1f}% y "
          f"{mejor * 100:+.1f}%")
    mejora = (peor - hoy_peor) * 100
    print(f"  Frente a la de hoy: {mejora:+.1f} puntos por operacion")
    _avisar_si_toca_el_borde(g, v, stop)
    print()
    _veredicto(peor, mejor)
    print("\nEsto no cambia nada del bot. Los numeros van a mano al YAML.")


if __name__ == "__main__":
    main()
