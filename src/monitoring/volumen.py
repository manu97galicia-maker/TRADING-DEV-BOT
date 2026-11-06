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
from collections import defaultdict, deque

from utils.logger import get_logger

logger = get_logger(__name__)

#: Discriminador de TradeEvent = primeros 8 bytes de sha256("event:TradeEvent").
#: Anchor lo antepone a los datos del evento dentro del "Program data:" del log.
DISC_TRADE = hashlib.sha256(b"event:TradeEvent").digest()[:8]
#: El evento que emite pump.fun al CREAR una moneda (create y create_v2).
DISC_CREATE = hashlib.sha256(b"event:CreateEvent").digest()[:8]

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
_last_slot: dict[str, int] = {}
_first_slot_buys: dict[str, int] = defaultdict(int)

#: Wallets distintas que compraron en el bloque de creacion, y SOL que
#: metieron. Un bundle de pump and dump se monta justo ahi: muchas carteras
#: entrando en el MISMO bloque que la creacion, para tener el token barato
#: antes que nadie y soltarlo despues sobre quien llegue.
_first_slot_wallets: dict[str, set[str]] = defaultdict(set)
_first_slot_sol: dict[str, float] = defaultdict(float)

# Primer slot posterior al de creacion. Se guarda por separado para poder
# probar, en paper, si la demanda continua en el bloque siguiente en vez de
# confundirla con las cuatro compras del nacimiento.
_second_slot: dict[str, int] = {}
_second_slot_buy_sol: dict[str, float] = defaultdict(float)
_second_slot_sell_sol: dict[str, float] = defaultdict(float)
_second_slot_buyers: dict[str, set[str]] = defaultdict(set)
_second_slot_buys: dict[str, int] = defaultdict(int)
_second_slot_sells: dict[str, int] = defaultdict(int)

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

#: mint -> ultimos precios, para poder ver si la moneda ACABA DE PEGAR UN
#: SALTO. `_precio` solo guarda el ultimo, y con un solo punto no se puede
#: saber si el precio de ahora es el de siempre o el de una vela verde
#: enorme que ya se ha ido.
#:
#: POR QUE HACE FALTA, con un caso concreto: el 23-sep el bot entro en CATO a
#: 26K de market cap, justo despues de que la moneda subiera de 7,5K a 25K en
#: medio minuto. Comprar el ultimo escalon de una subida vertical es comprar
#: el techo: medio minuto despues estaba a 12,4K.
#:
#: Son 128 puntos por moneda, no un historico: solo interesa lo de hace unos
#: segundos. Unos 3,6 KB por moneda ACTIVA, y solo las que operan tienen; con
#: el tope de `_MAX_MINTS` (4.000) son ~14 MB en el peor caso.
#:
#: POR QUE 128 Y NO 32, que es lo que habia hasta el 11-oct. El freno de la
#: vela verde esta configurado a 30 SEGUNDOS (`subida_seg_global`), pero
#: `subida()` solo puede mirar los puntos que haya en este buffer. Una moneda
#: caliente --justo la que importa-- hace varias operaciones por segundo, asi
#: que 32 puntos eran unos 6 segundos de historia: el filtro decia "30 s" y
#: miraba 6. La vela que se queria evitar cabia entera en el hueco.
#:
#: ES EL MISMO FALLO DE FAMILIA de `min_presion_ratio` sin `max_buy_age`: un
#: freno pedido, escrito, encendido... y mudo, porque le faltaba la pieza que
#: lo hace medir de verdad. Aqui no era una clave que faltara, era el tamano
#: del buffer. Lo ata `test_la_ventana_de_la_vela_verde_cabe_en_el_buffer`.
_historia_precio: dict[str, deque[tuple[float, float]]] = defaultdict(
    lambda: deque(maxlen=128)
)
#: Con un solo punto no se puede saber si el precio subio o lleva ahi desde
#: siempre, y bloquear una compra sin saberlo seria inventarse el motivo.
_MINIMO_PUNTOS_SUBIDA = 2
_OFFSET_VIRTUALES = 89
#: Decimales: el SOL va en lamports (9) y el token de pump.fun tiene 6.
_DEC_TOKEN = 10**6
_LAMPORTS = 10**9

