"""Mide que senales predicen un pump, apuntando lo que pasa DESPUES.

POR QUE ESTO Y NO UNA ESTRATEGIA DIRECTA. Elegir un umbral a ojo ("compra si
la presion es 3x") es apostar. Nadie --ni yo-- sabe si 3x predice algo en
pump.fun sin haberlo medido, y aqui se juega dinero de verdad.

Esto no compra nada. Escucha la cadena, y por cada moneda:

  1. Al cumplir la EDAD DE DECISION toma una foto de las senales: presion
     compradora, compradores distintos, velocidad de la curva, concentracion
     del primer bloque (el bundle), venta del creador.
  2. Sigue mirandola durante el SEGUIMIENTO y apunta hasta donde llego y
     hasta donde cayo, medido desde el precio de esa foto.

El resultado es una linea por moneda en trades/senales.jsonl con las senales
Y el desenlace. Con eso `analizar_senales.py` dice que umbral habria dado
que acierto -- con datos de verdad, no con una corazonada.

    uv run deploy/cazar_senales.py              # hasta Ctrl+C
    uv run deploy/cazar_senales.py 60           # 60 minutos y para

Cuantas mas monedas, mejor: con 200 ya se ve algo, con 1.000 se decide.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import websockets
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from monitoring import volumen

#: Programa de pump.fun. Direccion publica y fija.
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SALIDA = Path("trades") / "senales.jsonl"

#: Cuando se toma la foto de las senales, en segundos desde la primera
#: operacion vista. 20s es pronto para que quede recorrido por delante, y
#: tarde para que haya pasado algo que medir.
EDAD_DECISION = 20.0
#: Cuanto se sigue mirando despues de la foto.
SEGUIMIENTO = 300.0
#: Ventana corta para medir la oleada de compradores mas reciente.
VENTANA_OLA = 5.0
#: Sin esto se llenaria el fichero de monedas que nadie toco.
MIN_OPERACIONES = 5
#: LOS TRES NIVELES DE MEDIDA SON LOS TRES PELDANOS DE LA ESCALERA (25-sep).
#:
#: Hasta hoy no coincidian: la configuracion tenia uno o dos peldanos y aqui
#: se median tres niveles inventados, para poder ver cuantas monedas se
#: quedaban por debajo del objetivo. Con la escalera de tres peldanos
#: (+25%/+60%/+100%) esa necesidad desaparece: medir los peldanos de verdad
#: contesta la misma pregunta y ademas dice cuanto se cobro.
#:
#: LO QUE SE PIERDE AL IGUALARLOS, y conviene saberlo: ya no hay un nivel
#: intermedio por debajo del primer peldano, asi que el informe no dira
#: cuantas monedas se quedaron entre el +15% y el +25%. Si hiciera falta esa
#: pregunta, la forma correcta es anadir un cuarto nivel de medida, NO bajar
#: el take profit del bot para que cuadre con el medidor.
#:
#: `test_los_niveles_medidos_son_los_peldanos_del_bot` comprueba que siguen
#: coincidiendo: sin el, este script mediria objetivos que el bot ya no tiene
#: y los informes dirian cosas que no pasaron.
TP1, TP2, TP3 = 0.35, 0.65, 1.00
STOP = -0.30
#: Cada cuanto se imprime una linea de estado.
AVISO_SEG = 30


def _foto(mint: str) -> dict | None:
    """Las senales de una moneda en este instante, o None si no da."""
    precio = volumen.precio_en_vivo(mint, EDAD_DECISION * 2)
    curva = volumen.progreso_curva(mint)
    if precio is None or curva is None:
        return None
    m = volumen.metricas_segunda_ola(mint, ventana=VENTANA_OLA)
    if int(m["compras"]) < MIN_OPERACIONES:
        return None
    compra_ola, venta_ola = volumen.presion(mint, VENTANA_OLA)
    avance, sol_curva = curva
    edad = float(m["edad"]) or 1.0
    carteras_bloque = volumen.carteras_primer_bloque(mint)
    return {
        "mint": mint,
        "t": time.time(),
        "precio": precio,
        # --- SENALES ---
        # Presion: SOL comprados contra vendidos en la ventana corta. Es la
        # causa del movimiento; el precio es la consecuencia.
        "presion": (compra_ola / venta_ola) if venta_ola > 0 else 999.0,
        "ola_neto_sol": float(m["ola_neto_sol"]),
        # Compradores DISTINTOS en la ventana. Separa una subida de verdad
        # (mucha gente) de una wallet sola empujando el precio.
        "ola_compradores": int(m["ola_compradores"]),
        "compradores": int(m["compradores"]),
        # Velocidad: SOL por segundo que entran en la curva. Una moneda que
        # se va a disparar llena la curva deprisa, no solo "sube".
        "velocidad_sol_s": sol_curva / edad,
        "avance_curva": avance,
        "sol_curva": sol_curva,
        # Tamano de la mayor compra suelta. Si casi todo el neto es UNA
        # compra, no hay demanda: hay alguien inflando.
        "mayor_compra_sol": float(m["mayor_compra_sol"]),
        # --- BANDERAS DE RUG / BUNDLE ---
        # Carteras que entraron en el MISMO bloque que la creacion. Es la
        # firma de un bundle: se reparten el token barato antes que nadie.
        "carteras_primer_bloque": carteras_bloque,
        "sol_primer_bloque": volumen.sol_primer_bloque(mint),
        "vio_la_creacion": volumen.vio_la_creacion(mint),
        "venta_creador_sol": float(m["venta_creador_sol"]),
        "titulares": volumen.titulares(mint),
        "edad": edad,
    }


def _desenlace(mint: str, precio0: float) -> dict:
    """Hasta donde llego y hasta donde cayo, desde el precio de la foto."""
    precio = volumen.precio_en_vivo(mint, SEGUIMIENTO * 2)
    return {"ultimo": (precio / precio0 - 1) if precio else None}


class Cazador:
    """Lleva la cuenta de que monedas estan en foto y cuales en seguimiento."""

    def __init__(self) -> None:
        self.pendientes: dict[str, float] = {}  # mint -> primera vez vista
        self.siguiendo: dict[str, dict] = {}  # mint -> foto + extremos
        self.escritas = 0

    def ver(self, mints: list[str]) -> None:
        ahora = time.time()
        for mint in mints:
            if mint in self.siguiendo:
                self._actualizar(mint)
                continue
            if mint in self.pendientes:
                if (ahora - self.pendientes[mint]) >= EDAD_DECISION:
                    self._fotografiar(mint)
                continue
            self.pendientes[mint] = ahora

    def _fotografiar(self, mint: str) -> None:
        self.pendientes.pop(mint, None)
        foto = _foto(mint)
        if foto is None:
            return
        foto["pico"] = 0.0
        foto["suelo"] = 0.0
        self.siguiendo[mint] = foto

    def _actualizar(self, mint: str) -> None:
        """Apunta el maximo y el minimo alcanzados desde la foto."""
        d = self.siguiendo[mint]
        precio = volumen.precio_en_vivo(mint, 30)
        if precio:
            cambio = precio / d["precio"] - 1
            d["pico"] = max(d["pico"], cambio)
            d["suelo"] = min(d["suelo"], cambio)

    def cerrar_las_vencidas(self) -> None:
        ahora = time.time()
        for mint in [
            m for m, d in self.siguiendo.items() if ahora - d["t"] >= SEGUIMIENTO
        ]:
            d = self.siguiendo.pop(mint)
            d.update(_desenlace(mint, d["precio"]))
            # Los peldanos de la escalera actual, para poder preguntar
            # directamente "cuantas habrian cobrado el TP1".
            d["llego_tp1"] = d["pico"] >= TP1
            d["llego_tp2"] = d["pico"] >= TP2
            d["llego_tp3"] = d["pico"] >= TP3
            d["toco_stop"] = d["suelo"] <= STOP
            SALIDA.parent.mkdir(parents=True, exist_ok=True)
            with SALIDA.open("a", encoding="utf-8") as f:
                f.write(json.dumps(d) + "\n")
            self.escritas += 1

    def limpiar(self) -> None:
        """Las que nunca llegaron a la edad de decision se olvidan."""
        corte = time.time() - EDAD_DECISION * 4
        for mint in [m for m, t in self.pendientes.items() if t < corte]:
            self.pendientes.pop(mint, None)


async def _escuchar(wss: str, minutos: float) -> None:
    cazador = Cazador()
    fin = time.time() + minutos * 60 if minutos > 0 else 0.0
    ultimo_aviso = 0.0
    async with websockets.connect(wss, ping_interval=20) as ws:
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [{"mentions": [PUMP]}, {"commitment": "processed"}],
                }
            )
        )
        await ws.recv()
        print(
            f"Escuchando pump.fun. Foto a los {EDAD_DECISION:.0f}s, "
            f"seguimiento {SEGUIMIENTO / 60:.0f} min."
        )
        print(f"Se va escribiendo en {SALIDA}. Ctrl+C para parar.\n")
        while True:
            if fin and time.time() >= fin:
                print("\nTiempo cumplido.")
                return
            try:
                crudo = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                continue
            datos = json.loads(crudo)
            if datos.get("method") != "logsNotification":
                continue
            resultado = datos["params"]["result"]
            tocadas = volumen.registrar_logs(
                resultado.get("value", {}).get("logs", []),
                slot=resultado.get("context", {}).get("slot"),
            )
            cazador.ver(tocadas)
            cazador.cerrar_las_vencidas()
            cazador.limpiar()
            if time.time() - ultimo_aviso >= AVISO_SEG:
                ultimo_aviso = time.time()
                print(
                    f"  {cazador.escritas} monedas apuntadas · "
                    f"{len(cazador.siguiendo)} en seguimiento · "
                    f"{len(cazador.pendientes)} esperando",
                    flush=True,
                )


def main() -> None:
    """Escucha la cadena y apunta senal + desenlace de cada moneda."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(str(Path(".env").resolve()))
    wss = os.getenv("SOLANA_NODE_WSS_ENDPOINT", "").strip()
    if not wss:
        print("Falta SOLANA_NODE_WSS_ENDPOINT en el .env.")
        return
    minutos = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    try:
        asyncio.run(_escuchar(wss, minutos))
    except KeyboardInterrupt:
        print("\nParado.")
    print(f"Lo apuntado esta en {SALIDA}")


if __name__ == "__main__":
    main()
