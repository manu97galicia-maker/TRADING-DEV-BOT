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
SALDO_REFERENCIA = 1.0
SOL_USD = 91.24


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _fuente() -> str:
    return FUENTE.read_text(encoding="utf-8")


def vale_usd(cantidad: float, precio_sol: float) -> float:
    return cantidad * precio_sol * SOL_USD


def _importe(libre: float, t: dict) -> float:
    """El importe de una compra, como lo calcula el bot.

    El tope y el suelo solo se aplican si son MAYORES QUE CERO: en toda esta
    configuracion, cero significa "sin limite". Escribir `min(x, tope)` a secas
    daria 0 en cuanto el tope se apague.
    """
    imp = max(0.0, libre * t["fraccion_saldo"])
    tope = t["tope_compra_sol"]
    if tope > 0 and imp > tope:
        imp = tope
    return imp


def compra_tipica() -> float:
    """Tamano representativo de una posicion, en SOL.

    Desde el 19-sep el importe NO es fijo: es el 25% del saldo libre, asi que
    "el tamano de la compra" ya no es un numero del yaml. Estos tests necesitan
    uno concreto, y se toma el de la PRIMERA compra con un saldo de referencia
    de 1 SOL, que es el caso que interesa mirar (la posicion mas grande que se
    abre; si la regla del polvo no la suelta a ella, no suelta a ninguna).
    """
    t = _trade()
    return _importe(SALDO_REFERENCIA - t["min_reserve_sol"], t)


def test_el_minimo_es_un_dolar() -> None:
    assert _trade()["valor_minimo_posicion_usd"] == 1.0


def test_una_bolsa_de_centimos_esta_por_debajo() -> None:
    """El caso real: el moonbag del 2% de una compra que se hundio."""
    compra_sol = compra_tipica()
    moonbag = 0.02
    # La moneda cae un 90% desde la entrada.
    valor = compra_sol * moonbag * 0.10 * SOL_USD
    assert valor < _trade()["valor_minimo_posicion_usd"]


def test_una_posicion_entera_recien_comprada_no_se_suelta() -> None:
    """Lo que NO puede pasar: que la regla tire una posicion buena."""
    compra_sol = compra_tipica()
    assert compra_sol * SOL_USD > _trade()["valor_minimo_posicion_usd"] * 3


def test_hasta_donde_llega_la_escalera_con_el_tamano_de_compra_de_hoy() -> None:
    """CONSECUENCIA DE COMPRAR PEQUENO, escrita para que no sorprenda.

    La regla del polvo suelta la plaza cuando lo que queda vale menos de
    `valor_minimo_posicion_usd`. Con compras grandes eso solo pasaba al final
    de todo; con compras pequenas la escalera podria dejar de gestionarse
    mucho antes, y eso es lo que aqui se mide.

    Este test calcula EN QUE PELDANO pasa. El resultado es tranquilizador: la
    escalera aguanta ENTERA, hasta el x20. La razon es que la regla mira el
    VALOR de lo que queda, no la fraccion, y el precio sube mas deprisa de lo
    que encoge la bolsa.

    O sea que comprar pequeno NO deja los peldanos altos sin cobrar, que era
    lo que se temia. Si algun dia se baja mucho el porcentaje (o se sube el
    minimo de polvo), este test se pone rojo y lo dice.
    """
    t = _trade()
    valor = compra_tipica() * SOL_USD
    minimo = t["valor_minimo_posicion_usd"]
    escalera = [(t["take_profit_percentage"], t["take_profit_sell_percentage"])]
    n = 2
    while t.get(f"tp{n}_gain") and t.get(f"tp{n}_sell"):
        escalera.append((float(t[f"tp{n}_gain"]), float(t[f"tp{n}_sell"])))
        n += 1
    for e in t.get("tp_extra") or []:
        escalera.append((float(e["gain"]), float(e["sell"])))
    escalera.sort()

    queda = 1.0
    ultimo_gestionado = None
    for ganancia, vende in escalera:
        queda -= vende
        # Al cobrar este peldano el precio esta a (1 + ganancia) de la entrada.
        if queda * valor * (1 + ganancia) < minimo:
            break
        ultimo_gestionado = ganancia

    assert ultimo_gestionado is not None, (
        "con este tamano de compra la posicion es polvo desde el primer "
        "peldano: la escalera no gestionaria nada"
    )
    # Con 0,07 SOL aguanta la escalera entera: el ultimo peldano tambien.
    assert ultimo_gestionado == escalera[-1][0], (
        f"solo se gestiona hasta +{ultimo_gestionado * 100:.0f}% y la escalera "
        f"llega a +{escalera[-1][0] * 100:.0f}%: por encima la bolsa se "
        "quedaria en la wallet sin vender"
    )


def test_el_monitor_suelta_la_plaza_con_el_polvo() -> None:
    texto = _fuente()
    assert "_valor_usd = remaining * price * _SOL_USD" in texto
    assert "if _valor_usd < self.valor_minimo_posicion_usd:" in texto


def test_la_regla_se_puede_apagar() -> None:
    """Con 0 no se suelta nada, que es lo que espera quien pone un cero."""
    texto = _fuente()
    assert "if self.valor_minimo_posicion_usd > 0 and price > 0:" in texto


def test_un_precio_malo_no_suelta_la_posicion() -> None:
    """LA GUARDA QUE FALTABA, y que se anadio el 19-sep.

    `calculate_price` devuelve 0.0 cuando las reservas virtuales estan a cero,
    en vez de lanzar. Sin el `price > 0`, ese cero daba "vale 0 $, menos del
    minimo" y la regla hacia `break`: posicion sin vigilar y sin stop loss,
    para siempre, por UNA lectura mala.
    """
    texto = _fuente()
    assert "and price > 0:" in texto
    # Y ademas hace falta que se repita: una lectura suelta no decide.
    assert "_polvo_seguidas >= 2" in texto


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
