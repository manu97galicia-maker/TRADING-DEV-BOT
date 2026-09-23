"""Verificacion del TP y el SL sobre recorridos de precio completos.

Los otros tests miran piezas sueltas: que el YAML tenga unos numeros, que
9.00 signifique x10, que el suelo suba un escalon. Este junta todo y pasa una
posicion ENTERA por caminos de precio reales, reproduciendo el orden del
monitor: primero el stop (universal_trader.py:5128) y despues los peldanos
(:5173). Es la comprobacion de "¿esto haria lo que creo con mi dinero?".

Tambien fija que NADA sustituya la escalera configurada. En el codigo hay
tres caminos que la cambian por otra (TP agresivo para devs moonshot, devs
marcados) y dos que cambian el stop (dev_elite, dev_mediana). Con la config
de hoy estan todos apagados, pero se apagan solos por AUSENCIA de una clave:
si alguien anade `bro_address` apuntando a una watchlist .json, las monedas
de esos devs pasan a una escalera de 3 peldanos con moonbag del 40% sin que
nada avise. Estos tests avisan.
"""

# ruff: noqa: S101, PLR2004

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
ENTRADA = 1.0
CANTIDAD = 1000.0


def _vender_todo_en_stop() -> bool:
    """Si el stop se lleva la posicion entera. SALE DEL YAML, no a mano.

    Estuvo escrito a pelo como False y se quedo obsoleto en cuanto se activo
    la opcion: el simulador seguia dando verde probando un comportamiento que
    el bot ya no tenia. Un test asi es peor que no tenerlo, porque respalda
    una verificacion que ya no es cierta.
    """
    return bool(_trade().get("vender_todo_en_stop", False))


def _margen() -> float:
    """El margen del stop, DEL YAML, no escrito a mano.

    Sin esto el simulador calcularia suelos distintos de los del bot: daria
    verde sobre un comportamiento que el bot no tiene, que es peor que no
    tener test. Ya paso con `vender_todo_en_stop`, justo arriba.
    """
    return float(_trade().get("margen_stop_tras_tp", 0.0))


def _cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _trade() -> dict:
    return _cfg()["trade"]


def _tiers() -> list:
    t = _trade()
    pares = [(t["take_profit_percentage"], t["take_profit_sell_percentage"])]
    pares += [(t.get(f"tp{i}_gain"), t.get(f"tp{i}_sell")) for i in (2, 3, 4, 5, 6)]
    pares += _peldanos_extra(t.get("tp_extra"))
    return build_tiers(pares)


def simula(recorrido: list[float]) -> dict:
    """Pasa una posicion por ese camino de precios y dice como acaba.

    Reproduce el orden del monitor real: el stop se mira ANTES que los
    peldanos en cada tick, y al cobrar un peldano el suelo sube al escalon
    anterior.
    """
    tiers = _tiers()
    t = _trade()
    moonbag = moonbag_fraction(tiers)
    ejecutado = [False] * len(tiers)
    restante = CANTIDAD
    sl_price = ENTRADA * (1 - float(t["stop_loss_percentage"]))
    sube_tras_tp = bool(t["stop_a_entrada_tras_tp"])
    cobrado = 0.0
    peldanos_cobrados: list[int] = []
    paro_por_stop = False

    for precio in recorrido:
        if sl_price is not None and precio <= sl_price:
            keep = 0.0 if _vender_todo_en_stop() else CANTIDAD * moonbag
            vender = restante - keep
            paro_por_stop = True
            if vender > 1e-9:
                cobrado += vender * precio
                restante -= vender
            break
        for i in tiers_due(ENTRADA, precio, tiers, ejecutado):
            ejecutado[i] = True
            vender = min(CANTIDAD * tiers[i].sell, restante)
            cobrado += vender * precio
            restante -= vender
            peldanos_cobrados.append(i + 1)
            if sube_tras_tp:
                suelo = stepped_stop_price(ENTRADA, tiers, i, _margen())
                if suelo is not None and (sl_price is None or sl_price < suelo):
                    sl_price = suelo

    invertido = CANTIDAD * ENTRADA
    return {
        "peldanos": peldanos_cobrados,
        "paro_por_stop": paro_por_stop,
        "restante_pct": restante / CANTIDAD,
        "suelo_final": sl_price / ENTRADA,
        "resultado": (cobrado + restante * recorrido[-1]) / invertido - 1,
    }


