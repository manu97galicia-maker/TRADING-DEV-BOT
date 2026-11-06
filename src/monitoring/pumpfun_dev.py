"""Pump.fun creator-migration filter.

Computes a token creator's track record from pump.fun's own public API
(https://frontend-api-v3.pump.fun/coins?creator=<wallet>): how many tokens
they've launched and how many graduated (``complete: true`` = migrated).

This is the on-chain-truth source RugCheck lacks: RugCheck often returns no
data for fresh pump.fun tokens, so a dev-reputation filter built on it blocks
almost everything. pump.fun's API returns the creator's full coin list with the
migration flag in one call, cached per creator.
"""

from __future__ import annotations

import asyncio
import time

import aiohttp
import base58
from solders.pubkey import Pubkey

from utils.logger import get_logger

logger = get_logger(__name__)

_API = "https://frontend-api-v3.pump.fun/coins"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
#: CUANTO SE GUARDA EL HISTORIAL DE UN DEV: 3 horas (5-nov, "Y si puede ser
#: todavia mas rapido mejor" -> "Si"). Muchos devs lanzan varias monedas
#: seguidas: con esto la segunda y siguientes se deciden al instante, sin
#: volver a leer su historial. Venia de 10 min.
#:
#: Y SIN QUE EL DATO GUARDADO MIENTA A FAVOR: si el dev lanza mas monedas
#: mientras esta guardado, su porcentaje real BAJA (mas lanzadas, las mismas
#: migradas). Por eso se apuntan sus lanzamientos vistos nacer (`_vistas`) y
#: se suman a lo guardado (`_lanzadas_desde_la_foto`). Si una moneda suya
#: migra mientras tanto, no se cuenta hasta la siguiente lectura: eso se
#: equivoca hacia lo prudente.
#:
#: LA MONEDA QUE SE ESTA VALORANDO NO CUENTA (5-nov, opcion A: "se juzga al
#: dev por lo que ya ha hecho"). Por eso no basta un contador: se apunta QUE
#: monedas lanzo, para sumar las demas y dejar fuera justo esa. Es lo mismo
#: que pasa la primera vez: la cadena aun no tiene confirmada la recien
#: nacida cuando se lee el historial.
_TTL = 3 * 3600.0
_cache: dict[str, tuple[float, int, int, float]] = {}  # creator -> (ts, launches, migrations, rate)
#: Monedas que cada dev lanzo y el bot vio nacer: dev -> {mint: hora}.
_vistas: dict[str, dict[str, float]] = {}
#: Lo lanzado hasta este rato ANTES de guardar el historial puede no estar en
#: el (la cadena tarda en confirmar): se suma. Si si estaba, cuenta dos veces,
#: y eso solo baja el porcentaje: hacia lo prudente.
_MARGEN_FOTO = 60.0
_MAX_DEVS_VISTOS = 50_000


def _guardar(creator: str, now: float, launches: int, migrations: int, rate: float) -> None:
    _cache[creator] = (now, launches, migrations, rate)


def _apuntar_lanzamiento(creator: str, mint: str, now: float) -> None:
    vistas = _vistas.setdefault(creator, {})
    vistas[mint] = now
    viejo = now - _TTL - _MARGEN_FOTO
    for m in [m for m, t in vistas.items() if t < viejo]:
        del vistas[m]
    while len(_vistas) > _MAX_DEVS_VISTOS:
        _vistas.pop(next(iter(_vistas)))


def _lanzadas_desde_la_foto(creator: str, foto: float, actual: str | None) -> int:
    """Lanzamientos vistos desde (casi) la foto guardada, sin contar `actual`."""
    vistas = _vistas.get(creator) or {}
    return sum(1 for m, t in vistas.items() if t >= foto - _MARGEN_FOTO and m != actual)

