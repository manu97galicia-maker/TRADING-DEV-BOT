"""Tests ensuring DexScreener signals are new events, not old volume."""

# ruff: noqa: S101

from dashboard.dexpaid_feed import edad_de_pago_aceptable
from monitoring.dexpaid_source import DexPaidFreshnessTracker


def _candidate(mint: str, boosts: float = 0, signal_type: str = "paid") -> dict:
    return {"mint": mint, "boosts": boosts, "signal_type": signal_type}


def test_initial_snapshot_is_only_a_baseline() -> None:
    tracker = DexPaidFreshnessTracker()
    tracker.prime([_candidate("old", 100, "boost")])

    assert not tracker.accept(_candidate("old", 100, "boost"))


def test_new_boost_mint_is_accepted_once() -> None:
    tracker = DexPaidFreshnessTracker()
    tracker.prime([_candidate("old", 100, "boost")])

    assert tracker.accept(_candidate("new", 10, "boost"))
    assert not tracker.accept(_candidate("new", 10, "boost"))


def test_boost_increase_is_a_new_signal() -> None:
    tracker = DexPaidFreshnessTracker()
    tracker.prime([_candidate("mint", 10, "boost")])

    assert tracker.accept(_candidate("mint", 50, "boost"))


def test_new_paid_profile_is_accepted_once() -> None:
    tracker = DexPaidFreshnessTracker()
    tracker.prime([_candidate("old")])

    assert tracker.accept(_candidate("new"))
    assert not tracker.accept(_candidate("new"))


# --- La antiguedad del pago deja de filtrar cuando se pone a 0 ---


_pasa_el_filtro_de_edad = edad_de_pago_aceptable


def test_con_tope_el_pago_viejo_se_descarta() -> None:
    assert not _pasa_el_filtro_de_edad(30, 10)
    assert _pasa_el_filtro_de_edad(5, 10)


def test_con_el_tope_a_cero_pasa_cualquier_edad() -> None:
    assert _pasa_el_filtro_de_edad(30, 0)
    assert _pasa_el_filtro_de_edad(600, 0)


def test_sin_orden_de_pago_no_pasa_nunca() -> None:
    """La edad es opcional; que EXISTA un pago aprobado, no."""
    assert not _pasa_el_filtro_de_edad(None, 0)
    assert not _pasa_el_filtro_de_edad(None, 10)
