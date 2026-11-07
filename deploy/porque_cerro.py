"""POR QUE SE CERRO ESTA MONEDA. Se le pasa el mint y cuenta su vida entera.

POR QUE EXISTE. Es la pregunta que mas se ha hecho --cuatro veces en un dia--
y hasta ahora contestarla pedia inventar un `grep` distinto cada vez, adivinar
el simbolo a partir del mint y leer miles de lineas desde el movil. El dato
estaba todo el rato en `trades/trades.log`, que guarda cada operacion con su
mint y su `motivo`, pero nadie lo miraba porque no habia forma comoda.

QUE CONTESTA, en este orden:

    de donde vino la moneda (que fuente la trajo)
    a cuanto se compro y cuanto se gasto
    cada peldano que se cobro, con su precio
    hasta donde llego mientras se vigilaba (el pico)
    con que motivo se cerro, y ese motivo EXPLICADO en castellano
    cuanto se dejo encima de la mesa

    porque_cerro D5Sd6RGFcsyCg4UgKMTax22ZYjBrivQXAAGAVAaQpump
    porque_cerro D5Sd6RGF          # con los primeros caracteres basta
    porque_cerro                   # la ultima moneda cerrada

Solo LEE. No vende, no toca el bot, no imprime ninguna clave.
"""

from __future__ import annotations

import contextlib
import json
import signal
import sys
from pathlib import Path

# CORTAR LA SALIDA NO ES UN ERROR. Desde el movil esto se lee con
# `porque_cerro <mint> | head`, y al cerrar `head` la tuberia Python levanta
# BrokenPipeError y escupe un traceback de siete lineas encima de justo lo
# que se queria leer. Se restaura el comportamiento de siempre de Unix: si
# nadie escucha, se termina y ya esta.
with contextlib.suppress(AttributeError, ValueError):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

RAIZ = Path(__file__).resolve().parent.parent
REGISTRO = RAIZ / "trades" / "trades.log"
#: A partir de cuanto se avisa de que se vendio pronto. Por debajo de esto la
#: diferencia entre el precio de salida y el pico es ruido del propio tick.
AVISA_SI_SE_DEJO = 5.0

#: Que significa cada motivo de venta, en castellano y sin rodeos. La clave es
#: lo que escribe el bot en `motivo`; los peldanos llevan numero variable
#: (tp_tier1_+30%) y se tratan aparte.
MOTIVOS = {
    "anti_rug": (
        "ANTI-RUG: en pocos segundos se vendio mucho mas de lo que se compraba "
        "y el bot salio de la posicion ENTERA de emergencia. Si la moneda no "
        "se estaba hundiendo de verdad, el corte esta demasiado bajo "
        "(anti_rug_min_sol en el yaml)."
    ),
    "stop_loss": (
        "STOP LOSS: cayo por debajo del suelo. Si no se habia cobrado ningun "
        "peldano, el suelo era el stop normal; si se habia cobrado alguno, el "
        "suelo ya estaba subido y la venta fue EN VERDE."
    ),
    "trailing_stop": (
        "TRAILING: habia subido mas de un 20% y luego cayo desde el maximo mas "
        "de lo permitido. Se vende todo, moonbag incluido."
    ),
    "bajo_entrada": (
        "POR DEBAJO DE LA COMPRA demasiado tiempo seguido (venta_bajo_entrada_"
        "seg). Una moneda que al cabo de ese rato sigue sin recuperar el precio "
        "pagado no ha encontrado comprador."
    ),
    "muerta": (
        "MONEDA MUERTA: pasado el reloj (venta_muerta_seg) no habia hecho ni la "
        "subida minima. No es una perdida por caida, es una posicion que no iba "
        "a ningun sitio y ocupaba una plaza."
    ),
    "panic_sell": (
        "PANICO: alguien pulso 'vende todo' en el panel. No fue una decision de "
        "la estrategia."
    ),
    "final_target_mc": (
        "OBJETIVO DE MARKET CAP alcanzado (final_target_mc): se vende todo lo "
        "que queda."
    ),
    "manual": "VENTA MANUAL desde el panel de Telegram.",
    "timeout_seg": "SE ACABO EL TIEMPO maximo de la posicion (max_hold_time).",
    "max_hold_time": (
        "SE ACABO EL TIEMPO maximo de la posicion (max_hold_time). Se vende "
        "hasta el moonbag y el 2% se deja corriendo. Hoy esta a 0 (apagado), "
        "asi que si sale esto es que alguien lo encendio."
    ),
    "fin_mayhem": (
        "FIN DEL MAYHEM: la moneda llego a las 23 h de vida y se vendio todo "
        "(vender_antes_de_edad_seg). El agente de pump.fun deja de operarla "
        "a las 24 h y se queman sus tokens."
    ),
    "suelo_por_mc": (
        "SUELO DE MARKET CAP roto: la moneda cayo por debajo del market cap "
        "que se habia fijado como suelo tras subir, y se vendio todo para "
        "asegurar lo ganado. No es una perdida, es un cierre en verde."
    ),
}


