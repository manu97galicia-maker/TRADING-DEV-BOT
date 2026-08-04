"""Señales de GMGN cacheadas en memoria, refrescadas en segundo plano.

Por que no se consulta por moneda: `gmgn-cli` tarda 1,3 s por llamada (6,5 s si
coinciden varias a la vez en un droplet de 1 GB, medido el 4-ago). Preguntar por
cada moneda que aparece haria imposible comprar en los primeros segundos.

En vez de eso se pide la lista completa de señales cada pocos segundos -- una
sola llamada devuelve las 50 mas recientes de cada tipo -- y el filtro solo
consulta un diccionario en memoria. Coste por moneda: cero.

QUE MIDE CADA TIPO, medido el 4/5-ago sobre ~50 señales de cada uno comparando
el market cap al saltar contra el maximo que alcanzo despues:

    tipo 12   2,34x mediano   92% subio +30%   salta a ~$27.700
    tipo  6   2,16x           86%              ~$10.700
    tipo  5   2,12x           84%              ~$31.000
    tipo 11   2,04x           84%              ~$13.500
    tipo 18   2,00x           86%              ~$4.600   <-- el util aqui
    tipo  7   1,00x           26%              ~$2.600   <-- el peor

El 18 es el que sirve para esta estrategia: los demas rinden parecido o algo
mas, pero saltan a $10.000-$40.000 de market cap, donde ya no se entra barato.
El 18 salta en la banda de compra (44% de las veces por debajo de $4.000) y aun
asi duplica de mediana.

AVISO sobre esos multiplos: el `ath` es el maximo HISTORICO, y no se puede saber
si ocurrio antes o despues de que saltara la señal. Por construccion ath >=
trigger_mc, asi que el numero absoluto esta inflado. Lo que si vale es la
COMPARACION entre tipos, porque todos arrastran el mismo sesgo.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess  # noqa: S404
import time

from utils.logger import get_logger

logger = get_logger(__name__)

#: mint -> {tipo: momento_en_que_se_vio}
_senales: dict[str, dict[int, float]] = {}
_ultimo_refresco = 0.0
_VIDA = 600.0  # una señal deja de contar a los 10 minutos


def _exe() -> str | None:
    return shutil.which("gmgn-cli") or shutil.which("gmgn-cli.cmd")


def _consultar(tipo: int) -> list[dict]:
    exe = _exe()
    if not exe:
        return []
    try:
        r = subprocess.run(  # noqa: S603
            [exe, "market", "signal", "--chain", "sol",
             "--signal-type", str(tipo), "--raw"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []
        d = json.loads(r.stdout)
        return d if isinstance(d, list) else []
    except Exception:  # noqa: BLE001
        return []


def _limpiar() -> None:
    corte = time.time() - _VIDA
    for mint in [m for m, v in _senales.items()
                 if all(t < corte for t in v.values())]:
        _senales.pop(mint, None)


async def refrescar(tipos: list[int], cada: float = 20.0) -> None:
    """Bucle de fondo: baja las señales y las deja en memoria.

    No lanza nunca: si GMGN falla, se conserva lo que ya habia y se reintenta.
    Un fallo aqui no puede tumbar el bot.
    """
    global _ultimo_refresco  # noqa: PLW0603
    logger.info("Señales GMGN: refrescando tipos %s cada %.0fs", tipos, cada)
    while True:
        for tipo in tipos:
            try:
                filas = await asyncio.to_thread(_consultar, tipo)
            except Exception:  # noqa: BLE001
                filas = []
            ahora = time.time()
            for s in filas:
                mint = str(s.get("token_address") or "")
                if mint:
                    _senales.setdefault(mint, {})[tipo] = ahora
            if filas:
                _ultimo_refresco = ahora
        _limpiar()
        await asyncio.sleep(cada)


def tiene_senal(mint: str, tipos: list[int]) -> int | None:
    """Devuelve el tipo de señal activa para ese mint, o None si no tiene.

    "Activa" = vista en los ultimos `_VIDA` segundos.
    """
    v = _senales.get(mint)
    if not v:
        return None
    corte = time.time() - _VIDA
    for t in tipos:
        if v.get(t, 0) >= corte:
            return t
    return None


def estado() -> tuple[int, float]:
    """(monedas con señal en memoria, segundos desde el ultimo refresco util)."""
    return len(_senales), (time.time() - _ultimo_refresco) if _ultimo_refresco else -1.0
