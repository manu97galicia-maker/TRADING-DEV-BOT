"""Redes sociales de la moneda: si las tiene y si son SUYAS.

Cada moneda de pump.fun trae un `uri` en el CreateEvent que apunta a un JSON
con `twitter`, `telegram` y `website`. Ese JSON esta publicado antes de que la
moneda exista, asi que se puede leer en el mismo instante del lanzamiento.

QUE SE ENCONTRO AL MIRARLO (muestra de lanzamientos reales, 5-ago-2026):

    x.com/JackManell/status/2084449421186539     <- tuit de otro
    twitter.com/jackbutcher/status/208444946     <- tuit de otro
    x.com/Cointelegraph/status/20844494818       <- tuit de otro
    x.com/jncquant/status/208444408275186901     <- tuit de otro
    x.com/PandacoinOG                            <- cuenta propia

Casi todas las "redes sociales" de una moneda nueva son un enlace al tuit de
OTRA persona: el que lanza engancha su moneda a un post que ya tiene alcance
para aparentar respaldo. No es la red social de la moneda, es la de un tercero
que no sabe que existe -- cuando ese post deje de moverse no queda nada.

Original aqui significa las dos cosas a la vez:

  1. Hay un enlace a una CUENTA (perfil), no a un tuit suelto ni a una busqueda.
     Una cuenta se puede seguir y responde de alguien; un /status/ ajeno no.
  2. Esa cuenta no la ha usado ya otra moneda en las ultimas horas. El que
     lanza veinte clones reutiliza el mismo perfil en todos.

LO QUE ESTO NO PUEDE SABER: si la cuenta es de verdad de quien lanza, si tiene
seguidores o si la creo ayer. Para eso haria falta consultar X, que ni contesta
gratis ni contesta en 3 segundos. Se queda en "hay una cuenta propia y nadie la
habia usado", que es lo que si se puede comprobar sin pagar latencia.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import urlparse

import aiohttp

from utils.logger import get_logger

logger = get_logger(__name__)

#: ipfs.io devuelve 403 a todo (comprobado desde dos IPs distintas el 5-ago),
#: y la mitad de las monedas publican ahi. Estas dos si contestan: filebase
#: tardo 189 ms y dweb.link 365 ms sobre el mismo CID.
_PASARELAS = (
    "https://ipfs.filebase.io/ipfs/",
    "https://dweb.link/ipfs/",
)
_ROTAS = ("ipfs.io",)

_UA = {"User-Agent": "Mozilla/5.0"}

#: enlace de cuenta ya visto -> momento. Sirve para cazar al que relanza la
#: misma moneda con el mismo perfil una y otra vez.
_vistas: dict[str, float] = {}
_VIDA_VISTA = 6 * 3600.0

#: Rutas de X que NO son una cuenta: son un tuit concreto, una comunidad o una
#: busqueda. Todas apuntan a algo de otro o a algo que no se puede seguir.
_NO_ES_CUENTA = re.compile(
    r"/(status|statuses|i/|search|hashtag|intent|share)", re.IGNORECASE
)
#: Handles que no identifican a nadie (paginas propias de la plataforma).
_GENERICOS = {"home", "explore", "i", "search", "pumpdotfun", "pumpfun"}


def _urls_a_probar(uri: str) -> list[str]:
    """El propio uri y, si su pasarela no contesta, otras con el mismo CID."""
    urls = [uri]
    try:
        host = urlparse(uri).netloc
    except ValueError:
        return urls
    cid = uri.rstrip("/").rsplit("/", 1)[-1]
    if cid and (any(r in host for r in _ROTAS) or "/ipfs/" in uri):
        urls = [p + cid for p in _PASARELAS] if any(r in host for r in _ROTAS) else [
            uri, *[p + cid for p in _PASARELAS]
        ]
    return urls


async def leer(uri: str, timeout: float = 1.5) -> dict:
    """Baja el JSON de la moneda. Devuelve {} si no se puede (nunca lanza)."""
    if not uri:
        return {}
    fin = time.monotonic() + timeout
    async with aiohttp.ClientSession(headers=_UA) as ses:
        for url in _urls_a_probar(uri):
            queda = fin - time.monotonic()
            if queda <= 0.05:
                break
            try:
                async with ses.get(
                    url, timeout=aiohttp.ClientTimeout(total=queda)
                ) as r:
                    if r.status != 200:
                        continue
                    return json.loads(await r.text())
            except Exception:  # noqa: BLE001
                continue
    return {}


def _cuenta(enlace: str) -> str | None:
    """Devuelve la cuenta (en minusculas) si el enlace es un PERFIL.

    None si es un tuit de otro, una comunidad, una busqueda o no es X.
    """
    if not enlace or not isinstance(enlace, str):
        return None
    e = enlace.strip()
    if not e.startswith("http"):
        e = "https://" + e
    try:
        p = urlparse(e)
    except ValueError:
        return None
    host = p.netloc.lower().removeprefix("www.")
    ruta = p.path
    if host in ("x.com", "twitter.com"):
        if _NO_ES_CUENTA.search(ruta):
            return None
        handle = ruta.strip("/").split("/")[0].lower()
        if not handle or handle in _GENERICOS:
            return None
        return f"x:{handle}"
    if host in ("t.me", "telegram.me"):
        grupo = ruta.strip("/").split("/")[0].lower()
        # t.me/canal/1234 es un mensaje suelto dentro de un canal ajeno.
        if not grupo or len(ruta.strip("/").split("/")) > 1:
            return None
        return f"tg:{grupo}"
    return None


def _limpiar() -> None:
    corte = time.time() - _VIDA_VISTA
    for k in [k for k, t in _vistas.items() if t < corte]:
        _vistas.pop(k, None)


def evaluar(meta: dict, apuntar: bool = True) -> tuple[bool, str]:
    """(¿son originales?, motivo legible para el log).

    `apuntar=False` sirve para medir sin ensuciar la memoria de vistas.
    """
    if not meta:
        return False, "no se pudo leer el metadata"
    enlaces = [meta.get("twitter"), meta.get("telegram")]
    if not any(enlaces) and not meta.get("website"):
        return False, "sin redes"
    cuentas = [c for c in (_cuenta(e) for e in enlaces) if c]
    if not cuentas:
        # Tiene enlaces, pero ninguno es una cuenta suya.
        suelto = next((e for e in enlaces if e), meta.get("website"))
        return False, f"redes de otro ({str(suelto)[:60]})"
    _limpiar()
    ahora = time.time()
    for c in cuentas:
        anterior = _vistas.get(c)
        if anterior is not None and ahora - anterior < _VIDA_VISTA:
            hace = (ahora - anterior) / 60
            return False, f"cuenta reutilizada ({c}, hace {hace:.0f} min)"
    if apuntar:
        for c in cuentas:
            _vistas[c] = ahora
    return True, f"cuenta propia sin usar ({cuentas[0]})"


async def originales(uri: str, timeout: float = 1.5) -> tuple[bool, str]:
    """Atajo: baja el metadata y lo evalua."""
    return evaluar(await leer(uri, timeout))


def precargar(vistas: dict[str, float]) -> None:
    """Restaura las cuentas ya vistas tras un reinicio."""
    _vistas.update(vistas)


def volcado() -> dict[str, float]:
    _limpiar()
    return dict(_vistas)


__all__ = ["evaluar", "leer", "originales", "precargar", "volcado"]


if __name__ == "__main__":  # prueba manual rapida
    async def _p() -> None:
        for u in __import__("sys").argv[1:]:
            print(u, await originales(u))
    asyncio.run(_p())
