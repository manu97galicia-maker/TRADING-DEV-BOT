"""La comisión de prioridad: la de comprar, la de vender y el tope.

POR QUE HAY DOS. Una posición se compra UNA vez y se vende hasta DIEZ, una por
peldaño de la escalera. Poner la comisión rápida en las dos significa pagarla
once veces: con 0,001 SOL serían ~0,0084 SOL solo en ventas, el 7% de una
posición de 0,12 SOL. Comprar tarde cuesta mucho más que vender un peldaño
tarde, así que la prisa se paga donde hace falta.

EL NO-OP SILENCIOSO QUE FIJA EL PRIMER TEST. `hard_cap` valía lo mismo que
`fixed_amount`, así que subir la comisión no hacía absolutamente nada: el
cálculo la recortaba de vuelta y dejaba un `logger.warning` enterrado como
única pista. Es exactamente la clase de cambio que parece hecho y no lo está.
"""

# ruff: noqa: S101, PLR2004

import asyncio
from pathlib import Path

import yaml

from core.priority_fee.manager import PriorityFeeManager

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _comision(fixed: int, tope: int) -> int | None:
    """Lo que acaba pagando el camino normal (el de las ventas)."""
    gestor = PriorityFeeManager(
        client=None, enable_dynamic_fee=False, enable_fixed_fee=True,
        fixed_fee=fixed, extra_fee=0.0, hard_cap=tope,
    )
    return asyncio.run(gestor.calculate_priority_fee(None))


def test_el_tope_recorta_y_por_eso_hay_que_subirlo_tambien() -> None:
    """Subir la comisión sin subir el tope NO cambia nada."""
    assert _comision(7_000_000, 1_000_000) == 1_000_000
    assert _comision(7_000_000, 10_000_000) == 7_000_000


def test_la_config_no_tiene_el_tope_pegado_a_la_comision() -> None:
    """Si vuelven a quedar iguales, cualquier subida futura sería un no-op."""
    pf = _cfg()["priority_fees"]
    assert pf["hard_cap"] > pf["fixed_amount"]
    assert pf["hard_cap"] >= pf["fixed_amount_compra"]


# ------------------------------------------------- lo que cuesta de verdad


def _sol(fixed: int, unidades: int) -> float:
    """Microlamports por unidad de cómputo -> SOL.

    La unidad NO es SOL: el coste depende de las CU de cada operación. El
    comentario viejo del YAML decía `fixed_amount / 1e10`, que da el número
    aproximado pero esconde de qué depende.
    """
    return fixed * unidades / 1e15


def test_comprar_cuesta_lo_pedido() -> None:
    """~0,001 SOL, pedido por Manu el 19-sep."""
    c = _cfg()
    coste = _sol(c["priority_fees"]["fixed_amount_compra"], c["compute_units"]["buy"])
    assert 0.0008 < coste < 0.0015, f"la compra cuesta {coste:.5f} SOL"


def test_lo_que_se_va_en_comisiones_por_operacion() -> None:
    """CUANTO SE LLEVAN LAS COMISIONES DE PRIORIDAD, en tanto por uno de la
    posicion. No es un umbral inventado: es el numero que hay que mirar al
    cambiar el tamano de entrada o la comision, porque los dos lo mueven.

    Con la entrada en 0,04 SOL (19-sep):

        comprar                     0,00098 SOL = 2,45% de la posicion
        cada venta                  0,00012 SOL = 0,30%
        ida y vuelta (1 venta)      0,00110 SOL = 2,75%
        con la escalera entera      0,00218 SOL = 5,45%

    A 0,12 SOL la escalera entera costaba el 1,82%: bajar la entrada a un
    tercio TRIPLICA el peso de las comisiones, porque son fijas.

    EN LA PRACTICA seran 2 o 3 ventas, no 10: con el colchon del stop en 6,7%
    lo normal es cobrar un par de escalones y salir. O sea ~3%.

    El tope de este test (8%) no es un objetivo, es una alarma: si se cruza,
    la operacion nace perdiendo demasiado y hay que bajar la comision de
    compra o subir la entrada.
    """
    c = _cfg()
    compra = _sol(c["priority_fees"]["fixed_amount_compra"], c["compute_units"]["buy"])
    venta = _sol(c["priority_fees"]["fixed_amount"], c["compute_units"]["sell"])
    peldanos = 3 + len(c["trade"]["tp_extra"]) + 3
    _t = c["trade"]
    _frac = _t["fraccion_saldo"]
    posicion = (
        (1.0 - _t["min_reserve_sol"]) * _frac if _frac > 0 else _t["buy_amount"]
    )
    assert posicion > 0

    peor = (compra + venta * peldanos) / posicion
    assert peor < 0.08, (
        f"ida y vuelta con la escalera entera se lleva el {peor * 100:.1f}% "
        f"de una posicion de {posicion} SOL"
    )
    # Y el caso normal, que es el que de verdad se repite.
    normal = (compra + venta * 3) / posicion
    assert normal < 0.05, f"una operacion tipica se deja el {normal * 100:.1f}%"


def test_vender_es_mas_barato_que_comprar() -> None:
    pf = _cfg()["priority_fees"]
    assert pf["fixed_amount"] < pf["fixed_amount_compra"]
