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


def _trade_yaml() -> dict:
    """El bloque `trade` del yaml, leido una vez por llamada.

    Hay otro sitio en este fichero que importa yaml dentro de la funcion; se
    deja asi porque ya estaba, pero lo nuevo pasa por aqui.
    """
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


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
    assert _vendido_hasta(50.0) < 1.0, "queda moonbag: no puede venderse todo"
    assert _vendido_hasta(50.0) < 1.0, "queda moonbag: no puede venderse todo"
    # YA NO SE COMPRUEBA QUE QUEDE MOONBAG: desde la noche del 24-sep el take
    # profit unico vende el 100%. Que no quede resto lo vigila
    # test_stop_entero_y_renta.py, que es donde importa (un resto sin dueno
    # deja la cuenta de token abierta y su renta bloqueada).
    # Y el mayor de todos sigue leyendose como multiplo, no como porcentaje:
    # si "7.968" se leyera como +7,968% caeria por debajo del primer peldano.
    # SOLO CUANDO HAY PELDANOS LEJANOS. Desde la noche del 24-sep hay uno
    # solo, al +55,13%, y escribirlo como "0.5513" es correcto: es un
    # porcentaje de verdad, no un multiplo mal puesto. La trampa que vigila
    # este test --escribir "9" queriendo decir 9% y disparar a x10-- solo
    # existe cuando hay peldanos lejanos, asi que se comprueba cuando los
    # hay. Si vuelven, vuelve la vigilancia sola.
    #
    # "HAY LEJANOS" SE MIRA EN `tp_extra`, no en cuantos peldanos hay. La
    # version anterior usaba `len(_tiers()) > 1` y se puso roja al volver a
    # una escalera de DOS peldanos de cerca (+30% y +60%): dos peldanos no
    # son un peldano lejano, y x1,60 escrito como "0.60" es correcto. Los
    # peldanos en formato multiplo viven en `tp_extra` y solo ahi existe la
    # trampa que este test vigila.
    hay_lejanos = bool(_trade_yaml().get("tp_extra"))
    if hay_lejanos:
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
    """Lo VENDIDO ACUMULADO al llegar a cada multiplo (25-sep).

    TRES peldanos de porcentaje: +25% vende 20%, +60% vende 40%, +100% vende
    25%. El 15% que queda NO se vende por porcentaje -- se lo reparten los
    escalones de market cap, que son otra escalera y no se miden aqui: esta
    funcion solo recorre los peldanos de porcentaje.

    POR ESO LA ULTIMA FILA SE QUEDA EN 0,85 por mucho que suba el multiplo.
    No significa que sobre un 15%: significa que ese 15% sale por la otra
    puerta, y esa puerta depende del market cap en dolares, no del multiplo.
    """
    esperado = {
        1.24: 0.0,  # justo por debajo del primero
        1.25: 0.20,  # el primero cobra poco: esta ahi por el suelo que arma
        1.59: 0.20,
        1.60: 0.60,  # el segundo es el que se lleva el grueso
        1.99: 0.60,
        2.00: 0.85,  # los tres cobrados
        50.0: 0.85,  # el resto lo cobra la cola de market cap, no esta
    }
    for multiplo, acumulado in esperado.items():
        assert round(_vendido_hasta(multiplo), 6) == acumulado, f"fallo en x{multiplo}"


def test_ni_un_x500_vende_lo_que_va_por_market_cap() -> None:
    """La escalera de PORCENTAJE no toca el 15% de la cola, suba lo que suba.

    Antes este test se llamaba `test_ni_un_x500_vende_el_moonbag` y describia
    un resto que Manu vendia a mano. Ya no hay resto a mano: lo vende la cola
    de market cap. Lo que sigue siendo cierto --y es lo que fija-- es que los
    peldanos de porcentaje no pueden tocarlo.
    """
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