#: Sin esto la memoria crece sin limite: pump.fun lanza miles de monedas al dia
#: y el bot esta pensado para correr semanas seguidas.
#:
#: NO SE TOCAN A MANO: los pone `configurar_memoria()` al arrancar, a partir de
#: la ventana de compra que tenga puesta la estrategia. El 19-sep esa ventana
#: subio a 20 minutos y estos dos se quedaron en 5, asi que el bot prometia
#: mirar veinte minutos con datos de cinco: a las monedas que se le borraban en
#: marcha el recuento de titulares les volvia a CERO, que es exactamente la
#: queja de "el numero de holders tarda muchisimo".
_MAX_MINTS = 4000
_VIDA = 300.0  # segundos que interesa recordar de cada moneda

#: Cada cuanto se pasa la escoba. Antes corria en CADA notificacion y, con la
#: memoria llena, eso es recorrer y ordenar miles de monedas por cada operacion
#: de pump.fun: medido, 0,46 ms, o sea ~14% de un nucleo tirado. Nada de lo que
#: se limpia caduca en milisegundos, asi que cada dos segundos sobra.
_CADA_CUANTO_LIMPIA = 2.0
_ultima_limpieza = 0.0

#: TOPE DURO DE MEMORIA, en operaciones guardadas entre todas las monedas.
#:
#: El tope de MONEDAS no basta: una moneda tranquila ocupa treinta operaciones
#: y una que se pone de moda llega a `_MAX_OPS_POR_MONEDA` (1.200). Contando
#: solo monedas, un dia movido cabria en el limite y aun asi se comeria varios
#: GB. El droplet tiene 1 GB y un OOM mata el bot CON POSICIONES ABIERTAS, que
#: es la peor forma de fallar que tiene esto.
#:
#: Medido con tracemalloc: ~89 bytes por operacion, asi que 1,5 M de
#: operaciones son ~130 MB. Al pasarse se sueltan las monedas mas paradas, que
#: es degradar; quedarse sin memoria no es degradar.
_MAX_OPS_TOTAL = 1_500_000

#: Cuantas monedas se han borrado, y por que. Lo lee el parte periodico: si las
#: borradas POR TOPE dejan de ser cero, la memoria se ha quedado corta y el bot
#: esta perdiendo monedas que todavia estaba mirando.
_borradas_viejas = 0
_borradas_por_tope = 0

#: mint -> (momento del primer TradeEvent visto, si se le vieron las reservas
#: llenas). SOBREVIVE a `_olvidar()` a proposito, y es lo unico que lo hace.
#:
#: POR QUE. Sin esto, a una moneda borrada por falta de sitio le pasan dos
#: cosas y las dos malas: su edad vuelve a contarse desde cero --una moneda de
#: quince minutos parece recien nacida-- y pierde el "se le vio nacer", con lo
#: que `vio_la_creacion()` da False, la fuente de titulares la descarta como
#: "sin_historia" y ya no se puede comprar NUNCA, por mucho que despues junte
#: doscientos titulares.
#:
#: Cuesta ~150 bytes por moneda, contra los ~83 bytes POR OPERACION que cuesta
#: el historial entero. Por eso se puede recordar mucho mas tiempo que el resto.
_nacimiento: dict[str, tuple[float, bool]] = {}
_MAX_NACIMIENTOS = 60_000

#: RETRASO DEL STREAM: segundos entre que la cadena produce una operacion y el
#: bot la digiere. Sale GRATIS y hasta ahora se estaba tirando -- cada
#: TradeEvent trae SU PROPIA marca de tiempo (byte 81 del cuerpo: mint 32 +
#: sol 8 + tokens 8 + is_buy 1 + user 32), que es la de la CADENA, no la del
#: reloj de aqui.
#:
#: Resolucion de 1 segundo, que es como va la marca: sirve para saber si vamos
#: seis segundos por detras, no para afinar milisegundos.
_OFFSET_TS = 81
_retrasos: deque[float] = deque(maxlen=2000)

#: Tope de operaciones guardadas por moneda. Una que llega al final de su curva
#: acumula decenas de miles y la lista era lo unico que crecia sin limite. Se
#: conservan las PRIMERAS (la creacion y su bloque, que es donde se reconoce un
#: bundle) y las ULTIMAS (lo que esta pasando ahora); lo de en medio no decide
#: nada. A los pocos minutos de vida, que es cuando miran los filtros de
#: siempre, ninguna moneda llega a este tope, asi que no les cambia nada.
_MAX_OPS_POR_MONEDA = 1200
_OPS_INICIALES = 200


