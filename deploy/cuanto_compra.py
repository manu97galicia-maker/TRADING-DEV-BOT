"""DE CUANTO ESTA COMPRANDO DE VERDAD, y si cuadra con lo configurado.

POR QUE EXISTE. "Me parece que esta comprando mas del 25%" es una sospecha
razonable y hasta ahora comprobarla pedia tres `grep` distintos desde el
movil, sabiendo de antemano que linea buscar. El dato estaba en el log --el
bot escribe la cuenta desglosada antes de cada compra-- pero nadie lo miraba
porque no habia forma comoda.

LO QUE HACE. Empareja cada compra con la cuenta que la decidio y las pone en
una tabla, marcando las que se pasan del porcentaje configurado:

    cuanto_compra          ultimas 3 horas
    cuanto_compra 12       ultimas 12 horas

POR QUE HAY QUE EMPAREJARLAS. "Compro 0,25 SOL" no dice nada por si solo: el
25% de una cartera de 1 SOL son 0,25 y el de una de 2 SOL son 0,5. Sin la
base, cualquier numero parece correcto o parece un disparate segun el humor
del que mira.

LO QUE NO ES: una auditoria de la cadena. Esto lee lo que el bot DIJO que iba
a gastar, no lo que se firmo. Si el bot estuviera mintiendo en su propio log,
esto mentiria con el; para eso estan las firmas y `revisar_wallet.py`.

Solo LEE. No toca el bot ni imprime ninguna clave.
"""

from __future__ import annotations

import contextlib
import re
import signal
import subprocess
import sys
from pathlib import Path

import yaml

# Cortar la salida con `| head` no es un error: se restaura el comportamiento
# de Unix en vez de escupir un traceback encima de lo que se queria leer.
with contextlib.suppress(AttributeError, ValueError):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"
#: Cuanto se le perdona al porcentaje antes de marcarlo. El importe se decide
#: con el saldo recien leido y se escribe redondeado, asi que un punto arriba
#: o abajo es el redondeo, no un fallo.
TOLERANCIA_PUNTOS = 1.5

# Las tres formas en que el bot ha escrito la cuenta del tamano. Estan las
# tres a proposito: un log de hace horas puede llevar cualquiera de ellas, y
# reconocer solo la de hoy daria "ninguna compra" justo cuando se esta
# comparando el antes con el despues.
_CARTERA = re.compile(
    r"CARTERA: ([\d.]+) SOL \(([\d.]+) en wallet \+ ([\d.]+) en monedas\)"
)
_IMPORTE_USD = re.compile(r"IMPORTE: ([\d.]+) \$ a ([\d.]+) \$/SOL")
_SALDO = re.compile(r"SALDO: ([\d.]+) SOL libres")
_COMPRA = re.compile(r"Buying ([\d.]+) SOL worth of (\S+)")


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _log(horas: int) -> list[str]:
    try:
        salida = subprocess.run(  # noqa: S603
            ["journalctl", "-u", "pump-bot", "--since", f"-{horas} hours",  # noqa: S607
             "--no-pager"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        print("No pude leer el log del bot (journalctl).")
        raise SystemExit(1) from None
    return salida.stdout.splitlines()


def _cabecera(t: dict, horas: int) -> float:
    """Escribe lo que esta configurado y devuelve el porcentaje, si lo hay."""
    frac = float(t.get("fraccion_saldo") or 0)
    usd = float(t.get("buy_amount_usd") or 0)
    if frac > 0:
        print(f"== CONFIGURADO: {frac * 100:.0f}% de la cartera · ultimas {horas} h")
    elif usd > 0:
        print(f"== CONFIGURADO: {usd:.2f} $ por compra · ultimas {horas} h")
    else:
        fijo = float(t.get("buy_amount") or 0)
        print(f"== CONFIGURADO: {fijo:.4f} SOL por compra · ultimas {horas} h")
    return frac


def _compras(lineas: list[str]) -> list[tuple[str, float, str, float]]:
    """Empareja cada compra con la cuenta que la decidio, en orden."""
    base: tuple[str, float] | None = None
    filas: list[tuple[str, float, str, float]] = []
    for linea in lineas:
        if m := _CARTERA.search(linea):
            base = ("cartera", float(m.group(1)))
        elif m := _IMPORTE_USD.search(linea):
            base = ("dolares", float(m.group(1)) / float(m.group(2)))
        elif m := _SALDO.search(linea):
            base = ("saldo libre", float(m.group(1)))
        elif m := _COMPRA.search(linea):
            importe, sym = float(m.group(1)), m.group(2)
            if base is None:
                filas.append((sym, importe, "?", 0.0))
            else:
                modo, valor = base
                # EL PORCENTAJE SOLO SIGNIFICA ALGO SOBRE LA CARTERA. En modo
                # dolares la base ES el importe, asi que saldria "100%" en
                # todas y pareceria que se esta gastando la cartera entera.
                # Un numero que no significa nada es peor que ninguno.
                pct = importe / valor * 100 if modo == "cartera" and valor > 0 else 0.0
                filas.append((sym, importe, modo, pct))
            base = None
    return filas


def _veredicto(filas: list, frac: float, pasadas: int, horas: int) -> None:
    """Lo que hay que hacer con lo que se acaba de ver."""
    print(f"\n  {len(filas)} compras en {horas} h.")
    if frac <= 0:
        return
    viejas = sum(1 for _s, _i, m, _p in filas if m != "cartera")
    if viejas:
        print(f"  OJO: {viejas} se decidieron con OTRO modo de tamano (el bot se")
        print("  reinicio con otra configuracion). Esas no hay que compararlas")
        print(f"  con el {frac * 100:.0f}% de ahora.")
    if pasadas:
        print(f"  {pasadas} se pasan del {frac * 100:.0f}% configurado.")
        print("  Si la columna 'sobre' dice cartera, la base incluye lo que ya")
        print("  esta metido en monedas. Comprueba con `vigiladas` que esas")
        print("  posiciones existen de verdad: un marcador viejo inflaria la base.")
    else:
        print(f"  Ninguna se pasa del {frac * 100:.0f}%: el tamano cuadra.")


def main() -> None:
    horas = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 3
    frac = _cabecera(_trade(), horas)
    filas = _compras(_log(horas))

    if not filas:
        print("\n  No hay ninguna compra en esa ventana.")
        print("  Prueba con mas horas:  cuanto_compra 12")
        return

    print(f"\n  {'moneda':<12} {'compro':>9} {'sobre':>12} {'% de esa base':>14}")
    pasadas = 0
    for sym, importe, modo, pct in filas[-25:]:
        se_pasa = (
            frac > 0 and modo == "cartera" and pct > frac * 100 + TOLERANCIA_PUNTOS
        )
        pasadas += se_pasa
        cifra = f"{pct:.1f}%" if modo == "cartera" else "(no aplica)"
        aviso = "  <== SE PASA" if se_pasa else ""
        print(f"  {sym[:12]:<12} {importe:>8.4f}s {modo:>12} {cifra:>14}{aviso}")

    _veredicto(filas, frac, pasadas, horas)


if __name__ == "__main__":
    main()
