"""El stop loss tiene que poder EJECUTARSE, no solo dispararse.

PEDIDO EL 15-oct: "asegurate q el take profit y stop loss saltan de inmediato".

LO QUE YA ESTABA BIEN, y por eso no se toco: la DETECCION. El monitor mira el
precio cada `price_check_interval` (1 s) y, con el stream de precios apagado,
pregunta al nodo en cada vuelta SIN pasar por la cache --a proposito, lo
explica `_precio_actual`-- para que el stop no decida sobre un precio viejo.

LO QUE NO ESTABA. Disparar no es vender. Un stop salta justo cuando todo el
mundo esta vendiendo y pagando por salir, y ahi dos cosas deciden si tu venta
aterriza o se queda en la cola:

  1. LA COMISION. C123 tenia un solo `fixed_amount` para todo
     (8.480.000 -> 0,00102 SOL por venta). El mecanismo para reforzar SOLO
     las ventas protectoras existe --`fixed_amount_venta_proteccion`-- y
     estaba a 0, o sea apagado. El propio trader lo explica: "un stop loss no
     puede esperar: cada segundo de cola es precio que se pierde".
  2. EL SUELO DE VENTA. `sell_slippage_proteccion` estaba sin poner, asi que
     el stop salia con el 25% normal. En `_slippage_para`: "el suelo estrecho
     hacia que un stop loss NO SE EJECUTARA justo en la moneda que se estaba
     desplomando".

Y LA TRAMPA QUE ESTE FICHERO VIGILA DE VERDAD: el `hard_cap` RECORTA la
comision protectora (`universal_trader.py:775`). Con el tope pegado al
`fixed_amount`, subir la comision protectora la deja ESCRITA Y MUDA, que es
el fallo de familia de este repo (`min_presion_ratio` sin `max_buy_age`, el
buffer de 32 puntos, los dos cupos de compras). Por eso no basta con
comprobar que el numero esta: hay que comprobar que CABE.
"""

# ruff: noqa: S101, PLR2004, TRY003

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent

#: Unidades de computo de una venta, para pasar microlamports a SOL.
CU_VENTA = 120_000


def _bot_encendido() -> tuple[str, dict]:
    """El bot que de verdad arranca. Ver `run_all_bots`."""
    for fichero in sorted((RAIZ / "bots").glob("*.yaml")):
        cfg = yaml.safe_load(fichero.read_text(encoding="utf-8")) or {}
        if cfg.get("enabled", True):
            return fichero.name, cfg
    raise AssertionError("no hay ningun bot encendido en bots/")


def _sol(ulam: int) -> float:
    return ulam * CU_VENTA / 1e15


def test_la_venta_protectora_paga_mas_que_una_venta_normal() -> None:
    """Si paga lo mismo, el stop va en la misma cola que todo lo demas."""
    nombre, cfg = _bot_encendido()
    pf = cfg.get("priority_fees") or {}
    normal = int(pf.get("fixed_amount") or 0)
    protectora = int(pf.get("fixed_amount_venta_proteccion") or 0)
    assert protectora > 0, (
        f"{nombre}: `fixed_amount_venta_proteccion` a 0 = apagado. El stop "
        f"loss pagaria lo mismo que un take profit ({_sol(normal):.5f} SOL) "
        "y se queda en la cola justo cuando la moneda se desploma"
    )
    assert protectora > normal, (
        f"{nombre}: la comision protectora ({protectora}) no es mayor que la "
        f"normal ({normal}): no sirve de nada"
    )


def test_el_tope_no_deja_muda_la_comision_protectora() -> None:
    """EL TEST QUE IMPORTA. El `hard_cap` recorta, y recorta en silencio.

    `universal_trader.py` hace
    `if self.fee_venta_proteccion > hard_cap_prior_fee > 0: ... se recorta`.
    Con el tope por debajo, el numero queda escrito en el yaml y no se aplica.
    """
    nombre, cfg = _bot_encendido()
    pf = cfg.get("priority_fees") or {}
    protectora = int(pf.get("fixed_amount_venta_proteccion") or 0)
    tope = int(pf.get("hard_cap") or 0)
    if protectora <= 0:
        return  # lo cubre el test de arriba
    assert tope <= 0 or tope >= protectora, (
        f"{nombre}: el tope ({tope}) recorta la comision protectora "
        f"({protectora}). Quedaria escrita y MUDA: el stop volveria a pagar "
        f"lo mismo que una venta normal sin que nada lo dijera"
    )


def test_el_stop_sale_con_el_suelo_ancho() -> None:
    """En un desplome el precio se mueve mas que el slippage normal."""
    nombre, cfg = _bot_encendido()
    t = cfg["trade"]
    normal = float(t.get("sell_slippage") or 0)
    ancho = float(t.get("sell_slippage_proteccion") or 0)
    assert ancho > 0, (
        f"{nombre}: `sell_slippage_proteccion` sin poner. El stop sale con el "
        f"suelo normal ({normal:.0%}) y en un desplome el precio se mueve mas "
        "que eso entre que se decide y aterriza: la venta se cae y te quedas "
        "dentro"
    )
    assert ancho >= normal, (
        f"{nombre}: el suelo protector ({ancho:.0%}) es mas estrecho que el "
        f"normal ({normal:.0%}), que es lo contrario de lo que hace falta"
    )


def test_el_precio_del_stop_no_viene_de_una_cache() -> None:
    """Un stop que decide sobre un precio viejo llega tarde por definicion.

    Con `precio_stream_seg` apagado el trader pregunta al nodo en CADA vuelta
    sin cache. Eso es deliberado y esta explicado en el codigo; si alguien
    mete una cache en ese camino, el stop empieza a decidir tarde sin que
    nada cambie a la vista.
    """
    trader = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    assert "if self.precio_stream_seg <= 0:" in trader, (
        "ha desaparecido la rama que pregunta al nodo sin cache: revisar que "
        "el stop loss siga decidiendo sobre un precio fresco"
    )


def test_el_bot_mira_el_precio_a_menudo() -> None:
    """De nada sirve una venta rapida si se entera un minuto tarde."""
    nombre, cfg = _bot_encendido()
    intervalo = float(cfg["trade"].get("price_check_interval") or 0)
    assert 0 < intervalo <= 5, (
        f"{nombre}: mira el precio cada {intervalo}s. Por encima de 5 el stop "
        "loss reacciona tarde pase lo que pase con la comision"
    )