# ------------------------------------------------- nada sustituye la escalera


def test_ninguna_moneda_se_sale_de_la_escalera_configurada() -> None:
    """Si alguien enciende uno de estos, la escalera deja de ser la de arriba.

    `bro_address` es el peligroso: apuntando a una watchlist .json, los devs
    con pico historico >=120.000 $ pasan a +100/+300/+900 vendiendo 20% en
    cada uno, o sea MOONBAG DEL 40% en vez del 2%.
    """
    cfg, t = _cfg(), _trade()
    for clave in ("bro_address", "tp_marcados", "tp_sell_marcados"):
        assert not (t.get(clave) or cfg.get(clave)), (
            f"{clave} esta puesto: hay monedas que NO usan la escalera del YAML"
        )


def test_nada_sustituye_el_stop_loss_configurado() -> None:
    cfg, t = _cfg(), _trade()
    for clave in ("dev_elite", "dev_mediana", "mc_stop_loss"):
        assert not (t.get(clave) or cfg.get(clave)), (
            f"{clave} esta puesto: hay monedas con un stop distinto del -18%"
        )


# ----------------------------------------------------------------- recorridos


def test_un_rug_corta_en_el_stop_y_no_mas_abajo() -> None:
    """Tocando justo el suelo, se pierde justo eso y ni un punto mas.

    Los precios salen del `stop_loss_percentage` del yaml, no escritos a mano:
    el 23-sep paso de 18% a 28% y con el recorrido fijo la moneda ya no llegaba
    a tocar el suelo, asi que el test pasaba a medir otra cosa sin avisar.
    """
    sl = float(_trade()["stop_loss_percentage"])
    r = simula([1.0, 1.0 - sl / 2, 1.0 - sl])
    assert r["paro_por_stop"]
    assert r["peldanos"] == []
    assert round(r["resultado"], 6) == round(-sl, 6)


def test_tocar_el_tp1_asegura_ganancia_sin_vender_nada() -> None:
    """EL RECORRIDO QUE RESUME EL CAMBIO DEL 19-SEP.

    Antes, cobrado el TP1 el suelo pasaba al precio de COMPRA: la operacion
    dejaba de perder. Ahora pasa a compra +12%, asi que tocar el primer
    objetivo la convierte en GANADORA pase lo que pase despues -- y sin haber
    vendido un solo token, porque el TP1 esta a 0%.

    La moneda sube a +25%, se da la vuelta y cae. Cierra en +12%.
    """
    # Primero: toca el TP1 y se queda ahi. No se vende NADA y el suelo ya esta
    # en +12%.
    r = simula([1.0, 1.20, 1.26])
    assert r["peldanos"] == [1]
    assert not r["paro_por_stop"]
    assert round(r["restante_pct"], 6) == 1.0, "el TP1 no puede vender nada"
    assert round(r["suelo_final"], 4) == 1.12

    # Y ahora la vuelta atras: el stop la cierra en +12%, en verde. Aqui
    # `restante` SI acaba en 0 porque `vender_todo_en_stop` se lleva la
    # posicion entera -- que es lo correcto.
    # Cayendo justo hasta el suelo, se cobra el +12% clavado.
    r = simula([1.0, 1.20, 1.26, 1.15, 1.12])
    assert r["paro_por_stop"]
    assert round(r["resultado"], 4) == 0.12, "cierra en el +12% asegurado"

    # Y si el precio se salta el suelo entre dos lecturas, se vende a lo que
    # haya: sigue siendo verde, pero MENOS del 12%. El suelo es una orden, no
    # una garantia de precio -- en una moneda que cae a plomo hay hueco.
    r = simula([1.0, 1.20, 1.26, 1.15, 1.05])
    assert r["paro_por_stop"]
    assert 0 < r["resultado"] < 0.12


