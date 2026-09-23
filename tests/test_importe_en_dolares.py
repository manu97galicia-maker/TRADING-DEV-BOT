"""El tamano de la compra se decide en DOLARES: 10 $ por operacion.

PEDIDO EL 23-SEP: "compra 10 $ por operacion en sol".

POR QUE EN DOLARES Y NO EN SOL, que es la decision de fondo. "10 $" escrito
como un numero de SOL es un numero que caduca: con SOL a 220 $ son 0,045 SOL,
y el dia que SOL valga 300 esa misma linea gasta 13,50 $ sin que nadie lo haya
decidido. Dividiendo por el precio EN VIVO la apuesta vale lo mismo siempre.

LA TRAMPA QUE VIGILA ESTE FICHERO, y es de las caras. El precio de SOL escrito
a mano en el codigo es de agosto: 91,24 $. El bot lo refresca solo desde
DexScreener, pero hasta que acierta usa ese. Gastar "10 $" a 91,24 con SOL a
220 son 0,1096 SOL = 24 $ DE VERDAD, en cada compra y sin que nadie se entere.
Por eso, mientras el precio no este confirmado, NO SE COMPRA.
"""

# ruff: noqa: S101, SLF001, PLR2004, PLC0415, ANN003

import types
from pathlib import Path

import yaml

from trading.universal_trader import UniversalTrader

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"
FUENTE = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _trader(**kw) -> UniversalTrader:
    """Un trader con lo justo para el cartel, sin tocar red ni wallet."""
    t = UniversalTrader.__new__(UniversalTrader)
    for attr, _corto in UniversalTrader.FUENTES:
        setattr(t, attr, {})
    t.pausar_compras = False
    t.paper_trade = False
    t.paper_engine = types.SimpleNamespace(enabled=False)
    t.tope_compra_sol = 0
    t.buy_amount = 0
    t.fraccion_saldo = 0.0
    t.buy_amount_usd = 10.0
    t.max_posiciones = 5
    for k, v in kw.items():
        setattr(t, k, v)
    return t


# --------------------------------------------------------- la configuracion


def test_son_diez_dolares_y_manda_el() -> None:
    """Y solo el: dos modos con numero es un valor que parece decidir y no
    decide, que es la clase de cosa que hace buscar el fallo donde no esta."""
    t = _trade()
    assert t["buy_amount_usd"] == 10
    encendidos = [
        k
        for k in ("buy_amount_usd", "fraccion_saldo", "buy_amount")
        if t.get(k, 0) > 0
    ]
    assert encendidos == ["buy_amount_usd"], f"mandan: {encendidos}"


def test_el_yaml_llega_al_trader() -> None:
    """EL NO-OP MAS CARO QUE HAY: una clave nueva en el yaml que nadie lee.

    Todo pasa --el bot arranca, no se queja-- y sigue comprando con el modo
    anterior. Desde fuera el ajuste "no hace nada" y se busca el fallo en la
    estrategia.
    """
    runner = (RAIZ / "src/bot_runner.py").read_text(encoding="utf-8")
    assert 'buy_amount_usd=cfg["trade"].get("buy_amount_usd", 0)' in runner


# --------------------------------------------------- el calculo en el codigo


