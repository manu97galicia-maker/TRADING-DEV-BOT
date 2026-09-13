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
        (0.30, 0.50),
        (0.60, 0.20),
        (0.90, 0.05),
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


def test_el_moonbag_tiene_fecha_de_caducidad() -> None:
    """No corre para siempre: a las 2h se cierra pase lo que pase.

    Por eso los peldanos lejanos (3,3x/5x/7x) valen mas que dejar ese 18%
    en el moonbag: cobran cuando pasan, no cuando vence el reloj.
    """
    assert _trade()["max_hold_time"] == 7200


def test_los_peldanos_lejanos_cobran_una_salida_de_verdad() -> None:
    """Los de arriba son los que cobran cuando una sale de verdad."""
    lejanos = [(g, v) for g, v in _peldanos() if g >= 2.0]
    assert len(lejanos) == 7
    assert round(sum(v for _, v in lejanos), 6) == 0.23


def test_el_reloj_de_2h_hace_inalcanzables_los_peldanos_altos() -> None:
    """Aviso permanente, no un fallo: con max_hold_time en 7200 una moneda
    no da tiempo a hacer x10, asi que esos peldanos casi nunca cobran.

    Si algun dia se sube el reloj, este test hay que revisarlo -- que es
    justo el momento en que conviene volver a mirar la escalera.
    """
    assert _trade()["max_hold_time"] <= 7200
    assert any(g >= 9.0 for g, _ in _peldanos())


def test_el_ganador_minimo_no_llega_a_cubrir_al_perdedor() -> None:
    """Aviso permanente: con +16,5% contra -18%, hay que acertar mas de la
    mitad de las veces solo para empatar."""
    t = _trade()
    g, v = _peldanos()[0]
    assert g * v < t["stop_loss_percentage"]
