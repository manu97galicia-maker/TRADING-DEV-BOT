"""Read-only readiness checks. No signing, swap construction or transaction sends."""

# Checks report only known labels and public addresses, never exception text/URLs.
# ruff: noqa: PLC0415, TRY003, BLE001

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from dotenv import dotenv_values

if TYPE_CHECKING:
    from pathlib import Path

MAINNET_GENESIS = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
READ_METHODS = frozenset(
    {
        "getHealth",
        "getGenesisHash",
        "getBalance",
        "getTokenLargestAccounts",
        "getMultipleAccounts",
        "getTokenAccountsByOwner",
        "getSignaturesForAddress",
    }
)
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL = "So11111111111111111111111111111111111111112"


def _environment(plan: dict, config_path: Path) -> dict:
    path = config_path.resolve().parent / plan.get("env_file", "../.env")
    values = dotenv_values(path, interpolate=False) if path.exists() else {}
    return {
        **os.environ,
        **{key: value for key, value in values.items() if value is not None},
    }


def _record(report: dict, name: str, status: str, detail: str) -> None:
    report["checks"].append({"name": name, "status": status, "detail": detail})


def _finish(report: dict) -> dict:
    failed = any(check["status"] == "fail" for check in report["checks"])
    warnings = any(check["status"] == "warning" for check in report["checks"])
    report["ready_for_manual_start"] = report["online"] and not failed
    report["status"] = (
        "blocked"
        if failed
        else "offline_only"
        if not report["online"]
        else "ready_with_warnings"
        if warnings
        else "ready"
    )
    return report


def inspect_local(plan: dict, config_path: Path) -> dict:
    """Read credentials locally and report public identity and missing prerequisites."""
    env = _environment(plan, config_path)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "online": False,
        "execution": "read_only_preflight",
        "live_started": False,
        "enabled": bool(plan.get("enabled")),
        "checks": [],
    }
    _record(
        report, "private_purchase_alerts",
        "pass" if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID") else "warning",
        "Bot y destinatario de Telegram configurados; entrega aún no comprobada"
        if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")
        else "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID para enviar cada compra",
    )
    missing = [
        name
        for name in ("aiohttp", "websockets", "solders", "telethon")
        if importlib.util.find_spec(name) is None
    ]
    _record(
        report,
        "dependencies",
        "fail" if missing else "pass",
        ", ".join(missing) if missing else "Paquetes disponibles",
    )
    expected = plan["four_strategies"]["expected_wallet"]
    public = None
    try:
        from solders.keypair import Keypair

        public = str(
            Keypair.from_base58_string(env.get("SOLANA_PRIVATE_KEY", "")).pubkey()
        )
    except Exception:
        _record(
            report,
            "private_key_format",
            "fail",
            "Clave local ausente o formato no válido",
        )
    report["expected_wallet"] = expected
    report["configured_public_wallet"] = public
    _record(
        report,
        "wallet_identity",
        "pass" if public == expected else "fail",
        "La clave corresponde a la wallet solicitada"
        if public == expected
        else "La wallet configurada no coincide con la solicitada",
    )
    for name, variable, prefix in (
        ("rpc_config", "SOLANA_NODE_RPC_ENDPOINT", "https://"),
        ("wss_config", "SOLANA_NODE_WSS_ENDPOINT", "wss://"),
    ):
        _record(
            report,
            name,
            "pass" if env.get(variable, "").startswith(prefix) else "fail",
            "Variable configurada con transporte cifrado"
            if env.get(variable, "").startswith(prefix)
            else f"Falta una dirección válida en {variable}",
        )
    session = plan["filters"]["telegram_calls"]["session_name"]
    session_path = config_path.resolve().parent.parent / (session + ".session")
    tg_present = bool(
        env.get("TELEGRAM_API_ID")
        and env.get("TELEGRAM_API_HASH")
        and session_path.exists()
    )
    _record(
        report,
        "telegram_local",
        "pass" if tg_present else "warning",
        "Credenciales y archivo de sesión presentes; autorización por verificar"
        if tg_present
        else "Telegram no estará disponible hasta preparar credenciales y sesión",
    )
    _record(
        report,
        "jupiter_api_key",
        "pass" if env.get("JUPITER_API_KEY") else "warning",
        "Clave API presente"
        if env.get("JUPITER_API_KEY")
        else "Modo sin clave: 0,5 peticiones por segundo; salida migrada más lenta",
    )
    _record(
        report,
        "activation",
        "warning" if not plan.get("enabled") else "pass",
        "Deshabilitado: requiere activación manual"
        if not plan.get("enabled")
        else "Configuración habilitada; el diagnóstico no inicia el bot",
    )
    publication = plan["four_strategies"].get("callout_publication", {})
    if publication.get("enabled"):
        profile = config_path.resolve().parent.parent / publication["profile_directory"]
        prepared = (profile / "publisher-ready.json").exists()
        available = importlib.util.find_spec("playwright") is not None
        _record(
            report,
            "callout_publisher_local",
            "pass" if prepared and available else "warning",
            "Perfil del publicador preparado; se verificará la cuenta al arrancar"
            if prepared and available
            else "Publicador pendiente de sesión propia: ejecutar configurar-callouts.bat",
        )
    return _finish(report)


