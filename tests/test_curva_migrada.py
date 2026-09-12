"""Una curva vacia (moneda migrada) debe poder leerse sin exigir precio."""

# ruff: noqa: S101, SLF001

import pytest

from platforms.pumpfun.curve_manager import PumpFunCurveManager


class _ParserFalso:
    """Devuelve el estado que se le pida, sin tocar la cadena."""

    def __init__(self, estado: dict) -> None:
        self._estado = estado

    def decode_account_data(self, *_a: object, **_k: object) -> dict:
        return self._estado


def _manager(**campos: object) -> PumpFunCurveManager:
    base = {
        "virtual_token_reserves": 1_000_000,
        "virtual_sol_reserves": 30_000_000_000,
        "real_token_reserves": 0,
        "real_sol_reserves": 0,
        "token_total_supply": 1_000_000_000,
        "complete": False,
        "creator": "Creador111",
    }
    base.update(campos)
    return PumpFunCurveManager(object(), _ParserFalso(base))


def test_una_curva_viva_calcula_precio() -> None:
    estado = _manager()._decode_curve_state_with_idl(b"x")

    assert estado["price_per_token"] > 0
    assert not estado["complete"]


def test_una_curva_migrada_lanza_si_se_exige_precio() -> None:
    """El comportamiento de siempre para quien va a operar EN la curva."""
    manager = _manager(virtual_token_reserves=0, complete=True)

    with pytest.raises(ValueError, match="virtual_token_reserves"):
        manager._decode_curve_state_with_idl(b"x")


def test_una_curva_migrada_se_lee_sin_exigir_precio() -> None:
    """Sin esto, preguntar '¿migro?' fallaba justo cuando la respuesta era si.

    is_curve_complete() pasa por aqui, y una curva completa tiene las reservas
    a cero: el error saltaba antes de poder devolver `complete`, asi que la
    moneda se descartaba como ilegible en vez de reconocerse como migrada.
    """
    manager = _manager(virtual_token_reserves=0, virtual_sol_reserves=0, complete=True)

    estado = manager._decode_curve_state_with_idl(b"x", exigir_precio=False)

    assert estado["complete"] is True
    assert estado["price_per_token"] == 0.0
    assert estado["creator"] == "Creador111"


def test_las_reservas_de_sol_vacias_tambien_se_toleran() -> None:
    manager = _manager(virtual_sol_reserves=0, complete=True)

    estado = manager._decode_curve_state_with_idl(b"x", exigir_precio=False)

    assert estado["complete"] is True
