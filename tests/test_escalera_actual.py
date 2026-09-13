"""La escalera de ventas configurada, fijada para que no cambie por descuido.

Los numeros van a pelo a proposito: quien los toque ve el test en rojo y
tiene que mirar que ganancia gana o pierde antes de seguir.
"""

# ruff: noqa: S101, PLR2004

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _peldanos() -> list[tuple[float, float]]:
    t = _trade()
    pares = [
        (t["take_profit_percentage"], t["take_profit_sell_percentage"]),
        (t["tp2_gain"], t["tp2_sell"]),
        (t["tp3_gain"], t["tp3_sell"]),
        (t["tp4_gain"], t["tp4_sell"]),
        (t["tp5_gain"], t["tp5_sell"]),
        (t["tp6_gain"], t["tp6_sell"]),
        *((e["gain"], e["sell"]) for e in t.get("tp_extra") or []),
    ]
    return [(g, v) for g, v in pares if g > 0 and v > 0]


def test_los_peldanos_son_los_pedidos() -> None:
    assert _peldanos() == [
        (0.30, 0.30),
        (0.50, 0.10),
        (0.70, 0.35),
        (2.30, 0.06),  # 3,3x
        (4.00, 0.06),  # 5x
        (6.00, 0.06),  # 7x
        (9.00, 0.015),  # x10
        (19.00, 0.015),  # x20
        (49.00, 0.010),  # x50
        (99.00, 0.010),  # x100
    ]


def test_las_ganancias_van_en_orden_creciente() -> None:
    """Un peldano por debajo del anterior no llegaria a dispararse nunca."""
    ganancias = [g for g, _ in _peldanos()]
    assert ganancias == sorted(ganancias)


def test_nunca_se_vende_mas_del_100() -> None:
    """Vender mas de lo que se tiene es una orden imposible."""
    assert sum(v for _, v in _peldanos()) <= 1.0


def test_queda_moonbag_corriendo() -> None:
    """Ese 2% es el que Manu vende a mano: el bot no lo toca."""
    vendido = sum(v for _, v in _peldanos())
    assert round(1 - vendido, 6) == 0.02


def test_el_stop_sube_a_la_entrada_tras_el_primer_tp() -> None:
    """Es lo que hace que despues del primer TP ya no se pueda perder."""
    assert _trade()["stop_a_entrada_tras_tp"] is True


def test_no_se_cierra_ninguna_posicion_por_reloj() -> None:
    """0 = sin reloj. Con 7200 los peldanos de x10 a x100 no se disparaban
    nunca: el reloj vendia antes de que la moneda llegara."""
    assert _trade()["max_hold_time"] == 0


def test_los_peldanos_lejanos_cobran_una_salida_de_verdad() -> None:
    """Los de arriba son los que cobran cuando una sale de verdad."""
    lejanos = [(g, v) for g, v in _peldanos() if g >= 2.0]
    assert len(lejanos) == 7
    assert round(sum(v for _, v in lejanos), 6) == 0.23


def test_los_peldanos_altos_tienen_tiempo_de_llegar() -> None:
    """Si vuelve a ponerse un reloj corto, los peldanos de x10 a x100 dejan
    de tener sentido y este test avisa."""
    t = _trade()
    hay_altos = any(g >= 9.0 for g, _ in _peldanos())
    assert hay_altos
    assert t["max_hold_time"] == 0 or t["max_hold_time"] >= 43200


def test_el_abandono_no_depende_del_reloj_de_posicion() -> None:
    """La trampa que casi cuela: `abandon_after` salia de
    `(max_hold_time or 120) * 3 + 120`, asi que poner el reloj a 0 para
    dejar correr las posiciones hacia que el bot ABANDONASE cada una a los
    8 minutos, dejando la bolsa huerfana en la wallet.
    """
    t = _trade()
    assert t["abandonar_tras_seg"] >= 3600
    # Y con la formula vieja habria dado esto:
    assert (t["max_hold_time"] or 120) * 3 + 120 < t["abandonar_tras_seg"]


def test_el_ganador_minimo_no_llega_a_cubrir_al_perdedor() -> None:
    """Aviso permanente: el primer peldano cobra +9% de la posicion (el 30%
    de un +30%) y el stop cuesta -18%. Un ganador que solo llega al primer
    peldano NO cubre a un perdedor, asi que hay que acertar bastante mas de
    la mitad de las veces solo para empatar.

    Esto no es un fallo: el reparto esta echado adelante a proposito y la
    ganancia de verdad esta en los peldanos de +70% para arriba. El test
    esta para que nadie lo de por supuesto sin verlo escrito.
    """
    t = _trade()
    g, v = _peldanos()[0]
    assert g * v < t["stop_loss_percentage"]


def test_los_peldanos_de_cerca_son_los_pedidos() -> None:
    """30% al +30%, 10% al +50%, 35% al +70%: tres cuartos de la posicion."""
    cerca = [(g, v) for g, v in _peldanos() if g < 2.0]
    assert cerca == [(0.30, 0.30), (0.50, 0.10), (0.70, 0.35)]
    assert round(sum(v for _, v in cerca), 6) == 0.75