async def inspect_online(plan: dict, config_path: Path) -> dict:  # noqa: C901, PLR0915 -- independent read-only checks
    """Verify configured services with read methods only; never start a trader."""
    import aiohttp
    import websockets

    from trading.jupiter_access import API_BASE

    report = inspect_local(plan, config_path)
    report["online"] = True
    env = _environment(plan, config_path)
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15)
    ) as session:

        async def rpc(method: str, params: list) -> object:
            if method not in READ_METHODS:
                raise ValueError("Preflight permits only read methods")
            async with session.post(
                env["SOLANA_NODE_RPC_ENDPOINT"],
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            ) as response:
                response.raise_for_status()
                data = await response.json()
            if data.get("error") or data.get("result") is None:
                raise ValueError("Read method failed")
            return data["result"]

        try:
            health = await rpc("getHealth", [])
            genesis = await rpc("getGenesisHash", [])
            report["rpc_genesis_hash"] = genesis
            _record(
                report,
                "rpc_health",
                "pass" if health == "ok" else "fail",
                "RPC responde",
            )
            _record(
                report,
                "mainnet",
                "pass" if genesis == MAINNET_GENESIS else "fail",
                "Cluster verificado por genesis hash",
            )
            owner = report["configured_public_wallet"]
            if owner:
                balance = await rpc("getBalance", [owner, {"commitment": "finalized"}])
                lamports = int(balance["value"])
                report["confirmed_sol_balance"] = lamports / 1e9
                from trading.buy_budget import inclusive_balance_entry

                amount, budget = inclusive_balance_entry(
                    lamports,
                    plan["trade"]["fraccion_saldo"],
                    plan["trade"]["buy_slippage"],
                    3_000_000,
                )
                enough = amount > 0 and budget <= lamports - int(
                    plan["trade"]["min_reserve_sol"] * 1e9
                )
                _record(
                    report,
                    "native_reserve",
                    "pass" if enough else "fail",
                    "Saldo superior a la reserva"
                    if enough
                    else "Saldo insuficiente para conservar la reserva",
                )
        except Exception as error:
            _record(
                report,
                "rpc_online",
                "fail",
                f"Comprobación fallida: {type(error).__name__}",
            )
        if plan["four_strategies"].get("entry_protection", {}).get("enabled"):
            try:
                probe_mint = plan["four_strategies"]["entry_protection"][
                    "rpc_probe_mint"
                ]
                largest = await rpc(
                    "getTokenLargestAccounts", [probe_mint, {"commitment": "processed"}]
                )
                snapshot = await rpc(
                    "getMultipleAccounts",
                    [
                        [probe_mint],
                        {
                            "encoding": "jsonParsed",
                            "commitment": "processed",
                            "minContextSlot": largest["context"]["slot"],
                        },
                    ],
                )
                owned = await rpc(
                    "getTokenAccountsByOwner",
                    [
                        report["configured_public_wallet"],
                        {"mint": probe_mint},
                        {"encoding": "jsonParsed", "commitment": "processed"},
                    ],
                )
                available = bool(
                    largest["value"]
                    and snapshot["value"][0]
                    and isinstance(owned["value"], list)
                )
                _record(
                    report,
                    "entry_protection_rpc",
                    "pass" if available else "fail",
                    "Métodos de lectura para concentración disponibles"
                    if available
                    else "Datos de concentración incompletos",
                )
            except Exception as error:
                _record(
                    report,
                    "entry_protection_rpc",
                    "fail",
                    f"Consulta de concentración no disponible: {type(error).__name__}",
                )
        if float(plan["trade"].get("sin_volumen_bajo_entrada_seg", 0)) > 0:
            try:
                history = await rpc(
                    "getSignaturesForAddress",
                    [plan["four_strategies"]["expected_wallet"], {"commitment": "confirmed", "limit": 1}],
                )
                _record(
                    report, "inactivity_history_rpc", "pass" if isinstance(history, list) else "fail",
                    "Consulta de historial disponible; esta prueba no afirma ausencia de volumen",
                )
            except Exception as error:
                _record(report, "inactivity_history_rpc", "fail", f"Historial no disponible: {type(error).__name__}")
        try:
            async with websockets.connect(
                env["SOLANA_NODE_WSS_ENDPOINT"], open_timeout=12
            ) as websocket:
                await websocket.send(
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "slotSubscribe"})
                )
                reply = json.loads(await asyncio.wait_for(websocket.recv(), timeout=12))
                if reply.get("error") or "result" not in reply:
                    raise ValueError("Subscription rejected")  # noqa: TRY301
                await websocket.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "slotUnsubscribe",
                            "params": [reply["result"]],
                        }
                    )
                )
            _record(
                report, "websocket", "pass", "Suscripción de lectura aceptada y cerrada"
            )
        except Exception as error:
            _record(
                report,
                "websocket",
                "fail",
                f"Comprobación fallida: {type(error).__name__}",
            )
        try:
            headers = (
                {"x-api-key": env["JUPITER_API_KEY"]}
                if env.get("JUPITER_API_KEY")
                else {}
            )
            params = {
                "inputMint": WSOL,
                "outputMint": USDC,
                "amount": "1000000",
                "slippageBps": "50",
            }
            async with session.get(
                f"{API_BASE}/swap/v1/quote", params=params, headers=headers
            ) as response:
                response.raise_for_status()
                quote = await response.json()
            if int(quote.get("outAmount", 0)) <= 0:
                raise ValueError("No valid route")  # noqa: TRY301
            _record(
                report,
                "jupiter_quote",
                "pass",
                "Cotización de lectura disponible; no se construyó ni firmó ninguna transacción",
            )
        except Exception as error:
            _record(
                report,
                "jupiter_quote",
                "fail",
                f"Comprobación fallida: {type(error).__name__}",
            )
        try:
            async with session.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{WSOL}"
            ) as response:
                response.raise_for_status()
                pairs = (await response.json()).get("pairs") or []
            valid = [
                p
                for p in pairs
                if (p.get("baseToken") or {}).get("address") == WSOL
                and float(p.get("priceUsd") or 0) > 0
            ]
            _record(
                report,
                "sol_usd_reference",
                "pass" if valid else "fail",
                "Fuente de cotización SOL/USD comprobada",
            )
        except Exception as error:
            _record(
                report,
                "sol_usd_reference",
                "fail",
                f"Comprobación fallida: {type(error).__name__}",
            )
    await _telegram_check(plan, config_path, env, report)
    return _finish(report)


