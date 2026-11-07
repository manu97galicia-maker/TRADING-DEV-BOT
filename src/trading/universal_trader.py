"""
Universal trading coordinator that works with any platform.
Cleaned up to remove all platform-specific hardcoding.
"""

import asyncio
import copy
import json
import re
import subprocess
import sys
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import ClassVar

from solders.pubkey import Pubkey

from cleanup.modes import (
    handle_cleanup_after_failure,
    handle_cleanup_after_sell,
    handle_cleanup_post_session,
)
from core.client import SolanaClient
from core.priority_fee.manager import PriorityFeeManager
from core.pubkeys import (
    LAMPORTS_PER_SOL,
    TOKEN_2022_PROGRAM,
    TOKEN_DECIMALS,
    TOKEN_PROGRAM,
)
from core.wallet import Wallet
from core.wallet_pool import WalletPool
from dexscreener import DexScreenerFilter
from interfaces.core import Platform, TokenInfo
from monitoring import volumen
from monitoring.dev_blocklist import any_dev_blocked
from monitoring.listener_factory import ListenerFactory
from monitoring.volumen import precio_en_vivo
from platforms import get_platform_implementations
from trading.base import TradeResult
from trading.ganancia import Ganancia
from trading.panic import panic_time
from trading.platform_aware import PlatformAwareBuyer, PlatformAwareSeller
from trading.position import Position
from trading.reserva import reserva as _reserva_moneda
from trading.reserva import marca_comprada as _marca_comprada
from trading.reserva import suelta as _suelta_moneda
from trading.tiered_exit import (
    build_tiers,
    moonbag_fraction,
    stepped_stop_price,
    tiers_due,
)
from utils.logger import get_logger

# Try to use uvloop on Unix or winloop on Windows for better performance
# Fall back to standard asyncio if not available
try:
    if sys.platform == "win32":
        import winloop

        asyncio.set_event_loop_policy(winloop.EventLoopPolicy())
    else:
        import uvloop

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    # Standard asyncio is fine, just slightly slower
    pass

logger = get_logger(__name__)

# Por-posición: la wallet y el seller elegidos del pool para ESTA compra.
# Cada tarea asyncio hereda su propio contexto, así que dos posiciones
# simultáneas nunca se pisan entre sí.
_ctx_wallet: ContextVar[Wallet | None] = ContextVar("_ctx_wallet", default=None)
_ctx_seller: ContextVar[object | None] = ContextVar("_ctx_seller", default=None)


def identidad_del_dev(token_info: object) -> str | None:
    """A quien se le mira el historial de migradas: QUIEN CREO la moneda.

    5-nov ("Arreglarlo"): el filtro preguntaba por `creator`, que en las
    monedas reconstruidas desde la curva es quien COBRA las comisiones. Pump.fun
    puede cambiarlo a la cuenta de reparto (PDA "sharing-config" de Pump Fees),
    que no ha creado ninguna moneda: pump.fun contestaba 0 lanzadas y el filtro
    descartaba a todos. Ahora va primero `user` (quien firmo la creacion, ver
    `_dev_creacion` en monitoring/volumen.py) y nunca la cuenta de reparto.
    Devuelve None si solo se conoce esa cuenta: dev desconocido, no se compra.
    """
    from trading.pump_fee_identity import sharing_config_address  # noqa: PLC0415

    try:
        reparto = sharing_config_address(token_info.mint)
    except (ValueError, TypeError, AttributeError):
        reparto = None
    for campo in ("user", "creator"):
        valor = getattr(token_info, campo, None)
        if valor and str(valor) != reparto:
            return str(valor)
    return None


def _load_creator_file(path: str) -> set[str]:
    """Load wallet addresses from a ``.json`` or ``.txt`` file.

    ``.txt``: one address per line (``#`` lines and blanks ignored).
    ``.json``: an array of address strings, an array of objects each carrying
    an ``address``/``wallet``/``creator`` key, a ``{"wallets": [...]}`` wrapper,
    or a plain ``{address: {...}}`` mapping.
    """
    p = Path(path)
    if not p.is_file():
        logger.warning("Creator watch file not found: %s", path)
        return set()
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".txt":
        return {
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        }
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Creator watch file is not valid JSON: %s", path)
        return set()

    def _addr(item: object) -> str | None:
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            val = item.get("address") or item.get("wallet") or item.get("creator")
            return str(val).strip() if val else None
        return None

    out: set[str] = set()
    if isinstance(data, list):
        out = {a for a in (_addr(it) for it in data) if a}
    elif isinstance(data, dict):
        if isinstance(data.get("wallets"), list):
            out = {a for a in (_addr(it) for it in data["wallets"]) if a}
        else:
            out = {str(k).strip() for k in data if str(k).strip()}
    return out


# Precio SOL/USD usado para escalar la mediana histórica (misma escala con la
# que se calcularon los `median_peak` de las listas Dune). Da igual el precio
# real de SOL: la comparación mediana_vs_MC_actual es un RATIO, así que la
# constante se cancela mientras sea la misma a ambos lados.
#: Cuantas vueltas de 10 s se aguantan con el valor por defecto antes de
#: avisar por Telegram. 30 son cinco minutos.
_AVISAR_TRAS_VUELTAS = 30

_SOL_USD = 91.24  # actualizado 21-ago. OJO: cambiar esto mueve TODOS los
#                   filtros de market cap, porque el MC se calcula como
#                   precio x supply x _SOL_USD. Si se sube, hay que subir los
#                   techos en la misma proporcion o el filtro se vuelve mas
#                   estricto sin que nadie lo haya decidido.
# Supply total estándar de un token pump.fun (1e9 tokens enteros).
#: Copia del valor de arranque: sirve para saber si el refresco ya acerto.
_ARRANQUE_SOL_USD = _SOL_USD

_PUMP_TOTAL_SUPPLY = 1_000_000_000

#: Tope de seguridad de posiciones abiertas cuando `max_posiciones` es 0.
#: No pretende ser el limite util -- ese lo pone el saldo -- sino evitar que
#: un fallo raro abra cientos. Antes era un 20 suelto dentro del codigo, que
#: con "0" configurado se convertia en un tope invisible.
TOPE_DURO_POSICIONES = 40


class _CurvaMigrada(Exception):  # noqa: N818
    """La moneda ya no vive en la curva: su precio hay que pedirlo al DEX.

    No es un error, es un desvio: se lanza para entrar en la misma rama de
    respaldo que ya existia, sin tener que duplicarla ni preguntarle a una
    curva vacia una vez por segundo.
    """



def _peldanos_extra(
    extra: list[dict] | None,
) -> list[tuple[float | None, float | None]]:
    """Convierte la lista `tp_extra` del YAML en pares (ganancia, venta).

    Existe para no seguir anadiendo tp7_gain, tp8_gain... uno a uno: cada
    objetivo nuevo obligaba a tocar tres ficheros. Se salta en silencio lo
    que no tenga los dos campos, porque una entrada a medias en la config
    no puede tumbar el arranque del bot.
    """
    pares: list[tuple[float | None, float | None]] = []
    for entrada in extra or []:
        if not isinstance(entrada, dict):
            continue
        ganancia, venta = entrada.get("gain"), entrada.get("sell")
        if ganancia is None or venta is None:
            logger.warning("Peldano extra incompleto, se ignora: %s", entrada)
            continue
        pares.append((float(ganancia), float(venta)))
    return pares


def _load_creator_medians(value: object) -> dict[str, float]:
    """Devuelve ``{wallet: median_peak_usd}`` desde un JSON de watchlist.

    Solo aplica a ficheros ``.json`` cuyos items lleven ``median_peak`` (las
    listas de devs generadas por Dune). Cualquier otro caso -> dict vacío
    (sin datos de mediana => la excepción del filtro de holders no se activa).
    """
    if not isinstance(value, str):
        return {}
    s = value.strip()
    if not s.lower().endswith(".json"):
        return {}
    p = Path(s)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    items = data.get("wallets") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {}
    out: dict[str, float] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        addr = it.get("wallet") or it.get("address") or it.get("creator")
        med = it.get("median_peak")
        if addr and med:
            try:
                out[str(addr).strip()] = float(med)
            except (TypeError, ValueError):
                continue
    return out


def _load_creator_bestpeak(value: object) -> dict[str, float]:
    """Devuelve ``{wallet: best_peak_usd}`` de la watchlist. Solo lo llevan los
    devs moonshot (>=2 monedas >$120K); se usa para darles un TP más agresivo."""
    if not isinstance(value, str) or not value.strip().lower().endswith(".json"):
        return {}
    p = Path(value.strip())
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    items = data.get("wallets") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {}
    out: dict[str, float] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        addr = it.get("wallet") or it.get("address") or it.get("creator")
        bp = it.get("best_peak_usd")
        if addr and bp:
            try:
                out[str(addr).strip()] = float(bp)
            except (TypeError, ValueError):
                continue
    return out


def _load_creator_launches(value: object) -> dict[str, int]:
    """Devuelve ``{wallet: launches_7d}`` desde un JSON de watchlist (para mostrar
    el nº de lanzamientos del dev en las señales de Telegram)."""
    if not isinstance(value, str) or not value.strip().lower().endswith(".json"):
        return {}
    p = Path(value.strip())
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    items = data.get("wallets") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {}
    out: dict[str, int] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        addr = it.get("wallet") or it.get("address") or it.get("creator")
        n = it.get("launches_7d")
        if addr and n:
            try:
                out[str(addr).strip()] = int(n)
            except (TypeError, ValueError):
                continue
    return out


def _normalize_creators(value: object) -> frozenset[str] | None:
    """Normalize a ``bro_address`` config value into a set of wallets to watch.

    Accepts a single address string, a path to a ``.json``/``.txt`` file of
    addresses, or a list of addresses. Returns ``None`` (no creator filter)
    for empty/``"null"`` values so the old single-wallet behavior is preserved.
    """
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() == "null":
            return None
        if s.lower().endswith((".json", ".txt")):
            # A watch file was configured: return the set even if empty. An empty
            # result means "match nothing" (safe), NOT "no filter" — otherwise an
            # unfilled watch list would snipe every token on the platform.
            watched = frozenset(_load_creator_file(s))
            if not watched:
                logger.warning(
                    "Creator watch file %s resolved to 0 wallets; the bot will "
                    "buy NOTHING until it is populated.",
                    s,
                )
            return watched
        return frozenset({s})
    if isinstance(value, (list, tuple, set, frozenset)):
        # Lists may mix literal wallets and watch-list files.  This lets a
        # strategy safely extend a curated list without copying/staling it.
        watched: set[str] = set()
        for item in value:
            s = str(item).strip()
            if not s:
                continue
            if s.lower().endswith((".json", ".txt")):
                watched.update(_load_creator_file(s))
            else:
                watched.add(s)
        return frozenset(watched)
    return None


class UniversalTrader:
    """Universal trading coordinator that works with any supported platform."""

    #: Ventas fallidas seguidas sobre el mismo token antes de rendirse. Bajo a
    #: proposito: una venta que falla paga la comision de prioridad igual, y el
    #: bucle de vigilancia reintenta cada segundo. Si no sale en 3, no sale.
    MAX_FALLOS_VENTA = 3
    #: Espera antes de volver a intentar una venta que ya fallo tres veces.
    #: Antes esto no existia: al tercer fallo la moneda quedaba bloqueada PARA
    #: SIEMPRE, y con ella el stop loss. Ahora se espera y se reintenta, con la
    #: espera doblandose en cada fallo nuevo.
    #:
    #: Las que limitan perdidas esperan poco: si el stop loss no entra, lo que
    #: se pierde es mucho mas que la comision de reintentarlo. Los take profit
    #: esperan mucho: pueden esperar, y cada intento cuesta.
    ESPERA_PROTECTORA_SEG = 20.0
    ESPERA_TP_SEG = 300.0
    ESPERA_MAX_SEG = 1800.0

    #: Motivos de venta que existen para LIMITAR PERDIDAS. Nunca se abandonan.
    MOTIVOS_PROTECTORES = (
        "stop_loss", "trailing_stop", "anti_rug", "panic_sell",
        "bajo_entrada", "suelo_por_mc", "muerta", "max_hold_time", "manual_",
    )

    #: Lecturas seguidas sin precio (ni de la curva ni del DEX) antes de
    #: soltar la posicion. A un precio cada 3 s son unos 2 minutos. Con el
    #: bucle infinito de antes, BECKY tuvo a RAPIDA hora y media sin poder
    #: comprar nada porque solo admite una posicion a la vez.
    MAX_SIN_PRECIO = 40

    def __init__(
        self,
        rpc_endpoint: str,
        wss_endpoint: str,
        private_key: str,
        buy_amount: float,
        buy_slippage: float,
        sell_slippage: float,
        sell_slippage_proteccion: float = 0.0,
        # Platform configuration
        platform: Platform | str = Platform.PUMP_FUN,
        # Listener configuration
        listener_type: str = "logs",
        geyser_endpoint: str | None = None,
        geyser_api_token: str | None = None,
        geyser_auth_type: str = "x-token",
        pumpportal_url: str = "wss://pumpportal.fun/api/data",
        # Trading configuration
        extreme_fast_mode: bool = False,
        extreme_fast_token_amount: int = 30,
        # Exit strategy configuration
        exit_strategy: str = "time_based",
        # Los dos avisos de booleano posicional se silencian para no
        # sumar al recuento del fichero: este constructor ya tiene 19
        # iguales y siempre se llama con nombre desde bot_runner.
        pausar_compras: bool = False,  # noqa: FBT001, FBT002
        max_subida_global: float = 0.0,
        subida_seg_global: float = 10.0,
        min_compradores_global: int = 0,
        resumen_descartes_seg: float = 300.0,
        precio_stream_seg: float = 0.0,
        tick_stream_seg: float = 0.25,
        take_profit_percentage: float | None = None,
        stop_loss_percentage: float | None = None,
        max_hold_time: int | None = None,
        abandonar_tras_seg: int | None = None,
        # MONEDA MUERTA: a los N segundos, si no sube al menos `venta_muerta_gain`,
        # se vende entera. 0 = desactivado.
        venta_muerta_seg: float = 0.0,
        venta_bajo_entrada_seg: float = 0.0,
        venta_muerta_gain: float = 0.0,
        # SLIPPAGE QUE SUBE TRAS COMPRAS FALLIDAS (7-nov, Manu: "si hay tres
        # fallidas sube el slippage a 14%"). {"fallidas": 3, "slippage": 0.14}.
        # None o vacio = desactivado.
        subir_slippage_tras_fallidas: dict | None = None,
        # Tiered take-profit ladder (tier 1 = take_profit_percentage +
        # take_profit_sell_percentage; tiers 2/3 below). gain is fraction
        # above entry (x2 = 1.0), sell is fraction of the original position.
        take_profit_sell_percentage: float | None = None,
        tp2_gain: float | None = None,
        tp2_sell: float | None = None,
        tp3_gain: float | None = None,
        tp3_sell: float | None = None,
        # Cuarto tramo (3-ago): la escalera de Manu tiene 4 objetivos
        # (+50/+90/+150/+300) mas moonbag, y solo cabian 3.
        tp4_gain: float | None = None,
        tp4_sell: float | None = None,
        tp5_gain: float | None = None,
        tp5_sell: float | None = None,
        tp6_gain: float | None = None,
        tp6_sell: float | None = None,
        tp_extra: list[dict] | None = None,
        final_target_mc: float = 0.0,
        # STOP POR MARKET CAP absoluto: vender si el MC cae a este
        # valor, independientemente de a que precio se entro. 0 = off.
        mc_stop_loss: float = 0.0,
        mc_take_profit_tiers: list | None = None,
        # SUELOS QUE SUBEN CON LA MONEDA: [{alcanza, suelo}, ...] en dolares
        # de market cap. Al tocar `alcanza`, se arma un suelo en `suelo`.
        suelos_por_mc: list | None = None,
        # Rugs acumulados que bloquean a un dev para siempre.
        rugs_para_bloquear: int = 2,
        # Mover el stop al precio de entrada en cuanto cobra el primer TP.
        stop_a_entrada_tras_tp: bool = False,
        margen_stop_tras_tp: float = 0.0,
        # Mover el stop al precio objetivo del TP que acaba de ejecutarse.
        stop_al_tp_tras_tp: bool = False,
        # Segunda puerta de entrada: {max_mc, min_volumen_sol}. Si la moneda
        # se paso de max_entry_mc pero sigue por debajo de este max_mc Y ha
        # movido ese volumen, se compra igual.
        entrada_alternativa: dict | None = None,
        price_check_interval: int = 10,
        # Priority fee configuration
        enable_dynamic_priority_fee: bool = False,
        enable_fixed_priority_fee: bool = True,
        fixed_priority_fee: int = 200_000,
        #: Comision SOLO para comprar. 0 = usar la de siempre para todo.
        #: Existe porque comprar y vender no se pagan igual de veces: una
        #: posicion se compra UNA vez y se vende hasta DIEZ, una por peldano
        #: de la escalera. Una comision que merece la pena para entrar a
        #: tiempo se convierte, multiplicada por diez, en un mordisco real
        #: a la ganancia.
        fixed_priority_fee_compra: int = 0,
        fixed_priority_fee_venta_proteccion: int = 0,
        extra_priority_fee: float = 0.0,
        hard_cap_prior_fee: int = 200_000,
        # Retry and timeout settings
        max_retries: int = 3,
        wait_time_after_creation: int = 15,
        wait_time_after_buy: int = 15,
        wait_time_before_new_token: int = 15,
        max_token_age: int | float = 0.001,
        token_wait_timeout: int = 30,
        # Cleanup settings
        cleanup_mode: str = "disabled",
        cleanup_force_close_with_burn: bool = False,
        cleanup_with_priority_fee: bool = False,
        # Trading filters
        match_string: str | None = None,
        bro_address: str | None = None,
        marry_mode: bool = False,
        yolo_mode: bool = False,
        # DexScreener buy filter (disabled unless configured)
        dexscreener_config: dict | None = None,
        # Buy-on-dex-paid acquisition mode (disabled unless configured)
        buy_on_dex_paid: dict | None = None,
        birdeye_trending: dict | None = None,
        dextools_hotpools: dict | None = None,
        curva_96: dict | None = None,
        # PRESION EN LOS DEX (7-nov): PumpSwap, Raydium y Meteora/Bags, con
        # liquidez bloqueada. Ver monitoring/dex_presion_source.py.
        dex_presion: dict | None = None,
        presion: dict | None = None,
        holders: dict | None = None,
        # Telegram-calls acquisition mode (disabled unless configured)
        telegram_calls: dict | None = None,
        telegram_only: bool = False,
        discord_calls: dict | None = None,
        # GMGN smart-money/KOL signals (disabled unless configured)
        gmgn_signals: dict | None = None,
        # Queue a Pump.fun callout after a confirmed buy.
        callouts: dict | None = None,
        # Paper-trade mode: filter + record, never buy
        paper_trade: bool = False,
        # New safe simulator configuration; disabled unless explicitly enabled.
        paper_trading_config: dict | None = None,
        # Bundle detector: min SOL already in the bonding curve to consider a buy
        min_curve_sol: float = 0.0,
        # Tope de market cap (USD) al entrar: no comprar si la moneda ya está
        # pumpeada por encima (evita comprar el techo de un spike). 0 = sin tope.
        max_entry_mc: float = 0.0,
        min_entry_mc: float = 0.0,
        # Anti-concentración: no comprar si un holder (excluida la curva) tiene
        # más de esta fracción del supply (0 = desactivado).
        max_holder_pct: float = 0.0,
        max_bundle_pct: float = 0.0,
        # Anti dump-inicial: fracción máxima del supply que puede tener EL
        # CREADOR en el momento de comprar. Los otros filtros miran holders
        # anónimos; éste mira al único que sabe cuándo va a vender. 0 = off.
        max_dev_hold_pct: float = 0.0,
        # Al reves que el anterior: fraccion MINIMA del supply que tiene
        # que tener EL CREADOR para comprar. La tesis es que un dev con
        # piel en el juego no tira la moneda a los dos minutos. 0 = off.
        min_dev_hold_pct: float = 0.0,
        # Filtro orgánico: mínimo de poseedores distintos para comprar (menos =
        # bundle/dev-spam sin compras reales). 0 = desactivado.
        min_holders: int = 0,
        require_zero_initial_holders: bool = False,
        # Excepción al límite de holders: si el token ya subió >= este ratio
        # (ej. 3 = x3) el holder grande es momentum real (compró a mercado), no
        # un bundle -> se permite comprar. 0 = sin excepción.
        holder_bypass_ratio: float = 0.0,
        # Titulares mínimos SEGÚN GMGN. Distinto de `min_holders`, que se saca de
        # getTokenLargestAccounts y por eso NO PUEDE PASAR DE 19: ese RPC
        # devuelve como mucho 20 cuentas y una es la curva. Cualquier umbral de
        # 20 o más por esa vía rechaza el 100% de las monedas y el bot no compra
        # nunca. GMGN da el número real y sin tope (medido: 25 titulares a los
        # 16 segundos), así que los umbrales altos van por aquí.
        min_holders_gmgn: int = 0,
        # Volumen NEGOCIADO minimo (SOL) contado del stream de logs desde que
        # nacio la moneda. Distinto de `min_curve_sol`, que es el dinero NETO
        # dentro: el volumen suma compras Y ventas, asi que puede ser varias
        # veces mayor y no lo limita el techo de market cap.
        min_volumen_sol: float = 0.0,
        # COMPRAS minimas antes de entrar. A diferencia de `min_ops`, no cuenta
        # las ventas, por lo que reproduce el campo "Buys" de GMGN.
        min_buys: int = 0,
        # Compras que deben estar incluidas en el primer slot/bloque observado
        # para la moneda. 0 = desactivado.
        min_first_block_buys: int = 0,
        max_first_block_buys: int = 0,
        # Rango de USD comprados en el bloque de creación. Se convierte usando
        # la misma cotización SOL/USD viva que emplea el resto del trader.
        min_first_block_usd: float = 0.0,
        max_first_block_usd: float = 0.0,
        # No permite decidir la entrada hasta observar una operación en un slot
        # posterior al de creación, de modo que el primer bloque ya está cerrado.
        buy_from_second_block: bool = False,
        second_block_confirmation: dict | None = None,
        # TRANSACCIONES minimas antes de comprar, contadas del stream de logs.
        # Cuenta cada TradeEvent por separado, tambien los que entran juntos en
        # el primer bloque (un bundle son N transacciones y aqui salen como N,
        # no como una). Mide participacion, no dinero: 14 SOL en UNA compra es
        # el dev inflandose la propia moneda; 14 SOL repartidos en 6 compras es
        # gente entrando. 0 = desactivado.
        min_ops: int = 0,
        # TITULARES minimos contados del stream: wallets distintas cuyas compras
        # superan a sus ventas. Es el mismo dato que `min_holders_gmgn` pero sin
        # coste ni retraso -- GMGN tarda 1,3 s y a los 3 segundos de vida no
        # conoce a 1 de cada 3 monedas, asi que a esta edad es el unico que
        # sirve. 0 = desactivado.
        min_titulares: int = 0,
        # TECHO de carteras distintas. Suena al reves y no lo es: medido sobre
        # 279 monedas contra su maximo real, las que juntan 30 o mas carteras
        # en el primer segundo rinden PEOR que las de 15-29 (39% llega al +30%
        # frente al 54%, y ninguna migro). Treinta compradores distintos en un
        # segundo no son treinta personas: es una granja repartida en carteras
        # para que no parezca un bundle. Es justo la trampa que min_titulares
        # no ve. 0 = sin techo.
        max_titulares: int = 0,
        # COMPRAS POR CARTERA: cuantas veces compra de media cada mano distinta.
        # Es el segundo patron mas fuerte medido el 11-ago, y el mas dificil de
        # falsear. Entre 1,3 y 1,8 el 90% de las monedas llega al +30%; por
        # debajo de 1,3 baja al 73%.
        #
        # Lo que distingue: por debajo de 1,3 cada cartera compra una vez y se
        # va -- es un reparto, no interes. Muy por encima de 2 es una sola mano
        # dandole al boton para inflar el contador de operaciones. En medio esta
        # lo que parece gente: entras, miras, y algunos repiten.
        #
        # Chief, la que Manu quiere repetir, tenia 32 compras entre 21 carteras:
        # 1,5. Los dos a 0 = desactivado.
        min_compras_por_cartera: float = 0.0,
        max_compras_por_cartera: float = 0.0,
        # Exigir que la moneda traiga redes sociales SUYAS: un enlace a una
        # cuenta (no al tuit de otro, que es lo que trae la mayoria) y que esa
        # cuenta no la haya usado ya otra moneda en las ultimas 6 horas.
        redes_originales: bool = False,
        redes_timeout: float = 1.5,
        # TECHO de volumen negociado (SOL). Medido la noche del 3/4-ago sobre
        # operaciones reales: las monedas con MUCHO volumen en los primeros
        # segundos salieron todas mal (10 de 10 en perdida con 69-100 SOL),
        # mientras que las que apenas se habian movido fueron las que volaron
        # (FEETCATE: 16 SOL de volumen -> x15,9). Mucho movimiento tan pronto
        # significa que ya entro y salio gente y llegas al final. 0 = sin tope.
        max_volumen_sol: float = 0.0,
        # ACELERACION: SOL negociados en los ULTIMOS `vol_ventana_seg` segundos.
        # Distinto del volumen total: una moneda con 30 SOL en 40 segundos se
        # esta apagando y otra con 30 en 3 esta explotando, y hasta ahora el bot
        # las trataba igual. Las que volaron la noche del 3/4-ago cumplian el
        # volumen en 1-2 segundos; las que se arrastraban 19-24 s no fueron a
        # ningun lado. 0 = sin filtro.
        min_vol_ventana_sol: float = 0.0,
        vol_ventana_seg: float = 3.0,
        # PRESION COMPRADORA EN LA ENTRADA. Cuantas veces mas SOL se compran
        # que se venden en los ultimos `presion_seg` segundos. 0 = no se mira.
        # El volumen solo dice que hay movimiento; esto dice hacia donde va, y
        # evita entrar justo cuando el dinero esta saliendo.
        # LAS ULTIMAS N OPERACIONES TIENEN QUE SER COMPRAS. Distinto de
        # `min_presion_ratio`, que compara SOL comprado contra vendido: la
        # presion puede salir bien mientras alguien va saliendo por detras.
        # Esto exige que NADIE haya vendido en las ultimas operaciones.
        #
        # Medido el 18-ago sobre 92 monedas: las que tenian `tx/compras < 1,2`
        # --practicamente sin ventas-- hacian x2 el 41% de las veces, frente al
        # 17% de base. Es el rasgo que mas separaba de todos los mirados.
        # 0 = no se mira.
        ultimas_tx_compra: int = 0,
        min_presion_ratio: float = 0.0,
        presion_seg: float = 3.0,
        # Maximo de market cap en dolares por cada SOL que hay DE VERDAD en
        # la curva. 0 = no se mira. Es el filtro anti-humo.
        max_mc_por_sol_curva: float = 0.0,
        # Perdidas seguidas antes de que la estrategia deje de comprar.
        # 0 = nunca para.
        max_perdidas_seguidas: int = 0,
        # TOPE DE POSICIONES ENTRE TODOS LOS BOTS (7-nov, Manu: "maximo de
        # posiciones abiertas 4"). Cuenta los marcadores de todas las
        # estrategias que corren en esta carpeta. 0 = sin tope comun.
        max_posiciones_total: int = 0,
        # Importe reducido para la compra inmediatamente despues de un SL.
        # 0 = usar siempre el importe normal.
        buy_amount_tras_sl: float = 0.0,
        # Porcentaje MINIMO del supply que tiene que estar ya en manos de
        # compradores, es decir, fuera de la curva. 0 = no se mira.
        min_supply_en_manos: float = 0.0,
        # Fraccion MAXIMA de las compras que puede haber ocurrido en el
        # bloque de creacion. 0 = no se mira.
        max_first_block_pct: float = 0.0,
        # Posiciones abiertas a la vez. 0 = sin tope propio (manda el saldo).
        max_posiciones: int = 0,
        # DEVS MARCADOS: fichero de wallets que se han ganado mas confianza.
        # No deciden si se compra, solo cuanto y con que paciencia.
        devs_marcados: str | None = None,
        buy_amount_marcados: float = 0.0,
        # Si los devs marcados se saltan el AUTO-BANEO por rugs. True es lo de
        # siempre; dev1 lo pone en False (7-nov) porque Manu pidio "bloc dev
        # rugs" y la lista de marcados no puede deshacerlo en silencio.
        marcados_saltan_baneo: bool = True,
        # Techo absoluto de gasto por compra, en SOL, por encima de cualquier
        # otra via. Ver el comentario del TOPE DURO mas abajo.
        tope_compra_sol: float = 0.0,
        # TAMANO COMO FRACCION DEL SALDO en vez de un numero fijo. 0,70 = se
        # compra con el 70% de lo que quede libre despues de la reserva. Se
        # calcula en el momento de comprar y dentro del cerrojo de gasto, asi
        # que sube solo cuando la wallet sube y no hay que reeditar el yaml.
        fraccion_saldo: float = 0.0,
        fracciones_saldo: list[float] | None = None,
        buy_amount_usd: float = 0.0,
        fraccion_base: str = "cartera",
        fraccion_si_no_llega: float = 0.0,
        # Suelo por debajo del cual NO se compra. Las comisiones de pump.fun y
        # la renta de la cuenta de token son fijas: en una compra de 0,02 SOL
        # se llevan mas del 15% de ida y vuelta, asi que hace falta un +15%
        # solo para no perder. Comprar por debajo de esto es moler el saldo.
        compra_minima_sol: float = 0.0,
        escala_por_sol_ganado: float = 0.0,
        reiniciar_tanda: bool = True,
        # FRANJA HORARIA en la que se opera, hora de España. Fuera de ella el
        # bot sigue mirando el flujo pero no compra. {inicio: 18, fin: 2} son
        # las seis de la tarde a las dos de la madrugada -- cruza medianoche, y
        # eso hay que tratarlo aparte: 18 <= h < 24 O 0 <= h < 2.
        #
        # No se usa la hora del servidor: el droplet va en UTC y España esta en
        # UTC+2 en verano y UTC+1 en invierno. Con la hora del servidor la
        # franja se correria sola al cambiar la hora.
        horario: dict | None = None,
        # TRATO APARTE PARA LOS CREADORES BUENOS. Cuando el dev supera
        # `min_rate`, la posicion se gestiona con este stop y esta escalera en
        # vez de con los generales: si alguien ha migrado 4 de cada 10, se le
        # aguanta una caida que a un desconocido no se le aguantaria.
        dev_elite: dict | None = None,
        # SEGUNDA PUERTA POR MEDIANA. dev_elite exige 2+ monedas por encima de
        # un pico fijo (pensado para picos de 1M+). Esto es distinto: exige
        # que la MEDIANA de sus migradas ya supere `min_mediana_mc`, y a
        # cambio deja entrar hasta `max_entry_mc` (mas alto que el techo
        # general) sin tocar tamano ni stop -- el trato es solo el techo.
        dev_mediana: dict | None = None,
        # COMPRA GRANDE DEL PROPIO CREADOR: {min_sol}. Independiente de
        # pumpfun_dev -- no mira historial, mira el dinero de verdad puesto
        # en ESTE lanzamiento por su creador. Ver el comentario en el punto
        # donde se usa (busca "dev-buy").
        dev_buy: dict | None = None,
        # COMPRAR EL RETROCESO: {pico_min_mc, pico_max_mc, actual_min_mc,
        # actual_max_mc, ventana_seg}. No mira el nacimiento -- vigila
        # monedas ya vistas hasta `ventana_seg` despues de nacer, y compra
        # si llegaron a tener un pico dentro de la banda alta y AHORA estan
        # dentro de la banda baja (ya recogieron beneficios y volvieron a
        # bajar). Pedido 26-ago: "las que subieron a 5-6K y ahora estan en
        # 1,2K". Vende siempre al x2 desde ESTA compra, no desde el pico.
        dip: dict | None = None,
        # Techo del max_sol_cost de la compra, incluido el slippage.
        tope_compra_total_sol: float = 0.0,
        # Selector automatico: con 10 operaciones cerradas por estrategia deja
        # viva solo la de mejor retorno REAL. Ver src/trading/selector.py.
        usar_selector: bool = False,
        nombre_estrategia: str = "",
        tp_marcados: float = 0.0,
        tp_sell_marcados: float = 0.0,
        # Caida desde el MAXIMO que dispara el trailing. Si no se pone, se
        # usa stop_loss_percentage, que es como funcionaba antes.
        trailing_stop_percentage: float = 0.0,
        # Si el stop-loss se lleva TAMBIEN el moonbag. Por defecto no, que
        # es como se ha comportado siempre.
        vender_todo_en_stop: bool = False,
        # Firma de demanda externa en dos tiempos. Se configura como bloque y
        # queda desactivada por defecto para no cambiar bots existentes.
        second_wave: dict | None = None,
        # ANTI-RUG: sale de la posicion si las ventas aplastan a las compras.
        # ratio 2.0 = se vende el doble de lo que se compra. 0 = desactivado.
        # Señales de GMGN exigidas para comprar (lista de tipos). El 18 es el
        # unico que salta en la banda de compra: medido el 4/5-ago, 2,00x
        # mediano con 86% de acierto y el 44% de las veces por debajo de
        # $4.000. Vacio = sin filtro de señales.
        senal_gmgn: list | None = None,
        anti_rug_ratio: float = 0.0,
        anti_rug_seg: float = 4.0,
        anti_rug_min_sol: float = 3.0,
        # No comprar una moneda si otra con el MISMO símbolo apareció en los
        # últimos N minutos: los copias en cadena son el patrón del que lanza
        # veinte clones a ver cuál prende. 0 = desactivado.
        no_repeat_name_min: float = 0.0,
        ignorar_listas_de_devs: bool = False,
        # Edad máxima EN EL MOMENTO DE COMPRAR. `max_token_age` se mira al sacar
        # la moneda de la cola, antes de la espera de estabilización, así que no
        # limita la edad real de entrada. 0 = desactivado.
        max_buy_age: float = 0.0,
        # Edad MINIMA para que min_entry_mc cuente como cumplido. 0 =
        # desactivado (se acepta desde el primer instante). Pedido 26-ago
        # para mayhemsimple: una moneda que YA nace por encima del suelo (en
        # vez de subir hasta el) es sospechosa de venir inflada de fabrica
        # (dev-buy grande, bundle), no de tener demanda real construyendose.
        min_buy_age: float = 0.0,
        # Reserva mínima de SOL que el bot nunca gasta (siempre deja esto).
        min_reserve_sol: float = 0.03,
        valor_minimo_posicion_usd: float = 1.0,
        # Hard lifetime cap on successful real buys for a cautious live trial.
        # It is derived from the persistent bought-mints file, so restarts do
        # not reset the limit. 0 = unlimited.
        max_real_buys: int = 0,
        # Pump.fun creator-migration filter (via pump.fun API); off unless set
        pumpfun_dev: dict | None = None,
        # Copy-trading: follow wallets and copy their pump.fun buys; off unless set
        copy_wallets: dict | None = None,
        # Allow pump.fun "mayhem mode" tokens (default False = always avoided)
        allow_mayhem: bool = False,
        # Buy ONLY mayhem-mode tokens (default False). Implies allow_mayhem.
        require_mayhem: bool = False,
        # Cuanto gastar en una mayhem. 0 = el mismo importe que el resto. Sirve
        # para tener las mayhem a prueba con una apuesta pequena.
        mayhem_buy_amount: float = 0.0,
        # Perdidas seguidas en mayhem antes de dejar de comprarlas. 0 = nunca.
        # No para el bot: las normales siguen operando.
        mayhem_max_perdidas: int = 0,
        # How long (seconds) a paper candidate's price is followed
        paper_watch_secs: int = 300,
        # Maximum candidates observed by this process in paper mode. 0 = no cap.
        paper_max_samples: int = 0,
        # Suffix for per-bot state files so two bots on the same wallet don't
        # clobber each other's dedup / open-position recovery.
        state_suffix: str = "",
        # Compute unit configuration
        compute_units: dict | None = None,
        # Node provider configuration
        max_rps: float = 25.0,
        # Multi-wallet pool: path to wallets_generadas.json (None = wallet única)
        wallets_json: str | None = None,
    ):
        """Initialize the universal trader."""
        # Core components
        self.solana_client = SolanaClient(rpc_endpoint, max_rps=max_rps)
        self.wallet = Wallet(private_key)
        self.wallet_pool: WalletPool | None = (
            WalletPool(wallets_json) if wallets_json else None
        )
        self.priority_fee_manager = PriorityFeeManager(
            client=self.solana_client,
            enable_dynamic_fee=enable_dynamic_priority_fee,
            enable_fixed_fee=enable_fixed_priority_fee,
            fixed_fee=fixed_priority_fee,
            extra_fee=extra_priority_fee,
            hard_cap=hard_cap_prior_fee,
        )
        #: Comision de compra. Se aplica como `priority_fee_override`, y ese
        #: camino NO pasa por `calculate_priority_fee`, o sea que NO lo recorta
        #: el `hard_cap`. Se recorta aqui a mano: un tope que se puede saltar
        #: por una puerta lateral no es un tope, y este existe para que una
        #: errata de un cero de mas no vacie la cartera en comisiones.
        #: COMISION RAPIDA SOLO PARA LAS VENTAS QUE PROTEGEN (24-sep).
        #:
        #: POR QUE NO SE SUBE LA DE TODAS LAS VENTAS. Una posicion se compra
        #: UNA vez y se vende hasta siete --cuatro peldanos de porcentaje y
        #: tres escalones de market cap-- asi que poner la comision rapida en
        #: todas seria pagarla siete veces por posicion.
        #:
        #: Y NO HACE FALTA: un take profit puede esperar. Si no sale en esta
        #: vuelta sale en la siguiente y la moneda sigue arriba. Un stop loss
        #: no puede esperar: cada segundo de cola es precio que se pierde, y
        #: la comision de venta normal es 42 veces menor que la de compra.
        #:
        #: Se aplica a los motivos de `MOTIVOS_PROTECTORES`, los mismos que ya
        #: llevan el suelo de venta ancho. 0 = apagado, todas pagan igual.
        self.fee_venta_proteccion = int(fixed_priority_fee_venta_proteccion or 0)
        if self.fee_venta_proteccion > hard_cap_prior_fee > 0:
            logger.warning(
                "La comision de venta protectora (%d) pasa del tope (%d); se "
                "recorta.", self.fee_venta_proteccion, hard_cap_prior_fee,
            )
            self.fee_venta_proteccion = int(hard_cap_prior_fee)
        self.fee_compra = int(fixed_priority_fee_compra or 0)
        if self.fee_compra > hard_cap_prior_fee > 0:
            logger.warning(
                "La comision de compra (%d) pasa del tope (%d); se recorta.",
                self.fee_compra, hard_cap_prior_fee,
            )
            self.fee_compra = int(hard_cap_prior_fee)

        # Platform setup
        if isinstance(platform, str):
            self.platform = Platform(platform)
        else:
            self.platform = platform

        logger.info(f"Initialized Universal Trader for platform: {self.platform.value}")

        # Validate platform support
        try:
            from platforms import platform_factory

            if not platform_factory.registry.is_platform_supported(self.platform):
                raise ValueError(f"Platform {self.platform.value} is not supported")
        except Exception:
            logger.exception("Platform validation failed")
            raise

        # Get platform-specific implementations
        self.platform_implementations = get_platform_implementations(
            self.platform, self.solana_client
        )

        # Store compute unit configuration
        self.compute_units = compute_units or {}

        # Create platform-aware traders
        self.buyer = PlatformAwareBuyer(
            self.solana_client,
            self.wallet,
            self.priority_fee_manager,
            buy_amount,
            buy_slippage,
            max_retries,
            extreme_fast_token_amount,
            extreme_fast_mode,
            compute_units=self.compute_units,
            # require_mayhem implies mayhem tokens must be buyable, so OR it in.
            allow_mayhem=bool(allow_mayhem) or bool(require_mayhem),
            require_mayhem=bool(require_mayhem),
            devs_marcados=set(devs_marcados or ()),
        )

        self.seller = PlatformAwareSeller(
            self.solana_client,
            self.wallet,
            self.priority_fee_manager,
            sell_slippage,
            max_retries,
            compute_units=self.compute_units,
        )

        # La fuente de "curva casi llena" abre su PROPIA suscripcion a los
        # logs: cuando hay fuentes de adquisicion activas el listener de abajo
        # no llega a arrancar (ver `start`), y sin logs no hay forma de saber
        # por donde va ninguna curva.
        self.wss_endpoint = wss_endpoint

        # Initialize the appropriate listener with platform filtering.
        #
        # UN LISTENER MAL CONFIGURADO NO PUEDE TUMBAR EL BOT. Pedir `geyser`
        # sin GEYSER_ENDPOINT y GEYSER_API_TOKEN levantaba un ValueError AQUI,
        # en el constructor: el bot no arrancaba, y con posiciones abiertas eso
        # significa que nadie las vigila -- ni stop loss ni take profit. Es un
        # precio desproporcionado por un fallo de configuracion, sobre todo
        # cuando `logs` (que si esta configurado) hace el mismo trabajo algo
        # mas lento. Se avisa a gritos en el log y se sigue.
        # LAS CREDENCIALES DE GEYSER, GUARDADAS. Hasta ahora solo se le pasaban
        # al escuchador de creaciones -- que NO arranca cuando hay fuentes de
        # compra activas--, asi que tener Geyser configurado no servia de nada
        # con la estrategia de titulares. La fuente las necesita para usar la
        # tuberia rapida.
        self.geyser_endpoint = geyser_endpoint or ""
        self.geyser_api_token = geyser_api_token or ""
        self.geyser_auth_type = geyser_auth_type or "x-token"
        try:
            self.token_listener = ListenerFactory.create_listener(
                listener_type=listener_type,
                wss_endpoint=wss_endpoint,
                geyser_endpoint=geyser_endpoint,
                geyser_api_token=geyser_api_token,
                geyser_auth_type=geyser_auth_type,
                pumpportal_url=pumpportal_url,
                platforms=[self.platform],  # Only listen for our platform
            )
        except ValueError as exc:
            if listener_type.lower() == "logs" or not wss_endpoint:
                raise
            # Se silencia TRY400 a proposito: el traceback no aporta nada
            # sobre un fallo de configuracion y el mensaje ya dice que falta.
            logger.error(  # noqa: TRY400
                "listener_type='%s' no se puede montar (%s). Se sigue con "
                "'logs', que es mas lento pero funciona. Revisa el .env si "
                "querias %s de verdad.",
                listener_type, exc, listener_type,
            )
            self.token_listener = ListenerFactory.create_listener(
                listener_type="logs",
                wss_endpoint=wss_endpoint,
                platforms=[self.platform],
            )

        # Trading parameters
        self.buy_amount = buy_amount
        self.buy_slippage = buy_slippage
        self.sell_slippage = sell_slippage
        #: Suelo de precio para las ventas que existen para limitar perdidas.
        #: Ver el comentario del yaml: con el suelo estrecho, un stop loss
        #: puede NO EJECUTARSE en una moneda que se desploma, que es cuando
        #: mas falta hace. 0 = usar el mismo que el resto.
        self.sell_slippage_proteccion = float(sell_slippage_proteccion or 0)
        self.max_retries = max_retries
        self.extreme_fast_mode = extreme_fast_mode
        self.extreme_fast_token_amount = extreme_fast_token_amount

        # Exit strategy parameters
        self.exit_strategy = exit_strategy.lower()
        #: Segundos que se acepta el precio del stream antes de volver a
        #: preguntar al nodo. 0 apaga el stream y deja el comportamiento de
        #: siempre (una consulta al nodo por vuelta).
        #: Modo estudio: se filtra, se decide y se apunta, pero no se firma
        #: ninguna compra. Las posiciones YA abiertas se siguen vigilando con
        #: su stop loss y su escalera -- parar el bot entero seria lo que las
        #: dejaria a ciegas.
        self.pausar_compras = bool(pausar_compras)
        #: FRENOS CONTRA RUGS COMUNES A TODAS LAS FUENTES. Ver
        #: `_pasa_los_frenos_de_rug`: viven aqui y no en cada fuente porque el
        #: 24-sep se descubrio que al cambiar de estrategia se quedaron en la
        #: que se apagaba, y la curva y Telegram compraron sin ellos.
        self.max_subida_global = max(0.0, float(max_subida_global or 0))
        self.subida_seg_global = max(1.0, float(subida_seg_global or 10))
        self.min_compradores_global = max(0, int(min_compradores_global or 0))
        #: Cada cuanto se manda el parte de descartes. 0 lo apaga.
        self.resumen_descartes_seg = float(resumen_descartes_seg or 0)
        self.precio_stream_seg = float(precio_stream_seg or 0)
        self.tick_stream_seg = float(tick_stream_seg or 0)
        self.take_profit_percentage = take_profit_percentage
        self.stop_loss_percentage = stop_loss_percentage
        self.max_hold_time = max_hold_time
        #: Tope duro de vigilancia de UNA posicion, para que una moneda
        #: invendible (un rug) no retenga el semaforo para siempre. Iba
        #: atado a `max_hold_time`, lo que escondia una trampa: al poner
        #: `max_hold_time: 0` para dejar correr las posiciones, la formula
        #: caia a su valor por defecto y ABANDONABA todo a los 8 minutos.
        #: Ahora es independiente y por defecto son 24 horas.
        self.abandonar_tras_seg = int(abandonar_tras_seg or 86400)
        self.venta_muerta_seg = float(venta_muerta_seg or 0)
        #: Segundos seguidos por debajo del precio de compra tras los cuales
        #: se vende la posicion ENTERA, moonbag incluido. 0 lo desactiva.
        self.venta_bajo_entrada_seg = float(venta_bajo_entrada_seg or 0)
        self.venta_muerta_gain = float(venta_muerta_gain or 0)
        _sub = dict(subir_slippage_tras_fallidas or {})
        #: Compras fallidas SEGUIDAS que hacen subir el slippage de compra.
        self.fallidas_para_subir_slippage = int(_sub.get("fallidas") or 0)
        #: A cuanto sube el slippage de compra cuando se llega a esas fallidas.
        self.slippage_tras_fallidas = float(_sub.get("slippage") or 0)
        self._compras_fallidas_seguidas = 0
        self.price_check_interval = price_check_interval
        # Build the take-profit ladder. Tier 1 sells take_profit_sell_percentage
        # (default 100% for backward compatibility) at take_profit_percentage.
        self.tp_tiers = build_tiers(
            [
                (
                    take_profit_percentage,
                    take_profit_sell_percentage
                    if take_profit_sell_percentage is not None
                    else 1.0,
                ),
                (tp2_gain, tp2_sell),
                (tp3_gain, tp3_sell),
                (tp4_gain, tp4_sell),
                (tp5_gain, tp5_sell),
                # Sexto peldano. Con cinco no cabia la escalera pedida:
                # tres cortes cerca (+30/+60/+90) y tres lejos (3,3x/5x/7x).
                (tp6_gain, tp6_sell),
                # Peldanos extra, sin limite de cuantos. Ir anadiendo
                # tp7_gain, tp8_gain... no escala: cada nuevo objetivo
                # obligaba a tocar el trader, el runner y la config.
                *_peldanos_extra(tp_extra),
            ]
        )
        self.final_target_mc = float(final_target_mc or 0)
        self.mc_stop_loss = float(mc_stop_loss or 0)
        self.mc_take_profit_tiers: list[tuple[float, float]] = []
        for item in mc_take_profit_tiers or []:
            if not isinstance(item, dict):
                continue
            try:
                target_mc = float(item.get("target_mc") or 0)
                sell = float(item.get("sell") or 0)
            except (TypeError, ValueError):
                continue
            if target_mc > 0 and 0 < sell <= 1:
                self.mc_take_profit_tiers.append((target_mc, sell))
        self.mc_take_profit_tiers.sort(key=lambda item: item[0])
        #: SUELO QUE SUBE CON LA MONEDA (trinquete). En cuanto el market cap
        #: toca un escalon, queda armado un suelo por debajo; si luego cae
        #: ahi, se vende TODO. A diferencia del trailing del 33%, que mide un
        #: porcentaje desde el maximo, esto son cifras absolutas que Manu
        #: puede razonar: 'si llego a 15K, que no baje de 9K'.
        #:
        #: El margen se estrecha segun sube, y es deliberado: perder un 40%
        #: desde 15K cuesta poco, perder un 40% desde 100K es tirar la
        #: operacion entera.
        self.suelos_por_mc: list[tuple[float, float]] = []
        for item in suelos_por_mc or []:
            if not isinstance(item, dict):
                continue
            try:
                alcanza = float(item.get('alcanza') or 0)
                suelo = float(item.get('suelo') or 0)
            except (TypeError, ValueError):
                continue
            if alcanza > 0 and 0 < suelo < alcanza:
                self.suelos_por_mc.append((alcanza, suelo))
        self.suelos_por_mc.sort(key=lambda x: x[0])
        #: Con cuantos rugs se veta a un dev. Manu lo quiere en 1: el que te
        #: la juega una vez no merece una segunda oportunidad con dinero de
        #: verdad. Antes eran 2 para no vetar por una mala racha, pero con
        #: miles de creadores en pump.fun sobran candidatos.
        self.rugs_para_bloquear = max(1, int(rugs_para_bloquear or 2))
        #: STOP A LA ENTRADA TRAS EL PRIMER TAKE-PROFIT. Lo que queda dentro
        #: despues de cobrar el primer escalon deja de poder convertirse en
        #: perdida: si el precio vuelve a lo que se pago, se cierra.
        #: Encaja con una estrategia SIN stop de entrada -- se acepta el
        #: riesgo hasta el primer escalon, y a partir de ahi ya no.
        self.stop_a_entrada_tras_tp = bool(stop_a_entrada_tras_tp)
        #: Cuanto POR ENCIMA del escalon anterior queda el suelo al cobrar un
        #: take profit. 0 = como antes (el suelo va justo al escalon anterior,
        #: o a la entrada en el primero). Con 0,12 tocar el TP1 ya asegura un
        #: +12%, que es lo pedido.
        self.margen_stop_tras_tp = max(0.0, float(margen_stop_tras_tp or 0))
        self.stop_al_tp_tras_tp = bool(stop_al_tp_tras_tp)
        #: SEGUNDA PUERTA DE ENTRADA. La banda normal compra en el
        #: nacimiento, donde no hay volumen que medir porque no ha dado
        #: tiempo. Esta deja pasar tambien a la que ya se ha movido de ahi,
        #: pero exigiendole a cambio lo que la primera no puede pedir:
        #: volumen de verdad. Son dos caminos alternativos, no dos
        #: condiciones que haya que cumplir a la vez.
        _ea = entrada_alternativa or {}
        self.entrada_alt_max_mc = float(_ea.get('max_mc') or 0)
        self.entrada_alt_min_vol = float(_ea.get('min_volumen_sol') or 0)
        # TP AGRESIVO para devs moonshot (los que suelen llegar MUY alto): objetivos
        # más altos y moonbag más grande para pillar el pump. +100/300/900%, vende
        # 20% en cada uno, moonbag 40% (vs el normal +50/150/300% moonbag 20%).
        self.tp_tiers_aggressive = build_tiers([(1.0, 0.20), (3.0, 0.20), (9.0, 0.20)])
        self._creator_bestpeak = _load_creator_bestpeak(bro_address)

        #: Firmas de cada operacion, por mint: la de compra y las de todas las
        #: ventas. Sirven para medir el P&L REAL contra la cadena. El P&L que
        #: calcula el bot es `tokens x precio observado`, que NO es el SOL que
        #: entra en la cartera: no descuenta la comision de pump.fun, ni el
        #: impacto de la venta, ni lo que se pierde por slippage. El 9-ago
        #: reporto +0,16 SOL mientras la wallet perdia 0,75.
        self._sigs_operacion: dict[str, list[str]] = {}

        #: Ventas fallidas seguidas por mint. Cada intento fallido paga comision
        #: igual, asi que a partir de MAX_FALLOS_VENTA se deja de intentar en vez
        #: de reintentar cada segundo hasta agotar la wallet (ver _sell_amount).
        self._fallos_venta: dict[str, int] = {}
        #: Monedas de las que ya se ha avisado de un fallo de
        #: programacion. El bucle da cuatro vueltas por segundo: sin
        #: esto, un bug manda cuatro mensajes por segundo.
        self._bugs_avisados: set[str] = set()
        #: Hasta cuando no se vuelve a intentar vender cada moneda.
        self._bloqueada_hasta: dict[str, float] = {}
        #: Vueltas que lleva el refresco sin conseguir el precio de SOL.
        self._vueltas_sin_precio_sol = 0

        # Timing parameters
        self.wait_time_after_creation = wait_time_after_creation
        self.wait_time_after_buy = wait_time_after_buy
        self.wait_time_before_new_token = wait_time_before_new_token
        self.max_token_age = max_token_age
        self.token_wait_timeout = token_wait_timeout

        # Cleanup parameters
        self.cleanup_mode = cleanup_mode
        self.cleanup_force_close_with_burn = cleanup_force_close_with_burn
        self.cleanup_with_priority_fee = cleanup_with_priority_fee

        # Trading filters/modes
        self.match_string = match_string
        # bro_address may be a single wallet, a list of wallets, or a path to a
        # .json/.txt file of wallets — normalized to a set of addresses to watch.
        self.bro_address = _normalize_creators(bro_address)
        # {wallet: mediana_pico_usd} de la lista de devs (si el bro_address es un
        # .json con medianas). Se usa en la excepción del filtro de holders:
        # un holder gordo se tolera si la mediana del dev >> el MC actual.
        self._creator_medians = _load_creator_medians(bro_address)
        self._creator_launches = _load_creator_launches(bro_address)
        self.marry_mode = marry_mode
        self.yolo_mode = yolo_mode

        # Two-level concurrency control:
        #  - _filter_semaphore bounds how many tokens run the (slow, ~15s
        #    RugCheck) buy filter at once, so filtering keeps up with the launch
        #    flood without a held position blocking new checks or hammering the API.
        #  - _position_semaphore caps simultaneous OPEN positions so background
        #    buys/exit-monitors can't drain the wallet.
        self._filter_semaphore = asyncio.Semaphore(8)
        #: Posiciones abiertas simultaneas. Con `max_posiciones: 0` manda el
        #: SALDO -- antes de cada compra se comprueba que quede por encima de
        #: min_reserve_sol -- y esto es solo el tope de seguridad de ultima
        #: instancia. Estaba en 20 y se sube a TOPE_DURO_POSICIONES para que
        #: "0" signifique de verdad "lo que aguante la cartera".
        #:
        #: Manda siempre el que se quede corto de los dos: si no hay SOL para
        #: la siguiente, no se abre aunque el tope lo permita.
        self.max_posiciones = int(max_posiciones or TOPE_DURO_POSICIONES)
        self._position_semaphore = asyncio.Semaphore(self.max_posiciones)
        # Bounded concurrency for paper price-watchers (protects the RPC).
        self._watch_semaphore = asyncio.Semaphore(8)

        # Wallet-spend guard: reserve committed lamports under a lock so two
        # concurrent buys can never both pass on the same balance (TOCTOU-safe).
        self._spend_lock = asyncio.Lock()
        self._reserved_lamports = 0
        # Real-buy slots reserved by concurrent candidates. Without this, four
        # tokens can all observe 0 completed buys before the first confirms.
        self._reserved_real_buy_slots = 0
        self._fee_headroom_lamports = 3_000_000  # ~0.003 SOL: fees + ATA rent

        # Paper-trade: on filter PASS, record the candidate and skip the real buy.
        self.paper_trade = paper_trade

        # Pump.fun creator-migration filter config (off unless enabled).
        self.pumpfun_dev = pumpfun_dev or {}
        # Si pump.fun no contesta (al droplet le da 403, 5-nov), el historial
        # del dev se lee de la cadena por este mismo nodo. Ver
        # `usar_rpc_de_respaldo` en monitoring/pumpfun_dev.py.
        if self.pumpfun_dev.get("enabled"):
            from monitoring.pumpfun_dev import usar_rpc_de_respaldo  # noqa: PLC0415

            usar_rpc_de_respaldo(self.solana_client.post_rpc)
        # Copy-trading config (off unless enabled).
        self.copy_wallets = copy_wallets or {}
        # Allow mayhem-mode tokens for this strategy (default: avoid).
        # require_mayhem forces "only mayhem", which also requires allowing them.
        self.require_mayhem = bool(require_mayhem)
        self.allow_mayhem = bool(allow_mayhem) or self.require_mayhem
        # Las mayhem, a prueba: importe propio y corta-circuitos por racha.
        self.mayhem_buy_amount = float(mayhem_buy_amount or 0)
        self.mayhem_max_perdidas = int(mayhem_max_perdidas or 0)
        #: Perdidas seguidas en mayhem. Una ganadora lo devuelve a cero: lo que
        #: se corta es una mala racha, no el total historico.
        self._mayhem_perdidas = 0
        #: Se pone a True al llegar al limite. A partir de ahi no se compra
        #: ninguna mayhem mas en lo que queda de sesion, pero el bot sigue
        #: operando las normales con normalidad.
        self._mayhem_cerrado = False

        # Bundle detector: minimum SOL already sitting in the bonding curve.
        # A brand-new pump.fun token starts near 0; a *bundled* launch shows
        # several SOL within seconds because the operator self-bought big.
        # 0 disables the check.
        self.min_curve_sol = float(min_curve_sol or 0)
        self.max_entry_mc = float(max_entry_mc or 0)
        self.min_entry_mc = float(min_entry_mc or 0)
        self.min_holders = int(min_holders or 0)
        self.require_zero_initial_holders = bool(require_zero_initial_holders)
        self.max_holder_pct = float(max_holder_pct or 0)
        self.max_bundle_pct = float(max_bundle_pct or 0)
        self.max_dev_hold_pct = float(max_dev_hold_pct or 0)
        self.min_dev_hold_pct = float(min_dev_hold_pct or 0)
        self.holder_bypass_ratio = float(holder_bypass_ratio or 0)
        self.min_holders_gmgn = int(min_holders_gmgn or 0)
        self.min_volumen_sol = float(min_volumen_sol or 0)
        self.min_buys = int(min_buys or 0)
        self.min_first_block_buys = int(min_first_block_buys or 0)
        self.max_first_block_buys = int(max_first_block_buys or 0)
        self.min_first_block_usd = float(min_first_block_usd or 0)
        self.max_first_block_usd = float(max_first_block_usd or 0)
        self.buy_from_second_block = bool(buy_from_second_block)
        self.second_block_confirmation = dict(second_block_confirmation or {})
        self.min_ops = int(min_ops or 0)
        self.min_titulares = int(min_titulares or 0)
        self.max_titulares = int(max_titulares or 0)
        self.min_compras_por_cartera = float(min_compras_por_cartera or 0)
        self.max_compras_por_cartera = float(max_compras_por_cartera or 0)
        self.redes_originales = bool(redes_originales)
        self.redes_timeout = float(redes_timeout or 1.5)
        self.max_volumen_sol = float(max_volumen_sol or 0)
        self.min_vol_ventana_sol = float(min_vol_ventana_sol or 0)
        self.vol_ventana_seg = float(vol_ventana_seg or 3.0)
        self.min_presion_ratio = float(min_presion_ratio or 0)
        self.ultimas_tx_compra = int(ultimas_tx_compra or 0)
        self.presion_seg = float(presion_seg or 3.0)
        #: EL FILTRO ANTI-HUMO. Un market cap enorme no significa nada si la
        #: curva esta seca: el precio se empuja con cuatro duros cuando el
        #: supply lo tiene el que lanza, y luego no hay con que salir. Ya se
        #: midio antes: el 85% de las monedas que 'suben' tienen el pool
        #: vacio, y TNOS marcaba 27 millones de MC con 0,198 SOL dentro.
        #: Esto exige que el MC guarde una proporcion con el SOL real.
        self.max_mc_por_sol_curva = float(max_mc_por_sol_curva or 0)
        #: CORTA-CIRCUITOS. Con las tres estrategias compartiendo cartera, la
        #: que se tuerce se lleva por delante el saldo de las otras. A las N
        #: perdidas seguidas esta deja de COMPRAR -- sigue vendiendo lo que
        #: tenga abierto, que abandonarlo seria peor. Una ganadora pone el
        #: contador a cero: se corta una mala racha, no el historico.
        #: CUANTO SE HA COMPRADO DE VERDAD. Medido el 10-ago sobre cuatro
        #: monedas, es lo que mejor separa a la que sube de las que se
        #: hunden, y separa lo que los demas filtros no ven:
        #:
        #:   la que subio  curva 74,5% -> 25,5% del supply en manos
        #:   una mala      curva 97,3% ->  2,7%   (pasaba el anti-humo Y
        #:                                        tenia buen dev: 2 de 7)
        #:   dos dumps     curva  100% ->  0%
        #:
        #: Un market cap que sube con el 97% del supply todavia dentro de la
        #: curva no lo ha pagado nadie: se empujo con cuatro duros. Esto es
        #: lo unico que lo caza ANTES de comprar.
        self.min_supply_en_manos = float(min_supply_en_manos or 0)
        #: CUANTO DE LO COMPRADO ES BUNDLE. Comprar en el bloque de creacion
        #: no es demanda: es un sniper o el propio dev entrando a precio
        #: cero, y su unica salida eres tu. Medido sobre las seis primeras
        #: operaciones del 11-ago, separa sin solapamiento:
        #:
        #:   PHILO      1 de 45 ( 2%)  cobro el +25%
        #:   Crocodile  4 de 42 (10%)  cobro el +25%
        #:   Terry      7 de 53 (13%)  cobro el +25%
        #:   Roger      4 de 25 (16%)  cobro el +25%
        #:   test      10 de 29 (34%)  stop entero
        #:   FARM      14 de 37 (38%)  stop entero
        #:
        #: Se mira la PROPORCION y no el numero absoluto porque una moneda
        #: con 100 compras y 10 en el primer bloque esta sana y una con 15 y
        #: 10 no lo esta.
        self.max_first_block_pct = float(max_first_block_pct or 0)
        #: DOS VELOCIDADES, segun de quien venga la moneda. Con un creador
        #: cualquiera que cumpla el minimo se va a lo seguro: se recupera el
        #: capital cuanto antes, porque no se sabe casi nada de el. Con uno
        #: de los marcados -- medidos por no dumpear y por haber hecho
        #: monedas de +200K -- se puede ir con calma: se descarga menos en el
        #: primer escalon y se deja mas corriendo, que es donde estan los x10
        #: que se vieron en Dadjak, FROGGY y Kermit.
        try:
            self._devs_marcados = (
                _load_creator_file(devs_marcados) if devs_marcados else set()
            )
        except Exception:  # noqa: BLE001
            self._devs_marcados = set()
        if self._devs_marcados:
            logger.info(
                'Devs marcados: %d wallets (compran %s SOL y salen mas tarde)',
                len(self._devs_marcados),
                buy_amount_marcados or buy_amount,
            )
        self.buy_amount_marcados = float(buy_amount_marcados or 0)
        self.marcados_saltan_baneo = bool(marcados_saltan_baneo)
        # Maximo absoluto por compra, en SOL. 0 = sin tope. Se lee del YAML
        # como `tope_compra_sol`.
        self.tope_compra_sol = float(tope_compra_sol or 0)
        #: Fraccion de la CARTERA (saldo + lo que ya esta en monedas) con
        #: la que se compra. 0 = tamano fijo. Ver `_valor_en_posiciones_lamports`.
        self.fraccion_saldo = max(0.0, min(1.0, float(fraccion_saldo or 0)))
        #: ESCALERA DE FRACCIONES, una por posicion abierta. Reemplaza al
        #: escalar `fraccion_saldo` cuando esta puesta Y la base es "libre".
        #:
        #: PEDIDO (15-oct, palabras de Manu): "33% del portfolio de sol por
        #: operacion y el 40% de la segunda y un 50% de la restante a la
        #: tercera".
        #:
        #: POR QUE HACIA FALTA CODIGO NUEVO. `fraccion_saldo` es UN numero:
        #: con base "libre" se aplica al saldo gastable, asi que las compras
        #: menguan solas (33% · 22% · 15% del saldo inicial) pero el
        #: PORCENTAJE es siempre el mismo. Manu pide que el porcentaje SUBA
        #: con cada posicion. No se podia expresar con un escalar.
        #:
        #: Lo que sale con [0.33, 0.40, 0.50] y 1 SOL libre:
        #:   compra 1: 33% de 1,000 = 0,330  -> quedan 0,670
        #:   compra 2: 40% de 0,670 = 0,268  -> quedan 0,402
        #:   compra 3: 50% de 0,402 = 0,201  -> quedan 0,201
        #: Total desplegado 0,799: el 20% de la cartera NO se juega nunca.
        #: Ese resto no es un descuido, es la consecuencia de pedir "de la
        #: restante" y es el freno que evita comprometer la cartera entera.
        #:
        #: Que posicion toca se decide con `_posiciones_abiertas()`, que
        #: cuenta MARCADORES EN DISCO y no el semaforo: tras un reinicio el
        #: semaforo dice cero aunque haya posiciones vivas, y entonces la
        #: cuarta compra volveria a usar el 33% de un saldo ya comprometido.
        #:
        #: Si hay mas posiciones que peldanos se repite el ultimo, en vez de
        #: reventar por indice o caer al escalar sin avisar.
        self.fracciones_saldo = [
            max(0.0, min(1.0, float(x)))
            for x in (fracciones_saldo or [])
            if x is not None
        ]
        #: IMPORTE EN DOLARES por compra. 0 = apagado. Si es mayor que cero
        #: manda el, y los SOL salen de dividir por el precio de SOL EN VIVO
        #: (`_SOL_USD`, que refresca `_precio_sol_loop` desde DexScreener).
        #:
        #: POR QUE EN DOLARES Y NO EN SOL. "10 $ por operacion" en SOL es un
        #: numero que caduca: escrito 0,045 SOL con SOL a 220 $, el dia que
        #: SOL valga 300 esa misma compra son 13,50 $ sin que nadie lo haya
        #: decidido. Aqui la apuesta es la misma todos los dias.
        self.buy_amount_usd = max(0.0, float(buy_amount_usd or 0))
        #: SOBRE QUE se calcula `fraccion_saldo`. Es configurable y no una
        #: decision escrita en el codigo porque ha ido y venido tres veces en
        #: dos dias, y cada vuelta era un commit tocando el camino de compra.
        #:
        #:   "libre"   -> el SOL gastable: saldo menos la reserva minima menos
        #:                lo ya comprometido. Como mengua con cada posicion
        #:                abierta, las compras salen cada vez mas pequenas.
        #:   "cartera" -> saldo MAS lo que ya vale lo que hay en monedas. La
        #:                base no baja al comprar --el dinero cambia de sitio,
        #:                no desaparece-- asi que las compras salen iguales.
        #:
        #: Cualquier cosa que no sea "libre" se trata como "cartera": un valor
        #: mal escrito no puede cambiar en silencio cuanto se gasta.
        self.fraccion_base = (
            "libre" if str(fraccion_base or "").strip().lower() == "libre"
            else "cartera"
        )
        #: RED DE SEGURIDAD, no una forma de comprar. Cuando el saldo no
        #: da para el importe entero, en vez de saltarse la moneda se
        #: compra con esta fraccion de lo que quede libre. 0 = apagado,
        #: o sea el comportamiento de siempre: sin saldo, no se compra.
        #:
        #: NO ES LO MISMO QUE `fraccion_saldo`, y confundirlas cuesta
        #: dinero: aquella compra SIEMPRE un porcentaje del saldo, asi que
        #: con la wallet llena compraria muchisimo mas de lo autorizado.
        #: Esta solo se mira cuando la compra normal NO cabe.
        self.fraccion_si_no_llega = max(
            0.0, min(1.0, float(fraccion_si_no_llega or 0))
        )
        #: Suelo de compra en SOL. Por debajo se avisa y no se compra.
        self.compra_minima_sol = float(compra_minima_sol or 0)
        #: Cuanto crece la compra por cada SOL ganado de verdad. Con 0.35 y una
        #: base de 0,15 SOL: +1 SOL ganado -> 0,203 por compra; +5 -> 0,413.
        self.escala_por_sol_ganado = float(escala_por_sol_ganado or 0)
        #: Si la tanda se vuelve a abrir al quedarse sin posiciones. Con
        #: False, `max_real_buys` es un tope de por vida: sirve para pruebas
        #: acotadas ("haz N operaciones y para") sin tener que vigilarlas.
        self.reiniciar_tanda = bool(reiniciar_tanda)
        self._bote = Ganancia() if self.escala_por_sol_ganado > 0 else None
        self.horario = horario or {}
        self.dev_elite = dev_elite or {}
        #: LISTA DE DEVS QUE SUELEN PASAR DE 30K (7-nov): los que esten en ella
        #: salen con la escalera de `dev_elite`, aunque su tasa no llegue a
        #: `dev_elite.min_rate`.
        self._devs_elite_lista: set[str] = (
            _load_creator_file(str(self.dev_elite["lista"]))
            if self.dev_elite.get("lista") else set()
        )
        if self._devs_elite_lista:
            logger.info(
                "Lista de devs con salida propia (dev_elite): %d wallets",
                len(self._devs_elite_lista),
            )
        self.dev_mediana = dev_mediana or {}
        self.dev_buy = dev_buy or {}
        self.dip = dip or {}
        #: mint -> TokenInfo, solo si dip esta configurado. Lo necesita el
        #: scanner para poder comprar un mint visto hace minutos, cuando ya
        #: no esta en la cola normal (esa es de un solo paso).
        self._token_info_cache: dict[str, TokenInfo] = {}
        #: mint -> pico de market cap visto hasta ahora (solo mientras dip
        #: este configurado y el mint siga dentro de la ventana).
        self._dip_pico: dict[str, float] = {}
        #: mints por los que el scanner ya intento comprar, para no repetir.
        self._dip_intentado: set[str] = set()
        #: Tasa de migracion del creador de cada moneda, guardada al comprar
        #: para poder decidir la salida sin volver a preguntar a la API.
        self._tasa_dev: dict[str, float] = {}
        #: Monedas cuyo creador suele pumpear alto. Se marca al detectarlas,
        #: porque el tamano y la comision de prioridad se deciden al comprar y
        #: el stop-loss al vigilar: las tres necesitan el mismo dato.
        self._mints_elite: set[str] = set()
        #: Monedas que entraron por la puerta de la mediana (dev_mediana):
        #: se consulta en el techo de entrada para dejarlas pasar con un MC
        #: mas alto que el general. Mismo motivo de marcar-al-detectar que
        #: `_mints_elite`.
        self._mints_mediana: set[str] = set()
        #: SOL cobrado en la ultima venta. Cero si no se vendio nada.
        #: Es lo que se suma al total realizado, en vez del estimado:
        #: el 12-ago un cierre sin venta se apunto como +0,1496 SOL.
        self._ultimo_cobro = 0.0
        self.tope_compra_total_sol = float(tope_compra_total_sol or 0)
        #: Nombre con el que esta estrategia cuenta sus resultados. Por defecto
        #: el del fichero de configuracion, para que dos bots distintos no
        #: mezclen sus numeros.
        self.nombre_estrategia = str(nombre_estrategia or "sin-nombre")
        self._selector = None
        if usar_selector:
            try:
                from trading.selector import Selector

                self._selector = Selector()
                logger.info(
                    "selector activo para '%s'\n%s",
                    self.nombre_estrategia, self._selector.resumen(),
                )
            except Exception:  # noqa: BLE001
                logger.warning("selector: no se pudo cargar, se sigue sin el")
        self.tp_tiers_marcados = build_tiers(
            [(tp_marcados, tp_sell_marcados)]
        ) if tp_marcados and tp_sell_marcados else []
        self.max_perdidas_seguidas = int(max_perdidas_seguidas or 0)
        self.max_posiciones_total = int(max_posiciones_total or 0)
        self._perdidas_seguidas = 0
        #: REGIMEN APARTE para las monedas que no nacieron en pump.fun. Se
        #: cuentan solas porque son un experimento con menos red: no hay
        #: creador ni historial que mirar, y el supply no es el fijo de
        #: pump.fun. Probarlas con cupo y cortar rapido si fallan, sin que
        #: eso toque a las de casa.
        self._compras_otro_origen = 0
        self._perdidas_otro_origen = 0
        # Regimen aparte de las que entran por avance de curva: cupo de
        # compras y corte por perdidas propios, para poder probarlo sin que
        # se mezcle con el resto.
        #: Precio mas alto visto en cada moneda MIENTRAS se vigilaba. No es el
        #: maximo historico de la moneda: lo de antes de comprar y lo de
        #: despues de salir no se ve. Es "hasta donde llego estando dentro".
        self._pico_visto: dict[str, float] = {}
        #: Motivo de descarte -> cuantas veces. Es lo que contesta "por que no
        #: compra" sin tener que leer el log desde un movil.
        self._descartes: dict[str, int] = {}
        self._compras_del_resumen = 0
        self._ultimo_resumen = monotonic()
        #: Cuando se le pregunto al nodo por ultima vez, y que contesto. Con
        #: esto el monitor puede dar vueltas rapidas usando el precio del
        #: stream sin gastar ni una peticion de mas (ver `_precio_rapido`).
        self._ultima_rpc: dict[str, float] = {}
        self._ultimo_precio: dict[str, float] = {}
        self._compras_curva96 = 0
        self._perdidas_curva96 = 0
        self._parado_por_perdidas = False
        self.buy_amount_tras_sl = float(buy_amount_tras_sl or 0)
        self._ultimo_cierre_fue_sl = False
        # El stop de ENTRADA y el TRAILING eran el mismo numero, y no pueden
        # serlo: el de entrada tiene que aguantar el zarandeo del nacimiento
        # (medido el 10-ago: Kermit se hundio un 44% antes de hacer 13x) y el
        # trailing tiene que ser corto para no devolver media ganancia desde
        # el techo. Si no se configura, se comporta como antes.
        self.trailing_stop_percentage = float(
            (stop_loss_percentage or 0)
            if trailing_stop_percentage is None else trailing_stop_percentage
        )
        self.vender_todo_en_stop = bool(vender_todo_en_stop)
        self.second_wave = dict(second_wave or {})
        self.senal_gmgn = [int(x) for x in (senal_gmgn or [])]
        self.anti_rug_ratio = float(anti_rug_ratio or 0)
        self.anti_rug_seg = float(anti_rug_seg or 4.0)
        self.anti_rug_min_sol = float(anti_rug_min_sol or 3.0)
        self.no_repeat_name_min = float(no_repeat_name_min or 0)
        # Cuando es True no se mira ninguna lista de creadores: ni la blacklist
        # manual ni el auto-baneo por rugs. Lo pidio Manu para DEV10: si el dev
        # ha migrado mas del 10%, se le compra aunque este fuera de cualquier
        # lista o dentro de una de bloqueo. La reputacion se calcula en vivo
        # contra la API de pump.fun, que es un dato mas fresco que una lista
        # escrita a mano hace semanas.
        self.ignorar_listas_de_devs = bool(ignorar_listas_de_devs)
        self.max_buy_age = float(max_buy_age or 0)
        self.min_buy_age = float(min_buy_age or 0)
        #: símbolo -> monotonic() de la última vez que se vio. Solo memoria del
        #: proceso: tras un reinicio la ventana empieza vacía.
        self._nombres_vistos: dict[str, float] = {}
        # Reserva mínima de SOL que nunca se gasta (para fees / no quedarse a 0).
        self.min_reserve_lamports = int(float(min_reserve_sol or 0.03) * LAMPORTS_PER_SOL)
        #: Por debajo de esto una posicion deja de gestionarse. No es un
        #: capricho de limpieza: VENDER cuesta comision de prioridad mas la
        #: renta de la cuenta de token, asi que por debajo de un dolar la
        #: venta se lleva mas de lo que recupera. Y mientras tanto esa bolsa
        #: ocupa una de las plazas de posicion y gasta una consulta de precio
        #: por segundo, que es justo lo que necesitan las que si valen algo.
        self.valor_minimo_posicion_usd = float(valor_minimo_posicion_usd or 0)
        self.paper_watch_secs = int(paper_watch_secs or 300)
        from paper_trading import PaperTradingEngine

        self.paper_engine = PaperTradingEngine(paper_trading_config)

        # DexScreener buy filter (no-op unless enabled in config)
        self.dex_filter = DexScreenerFilter(dexscreener_config)
        # Buy-on-dex-paid acquisition mode config (off unless enabled)
        self.buy_on_dex_paid = buy_on_dex_paid or {}
        self.birdeye_trending = birdeye_trending or {}
        self.dextools_hotpools = dextools_hotpools or {}
        self.curva_96 = curva_96 or {}
        self.dex_presion = dex_presion or {}
        #: Compra por oleada de compradores distintos. Es la fuente que sale
        #: de lo medido: 27.241 monedas dijeron que lo que predice un pump no
        #: es el ratio compras/ventas sino cuanta GENTE DISTINTA entra.
        self.presion = presion or {}
        #: Compra por titulares: N carteras distintas dentro y menos de X
        #: minutos de vida. DOS CONDICIONES Y NADA MAS, pedido asi por
        #: Manu. Lo que se pierde esta escrito en holders_source.py: sin
        #: anti-bundle, un bundle de N carteras recien creadas cumple las
        #: dos a la perfeccion.
        self.holders = holders or {}
        # Telegram-calls acquisition mode config (off unless enabled)
        self.telegram_calls = telegram_calls or {}
        self.telegram_only = bool(telegram_only)
        self.discord_calls = discord_calls or {}
        # GMGN signals acquisition mode config (off unless enabled)
        self.gmgn_signals = gmgn_signals or {}
        self.callouts = callouts or {}

        # Persistent "already bought" blocklist: never buy the same coin twice,
        # even across restarts (in-memory processed_tokens resets on restart).
        _sfx = f"_{state_suffix}" if state_suffix else ""
        self._bought_mints_path = Path(f"traded_mints{_sfx}.json")
        self.bought_mints: set[str] = self._load_bought_mints()
        # C2: record the currently-monitored position so a crash mid-position is
        # detected on restart (otherwise a bought bag is left orphaned forever).
        self._open_pos_path = Path(f"open_position{_sfx}.json")
        # Panel de Telegram: el monitor revisa este fichero cada vuelta; si el panel
        # escribe {mint, frac}, se vende esa fracción del RESTANTE de forma manual.
        self._sell_cmd_path = Path(f"sell_cmd{_sfx}.json")
        self._state_suffix = _sfx  # p.ej. "_bot-sniper-gemas" (lo usa el panel)
        self.max_real_buys = max(0, int(max_real_buys or 0))
        self.paper_max_samples = max(0, int(paper_max_samples or 0))
        self._paper_samples_started = 0
        #: Compras hechas DESDE QUE ARRANCO ESTE PROCESO. `bought_mints` no
        #: sirve para el tope: se carga de traded_mints*.json y acumula todas
        #: las monedas compradas en la vida del bot -- con 115 dentro, un tope
        #: de 3 bloqueaba antes de empezar ("Limite alcanzado: 115/3").
        self._compras_de_la_tanda: int = 0
        # Precio de entrada por mint, apuntado al comprar. El aviso de venta
        # REAL lo necesita para dar el P&L, y a esas alturas del codigo ya no
        # hay forma de saberlo: `_vender` solo conoce el precio de salida.
        self._precio_entrada: dict[str, float] = {}
        #: mint -> (momento, precio en SOL) del DEX. Ver `_precio_dex`.
        self._precio_dex_cache: dict[str, tuple[float, float | None]] = {}
        # Moonbags retenidos (no se auto-venden): el avisador manda un aviso a
        # Telegram cada 5 min mientras sigan en la wallet, para venderlos a mano.
        self._moonbags_path = Path(f"moonbags{_sfx}.json")
        # Auto-baneo: {dev: rugs_acumulados}. Al alcanzar el umbral configurado
        # queda bloqueado; una ganadora intermedia no borra sus antecedentes.
        self._blocked_devs_path = Path(f"blocked_devs{_sfx}.json")
        # Blacklist DURA manual (blacklist_devs.json): devs que NUNCA se compran,
        # bloqueo permanente compartido por todos los bots (no depende del auto-baneo).
        try:
            self._manual_blacklist = _load_creator_file("blacklist_devs.json")
        except Exception:  # noqa: BLE001 - sin blacklist = set vacío
            self._manual_blacklist = set()
        if self._manual_blacklist:
            logger.info(
                f"Blacklist manual: {len(self._manual_blacklist)} dev(s) vetados"
            )

        # State tracking
        self.traded_mints: set[Pubkey] = set()
        self.traded_token_programs: dict[
            str, Pubkey
        ] = {}  # Maps mint (as string) to token_program_id
        self.token_queue: asyncio.Queue = asyncio.Queue()
        self.processing: bool = False
        self.processed_tokens: set[str] = set()
        self.token_timestamps: dict[str, float] = {}

    def _make_seller(self, wallet: Wallet) -> PlatformAwareSeller:
        """Crea un seller para la wallet de esta posición (modo pool)."""
        return PlatformAwareSeller(
            self.solana_client,
            wallet,
            self.priority_fee_manager,
            self.sell_slippage,
            self.max_retries,
            compute_units=self.compute_units,
        )

    def _make_buyer(self, wallet: Wallet) -> PlatformAwareBuyer:
        """Crea un buyer para la wallet de esta posición (modo pool)."""
        return PlatformAwareBuyer(
            self.solana_client,
            wallet,
            self.priority_fee_manager,
            self.buy_amount,
            self.buy_slippage,
            self.max_retries,
            self.extreme_fast_token_amount,
            self.extreme_fast_mode,
            compute_units=self.compute_units,
            allow_mayhem=self.buyer.allow_mayhem,
            require_mayhem=self.buyer.require_mayhem,
            devs_marcados=self.buyer._devs_marcados,
        )

    #: Las fuentes de compra, en un solo sitio. Cada entrada es
    #: (atributo, nombre corto). ESTA LISTA VIVIA REPETIDA y se desincronizo:
    #: al anadir `presion` faltaba en tres sitios distintos, y uno de ellos era
    #: el revisor del droplet, que habria dicho "sin fuentes activas" con el
    #: bot comprando. Quien anada una fuente la pone AQUI y las dos lecturas
    #: -- el arranque y el cartel de configuracion -- se enteran solas.
    FUENTES: ClassVar[tuple[tuple[str, str], ...]] = (
        ("buy_on_dex_paid", "dexpaid"),
        ("birdeye_trending", "birdeye"),
        ("dextools_hotpools", "dextools"),
        ("curva_96", "curva96"),
        ("dex_presion", "dex_presion"),
        ("presion", "presion"),
        ("holders", "holders"),
        ("telegram_calls", "telegram"),
        ("discord_calls", "discord"),
        ("gmgn_signals", "gmgn"),
    )

    def _fuentes_activas(self) -> list[str]:
        """Nombres cortos de las fuentes encendidas ahora mismo."""
        return [
            corto
            for attr, corto in self.FUENTES
            if (getattr(self, attr, None) or {}).get("enabled")
        ]

    def _commit_actual(self) -> str:
        """El commit que esta corriendo, o '?' si no se puede saber.

        Es el dato que mas falta ha hecho: el droplet estuvo dias con codigo
        viejo sin que se pudiera ver de un vistazo, y los comandos que se le
        mandaban eran de ficheros que su commit todavia no tenia.
        """
        try:
            return subprocess.run(
                ["/usr/bin/git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout.strip() or "?"
        except Exception:  # noqa: BLE001
            return "?"

    def _cartel_de_configuracion(self) -> str:
        """UNA linea con todo lo que hay que saber para diagnosticar.

        POR QUE EN UNA SOLA LINEA. Saber que estaba corriendo el bot pedia
        cuatro comandos y cuatro capturas desde el movil: el commit, si estaba
        en pausa, que fuentes y con cuanto compraba. Media manana para una
        pregunta. Asi una captura de

            journalctl -u pump-bot | grep "CONFIG ACTIVA" | tail -1

        las contesta todas de golpe.

        No lleva NADA del .env: ni claves, ni endpoints, ni la wallet.
        """
        fuentes = ", ".join(self._fuentes_activas()) or "NINGUNA (listener normal)"
        if getattr(self, "pausar_compras", False):
            compras = "EN PAUSA (modo estudio, no gasta)"
        elif self.paper_trade or self.paper_engine.enabled:
            compras = "PAPEL (simuladas)"
        elif getattr(self, "buy_amount_usd", 0) > 0:
            # EN DOLARES Y EN SOL, los dos. El dolar es lo que se decidio; los
            # SOL son lo que de verdad va a salir de la wallet, y cual de los
            # dos esta mal solo se ve si estan los dos escritos. Si el precio
            # de SOL no se ha refrescado, el cartel lo dice aqui en vez de
            # dejar que se descubra en la primera compra.
            _sol = float(_SOL_USD)
            _sin = _sol == _ARRANQUE_SOL_USD
            compras = (
                f"REALES {self.buy_amount_usd:.2f} $ "
                f"(~{self.buy_amount_usd / _sol:.4f} SOL a {_sol:.2f} $/SOL)"
                + (" · PRECIO DE SOL SIN CONFIRMAR" if _sin else "")
            )
        elif self.fraccion_saldo > 0:
            # EL CARTEL TIENE QUE DECIR LO QUE COMPRA, no el tope. La linea de
            # abajo enseñaba `tope_compra_sol`, que desde que manda el
            # porcentaje ya no es el importe sino un freno contra fallos: diria
            # "REALES 0.5000 SOL" comprando 0,24. Es la linea que se mira al
            # arrancar para confirmar la configuracion, asi que mentir aqui es
            # exactamente lo que no puede pasar.
            # LA BASE VA EN EL CARTEL, no solo el porcentaje. "15%" no dice
            # nada sin saber de que: el 15% del saldo libre y el 15% de la
            # cartera son numeros distintos y menguan distinto.
            _base = (
                "del saldo libre"
                if getattr(self, "fraccion_base", "cartera") == "libre"
                else "de la cartera"
            )
            compras = (
                f"REALES {self.fraccion_saldo * 100:.0f}% {_base} "
                f"(tope {self.tope_compra_sol:.4f} SOL)"
            )
        else:
            compras = f"REALES {self.tope_compra_sol or self.buy_amount:.4f} SOL"
        return (
            f"CONFIG ACTIVA · commit {self._commit_actual()} · "
            f"compras {compras} · {self.max_posiciones} plazas · "
            f"fuentes: {fuentes}"
        )

    def _cartel_de_salidas(self) -> str:
        """La escalera y el stop EXACTOS con los que se esta operando.

        POR QUE HACE FALTA, y es una leccion repetida: "no saltan los take
        profit" es imposible de contestar sin saber que numeros esta usando la
        maquina. El yaml del repo no sirve de prueba -- el droplet puede estar
        en otro commit, o con el fichero tocado a mano. Se han ido dias en esa
        duda.
        """
        tiers = getattr(self, "tp_tiers", None) or []
        peldanos = " · ".join(
            f"{'+' + format(t.gain * 100, '.0f') + '%' if t.gain < 1 else 'x' + format(1 + t.gain, '.1f')}"
            f"→{t.sell * 100:.1f}%"
            for t in tiers
        )
        vendido = sum(t.sell for t in tiers)
        stop = float(getattr(self, "stop_loss_percentage", 0) or 0) * 100
        margen = float(getattr(self, "margen_stop_tras_tp", 0) or 0) * 100
        ancho = float(getattr(self, "sell_slippage_proteccion", 0) or 0) * 100
        return (
            f"SALIDAS ACTIVAS · stop -{stop:.0f}% · tras cada peldaño el suelo "
            f"sube al anterior +{margen:.0f}% · suelo de venta protectora "
            f"{ancho:.0f}% · vendido {vendido * 100:.0f}% en "
            f"{len(tiers)} peldaños: {peldanos}"
        )

    async def start(self) -> None:
        """Start the trading bot and listen for new tokens."""
        logger.info(f"Starting Universal Trader for {self.platform.value}")
        _cartel = self._cartel_de_configuracion()
        logger.info(_cartel)
        # LA ESCALERA Y EL STOP, EN LINEA APARTE. El cartel de arriba tiene que
        # caber en una linea de movil --hay un test que lo exige-- asi que esto
        # va suelto. Sale una vez por arranque.
        #
        # POR QUE EXISTE: "no saltan los take profit" es imposible de contestar
        # sin saber que numeros esta usando LA MAQUINA. El yaml del repo no
        # sirve de prueba: el droplet puede estar en otro commit o con el
        # fichero tocado a mano. Se han ido dias en esa duda.
        logger.info(self._cartel_de_salidas())
        # Y TAMBIEN A TELEGRAM. El log vive en un droplet que se administra
        # desde un movil: en la practica no lo lee nadie. Manu si lee Telegram,
        # y esta es la linea que contesta "¿ya va en real?" sin escribir ni un
        # comando -- la pregunta que se ha hecho cuatro veces esta semana
        # porque no habia forma de verlo.
        #
        # Sale UNA vez por arranque, asi que no puede volverse ruido.
        try:
            from monitoring.notifier import notify  # noqa: PLC0415

            notify("🤖 <b>BOT ARRANCADO</b>\n" + _cartel.replace(" · ", "\n"))
        except Exception:  # noqa: BLE001, S110
            pass
        self.paper_engine.start()
        # RETOMAR ANTES QUE NADA. Lo que habia aqui era `_check_orphan_position`,
        # que avisaba de la posicion abierta y borraba su marcador SIN volver a
        # vigilarla: la moneda se quedaba en la wallet sin stop loss ni take
        # profit. Un reinicio de treinta segundos desarmaba la estrategia
        # entera, y con varios reinicios en un dia no quedaba nada en pie.
        #
        # Ahora se retoman. Lo que no se pueda retomar (no se reconstruye, o no
        # se puede leer su saldo) conserva su marcador y lo avisa el chequeo de
        # huerfanas de la vuelta siguiente, en vez de borrarse en silencio.
        await self._reanudar_posiciones()
        # Avisador de moonbags: cada 5 min notifica por Telegram los moonbags
        # retenidos para venderlos a mano (nunca los vende el bot).
        asyncio.create_task(self._moonbag_notifier_loop())  # noqa: RUF006
        asyncio.create_task(self._reinicia_tanda_loop())  # noqa: RUF006
        asyncio.create_task(self._precio_sol_loop())  # noqa: RUF006
        if self.dip.get("pico_min_mc"):
            asyncio.create_task(self._dip_scanner_loop())  # noqa: RUF006
        if self.senal_gmgn:
            from monitoring.senales import refrescar

            asyncio.create_task(refrescar(self.senal_gmgn))  # noqa: RUF006
        logger.info(
            f"Match filter: {self.match_string if self.match_string else 'None'}"
        )
        if self.bro_address:
            _preview = ", ".join(sorted(self.bro_address)[:3])
            _more = "" if len(self.bro_address) <= 3 else f" (+{len(self.bro_address) - 3} more)"
            logger.info(f"Creator filter: watching {len(self.bro_address)} wallet(s): {_preview}{_more}")
        else:
            logger.info("Creator filter: None")
        logger.info(f"Marry mode: {self.marry_mode}")
        logger.info(f"YOLO mode: {self.yolo_mode}")
        logger.info(self.dex_filter.describe())
        logger.info(f"Exit strategy: {self.exit_strategy}")

        if self.exit_strategy == "tp_sl":
            logger.info(
                f"Take profit: {self.take_profit_percentage * 100 if self.take_profit_percentage else 'None'}%"
            )
            logger.info(
                f"Stop loss: {self.stop_loss_percentage * 100 if self.stop_loss_percentage else 'None'}%"
            )
            logger.info(
                f"Max hold time: {self.max_hold_time if self.max_hold_time else 'None'} seconds"
            )

        logger.info(f"Max token age: {self.max_token_age} seconds")

        try:
            health_resp = await self.solana_client.get_health()
            logger.info(f"RPC warm-up successful (getHealth passed: {health_resp})")
        except Exception as e:
            logger.warning(f"RPC warm-up failed: {e!s}")

        if self.copy_wallets.get("enabled"):
            logger.info("Copy-trading mode ENABLED (copia compras de wallets)")
            await self._run_copy_mode()
            return

        await self._fuentes_o_escuchador()

    async def _fuentes_o_escuchador(self) -> None:  # noqa: C901, PLR0912
        """Arranca las fuentes encendidas o, si no hay ninguna, el escuchador."""
        # LAS FUENTES SALEN DE `FUENTES`, no de una lista escrita aqui. Antes
        # esto eran ocho `if` a mano y la lista vivia repetida en cuatro
        # sitios; al anadir `presion` falto en tres de ellos. Ahora anadir una
        # fuente es una linea en `FUENTES` y su `_run_*_mode`.
        # EL PARTE DE DESCARTES NO ES UNA FUENTE (6-nov). Iba metido en la
        # lista de fuentes, asi que con TODAS las fuentes apagadas la lista
        # nunca estaba vacia: el bot se quedaba esperando al parte y no llegaba
        # a abrir el escuchador. Vivo, callado y sin ver una sola moneda. Paso
        # en dev1 al apagar curva_96 ("fuentes: NINGUNA (listener normal)").
        resumen_task: asyncio.Task | None = None
        if self.resumen_descartes_seg > 0:
            resumen_task = asyncio.create_task(self._vigilar_los_descartes())
        acquisition_tasks: list[asyncio.Task] = []
        for attr, corto in self.FUENTES:
            if not (getattr(self, attr, None) or {}).get("enabled"):
                continue
            arranca = getattr(self, f"_run_{corto}_mode", None)
            if arranca is None:
                logger.error(
                    "La fuente '%s' esta encendida pero no tiene _run_%s_mode: "
                    "no se va a ejecutar", corto, corto,
                )
                continue
            logger.info("%s acquisition ENABLED", corto)
            acquisition_tasks.append(asyncio.create_task(arranca()))
        if acquisition_tasks:
            if resumen_task is not None:
                acquisition_tasks.append(resumen_task)
            await asyncio.gather(*acquisition_tasks)
            return

        logger.info("Sin fuentes: escuchando las creaciones con el listener normal")
        try:
            # Choose operating mode based on yolo_mode
            if not self.yolo_mode:
                # Single token mode: process one token and exit
                logger.info(
                    "Running in single token mode - will process one token and exit"
                )
                token_info = await self._wait_for_token()
                if token_info:
                    await self._handle_token(token_info)
                    logger.info("Finished processing single token. Exiting...")
                else:
                    logger.info(
                        f"No suitable token found within timeout period ({self.token_wait_timeout}s). Exiting..."
                    )
            else:
                # Continuous mode: process tokens until interrupted
                logger.info(
                    "Running in continuous mode - will process tokens until interrupted"
                )
                processor_task = asyncio.create_task(self._process_token_queue())

                try:
                    await self.token_listener.listen_for_tokens(
                        lambda token: self._queue_token(token),
                        self.match_string,
                        self.bro_address,
                    )
                except Exception:
                    logger.exception("Token listening stopped due to error")
                finally:
                    processor_task.cancel()
                    try:
                        await processor_task
                    except asyncio.CancelledError:
                        pass

        except Exception:
            logger.exception("Trading stopped due to error")

        finally:
            if resumen_task is not None:
                resumen_task.cancel()
            await self._cleanup_resources()
            logger.info("Universal Trader has shut down")

    async def _wait_for_token(self) -> TokenInfo | None:
        """Wait for a single token to be detected."""
        # Create a one-time event to signal when a token is found
        token_found = asyncio.Event()
        found_token = None

        async def token_callback(token: TokenInfo) -> None:
            nonlocal found_token
            token_key = str(token.mint)

            # Only process if not already processed and fresh
            if token_key not in self.processed_tokens:
                # Record when the token was discovered
                self.token_timestamps[token_key] = monotonic()
                found_token = token
                self.processed_tokens.add(token_key)
                token_found.set()

        listener_task = asyncio.create_task(
            self.token_listener.listen_for_tokens(
                token_callback,
                self.match_string,
                self.bro_address,
            )
        )

        # Wait for a token with a timeout
        try:
            logger.info(
                f"Waiting for a suitable token (timeout: {self.token_wait_timeout}s)..."
            )
            await asyncio.wait_for(token_found.wait(), timeout=self.token_wait_timeout)
            logger.info(f"Found token: {found_token.symbol} ({found_token.mint})")
            return found_token
        except TimeoutError:
            logger.info(
                f"Timed out after waiting {self.token_wait_timeout}s for a token"
            )
            return None
        finally:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

    async def _cleanup_resources(self) -> None:
        """Perform cleanup operations before shutting down."""
        if self.traded_mints:
            try:
                logger.info(f"Cleaning up {len(self.traded_mints)} traded token(s)...")
                # Build parallel lists of mints and token_program_ids
                mints_list = list(self.traded_mints)
                token_program_ids = [
                    self.traded_token_programs.get(str(mint)) for mint in mints_list
                ]
                await handle_cleanup_post_session(
                    self.solana_client,
                    self.wallet,
                    mints_list,
                    token_program_ids,
                    self.priority_fee_manager,
                    self.cleanup_mode,
                    self.cleanup_with_priority_fee,
                    self.cleanup_force_close_with_burn,
                )
            except Exception:
                logger.exception("Error during cleanup")

        old_keys = {k for k in self.token_timestamps if k not in self.processed_tokens}
        for key in old_keys:
            self.token_timestamps.pop(key, None)

        await self.dex_filter.close()
        await self.solana_client.close()

    async def _queue_token(self, token_info: TokenInfo) -> None:
        """Queue a token for processing if not already processed."""
        token_key = str(token_info.mint)

        if token_key in self.processed_tokens:
            logger.debug(f"Token {token_info.symbol} already processed. Skipping...")
            return

        # Record timestamp when token was discovered
        self.token_timestamps[token_key] = monotonic()
        if self.dip.get("pico_min_mc"):
            self._token_info_cache[token_key] = token_info
            if len(self._token_info_cache) > 4000:
                # Se tira lo mas viejo; el scanner de dip no mira mas alla
                # de dip.ventana_seg de todas formas.
                _viejo = min(
                    self._token_info_cache,
                    key=lambda m: self.token_timestamps.get(m, 0.0),
                )
                self._token_info_cache.pop(_viejo, None)
                self._dip_pico.pop(_viejo, None)

        await self.token_queue.put(token_info)
        logger.info(
            f"Queued new token: {token_info.symbol} ({token_info.mint}) on {token_info.platform.value}"
        )

    async def _process_token_queue(self) -> None:
        """Continuously process tokens from the queue, only if they're fresh."""
        while True:
            try:
                token_info = await self.token_queue.get()
                token_key = str(token_info.mint)

                # Check if token is still "fresh"
                current_time = monotonic()
                token_age = current_time - self.token_timestamps.get(
                    token_key, current_time
                )

                if token_age > self.max_token_age:
                    logger.info(
                        f"Skipping token {token_info.symbol} - too old ({token_age:.1f}s > {self.max_token_age}s)"
                    )
                    continue

                # Skip only when the FILTER stage is saturated (bounds pending
                # work); held positions no longer block new filtering.
                if self._filter_semaphore.locked():
                    logger.debug(f"Filtro saturado; se omite {token_info.symbol}")
                    continue

                self.processed_tokens.add(token_key)

                logger.info(
                    f"Processing fresh token: {token_info.symbol} (age: {token_age:.1f}s)"
                )
                # Run filter+buy+exit-monitor in the BACKGROUND so held positions
                # don't block detection. Concurrency is bounded inside _handle_token
                # (_filter_semaphore for checks, _position_semaphore for open buys).
                asyncio.create_task(self._handle_token(token_info))  # noqa: RUF006

            except asyncio.CancelledError:
                logger.info("Token queue processor was cancelled")
                break
            except Exception:
                logger.exception("Error in token queue processor")
            finally:
                self.token_queue.task_done()

    async def _run_copy_mode(self) -> None:
        """Copy the pump.fun buys of the watched wallets until interrupted."""
        from monitoring.wallet_copy_source import WalletCopySource

        source = WalletCopySource(
            self.copy_wallets.get("wallets", []),
            self.platform_implementations,
            self.solana_client,
            rpc_endpoint=self.solana_client.rpc_endpoint,
            poll_interval=float(self.copy_wallets.get("poll_interval", 2.0)),
            follow_funded=bool(self.copy_wallets.get("follow_funded", True)),
            min_funding_sol=float(self.copy_wallets.get("min_funding_sol", 0.05)),
            max_discovered=int(self.copy_wallets.get("max_discovered", 40)),
            wallets_file=self.copy_wallets.get("wallets_file"),
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("Copy-trading mode stopped")

    async def _run_dexpaid_mode(self) -> None:
        """Run the buy-on-dex-paid acquisition loop until interrupted."""
        from monitoring.dexpaid_source import DexPaidSource

        source = DexPaidSource(
            self.platform_implementations,
            self.solana_client,
            poll_interval=float(self.buy_on_dex_paid.get("poll_interval", 30)),
            max_age_minutes=float(self.buy_on_dex_paid.get("max_age_minutes", 180)),
            max_token_age_minutes=float(
                self.buy_on_dex_paid.get("max_token_age_minutes", 30)
            ),
            normal_buy_amount=float(
                self.buy_on_dex_paid.get("normal_buy_amount", self.buy_amount)
            ),
            heartbeat_interval=float(
                self.buy_on_dex_paid.get("heartbeat_interval", 300)
            ),
            paper_mode=self.paper_trade or self.paper_engine.enabled,
            incluir_migradas=bool(
                self.buy_on_dex_paid.get("incluir_migradas", False)
            ),
            incluir_cualquier_origen=bool(
                self.buy_on_dex_paid.get("incluir_cualquier_origen", False)
            ),
            min_lp_locked_pct=float(
                self.buy_on_dex_paid.get("min_lp_locked_pct", 50)
            ),
            otros_max_holder_pct=float(
                self.buy_on_dex_paid.get("otros_max_holder_pct", 0.05)
            ),
            otros_min_market_cap=float(
                self.buy_on_dex_paid.get("otros_min_market_cap", 0)
            ),
            otros_factor_importe=float(
                self.buy_on_dex_paid.get("otros_factor_importe", 1.0)
            ),
            boost_extra_umbral=float(
                self.buy_on_dex_paid.get("boost_extra_umbral", 0)
            ),
            boost_extra_factor=float(
                self.buy_on_dex_paid.get("boost_extra_factor", 1.0)
            ),
            candidatos_por_vuelta=int(
                self.buy_on_dex_paid.get("candidatos_por_vuelta", 30)
            ),
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("Buy-on-dex-paid mode stopped")

    async def _run_birdeye_mode(self) -> None:
        """Run the Birdeye-trending acquisition loop until interrupted."""
        from monitoring.birdeye_source import BirdeyeTrendingSource

        cfg = self.birdeye_trending
        source = BirdeyeTrendingSource(
            self.platform_implementations,
            self.solana_client,
            poll_interval=float(cfg.get("poll_interval", 30)),
            limit=int(cfg.get("limit", 20)),
            buy_amount=float(cfg.get("buy_amount", self.buy_amount)),
            min_liquidity_usd=float(cfg.get("min_liquidity_usd", 0)),
            min_volume_h24=float(cfg.get("min_volume_h24", 0)),
            heartbeat_interval=float(cfg.get("heartbeat_interval", 300)),
            paper_mode=self.paper_trade or self.paper_engine.enabled,
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("Birdeye-trending mode stopped")

    def _otro_origen_agotado(self, token_info: TokenInfo) -> bool:
        """True si la moneda es de fuera de pump.fun y ya no toca comprarla.

        Dos frenos independientes de los de casa: un cupo de compras y un
        corte por perdidas. Las de pump.fun no pasan por aqui, asi que su
        comportamiento no cambia en nada.
        """
        if (token_info.additional_data or {}).get("origen") != "otro":
            return False
        cfg = self.buy_on_dex_paid or {}
        cupo = int(cfg.get("otros_max_compras", 0) or 0)
        tope_perdidas = int(cfg.get("otros_parar_tras_perdidas", 0) or 0)
        if tope_perdidas > 0 and self._perdidas_otro_origen >= tope_perdidas:
            logger.warning(
                "OTRO ORIGEN PARADO: %d perdidas; %s no se compra. Las de "
                "pump.fun siguen igual.",
                self._perdidas_otro_origen,
                token_info.symbol,
            )
            return True
        if cupo > 0 and self._compras_otro_origen >= cupo:
            logger.info(
                "Cupo de otro origen agotado (%d/%d); %s no se compra",
                self._compras_otro_origen,
                cupo,
                token_info.symbol,
            )
            return True
        # Igual que arriba: se cuenta la compra confirmada, no el intento.
        return False

    def _curva96_agotada(self, token_info: TokenInfo) -> bool:
        """True si la moneda entro por avance de curva y ya no toca comprarla.

        Es una prueba acotada a proposito: se compran unas pocas y, si la
        mayoria sale mal, se corta sola. Los frenos son SUYOS, independientes
        de los de pump.fun normal y de los de fuera, asi que ni gastan el cupo
        de las otras ni se lo comen.
        """
        if (token_info.additional_data or {}).get("origen") != "curva96":
            return False
        cfg = self.curva_96 or {}
        cupo = int(cfg.get("max_compras", 3) or 0)
        tope_perdidas = int(cfg.get("parar_tras_perdidas", 2) or 0)
        if tope_perdidas > 0 and self._perdidas_curva96 >= tope_perdidas:
            logger.warning(
                "CURVA 96%% PARADA: %d pérdidas; %s no se compra. El resto de "
                "fuentes sigue igual.",
                self._perdidas_curva96,
                token_info.symbol,
            )
            return True
        if cupo > 0 and self._compras_curva96 >= cupo:
            logger.info(
                "Cupo de curva 96%% agotado (%d/%d); %s no se compra",
                self._compras_curva96,
                cupo,
                token_info.symbol,
            )
            return True
        # OJO: aqui NO se suma nada. El cupo cuenta POSICIONES ABIERTAS, no
        # intentos. Al 96% la moneda puede migrar en segundos y la transaccion
        # falla; contando intentos, tres fallos se comian el cupo entero y la
        # prueba no llegaba a hacerse. Ademas "si en dos perdemos" no se
        # sostiene sobre compras que nunca entraron. Se suma en
        # `_apunta_compra_por_origen`, cuando la compra esta confirmada.
        return False

    def _cuenta_compra_para_el_resumen(self) -> None:
        """Una compra apaga el parte de descartes de este tramo."""
        self._compras_del_resumen += 1

    def _apunta_compra_por_origen(self, token_info: TokenInfo) -> None:
        """Suma una POSICION ABIERTA al cupo del regimen que la trajo.

        Se llama con la compra ya confirmada, no al intentarla: un intento que
        no entra no gasta cupo ni puede perder dinero, asi que contarlo
        gastaria la prueba sin haberla hecho.
        """
        origen = (token_info.additional_data or {}).get("origen")
        if origen == "curva96":
            self._compras_curva96 += 1
            logger.info(
                "CURVA 96%%: compra %d de %d abierta (%s)",
                self._compras_curva96,
                int((self.curva_96 or {}).get("max_compras", 3) or 0),
                token_info.symbol,
            )
        elif origen == "otro":
            self._compras_otro_origen += 1
            logger.info(
                "OTRO ORIGEN: compra %d de %d abierta (%s)",
                self._compras_otro_origen,
                int(
                    (self.buy_on_dex_paid or {}).get("otros_max_compras", 0) or 0
                ),
                token_info.symbol,
            )

    def _apunta_cierre_curva96(self, token_info: TokenInfo, neto: float) -> None:
        """Cuenta una perdida del regimen de curva, para poder cortarlo."""
        if (token_info.additional_data or {}).get("origen") != "curva96":
            return
        if neto >= 0:
            return
        self._perdidas_curva96 += 1
        tope = int((self.curva_96 or {}).get("parar_tras_perdidas", 2) or 0)
        logger.warning(
            "CURVA 96%%: pérdida %d de %d (%+.4f SOL) en %s",
            self._perdidas_curva96,
            tope,
            neto,
            token_info.symbol,
        )

    def _apunta_cierre_otro_origen(self, token_info: TokenInfo, neto: float) -> None:
        """Cuenta una perdida del regimen aparte, para poder cortarlo."""
        if (token_info.additional_data or {}).get("origen") != "otro":
            return
        if neto >= 0:
            return
        self._perdidas_otro_origen += 1
        tope = int(
            (self.buy_on_dex_paid or {}).get("otros_parar_tras_perdidas", 0) or 0
        )
        logger.warning(
            "OTRO ORIGEN: perdida %d de %d (%+.4f SOL) en %s",
            self._perdidas_otro_origen,
            tope,
            neto,
            token_info.symbol,
        )

    def _avisa_si_el_nodo_no_da_mas(self) -> None:
        """Avisa cuando hay mas posiciones de las que el nodo puede vigilar.

        Cada posicion abierta consulta su precio cada `price_check_interval`
        segundos. Pasado el limite del nodo (`node.max_rps`) las consultas se
        encolan, y lo que se retrasa es el STOP LOSS: justo lo que no puede
        llegar tarde. Se avisa en vez de bloquear porque quien decide cuanto
        riesgo asume es quien puso el dinero, no este metodo.
        """
        intervalo = float(getattr(self, "price_check_interval", 0) or 0)
        tope_rps = float(getattr(self.solana_client, "max_rps", 0) or 0)
        if intervalo <= 0 or tope_rps <= 0:
            return
        # Se deja la mitad del presupuesto para comprar, no solo vigilar.
        vigilables = int(tope_rps * intervalo / 2)
        abiertas = self._posiciones_abiertas()
        if vigilables > 0 and abiertas >= vigilables:
            logger.warning(
                "NODO AL LIMITE: %s posiciones abiertas y el nodo solo da para "
                "vigilar ~%s cada %.0fs. El stop loss puede reaccionar tarde. "
                "Sube price_check_interval antes que el numero de posiciones.",
                abiertas,
                vigilables,
                intervalo,
            )

    def _posiciones_abiertas(self) -> int:
        """Cuantas posiciones tienen ahora mismo capital dentro.

        Se cuentan los marcadores en disco (`_open_position_paths`), que es
        lo que sobrevive a un reinicio: el semaforo se recrea vacio al
        arrancar y diria cero con posiciones abiertas de verdad.
        """
        try:
            return len(self._open_position_paths())
        except OSError:
            return 0

    def _abiertas_entre_todos(self) -> int:
        """Posiciones abiertas de TODAS las estrategias de esta carpeta.

        Cada bot deja `open_position_<estrategia>__<mint>.json` mientras vigila
        una moneda, y todos arrancan en la misma carpeta: contar esos ficheros
        es contar las posiciones de todos, sobreviva o no un reinicio.
        """
        try:
            return len(list(self._open_pos_path.parent.glob("open_position*.json")))
        except OSError:
            return 0

    def _tope_comun_lleno(self) -> bool:
        """True si ya hay `max_posiciones_total` posiciones entre todos."""
        tope = int(getattr(self, "max_posiciones_total", 0) or 0)
        return tope > 0 and self._abiertas_entre_todos() >= tope

    def _fraccion_de_esta_compra(self) -> tuple[float, int]:
        """La fraccion que toca a la compra que se esta montando ahora.

        Devuelve `(fraccion, indice_del_peldano)`. Sin escalera configurada
        devuelve el escalar `fraccion_saldo` y el indice 0, o sea el
        comportamiento de siempre.

        PEDIDO (15-oct): "33% del portfolio de sol por operacion y el 40% de
        la segunda y un 50% de la restante a la tercera".

        QUE POSICION ES ESTA. Se cuenta con `_posiciones_abiertas()`, que lee
        los marcadores EN DISCO. Hay dos razones para no usar el semaforo:
        tras un reinicio el semaforo se recrea vacio y diria cero con
        posiciones vivas --la siguiente compra usaria el 33% de un saldo ya
        comprometido-- y la posicion que se esta montando ahora todavia no
        tiene marcador, asi que "0 abiertas" significa correctamente "esta es
        la primera".

        SI HAY MAS POSICIONES QUE PELDANOS se repite el ultimo. No se cae al
        escalar ni se levanta por indice: con `max_posiciones` por encima de
        los peldanos escritos, la compra de mas usaria el porcentaje mas alto,
        que sobre un saldo ya mermado es el lado seguro.
        """
        if not self.fracciones_saldo:
            return self.fraccion_saldo, 0
        abiertas = self._posiciones_abiertas()
        indice = min(max(0, abiertas), len(self.fracciones_saldo) - 1)
        return self.fracciones_saldo[indice], indice

    def _valor_en_posiciones_lamports(self) -> int:
        """Lo que ya esta metido en monedas, en lamports. 0 si no se sabe.

        POR QUE EXISTE. El tamano de compra se pide "el 30% de la CARTERA",
        y el saldo de la wallet no es la cartera: en cuanto se abre una
        posicion, ese SOL deja de estar en la wallet y pasa a ser moneda.
        Calculando sobre el saldo a secas, cada compra sale mas pequena que
        la anterior (0,30 · 0,21 · 0,15 · 0,10) aunque no se haya perdido
        nada -- que es justo lo que no se quiere. Sumando lo que hay dentro
        de las posiciones, la base vuelve a ser la cartera entera y las tres
        posiciones salen iguales.

        De donde sale el numero: del mismo marcador en disco que ya sirve
        para retomar las posiciones tras un reinicio. Se prefiere
        `value_sol` --el valor de AHORA, que refresca `_write_live_status`
        con cada vuelta-- y si el marcador es recien nacido y todavia no lo
        tiene, se usa el coste de entrada (`entry_price * amount`).

        NUNCA LEVANTA. Un marcador ilegible no puede impedir una compra: en
        el peor caso devuelve de menos y se compra mas pequeno, que es el
        lado seguro del error.
        """
        total = 0.0
        try:
            paths = self._open_position_paths()
        except OSError:
            return 0
        for path in paths:
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            try:
                valor = float(rec.get("value_sol") or 0)
                if valor <= 0:
                    valor = float(rec.get("entry_price") or 0) * float(
                        rec.get("amount") or 0
                    )
            except (TypeError, ValueError):
                continue
            if valor > 0:
                total += valor
        return int(total * LAMPORTS_PER_SOL)

    async def _run_dextools_mode(self) -> None:
        """Run the DEXTools DEXTboard acquisition loop until interrupted."""
        from monitoring.dextools_source import DextoolsHotPoolsSource

        cfg = self.dextools_hotpools
        source = DextoolsHotPoolsSource(
            self.platform_implementations,
            self.solana_client,
            poll_interval=float(cfg.get("poll_interval", 15)),
            limit=int(cfg.get("limit", 30)),
            normal_buy_amount=float(cfg.get("normal_buy_amount", self.buy_amount)),
            promoted_rank=int(cfg.get("promoted_rank", 10)),
            promoted_buy_amount=float(
                cfg.get("promoted_buy_amount", cfg.get("normal_buy_amount", 0.2))
            ),
            medium_rank=int(cfg.get("medium_rank", 0)),
            medium_buy_amount=float(cfg.get("medium_buy_amount", 0)),
            heartbeat_interval=float(cfg.get("heartbeat_interval", 300)),
            paper_mode=self.paper_trade or self.paper_engine.enabled,
            incluir_migradas=bool(cfg.get("incluir_migradas", True)),
            incluir_cualquier_origen=bool(cfg.get("incluir_cualquier_origen", False)),
            min_lp_locked_pct=float(cfg.get("min_lp_locked_pct", 50)),
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("DEXTools-hotpools mode stopped")

    async def _run_curva96_mode(self) -> None:
        """Compra las monedas de pump.fun cuya curva va casi llena."""
        from monitoring.curva96_source import Curva96Source

        cfg = self.curva_96
        source = Curva96Source(
            self.wss_endpoint,
            self.platform_implementations,
            self.solana_client,
            umbral=float(cfg.get("umbral", 0.96)),
            tope=float(cfg.get("tope", 0.995)),
            buy_amount=float(cfg.get("buy_amount", 0.2)),
            max_carteras_primer_bloque=int(cfg.get("max_carteras_primer_bloque", 3)),
            max_sol_primer_bloque=float(cfg.get("max_sol_primer_bloque", 2.0)),
            min_compradores=int(cfg.get("min_compradores", 30)),
            min_edad_seg=float(cfg.get("min_edad_seg", 0) or 0),
            max_edad_seg=float(cfg.get("max_edad_seg", 0) or 0),
            min_compradores_ola=int(cfg.get("min_compradores_ola", 8)),
            ventana_presion=float(cfg.get("ventana_presion", 3.0)),
            min_presion_ratio=float(cfg.get("min_presion_ratio", 2.0)),
            min_compra_sol=float(cfg.get("min_compra_sol", 1.0)),
            vela_seg=float(cfg.get("vela_seg", 1.0)),
            max_subida_vela=float(cfg.get("max_subida_vela", 0.35)),
            max_bundle_pct=float(cfg.get("max_bundle_pct", 0.5)),
            max_bundle_pct_sin_historia=float(
                cfg.get("max_bundle_pct_sin_historia", 0.35)
            ),
            exigir_ver_creacion=bool(cfg.get("exigir_ver_creacion", True)),
            heartbeat_interval=float(cfg.get("heartbeat_interval", 300)),
            paper_mode=self.paper_trade or self.paper_engine.enabled,
            revisar_cada_seg=float(cfg.get("revisar_cada_seg", 0) or 0),
            log_presion_cada_seg=float(cfg.get("log_presion_cada_seg", 0) or 0),
            ultimas_compras=int(cfg.get("ultimas_compras", 0) or 0),
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("Curva-casi-llena mode stopped")

    async def _run_dex_presion_mode(self) -> None:
        """Presion compradora en PumpSwap, Raydium y Meteora (7-nov)."""
        from monitoring.dex_presion_source import DexPresionSource

        cfg = self.dex_presion
        salida = {
            "tramos": [(t.gain, t.sell) for t in self.tp_tiers],
            "stop": float(self.stop_loss_percentage or 0),
            "trailing": float(self.trailing_stop_percentage or 0),
            "margen_tras_tp": (
                self.margen_stop_tras_tp if self.stop_a_entrada_tras_tp else 0.0
            ),
            "muerta_seg": float(self.venta_muerta_seg or 0),
            "muerta_gain": float(self.venta_muerta_gain or 0),
        }
        source = DexPresionSource(
            self.solana_client,
            self.platform_implementations,
            cfg,
            salida,
            en_papel=bool(cfg.get("en_papel", True)) or self.paper_trade,
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("DEX presion mode stopped")

    async def _run_presion_mode(self) -> None:
        """Compra las monedas donde entra mucha gente distinta de golpe."""
        from monitoring.presion_source import PresionSource

        cfg = self.presion
        source = PresionSource(
            self.wss_endpoint,
            self.platform_implementations,
            self.solana_client,
            min_compradores_ola=int(cfg.get("min_compradores_ola", 12)),
            ventana=float(cfg.get("ventana", 5.0)),
            buy_amount=float(cfg.get("buy_amount", 0.07)),
            max_avance_curva=float(cfg.get("max_avance_curva", 0.95)),
            max_carteras_primer_bloque=int(cfg.get("max_carteras_primer_bloque", 3)),
            max_sol_primer_bloque=float(cfg.get("max_sol_primer_bloque", 2.0)),
            max_bundle_pct=float(cfg.get("max_bundle_pct", 0.5)),
            max_holder_pct=float(cfg.get("max_holder_pct", 0.30)),
            max_holder_pct_sin_historia=float(
                cfg.get("max_holder_pct_sin_historia", 0.25)
            ),
            exigir_ver_creacion=bool(cfg.get("exigir_ver_creacion", True)),
            max_bundle_pct_sin_historia=float(
                cfg.get("max_bundle_pct_sin_historia", 0.35)
            ),
            heartbeat_interval=float(cfg.get("heartbeat_interval", 300)),
            paper_mode=self.paper_trade or self.paper_engine.enabled,
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("Presion-compradora mode stopped")

    async def _run_holders_mode(self) -> None:
        """Compra con N titulares y poca vida. Sin mas condiciones."""
        from monitoring.holders_source import HoldersSource

        cfg = self.holders
        source = HoldersSource(
            self.wss_endpoint,
            self.platform_implementations,
            self.solana_client,
            min_holders=int(cfg.get("min_holders", 90)),
            max_edad_seg=float(cfg.get("max_edad_seg", 300.0)),
            ultimas_compras=int(cfg.get("ultimas_compras", 2)),
            presion_seg=float(cfg.get("presion_seg", 3.0)),
            subida_seg=float(cfg.get("subida_seg", 10.0)),
            max_subida=float(cfg.get("max_subida", 0)),
            volumen_min_sol=float(cfg.get("volumen_min_sol", 0)),
            buy_amount=float(cfg.get("buy_amount", 0.07)),
            heartbeat_interval=float(cfg.get("heartbeat_interval", 300)),
            paper_mode=self.paper_trade or self.paper_engine.enabled,
            geyser_endpoint=self.geyser_endpoint,
            geyser_token=self.geyser_api_token,
            geyser_auth=self.geyser_auth_type,
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("Titulares mode stopped")

    async def _run_telegram_mode(self) -> None:
        """Run the Telegram-calls acquisition loop until interrupted."""
        from monitoring.telegram_source import TelegramSource

        source = TelegramSource(
            self.telegram_calls,
            self.platform_implementations,
            self.solana_client,
            self.platform,
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("Telegram-calls mode stopped")

    async def _run_discord_mode(self) -> None:
        """Escucha las calls de Discord hasta que se interrumpa."""
        from monitoring.discord_source import DiscordSource

        source = DiscordSource(
            self.discord_calls,
            self.platform_implementations,
            self.solana_client,
            self.platform,
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("Discord-calls mode stopped")

    async def _run_gmgn_mode(self) -> None:
        """Run the GMGN smart-money/KOL copy loop until interrupted."""
        from monitoring.gmgn_source import GmgnSource

        source = GmgnSource(self.gmgn_signals, self.platform_implementations, self.solana_client)
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("GMGN signals mode stopped")

    async def _on_dexpaid_token(self, token_info: TokenInfo) -> None:
        """Handle a dex-paid candidate: buy in the background (non-blocking)."""
        # Run each candidate independently so one position's exit monitor
        # doesn't stall detection of the next candidate.
        asyncio.create_task(self._handle_token(token_info))  # noqa: RUF006

    async def _paper_watch(
        self, token_info: TokenInfo, entry_price: float, curve_sol: float
    ) -> None:
        """Follow a detected bundle's price to see what the ladder would have done.

        Records max gain, max drawdown, each simulated sale price, the effective
        exit price and the configured stop ratchet. Pure observation: never trades.
        """
        import json
        import time
        from pathlib import Path

        # Use THIS bot's configured exit ladder (not a fixed one) so paper measures
        # the real strategy: TP1/TP2 gains and stop-loss come straight from config.
        # Los objetivos salen de `self.tp_tiers`, que es donde el trader guarda
        # de verdad la escalera. Antes esta linea leia `self.tp2_gain`, que NO
        # existe como atributo (solo llega como argumento del constructor): la
        # tarea de seguimiento moria con AttributeError en la primera linea, con
        # cada moneda, y el error se quedaba enterrado en un task sin dueno. El
        # 3-ago eso dejo la simulacion 14 minutos sin emitir un solo resultado
        # pareciendo que simplemente "aun no habia terminado".
        _g1 = float(self.tp_tiers[0].gain) if self.tp_tiers else 0.5
        _g2 = float(self.tp_tiers[1].gain) if len(self.tp_tiers) > 1 else None
        # Mirror the real exit monitor exactly.  ``0`` means that the
        # percentage stop is disabled; it must not silently become the old
        # paper-only -30% fallback.  An absolute MC stop is converted with the
        # same live SOL/USD snapshot used by the real monitor.
        _paper_sl_pct = float(self.stop_loss_percentage or 0)
        sl = entry_price * (1 - _paper_sl_pct) if _paper_sl_pct > 0 else 0.0
        if self.mc_stop_loss > 0:
            sl = max(
                sl,
                self.mc_stop_loss / (_PUMP_TOTAL_SUPPLY * _SOL_USD),
            )
        peak = trough = entry_price
        hit = {"tp1": False, "tp2": False, "sl": False}
        paper_executed = [False] * len(self.tp_tiers)
        tier_sale_prices: list[float | None] = [None] * len(self.tp_tiers)
        paper_mc_executed = [False] * len(self.mc_take_profit_tiers)
        mc_tier_sale_prices: list[float | None] = [
            None
        ] * len(self.mc_take_profit_tiers)
        final_target_price = (
            self.final_target_mc / (_PUMP_TOTAL_SUPPLY * _SOL_USD)
            if self.final_target_mc > 0
            else None
        )
        final_target_hit = False
        final_target_sale_price: float | None = None
        paper_floor = sl
        stop_exit_return: float | None = None
        strategy_closed = False
        first_hit = None
        last_price = entry_price
        start = time.time()
        try:
            async with self._watch_semaphore:
                while time.time() - start < self.paper_watch_secs:
                    await asyncio.sleep(5)
                    try:
                        st = await self.platform_implementations.curve_manager.get_pool_state(
                            token_info.bonding_curve, commitment="processed"
                        )
                        p = float(st.get("price_per_token") or 0)
                    except Exception:
                        continue
                    if p <= 0:
                        continue
                    last_price = p
                    peak, trough = max(peak, p), min(trough, p)
                    if strategy_closed:
                        continue
                    if p <= paper_floor:
                        hit["sl"] = True
                        stop_exit_return = paper_floor / entry_price - 1
                        first_hit = first_hit or "sl"
                        strategy_closed = True
                        self._avisa_peldano(
                            "STOP",
                            token_info,
                            entry_price,
                            paper_floor,
                            paper_executed,
                            tier_sale_prices,
                        )
                        continue
                    # El monitor real vende el remanente cuando alcanza un
                    # market cap absoluto. El observador paper debe simular
                    # exactamente la misma puerta; convertirlo a un porcentaje
                    # fijo es incorrecto porque cada entrada tiene otro MC.
                    if final_target_price is not None and p >= final_target_price:
                        final_target_hit = True
                        final_target_sale_price = final_target_price
                        first_hit = first_hit or "final_target_mc"
                        strategy_closed = True
                        continue
                    for i in tiers_due(
                        entry_price, p, self.tp_tiers, paper_executed
                    ):
                        paper_executed[i] = True
                        tier_sale_prices[i] = entry_price * (
                            1 + self.tp_tiers[i].gain
                        )
                        first_hit = first_hit or f"tp{i + 1}"
                        if i == 0:
                            hit["tp1"] = True
                        elif i == 1:
                            hit["tp2"] = True
                        if self.stop_a_entrada_tras_tp or self.stop_al_tp_tras_tp:
                            if i == 0 and self.stop_al_tp_tras_tp:
                                new_floor = entry_price * (1 + self.tp_tiers[0].gain)
                            else:
                                new_floor = stepped_stop_price(
                                    entry_price, self.tp_tiers, i
                                )
                            if new_floor is not None:
                                paper_floor = max(paper_floor, new_floor)
                        self._avisa_peldano(
                            f"TP{i + 1}",
                            token_info,
                            entry_price,
                            tier_sale_prices[i] or p,
                            paper_executed,
                            tier_sale_prices,
                            nuevo_sl=paper_floor,
                        )
                    # The real MC ladder is armed only after every percentage
                    # tier.  Fractions are relative to the original position.
                    if self.tp_tiers and all(paper_executed):
                        current_mc = p * _PUMP_TOTAL_SUPPLY * _SOL_USD
                        for i, (target_mc, _sell) in enumerate(
                            self.mc_take_profit_tiers
                        ):
                            if paper_mc_executed[i] or current_mc < target_mc:
                                continue
                            paper_mc_executed[i] = True
                            mc_tier_sale_prices[i] = (
                                target_mc / (_PUMP_TOTAL_SUPPLY * _SOL_USD)
                            )
                            first_hit = first_hit or f"mc_tp_{target_mc:.0f}"
                    sold_fraction = sum(
                        tier.sell
                        for i, tier in enumerate(self.tp_tiers)
                        if paper_executed[i]
                    )
                    sold_fraction += sum(
                        sell
                        for i, (_target_mc, sell) in enumerate(
                            self.mc_take_profit_tiers
                        )
                        if paper_mc_executed[i]
                    )
                    if sold_fraction >= 1.0:
                        strategy_closed = True
                    # NOTE: deliberately do NOT stop early. We must observe the
                    # full path to learn the real peak distribution (fat tail);
                    # stopping at +140% would hide the 10x/100x runners and make
                    # any take-profit choice unmeasurable.
        finally:
            signal_data = token_info.additional_data or {}
            paper_amount = float(
                signal_data.get("signal_buy_amount") or self.buy_amount
            )
            roi = 0.0
            remaining = 1.0
            sales: list[dict[str, float | str]] = []
            for i, tier in enumerate(self.tp_tiers):
                if not paper_executed[i] or remaining <= 0:
                    continue
                sold = min(float(tier.sell), remaining)
                sale_price = float(
                    tier_sale_prices[i] or entry_price * (1 + tier.gain)
                )
                roi += sold * (sale_price / entry_price - 1)
                remaining -= sold
                sales.append(
                    {
                        "reason": f"TP{i + 1}",
                        "position_pct": round(sold * 100, 4),
                        "price_sol": sale_price,
                    }
                )
            for i, (target_mc, sell_fraction) in enumerate(
                self.mc_take_profit_tiers
            ):
                if not paper_mc_executed[i] or remaining <= 0:
                    continue
                sold = min(float(sell_fraction), remaining)
                sale_price = float(
                    mc_tier_sale_prices[i]
                    or target_mc / (_PUMP_TOTAL_SUPPLY * _SOL_USD)
                )
                roi += sold * (sale_price / entry_price - 1)
                remaining -= sold
                sales.append(
                    {
                        "reason": f"MC_TP_{target_mc:.0f}",
                        "position_pct": round(sold * 100, 4),
                        "price_sol": sale_price,
                    }
                )
            if final_target_hit and remaining > 0:
                sale_price = float(final_target_sale_price or final_target_price or 0)
                target_return = sale_price / entry_price - 1
                roi += remaining * target_return
                sales.append(
                    {
                        "reason": "FINAL_TARGET_MC",
                        "position_pct": round(remaining * 100, 4),
                        "price_sol": sale_price,
                    }
                )
                remaining = 0.0
            if stop_exit_return is not None and remaining > 0:
                stop_price = entry_price * (1 + stop_exit_return)
                roi += remaining * stop_exit_return
                sales.append(
                    {
                        "reason": "STOP",
                        "position_pct": round(remaining * 100, 4),
                        "price_sol": stop_price,
                    }
                )
                remaining = 0.0
            sold_fraction = max(0.0, 1.0 - remaining)
            average_sale_price = (
                sum(
                    float(sale["price_sol"])
                    * (float(sale["position_pct"]) / 100)
                    for sale in sales
                )
                / sold_fraction
                if sold_fraction > 0
                else None
            )
            value = paper_amount * (1 + roi)
            pnl = value - paper_amount
            win = pnl > 0
            mark_to_market_roi = last_price / entry_price - 1
            mark_to_market_value = paper_amount * (1 + mark_to_market_roi)
            if stop_exit_return is not None:
                exit_reason = "stop"
            elif final_target_hit:
                exit_reason = "final_target_mc"
            elif remaining <= 1e-9:
                exit_reason = "take_profit"
            elif sales:
                exit_reason = "partial_take_profit"
            else:
                exit_reason = "open"
            rec = {
                "src": "bundle" if self.min_curve_sol > 0 else "dexpaid",
                "mint": str(token_info.mint),
                "symbol": (token_info.symbol or "").strip(),
                "name": (token_info.name or "").strip(),
                "acquisition_source": str(
                    signal_data.get("acquisition_source") or "deteccion directa"
                ),
                "dexscreener_boosts": float(
                    signal_data.get("dexscreener_boosts") or 0
                ),
                "dextools_nitro_score": float(
                    signal_data.get("dextools_nitro_score") or 0
                ),
                "amount_sol": paper_amount,
                "amount_usd": float(signal_data.get("paper_amount_usd") or 0),
                "curve_sol": round(curve_sol, 3),
                "entry": entry_price,
                "entry_price_sol": entry_price,
                "last_price_sol": last_price,
                "peak_pct": round((peak / entry_price - 1) * 100, 1),
                "trough_pct": round((trough / entry_price - 1) * 100, 1),
                "tp1": hit["tp1"],
                "tp2": hit["tp2"],
                "sl": hit["sl"],
                "final_target_mc": self.final_target_mc,
                "final_target_hit": final_target_hit,
                "stop_exit_pct": (
                    round(stop_exit_return * 100, 1)
                    if stop_exit_return is not None
                    else None
                ),
                "tp1_gain": round(_g1, 2),
                "tp2_gain": round(_g2 or 0.0, 2),
                "sl_pct": round(float(self.stop_loss_percentage or 0), 2),
                "initial_stop_price_sol": sl,
                "final_stop_price_sol": paper_floor,
                "take_profit_tiers": [
                    {
                        "reason": f"TP{i + 1}",
                        "gain_pct": round(tier.gain * 100, 4),
                        "sell_pct": round(tier.sell * 100, 4),
                        "price_sol": entry_price * (1 + tier.gain),
                        "hit": paper_executed[i],
                    }
                    for i, tier in enumerate(self.tp_tiers)
                ],
                "mc_take_profit_tiers": [
                    {
                        "target_mc": target_mc,
                        "sell_pct": round(sell * 100, 4),
                        "price_sol": (
                            target_mc / (_PUMP_TOTAL_SUPPLY * _SOL_USD)
                        ),
                        "hit": paper_mc_executed[i],
                    }
                    for i, (target_mc, sell) in enumerate(
                        self.mc_take_profit_tiers
                    )
                ],
                "sales": sales,
                "average_sale_price_sol": average_sale_price,
                "exit_price_sol": (
                    average_sale_price if remaining <= 1e-9 else None
                ),
                "exit_reason": exit_reason,
                "strategy_closed": remaining <= 1e-9,
                "position_sold_pct": round(sold_fraction * 100, 4),
                "position_remaining_pct": round(remaining * 100, 4),
                "tp_hits": sum(paper_executed) + sum(paper_mc_executed),
                "roi_pct": round(roi * 100, 4),
                "value_sol": value,
                "pnl_sol": pnl,
                "win": win,
                # Sin TP/SL no hay venta simulada. Estas cifras valoran la
                # posición al último precio observado para que el experimento
                # siga siendo medible sin inventar una salida.
                "mark_to_market_roi_pct": round(mark_to_market_roi * 100, 4),
                "mark_to_market_value_sol": mark_to_market_value,
                "mark_to_market_pnl_sol": mark_to_market_value - paper_amount,
                "first": first_hit,
                "secs": round(time.time() - start),
            }
            try:
                d = Path("paper_trades")
                d.mkdir(exist_ok=True)
                with open(d / "results.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            except Exception:
                logger.exception("paper watch write failed")
            logger.info(
                f"PAPER RESULT {rec['symbol']}: pico {rec['peak_pct']:+.1f}% "
                f"minimo {rec['trough_pct']:+.1f}% first={first_hit}"
            )
            try:
                from monitoring.notifier import (
                    build_paper_result_notification,
                    notify,
                )

                totals = self._bump_paper_total(pnl, win)
                notify(build_paper_result_notification(rec, signal_data, totals))
            except Exception:  # noqa: BLE001 - notificar nunca rompe el trading
                pass

    def _avisa_peldano(  # noqa: PLR0913
        self,
        evento: str,
        token_info: TokenInfo,
        entry_price: float,
        price: float,
        executed: list[bool],
        sale_prices: list[float | None],
        nuevo_sl: float | None = None,
    ) -> None:
        """Avisa por Telegram en el momento en que salta un TP o el stop.

        El informe final sigue saliendo al terminar la observacion; esto es lo
        que faltaba: enterarse CUANDO ocurre, con el motivo de compra y el P&L
        que lleva la posicion, no media hora despues.
        """
        try:
            from monitoring.notifier import build_ladder_event_notification, notify
            from trading.tiered_exit import ladder_progress

            signal_data = token_info.additional_data or {}
            amount = float(signal_data.get("signal_buy_amount") or self.buy_amount)
            vendido, _realizado, total = ladder_progress(
                entry_price, price, self.tp_tiers, executed, sale_prices
            )
            indice = int(evento[2:]) - 1 if evento.upper().startswith("TP") else -1
            este_peldano = (
                float(self.tp_tiers[indice].sell) * 100
                if 0 <= indice < len(self.tp_tiers)
                else 0.0
            )
            notify(
                build_ladder_event_notification(
                    evento,
                    token_info.name,
                    token_info.symbol,
                    str(token_info.mint),
                    signal_data,
                    price,
                    entry_price,
                    amount,
                    este_peldano,
                    max(0.0, 100.0 - vendido * 100),
                    total,
                    paper=True,
                    new_stop_price_sol=nuevo_sl,
                )
            )
        except Exception:  # noqa: BLE001 - notificar nunca rompe el trading
            logger.debug("aviso de peldano fallido", exc_info=True)

    def _bump_paper_total(self, pnl: float, win: bool) -> dict:
        """Acumula el P&L total de la simulacion paper y lo persiste (para el
        resumen que sale en cada aviso de Telegram)."""
        import json
        from pathlib import Path

        p = Path(f"paper_stats{self._state_suffix}.json")
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except Exception:  # noqa: BLE001
            data = {}
        data["pnl"] = float(data.get("pnl", 0.0)) + pnl
        data["n"] = int(data.get("n", 0)) + 1
        data["wins"] = int(data.get("wins", 0)) + (1 if win else 0)
        data["losses"] = int(data.get("losses", 0)) + (0 if win else 1)
        data["winrate"] = 100.0 * data["wins"] / data["n"] if data["n"] else 0.0
        try:
            p.write_text(json.dumps(data), encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.debug("no pude escribir paper_stats", exc_info=True)
        return data

    def _record_paper(
        self, token_info: TokenInfo, curve_sol: float = 0.0, entry: float = 0.0
    ) -> None:
        """Record a filter-passing candidate for offline ROI simulation (no buy)."""
        import json
        import time
        from pathlib import Path

        signal_data = token_info.additional_data or {}
        paper_amount = float(signal_data.get("signal_buy_amount") or self.buy_amount)
        rec = {
            "t": time.time(),
            "mint": str(token_info.mint),
            "symbol": (token_info.symbol or "").strip(),
            "acquisition_source": str(
                signal_data.get("acquisition_source") or "deteccion directa"
            ),
            "dexscreener_boosts": float(
                signal_data.get("dexscreener_boosts") or 0
            ),
            "dextools_nitro_score": float(
                signal_data.get("dextools_nitro_score") or 0
            ),
            "amount_sol": paper_amount,
            "curve_sol": round(curve_sol, 3),
            "entry": entry,
        }
        try:
            d = Path("paper_trades")
            d.mkdir(exist_ok=True)
            with open(d / "entries.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            logger.exception("paper record failed")
        logger.info(f"PAPER PASS registrado: {rec['symbol']} ({rec['mint']})")
        try:
            from monitoring.notifier import build_paper_entry_notification, notify

            mint = rec["mint"]
            market_cap_usd = entry * _PUMP_TOTAL_SUPPLY * _SOL_USD
            strategy = " · ".join(
                f"+{tier.gain * 100:.0f}%/{tier.sell * 100:.0f}%"
                for tier in self.tp_tiers
            )
            notify(
                build_paper_entry_notification(
                    symbol=rec["symbol"],
                    mint=mint,
                    name=rec.get("name"),
                    amount_sol=paper_amount,
                    source_data=signal_data,
                    entry_price_sol=entry,
                    market_cap_usd=market_cap_usd,
                    curve_sol=rec["curve_sol"],
                    strategy=strategy,
                    stop_loss_pct=self.stop_loss_percentage * 100,
                    take_profit_tiers=[
                        (tier.gain, tier.sell) for tier in self.tp_tiers
                    ],
                )
            )
        except Exception:  # noqa: BLE001 - notificar nunca rompe el trading
            pass

    def _load_bought_mints(self) -> set[str]:
        """Load the persistent already-bought mint blocklist from disk."""
        try:
            if self._bought_mints_path.exists():
                data = json.loads(self._bought_mints_path.read_text(encoding="utf-8"))
                return {str(m) for m in data}
        except Exception:
            logger.exception("No pude leer traded_mints.json; empiezo vacío")
        return set()

    def _persist_bought_mint(self, mint_str: str) -> None:
        """Persist the already-bought blocklist atomically (temp file + replace).

        B1 fix: a crash mid-write used to corrupt traded_mints.json, and
        _load_bought_mints swallows the error and starts with an EMPTY set —
        losing all dedup and risking mass re-buys on restart. Write to a temp
        file then os.replace (atomic on the same filesystem).
        """
        try:
            import os

            tmp = self._bought_mints_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(sorted(self.bought_mints)), encoding="utf-8")
            os.replace(tmp, self._bought_mints_path)
        except Exception:
            logger.exception(f"No pude persistir mint comprado {mint_str}")

    def _record_open_position(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Persist one marker per actively monitored position."""
        try:
            import os
            import time

            rec = {
                "mint": str(token_info.mint),
                "symbol": (token_info.symbol or "").strip(),
                "entry_price": buy_result.price,
                "amount": buy_result.amount,
                "suffix": self._state_suffix,
                "platform": token_info.platform.value,
                # Con esto se puede leer el saldo al retomar SIN reconstruir
                # la moneda antes: una que ya se vendio a mano se descarta
                # con una sola llamada en vez de con media docena.
                #
                # Va con getattr a proposito. Este marcador es LA RED DE
                # SEGURIDAD de la posicion: si un campo opcional que falta
                # hiciera saltar la excepcion, no se escribiria el marcador
                # entero y la posicion se quedaria sin forma de retomarse
                # tras un reinicio. Sin programa solo se pierde el atajo.
                "token_program": str(
                    getattr(token_info, "token_program_id", "") or ""
                ),
                "ts": time.time(),
            }
            path = self._open_position_path(str(token_info.mint))
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rec), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            logger.exception("No pude registrar la posición abierta")

    def _open_position_path(self, mint: str) -> Path:
        """Return a mint-specific marker path safe for concurrent positions."""
        safe_mint = "".join(c for c in str(mint) if c.isalnum())
        return self._open_pos_path.with_name(
            f"{self._open_pos_path.stem}__{safe_mint}.json"
        )

    def _open_position_paths(self) -> list[Path]:
        """Return legacy and mint-specific markers for this bot only."""
        pattern = f"{self._open_pos_path.stem}*.json"
        return sorted(self._open_pos_path.parent.glob(pattern))

    def _clear_open_position(self, mint: str | None = None) -> None:
        """Clear one marker, or every marker during startup recovery."""
        try:
            if mint is None:
                for path in self._open_position_paths():
                    path.unlink(missing_ok=True)
                return

            self._open_position_path(mint).unlink(missing_ok=True)
            # Backward compatibility with the old shared marker. Only remove it
            # when it belongs to this mint, never when another position owns it.
            if self._open_pos_path.exists():
                try:
                    rec = json.loads(self._open_pos_path.read_text(encoding="utf-8"))
                except Exception:
                    rec = {}
                if str(rec.get("mint") or "") == str(mint):
                    self._open_pos_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("No pude limpiar open_position.json")

    def _write_live_status(
        self,
        token_info: TokenInfo,
        entry_price: float,
        remaining: float,
        price: float,
        original_qty: float,
    ) -> None:
        """Escribe el estado en vivo de la posición para el panel de Telegram.

        Actualiza el archivo independiente de esta posición. Best-effort.
        """
        try:
            import os
            import time

            pnl = ((price / entry_price) - 1.0) * 100.0 if entry_price > 0 else 0.0
            value_sol = remaining * price
            rec = {
                "mint": str(token_info.mint),
                "symbol": (token_info.symbol or "").strip(),
                "suffix": self._state_suffix,
                "entry_price": entry_price,
                "amount": original_qty,
                "remaining": remaining,
                "price": price,
                "pnl_pct": round(pnl, 1),
                "value_sol": round(value_sol, 4),
                "platform": token_info.platform.value,
                "ts": time.time(),
            }
            path = self._open_position_path(str(token_info.mint))
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rec), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 - nunca romper el trading por el panel
            pass

    async def _check_manual_sell(
        self, token_info: TokenInfo, remaining: float, price: float
    ) -> float:
        """Panel de Telegram: si hay orden de venta manual para este mint, la
        ejecuta y devuelve el nuevo ``remaining``. Vende ``frac`` del RESTANTE
        (frac>=1 => todo). Best-effort; nunca lanza."""
        try:
            if not self._sell_cmd_path.exists():
                return remaining
            cmd = json.loads(self._sell_cmd_path.read_text(encoding="utf-8"))
            self._sell_cmd_path.unlink(missing_ok=True)
            if str(cmd.get("mint")) != str(token_info.mint):
                return remaining
            frac = float(cmd.get("frac") or 0)
            if frac <= 0:
                return remaining
            amount = remaining if frac >= 0.999 else remaining * frac
            if amount <= 1e-9:
                return remaining
            reason = f"manual_{int(round(frac * 100))}pct"
            if await self._sell_amount(token_info, amount, price, reason):
                remaining -= amount
                try:
                    from monitoring.notifier import notify

                    notify(
                        f"✅ VENTA MANUAL {int(round(frac * 100))}% · "
                        f"<b>{token_info.symbol}</b> @ {price:.8f} SOL"
                    )
                except Exception:  # noqa: BLE001
                    pass
        except Exception:
            logger.exception("Error en venta manual (panel)")
        return remaining

    async def _reanudar_posiciones(self) -> None:
        """Retoma la vigilancia de las posiciones que seguian abiertas.

        POR QUE ESTO ES LO MAS IMPORTANTE DE TODO EL ARRANQUE. Hasta ahora, al
        reiniciar el bot cada posicion abierta se daba por perdida: se avisaba
        de "bolsa huerfana", se borraba el marcador y nadie volvia a mirarla.
        Eso deja la moneda en la wallet SIN STOP LOSS y sin take profit, con lo
        que un reinicio de treinta segundos convertia una posicion gestionada
        en una apuesta a ciegas. Con varios reinicios en un dia --y aqui los
        hay-- eso es la diferencia entre tener una estrategia y no tenerla.

        Lo que se temia quien lo dejo asi era reconstruir mal la venta y vender
        una cantidad equivocada. Eso se evita sin renunciar al stop: la
        cantidad NO se cree del fichero, se lee de la cadena. El saldo real de
        la cuenta de token es la verdad, y ademas recoge lo que se haya vendido
        a mano mientras el bot estaba parado.

        El precio de entrada si sale del marcador: es un hecho historico que no
        se puede consultar en ningun sitio, y sin el no hay ni stop ni escalera.
        """
        try:
            paths = self._open_position_paths()
        except OSError:
            return
        if not paths:
            return

        logger.info("Retomando %d posicion(es) que quedaron abiertas", len(paths))
        for path in paths:
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            try:
                await self._reanudar_una(rec, path)
            except Exception:
                # Una posicion que no se puede retomar no puede impedir que se
                # retomen las demas: cada una lleva su propio stop loss.
                logger.exception(
                    "No pude retomar %s; queda como bolsa suelta",
                    rec.get("symbol") or rec.get("mint"),
                )

    async def _sigue_en_la_wallet(self, mint: str, token_program: str) -> bool:
        """True si la cuenta de token aun tiene algo. Ante la duda, True.

        Un fallo de red no puede hacer que se suelte una posicion viva: eso la
        dejaria sin stop loss, que es justo lo que se esta arreglando.
        """
        from solders.pubkey import Pubkey

        try:
            ata = self.wallet.get_associated_token_address(
                Pubkey.from_string(mint), Pubkey.from_string(token_program)
            )
            return bool(await self.solana_client.get_token_account_balance(ata))
        except Exception:  # noqa: BLE001
            return True

    async def _reanudar_una(self, rec: dict, path: Path) -> None:
        """Retoma UNA posicion, o suelta su marcador si ya no esta."""
        from trading.token_builder import build_pumpfun_token_info

        mint = str(rec.get("mint") or "").strip()
        sym = rec.get("symbol") or mint[:6]
        entrada = float(rec.get("entry_price") or 0)
        if not mint or entrada <= 0:
            path.unlink(missing_ok=True)
            return

        # ATAJO BARATO: si el marcador trae el programa de token se puede
        # comprobar el saldo YA. Una moneda vendida a mano con el bot parado
        # se descarta con una llamada, sin reconstruirla entera antes.
        _prog = str(rec.get("token_program") or "").strip()
        if _prog and not await self._sigue_en_la_wallet(mint, _prog):
            logger.info("%s ya no esta en la wallet: suelto el marcador", sym)
            path.unlink(missing_ok=True)
            return

        # Se permite migrada a proposito: una moneda que se fue a PumpSwap
        # mientras el bot estaba parado sigue siendo una posicion que vigilar.
        token_info = await build_pumpfun_token_info(
            mint, self.platform_implementations, self.solana_client,
            symbol=sym, permitir_migradas=True,
        )
        if token_info is None:
            logger.warning("No pude reconstruir %s (%s): queda suelta", sym, mint)
            return

        # LA CANTIDAD SALE DE LA CADENA, no del fichero. Si se vendio algo a
        # mano con el bot parado, el fichero miente y el saldo no.
        try:
            ata = self.wallet.get_associated_token_address(
                token_info.mint, token_info.token_program_id
            )
            cantidad = await self.solana_client.get_token_account_balance(ata)
        except Exception:  # noqa: BLE001
            logger.warning(
                "No pude leer el saldo de %s: no la retomo para no vender a "
                "ciegas una cantidad que no se", sym,
            )
            return

        if not cantidad or cantidad <= 0:
            logger.info("%s ya no esta en la wallet (vendida): suelto el marcador", sym)
            path.unlink(missing_ok=True)
            return

        self.bought_mints.add(mint)  # nunca recomprarla mientras se vigila
        logger.info(
            "RETOMADA %s: %.0f tokens, entrada %.10f SOL. Stop y take profit "
            "vuelven a estar activos.", sym, cantidad, entrada,
        )
        try:
            from monitoring.notifier import notify

            notify(
                f"🔄 <b>RETOMADA</b> · {sym}\n"
                f"El bot vuelve a vigilarla tras el reinicio: stop loss y take "
                f"profit activos otra vez."
            )
        except Exception:  # noqa: BLE001, S110
            pass

        rehecho = TradeResult(
            success=True,
            platform=token_info.platform,
            amount=cantidad,
            price=entrada,
        )

        async def _vigilar() -> None:
            # VIGILAR NUNCA ESPERA SU TURNO. Antes esto era un `async with` sobre
            # el semaforo de posiciones, y ahi habia un agujero serio: el
            # semaforo cuenta cuantas posiciones se pueden ABRIR a la vez. Si al
            # reiniciar ya habia 5 monedas vigilandose, la sexta que se retomaba
            # se quedaba esperando un hueco -- sin stop loss y sin take profit --
            # hasta que alguna de las otras cerrara. Que puede ser nunca.
            #
            # El dinero de una posicion retomada YA esta gastado: el tope no
            # pinta nada aqui, lo unico que hace es dejar de mirar una moneda
            # que esta viva. Se coge el hueco si lo hay (para que el bot no
            # abra otra encima) y, si no lo hay, se vigila igual.
            hueco = not self._position_semaphore.locked()
            if hueco:
                await self._position_semaphore.acquire()
            else:
                logger.warning(
                    "%s se retoma SIN ocupar hueco (ya hay %s posiciones): "
                    "se vigila igual, el dinero ya esta dentro.",
                    sym, self.max_posiciones,
                )
            try:
                await self._monitor_tiered_exit(token_info, rehecho)
            finally:
                if hueco:
                    self._position_semaphore.release()

        asyncio.create_task(_vigilar())  # noqa: RUF006

    def _check_orphan_position(self) -> None:
        """C2: on startup, alert if a previous run crashed with a position open.

        We do NOT auto-sell (reconstructing the exact sell params blindly is
        riskier than a manual sell): instead we warn loudly + notify Telegram
        with a link, and block re-buying that mint. Then clear the marker so the
        alert fires only once.
        """
        try:
            paths = self._open_position_paths()
            if not paths:
                return
        except Exception:
            self._clear_open_position()
            return
        for path in paths:
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            mint = str(rec.get("mint", "")).strip()
            sym = rec.get("symbol", "?") or "?"
            logger.error(
                "POSIBLE BOLSA HUÉRFANA de una sesión anterior: %s (%s). "
                "El bot NO la vende sola; revísala/véndela a mano.",
                sym,
                mint,
            )
            if mint:
                self.bought_mints.add(mint)  # nunca la recompres
                try:
                    from monitoring.notifier import notify

                    notify(
                        f"⚠️ <b>BOLSA HUÉRFANA</b> (crash previo)\n"
                        f"<b>{sym}</b> — véndela a mano:\n"
                        f'📊 <a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
                        f'🤖 <a href="https://t.me/MaestroSniperBot?start={mint}">Maestro</a>\n'
                        f"<code>{mint}</code>"
                    )
                except Exception:  # noqa: BLE001
                    pass
        self._clear_open_position()

    async def _dev_holds_too_much(self, token_info: TokenInfo) -> bool:
        """True si NO se debe comprar porque el CREADOR aún tiene demasiado.

        Es el filtro anti dump-inicial. Los demás miran la concentración de
        holders anónimos; éste mira la única cartera que sabe de antemano
        cuándo va a vender. Un dev que se queda un 20% del supply tiene, por
        construcción, un botón para hundirte el precio.

        Se consulta el saldo real del creador para ESTE mint
        (``getTokenAccountsByOwner``), no su histórico: da igual lo bien que se
        haya portado antes si ahora mismo está cargado.

        Fail-OPEN: si el RPC falla, deja pasar. Un filtro de reputación puede
        permitirse ser estricto ante la duda, pero éste corre en el camino
        crítico de la compra y bloquearlo todo por un timeout convertiría una
        red intermitente en "el bot no compra nada".
        """
        if self.max_dev_hold_pct <= 0 and self.min_dev_hold_pct <= 0:
            return False
        # El MINIMO falla CERRADO, al reves que el maximo. El maximo protege de
        # un dump y ante la duda dejar pasar solo te expone; el minimo ES la
        # tesis de entrada ("solo devs con piel en el juego"), y dejar pasar sin
        # el dato equivale a comprar sin filtro ninguno.
        _fallo_min = self.min_dev_hold_pct > 0
        creator = getattr(token_info, "creator", None)
        if not creator:
            if _fallo_min:
                logger.info(
                    f"{token_info.symbol}: sin creador en el evento, no puedo "
                    f"medir lo que tiene el dev -> se omite"
                )
            return _fallo_min
        try:
            import aiohttp  # noqa: PLC0415 - perezoso, igual que en _top_holder_exceeds

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    str(creator),
                    {"mint": str(token_info.mint)},
                    {"encoding": "jsonParsed", "commitment": "processed"},
                ],
            }
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout) as s, s.post(
                self.solana_client.rpc_endpoint, json=payload
            ) as r:
                data = await r.json()

            accounts = ((data or {}).get("result") or {}).get("value") or []
            held = 0.0
            for acc in accounts:
                info = (
                    ((acc.get("account") or {}).get("data") or {}).get("parsed") or {}
                ).get("info") or {}
                held += float((info.get("tokenAmount") or {}).get("uiAmount") or 0)

            supply = float(getattr(token_info, "token_total_supply", 0) or 0)
            if supply <= 0:
                # El evento no siempre trae el supply, pero en pump.fun es
                # SIEMPRE 1.000.000.000 -- la misma constante que se usa para
                # calcular el market cap en todo el fichero. Sin esta reserva,
                # el filtro descartaba monedas por no poder medirlas, que no es
                # lo mismo que medirlas y que no cumplan.
                supply = float(_PUMP_TOTAL_SUPPLY)
                logger.debug(
                    "%s: sin supply en el evento, uso el de pump.fun (%s)",
                    token_info.symbol, supply,
                )
            share = held / supply
            if self.min_dev_hold_pct > 0 and share < self.min_dev_hold_pct:
                logger.info(
                    f"{token_info.symbol}: el DEV solo tiene {share * 100:.1f}% "
                    f"(< {self.min_dev_hold_pct * 100:.0f}%) -> sin piel en el "
                    f"juego, se omite"
                )
                return True
            if self.max_dev_hold_pct > 0 and share > self.max_dev_hold_pct:
                logger.info(
                    f"{token_info.symbol}: el DEV tiene {share * 100:.1f}% del supply "
                    f"(> {self.max_dev_hold_pct * 100:.0f}%) -> riesgo de dump, se omite"
                )
                return True
            logger.info(f"DEV HOLD OK {token_info.symbol}: el creador tiene {share * 100:.1f}%")
        except Exception as exc:  # noqa: BLE001 - fail-open, ver docstring
            logger.debug(f"No pude mirar lo que tiene el dev de {token_info.symbol}: {exc}")
            if _fallo_min:
                logger.info(
                    f"{token_info.symbol}: fallo al consultar lo que tiene el "
                    f"dev ({exc}) -> se omite"
                )
            return _fallo_min
        return False

    async def _top_holder_exceeds(self, token_info: TokenInfo) -> bool:
        """True si NO se debe comprar por motivos de holders:
        - un holder (excluida la curva) tiene > ``max_holder_pct`` del supply
          (bundler/dev que puede dumpear), o
        - hay menos de ``min_holders`` poseedores distintos (token NO orgánico:
          bundle/dev-spam sin compras reales de gente).
        Usa getTokenLargestAccounts. Fail-open: ante error/RPC devuelve False
        para no frenar el trading.
        """
        # TOPE POR MONEDA. Una de fuera de pump.fun trae el suyo en
        # `additional_data`, mas estricto: alli el supply no es el fijo de
        # pump.fun y no hay curva ni creador que mirar, asi que un titular
        # gordo pesa mas que en una de casa.
        _tope_holder = float(
            (token_info.additional_data or {}).get("max_holder_pct")
            or self.max_holder_pct
        )
        # TOPE DE BUNDLE POR MONEDA. Una que entra por avance de curva trae el
        # suyo: a esas alturas la curva ya solto casi todo el supply, asi que
        # los mayores titulares concentran mucho mas que en una recien nacida y
        # el tope general las tumbaria todas.
        _tope_bundle = float(
            (token_info.additional_data or {}).get("max_bundle_pct")
            or self.max_bundle_pct
        )
        if (
            _tope_holder <= 0
            and self.min_holders <= 0
            and _tope_bundle <= 0
            and self.min_supply_en_manos <= 0
            and not self.require_zero_initial_holders
        ):
            return False
        try:
            import aiohttp

            mint = str(token_info.mint)
            # EN UNA MIGRADA LA CURVA YA NO GUARDA NADA. Los tokens estan en
            # el pool de PumpSwap/Raydium, que es OTRA direccion. Excluyendo
            # solo la curva (vacia), el pool contaba como un titular normal
            # con medio supply en la mano y la moneda se descartaba SIEMPRE
            # por concentracion. Eso tiraba todas las de pumpswap que pagan
            # DexScreener, que son justo la mitad de la senal.
            #
            # Poniendo curve_ata a None entra el camino de abajo, que excluye
            # al mayor titular -- y en una migrada el mayor es el pool.
            _migrada = bool((token_info.additional_data or {}).get("migrated"))
            curve_ata = (
                str(token_info.associated_bonding_curve)
                if token_info.associated_bonding_curve and not _migrada
                else None
            )
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [mint, {"commitment": "processed"}],
            }
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(
                    self.solana_client.rpc_endpoint, json=payload
                ) as r:
                    data = await r.json()
            accts = ((data or {}).get("result") or {}).get("value") or []
            if not accts:
                return self.require_zero_initial_holders
            supply = float(getattr(token_info, "token_total_supply", 0) or 0)
            if supply <= 0:
                supply = sum(float(a.get("amount") or 0) for a in accts)
            if supply <= 0:
                return False
            largest_addr = accts[0].get("address")

            real = [a for a in accts if float(a.get("amount") or 0) > 0]
            if curve_ata:
                holders = sum(1 for a in real if a.get("address") != curve_ata)
            else:
                holders = max(0, len(real) - 1)
            if self.require_zero_initial_holders and holders != 0:
                logger.info(
                    f"{token_info.symbol}: ya tiene {holders} holders externos "
                    "(se exigen 0) -> se omite"
                )
                return True

            # CUANTO SE HA COMPRADO DE VERDAD. Todo lo que no esta en la curva
            # lo tiene alguien que lo pago. Si la curva sigue teniendo casi todo,
            # el precio subio sin que nadie comprara y no hay a quien vendérselo.
            if self.min_supply_en_manos > 0 and curve_ata:
                _en_curva = sum(
                    float(a.get("amount") or 0)
                    for a in accts
                    if a.get("address") == curve_ata
                )
                _fuera = (supply - _en_curva) / supply if supply > 0 else 0.0
                if _fuera < self.min_supply_en_manos:
                    logger.info(
                        f"Evitado {token_info.symbol}: solo el {_fuera * 100:.1f}% "
                        f"del supply esta en manos de alguien (se exige "
                        f"{self.min_supply_en_manos * 100:.0f}%) — el precio subio "
                        f"sin compradores"
                    )
                    return True

            # Filtro ORGÁNICO: cuenta poseedores reales (con saldo>0, excluida la
            # curva). Pocos holders = bundle/dev-spam, no compra real de gente.
            if self.min_holders > 0:
                if holders < self.min_holders:
                    logger.info(
                        f"{token_info.symbol}: solo {holders} holders "
                        f"(< {self.min_holders}) -> NO orgánico, se omite"
                    )
                    return True

            # BUNDLE %: suma de los top holders EXCLUIDA la curva. Si concentran
            # mas de max_bundle_pct del supply -> bundle -> se omite (regla Manu).
            if _tope_bundle > 0:
                bundle_sum = 0.0
                for a in accts:
                    addr = a.get("address")
                    if curve_ata and addr == curve_ata:
                        continue
                    if not curve_ata and addr == largest_addr:
                        continue
                    bundle_sum += float(a.get("amount") or 0) / supply
                if bundle_sum > _tope_bundle:
                    logger.info(
                        f"{token_info.symbol}: bundle {bundle_sum * 100:.0f}% "
                        f"(top holders) > {_tope_bundle * 100:.0f}% -> se omite"
                    )
                    return True

            for a in accts:
                addr = a.get("address")
                # Excluir la curva: por dirección si la conocemos; si no, el
                # mayor holder (en un token fresco la curva es el mayor reserva).
                if curve_ata and addr == curve_ata:
                    continue
                if not curve_ata and addr == largest_addr:
                    continue
                pct = float(a.get("amount") or 0) / supply
                if pct > _tope_holder:
                    # Excepción: un holder gordo temprano suele ser pump&dump...
                    # PERO si es un dev PROBADO cuya MEDIANA histórica de pico es
                    # >= holder_bypass_ratio (x3) veces el market cap ACTUAL, hay
                    # recorrido de sobra (esa moneda suele subir mucho más) -> se
                    # permite comprar. Ej: MC ahora $6K, mediana dev $20K (x3.3).
                    if self.holder_bypass_ratio > 0 and self._creator_medians:
                        try:
                            dev = str(
                                getattr(token_info, "user", "")
                                or getattr(token_info, "creator", "")
                                or ""
                            ).strip()
                            dev_median = float(self._creator_medians.get(dev, 0) or 0)
                            if dev_median > 0:
                                st = await self.platform_implementations.curve_manager.get_pool_state(
                                    token_info.bonding_curve, commitment="processed"
                                )
                                price = float(st.get("price_per_token") or 0)
                                cur_mc = price * _PUMP_TOTAL_SUPPLY * _SOL_USD
                                if cur_mc > 0 and dev_median >= (
                                    self.holder_bypass_ratio * cur_mc
                                ):
                                    logger.info(
                                        f"{token_info.symbol}: holder {pct * 100:.0f}% "
                                        f"pero mediana dev ${dev_median:,.0f} >= "
                                        f"{self.holder_bypass_ratio:.0f}x MC actual "
                                        f"${cur_mc:,.0f} (recorrido) -> se permite"
                                    )
                                    return False
                        except Exception:  # noqa: BLE001
                            pass
                    logger.info(
                        f"{token_info.symbol}: holder con {pct * 100:.1f}% del "
                        f"supply (> {_tope_holder * 100:.0f}%) y sin recorrido "
                        f"por mediana -> se omite"
                    )
                    return True
            return False
        except Exception:  # noqa: BLE001
            # Para una entrada que exige exactamente cero, no saberlo debe
            # bloquear la compra; el resto de estrategias conserva fail-open.
            return self.require_zero_initial_holders

    async def _holders_gmgn(self, mint: str) -> int:
        """Titulares según GMGN, o -1 si no se puede saber.

        Se devuelve -1 (y NO 0) cuando la consulta falla, para que quien llame
        distinga "no hay gente" de "no me he podido enterar". Un fallo de red no
        debe leerse como moneda vacía ni, al revés, abrir la puerta a comprar
        sin comprobar.
        """
        import shutil

        exe = shutil.which("gmgn-cli") or shutil.which("gmgn-cli.cmd")
        if not exe:
            return -1

        def _consulta() -> int:
            try:
                import json as _json
                import subprocess  # noqa: PLC0415

                r = subprocess.run(  # noqa: S603
                    [exe, "token", "info", "--chain", "sol",
                     "--address", mint, "--raw"],
                    capture_output=True, text=True, timeout=20,
                    encoding="utf-8", errors="replace",
                )
                if r.returncode != 0:
                    return -1
                return int(_json.loads(r.stdout).get("holder_count") or 0)
            except Exception:  # noqa: BLE001
                return -1

        try:
            return await asyncio.wait_for(asyncio.to_thread(_consulta), timeout=25)
        except Exception:  # noqa: BLE001
            return -1

    async def _vigilar_los_descartes(self) -> None:
        """Manda el parte de descartes cada tanto, haya o no posiciones.

        VA EN SU PROPIA TAREA A PROPOSITO. La primera version lo colgue del
        bucle que vigila las posiciones abiertas, y eso era exactamente al
        reves: ese bucle SOLO corre cuando hay algo comprado, asi que el aviso
        de "no compra nada" no habria salido justo en el unico caso para el
        que existe.
        """
        while True:
            await asyncio.sleep(min(30.0, max(5.0, self.resumen_descartes_seg / 4)))
            try:
                self._avisar_de_los_descartes_si_toca()
            except Exception:  # noqa: BLE001
                logger.debug("parte de descartes", exc_info=True)

    async def _dev_que_mintea(self, token_info: TokenInfo) -> str | None:
        """El dev al que se le mira el historial: LA CARTERA QUE MINTEO.

        6-nov, "El dev es el q mint". Normalmente es quien firmo la creacion
        (`identidad_del_dev`). Si la moneda la creo una plataforma, eso es una
        cuenta de programa, no una cartera; entonces el dev es la cartera que
        pago y firmo la transaccion de creacion (`pumpfun_dev.dev_que_mintea`).
        None si no se puede saber: no se compra.
        """
        from monitoring.pumpfun_dev import dev_que_mintea, es_cartera  # noqa: PLC0415

        dev = identidad_del_dev(token_info)
        if dev and es_cartera(dev):
            return dev
        minteo = await dev_que_mintea(str(token_info.mint))
        if minteo:
            logger.info(
                "DEV de %s: la creo una plataforma; se mira la cartera que la "
                "minteo (%s)", token_info.symbol, minteo[:8],
            )
        return minteo

    def _demasiado_vieja_para_comprar(self, token_info: TokenInfo) -> bool:
        """True si la moneda ya pasa de `curva_96.max_edad_seg` al ir a comprar.

        La edad sale de la marca de tiempo de la CADENA de su creacion
        (monitoring/volumen.py, `edad`), no de cuando se entero el bot. Sin
        edad conocida (no se vio nacer) tampoco se compra: no se puede afirmar
        que sea nueva. Con `max_edad_seg` a 0 no hace nada. Deja escrita la
        edad con la que se compra, para poder comprobarlo en el log.
        """
        maximo = float((getattr(self, "curva_96", None) or {}).get("max_edad_seg") or 0)
        if maximo <= 0:
            return False
        # Desde el nacimiento REAL (5-nov): ver `volumen.edad_real`.
        from monitoring.volumen import edad_real  # noqa: PLC0415

        vida = edad_real(str(token_info.mint))
        if vida is None:
            self._descarta(token_info, "edad desconocida al ir a comprar (no se vio nacer)")
            return True
        if vida > maximo:
            self._descarta(
                token_info,
                f"ya tiene {vida:.1f} s al ir a comprar (máx {maximo:.0f} s)",
            )
            return True
        logger.info(
            "EDAD AL COMPRAR %s: %.1f s (máx %.0f s)", token_info.symbol, vida, maximo
        )
        return False

    def _descarta(self, token_info: TokenInfo, motivo: str) -> None:
        """Apunta POR QUE no se compro esta moneda, y lo cuenta.

        POR QUE HACE FALTA CONTARLOS. El bot ya escribia cada descarte en el
        log, pero ese log vive en un droplet que se administra desde un movil:
        los motivos estan mezclados entre miles de lineas y en la practica
        nadie los lee nunca. Dos dias enteros diagnosticando a ciegas por eso,
        con cada hipotesis costando un viaje de ida y vuelta.

        El motivo que llega aqui va SIN el simbolo ni los numeros de la moneda
        --"un titular concentra demasiado", no "PEPE: el titular X tiene el
        34,2%"-- porque si no cada descarte seria unico y el recuento saldria
        todo a uno, que no dice nada. El detalle concreto sigue yendo al log.
        """
        self._descartes[motivo] = self._descartes.get(motivo, 0) + 1
        logger.info("Evitado %s: %s", token_info.symbol, motivo)

    def _resumen_de_descartes(self) -> str | None:
        """El parte de por que no se compra, o None si no hay nada que contar.

        Devuelve None cuando NO hubo descartes, y quien llama tampoco lo manda
        si hubo compras: un aviso que sale siempre deja de leerse a la segunda
        semana, y entonces no sirve para el dia que de verdad pasa algo.
        """
        if not self._descartes:
            return None
        total = sum(self._descartes.values())
        lineas = [
            f"🔎 <b>POR QUÉ NO COMPRA</b> (últimos {self.resumen_descartes_seg / 60:.0f} min)",
            f"{total} candidatas · {self._compras_del_resumen} compradas",
            "",
        ]
        # De mas frecuente a menos: el motivo gordo es el que hay que mirar, y
        # tiene que estar el primero aunque el mensaje se corte en el movil.
        for motivo, veces in sorted(
            self._descartes.items(), key=lambda x: -x[1]
        )[:6]:
            lineas.append(f"<b>{veces}</b> · {motivo}")
        return "\n".join(lineas)

    def _avisar_de_los_descartes_si_toca(self) -> None:
        """Manda el parte cada `resumen_descartes_seg`, y solo si hace falta."""
        if self.resumen_descartes_seg <= 0:
            return
        ahora = monotonic()
        if ahora - self._ultimo_resumen < self.resumen_descartes_seg:
            return
        self._ultimo_resumen = ahora
        # SI ESTA COMPRANDO, NO SE AVISA DE NADA. Descartar monedas es lo que
        # hace un filtro; solo es un problema cuando NINGUNA pasa.
        if self._compras_del_resumen == 0:
            parte = self._resumen_de_descartes()
            if parte:
                try:
                    # Perezoso como las demas llamadas a notify de este
                    # fichero, para no arrastrar Telegram en el arranque.
                    from monitoring.notifier import notify  # noqa: PLC0415

                    notify(parte)
                except Exception:  # noqa: BLE001, S110
                    pass
        self._descartes.clear()
        self._compras_del_resumen = 0

    async def _pasa_los_frenos_de_rug(self, token_info: TokenInfo) -> bool:
        """False si la moneda cae por uno de los frenos comunes contra rugs.

        Son dos, y los dos se pidieron el 23-sep para la estrategia de
        titulares. Al cambiar de estrategia se quedaron alli y dejaron de
        aplicarse; ahora valen para TODAS las fuentes, incluida Telegram, que
        hasta hoy no miraba absolutamente nada.

        NUNCA LEVANTA. Un fallo aqui no puede impedir una compra buena ni
        dejar pasar una mala en silencio: ante la duda se compra, que es el
        comportamiento de antes, y queda escrito en el log.
        """
        mint = str(token_info.mint)

        # 1. DETRAS DE UNA VELA VERDE ENORME no se entra. Lo pidio Manu el
        #    23-sep: "evita comprar cuando el grafico de un segundo es una
        #    vela verde enorme". Cuando una moneda acaba de doblar en
        #    segundos, la presion compradora parece inmejorable justo en el
        #    peor momento para entrar -- eso ya lo ha pasado, y lo que queda
        #    por delante es la vuelta.
        if self.max_subida_global > 0:
            try:
                _sub = volumen.subida(mint, self.subida_seg_global)
            except Exception:  # noqa: BLE001
                _sub = 0.0
            if _sub > self.max_subida_global:
                logger.info(
                    "Evitado %s: +%.0f%% en %.0fs, se compraria detras de una "
                    "vela verde enorme",
                    token_info.symbol, _sub * 100, self.subida_seg_global,
                )
                self._descarta(token_info, "detrás de una vela verde")
                return False

        # 2. POCA GENTE DISTINTA. Una moneda que ha movido su curva con cuatro
        #    carteras no tiene demanda: tiene un grupo empujando para
        #    soltartela. La curva ya lo miraba por su cuenta; Telegram no.
        if self.min_compradores_global > 0:
            try:
                # La misma cuenta que usa la curva (`_poca_gente`), para que
                # las dos puertas midan LO MISMO. Dos formas de contar
                # compradores daria dos veredictos distintos sobre la misma
                # moneda segun por donde entrara, que es justo el lio que se
                # esta arreglando.
                _gente = int(
                    volumen.metricas_segunda_ola(mint).get("compradores", 0) or 0
                )
            except Exception:  # noqa: BLE001
                _gente = -1
            # -1 = no se pudo contar. NO se descarta por eso: quedarse sin
            # dato no es lo mismo que tener un dato malo, y bloquear a ciegas
            # apagaria el bot entero el dia que falle el contador.
            if 0 <= _gente < self.min_compradores_global:
                logger.info(
                    "Evitado %s: solo %d compradores distintos (hacen falta %d)",
                    token_info.symbol, _gente, self.min_compradores_global,
                )
                self._descarta(token_info, "pocos compradores distintos")
                return False

        return True

    async def _handle_token(self, token_info: TokenInfo) -> None:
        """Handle a new token creation event."""
        try:
            signal_data = token_info.additional_data or {}
            if (
                self.telegram_only
                and signal_data.get("acquisition_kind") != "telegram"
            ):
                return

            # Validate that token is for our platform
            if token_info.platform != self.platform:
                logger.warning(
                    f"Token platform mismatch: expected {self.platform.value}, got {token_info.platform.value}"
                )
                return

            # Never buy a coin we've already bought (persistent across restarts).
            if str(token_info.mint) in self.bought_mints:
                logger.info(
                    f"Evitado {token_info.symbol}: ya comprada antes (no repetir)"
                )
                return

            # ---- FRENOS CONTRA RUGS, PARA TODAS LAS FUENTES -----------------
            #
            # POR QUE ESTAN AQUI Y NO EN CADA FUENTE, que es el fallo que
            # arreglan. `_handle_token` es la unica puerta por la que pasan
            # TODAS las compras; cada fuente tiene ademas sus propios filtros.
            # Cuando el 23-sep la estrategia paso de titulares a la curva, dos
            # protecciones que se habian pedido expresamente se quedaron en el
            # bloque de titulares, que quedo apagado -- y la curva y Telegram
            # empezaron a comprar sin ellas sin que nada lo dijera.
            #
            # LO QUE COSTO: FUCKETHAN, el 24-sep a las 05:18. Cinco titulares,
            # comprada en la punta de una vela vertical de 20k a 43k de market
            # cap, cerrada a -91,9% cuarenta segundos despues. Se llevo el 60%
            # de la cartera. Era exactamente lo que estos dos frenos existen
            # para evitar, y estaban puestos... en la estrategia apagada.
            #
            # La regla que sale de ahi: un freno contra rugs no puede vivir en
            # una fuente. Las fuentes deciden QUE mirar; esto decide que no se
            # compra pase lo que pase.
            if not await self._pasa_los_frenos_de_rug(token_info):
                return

            _dev_user = str(token_info.user)
            _dev_creator = str(getattr(token_info, "creator", "") or "")

            # Las dos listas de abajo bloquean a un creador sin mirar su tasa de
            # migracion. Con `ignorar_listas_de_devs` se saltan las dos y decide
            # solo el filtro de reputacion en vivo.
            if not self.ignorar_listas_de_devs:
                # QUIEN ES EL DEV, TAMBIEN AQUI (6-nov): la cartera que minteo,
                # aunque la moneda la lanzara una plataforma ("El dev es el q
                # mint"). Sin esto, un dev que rugea a traves de una plataforma
                # no quedaba apuntado, y la cuenta de la plataforma (comun a
                # todas sus monedas) acababa vetando a todos los que la usan.
                _minteo = (
                    await self._dev_que_mintea(token_info)
                    if self.pumpfun_dev.get("enabled")
                    else None
                )
                _ids = self._identidades_del_dev(token_info, _minteo)
                # Blacklist DURA manual: dev vetado a mano -> nunca se compra.
                if (
                    _dev_user in self._manual_blacklist
                    or _dev_creator in self._manual_blacklist
                    or bool(_ids & self._manual_blacklist)
                ):
                    self._descarta(token_info, "dev en BLACKLIST manual")
                    return

                # Auto-baneo: dev que alcanzo el umbral configurado -> no se compra.
                # Los marcados se lo saltan: si Manu pide expresamente snipear a
                # uno, esa decision manda sobre el contador automatico. La
                # BLACKLIST MANUAL de arriba NO se salta -- esa es una decision
                # explicita en sentido contrario y tiene que pesar mas.
                if self._is_dev_blocked(*_ids) and not (
                    self.marcados_saltan_baneo and self._es_marcado(token_info)
                ):
                    self._descarta(
                        token_info,
                        f"dev bloqueado por {self.rugs_para_bloquear} rug(s)",
                    )
                    return

            # Avoid pump.fun "mayhem mode" tokens unless this strategy allows them.
            _es_mayhem = bool(getattr(token_info, "is_mayhem_mode", False))
            if not self.allow_mayhem and _es_mayhem:
                self._descarta(token_info, "mayhem mode (allow_mayhem: false)")
                return

            if not self._dentro_del_horario():
                logger.info(
                    f"Evitado {token_info.symbol}: fuera de la franja horaria "
                    f"({self.horario.get('inicio')}:00-{self.horario.get('fin')}:00 "
                    f"hora de España)"
                )
                return

            # CORTA-CIRCUITOS DE LA ESTRATEGIA ENTERA. Va el primero de todos:
            # si esta parada, no tiene sentido gastar ni una comprobacion mas.
            if self._parado_por_perdidas:
                logger.info(
                    f"Evitado {token_info.symbol}: estrategia PARADA "
                    f"({self.max_perdidas_seguidas} perdidas seguidas)"
                )
                return

            # Corta-circuitos de las mayhem: tras N perdidas seguidas se deja de
            # comprarlas hasta el proximo arranque. Las normales no se tocan.
            if _es_mayhem and self._mayhem_cerrado:
                logger.info(
                    f"Evitado {token_info.symbol}: mayhem CERRADAS "
                    f"({self.mayhem_max_perdidas} perdidas seguidas)"
                )
                return

            # NOMBRE REPETIDO: se comprueba AQUÍ, al detectarla, no después de
            # la espera. Si se hiciera después, dos clones lanzados con pocos
            # segundos de diferencia terminarían la espera casi a la vez y los
            # dos se darían por buenos.
            if self.no_repeat_name_min > 0:
                nombre = str(token_info.symbol or "").strip().upper()
                if nombre:
                    ahora_n = monotonic()
                    corte = ahora_n - self.no_repeat_name_min * 60
                    self._nombres_vistos = {
                        k: v for k, v in self._nombres_vistos.items() if v >= corte
                    }
                    repetida = nombre in self._nombres_vistos
                    self._nombres_vistos[nombre] = ahora_n
                    if repetida:
                        logger.info(
                            f"Evitado {token_info.symbol}: ya salió una moneda "
                            f"con ese nombre en los últimos "
                            f"{self.no_repeat_name_min:.0f} min"
                        )
                        return

            # Wait for pool/curve to stabilize (unless in extreme fast mode)
            if not self.extreme_fast_mode:
                await self._save_token_info(token_info)
                logger.info(
                    f"Waiting for {self.wait_time_after_creation} seconds for the pool/curve to stabilize..."
                )
                await asyncio.sleep(self.wait_time_after_creation)

            # VIGILANCIA CONTINUA hasta que la moneda cumpla, en vez de mirarla
            # una sola vez y descartarla.
            #
            # Antes el bot echaba un vistazo al terminar la espera: si en ese
            # instante concreto no cumplia (aun no habia movido el volumen, o
            # el market cap todavia no habia llegado al suelo), la tiraba para
            # siempre. Una moneda que cumplia al segundo 8 se perdia por haber
            # mirado en el 5.
            #
            # Ahora se queda vigilando hasta `max_buy_age` y compra en el
            # momento exacto en que cumple. El coste de red esta controlado: el
            # volumen se lee de memoria (gratis) y la curva SOLO se consulta
            # cuando el volumen ya da el minimo, asi que no se gastan llamadas
            # en monedas que ni siquiera se han movido.
            # REDES SOCIALES. La descarga del metadata se lanza AQUI, antes de
            # ponerse a vigilar volumen y titulares, y no se recoge hasta que
            # todo lo demas ha pasado. Asi los 200-400 ms que tarda transcurren
            # mientras el bot esta esperando de todas formas: cuando llega el
            # momento de comprar el dato ya esta en la mano y no retrasa nada.
            _tarea_redes = None
            if self.redes_originales:
                from monitoring import redes as _redes

                _tarea_redes = asyncio.create_task(
                    _redes.leer(token_info.uri, self.redes_timeout)
                )

            # COMPRA GRANDE DEL PROPIO CREADOR (dev-buy). Independiente del
            # historial de `pumpfun_dev` de mas abajo -- esto no mira cuantas
            # veces ha migrado, mira si SE HA JUGADO dinero de verdad en ESTE
            # lanzamiento. Pedido 26-ago tras ver Frug: el creador metio 6,01
            # SOL en la misma transaccion de creacion, y en ese mismo segundo
            # entraron ya 4 compradores reales mas.
            #
            # SALE GRATIS, SIN LLAMADA NINGUNA. Si la creacion vino con un
            # buy en la misma transaccion, `registrar_logs` ya lo conto en
            # `volumen._ops` ANTES de que el parser devolviera este
            # TokenInfo -- las dos funciones leen los MISMOS logs de la MISMA
            # notificacion del websocket (universal_logs_listener.py:214-231).
            if self.dev_buy.get("min_sol"):
                from monitoring.volumen import compra_creador_sol

                _creador_db = str(
                    getattr(token_info, "creator", None)
                    or getattr(token_info, "user", None)
                    or ""
                )
                _compra_dev = compra_creador_sol(str(token_info.mint), _creador_db)
                _min_dev_buy = float(self.dev_buy["min_sol"])
                if _compra_dev < _min_dev_buy:
                    logger.info(
                        f"Evitado {token_info.symbol}: dev-buy de "
                        f"{_compra_dev:.3f} SOL < minimo {_min_dev_buy:.2f}"
                    )
                    return
                logger.info(
                    f"{token_info.symbol}: DEV-BUY DE {_compra_dev:.3f} SOL "
                    f"-> compra inmediata, sin esperar confirmacion"
                )

            # EL HISTORIAL DEL CREADOR, LO PRIMERO DE TODO.
            #
            # Con `compra_inmediata` la decision se toma AQUI, nada mas ver la
            # moneda, y no al final. Dos motivos:
            #
            # 1. LO QUE NO PASA EL FILTRO SE CAE YA. Exigiendo >=20% se cae el
            #    99,7% del flujo (13 de 5.167 evaluaciones). Antes ese 99,7%
            #    se llevaba por delante 3 segundos de vigilancia y una lectura
            #    de curva por RPC cada una. En un servidor de 1 vCPU ese
            #    trabajo tirado es latencia real para la que SI cumple.
            # 2. LA QUE PASA SE COMPRA SIN ESPERAR CONFIRMACION. La apuesta es
            #    el historial de quien lanza, no que el mercado lo confirme:
            #    cuando ya han entrado 6 carteras, el que compro primero lleva
            #    ventaja. Quitar esa espera es el segundo entre comprar de
            #    primero o de decimo -- y es gratis, no cuesta comision.
            #
            # El suelo de latencia es la llamada a la API de pump.fun: 200-400
            # ms la primera vez por creador y 0 los siguientes 10 minutos. No
            # se puede bajar sin renunciar a saber quien lanza.
            _dev_ya = None
            if self.pumpfun_dev.get("enabled"):
                _dev0 = await self._dev_que_mintea(token_info)
                if _dev0:
                    from monitoring.pumpfun_dev import creator_migration_stats

                    if self.pumpfun_dev.get("compra_inmediata") and (
                        self.pumpfun_dev.get("strict_only", False)
                        or not self._es_marcado(token_info)
                    ):
                        _tope_api = float(
                            self.pumpfun_dev.get("timeout_seg", 2.0) or 2.0
                        )
                        try:
                            _l, _m, _r = await asyncio.wait_for(
                                creator_migration_stats(
                                    str(_dev0), actual=str(token_info.mint)
                                ),
                                timeout=_tope_api,
                            )
                        except (TimeoutError, asyncio.TimeoutError):
                            # Sin el dato NO se compra. Comprar a ciegas aqui
                            # es comprar cualquier moneda, que es justo lo que
                            # este filtro existe para evitar.
                            logger.info(
                                f"Evitado {token_info.symbol}: la API del creador "
                                f"no contesto en {_tope_api:.1f}s"
                            )
                            return
                        _min_m = int(self.pumpfun_dev.get("min_migrations", 2))
                        _min_r = float(self.pumpfun_dev.get("min_rate", 0.24))
                        _por_mediana = False
                        if _m < _min_m or _r < _min_r:
                            # SEGUNDA PUERTA POR MEDIANA. No llega al filtro
                            # normal, pero si la mediana de sus migradas ya
                            # supera min_mediana_mc, se compra igual -- luego,
                            # en el techo de entrada, este mint usa el max_mc
                            # de dev_mediana en vez del general.
                            _min_med = float(
                                self.dev_mediana.get("min_mediana_mc") or 0
                            )
                            if _min_med > 0 and not self.pumpfun_dev.get("strict_only", False):
                                from monitoring.pumpfun_dev import mediana_pico

                                _med = mediana_pico(
                                    str(_dev0),
                                    int(self.dev_mediana.get("min_monedas", 2)),
                                )
                                _min_lanz = int(
                                    self.dev_mediana.get("min_lanzamientos", 0) or 0
                                )
                                _por_mediana = (
                                    _med >= _min_med and _l >= _min_lanz
                                )
                            if not _por_mediana:
                                logger.info(
                                    f"DEV filtro BLOCK {token_info.symbol}: "
                                    f"{_m} migr / {_l} lanz ({_r * 100:.0f}%) "
                                    f"< {_min_m} y {int(_min_r * 100)}%"
                                )
                                return
                            if len(self._mints_mediana) > 5000:
                                self._mints_mediana.clear()
                            self._mints_mediana.add(str(token_info.mint))
                            logger.info(
                                f"DEV OK {token_info.symbol} por MEDIANA: "
                                f"${_med:,.0f}, {_l} lanzamientos "
                                f"(no llega al filtro normal: "
                                f"{_m} migr/{_l} lanz, {_r * 100:.0f}%) -> "
                                f"compra inmediata"
                            )
                        _dev_ya = (_m, _l, _r)
                        self._tasa_dev[str(token_info.mint)] = _r
                        if not _por_mediana:
                            logger.info(
                                f"DEV OK {token_info.symbol}: {_m} migraciones de "
                                f"{_l} lanzamientos ({_r * 100:.0f}%) -> compra "
                                f"inmediata, sin esperar confirmacion del mercado"
                            )
                        # DEV QUE SUELE PUMPEAR ALTO. Se decide AQUI, mientras
                        # el dato del creador esta recien traido, porque hacen
                        # falta las dos cosas antes de comprar: el tamano y la
                        # comision de prioridad. Solo cuentan los picos de
                        # monedas que MIGRARON -- ver el comentario de `_picos`
                        # en monitoring/pumpfun_dev.py.
                        if self.dev_elite.get("min_pico_mc"):
                            from monitoring.pumpfun_dev import (
                                mejor_pico,
                                monedas_por_encima,
                            )

                            _minmc = float(self.dev_elite["min_pico_mc"])
                            _n_altas = monedas_por_encima(str(_dev0), _minmc)
                            if _n_altas >= int(
                                self.dev_elite.get("min_monedas_altas", 2)
                            ):
                                if len(self._mints_elite) > 5000:
                                    self._mints_elite.clear()
                                self._mints_elite.add(str(token_info.mint))
                                logger.info(
                                    f"DEV QUE PUMPEA {token_info.symbol}: "
                                    f"{_n_altas} monedas migradas por encima de "
                                    f"${_minmc:,.0f} (mejor: "
                                    f"${mejor_pico(str(_dev0)):,.0f}) -> "
                                    f"trato elite"
                                )
                    else:
                        # Sin compra inmediata basta con dejarla pedida: la
                        # comprobacion de mas abajo se engancha a esta misma
                        # llamada y no vuelve a pagar la latencia.
                        asyncio.ensure_future(
                            creator_migration_stats(str(_dev0), actual=str(token_info.mint))
                        )

            if self.max_buy_age > 0 and (
                self.min_volumen_sol > 0
                or self.min_entry_mc > 0
                or self.min_buys > 0
                or self.min_first_block_buys > 0
                or self.max_first_block_buys > 0
                or self.min_first_block_usd > 0
                or self.max_first_block_usd > 0
                or self.buy_from_second_block
                or self.second_block_confirmation.get("enabled")
                or self.min_ops > 0
                or self.min_titulares > 0
                or self.second_wave.get("enabled")
            ):
                from monitoring.volumen import compras as _n_buys
                from monitoring.volumen import compras_primer_bloque as _n_first_buys
                from monitoring.volumen import (
                    en_bloque_posterior_al_inicial as _bloque_dos,
                )
                from monitoring.volumen import metricas_segunda_ola as _segunda_ola
                from monitoring.volumen import operaciones as _n_ops
                from monitoring.volumen import presion as _presion
                from monitoring.volumen import sol_primer_bloque as _sol_first_block
                from monitoring.volumen import metricas_segundo_bloque as _second_block
                from monitoring.volumen import titulares as _n_tit
                from monitoring.volumen import ultimas_son_compra as _ult_compra
                from monitoring.volumen import volumen_sol as _vol_sol

                _nacida = self.token_timestamps.get(str(token_info.mint))
                _t0 = _nacida if _nacida is not None else monotonic()
                _visto_vol = 0.0
                _visto_buys = _visto_first_buys = _visto_ops = _visto_tit = 0
                #: Ultima lectura de presion que NO paso, para el aviso de
                #: descarte: sin esto no se sabe si sobro el dinero o la
                #: direccion del dinero.
                _visto_presion = "no medida"
                #: Por que no se pudo leer la curva, si fallo (6-nov). Antes el
                #: fallo se reintentaba EN SILENCIO hasta agotar la ventana: si
                #: un dia la lectura fallara por otra cosa, el bot no compraria
                #: nada y el log no diria por que.
                _motivo_curva = ""
                # SIEMPRE 0,15 s. Antes se pasaba a 0,5 con ventanas de mas de
                # 6 segundos, y eso costaba velocidad justo donde importa: con
                # la ventana en 18 s se miraba 3 veces menos, asi que entre que
                # la moneda cumplia y el bot se enteraba podian pasar medio
                # segundo -- una eternidad en una moneda que hace x6 en segundo
                # y medio (caso DYOR: 2.097 -> 12.502 $).
                #
                # No cuesta RPC: en cada vuelta solo se miran contadores que ya
                # estan en memoria. La curva, que es la unica llamada de red, se
                # consulta solo cuando el volumen ya cumple.
                _paso = 0.15
                # Primera comprobacion inmediata: si la moneda ya cumple al
                # segundo 1, se compra al segundo 1. Solo se duerme cuando NO
                # cumple, nunca antes de mirar.
                # SIN VENTANA DE ESPERA NO HAY NADA QUE ESPERAR, y eso NO
                # puede significar "descarta todo".
                #
                # EL FALLO QUE ESTO ARREGLA, que tuvo el bot sin comprar una
                # sola moneda durante horas pareciendo que funcionaba: esto es
                # un `while ... else`, y en Python el `else` se ejecuta cuando
                # el bucle termina SIN `break`. Con `max_buy_age: 0` la
                # condicion es falsa desde el primer instante, el cuerpo no
                # corre NUNCA, el `break` de dentro no se alcanza jamas y el
                # `else` de abajo se dispara SIEMPRE -- con su `return`. Cada
                # moneda salia rechazada con "no cumplio en 0s" sin que se
                # hubiera comprobado absolutamente nada.
                #
                # En el resto de esta configuracion 0 significa "sin limite",
                # asi que aqui tiene que significar lo mismo: sin ventana, se
                # decide ya. Todos los filtros que viven dentro del bucle
                # (volumen minimo, compras, presion, titulares, market cap...)
                # se saltan igual que se saltaban antes, porque estan a cero:
                # si alguien enciende uno, tiene que poner tambien una ventana
                # en la que comprobarlo.
                if self.max_buy_age <= 0:
                    logger.debug(
                        "%s: sin ventana de espera (max_buy_age=0), se decide ya",
                        token_info.symbol,
                    )
                while monotonic() - _t0 < self.max_buy_age:
                    # Los tres se leen SIEMPRE, aunque el primero ya descarte:
                    # los tres salen del mismo diccionario en memoria y no
                    # cuestan nada, y si solo se midiera hasta el que falla, el
                    # aviso de descarte diria "0 tx" en todas y no habria forma
                    # de saber cual es el filtro que sobra.
                    _v = _vol_sol(str(token_info.mint))
                    _o = _n_ops(str(token_info.mint))
                    _b = _n_buys(str(token_info.mint))
                    _fb = _n_first_buys(str(token_info.mint))
                    _fb_usd = _sol_first_block(str(token_info.mint)) * _SOL_USD
                    _en_bloque_dos = _bloque_dos(str(token_info.mint))
                    _h = _n_tit(str(token_info.mint))
                    _visto_vol = max(_visto_vol, _v)
                    _visto_buys = max(_visto_buys, _b)
                    _visto_first_buys = max(_visto_first_buys, _fb)
                    _visto_ops = max(_visto_ops, _o)
                    _visto_tit = max(_visto_tit, _h)
                    if self.min_volumen_sol > 0 and _v < self.min_volumen_sol:
                        await asyncio.sleep(_paso)
                        continue
                    if self.min_buys > 0 and _b < self.min_buys:
                        await asyncio.sleep(_paso)
                        continue
                    # PRESION COMPRADORA. Se comprueba JUSTO despues del
                    # volumen y antes de mirar la curva: llegar al volumen
                    # pedido no dice nada de la direccion -- se llega igual
                    # subiendo que mientras todos venden. Aqui se exige que en
                    # los ultimos segundos entre mas dinero del que sale, para
                    # no comprar la primera vela roja de un dump.
                    if self.min_presion_ratio > 0:
                        _pc, _pv = _presion(str(token_info.mint), self.presion_seg)
                        if _pc <= 0 and _pv <= 0:
                            # SIN DATOS != CAYENDO. Medido el 10-ago: las 6
                            # monedas que llegaron a cruzar el volumen pedido lo
                            # hicieron con la ventana vacia (0,00 / 0,00) y se
                            # descartaron las seis. El volumen se acumula en el
                            # estallido inicial y luego la moneda se queda unos
                            # segundos quieta; justo cuando el contador cruza el
                            # umbral no hay nada reciente que medir. Tratar ese
                            # silencio como presion vendedora vetaba TODAS las
                            # candidatas por falta de dato, no por ir mal.
                            _ok_presion = True
                        elif _pv <= 0:
                            # Compras y ninguna venta: no hay division posible y
                            # la presion es inmejorable.
                            _ok_presion = True
                        else:
                            _ok_presion = (_pc / _pv) >= self.min_presion_ratio
                        if not _ok_presion:
                            _visto_presion = (
                                f"{_pc:.2f} comprados / {_pv:.2f} vendidos "
                                f"en {self.presion_seg:.0f}s"
                            )
                            await asyncio.sleep(_paso)
                            continue
                    # El bloque inicial solo queda cerrado cuando llega una
                    # operación de un slot posterior. Antes de eso sus compras
                    # y su volumen todavía pueden crecer, así que se espera.
                    if self.buy_from_second_block and not _en_bloque_dos:
                        await asyncio.sleep(_paso)
                        continue
                    if self.second_block_confirmation.get("enabled"):
                        _sb_cfg = self.second_block_confirmation
                        _sb = _second_block(str(token_info.mint))
                        if not _sb["observado"]:
                            await asyncio.sleep(_paso)
                            continue
                        if _sb["compradores"] < int(_sb_cfg.get("min_buyers", 0)):
                            await asyncio.sleep(_paso)
                            continue
                        if _sb["compra_sol"] < float(_sb_cfg.get("min_buy_sol", 0)):
                            await asyncio.sleep(_paso)
                            continue
                        if _sb["neto_sol"] < float(_sb_cfg.get("min_net_sol", 0)):
                            await asyncio.sleep(_paso)
                            continue
                        if _sb["ventas"] > int(_sb_cfg.get("max_sells", 999999)):
                            await asyncio.sleep(_paso)
                            continue
                    if (
                        self.max_first_block_buys > 0
                        and _fb > self.max_first_block_buys
                    ):
                        logger.info(
                            "Evitado %s: %d compras en el primer bloque (máx %d)",
                            token_info.symbol, _fb, self.max_first_block_buys,
                        )
                        return
                    if (
                        self.min_first_block_buys > 0
                        and _fb < self.min_first_block_buys
                    ):
                        if _en_bloque_dos:
                            logger.info(
                                "Evitado %s: %d compras en el primer bloque (mín %d)",
                                token_info.symbol, _fb, self.min_first_block_buys,
                            )
                            return
                        await asyncio.sleep(_paso)
                        continue
                    if (
                        self.max_first_block_usd > 0
                        and _fb_usd > self.max_first_block_usd
                    ):
                        logger.info(
                            "Evitado %s: $%.2f comprados en el primer bloque (máx $%.2f)",
                            token_info.symbol, _fb_usd, self.max_first_block_usd,
                        )
                        return
                    if (
                        self.min_first_block_usd > 0
                        and _fb_usd < self.min_first_block_usd
                    ):
                        if _en_bloque_dos:
                            logger.info(
                                "Evitado %s: $%.2f comprados en el primer bloque (mín $%.2f)",
                                token_info.symbol, _fb_usd, self.min_first_block_usd,
                            )
                            return
                        await asyncio.sleep(_paso)
                        continue
                    # BUNDLE EN EL NACIMIENTO. Se descarta entera, no se espera:
                    # la proporcion de compras hechas en el bloque de creacion
                    # solo puede empeorar con el tiempo si el resto no compra, y
                    # si mejora es porque ha entrado gente -- pero el bundle
                    # sigue ahi con su bolsa a precio cero.
                    if self.max_first_block_pct > 0 and _b > 0:
                        _pct_bundle = _fb / _b
                        if _pct_bundle > self.max_first_block_pct:
                            logger.info(
                                f"Evitado {token_info.symbol}: BUNDLE — "
                                f"{_fb} de {_b} compras ({_pct_bundle * 100:.0f}%) "
                                f"en el bloque de creacion, maximo "
                                f"{self.max_first_block_pct * 100:.0f}%"
                            )
                            return
                    # PARTICIPACION. El volumen solo dice cuanto dinero se ha
                    # movido; esto dice entre cuantas manos. Una moneda con el
                    # volumen hecho de una sola compra del creador cumple el
                    # filtro de dinero y no significa nada.
                    if self.min_ops > 0 and _o < self.min_ops:
                        await asyncio.sleep(_paso)
                        continue
                    if self.ultimas_tx_compra > 0 and not _ult_compra(
                        str(token_info.mint), self.ultimas_tx_compra
                    ):
                        # Se espera, no se descarta: basta con que lleguen unas
                        # cuantas compras seguidas para que empiece a cumplir.
                        await asyncio.sleep(_paso)
                        continue
                    if self.min_titulares > 0 and _h < self.min_titulares:
                        await asyncio.sleep(_paso)
                        continue
                    if (self.min_compras_por_cartera > 0 or
                            self.max_compras_por_cartera > 0) and _h > 0:
                        _ratio = _b / _h
                        if (self.min_compras_por_cartera > 0
                                and _ratio < self.min_compras_por_cartera):
                            # Este SI espera: el ratio sube segun repiten, asi
                            # que puede cumplir mas adelante dentro del plazo.
                            await asyncio.sleep(_paso)
                            continue
                        if (self.max_compras_por_cartera > 0
                                and _ratio > self.max_compras_por_cartera):
                            logger.info(
                                f"Evitado {token_info.symbol}: {_b} compras "
                                f"entre {_h} carteras = {_ratio:.1f} por mano "
                                f"(> {self.max_compras_por_cartera}) -> una "
                                f"sola mano inflando el contador"
                            )
                            return
                    if self.max_titulares > 0 and _h > self.max_titulares:
                        # Este NO espera: las carteras solo suben, asi que si ya
                        # se paso del techo no va a volver a bajar. Seguir dando
                        # vueltas seria gastar la ventana en una moneda perdida.
                        logger.info(
                            f"Evitado {token_info.symbol}: {_h} carteras "
                            f"distintas en {monotonic() - _t0:.1f}s "
                            f"(> {self.max_titulares}) -> granja repartida"
                        )
                        return
                    if self.second_wave.get("enabled"):
                        _sw = self.second_wave
                        _m = _segunda_ola(
                            str(token_info.mint),
                            ventana=float(_sw.get("window_seconds", 1.5)),
                            creador=str(
                                getattr(token_info, "creator", "")
                                or getattr(token_info, "user", "")
                                or ""
                            ),
                        )
                        if _m["edad"] < float(_sw.get("min_age_seconds", 5.0)):
                            await asyncio.sleep(_paso)
                            continue
                        if _m["neto_sol"] < float(_sw.get("min_net_buy_sol", 8.0)):
                            await asyncio.sleep(_paso)
                            continue
                        if _m["compras"] < int(_sw.get("min_buys", 20)):
                            await asyncio.sleep(_paso)
                            continue
                        if _m["compradores"] < int(_sw.get("min_buyers", 15)):
                            await asyncio.sleep(_paso)
                            continue
                        if _m["ola_compra_sol"] < float(
                            _sw.get("min_wave_buy_sol", 8.0)
                        ):
                            await asyncio.sleep(_paso)
                            continue
                        if _m["ola_neto_sol"] < float(
                            _sw.get("min_wave_net_sol", 5.0)
                        ):
                            await asyncio.sleep(_paso)
                            continue
                        if _m["ola_compradores"] < int(
                            _sw.get("min_wave_buyers", 5)
                        ):
                            await asyncio.sleep(_paso)
                            continue
                        if _m["mayor_compra_sol"] < float(
                            _sw.get("min_largest_buy_sol", 3.0)
                        ):
                            await asyncio.sleep(_paso)
                            continue
                    # Aceleracion: no basta con haber movido mucho en total,
                    # tiene que estar moviendose AHORA.
                    if self.min_vol_ventana_sol > 0:
                        _vr = _vol_sol(str(token_info.mint), self.vol_ventana_seg)
                        if _vr < self.min_vol_ventana_sol:
                            await asyncio.sleep(_paso)
                            continue
                    # El volumen ya cumple: ahora si vale la pena mirar la curva.
                    try:
                        _st = await self.platform_implementations.curve_manager.get_pool_state(
                            token_info.bonding_curve, commitment="processed"
                        )
                        _p = float(_st.get("price_per_token") or 0)
                    except Exception as _e_curva:  # noqa: BLE001
                        if not _motivo_curva:
                            # Solo el tipo y el texto corto, sin claves: un
                            # error de red podria llevar la URL del nodo.
                            _motivo_curva = re.sub(
                                r"(api[-_]?key=)[^&\s\"']+",
                                r"\1***",
                                f"{type(_e_curva).__name__}: {_e_curva}"[:120],
                                flags=re.IGNORECASE,
                            )
                            logger.info(
                                "Esperando %s: no se pudo leer su curva (%s); "
                                "se reintenta hasta agotar la ventana",
                                token_info.symbol,
                                _motivo_curva,
                            )
                        await asyncio.sleep(_paso)
                        continue
                    _mc = _p * _PUMP_TOTAL_SUPPLY * _SOL_USD

                    # ANTI-HUMO. Se aprovecha que la curva ya esta leida: no
                    # cuesta ni una llamada mas. Si el market cap no guarda
                    # proporcion con el SOL que hay DENTRO, ese precio no lo
                    # sostiene nadie y no se puede vender a el.
                    if self.max_mc_por_sol_curva > 0:
                        _sol_dentro = (
                            float(_st.get("real_sol_reserves") or 0) / LAMPORTS_PER_SOL
                        )
                        _ratio = _mc / _sol_dentro if _sol_dentro > 0 else float("inf")
                        if _ratio > self.max_mc_por_sol_curva:
                            logger.info(
                                f"Evitado {token_info.symbol}: HUMO — "
                                f"MC ${_mc:,.0f} con solo {_sol_dentro:.2f} SOL en "
                                f"la curva (${_ratio:,.0f} por SOL, maximo "
                                f"${self.max_mc_por_sol_curva:,.0f})"
                            )
                            return

                    if self.max_entry_mc > 0 and _mc > self.max_entry_mc:
                        # SEGUNDA PUERTA. Se paso de la banda del nacimiento,
                        # pero si sigue por debajo del techo alternativo Y ha
                        # movido volumen de verdad, se compra igual: ahi ya no
                        # se compra a ciegas, se compra algo que el mercado ha
                        # empezado a pagar.
                        _alt_ok = (
                            self.entrada_alt_max_mc > 0
                            and _mc <= self.entrada_alt_max_mc
                            and _v >= self.entrada_alt_min_vol
                        )
                        # TERCERA PUERTA: dev_mediana. Este mint ya se marco
                        # como "entra por mediana" en el filtro de creador; si
                        # su MC sigue por debajo del techo propio de esa
                        # puerta (mas alto que el general a proposito), se
                        # compra igual.
                        _mediana_max = float(self.dev_mediana.get("max_entry_mc") or 0)
                        _mediana_ok = (
                            _mediana_max > 0
                            and str(token_info.mint) in self._mints_mediana
                            and _mc <= _mediana_max
                        )
                        if not _alt_ok and not _mediana_ok:
                            _porque = ""
                            if self.entrada_alt_max_mc > 0:
                                _porque = (
                                    f" · la 2a puerta pide MC<="
                                    f"${self.entrada_alt_max_mc:,.0f} con "
                                    f"{self.entrada_alt_min_vol:.0f} SOL y hay "
                                    f"{_v:.1f}"
                                )
                            if _mediana_max > 0:
                                _porque += (
                                    f" · la puerta de mediana pide MC<="
                                    f"${_mediana_max:,.0f}"
                                )
                            logger.info(
                                f"Evitado {token_info.symbol}: se paso de la banda "
                                f"(MC ${_mc:,.0f} > ${self.max_entry_mc:,.0f})"
                                f"{_porque}"
                                # Marca los que SOLO fallaron por market cap:
                                # el dev ya habia pasado su filtro. Son los
                                # candidatos que perdemos por llegar tarde, no
                                # por ser malos -- si salen muchos, el techo
                                # esta demasiado bajo o entramos con retraso.
                                + (
                                    " · DEV OK, solo fallo el MC"
                                    if _dev_ya else ""
                                )
                            )
                            return
                        if _mediana_ok:
                            logger.info(
                                f"{token_info.symbol}: entra por la PUERTA DE "
                                f"MEDIANA (MC ${_mc:,.0f} <= ${_mediana_max:,.0f})"
                            )
                        else:
                            logger.info(
                                f"{token_info.symbol}: entra por la SEGUNDA PUERTA "
                                f"(MC ${_mc:,.0f} con {_v:.1f} SOL de volumen)"
                            )
                    if self.min_entry_mc > 0 and _mc < self.min_entry_mc:
                        await asyncio.sleep(_paso)
                        continue
                    # EDAD MINIMA PARA CONTAR EL SUELO. Ya paso el suelo, pero
                    # si lo hizo ANTES de min_buy_age no cuenta -- ya nacio
                    # por encima, no subio hasta ahi, y eso huele a dev-buy
                    # grande o bundle en vez de demanda real construyendose.
                    if self.min_buy_age > 0 and monotonic() - _t0 < self.min_buy_age:
                        await asyncio.sleep(_paso)
                        continue
                    _acel = (
                        f" · {_vol_sol(str(token_info.mint), self.vol_ventana_seg):.1f} "
                        f"SOL en los ultimos {self.vol_ventana_seg:.0f}s"
                        if self.min_vol_ventana_sol > 0 else ""
                    )
                    logger.info(
                        f"CUMPLE {token_info.symbol}: {_v:.1f} SOL en "
                        f"{_n_buys(str(token_info.mint))} compras, "
                        f"{_n_first_buys(str(token_info.mint))} en primer bloque, "
                        f"{_n_ops(str(token_info.mint))} tx de "
                        f"{_n_tit(str(token_info.mint))} wallets, MC ${_mc:,.0f} "
                        f"a los {monotonic() - _t0:.1f}s{_acel}"
                    )
                    break
                else:
                    # OJO: este `else` es del `while`, no de ningun `if`. Se
                    # ejecuta cuando el bucle acaba sin `break`, INCLUIDO el
                    # caso de que no llegara a correr. Por eso hace falta la
                    # guarda: sin ella, `max_buy_age: 0` rechazaba todo.
                    if self.max_buy_age > 0:
                        # Se dice QUE falto, no solo que fallo: sin esto no hay
                        # forma de saber si el filtro que sobra es el dinero o
                        # la gente.
                        logger.info(
                            f"Evitado {token_info.symbol}: no cumplio en "
                            f"{self.max_buy_age:.0f}s (lo mejor visto: "
                            f"{_visto_vol:.1f} SOL, {_visto_buys} compras, "
                            f"{_visto_first_buys} en primer bloque, "
                            f"{_visto_ops} tx, "
                            f"{_visto_tit} titulares, "
                            f"presion: {_visto_presion}"
                            + (f", curva ilegible: {_motivo_curva}" if _motivo_curva else "")
                            + ")"
                        )
                        if _tarea_redes is not None:
                            _tarea_redes.cancel()
                        return

            # EDAD AL COMPRAR. `max_token_age` se comprueba al sacarla de la
            # cola, cuando la moneda tiene ~1 segundo, así que no acota nada.
            # Éste es el que hace cumplir "menos de N segundos de vida".
            if self.max_buy_age > 0:
                nacida = self.token_timestamps.get(str(token_info.mint))
                if nacida is not None:
                    edad = monotonic() - nacida
                    if edad > self.max_buy_age:
                        logger.info(
                            f"Evitado {token_info.symbol}: {edad:.0f}s de vida "
                            f"(> {self.max_buy_age:.0f}s) al ir a comprar"
                        )
                        if _tarea_redes is not None:
                            _tarea_redes.cancel()
                        return

            # Se recoge lo que se mando a buscar al principio. Ya deberia estar
            # listo; si la pasarela se ha atascado se espera lo justo y, si ni
            # aun asi contesta, NO se compra: sin metadata no hay forma de saber
            # si las redes son suyas, y comprar a ciegas es saltarse el filtro.
            if _tarea_redes is not None:
                from monitoring import redes as _redes

                try:
                    _meta = await asyncio.wait_for(_tarea_redes, timeout=1.0)
                except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
                    _tarea_redes.cancel()
                    _meta = {}
                _ok_redes, _motivo = _redes.evaluar(_meta)
                if not _ok_redes:
                    logger.info(
                        f"Evitado {token_info.symbol}: redes no originales "
                        f"-- {_motivo}"
                    )
                    return
                logger.info(f"Redes OK {token_info.symbol}: {_motivo}")

            # BUNDLE DETECTOR: read the bonding curve once. A fresh pump.fun
            # token starts near 0 SOL; a *bundled* launch already holds several
            # SOL seconds later because the operator self-bought big (observed:
            # ~4.3 + ~11.2 SOL). Those are the launches that actually pump, and
            # they rotate the dev wallet so dev-history filters never catch them.
            curve_sol = entry_price = 0.0
            if (
                self.min_curve_sol > 0
                or self.paper_trade
                or self.paper_engine.enabled
                or self.max_entry_mc > 0
                or self.min_entry_mc > 0
            ):
                try:
                    # commitment="processed" OBLIGATORIO. El cliente se crea sin
                    # commitment y solana-py usa "finalized" por defecto, que va
                    # 13-30 segundos por detras: leer la curva de una moneda de
                    # 12 segundos devolvia "Account not found" en el 100% de los
                    # casos (medido 3-ago: 40 de 44 ilegibles, 0 leidas). El bot
                    # parecia vivo y no podia comprar nada.
                    state = await self.platform_implementations.curve_manager.get_pool_state(
                        token_info.bonding_curve, commitment="processed"
                    )
                    curve_sol = (
                        float(state.get("real_sol_reserves", 0)) / LAMPORTS_PER_SOL
                    )
                    entry_price = float(state.get("price_per_token") or 0)
                except Exception:
                    if self.min_curve_sol > 0:
                        logger.info(f"{token_info.symbol}: curva ilegible; se omite")
                        return

            # Dedicated simulation mode. It is intentionally an early-return
            # branch: enabled means every candidate is either observed or
            # rejected here, and no real buyer/transaction path is reachable.
            if self.paper_engine.enabled:
                from paper_trading.models import Candidate

                creator = str(
                    getattr(token_info, "creator", None) or token_info.user or ""
                )
                entry_mc = entry_price * _PUMP_TOTAL_SUPPLY * _SOL_USD
                features = {}
                try:
                    from monitoring.volumen import operaciones, volumen_sol

                    features["volume"] = volumen_sol(str(token_info.mint))
                    # This feed counts transactions, not unique wallets. Keep
                    # the honest name so the optimizer never mistakes it for
                    # the optional `buyers` feature.
                    features["operations"] = operaciones(str(token_info.mint))
                except Exception:  # noqa: BLE001 - optional point-in-time features
                    pass

                async def _paper_market_cap() -> float:
                    state = await self.platform_implementations.curve_manager.get_pool_state(
                        token_info.bonding_curve, commitment="processed"
                    )
                    price = float(state.get("price_per_token") or 0)
                    return price * _PUMP_TOTAL_SUPPLY * _SOL_USD

                await self.paper_engine.consider(
                    Candidate(
                        signal_ts=datetime.utcnow().timestamp(),
                        mint=str(token_info.mint),
                        symbol=(token_info.symbol or "").strip(),
                        dev=creator,
                        market_cap=entry_mc,
                        features=features,
                    ),
                    _paper_market_cap,
                )
                return

            # OPORTUNIDAD: aviso TEMPRANO al feed de Telegram (antes de los filtros
            # de compra) para lanzamientos frescos de devs de la lista.
            self._notify_opportunity(token_info, entry_price)

            if self.min_curve_sol > 0:
                if curve_sol < self.min_curve_sol:
                    logger.info(
                        f"BUNDLE filtro BLOCK {token_info.symbol}: "
                        f"{curve_sol:.2f} SOL en curva < {self.min_curve_sol}"
                    )
                    return
                logger.info(
                    f"BUNDLE DETECTADO {token_info.symbol}: {curve_sol:.2f} SOL en la curva"
                )

            # TOPE DE ENTRADA: no comprar si la moneda YA está pumpeada por encima
            # de max_entry_mc (USD). Evita entrar en el techo de un spike (compras
            # tipo GigaBull a $5.5K que luego se desploman). Se compra CERCA del
            # lanzamiento (~$2.1K) donde está el recorrido de verdad.
            if self.max_entry_mc > 0 and entry_price > 0:
                entry_mc = entry_price * _PUMP_TOTAL_SUPPLY * _SOL_USD
                if entry_mc > self.max_entry_mc:
                    # PUERTA DE MEDIANA: este mint ya paso el filtro de creador
                    # por mediana (no por tasa/migraciones), y esa puerta trae
                    # su propio techo, mas alto que el general a proposito.
                    _mediana_max = float(self.dev_mediana.get("max_entry_mc") or 0)
                    if (
                        _mediana_max > 0
                        and str(token_info.mint) in self._mints_mediana
                        and entry_mc <= _mediana_max
                    ):
                        logger.info(
                            f"{token_info.symbol}: entra por la PUERTA DE MEDIANA "
                            f"(MC ${entry_mc:,.0f} <= ${_mediana_max:,.0f})"
                        )
                    else:
                        logger.info(
                            f"MC filtro BLOCK {token_info.symbol}: MC entrada "
                            f"${entry_mc:,.0f} > máx ${self.max_entry_mc:,.0f} "
                            f"(ya pumpeada, no entro en el techo)"
                        )
                        return

            # SUELO DE ENTRADA: simétrico al tope. Exige que la moneda YA haya
            # despegado un mínimo antes de entrar. Una moneda recién creada sale
            # sobre los $2K; si a los 20 segundos no ha pasado de ahí es que no
            # ha entrado nadie, y en la muestra de Dune la perdedora típica se
            # queda en $2.462 a los 20s frente a los $8.395 de la ganadora.
            if self.min_entry_mc > 0:
                # Curva ilegible (entry_price 0) = NO se puede afirmar que la
                # moneda haya despegado, que es justo lo que este filtro exige.
                # Antes la condición pedía `entry_price > 0`, así que un fallo
                # de lectura SALTABA el filtro y la moneda entraba con MC $0:
                # el 45% de las señales de VELOCIDAD del 1-ago eran de esas.
                if entry_price <= 0:
                    logger.info(
                        f"MC filtro BLOCK {token_info.symbol}: curva ilegible "
                        f"(MC $0), no puedo confirmar que haya despegado"
                    )
                    return
                entry_mc = entry_price * _PUMP_TOTAL_SUPPLY * _SOL_USD
                if entry_mc < self.min_entry_mc:
                    logger.info(
                        f"MC filtro BLOCK {token_info.symbol}: MC entrada "
                        f"${entry_mc:,.0f} < mín ${self.min_entry_mc:,.0f} "
                        f"(no ha despegado, no entro)"
                    )
                    return

            # VOLUMEN NEGOCIADO. Va antes que GMGN porque es gratis: el dato ya
            # esta en memoria, contado del mismo stream de logs que detecta las
            # monedas. Cero llamadas de red.
            if self.min_volumen_sol > 0 or self.max_volumen_sol > 0:
                from monitoring.volumen import operaciones, volumen_sol

                vol = volumen_sol(str(token_info.mint))
                if self.max_volumen_sol > 0 and vol > self.max_volumen_sol:
                    logger.info(
                        f"VOLUMEN filtro BLOCK {token_info.symbol}: "
                        f"{vol:.2f} SOL negociados > máx {self.max_volumen_sol} "
                        f"(ya pumpeada, llego tarde)"
                    )
                    return
                if self.min_volumen_sol > 0 and vol < self.min_volumen_sol:
                    logger.info(
                        f"VOLUMEN filtro BLOCK {token_info.symbol}: "
                        f"{vol:.2f} SOL negociados < {self.min_volumen_sol} "
                        f"({operaciones(str(token_info.mint))} operaciones)"
                    )
                    return
                logger.info(
                    f"VOLUMEN OK {token_info.symbol}: {vol:.2f} SOL negociados "
                    f"en {operaciones(str(token_info.mint))} operaciones"
                )

            # SEÑAL DE GMGN. Se mira en memoria (la lista se refresca en
            # segundo plano cada 20 s), asi que no cuesta ni una llamada ni un
            # segundo de retraso aqui.
            if self.senal_gmgn:
                from monitoring.senales import tiene_senal

                _t = tiene_senal(str(token_info.mint), self.senal_gmgn)
                if _t is None:
                    logger.info(
                        f"SEÑAL filtro BLOCK {token_info.symbol}: sin señal "
                        f"GMGN {self.senal_gmgn}"
                    )
                    return
                logger.info(f"SEÑAL OK {token_info.symbol}: tipo {_t} de GMGN")

            # TITULARES (GMGN). Va después del market cap a propósito: es la
            # única comprobación que sale del proceso, así que solo se gasta en
            # monedas que ya cumplen lo barato. Si GMGN no contesta se DESCARTA
            # la moneda: con dinero real, "no lo sé" no es "adelante".
            if self.min_holders_gmgn > 0:
                n_hold = await self._holders_gmgn(str(token_info.mint))
                if n_hold < 0:
                    logger.info(
                        f"Evitado {token_info.symbol}: GMGN no dio titulares "
                        f"(no compro a ciegas)"
                    )
                    return
                if n_hold < self.min_holders_gmgn:
                    logger.info(
                        f"HOLDERS filtro BLOCK {token_info.symbol}: "
                        f"{n_hold} titulares < {self.min_holders_gmgn}"
                    )
                    return
                logger.info(
                    f"HOLDERS OK {token_info.symbol}: {n_hold} titulares"
                )

            # DexScreener buy filter (skips the buy if criteria not met).
            # Bounded by _filter_semaphore so many tokens can run the slow
            # (~15s RugCheck) filter concurrently, keeping up with the launch
            # flood — without held positions blocking new filtering.
            async with self._filter_semaphore:
                # Motivo de compra (para el aviso de Telegram): se rellena según
                # qué disparó la compra (migración del dev o pertenencia a lista).
                dev_stats = None
                # Pump.fun creator-migration filter (dev's track record via the
                # pump.fun API — the data RugCheck lacks). Buy only if the dev
                # has migrated >= min_migrations tokens AND rate >= min_rate.
                if self.pumpfun_dev.get("enabled") and (
                    self.pumpfun_dev.get("strict_only", False)
                    or not self._es_marcado(token_info)
                ):
                    creator = await self._dev_que_mintea(token_info)
                    if not creator:
                        # Solo se conoce la cuenta de reparto de comisiones: no
                        # se vio nacer y la curva no dice quien la creo. Antes
                        # esto salia como "0 migr / 0 lanz" sin decir por que.
                        self._descarta(
                            token_info,
                            "dev desconocido (no se sabe que cartera la "
                            "minteo)",
                        )
                        return
                    from monitoring.pumpfun_dev import creator_migration_stats

                    if _dev_ya is not None:
                        # Ya se comprobo al detectarla y paso. Repetirlo aqui
                        # solo anade un await mas al camino critico.
                        migs, launches, rate = _dev_ya
                    else:
                        launches, migs, rate = await creator_migration_stats(
                            str(creator), actual=str(token_info.mint)
                        )
                    min_migs = int(self.pumpfun_dev.get("min_migrations", 2))
                    min_rate = float(self.pumpfun_dev.get("min_rate", 0.24))
                    if migs < min_migs or rate < min_rate:
                        logger.info(
                            f"DEV filtro BLOCK {token_info.symbol}: "
                            f"{migs} migr / {launches} lanz ({rate * 100:.0f}%) "
                            f"< {min_migs} y {int(min_rate * 100)}%"
                        )
                        return
                    self._tasa_dev[str(token_info.mint)] = rate
                    logger.info(
                        f"DEV OK {token_info.symbol}: {migs} migraciones de "
                        f"{launches} lanzamientos ({rate * 100:.0f}%)"
                    )
                    dev_stats = (migs, launches, rate)

                # Anti dump-inicial: qué parte del supply sigue en manos del dev.
                # Va DESPUÉS del filtro de reputación y ANTES del de holders:
                # sólo se gasta esta llamada RPC en tokens que ya han pasado el
                # corte del creador, y evita el trabajo de holders si el dev ya
                # está cargado y va a vender encima.
                if await self._dev_holds_too_much(token_info):
                    self._descarta(token_info, "el dev tiene demasiado supply")
                    return

                # Anti-concentración: no comprar si un holder tiene >max_holder_pct.
                if (
                    self.max_holder_pct > 0 or self.min_holders > 0
                ) and await self._top_holder_exceeds(
                    token_info
                ):
                    _tope = float(
                        (token_info.additional_data or {}).get("max_holder_pct")
                        or self.max_holder_pct
                    )
                    self._descarta(
                        token_info,
                        f"un titular concentra más del {_tope * 100:.0f}% "
                        "(o hay muy pocos)",
                    )
                    return

                if not await self.dex_filter.should_buy(
                    str(token_info.mint), token_info.symbol
                ):
                    self._descarta(token_info, "no pasa el filtro de DexScreener")
                    return

            # SEÑAL: aviso DESACTIVADO el 4-ago a peticion de Manu. Se mandaba por
            # CADA moneda que pasaba los filtros, asi que llegaban dos mensajes
            # por operacion (senal + compra) y el chat se llenaba de monedas que
            # al final ni se compraban. Ahora solo avisan compras y cierres.
            # Para reactivarlo, poner `if True:`.
            if False:
                try:
                    from monitoring.notifier import notify

                    _bot = self._state_suffix.lstrip("_").replace("bot-sniper-", "").upper()
                    _mc = entry_price * _PUMP_TOTAL_SUPPLY * _SOL_USD if entry_price > 0 else 0
                    _reason = self._build_buy_reason(token_info, dev_stats)
                    notify(
                        f"🎯 SEÑAL · <b>{token_info.symbol}</b> ({_bot})\n"
                        f"{_reason}\n"
                        f"MC entrada ~${_mc:,.0f} · buy {self.buy_amount} SOL\n"
                        f'📊 <a href="https://dexscreener.com/solana/{token_info.mint}">DexScreener</a> · '
                        f'<a href="https://gmgn.ai/sol/token/{token_info.mint}">GMGN</a>\n'
                        f"CA: <code>{token_info.mint}</code>"
                    )
                except Exception:  # noqa: BLE001
                    pass

            # LA EDAD OTRA VEZ, JUSTO ANTES DE COMPRAR (5-nov). La ventana de
            # edad de curva96 (`max_edad_seg`) se mira al DETECTAR la moneda;
            # entre eso y aqui va la lectura del historial del dev, que puede
            # tardar varios segundos. Pedido: "que hace comprando esta? Tiene
            # que tener menos de 2 segundos segun sale a pump.fun".
            if self._demasiado_vieja_para_comprar(token_info):
                return

            # Paper-trade: record the candidate, follow its price, never buy.
            if self.paper_trade:
                if (
                    self.paper_max_samples > 0
                    and self._paper_samples_started >= self.paper_max_samples
                ):
                    logger.info(
                        "PAPER: muestra completada (%d/%d); %s no se simula",
                        self._paper_samples_started,
                        self.paper_max_samples,
                        token_info.symbol,
                    )
                    return
                # No hay await entre comprobar y reservar: dos candidatos no
                # pueden adjudicarse el mismo quinto hueco en el event loop.
                self._paper_samples_started += 1
                if self.buy_amount_usd > 0:
                    paper_amount = self.buy_amount_usd / float(_SOL_USD)
                    token_info.additional_data = {
                        **(token_info.additional_data or {}),
                        "signal_buy_amount": paper_amount,
                        "paper_amount_usd": self.buy_amount_usd,
                    }
                elif self.fraccion_saldo > 0:
                    try:
                        balance = await self.solana_client.get_sol_balance(
                            self.wallet.pubkey
                        )
                        free_lamports = max(
                            0,
                            balance - self.min_reserve_lamports,
                        )
                        paper_amount = (
                            free_lamports
                            / LAMPORTS_PER_SOL
                            * self.fraccion_saldo
                        )
                        if self.tope_compra_sol > 0:
                            paper_amount = min(
                                paper_amount,
                                self.tope_compra_sol,
                            )
                        token_info.additional_data = {
                            **(token_info.additional_data or {}),
                            "signal_buy_amount": paper_amount,
                            "balance_fraction": self.fraccion_saldo,
                        }
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "PAPER: no se pudo calcular el 25%% del saldo; "
                            "se usa el importe base",
                            exc_info=True,
                        )
                self._record_paper(token_info, curve_sol, entry_price)
                if entry_price > 0:
                    # Con guardia: una excepcion dentro de _paper_watch se
                    # quedaba en un task sin dueno ("Task exception was never
                    # retrieved") y la simulacion parecia simplemente lenta. Si
                    # vuelve a romperse, ahora sale en el log y en Telegram.
                    async def _seguir(ti=token_info, ep=entry_price, cs=curve_sol):
                        try:
                            await self._paper_watch(ti, ep, cs)
                        except Exception as e:
                            logger.exception("paper watch roto")
                            try:
                                from monitoring.notifier import notify

                                notify(
                                    f"⚠️ <b>Seguimiento en papel roto</b> "
                                    f"({ti.symbol}): {type(e).__name__}: {e}"
                                )
                            except Exception:  # noqa: BLE001, S110
                                pass

                    asyncio.create_task(_seguir())  # noqa: RUF006
                return

            # Passed the filter -> buy + exit-monitor, bounded by the open-position
            # cap so at most N positions deploy real capital at once.
            if self._position_semaphore.locked():
                logger.info(
                    f"Max posiciones abiertas; {token_info.symbol} pasó el filtro pero no se compra"
                )
                return
            if self._tope_comun_lleno():
                logger.info(
                    "Tope comun de %d posiciones entre todas las estrategias; "
                    "%s pasó el filtro pero no se compra",
                    self.max_posiciones_total, token_info.symbol,
                )
                return
            if self._otro_origen_agotado(token_info):
                return
            if self._curva96_agotada(token_info):
                return
            self._avisa_si_el_nodo_no_da_mas()
            async with self._position_semaphore:
                # A2 fix: re-check the "already bought" set INSIDE the semaphore.
                # Two create events for the same mint (websocket redelivery) can
                # both pass the earlier check (~L877) before either buys; the
                # semaphore serializes them, so without this the second task
                # re-buys the same coin (double exposure). Cheap in-memory guard.
                if str(token_info.mint) in self.bought_mints:
                    logger.info(
                        f"Evitado {token_info.symbol}: ya comprada (re-check en semáforo)"
                    )
                    return
                if (self.max_real_buys
                        and self._compras_de_la_tanda >= self.max_real_buys):
                    logger.warning(
                        "Tanda completada: %s/%s compras; %s no se compra",
                        self._compras_de_la_tanda, self.max_real_buys,
                        token_info.symbol,
                    )
                    return
                # The logs listener cannot reliably tell legacy SPL Token from
                # Token-2022 creation events. Never guess here: the mint account
                # owner is authoritative. A wrong program derives the wrong ATA
                # and produces a guaranteed failed transaction.
                try:
                    prepared_program = getattr(self, "_prepared_entry_token_program", None)
                    actual_program = prepared_program(token_info) if callable(prepared_program) else None
                    if actual_program is None:
                        mint_account = await self.solana_client.get_account_info(
                            token_info.mint, commitment="processed"
                        )
                        actual_program = getattr(mint_account, "owner", None)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Compra de %s omitida: no pude verificar el programa del mint",
                        token_info.symbol,
                    )
                    return
                if actual_program not in {TOKEN_PROGRAM, TOKEN_2022_PROGRAM}:
                    logger.warning(
                        "Compra de %s omitida: programa de token no reconocido (%s)",
                        token_info.symbol,
                        actual_program,
                    )
                    return
                if token_info.token_program_id != actual_program:
                    token_info.token_program_id = actual_program
                    address_provider = self.platform_implementations.address_provider
                    token_info.associated_bonding_curve = (
                        address_provider.derive_associated_bonding_curve(
                            token_info.mint,
                            token_info.bonding_curve,
                            actual_program,
                        )
                    )
                # Wallet-balance guard: never commit more lamports than we hold
                # (reserve under a lock so concurrent buys can't both pass).
                # Importe de ESTA compra. Las mayhem van con su propio tamano
                # (mas pequeno) si se ha configurado; el resto, el de siempre.
                if (
                    getattr(token_info, "is_mayhem_mode", False)
                    and self.mayhem_buy_amount > 0
                ):
                    _importe = self.mayhem_buy_amount
                elif self.buy_amount_marcados > 0 and self._es_marcado(token_info):
                    _importe = self.buy_amount_marcados
                else:
                    _importe = self.buy_amount

                signal_data = token_info.additional_data or {}
                signal_amount = float(signal_data.get("signal_buy_amount") or 0)
                if signal_amount > 0:
                    _importe = signal_amount
                    logger.info(
                        "Signal %s selected buy amount %.4f SOL",
                        signal_data.get("acquisition_source", "external"),
                        _importe,
                    )

                # TRAS UN SL: la siguiente compra va con importe reducido.
                # Se consume una sola vez y vuelve al tamaño normal.
                # NO SE ANUNCIA LO QUE NO VA A PASAR. Si manda el porcentaje
                # del saldo, este importe se recalcula despues dentro del
                # cerrojo de gasto y esta asignacion no sobrevive -- pero la
                # linea de log SI salia, diciendo "compra reducida 0,0700 SOL"
                # de una compra que iba a ser otra cosa. Un log que miente es
                # peor que no tenerlo: manda a buscar el fallo donde no esta.
                if (
                    self._ultimo_cierre_fue_sl
                    and self.buy_amount_tras_sl > 0
                    and self.fraccion_saldo <= 0
                ):
                    logger.info(
                        "TRAS SL: compra reducida %.4f SOL (normal %.4f)",
                        self.buy_amount_tras_sl, _importe,
                    )
                    _importe = self.buy_amount_tras_sl
                self._ultimo_cierre_fue_sl = False

                # DEV QUE SUELE PUMPEAR ALTO: mas dinero y comision de
                # prioridad alta para entrar de primero.
                #
                # LA COMISION SOLO SE PAGA AQUI, y por eso. Es FIJA: 1,01 $
                # sobre una posicion de 0,15 SOL (14 $) es el 7,2% de ida y
                # otro tanto de vuelta, o sea 13,4% que hay que ganar antes de
                # empezar a ganar. Sobre 0,30 SOL son 6,7%. Pagarla en todas
                # las compras fue lo que convirtio un dia de +0,4455 SOL en
                # uno de -0,3221.
                _elite_compra = str(token_info.mint) in self._mints_elite
                # COMPRAR CORRE MAS PRISA QUE VENDER. Si hay comision propia de
                # compra, se usa; si no, `None` deja que mande la de siempre.
                _fee_override = self.fee_compra or None
                if _elite_compra:
                    _imp_elite = float(self.dev_elite.get("buy_amount") or 0)
                    if _imp_elite > 0:
                        _importe = _imp_elite
                    _fee_elite = int(self.dev_elite.get("fixed_amount") or 0)
                    if _fee_elite > 0:
                        _fee_override = _fee_elite

                # LA COMPRA CRECE CON LO GANADO. El bote es comun a los tres
                # bots --comparten wallet-- y solo cuenta P&L REAL leido de la
                # cadena. En negativo el multiplicador se queda en 1: no se
                # reduce el tamano tras una racha mala, porque con la mitad de
                # tamano hace falta el doble de aciertos para recuperar.
                if self._bote is not None:
                    _mult = self._bote.multiplicador(self.escala_por_sol_ganado)
                    if _mult > 1.0:
                        _antes = _importe
                        _importe *= _mult
                        logger.info(
                            "BOTE: %+.4f SOL ganados -> la compra sube de %.4f a "
                            "%.4f (x%.2f)",
                            self._bote.acumulada(), _antes, _importe, _mult,
                        )

                if self._selector is not None and not self._selector.puede_operar(
                    self.nombre_estrategia
                ):
                    logger.info(
                        "Evitado %s: el selector ya eligio otra estrategia",
                        token_info.symbol,
                    )
                    return

                # TOPE DURO. El 12-ago la cadena registro compras de 0,25 y
                # 0,30 SOL con `buy_amount: 0.15` configurado, y el log del bot
                # solo apunto dos de la docena que hubo. Hasta saber por que,
                # esto garantiza que ninguna via --marcados, mayhem, o la que
                # sea-- pueda gastar mas de lo que Manu ha autorizado.
                _tope = float(getattr(self, "tope_compra_sol", 0) or 0)
                if _tope > 0 and _importe > _tope:
                    logger.warning(
                        "TOPE: se iba a comprar %.4f SOL de %s y el maximo "
                        "autorizado es %.4f. Se recorta.",
                        _importe, token_info.symbol, _tope,
                    )
                    _importe = _tope
                _tope_total = float(
                    getattr(self, "tope_compra_total_sol", 0) or 0
                )
                _coste_maximo = _importe * (1 + self.buy_slippage)
                if _tope_total > 0 and _coste_maximo > _tope_total:
                    _importe = _tope_total / (1 + self.buy_slippage)
                    logger.warning(
                        "TOPE TOTAL: compra nominal recortada a %.6f SOL; "
                        "con %.1f%% de slippage no puede superar %.4f SOL.",
                        _importe,
                        self.buy_slippage * 100,
                        _tope_total,
                    )
                need = int(
                    _importe * (1 + self.buy_slippage) * LAMPORTS_PER_SOL
                ) + self._fee_headroom_lamports
                # UN BOT POR MONEDA. Los tres procesos ven el mismo websocket
                # y comparten wallet: sin esto, una moneda que cumple en varios
                # se compra varias veces y tres apuestas se convierten en una
                # del triple de tamano. La reserva es atomica (O_EXCL), asi que
                # aunque lleguen en el mismo milisegundo solo gana uno.
                _mint_str = str(token_info.mint)
                if not _reserva_moneda(_mint_str, self.nombre_estrategia):
                    return

                async with self._spend_lock:
                    if (
                        self.max_real_buys
                        and self._compras_de_la_tanda
                        + self._reserved_real_buy_slots
                        >= self.max_real_buys
                    ):
                        logger.warning(
                            "Tanda: %s completadas + %s en curso / %s; "
                            "%s no se compra",
                            self._compras_de_la_tanda,
                            self._reserved_real_buy_slots,
                            self.max_real_buys,
                            token_info.symbol,
                        )
                        return
                    entry_wallet_balance = getattr(self, "_entry_wallet_balance", None)
                    bal = (await entry_wallet_balance(token_info) if callable(entry_wallet_balance)
                           else await self.solana_client.get_sol_balance(self.wallet.pubkey))
                    # Nunca gastar por debajo de la reserva mínima (min_reserve).
                    free = bal - self._reserved_lamports - self.min_reserve_lamports
                    # Una fuente puede pedir su propia fracción del saldo. Es
                    # el caso de las calls de Telegram: 15%, mientras la curva
                    # sigue con el 20% global. Se decide dentro del cerrojo de
                    # gasto para que dos calls simultáneas no calculen ambas
                    # contra el mismo saldo.
                    _signal_fraction = max(
                        0.0,
                        min(
                            1.0,
                            float(signal_data.get("balance_fraction") or 0),
                        ),
                    )
                    # EL TAMANO SALE DEL SALDO. Se calcula aqui dentro, con el
                    # saldo recien leido y bajo el mismo cerrojo que lo reserva:
                    # calculado fuera, dos compras a la vez pedirian cada una el
                    # 70% del mismo saldo y sumarian el 140%.
                    _inclusive_sizer = getattr(self, "_inclusive_entry_budget", None)
                    _inclusive_budget = (
                        _inclusive_sizer(token_info, bal, cap_sol=_tope)
                        if callable(_inclusive_sizer) else None
                    )
                    if _inclusive_budget is not None:
                        # Shared runtimes provide a total budget that already
                        # includes slippage and fees. Do not size nominal SOL
                        # from the fraction again before their durable buyer.
                        _importe, need = _inclusive_budget
                        if _importe <= 0:
                            logger.info("Shared inclusive budget unavailable for %s", token_info.symbol)
                            _suelta_moneda(_mint_str)
                            return
                        logger.info(
                            "INCLUSIVE SHARED: %.6f SOL nominal, %.6f total; %.6f free",
                            _importe, need / LAMPORTS_PER_SOL, free / LAMPORTS_PER_SOL,
                        )
                    elif self.buy_amount_usd > 0:
                        # IMPORTE EN DOLARES (23-sep, pedido: "10 $ por
                        # operacion en SOL"). Los SOL salen de dividir por el
                        # precio de SOL EN VIVO, no por un numero escrito a
                        # mano: asi la apuesta vale lo mismo hoy que dentro de
                        # un mes, suba o baje SOL.
                        #
                        # SE LEE LA GLOBAL EN CADA COMPRA, no una copia
                        # guardada al arrancar. `_precio_sol_loop` reescribe
                        # `_SOL_USD` cada hora y esta lectura recoge el valor
                        # nuevo; una copia hecha en el constructor se quedaria
                        # congelada en el de arranque para siempre -- el bot
                        # funcionando, sin errores, y comprando mal.
                        _sol_usd = float(_SOL_USD)
                        # EL PRECIO POR DEFECTO NO VALE PARA ESTO, y aqui esta
                        # la diferencia entre un aviso y una perdida. El valor
                        # escrito a mano es 91,24 $ (de agosto). Con SOL a
                        # 220 $, gastar "10 $" a ese cambio son 0,1096 SOL, o
                        # sea 24 $ de verdad: 2,4 veces la apuesta acordada,
                        # en cada compra y sin que nadie se entere.
                        #
                        # Mientras el precio no se haya confirmado NO SE
                        # COMPRA. El refresco reintenta cada 10 segundos hasta
                        # que acierta, asi que esto se arregla solo en
                        # segundos; si dura, es que el bot no llega a
                        # DexScreener y entonces hay una averia que hay que
                        # ver, no una compra que hacer a ciegas.
                        if _sol_usd == _ARRANQUE_SOL_USD:
                            logger.error(
                                "Compra de %s omitida: el precio de SOL sigue "
                                "en el valor por defecto (%.2f $) y los "
                                "importes en dolares estarian mal. Se "
                                "reintenta en segundos.",
                                token_info.symbol, _ARRANQUE_SOL_USD,
                            )
                            _suelta_moneda(_mint_str)
                            return
                        _importe = self.buy_amount_usd / _sol_usd
                        if _tope > 0 and _importe > _tope:
                            _importe = _tope
                        need = int(
                            _importe * (1 + self.buy_slippage) * LAMPORTS_PER_SOL
                        ) + self._fee_headroom_lamports
                        logger.info(
                            "IMPORTE: %.2f $ a %.2f $/SOL -> compra de %.4f "
                            "SOL · libres %.4f",
                            self.buy_amount_usd, _sol_usd, _importe,
                            free / LAMPORTS_PER_SOL,
                        )
                    elif (
                        _signal_fraction > 0
                        or self.fraccion_saldo > 0
                        or self.fracciones_saldo
                    ) and self.fraccion_base == "libre":
                        # SOBRE EL SALDO LIBRE (24-sep, pedido: "cada compra
                        # sera del 15% del total de sol libre").
                        #
                        # `free` es lo gastable: saldo menos la reserva minima
                        # menos lo comprometido por las compras en curso. Como
                        # mengua con cada posicion abierta, las compras salen
                        # cada vez mas pequenas -- con 1 SOL: 0,145 · 0,123 ·
                        # 0,105 · 0,089 · 0,076.
                        #
                        # ESO NO ES UN DEFECTO, es lo que hace esta base: el
                        # tamano se frena solo y nunca se llega a comprometer
                        # la cartera entera. Es el freno automatico que se
                        # perdio al pasar a la base "cartera" el 23-sep.
                        if _signal_fraction > 0:
                            _frac, _peldano = _signal_fraction, 0
                        else:
                            _frac, _peldano = self._fraccion_de_esta_compra()
                        _importe = max(0.0, free / LAMPORTS_PER_SOL * _frac)
                        if _tope > 0 and _importe > _tope:
                            _importe = _tope
                        need = int(
                            _importe * (1 + self.buy_slippage) * LAMPORTS_PER_SOL
                        ) + self._fee_headroom_lamports
                        logger.info(
                            "LIBRE: %.4f SOL gastables -> compra de %.4f (%.0f%%"
                            "%s)",
                            free / LAMPORTS_PER_SOL,
                            _importe,
                            _frac * 100,
                            f", peldano {_peldano + 1} de {len(self.fracciones_saldo)}"
                            if self.fracciones_saldo and _signal_fraction <= 0
                            else "",
                        )
                    elif _signal_fraction > 0 or self.fraccion_saldo > 0:
                        # SOBRE LA CARTERA ENTERA, no sobre el saldo libre
                        # (23-sep, pedido: "sobre el portfolio total").
                        #
                        # La diferencia no es un matiz. Con el saldo libre,
                        # cada compra es un porcentaje de lo que SOBRA, asi
                        # que las posiciones menguan solas: 0,30 · 0,21 ·
                        # 0,15 · 0,10 · 0,07. La quinta valia la cuarta
                        # parte que la primera sin que nadie lo pidiera, y
                        # las ultimas acababan por debajo del suelo donde
                        # las comisiones se comen la operacion.
                        #
                        # Sumando lo que ya esta dentro de las monedas, la
                        # base es la cartera de verdad y las posiciones
                        # salen IGUALES. Con el 30% caben tres enteras; la
                        # cuarta la recorta `fraccion_si_no_llega` con lo
                        # que quede, y la quinta no se compra por falta de
                        # saldo. Es lo pedido y hay que saberlo: tres
                        # posiciones del 30% son el 90% de la cartera
                        # jugado a la vez.
                        _dentro = self._valor_en_posiciones_lamports()
                        cartera = bal + _dentro
                        _frac = _signal_fraction or self.fraccion_saldo
                        _importe = max(0.0, cartera / LAMPORTS_PER_SOL * _frac)
                        if _tope > 0 and _importe > _tope:
                            _importe = _tope
                        need = int(
                            _importe * (1 + self.buy_slippage) * LAMPORTS_PER_SOL
                        ) + self._fee_headroom_lamports
                        logger.info(
                            "CARTERA: %.4f SOL (%.4f en wallet + %.4f en "
                            "monedas) · libres %.4f -> compra de %.4f (%.0f%%)",
                            cartera / LAMPORTS_PER_SOL,
                            bal / LAMPORTS_PER_SOL,
                            _dentro / LAMPORTS_PER_SOL,
                            free / LAMPORTS_PER_SOL,
                            _importe,
                            _frac * 100,
                        )
                    # SUELO DE COMPRA. Los costes fijos --renta de la cuenta de
                    # token y comision de prioridad-- no bajan con el tamano:
                    # por debajo de este suelo se van en comisiones y la
                    # operacion nace perdiendo.
                    if self.compra_minima_sol > 0 and _importe < self.compra_minima_sol:
                        logger.warning(
                            "Compra de %s omitida: saldrian %.4f SOL y el minimo "
                            "que compensa es %.4f (por debajo mandan las "
                            "comisiones fijas). Recarga la wallet.",
                            token_info.symbol, _importe, self.compra_minima_sol,
                        )
                        _suelta_moneda(_mint_str)
                        return
                    # NO LLEGA EL IMPORTE ENTERO: se compra con una parte
                    # de lo que quede en vez de saltarse la moneda. Pedido asi
                    # ("0,12 SOL y si no hay saldo, el 75% de lo que quede").
                    #
                    # `free` ya viene limpio: es el saldo menos la reserva
                    # minima y menos lo comprometido por las posiciones que
                    # estan abriendose. Coger el 75% DE ESO deja el otro 25%
                    # para el slippage y las comisiones, que es justo lo que
                    # pide `need` -- por eso el recorte cabe casi siempre y se
                    # vuelve a comprobar de todas formas.
                    if free < need and self.fraccion_si_no_llega > 0 and free > 0:
                        _recorte = (
                            free / LAMPORTS_PER_SOL * self.fraccion_si_no_llega
                        )
                        if _tope > 0 and _recorte > _tope:
                            _recorte = _tope
                        _need_recorte = int(
                            _recorte * (1 + self.buy_slippage) * LAMPORTS_PER_SOL
                        ) + self._fee_headroom_lamports
                        _bajo_minimo = (
                            self.compra_minima_sol > 0
                            and _recorte < self.compra_minima_sol
                        )
                        if _recorte > 0 and _need_recorte <= free and not _bajo_minimo:
                            logger.warning(
                                "SALDO CORTO: %s no llegaba a %.4f SOL; se "
                                "compra con %.4f (el %.0f%% de los %.4f libres).",
                                token_info.symbol, _importe, _recorte,
                                self.fraccion_si_no_llega * 100,
                                free / LAMPORTS_PER_SOL,
                            )
                            _importe = _recorte
                            need = _need_recorte
                        elif _bajo_minimo:
                            # NO se compra polvo. Por debajo del suelo la
                            # operacion nace perdiendo: la comision de
                            # prioridad y la renta de la cuenta de token no
                            # bajan con el tamano de la compra.
                            logger.warning(
                                "Compra de %s omitida: del saldo libre solo "
                                "saldrian %.4f SOL y el minimo que compensa es "
                                "%.4f. Recarga la wallet.",
                                token_info.symbol, _recorte,
                                self.compra_minima_sol,
                            )
                    if free < need:
                        logger.warning(
                            f"Compra de {token_info.symbol} omitida: SOL insuficiente "
                            f"(libre={free / LAMPORTS_PER_SOL:.4f}, "
                            f"necesita={need / LAMPORTS_PER_SOL:.4f})"
                        )
                        # La reserva se suelta: si no, esta moneda quedaba
                        # bloqueada para los tres bots hasta que caducara el
                        # fichero (10 min). El camino de al lado, el del suelo
                        # de compra, ya lo hacia.
                        _suelta_moneda(_mint_str)
                        return
                    self._reserved_lamports += need
                    self._reserved_real_buy_slots += 1
                _pool_idx: int | None = None
                try:
                    # Modo multi-wallet: elegir wallet del pool y fijar el
                    # contexto de esta posición. Las demás posiciones concurrentes
                    # tienen su propio Task → su propio contexto → no se pisan.
                    if self.wallet_pool is not None:
                        _picked_w, _pool_idx = await self.wallet_pool.pick(
                            need, self.solana_client
                        )
                        if _picked_w is None:
                            logger.warning(
                                "Pool sin saldo suficiente para %s (need %.4f SOL)",
                                token_info.symbol, need / LAMPORTS_PER_SOL,
                            )
                            _suelta_moneda(_mint_str)
                            return
                        _ctx_wallet.set(_picked_w)
                        _ctx_seller.set(self._make_seller(_picked_w))
                        _active_buyer = self._make_buyer(_picked_w)
                        logger.info(
                            "Pool: usando wallet %s para %s",
                            str(_picked_w.pubkey)[:8], token_info.symbol,
                        )
                    else:
                        _active_buyer = self.buyer

                    # Buy token
                    logger.info(
                        f"Buying {_importe:.6f} SOL worth of {token_info.symbol} "
                        f"on {token_info.platform.value}..."
                        + (" [MAYHEM a prueba]" if _importe != self.buy_amount else "")
                    )
                    if self.pausar_compras:
                        # MODO ESTUDIO: se hace TODO menos gastar. La moneda
                        # ha pasado los filtros, se sabe el importe exacto y
                        # con que wallet iria -- solo que no se firma nada.
                        # El `finally` de abajo devuelve los lamports que se
                        # habian apartado.
                        self._apuntar_compra_en_pausa(token_info, _importe)
                        _suelta_moneda(_mint_str)
                        return
                    # Una moneda migrada no tiene curva que comprar: su
                    # liquidez esta en un pool y hay que rutar por Jupiter.
                    if (token_info.additional_data or {}).get("migrated"):
                        buy_result: TradeResult = await self._comprar_migrada(
                            token_info, _importe
                        )
                    else:
                        buy_result = await _active_buyer.execute(
                            token_info,
                            amount_sol=_importe,
                            priority_fee_override=_fee_override,
                        )

                    if not buy_result.success:
                        # La compra fallo: la moneda vuelve a estar libre para
                        # que otro bot pueda intentarla.
                        _suelta_moneda(_mint_str)
                    self._anota_compra_para_slippage(ok=bool(buy_result.success))
                    if buy_result.success:
                        buy_reason = self._build_buy_reason(token_info, dev_stats)
                        await self._handle_successful_buy(
                            token_info, buy_result, buy_reason
                        )
                    else:
                        await self._handle_failed_buy(token_info, buy_result)
                finally:
                    async with self._spend_lock:
                        self._reserved_lamports -= need
                        self._reserved_real_buy_slots = max(
                            0, self._reserved_real_buy_slots - 1
                        )
                    if _pool_idx is not None and self.wallet_pool is not None:
                        self.wallet_pool.release(_pool_idx, need)

            # Only wait for next token in yolo mode
            if self.yolo_mode:
                logger.info(
                    f"YOLO mode enabled. Waiting {self.wait_time_before_new_token} seconds before looking for next token..."
                )
                await asyncio.sleep(self.wait_time_before_new_token)

        except Exception:
            logger.exception(f"Error handling token {token_info.symbol}")

    def _build_buy_reason(
        self, token_info: TokenInfo, dev_stats: tuple | None = None
    ) -> str:
        """Motivo legible de por qué se compró (para el aviso de Telegram):
        migración del dev, o pertenencia a lista (+ mediana si la hay)."""
        try:
            if dev_stats is not None:
                migs, launches, rate = dev_stats
                return f"dev migró {rate * 100:.0f}% ({migs}/{launches} lanz)"
            bot = self._state_suffix.lstrip("_").replace("bot-sniper-", "").upper()
            dev = str(
                getattr(token_info, "user", "")
                or getattr(token_info, "creator", "")
                or ""
            ).strip()
            med = self._creator_medians.get(dev) if self._creator_medians else None
            lanz = self._creator_launches.get(dev) if self._creator_launches else None
            extra = ""
            if med:
                extra += f" · mediana ${int(med):,}"
            if lanz:
                extra += f" · {lanz} lanz/7d"
            if extra or self.bro_address is not None:
                return f"lista {bot}{extra}"
            return ""
        except Exception:  # noqa: BLE001
            return ""

    def _notify_opportunity(self, token_info: TokenInfo, entry_price: float) -> None:
        """Feed de OPORTUNIDADES en Telegram: aviso TEMPRANO de un lanzamiento
        fresco de un dev de la lista (antes de los filtros de compra), con el
        porqué, el MC actual y la mediana del dev. Solo bots con lista+mediana
        (no DEV-MIGRATE, que vigila todas y spamearía). Best-effort."""
        try:
            if not self._creator_medians or entry_price <= 0:
                return
            dev = str(
                getattr(token_info, "user", "")
                or getattr(token_info, "creator", "")
                or ""
            ).strip()
            med = self._creator_medians.get(dev)
            if not med:
                return
            lanz = self._creator_launches.get(dev)
            mc = entry_price * _PUMP_TOTAL_SUPPLY * _SOL_USD
            if mc <= 0:
                return
            recorrido = med / mc
            # Solo avisar si hay recorrido REAL (no ya pumpeada cerca de su techo).
            if recorrido < 3:
                return
            if med >= 25000:
                rating = "💎 MUY BUENA"
                porque = "Dev ÉLITE — ~90% de sus monedas llegan a $20K"
            elif med >= 15000:
                rating = "⭐ BUENA"
                porque = "Dev de calidad — buen historial"
            else:
                return  # mediana baja: no es oportunidad
            dev_line = (
                f"Dev: <b>{lanz} lanzamientos</b> (7d) · mediana ${int(med):,}"
                if lanz else f"Dev: mediana ${int(med):,}"
            )
            from monitoring.notifier import notify

            notify(
                f"{rating} · <b>{token_info.symbol}</b>\n"
                f"{porque}\n"
                f"MC ahora: ~${mc:,.0f}  →  <b>MC estimado: ~${int(med):,}</b> (x{recorrido:.1f})\n"
                f"{dev_line}\n"
                f'📊 <a href="https://dexscreener.com/solana/{token_info.mint}">DexScreener</a> · '
                f'<a href="https://gmgn.ai/sol/token/{token_info.mint}">GMGN</a>\n'
                f"CA: <code>{token_info.mint}</code>"
            )
        except Exception:  # noqa: BLE001
            pass

    async def _handle_successful_buy(
        self,
        token_info: TokenInfo,
        buy_result: TradeResult,
        buy_reason: str | None = None,
    ) -> None:
        """Handle successful token purchase."""
        # YA COMPRADA (7-nov, "cuidado con esto, me expones en dos compras"):
        # ningun bot vuelve a comprar esta moneda en 24 h. Aqui y no en cada
        # camino de compra, para que no se escape ninguno. Solo en real: un bot
        # de papel no puede dejar sin comprar a uno de verdad.
        if not getattr(self, "paper_trade", False):
            _marca_comprada(
                str(token_info.mint), str(getattr(self, "nombre_estrategia", "") or "?")
            )
        # CUANTO SE TARDO EN ENTRAR. Es el dato que decide si una compra mala
        # fue mala idea o simplemente llego tarde, y hasta ahora habia que
        # sacarlo restando a mano las horas de dos lineas distintas del log.
        # El 19-sep eso costo una moneda comprada despues de desplomarse.
        _decidida = (token_info.additional_data or {}).get("decidida_en")
        _tardanza = ""
        if _decidida:
            _tardanza = f" — tardo {monotonic() - float(_decidida):.1f}s en entrar"
        logger.info(
            f"Successfully bought {token_info.symbol} on "
            f"{token_info.platform.value}{_tardanza}"
        )
        self._compras_de_la_tanda += 1
        self._apunta_compra_por_origen(token_info)
        self._cuenta_compra_para_el_resumen()
        self._anota_firma(str(token_info.mint), buy_result.tx_signature)
        self._log_trade(
            "buy",
            token_info,
            buy_result.price,
            buy_result.amount,
            buy_result.tx_signature,
            buy_reason,
        )
        self.traded_mints.add(token_info.mint)
        # Track token program for cleanup
        mint_str = str(token_info.mint)
        # Persist to the "already bought" blocklist so we never re-buy this coin.
        self.bought_mints.add(mint_str)
        self._persist_bought_mint(mint_str)
        if self.callouts.get("enabled"):
            try:
                from monitoring.callout_outbox import enqueue_callout

                _senal = token_info.additional_data or {}
                callout_message = enqueue_callout(
                    token_info.name or token_info.symbol,
                    mint_str,
                    buy_result.tx_signature,
                    Path(
                        self.callouts.get("outbox")
                        or "trades/pumpfun_callout_outbox.jsonl"
                    ),
                    signal_strength=max(
                        float(_senal.get("dexscreener_boosts") or 0),
                        float(_senal.get("dextools_nitro_score") or 0),
                    ),
                )
                from monitoring.notifier import notify

                notify(f"📣 <b>CALLOUT PREPARADO</b>\n{callout_message}")
            except Exception:
                logger.exception("Failed to enqueue post-buy callout for %s", mint_str)
        if token_info.token_program_id:
            self.traded_token_programs[mint_str] = token_info.token_program_id

        if not (token_info.additional_data or {}).get("four_purchase_notice"):
            # AVISO DE COMPRA con el porque. Hasta ahora la compra solo se apuntaba
            # en el log: desde Telegram se veia que habia entrado pero no que la
            # habia hecho entrar, asi que era imposible juzgar la señal despues.
            try:
                from monitoring.notifier import build_purchase_notification, notify
                from monitoring.senales import tiene_senal
                from monitoring.volumen import operaciones, volumen_sol

                _lineas = []

                _t = tiene_senal(mint_str, self.senal_gmgn) if self.senal_gmgn else None
                if _t:
                    _lineas.append(f"📡 Señal GMGN <b>tipo {_t}</b>")

                _dev = str(
                    getattr(token_info, "creator", "")
                    or getattr(token_info, "user", "")
                    or ""
                ).strip()
                if _dev:
                    # AQUI HABIA UN `await creator_migration_stats(_dev)`, y era
                    # doce segundos de ceguera en el peor momento posible.
                    #
                    # QUE HACIA: una llamada HTTP a la API de pump.fun, con
                    # `ClientTimeout(total=12)`, para preguntar cuantas monedas ha
                    # migrado el creador. UNICO uso: la linea "👤 Dev migró X%" de
                    # este mensaje de Telegram.
                    #
                    # DONDE ESTABA: en el camino critico, entre que la compra
                    # confirma y que arranca el monitor de la posicion. Durante esos
                    # segundos la moneda recien comprada NO la mira nadie: ni take
                    # profit, ni stop loss. En una moneda que hace x3,75 en un
                    # minuto, doce segundos es media subida.
                    #
                    # Y ADEMAS ES MEDIR UN DEV, que Manu pidio quitar el 19-sep.
                    # Las dos razones apuntan al mismo sitio, asi que fuera. La
                    # mediana de abajo se queda: sale de un diccionario en memoria
                    # y no cuesta ni una llamada.
                    _med = (self._creator_medians or {}).get(_dev)
                    if _med:
                        _lineas.append(f"📈 Mediana del dev: <b>${int(_med):,}</b>")

                _v = volumen_sol(mint_str)
                if _v:
                    _lineas.append(
                        f"💧 {_v:.1f} SOL negociados en {operaciones(mint_str)} ops"
                    )

                _mc = (buy_result.price or 0) * _PUMP_TOTAL_SUPPLY * _SOL_USD
                _tr = " · ".join(
                    f"+{t.gain * 100:.0f}%/{t.sell * 100:.0f}%" for t in self.tp_tiers
                )
                _signal_data = token_info.additional_data or {}
                _amount = float(_signal_data.get("signal_buy_amount") or self.buy_amount)
                notify(
                    build_purchase_notification(
                        symbol=token_info.symbol,
                        mint=mint_str,
                        amount_sol=_amount,
                        source_data=_signal_data,
                        market_cap_usd=_mc,
                        strategy=_tr,
                        stop_loss_pct=self.stop_loss_percentage * 100,
                        extra_lines=_lineas,
                        entry_price_sol=buy_result.price or 0.0,
                        take_profit_tiers=[
                            (tier.gain, tier.sell) for tier in self.tp_tiers
                        ],
                    ),
                    tipo="compra",
                )
            except Exception:  # noqa: BLE001
                pass

        # Choose exit strategy
        if not self.marry_mode:
            if self.exit_strategy == "tp_sl":
                await self._handle_tp_sl_exit(token_info, buy_result)
            elif self.exit_strategy == "time_based":
                await self._handle_time_based_exit(token_info, buy_result)
            elif self.exit_strategy == "manual":
                logger.info("Manual exit strategy - position will remain open")
        else:
            logger.info("Marry mode enabled. Skipping sell operation.")

    async def _comprar_migrada(
        self, token_info: TokenInfo, amount_sol: float
    ) -> TradeResult:
        """Compra por el pool (via Jupiter) una moneda que ya salio de la curva.

        El comprador normal construye la instruccion CONTRA la curva de
        pump.fun. En una moneda migrada esa curva esta vacia, asi que esa
        compra sencillamente no existe: la liquidez vive en PumpSwap o Raydium
        y Jupiter es quien sabe llegar hasta ahi.

        Args:
            token_info: La moneda, ya marcada como migrada.
            amount_sol: SOL a gastar.

        Returns:
            Un TradeResult equivalente al del comprador de curva, para que el
            resto del flujo (monitor de salida, avisos, registro) no tenga que
            saber por donde se entro.
        """
        from trading.jupiter_seller import jupiter_buy

        wallet = _ctx_wallet.get() or self.wallet
        logger.info(
            "%s ya migro: comprando %.6f SOL por el pool (Jupiter)",
            token_info.symbol,
            amount_sol,
        )
        ok, firma, error, tokens = await jupiter_buy(
            wallet.keypair,
            str(token_info.mint),
            amount_sol,
            slippage_bps=int(self.buy_slippage * 10000),
        )
        if not ok or tokens <= 0:
            return TradeResult(
                success=False,
                platform=token_info.platform,
                error_message=error or "Jupiter no devolvio tokens",
            )
        # El precio de entrada sale de lo gastado entre lo recibido: es el
        # precio REAL pagado, con su slippage dentro, y es el que la escalera
        # de TP/SL tiene que usar como referencia.
        return TradeResult(
            success=True,
            platform=token_info.platform,
            tx_signature=firma,
            amount=tokens,
            price=amount_sol / tokens,
        )

    async def _handle_failed_buy(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle failed token purchase."""
        logger.error(f"Failed to buy {token_info.symbol}: {buy_result.error_message}")
        # Close ATA if enabled
        await handle_cleanup_after_failure(
            self.solana_client,
            _ctx_wallet.get() or self.wallet,
            token_info.mint,
            token_info.token_program_id,
            self.priority_fee_manager,
            self.cleanup_mode,
            self.cleanup_with_priority_fee,
            self.cleanup_force_close_with_burn,
        )

    async def _handle_tp_sl_exit(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle the tiered take-profit / stop-loss exit strategy."""
        await self._monitor_tiered_exit(token_info, buy_result)

    async def _saldo_real_tokens(self, token_info: TokenInfo) -> float | None:
        """Tokens que hay DE VERDAD en la wallet, o None si no se puede leer.

        Devolver None (y no 0) cuando falla la lectura es deliberado: si el RPC
        no responde, se prefiere intentar la venta con la cantidad estimada
        antes que dar por cerrada una posicion que sigue abierta.

        SE MIRAN LOS DOS PROGRAMAS DE TOKEN. La direccion de la cuenta donde
        viven los tokens sale de mezclar wallet + mint + PROGRAMA. Si el
        programa que traia `token_info` no es el bueno, la direccion calculada
        no existe, el saldo se lee como cero y la posicion se da por cerrada
        sin haber vendido nada -- 37 de 373 ventas acabaron asi. El 18-ago
        quedaban 3 bolsas atrapadas (163k, 1,64M y 1,45M de tokens): las tres
        estaban en Token-2022 y con el programa clasico no aparecian.

        Cuando la bolsa se encuentra bajo el otro programa se CORRIGE
        `token_info.token_program_id`, no solo se lee: la venta construye su
        lista de cuentas con ese mismo campo (`address_provider`), asi que
        arreglar la lectura sin arreglar el campo dejaria la venta apuntando
        igualmente a una cuenta vacia.
        """
        prog = token_info.token_program_id or TOKEN_2022_PROGRAM
        otro = TOKEN_PROGRAM if prog == TOKEN_2022_PROGRAM else TOKEN_2022_PROGRAM

        async def _leer(programa: Pubkey) -> int | None:
            """Saldo crudo en esa cuenta, o None si el RPC no supo contestar."""
            try:
                ata = self.platform_implementations.address_provider.derive_user_token_account(
                    (_ctx_wallet.get() or self.wallet).pubkey, token_info.mint, programa
                )
                return await self.solana_client.get_token_account_balance(ata)
            except Exception:  # noqa: BLE001
                return None

        async def _ronda() -> tuple[int | None, Pubkey]:
            crudo = await _leer(prog)
            if crudo:
                return crudo, prog
            crudo_otro = await _leer(otro)
            if crudo_otro:
                return crudo_otro, otro
            # Ninguno tiene nada: se devuelve lo que dijo el programa esperado,
            # para no confundir "esta vacia" con "no se pudo leer".
            return crudo, prog

        crudo, encontrado = await _ronda()
        if crudo is None:
            return None
        if crudo <= 0:
            # CERO NO ES CERO A LA PRIMERA. El 12-ago `degen` leyo 0 tokens
            # recien comprada --la cuenta todavia no se veia en ese nodo-- y el
            # bot dio la posicion por cerrada sin vender nada, apuntando ademas
            # que habia cobrado 0,1496 SOL. La cadena dice que se perdieron los
            # 0,1687 enteros. Se pregunta otra vez y solo se cree el cero si
            # sale dos veces.
            await asyncio.sleep(1.5)
            crudo, encontrado = await _ronda()
            if crudo is None:
                return None
            if crudo > 0:
                logger.warning(
                    "%s: el saldo salio 0 y a la segunda lectura habia %.6f "
                    "tokens. Era retraso del RPC, no una posicion cerrada.",
                    token_info.symbol, crudo / 10**TOKEN_DECIMALS,
                )
        if crudo > 0 and encontrado != token_info.token_program_id:
            _nombre = lambda p: "Token-2022" if p == TOKEN_2022_PROGRAM else "Token clasico"  # noqa: E731
            logger.warning(
                "%s: los tokens estaban en %s, no en %s. Corrijo el programa: "
                "sin esto la venta se construiria contra una cuenta vacia y la "
                "bolsa se quedaria atrapada.",
                token_info.symbol, _nombre(encontrado),
                _nombre(token_info.token_program_id),
            )
            token_info.token_program_id = encontrado
        return crudo / 10**TOKEN_DECIMALS

    def _dentro_del_horario(self) -> bool:
        """True si toca operar ahora, en hora de España.

        Sin franja configurada se opera siempre. La hora se saca de la zona
        `Europe/Madrid` y no del reloj del servidor: el droplet va en UTC, y
        usar su hora movería la franja una hora entera al cambiar a horario de
        invierno sin que nadie tocara nada.
        """
        if not self.horario:
            return True
        try:
            inicio = int(self.horario.get("inicio", 0))
            fin = int(self.horario.get("fin", 24))
        except (TypeError, ValueError):
            return True
        if inicio == fin:
            return True
        try:
            from zoneinfo import ZoneInfo

            ahora = datetime.now(ZoneInfo("Europe/Madrid")).hour
        except Exception:  # noqa: BLE001
            # Sin zona horaria disponible se opera, que es el fallo menos malo:
            # quedarse mudo por no saber la hora seria peor que operar de mas.
            return True
        if inicio < fin:
            return inicio <= ahora < fin
        # La franja cruza medianoche (18 -> 2): vale la cola del dia o la
        # cabeza del siguiente.
        return ahora >= inicio or ahora < fin

    def _es_marcado(self, token_info: TokenInfo) -> bool:
        """True si la moneda viene de un dev de la lista de confianza."""
        if not self._devs_marcados:
            return False
        # 7-nov: tambien la cartera que la minteo, si la lanzo una plataforma
        # ("El dev es el q mint", ver `_identidades_del_dev`).
        ids = {str(token_info.user), str(getattr(token_info, 'creator', '') or '')}
        return bool((ids | self._identidades_del_dev(token_info)) & self._devs_marcados)

    def _en_lista_elite(self, token_info: TokenInfo) -> bool:
        """True si el dev esta en `dev_elite.lista` (7-nov, devs de 30k+)."""
        lista = getattr(self, "_devs_elite_lista", None)
        if not lista:
            return False
        ids = {str(token_info.user), str(getattr(token_info, 'creator', '') or '')}
        return bool((ids | self._identidades_del_dev(token_info)) & lista)

    def _anota_firma(self, mint: str, sig: str | None) -> None:
        """Guarda la firma de una compra o venta para la contabilidad real."""
        if sig:
            self._sigs_operacion.setdefault(str(mint), []).append(str(sig))

    async def _pnl_real(self, mint: str) -> float | None:
        """SOL que de verdad entro y salio de la cartera en esta operacion.

        Suma el delta de saldo de la wallet en cada transaccion de la operacion,
        leido de la cadena (preBalances/postBalances). Incluye comisiones,
        impacto de precio y slippage, que es justo lo que el P&L teorico se deja
        fuera. Devuelve None si no se puede leer alguna transaccion.
        """
        sigs = self._sigs_operacion.pop(str(mint), [])
        if not sigs:
            return None
        yo = str((_ctx_wallet.get() or self.wallet).pubkey)
        neto = 0.0
        for sig in sigs:
            try:
                tx = await self.solana_client._get_transaction_result(sig)
            except Exception:  # noqa: BLE001
                return None
            if not tx:
                return None
            claves = [
                str(k.get("pubkey") if isinstance(k, dict) else k)
                for k in tx["transaction"]["message"]["accountKeys"]
            ]
            if yo not in claves:
                return None
            i = claves.index(yo)
            neto += (
                tx["meta"]["postBalances"][i] - tx["meta"]["preBalances"][i]
            ) / LAMPORTS_PER_SOL
        return neto

    async def _sell_amount(
        self,
        token_info: TokenInfo,
        amount: float,
        ref_price: float,
        reason: str,
        *,
        full_exit: bool = False,
    ) -> bool:
        """Sell a specific token amount and log it. Returns success.

        El importe se limita SIEMPRE al saldo real de la cuenta de tokens. El
        1-ago esto vacio una wallet: el bot llevaba su propia contabilidad de
        cuantos tokens tenia, pedia vender mas de los que habia, pump.fun
        respondia 6023 NotEnoughTokensToSell, y quien llamaba reintentaba cada
        segundo. 178 ventas fallidas, 0,64 SOL quemados solo en comisiones de
        prioridad. La contabilidad interna se desvia de la realidad en cuanto
        una compra recibe menos tokens de los estimados (slippage), asi que la
        unica fuente de verdad es la cadena.
        """
        # Freno de mano: cada intento de venta que falla CUESTA dinero (la
        # comision de prioridad se paga aunque la transaccion revierta). Quien
        # llama reintenta cada `price_check_interval` durante `abandon_after`
        # segundos, o sea hasta 660 intentos por posicion. El 1-ago eso quemo
        # 0,64 SOL en 178 ventas fallidas. Tras unos pocos fallos seguidos se
        # deja de pagar: si no ha salido en 3, no va a salir en 300.
        mint = str(token_info.mint)
        # ESPERA, NO RENDICION. Antes, al tercer fallo esta moneda quedaba
        # bloqueada para siempre: devolvia False sin tocar la red para TODO --
        # take profits, stop loss, anti-rug, panico y ventas manuales--, y lo
        # unico que lo reiniciaba era una venta con exito, que ya no podia
        # ocurrir. La posicion se quedaba sin freno hasta las 24 horas de
        # `abandonar_tras_seg`.
        #
        # Y se alimentaba solo: el suelo de slippage se calcula contra el
        # precio del tick anterior menos un 10%, asi que en una caida rapida
        # los tres fallos llegan seguidos JUSTO cuando hace falta vender.
        #
        # Esto gasta menos que antes en el caso normal --un intento por
        # ventana en vez de uno por segundo hasta el tope-- y no abandona.
        _hasta = self._bloqueada_hasta.get(mint, 0.0)
        if _hasta and monotonic() < _hasta:
            self._ultimo_cobro = 0.0
            return False

        # Se pone a cero ANTES de intentar nada: cada camino que si cobre lo
        # rellena, y asi ningun fallo puede dejar el valor de la venta anterior.
        self._ultimo_cobro = 0.0

        from trading.sell_precision import align_full_sell

        real = await self._saldo_real_tokens(token_info)
        if real is not None:
            if real <= 0:
                logger.info(
                    f"{token_info.symbol}: no quedan tokens que vender ({reason}); "
                    f"la posicion ya estaba cerrada"
                )
                # No se ha cobrado NADA. Quien llama suma `self._ultimo_cobro`
                # al total realizado, asi que dejarlo en cero es lo que impide
                # que un cierre sin venta se apunte como ingreso.
                self._ultimo_cobro = 0.0
                return True  # nada que vender = objetivo cumplido, no un fallo
            if amount > real:
                logger.info(
                    f"{token_info.symbol}: pedia vender {amount:.6f} pero solo hay "
                    f"{real:.6f}; ajusto al saldo real"
                )
                amount = real

            amount, _ = align_full_sell(amount, real, TOKEN_DECIMALS, full_exit=full_exit)

        _seller = self._vendedor_para(reason)
        sell_result = await _seller.execute(
            token_info,
            token_amount=amount,
            token_price=ref_price,
            priority_fee_override=self._fee_de_venta_para(reason),
        )
        if sell_result.success:
            self._fallos_venta.pop(mint, None)
            self._bloqueada_hasta.pop(mint, None)
            logger.info(
                f"Sold {amount:.6f} {token_info.symbol} ({reason}) "
                f"@ {sell_result.price:.8f} SOL"
            )
            self._log_trade(
                "sell",
                token_info,
                sell_result.price,
                sell_result.amount,
                sell_result.tx_signature,
                reason,
            )
            self._anota_firma(str(token_info.mint), sell_result.tx_signature)
            self._ultimo_cobro = sell_result.amount * sell_result.price
            return True

        # Fallback: the curve sell may have failed because the token migrated
        # (bonding curve complete). Sell via Jupiter, which routes through any
        # DEX (PumpSwap/Raydium/…), so a held position can always be exited.
        logger.warning(
            "Curve sell failed for %s (%s): %s — trying Jupiter fallback",
            token_info.symbol, reason, sell_result.error_message,
        )
        try:
            from trading.jupiter_seller import jupiter_sell

            ok, sig, err, _sol_out = await jupiter_sell(
                self.wallet.keypair, str(token_info.mint), amount,
                slippage_bps=int(self._slippage_para(reason) * 10000),
            )
        except Exception as exc:  # noqa: BLE001
            ok, sig, err, _sol_out = False, None, str(exc), 0.0
        if ok:
            self._fallos_venta.pop(mint, None)
            self._bloqueada_hasta.pop(mint, None)
            # EL PRECIO QUE SE APUNTA ES EL DE LA VENTA, no el de antes de
            # venderla, y esto costo un diagnostico entero el 24-sep.
            #
            # `ref_price` es el ultimo precio VISTO, o sea el de la vuelta
            # anterior del vigilante. En una venta normal se parecen; en un
            # desplome no tienen nada que ver. FUCKETHAN se ejecuto a -92% y
            # quedo apuntada como -60%, asi que `porque_cerro` y todas las
            # mediciones daban una estrategia mejor de la que hay -- y encima
            # el error era siempre a favor, que es como no enterarse nunca.
            #
            # `_sol_out` es lo que la cotizacion de Jupiter dice que entra. No
            # es la liquidacion definitiva --el slippage puede recortarla--
            # pero se equivoca por muchisimo menos. Si viene a cero (una
            # version vieja, o una cotizacion ilegible) se usa el de antes:
            # un numero flojo es mejor que ninguno.
            _precio = (_sol_out / amount) if (_sol_out > 0 and amount > 0) else ref_price
            logger.info(
                "Sold %s via Jupiter (%s) @ %.8f SOL tx=%s",
                token_info.symbol, reason, _precio, sig,
            )
            self._anota_firma(str(token_info.mint), sig)
            self._log_trade("sell", token_info, _precio, amount, sig, reason)
            self._ultimo_cobro = amount * _precio
            return True

        fallos = self._fallos_venta.get(mint, 0) + 1
        self._fallos_venta[mint] = fallos
        logger.error(
            "Sell failed for %s (%s) [%d/%d]: curve=%s | jupiter=%s",
            token_info.symbol, reason, fallos, self.MAX_FALLOS_VENTA,
            sell_result.error_message, err,
        )
        if fallos >= self.MAX_FALLOS_VENTA:
            # La espera se dobla en cada fallo nuevo, y las que limitan
            # perdidas arrancan de una espera mucho mas corta.
            _base = (
                self.ESPERA_PROTECTORA_SEG
                if self._es_venta_protectora(reason)
                else self.ESPERA_TP_SEG
            )
            _espera = min(
                _base * (2 ** (fallos - self.MAX_FALLOS_VENTA)),
                self.ESPERA_MAX_SEG,
            )
            self._bloqueada_hasta[mint] = monotonic() + _espera
            logger.error(
                "%s: %d ventas fallidas seguidas (%s); espero %.0fs antes de "
                "volver a intentarlo, para no quemar comisiones.",
                token_info.symbol, fallos, reason, _espera,
            )
            try:
                from monitoring.notifier import notify

                notify(
                    f"⏳ <b>VENTA EN ESPERA</b> · <b>{token_info.symbol}</b>\n"
                    f"{fallos} intentos fallidos ({sell_result.error_message}).\n"
                    f"Reintento en {_espera:.0f}s. La posición SIGUE vigilada.\n"
                    f"CA: <code>{mint}</code>"
                )
            except Exception:  # noqa: BLE001
                pass
        return False

    def _slippage_para(self, reason: str) -> float:
        """El suelo de precio que le toca a esta venta.

        Ancho si la venta existe para limitar perdidas; el de siempre si es un
        take profit. Ver `sell_slippage_proteccion` en el yaml: el suelo
        estrecho hacia que un stop loss NO SE EJECUTARA justo en la moneda que
        se estaba desplomando.
        """
        # Con `getattr` a proposito: los tests arman traders a medias --sin
        # pasar por `__init__`-- para poder probar el camino de venta sin
        # cartera ni nodo, y un atributo que falte no puede tumbar un stop.
        normal = float(getattr(self, "sell_slippage", 0.1) or 0.1)
        ancho = float(getattr(self, "sell_slippage_proteccion", 0) or 0)
        if ancho > 0 and self._es_venta_protectora(reason):
            return max(normal, ancho)
        return normal

    def _vendedor_para(self, reason: str):  # noqa: ANN202
        """El vendedor de siempre, o una copia con el suelo ancho.

        SE COPIA EN VEZ DE CAMBIARLE EL SUELO AL COMPARTIDO: varias posiciones
        pueden estar vendiendo a la vez, y tocar el objeto de todas para una
        venta concreta le cambiaria el suelo a las demas a mitad de camino.
        La copia es superficial -- comparte cliente, cartera y comisiones-- asi
        que no cuesta nada.
        """
        base = _ctx_seller.get() or self.seller
        slippage = self._slippage_para(reason)
        if slippage == getattr(base, "slippage", slippage):
            return base
        otro = copy.copy(base)
        otro.slippage = slippage
        return otro

    def _fee_de_venta_para(self, reason: str) -> int | None:
        """La comision de prioridad que le toca a esta venta, o None.

        LA PRISA SE PAGA DONDE HACE FALTA. Un take profit puede esperar: si no
        sale en esta vuelta sale en la siguiente y la moneda sigue arriba. Un
        stop loss no -- cada segundo de cola es precio perdido, y la comision
        de venta normal es 42 veces menor que la de compra.

        None = la de siempre. Se devuelve None y no un cero porque cero
        significaria "sin prioridad ninguna", que es lo contrario.

        Con `getattr` a proposito, igual que `_slippage_para`: los tests arman
        traders a medias --sin pasar por `__init__`-- para poder probar el
        camino de venta sin cartera ni nodo, y un atributo que falte no puede
        tumbar un stop.
        """
        rapida = int(getattr(self, "fee_venta_proteccion", 0) or 0)
        if rapida > 0 and self._es_venta_protectora(reason):
            return rapida
        return None

    def _anota_compra_para_slippage(self, *, ok: bool) -> None:
        """Cuenta las compras fallidas seguidas y sube el slippage si toca.

        Pedido el 7-nov: "si hay tres fallidas sube el slippage a 14%". Con un
        slippage de compra bajo, la compra falla cuando el precio se mueve mas
        de ese % mientras llega, que es justo lo que pasa en monedas con
        presion. Tras N fallidas SEGUIDAS se sube, y se queda subido hasta que
        el bot se reinicie. Una compra buena pone la cuenta a cero. Nunca baja
        el slippage: si ya estaba por encima, no se toca.
        """
        # Con `getattr`: hay traders (FourStrategyTrader, tests) que se arman
        # sin pasar por este `__init__`, y esto no puede tumbar una compra.
        if ok:
            self._compras_fallidas_seguidas = 0
            return
        self._compras_fallidas_seguidas = (
            int(getattr(self, "_compras_fallidas_seguidas", 0)) + 1
        )
        n = int(getattr(self, "fallidas_para_subir_slippage", 0) or 0)
        nuevo = float(getattr(self, "slippage_tras_fallidas", 0) or 0)
        if n <= 0 or nuevo <= self.buy_slippage:
            return
        if self._compras_fallidas_seguidas < n:
            return
        logger.warning(
            "SLIPPAGE DE COMPRA %.0f%% -> %.0f%%: %d compras fallidas seguidas",
            self.buy_slippage * 100, nuevo * 100, self._compras_fallidas_seguidas,
        )
        self.buy_slippage = nuevo
        self.buyer.slippage = nuevo

    def _es_venta_protectora(self, reason: str) -> bool:
        """True si esta venta existe para limitar perdidas.

        Un take profit puede esperar: si no sale hoy, sale manana o no sale, y
        cada intento cuesta comision. Un stop loss no: lo que se pierde por no
        ejecutarlo es mucho mayor que lo que cuesta reintentarlo.
        """
        _r = (reason or "").lower()
        return any(_r.startswith(m) for m in self.MOTIVOS_PROTECTORES)

    def _apuntar_compra_en_pausa(
        self, token_info: TokenInfo, importe: float
    ) -> None:
        """Deja constancia de una compra que NO se hizo, y por que.

        En modo estudio esto es el producto: la lista de lo que el bot habria
        comprado, con su importe y su origen, sin haber gastado un duro. Sirve
        para dos cosas que no se pueden hacer mirando el log a secas -- contar
        cuantas oportunidades da cada fuente al dia, y despues comprobar que
        habria pasado con cada una.

        Se escribe en el MISMO fichero de operaciones y con el mismo formato
        que una compra real, marcada con `motivo: "pausa"`. Asi los informes
        que ya existen la ven sin tocarles nada.
        """
        origen = (token_info.additional_data or {}).get(
            "acquisition_source", "listener"
        )
        logger.info(
            "PAUSA · %s: habria comprado %.4f SOL (origen %s). No se gasta "
            "nada: `pausar_compras` esta puesto.",
            token_info.symbol, importe, origen,
        )
        try:
            # `amount` lleva el importe en SOL que se habria gastado, y el
            # precio va a 0 a proposito: no hay compra, asi que no hay precio
            # de entrada que apuntar. La accion propia
            # ("compra_en_pausa") impide que ningun informe la sume como si
            # fuera una compra de verdad.
            self._log_trade(
                "compra_en_pausa",
                token_info,
                0.0,
                round(importe, 6),
                None,
                reason="pausar_compras",
            )
        except Exception:  # noqa: BLE001, S110
            # Apuntarlo es lo que da valor al modo estudio, pero si fallara
            # tampoco puede tumbar el bot: la linea del log de arriba ya ha
            # salido.
            pass

    async def _precio_rapido(
        self, mint: str, pool_address: "Pubkey", curve_manager: object
    ) -> float:
        """Precio de la curva: del stream si lo hay, y si no del nodo.

        POR QUE EXISTE. El precio de una bonding curve solo cambia cuando
        alguien opera, y cada operacion publica en su TradeEvent las reservas
        virtuales que quedan despues -- que es de donde sale el precio. El bot
        ya esta suscrito a ese stream, asi que tenia el precio exacto delante
        y aun asi le preguntaba al nodo una vez por segundo. Eso es hasta un
        segundo tarde (justo lo que dura un pico) y cinco peticiones por
        segundo del presupuesto del nodo con cinco posiciones abiertas.

        LO QUE NO CAMBIA: el nodo se sigue consultando a su ritmo de siempre
        (`price_check_interval`). No es redundancia, es el unico modo de
        notar que el stream se ha caido: un websocket cortado no avisa, deja
        de mandar eventos, y desde aqui eso es idéntico a "no hay
        operaciones". Sin esa consulta periodica una posicion podria quedarse
        mirando un precio congelado mientras la moneda se hunde de verdad, y
        el stop loss no saltaria nunca.

        Entre consulta y consulta se usa el stream, que es donde se gana la
        velocidad. Si tampoco hay stream se devuelve el ultimo precio sabido:
        preguntar al nodo mas a menudo de lo configurado es justo lo que este
        cambio viene a evitar.

        Raises:
            Lo que lance el nodo. El monitor lo recoge para pasarse al DEX,
            que es como detecta que una moneda migro.
        """
        # Con el stream apagado se pregunta al nodo SIEMPRE, sin pasar por la
        # cache. Es el comportamiento de siempre y tiene que quedar identico:
        # quien marca el ritmo entonces es el bucle, que duerme
        # `price_check_interval` entre vueltas. Dejar la cache puesta aqui
        # haria que una vuelta que llega un pelo pronto devolviera el precio
        # anterior en vez de uno fresco -- una diferencia pequena, pero en el
        # camino por el que pasa el stop loss.
        if self.precio_stream_seg <= 0:
            precio = await curve_manager.calculate_price(pool_address)
            self._ultima_rpc[mint] = monotonic()
            self._ultimo_precio[mint] = precio
            return precio

        ahora = monotonic()
        toca_nodo = (ahora - self._ultima_rpc.get(mint, 0.0)) >= self.price_check_interval
        if not toca_nodo:
            precio = precio_en_vivo(mint, self.precio_stream_seg)
            if precio is not None:
                return precio
            ultimo = self._ultimo_precio.get(mint)
            if ultimo is not None:
                return ultimo
        precio = await curve_manager.calculate_price(pool_address)
        self._ultima_rpc[mint] = monotonic()
        self._ultimo_precio[mint] = precio
        return precio

    def _tick_vigilancia(self) -> float:
        """Cada cuanto da una vuelta el monitor de salida.

        Con el precio del stream encendido la vuelta es corta a proposito: es
        lo que hace que un take profit se cobre en la operacion y no hasta un
        segundo despues. No cuesta peticiones al nodo -- `_precio_rapido` lo
        consulta a su ritmo pase lo que pase.
        """
        if self.precio_stream_seg > 0 and self.tick_stream_seg > 0:
            return min(self.price_check_interval, self.tick_stream_seg)
        return self.price_check_interval

    async def _monitor_tiered_exit(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Monitor a position and sell in tiers, holding any moonbag.

        Take-profit tiers fire as their targets are reached (partial sells);
        stop-loss dumps everything still held; an optional max_hold_time is a
        time backstop. Whatever remains after all tiers fire is held.
        """
        entry_price = buy_result.price
        original_qty = buy_result.amount
        # TP adaptativo: si el dev suele llegar MUY alto (moonshot >$120K), tiers
        # agresivos (objetivos altos + moonbag grande) para pillar el pump.
        _dev = str(token_info.user)
        _bestpeak = self._creator_bestpeak.get(_dev, 0)
        if _bestpeak >= 120000:
            tiers = self.tp_tiers_aggressive
            logger.info(
                f"{token_info.symbol}: dev MOONSHOT (pico histórico "
                f"${int(_bestpeak):,}) -> TP AGRESIVO (+100/300/900%, moonbag 40%)"
            )
        elif self.tp_tiers_marcados and self._es_marcado(token_info):
            tiers = self.tp_tiers_marcados
            logger.info(
                f"{token_info.symbol}: DEV MARCADO -> salida con calma "
                f"{[(f'+{t.gain * 100:.0f}%', f'{t.sell * 100:.0f}%') for t in tiers]}"
            )
        else:
            tiers = self.tp_tiers
        # ¿Es de un creador bueno? Se decide con la tasa que se guardo al
        # comprobar el dev, no volviendo a preguntar: la API tarda y aqui ya no
        # hay prisa que valga -- si fallara la consulta se caeria al trato
        # general sin avisar, que es peor que no tenerlo.
        _tasa = self._tasa_dev.get(str(token_info.mint), 0.0)
        # Dos formas de ser elite: por tasa de migracion o por soler pumpear
        # alto. La segunda se marco al detectar la moneda (`_mints_elite`),
        # con el historial del creador recien traido de la API.
        _en_lista = self._en_lista_elite(token_info)
        _elite = bool(self.dev_elite) and (
            _tasa >= float(self.dev_elite.get("min_rate", 1.0))
            or str(token_info.mint) in self._mints_elite
            or _en_lista
        )
        if _elite:
            # Escalera completa, no solo el primer escalon: para un creador con
            # historial se deja correr con pasos geometricos --cada uno un 50%
            # por encima del anterior-- en vez de cerrar pronto.
            _pares = [(
                self.dev_elite.get("take_profit_percentage"),
                self.dev_elite.get("take_profit_sell_percentage", 1.0),
            )]
            for _n in (2, 3, 4):
                _pares.append((self.dev_elite.get(f"tp{_n}_gain"),
                               self.dev_elite.get(f"tp{_n}_sell")))
            _t = build_tiers(_pares)
            if _t:
                tiers = _t
            logger.info(
                f"{token_info.symbol}: CREADOR BUENO "
                f"({'en la lista de 30k+' if _en_lista else f'{_tasa * 100:.0f}% migrado'}) "
                f"-> stop {float(self.dev_elite.get('stop_loss_percentage', 0)) * 100:.0f}% "
                f"y escalera propia"
            )

        # ENTRO POR LA PUERTA DE MEDIANA: sin escalera propia se vendia con la
        # de siempre (+35%), pensada para un dev que apenas roza el filtro
        # normal. Aqui el dev YA ha demostrado una mediana de 20K+ en sus
        # migradas -- vender casi todo al primer +35% es regalar el recorrido
        # que precisamente esta puerta busca. Sin dato propio de ESTE grupo
        # todavia (puerta nueva), se reusa +75% (x1,75): es el mejor numero
        # medido en el backtest general de 1.016 monedas sin filtrar por dev,
        # y este grupo se parece mas a esa poblacion general que al filtro de
        # 25%/2 migraciones que SI necesito el +35% para compensar su bajo
        # acierto. No manda sobre un creador elite: si las dos puertas
        # coinciden, gana `dev_elite`.
        _mediana = (
            not _elite
            and bool(self.dev_mediana.get("take_profit_percentage"))
            and str(token_info.mint) in self._mints_mediana
        )
        if _mediana:
            _pares = [(
                self.dev_mediana.get("take_profit_percentage"),
                self.dev_mediana.get("take_profit_sell_percentage", 1.0),
            )]
            for _n in (2, 3, 4, 5):
                _pares.append((self.dev_mediana.get(f"tp{_n}_gain"),
                               self.dev_mediana.get(f"tp{_n}_sell")))
            _t = build_tiers(_pares)
            if _t:
                tiers = _t
            logger.info(
                f"{token_info.symbol}: ENTRADA POR MEDIANA -> stop "
                f"{float(self.dev_mediana.get('stop_loss_percentage', 0)) * 100:.0f}% "
                f"y escalera propia"
            )

        executed = [False] * len(tiers)
        remaining = original_qty
        # Sin stop propio para los elite se cae al de siempre. Antes caia a 0,
        # que NO es "sin cambios": es quedarse sin stop-loss ninguno.
        _sl_elite = float(self.dev_elite.get("stop_loss_percentage", 0) or 0)
        _sl_mediana = float(self.dev_mediana.get("stop_loss_percentage", 0) or 0)
        if _elite and _sl_elite > 0:
            _sl_pct = _sl_elite
        elif _mediana and _sl_mediana > 0:
            _sl_pct = _sl_mediana
        else:
            _sl_pct = self.stop_loss_percentage
        sl_price = entry_price * (1 - _sl_pct) if _sl_pct else None
        # STOP POR MC. El porcentual mide desde TU entrada; este mide en
        # absoluto ("si baja de $X, fuera"), que es como esta pensada la
        # estrategia de rebote -- todo en market cap. Con los dos puestos
        # manda el que salte antes: cayendo el precio, el mas alto.
        _mc_sl = float(self.mc_stop_loss or 0)
        if _mc_sl > 0:
            _mc_sl_price = _mc_sl / (_PUMP_TOTAL_SUPPLY * _SOL_USD)
            sl_price = (
                _mc_sl_price if sl_price is None
                else max(sl_price, _mc_sl_price)
            )
        moonbag = moonbag_fraction(tiers)
        _final_mc = (
            float(self.dev_elite.get("final_target_mc") or 0) if _elite
            else self.final_target_mc
        )
        target_price = (
            _final_mc / (_PUMP_TOTAL_SUPPLY * _SOL_USD)
            if _final_mc > 0 else None
        )
        mc_tiers = self.mc_take_profit_tiers
        if _elite and self.dev_elite.get("mc_take_profit_tiers"):
            mc_tiers = []
            for _it in self.dev_elite["mc_take_profit_tiers"]:
                try:
                    _mc, _v = float(_it.get("target_mc") or 0), float(_it.get("sell") or 0)
                except (TypeError, ValueError):
                    continue
                if _mc > 0 and 0 < _v <= 1:
                    mc_tiers.append((_mc, _v))
            mc_tiers.sort(key=lambda x: x[0])
        mc_executed = [False] * len(mc_tiers)
        # EL MOONBAG TIENE QUE CONTAR LOS ESCALONES DE MARKET CAP. `moonbag_fraction`
        # solo mira los escalones de porcentaje, asi que con una escalera de un solo
        # tramo (+50% vende 50%) daba un moonbag del 50% aunque el resto estuviera
        # asignado a escalones de MC. El 18-ago WIW salto el stop en el precio de
        # entrada y dejo MEDIA POSICION sin vender: cobrado -0,0220 SOL y 0,0720
        # colgando en una moneda que ya habia vuelto a la entrada. Lo que se guarda
        # al saltar el stop debe ser lo que de verdad sobra despues de TODOS los
        # escalones, no lo que sobra de los de porcentaje.
        if mc_tiers:
            moonbag = max(0.0, moonbag - sum(_frac for _mc, _frac in mc_tiers))
        #: Suelo armado por el trinquete. Solo sube, nunca baja.
        suelo_mc = 0.0
        had_tp = False  # ¿disparó algún TP? (para el auto-baneo de devs)
        hit_sl = False  # ¿saltó el stop-loss sin TP previo? = rug
        logger.info(
            f"Tiered exit active for {token_info.symbol}: "
            f"tiers={[(f'+{t.gain*100:.0f}%', f'{t.sell*100:.0f}%') for t in tiers]} "
            f"SL={self.stop_loss_percentage} moonbag={moonbag*100:.0f}%"
        )

        # Footgun guard: with no tiers, no stop-loss and no time backstop there
        # is no exit condition — don't spin forever.
        if not tiers and sl_price is None and not self.max_hold_time:
            logger.warning(
                "tp_sl exit for %s has no TP tiers, no stop-loss and no "
                "max_hold_time — nothing to monitor; holding.",
                token_info.symbol,
            )
            return

        pool_address = self._get_pool_address(token_info)
        curve_manager = self.platform_implementations.curve_manager
        start = monotonic()
        position_start_wall = datetime.utcnow().timestamp()
        fully_exited = False
        # C2: mark this position as open so a crash mid-monitor is detected on
        # restart (cleared right after the loop ends, whatever the outcome).
        self._record_open_position(token_info, buy_result)
        # A1 fix: absolute ceiling so an unsellable token (rug/migrated, curve AND
        # Jupiter sells both failing) can NEVER freeze the bot. The position
        # semaphore is held for this whole monitor, so an infinite sell-retry loop
        # would halt ALL new trades. After this cap we abandon the (already lost)
        # bag, release the semaphore, and keep operating.
        # NO depende de `max_hold_time`: con el reloj quitado (0), la vieja
        # formula `(max_hold_time or 120) * 3 + 120` daba 480 segundos y el
        # bot soltaba cada posicion a los 8 minutos.
        abandon_after = self.abandonar_tras_seg

        # ANTI PUMP&DUMP: pico observado para el trailing stop del moonbag.
        # El SL normal mide desde la ENTRADA, así que un pump+dump que vuelve
        # por encima de la entrada nunca lo dispara y el moonbag cae a cero.
        # El trailing mide desde el PICO: si cae stop_loss_percentage desde ahí,
        # se vende TODO (moonbag incluido).
        peak_price = entry_price
        # Auto-baneo por P&L NETO: SOL cobrado en ventas vs coste de entrada.
        realized_sol = 0.0
        #: Lecturas seguidas en las que la bolsa valia menos del
        #: minimo. Hacen falta dos para soltarla: ver mas abajo.
        _polvo_seguidas = 0
        cost_sol = original_qty * entry_price
        last_price = entry_price
        _muerta_revisada = False
        _sin_precio = 0  # lecturas seguidas sin precio (ver MAX_SIN_PRECIO)
        _bajo_desde = None  # desde cuando esta por debajo de la compra
        # Si la moneda ya no vive en la curva. Arranca en True cuando se
        # compro ya migrada (por el pool), asi no se le pregunta a una curva
        # vacia ni la primera vez.
        _ya_migro = bool((token_info.additional_data or {}).get("migrated"))

        while remaining > 1e-9:
            if (monotonic() - start) >= abandon_after:
                logger.error(
                    "ABANDONO %s: invendible tras %ss; se suelta el semáforo y el "
                    "bot sigue operando. Bolsa huérfana en la wallet.",
                    token_info.symbol,
                    int(abandon_after),
                )
                try:
                    from monitoring.notifier import notify

                    notify(
                        f"⚠️ ABANDONO · <b>{token_info.symbol}</b>\n"
                        f"No se pudo vender (rug/invendible). El bot sigue operando."
                    )
                except Exception:  # noqa: BLE001
                    pass
                break
            try:
                try:
                    if _ya_migro:
                        # YA SABEMOS QUE MIGRO: no se vuelve a preguntar a la
                        # curva. Preguntarlo igual costaba una llamada al nodo
                        # y un traceback completo en el log CADA SEGUNDO y por
                        # cada posicion migrada -- con el precio comprobandose
                        # cada segundo, eso llenaba el disco del droplet y se
                        # comia el presupuesto de RPC que necesita el stop
                        # loss. El precio de una migrada vive en el DEX.
                        raise _CurvaMigrada
                    price = await self._precio_rapido(
                        str(token_info.mint), pool_address, curve_manager
                    )
                    _sin_precio = 0
                except Exception:
                    # La curva ya no se puede leer: casi siempre porque la
                    # moneda migro. Se pregunta al DEX antes de darse por
                    # vencido, que es donde vive el precio a partir de ahi.
                    price = await self._precio_dex(str(token_info.mint))
                    if price is None:
                        _sin_precio += 1
                        if _sin_precio == 1 or _sin_precio % 20 == 0:
                            logger.warning(
                                "%s: sin precio ni en la curva ni en el DEX "
                                "(%d seguidos)", token_info.symbol, _sin_precio,
                            )
                        if _sin_precio >= self.MAX_SIN_PRECIO:
                            logger.error(
                                "ABANDONO %s: %d lecturas seguidas sin precio. "
                                "Se suelta el hueco para que el bot siga "
                                "comprando; la bolsa queda en la wallet.",
                                token_info.symbol, _sin_precio,
                            )
                            self._record_moonbag(
                                token_info, remaining, entry_price, pool_address
                            )
                            break
                        await asyncio.sleep(self.price_check_interval)
                        continue
                    if not _ya_migro:
                        # Se apunta UNA vez: a partir de aqui el precio sale
                        # del DEX directamente, sin pasar por la curva. Y se
                        # avisa UNA vez, que es lo que hace falta saber.
                        #
                        # Antes esta linea salia CADA SEGUNDO mientras durara
                        # la posicion. La condicion era `_sin_precio == 0`, y
                        # `_sin_precio` se pone a 0 justo debajo y solo sube
                        # cuando NO hay precio: en una migrada con precio en
                        # el DEX valia siempre cero, asi que "avisar una vez"
                        # eran 3.600 lineas por hora y posicion. En un droplet
                        # de 1 GB eso llena el disco sin que nadie lo note.
                        _ya_migro = True
                        logger.info(
                            "%s: la curva ya no sirve (migró); sigo con el "
                            "precio del DEX: %.10f SOL",
                            token_info.symbol, price,
                        )
                    elif _sin_precio:
                        # Vuelve a haber precio despues de un apagon. Esto si
                        # merece una linea: cierra el aviso de arriba.
                        logger.info(
                            "%s: vuelve a haber precio en el DEX tras %d "
                            "lecturas en blanco",
                            token_info.symbol, _sin_precio,
                        )
                    _sin_precio = 0

                # POLVO: lo que queda ya no vale la pena gestionar. Se suelta
                # la plaza y se deja de mirar el precio.
                #
                # No es limpieza: VENDER cuesta comision de prioridad mas la
                # renta de la cuenta de token, asi que por debajo de un dolar
                # la venta se lleva mas de lo que recupera -- el stop loss y
                # los take profit no tienen nada que defender ahi. Mientras
                # tanto esa bolsa ocupaba una de las 5 plazas y gastaba una
                # consulta de precio POR SEGUNDO, que es lo que necesitan las
                # posiciones que si valen algo.
                #
                # La bolsa NO se vende ni se pierde: se queda en la wallet y
                # la recoge la limpieza de cuentas al cerrar la sesion.
                # DOS GUARDAS, y las dos por el mismo motivo: esto hace
                # `break`, o sea que deja la posicion SIN VIGILAR para siempre
                # -- sin stop loss. Un error aqui no se puede deshacer.
                #
                # 1. Un precio 0 no es "no vale nada". `calculate_price`
                #    devuelve 0.0 cuando las reservas virtuales estan a cero en
                #    vez de lanzar, asi que UNA sola lectura mala abandonaba la
                #    posicion entera.
                # 2. Hace falta que se cumpla dos veces seguidas. Una lectura
                #    suelta no decide si se suelta una posicion.
                if self.valor_minimo_posicion_usd > 0 and price > 0:
                    _valor_usd = remaining * price * _SOL_USD
                    if _valor_usd >= self.valor_minimo_posicion_usd:
                        _polvo_seguidas = 0
                    else:
                        _polvo_seguidas += 1
                    if (
                        _valor_usd < self.valor_minimo_posicion_usd
                        and _polvo_seguidas >= 2
                    ):
                        logger.info(
                            "%s: lo que queda vale $%.2f (mínimo $%.2f). Se "
                            "suelta la plaza y se deja de vigilar: venderlo "
                            "costaría más en comisiones de lo que vale. La "
                            "bolsa se queda en la wallet.",
                            token_info.symbol,
                            _valor_usd,
                            self.valor_minimo_posicion_usd,
                        )
                        break

                # The final remainder has an absolute market-cap target.  It is
                # only armed after every percentage TP has executed.
                if (
                    target_price is not None
                    and price >= target_price
                    and remaining > 1e-9
                ):
                    if await self._sell_amount(token_info, remaining, price, "final_target_mc"):
                        realized_sol += self._ultimo_cobro
                        remaining = 0.0
                        fully_exited = True
                        break

                # Absolute market-cap ladder, armed only after the percentage
                # TP ladder. Sell fractions are relative to the original buy.
                if tiers and all(executed) and mc_tiers:
                    current_mc = price * _PUMP_TOTAL_SUPPLY * _SOL_USD
                    for i, (target_mc, sell_fraction) in enumerate(mc_tiers):
                        if mc_executed[i] or current_mc < target_mc:
                            continue
                        amount = min(original_qty * sell_fraction, remaining)
                        if amount <= 1e-9:
                            mc_executed[i] = True
                            continue
                        if await self._sell_amount(
                            token_info, amount, price, f"mc_tier_${target_mc:.0f}"
                        ):
                            realized_sol += self._ultimo_cobro
                            remaining -= amount
                            mc_executed[i] = True
                    if all(mc_executed):
                        if remaining > 1e-9:
                            self._record_moonbag(
                                token_info, remaining, entry_price, pool_address
                            )
                        break
                    # NO se hace `continue` aqui, y es importante. Antes se
                    # hacia, y eso significaba que en cuanto saltaba el primer
                    # take-profit el bucle entraba en esta rama en cada vuelta y
                    # se saltaba TODO lo de abajo: anti-rug, trinquete y
                    # stop-loss. Con la configuracion actual dejaba muerta la
                    # proteccion principal -- el stop que sube al precio de
                    # entrada tras el primer TP no se habria disparado nunca.
                    #
                    # Ahora se sigue el bucle: los escalones de market cap se
                    # atienden si toca y las protecciones se comprueban igual.

                # ANTI-RUG: salida de emergencia por presion vendedora.
                #
                # El stop-loss reacciona al PRECIO, que es la consecuencia; para
                # cuando el precio ha caido un 35% el volcado ya ha pasado y solo
                # queda lo que nadie quiso. Esto mira la CAUSA -- quien esta
                # vendiendo ahora mismo -- leyendo los TradeEvent de la cadena, y
                # sale antes de que el precio termine de desplomarse.
                #
                # Dispara cuando en los ultimos segundos las ventas superan a las
                # compras por el ratio configurado Y hay un volumen minimo, para
                # no salir corriendo por una venta suelta de dos SOL.
                if self.anti_rug_ratio > 0:
                    try:
                        from monitoring.volumen import presion

                        _c, _v = presion(str(token_info.mint), self.anti_rug_seg)
                        if (
                            _v >= self.anti_rug_min_sol
                            and _v >= _c * self.anti_rug_ratio
                        ):
                            logger.warning(
                                "ANTI-RUG %s: %.1f SOL vendidos contra %.1f "
                                "comprados en %.0fs -> salgo YA",
                                token_info.symbol, _v, _c, self.anti_rug_seg,
                            )
                            try:
                                from monitoring.notifier import notify

                                notify(
                                    f"🚨 <b>ANTI-RUG</b> · {token_info.symbol}\n"
                                    f"{_v:.1f} SOL vendidos contra {_c:.1f} "
                                    f"comprados en {self.anti_rug_seg:.0f}s\n"
                                    "Saliendo de la posición entera."
                                )
                            except Exception:  # noqa: BLE001
                                pass
                            # EL MOTIVO ES OBLIGATORIO, y aqui faltaba.
                            #
                            # `_sell_amount` pide cuatro datos y se le pasaban
                            # tres, asi que esta llamada lanzaba TypeError
                            # SIEMPRE. Se la tragaba el `except Exception` del
                            # final del bucle, que lo apuntaba en el log y
                            # seguia. Resultado: el aviso de Telegram de aqui
                            # arriba salia, decia "saliendo de la posicion
                            # entera", y no se vendia nada -- y como la moneda
                            # seguia cayendo, volvia a avisar y a reventar en
                            # cada vuelta. La salida de emergencia no ha
                            # vendido nunca.
                            if await self._sell_amount(
                                token_info, remaining, price, "anti_rug"
                            ):
                                realized_sol += self._ultimo_cobro
                                remaining = 0.0
                                hit_sl = True
                                break
                    except Exception:  # noqa: BLE001
                        pass

                # ANTI PUMP&DUMP — trailing stop desde el pico. Solo se arma tras
                # subir >=20% (para no chocar con el SL de entrada). Si cae
                # stop_loss_percentage desde el máximo, se vende TODO lo que queda
                # (incluido el moonbag) en vez de dejarlo caer a cero.
                peak_price = max(peak_price, price)
                # Se apunta para que la VENTA pueda registrarlo. Hasta ahora
                # el pico solo vivia en esta variable y se perdia al cerrar
                # la posicion, asi que despues no habia forma de saber hasta
                # donde habia llegado una moneda ni si se vendio pronto.
                self._pico_visto[str(token_info.mint)] = peak_price
                last_price = price

                # TRINQUETE POR MARKET CAP. Va ANTES del trailing porcentual a
                # proposito: es mas apretado en las monedas grandes y tiene que
                # poder dispararse antes. En cuanto el market cap toca un
                # escalon queda armado un suelo, y ese suelo no baja nunca --
                # aunque la moneda se desplome, el suelo se queda donde estaba.
                if self.suelos_por_mc and remaining > 1e-9:
                    _mc_ahora = price * _PUMP_TOTAL_SUPPLY * _SOL_USD
                    for _alcanza, _suelo in self.suelos_por_mc:
                        if _mc_ahora >= _alcanza and _suelo > suelo_mc:
                            suelo_mc = _suelo
                            logger.info(
                                f"SUELO ARMADO {token_info.symbol}: toco "
                                f"${_mc_ahora:,.0f} -> no puede bajar de "
                                f"${_suelo:,.0f}"
                            )
                    if suelo_mc > 0 and _mc_ahora < suelo_mc:
                        logger.info(
                            f"SUELO ROTO {token_info.symbol}: ${_mc_ahora:,.0f} "
                            f"< ${suelo_mc:,.0f} -> vendo TODO (ganancia "
                            f"asegurada)"
                        )
                        if await self._sell_amount(
                            token_info, remaining, price, "suelo_por_mc"
                        ):
                            realized_sol += self._ultimo_cobro
                            remaining = 0.0
                            fully_exited = True
                            break
                        await asyncio.sleep(self.price_check_interval)
                        continue

                if (
                    self.trailing_stop_percentage
                    and peak_price >= entry_price * 1.2
                    and price <= peak_price * (1 - self.trailing_stop_percentage)
                ):
                    logger.info(
                        f"TRAILING STOP {token_info.symbol}: {price:.8f} SOL <= pico "
                        f"{peak_price:.8f} -{self.trailing_stop_percentage * 100:.0f}% "
                        f"-> vendo TODO (anti pump&dump, moonbag incluido)"
                    )
                    if await self._sell_amount(
                        token_info, remaining, price, "trailing_stop"
                    ):
                        realized_sol += self._ultimo_cobro
                        remaining = 0.0
                        fully_exited = True
                        break
                    await asyncio.sleep(self.price_check_interval)
                    continue

                # Panel de Telegram: publica PnL en vivo y atiende ventas manuales.
                self._write_live_status(
                    token_info, entry_price, remaining, price, original_qty
                )
                # SOLO SE SUMA SI DE VERDAD SE VENDIO. `_check_manual_sell`
                # vuelve sin tocar `_ultimo_cobro` cuando no hay orden manual
                # --que es casi siempre--, asi que sumar a ciegas repetia el
                # cobro de la ULTIMA venta buena cuatro veces por segundo
                # durante el resto de la vida de la posicion. La linea del P&L
                # es con la que se juzga si la estrategia gana dinero.
                #
                # La variable ya estaba escrita y sin usar: la intencion era
                # esta.
                _before_manual = remaining
                remaining = await self._check_manual_sell(token_info, remaining, price)
                if remaining < _before_manual - 1e-12:
                    realized_sol += self._ultimo_cobro
                if remaining <= 1e-9:
                    fully_exited = True
                    break

                # Panic sell (dashboard "vende todo"): dump if the flag was set
                # after this position opened.
                pt = panic_time()
                if pt is not None and pt >= position_start_wall:
                    logger.info("PANIC SELL for %s — dumping remaining", token_info.symbol)
                    if await self._sell_amount(token_info, remaining, price, "panic_sell"):
                        realized_sol += self._ultimo_cobro
                        remaining = 0.0
                        fully_exited = True
                        break
                    await asyncio.sleep(self.price_check_interval)
                    continue

                # Stop loss: vende hasta el MOONBAG (lo que quede sin cubrir por
                # los peldanos; hoy el 2%, pero sale de la config, no de aqui),
                # priced at the LIVE price. El moonbag NUNCA se auto-vende, ni en
                # stop-loss: se registra para avisar por Telegram y lo vende el usuario.
                # MAS DE UN MINUTO POR DEBAJO DE LA COMPRA -> FUERA ENTERA.
                # El suelo estructural son 2.097 $ y la entrada esta en 2.800:
                # una moneda que al minuto sigue por debajo de lo que pagamos
                # no ha encontrado comprador, y lo unico que queda por delante
                # es la caida hasta ese suelo. El stop del 80% esta tan abajo
                # que no la saca; esto si. Se lleva TAMBIEN el moonbag: dejar
                # un resto corriendo en una moneda que nadie quiere es como se
                # acumularon las bolsas huerfanas.
                if self.venta_bajo_entrada_seg and remaining > 1e-9:
                    if price < entry_price:
                        if _bajo_desde is None:
                            _bajo_desde = monotonic()
                        elif (monotonic() - _bajo_desde) >= self.venta_bajo_entrada_seg:
                            logger.warning(
                                "%s: %ds seguidos por debajo del precio de compra "
                                "(%.10f < %.10f). Se vende ENTERA, moonbag incluido.",
                                token_info.symbol,
                                int(self.venta_bajo_entrada_seg), price, entry_price,
                            )
                            if await self._sell_amount(
                                token_info, remaining, price, "bajo_entrada"
                            ):
                                realized_sol += self._ultimo_cobro
                                remaining = 0.0
                                fully_exited = True
                                break
                            # Si no se pudo vender se reintenta a la vuelta
                            # siguiente en vez de dar la posicion por cerrada.
                            await asyncio.sleep(self.price_check_interval)
                            continue
                    else:
                        # Vuelve a estar por encima: el contador se reinicia.
                        _bajo_desde = None

                if sl_price is not None and price <= sl_price:
                    hit_sl = True  # rug si no hubo TP antes
                    # Con `vender_todo_en_stop` el stop se lleva la posicion
                    # entera. Hace falta en las estrategias de un solo escalon:
                    # ahi el "moonbag" es todo lo que no cubre ese escalon (en
                    # CORREDORES, el 50%), y dejarlo puesto significa que un
                    # stop vende media posicion y abandona la otra media en una
                    # moneda que se esta hundiendo. El moonbag tiene sentido
                    # cuando lo gestiona el trailing, no cuando salta el stop.
                    keep = 0.0 if self.vender_todo_en_stop else original_qty * moonbag
                    to_sell = remaining - keep
                    logger.info(
                        f"STOP LOSS for {token_info.symbol} at {price:.8f} SOL "
                        f"(<= {sl_price:.8f}) — "
                        + (
                            "vendo TODO"
                            if self.vender_todo_en_stop
                            else f"dejo moonbag {moonbag * 100:.0f}%"
                        )
                    )
                    if to_sell <= 1e-9:
                        # ya estamos en el moonbag: no se vende, se deja y se avisa
                        self._record_moonbag(
                            token_info, remaining, entry_price, pool_address
                        )
                        break
                    if await self._sell_amount(
                        token_info, to_sell, price, "stop_loss"
                    ):
                        realized_sol += self._ultimo_cobro
                        remaining -= to_sell
                        if remaining > 1e-9:
                            self._record_moonbag(
                                token_info, remaining, entry_price, pool_address
                            )
                        else:
                            fully_exited = True  # sin moonbag configurado
                        break
                    logger.warning(
                        "Stop-loss sell failed for %s; retrying", token_info.symbol
                    )
                    await asyncio.sleep(self.price_check_interval)
                    continue

                # Take-profit tiers that are now due (priced at the LIVE price).
                for i in tiers_due(entry_price, price, tiers, executed):
                    amount = min(original_qty * tiers[i].sell, remaining)
                    reason = f"tp_tier{i + 1}_+{tiers[i].gain * 100:.0f}%"
                    # UN ESCALON QUE NO VENDE SIGUE CONTANDO. El TP1 esta
                    # puesto a 0%: no cobra nada, pero tocarlo sube el stop y
                    # eso es TODO su trabajo. Antes este caso salia por un
                    # `continue` que se saltaba la subida del suelo, o sea que
                    # el escalon no hacia absolutamente nada.
                    _vendido = True
                    if amount > 1e-9:
                        _vendido = await self._sell_amount(
                            token_info, amount, price, reason
                        )
                        if _vendido:
                            realized_sol += self._ultimo_cobro
                            remaining -= amount
                    if _vendido:
                        executed[i] = True
                        # Llegar al objetivo cuenta como pump AUNQUE no se
                        # venda: alimenta la clasificacion rug/win del dev, y
                        # la moneda entrego igual.
                        had_tp = True  # el dev entregó un pump -> no es rug
                        # STOP A LA ENTRADA. En cuanto cobra el primer escalon,
                        # lo que sigue dentro deja de poder acabar en perdida:
                        # si el precio vuelve a lo que se pago, se cierra.
                        # TRINQUETE ESCALON A ESCALON. La regla de Manu: al
                        # cobrar el PRIMER take-profit el suelo pasa al precio
                        # de compra, y al cobrar el SEGUNDO pasa al primer
                        # take-profit, y asi sucesivamente.
                        #
                        # Antes esto estaba mal de dos maneras: con solo
                        # `stop_a_entrada_tras_tp` el suelo se quedaba en la
                        # entrada para siempre --cobrabas el x3 y el suelo
                        # seguia en el precio de compra--, y el flag
                        # `stop_al_tp_tras_tp` usaba `tiers[i]`, el escalon que
                        # ACABA de tocarse, con lo que el suelo quedaba pegado
                        # al precio actual y cualquier respiro normal cerraba
                        # la posicion.
                        #
                        # Lo correcto es el escalon ANTERIOR: deja sitio para
                        # que la moneda respire entre objetivos y aun asi cada
                        # cobro asegura lo ya ganado.
                        if self.stop_a_entrada_tras_tp or self.stop_al_tp_tras_tp:
                            if i == 0 and self.stop_al_tp_tras_tp:
                                # SL sube al precio del propio TP1 (no a entrada)
                                _suelo = entry_price * (1 + tiers[0].gain)
                            else:
                                _suelo = stepped_stop_price(
                                    entry_price, tiers, i,
                                    self.margen_stop_tras_tp,
                                )
                                if _suelo is None:
                                    continue
                            if sl_price is None or sl_price < _suelo:
                                sl_price = _suelo
                                _donde = (
                                    f"TP1 (+{tiers[0].gain * 100:.0f}%)" if i == 0 and self.stop_al_tp_tras_tp
                                    else (
                                        f"compra +{self.margen_stop_tras_tp * 100:.0f}%"
                                        if self.margen_stop_tras_tp > 0
                                        else "el PRECIO DE COMPRA"
                                    ) if i == 0
                                    else f"TP{i} (+{tiers[i - 1].gain * 100:.0f}%)"
                                )
                                logger.info(
                                    f"STOP SUBIDO {token_info.symbol}: cobrado "
                                    f"{reason}, el suelo pasa a {_donde} "
                                    f"({_suelo:.8f} SOL)"
                                )

                if remaining <= 1e-9:
                    fully_exited = True
                    break

                # Without a trailing or TP floor, the remainder becomes a
                # manually managed moonbag and monitoring can stop.
                if (
                    tiers
                    and all(executed)
                    and target_price is None
                    and not mc_tiers
                    and not self.trailing_stop_percentage
                    and not self.stop_al_tp_tras_tp
                ):
                    logger.info(
                        f"All TP tiers hit for {token_info.symbol} — "
                        f"holding moonbag {moonbag * 100:.0f}% "
                        f"({remaining:.6f} tokens)"
                    )
                    if remaining > 1e-9:
                        self._record_moonbag(
                            token_info, remaining, entry_price, pool_address
                        )
                    break

                # When an absolute final target is configured, the remaining
                # moonbag is protected from trailing/max-hold exits and stays
                # monitored until that target (or an explicit manual/panic sell).
                if tiers and all(executed) and target_price is not None:
                    await asyncio.sleep(self.price_check_interval)
                    continue

                # Time backstop: al llegar al tiempo SOLO se cierra si la posición
                # está EN NEGATIVO (precio < entrada). Si va en positivo (aunque
                # aún no llegue al TP1), se deja correr para darle opción a alcanzar
                # el take-profit. Al llegar a max_hold vendemos el grueso (dejando
                # el moonbag corriendo libre), EN VERDE O EN ROJO. Antes solo vendía
                # si estaba en rojo (price < entry): una posición en verde pero sin
                # llegar al TP1 no se vendía y acababa ABANDONADA sin vender al tocar
                # el tope de abandono (bolsa huérfana en ganancias). El moonbag sigue
                # libre igualmente.
                # MONEDA MUERTA. Distinto del tope de tiempo: aquel espera al
                # final por si arranca, y esto corta antes si a los N segundos
                # no ha hecho NADA. Sin esto, con `max_hold_time` largo (que
                # hace falta para que un ganador llegue al +400%) una moneda que
                # nace plana se queda ocupando la posicion media hora.
                #
                # Se mira UNA sola vez, al cumplirse el plazo: si en ese momento
                # va por encima del umbral se la deja correr y ya no se vuelve a
                # preguntar, porque a partir de ahi mandan los take-profit.
                if (
                    self.venta_muerta_seg > 0
                    and not _muerta_revisada
                    and (monotonic() - start) >= self.venta_muerta_seg
                ):
                    _sube = (price / entry_price - 1) if entry_price else 0.0
                    if _sube < self.venta_muerta_gain:
                        logger.info(
                            f"MUERTA {token_info.symbol}: {_sube * 100:+.0f}% a los "
                            f"{self.venta_muerta_seg:.0f}s (hace falta "
                            f"{self.venta_muerta_gain * 100:+.0f}%) -> se vende todo"
                        )
                        if await self._sell_amount(
                            token_info, remaining, price, "muerta"
                        ):
                            _muerta_revisada = True
                            realized_sol += self._ultimo_cobro
                            remaining = 0.0
                            fully_exited = True
                            break
                        logger.warning(
                            "Venta por muerta fallo en %s; lo reintenta el bucle",
                            token_info.symbol,
                        )
                    else:
                        _muerta_revisada = True
                        logger.info(
                            f"VIVA {token_info.symbol}: {_sube * 100:+.0f}% a los "
                            f"{self.venta_muerta_seg:.0f}s -> se deja correr"
                        )

                if (
                    self.max_hold_time
                    and (monotonic() - start) >= self.max_hold_time
                ):
                    keep = original_qty * moonbag  # moonbag: se mantiene sin tiempo
                    to_sell = remaining - keep
                    if to_sell > 1e-9:
                        logger.info(
                            f"Max hold {token_info.symbol}: vendo hasta el moonbag "
                            f"(dejo {moonbag * 100:.0f}% corriendo libre)"
                        )
                        if await self._sell_amount(
                            token_info, to_sell, price, "max_hold_time"
                        ):
                            realized_sol += self._ultimo_cobro
                            remaining -= to_sell
                        else:
                            logger.warning(
                                "Max-hold sell failed for %s; retrying",
                                token_info.symbol,
                            )
                            await asyncio.sleep(self.price_check_interval)
                            continue
                    if remaining <= 1e-9:
                        fully_exited = True  # sin moonbag configurado -> cierre total
                    else:
                        logger.info(
                            f"Moonbag {moonbag * 100:.0f}% de {token_info.symbol} "
                            f"corriendo LIBRE (aviso a Telegram cada 5 min)"
                        )
                        self._record_moonbag(
                            token_info, remaining, entry_price, pool_address
                        )
                    break

                await asyncio.sleep(self._tick_vigilancia())

            except (TypeError, AttributeError) as _bug:
                # UN FALLO DE PROGRAMACION NO PUEDE SER SILENCIOSO.
                #
                # Esta captura general trataba igual un fallo de red --que es
                # normal y pasa todos los dias-- y una llamada mal escrita
                # --que es SIEMPRE un bug--. La segunda clase se quedaba en una
                # linea del log que nadie lee desde el movil.
                #
                # Lo que costo: la salida de emergencia anti-rug llamaba a
                # `_sell_amount` con tres argumentos de los cuatro que pide.
                # Lanzaba TypeError en cada disparo, esto se lo tragaba, y la
                # posicion no se vendia NUNCA mientras el aviso de Telegram
                # decia que si. Estuvo asi sin que nadie lo viera.
                #
                # Se sigue capturando --matar el monitor dejaria la posicion
                # sin vigilancia, que es peor-- pero ahora avisa. Una vez por
                # posicion: en un bucle de cuatro vueltas por segundo, avisar
                # siempre seria inutilizable.
                logger.exception("BUG en el monitor de salida (no es red)")
                _mint_bug = str(token_info.mint)
                if _mint_bug not in self._bugs_avisados:
                    self._bugs_avisados.add(_mint_bug)
                    try:
                        from monitoring.notifier import notify

                        notify(
                            f"🐞 <b>FALLO DEL BOT</b> · <b>{token_info.symbol}</b>\n"
                            f"{type(_bug).__name__}: {_bug}\n"
                            "La vigilancia sigue, pero algo de esta posicion no "
                            "se esta ejecutando. Mandame esto.\n"
                            f"CA: <code>{_mint_bug}</code>"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(self.price_check_interval)
            except Exception:
                logger.exception("Error in tiered exit monitor")
                # Aqui NO se usa el tick corto: si algo esta fallando, dar
                # vueltas mas deprisa solo llena el log mas deprisa.
                await asyncio.sleep(self.price_check_interval)

        # Marca para la proxima compra: si cerro por SL, la siguiente va reducida.
        if hit_sl and self.buy_amount_tras_sl > 0:
            self._ultimo_cierre_fue_sl = True

        # Auto-baneo de devs por P&L NETO real (no por "tocó un TP"): SOL cobrado
        # + valor del moonbag aún en la wallet, menos el coste de entrada. Así el
        # pump&dumper que clipa un TP1 y luego tira SÍ cuenta como pérdida (rug),
        # y 2 pérdidas acumuladas del mismo dev -> se le bloquea.
        held_val = remaining * last_price  # moonbag que sigue en la wallet
        net_sol = realized_sol + held_val - cost_sol
        # Una perdida >=50% cuenta como rug grave y aplica el mismo umbral
        # configurado para bloquear al dev.
        _big_loss = cost_sol > 0 and net_sol <= -0.50 * cost_sol
        if _big_loss:
            _outcome = "hardrug"
        elif net_sol > cost_sol * 0.05:  # ganó neto (>+5%) -> limpia y desbloquea
            _outcome = "win"
        elif net_sol < -cost_sol * 0.15:  # perdió neto (>-15%) -> strike
            _outcome = "rug"
        else:
            _outcome = "neutral"
        logger.info(
            f"{token_info.symbol}: P&L neto {net_sol:+.4f} SOL (cobrado "
            f"{realized_sol:.4f} + moonbag {held_val:.4f} - coste {cost_sol:.4f}) "
            f"-> {_outcome} [rug-streak dev]"
        )

        # EL NUMERO QUE MANDA. El de arriba es `tokens x precio observado` y no
        # descuenta comision de pump.fun, impacto de la venta ni slippage; el
        # 9-ago sumaba +0,16 SOL mientras la cartera perdia 0,75. Este lee el
        # movimiento real de la wallet en la cadena.
        try:
            _real = await self._pnl_real(str(token_info.mint))
        except Exception:  # noqa: BLE001
            _real = None
        if _real is not None:
            _dif = _real - net_sol
            logger.info(
                "%s: P&L REAL %+.4f SOL cobrado%s | teorico %+.4f (se equivocaba "
                "en %+.4f)",
                token_info.symbol,
                _real,
                (
                    f" + {held_val:.4f} en moonbag = {_real + held_val:+.4f}"
                    if held_val > 1e-9
                    else ""
                ),
                net_sol,
                _dif,
            )
        else:
            logger.warning(
                "%s: no pude leer el P&L real de la cadena; solo hay el teorico",
                token_info.symbol,
            )

        # RACHA DE PERDIDAS DE LA ESTRATEGIA. Se mide con el P&L REAL cuando se
        # ha podido leer de la cadena, y solo si no, con el teorico: parar un
        # bot por una perdida que en realidad fue ganancia (o al reves) seria
        # exactamente el error que ya costo dinero el 9-ago.
        # AL P&L REAL HAY QUE SUMARLE EL MOONBAG. `_real` mide el SOL que entra
        # y sale de la cartera, y los tokens que siguen dentro no son SOL: una
        # operacion que cobra su objetivo y deja el 20% corriendo aparecia como
        # PERDIDA. Paso el 10-ago con Crocodile y Terry, que cobraron el +25% y
        # aun asi sumaron dos strikes de la racha; con el tercero la estrategia
        # se paro sola sin motivo real.
        _para_racha = (_real + held_val) if _real is not None else net_sol

        # BOTE COMUN, FUERA DEL CONTADOR DE RACHA. Estaba metido dentro del
        # `if max_perdidas_seguidas > 0` y ninguno de los tres yaml define esa
        # clave, asi que valia 0 y el bote NUNCA se alimentaba: el 19-ago se
        # cerraron 12 operaciones y `ganancia_real.json` seguia sin existir.
        # El tamano de la compra no puede depender de que este configurado un
        # freno que no tiene nada que ver.
        if self._bote is not None:
            try:
                _total = self._bote.suma(_para_racha)
                logger.info(
                    "BOTE: %+.4f SOL en esta operacion, %+.4f acumulados",
                    _para_racha, _total,
                )
            except Exception:  # noqa: BLE001
                logger.warning("bote: no se pudo apuntar la operacion")

        # SELECTOR. Se le pasa este mismo numero y no el teorico: el teorico ha
        # llegado a equivocarse de signo, y un selector alimentado con el
        # elegiria la estrategia que peor va.
        if self._selector is not None:
            try:
                self._selector.apunta(
                    self.nombre_estrategia, _para_racha, self.buy_amount
                )
            except Exception:  # noqa: BLE001
                logger.warning("selector: no se pudo apuntar la operacion")

        self._apunta_cierre_otro_origen(token_info, _para_racha)
        self._apunta_cierre_curva96(token_info, _para_racha)

        if self.max_perdidas_seguidas > 0 and not self._parado_por_perdidas:
            if _para_racha < 0:
                self._perdidas_seguidas += 1
                logger.warning(
                    "%s: perdida %d de %d seguidas (%+.4f SOL)",
                    token_info.symbol,
                    self._perdidas_seguidas,
                    self.max_perdidas_seguidas,
                    _para_racha,
                )
                if self._perdidas_seguidas >= self.max_perdidas_seguidas:
                    self._parado_por_perdidas = True
                    logger.error(
                        "ESTRATEGIA PARADA: %d perdidas seguidas. No se compra "
                        "mas; lo que quede abierto se sigue vendiendo.",
                        self._perdidas_seguidas,
                    )
                    try:
                        from monitoring.notifier import notify

                        _n = self._state_suffix.lstrip("_").replace(
                            "bot-sniper-", ""
                        ).upper()
                        notify(
                            f"PARADA - <b>{_n}</b>\n"
                            f"{self._perdidas_seguidas} perdidas seguidas.\n"
                            f"Deja de comprar. Lo abierto se sigue vendiendo.",
                            tipo="compra",
                        )
                    except Exception:  # noqa: BLE001
                        pass
            elif self._perdidas_seguidas:
                logger.info(
                    "%s: ganadora (%+.4f SOL) -> racha de perdidas a cero",
                    token_info.symbol,
                    _para_racha,
                )
                self._perdidas_seguidas = 0

        # RACHA DE LAS MAYHEM. Se mide con el mismo P&L neto de arriba: salir con
        # menos SOL de los que entraron es perder, y da igual que tocara un TP
        # por el camino. Una ganadora pone el contador a cero -- lo que se corta
        # es una mala racha, no el historico.
        if (
            self.mayhem_max_perdidas > 0
            and getattr(token_info, "is_mayhem_mode", False)
            and not self._mayhem_cerrado
        ):
            if net_sol < 0:
                self._mayhem_perdidas += 1
                logger.warning(
                    "MAYHEM perdida %d de %d seguidas (%s: %+.4f SOL)",
                    self._mayhem_perdidas,
                    self.mayhem_max_perdidas,
                    token_info.symbol,
                    net_sol,
                )
                if self._mayhem_perdidas >= self.mayhem_max_perdidas:
                    self._mayhem_cerrado = True
                    logger.error(
                        "MAYHEM CERRADAS: %d perdidas seguidas. No se compran mas "
                        "hasta reiniciar; las normales siguen operando.",
                        self._mayhem_perdidas,
                    )
                    try:
                        from monitoring.notifier import notify

                        notify(
                            f"⛔ <b>MAYHEM CERRADAS</b>\n"
                            f"{self._mayhem_perdidas} pérdidas seguidas.\n"
                            f"El bot sigue comprando las normales a "
                            f"{self.buy_amount} SOL."
                        )
                    except Exception:  # noqa: BLE001
                        pass
            elif self._mayhem_perdidas:
                logger.info(
                    "MAYHEM: ganadora (%s: %+.4f SOL) -> racha a cero",
                    token_info.symbol,
                    net_sol,
                )
                self._mayhem_perdidas = 0
        # Se anota a las DOS identidades. Si solo se apuntara a `user`, un dev
        # que firma con carteras nuevas pero deja el mismo `creator` acumularia
        # un strike suelto en cada una y nunca llegaria a dos.
        # 6-nov: y a la cartera que la minteo; nunca a una cuenta de programa
        # (ver `_identidades_del_dev`).
        _ident = self._identidades_del_dev(token_info)
        for _d in _ident:
            if _d:
                self._bump_dev_rug(_d, _outcome)

        # C2: monitor ended (sold / moonbag / abandoned) — no longer an actively
        # managed position, so clear the crash-recovery marker.
        self._clear_open_position(str(token_info.mint))

        # Close the token account only after a FULL exit (not a held moonbag).
        if fully_exited:
            try:
                await handle_cleanup_after_sell(
                    self.solana_client,
                    _ctx_wallet.get() or self.wallet,
                    token_info.mint,
                    token_info.token_program_id,
                    self.priority_fee_manager,
                    self.cleanup_mode,
                    self.cleanup_with_priority_fee,
                    self.cleanup_force_close_with_burn,
                )
            except Exception:
                logger.exception("Cleanup after tiered exit failed")

    async def _handle_time_based_exit(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle legacy time-based exit strategy.

        Args:
            token_info: Token information
            buy_result: Result from the buy operation (contains token amount)
        """
        logger.info(f"Waiting for {self.wait_time_after_buy} seconds before selling...")
        await asyncio.sleep(self.wait_time_after_buy)

        logger.info(f"Selling {token_info.symbol}...")
        # Pass token amount and price from buy result to avoid RPC delays
        sell_result: TradeResult = await (_ctx_seller.get() or self.seller).execute(
            token_info, token_amount=buy_result.amount, token_price=buy_result.price
        )

        if sell_result.success:
            logger.info(f"Successfully sold {token_info.symbol}")
            self._log_trade(
                "sell",
                token_info,
                sell_result.price,
                sell_result.amount,
                sell_result.tx_signature,
            )
            # Close ATA if enabled
            await handle_cleanup_after_sell(
                self.solana_client,
                _ctx_wallet.get() or self.wallet,
                token_info.mint,
                token_info.token_program_id,
                self.priority_fee_manager,
                self.cleanup_mode,
                self.cleanup_with_priority_fee,
                self.cleanup_force_close_with_burn,
            )
        else:
            logger.error(
                f"Failed to sell {token_info.symbol}: {sell_result.error_message}"
            )

    async def _monitor_position_until_exit(
        self, token_info: TokenInfo, position: Position
    ) -> None:
        """Monitor a position until exit conditions are met."""
        logger.info(
            f"Starting position monitoring (check interval: {self.price_check_interval}s)"
        )

        # Get pool address for price monitoring using platform-agnostic method
        pool_address = self._get_pool_address(token_info)
        curve_manager = self.platform_implementations.curve_manager

        while position.is_active:
            try:
                # Get current price from pool/curve
                current_price = await curve_manager.calculate_price(pool_address)

                # Check if position should be exited
                should_exit, exit_reason = position.should_exit(current_price)

                if should_exit and exit_reason:
                    logger.info(f"Exit condition met: {exit_reason.value}")
                    logger.info(f"Current price: {current_price:.8f} SOL")

                    # Log PnL before exit
                    pnl = position.get_pnl(current_price)
                    logger.info(
                        f"Position PnL: {pnl['price_change_pct']:.2f}% ({pnl['unrealized_pnl_sol']:.6f} SOL)"
                    )

                    # Execute sell with position quantity and entry price to avoid RPC delays
                    sell_result = await (_ctx_seller.get() or self.seller).execute(
                        token_info,
                        token_amount=position.quantity,
                        token_price=position.entry_price,
                    )

                    if sell_result.success:
                        # Close position with actual exit price
                        position.close_position(sell_result.price, exit_reason)

                        logger.info(
                            f"Successfully exited position: {exit_reason.value}"
                        )
                        self._log_trade(
                            "sell",
                            token_info,
                            sell_result.price,
                            sell_result.amount,
                            sell_result.tx_signature,
                        )

                        # Log final PnL
                        final_pnl = position.get_pnl()
                        logger.info(
                            f"Final PnL: {final_pnl['price_change_pct']:.2f}% ({final_pnl['unrealized_pnl_sol']:.6f} SOL)"
                        )

                        # Close ATA if enabled
                        await handle_cleanup_after_sell(
                            self.solana_client,
                            _ctx_wallet.get() or self.wallet,
                            token_info.mint,
                            token_info.token_program_id,
                            self.priority_fee_manager,
                            self.cleanup_mode,
                            self.cleanup_with_priority_fee,
                            self.cleanup_force_close_with_burn,
                        )
                    else:
                        logger.error(
                            f"Failed to exit position: {sell_result.error_message}"
                        )
                        # Keep monitoring in case sell can be retried

                    break
                else:
                    # Log current status
                    pnl = position.get_pnl(current_price)
                    logger.debug(
                        f"Position status: {current_price:.8f} SOL ({pnl['price_change_pct']:+.2f}%)"
                    )

                # Wait before next price check
                await asyncio.sleep(self.price_check_interval)

            except Exception:
                logger.exception("Error monitoring position")
                await asyncio.sleep(
                    self.price_check_interval
                )  # Continue monitoring despite errors

    def _get_pool_address(self, token_info: TokenInfo) -> Pubkey:
        """Get the pool/curve address for price monitoring using platform-agnostic method."""
        address_provider = self.platform_implementations.address_provider

        # Use platform-specific logic to get the appropriate address
        if hasattr(token_info, "bonding_curve") and token_info.bonding_curve:
            return token_info.bonding_curve
        elif hasattr(token_info, "pool_state") and token_info.pool_state:
            return token_info.pool_state
        else:
            # Fallback to deriving the address using platform provider
            return address_provider.derive_pool_address(token_info.mint)

    async def _save_token_info(self, token_info: TokenInfo) -> None:
        """Save token information to a file."""
        try:
            trades_dir = Path("trades")
            trades_dir.mkdir(exist_ok=True)
            file_path = trades_dir / f"{token_info.mint}.txt"

            # Convert to dictionary for saving - platform-agnostic
            token_dict = {
                "name": token_info.name,
                "symbol": token_info.symbol,
                "uri": token_info.uri,
                "mint": str(token_info.mint),
                "platform": token_info.platform.value,
                "user": str(token_info.user) if token_info.user else None,
                "creator": str(token_info.creator) if token_info.creator else None,
                "creation_timestamp": token_info.creation_timestamp,
            }

            # Add platform-specific fields only if they exist
            platform_fields = {
                "bonding_curve": token_info.bonding_curve,
                "associated_bonding_curve": token_info.associated_bonding_curve,
                "creator_vault": token_info.creator_vault,
                "pool_state": token_info.pool_state,
                "base_vault": token_info.base_vault,
                "quote_vault": token_info.quote_vault,
            }

            for field_name, field_value in platform_fields.items():
                if field_value is not None:
                    token_dict[field_name] = str(field_value)

            file_path.write_text(json.dumps(token_dict, indent=2))

            logger.info(f"Token information saved to {file_path}")
        except OSError:
            logger.exception("Failed to save token information")

    def _load_blocked_devs(self) -> dict:
        """Load the persisted ``{developer: accumulated_rugs}`` mapping."""
        try:
            if self._blocked_devs_path.is_file():
                return json.loads(self._blocked_devs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _identidades_del_dev(
        self, token_info: TokenInfo, minteo: str | None = None
    ) -> set[str]:
        """Las carteras con las que se apunta y se veta a un dev por rugs.

        `user` (quien firma), `creator` (a quien apunta la curva) y, si la
        lanzo una plataforma, la cartera que la minteo (6-nov). Solo CARTERAS:
        una cuenta de programa (la de la plataforma, o la de reparto de
        comisiones) es comun a muchas monedas o no es de nadie, y vetarla
        vetaria a gente que no ha hecho nada.
        """
        from monitoring.pumpfun_dev import dev_minteado, es_cartera  # noqa: PLC0415

        ids = {
            str(getattr(token_info, "user", "") or ""),
            str(getattr(token_info, "creator", "") or ""),
            minteo or dev_minteado(str(token_info.mint)) or "",
        }
        return {i for i in ids if i and es_cartera(i)}

    def _is_dev_blocked(self, *devs: str) -> bool:
        """True si alguna identidad alcanzo el umbral de rugs configurado.

        Se aceptan varias porque un lanzamiento tiene dos: `user` (quien firma)
        y `creator` (a quien apunta la curva). Normalmente coinciden, pero
        cuando no, mirar solo una dejaba entrar al mismo dev por la otra puerta.
        """
        data = self._load_blocked_devs()
        return any_dev_blocked(data, devs, self.rugs_para_bloquear)

    def _bump_dev_rug(self, dev: str, outcome: str) -> None:
        """Actualiza el contador de rugs del dev.

        outcome: 'rug'/'hardrug' (+1), 'win'/'neutral' (sin cambio).
        """
        if not dev or outcome == "neutral":
            return
        data = self._load_blocked_devs()
        prev = int(data.get(dev, 0))
        if outcome in {"rug", "hardrug"}:
            data[dev] = prev + 1
            if data[dev] >= self.rugs_para_bloquear:
                logger.warning(
                    "DEV BLOQUEADO %s: %s rugs acumulados -> no se le compra mas",
                    dev,
                    data[dev],
                )
                try:
                    from monitoring.notifier import notify

                    notify(
                        f"⛔ <b>DEV BLOQUEADO</b>\n"
                        f"{data[dev]} rug(s) detectados. No se volverá a comprar.\n"
                        f"<code>{dev}</code>"
                    )
                except Exception:  # noqa: BLE001
                    pass
        try:
            self._blocked_devs_path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            logger.debug("no pude escribir blocked_devs", exc_info=True)

    def _record_moonbag(
        self,
        token_info: TokenInfo,
        qty: float,
        entry_price: float,
        pool_address: Pubkey,
    ) -> None:
        """Registra un moonbag retenido para avisar por Telegram cada 5 min.

        No se auto-vende: lo vende el usuario a mano. El avisador lo elimina del
        fichero en cuanto detecta que ya no está en la wallet (balance 0).
        """
        try:
            data = []
            if self._moonbags_path.is_file():
                data = json.loads(self._moonbags_path.read_text(encoding="utf-8"))
            data = [m for m in data if m.get("mint") != str(token_info.mint)]
            data.append(
                {
                    "mint": str(token_info.mint),
                    "symbol": token_info.symbol,
                    "qty": qty,
                    "entry_price": entry_price,
                    "pool": str(pool_address),
                    "token_program": str(token_info.token_program_id),
                }
            )
            self._moonbags_path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Moonbag {token_info.symbol} registrado (avisos Telegram cada 5 min)")
        except Exception:  # noqa: BLE001 - registrar el moonbag nunca debe romper
            logger.debug("no pude registrar moonbag", exc_info=True)

    async def _precio_dex(self, mint: str) -> float | None:
        """Precio en SOL segun el DEX. Para cuando la curva ya no sirve.

        Cuando una moneda migra, la curva se vacia
        (`virtual_token_reserves: 0`) y `calculate_price` empieza a lanzar. El
        18-ago BECKY migro con el escalon de 34.000 $ sin cobrar: el monitor se
        quedo dando vueltas, 16.623 errores en hora y media, sin cerrar la
        posicion ni soltar el hueco. RAPIDA estuvo esa hora y media sin poder
        comprar nada porque solo admite una posicion a la vez.

        Migrar no es morir: BECKY tenia 11.427 $ de liquidez real en PumpSwap y
        203.360 $ de volumen diario. El precio existe, solo que ya no esta en
        la curva.

        Se cachea unos segundos porque el monitor pregunta el precio cada pocos
        segundos y DexScreener limita por ratio.
        """
        ahora = monotonic()
        _cache = self._precio_dex_cache.get(mint)
        if _cache and ahora - _cache[0] < 5.0:
            return _cache[1]
        precio = None
        try:
            import aiohttp

            url = "https://api.dexscreener.com/latest/dex/tokens/%s" % mint
            tiempo = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=tiempo) as ses:
                async with ses.get(url) as r:
                    if r.status == 200:
                        d = await r.json()
                        mejor = 0.0
                        for par in (d.get("pairs") or []):
                            # Solo pares contra SOL: el precio del monitor esta
                            # en SOL por token, no en dolares.
                            if (par.get("quoteToken") or {}).get("symbol") != "SOL":
                                continue
                            liq = float((par.get("liquidity") or {}).get("usd") or 0)
                            if liq <= mejor:
                                continue
                            try:
                                p = float(par.get("priceNative") or 0)
                            except (TypeError, ValueError):
                                continue
                            if p > 0:
                                mejor, precio = liq, p
        except Exception:  # noqa: BLE001 - sin precio del DEX se sigue sin el
            precio = None
        self._precio_dex_cache[mint] = (ahora, precio)
        return precio

    async def _precio_sol_loop(self) -> None:
        """Refresca el precio de SOL cada hora y mueve los techos con el.

        POR QUE HACE FALTA. El market cap se calcula como
        `precio x supply x _SOL_USD`. Esa constante estuvo en 75 $ desde el
        9-ago; el 21-ago SOL valia 91,24 y llevaba semanas sin tocarse, asi que
        el bot creia comprar a 2.800 $ cuando en realidad compraba a 3.406.

        POR QUE SE MUEVE TAMBIEN EL TECHO. Actualizar el precio a secas cambia
        en silencio lo que compra el bot: con el mismo `max_entry_mc`, subir el
        precio de SOL vuelve el filtro mas estricto sin que nadie lo haya
        decidido. Aqui el techo se escala en la misma proporcion, asi que el
        comportamiento se mantiene y lo unico que cambia es que los numeros en
        pantalla dejan de mentir.

        Si la consulta falla se deja el precio anterior: un valor viejo es malo,
        pero un cero convertiria todos los market caps en cero y el bot
        compraria cualquier cosa.
        """
        import trading.universal_trader as _mod

        while True:
            try:
                import aiohttp

                url = ("https://api.dexscreener.com/latest/dex/tokens/"
                       "So11111111111111111111111111111111111111112")
                nuevo = None
                tiempo = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=tiempo) as ses:
                    async with ses.get(url) as r:
                        if r.status == 200:
                            d = await r.json()
                            mejor = 0.0
                            for par in (d.get("pairs") or []):
                                liq = float((par.get("liquidity") or {}).get("usd") or 0)
                                try:
                                    p = float(par.get("priceUsd") or 0)
                                except (TypeError, ValueError):
                                    continue
                                if p > 0 and liq > mejor:
                                    mejor, nuevo = liq, p
                # Un salto enorme es casi siempre un dato malo, no el mercado.
                if nuevo and 20.0 < nuevo < 2000.0:
                    viejo = float(_mod._SOL_USD)
                    if abs(nuevo - viejo) / viejo > 0.005:
                        razon = nuevo / viejo
                        _mod._SOL_USD = nuevo
                        if self.max_entry_mc:
                            antes = self.max_entry_mc
                            self.max_entry_mc = antes * razon
                            logger.info(
                                "SOL %.2f -> %.2f $. El techo de entrada pasa de "
                                "%.0f a %.0f $ para que el filtro no se mueva.",
                                viejo, nuevo, antes, self.max_entry_mc,
                            )
                        else:
                            logger.info("SOL %.2f -> %.2f $", viejo, nuevo)
            except Exception:  # noqa: BLE001 - esto nunca debe romper el bot
                logger.debug("no pude refrescar el precio de SOL", exc_info=True)
            # AL PRINCIPIO, DEPRISA. Hasta el primer acierto el bot usa el
            # valor escrito a mano en `_SOL_USD`, y con SOL a ~220 $ ese valor
            # (91,24) deja TODOS los dolares del bot 2,4 veces por debajo de lo
            # real -- la regla que abandona posiciones por valer menos de 1 $
            # incluida. Esperar una hora a la primera lectura es una hora
            # entera decidiendo con numeros falsos.
            if _mod._SOL_USD == _ARRANQUE_SOL_USD:
                self._vueltas_sin_precio_sol += 1
                if self._vueltas_sin_precio_sol == _AVISAR_TRAS_VUELTAS:
                    logger.error(
                        "El precio de SOL sigue en el valor por defecto "
                        "(%.2f $) tras %d intentos: todos los importes en "
                        "dolares estan mal.", _ARRANQUE_SOL_USD,
                        self._vueltas_sin_precio_sol,
                    )
                    try:
                        from monitoring.notifier import notify

                        notify(
                            "⚠️ <b>PRECIO DE SOL SIN ACTUALIZAR</b>\n"
                            f"Sigue en {_ARRANQUE_SOL_USD:.2f} $ por defecto. "
                            "Todo lo que el bot calcula en dólares está mal "
                            "hasta que se corrija."
                        )
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(10)
                continue
            await asyncio.sleep(3600)

    async def _reinicia_tanda_loop(self) -> None:
        """Devuelve el contador de la tanda a cero cuando no queda nada abierto.

        POR QUE HACE FALTA. `_compras_de_la_tanda` se creaba a 0 al arrancar y
        solo subia: con `max_real_buys: 3` cada bot compraba tres veces y se
        callaba PARA SIEMPRE. El 19-ago los tres hicieron sus 3 compras entre
        las 05:00 y las 06:52 y estuvieron 23 HORAS sin comprar nada, con 4.435
        monedas cumpliendo los filtros por delante. El servicio seguia
        "active", asi que por fuera parecia que todo iba bien.

        El limite se llama "tanda" en todo el codigo, y una tanda que no acaba
        nunca no es un limite por tanda: es un apagado con retardo. Aqui se
        cierra la tanda cuando el bot se queda sin posiciones abiertas, que es
        el unico momento en el que "la tanda ha terminado" significa algo.

        Se mira el disco y no un contador en memoria a proposito: las marcas
        `open_position_*.json` son las mismas que consulta `desplegar.sh` para
        no reiniciar encima de una posicion viva, y ya se ha visto que un
        contador en memoria y la realidad se separan.
        """
        while True:
            await asyncio.sleep(60)
            try:
                if not self.reiniciar_tanda or not self._compras_de_la_tanda:
                    continue
                if list(Path().glob(f"open_position{self._state_suffix}_*.json")):
                    continue
                logger.info(
                    "TANDA cerrada: %d compras hechas y ninguna posicion abierta. "
                    "El contador vuelve a 0 y se puede seguir comprando.",
                    self._compras_de_la_tanda,
                )
                self._compras_de_la_tanda = 0
            except Exception:  # noqa: BLE001 - esto nunca debe romper el bot
                logger.debug("reinicio de tanda", exc_info=True)

    async def _dip_scanner_loop(self) -> None:
        """COMPRAR EL RETROCESO. No mira monedas al nacer -- vigila las que
        ya se vieron nacer (hasta `dip.ventana_seg` de vida) buscando que
        hayan tenido un pico dentro de la banda alta y que AHORA esten
        dentro de la banda baja: subieron, alguien recogio beneficios, y
        volvieron a caer. Compra ahi, no en el pico.

        Separado a proposito del filtro de nacimiento (`_handle_token`): ese
        decide en los primeros segundos con la moneda en la cola; esto
        decide minutos despues con la moneda ya fuera de la cola, así que
        necesita su propia copia del TokenInfo (`_token_info_cache`) para
        poder comprar.
        """
        _pico_min = float(self.dip.get("pico_min_mc") or 0)
        _pico_max = float(self.dip.get("pico_max_mc") or 0)
        _act_min = float(self.dip.get("actual_min_mc") or 0)
        _act_max = float(self.dip.get("actual_max_mc") or 0)
        _ventana = float(self.dip.get("ventana_seg") or 300)
        if not (_pico_min and _pico_max and _act_min and _act_max):
            return
        from monitoring.volumen import compras as _dip_compras
        from monitoring.volumen import ultimas_son_compra as _dip_ultimas

        _LOTE = 20  # consultas simultaneas por tanda
        _fallos: dict[str, int] = {}

        async def _precio_de(ti: TokenInfo) -> float | None:
            """Precio de una moneda; None si la curva no se puede leer."""
            try:
                st = await (
                    self.platform_implementations.curve_manager.get_pool_state(
                        ti.bonding_curve, commitment="processed"
                    )
                )
                return float(st.get("price_per_token") or 0)
            except Exception:  # noqa: BLE001
                return None

        logger.info(
            "DIP scanner activo: pico $%.0f-$%.0f, ahora $%.0f-$%.0f, "
            "ventana %.0fs",
            _pico_min, _pico_max, _act_min, _act_max, _ventana,
        )
        while True:
            await asyncio.sleep(2.0)
            try:
                ahora = monotonic()
                _pend: list[tuple[str, TokenInfo]] = []
                for mint, token_info in list(self._token_info_cache.items()):
                    if mint in self._dip_intentado or mint in self.bought_mints:
                        continue
                    nacida = self.token_timestamps.get(mint)
                    if nacida is None:
                        continue
                    edad = ahora - nacida
                    if edad > _ventana:
                        self._token_info_cache.pop(mint, None)
                        self._dip_pico.pop(mint, None)
                        _fallos.pop(mint, None)
                        continue
                    _pend.append((mint, token_info))
                # EN PARALELO, no en fila. En serie, una pasada sobre ~100
                # monedas tardaba 7-17s: cada moneda se miraba una vez cada
                # 10s y los retrocesos cortos se perdian entre muestras --
                # tanto la entrada a la banda como el propio pico.
                _res: list = []
                for _i in range(0, len(_pend), _LOTE):
                    _tanda = _pend[_i:_i + _LOTE]
                    _res.extend(
                        zip(
                            _tanda,
                            await asyncio.gather(
                                *(_precio_de(_ti) for _, _ti in _tanda)
                            ),
                        )
                    )
                for (mint, token_info), precio in _res:
                    edad = ahora - self.token_timestamps.get(mint, ahora)
                    if not precio or precio <= 0:
                        # Curva ilegible (migrada o de otro tipo). Tras 3
                        # intentos se deja de preguntar: eran cientos de
                        # llamadas tiradas a la basura cada pocos minutos.
                        _fallos[mint] = _fallos.get(mint, 0) + 1
                        if _fallos[mint] >= 3:
                            self._token_info_cache.pop(mint, None)
                            self._dip_pico.pop(mint, None)
                            _fallos.pop(mint, None)
                        continue
                    _fallos.pop(mint, None)
                    mc = precio * _PUMP_TOTAL_SUPPLY * _SOL_USD
                    pico = max(self._dip_pico.get(mint, 0.0), mc)
                    self._dip_pico[mint] = pico
                    if not (_pico_min <= pico <= _pico_max):
                        continue
                    if not (_act_min <= mc <= _act_max):
                        continue
                    # COMPRAS SEGUIDAS. El pico y la banda dicen que la moneda
                    # retrocedio; esto dice que alguien ya esta recomprando
                    # abajo. Sin esto se entra a cuchillo en plena caida.
                    #
                    # OJO: no se marca como intentada si falla -- se vuelve a
                    # mirar en la siguiente pasada (2s) mientras siga dentro
                    # de la banda. Marcarla aqui la quemaria para siempre por
                    # no cumplir en el primer vistazo.
                    _nc = _dip_compras(mint)
                    if self.min_buys > 0 and _nc < self.min_buys:
                        continue
                    if self.ultimas_tx_compra > 0 and not _dip_ultimas(
                        mint, self.ultimas_tx_compra
                    ):
                        continue
                    self._dip_intentado.add(mint)
                    logger.info(
                        f"DIP {token_info.symbol}: pico ${pico:,.0f} "
                        f"(dentro de ${_pico_min:,.0f}-${_pico_max:,.0f}), "
                        f"ahora ${mc:,.0f} (dentro de ${_act_min:,.0f}-"
                        f"${_act_max:,.0f}) a los {edad:.0f}s, "
                        f"{_nc} compras -> compra"
                    )
                    asyncio.create_task(self._comprar_dip(token_info))  # noqa: RUF006
            except Exception:  # noqa: BLE001 - un fallo aqui no tumba el scanner
                logger.debug("dip scanner", exc_info=True)

    async def _comprar_dip(self, token_info: TokenInfo) -> None:
        """Compra fuera de la cola normal, con el mismo cerrojo de saldo y
        el mismo tope de posiciones que cualquier otra compra -- lo unico
        que cambia es que la decision no viene de `_handle_token`."""
        importe = float(self.dip.get("buy_amount") or self.buy_amount)
        need = int(
            importe * (1 + self.buy_slippage) * LAMPORTS_PER_SOL
        ) + self._fee_headroom_lamports
        if self._position_semaphore.locked():
            logger.info(
                f"DIP {token_info.symbol}: max posiciones abiertas, no compra"
            )
            return
        async with self._position_semaphore:
            async with self._spend_lock:
                bal = await self.solana_client.get_sol_balance(self.wallet.pubkey)
                free = bal - self._reserved_lamports - self.min_reserve_lamports
                if free < need:
                    logger.warning(
                        f"DIP {token_info.symbol}: SOL insuficiente "
                        f"(libre={free / LAMPORTS_PER_SOL:.4f}, "
                        f"necesita={need / LAMPORTS_PER_SOL:.4f})"
                    )
                    return
                self._reserved_lamports += need
            if self.pausar_compras:
                self._apuntar_compra_en_pausa(token_info, importe)
                return
            try:
                buy_result: TradeResult = await self.buyer.execute(
                    token_info, amount_sol=importe,
                )
                if not buy_result.success:
                    await self._handle_failed_buy(token_info, buy_result)
                    return
                _pico = self._dip_pico.get(str(token_info.mint), 0.0)
                await self._handle_successful_buy(
                    token_info, buy_result, f"DIP: pico ${_pico:,.0f}"
                )
            finally:
                async with self._spend_lock:
                    self._reserved_lamports -= need

    async def _moonbag_notifier_loop(self) -> None:
        """Cada 5 min avisa por Telegram de los moonbags retenidos (venta manual)."""
        while True:
            await asyncio.sleep(300)
            try:
                await self._notify_moonbags()
            except Exception:  # noqa: BLE001 - el avisador nunca debe romper el bot
                logger.debug("moonbag notifier error", exc_info=True)

    async def _notify_moonbags(self) -> None:
        """Avisa de cada moonbag aún en la wallet; limpia los ya vendidos."""
        if not self._moonbags_path.is_file():
            return
        try:
            data = json.loads(self._moonbags_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not data:
            return
        from monitoring.notifier import notify

        keep = []
        for m in data:
            mint = m.get("mint")
            if not mint:
                continue
            # ¿sigue en la wallet? balance 0 = vendido -> dejamos de avisar
            held = True
            try:
                tp_str = m.get("token_program")
                if tp_str:
                    ata = self.wallet.get_associated_token_address(
                        Pubkey.from_string(mint), Pubkey.from_string(tp_str)
                    )
                    bal = await self.solana_client.get_token_account_balance(ata)
                    held = bal > 0
            except Exception as exc:  # noqa: BLE001
                # Al vender por Maestro se cierra el ATA -> la consulta falla con
                # "account not found": eso es VENDIDO (dejamos de avisar). Solo en
                # errores de red genuinos seguimos avisando (held=True).
                _m = str(exc).lower()
                held = not any(
                    s in _m for s in ("not found", "could not find", "invalid param")
                )
            if not held:
                notify(
                    f"✅ Moonbag <b>{m.get('symbol', '?')}</b> ya no está en la "
                    f"wallet (vendido). Dejo de avisar."
                )
                continue
            keep.append(m)
            price = None
            try:
                price = await self.platform_implementations.curve_manager.calculate_price(
                    Pubkey.from_string(m["pool"])
                )
            except Exception:  # noqa: BLE001 - migrado/ilegible: aviso sin PnL
                price = None
            ep = m.get("entry_price") or 0
            # El mismo criterio que en el monitor: un moonbag que ha caido por
            # debajo del minimo no da para venderlo, asi que avisar de el cada
            # cinco minutos es ruido. Se avisa UNA vez y se deja de seguir.
            if price and self.valor_minimo_posicion_usd > 0:
                _valor_usd = float(m.get("qty") or 0) * price * _SOL_USD
                if _valor_usd < self.valor_minimo_posicion_usd:
                    notify(
                        f"🧹 Moonbag <b>{m.get('symbol', '?')}</b> vale "
                        f"${_valor_usd:.2f} (mínimo ${self.valor_minimo_posicion_usd:.2f}). "
                        f"Venderlo cuesta más en comisiones de lo que saca, "
                        f"así que dejo de avisar. Sigue en tu wallet."
                    )
                    keep.pop()
                    continue
            maestro = (
                f'🤖 <a href="https://t.me/MaestroSniperBot?start={mint}">Maestro (vender)</a>'
            )
            if price and ep:
                pnl = (price / ep - 1) * 100
                mc = price * _PUMP_TOTAL_SUPPLY * _SOL_USD
                notify(
                    f"🌙 MOONBAG · <b>{m.get('symbol', '?')}</b>\n"
                    f"PnL {pnl:+.0f}% · MC ${mc:,.0f} · véndelo a mano cuando quieras\n"
                    f'📊 <a href="https://dexscreener.com/solana/{mint}">DexScreener</a> · {maestro}\n'
                    f"CA: <code>{mint}</code>"
                )
            else:
                notify(
                    f"🌙 MOONBAG · <b>{m.get('symbol', '?')}</b> (migrado / precio no disp.)\n"
                    f"{maestro}\n"
                    f"CA: <code>{mint}</code>"
                )
        try:
            self._moonbags_path.write_text(json.dumps(keep), encoding="utf-8")
        except OSError:
            pass

    def _log_trade(
        self,
        action: str,
        token_info: TokenInfo,
        price: float,
        amount: float,
        tx_hash: str | None,
        reason: str | None = None,
    ) -> None:
        """Log trade information."""
        try:
            trades_dir = Path("trades")
            trades_dir.mkdir(exist_ok=True)

            # DE DONDE VINO Y POR QUE SALIO. Sin estos dos campos no se puede
            # responder "que fuente va mejor": el registro decia que se compro
            # y a cuanto, pero no quien la trajo, asi que comparar DexScreener
            # contra la curva del 95% era imposible aunque los datos
            # estuvieran delante. El motivo de venta ya se pasaba a esta
            # funcion y se tiraba sin escribirlo.
            _senal = token_info.additional_data or {}
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "action": action,
                "platform": token_info.platform.value,
                "token_address": str(token_info.mint),
                "symbol": token_info.symbol,
                "price": price,
                "amount": amount,
                "tx_hash": str(tx_hash) if tx_hash else None,
                "origen": _senal.get("acquisition_source") or "listener",
                "motivo": reason,
                # Hasta donde llego la moneda mientras se vigilaba. Sirve para
                # ver si se vendio pronto: con la entrada y el pico se sabe
                # cuanto recorrido se dejo encima de la mesa.
                "pico": self._pico_visto.get(str(token_info.mint)),
            }

            log_file_path = trades_dir / "trades.log"
            with log_file_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(log_entry) + "\n")
        except OSError:
            logger.exception("Failed to log trade information")

        # Fire-and-forget alert (local beep + optional Telegram); never blocks.
        try:
            from monitoring.notifier import alert_trade

            mint_str = str(token_info.mint)
            if action == "buy" and (price or 0) > 0:
                self._precio_entrada[mint_str] = float(price)
            if action == "buy" and (token_info.additional_data or {}).get("four_purchase_notice"):
                return
            alert_trade(
                action,
                token_info.symbol,
                (price or 0) * (amount or 0),
                mint_str,
                reason,
                price_sol=price or 0.0,
                name=token_info.name,
                source_data=token_info.additional_data,
                entry_price_sol=self._precio_entrada.get(mint_str, 0.0),
            )
        except Exception:  # noqa: BLE001 - alerts must never crash trading
            logger.debug("trade alert failed", exc_info=True)


# Backward compatibility alias
PumpTrader = UniversalTrader  # Legacy name for backward compatibility