def configurar_memoria(ventana_de_compra: float, margen: float = 1.5) -> None:
    """Recordar de cada moneda al menos lo que dura la ventana de compra.

    LO QUE ARREGLA. `_VIDA` estaba escrito a mano en cinco minutos. Cuando la
    ventana de compra subio a veinte, el bot empezo a borrar en marcha monedas
    que todavia estaba mirando: volvian a cero titulares y, peor, perdian el
    "se le vio nacer", que las deja descartadas para siempre. Ahora el limite
    lo pone quien conoce la ventana, no un numero suelto en este fichero.

    Args:
        ventana_de_compra: Segundos de vida que la estrategia acepta comprar.
        margen: Multiplicador de seguridad. Una moneda puede pasarse un rato
            sin operar y volver; si se la olvida justo antes de volver, se
            pierde entera.
    """
    global _VIDA, _MAX_MINTS  # noqa: PLW0603
    nueva = max(float(ventana_de_compra) * float(margen), 300.0)
    if nueva <= _VIDA:
        return
    # El tope de monedas tiene que crecer con la ventana o vuelve a ser EL que
    # borra: recordar veinte minutos en hueco para cuatro mil no recuerda
    # veinte minutos. Medido con tracemalloc, ~83 bytes por operacion
    # compartiendo cadenas (ver el cache de `_b58`), asi que sale del orden de
    # 80 MB. El droplet tiene 1 GB.
    _MAX_MINTS = max(_MAX_MINTS, int(4000 * nueva / 300.0))
    _VIDA = nueva
    logger.info(
        "Memoria del stream: se recuerdan %.0f min por moneda, hasta %d monedas "
        "(ventana de compra %.0f min)",
        nueva / 60,
        _MAX_MINTS,
        float(ventana_de_compra) / 60,
    )


def _limpiar() -> None:
    global _ultima_limpieza, _borradas_viejas, _borradas_por_tope  # noqa: PLW0603
    # NO EN CADA NOTIFICACION. Con la memoria llena esto recorre y ordena miles
    # de monedas, y se llamaba una vez por operacion de pump.fun.
    ahora = time.time()
    if ahora - _ultima_limpieza < _CADA_CUANTO_LIMPIA:
        return
    _ultima_limpieza = ahora
    if len(_ops) > _MAX_MINTS:
        corte = ahora - _VIDA
        muertos = [m for m, v in _ops.items() if not v or v[-1][0] < corte]
        for m in muertos:
            _olvidar(m)
        _borradas_viejas += len(muertos)
        # Si aun asi no baja (mucha moneda viva a la vez), se tira lo mas
        # parado.
        if len(_ops) > _MAX_MINTS:
            sobran = len(_ops) - _MAX_MINTS
            for m in sorted(_ops, key=lambda k: _ops[k][-1][0])[:sobran]:
                _olvidar(m)
            _borradas_por_tope += sobran
    # FUERA del `if` de arriba a proposito: el tope de memoria NO depende de
    # cuantas monedas haya. Pocas monedas muy activas ocupan mas que muchas
    # tranquilas, y comprobarlo solo al pasarse el tope de monedas dejaba el
    # presupuesto sin vigilar justo en ese caso.
    _borradas_por_tope += _respetar_el_tope_de_memoria()


def _respetar_el_tope_de_memoria() -> int:
    """Suelta monedas paradas hasta bajar del presupuesto de operaciones.

    Devuelve cuantas ha soltado. Contar monedas no acota la memoria: lo que
    ocupa son las OPERACIONES, y una moneda de moda guarda cuarenta veces mas
    que una tranquila. Ver `_MAX_OPS_TOTAL`.
    """
    total = sum(len(v) for v in _ops.values())
    if total <= _MAX_OPS_TOTAL:
        return 0
    soltadas = 0
    for m in sorted(_ops, key=lambda k: _ops[k][-1][0]):
        if total <= _MAX_OPS_TOTAL:
            break
        total -= len(_ops[m])
        _olvidar(m)
        soltadas += 1
    logger.warning(
        "Memoria del stream al tope: se sueltan %d monedas paradas. Si esto "
        "sale a menudo, la ventana de compra no cabe y hay monedas que se "
        "quedan sin comprar.",
        soltadas,
    )
    return soltadas


