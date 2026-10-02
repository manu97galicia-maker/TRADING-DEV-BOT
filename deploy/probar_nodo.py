"""Dice en 20 segundos si un nodo sirve para este bot, sin tocar nada.

PARA QUE EXISTE. El 30-sep el bot dejo de comprar y habia dos sospechosos que
llevan a sitios opuestos:

  - el NODO esta caido o devuelve 429, y el bot esta ciego;
  - el nodo va bien y lo que corta es un FILTRO (la vela verde).

Los dos se ven igual desde fuera --"no compra"-- y arreglar el que no es
cuesta dias. Ya paso el 21 y el 22-sep: se tocaron filtros durante dos dias
mientras el problema era un 429. Esto separa los dos casos sin adivinar.

QUE HACE. Se conecta al websocket, pide el chorro de operaciones de pump.fun
--el mismo que usa la estrategia de la curva-- y cuenta cuantos mensajes
llegan en 20 segundos. Nada mas: no compra, no vende, no toca el bot ni el
`.env`. Se puede lanzar con el bot corriendo.

COMO SE USA

    uv run deploy/probar_nodo.py            # prueba el nodo que usa el bot
    uv run deploy/probar_nodo.py wss://...  # prueba otro cualquiera

COMO SE LEE

    mas de 100 mensajes  ->  el nodo SIRVE. Si el bot no compra, no es esto.
    entre 1 y 100        ->  llega a cuentagotas: no vale para la curva.
    0, o FALLO, o 429    ->  el nodo esta roto o agotado. Es esto.

LA CLAVE NO SE IMPRIME NUNCA. La URL de Helius lleva la api-key dentro, y
estas pantallas acaban en capturas por WhatsApp. Se enseña tapada.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

#: El programa de pump.fun. Es el mismo que mira `curva96_source.py`, asi que
#: lo que cuenta este script es exactamente lo que alimenta a la estrategia.
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

#: Segundos contando mensajes. 20 bastan: en un nodo sano llegan cientos.
VENTANA = 20.0

#: A partir de aqui el chorro se considera sano.
SANO = 100

#: Por debajo de aqui, pero por encima de 0, es un goteo inservible.
GOTEO = 1


def _tapar(url: str) -> str:
    """La URL sin la clave, para poder enseñar la pantalla sin regalarla."""
    if "api-key=" in url:
        cabeza, _, _resto = url.partition("api-key=")
        return f"{cabeza}api-key=********"
    # Otros proveedores meten la clave en el camino: se deja solo el dominio.
    esquema, sep, resto = url.partition("://")
    if not sep:
        # No es una URL. Se dice, en vez de inventar una que parezca una.
        return f"(no parece una URL: {len(url)} caracteres)"
    dominio = resto.split("/")[0]
    return (
        f"{esquema}://{dominio}/********" if "/" in resto else f"{esquema}://{dominio}"
    )


def _del_env() -> str | None:
    """El `SOLANA_NODE_WSS_ENDPOINT` del .env, sin importar dotenv.

    Se lee a mano a proposito: el `.env` tiene la clave privada de la wallet
    dentro y este script solo debe mirar UNA linea de el.
    """
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.is_file():
        return None
    for linea in env.read_text(encoding="utf-8", errors="replace").splitlines():
        limpia = linea.strip()
        if limpia.startswith("SOLANA_NODE_WSS_ENDPOINT="):
            valor = limpia.split("=", 1)[1].strip().strip('"').strip("'")
            return valor or None
    return None


async def _medir(url: str) -> int:
    """Mensajes recibidos en la ventana. -1 si no se pudo ni conectar."""
    try:
        async with websockets.connect(url, ping_interval=20, open_timeout=15) as ws:
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
            saludo = await asyncio.wait_for(ws.recv(), timeout=15)
            print(f"  saludo del nodo: {str(saludo)[:120]}")
            if "error" in str(saludo).lower():
                print("  !! el nodo ha contestado un ERROR al suscribirse")

            vistos = 0
            fin = time.monotonic() + VENTANA
            while time.monotonic() < fin:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=6)
                    vistos += 1
                except TimeoutError:
                    break
            return vistos
    except Exception as e:  # noqa: BLE001 - aqui interesa CUALQUIER fallo
        print(f"  FALLO al conectar: {type(e).__name__}: {e}")
        return -1


def _veredicto(vistos: int) -> None:
    print()
    if vistos < 0:
        print("  NO SE PUDO CONECTAR. El nodo esta caido, la URL esta mal, o")
        print("  el plan se ha agotado. Este nodo NO sirve.")
    elif vistos == 0:
        print("  CONECTA PERO NO MANDA NADA. Suele ser el plan agotado: deja")
        print("  abrir el websocket y luego no entrega. Este nodo NO sirve.")
    elif vistos < SANO:
        print(f"  SOLO {vistos} MENSAJES EN {VENTANA:.0f} s. Llega a cuentagotas.")
        print("  La estrategia de la curva necesita ver TODAS las operaciones")
        print("  para saber por donde va cada moneda. Con esto va ciega a ratos.")
    else:
        print(f"  {vistos} MENSAJES EN {VENTANA:.0f} s: el nodo SIRVE.")
        print("  Si el bot no compra, el problema NO es el nodo. Mira los")
        print("  filtros con:  porque 60")
    print()


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else _del_env()
    if not url:
        print("No hay URL. Pasa una, o pon SOLANA_NODE_WSS_ENDPOINT en el .env:")
        print("  uv run deploy/probar_nodo.py wss://api.mainnet-beta.solana.com")
        raise SystemExit(1)
    if not url.startswith("ws"):
        print(f"Eso no es un websocket: {_tapar(url)}")
        print("Tiene que empezar por wss:// (no https://).")
        raise SystemExit(1)

    print()
    print(f"Probando: {_tapar(url)}")
    print(f"Contando operaciones de pump.fun durante {VENTANA:.0f} segundos...")
    print()
    _veredicto(await _medir(url))


if __name__ == "__main__":
    asyncio.run(main())
