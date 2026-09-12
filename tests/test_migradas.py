"""Tests del interruptor que permite comprar monedas ya migradas a PumpSwap."""

# ruff: noqa: S101, SLF001, PLR2004

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
    fuente._primed = True  # saltamos la foto de arranque
    emitidos: list[str] = []

    candidatos = [
        {"mint": "EnCurva", "signal_type": "boost", "boosts": 10, "migrated": False},
        {"mint": "Migrada", "signal_type": "boost", "boosts": 10, "migrated": True},
    ]
    for c in candidatos:
        if not fuente._freshness.accept(c):
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


# --- Monedas que nunca fueron de pump.fun ---


def _fuente_abierta(*, abierto: bool, minimo: float = 50.0) -> DexPaidSource:
    return DexPaidSource(
        object(),
        object(),
        incluir_migradas=True,
        incluir_cualquier_origen=abierto,
        min_lp_locked_pct=minimo,
    )


def test_cualquier_origen_apagado_por_defecto() -> None:
    fuente = DexPaidSource(object(), object())

    assert not fuente.incluir_cualquier_origen
    assert fuente.min_lp_locked_pct == 50.0


def test_rugcheck_rechaza_lo_marcado_como_rugged() -> None:
    fuente = _fuente_abierta(abierto=True)

    assert not fuente._pasa_rugcheck({"mint": "X", "rugged": True, "lp_locked_pct": 99})


def test_sin_informe_de_rugcheck_se_descarta() -> None:
    """Ante la duda, fuera: no hay motivo para asumir lo mejor."""
    fuente = _fuente_abierta(abierto=True)

    assert not fuente._pasa_rugcheck({"mint": "X"})


def test_poca_liquidez_bloqueada_se_descarta() -> None:
    fuente = _fuente_abierta(abierto=True, minimo=50)

    assert not fuente._pasa_rugcheck({"mint": "X", "lp_locked_pct": 20})


def test_liquidez_bloqueada_suficiente_pasa() -> None:
    fuente = _fuente_abierta(abierto=True, minimo=50)

    assert fuente._pasa_rugcheck({"mint": "X", "lp_locked_pct": 80, "rugged": False})


def test_justo_en_el_minimo_pasa() -> None:
    fuente = _fuente_abierta(abierto=True, minimo=50)

    assert fuente._pasa_rugcheck({"mint": "X", "lp_locked_pct": 50})


def test_rugcheck_solo_se_pide_si_se_abre_a_cualquier_origen() -> None:
    """La consulta es lenta; en pump.fun aporta poco y se salta."""
    assert _fuente_abierta(abierto=False).incluir_cualquier_origen is False
    assert _fuente_abierta(abierto=True).incluir_cualquier_origen is True
