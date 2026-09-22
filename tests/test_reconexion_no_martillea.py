"""El bot no puede martillear al nodo cuando este le dice que pare.

LO QUE PASO EL 22-SEP, y costo dos dias con el bot comprando de verdad y sin
ver una sola moneda:

    websockets.exceptions.InvalidStatus: server rejected WebSocket
    connection: HTTP 429

429 es "demasiadas peticiones". El bot reintentaba CADA 5 SEGUNDOS y PARA
SIEMPRE, o sea 720 intentos por hora contra un nodo que ya estaba diciendo que
sobraba. Ese martilleo mantenia vivo el 429: el bot se autobloqueaba y no se
recuperaba solo nunca. Ni una operacion de la cadena le llego en dias.

Y escribia el rastro entero de la excepcion en cada intento, asi que el log
eran miles de lineas de traza y el motivo de verdad --una linea con el 429 al
final-- quedaba enterrado. Desde el movil eso es invisible.
"""

# ruff: noqa: S101, SLF001

import logging
from types import SimpleNamespace

from monitoring.holders_source import (
    _ESPERA_RECONEXION,
    _ESPERA_RECONEXION_MAX,
    _ESPERA_TRAS_429,
    HoldersSource,
)


def _fuente() -> HoldersSource:
    return HoldersSource(
        "wss://x", SimpleNamespace(), SimpleNamespace(), heartbeat_interval=0
    )


def test_un_429_espera_mucho_desde_el_primer_intento() -> None:
    """No es un corte de red: es una cuota. Reintentar enseguida solo la gasta
    mas, asi que no vale con ir subiendo poco a poco."""
    f = _fuente()
    exc = Exception("server rejected WebSocket connection: HTTP 429")
    assert f._avisa_de_la_caida(exc, _ESPERA_RECONEXION) >= _ESPERA_TRAS_429


def test_una_caida_normal_empieza_corta_y_se_va_doblando() -> None:
    """Un corte de red si merece reintentar rapido; lo que no vale es quedarse
    en 5 segundos para siempre."""
    f = _fuente()
    espera = _ESPERA_RECONEXION
    vistas = []
    for _ in range(8):
        espera = f._avisa_de_la_caida(ConnectionResetError("se corto"), espera)
        vistas.append(espera)
        espera = min(espera * 2, _ESPERA_RECONEXION_MAX)
    assert vistas[0] == _ESPERA_RECONEXION
    assert vistas[-1] > vistas[0], "no crece: volveria a martillear"
    assert max(vistas) <= _ESPERA_RECONEXION_MAX, "sin tope, dejaria de reintentar"


def test_la_espera_tiene_tope() -> None:
    """Que crezca no puede significar que un dia deje de reintentar."""
    f = _fuente()
    espera = _ESPERA_RECONEXION_MAX
    for _ in range(5):
        espera = f._avisa_de_la_caida(ConnectionResetError("x"), espera)
        espera = min(espera * 2, _ESPERA_RECONEXION_MAX)
    assert espera == _ESPERA_RECONEXION_MAX


def test_la_caida_se_cuenta_en_una_linea_no_con_el_rastro_entero(caplog) -> None:  # noqa: ANN001
    """Con un fallo cada pocos segundos, el rastro completo llena el log de
    miles de lineas y esconde el motivo. Desde el movil eso es no tener log."""
    f = _fuente()
    with caplog.at_level(logging.WARNING):
        f._avisa_de_la_caida(Exception("HTTP 429"), _ESPERA_RECONEXION)
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None, "sigue volcando el rastro entero"
    texto = caplog.text
    assert "429" in texto
    assert "EXCESO DE PETICIONES" in texto, "no explica que es un 429"


def test_el_bucle_reinicia_la_espera_al_reconectar() -> None:
    """Si no se reiniciara, tras una caida larga el bot se quedaria esperando
    cinco minutos entre intentos aunque el nodo ya estuviera bien."""
    fuente = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "src/monitoring/holders_source.py"
    ).read_text(encoding="utf-8")
    corte = fuente.index("await self._suscribir(ws, program_id)")
    tras_suscribir = fuente[corte : corte + 400]
    assert "espera = _ESPERA_RECONEXION" in tras_suscribir, (
        "la espera no se reinicia despues de conectar y suscribirse"
    )