def _olvidar(mint: str) -> None:
    """Borra todo rastro de una moneda. Que no se escape ningun diccionario:
    uno olvidado aqui es una fuga de memoria que solo se nota tras dias."""
    _ops.pop(mint, None)
    _dev_creacion.pop(mint, None)
    _first_slot.pop(mint, None)
    _last_slot.pop(mint, None)
    _first_slot_buys.pop(mint, None)
    _first_slot_wallets.pop(mint, None)
    _first_slot_sol.pop(mint, None)
    _second_slot.pop(mint, None)
    _second_slot_buy_sol.pop(mint, None)
    _second_slot_sell_sol.pop(mint, None)
    _second_slot_buyers.pop(mint, None)
    _second_slot_buys.pop(mint, None)
    _second_slot_sells.pop(mint, None)
    _curva.pop(mint, None)
    # Olvidar el precio no deja ninguna posicion a ciegas: `precio_en_vivo`
    # devuelve None y quien lo use vuelve a preguntar al nodo.
    _precio.pop(mint, None)
    _historia_precio.pop(mint, None)
    # `_nacimiento` NO se toca aqui, y es lo unico que sobrevive. Sin el, una
    # moneda borrada por falta de sitio miente sobre su edad y queda marcada
    # como "sin historia" de por vida. Ver su comentario mas arriba.


#: QUIEN FIRMO LA CREACION de cada moneda: el dev de verdad (5-nov).
#:
#: Las monedas que entran por la fuente de curva (curva96) se reconstruyen
#: leyendo la cuenta BondingCurve, y ahi `creator` es quien COBRA las
#: comisiones, no quien creo la moneda. Pump.fun puede cambiarlo a la cuenta
#: de reparto de comisiones (PDA "sharing-config" del programa Pump Fees), y
#: entonces el filtro de migradas preguntaba a pump.fun por una direccion que
#: no ha creado nada: "0 migr / 0 lanz" y descarte. Pedido el 5-nov
#: ("Arreglarlo") tras ver 23 de 23 devs rechazados mientras en pump.fun
#: salian monedas de devs con migradas. Se guarda aqui al ver la creacion
#: (CreateEvent.user) y lo recoge el constructor de la ficha.
_dev_creacion: dict[str, str] = {}
#: Tope de memoria: las monedas que nacen y nunca operan no pasan por
#: `_olvidar`. Se sueltan las mas viejas (el dict guarda el orden de entrada).
_MAX_DEV_CREACION = 20_000


def dev_de_la_creacion(mint: str) -> str | None:
    """El firmante de la creacion de `mint`, si se vio nacer."""
    return _dev_creacion.get(str(mint))


def _apuntar_creacion(mint: str, ahora: float) -> None:
    """Apunta la hora (de este reloj) a la que llego la creacion del mint.

    Solo la primera vez: la creacion ocurre una vez y la ven dos escuchas.
    """
    if mint not in _creacion:
        _creacion[mint] = ahora
        while len(_creacion) > _MAX_DEV_CREACION:
            _creacion.pop(next(iter(_creacion)))


def _mint_de_la_creacion(cuerpo: bytes) -> str | None:
    """Mint de un CreateEvent (sin discriminador). None si no se puede leer.

    Layout: name, symbol, uri (u32 de largo + bytes cada una) y luego mint.
    """
    pos = 0
    for _ in range(3):
        if pos + 4 > len(cuerpo):
            return None
        largo = struct.unpack_from("<I", cuerpo, pos)[0]
        pos += 4 + largo
    if pos + 32 > len(cuerpo):
        return None
    return _b58(cuerpo[pos : pos + 32])


def _creacion_del_evento(cuerpo: bytes, ahora: float) -> None:
    """Apunta la creacion leida de un CreateEvent. No lanza nunca."""
    try:
        mint = _mint_de_la_creacion(cuerpo)
    except Exception:  # noqa: BLE001
        return
    if mint:
        _apuntar_creacion(mint, ahora)


#: Firma de la transaccion que creo cada moneda (6-nov). Hace falta para
#: saber quien la minteo cuando la crea una plataforma (ver
#: `pumpfun_dev.dev_que_mintea`).
_firma_creacion: dict[str, str] = {}