async def _telegram_check(
    plan: dict, config_path: Path, env: dict, report: dict
) -> None:
    config = plan["filters"]["telegram_calls"]
    session_path = config_path.resolve().parent.parent / config["session_name"]
    if (
        not session_path.with_suffix(".session").exists()
        or not env.get("TELEGRAM_API_ID")
        or not env.get("TELEGRAM_API_HASH")
    ):
        _record(
            report,
            "telegram_access",
            "warning",
            "No hay una sesión existente para comprobar; las tres fuentes on-chain pueden continuar",
        )
        return
    client = None
    try:
        from telethon import TelegramClient

        client = TelegramClient(
            str(session_path),
            int(env["TELEGRAM_API_ID"]),
            env["TELEGRAM_API_HASH"],
            connection_retries=0,
        )
        await asyncio.wait_for(client.connect(), timeout=15)
        if not await asyncio.wait_for(client.is_user_authorized(), timeout=10):
            _record(
                report,
                "telegram_access",
                "warning",
                "Sesión no autorizada; no se intentó iniciar sesión",
            )
            return
        available = []
        for channel in config["channels"]:
            try:
                await asyncio.wait_for(client.get_entity(channel), timeout=10)
                available.append(channel)
            except Exception:
                report.setdefault("telegram_channels_unavailable", []).append(channel)
        report["telegram_channels_available"] = available
        _record(
            report,
            "telegram_access",
            "pass" if len(available) == len(config["channels"]) else "warning",
            f"Canales accesibles: {len(available)}/{len(config['channels'])}",
        )
    except Exception as error:
        _record(
            report,
            "telegram_access",
            "warning",
            f"Comprobación fallida: {type(error).__name__}; no se inició sesión",
        )
    finally:
        if client is not None:
            await client.disconnect()
