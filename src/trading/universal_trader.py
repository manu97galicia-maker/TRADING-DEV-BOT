"""
Universal trading coordinator that works with any platform.
Cleaned up to remove all platform-specific hardcoding.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic

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
from dexscreener import DexScreenerFilter
from interfaces.core import Platform, TokenInfo
from monitoring.listener_factory import ListenerFactory
from platforms import get_platform_implementations
from trading.base import TradeResult
from trading.platform_aware import PlatformAwareBuyer, PlatformAwareSeller
from trading.position import Position
from trading.panic import panic_time
from trading.tiered_exit import build_tiers, moonbag_fraction, tiers_due
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
_SOL_USD = 72.98  # actualizado 4-ago (pump.fun/sol-price)
# Supply total estándar de un token pump.fun (1e9 tokens enteros).
_PUMP_TOTAL_SUPPLY = 1_000_000_000


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

    def __init__(
        self,
        rpc_endpoint: str,
        wss_endpoint: str,
        private_key: str,
        buy_amount: float,
        buy_slippage: float,
        sell_slippage: float,
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
        take_profit_percentage: float | None = None,
        stop_loss_percentage: float | None = None,
        max_hold_time: int | None = None,
        # MONEDA MUERTA: a los N segundos, si no sube al menos `venta_muerta_gain`,
        # se vende entera. 0 = desactivado.
        venta_muerta_seg: float = 0.0,
        venta_muerta_gain: float = 0.0,
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
        final_target_mc: float = 0.0,
        mc_take_profit_tiers: list | None = None,
        price_check_interval: int = 10,
        # Priority fee configuration
        enable_dynamic_priority_fee: bool = False,
        enable_fixed_priority_fee: bool = True,
        fixed_priority_fee: int = 200_000,
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
        # Telegram-calls acquisition mode (disabled unless configured)
        telegram_calls: dict | None = None,
        # GMGN smart-money/KOL signals (disabled unless configured)
        gmgn_signals: dict | None = None,
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
        # Edad máxima EN EL MOMENTO DE COMPRAR. `max_token_age` se mira al sacar
        # la moneda de la cola, antes de la espera de estabilización, así que no
        # limita la edad real de entrada. 0 = desactivado.
        max_buy_age: float = 0.0,
        # Reserva mínima de SOL que el bot nunca gasta (siempre deja esto).
        min_reserve_sol: float = 0.03,
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
        # How long (seconds) a paper candidate's price is followed
        paper_watch_secs: int = 300,
        # Suffix for per-bot state files so two bots on the same wallet don't
        # clobber each other's dedup / open-position recovery.
        state_suffix: str = "",
        # Compute unit configuration
        compute_units: dict | None = None,
        # Node provider configuration
        max_rps: float = 25.0,
    ):
        """Initialize the universal trader."""
        # Core components
        self.solana_client = SolanaClient(rpc_endpoint, max_rps=max_rps)
        self.wallet = Wallet(private_key)
        self.priority_fee_manager = PriorityFeeManager(
            client=self.solana_client,
            enable_dynamic_fee=enable_dynamic_priority_fee,
            enable_fixed_fee=enable_fixed_priority_fee,
            fixed_fee=fixed_priority_fee,
            extra_fee=extra_priority_fee,
            hard_cap=hard_cap_prior_fee,
        )

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
        )

        self.seller = PlatformAwareSeller(
            self.solana_client,
            self.wallet,
            self.priority_fee_manager,
            sell_slippage,
            max_retries,
            compute_units=self.compute_units,
        )

        # Initialize the appropriate listener with platform filtering
        self.token_listener = ListenerFactory.create_listener(
            listener_type=listener_type,
            wss_endpoint=wss_endpoint,
            geyser_endpoint=geyser_endpoint,
            geyser_api_token=geyser_api_token,
            geyser_auth_type=geyser_auth_type,
            pumpportal_url=pumpportal_url,
            platforms=[self.platform],  # Only listen for our platform
        )

        # Trading parameters
        self.buy_amount = buy_amount
        self.buy_slippage = buy_slippage
        self.sell_slippage = sell_slippage
        self.max_retries = max_retries
        self.extreme_fast_mode = extreme_fast_mode
        self.extreme_fast_token_amount = extreme_fast_token_amount

        # Exit strategy parameters
        self.exit_strategy = exit_strategy.lower()
        self.take_profit_percentage = take_profit_percentage
        self.stop_loss_percentage = stop_loss_percentage
        self.max_hold_time = max_hold_time
        self.venta_muerta_seg = float(venta_muerta_seg or 0)
        self.venta_muerta_gain = float(venta_muerta_gain or 0)
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
            ]
        )
        self.final_target_mc = float(final_target_mc or 0)
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
        # TP AGRESIVO para devs moonshot (los que suelen llegar MUY alto): objetivos
        # más altos y moonbag más grande para pillar el pump. +100/300/900%, vende
        # 20% en cada uno, moonbag 40% (vs el normal +50/150/300% moonbag 20%).
        self.tp_tiers_aggressive = build_tiers([(1.0, 0.20), (3.0, 0.20), (9.0, 0.20)])
        self._creator_bestpeak = _load_creator_bestpeak(bro_address)

        #: Ventas fallidas seguidas por mint. Cada intento fallido paga comision
        #: igual, asi que a partir de MAX_FALLOS_VENTA se deja de intentar en vez
        #: de reintentar cada segundo hasta agotar la wallet (ver _sell_amount).
        self._fallos_venta: dict[str, int] = {}

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
        self._position_semaphore = asyncio.Semaphore(20)  # sin tope práctico (2026-07-28): el límite real es el SOL disponible (spend-lock + min_reserve_sol)
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
        # Copy-trading config (off unless enabled).
        self.copy_wallets = copy_wallets or {}
        # Allow mayhem-mode tokens for this strategy (default: avoid).
        # require_mayhem forces "only mayhem", which also requires allowing them.
        self.require_mayhem = bool(require_mayhem)
        self.allow_mayhem = bool(allow_mayhem) or self.require_mayhem

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
        self.holder_bypass_ratio = float(holder_bypass_ratio or 0)
        self.min_holders_gmgn = int(min_holders_gmgn or 0)
        self.min_volumen_sol = float(min_volumen_sol or 0)
        self.min_buys = int(min_buys or 0)
        self.min_first_block_buys = int(min_first_block_buys or 0)
        self.min_ops = int(min_ops or 0)
        self.min_titulares = int(min_titulares or 0)
        self.redes_originales = bool(redes_originales)
        self.redes_timeout = float(redes_timeout or 1.5)
        self.max_volumen_sol = float(max_volumen_sol or 0)
        self.min_vol_ventana_sol = float(min_vol_ventana_sol or 0)
        self.vol_ventana_seg = float(vol_ventana_seg or 3.0)
        self.senal_gmgn = [int(x) for x in (senal_gmgn or [])]
        self.anti_rug_ratio = float(anti_rug_ratio or 0)
        self.anti_rug_seg = float(anti_rug_seg or 4.0)
        self.anti_rug_min_sol = float(anti_rug_min_sol or 3.0)
        self.no_repeat_name_min = float(no_repeat_name_min or 0)
        self.max_buy_age = float(max_buy_age or 0)
        #: símbolo -> monotonic() de la última vez que se vio. Solo memoria del
        #: proceso: tras un reinicio la ventana empieza vacía.
        self._nombres_vistos: dict[str, float] = {}
        # Reserva mínima de SOL que nunca se gasta (para fees / no quedarse a 0).
        self.min_reserve_lamports = int(float(min_reserve_sol or 0.03) * LAMPORTS_PER_SOL)
        self.paper_watch_secs = int(paper_watch_secs or 300)
        from paper_trading import PaperTradingEngine

        self.paper_engine = PaperTradingEngine(paper_trading_config)

        # DexScreener buy filter (no-op unless enabled in config)
        self.dex_filter = DexScreenerFilter(dexscreener_config)
        # Buy-on-dex-paid acquisition mode config (off unless enabled)
        self.buy_on_dex_paid = buy_on_dex_paid or {}
        # Telegram-calls acquisition mode config (off unless enabled)
        self.telegram_calls = telegram_calls or {}
        # GMGN signals acquisition mode config (off unless enabled)
        self.gmgn_signals = gmgn_signals or {}

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
        # Moonbags retenidos (no se auto-venden): el avisador manda un aviso a
        # Telegram cada 5 min mientras sigan en la wallet, para venderlos a mano.
        self._moonbags_path = Path(f"moonbags{_sfx}.json")
        # Auto-baneo: {dev: rugs_acumulados}. Con 2 rugs totales el dev queda
        # bloqueado; una ganadora intermedia no borra sus antecedentes.
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

    async def start(self) -> None:
        """Start the trading bot and listen for new tokens."""
        logger.info(f"Starting Universal Trader for {self.platform.value}")
        self.paper_engine.start()
        self._check_orphan_position()  # C2: warn if a previous run crashed open
        # Avisador de moonbags: cada 5 min notifica por Telegram los moonbags
        # retenidos para venderlos a mano (nunca los vende el bot).
        asyncio.create_task(self._moonbag_notifier_loop())  # noqa: RUF006
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

        if self.buy_on_dex_paid.get("enabled"):
            logger.info("Buy-on-dex-paid mode ENABLED (bypasses new-token listener)")
            await self._run_dexpaid_mode()
            return

        if self.telegram_calls.get("enabled"):
            logger.info("Telegram-calls mode ENABLED (bypasses new-token listener)")
            await self._run_telegram_mode()
            return

        if self.gmgn_signals.get("enabled"):
            logger.info("GMGN signals mode ENABLED (copy smart money / KOLs)")
            await self._run_gmgn_mode()
            return

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
        )
        try:
            await source.run(self._on_dexpaid_token)
        except asyncio.CancelledError:
            logger.info("Buy-on-dex-paid mode stopped")

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

        Records max gain / max drawdown and whether TP1 (+50%), TP2 (+140%) or
        the -17% stop would have triggered. Pure observation: never trades.
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
        tp1 = entry_price * (1 + _g1)
        tp2 = entry_price * (1 + _g2) if _g2 else entry_price * 999
        sl = entry_price * (1 - float(self.stop_loss_percentage or 0.3))
        peak = trough = entry_price
        hit = {"tp1": False, "tp2": False, "sl": False}
        first_hit = None
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
                    peak, trough = max(peak, p), min(trough, p)
                    if p >= tp2 and not hit["tp2"]:
                        hit["tp2"] = True
                        first_hit = first_hit or "tp2"
                    if p >= tp1 and not hit["tp1"]:
                        hit["tp1"] = True
                        first_hit = first_hit or "tp1"
                    if p <= sl and not hit["sl"]:
                        hit["sl"] = True
                        first_hit = first_hit or "sl"
                    # NOTE: deliberately do NOT stop early. We must observe the
                    # full path to learn the real peak distribution (fat tail);
                    # stopping at +140% would hide the 10x/100x runners and make
                    # any take-profit choice unmeasurable.
        finally:
            rec = {
                "src": "bundle" if self.min_curve_sol > 0 else "dexpaid",
                "mint": str(token_info.mint),
                "symbol": (token_info.symbol or "").strip(),
                "curve_sol": round(curve_sol, 3),
                "entry": entry_price,
                "peak_pct": round((peak / entry_price - 1) * 100, 1),
                "trough_pct": round((trough / entry_price - 1) * 100, 1),
                "tp1": hit["tp1"],
                "tp2": hit["tp2"],
                "sl": hit["sl"],
                "tp1_gain": round(_g1, 2),
                "tp2_gain": round(_g2 or 0.0, 2),
                "sl_pct": round(float(self.stop_loss_percentage or 0), 2),
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
                from monitoring.notifier import notify

                mint = rec["mint"]
                slp = float(self.stop_loss_percentage or 0.3)
                peak_frac = rec["peak_pct"] / 100.0
                if first_hit == "sl":
                    roi = -slp
                else:
                    roi = 0.0
                    pos = 1.0
                    for t in self.tp_tiers:  # vende por tramos segun el pico alcanzado
                        if peak_frac >= t.gain and pos > 0:
                            roi += t.sell * t.gain
                            pos -= t.sell
                    if hit["sl"]:  # el moonbag restante cayo al stop
                        roi += pos * (-slp)
                value = self.buy_amount * (1 + roi)
                pnl = value - self.buy_amount
                win = str(first_hit).startswith("tp")
                head = "✅ GANADOR" if win else ("🛑 STOP" if first_hit == "sl" else "⏹️ CIERRE")
                tot = self._bump_paper_total(pnl, win)  # acumulado total simulacion
                # Escalera EXPLICITA: que objetivo salto y cual no. Antes el
                # aviso daba un ROI sin decir de donde salia.
                marcas = []
                for i, t in enumerate(self.tp_tiers, 1):
                    ok = peak_frac >= t.gain
                    marcas.append(
                        f"{'✅' if ok else '⬜'} TP{i} +{t.gain * 100:.0f}%"
                    )
                marcas.append(f"{'🛑' if hit['sl'] else '⬜'} SL −{slp * 100:.0f}%")
                # La curva avisa de la liquidez: el 3-ago tres monedas con
                # "ganancia" en pantalla resultaron invendibles tras media hora
                # de intentos. Por debajo de ~7 SOL ese ROI puede no cobrarse.
                liq = float(rec.get("curve_sol") or 0.0)
                aviso_liq = "" if liq >= 7 else "  ⚠️ puede no ser vendible"
                notify(
                    f"{head} · <b>{rec['symbol']}</b> (paper)\n"
                    f"{' · '.join(marcas)}\n"
                    f"📊 ROI <b>{roi * 100:+.0f}%</b> · pico {rec['peak_pct']:+.0f}% · mín {rec['trough_pct']:+.0f}%\n"
                    f"💧 curva {liq:.1f} SOL{aviso_liq}\n"
                    f"💰 {self.buy_amount} SOL → <b>{value:.4f} SOL</b> ({pnl:+.4f})\n"
                    f"━━━━━━━━━━━━\n"
                    f"📈 <b>TOTAL: {tot['pnl']:+.4f} SOL</b> · {tot['n']} trades · "
                    f"{tot['wins']}✅/{tot['losses']}❌ ({tot['winrate']:.0f}% acierto)\n"
                    f'📊 <a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
                    f'🤖 <a href="https://t.me/MaestroSniperBot?start={mint}">Maestro</a>'
                )
            except Exception:  # noqa: BLE001 - notificar nunca rompe el trading
                pass

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

        rec = {
            "t": time.time(),
            "mint": str(token_info.mint),
            "symbol": (token_info.symbol or "").strip(),
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
            from monitoring.notifier import notify

            mint = rec["mint"]
            mc_sol = (rec.get("entry") or 0) * 1e9  # MC aprox en SOL
            notify(
                f"🎯 <b>SNIPE</b> · <b>{rec['symbol']}</b> (paper)\n"
                f"💰 Compra: {self.buy_amount} SOL\n"
                f"📈 MC entrada: ~{mc_sol:.0f} SOL (~${mc_sol * 75:,.0f})\n"
                f"💧 Curva: {rec['curve_sol']} SOL\n"
                f'📊 <a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
                f'🤖 <a href="https://t.me/MaestroSniperBot?start={mint}">Maestro</a> · '
                f'🚀 <a href="https://pump.fun/{mint}">pump.fun</a>\n'
                f"<code>{mint}</code>"
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
        except Exception:  # noqa: BLE001
            logger.exception("Error en venta manual (panel)")
        return remaining

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
        if self.max_dev_hold_pct <= 0:
            return False
        creator = getattr(token_info, "creator", None)
        if not creator:
            return False
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
                return False
            share = held / supply
            if share > self.max_dev_hold_pct:
                logger.info(
                    f"{token_info.symbol}: el DEV tiene {share * 100:.1f}% del supply "
                    f"(> {self.max_dev_hold_pct * 100:.0f}%) -> riesgo de dump, se omite"
                )
                return True
            logger.info(f"DEV HOLD OK {token_info.symbol}: el creador tiene {share * 100:.1f}%")
        except Exception as exc:  # noqa: BLE001 - fail-open, ver docstring
            logger.debug(f"No pude mirar lo que tiene el dev de {token_info.symbol}: {exc}")
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
        if (
            self.max_holder_pct <= 0
            and self.min_holders <= 0
            and self.max_bundle_pct <= 0
            and not self.require_zero_initial_holders
        ):
            return False
        try:
            import aiohttp

            mint = str(token_info.mint)
            curve_ata = (
                str(token_info.associated_bonding_curve)
                if token_info.associated_bonding_curve
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
            if self.max_bundle_pct > 0:
                bundle_sum = 0.0
                for a in accts:
                    addr = a.get("address")
                    if curve_ata and addr == curve_ata:
                        continue
                    if not curve_ata and addr == largest_addr:
                        continue
                    bundle_sum += float(a.get("amount") or 0) / supply
                if bundle_sum > self.max_bundle_pct:
                    logger.info(
                        f"{token_info.symbol}: bundle {bundle_sum * 100:.0f}% "
                        f"(top holders) > {self.max_bundle_pct * 100:.0f}% -> se omite"
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
                if pct > self.max_holder_pct:
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
                        f"supply (> {self.max_holder_pct * 100:.0f}%) y sin recorrido "
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
                import subprocess  # noqa: PLC0415, S404

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

    async def _handle_token(self, token_info: TokenInfo) -> None:
        """Handle a new token creation event."""
        try:
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

            # Blacklist DURA manual: dev vetado a mano -> nunca se compra.
            _dev_user = str(token_info.user)
            _dev_creator = str(getattr(token_info, "creator", "") or "")
            if (
                _dev_user in self._manual_blacklist
                or _dev_creator in self._manual_blacklist
            ):
                logger.info(
                    f"Evitado {token_info.symbol}: dev {_dev_user[:8]} en "
                    f"BLACKLIST manual (bloqueo permanente)"
                )
                return

            # Auto-baneo: dev que ha rugeado 2 veces seguidas -> no se le compra.
            if self._is_dev_blocked(_dev_user, _dev_creator):
                logger.info(
                    f"Evitado {token_info.symbol}: dev "
                    f"{_dev_user[:8]} BLOQUEADO (2 rugs acumulados)"
                )
                return

            # Avoid pump.fun "mayhem mode" tokens unless this strategy allows them.
            if not self.allow_mayhem and getattr(token_info, "is_mayhem_mode", False):
                logger.info(f"Evitado {token_info.symbol}: mayhem mode")
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

            # El historial del creador, igual: se pide AQUI aunque no se mire
            # hasta el final. La comprobacion vive mas abajo, despues de la
            # edad maxima, asi que si se pidiera alli sumaria su tiempo a los 3
            # segundos y la moneda se caeria por vieja. Pidiendola ahora, para
            # cuando toca decidir ya esta en la cache y no cuesta nada. La
            # respuesta se cachea 10 min por creador, asi que un dev que
            # relanza no se vuelve a consultar.
            if self.pumpfun_dev.get("enabled"):
                _dev0 = getattr(token_info, "creator", None) or getattr(
                    token_info, "user", None
                )
                if _dev0:
                    from monitoring.pumpfun_dev import creator_migration_stats

                    asyncio.ensure_future(creator_migration_stats(str(_dev0)))

            if self.max_buy_age > 0 and (
                self.min_volumen_sol > 0
                or self.min_entry_mc > 0
                or self.min_buys > 0
                or self.min_first_block_buys > 0
                or self.min_ops > 0
                or self.min_titulares > 0
            ):
                from monitoring.volumen import operaciones as _n_ops
                from monitoring.volumen import compras as _n_buys
                from monitoring.volumen import compras_primer_bloque as _n_first_buys
                from monitoring.volumen import titulares as _n_tit
                from monitoring.volumen import volumen_sol as _vol_sol

                _nacida = self.token_timestamps.get(str(token_info.mint))
                _t0 = _nacida if _nacida is not None else monotonic()
                _visto_vol = 0.0
                _visto_buys = _visto_first_buys = _visto_ops = _visto_tit = 0
                # Ventana corta: mirar cada medio segundo se come casi la mitad
                # de una ventana de 3 s. Con 0,15 s se comprueba ~20 veces y se
                # compra en el instante en que cumple.
                _paso = 0.15 if self.max_buy_age <= 6 else 0.5
                # Primera comprobacion inmediata: si la moneda ya cumple al
                # segundo 1, se compra al segundo 1. Solo se duerme cuando NO
                # cumple, nunca antes de mirar.
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
                    if (
                        self.min_first_block_buys > 0
                        and _fb < self.min_first_block_buys
                    ):
                        await asyncio.sleep(_paso)
                        continue
                    # PARTICIPACION. El volumen solo dice cuanto dinero se ha
                    # movido; esto dice entre cuantas manos. Una moneda con el
                    # volumen hecho de una sola compra del creador cumple el
                    # filtro de dinero y no significa nada.
                    if self.min_ops > 0 and _o < self.min_ops:
                        await asyncio.sleep(_paso)
                        continue
                    if self.min_titulares > 0 and _h < self.min_titulares:
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
                    except Exception:  # noqa: BLE001
                        await asyncio.sleep(_paso)
                        continue
                    _mc = _p * _PUMP_TOTAL_SUPPLY * _SOL_USD
                    if self.max_entry_mc > 0 and _mc > self.max_entry_mc:
                        logger.info(
                            f"Evitado {token_info.symbol}: se paso de la banda "
                            f"(MC ${_mc:,.0f} > ${self.max_entry_mc:,.0f}) "
                            f"mientras se vigilaba"
                        )
                        return
                    if self.min_entry_mc > 0 and _mc < self.min_entry_mc:
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
                    # Se dice QUE falto, no solo que fallo: sin esto no hay forma
                    # de saber si el filtro que sobra es el dinero o la gente.
                    logger.info(
                        f"Evitado {token_info.symbol}: no cumplio en "
                        f"{self.max_buy_age:.0f}s (lo mejor visto: "
                        f"{_visto_vol:.1f} SOL, {_visto_buys} compras, "
                        f"{_visto_first_buys} en primer bloque, "
                        f"{_visto_ops} tx, "
                        f"{_visto_tit} titulares)"
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
                if self.pumpfun_dev.get("enabled"):
                    creator = getattr(token_info, "creator", None)
                    if not creator:
                        logger.info(f"{token_info.symbol}: sin creator; se omite")
                        return
                    from monitoring.pumpfun_dev import creator_migration_stats

                    launches, migs, rate = await creator_migration_stats(str(creator))
                    min_migs = int(self.pumpfun_dev.get("min_migrations", 2))
                    min_rate = float(self.pumpfun_dev.get("min_rate", 0.24))
                    if migs < min_migs or rate < min_rate:
                        logger.info(
                            f"DEV filtro BLOCK {token_info.symbol}: "
                            f"{migs} migr / {launches} lanz ({rate * 100:.0f}%) "
                            f"< {min_migs} y {int(min_rate * 100)}%"
                        )
                        return
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
                    return

                # Anti-concentración: no comprar si un holder tiene >max_holder_pct.
                if (
                    self.max_holder_pct > 0 or self.min_holders > 0
                ) and await self._top_holder_exceeds(
                    token_info
                ):
                    return

                if not await self.dex_filter.should_buy(
                    str(token_info.mint), token_info.symbol
                ):
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

            # Paper-trade: record the candidate, follow its price, never buy.
            if self.paper_trade:
                self._record_paper(token_info, curve_sol, entry_price)
                if entry_price > 0:
                    # Con guardia: una excepcion dentro de _paper_watch se
                    # quedaba en un task sin dueno ("Task exception was never
                    # retrieved") y la simulacion parecia simplemente lenta. Si
                    # vuelve a romperse, ahora sale en el log y en Telegram.
                    async def _seguir(ti=token_info, ep=entry_price, cs=curve_sol):
                        try:
                            await self._paper_watch(ti, ep, cs)
                        except Exception as e:  # noqa: BLE001
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
                if self.max_real_buys and len(self.bought_mints) >= self.max_real_buys:
                    logger.warning(
                        "Limite de prueba alcanzado: %s/%s compras reales; %s no se compra",
                        len(self.bought_mints), self.max_real_buys, token_info.symbol,
                    )
                    return
                # The logs listener cannot reliably tell legacy SPL Token from
                # Token-2022 creation events. Never guess here: the mint account
                # owner is authoritative. A wrong program derives the wrong ATA
                # and produces a guaranteed failed transaction.
                try:
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
                need = int(
                    self.buy_amount * (1 + self.buy_slippage) * LAMPORTS_PER_SOL
                ) + self._fee_headroom_lamports
                async with self._spend_lock:
                    if (
                        self.max_real_buys
                        and len(self.bought_mints) + self._reserved_real_buy_slots
                        >= self.max_real_buys
                    ):
                        logger.warning(
                            "Limite de prueba reservado/alcanzado: %s completadas + "
                            "%s en curso / %s; %s no se compra",
                            len(self.bought_mints),
                            self._reserved_real_buy_slots,
                            self.max_real_buys,
                            token_info.symbol,
                        )
                        return
                    bal = await self.solana_client.get_sol_balance(self.wallet.pubkey)
                    # Nunca gastar por debajo de la reserva mínima (min_reserve).
                    free = bal - self._reserved_lamports - self.min_reserve_lamports
                    if free < need:
                        logger.warning(
                            f"Compra de {token_info.symbol} omitida: SOL insuficiente "
                            f"(libre={free / LAMPORTS_PER_SOL:.4f}, "
                            f"necesita={need / LAMPORTS_PER_SOL:.4f})"
                        )
                        return
                    self._reserved_lamports += need
                    self._reserved_real_buy_slots += 1
                try:
                    # Buy token
                    logger.info(
                        f"Buying {self.buy_amount:.6f} SOL worth of {token_info.symbol} on {token_info.platform.value}..."
                    )
                    buy_result: TradeResult = await self.buyer.execute(token_info)

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
        logger.info(
            f"Successfully bought {token_info.symbol} on {token_info.platform.value}"
        )
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
        if token_info.token_program_id:
            self.traded_token_programs[mint_str] = token_info.token_program_id

        # AVISO DE COMPRA con el porque. Hasta ahora la compra solo se apuntaba
        # en el log: desde Telegram se veia que habia entrado pero no que la
        # habia hecho entrar, asi que era imposible juzgar la señal despues.
        try:
            from monitoring.notifier import notify
            from monitoring.pumpfun_dev import creator_migration_stats
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
                try:
                    _l, _m, _r = await creator_migration_stats(_dev)
                    if _l:
                        _lineas.append(
                            f"👤 Dev migró <b>{_r * 100:.0f}%</b> ({_m}/{_l})"
                        )
                except Exception:  # noqa: BLE001
                    pass
                _med = (self._creator_medians or {}).get(_dev)
                if _med:
                    _lineas.append(f"📈 Mediana del dev: <b>${int(_med):,}</b>")

            _v = volumen_sol(mint_str)
            if _v:
                _lineas.append(
                    f"💧 {_v:.1f} SOL negociados en {operaciones(mint_str)} ops"
                )

            _mc = (buy_result.price or 0) * _PUMP_TOTAL_SUPPLY * _SOL_USD
            _bot = self._state_suffix.lstrip("_").replace("bot-sniper-", "").upper()
            _tr = " · ".join(
                f"+{t.gain * 100:.0f}%/{t.sell * 100:.0f}%" for t in self.tp_tiers
            )
            notify(
                f"🟢 <b>COMPRA</b> · {token_info.symbol} ({_bot})\n"
                + ("\n".join(_lineas) + "\n" if _lineas else "")
                + f"💵 {self.buy_amount} SOL · MC entrada ~${_mc:,.0f}\n"
                + f"🎯 {_tr} · SL -{self.stop_loss_percentage * 100:.0f}%\n"
                + f'📊 <a href="https://gmgn.ai/sol/token/{mint_str}">GMGN</a>\n'
                + f"CA: <code>{mint_str}</code>"
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

    async def _handle_failed_buy(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle failed token purchase."""
        logger.error(f"Failed to buy {token_info.symbol}: {buy_result.error_message}")
        # Close ATA if enabled
        await handle_cleanup_after_failure(
            self.solana_client,
            self.wallet,
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
        """
        try:
            ata = self.platform_implementations.address_provider.derive_user_token_account(
                self.wallet.pubkey, token_info.mint, token_info.token_program_id
            )
            crudo = await self.solana_client.get_token_account_balance(ata)
            return crudo / 10**TOKEN_DECIMALS
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"no pude leer el saldo de {token_info.symbol}: {exc}")
            return None

    async def _sell_amount(
        self,
        token_info: TokenInfo,
        amount: float,
        ref_price: float,
        reason: str,
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
        if self._fallos_venta.get(mint, 0) >= self.MAX_FALLOS_VENTA:
            return False

        real = await self._saldo_real_tokens(token_info)
        if real is not None:
            if real <= 0:
                logger.info(
                    f"{token_info.symbol}: no quedan tokens que vender ({reason}); "
                    f"la posicion ya estaba cerrada"
                )
                return True  # nada que vender = objetivo cumplido, no un fallo
            if amount > real:
                logger.info(
                    f"{token_info.symbol}: pedia vender {amount:.6f} pero solo hay "
                    f"{real:.6f}; ajusto al saldo real"
                )
                amount = real

        sell_result = await self.seller.execute(
            token_info, token_amount=amount, token_price=ref_price
        )
        if sell_result.success:
            self._fallos_venta.pop(mint, None)
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

            ok, sig, err = await jupiter_sell(
                self.wallet.keypair, str(token_info.mint), amount,
                slippage_bps=int(self.sell_slippage * 10000),
            )
        except Exception as exc:  # noqa: BLE001
            ok, sig, err = False, None, str(exc)
        if ok:
            self._fallos_venta.pop(mint, None)
            logger.info("Sold %s via Jupiter (%s) tx=%s", token_info.symbol, reason, sig)
            self._log_trade("sell", token_info, ref_price, amount, sig, reason)
            return True

        fallos = self._fallos_venta.get(mint, 0) + 1
        self._fallos_venta[mint] = fallos
        logger.error(
            "Sell failed for %s (%s) [%d/%d]: curve=%s | jupiter=%s",
            token_info.symbol, reason, fallos, self.MAX_FALLOS_VENTA,
            sell_result.error_message, err,
        )
        if fallos >= self.MAX_FALLOS_VENTA:
            logger.error(
                "%s: %d ventas fallidas seguidas; dejo de intentarlo para no "
                "seguir quemando comisiones. La bolsa queda en la wallet.",
                token_info.symbol, fallos,
            )
            try:
                from monitoring.notifier import notify

                notify(
                    f"🛑 <b>VENTA BLOQUEADA</b> · <b>{token_info.symbol}</b>\n"
                    f"{fallos} intentos fallidos ({sell_result.error_message}).\n"
                    f"Dejo de reintentar: cada intento cuesta comision.\n"
                    f"CA: <code>{mint}</code>"
                )
            except Exception:  # noqa: BLE001
                pass
        return False

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
        else:
            tiers = self.tp_tiers
        executed = [False] * len(tiers)
        remaining = original_qty
        sl_price = (
            entry_price * (1 - self.stop_loss_percentage)
            if self.stop_loss_percentage
            else None
        )
        moonbag = moonbag_fraction(tiers)
        target_price = (
            self.final_target_mc / (_PUMP_TOTAL_SUPPLY * _SOL_USD)
            if self.final_target_mc > 0 else None
        )
        mc_tiers = self.mc_take_profit_tiers
        mc_executed = [False] * len(mc_tiers)
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
        abandon_after = (self.max_hold_time or 120) * 3 + 120

        # ANTI PUMP&DUMP: pico observado para el trailing stop del moonbag.
        # El SL normal mide desde la ENTRADA, así que un pump+dump que vuelve
        # por encima de la entrada nunca lo dispara y el moonbag cae a cero.
        # El trailing mide desde el PICO: si cae stop_loss_percentage desde ahí,
        # se vende TODO (moonbag incluido).
        peak_price = entry_price
        # Auto-baneo por P&L NETO: SOL cobrado en ventas vs coste de entrada.
        realized_sol = 0.0
        cost_sol = original_qty * entry_price
        last_price = entry_price
        _muerta_revisada = False

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
                price = await curve_manager.calculate_price(pool_address)

                # The final remainder has an absolute market-cap target.  It is
                # only armed after every percentage TP has executed.
                if (
                    target_price is not None
                    and tiers
                    and all(executed)
                    and price >= target_price
                ):
                    if await self._sell_amount(token_info, remaining, price, "final_target_mc"):
                        realized_sol += remaining * price
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
                            realized_sol += amount * price
                            remaining -= amount
                            mc_executed[i] = True
                    if all(mc_executed):
                        if remaining > 1e-9:
                            self._record_moonbag(
                                token_info, remaining, entry_price, pool_address
                            )
                        break
                    await asyncio.sleep(self.price_check_interval)
                    continue

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
                            if await self._sell_amount(token_info, remaining, price):
                                realized_sol += remaining * price
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
                last_price = price
                if (
                    self.stop_loss_percentage
                    and peak_price >= entry_price * 1.2
                    and price <= peak_price * (1 - self.stop_loss_percentage)
                ):
                    logger.info(
                        f"TRAILING STOP {token_info.symbol}: {price:.8f} SOL <= pico "
                        f"{peak_price:.8f} -{self.stop_loss_percentage * 100:.0f}% "
                        f"-> vendo TODO (anti pump&dump, moonbag incluido)"
                    )
                    if await self._sell_amount(
                        token_info, remaining, price, "trailing_stop"
                    ):
                        realized_sol += remaining * price
                        remaining = 0.0
                        fully_exited = True
                        break
                    await asyncio.sleep(self.price_check_interval)
                    continue

                # Panel de Telegram: publica PnL en vivo y atiende ventas manuales.
                self._write_live_status(
                    token_info, entry_price, remaining, price, original_qty
                )
                _before_manual = remaining
                remaining = await self._check_manual_sell(token_info, remaining, price)
                realized_sol += max(0.0, _before_manual - remaining) * price
                if remaining <= 1e-9:
                    fully_exited = True
                    break

                # Panic sell (dashboard "vende todo"): dump if the flag was set
                # after this position opened.
                pt = panic_time()
                if pt is not None and pt >= position_start_wall:
                    logger.info("PANIC SELL for %s — dumping remaining", token_info.symbol)
                    if await self._sell_amount(token_info, remaining, price, "panic_sell"):
                        realized_sol += remaining * price
                        remaining = 0.0
                        fully_exited = True
                        break
                    await asyncio.sleep(self.price_check_interval)
                    continue

                # Stop loss: vende hasta el MOONBAG (deja el 20% para venta manual),
                # priced at the LIVE price. El moonbag NUNCA se auto-vende, ni en
                # stop-loss: se registra para avisar por Telegram y lo vende el usuario.
                if sl_price is not None and price <= sl_price:
                    hit_sl = True  # rug si no hubo TP antes
                    keep = original_qty * moonbag
                    to_sell = remaining - keep
                    logger.info(
                        f"STOP LOSS for {token_info.symbol} at {price:.8f} SOL "
                        f"(<= {sl_price:.8f}) — dejo moonbag {moonbag * 100:.0f}%"
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
                        realized_sol += to_sell * price
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
                    if amount <= 1e-9:
                        executed[i] = True
                        continue
                    reason = f"tp_tier{i + 1}_+{tiers[i].gain * 100:.0f}%"
                    if await self._sell_amount(token_info, amount, price, reason):
                        realized_sol += amount * price
                        executed[i] = True
                        remaining -= amount
                        had_tp = True  # el dev entregó un pump -> no es rug

                if remaining <= 1e-9:
                    fully_exited = True
                    break

                # All tiers done -> hold the moonbag and stop monitoring.
                if tiers and all(executed) and target_price is None and not mc_tiers:
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
                    _muerta_revisada = True
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
                            realized_sol += remaining * price
                            remaining = 0.0
                            fully_exited = True
                            break
                        logger.warning(
                            "Venta por muerta fallo en %s; lo reintenta el bucle",
                            token_info.symbol,
                        )
                    else:
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
                            realized_sol += to_sell * price
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

                await asyncio.sleep(self.price_check_interval)

            except Exception:
                logger.exception("Error in tiered exit monitor")
                await asyncio.sleep(self.price_check_interval)

        # Auto-baneo de devs por P&L NETO real (no por "tocó un TP"): SOL cobrado
        # + valor del moonbag aún en la wallet, menos el coste de entrada. Así el
        # pump&dumper que clipa un TP1 y luego tira SÍ cuenta como pérdida (rug),
        # y 2 pérdidas acumuladas del mismo dev -> se le bloquea.
        held_val = remaining * last_price  # moonbag que sigue en la wallet
        net_sol = realized_sol + held_val - cost_sol
        # Una perdida >=50% cuenta como rug grave, pero también requiere dos
        # rugs acumulados para bloquear al dev.
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
        # Se anota a las DOS identidades. Si solo se apuntara a `user`, un dev
        # que firma con carteras nuevas pero deja el mismo `creator` acumularia
        # un strike suelto en cada una y nunca llegaria a dos.
        _ident = {str(token_info.user), str(getattr(token_info, "creator", "") or "")}
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
                    self.wallet,
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
        sell_result: TradeResult = await self.seller.execute(
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
                self.wallet,
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
                    sell_result = await self.seller.execute(
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
                            self.wallet,
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
        """{dev: rugs_acumulados}. >=2 => bloqueado."""
        try:
            if self._blocked_devs_path.is_file():
                return json.loads(self._blocked_devs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _is_dev_blocked(self, *devs: str) -> bool:
        """True si ALGUNA de las identidades dadas lleva 2+ rugs acumulados.

        Se aceptan varias porque un lanzamiento tiene dos: `user` (quien firma)
        y `creator` (a quien apunta la curva). Normalmente coinciden, pero
        cuando no, mirar solo una dejaba entrar al mismo dev por la otra puerta.
        """
        data = self._load_blocked_devs()
        return any(d and int(data.get(d, 0)) >= 2 for d in devs)

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
            if data[dev] >= 2:
                logger.warning(
                    "DEV BLOQUEADO %s: %s rugs acumulados -> no se le compra mas",
                    dev,
                    data[dev],
                )
                try:
                    from monitoring.notifier import notify

                    notify(
                        f"⛔ <b>DEV BLOQUEADO</b>\n"
                        f"Dos rugs detectados. No se volverá a comprar.\n"
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

            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "action": action,
                "platform": token_info.platform.value,
                "token_address": str(token_info.mint),
                "symbol": token_info.symbol,
                "price": price,
                "amount": amount,
                "tx_hash": str(tx_hash) if tx_hash else None,
            }

            log_file_path = trades_dir / "trades.log"
            with log_file_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(log_entry) + "\n")
        except OSError:
            logger.exception("Failed to log trade information")

        # Fire-and-forget alert (local beep + optional Telegram); never blocks.
        try:
            from monitoring.notifier import alert_trade

            alert_trade(
                action,
                token_info.symbol,
                (price or 0) * (amount or 0),
                str(token_info.mint),
                reason,
            )
        except Exception:  # noqa: BLE001 - alerts must never crash trading
            logger.debug("trade alert failed", exc_info=True)


# Backward compatibility alias
PumpTrader = UniversalTrader  # Legacy name for backward compatibility
