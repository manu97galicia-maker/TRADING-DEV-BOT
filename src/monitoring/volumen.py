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

# Primer slot observado por moneda y numero de compras incluidas en ese mismo
# bloque. Se mantiene separado de ``_ops`` para no cambiar el formato de los
# contadores existentes.
_first_slot: dict[str, int] = {}
_first_slot_buys: dict[str, int] = defaultdict(int)

#: Wallets distintas que compraron en el bloque de creacion, y SOL que
#: metieron. Un bundle de pump and dump se monta justo ahi: muchas carteras
#: entrando en el MISMO bloque que la creacion, para tener el token barato
#: antes que nadie y soltarlo despues sobre quien llegue.
_first_slot_wallets: dict[str, set[str]] = defaultdict(set)
_first_slot_sol: dict[str, float] = defaultdict(float)

#: mint -> (momento, tokens_reales, sol_reales, tope_tokens_visto).
#:
#: Cada TradeEvent publica las reservas de la curva DESPUES de ejecutarse, asi
#: que saber por que punto de la curva va una moneda sale GRATIS del stream que
#: ya se esta leyendo. Preguntarselo al nodo seria una llamada por moneda y por
#: vuelta, y con miles de monedas vivas eso no cabe en el presupuesto de RPC.
_curva: dict[str, tuple[float, int, int, int]] = {}

#: Tokens que la curva de pump.fun pone a la venta al nacer (793,1 M con 6
#: decimales). El avance de la curva es cuanto de eso se ha vendido ya.
RESERVA_TOKENS_INICIAL = 793_100_000_000_000

#: Bytes minimos del cuerpo del evento para que lleguen las reservas reales.
#: Layout: mint(32) sol(8) tokens(8) is_buy(1) user(32) ts(8) v_sol(8)
#: v_tokens(8) real_sol(8) real_tokens(8) = 121.
_LARGO_RESERVAS = 121
_OFFSET_RESERVAS = 105

#: mint -> (momento, precio en SOL). El PRECIO EN VIVO, sacado del mismo
#: evento y gratis.
#:
#: POR QUE ESTO IMPORTA MAS DE LO QUE PARECE. En una bonding curve el precio
#: NO se mueve solo: solo cambia cuando alguien compra o vende. Y cada
#: operacion publica en su TradeEvent las reservas virtuales que quedan
#: DESPUES de ejecutarse, que es exactamente de donde sale el precio. O sea
#: que el precio que llega por el stream no es una estimacion ni una foto
#: vieja: es el precio exacto, en el instante en que se forma.
#:
#: Hasta ahora el bot lo tenia delante y no lo miraba: preguntaba al nodo una
#: vez por segundo, por cada posicion abierta. Eso es hasta un segundo tarde
#: --el suficiente para perderse un pico-- y cinco peticiones por segundo del
#: presupuesto del nodo con cinco posiciones.
#:
#: Las reservas VIRTUALES van en 89 (sol) y 97 (tokens); las que hay en 105
#: son las REALES, que sirven para el avance de la curva pero no para el
#: precio.
_precio: dict[str, tuple[float, float]] = {}
_OFFSET_VIRTUALES = 89
#: Decimales: el SOL va en lamports (9) y el token de pump.fun tiene 6.
_DEC_TOKEN = 10**6
_LAMPORTS = 10**9

#: Sin esto la memoria crece sin limite: pump.fun lanza miles de monedas al dia
#: y el bot esta pensado para correr semanas seguidas.
_MAX_MINTS = 4000
_VIDA = 300.0  # segundos que interesa recordar de cada moneda

#: Tope de operaciones guardadas por moneda. Una que llega al final de su curva
#: acumula decenas de miles y la lista era lo unico que crecia sin limite. Se
#: conservan las PRIMERAS (la creacion y su bloque, que es donde se reconoce un
#: bundle) y las ULTIMAS (lo que esta pasando ahora); lo de en medio no decide
#: nada. A los pocos minutos de vida, que es cuando miran los filtros de
#: siempre, ninguna moneda llega a este tope, asi que no les cambia nada.
_MAX_OPS_POR_MONEDA = 1200
_OPS_INICIALES = 200


