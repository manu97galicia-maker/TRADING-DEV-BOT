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
    ]
    return [(g, v) for g, v in pares if g > 0 and v > 0]


def test_los_peldanos_son_los_pedidos() -> None:
    assert _peldanos() == [(0.30, 0.55), (0.60, 0.20), (0.90, 0.05)]


def test_las_ganancias_van_en_orden_creciente() -> None:
    """Un peldano por debajo del anterior no llegaria a dispararse nunca."""
    ganancias = [g for g, _ in _peldanos()]
    assert ganancias == sorted(ganancias)


def test_nunca_se_vende_mas_del_100() -> None:
    """Vender mas de lo que se tiene es una orden imposible."""
    assert sum(v for _, v in _peldanos()) <= 1.0


def test_queda_moonbag_corriendo() -> None:
    """Sin moonbag la ganancia tiene techo, haga lo que haga la moneda."""
    vendido = sum(v for _, v in _peldanos())
    assert round(1 - vendido, 6) == 0.20


def test_el_stop_sube_a_la_entrada_tras_el_primer_tp() -> None:
    """Es lo que hace que despues del primer TP ya no se pueda perder."""
    assert _trade()["stop_a_entrada_tras_tp"] is True


def test_el_moonbag_tiene_fecha_de_caducidad() -> None:
    """No corre para siempre: a las 2h se cierra pase lo que pase."""
    assert _trade()["max_hold_time"] == 7200


def test_el_ganador_minimo_no_llega_a_cubrir_al_perdedor() -> None:
    """Aviso permanente: con +16,5% contra -18%, hay que acertar mas de la
    mitad de las veces solo para empatar."""
    t = _trade()
    g, v = _peldanos()[0]
    assert g * v < t["stop_loss_percentage"]
