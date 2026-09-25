"""La fuente de la curva no puede anunciar un importe que no decide.

EL FALLO QUE VIGILA, y costo un susto el 25-sep. La linea del log decia:

    CURVA AL 71.5% — ? (274aQvp...pump): 33.6 SOL en la curva,
    se compra con 0.0000 SOL

Un importe a cero parece la explicacion entera de por que el bot no compra. No
lo era: `curva_96.buy_amount` esta a 0 A PROPOSITO, porque quien decide el
tamano es `fraccion_saldo` en el trader --el 50% de la cartera-- y lo
recalcula desde el saldo recien leido, bajo el cerrojo de gasto. La fuente
imprimia su propio numero, que no es el que se usa.

ES EL MISMO FALLO que ya se corrigio en la linea "TRAS SL: compra reducida
0,0700 SOL" del trader, y por la misma razon, que esta escrita alli: un log
que miente es peor que no tenerlo, porque manda a buscar el fallo donde no
esta. Que haya reaparecido en otro fichero es el motivo de que ahora tenga
test propio en vez de un comentario.

LO QUE SE FIJA: que cuando la fuente no decide el importe, no se imprima
ninguna cifra de SOL como si fuera la compra.
"""

# ruff: noqa: S101, SLF001, ANN003, PLC0415

import logging
from types import SimpleNamespace

from monitoring.curva96_source import Curva96Source


def _fuente(**extra) -> Curva96Source:
    opciones = {"umbral": 0.71, "buy_amount": 0.0, "min_compradores": 0}
    opciones.update(extra)
    return Curva96Source("wss://x", SimpleNamespace(), SimpleNamespace(), **opciones)


def _linea(fuente: Curva96Source, sol_curva: float, progreso: float) -> str:
    """Reproduce la decision de formato de `_evaluar`, sin red ni wallet.

    Se copia el `if` en vez de llamar a `_evaluar` porque esa funcion necesita
    construir un TokenInfo contra la cadena. Lo que este test protege es la
    DECISION de que imprimir, y `test_la_fuente_usa_esta_misma_decision` ata
    esta copia al codigo de verdad.
    """
    if fuente.buy_amount > 0:
        importe = f"se compra con {fuente.buy_amount:.4f} SOL"
    else:
        importe = "el importe lo decide el trader (% del saldo)"
    return f"CURVA AL {progreso * 100:.1f}% — X (m): {sol_curva:.1f} SOL en la curva, {importe}"


def test_sin_importe_propio_no_se_anuncia_ninguna_cifra_de_compra() -> None:
    """Con `buy_amount: 0` la linea dice quien decide, no un cero enganoso."""
    linea = _linea(_fuente(buy_amount=0.0), 33.6, 0.715)
    assert "0.0000 SOL" not in linea, f"vuelve a anunciar un importe a cero: {linea}"
    assert "se compra con" not in linea, (
        f"con buy_amount a 0 la fuente no compra con nada: {linea}"
    )
    assert "lo decide el trader" in linea, (
        f"la linea tiene que decir quien decide de verdad el importe: {linea}"
    )
    # Y el SOL de la curva SI se sigue viendo: es el dato util de la linea.
    assert "33.6 SOL en la curva" in linea


def test_con_importe_propio_si_se_anuncia() -> None:
    """Si alguien pone un importe fijo en la fuente, ese SI manda y se dice.

    Esta rama existe porque otras fuentes (Telegram) si traen su propio
    importe, y la linea tiene que seguir sirviendo para ellas.
    """
    linea = _linea(_fuente(buy_amount=0.07), 33.6, 0.715)
    assert "se compra con 0.0700 SOL" in linea


def test_la_fuente_usa_esta_misma_decision() -> None:
    """Ata la copia de arriba al codigo real, leyendo el fuente.

    Sin esto, alguien podria cambiar `curva96_source.py` y dejar este fichero
    verde comprobando una copia que ya no se parece al original -- que es
    justo la clase de test que no sirve para nada.
    """
    import inspect

    codigo = inspect.getsource(Curva96Source._evaluar)
    assert "el importe lo decide el trader" in codigo, (
        "la fuente ha dejado de explicar quien decide el importe"
    )
    assert "if self.buy_amount > 0:" in codigo, (
        "la fuente ya no distingue entre tener importe propio y no tenerlo"
    )
    # Y que no haya vuelto a colar `self.buy_amount` sin condicion en el log.
    assert "se compra con %.4f SOL" not in codigo, (
        "ha vuelto el formato que imprimia el importe pasara lo que pasara"
    )


def test_el_importe_que_viaja_en_la_senal_sigue_siendo_el_de_la_fuente() -> None:
    """Cambiar el LOG no puede cambiar lo que se manda al trader.

    `signal_buy_amount` tiene que seguir llevando el `buy_amount` de la
    fuente, 0 incluido: es ese 0 el que hace que el trader caiga en
    `fraccion_saldo`. Si alguien "arreglara" el cero poniendo otro numero
    aqui, el 50% de la cartera dejaria de aplicarse sin que nada lo dijera.
    """
    import inspect

    codigo = inspect.getsource(Curva96Source._evaluar)
    assert '"signal_buy_amount": self.buy_amount,' in codigo, (
        "la senal ya no lleva el importe de la fuente: revisar que el "
        "porcentaje del saldo siga mandando"
    )


def test_el_log_de_la_fuente_sale_por_info() -> None:
    """Es la linea que se lee con `journalctl`, asi que no puede ser debug."""
    import inspect

    codigo = inspect.getsource(Curva96Source._evaluar)
    assert "logger.info(" in codigo
    assert "CURVA AL %.1f%%" in codigo
    assert logging.INFO < logging.WARNING
