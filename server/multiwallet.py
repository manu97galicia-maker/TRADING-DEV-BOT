"""Wallets secundarias para repartir operaciones sin filtrar claves al navegador.

Las claves se generan y guardan en ``state/wallets.json`` con permisos 600.
Todas las respuestas contienen sólo direcciones públicas y resultados de red.
La funcionalidad comparte el mismo interruptor y límites de la wallet caliente.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from . import config, wallet


class MultiWalletError(wallet.WalletError):
    """Configuración o petición inválida de wallets secundarias."""


_STORE_LOCK = asyncio.Lock()


def _path() -> Path:
    return config.STATE_DIR / "wallets.json"


def _load() -> list[dict[str, str]]:
    path = _path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MultiWalletError("No se pudo leer state/wallets.json") from exc
    if not isinstance(data, list):
        raise MultiWalletError("state/wallets.json tiene un formato inválido")
    rows = []
    for item in data:
        if isinstance(item, dict) and item.get("address") and item.get("secret"):
            rows.append({"address": str(item["address"]), "secret": str(item["secret"])})
    return rows


def _save(rows: list[dict[str, str]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2)
    tmp.replace(path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _count(value: Any, *, allow_zero: bool = False) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MultiWalletError("El número de wallets no es válido") from exc
    minimum = 0 if allow_zero else 1
    if not minimum <= number <= config.MAX_SECONDARY_WALLETS:
        raise MultiWalletError(
            f"El número de wallets debe estar entre {minimum} y {config.MAX_SECONDARY_WALLETS}"
        )
    return number


def _selected(count: Any) -> list[tuple[dict[str, str], Any]]:
    rows = _load()
    number = _count(count)
    if len(rows) < number:
        raise MultiWalletError(f"Sólo hay {len(rows)} wallets creadas; pediste {number}")
    return [(row, wallet.keypair_from_secret(row["secret"])) for row in rows[:number]]


async def list_wallets() -> list[dict[str, Any]]:
    rows = _load()

    async def snapshot(index: int, row: dict[str, str]) -> dict[str, Any]:
        try:
            balance = await wallet.sol_balance(wallet.keypair_from_secret(row["secret"]))
            error = None
        except wallet.WalletError as exc:
            balance, error = 0.0, str(exc)
        return {
            "nombre": f"Wallet {index + 1}",
            "address": row["address"],
            "sol": round(balance, 9),
            "operativa": balance > config.SECONDARY_FEE_RESERVE_SOL,
            **({"error": error} if error else {}),
        }

    return list(await asyncio.gather(*(snapshot(i, row) for i, row in enumerate(rows))))


async def create_wallets(count: Any) -> list[dict[str, Any]]:
    wallet._require_hot()
    number = _count(count)
    try:
        from solders.keypair import Keypair  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise MultiWalletError("Falta 'solders' para crear wallets") from exc
    async with _STORE_LOCK:
        rows = _load()
        if len(rows) + number > config.MAX_SECONDARY_WALLETS:
            raise MultiWalletError(f"El máximo es {config.MAX_SECONDARY_WALLETS}; ya existen {len(rows)}")
        for _ in range(number):
            keypair = Keypair()
            rows.append({"address": str(keypair.pubkey()), "secret": json.dumps(list(bytes(keypair)))})
        _save(rows)
    return await list_wallets()


async def _summarize(tasks: list[Awaitable[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[str]]:
    results = await asyncio.gather(*tasks, return_exceptions=True)
    good: list[dict[str, Any]] = []
    errors: list[str] = []
    for result in results:
        if isinstance(result, BaseException):
            errors.append(str(result))
        else:
            good.append(result)
    return good, errors


async def spread(total_sol: float, count: Any) -> dict[str, Any]:
    wallet._require_hot()
    selected = _selected(count)
    if total_sol <= 0 or total_sol > config.MAX_SPREAD_SOL:
        raise MultiWalletError(f"El reparto debe ser mayor que 0 y máximo {config.MAX_SPREAD_SOL} SOL")
    per_wallet = total_sol / len(selected)
    if per_wallet <= config.SECONDARY_FEE_RESERVE_SOL:
        raise MultiWalletError("El importe por wallet no cubre la reserva mínima para comisiones")
    primary = wallet.primary_keypair()
    available = await wallet.sol_balance(primary)
    network_reserve = 0.00001 * len(selected)
    if available < total_sol + network_reserve:
        raise MultiWalletError(
            f"Saldo insuficiente: hay {available:.6f} SOL y hacen falta "
            f"{total_sol + network_reserve:.6f} SOL con comisiones"
        )
    good, errors = await _summarize(
        [wallet.send_sol_with_keypair(primary, row["address"], per_wallet) for row, _ in selected]
    )
    return {
        "repartido": round(sum(float(item["sol"]) for item in good), 9),
        "por_wallet": round(per_wallet, 9),
        "ok": len(good),
        "fallos": len(errors),
        "errores": errors,
        "transacciones": good,
    }


async def collect() -> dict[str, Any]:
    wallet._require_hot()
    primary_address = str(wallet.primary_keypair().pubkey())
    tasks: list[Awaitable[dict[str, Any]]] = []
    for row in _load():
        keypair = wallet.keypair_from_secret(row["secret"])
        balance = await wallet.sol_balance(keypair)
        amount = balance - config.SECONDARY_FEE_RESERVE_SOL - 0.00001
        if amount > 0:
            tasks.append(wallet.send_sol_with_keypair(keypair, primary_address, amount))
    good, errors = await _summarize(tasks)
    return {
        "recogido": round(sum(float(item["sol"]) for item in good), 9),
        "ok": len(good),
        "fallos": len(errors),
        "errores": errors,
        "transacciones": good,
    }


async def buy(mint: str, total_sol: float, count: Any) -> dict[str, Any]:
    wallet._require_hot()
    selected = _selected(count)
    mint = mint.strip()
    if not mint:
        raise MultiWalletError("Falta el contrato del token")
    if total_sol <= 0 or total_sol > config.MAX_BUY_SOL:
        raise MultiWalletError(f"La compra total debe ser mayor que 0 y máximo {config.MAX_BUY_SOL} SOL")
    per_wallet = total_sol / len(selected)
    funded = []
    for row, keypair in selected:
        balance = await wallet.sol_balance(keypair)
        if balance >= per_wallet + config.SECONDARY_FEE_RESERVE_SOL:
            funded.append((row, keypair))
    if not funded:
        raise MultiWalletError("Ninguna wallet seleccionada tiene saldo suficiente para comprar y pagar fees")
    good, errors = await _summarize(
        [wallet.buy_with_keypair(keypair, mint, per_wallet) for _, keypair in funded]
    )
    return {
        "gastado": round(sum(float(item["spent_sol"]) for item in good), 9),
        "ok": len(good),
        "fallos": len(errors) + len(selected) - len(funded),
        "errores": errors,
        "transacciones": good,
    }


async def sell(mint: str, percent: float, count: Any) -> dict[str, Any]:
    wallet._require_hot()
    selected = _selected(count)
    mint = mint.strip()
    if not mint:
        raise MultiWalletError("Falta el contrato del token")
    if not 0 < percent <= 100:
        raise MultiWalletError("El porcentaje debe estar entre 0 y 100")
    good, errors = await _summarize(
        [wallet.sell_with_keypair(keypair, mint, percent) for _, keypair in selected]
    )
    return {
        "recibido": round(sum(float(item.get("expected_sol", 0)) for item in good), 9),
        "ok": len(good),
        "fallos": len(errors),
        "errores": errors,
        "transacciones": good,
    }
