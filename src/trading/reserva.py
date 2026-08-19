"""Impide que dos bots compren la misma moneda a la vez.

EL PROBLEMA. Los tres bots (DEFINITIVA, ELITE, RAPIDA) son procesos distintos
que miran el mismo websocket y comparten wallet. Cuando una moneda cumple los
filtros de varios, la compran todos: el 18-ago BECKY salio con tres lineas
"CUMPLE" en el mismo segundo. Con `max_posiciones: 1` cada uno, eso convierte
tres apuestas independientes en una sola apuesta del triple de tamano. GAMBLE
costo 0,27 SOL repartidos entre dos bots por esto mismo.

COMO SE EVITA. Antes de comprar, el bot intenta crear un fichero
`claim_<mint>.json` con `O_EXCL`. El sistema de ficheros garantiza que solo uno
de los tres lo consigue, aunque lleguen en el mismo milisegundo: es una
operacion atomica del kernel, no un "mira si existe y luego crealo", que es
justo la comprobacion que falla cuando hay carrera.

El que gana compra; los otros dos se van a por otra moneda.

CADUCIDAD. Si un bot muere entre la reserva y la compra, el fichero se queda
ahi y bloquearia esa moneda para siempre. Por eso una reserva de mas de
``CADUCIDAD_SEG`` se considera basura y se pisa. Diez minutos es de sobra: la
compra tarda segundos.

Que la reserva siga puesta despues de cerrar la posicion es a proposito: evita
que otro bot vuelva a entrar en una moneda de la que se acaba de salir.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

#: Segundos tras los cuales una reserva se considera abandonada.
CADUCIDAD_SEG = 600

#: Carpeta de reservas, compartida por los tres bots.
CARPETA = Path(__file__).resolve().parents[2] / "reservas"

#: Cada cuantas reservas se barre la carpeta. Sin esto se acumulan miles de
#: ficheros: el bot ve cientos de monedas por minuto y cada una deja el suyo.
_CADA = 200
_cuenta = 0


def _ruta(mint: str) -> Path:
    return CARPETA / ("claim_%s.json" % mint)


def _barre() -> None:
    """Borra las reservas caducadas. Se llama de vez en cuando, no siempre."""
    global _cuenta
    _cuenta += 1
    if _cuenta % _CADA:
        return
    corte = time.time() - CADUCIDAD_SEG
    borradas = 0
    try:
        for f in CARPETA.glob("claim_*.json"):
            try:
                if f.stat().st_mtime < corte:
                    f.unlink(missing_ok=True)
                    borradas += 1
            except OSError:
                pass
    except OSError:
        return
    if borradas:
        logger.debug("RESERVA: barridas %d reservas caducadas", borradas)


def reserva(mint: str, quien: str) -> bool:
    """True si ESTE bot se queda la moneda; False si ya la tiene otro."""
    try:
        CARPETA.mkdir(parents=True, exist_ok=True)
        _barre()
        p = _ruta(mint)
        # Reserva caducada = bot muerto a medias. Se borra y se vuelve a
        # intentar, que es mejor que dejar la moneda bloqueada para siempre.
        try:
            if time.time() - p.stat().st_mtime > CADUCIDAD_SEG:
                p.unlink(missing_ok=True)
        except OSError:
            pass
        # O_EXCL: o lo crea este proceso, o falla. No hay hueco entre mirar y
        # crear, que es donde se cuelan las carreras.
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"bot": quien, "ts": time.time()}, fh)
        return True
    except FileExistsError:
        try:
            otro = json.loads(_ruta(mint).read_text(encoding="utf-8")).get("bot", "?")
        except Exception:  # noqa: BLE001
            otro = "?"
        logger.info("RESERVA: %s ya la lleva %s; no la compro", mint[:10], otro)
        return False
    except Exception as exc:  # noqa: BLE001
        # Si el sistema de ficheros falla, se deja comprar. Perder una moneda
        # buena por no poder escribir un fichero seria peor que el duplicado
        # que esto evita.
        logger.warning("RESERVA: no pude reservar %s (%s); dejo comprar", mint[:10], exc)
        return True


def suelta(mint: str) -> None:
    """Libera la moneda: la compra fallo y otro bot puede intentarla."""
    try:
        _ruta(mint).unlink(missing_ok=True)
    except OSError:
        pass
