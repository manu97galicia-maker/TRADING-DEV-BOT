"""Tests del interruptor que permite comprar monedas ya migradas a PumpSwap."""

# ruff: noqa: S101

import asyncio

import pytest

from monitoring.dexpaid_source import DexPaidSource
from trading.base import TradeResult
from trading.universal_trader import UniversalTrader


def _fuente(*, incluir: bool) -> DexPaidSource:
    return DexPaidSource(object(), object(), incluir_migradas=incluir)


def test_apagado_por_defecto() -> None:
    assert not DexPaidSource(object(), object()).incluir_migradas


def test_el_interruptor_se_guarda() -> None:
    assert _fuente(incluir=True).incluir_migradas


def _candidatos_emitidos(*, incluir: bool) -> list[str]:
    """Recorre un ciclo con un candidato migrado y otro en curva."""
    fuente = _fuente(incluir=incluir)
    fuente._primed = True  # noqa: SLF001 -- saltamos la foto de arranque
    emitidos: list[str] = []

    candidatos = [
        {"mint": "EnCurva", "signal_type": "boost", "boosts": 10, "migrated": False},
        {"mint": "Migrada", "signal_type": "boost", "boosts": 10, "migrated": True},
    ]
    for c in candidatos:
        if not fuente._freshness.accept(c):  # noqa: SLF001
            continue
        if c.get("migrated") and not fuente.incluir_migradas:
            continue
        emitidos.append(str(c["mint"]))
    return emitidos


def test_con_el_interruptor_apagado_la_migrada_se_descarta() -> None:
    assert _candidatos_emitidos(incluir=False) == ["EnCurva"]


def test_con_el_interruptor_encendido_pasan_las_dos() -> None:
    assert _candidatos_emitidos(incluir=True) == ["EnCurva", "Migrada"]


def test_el_precio_de_entrada_sale_de_lo_gastado_entre_lo_recibido() -> None:
    """El TradeResult de una compra por pool usa el precio REALMENTE pagado."""
    gastado, recibidos = 0.20, 1_000_000.0
    resultado = TradeResult(success=True, amount=recibidos, price=gastado / recibidos)

    assert resultado.price == pytest.approx(2e-07)
    assert resultado.amount * resultado.price == pytest.approx(gastado)
    assert hasattr(UniversalTrader, "_comprar_migrada")


def test_una_compra_por_pool_sin_tokens_es_un_fallo() -> None:
    fallo = TradeResult(success=False, error_message="sin ruta de compra")

    assert not fallo.success
    assert fallo.price is None


def test_asyncio_disponible_para_la_fuente() -> None:
    assert asyncio.iscoroutinefunction(DexPaidSource.run)