def _limpiar() -> None:
    if len(_ops) <= _MAX_MINTS:
        return
    corte = time.time() - _VIDA
    muertos = [m for m, v in _ops.items() if not v or v[-1][0] < corte]
    for m in muertos:
        _olvidar(m)
    # Si aun asi no baja (mucha moneda viva a la vez), se tira lo mas antiguo.
    if len(_ops) > _MAX_MINTS:
        for m in sorted(_ops, key=lambda k: _ops[k][-1][0])[: len(_ops) - _MAX_MINTS]:
            _olvidar(m)


def _olvidar(mint: str) -> None:
    """Borra todo rastro de una moneda. Que no se escape ningun diccionario:
    uno olvidado aqui es una fuga de memoria que solo se nota tras dias."""
    _ops.pop(mint, None)
    _first_slot.pop(mint, None)
    _first_slot_buys.pop(mint, None)
    _first_slot_wallets.pop(mint, None)
    _first_slot_sol.pop(mint, None)
    _curva.pop(mint, None)
    # Olvidar el precio no deja ninguna posicion a ciegas: `precio_en_vivo`
    # devuelve None y quien lo use vuelve a preguntar al nodo.
    _precio.pop(mint, None)


def registrar_bloque_inicial(mint: str, slot: int | None) -> None:
    """Fija el slot de creacion para contar solo sus compras.

    ``registrar_logs`` se ejecuta antes de que el parser conozca el mint. Si la
    creacion no incluyera una compra, sin esta marca el primer trade posterior
    se confundiria con el primer bloque.
    """
    if slot is None:
        return
    bloque = int(slot)
    primero = _first_slot.get(mint)
    if primero is None or bloque < primero:
        _first_slot[mint] = bloque
        _first_slot_buys[mint] = 0
        _first_slot_wallets[mint] = set()
        _first_slot_sol[mint] = 0.0


def registrar_logs(logs: list[str], slot: int | None = None) -> list[str]:
    """Apunta las operaciones que aparezcan en estos logs. No lanza nunca.

    Se llama para CADA notificacion del websocket, incluidas las que no son
    creaciones, asi que tiene que ser barata y silenciosa: un fallo aqui no
    puede tumbar la deteccion de monedas nuevas.

    Returns:
        Las monedas que han operado en estos logs, en orden y sin repetir. Asi
        quien vigila el avance de la curva solo mira lo que acaba de moverse,
        en vez de recorrer los miles de mints que hay en memoria.
    """
    ahora = time.time()
    tocadas: list[str] = []
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
            sol = sol_lamports / 1e9
            _ops[mint].append((ahora, sol, es_compra, wallet))
            _recortar(mint)
            if mint not in tocadas:
                tocadas.append(mint)
            if len(cuerpo) >= _LARGO_RESERVAS:
                # Reservas VIRTUALES: de aqui sale el precio, que es la misma
                # cuenta que hace el nodo cuando se le pregunta.
                v_sol, v_tokens = struct.unpack_from(
                    "<QQ", cuerpo, _OFFSET_VIRTUALES
                )
                if v_tokens > 0:
                    _precio[mint] = (
                        ahora,
                        (v_sol / _LAMPORTS) / (v_tokens / _DEC_TOKEN),
                    )
                # Reservas de la curva DESPUES de esta operacion: con ellas se
                # sabe por que punto de la curva va sin preguntar al nodo.
                sol_real, tokens_real = struct.unpack_from(
                    "<QQ", cuerpo, _OFFSET_RESERVAS
                )
                previo = _curva.get(mint)
                tope = max(tokens_real, previo[3]) if previo else tokens_real
                _curva[mint] = (ahora, tokens_real, sol_real, tope)
            if slot is not None:
                _anotar_primer_bloque(mint, int(slot), sol, wallet, es_compra=es_compra)
        except Exception:  # noqa: BLE001
            continue
    _limpiar()
    return tocadas


def _anotar_primer_bloque(
    mint: str,
    bloque: int,
    sol: float,
    wallet: str,
    *,
    es_compra: bool,
) -> None:
    """Acumula quien compro en el bloque de creacion, que es donde se ve el
    bundle: muchas carteras entrando a la vez que nace la moneda."""
    primero = _first_slot.get(mint)
    if primero is None or bloque < primero:
        _first_slot[mint] = bloque
        _first_slot_buys[mint] = 1 if es_compra else 0
        _first_slot_wallets[mint] = {wallet} if es_compra and wallet else set()
        _first_slot_sol[mint] = sol if es_compra else 0.0
        return
    if bloque != primero or not es_compra:
        return
    _first_slot_buys[mint] += 1
    _first_slot_sol[mint] += sol
    if wallet:
        _first_slot_wallets[mint].add(wallet)


