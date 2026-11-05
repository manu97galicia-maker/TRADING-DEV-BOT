"""Edad desde el nacimiento REAL (5-nov: "Pon 3 segundos" -> "Desde su nacimiento real").

La cadena apunta la hora en segundos enteros: `edad` ve cada moneda hasta casi
1 s mas vieja. `edad_real` afina con la hora de llegada de este reloj y el
retraso medido, sin salirse nunca de lo que la cadena permite: [edad-1, edad].
"""

# ruff: noqa: S101, PLR2004

import base64
import struct
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
    volumen._creacion.pop(M, None)
    yield
    volumen._nacimiento.pop(M, None)
    volumen._llegada.pop(M, None)
    volumen._creacion.pop(M, None)


def _moneda(cadena_hace: float, llego_hace: float | None, retraso: float | None = 0.7,
            muestras: int = 50, creada_hace: float | None = None) -> None:
    """Moneda vista nacer: su creacion llego hace `creada_hace` (por defecto,
    a la vez que su primera operacion, como cuando el dev compra al crear)."""
    ahora = time.time()
    volumen._nacimiento[M] = (ahora - cadena_hace, True)
    if llego_hace is not None:
        volumen._llegada[M] = ahora - llego_hace
    if creada_hace is None:
        creada_hace = llego_hace if llego_hace is not None else cadena_hace
    volumen._creacion[M] = ahora - creada_hace
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


def test_sin_hora_de_llegada_cuenta_desde_la_creacion():
    # Creada hace 2,5 s y el aviso tarda 0,2 s: 2,7 s, sin techo de la cadena
    # porque no hay operacion de la misma transaccion con la que comparar.
    _moneda(cadena_hace=2.5, llego_hace=None)
    assert volumen.edad_real(M) == pytest.approx(2.7, abs=0.05)


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


# --- 5-nov: "Ha comprado una de hace 9 dias. Repito: maximo 3 segundos del
# mint". Una moneda vieja y muerta vuelve a tener la curva llena y su primera
# operacion de hoy parecia su nacimiento. Solo cuenta la creacion del mint.


def _moneda_muerta_revivida() -> None:
    """Curva llena y primera operacion vista hace medio segundo, pero el bot
    NUNCA vio la transaccion que creo el mint (fue hace 9 dias)."""
    ahora = time.time()
    volumen._nacimiento[M] = (ahora - 0.5, True)
    volumen._llegada[M] = ahora - 0.5


def test_una_moneda_de_9_dias_revivida_no_tiene_edad():
    _moneda_muerta_revivida()
    assert volumen.vio_la_creacion(M) is True  # el engano de antes
    assert volumen.edad_real(M) is None


def test_una_moneda_de_9_dias_revivida_no_se_compra():
    _moneda_muerta_revivida()
    motivos = []
    yo = SimpleNamespace(curva_96={"max_edad_seg": 3}, _descarta=lambda _t, m: motivos.append(m))
    assert UniversalTrader._demasiado_vieja_para_comprar(yo, SimpleNamespace(mint=M, symbol="X")) is True
    assert motivos


def _evento_de_creacion(mint_bytes: bytes) -> str:
    cuerpo = b""
    for texto in (b"Nombre", b"SYM", b"https://x"):
        cuerpo += struct.pack("<I", len(texto)) + texto
    cuerpo += mint_bytes + bytes(32 * 3) + bytes(8 * 5)
    return "Program data: " + base64.b64encode(volumen.DISC_CREATE + cuerpo).decode()


def test_la_creacion_se_apunta_desde_los_logs():
    """Las dos escuchas pasan por `registrar_logs`: la creacion queda apuntada
    aunque la primera compra llegue por la otra escucha antes que el aviso de
    creaciones."""
    mint_bytes = bytes(range(1, 33))
    mint = volumen._b58(mint_bytes)
    volumen._creacion.pop(mint, None)
    try:
        volumen.registrar_logs(["Program log: Instruction: Create", _evento_de_creacion(mint_bytes)])
        assert mint in volumen._creacion
        assert time.time() - volumen._creacion[mint] < 1.0
    finally:
        volumen._creacion.pop(mint, None)


def test_la_creacion_se_apunta_desde_el_escuchador():
    volumen.registrar_bloque_inicial(M, None, dev="dev")
    assert M in volumen._creacion
    volumen._dev_creacion.pop(M, None)


def test_la_creacion_no_se_reescribe():
    volumen._creacion[M] = 123.0
    volumen.registrar_bloque_inicial(M, None)
    assert volumen._creacion[M] == 123.0
