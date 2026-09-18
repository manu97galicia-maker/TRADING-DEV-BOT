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


def importe(libre: float, t: dict | None = None) -> float:
    """El importe de UNA compra con ese saldo libre, como lo calcula el bot.

    Copia la logica, incluidas las dos guardas que importan: el tope y el suelo
    solo se aplican si son MAYORES QUE CERO. Cero significa "sin limite" en
    toda esta configuracion, y escribir `min(x, tope)` a secas daria 0.
    """
    t = t or _trade()
    imp = max(0.0, libre * t["fraccion_saldo"])
    tope = t["tope_compra_sol"]
    if tope > 0 and imp > tope:
        imp = tope
    return imp


def _tamanos(saldo: float, plazas: int | None = None) -> list[float]:
    """Reproduce la cuenta del trader para un saldo dado.

    `free` = saldo - reserva intocable - lo ya comprometido. Cada compra sale
    del 25% de lo que queda LIBRE en ese momento, no del saldo inicial.
    """
    t = _trade()
    libre = saldo - t["min_reserve_sol"]
    salen: list[float] = []
    for _ in range(plazas or t["max_posiciones"]):
        imp = importe(libre, t)
        suelo = t["compra_minima_sol"]
        if imp <= 0 or (suelo > 0 and imp < suelo):
            break
        salen.append(round(imp, 6))
        libre -= imp * (1 + t["buy_slippage"]) + 0.003
    return salen


def test_la_config_pide_el_25_por_ciento() -> None:
    assert _trade()["fraccion_saldo"] == 0.25


def test_el_tope_no_recorta_el_25_por_ciento() -> None:
    """EL TEST QUE IMPIDE EL NO-OP.

    El tope recorta SIN dejar rastro en el log de la compra, asi que si alguien
    lo deja por debajo del 25% el porcentaje deja de aplicarse y desde fuera
    parece que el ajuste no hace nada. Hoy esta a 0 (sin tope) y el test vale
    igual para el dia que se vuelva a poner.
    """
    t = _trade()
    for saldo in (0.5, 1.0, 2.0, 10.0):
        libre = saldo - t["min_reserve_sol"]
        pedido = libre * t["fraccion_saldo"]
        assert abs(importe(libre, t) - pedido) < 1e-12, (
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


def test_sin_suelo_compra_con_lo_que_haya() -> None:
    """EL SUELO SE QUITO el 19-sep, pedido. Antes, por debajo de ~0,11 SOL el
    bot se quedaba quieto; ahora compra igual, mas pequeno.

    LO QUE CUESTA: la comision de prioridad y la renta de la cuenta de token no
    bajan con el tamano. Una compra de 0,005 SOL se deja una parte enorme en
    entrar y salir. El test no lo impide --es lo pedido-- pero lo deja escrito.
    """
    assert _trade()["compra_minima_sol"] == 0
    assert len(_tamanos(0.10)) >= 1, "con el suelo a 0 tiene que comprar igual"
    assert len(_tamanos(0.05)) >= 1


def test_la_reserva_sigue_siendo_intocable() -> None:
    """Lo unico que queda protegiendo: sin esto no habria gas para VENDER lo
    comprado, y una posicion sin salida vale cero."""
    t = _trade()
    assert t["min_reserve_sol"] > 0
    # Con un saldo igual a la reserva no se compra nada.
    assert _tamanos(t["min_reserve_sol"]) == []


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
