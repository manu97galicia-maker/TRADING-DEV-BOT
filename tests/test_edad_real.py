"""Edad desde el nacimiento REAL (5-nov: "Pon 3 segundos" -> "Desde su nacimiento real").

La cadena apunta la hora en segundos enteros: `edad` ve cada moneda hasta casi
1 s mas vieja. `edad_real` afina con la hora de llegada de este reloj y el
retraso medido, sin salirse nunca de lo que la cadena permite: [edad-1, edad].
"""

# ruff: noqa: S101, PLR2004

import time
from collections import deque
from types import SimpleNamespace

import pytest

from monitoring import volumen
from trading.universal_trader import UniversalTrader

M = "MonedaDePruebaEdadReal"


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    monkeypatch.setattr(volumen, "_retrasos", deque(maxlen=2000))
    volumen._nacimiento.pop(M, None)
    volumen._llegada.pop(M, None)
    yield
    volumen._nacimiento.pop(M, None)
    volumen._llegada.pop(M, None)


def _moneda(cadena_hace: float, llego_hace: float | None, retraso: float | None = 0.7,
            muestras: int = 50) -> None:
    ahora = time.time()
    volumen._nacimiento[M] = (ahora - cadena_hace, True)
    if llego_hace is not None:
        volumen._llegada[M] = ahora - llego_hace
    if retraso is not None:
        volumen._retrasos.extend([retraso] * muestras)


def test_afina_con_el_reloj_del_bot():
    # La cadena dice 3,6 s (redondeada hacia abajo); llego hace 2,4 s y el aviso
    # tarda 0,7 - 0,5 = 0,2 s -> real ~2,6 s. Con 3 s de maximo, ENTRA.
    _moneda(cadena_hace=3.6, llego_hace=2.4)
    assert volumen.edad_real(M) == pytest.approx(2.6, abs=0.05)
    assert volumen.edad(M) == pytest.approx(3.6, abs=0.05)


def test_nunca_mas_joven_de_lo_que_la_cadena_permite():
    # Un aviso que llega anormalmente tarde (hace 0,1 s) de una moneda que la
    # cadena sabe de hace 5 s: no puede pasar por nueva. Suelo = 5 - 1 = 4.
    _moneda(cadena_hace=5.0, llego_hace=0.1)
    assert volumen.edad_real(M) == pytest.approx(4.0, abs=0.05)


def test_nunca_mas_vieja_que_la_cuenta_de_la_cadena():
    _moneda(cadena_hace=3.0, llego_hace=10.0)
    assert volumen.edad_real(M) == pytest.approx(3.0, abs=0.05)


def test_con_pocas_muestras_de_retraso_no_se_fia_de_la_mediana():
    _moneda(cadena_hace=3.6, llego_hace=2.4, muestras=5)
    # Sin corregir el aviso: 2,4, pero el suelo de la cadena es 2,6.
    assert volumen.edad_real(M) == pytest.approx(2.6, abs=0.05)


def test_sin_hora_de_llegada_es_la_de_siempre():
    _moneda(cadena_hace=2.5, llego_hace=None)
    assert volumen.edad_real(M) == pytest.approx(volumen.edad(M), abs=0.01)


def test_sin_verla_nacer_no_hay_edad():
    assert volumen.edad_real("nunca-vista-nacer") is None


def test_se_apunta_la_llegada_al_ver_la_moneda():
    volumen._nacimiento.pop(M, None)
    volumen._anotar_nacimiento(M, 1000.0, llegada=1000.8)
    assert volumen._llegada[M] == 1000.8


def test_una_moneda_de_2_6_s_reales_entra_con_3_s_de_maximo():
    """El caso que motivo el cambio: la cadena la ve de 3,6 s, pero no lo es."""
    _moneda(cadena_hace=3.6, llego_hace=2.4)
    motivos = []
    yo = SimpleNamespace(curva_96={"max_edad_seg": 3}, _descarta=lambda _t, m: motivos.append(m))
    assert UniversalTrader._demasiado_vieja_para_comprar(yo, SimpleNamespace(mint=M, symbol="X")) is False
    assert motivos == []