def _recortar(mint: str) -> None:
    """Deja la moneda en las primeras y las ultimas operaciones."""
    lista = _ops[mint]
    if len(lista) <= _MAX_OPS_POR_MONEDA:
        return
    conservar_final = _MAX_OPS_POR_MONEDA - _OPS_INICIALES
    del lista[_OPS_INICIALES : len(lista) - conservar_final]


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


def ultimas_son_compra(mint: str, cuantas: int) -> bool:
    """True si las ultimas `cuantas` operaciones de la moneda son COMPRAS.

    No es lo mismo que "hay mas compras que ventas": eso lo mide `presion`, y
    puede dar bien mientras alguien esta saliendo por detras. Esto exige que
    NADIE haya vendido en las ultimas operaciones, que es lo que de verdad
    separo al medirlo -- las monedas con `tx/compras < 1,2` hacian x2 el 41% de
    las veces frente al 17% de base.

    Si todavia no hay `cuantas` operaciones registradas devuelve False: no se
    puede afirmar algo de lo que no hay datos.
    """
    ops = _ops.get(mint)
    if not ops or len(ops) < cuantas or cuantas <= 0:
        return False
    return all(es_compra for _, _, es_compra, _ in list(ops)[-cuantas:])


def compras(mint: str) -> int:
    """Numero de compras vistas en esa moneda, sin contar ventas."""
    return sum(1 for _t, _s, es_compra, _w in (_ops.get(mint) or ()) if es_compra)


def compras_primer_bloque(mint: str) -> int:
    """Numero de compras vistas en el slot donde se creo la moneda."""
    return int(_first_slot_buys.get(mint, 0))


def precio_en_vivo(mint: str, max_edad: float) -> float | None:
    """Precio en SOL de la ultima operacion vista, si es lo bastante reciente.

    Devuelve None cuando no hay nada apuntado o cuando lo apuntado es mas
    viejo que ``max_edad`` segundos.

    POR QUE HAY UN LIMITE DE EDAD SI EL PRECIO NO CADUCA. En una bonding curve
    el precio solo se mueve cuando alguien opera, asi que un precio de hace
    veinte segundos sin operaciones de por medio SIGUE SIENDO EL PRECIO. El
    limite no esta por eso: esta por si el stream se cae.

    Esa es la diferencia que importa. Si el websocket se corta, aqui no llega
    ningun aviso -- simplemente dejan de entrar eventos, que desde fuera es
    idéntico a "no hay operaciones". Sin limite de edad, una posicion se
    quedaria mirando un precio congelado mientras la moneda se desploma de
    verdad, y el stop loss no saltaria NUNCA. Con limite, en cuanto pasan esos
    segundos se vuelve a preguntar al nodo, que es lento pero no miente.

    Args:
        mint: la moneda.
        max_edad: segundos que se acepta el ultimo precio visto. 0 o menos
            desactiva la funcion (devuelve None siempre).
    """
    if max_edad <= 0:
        return None
    dato = _precio.get(mint)
    if not dato:
        return None
    visto, precio = dato
    if (time.time() - visto) > max_edad:
        return None
    return precio or None


def edad_del_precio(mint: str) -> float | None:
    """Segundos desde la ultima operacion vista, o None si no hay ninguna."""
    dato = _precio.get(mint)
    return (time.time() - dato[0]) if dato else None


def progreso_curva(mint: str) -> tuple[float, float] | None:
    """Por donde va la curva de esa moneda, sin gastar una sola llamada al nodo.

    Returns:
        ``(avance, sol_en_curva)`` con el avance entre 0 y 1, o None si todavia
        no se ha visto ninguna operacion suya con reservas.

    El avance es "cuanto del tramo a la venta se ha vendido ya":
    ``1 - tokens_que_quedan / tokens_iniciales``. La referencia es el maximo de
    tokens visto en esa misma moneda cuando supera al inicial de fabrica, para
    que un cambio de pump.fun en esa constante no descuadre la cuenta.
    """
    datos = _curva.get(mint)
    if not datos:
        return None
    _t, tokens, sol, tope = datos
    base = max(tope, RESERVA_TOKENS_INICIAL)
    if base <= 0:
        return None
    avance = 1.0 - (tokens / base)
    return (max(0.0, min(1.0, avance)), sol / 1e9)