def test_los_tres_peldanos_de_cerca_y_vuelta_atras() -> None:
    """Llega al +80%, cobra 62,9% de la posicion y el suelo queda en +68%.

    El trinquete va un peldano por detras Y con el margen: cobrado el tercero,
    el suelo sube al precio del SEGUNDO (+50%) mas un 12% -> +68%. Deja sitio
    para que la moneda respire y aun asi cada cobro asegura lo ya ganado.
    """
    r = simula([1.0, 1.26, 1.51, 1.81, 1.75, 1.67])
    assert r["peldanos"] == [1, 2, 3]
    assert r["paro_por_stop"]
    assert round(r["suelo_final"], 4) == 1.68
    assert r["resultado"] > 0.5


def test_un_salto_de_golpe_cobra_todos_los_peldanos_que_deja_atras() -> None:
    """Si el precio salta a x7 en un tick, no se pierden los siete de abajo:
    se venden todos, a x7, que es el precio al que se esta vendiendo."""
    r = simula([1.0, 7.0])
    assert r["peldanos"] == [1, 2, 3, 4, 5, 6, 7]
    assert not r["paro_por_stop"]
    assert round(r["restante_pct"], 6) == 0.097


def test_ni_un_x100_toca_el_moonbag() -> None:
    """Ese 2% es el que vende Manu a mano: la escalera no lo vende nunca."""
    tiers = _tiers()
    r = simula([1.0, *[ENTRADA * (1 + t.gain) for t in tiers]])
    assert r["peldanos"] == list(range(1, len(tiers) + 1))
    assert round(r["restante_pct"], 6) == round(moonbag_fraction(tiers), 6)


def test_el_stop_se_lleva_la_posicion_entera() -> None:
    """Con `vender_todo_en_stop` encendido, un stop no deja nada corriendo en
    una moneda que se esta cayendo a cero. Ademas la cuenta de token queda
    vacia, que es lo que permite cerrarla y recuperar su renta.

    El precio sale del yaml: con el suelo escrito a mano (0,80, de cuando el
    stop era del 18%) este test dejo de tocar el stop al pasarlo a 28% y
    comprobaba otra cosa."""
    r = simula([1.0, 1.0 - float(_trade()["stop_loss_percentage"])])
    assert r["paro_por_stop"]
    assert round(r["restante_pct"], 6) == 0.0


def test_el_moonbag_solo_sobrevive_si_no_salta_el_stop() -> None:
    """La distincion que importa: el 2% se conserva en las que RECORREN la
    escalera, no en las que se hunden."""
    tiers = _tiers()
    r = simula([1.0, *[ENTRADA * (1 + t.gain) for t in tiers]])
    assert not r["paro_por_stop"]
    assert round(r["restante_pct"], 6) == 0.02


# -------------------------------------------------------------- el trinquete


def test_el_suelo_nunca_queda_por_encima_del_precio_que_lo_arma() -> None:
    """Si al cobrar un peldano el suelo quedara por encima del precio de ese
    peldano, la posicion se cerraria sola en el mismo instante de cobrarlo."""
    tiers = _tiers()
    for i, tier in enumerate(tiers):
        precio_disparo = ENTRADA * (1 + tier.gain)
        suelo = stepped_stop_price(ENTRADA, tiers, i, _margen())
        assert suelo < precio_disparo, f"TP{i + 1}: el suelo cerraria al cobrar"


def test_el_suelo_solo_sube() -> None:
    """Una bajada del suelo devolveria a la mesa algo ya asegurado."""
    r = simula([1.0, 1.31, 1.52, 1.71, 3.3, 5.0, 7.0])
    assert r["suelo_final"] >= 1.0
    tiers = _tiers()
    suelos = [
        stepped_stop_price(ENTRADA, tiers, i, _margen())
        for i in range(len(tiers))
    ]
    assert suelos == sorted(suelos)
