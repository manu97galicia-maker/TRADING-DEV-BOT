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

#: Por debajo de esto un peldano no puede ser "lejano": si "7.968" se leyera
#: como un +7,968% en vez de como x8,968, el ultimo peldano caeria aqui
#: debajo. Es el canario del formato `multiplo menos uno`.
MULTIPLO_MINIMO_LEJANO = 2.0


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
    """El error que habria sido caro: con el formato mal leido, un peldaño de
    "gain: 9" se habria disparado a +9% y habria vendido casi todo enseguida.

    VUELVEN LOS PELDANOS LEJANOS la tarde del 24-sep, en `tp_extra`: cinco,
    de x2,70 a x8,968, escritos como multiplo menos uno (1.70, 2.645...). O
    sea que el formato peligroso vuelve a estar en uso y este test vuelve a
    tener trabajo de verdad, no solo de guardia.
    """
    assert _vendido_hasta(1.09) == 0.0, (
        "a +9% no se vende nada: el primer peldano esta en +25%"
    )
    # Pasado el ultimo peldano ya no se vende mas: lo que queda es el moonbag.
    tope = 1 + max(t.gain for t in _tiers())
    assert _vendido_hasta(50.0) == _vendido_hasta(tope)
    assert _vendido_hasta(50.0) < 1.0, "el moonbag no se vende nunca"
    # Y el mayor de todos sigue leyendose como multiplo, no como porcentaje:
    # si "7.968" se leyera como +7,968% caeria por debajo del primer peldano.
    assert tope > MULTIPLO_MINIMO_LEJANO, (
        "el ultimo peldano tiene que ser un multiplo, no un porcentaje"
    )


def test_no_se_vende_nada_por_debajo_del_primer_objetivo() -> None:
    """EL PRECIO SALE DE LA CONFIG, no escrito a mano.

    Estaba fijado en 1,24 --justo por debajo del TP1 de entonces, +25%-- y
    siguio en verde cuando el peldano se movio a +30%: comprobaba que no se
    vende a +24% con el corte en +30%, o sea nada. Al bajar el TP1 a +15% se
    puso rojo por fin, que es como se descubrio.

    Un precio escrito a mano en un test de escalera caduca en cuanto alguien
    toca la escalera, y no avisa: se queda verde midiendo un hueco vacio.
    """
    import yaml

    tp1 = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"][
        "take_profit_percentage"
    ]
    justo_debajo = 1 + tp1 - 0.01
    assert _vendido_hasta(justo_debajo) == 0.0, (
        f"a +{(justo_debajo - 1) * 100:.0f}% no puede haberse vendido nada: "
        f"el primer peldano esta en +{tp1 * 100:.0f}%"
    )
    # Y justo por encima SI vende: si no, el test de arriba pasaria tambien
    # con una escalera apagada.
    assert _vendido_hasta(1 + tp1 + 0.01) > 0


def test_cada_peldano_dispara_en_su_multiplo_exacto() -> None:
    # Lo VENDIDO ACUMULADO al llegar a cada multiplo. Desde la tarde del
    # 24-sep (noche) son SIETE peldanos: tres de cerca (+18%, +38%, +55,1%)
    # y cuatro
    # lejanos en `tp_extra`, cada uno un 35% mas caro que el anterior.
    esperado = {
        1.17: 0.0,    # justo por debajo del primero
        1.18: 0.10,
        1.38: 0.25,   # hasta aqui solo se ha vendido un cuarto: no frena
        1.5513: 0.40,  # 100k de market cap
        2.3269: 0.40,  # el de 150k es x2,3270: a x2,3269 no entra aun
        2.3270: 0.60,  # 150k
        3.1026: 0.77,  # 200k
        4.6539: 0.89,  # 300k
        6.2052: 0.97,  # 400k; los siete cobrados y queda el 3% de moonbag
        50.0: 0.97,   # por mucho que suba, aqui ya no queda nada que cobrar
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
