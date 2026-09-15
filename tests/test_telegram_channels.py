"""Tests de la resolucion de canales de Telegram.

El riesgo que cubren: Telethon revienta al arrancar si UNO de los nombres
de ``chats=`` no se puede resolver, y eso se llevaba por delante la fuente
entera. Anadir MoonTok al lado de Nitro hacia ese fallo mas probable.
"""

# ruff: noqa: S101

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import yaml

from monitoring.telegram_watcher import resolve_channels


def _resolver(validos: set[str]) -> Callable[[str | int], Awaitable[object]]:
    """Devuelve un resolvedor falso que imita a Telethon: o entidad o error."""

    async def resolver(canal: str | int) -> object:
        if canal not in validos:
            msg = f"Cannot find any entity corresponding to {canal!r}"
            raise ValueError(msg)
        return f"entidad:{canal}"

    return resolver


def test_un_canal_malo_no_se_lleva_por_delante_a_los_buenos() -> None:
    """Es el caso real: MoonTok mal escrito no debe callar a Nitro."""
    resueltos, fallidos = asyncio.run(
        resolve_channels(
            _resolver({"dextools_nitro"}), ["dextools_nitro", "canal_inventado"]
        )
    )
    assert resueltos == ["entidad:dextools_nitro"]
    assert fallidos == ["canal_inventado"]


def test_todos_buenos_se_resuelven_todos() -> None:
    resueltos, fallidos = asyncio.run(
        resolve_channels(
            _resolver({"dextools_nitro", "cryptolistings"}),
            ["dextools_nitro", "cryptolistings"],
        )
    )
    assert resueltos == ["entidad:dextools_nitro", "entidad:cryptolistings"]
    assert fallidos == []


def test_todos_malos_no_devuelve_ninguno() -> None:
    """Quien llama usa esta lista vacia para NO caer en 'todos los chats'."""
    resueltos, fallidos = asyncio.run(
        resolve_channels(_resolver(set()), ["uno", "otro"])
    )
    assert resueltos == []
    assert fallidos == ["uno", "otro"]


def test_sin_canales_configurados_no_resuelve_nada() -> None:
    """None significa 'todos los chats', y eso lo decide quien llama."""
    assert asyncio.run(resolve_channels(_resolver(set()), None)) == ([], [])
    assert asyncio.run(resolve_channels(_resolver(set()), [])) == ([], [])


def test_una_entidad_nula_cuenta_como_fallo() -> None:
    """Telethon puede devolver None en vez de lanzar; es igual de inservible."""

    async def resolver(_canal: str | int) -> object:
        return None

    resueltos, fallidos = asyncio.run(resolve_channels(resolver, ["x"]))
    assert resueltos == []
    assert fallidos == ["x"]


def test_los_canales_configurados_son_los_pedidos() -> None:
    """Cada canal de esta lista es una fuente de compra con dinero real, asi
    que anadir o quitar uno se ve aqui en rojo.

    Van mezclados dos tipos y conviene saberlo al leer resultados:
      - dextools_nitro, cryptolistings: hay un PAGO detras (Nitro, listing).
      - Wagbocooks, LegendsAlphas: son CALLS, alguien opina sin pagar.

    Desde el 14-sep cada compra apunta de que canal vino, asi que
    `deploy/resumen_dia.py` los separa y se puede ver cual gana.
    """
    cfg = yaml.safe_load(
        (
            Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
        ).read_text(encoding="utf-8")
    )
    canales = cfg["filters"]["telegram_calls"]["channels"]
    assert canales == [
        "dextools_nitro",
        "cryptolistings",
        "Wagbocooks",
        "LegendsAlphas",
    ]
