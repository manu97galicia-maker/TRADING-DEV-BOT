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


def test_tocar_el_tp1_cobra_y_asegura() -> None:
    """EL RECORRIDO QUE RESUME LA ESCALERA.

    Tocar el primer objetivo hace DOS cosas: cobra su fraccion y sube el
    suelo a compra +5%, asi que la operacion pasa a ser ganadora pase lo que
    pase despues.

    Historial del TP1: no vendia nada (19-sep) -> +30%/20% -> +15%/20% ->
    +25%/15% (24-sep). Lo que no cambia en ninguna version es la segunda
    parte, que es la que de verdad importa.
    """
    # LOS PRECIOS SALEN DE LOS PELDANOS, no escritos a mano. Estaban fijos
    # en 1,20 y 1,31 contra un TP1 de +30%; al moverse el peldano el test
    # habria seguido en verde midiendo un recorrido que ya no toca nada.
    t1 = _tiers()[0]
    justo_debajo, justo_encima = 1 + t1.gain - 0.05, 1 + t1.gain + 0.01

    r = simula([1.0, justo_debajo, justo_encima])
    assert r["peldanos"] == [1]
    assert not r["paro_por_stop"]
    assert round(r["restante_pct"], 6) == round(1 - t1.sell, 6)
    assert round(r["suelo_final"], 4) == round(1 + _margen(), 4)

    # Y ahora la vuelta atras: el stop la cierra en +12%, en verde. Aqui
    # `restante` SI acaba en 0 porque `vender_todo_en_stop` se lleva la
    # posicion entera -- que es lo correcto.
    # Cayendo justo hasta el suelo, se cobra el +12% clavado.
    r = simula([1.0, justo_debajo, justo_encima, 1.05, 1 + _margen()])
    assert r["paro_por_stop"]
    # Ya no es el margen clavado: un 20% se cobro a +30%, asi que el conjunto
    # sale MEJOR que el suelo. Eso es justo lo que se gana al cobrar arriba.
    assert r["resultado"] > _margen()

    # Y si el precio se salta el suelo entre dos lecturas, se vende a lo que
    # haya: sigue siendo verde, pero MENOS del 12%. El suelo es una orden, no
    # una garantia de precio -- en una moneda que cae a plomo hay hueco.
    r = simula([1.0, justo_debajo, justo_encima, 1.05, 1 + _margen() / 2])
    assert r["paro_por_stop"]
    assert 0 < r["resultado"] < _margen() + t1.sell * t1.gain


def test_los_peldanos_cobran_y_el_resto_pasa_a_la_cola() -> None:
    """Se tocan los cuatro objetivos, se vende el 85% y el 15% pasa a la cola.

    ESTE TEST SE HA REESCRITO CUATRO VECES con la escalera: exigia que la
    posicion quedara a CERO (take profit unico al 100%), luego que sobrara un
    10% (dos peldanos), y ahora que sobre el 15% que recogen los escalones de
    market cap.

    LO QUE SE COMPRUEBA AQUI es solo el tramo de PORCENTAJE: que los tres
    peldanos disparan en orden y dejan dentro exactamente lo que dice
    `moonbag_fraction`. Lo que pasa despues --la cola de market cap-- depende
    del precio de SOL y del market cap, y se fija en test_escalera_actual.py.
    """
    t = _tiers()
    assert len(t) == 6, f"este test describe la escalera de seis peldanos: {t}"
    r = simula([1.0, *(1 + x.gain + 0.01 for x in t)])
    assert r["peldanos"] == [1, 2, 3, 4, 5, 6]
    assert not r["paro_por_stop"], "no deberia saltar el stop: se cobraron todos"
    assert round(r["restante_pct"], 6) == round(moonbag_fraction(t), 6)
    assert r["resultado"] > 0


def test_por_debajo_del_objetivo_manda_el_stop_entero() -> None:
    """EL PRECIO DE TENER UN SOLO TAKE PROFIT, escrito para que no sorprenda.

    Sin un peldano pequeno que apague el stop, una moneda que sube casi hasta
    el objetivo y se da la vuelta cuesta el stop ENTERO. No hay medias
    tintas: con la escalera de antes, un pico del +54% dejaba +5,6%; ahora
    deja -26%.

    Esta puesto a sabiendas --se pidio "solo un tp y un sl" para pagar menos
    comisiones-- y esta calculado en el comentario del yaml. El test existe
    para que nadie descubra esto mirando una operacion en rojo.
    """
    t = _tiers()
    objetivo = t[0].gain
    stop = float(_trade()["stop_loss_percentage"])
    # Sube hasta justo por debajo del objetivo y se cae hasta el stop.
    r = simula([1.0, 1 + objetivo - 0.01, 1 - stop])
    assert r["peldanos"] == [], "no se cobra nada: no se llego al objetivo"
    assert r["paro_por_stop"]
    assert r["resultado"] < 0, (
        "una moneda que se queda a un punto del objetivo cuesta el stop entero"
    )


def test_un_salto_de_golpe_cobra_todos_los_peldanos_que_deja_atras() -> None:
    """Si el precio salta por encima del ultimo peldano en un solo tick, no se
    pierden los de abajo: se venden todos, al precio al que se esta vendiendo.

    EL SALTO SALE DE LA ESCALERA, no escrito a mano. Estaba fijo en x7, y la
    tarde del 24-sep la escalera paso a llegar hasta x8,968: el test se puso
    rojo, que es lo correcto, pero por la razon equivocada --no es que el
    salto no cobre los de abajo, es que x7 ya no los deja todos atras--.
    """
    t = _tiers()
    salto = (1 + max(x.gain for x in t)) * 1.1
    r = simula([1.0, salto])
    # Los que haya, no un numero escrito a mano.
    assert r["peldanos"] == list(range(1, len(t) + 1))
    assert not r["paro_por_stop"]
    assert round(r["restante_pct"], 6) == round(1 - sum(x.sell for x in t), 6)


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
    # Lo que queda tras la escalera de PORCENTAJE. No es el moonbag final
    # (3%): el 19% de en medio lo reparten los escalones de market cap, que
    # esta simulacion no recorre porque van en dolares y no en multiplos.
    assert round(r["restante_pct"], 6) == round(1 - sum(x.sell for x in tiers), 6)


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
        stepped_stop_price(ENTRADA, tiers, i, _margen()) for i in range(len(tiers))
    ]
    assert suelos == sorted(suelos)
