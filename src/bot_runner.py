import asyncio
import logging
import math
import multiprocessing
import re
import sys
from datetime import datetime
from pathlib import Path

# La consola de Windows va en cp1252 y el bot loguea emojis. Sin esto, CADA
# linea con un emoji lanza una excepcion dentro del propio logging: el 3-ago se
# contaron 737 en una sola sesion. No rompen el trading, pero llenan el log de
# "Traceback" falsos y esconden los errores de verdad, que es peor.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # flujo redirigido o ya cerrado
        pass

# Try to use uvloop on Unix or winloop on Windows for better performance
# Fall back to standard asyncio if not available
try:
    if sys.platform == "win32":
        import winloop

        asyncio.set_event_loop_policy(winloop.EventLoopPolicy())
        logging.info("Using winloop event loop policy for improved performance")
    else:
        import uvloop

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logging.info("Using uvloop event loop policy for improved performance")
except ImportError:
    logging.info(
        "Using standard asyncio event loop (install uvloop/winloop for better performance)"
    )

from config_loader import (
    get_platform_from_config,
    load_bot_config,
    print_config_summary,
    validate_platform_listener_combination,
)
from trading.universal_trader import UniversalTrader
from utils.logger import setup_file_logging


def setup_logging(bot_name: str):
    """Set up logging to file for a specific bot instance."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # El nombre del bot va en el fichero de log, asi que hay que limpiarlo: una
    # sola "/" en el `name` del YAML lo convierte en una ruta a un directorio
    # que no existe, FileHandler revienta con FileNotFoundError y el bot muere
    # ANTES de arrancar. Con systemd eso es un bucle de reinicios cada 15 s.
    # Paso el 4-ago con un bot llamado "... TP +30/+80".
    seguro = re.sub(r'[^\w.,+ -]', "_", bot_name).strip() or "bot"
    log_filename = log_dir / f"{seguro}_{timestamp}.log"

    setup_file_logging(str(log_filename))


async def _auto_scale(trader, interval: int = 60) -> None:
    """Each time wallet balance doubles from start, grow buy_amount by +30%."""
    _LAMPORTS = 1_000_000_000
    try:
        start = await trader.solana_client.get_sol_balance(trader.wallet.pubkey)
    except Exception:
        logging.warning("ESCALA: no se pudo leer el saldo inicial, escalado desactivado")
        return
    if start <= 0:
        return
    base_buy = trader.buy_amount
    base_mayhem = trader.mayhem_buy_amount
    n = 0
    while True:
        await asyncio.sleep(interval)
        try:
            cur = await trader.solana_client.get_sol_balance(trader.wallet.pubkey)
        except Exception:
            continue
        if cur <= 0:
            continue
        doublings = int(math.log2(cur / start)) if cur >= start else 0
        doublings = max(0, doublings)
        if doublings > n:
            n = doublings
            new_buy = round(base_buy * (1.30 ** n), 6)
            new_mayhem = round(base_mayhem * (1.30 ** n), 6)
            trader.buy_amount = new_buy
            trader.mayhem_buy_amount = new_mayhem
            logging.info(
                "ESCALA x%d: saldo %.4f SOL (inicio %.4f) -> "
                "buy=%.4f SOL mayhem=%.4f SOL",
                n, cur / _LAMPORTS, start / _LAMPORTS, new_buy, new_mayhem,
            )


async def start_bot(config_path: str):
    """Start a trading bot with the configuration from the specified path."""
    cfg = load_bot_config(config_path)
    setup_logging(cfg["name"])

    # Que avisos de Telegram se mandan. Sin esta clave se manda todo, que
    # es como funcionaba antes.
    from monitoring.notifier import configurar_avisos

    configurar_avisos(cfg.get("avisos_telegram"))
    print_config_summary(cfg)

    # Get and validate platform from configuration
    try:
        platform = get_platform_from_config(cfg)
        logging.info(f"Detected platform: {platform.value}")
    except ValueError as e:
        logging.exception(f"Platform configuration error: {e}")
        return

    # Validate platform support
    try:
        from platforms import platform_factory

        if not platform_factory.registry.is_platform_supported(platform):
            logging.error(
                f"Platform {platform.value} is not supported. Available platforms: {[p.value for p in platform_factory.get_supported_platforms()]}"
            )
            return
    except Exception as e:
        logging.exception(f"Could not validate platform support: {e}")
        return

    # Validate listener compatibility
    listener_type = cfg["filters"]["listener_type"]
    if not validate_platform_listener_combination(platform, listener_type):
        from config_loader import get_supported_listeners_for_platform

        supported = get_supported_listeners_for_platform(platform)
        logging.error(
            f"Listener '{listener_type}' is not compatible with platform '{platform.value}'. Supported listeners: {supported}"
        )
        return

    # Initialize universal trader with platform-specific configuration
    try:
        trader = UniversalTrader(
            # Connection settings
            rpc_endpoint=cfg["rpc_endpoint"],
            wss_endpoint=cfg["wss_endpoint"],
            private_key=cfg["private_key"],
            wallets_json=cfg.get("wallets_json"),
            # Platform configuration - pass platform enum directly
            platform=platform,
            # Trade parameters
            buy_amount=cfg["trade"]["buy_amount"],
            buy_slippage=cfg["trade"]["buy_slippage"],
            sell_slippage=cfg["trade"]["sell_slippage"],
            # Extreme fast mode settings
            extreme_fast_mode=cfg["trade"].get("extreme_fast_mode", False),
            extreme_fast_token_amount=cfg["trade"].get("extreme_fast_token_amount", 30),
            # Exit strategy configuration
            exit_strategy=cfg["trade"].get("exit_strategy", "time_based"),
            take_profit_percentage=cfg["trade"].get("take_profit_percentage"),
            stop_loss_percentage=cfg["trade"].get("stop_loss_percentage"),
            max_hold_time=cfg["trade"].get("max_hold_time"),
            abandonar_tras_seg=cfg["trade"].get("abandonar_tras_seg"),
            venta_muerta_seg=cfg["trade"].get("venta_muerta_seg", 0),
            venta_muerta_gain=cfg["trade"].get("venta_muerta_gain", 0),
            # Tiered take-profit ladder (partial sells + moonbag)
            take_profit_sell_percentage=cfg["trade"].get("take_profit_sell_percentage"),
            tp2_gain=cfg["trade"].get("tp2_gain"),
            tp2_sell=cfg["trade"].get("tp2_sell"),
            tp3_gain=cfg["trade"].get("tp3_gain"),
            tp3_sell=cfg["trade"].get("tp3_sell"),
            tp4_gain=cfg["trade"].get("tp4_gain"),
            tp4_sell=cfg["trade"].get("tp4_sell"),
            tp5_gain=cfg["trade"].get("tp5_gain"),
            tp5_sell=cfg["trade"].get("tp5_sell"),
            tp6_gain=cfg["trade"].get("tp6_gain"),
            tp6_sell=cfg["trade"].get("tp6_sell"),
            tp_extra=cfg["trade"].get("tp_extra"),
            final_target_mc=cfg["trade"].get("final_target_mc", 0),
            mc_stop_loss=cfg["trade"].get("mc_stop_loss", 0),
            mc_take_profit_tiers=cfg["trade"].get("mc_take_profit_tiers"),
            suelos_por_mc=cfg["trade"].get("suelos_por_mc"),
            rugs_para_bloquear=cfg["trade"].get("rugs_para_bloquear", 2),
            stop_a_entrada_tras_tp=cfg["trade"].get(
                "stop_a_entrada_tras_tp", False
            ),
            stop_al_tp_tras_tp=cfg["trade"].get("stop_al_tp_tras_tp", False),
            entrada_alternativa=cfg.get("filters", {}).get(
                "entrada_alternativa"
            ),
            price_check_interval=cfg["trade"].get("price_check_interval", 10),
            precio_stream_seg=cfg["trade"].get("precio_stream_seg", 0),
            tick_stream_seg=cfg["trade"].get("tick_stream_seg", 0.25),
            # Listener configuration
            listener_type=cfg["filters"]["listener_type"],
            # Geyser configuration (if applicable)
            geyser_endpoint=cfg.get("geyser", {}).get("endpoint"),
            geyser_api_token=cfg.get("geyser", {}).get("api_token"),
            geyser_auth_type=cfg.get("geyser", {}).get("auth_type", "x-token"),
            # PumpPortal configuration (if applicable)
            pumpportal_url=cfg.get("pumpportal", {}).get(
                "url", "wss://pumpportal.fun/api/data"
            ),
            # Priority fee configuration
            enable_dynamic_priority_fee=cfg.get("priority_fees", {}).get(
                "enable_dynamic", False
            ),
            enable_fixed_priority_fee=cfg.get("priority_fees", {}).get(
                "enable_fixed", True
            ),
            fixed_priority_fee=cfg.get("priority_fees", {}).get("fixed_amount", 500000),
            extra_priority_fee=cfg.get("priority_fees", {}).get(
                "extra_percentage", 0.0
            ),
            hard_cap_prior_fee=cfg.get("priority_fees", {}).get("hard_cap", 500000),
            # Retry and timeout settings
            max_retries=cfg.get("retries", {}).get("max_attempts", 10),
            wait_time_after_creation=cfg.get("retries", {}).get(
                "wait_after_creation", 15
            ),
            wait_time_after_buy=cfg.get("retries", {}).get("wait_after_buy", 15),
            wait_time_before_new_token=cfg.get("retries", {}).get(
                "wait_before_new_token", 15
            ),
            max_token_age=cfg.get("filters", {}).get("max_token_age", 0.001),
            token_wait_timeout=cfg.get("timing", {}).get("token_wait_timeout", 120),
            # Cleanup settings
            cleanup_mode=cfg.get("cleanup", {}).get("mode", "disabled"),
            cleanup_force_close_with_burn=cfg.get("cleanup", {}).get(
                "force_close_with_burn", False
            ),
            cleanup_with_priority_fee=cfg.get("cleanup", {}).get(
                "with_priority_fee", False
            ),
            # Trading filters
            match_string=cfg["filters"].get("match_string"),
            bro_address=cfg["filters"].get("bro_address"),
            marry_mode=cfg["filters"].get("marry_mode", False),
            yolo_mode=cfg["filters"].get("yolo_mode", False),
            # DexScreener buy filter (disabled unless present + enabled)
            dexscreener_config=cfg.get("filters", {}).get("dexscreener"),
            # Buy-on-dex-paid acquisition mode (disabled unless present + enabled)
            buy_on_dex_paid=cfg.get("filters", {}).get("buy_on_dex_paid"),
            # Birdeye trending acquisition (disabled unless present + enabled)
            birdeye_trending=cfg.get("filters", {}).get("birdeye_trending"),
            dextools_hotpools=cfg.get("filters", {}).get("dextools_hotpools"),
            # Telegram-calls acquisition mode (disabled unless present + enabled)
            valor_minimo_posicion_usd=float(
                cfg["trade"].get("valor_minimo_posicion_usd", 1.0)
            ),
            curva_96=cfg.get("filters", {}).get("curva_96"),
            telegram_calls=cfg.get("filters", {}).get("telegram_calls"),
            discord_calls=cfg.get("filters", {}).get("discord_calls"),
            # GMGN smart-money/KOL signals (disabled unless present + enabled)
            gmgn_signals=cfg.get("filters", {}).get("gmgn_signals"),
            callouts=cfg.get("callouts"),
            # Paper-trade mode: filter + record, never buy (for ROI tracking)
            paper_trade=cfg.get("filters", {}).get("paper_trade", False),
            # Safe point-in-time simulator. When enabled it owns the candidate
            # path and always returns before any buy transaction is constructed.
            paper_trading_config=cfg.get("paper_trading"),
            # Bundle detector: min SOL in the bonding curve to consider a buy
            min_curve_sol=cfg.get("filters", {}).get("min_curve_sol", 0.0),
            max_entry_mc=cfg.get("filters", {}).get("max_entry_mc", 0.0),
            min_entry_mc=cfg.get("filters", {}).get("min_entry_mc", 0.0),
            max_holder_pct=cfg.get("filters", {}).get("max_holder_pct", 0.0),
            max_bundle_pct=cfg.get("filters", {}).get("max_bundle_pct", 0.0),
            max_dev_hold_pct=cfg.get("filters", {}).get("max_dev_hold_pct", 0.0),
            min_dev_hold_pct=cfg.get("filters", {}).get("min_dev_hold_pct", 0.0),
            min_holders=cfg.get("filters", {}).get("min_holders", 0),
            require_zero_initial_holders=cfg.get("filters", {}).get(
                "require_zero_initial_holders", False
            ),
            holder_bypass_ratio=cfg.get("filters", {}).get("holder_bypass_ratio", 0.0),
            min_holders_gmgn=cfg.get("filters", {}).get("min_holders_gmgn", 0),
            min_volumen_sol=cfg.get("filters", {}).get("min_volumen_sol", 0.0),
            min_buys=cfg.get("filters", {}).get("min_buys", 0),
            min_first_block_buys=cfg.get("filters", {}).get(
                "min_first_block_buys", 0
            ),
            min_ops=cfg.get("filters", {}).get("min_ops", 0),
            min_titulares=cfg.get("filters", {}).get("min_titulares", 0),
            redes_originales=cfg.get("filters", {}).get("redes_originales", False),
            redes_timeout=cfg.get("filters", {}).get("redes_timeout", 1.5),
            max_volumen_sol=cfg.get("filters", {}).get("max_volumen_sol", 0.0),
            min_vol_ventana_sol=cfg.get("filters", {}).get("min_vol_ventana_sol", 0.0),
            vol_ventana_seg=cfg.get("filters", {}).get("vol_ventana_seg", 3.0),
            second_wave=cfg.get("filters", {}).get("second_wave"),
            senal_gmgn=cfg.get("filters", {}).get("senal_gmgn"),
            anti_rug_ratio=cfg.get("trade", {}).get("anti_rug_ratio", 0.0),
            anti_rug_seg=cfg.get("trade", {}).get("anti_rug_seg", 4.0),
            anti_rug_min_sol=cfg.get("trade", {}).get("anti_rug_min_sol", 3.0),
            no_repeat_name_min=cfg.get("filters", {}).get("no_repeat_name_min", 0.0),
            ignorar_listas_de_devs=cfg.get("filters", {}).get(
                "ignorar_listas_de_devs", False
            ),
            min_presion_ratio=cfg.get("filters", {}).get("min_presion_ratio", 0),
            ultimas_tx_compra=cfg.get("filters", {}).get("ultimas_tx_compra", 0),
            presion_seg=cfg.get("filters", {}).get("presion_seg", 3.0),
            max_mc_por_sol_curva=cfg.get("filters", {}).get(
                "max_mc_por_sol_curva", 0
            ),
            max_perdidas_seguidas=cfg["trade"].get("max_perdidas_seguidas", 0),
            buy_amount_tras_sl=cfg["trade"].get("buy_amount_tras_sl", 0),
            min_supply_en_manos=cfg.get("filters", {}).get(
                "min_supply_en_manos", 0
            ),
            max_first_block_pct=cfg.get("filters", {}).get(
                "max_first_block_pct", 0
            ),
            max_posiciones=cfg["trade"].get("max_posiciones", 0),
            devs_marcados=cfg["trade"].get("devs_marcados"),
            buy_amount_marcados=cfg["trade"].get("buy_amount_marcados", 0),
            tope_compra_sol=cfg["trade"].get("tope_compra_sol", 0),
            fraccion_saldo=cfg["trade"].get("fraccion_saldo", 0),
            compra_minima_sol=cfg["trade"].get("compra_minima_sol", 0),
            escala_por_sol_ganado=cfg["trade"].get("escala_por_sol_ganado", 0),
            reiniciar_tanda=cfg["trade"].get("reiniciar_tanda", True),
            venta_bajo_entrada_seg=cfg["trade"].get("venta_bajo_entrada_seg", 0),
            # Estos dos se añadieron al trader pero se habian quedado sin
            # cablear aqui, asi que `usar_selector: true` en el YAML no llegaba
            # a ninguna parte y el selector nunca conto una sola operacion.
            usar_selector=cfg["trade"].get("usar_selector", False),
            nombre_estrategia=cfg["trade"].get("nombre_estrategia", ""),
            horario=cfg.get("filters", {}).get("horario"),
            dev_elite=cfg.get("filters", {}).get("dev_elite"),
            dev_mediana=cfg.get("filters", {}).get("dev_mediana"),
            dev_buy=cfg.get("filters", {}).get("dev_buy"),
            dip=cfg.get("filters", {}).get("dip"),
            tope_compra_total_sol=cfg["trade"].get(
                "tope_compra_total_sol", 0
            ),
            tp_marcados=cfg["trade"].get("tp_marcados", 0),
            tp_sell_marcados=cfg["trade"].get("tp_sell_marcados", 0),
            trailing_stop_percentage=cfg["trade"].get("trailing_stop_percentage", 0),
            vender_todo_en_stop=cfg["trade"].get("vender_todo_en_stop", False),
            mayhem_buy_amount=cfg["trade"].get("mayhem_buy_amount", 0),
            mayhem_max_perdidas=cfg["trade"].get("mayhem_max_perdidas", 0),
            max_buy_age=cfg.get("filters", {}).get("max_buy_age", 0.0),
            min_buy_age=cfg.get("filters", {}).get("min_buy_age", 0.0),
            min_reserve_sol=cfg.get("trade", {}).get("min_reserve_sol", 0.03),
            max_real_buys=cfg.get("trade", {}).get("max_real_buys", 0),
            pumpfun_dev=cfg.get("filters", {}).get("pumpfun_dev"),
            copy_wallets=cfg.get("filters", {}).get("copy_wallets"),
            allow_mayhem=cfg.get("filters", {}).get("allow_mayhem", False),
            require_mayhem=cfg.get("filters", {}).get("require_mayhem", False),
            paper_watch_secs=cfg.get("filters", {}).get("paper_watch_secs", 300),
            # Per-bot state files (dedup / open-position) so two bots on the same
            # wallet don't clobber each other. Derived from the config file name.
            state_suffix=Path(config_path).stem,
            # Compute unit configuration
            compute_units=cfg.get("compute_units", {}),
            # Node provider configuration
            max_rps=cfg.get("node", {}).get("max_rps", 25),
        )

        # Escalar posicion un 30% cada vez que el saldo de la wallet se doble
        # respecto al momento de arranque. El check es cada 60 s y es una
        # tarea de fondo: no bloquea el trading en ningún caso.
        _scale_task = asyncio.create_task(_auto_scale(trader))
        try:
            await trader.start()
        finally:
            _scale_task.cancel()
            try:
                await _scale_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logging.exception(f"Failed to initialize or start trader: {e}")
        raise


def _pid_alive(pid):
    """True if a process with this PID exists (cross-platform, no psutil)."""
    import os
    import sys

    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k = ctypes.windll.kernel32
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def run_bot_process(config_path, parent_pid=None):
    # Parent-liveness watchdog: if the launching process dies (crash, kill,
    # dashboard restart), hard-exit so this child can't keep trading orphaned.
    if parent_pid:
        import os
        import threading
        import time as _time

        def _watch_parent():
            while True:
                _time.sleep(2)
                if not _pid_alive(parent_pid):
                    os._exit(1)

        threading.Thread(target=_watch_parent, daemon=True).start()
    asyncio.run(start_bot(config_path))


def _read_live_bot_pid(pidfile):
    """Return a live *other* bot_runner PID from the pidfile, else None."""
    import os

    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid == os.getpid():
        return None
    return pid if _pid_alive(pid) else None


def run_all_bots():
    """Run all bots defined in YAML files in the 'bots' directory."""
    import os

    bot_dir = Path("bots")
    if not bot_dir.exists():
        logging.error(f"Bot directory '{bot_dir}' not found")
        return

    bot_files = list(bot_dir.glob("*.yaml"))
    if not bot_files:
        logging.error(f"No bot configuration files found in '{bot_dir}'")
        return

    # Single-owner lock: refuse to start if another bot_runner is already live
    # (prevents two bots trading the same wallet after a restart/mixup).
    pidfile = Path(".bot.pid")
    other = _read_live_bot_pid(pidfile)
    if other:
        logging.error(
            f"Otro bot_runner ya está corriendo (PID {other}); no arranco un segundo."
        )
        return
    try:
        pidfile.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        logging.warning("No pude escribir .bot.pid")

    logging.info(f"Found {len(bot_files)} bot configuration files")

    processes = []
    en_proceso: list[str] = []
    skipped_bots = 0

    for file in bot_files:
        try:
            cfg = load_bot_config(str(file))
            bot_name = cfg.get("name", file.stem)

            # Skip bots with enabled=False
            if not cfg.get("enabled", True):
                logging.info(f"Skipping disabled bot '{bot_name}'")
                skipped_bots += 1
                continue

            # Validate platform configuration
            try:
                platform = get_platform_from_config(cfg)

                # Check platform support
                from platforms import platform_factory

                if not platform_factory.registry.is_platform_supported(platform):
                    logging.error(
                        f"Platform {platform.value} is not supported for bot '{bot_name}'. Available platforms: {[p.value for p in platform_factory.get_supported_platforms()]}"
                    )
                    skipped_bots += 1
                    continue

                # Validate listener compatibility
                listener_type = cfg["filters"]["listener_type"]
                if not validate_platform_listener_combination(platform, listener_type):
                    from config_loader import get_supported_listeners_for_platform

                    supported = get_supported_listeners_for_platform(platform)
                    logging.error(
                        f"Listener '{listener_type}' is not compatible with platform '{platform.value}' for bot '{bot_name}'. Supported listeners: {supported}"
                    )
                    skipped_bots += 1
                    continue

            except Exception as e:
                logging.exception(
                    f"Invalid platform configuration for bot '{bot_name}': {e}. Skipping..."
                )
                skipped_bots += 1
                continue

            # Start bot in separate process or main process
            if cfg.get("separate_process", False):
                logging.info(
                    f"Starting bot '{bot_name}' ({platform.value}) in separate process"
                )
                p = multiprocessing.Process(
                    target=run_bot_process,
                    args=(str(file), os.getpid()),
                    name=f"bot-{bot_name}",
                )
                p.daemon = True
                p.start()
                processes.append(p)
            else:
                # NO se arranca aqui: `asyncio.run` bloquea hasta que el bot
                # termine, y un bot no termina nunca. Arrancarlo en este punto
                # hacia que el bucle no llegara nunca al segundo ni al tercer
                # fichero: de tres estrategias configuradas corria UNA sola, en
                # silencio y sin ningun error. Se apuntan y al final se lanzan
                # todas juntas con asyncio.gather.
                logging.info(
                    f"Starting bot '{bot_name}' ({platform.value}) in main process"
                )
                en_proceso.append(str(file))

        except Exception as e:
            logging.exception(f"Failed to start bot from {file}: {e}")
            skipped_bots += 1

    logging.info(
        f"Started {len(bot_files) - skipped_bots} bots, skipped {skipped_bots} disabled/invalid bots"
    )

    # Wait for all processes to complete
    # Todos los bots que van en este proceso, a la vez. Si uno se cae, se
    # registra pero los demas siguen: return_exceptions evita que una
    # excepcion en uno mate a los otros dos.
    if en_proceso:
        async def _todos() -> None:
            res = await asyncio.gather(
                *(start_bot(f) for f in en_proceso), return_exceptions=True
            )
            for f, r in zip(en_proceso, res, strict=False):
                if isinstance(r, Exception):
                    logging.error(f"Bot {f} murio: {r!r}")

        asyncio.run(_todos())

    for p in processes:
        p.join()
        logging.info(f"Process {p.name} completed")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Log supported platforms and listeners
    try:
        from platforms import platform_factory

        supported_platforms = platform_factory.get_supported_platforms()
        logging.info(f"Supported platforms: {[p.value for p in supported_platforms]}")

        # Log listener compatibility for each platform
        from config_loader import get_supported_listeners_for_platform

        for platform in supported_platforms:
            listeners = get_supported_listeners_for_platform(platform)
            logging.info(f"Platform {platform.value} supports listeners: {listeners}")

    except Exception as e:
        logging.warning(f"Could not load platform information: {e}")

    run_all_bots()


if __name__ == "__main__":
    main()
