"""El sitio por donde se entra tiene que dar para cobrar el primer peldano.

EL DESCUBRIMIENTO (23-sep), al bajar el umbral de entrada a peticion ("entra
un 1,6% antes"). Son dos ajustes que siempre se habian tocado por separado y
resulta que tienen que cuadrar entre si:

* `filters.curva_96.umbral` dice por donde se ENTRA.
* `trade.take_profit_percentage` dice cuanto tiene que subir para cobrar el
  primer peldano.

Con el umbral al 96% quedaba un +24% de recorrido hasta migrar y el TP1 estaba
en +30%. O sea que la escalera NO PODIA cobrar ni un peldano mientras la moneda
siguiera en la curva: hacia falta que migrara Y siguiera subiendo ya en
PumpSwap, que es otro mercado y otra liquidez. Nada fallaba, no habia ningun
error en el log: simplemente los dos numeros no encajaban.

LA CUENTA. La curva de pump.fun es un producto constante entre reservas
virtuales, asi que el precio va como 1/(tokens_virtuales)^2 y sube cada vez mas
rapido segun se llena. Por eso entrar "1,6 puntos antes" no es entrar un 1,6%
mas barato: es entrar un 7,7% mas abajo y con un 43% mas de recorrido.

Este test no exige un umbral concreto --ese es una decision, y se cambia-- sino
que los dos numeros sigan cuadrando. Si alguien sube el umbral o el TP1 hasta
que dejen de hacerlo, lo dice aqui en vez de descubrirse en las operaciones.
"""

# ruff: noqa: S101, PLR2004

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"

#: Reservas con las que nace una curva de pump.fun.
V_TOKENS = 1_073_000_000.0
V_SOL = 30.0
#: Tokens que la curva pone a la venta. Cuando se agotan, la moneda migra.
TOKENS_A_LA_VENTA = 793_100_000.0
_K = V_TOKENS * V_SOL


def _cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def precio(avance: float) -> float:
    """Precio relativo con la curva llena al `avance` (0 a 1).

    `avance` es lo mismo que mide el bot en `volumen.progreso_curva`: cuanto
    del tramo a la venta se ha vendido ya.
    """
    tokens_virtuales = V_TOKENS - TOKENS_A_LA_VENTA * avance
    return _K / tokens_virtuales**2


def recorrido_hasta_migrar(avance: float) -> float:
    """Cuanto sube el precio desde ese punto hasta que la moneda migra."""
    return precio(1.0) / precio(avance) - 1.0


def test_la_cuenta_de_la_curva_da_los_85_sol_conocidos() -> None:
    """PRIMERO, QUE EL MODELO SEA EL DE VERDAD.

    Un test construido sobre una formula equivocada da veredictos con total
    seguridad y manda a cambiar cosas que estaban bien. La comprobacion
    barata: una curva de pump.fun migra con unos 85 SOL dentro, y eso es un
    hecho publico que no depende de esta configuracion.
    """
    sol_dentro = _K / (V_TOKENS - TOKENS_A_LA_VENTA) - V_SOL
    assert 84.0 < sol_dentro < 86.0, f"la formula da {sol_dentro:.1f} SOL"


def test_desde_donde_se_entra_se_puede_cobrar_el_primer_peldano() -> None:
    """LO QUE TIENE QUE CUADRAR, y es el motivo de este fichero.

    Si el recorrido que queda es menor que el primer peldano, la escalera no
    cobra nada mientras la moneda siga en la curva. No es un fallo que se vea:
    el bot compra, vigila y vende por tiempo o por stop, y los take profit
    parecen "no saltar nunca".
    """
    c = _cfg()
    umbral = c["filters"]["curva_96"]["umbral"]
    tp1 = c["trade"]["take_profit_percentage"]
    queda = recorrido_hasta_migrar(umbral)
    assert queda > tp1, (
        f"entrando al {umbral:.1%} solo queda un {queda:.1%} hasta migrar, y "
        f"el primer take profit pide un {tp1:.1%}: la escalera no puede "
        "cobrar ni un peldano en la curva. O se entra antes, o se baja el TP1."
    )


def test_hay_margen_y_no_se_cobra_justo_en_la_migracion() -> None:
    """Que cuadre por los pelos no sirve: la migracion no es un punto amable.

    Justo ahi la curva deja de operar, hay un hueco sin precio y la liquidez
    se muda a otro sitio. Cobrar el TP1 tiene que caer con holgura ANTES de
    eso, no en el ultimo suspiro.
    """
    c = _cfg()
    umbral = c["filters"]["curva_96"]["umbral"]
    tp1 = c["trade"]["take_profit_percentage"]
    assert recorrido_hasta_migrar(umbral) > tp1 * 1.10, (
        "el primer peldano cae demasiado cerca de la migracion"
    )


def test_no_se_entra_tan_abajo_que_deje_de_ser_esta_estrategia() -> None:
    """EL OTRO LADO, porque bajar el umbral no es gratis.

    La idea de esta fuente es comprar monedas que ya casi lo han conseguido:
    cuanto mas abajo se entra, mas se parece a comprar una moneda cualquiera y
    mas de ellas se quedan a medias sin migrar nunca. Por debajo del 90% ya no
    es "casi llena" y habria que llamarla otra cosa.
    """
    c = _cfg()["filters"]["curva_96"]
    assert c["umbral"] >= 0.90, "por debajo del 90% esto ya no es la curva casi llena"
    assert c["umbral"] < c["tope"] <= 1.0
