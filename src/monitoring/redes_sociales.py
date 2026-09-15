# ruff: noqa: ANN401 -- `enlaces` llega crudo del feed y viene en dos
# formatos distintos (lista de DexScreener, dict de DEXTools). Tiparlo mas
# estrecho obligaria a quien llama a traducir, que es justo lo que este
# modulo existe para evitar.
"""Detecta cuando una moneda ESTRENA redes sociales.

Por que es una senal. Un dev que anade Twitter o web a su moneda esta
invirtiendo tiempo en ella: es lo contrario de lanzar y desaparecer. No
garantiza nada, pero separa al que se molesta del que no.

Por que hay que recordar. Ni DexScreener ni DEXTools avisan de "acaba de
anadir redes": dan la lista de enlaces que hay AHORA. La unica forma de saber
que han cambiado es guardar una foto y compararla. Eso es todo lo que hace
este modulo.

Por que se guarda en disco. Sin persistir, al reiniciar el bot toda moneda
con redes pareceria estrenarlas, y compraria de golpe decenas de monedas
viejas por una senal que en realidad es de hace dias.

CUIDADO CON EL SOLAPAMIENTO: pagar el perfil de DexScreener ES, en la
practica, poner las redes. Asi que muchas monedas daran las dos senales a la
vez. Quien use esto tiene que decidir que hace en ese caso; aqui solo se
informa de cual es.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

RUTA = Path("trades") / "redes_vistas.json"
#: Monedas recordadas. Pasado el tope se tiran las mas antiguas.
_MAX = 20_000
#: Redes que cuentan. Un icono o una descripcion no son una red social.
_TIPOS = ("twitter", "telegram", "website", "discord", "x", "web")


def _normaliza(enlaces: Any) -> frozenset[str]:
    """Saca el conjunto de redes de lo que devuelva el feed.

    DexScreener las da como ``links: [{type|label, url}]``; DEXTools como un
    ``socialInfo: {twitter: ..., telegram: ...}``. Se aceptan las dos formas
    porque la senal es la misma y quien llama no tiene por que traducir.
    """
    fuera: set[str] = set()
    if isinstance(enlaces, dict):
        for clave, valor in enlaces.items():
            if valor and str(valor).strip() and str(clave).lower() in _TIPOS:
                fuera.add(str(clave).lower())
        return frozenset(fuera)
    if not isinstance(enlaces, list):
        return frozenset()
    for enlace in enlaces:
        if not isinstance(enlace, dict):
            continue
        tipo = str(enlace.get("type") or enlace.get("label") or "").lower().strip()
        if not enlace.get("url"):
            continue
        if tipo in _TIPOS:
            fuera.add(tipo)
        elif tipo:
            # Un tipo desconocido con url sigue siendo una red: se guarda tal
            # cual en vez de tirarlo, que es lo que haria perder senal cuando
            # DexScreener anada una red nueva que aqui no esta listada.
            fuera.add(tipo)
    return frozenset(fuera)


class RedesVistas:
    """Recuerda que redes tenia cada moneda para saber cuando estrena una."""

    def __init__(self, ruta: Path = RUTA) -> None:
        self._ruta = ruta
        self._vistas: dict[str, list[str]] = {}
        self._cuando: dict[str, float] = {}
        self._cargar()

    def _cargar(self) -> None:
        try:
            datos = json.loads(self._ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(datos, dict):
            return
        self._vistas = {
            k: list(v)
            for k, v in (datos.get("redes") or {}).items()
            if isinstance(v, list)
        }
        self._cuando = {k: float(v) for k, v in (datos.get("cuando") or {}).items()}
        logger.info("Redes sociales: %d monedas recordadas", len(self._vistas))

    def guardar(self) -> None:
        """Escribe la foto en disco. No lanza: perderla no puede tumbar nada."""
        try:
            self._ruta.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ruta.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"redes": self._vistas, "cuando": self._cuando}),
                encoding="utf-8",
            )
            tmp.replace(self._ruta)
        except OSError:
            logger.debug("no pude guardar las redes vistas", exc_info=True)

    def _podar(self) -> None:
        if len(self._vistas) <= _MAX:
            return
        viejas = sorted(self._cuando, key=lambda m: self._cuando.get(m, 0))
        for mint in viejas[: len(self._vistas) - _MAX]:
            self._vistas.pop(mint, None)
            self._cuando.pop(mint, None)

    def novedades(self, mint: str, enlaces: Any) -> frozenset[str]:
        """Redes que esta moneda NO tenia la ultima vez que se la miro.

        La primera vez que se ve una moneda NO cuenta como estreno: solo se
        apunta lo que tiene. Si contara, al arrancar el bot compraria de golpe
        todas las monedas con redes del feed, por algo que puede ser de hace
        dias.

        Returns:
            Las redes nuevas, o un conjunto vacio si no hay novedad.
        """
        ahora = _normaliza(enlaces)
        self._cuando[mint] = time.time()
        if mint not in self._vistas:
            self._vistas[mint] = sorted(ahora)
            self._podar()
            return frozenset()
        antes = frozenset(self._vistas[mint])
        nuevas = ahora - antes
        if nuevas:
            self._vistas[mint] = sorted(ahora | antes)
            self._podar()
        return nuevas