def firma_de_la_creacion(mint: str) -> str | None:
    """La firma de la transaccion que creo `mint`, si el bot la vio."""
    return _firma_creacion.get(str(mint))


def registrar_bloque_inicial(
    mint: str, slot: int | None, dev: str | None = None, firma: str | None = None
) -> None:
    """Fija el slot de creacion para contar solo sus compras.

    ``registrar_logs`` se ejecuta antes de que el parser conozca el mint. Si la
    creacion no incluyera una compra, sin esta marca el primer trade posterior
    se confundiria con el primer bloque.

    `dev` es quien firmo la creacion (CreateEvent.user). Se guarda aunque no
    haya slot: es la identidad del dev, no depende del bloque.
    """
    if dev:
        _dev_creacion[str(mint)] = str(dev)
        while len(_dev_creacion) > _MAX_DEV_CREACION:
            _dev_creacion.pop(next(iter(_dev_creacion)))
    if firma and firma != "unknown":
        _firma_creacion[str(mint)] = str(firma)
        while len(_firma_creacion) > _MAX_DEV_CREACION:
            _firma_creacion.pop(next(iter(_firma_creacion)))
    # LA CREACION DE VERDAD, con la hora de este reloj (ver `edad_real`).
    _apuntar_creacion(str(mint), time.time())
    if slot is None:
        return
    bloque = int(slot)
    primero = _first_slot.get(mint)
    if primero is None or bloque < primero:
        _first_slot[mint] = bloque
        _last_slot[mint] = bloque
        _first_slot_buys[mint] = 0
        _first_slot_wallets[mint] = set()
        _first_slot_sol[mint] = 0.0


