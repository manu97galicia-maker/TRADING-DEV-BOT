"""Con `max_buy_age: 0` el bot rechazaba TODAS las monedas. Nunca compraba.

EL FALLO, que tuvo el bot horas encendido y en modo real sin comprar una sola
moneda, pareciendo que simplemente no llegaban candidatas:

    while monotonic() - _t0 < self.max_buy_age:   # con 0 -> False ya
        ...
        break                                     # nunca se alcanza
    else:
        logger.info("Evitado ...: no cumplio en 0s")
        return                                    # SIEMPRE

Es un `while ... else` de Python, y ese `else` se ejecuta cuando el bucle
termina sin `break` -- INCLUIDO el caso de que no llegara a correr ni una
vuelta. Con la ventana a cero la condicion es falsa desde el primer instante,
el cuerpo no se ejecuta, el `break` no se alcanza, y el `else` rechaza la
moneda sin haber comprobado absolutamente nada.

Lo insidioso es que el log decia "no cumplio en 0s (lo mejor visto: 0.0 SOL,
0 compras...)", que se lee como "la moneda era mala" cuando en realidad era
"no se ha mirado". Y en el resto de este YAML el 0 significa "sin limite":
`max_hold_time: 0` es sin reloj, `max_real_buys: 0` es sin tope. Aqui
significaba "descarta todo".
"""

# ruff: noqa: S101

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = RAIZ / "src/trading/universal_trader.py"
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"


def _bloque_del_else() -> str:
    """El `else` del while y lo que hay dentro."""
    texto = FUENTE.read_text(encoding="utf-8")
    inicio = texto.index("while monotonic() - _t0 < self.max_buy_age:")
    fin = texto.index("# Se recoge lo que se mando a buscar al principio", inicio)
    trozo = texto[inicio:fin]
    return trozo[trozo.index("\n                else:") :]


def test_sin_ventana_no_se_rechaza_la_moneda() -> None:
    """LA COMPROBACION QUE IMPORTA. El `return` del `else` tiene que estar
    dentro de una guarda que exija ventana; suelto, rechaza todo."""
    bloque = _bloque_del_else()
    assert "if self.max_buy_age > 0:" in bloque, (
        "el else del while no comprueba que haya ventana: con max_buy_age 0 "
        "vuelve a rechazar todas las monedas"
    )
    # Y el `return` va DESPUES de esa guarda, no antes.
    assert bloque.index("if self.max_buy_age > 0:") < bloque.index("return")


def test_el_aviso_enganoso_tampoco_sale_sin_ventana() -> None:
    """Decia "no cumplio en 0s (lo mejor visto: 0.0 SOL, 0 compras)", que se
    lee como "la moneda era mala" cuando era "no se ha mirado"."""
    bloque = _bloque_del_else()
    aviso = bloque.index("no cumplio en")
    guarda = bloque.index("if self.max_buy_age > 0:")
    assert guarda < aviso


def test_queda_dicho_que_el_else_es_del_while() -> None:
    """Es la trampa del lenguaje que causo el fallo: un `else` pegado a un
    `while` no se lee como un `else` de `if`, pero se comporta distinto."""
    bloque = _bloque_del_else()
    assert "del `while`" in bloque or "del while" in bloque


def test_la_config_de_hoy_va_sin_ventana() -> None:
    """Por eso importa: es exactamente el caso que estaba roto."""
    f = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]
    assert f["max_buy_age"] == 0


def test_los_filtros_del_bucle_estan_apagados() -> None:
    """Saltarse el bucle solo es inofensivo si no hay nada que comprobar
    dentro. Si alguien enciende uno de estos, tiene que poner tambien una
    ventana en la que comprobarlo -- y este test se lo dira."""
    f = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]
    dentro = (
        "min_volumen_sol", "min_buys", "min_presion_ratio", "min_titulares",
        "max_titulares", "min_compras_por_cartera", "max_compras_por_cartera",
        "min_first_buys", "max_first_buys", "min_entry_mc", "min_buy_age",
        "min_vol_ventana_sol",
    )
    if f.get("max_buy_age", 0) > 0:
        return  # con ventana, los filtros se comprueban de verdad
    encendidos = [k for k in dentro if f.get(k)]
    assert not encendidos, (
        f"{encendidos} solo se comprueban dentro de la ventana de espera, y "
        "max_buy_age esta a 0: no se van a aplicar. Poner una ventana."
    )
    assert not (f.get("second_wave") or {}).get("enabled")
