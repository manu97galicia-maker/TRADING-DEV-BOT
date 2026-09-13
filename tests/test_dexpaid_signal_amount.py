"""Cuanto se compra segun lo que la moneda haya pagado en DexScreener.

La base sale de un tramo de boosts, como fraccion del importe normal:

    hasta 50 boosts (y el pago sin boost) ....... un cuarto
    de 51 a 100 ................................ la mitad
    mas de 100 ................................. dos tercios

Y pagar el perfil Y ADEMAS boosts sube un 20% sobre el tramo que toque.
Encima de esta base multiplican despues el factor de origen (2/3 fuera de
pump.fun) y el premio por 30 boosts.
"""

# ruff: noqa: S101, PLR2004

from monitoring.dexpaid_source import (
    FACTOR_PAGO_Y_BOOST,
    fraccion_por_tramo,
    importe_por_senal,
)

NORMAL = 0.2856


def test_un_pago_sin_boost_va_en_el_primer_tramo() -> None:
    """Es la senal mas floja que se compra: no puede pagar mas que una con
    boosts."""
    assert fraccion_por_tramo(0) == 0.25


def test_hasta_cincuenta_boosts_es_un_cuarto() -> None:
    assert fraccion_por_tramo(1) == 0.25
    assert fraccion_por_tramo(50) == 0.25


def test_de_cincuenta_y_uno_a_cien_es_la_mitad() -> None:
    assert fraccion_por_tramo(51) == 0.50
    assert fraccion_por_tramo(100) == 0.50


def test_por_encima_de_cien_son_dos_tercios() -> None:
    assert round(fraccion_por_tramo(101), 4) == 0.6667
    assert round(fraccion_por_tramo(5000), 4) == 0.6667


def test_los_tramos_nunca_bajan_al_subir_los_boosts() -> None:
    """Pagar mas no puede hacer que se invierta menos."""
    valores = [fraccion_por_tramo(b) for b in (0, 10, 50, 51, 100, 101, 999)]
    assert valores == sorted(valores)


def test_el_importe_es_la_fraccion_del_normal() -> None:
    assert round(importe_por_senal(0, NORMAL), 4) == round(NORMAL * 0.25, 4)
    assert round(importe_por_senal(75, NORMAL), 4) == round(NORMAL * 0.50, 4)


def test_pagar_perfil_y_ademas_boosts_sube_un_veinte_por_ciento() -> None:
    """Son dos gastos distintos: cuenta mas que cualquiera por separado."""
    sin = importe_por_senal(75, NORMAL)
    con = importe_por_senal(75, NORMAL, pago_y_boost=True)
    assert round(con / sin, 4) == FACTOR_PAGO_Y_BOOST


def test_nunca_se_compra_mas_que_el_importe_normal_por_esta_via() -> None:
    """El tramo mas alto con premio sigue por debajo del normal entero: el
    tope de verdad lo pone tope_compra_sol, pero esto ya no se pasa."""
    maximo = importe_por_senal(99999, NORMAL, pago_y_boost=True)
    assert maximo <= NORMAL
