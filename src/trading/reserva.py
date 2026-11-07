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

YA COMPRADA (7-nov). Los diez minutos no bastaban: la estrategia de 6k
compro una moneda a las 9:22, salio a las 9:24, la reserva caduco a las 9:32
y la de 15,3k volvio a comprarla a las 9:37. Manu: "cuidado con esto, me
expones en dos compras". Asi que una compra HECHA deja otra marca aparte,
`comprada_<mint>.json`, que dura ``COMPRADA_SEG`` (24 h): mientras exista,
ningun bot vuelve a comprar esa moneda. La caducidad corta de arriba sigue
para lo suyo (un bot muerto a medias no bloquea la moneda).
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

#: Segundos durante los que una moneda ya comprada no se vuelve a comprar.
COMPRADA_SEG = 24 * 3600

#: Carpeta de reservas, compartida por los tres bots.
CARPETA = Path(__file__).resolve().parents[2] / "reservas"

#: Cada cuantas reservas se barre la carpeta. Sin esto se acumulan miles de
#: ficheros: el bot ve cientos de monedas por minuto y cada una deja el suyo.
_CADA = 200
_cuenta = 0


def _ruta(mint: str) -> Path:
    return CARPETA / ("claim_%s.json" % mint)


def _ruta_comprada(mint: str) -> Path:
    return CARPETA / ("comprada_%s.json" % mint)


def _comprada_por(mint: str) -> str | None:
    """Quien la compro, si se compro hace menos de COMPRADA_SEG; si no, None."""
    p = _ruta_comprada(mint)
    try:
        if time.time() - p.stat().st_mtime > COMPRADA_SEG:
            return None
    except OSError:
        return None
    try:
        return str(json.loads(p.read_text(encoding="utf-8")).get("bot", "?"))
    except Exception:  # noqa: BLE001
        return "?"


def marca_comprada(mint: str, quien: str) -> None:
    """Apunta que ESTE bot ha comprado la moneda: nadie la recompra en 24 h."""
    try:
        CARPETA.mkdir(parents=True, exist_ok=True)
        _ruta_comprada(mint).write_text(
            json.dumps({"bot": quien, "ts": time.time()}), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("RESERVA: no pude marcar %s como comprada (%s)", mint[:10], exc)


def _barre() -> None:
    """Borra las reservas caducadas. Se llama de vez en cuando, no siempre."""
    global _cuenta
    _cuenta += 1
    if _cuenta % _CADA:
        return
    borradas = 0
    for patron, seg in (("claim_*.json", CADUCIDAD_SEG), ("comprada_*.json", COMPRADA_SEG)):
        corte = time.time() - seg
        try:
            for f in CARPETA.glob(patron):
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
        ya = _comprada_por(mint)
        if ya is not None:
            logger.info(
                "RESERVA: %s ya la compro %s en las ultimas 24 h; no la recompro",
                mint[:10], ya,
            )
            return False
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
