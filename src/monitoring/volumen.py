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

#: mint -> [(momento, sol_negociado, es_compra, wallet), ...]. Se guarda con
#: marca de tiempo para poder preguntar "cuanto se movio en los ultimos N
#: segundos", con el lado de la operacion para detectar un volcado en marcha
#: (si las VENTAS aplastan a las compras la moneda se esta cayendo AHORA y no
#: hay que esperar a que el precio toque el stop), y con la WALLET para poder
#: contar titulares distintos sin preguntarle a nadie.
_ops: dict[str, list[tuple[float, float, bool, str]]] = defaultdict(list)

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
            # Layout de TradeEvent:
            #   mint(32) sol_amount(8) token_amount(8) is_buy(1) user(32) ...
            es_compra = cuerpo[48] == 1
            wallet = _b58(cuerpo[49:81]) if len(cuerpo) >= 81 else ""
            _ops[mint].append((ahora, sol_lamports / 1e9, es_compra, wallet))
        except Exception:  # noqa: BLE001
            continue
    _limpiar()


def volumen_sol(mint: str, segundos: float = 0.0) -> float:
    """SOL negociado en esa moneda. Si `segundos` > 0, solo los ultimos N."""
    ops = _ops.get(mint)
    if not ops:
        return 0.0
    if segundos <= 0:
        return sum(s for _t, s, _b, _w in ops)
    corte = time.time() - segundos
    return sum(s for t, s, _b, _w in ops if t >= corte)


def operaciones(mint: str) -> int:
    """Numero de transacciones vistas en esa moneda.

    Cuenta CADA TradeEvent, tambien los que van en un mismo bloque o en un
    mismo bundle: se leen del log de la cadena uno a uno, no agrupados.
    """
    return len(_ops.get(mint) or ())


def compras(mint: str) -> int:
    """Numero de compras vistas en esa moneda, sin contar ventas."""
    return sum(1 for _t, _s, es_compra, _w in (_ops.get(mint) or ()) if es_compra)


def compradores_unicos(mint: str) -> int:
    """Wallets distintas que han realizado al menos una compra."""
    return len(
        {
            wallet
            for _t, _s, es_compra, wallet in (_ops.get(mint) or ())
            if es_compra and wallet
        }
    )


def vendedores_unicos(mint: str) -> int:
    """Wallets distintas que han realizado al menos una venta."""
    return len(
        {
            wallet
            for _t, _s, es_compra, wallet in (_ops.get(mint) or ())
            if not es_compra and wallet
        }
    )


def titulares(mint: str) -> int:
    """Wallets distintas que han comprado y todavia no han soltado.

    Se cuenta del propio stream, no de un indexador: por cada wallet se suma
    lo comprado y se resta lo vendido, y cuenta como titular la que sale en
    positivo. Cuesta cero y esta disponible en el mismo instante, que es lo que
    hace falta para decidir a los 3 segundos -- GMGN a esa edad no conoce a 1
    de cada 3 monedas y ademas tarda 1,3 s en contestar.

    Lo que NO ve: quien recibiera los tokens por transferencia en vez de
    comprarlos. A los pocos segundos de vida eso no pasa en la practica.
    """
    ops = _ops.get(mint)
    if not ops:
        return 0
    neto: dict[str, float] = defaultdict(float)
    for _t, s, es_compra, w in ops:
        if w:
            neto[w] += s if es_compra else -s
    return sum(1 for v in neto.values() if v > 0)


def presion(mint: str, segundos: float = 3.0) -> tuple[float, float]:
    """(SOL comprados, SOL vendidos) en los ultimos `segundos`.

    Sirve para ver si la moneda se esta volcando AHORA. El stop-loss reacciona
    al precio, que es una consecuencia; esto mira la causa y llega antes.
    """
    ops = _ops.get(mint)
    if not ops:
        return (0.0, 0.0)
    corte = time.time() - segundos
    compra = vende = 0.0
    for t, s, es_compra, _w in ops:
        if t < corte:
            continue
        if es_compra:
            compra += s
        else:
            vende += s
    return (compra, vende)


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