def test_el_importe_se_calcula_con_el_precio_en_vivo() -> None:
    """SE LEE EN CADA COMPRA, no una copia guardada al arrancar.

    `_precio_sol_loop` reescribe `_SOL_USD` cada hora. Si el importe se
    calculara con una copia hecha en el constructor, el bot se quedaria
    congelado en el precio de arranque para siempre y la correccion horaria no
    serviria de nada -- funcionando, sin errores y comprando mal.

    Se comprueba de verdad, moviendo el precio y mirando el numero que sale,
    en vez de buscar un texto en el fichero: asi tambien caza el dia que
    alguien guarde el precio en `self.` en el constructor, que es justo la
    forma en que esto se rompe.
    """
    import trading.universal_trader as _mod

    t = _trader()
    viejo = _mod._SOL_USD
    try:
        _mod._SOL_USD = 220.0
        uno = t._cartel_de_configuracion()
        _mod._SOL_USD = 110.0
        dos = t._cartel_de_configuracion()
    finally:
        _mod._SOL_USD = viejo
    assert "0.0455 SOL" in uno, uno
    assert "0.0909 SOL" in dos, "al bajar SOL a la mitad, la compra dobla en SOL"

    # Y el calculo de la compra sale de esa misma global, no de una copia.
    assert "_sol_usd = float(_SOL_USD)" in FUENTE
    assert "_importe = self.buy_amount_usd / _sol_usd" in FUENTE


def test_sin_precio_confirmado_no_se_compra() -> None:
    """EL FRENO QUE EVITA GASTAR 2,4 VECES LA APUESTA.

    Mientras `_SOL_USD` siga en el valor por defecto de agosto, cualquier
    importe en dolares esta mal. El refresco reintenta cada 10 segundos, asi
    que esperar cuesta segundos; comprar a ciegas cuesta dinero en cada
    operacion.

    Y SE SUELTA LA MONEDA. Sin eso, la moneda se quedaria reservada para los
    tres bots hasta que caducara el fichero de reserva (10 min), o sea que un
    precio sin confirmar durante un minuto bloquearia monedas que luego si se
    podrian comprar.
    """
    trozo = FUENTE[FUENTE.index("if self.buy_amount_usd > 0:"):]
    trozo = trozo[: trozo.index("elif self.fraccion_saldo > 0:")]
    assert "if _sol_usd == _ARRANQUE_SOL_USD:" in trozo
    assert "_suelta_moneda(_mint_str)" in trozo
    assert "return" in trozo
    # Y el aviso tiene que ser de los que se ven: esto para las compras.
    assert "logger.error(" in trozo


def test_el_log_deja_rehacer_la_cuenta_a_mano() -> None:
    """Desde el movil el log es lo unico que hay. "compra de 0,045 SOL" no
    deja comprobar nada; con los dolares y el cambio escritos al lado se ve de
    un vistazo si el precio de SOL que esta usando es creible."""
    assert "IMPORTE: %.2f $ a %.2f $/SOL" in FUENTE


# ------------------------------------------------------------- el cartel


def test_el_cartel_dice_los_dolares_y_los_sol() -> None:
    """LOS DOS NUMEROS, no uno.

    El dolar es lo que se decidio; los SOL son lo que de verdad va a salir de
    la wallet. Cual de los dos esta mal solo se ve si estan los dos escritos
    -- con el cambio en medio, un precio de SOL disparatado canta a la
    primera.
    """
    import trading.universal_trader as _mod

    viejo = _mod._SOL_USD
    try:
        _mod._SOL_USD = 220.0
        cartel = _trader()._cartel_de_configuracion()
    finally:
        _mod._SOL_USD = viejo
    assert "10.00 $" in cartel
    assert "0.0455 SOL" in cartel
    assert "220.00 $/SOL" in cartel
    assert "SIN CONFIRMAR" not in cartel


def test_el_cartel_avisa_si_el_precio_de_sol_no_se_ha_confirmado() -> None:
    """Que se sepa AL ARRANCAR, no en la primera compra fallida.

    Es la linea que se mira para confirmar que lo que corre es lo que se cree.
    Un bot que no va a comprar tiene que decirlo ahi, no callarse y parecer
    normal -- ese silencio ya costo dos dias con el 429 de Helius.
    """
    import trading.universal_trader as _mod

    viejo = _mod._SOL_USD
    try:
        _mod._SOL_USD = _mod._ARRANQUE_SOL_USD
        cartel = _trader()._cartel_de_configuracion()
    finally:
        _mod._SOL_USD = viejo
    assert "SIN CONFIRMAR" in cartel
