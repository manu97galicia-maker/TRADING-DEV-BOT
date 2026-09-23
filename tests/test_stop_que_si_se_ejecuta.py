"""El stop loss tiene que EJECUTARSE, no solo dispararse.

EL CASO REAL, CATO, 23-sep: entrada a 26K de market cap, la moneda se parte
por la mitad en medio minuto y la posicion acaba cerrada a -52,3%, con el stop
puesto en -28%. Veinticuatro puntos de diferencia entre lo configurado y lo
que paso.

POR QUE. El slippage de venta no es una preferencia: es un SUELO escrito
dentro de la transaccion (`min_sol_output`). Con 0,1 la venta le dice a la
cadena "no aceptes menos del 90% de lo que vale ahora". Si mientras la
transaccion vuela el precio cae mas de ese 10%, la cadena la RECHAZA -- y en
una moneda que se desploma eso pasa una vez, y otra, y otra. Tras tres fallos
el bot espera 20 s antes de reintentar una venta protectora, y para entonces
ya no hay nada que salvar.

O sea que el suelo estrecho hacia que el stop loss fallara JUSTO en el unico
caso para el que existe. Un take profit si puede rechazar un mal precio: si no
sale hoy, sale manana. Un stop no.
"""

# ruff: noqa: S101, SLF001, PLR2004

from pathlib import Path

import yaml

from trading.universal_trader import UniversalTrader

RAIZ = Path(__file__).resolve().parent.parent


def _trade() -> dict:
    return yaml.safe_load(
        (RAIZ / "bots/bot-paper-ai-entry.yaml").read_text(encoding="utf-8")
    )["trade"]


def _trader(normal: float = 0.1, ancho: float = 0.5) -> UniversalTrader:
    t = UniversalTrader.__new__(UniversalTrader)
    t.sell_slippage = normal
    t.sell_slippage_proteccion = ancho
    return t


def test_un_stop_loss_vende_con_el_suelo_ancho() -> None:
    """Lo que se pierde por no ejecutar un stop es mucho mayor que lo que
    cuesta ejecutarlo a mal precio."""
    t = _trader()
    for motivo in ("stop_loss", "anti_rug", "panic_sell", "trailing_stop"):
        assert t._slippage_para(motivo) == 0.5, motivo


def test_un_take_profit_mantiene_el_suelo_estrecho() -> None:
    """Un take profit SI puede esperar: si no sale a buen precio, no sale."""
    t = _trader()
    for motivo in ("tp_tier1_+25%", "tp_tier3_+80%", "moonbag"):
        assert t._slippage_para(motivo) == 0.1, motivo


def test_el_suelo_protector_nunca_es_mas_estrecho_que_el_normal() -> None:
    """Configurarlo mas bajo que el normal seria empeorar el stop sin querer."""
    t = _trader(normal=0.3, ancho=0.1)
    assert t._slippage_para("stop_loss") == 0.3


def test_sin_configurar_se_comporta_como_antes() -> None:
    """A cero, todo sigue igual: el cambio no puede alterar nada por defecto."""
    t = _trader(ancho=0)
    assert t._slippage_para("stop_loss") == 0.1
    assert t._slippage_para("tp_tier1_+25%") == 0.1


def test_no_se_le_cambia_el_suelo_al_vendedor_compartido() -> None:
    """Varias posiciones pueden estar vendiendo a la vez. Tocar el objeto
    compartido para una venta le cambiaria el suelo a las demas a mitad de
    camino, y una de ellas podria ser un take profit."""

    class _Vendedor:
        slippage = 0.1

    t = _trader()
    t.seller = _Vendedor()
    protector = t._vendedor_para("stop_loss")
    assert protector.slippage == 0.5
    assert t.seller.slippage == 0.1, "le ha cambiado el suelo al compartido"
    assert t._vendedor_para("tp_tier1_+25%") is t.seller


def test_la_config_lo_lleva_puesto() -> None:
    t = _trade()
    assert t["sell_slippage_proteccion"] >= 0.3, (
        "con el suelo estrecho el stop loss puede no ejecutarse"
    )
    assert t["sell_slippage_proteccion"] > t["sell_slippage"]
