"""Una moneda migrada no vuelve a preguntarle a su curva.

Lo que pasaba el 13-sep en el droplet: una posicion abierta en una moneda ya
migrada a PumpSwap. El monitor pide el precio CADA SEGUNDO, y cada vez
intentaba leer la curva primero. La curva de una migrada tiene las reservas a
cero, asi que `curve_manager` soltaba un traceback completo como ERROR y
lanzaba; el monitor lo cazaba y sacaba el precio del DEX, que es lo correcto.

Funcionaba, pero costaba dos cosas por segundo Y POR POSICION migrada:
  - unas diez lineas de traceback en el journal, en un droplet de 1 GB
  - una llamada al nodo condenada a fallar, del presupuesto de 15 por segundo
    que necesita el stop loss para reaccionar a tiempo

Con cinco posiciones migradas eran cinco de esas quince llamadas tiradas a la
basura. Ahora se apunta que migro y el precio se pide al DEX directamente.
"""

# ruff: noqa: S101

import re
from pathlib import Path

FUENTE = Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"


def _monitor() -> str:
    """El cuerpo del bucle que vigila el precio de una posicion."""
    texto = FUENTE.read_text(encoding="utf-8")
    inicio = texto.index("while remaining > 1e-9:")
    return texto[inicio : inicio + 4000]


def test_la_bandera_arranca_de_la_compra() -> None:
    """Comprada ya migrada (por el pool), no se pregunta ni la primera vez."""
    texto = FUENTE.read_text(encoding="utf-8")
    assert re.search(
        r'_ya_migro = bool\(\(token_info\.additional_data or \{\}\)\.get\("migrated"\)\)',
        texto,
    ), "la bandera de migrada no se inicializa desde la compra"


def test_no_se_pide_la_curva_cuando_ya_se_sabe_que_migro() -> None:
    """El corte tiene que ir ANTES de pedir el precio, sea a quien sea.

    La lectura del precio pasa ahora por `_precio_rapido`, que mira primero
    el stream y solo llama a la curva cuando toca. Da igual: lo que este test
    defiende es el ORDEN. Si el corte por migrada quedara detras, se seguiria
    preguntando por una curva vacia una vez por segundo y por posicion, que
    es lo que llenaba el disco del droplet de tracebacks.
    """
    cuerpo = _monitor()
    salto = cuerpo.index("if _ya_migro:")
    precio = cuerpo.index("await self._precio_rapido(")
    assert salto < precio, (
        "la lectura del precio va ANTES del corte: se sigue preguntando a una "
        "curva vacia una vez por segundo"
    )


def test_quien_habla_con_la_curva_es_precio_rapido() -> None:
    """Y la curva se pide en UN solo sitio, para que el corte de arriba baste.

    Si alguien anadiera otra llamada suelta a `calculate_price` dentro del
    bucle, el corte por migrada dejaria de protegerla sin que nadie lo note.
    """
    texto = FUENTE.read_text(encoding="utf-8")
    inicio = texto.index("while remaining > 1e-9:")
    cuerpo = texto[inicio:]
    fin = cuerpo.index("# Marca para la proxima compra")
    assert "curve_manager.calculate_price" not in cuerpo[:fin], (
        "hay una llamada a la curva suelta dentro del bucle: deberia pasar "
        "por _precio_rapido"
    )


def test_la_migracion_se_apunta_una_sola_vez() -> None:
    """Sin apuntarlo, cada vuelta volveria a intentar la curva."""
    cuerpo = _monitor()
    assert "_ya_migro = True" in cuerpo
    assert "if not _ya_migro:" in cuerpo


def test_el_desvio_no_se_confunde_con_un_fallo_de_red() -> None:
    """Se usa una excepcion propia, no una generica: si algun dia alguien
    afina el `except`, esto no puede colarse como un error del nodo."""
    texto = FUENTE.read_text(encoding="utf-8")
    assert "class _CurvaMigrada(Exception)" in texto
    assert "raise _CurvaMigrada" in _monitor()


def test_el_aviso_de_migracion_sale_una_vez_no_una_por_segundo() -> None:
    """El segundo fallo, que sobrevivio al primer arreglo.

    Quitar el traceback no bastaba: quedaba una linea de INFO por segundo.
    Su condicion era `_sin_precio == 0`, y `_sin_precio` se pone a cero justo
    despues y solo sube cuando NO hay precio -- en una migrada con precio en
    el DEX valia SIEMPRE cero. O sea 3.600 lineas por hora y por posicion.

    Ahora el aviso cuelga de `_ya_migro`, que por definicion solo pasa de
    falso a cierto una vez en la vida de la posicion.
    """
    cuerpo = _monitor()
    migro = cuerpo.index("la curva ya no sirve")
    guarda = cuerpo.rindex("if not _ya_migro:", 0, migro)
    # Entre la guarda y el aviso no puede colarse otra condicion: el aviso
    # tiene que estar DENTRO del `if not _ya_migro`.
    entre = cuerpo[guarda:migro]
    assert "if _sin_precio == 0:" not in entre, (
        "el aviso volvio a colgar de _sin_precio: saldra una vez por segundo"
    )
    assert "_ya_migro = True" in entre


def test_el_precio_del_dex_sigue_alimentando_el_stop() -> None:
    """Lo que NO puede pasar: que saltarse la curva deje la posicion sin
    precio. El respaldo del DEX tiene que seguir en la misma rama."""
    cuerpo = _monitor()
    assert "price = await self._precio_dex(str(token_info.mint))" in cuerpo
