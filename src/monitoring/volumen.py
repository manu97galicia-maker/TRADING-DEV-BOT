"""Contador de volumen NEGOCIADO por moneda, leido del stream de logs.

Por que existe esto: ni GMGN ni la API de pump.fun dan volumen para una moneda
de 20 segundos -- se comprobo campo por campo el 3-ago-2026 y simplemente no
esta indexado todavia. Lo unico disponible era el SOL de la curva, que es el
dinero NETO dentro, no lo negociado.

La diferencia importa. El market cap y el SOL de la curva son la MISMA magnitud
en una bonding curve, asi que pedir "market cap entre 3.000 y 4.200" ya fija el
neto entre 6,6 y 13,2 SOL: no se puede exigir ademas un neto mayor. El volumen
si es otra cosa -- suma compras Y ventas -- y puede ser varias veces el neto si
la moneda ha rotado.

Aqui se cuenta de verdad: el bot ya esta suscrito a TODOS los logs del programa
de pump.fun (`logsSubscribe` con `mentions`), pero hasta ahora solo miraba los
de creacion y tiraba el resto. Cada operacion emite un `TradeEvent` con el
`sol_amount` exacto, asi que basta con no tirarlos.

Es ademas el unico dato de dinero que NO se puede falsear desde fuera: sale de
la cadena, no de un indexador.
"""

from __future__ import annotations

import base64
import hashlib
import struct
import time
from collections import defaultdict

from utils.logger import get_logger

logger = get_logger(__name__)

#: Discriminador de TradeEvent = primeros 8 bytes de sha256("event:TradeEvent").
#: Anchor lo antepone a los datos del evento dentro del "Program data:" del log.
DISC_TRADE = hashlib.sha256(b"event:TradeEvent").digest()[:8]

#: mint -> [(momento, sol_negociado), ...]. Se guarda con marca de tiempo para
#: poder preguntar "cuanto se movio en los primeros N segundos" y no solo el
#: acumulado total.
_ops: dict[str, list[tuple[float, float]]] = defaultdict(list)

#: Sin esto la memoria crece sin limite: pump.fun lanza miles de monedas al dia
#: y el bot esta pensado para correr semanas seguidas.
_MAX_MINTS = 4000
_VIDA = 300.0  # segundos que interesa recordar de cada moneda


def _limpiar() -> None:
    if len(_ops) <= _MAX_MINTS:
        return
    corte = time.time() - _VIDA
    muertos = [m for m, v in _ops.items() if not v or v[-1][0] < corte]
    for m in muertos:
        _ops.pop(m, None)
    # Si aun asi no baja (mucha moneda viva a la vez), se tira lo mas antiguo.
    if len(_ops) > _MAX_MINTS:
        for m in sorted(_ops, key=lambda k: _ops[k][-1][0])[: len(_ops) - _MAX_MINTS]:
            _ops.pop(m, None)


def registrar_logs(logs: list[str]) -> None:
    """Apunta las operaciones que aparezcan en estos logs. No lanza nunca.

    Se llama para CADA notificacion del websocket, incluidas las que no son
    creaciones, asi que tiene que ser barata y silenciosa: un fallo aqui no
    puede tumbar la deteccion de monedas nuevas.
    """
    ahora = time.time()
    for linea in logs:
        if "Program data:" not in linea:
            continue
        try:
            crudo = base64.b64decode(linea.split("Program data:", 1)[1].strip())
        except Exception:  # noqa: BLE001
            continue
        if len(crudo) < 8 + 32 + 8 + 8 + 1 or crudo[:8] != DISC_TRADE:
            continue
        try:
            cuerpo = crudo[8:]
            mint = _b58(cuerpo[:32])
            sol_lamports = struct.unpack_from("<Q", cuerpo, 32)[0]
            _ops[mint].append((ahora, sol_lamports / 1e9))
        except Exception:  # noqa: BLE001
            continue
    _limpiar()


def volumen_sol(mint: str, segundos: float = 0.0) -> float:
    """SOL negociado en esa moneda. Si `segundos` > 0, solo los ultimos N."""
    ops = _ops.get(mint)
    if not ops:
        return 0.0
    if segundos <= 0:
        return sum(s for _t, s in ops)
    corte = time.time() - segundos
    return sum(s for t, s in ops if t >= corte)


def operaciones(mint: str) -> int:
    return len(_ops.get(mint) or ())


_ALFA = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58(datos: bytes) -> str:
    n = int.from_bytes(datos, "big")
    salida = ""
    while n > 0:
        n, resto = divmod(n, 58)
        salida = _ALFA[resto] + salida
    # Cada byte cero por delante es un '1' en base58.
    for b in datos:
        if b != 0:
            break
        salida = "1" + salida
    return salida or "1"