def vio_la_creacion(mint: str) -> bool:
    """True si esta moneda se lleva siguiendo desde que nacio.

    No se pregunta al listener: se comprueba contra la cadena. Si en algun
    momento se le vieron las reservas llenas, es que se la vio recien creada.
    Importa porque todo lo que delata un bundle (quien compro en el bloque de
    creacion) solo existe si se estaba mirando en ese momento; si el bot arranco
    con la moneda ya en marcha, esos contadores valen cero y darian el visto
    bueno a un bundle por no haberlo visto.
    """
    datos = _curva.get(mint)
    if not datos:
        return False
    return datos[3] >= RESERVA_TOKENS_INICIAL * 0.99


def carteras_primer_bloque(mint: str) -> int:
    """Wallets distintas que compraron en el bloque de creacion."""
    return len(_first_slot_wallets.get(mint) or ())


def sol_primer_bloque(mint: str) -> float:
    """SOL comprado en el bloque de creacion."""
    return float(_first_slot_sol.get(mint, 0.0))


def compra_creador_sol(mint: str, creador: str) -> float:
    """SOL que el propio creador se ha gastado comprando su moneda.

    Suma TODAS sus compras vistas, no solo la del bloque de creacion -- en la
    practica es casi siempre la misma, porque el dev-buy de creacion es
    la unica compra del creador que hay antes de que el bot decida. Cuesta
    cero: es el mismo stream de ``TradeEvent`` que ya se lee para el volumen.
    """
    if not creador:
        return 0.0
    return sum(
        s for _t, s, es_compra, w in (_ops.get(mint) or ())
        if es_compra and w == creador
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


def metricas_segunda_ola(
    mint: str,
    ventana: float = 1.5,
    creador: str = "",
    ahora: float | None = None,
) -> dict[str, float | int]:
    """Resume el flujo acumulado y la oleada mas reciente de compradores.

    La edad se mide desde el primer ``TradeEvent`` observado, que en pump.fun
    coincide con la compra de creacion. Las metricas recientes excluyen al
    creador: una recompra del dev no debe fingir demanda externa.
    """
    ops = _ops.get(mint)
    if not ops:
        return {
            "edad": 0.0,
            "compras": 0,
            "compradores": 0,
            "neto_sol": 0.0,
            "ola_compra_sol": 0.0,
            "ola_venta_sol": 0.0,
            "ola_neto_sol": 0.0,
            "ola_compradores": 0,
            "mayor_compra_sol": 0.0,
            "venta_creador_sol": 0.0,
        }

    instante = time.time() if ahora is None else ahora
    corte = instante - max(float(ventana), 0.0)
    compra_total = venta_total = venta_creador = 0.0
    compras_total = 0
    compradores: set[str] = set()
    ola_compra = ola_venta = mayor_compra = 0.0
    ola_compradores: set[str] = set()

    for t, sol, es_compra, wallet in ops:
        if es_compra:
            compra_total += sol
            compras_total += 1
            if wallet and wallet != creador:
                compradores.add(wallet)
        else:
            venta_total += sol
            if creador and wallet == creador:
                venta_creador += sol

        if t < corte or (creador and wallet == creador):
            continue
        if es_compra:
            ola_compra += sol
            mayor_compra = max(mayor_compra, sol)
            if wallet:
                ola_compradores.add(wallet)
        else:
            ola_venta += sol

    return {
        "edad": max(0.0, instante - ops[0][0]),
        "compras": compras_total,
        "compradores": len(compradores),
        "neto_sol": compra_total - venta_total,
        "ola_compra_sol": ola_compra,
        "ola_venta_sol": ola_venta,
        "ola_neto_sol": ola_compra - ola_venta,
        "ola_compradores": len(ola_compradores),
        "mayor_compra_sol": mayor_compra,
        "venta_creador_sol": venta_creador,
    }


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
