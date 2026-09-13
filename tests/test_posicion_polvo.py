"""Una posicion de menos de un dolar deja de gestionarse.

Lo pedido: no tener en consideracion las monedas con posicion abierta por
debajo de un dolar.

El motivo de fondo es economico, no de limpieza. Vender cuesta comision de
prioridad mas la renta de la cuenta de token; por debajo de un dolar la venta
se lleva mas de lo que recupera, asi que el stop loss y los take profit no
tienen nada que defender ahi. Y mientras tanto esa bolsa:

  - ocupa una de las 5 plazas de posicion, que es una compra que no se hace
  - gasta una consulta de precio POR SEGUNDO de las 15 que da el nodo, que
    es justo lo que necesitan las posiciones que si valen algo

La bolsa no se vende ni se pierde: se queda en la wallet.
"""

# ruff: noqa: S101

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
FUENTE = Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"
#: El precio del SOL que usa el bot para pasar a dolares (_SOL_USD).
SOL_USD = 91.24


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _fuente() -> str:
    return FUENTE.read_text(encoding="utf-8")


def vale_usd(cantidad: float, precio_sol: float) -> float:
    return cantidad * precio_sol * SOL_USD


def test_el_minimo_es_un_dolar() -> None:
    assert _trade()["valor_minimo_posicion_usd"] == 1.0


def test_una_bolsa_de_centimos_esta_por_debajo() -> None:
    """El caso real: el moonbag del 2% de una compra que se hundio."""
    compra_sol = _trade()["buy_amount"]
    moonbag = 0.02
    # La moneda cae un 90% desde la entrada.
    valor = compra_sol * moonbag * 0.10 * SOL_USD
    assert valor < _trade()["valor_minimo_posicion_usd"]


def test_una_posicion_entera_recien_comprada_no_se_suelta() -> None:
    """Lo que NO puede pasar: que la regla tire una posicion buena."""
    compra_sol = _trade()["buy_amount"]
    assert compra_sol * SOL_USD > _trade()["valor_minimo_posicion_usd"] * 30


def test_el_monitor_suelta_la_plaza_con_el_polvo() -> None:
    texto = _fuente()
    assert "_valor_usd = remaining * price * _SOL_USD" in texto
    assert "if _valor_usd < self.valor_minimo_posicion_usd:" in texto


def test_la_regla_se_puede_apagar() -> None:
    """Con 0 no se suelta nada, que es lo que espera quien pone un cero."""
    texto = _fuente()
    assert "if self.valor_minimo_posicion_usd > 0:" in texto


def test_el_avisador_deja_de_dar_la_lata_con_el_polvo() -> None:
    """Sin esto, un moonbag de 30 centimos avisa cada 5 minutos para siempre."""
    texto = _fuente()
    assert "dejo de avisar" in texto
    assert 'float(m.get("qty") or 0) * price * _SOL_USD' in texto


def test_la_bolsa_no_se_vende_al_soltarla() -> None:
    """Soltar la plaza no es vender: a ese precio la comision se lo come.
    La bolsa se queda en la wallet y la recoge la limpieza de cuentas."""
    texto = _fuente()
    inicio = texto.index("POLVO: lo que queda ya no vale la pena gestionar")
    bloque = texto[inicio : inicio + 1800]
    corte = bloque.index("break")
    assert "_sell_amount" not in bloque[:corte], (
        "el bloque del polvo esta vendiendo: deberia solo soltar la plaza"
    )