#: Picos de las monedas del creador que SI migraron, por creador.
#:
#: POR QUE SOLO LAS MIGRADAS. El campo ``ath_market_cap`` de pump.fun se infla
#: solo: un creador llego a ensenar 89.950.686 $ de maximo con 1.387 $ de
#: liquidez y 5 $ de volumen diario. Ordenar devs por ese numero premia a las
#: granjas. Migrar, en cambio, exige que entren 85 SOL de verdad en la curva y
#: eso no se puede fingir -- asi que aqui solo cuenta el pico de una moneda que
#: ademas migro.
_picos: dict[str, list[float]] = {}

#: Consultas en vuelo, por creador. Sin esto, pedir las estadisticas nada mas
#: ver la moneda y volver a pedirlas al ir a comprar lanza DOS llamadas a la
#: API para el mismo dev: la primera aun no ha terminado, asi que la cache
#: todavia esta vacia. Con esto la segunda se engancha a la primera.
_en_vuelo: dict[str, "asyncio.Task"] = {}

#: RESPALDO POR LA CADENA, A TRAVES DE HELIUS (5-nov).
#:
#: Pedido: "Arreglarlo", tras ver con deploy/porque-no-compra.sh que pump.fun
#: contesta 403 al droplet de DigitalOcean: los 12 devs rechazados salian con
#: 0 lanzadas porque el dato no llegaba. Sin pump.fun, el historial se lee de
#: la cadena con la fuente que construyo Codex
#: (monitoring/creator_transaction_history.py): las transacciones que FIRMO el
#: dev (getTransactionsForAddress de Helius), cuales crearon una moneda de
#: pump.fun, y cuales de esas tienen su pool canonico de PumpSwap (migro).
#:
#: Cuesta consultas de Helius por cada dev NUEVO; luego se guarda 10 min como
#: lo de pump.fun. Solo se usa si pump.fun falla.
_rpc_respaldo = None
#: Tras un rechazo de pump.fun no se le vuelve a preguntar en este rato: en
#: el droplet contesta 403 siempre, y preguntar en cada moneda solo es tiempo
#: perdido antes de ir a Helius.
_PAUSA_PUMPFUN = 600.0
_pumpfun_bloqueado_hasta = 0.0
#: Limites prudentes para un droplet de 1 nucleo y una cuota de Helius
#: (Codex usaba 64 paginas). Un dev con mas de 3.000 transacciones da "sin
#: dato" y no se compra: con dinero real, no saber no es que si.
_POLITICA_HELIUS = {
    "history_provider": "helius_transactions",
    "history_page_size": 100,
    "history_max_pages": 30,
    "history_max_transactions": 3000,
    "history_max_accounts": 3000,
    "min_creator_migration_fraction": 0.01,
}
_ESPERA_HELIUS = 8.0


def usar_rpc_de_respaldo(post_rpc: object) -> None:
    """Registra el cliente del nodo (Helius) para leer la cadena si pump.fun falla.

    `post_rpc` es `SolanaClient.post_rpc`: recibe el cuerpo JSON-RPC y devuelve
    la respuesta entera.
    """
    global _rpc_respaldo  # noqa: PLW0603
    _rpc_respaldo = post_rpc


