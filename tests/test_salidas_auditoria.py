"""Auditoria de la escalera y el stop, con la config REAL.

No comprueba que el YAML tenga unos numeros concretos (de eso ya hay otro
test), sino que el bot los ENTIENDE bien: que un peldaño de 9.00 dispara a
x10 y no a +9%, y que el stop va subiendo escalon a escalon segun cobra.

Es la comprobacion que hacia falta despues de pasar de 4 peldaños a 10 y de
meter multiplicadores grandes por primera vez.
"""

# ruff: noqa: S101

from pathlib import Path

import yaml

from trading.tiered_exit import (
    build_tiers,
    moonbag_fraction,
    stepped_stop_price,
    tiers_due,
)
from trading.universal_trader import _peldanos_extra

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
ENTRADA = 0.0000001


def _tiers() -> list:
    t = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]
    pares = [(t["take_profit_percentage"], t["take_profit_sell_percentage"])]
    pares += [(t.get(f"tp{i}_gain"), t.get(f"tp{i}_sell")) for i in (2, 3, 4, 5, 6)]
    pares += _peldanos_extra(t.get("tp_extra"))
    return build_tiers(pares)


def _vendido_hasta(multiplo: float) -> float:
    """Cuanto se ha vendido cuando la moneda llega a ese multiplo."""
    tiers = _tiers()
    ejecutado = [False] * len(tiers)
    vendido = 0.0
    for i in tiers_due(ENTRADA, ENTRADA * multiplo, tiers, ejecutado):
        ejecutado[i] = True
        vendido += tiers[i].sell
    return vendido


def test_una_ganancia_de_9_significa_x10_y_no_mas_9_por_ciento() -> None:
    """El error que habria sido caro: con el formato mal leido, el peldaño
    de x10 se habria disparado a +9% y habria vendido casi todo enseguida.
    """
    assert _vendido_hasta(1.09) == 0.0
    assert _vendido_hasta(10.0) > _vendido_hasta(7.0)


def test_no_se_vende_nada_por_debajo_del_primer_objetivo() -> None:
    assert _vendido_hasta(1.24) == 0.0


def test_cada_peldano_dispara_en_su_multiplo_exacto() -> None:
    # Lo VENDIDO ACUMULADO al llegar a cada multiplo (23-sep: el TP1 pasa a
    # +30% y cobra un 20%, y el de +80% cede doce puntos a los lejanos).
    esperado = {
        1.30: 0.20,   # el TP1 ya cobra
        1.50: 0.399,
        1.80: 0.581,
        2.50: 0.682,  # x2,5
        3.30: 0.767,  # x3,3
        5.0: 0.836,
        7.0: 0.892,
        10.0: 0.932,
        15.0: 0.956,
        20.0: 0.98,   # el ultimo peldano: solo queda el moonbag
    }
    for multiplo, acumulado in esperado.items():
        assert round(_vendido_hasta(multiplo), 6) == acumulado, f"fallo en x{multiplo}"


def test_ni_un_x500_vende_el_moonbag() -> None:
    """Ese resto es el que Manu vende a mano: la escalera no lo toca."""
    assert round(1 - _vendido_hasta(500.0), 6) == round(moonbag_fraction(_tiers()), 6)


def test_el_stop_sube_al_precio_de_compra_con_el_primer_cobro() -> None:
    """Es lo que hace que tras el primer TP ya no se pueda perder."""
    assert stepped_stop_price(ENTRADA, _tiers(), 0) == ENTRADA


def test_el_stop_va_subiendo_un_escalon_por_detras() -> None:
    """Un escalon POR DETRAS, no el recien tocado: si el suelo se pegara al
    precio actual, cualquier respiro normal cerraria la posicion.
    """
    tiers = _tiers()
    for i in range(1, len(tiers)):
        suelo = stepped_stop_price(ENTRADA, tiers, i)
        assert suelo == ENTRADA * (1 + tiers[i - 1].gain)


def test_el_suelo_nunca_baja_al_avanzar_la_escalera() -> None:
    tiers = _tiers()
    suelos = [stepped_stop_price(ENTRADA, tiers, i) for i in range(len(tiers))]
    assert suelos == sorted(suelos)


def test_el_trailing_esta_apagado_para_no_tocar_el_moonbag() -> None:
    """Con trailing puesto se vende TODO al caer desde el pico, moonbag
    incluido -- justo lo contrario de venderlo a mano."""
    t = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]
    assert t["trailing_stop_percentage"] == 0
