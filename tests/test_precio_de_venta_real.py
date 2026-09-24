"""El precio que se apunta es el de la VENTA, no el de antes de venderla.

EL FALLO, 24-sep. FUCKETHAN se vendio de verdad a -92% y quedo apuntada en el
registro como -60%. `porque_cerro` lo enseñaba asi, y con el todas las
mediciones: la estrategia parecia bastante mejor de lo que es.

DE DONDE VENIA. La venta por la curva apunta `sell_result.price`, que es lo
que se ejecuto. Pero cuando la curva falla --una moneda que se esta
desplomando, o que ya migro-- se vende por Jupiter, y ESE camino apuntaba
`ref_price`: el ultimo precio VISTO, o sea el de la vuelta anterior del
vigilante. En una venta tranquila los dos se parecen; en un desplome no
tienen nada que ver.

POR QUE ES PEOR QUE UN ERROR NORMAL: se equivoca SIEMPRE A FAVOR y SOLO en
las ventas que peor van, que son justo las unicas que hay que mirar para
decidir si la estrategia funciona. Un error que solo aparece donde no lo
buscas y que siempre te da la razon es la forma mas eficiente de no
enterarse nunca.
"""

# ruff: noqa: S101, PLC0415
# ruff: noqa: S101, PLC0415, PLR2004
import inspect
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")


def test_jupiter_devuelve_los_sol_que_entran() -> None:
    """Sin ese dato, quien llama no PUEDE apuntar el precio real."""
    from trading.jupiter_seller import jupiter_sell

    firma = inspect.signature(jupiter_sell)
    assert "float" in str(firma.return_annotation), (
        "jupiter_sell tiene que devolver tambien los SOL que entran"
    )


def test_la_cotizacion_ilegible_no_inventa_un_cobro() -> None:
    """Cero significa NO SE SABE, no "se cobro cero".

    Si una cotizacion rota devolviera 0 y quien llama lo apuntara tal cual,
    el P&L saldria falseado en la direccion contraria -- una venta buena
    contada como perdida total. Quien llama se queda con el precio de
    referencia cuando esto da cero.
    """
    from trading.jupiter_seller import _sol_de

    assert _sol_de({"outAmount": "1500000000"}) == 1.5
    assert _sol_de({}) == 0.0
    assert _sol_de({"outAmount": "no-es-un-numero"}) == 0.0
    assert _sol_de(None) == 0.0


def test_el_trader_apunta_el_precio_de_jupiter_y_no_el_de_referencia() -> None:
    """EL NUCLEO. Es el cambio que hace que el registro deje de mentir.

    Se comprueba tambien que el respaldo sigue: si Jupiter no dice cuanto
    entro, se usa `ref_price`. Un numero flojo es mejor que ninguno -- lo que
    no vale es presentarlo como el bueno sin mas.
    """
    trozo = FUENTE[FUENTE.index("trying Jupiter fallback"):]
    trozo = trozo[:3000]
    assert "_precio = (_sol_out / amount) if (_sol_out > 0 and amount > 0) else ref_price" in trozo
    assert "self._log_trade(\"sell\", token_info, _precio, amount, sig, reason)" in trozo
    assert "self._ultimo_cobro = amount * _precio" in trozo
    assert "self._log_trade(\"sell\", token_info, ref_price" not in trozo, (
        "quedo el registro viejo, que apunta el precio de ANTES de vender"
    )


def test_la_venta_por_la_curva_ya_apuntaba_el_precio_ejecutado() -> None:
    """LO QUE NO HABIA QUE TOCAR. Ese camino siempre estuvo bien: apunta
    `sell_result.price`. El test esta para que el arreglo del otro camino no
    se lo lleve por delante de rebote."""
    # LA VENTANA SE MIDE HASTA EL FALLBACK, no con un numero de caracteres.
    # Estaba en 1200 y se quedo corta en cuanto se anadio la comision rapida
    # en medio: el test se puso rojo por la longitud del comentario, no por
    # el codigo. Anclarlo a algo del propio programa no caduca al escribir.
    trozo = FUENTE[FUENTE.index("_seller = self._vendedor_para(reason)"):]
    trozo = trozo[: trozo.index("trying Jupiter fallback")]
    assert "sell_result.price," in trozo
    assert "self._ultimo_cobro = sell_result.amount * sell_result.price" in trozo


def test_el_log_dice_a_cuanto_se_vendio_por_jupiter() -> None:
    """Antes la linea de Jupiter decia solo la firma. Sin el precio, la unica
    forma de saber a cuanto se vendio era abrir la transaccion en un
    explorador -- desde el movil, a las cinco de la manana."""
    assert "Sold %s via Jupiter (%s) @ %.8f SOL tx=%s" in FUENTE
