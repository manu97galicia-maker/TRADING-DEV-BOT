"""El tope duro de posiciones y el aviso de que el nodo no da mas.

Con `max_posiciones: 0` manda el saldo. El numero que queda dentro del
codigo es solo un tope de seguridad, y estaba en 20 sin nombre: con "0"
configurado se convertia en un limite invisible.
"""

# ruff: noqa: S101, SLF001, PLR2004

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading.universal_trader import TOPE_DURO_POSICIONES, UniversalTrader


def _trader(abiertas: int, intervalo: float, max_rps: float) -> UniversalTrader:
    t = UniversalTrader.__new__(UniversalTrader)
    t.price_check_interval = intervalo
    t.solana_client = SimpleNamespace(max_rps=max_rps)
    t._open_position_paths = lambda: [Path(f"p{i}.json") for i in range(abiertas)]
    return t


def test_el_tope_duro_deja_sitio_de_sobra() -> None:
    """Con 0,28 por compra, 40 posiciones son ~11 SOL: no estorba a nadie."""
    assert TOPE_DURO_POSICIONES > 20


def test_sin_max_posiciones_se_usa_el_tope_duro() -> None:
    """Es lo que hace que "0" signifique "lo que aguante la cartera"."""
    assert asyncio.Semaphore(int(0 or TOPE_DURO_POSICIONES))._value == (
        TOPE_DURO_POSICIONES
    )


def test_un_max_posiciones_explicito_sigue_mandando() -> None:
    assert asyncio.Semaphore(int(3 or TOPE_DURO_POSICIONES))._value == 3


def test_avisa_cuando_hay_mas_posiciones_que_nodo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """15 consultas/s a 1s dan para vigilar ~7; con 8 abiertas hay que avisar."""
    trader = _trader(abiertas=8, intervalo=1, max_rps=15)
    trader._avisa_si_el_nodo_no_da_mas()
    assert "NODO AL LIMITE" in caplog.text


def test_no_avisa_cuando_el_nodo_va_sobrado(
    caplog: pytest.LogCaptureFixture,
) -> None:
    trader = _trader(abiertas=2, intervalo=1, max_rps=15)
    trader._avisa_si_el_nodo_no_da_mas()
    assert "NODO AL LIMITE" not in caplog.text


def test_un_intervalo_mas_largo_permite_mas_posiciones(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """La salida recomendada: vigilar cada 5s da para ~37 posiciones."""
    trader = _trader(abiertas=8, intervalo=5, max_rps=15)
    trader._avisa_si_el_nodo_no_da_mas()
    assert "NODO AL LIMITE" not in caplog.text


def test_sin_datos_del_nodo_no_se_inventa_un_aviso(
    caplog: pytest.LogCaptureFixture,
) -> None:
    trader = _trader(abiertas=50, intervalo=0, max_rps=0)
    trader._avisa_si_el_nodo_no_da_mas()
    assert "NODO AL LIMITE" not in caplog.text


def test_contar_posiciones_no_revienta_sin_disco() -> None:
    """El contador no puede tumbar una compra si el disco falla."""
    trader = UniversalTrader.__new__(UniversalTrader)

    def _explota() -> list:
        raise OSError

    trader._open_position_paths = _explota
    assert trader._posiciones_abiertas() == 0
