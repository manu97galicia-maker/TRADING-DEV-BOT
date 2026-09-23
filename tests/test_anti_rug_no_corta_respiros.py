"""El anti-rug tiene que salir de un volcado, no de un respiro.

EL CASO QUE LO DESTAPO (23-sep). ManucallsAI: comprada a 39,9k de market cap y
vendida ENTERA cuarenta segundos despues, a +4,8%. La moneda siguio subiendo
hasta 58,2k sin nosotros.

COMO SE SUPO QUE ERA ESTO y no un take profit: la posicion registro DOS
transacciones en total, o sea una compra y una venta. Un peldano de la
escalera vende una fraccion y deja el resto, asi que habria tres o mas. Una
sola venta de todo, a los cuarenta segundos, solo la hacen tres cosas: el
anti-rug, el boton de panico y una venta manual.

POR QUE SALTABA. Estaba en 3 SOL con ratio 2 en 4 segundos. 3 SOL son unos
660 $: que en CUATRO SEGUNDOS se vendan 660 $ y se compren menos de 330 no es
un volcado, en una moneda que se esta moviendo de 40k a 58k es un respiro
normal. El corte venia de monedas mucho mas grandes.

LO QUE SE ELIGIO: 12 SOL con ratio 3. Y lo que cuesta, que hay que tenerlo
escrito: en un rug que empiece flojo se sale mas tarde y mas abajo. Debajo
sigue el stop del 40%.
"""

# ruff: noqa: S101, PLR2004

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"
FUENTE = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
#: Precio de SOL de referencia para pasar los cortes a dolares. No es el que
#: usa el bot (ese lo refresca solo); aqui solo sirve para razonar.
SOL_USD_REF = 220.0


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def test_el_corte_esta_donde_se_decidio() -> None:
    """Los tres numeros juntos, porque solo juntos significan algo."""
    t = _trade()
    assert t["anti_rug_min_sol"] == 12
    assert t["anti_rug_ratio"] == 3.0
    assert t["anti_rug_seg"] == 4


def test_un_respiro_normal_ya_no_dispara() -> None:
    """EL CASO DE ManucallsAI, con numeros.

    Unos cientos de dolares vendidos en cuatro segundos contra la mitad de
    compras es lo que hace cualquier moneda que sube a tirones. Con el corte
    viejo eso cerraba la posicion entera.
    """
    t = _trade()
    vendidos_sol = 660 / SOL_USD_REF  # los ~3 SOL del corte viejo
    comprados_sol = 330 / SOL_USD_REF
    dispara = (
        vendidos_sol >= t["anti_rug_min_sol"]
        and vendidos_sol >= comprados_sol * t["anti_rug_ratio"]
    )
    assert not dispara, (
        f"{vendidos_sol:.1f} SOL vendidos contra {comprados_sol:.1f} en "
        f"{t['anti_rug_seg']}s siguen disparando el anti-rug: es un respiro, "
        "no un volcado"
    )


def test_un_volcado_de_verdad_sigue_disparando() -> None:
    """LO QUE NO SE PUEDE PERDER al aflojarlo.

    Un rug vacia la curva: decenas de SOL en segundos y practicamente nadie
    comprando. Si el corte sube tanto que ni eso lo dispara, la proteccion
    deja de existir y solo queda el stop del 40%.
    """
    t = _trade()
    vendidos_sol = 40.0  # ~8.800 $ en cuatro segundos
    comprados_sol = 2.0
    dispara = (
        vendidos_sol >= t["anti_rug_min_sol"]
        and vendidos_sol >= comprados_sol * t["anti_rug_ratio"]
    )
    assert dispara, "un volcado de 40 SOL en 4s tiene que sacar de la posicion"


def test_el_stop_sigue_debajo_como_red() -> None:
    """Aflojar el anti-rug solo es aceptable porque hay algo debajo."""
    t = _trade()
    assert t["stop_loss_percentage"] > 0, (
        "sin stop, aflojar el anti-rug deja la posicion sin ninguna red"
    )


def test_el_anti_rug_vende_la_posicion_entera() -> None:
    """Es una salida de EMERGENCIA, no un peldano: se lleva todo, moonbag
    incluido. Si algun dia vendiera una fraccion, dejaria un resto corriendo
    en una moneda que se esta hundiendo -- que es como se acumularon las
    bolsas huerfanas."""
    trozo = FUENTE[FUENTE.index("if self.anti_rug_ratio > 0:"):]
    trozo = trozo[:3000]
    assert 'self._sell_amount(\n                                token_info, remaining, price, "anti_rug"' in trozo, (
        "el anti-rug tiene que vender `remaining`, o sea todo lo que queda"
    )