async def _rpc(method: str, params: list) -> object:
    """Una llamada al nodo, con UN reintento (5-nov: 22 de 67 fallos eran
    "sin respuesta del nodo para getTransactionsForAddress")."""
    motivo = ""
    for intento in range(2):
        if intento:
            await asyncio.sleep(0.5)
        data = await _rpc_respaldo(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        if data and not data.get("error") and data.get("result") is not None:
            return data["result"]
        # El mensaje de error de Helius (p. ej. "method not available"), corto.
        # No lleva la clave: la clave va en la URL, no en la respuesta.
        error = (data or {}).get("error") or {}
        motivo = str(error.get("message", ""))[:80] if isinstance(error, dict) else ""
    raise RuntimeError(
        f"sin respuesta del nodo para {method}" + (f": {motivo}" if motivo else "")
    )


#: Las dos instrucciones de pump.fun que crean una moneda (comprobado contra
#: idl/pump_fun_idl.json: create = 181ec828051c0777, create_v2 = d6904cec5f8b31b4).
_CREAR = {
    bytes.fromhex("181ec828051c0777"),
    bytes.fromhex("d6904cec5f8b31b4"),
}
_PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def _creacion_firmada_sin_logs(tx: dict, developer: str) -> bool:
    """¿Esta transaccion crea una moneda de pump.fun firmada por `developer`?

    Se mira la INSTRUCCION, no los logs: es lo que se usa cuando los logs no
    se pueden leer (Solana los corta en las transacciones largas).
    """
    try:
        message = tx["transaction"]["message"]
        meta = tx.get("meta") or {}
        keys = list(message["accountKeys"])
        firmantes = keys[: int(message["header"]["numRequiredSignatures"])]
        if developer not in firmantes:
            return False
        cargadas = meta.get("loadedAddresses") or {}
        todas = [*keys, *cargadas.get("writable", []), *cargadas.get("readonly", [])]
        instrucciones = list(message.get("instructions") or [])
        for interna in meta.get("innerInstructions") or []:
            instrucciones.extend(interna.get("instructions") or [])
        for ix in instrucciones:
            i = ix.get("programIdIndex")
            if not isinstance(i, int) or not 0 <= i < len(todas) or todas[i] != _PUMP:
                continue
            if base58.b58decode(ix.get("data") or "")[:8] in _CREAR:
                return True
    except Exception:  # noqa: BLE001
        # Ni siquiera se sabe si es una creacion: se cuenta como si lo fuera.
        # Asi el porcentaje solo puede salir MAS BAJO, nunca mas alto.
        return True
    return False


def _minteadas_por_plataforma(tx: dict, developer: str) -> set[str]:
    """Monedas que `developer` MINTEO a traves de una plataforma (6-nov).

    `creation_mints` solo cuenta las creaciones cuyo `user` es el propio dev.
    Cuando la crea una plataforma, `user` es una cuenta del programa y la
    cartera que la minteo es otra firmante de la transaccion: esa es el dev
    ("El dev es el q mint"). Se cuentan aqui. Lanza si no se puede leer; el
    llamador ya trata lo ilegible como lanzada y no migrada.
    """
    from monitoring.creator_transaction_history import (  # noqa: PLC0415
        CREATE,
        event_parser,
        pump_events,
    )

    message, meta = tx["transaction"]["message"], tx["meta"]
    firmantes = list(message["accountKeys"])[: int(message["header"]["numRequiredSignatures"])]
    if developer not in firmantes:
        return set()
    mints: set[str] = set()
    for raw in pump_events(meta.get("logMessages") or [], strict=True):
        if raw[:8] != CREATE:
            continue
        event = event_parser().decode_event_data(raw, "CreateEvent")
        fields = (event or {}).get("fields") or {}
        user, mint = fields.get("user"), fields.get("mint")
        if user and mint and user != developer and not es_cartera(user):
            mints.add(str(mint))
    return mints


async def _historial_tolerante(developer: str) -> tuple[int, int]:  # noqa: C901, PLR0912 -- cada tx ilegible tiene que decidirse aparte
    """(lanzadas, migradas) leidas de la cadena, tolerando transacciones ilegibles.

    5-nov: el lector de Codex da por invalido TODO el historial si una sola
    transaccion trae los logs cortados ("log truncated"): 42 de 67 fallos en
    el droplet eran eso ("Unverifiable historical creation evidence"). Aqui:

      - lo que se puede leer, se lee con las piezas de Codex
        (`creation_mints`, `verify_migrations`);
      - una transaccion ilegible que SI crea una moneda firmada por el dev
        cuenta como LANZADA Y NO MIGRADA: la peor suposicion. El porcentaje
        solo puede salir mas bajo que el real, nunca mas alto, asi que nunca
        se compra por error a un dev que no llega al 1%;
      - una ilegible que no crea nada (una compra o una venta) se ignora.
    """
    from monitoring.creator_history_index import (  # noqa: PLC0415
        HistoryUnavailableError,
    )
    from monitoring.creator_transaction_history import (  # noqa: PLC0415
        creation_mints,
        verify_migrations,
    )

    pol = _POLITICA_HELIUS
    if not Pubkey.from_string(developer).is_on_curve():
        raise HistoryUnavailableError("Developer must be a creation signer wallet")  # noqa: TRY003
    anchor = await _rpc("getSlot", [{"commitment": "finalized"}])
    if type(anchor) is not int or anchor <= 0:
        raise HistoryUnavailableError("Finalized history anchor unavailable")  # noqa: TRY003
    cursor, vistas, mints, dudosas = None, set(), set(), 0
    for _ in range(pol["history_max_pages"]):
        config = {
            "transactionDetails": "full", "limit": pol["history_page_size"],
            "sortOrder": "desc", "encoding": "json",
            "maxSupportedTransactionVersion": 1, "commitment": "finalized",
            "minContextSlot": anchor,
            "filters": {"status": "succeeded", "tokenAccounts": "none",
                        "slot": {"lte": anchor}},
        }
        if cursor:
            config["paginationToken"] = cursor
        page = await _rpc("getTransactionsForAddress", [developer, config])
        datos = page.get("data") if isinstance(page, dict) else None
        if not isinstance(datos, list):
            raise HistoryUnavailableError("Incomplete historical page")  # noqa: TRY003
        for tx in datos:
            try:
                firma = tx["transaction"]["signatures"][0]
            except (KeyError, IndexError, TypeError):
                firma = None
            if firma is not None and firma in vistas:
                continue
            vistas.add(firma)
            if len(vistas) > pol["history_max_transactions"]:
                raise HistoryUnavailableError("Historical transaction bound reached")  # noqa: TRY003
            try:
                mints |= creation_mints(tx, developer)
                mints |= _minteadas_por_plataforma(tx, developer)
            except Exception:  # noqa: BLE001
                if _creacion_firmada_sin_logs(tx, developer):
                    dudosas += 1
        cursor = page.get("paginationToken")
        if not cursor:
            break
    else:
        raise HistoryUnavailableError("Historical pagination bound reached")  # noqa: TRY003
    if not mints and not dudosas:
        raise HistoryUnavailableError("No verified creation history")  # noqa: TRY003
    migradas = set()
    if mints:
        migradas, _ = await verify_migrations(mints, _rpc, pol, anchor)
    if dudosas:
        logger.info(
            "Historial del dev %s: %d creaciones ilegibles contadas como no "
            "migradas (lo prudente)", developer[:8], dudosas,
        )
    return len(mints) + dudosas, len(migradas)


async def _por_la_cadena(creator: str) -> tuple[int, int] | None:
    """(lanzadas, migradas) leidas de la cadena, o None si no se pudo."""
    if _rpc_respaldo is None:
        return None
    try:
        leido = await asyncio.wait_for(
            _historial_tolerante(creator), timeout=_ESPERA_HELIUS
        )
    except Exception as error:  # noqa: BLE001
        # EL MOTIVO, NO SOLO EL TIPO (5-nov): con solo "HistoryUnavailableError"
        # no se distinguia un dev sin monedas previas (lo normal) de una
        # averia. Se escribe el texto SOLO de los errores cuyo texto es fijo
        # (las etiquetas de Codex y el RuntimeError de `_rpc`): un error de
        # red podria llevar la URL del nodo, y en ella la clave de Helius.
        from monitoring.creator_history_index import (  # noqa: PLC0415
            HistoryUnavailableError,
        )

        fijo = isinstance(error, HistoryUnavailableError | RuntimeError)
        logger.warning(
            "Historial del dev %s por la cadena no disponible (%s)",
            creator[:8],
            f"{type(error).__name__}: {error}" if fijo else type(error).__name__,
        )
        return None
    return leido


def precalentar(creator: str, mint: str | None = None) -> None:
    """Empieza a leer el historial del dev YA, sin esperar el resultado.

    5-nov, "Y si puede ser todavia mas rapido mejor": el bot sabe quien es el
    dev en el instante en que ve la creacion, pero el trader no preguntaba
    hasta despues de montar la ficha de la moneda (varias llamadas al nodo).
    Llamado desde el escuchador al ver la creacion, la consulta corre A LA VEZ
    que se monta la ficha; cuando el trader pregunta, se engancha a esta
    misma consulta (`_en_vuelo`) o la encuentra ya guardada. No cambia lo que
    se compra, solo cuando se sabe.

    Solo actua si hay una estrategia con el filtro del dev encendido (es la
    que registra el nodo con `usar_rpc_de_respaldo`): sin ella no gasta nada.
    """
    if not creator:
        return
    # Se apunta SIEMPRE que se ve nacer una moneda suya (ver `_TTL`).
    if mint:
        _apuntar_lanzamiento(creator, str(mint), time.time())
    if _rpc_respaldo is None:
        return
    hit = _cache.get(creator)
    if hit and time.time() - hit[0] < _TTL:
        return
    tarea = _en_vuelo.get(creator)
    if tarea is not None and not tarea.done():
        return
    try:
        tarea = asyncio.ensure_future(_consultar(creator))
    except RuntimeError:
        # Sin bucle de eventos en marcha (no deberia pasar en el bot).
        return
    # SE APUNTA YA, no en la siguiente vuelta del bucle: si el trader pregunta
    # en este mismo instante, tiene que engancharse a esta, no lanzar otra.
    _en_vuelo[creator] = tarea

    def _al_acabar(t: asyncio.Task, c: str = creator) -> None:
        if not t.cancelled():
            t.exception()  # que no quede un "exception was never retrieved"
        if _en_vuelo.get(c) is t:
            _en_vuelo.pop(c, None)

    tarea.add_done_callback(_al_acabar)


# --- EL DEV ES EL QUE MINTEA (6-nov) ----------------------------------------
# Pedido: "El dev es el q mint". Cuando una moneda se crea A TRAVES de una
# plataforma de lanzamientos, pump.fun apunta como `user` una cuenta del
# programa (no es una cartera: esta fuera de la curva y nadie firma con ella).
# Antes esas monedas se descartaban ("Developer must be a creation signer
# wallet"). Ahora el dev es la cartera que PAGO Y FIRMO la transaccion de
# creacion: la primera cuenta del mensaje. Se le aplica la misma regla.

#: mint -> cartera que la minteo (ya resuelta).
_dev_por_mint: dict[str, str] = {}
#: mint -> busqueda en marcha de esa cartera.
_buscando_dev: dict[str, asyncio.Future] = {}
#: Intentos de leer la transaccion de creacion. Recien creada puede no estar
#: aun disponible en el nodo; 8 x 0,4 s = ~3 s como mucho.
_INTENTOS_FIRMA = 8
_MAX_DEV_POR_MINT = 20_000
_PAUSA_FIRMA = 0.4


def es_cartera(direccion: str) -> bool:
    """True si es una cartera (una clave que puede firmar), no una cuenta de
    programa. Las cuentas de programa estan fuera de la curva."""
    try:
        return Pubkey.from_string(str(direccion)).is_on_curve()
    except (ValueError, TypeError):
        return False


async def _pagador_de_la_creacion(mint: str) -> str | None:
    """La cartera que pago y firmo la creacion de `mint`, o None."""
    from monitoring.volumen import firma_de_la_creacion  # noqa: PLC0415

    firma = firma_de_la_creacion(mint)
    if not firma or _rpc_respaldo is None:
        return None
    params = [
        firma,
        {
            "encoding": "json",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        },
    ]
    for intento in range(_INTENTOS_FIRMA):
        if intento:
            await asyncio.sleep(_PAUSA_FIRMA)
        try:
            data = await _rpc_respaldo(
                {"jsonrpc": "2.0", "id": 1, "method": "getTransaction", "params": params}
            )
        except Exception:  # noqa: BLE001, S112 -- un fallo de red: se reintenta
            continue
        tx = (data or {}).get("result")
        if not tx:
            continue
        try:
            pagador = str(tx["transaction"]["message"]["accountKeys"][0])
        except (KeyError, IndexError, TypeError):
            return None
        return pagador if es_cartera(pagador) else None
    return None


def _buscar_dev(mint: str) -> asyncio.Future | None:
    """Lanza (o devuelve la ya lanzada) busqueda de quien minteo `mint`."""
    tarea = _buscando_dev.get(mint)
    if tarea is not None:
        return tarea
    try:
        tarea = asyncio.ensure_future(_pagador_de_la_creacion(mint))
    except RuntimeError:
        return None
    _buscando_dev[mint] = tarea

    def _al_acabar(t: asyncio.Future, m: str = mint) -> None:
        _buscando_dev.pop(m, None)
        if t.cancelled() or t.exception() is not None:
            return
        dev = t.result()
        if dev:
            _dev_por_mint[m] = dev
            while len(_dev_por_mint) > _MAX_DEV_POR_MINT:
                _dev_por_mint.pop(next(iter(_dev_por_mint)))
            # Y su historial empieza a leerse ya, como con cualquier dev.
            precalentar(dev, m)

    tarea.add_done_callback(_al_acabar)
    return tarea


def precalentar_por_firma(mint: str) -> None:
    """Al ver nacer una moneda creada por una plataforma: busca YA quien la
    minteo y, en cuanto lo sabe, empieza a leer su historial."""
    if _rpc_respaldo is None or str(mint) in _dev_por_mint:
        return
    _buscar_dev(str(mint))


def dev_minteado(mint: str) -> str | None:
    """La cartera que minteo `mint`, si ya se resolvio (sin esperar)."""
    return _dev_por_mint.get(str(mint))


async def dev_que_mintea(mint: str) -> str | None:
    """La cartera que minteo `mint` (ver arriba). None si no se puede saber."""
    mint = str(mint)
    if mint in _dev_por_mint:
        return _dev_por_mint[mint]
    tarea = _buscar_dev(mint)
    if tarea is None:
        return None
    try:
        dev = await asyncio.shield(tarea)
    except Exception:  # noqa: BLE001
        return None
    return dev or None


async def creator_migration_stats(
    creator: str, actual: str | None = None
) -> tuple[int, int, float]:
    """Return ``(launches, migrations, rate)`` for a pump.fun creator.

    ``migrations`` counts the creator's coins with ``complete == True``.
    Returns ``(0, 0, 0.0)`` on any error (fail-closed for a reputation filter).

    `actual` es la moneda que se esta valorando: no cuenta en sus lanzadas
    (5-nov, opcion A). Sin ella, se cuentan todas las vistas.
    """
    guardado = _con_lo_visto(creator, actual)
    if guardado is not None:
        return guardado

    tarea = _en_vuelo.get(creator)
    if tarea is None or tarea.done():
        tarea = asyncio.ensure_future(_consultar(creator))
        _en_vuelo[creator] = tarea
    try:
        resultado = await asyncio.shield(tarea)
    finally:
        if _en_vuelo.get(creator) is tarea and tarea.done():
            _en_vuelo.pop(creator, None)
    # IGUAL QUE SI VINIERA DE LO GUARDADO: recien leido, tambien se suman los
    # lanzamientos vistos que la lectura aun no incluye, y se deja fuera la
    # moneda que se esta valorando. Si la lectura fallo, no se guardo nada y
    # se devuelve tal cual (sin dato, no se compra).
    return _con_lo_visto(creator, actual) or resultado


def _con_lo_visto(creator: str, actual: str | None) -> tuple[int, int, float] | None:
    """Lo guardado del dev mas lo lanzado despues, sin `actual`. None si no hay."""
    hit = _cache.get(creator)
    if not hit or time.time() - hit[0] >= _TTL:
        return None
    lanzadas = hit[1] + _lanzadas_desde_la_foto(creator, hit[0], actual)
    migradas = hit[2]
    return lanzadas, migradas, (migradas / lanzadas if lanzadas else 0.0)


async def _consultar_por_la_cadena(
    creator: str, now: float
) -> tuple[int, int, float]:
    """Respaldo cuando pump.fun no contesta. Sin dato, (0, 0, 0): no compra."""
    leido = await _por_la_cadena(creator)
    if leido is None:
        return 0, 0, 0.0
    launches, migrations = leido
    rate = migrations / launches if launches else 0.0
    _guardar(creator, now, launches, migrations, rate)
    return launches, migrations, rate


async def _consultar(creator: str) -> tuple[int, int, float]:
    global _pumpfun_bloqueado_hasta  # noqa: PLW0603
    now = time.time()
    if now < _pumpfun_bloqueado_hasta:
        return await _consultar_por_la_cadena(creator, now)
    url = f"{_API}?creator={creator}&limit=200&offset=0"
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=_HEADERS) as resp:
                if resp.status != 200:
                    # ANTES ESTO ERA UN 0 CALLADO: "0 migr / 0 lanz" y descarte,
                    # sin que nada dijera que pump.fun no habia contestado.
                    logger.warning(
                        "pump.fun contesta %s al pedir el historial del dev; "
                        "se usa la cadena (Helius)", resp.status,
                    )
                    _pumpfun_bloqueado_hasta = now + _PAUSA_PUMPFUN
                    return await _consultar_por_la_cadena(creator, now)
                data = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001
        logger.warning("pumpfun creator stats failed for %s", creator[:8])
        return await _consultar_por_la_cadena(creator, now)

    coins = data if isinstance(data, list) else []
    launches = len(coins)
    migrations = sum(1 for c in coins if c.get("complete") is True)
    rate = migrations / launches if launches else 0.0
    picos = []
    for c in coins:
        if c.get("complete") is not True:
            continue
        for campo in ("ath_market_cap", "ath_market_cap_usd", "usd_market_cap"):
            v = c.get(campo)
            if isinstance(v, (int, float)) and v > 0:
                picos.append(float(v))
                break
    _picos[creator] = sorted(picos, reverse=True)
    _guardar(creator, now, launches, migrations, rate)
    return launches, migrations, rate