def explica(motivo: str) -> str:
    """El motivo en castellano. Los peldanos se reconocen por el nombre."""
    if not motivo:
        return (
            "SIN MOTIVO APUNTADO. Las ventas viejas no lo guardaban; si es "
            "reciente, mira el log con:  journalctl -u pump-bot | grep Sold"
        )
    if motivo.startswith("tp_tier"):
        return f"TAKE PROFIT: se cobro el peldano {motivo}. Esto es lo que TIENE que pasar."
    if motivo.startswith("mc_tier"):
        return f"PELDANO DE MARKET CAP {motivo}: la moneda llego a ese market cap."
    return MOTIVOS.get(motivo, f"Motivo '{motivo}' (no catalogado aqui).")


def _lee(mint: str) -> list[dict]:
    """Las operaciones de esa moneda, en orden. Basta con el principio del mint."""
    if not REGISTRO.exists():
        print(f"No existe {REGISTRO}. El bot aun no ha registrado ninguna operacion.")
        raise SystemExit(1)
    filas = []
    for cruda in REGISTRO.read_text(encoding="utf-8", errors="replace").splitlines():
        linea = cruda.strip()
        if not linea:
            continue
        try:
            d = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if not mint or str(d.get("token_address", "")).startswith(mint):
            filas.append(d)
    return filas


def main() -> None:
    pedido = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    filas = _lee(pedido)
    if not filas:
        print(f"No hay ninguna operacion registrada para '{pedido}'.")
        print("Comprueba el mint, o mira las ultimas con:  porque_cerro")
        raise SystemExit(1)

    if not pedido:
        # Sin argumento: la ultima moneda que se toco.
        ultimo = filas[-1]["token_address"]
        filas = [f for f in filas if f["token_address"] == ultimo]

    sym = next((f.get("symbol") for f in filas if f.get("symbol")), "?")
    mint = filas[0].get("token_address", "?")
    print(f"== {sym}  ({mint})")

    compras = [f for f in filas if f.get("action") == "buy"]
    ventas = [f for f in filas if f.get("action") == "sell"]

    entrada = 0.0
    if compras:
        c = compras[0]
        entrada = float(c.get("price") or 0)
        gastado = entrada * float(c.get("amount") or 0)
        print(f"   fuente:  {c.get('origen') or '?'}")
        print(f"   compra:  {c.get('timestamp', '?')[:19].replace('T', ' ')}")
        print(f"            {gastado:.4f} SOL  ·  precio {entrada:.10f}")
    else:
        print("   (no hay compra registrada: puede ser de antes de este registro)")

    if not ventas:
        print("\n   TODAVIA NO SE HA VENDIDO. La posicion sigue abierta.")
        return

    print(f"\n   {len(ventas)} venta(s):")
    cobrado = 0.0
    for v in ventas:
        p = float(v.get("price") or 0)
        cant = float(v.get("amount") or 0)
        cobrado += p * cant
        pct = (p / entrada - 1) * 100 if entrada > 0 else 0.0
        cuando = str(v.get("timestamp", ""))[:19].replace("T", " ")
        print(f"     {cuando}  {p * cant:.4f} SOL  {pct:+.1f}%  [{v.get('motivo') or '?'}]")

    # EL PICO ES LO QUE DICE SI SE VENDIO PRONTO. Sin el, un "+5%" no se sabe
    # si fue todo lo que dio la moneda o si se dejo un x3 encima de la mesa.
    picos = [float(v["pico"]) for v in ventas if v.get("pico")]
    if picos and entrada > 0:
        pico = max(picos)
        print(f"\n   llego a:  {(pico / entrada - 1) * 100:+.1f}%  (mientras se vigilaba)")

    ultimo = ventas[-1]
    print(f"\n   POR QUE SE CERRO:\n     {explica(str(ultimo.get('motivo') or ''))}")

    if picos and entrada > 0:
        salida_media = (cobrado / sum(float(v.get("amount") or 0) for v in ventas)
                        if sum(float(v.get("amount") or 0) for v in ventas) else 0)
        if salida_media > 0:
            dejado = (max(picos) / salida_media - 1) * 100
            if dejado > AVISA_SI_SE_DEJO:
                print(f"\n   SE DEJO ENCIMA DE LA MESA: {dejado:.0f}% entre el precio")
                print("   medio de salida y el maximo que llego a marcar.")


if __name__ == "__main__":
    main()
