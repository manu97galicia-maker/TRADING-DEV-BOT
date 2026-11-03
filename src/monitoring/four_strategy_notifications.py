"""Purchase explanations and optional token labels; no trading or broadcasting."""

from __future__ import annotations

import asyncio
import math

from monitoring.notifier import build_purchase_notification
from trading.second_block_policy import STRATEGY_ID, position_exit_plan
from utils.logger import get_logger

logger = get_logger(__name__)


def entry_reason(features: dict) -> str:
    """Describe observed entry evidence, without inventing profitability claims."""
    strategy = features.get("strategy")
    if strategy == "telegram-immediate":
        return f"Señal reciente de Telegram: {features.get('source') or 'canal configurado'}"
    if strategy == "first-block-four-buyers":
        block = features.get("first_block") or {}
        return (
            f"Primer bloque: {int(block.get('buyers', 0))} compradores distintos, "
            f"{float(block.get('buy_sol', 0)):.3f} SOL comprados"
        )
    if strategy == STRATEGY_ID:
        history = features.get("creator_history") or {}
        minimum = "al menos " if history.get("migration_fraction_is_lower_bound") else ""
        return (
            f"Segundo bloque observado: {int(features.get('buyers', 0))} wallets compradoras "
            f"en el primero, volumen ${float(features.get('volume_usd', 0)):,.0f}; "
            f"dev firmante con {minimum}{int(history.get('migrations', 0))}/{int(history.get('launches', 0))} "
            "migraciones PumpSwap verificadas (no garantiza inclusión en ese bloque)"
        )
    label = "Cruce alcista de 21K" if strategy == "strength-21k" else "Presión compradora desde 8K"
    return (
        f"{label}: {int(features.get('buyers', 0))} compradores distintos, "
        f"volumen ${float(features.get('volume_usd', 0)):,.0f}/8s; "
        f"compras {float(features.get('buy_sol', 0)):.3f} SOL / "
        f"ventas {float(features.get('sell_sol', 0)):.3f} SOL"
    )


async def token_labels(token: object, rpc: object) -> tuple[str, str]:
    """Use known labels first; an optional DAS read must not delay exits."""
    mint = str(token.mint)
    name, symbol = str(token.name or ""), str(token.symbol or "")
    if name and name != mint and symbol not in {"", "?"}:
        return name, symbol
    try:
        asset = await asyncio.wait_for(rpc("getAsset", {"id": mint}), timeout=2)
        if isinstance(asset, dict) and asset.get("id") == mint:
            metadata = (asset.get("content") or {}).get("metadata") or {}
            for key in ("name", "symbol"):
                value = metadata.get(key)
                if isinstance(value, str):
                    value = " ".join(value.replace("\x00", "").split())[:120]
                    if value:
                        if key == "name":
                            name = value
                        else:
                            symbol = value
    except Exception as error:  # noqa: BLE001 -- labels are optional
        logger.debug("Optional token label unavailable (%s)", type(error).__name__)
    if not name or name == mint:
        name = "Nombre no disponible"
    return name, "" if symbol == "?" else symbol


def purchase_message(token: object, result: object, details: dict, exits: dict) -> str:
    features = details.get("features") or {}
    sol_usd = float(details.get("entry_sol_usd") or 0)
    mc = float(result.price or 0) * 1e9 * sol_usd
    tiers, stop = position_exit_plan({**details, "entry_price": float(result.price or 0)}, exits) if math.isfinite(mc) and mc > 0 else ([], float(exits["stop_loss_fraction"]))
    return build_purchase_notification(
        symbol=token.symbol,
        name=token.name,
        mint=str(token.mint),
        amount_sol=float(result.price or 0) * float(result.amount or 0),
        source_data={"acquisition_source": entry_reason(features)},
        market_cap_usd=mc,
        strategy=str(features.get("strategy") or "four-strategies"),
        stop_loss_pct=stop*100,
        entry_price_sol=float(result.price or 0),
        take_profit_tiers=[(tier.gain_from_entry, tier.sell_original_fraction) for tier in tiers],
    )
