"""El precio sale del stream de operaciones, no de preguntarle al nodo.

QUE ARREGLA. El bot ya esta suscrito a todas las operaciones de pump.fun, y
cada una publica en su TradeEvent las reservas virtuales que quedan DESPUES
de ejecutarse -- que es exactamente de donde sale el precio. Tenia el dato
delante y aun asi le preguntaba al nodo una vez por segundo, por cada
posicion abierta: hasta un segundo tarde (lo que dura un pico) y cinco
peticiones por segundo del presupuesto del nodo con cinco posiciones.

LO QUE NO SE PUEDE ROMPER AL HACERLO. Un websocket que se cae no avisa: deja
de mandar eventos, y desde dentro eso es identico a "no hay operaciones". Si
el precio del stream no caducara, una posicion se quedaria mirando un precio
congelado mientras la moneda se hunde y el stop loss no saltaria nunca. Por
eso se sigue preguntando al nodo cada `price_check_interval` pase lo que
pase, y por eso el precio del stream tiene edad maxima.
"""

# ruff: noqa: S101, SLF001, PLR2004, ANN202

import asyncio
import base64
import struct
import time
from pathlib import Path

import pytest
import yaml

from monitoring import volumen
from trading.universal_trader import UniversalTrader

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
MINT = "Er1sFZm3HGuXFiKeXQLg84H9B9Vqk3dp94AjLiS9pump"


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


@pytest.fixture(autouse=True)
def _limpio():
    volumen._precio.clear()
    yield
    volumen._precio.clear()


# ------------------------------------------------- leer el precio del stream


def test_el_precio_sale_de_las_reservas_virtuales() -> None:
    """La misma cuenta que hace el nodo: sol virtual entre tokens virtuales,
    con sus decimales (9 el SOL, 6 el token)."""
    v_sol, v_tokens = 85_000_000_000, 700_000_000_000_000
    volumen._precio[MINT] = (time.time(), (v_sol / 10**9) / (v_tokens / 10**6))
    esperado = (v_sol / 10**9) / (v_tokens / 10**6)
    assert volumen.precio_en_vivo(MINT, 8) == pytest.approx(esperado)


def test_un_precio_viejo_no_vale() -> None:
    """Es el seguro contra un stream caido: pasada la edad, se vuelve al nodo."""
    volumen._precio[MINT] = (time.time() - 30, 0.0000001)
    assert volumen.precio_en_vivo(MINT, 8) is None


def test_a_cero_el_stream_esta_apagado() -> None:
    """Poner 0 en la config tiene que devolver el comportamiento de siempre."""
    volumen._precio[MINT] = (time.time(), 0.0000001)
    assert volumen.precio_en_vivo(MINT, 0) is None


def test_una_moneda_que_no_se_ha_visto_no_inventa_precio() -> None:
    assert volumen.precio_en_vivo(MINT, 8) is None


def test_olvidar_una_moneda_no_deja_un_precio_colgando() -> None:
    """Una fuga aqui solo se nota tras dias corriendo."""
    volumen._precio[MINT] = (time.time(), 0.0000001)
    volumen._olvidar(MINT)
    assert MINT not in volumen._precio


# ------------------------------------------- el TradeEvent trae ese precio


def _evento(v_sol: int, v_tokens: int) -> str:
    """Un TradeEvent de verdad, con el layout que publica pump.fun."""
    cuerpo = bytearray(121)
    cuerpo[0:32] = bytes(range(32))  # mint
    struct.pack_into("<Q", cuerpo, 32, 1_000_000)  # sol_amount
    struct.pack_into("<Q", cuerpo, 40, 2_000_000)  # token_amount
    cuerpo[48] = 1  # is_buy
    struct.pack_into("<Q", cuerpo, 81, int(time.time()))  # timestamp
    struct.pack_into("<QQ", cuerpo, 89, v_sol, v_tokens)  # VIRTUALES
    struct.pack_into("<QQ", cuerpo, 105, 40_000_000_000, 700_000_000_000_000)
    return "Program data: " + base64.b64encode(
        volumen.DISC_TRADE + bytes(cuerpo)
    ).decode()


def test_el_precio_se_apunta_al_leer_una_operacion() -> None:
    """De punta a punta: llega el log, queda el precio."""
    v_sol, v_tokens = 85_000_000_000, 700_000_000_000_000
    tocadas = volumen.registrar_logs([_evento(v_sol, v_tokens)])
    assert len(tocadas) == 1
    precio = volumen.precio_en_vivo(tocadas[0], 8)
    assert precio == pytest.approx((v_sol / 10**9) / (v_tokens / 10**6))


def test_no_se_confunden_las_reservas_virtuales_con_las_reales() -> None:
    """EL FALLO QUE HABRIA SIDO CARO. Las reales estan 16 bytes mas alla y
    valen para el avance de la curva, no para el precio. Leyendo las de
    105 el precio saldria disparatado y la escalera venderia donde no debe.
    """
    v_sol, v_tokens = 85_000_000_000, 700_000_000_000_000
    real_sol, real_tokens = 40_000_000_000, 700_000_000_000_000
    mint = volumen.registrar_logs([_evento(v_sol, v_tokens)])[0]
    precio = volumen.precio_en_vivo(mint, 8)
    assert precio != pytest.approx((real_sol / 10**9) / (real_tokens / 10**6))
    assert precio == pytest.approx((v_sol / 10**9) / (v_tokens / 10**6))


