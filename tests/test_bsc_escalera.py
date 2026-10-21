"""La escalera de salida del bot BSC hace lo que pide INSTRUCCIONES.md.

Todo aqui es aritmetica, sin red ni wallet: es la parte de la estrategia
donde un error se paga en dinero y tiene que poder comprobarse entera.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bsc.escalera import (  # noqa: E402
    Disparo,
    construir_escalera,
    escalones_pendientes,
    fraccion_total,
    precio_de_stop,
    tokens_a_vender,
)


def test_la_escalera_vende_exactamente_el_cien_por_cien() -> None:
    """62,5% + 27,5% + 10% = 100%. Ni un token colgado, ni uno de mas.

    Si sumara menos, quedaria un resto que no se vende NUNCA y la cuenta de
    la wallet seguiria abierta con su renta dentro. Si sumara mas, la ultima
    venta pediria mas tokens de los que hay y revertiria.
    """
    assert fraccion_total(construir_escalera()) == pytest.approx(1.0, abs=1e-12)


def test_los_tres_tramos_pedidos_estan_y_con_su_peso() -> None:
    """Los tres bloques de INSTRUCCIONES.md, por separado."""
    esc = construir_escalera()

    recupera = [e for e in esc if e.disparo is Disparo.SUBIDA]
    assert len(recupera) == 1, "solo el primer escalon mira la subida"
    assert recupera[0].umbral == pytest.approx(0.60)
    assert recupera[0].vender == pytest.approx(0.625)

    intermedios = [
        e for e in esc if e.disparo is Disparo.MARKET_CAP and e.umbral <= 35_000
    ]
    assert [e.umbral for e in intermedios] == [
        10_000, 15_000, 20_000, 25_000, 30_000, 35_000,
    ]
    assert sum(e.vender for e in intermedios) == pytest.approx(0.275)

    moonbag = [e for e in esc if e.umbral >= 180_000]
    assert [(e.umbral, e.vender) for e in moonbag] == [
        (180_000.0, 0.050), (400_000.0, 0.025), (900_000.0, 0.025),
    ]
    assert sum(e.vender for e in moonbag) == pytest.approx(0.10)


def test_el_primer_escalon_recupera_el_capital_antes_de_comisiones() -> None:
    """ "Vende 62,5% para recuperar el capital inicial antes de comisiones".

    Es una cuenta exacta: 0,625 x 1,60 = 1,00. Si alguien mueve el 62,5% o el
    +60% sin mover el otro, esa propiedad se rompe en silencio -- y es la
    razon de ser de ese escalon.
    """
    primero = construir_escalera()[0]
    assert primero.vender * (1 + primero.umbral) == pytest.approx(1.0, abs=1e-9)


def test_el_orden_de_la_escalera_es_creciente_dentro_de_cada_disparo() -> None:
    """Dos disparadores distintos no se pueden mezclar en una sola lista.

    El primer escalon mira la SUBIDA (+60%) y el resto el MARKET CAP
    absoluto. El market cap al que equivale un +60% depende de donde se
    compro, asi que ordenarlos juntos por "umbral" seria comparar manzanas
    con peras. Lo que si tiene que ser creciente es cada grupo por separado.
    """
    esc = construir_escalera()
    mcs = [e.umbral for e in esc if e.disparo is Disparo.MARKET_CAP]
    assert mcs == sorted(mcs), "los escalones de market cap van de menos a mas"


def test_una_vela_que_salta_tres_escalones_los_cobra_todos() -> None:
    """Cobrar solo el siguiente dejaria la posicion vendiendo tarde el resto.

    Pasa de verdad: entre dos vueltas del bucle de vigilancia el precio puede
    cruzar varios escalones. El bot tiene que vender lo de los tres.
    """
    esc = construir_escalera()
    tocan = escalones_pendientes(esc, cobrados=set(), ganancia=0.0, market_cap_usd=22_000)
    assert [e.nombre for e in tocan] == ["MC 10k", "MC 15k", "MC 20k"]


def test_lo_ya_cobrado_no_se_repite_tras_un_reinicio() -> None:
    """El estado persistente existe justo para esto."""
    esc = construir_escalera()
    cobrados = {"MC 10k", "MC 15k"}
    tocan = escalones_pendientes(esc, cobrados, ganancia=0.0, market_cap_usd=22_000)
    assert [e.nombre for e in tocan] == ["MC 20k"]


def test_nunca_se_piden_mas_tokens_de_los_que_quedan() -> None:
    """`vender` es fraccion del ORIGINAL, y varios escalones se suman.

    Sin el recorte, una venta pediria mas tokens de los que tiene la wallet y
    la transaccion revertiria: la posicion se quedaria ENTERA sin vender en
    vez de vender lo que se podia.
    """
    esc = construir_escalera()
    tocan = escalones_pendientes(esc, set(), ganancia=5.0, market_cap_usd=1_000_000)
    assert len(tocan) == len(esc), "a x6 y 1M de MC tocan todos"

    # Quedan 100 de los 1000 originales (ya se vendio el 90% a mano).
    assert tokens_a_vender(tocan, 1000.0, 100.0) == pytest.approx(100.0)
    # Y con la posicion entera, se vende entera y ni un token mas.
    assert tokens_a_vender(tocan, 1000.0, 1000.0) == pytest.approx(1000.0)


def test_el_stop_mide_desde_el_precio_ejecutado() -> None:
    """ "-38% desde el precio EJECUTADO", no desde el que se vio al decidir.

    Con el slippage de estas monedas la diferencia es real: medir desde el
    precio visto dejaria el stop mas abajo de lo pedido sin que se note.
    """
    assert precio_de_stop(100.0) == pytest.approx(62.0)
    assert precio_de_stop(100.0, 0.38) == pytest.approx(62.0)
    # Y el ejecutado suele ser peor que el visto: el stop baja con el.
    assert precio_de_stop(110.0) > precio_de_stop(100.0)


def test_por_debajo_del_umbral_no_toca_nada() -> None:
    """Lo contrario del test de la vela: un escalon no se adelanta."""
    esc = construir_escalera()
    assert escalones_pendientes(esc, set(), ganancia=0.59, market_cap_usd=9_999) == []
    # Justo en el umbral SI toca: el pedido dice "al subir 60%", no "mas de".
    assert len(escalones_pendientes(esc, set(), ganancia=0.60, market_cap_usd=0)) == 1
