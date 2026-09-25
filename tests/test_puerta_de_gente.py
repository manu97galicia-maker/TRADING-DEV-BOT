"""La puerta de "cuanta gente ha pasado" tiene que ir con la entrada.

EL FALLO QUE VIGILA, y tuvo al bot mudo varios dias (25-sep). `min_compradores`
se calibro en 30 cuando se entraba al 96% de la curva -- de ahi el nombre
`curva_96`. Despues la entrada bajo al 90, al 84, al 79 y al 71 en dos dias, y
este numero se quedo quieto. El resultado no fue "compra peor": fue que dejo de
comprar. El embudo de esa tarde: 13.185 monedas con movimiento, 14 llegaron al
umbral, 0 se compraron, y las que se caian lo hacian aqui, con mensajes como
"solo 1 compradores distintos (min 30)".

Lo que se aprende: bajar la entrada y este numero son EL MISMO mando. Una
moneda al 71% de la curva ha pasado por menos carteras que una al 96%, asi que
mover una sin mover el otro aprieta el filtro sin que nadie lo pida -- y aprieta
en silencio, que es lo caro.

LA CUENTA. Lo que mide `min_compradores` es cuanta gente ha pasado por la
moneda, y eso va con el SOL que hay DENTRO de la curva en el punto de entrada:

    min_compradores = 30 x (SOL dentro al umbral / SOL dentro al 96%)

Este fichero NO fija el 14 a mano: recalcula el numero desde el `umbral` que
haya en el YAML. Asi que si manana la entrada sube o baja, el test dice en rojo
cual es el numero nuevo en vez de dejarlo pasar.
"""

# ruff: noqa: S101

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"

# La curva virtual de pump.fun, la misma que usa el resto del repo.
_VT_INICIAL = 1073e6
_VT_RECORRIDO = 793.1e6
_K = 30 * _VT_INICIAL

# El punto donde se calibro el 30, escrito en el nombre de la estrategia.
_CALIBRADA_EN = 0.96
_CALIBRADA_CON = 30


def _sol_dentro(avance: float) -> float:
    """SOL acumulados en la curva cuando lleva recorrido ``avance``."""
    return _K / (_VT_INICIAL - _VT_RECORRIDO * avance) - 30


def _esperado(umbral: float) -> int:
    """Lo que tendria que pedir la puerta de gente para esa entrada."""
    proporcion = _sol_dentro(umbral) / _sol_dentro(_CALIBRADA_EN)
    return round(_CALIBRADA_CON * proporcion)


def _curva() -> dict:
    cfg = yaml.safe_load(CONFIG.read_text())
    return cfg["filters"]["curva_96"]


def test_la_puerta_de_gente_va_con_la_entrada() -> None:
    """Si alguien mueve el umbral, este numero se mueve con el o esto es rojo."""
    curva = _curva()
    umbral = float(curva["umbral"])
    pedido = int(curva["min_compradores"])
    debido = _esperado(umbral)
    assert pedido == debido, (
        f"Se entra al {umbral:.1%} de la curva, donde hay "
        f"{_sol_dentro(umbral):.1f} SOL dentro. Para esa entrada la puerta "
        f"tendria que pedir {debido} compradores distintos, no {pedido}. "
        "Bajar la entrada sin bajar esto aprieta el filtro en silencio, que es "
        "justo lo que dejo al bot sin comprar el 25-sep."
    )


def test_la_cuenta_da_el_30_en_su_punto_de_origen() -> None:
    """La formula tiene que devolver el 30 donde se calibro, o no es la misma."""
    assert _esperado(_CALIBRADA_EN) == _CALIBRADA_CON


def test_la_puerta_de_la_curva_no_baja_del_suelo_de_todas_las_fuentes() -> None:
    """El corte de esta estrategia es mas fino que el minimo comun, no mas laxo.

    `min_compradores_global` es el suelo que se aplica venga la moneda de donde
    venga. Si la puerta de la curva cayera por debajo, dejaria de decidir nada:
    mandaria el suelo y este numero seria decorativo.
    """
    cfg = yaml.safe_load(CONFIG.read_text())
    suelo = int(cfg["filters"]["min_compradores_global"])
    curva = int(cfg["filters"]["curva_96"]["min_compradores"])
    assert curva >= suelo, (
        f"La curva pide {curva} compradores y el suelo comun pide {suelo}: "
        "por debajo del suelo este numero no decide nada."
    )


def test_pedir_mas_gente_cuanto_mas_llena_la_curva() -> None:
    """Cuanto mas arriba se entra, mas gente ha pasado ya: la cuenta lo refleja."""
    escalones = [_esperado(a) for a in (0.71, 0.79, 0.90, 0.96)]
    assert escalones == sorted(escalones)
    assert escalones[0] < escalones[-1]
