"""Comprar un porcentaje del saldo en vez de una cantidad fija.

Pedido el 19-sep: "pasa a un 25% del portfolio por compra".

LA TRAMPA QUE ESTO EVITA. El tope duro (`tope_compra_sol`) recorta el importe
SIN AVISAR en el log de la compra. Estaba en 0,12 -- el importe fijo anterior --
asi que activar el 25% sin tocarlo habria seguido comprando 0,12 en cuanto el
saldo pasara de 0,48 SOL, y desde fuera habria parecido que el ajuste no hacia
nada. Es el mismo tipo de no-op silencioso que ya paso con `hard_cap` en las
comisiones de prioridad.
"""

# ruff: noqa: S101, PLR2004, PLC0415

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _tamanos(saldo: float, plazas: int | None = None) -> list[float]:
    """Reproduce la cuenta del trader para un saldo dado.

    `free` = saldo - reserva intocable - lo ya comprometido. Cada compra sale
    del 25% de lo que queda LIBRE en ese momento, no del saldo inicial.
    """
    t = _trade()
    libre = saldo - t["min_reserve_sol"]
    salen: list[float] = []
    for _ in range(plazas or t["max_posiciones"]):
        imp = min(libre * t["fraccion_saldo"], t["tope_compra_sol"])
        if imp < t["compra_minima_sol"]:
            break
        salen.append(round(imp, 6))
        libre -= imp * (1 + t["buy_slippage"]) + 0.003
    return salen


def test_la_config_pide_el_25_por_ciento() -> None:
    assert _trade()["fraccion_saldo"] == 0.25


def test_el_tope_no_recorta_el_25_por_ciento_en_saldos_normales() -> None:
    """EL TEST QUE IMPIDE EL NO-OP. Si alguien deja el tope en el importe fijo
    viejo, el 25% deja de aplicarse en cuanto haya algo de saldo."""
    t = _trade()
    for saldo in (0.5, 1.0, 2.0):
        libre = saldo - t["min_reserve_sol"]
        pedido = libre * t["fraccion_saldo"]
        assert pedido <= t["tope_compra_sol"], (
            f"con {saldo} SOL el tope ({t['tope_compra_sol']}) recorta el 25% "
            f"({pedido:.3f}) sin avisar"
        )


def test_las_posiciones_van_menguando() -> None:
    """Cada compra es el 25% de lo que QUEDA, no de lo inicial. Es a proposito:
    la quinta oportunidad no vale lo mismo que la primera."""
    t = _tamanos(1.0)
    assert len(t) == 5
    assert t == sorted(t, reverse=True), f"deberian ir a menos: {t}"
    assert t[0] > t[-1] * 2


def test_nunca_se_gasta_mas_de_lo_que_hay() -> None:
    """Lo que de verdad importa: que quede SOL para vender lo comprado."""
    cfg = _trade()
    for saldo in (0.2, 0.5, 1.0, 2.0, 5.0):
        gastado = sum(_tamanos(saldo))
        assert gastado <= saldo - cfg["min_reserve_sol"], (
            f"con {saldo} SOL se gastarian {gastado:.3f} y hay que dejar "
            f"{cfg['min_reserve_sol']} de reserva"
        )


def test_con_la_cartera_casi_vacia_no_compra_polvo() -> None:
    """El suelo de compra manda sobre el porcentaje.

    Con 0,10 SOL, el 25% de lo libre son 0,0175 -- por debajo del minimo que
    compensa, asi que NO se compra nada. Es correcto (esa compra nacería
    perdiendo), pero hay que saberlo: por debajo de ~0,11 SOL el bot se queda
    quieto, y eso no es un fallo.
    """
    assert _tamanos(0.10) == []
    assert len(_tamanos(0.20)) >= 1


def test_el_umbral_a_partir_del_cual_compra() -> None:
    """Se fija el numero para que sea consultable sin tener que calcularlo."""
    t = _trade()
    umbral = t["min_reserve_sol"] + t["compra_minima_sol"] / t["fraccion_saldo"]
    assert round(umbral, 3) == 0.110


def test_no_queda_ningun_importe_fijo() -> None:
    """Pedido el 19-sep: "elimina lo del 0.12, solo el porcentaje".

    Todos a cero, y el cero es la decision importante: si alguien apaga el
    porcentaje, el importe sale 0, cae por debajo de `compra_minima_sol` y la
    compra se salta con un aviso. El bot se PARA en vez de gastar una cantidad
    vieja que nadie ha pedido. Es el fallo seguro.
    """
    t = _trade()
    assert t["buy_amount"] == 0
    assert t["buy_amount_tras_sl"] == 0
    assert t["mayhem_buy_amount"] == 0
    import yaml
    h = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]["holders"]
    assert h["buy_amount"] == 0