def registrar_logs(logs: list[str], slot: int | None = None, *, allowed_mints: set[str] | None = None) -> list[str]:
    """Apunta las operaciones que aparezcan en estos logs. No lanza nunca.

    Se llama para CADA notificacion del websocket, incluidas las que no son
    creaciones, asi que tiene que ser barata y silenciosa: un fallo aqui no
    puede tumbar la deteccion de monedas nuevas.
    ``allowed_mints`` limita la agregacion a posiciones vigiladas; ``None``
    conserva el registro de todos los mints para las fuentes generales.

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
        if crudo[:8] == DISC_CREATE:
            # LA CREACION, vista aqui mismo (5-nov, "maximo 3 segundos del
            # mint"). Las dos escuchas pasan por esta funcion, asi que la
            # creacion queda apuntada ANTES de mirar la primera compra, sin
            # depender de cual de las dos llegue primero.
            _creacion_del_evento(crudo[8:], ahora)
            continue
        if len(crudo) < 8 + 32 + 8 + 8 + 1 or crudo[:8] != DISC_TRADE:
            continue
        try:
            cuerpo = crudo[8:]
            mint = _b58(cuerpo[:32])
            if allowed_mints is not None and mint not in allowed_mints:
                continue
            sol_lamports = struct.unpack_from("<Q", cuerpo, 32)[0]
            # Layout de TradeEvent:
            #   mint(32) sol_amount(8) token_amount(8) is_buy(1) user(32) ...
            es_compra = cuerpo[48] == 1
            wallet = _b58(cuerpo[49:81]) if len(cuerpo) >= 81 else ""
            sol = sol_lamports / 1e9
            _ops[mint].append((ahora, sol, es_compra, wallet))
            _recortar(mint)
            _anotar_nacimiento(mint, _marca_de_la_cadena(cuerpo, ahora), llegada=ahora)
            if mint not in tocadas:
                tocadas.append(mint)
            if len(cuerpo) >= _LARGO_RESERVAS:
                # Reservas VIRTUALES: de aqui sale el precio, que es la misma
                # cuenta que hace el nodo cuando se le pregunta.
                v_sol, v_tokens = struct.unpack_from("<QQ", cuerpo, _OFFSET_VIRTUALES)
                if v_tokens > 0:
                    _p = (v_sol / _LAMPORTS) / (v_tokens / _DEC_TOKEN)
                    _precio[mint] = (ahora, _p)
                    _historia_precio[mint].append((ahora, _p))
                # Reservas de la curva DESPUES de esta operacion: con ellas se
                # sabe por que punto de la curva va sin preguntar al nodo.
                sol_real, tokens_real = struct.unpack_from(
                    "<QQ", cuerpo, _OFFSET_RESERVAS
                )
                previo = _curva.get(mint)
                tope = max(tokens_real, previo[3]) if previo else tokens_real
                _curva[mint] = (ahora, tokens_real, sol_real, tope)
                if tope >= RESERVA_TOKENS_INICIAL * 0.99:
                    # Se la vio con la curva llena: se la vio NACER. Queda
                    # apuntado en el sitio que sobrevive al borrado.
                    _nac = _nacimiento.get(mint)
                    _nacimiento[mint] = ((_nac[0] if _nac else ahora), True)
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
    _last_slot[mint] = max(bloque, _last_slot.get(mint, bloque))
    primero = _first_slot.get(mint)
    if primero is None or bloque < primero:
        _first_slot[mint] = bloque
        _first_slot_buys[mint] = 1 if es_compra else 0
        _first_slot_wallets[mint] = {wallet} if es_compra and wallet else set()
        _first_slot_sol[mint] = sol if es_compra else 0.0
        return
    if bloque != primero:
        segundo = _second_slot.get(mint)
        if segundo is None:
            _second_slot[mint] = bloque
            segundo = bloque
        # Solo interesa el primer bloque posterior, no todos los siguientes.
        if bloque == segundo:
            if es_compra:
                _second_slot_buys[mint] += 1
                _second_slot_buy_sol[mint] += sol
                if wallet:
                    _second_slot_buyers[mint].add(wallet)
            else:
                _second_slot_sells[mint] += 1
                _second_slot_sell_sol[mint] += sol
        return
    if not es_compra:
        return
    _first_slot_buys[mint] += 1
    _first_slot_sol[mint] += sol
    if wallet:
        _first_slot_wallets[mint].add(wallet)


def metricas_segundo_bloque(mint: str) -> dict[str, float | int]:
    """Flujo observado exclusivamente en el primer slot tras la creacion."""
    compra = float(_second_slot_buy_sol.get(mint, 0.0))
    venta = float(_second_slot_sell_sol.get(mint, 0.0))
    return {
        "observado": int(mint in _second_slot),
        "compras": int(_second_slot_buys.get(mint, 0)),
        "ventas": int(_second_slot_sells.get(mint, 0)),
        "compradores": len(_second_slot_buyers.get(mint) or ()),
        "compra_sol": compra,
        "venta_sol": venta,
        "neto_sol": compra - venta,
    }


#: Retraso maximo que se considera creible. Por encima no es retraso: es un
#: reloj descuadrado en el droplet o una operacion vieja reprocesada, y contarlo
#: estropearia justo el numero con el que se va a decidir.
_RETRASO_CREIBLE = 600.0


def _marca_de_la_cadena(cuerpo: bytes, ahora: float) -> float:
    """Cuando ocurrio de verdad la operacion, y de paso apunta el retraso.

    La marca es de la CADENA, asi que `ahora - marca` mide el camino entero
    --validador, nodo RPC, websocket, este bucle-- y no el reloj de aqui contra
    si mismo. Es la medida que faltaba: contar titulares esta medido en 0,03 ms
    y no puede ser lo lento, asi que si el bot va tarde es porque los datos
    llegan tarde. Es lo unico que distingue eso de "las monedas tardan en
    juntar gente", que se arreglan al reves.

    Y LA EDAD SE CUENTA DESDE AQUI, no desde que llego el dato: si no, una
    moneda cuya creacion llega con retraso parece recien nacida justo cuando el
    bot va tarde, y un error tapaba al otro.
    """
    if len(cuerpo) < _OFFSET_TS + 8:
        return ahora
    ts_cadena = float(struct.unpack_from("<q", cuerpo, _OFFSET_TS)[0])
    atraso = ahora - ts_cadena
    if not 0.0 <= atraso <= _RETRASO_CREIBLE:
        return ahora
    _retrasos.append(atraso)
    return ts_cadena


def _anotar_nacimiento(mint: str, nacio: float, llegada: float | None = None) -> None:
    """Primera vez que se ve la moneda. No se borra con el resto.

    `llegada` es la hora de ESTE reloj (al milisegundo) a la que llego su
    primera operacion; la usa `edad_real`.
    """
    if mint in _nacimiento:
        return
    if len(_nacimiento) >= _MAX_NACIMIENTOS:
        # Se tira la mitad mas antigua de golpe: recortar de uno en uno a cada
        # moneda nueva seria ordenar sesenta mil entradas por operacion.
        for viejo in sorted(_nacimiento, key=lambda k: _nacimiento[k][0])[
            : _MAX_NACIMIENTOS // 2
        ]:
            del _nacimiento[viejo]
            _llegada.pop(viejo, None)
    _nacimiento[mint] = (nacio, False)
    if llegada is not None:
        _llegada[mint] = llegada


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


def en_bloque_posterior_al_inicial(mint: str) -> bool:
    """True tras observar una operación en un slot posterior al de creación."""
    primero = _first_slot.get(mint)
    ultimo = _last_slot.get(mint)
    return primero is not None and ultimo is not None and ultimo > primero


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
    nac = _nacimiento.get(mint)
    if nac and nac[1]:
        # Se le vieron las reservas llenas en algun momento. Vale AUNQUE el
        # historial se haya borrado despues por falta de sitio: eso no deshace
        # haberla visto nacer, y antes si lo deshacia.
        return True
    datos = _curva.get(mint)
    if not datos:
        return False
    return datos[3] >= RESERVA_TOKENS_INICIAL * 0.99


def edad(mint: str) -> float | None:
    """Segundos de vida, o None si no se vio nacer la moneda.

    Se mira `_nacimiento`, que sobrevive a que se borre el historial: si se
    usara la primera operacion guardada, una moneda de quince minutos a la que
    se le limpio la lista pareceria recien nacida -- y se compraria por nueva.
    """
    if not vio_la_creacion(mint):
        return None
    nac = _nacimiento.get(mint)
    if nac:
        return max(0.0, time.time() - nac[0])
    # Sin nacimiento apuntado se cae a la primera operacion guardada, que es lo
    # de siempre. Solo pasa si alguien llena `_ops` sin pasar por
    # `registrar_logs`; los tests lo hacen.
    ops = _ops.get(mint)
    if not ops:
        return 0.0
    return max(0.0, time.time() - ops[0][0])


#: Hora de ESTE reloj a la que llego la primera operacion de cada moneda.
_llegada: dict[str, float] = {}
#: Hora de ESTE reloj a la que llego la CREACION de cada moneda: la
#: transaccion que crea el mint (su CreateEvent, en `registrar_logs`, o el
#: escuchador de creaciones, `registrar_bloque_inicial`). Es lo unico que
#: prueba que se la vio nacer.
_creacion: dict[str, float] = {}
#: Con menos muestras de retraso no se fia de la mediana.
_MIN_MUESTRAS_RETRASO = 20


def edad_real(mint: str) -> float | None:
    """Segundos desde la CREACION DEL MINT. None si no se vio crear.

    5-nov, "Pon 3 segundos de vida maxima" -> "Desde su nacimiento real" ->
    "Ha comprado una de hace 9 dias. Repito: maximo 3 segundos del mint".

    POR QUE NO VALE `edad` / `vio_la_creacion`. Dan por "vista nacer" una
    moneda en cuanto se le ven las reservas de la curva llenas, y apuntan como
    nacimiento la PRIMERA OPERACION que el bot ve. Una moneda vieja y muerta,
    en la que todos vendieron, vuelve a tener la curva llena: si alguien la
    compra hoy, parecia recien nacida. Asi se compro una de 9 dias.

    AQUI SOLO CUENTA LA CREACION DE VERDAD: la transaccion que crea el mint,
    que el escuchador ve y apunta (`_creacion`). Sin ella, None: edad
    desconocida, no se compra.

    La edad se mide con el reloj de este bot (al milisegundo):
        (ahora - cuando llego la creacion) + lo que tarda el aviso
    Lo que tarda el aviso sale de la mediana de `retraso()`, que se mide
    contra la hora de la cadena y lleva de media +0,5 s del redondeo.

    Y se acota con la cadena, que apunta su hora en segundos enteros:
      - nunca MAS JOVEN que `edad - 1` (la creacion fue antes de cualquier
        operacion vista);
      - nunca MAS VIEJA que `edad`, pero solo si esa hora salio de la misma
        transaccion de creacion (la compra del dev al crear), que es cuando
        `edad` cuenta desde el mint.
    """
    creada = _creacion.get(mint)
    if creada is None:
        return None
    ahora = time.time()
    p50, _p95, muestras = retraso()
    aviso = max(0.0, p50 - 0.5) if muestras >= _MIN_MUESTRAS_RETRASO else 0.0
    estimada = max(0.0, (ahora - creada) + aviso)
    cadena = edad(mint) if vio_la_creacion(mint) else None
    if cadena is None:
        return estimada
    estimada = max(estimada, cadena - 1.0)
    llegada = _llegada.get(mint)
    if llegada is not None and abs(llegada - creada) < 1.0:
        estimada = min(estimada, cadena)
    return estimada


def retraso() -> tuple[float, float, int]:
    """(mediana, percentil 95, muestras) del retraso del stream, en segundos.

    Es el tiempo entre que la cadena produce una operacion y el bot la digiere.
    Si sale alto, el recuento de titulares es exacto pero de hace varios
    segundos, y no hay filtro que arregle eso.
    """
    if not _retrasos:
        return (0.0, 0.0, 0)
    orden = sorted(_retrasos)
    return (
        orden[len(orden) // 2],
        orden[min(len(orden) - 1, int(len(orden) * 0.95))],
        len(orden),
    )


def estado_memoria() -> dict[str, int]:
    """Cuantas monedas se recuerdan y cuantas se han tenido que borrar.

    `por_tope` distinto de cero significa que la memoria se ha quedado corta:
    el bot esta olvidando monedas que todavia estaba mirando, y a esas el
    recuento de titulares les vuelve a empezar en cero.
    """
    return {
        "monedas": len(_ops),
        "tope": _MAX_MINTS,
        "ventana_seg": int(_VIDA),
        "borradas_viejas": _borradas_viejas,
        "por_tope": _borradas_por_tope,
        "nacimientos": len(_nacimiento),
    }


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
        s
        for _t, s, es_compra, w in (_ops.get(mint) or ())
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


def subida(mint: str, segundos: float = 10.0) -> float:
    """Cuanto ha subido el precio desde su minimo en los ultimos `segundos`.

    Devuelve la subida en tanto por uno (0,8 = ha subido un 80%), o 0 si no
    hay con que compararlo -- porque no hay datos NO es lo mismo que porque
    este plana, y bloquear una compra por falta de datos seria inventarse un
    motivo.

    PARA QUE SIRVE: para no comprar el ultimo escalon de una vela verde. Una
    moneda que acaba de triplicar en medio minuto ya ha hecho el movimiento;
    quien entra ahi compra el techo. Pedido el 23-sep tras entrar en CATO a
    26K justo despues de que subiera de 7,5K a 25K.
    """
    puntos = _historia_precio.get(mint)
    if not puntos:
        return 0.0
    corte = time.time() - max(float(segundos), 0.0)
    recientes = [p for t, p in puntos if t >= corte and p > 0]
    if len(recientes) < _MINIMO_PUNTOS_SUBIDA:
        return 0.0
    suelo = min(recientes)
    if suelo <= 0:
        return 0.0
    return max(0.0, recientes[-1] / suelo - 1.0)


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
        # Del nacimiento apuntado aparte, no de la primera operacion que
        # quede en la lista: esa se puede haber borrado, y entonces una moneda
        # vieja pareceria recien nacida.
        "edad": _edad_desde_nacimiento(mint, instante, ops[0][0]),
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


def _edad_desde_nacimiento(mint: str, instante: float, primera_op: float) -> float:
    nac = _nacimiento.get(mint)
    return max(0.0, instante - (nac[0] if nac else primera_op))


_ALFA = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

#: Cache de base58. Las mismas pocas centenas de wallets salen una y otra vez,
#: y cada `_b58` costaba 5,1 us y creaba una cadena NUEVA. Con cache son 0,09 us
#: y --lo que de verdad importa-- una sola copia en memoria: medido, 83 bytes
#: por operacion compartiendo cadenas contra casi el doble sin compartir. Es lo
#: que hace que quepa recordar veinte minutos en un droplet de 1 GB.
_b58_cache: dict[bytes, str] = {}
_MAX_B58 = 50_000


def _b58(datos: bytes) -> str:
    cacheado = _b58_cache.get(datos)
    if cacheado is not None:
        return cacheado
    salida = _b58_calcula(datos)
    if len(_b58_cache) >= _MAX_B58:
        _b58_cache.clear()
    _b58_cache[datos] = salida
    return salida


def _b58_calcula(datos: bytes) -> str:
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
