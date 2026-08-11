"""Que hay de verdad en el segundo uno de una moneda de pump.fun.

Manu quiere comprar en el primer segundo manteniendo la banda de market cap.
La pregunta es si eso existe: cuanto volumen y cuanto market cap tiene una
moneda recien nacida al segundo 1, 2 y 3. Si en el segundo 1 casi ninguna llega
a 4.000 $ de market cap, no hay filtro que valga -- hay que elegir entre entrar
pronto o entrar con la moneda ya demostrada.

Escucha el mismo flujo que el bot (los logs del programa de pump.fun por
websocket) y usa el mismo contador de volumen en memoria, asi que lo que mide
es EXACTAMENTE lo que el bot ve en el momento de decidir. No es una estimacion
sacada de velas de 30 segundos.

No compra nada: solo observa y saca la distribucion.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from monitoring.universal_logs_listener import UniversalLogsListener  # noqa: E402
from monitoring.volumen import volumen_sol, compras, titulares  # noqa: E402
from interfaces.core import Platform  # noqa: E402

_SUPPLY = 1_000_000_000
_SOL_USD = 75.0
MINUTOS = 5
CORTES = (1.0, 2.0, 3.0, 5.0, 8.0)

muestras: dict[float, list[tuple[float, float]]] = {c: [] for c in CORTES}
vistas = 0


async def vigilar(token_info) -> None:
    """Mide una moneda en cada corte de tiempo desde que nace."""
    global vistas
    vistas += 1
    mint = str(token_info.mint)
    t0 = time.monotonic()
    for corte in CORTES:
        await asyncio.sleep(max(0.0, corte - (time.monotonic() - t0)))
        vol = volumen_sol(mint)
        # El market cap se estima con el volumen que ha entrado en la curva,
        # que es lo unico disponible sin gastar una llamada RPC por moneda.
        # Es la misma aproximacion que usa el bot antes de mirar la curva.
        muestras[corte].append((vol, compras(mint)))


async def main() -> None:
    listener = UniversalLogsListener(
        wss_endpoint=os.getenv("SOLANA_NODE_WSS_ENDPOINT"),
        platforms=[Platform.PUMP_FUN],
    )
    print(f"Escuchando {MINUTOS} minutos de lanzamientos reales...\n")
    tarea = asyncio.create_task(
        listener.listen_for_tokens(
            lambda t: asyncio.create_task(vigilar(t)),
            match_string=None,
            creator_address=None,
        )
    )
    await asyncio.sleep(MINUTOS * 60)
    tarea.cancel()

    print(f"Monedas observadas: {vistas}\n")
    print("%-10s %-28s %-22s %s" % (
        "segundo", "volumen SOL (mediana/p90/max)", "compras (mediana/max)",
        "llegan a 30 SOL"))
    print("-" * 90)
    for corte in CORTES:
        datos = [m for m in muestras[corte] if m]
        if not datos:
            continue
        vols = sorted(v for v, _ in datos)
        comps = sorted(c for _, c in datos)
        p90 = vols[int(len(vols) * 0.9)] if vols else 0
        pasan = sum(1 for v in vols if v >= 30)
        print("%-10s %-28s %-22s %d de %d (%.1f%%)" % (
            "%.0f s" % corte,
            "%.1f / %.1f / %.1f" % (statistics.median(vols), p90, max(vols)),
            "%.0f / %.0f" % (statistics.median(comps), max(comps)),
            pasan, len(vols), pasan / len(vols) * 100))

    print()
    print("LECTURA: si en el segundo 1 la mediana de volumen es de unos pocos")
    print("SOL, exigir 30 en esa ventana es pedir algo que casi no ocurre.")


if __name__ == "__main__":
    asyncio.run(main())
