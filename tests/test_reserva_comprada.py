"""7-nov: "cuidado con esto, me expones en dos compras" (la misma moneda,
comprada por la de 6k a las 9:22 y otra vez por la de 15,3k a las 9:37)."""

# ruff: noqa: S101, ANN001, ANN201, SLF001

import os
import time

import pytest

from trading import reserva as r


@pytest.fixture(autouse=True)
def _carpeta(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(r, "CARPETA", tmp_path)


def test_comprada_no_se_recompra_aunque_caduque_la_reserva():
    m = "MintDePrueba111"
    assert r.reserva(m, "real-presion6k") is True
    r.marca_comprada(m, "real-presion6k")
    # 15 minutos despues: la reserva corta (10 min) ya habria caducado.
    viejo = time.time() - 15 * 60
    os.utime(r._ruta(m), (viejo, viejo))
    assert r.reserva(m, "real-presion15k") is False


def test_sin_compra_la_reserva_caducada_si_se_libera():
    m = "MintDePrueba222"
    assert r.reserva(m, "a") is True
    viejo = time.time() - 15 * 60
    os.utime(r._ruta(m), (viejo, viejo))
    assert r.reserva(m, "b") is True


def test_la_marca_de_comprada_dura_24_horas():
    m = "MintDePrueba333"
    r.marca_comprada(m, "a")
    hace = time.time() - (r.COMPRADA_SEG - 60)
    os.utime(r._ruta_comprada(m), (hace, hace))
    assert r.reserva(m, "b") is False
    hace = time.time() - (r.COMPRADA_SEG + 60)
    os.utime(r._ruta_comprada(m), (hace, hace))
    assert r.reserva(m, "b") is True


def test_compra_fallida_no_marca():
    m = "MintDePrueba444"
    assert r.reserva(m, "a") is True
    r.suelta(m)
    assert r.reserva(m, "b") is True
