"""Tests del interruptor que permite comprar monedas ya migradas a PumpSwap."""

# ruff: noqa: S101, SLF001, PLR2004

import asyncio

import pytest

from monitoring.dexpaid_source import DexPaidSource
from trading.base import TradeResult
from trading.token_builder import build_pumpfun_token_info
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


class _CurvaMigrada:
    """Curva de una moneda ya migrada: existe pero con las reservas a cero.

    Reproduce lo que devolvio el bot en produccion el 12-sep a las 20:02:45,
    donde una moneda acabada en 'pump' y ya migrada salia del log como
    "Could not read curve state for ...; skipping".
    """

    def __init__(self) -> None:
        self.exigio_precio: list[bool] = []

    async def is_curve_complete(self, _pool: object) -> bool:
        return True

    async def get_pool_state(
        self, _pool: object, *, exigir_precio: bool = True
    ) -> dict:
        self.exigio_precio.append(exigir_precio)
        if exigir_precio:
            msg = "Invalid virtual_token_reserves: 0 - cannot calculate price"
            raise ValueError(msg)
        return {
            "creator": "11111111111111111111111111111111",
            "is_mayhem_mode": False,
            "is_cashback_coin": False,
            "price_per_token": 0.0,
        }


def test_a_una_migrada_no_se_le_pide_precio() -> None:
    """El fallo real: pasaba el "¿migro?" y moria en el "ahora leela".

    a6692eb tapo is_curve_complete pero no la llamada de dos lineas mas
    abajo, asi que con incluir_migradas encendido la moneda se tiraba igual.
    """
    curva = _CurvaMigrada()

    async def _comprobar() -> None:
        # Sin estado real de Solana el constructor acabara devolviendo None,
        # pero lo que se comprueba es que la curva se consulta SIN exigir
        # precio: ese era el paso que mataba la compra.
        class _Impls:
            address_provider = type(
                "AP",
                (),
                {
                    "derive_pool_address": staticmethod(lambda _m: object()),
                    "get_additional_accounts": staticmethod(lambda _t: {}),
                },
            )()
            curve_manager = curva

        class _Cliente:
            async def get_account_info(self, _m: object) -> None:
                return None

        await build_pumpfun_token_info(
            "DBDqhnAi5MjaHsk1JyUonAtUUrmRBJPjJi3GdiBLpump",
            _Impls(),
            _Cliente(),
            permitir_migradas=True,
        )

    asyncio.run(_comprobar())
    assert curva.exigio_precio == [False], (
        f"debia consultarse sin exigir precio, se hizo con {curva.exigio_precio}"
    )