def monedas_por_encima(creator: str, min_mc: float) -> int:
    """Cuantas monedas MIGRADAS del creador superaron ``min_mc`` dolares.

    Se lee de lo que dejo ``creator_migration_stats``; no hace red. Devuelve 0
    si de ese creador no se ha consultado nada, que es el fallo correcto para
    un filtro de reputacion: sin dato, no es elite.
    """
    return sum(1 for p in _picos.get(creator, ()) if p >= min_mc)


def mejor_pico(creator: str) -> float:
    """El pico mas alto de una moneda migrada del creador. 0 si no hay dato."""
    picos = _picos.get(creator) or []
    return picos[0] if picos else 0.0


def mediana_pico(creator: str, min_monedas: int = 2) -> float:
    """Mediana de picos de las monedas MIGRADAS del creador.

    0 si hay menos de ``min_monedas`` migradas: con una sola no es mediana,
    es la suerte de esa moneda. Igual que en ``mejor_pico``, solo cuentan las
    migradas -- el resto se filtra ya en ``_consultar``.
    """
    picos = _picos.get(creator) or []
    if len(picos) < max(1, min_monedas):
        return 0.0
    picos_ordenados = sorted(picos)
    n = len(picos_ordenados)
    mitad = n // 2
    if n % 2:
        return picos_ordenados[mitad]
    return (picos_ordenados[mitad - 1] + picos_ordenados[mitad]) / 2