# --------------------------------------------- el monitor lo usa bien

class _Curva:
    """Cuenta cuantas veces se le pregunta al nodo."""

    def __init__(self, precio: float) -> None:
        self.precio, self.veces = precio, 0

    async def calculate_price(self, _pool: object) -> float:
        self.veces += 1
        return self.precio


def _trader(stream_seg: float, intervalo: float = 1.0) -> UniversalTrader:
    t = UniversalTrader.__new__(UniversalTrader)
    t.precio_stream_seg = stream_seg
    t.tick_stream_seg = 0.25
    t.price_check_interval = intervalo
    t._ultima_rpc = {}
    t._ultimo_precio = {}
    return t


def test_la_primera_vuelta_siempre_pregunta_al_nodo() -> None:
    """Hay que tener una referencia del nodo antes de fiarse del stream."""
    t, curva = _trader(8), _Curva(0.000002)
    volumen._precio[MINT] = (time.time(), 0.000001)
    assert asyncio.run(t._precio_rapido(MINT, None, curva)) == 0.000002
    assert curva.veces == 1


def test_entre_consultas_manda_el_stream() -> None:
    """Aqui es donde se gana la velocidad: sin gastar ni una peticion."""
    t, curva = _trader(8), _Curva(0.000002)
    asyncio.run(t._precio_rapido(MINT, None, curva))
    volumen._precio[MINT] = (time.time(), 0.000009)
    for _ in range(10):
        assert asyncio.run(t._precio_rapido(MINT, None, curva)) == 0.000009
    assert curva.veces == 1, "se pregunto al nodo teniendo el precio del stream"


def test_sin_stream_se_repite_el_ultimo_sabido_y_no_se_machaca_el_nodo() -> None:
    """El tick es corto (0,25s) pero al nodo se le pregunta a su ritmo.

    Si cada vuelta sin stream preguntara al nodo, bajar el tick habria
    cuadruplicado las peticiones -- justo lo contrario de lo que se busca.
    """
    t, curva = _trader(8), _Curva(0.000002)
    asyncio.run(t._precio_rapido(MINT, None, curva))
    for _ in range(10):
        assert asyncio.run(t._precio_rapido(MINT, None, curva)) == 0.000002
    assert curva.veces == 1


def test_pasado_el_intervalo_se_vuelve_a_preguntar_al_nodo() -> None:
    """EL SEGURO CONTRA UN STREAM CAIDO. Un websocket cortado no avisa: deja
    de mandar eventos. Sin esta consulta, una posicion se quedaria con un
    precio congelado y el stop loss no saltaria nunca."""
    t, curva = _trader(8), _Curva(0.000002)
    asyncio.run(t._precio_rapido(MINT, None, curva))
    volumen._precio[MINT] = (time.time(), 0.000009)
    t._ultima_rpc[MINT] = 0.0  # como si hubiera pasado el intervalo
    curva.precio = 0.000003
    assert asyncio.run(t._precio_rapido(MINT, None, curva)) == 0.000003
    assert curva.veces == 2


def test_con_el_stream_apagado_se_pregunta_al_nodo_cada_vuelta() -> None:
    """El comportamiento de siempre, intacto, poniendo 0 en la config."""
    t, curva = _trader(0), _Curva(0.000002)
    volumen._precio[MINT] = (time.time(), 0.000009)
    for _ in range(3):
        assert asyncio.run(t._precio_rapido(MINT, None, curva)) == 0.000002
    assert curva.veces == 3


# ------------------------------------------------------ el ritmo del bucle


def test_con_stream_el_bucle_da_vueltas_mas_rapido() -> None:
    """Es lo que hace que el take profit se cobre EN la operacion."""
    t = _trader(8, intervalo=1.0)
    assert t._tick_vigilancia() == 0.25


def test_sin_stream_el_bucle_mantiene_el_ritmo_de_siempre() -> None:
    t = _trader(0, intervalo=1.0)
    assert t._tick_vigilancia() == 1.0


def test_el_tick_nunca_es_mas_lento_que_el_intervalo() -> None:
    """Con un intervalo corto manda el intervalo, no el tick."""
    t = _trader(8, intervalo=0.1)
    assert t._tick_vigilancia() == 0.1


def test_la_config_lo_trae_encendido_y_con_seguro() -> None:
    """Los dos numeros que importan, fijados para que no cambien a ciegas."""
    cfg = _trade()
    assert cfg["precio_stream_seg"] == 8
    assert cfg["tick_stream_seg"] == 0.25
    # El seguro tiene que ser MAS largo que el intervalo del nodo, o el
    # stream no llegaria a usarse nunca.
    assert cfg["precio_stream_seg"] > cfg["price_check_interval"]
