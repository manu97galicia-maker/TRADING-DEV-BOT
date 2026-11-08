"""8-nov: "deja solo a los grandes dev y mete presion de proyectos que se van
a disparar comprando de 5-12k de mc" -> "27 devs + presion aparte" con "la
presion de ahora"."""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN201, ANN202

from pathlib import Path

import test_tp_sl_en_vivo as tv
import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

from monitoring import curva96_source as cs

RAIZ = Path(__file__).resolve().parents[1]
YAML = "bots/bot-real-presion512.yaml"


def _cfg():
    return yaml.safe_load((RAIZ / YAML).read_text(encoding="utf-8"))


def test_config_presion512():
    c = _cfg()
    cv, fl, t = c["filters"]["curva_96"], c["filters"], c["trade"]
    assert fl["paper_trade"] is False and cv["enabled"] is True
    assert cv["umbral"] == 0.0 and cv["min_mc_usd"] == 5000
    assert cv["max_mc_usd"] == 12000
    # "la presion de ahora" (la de la curva) y organica.
    assert cv["min_compradores_ola"] == 3 and cv["min_presion_ratio"] == 1.4
    assert cv["ventana_presion"] == 7.0 and cv["ultimas_compras"] == 2
    assert cv["min_volumen_reciente_sol"] == 3.0 and cv["min_compradores"] == 45
    # Frenos anti-rug.
    assert fl["max_dev_hold_pct"] == 0.10 and fl["allow_mayhem"] is False
    assert cv["max_carteras_primer_bloque"] == 5 and cv["max_subida_vela2"] == 0.60
    # 70%, todo a +45%, stop -22% (no el de 7.400 $).
    assert t["fraccion_saldo"] == 0.70 and t["fraccion_base"] == "cartera"
    assert t["take_profit_percentage"] == 0.45
    assert t["take_profit_sell_percentage"] == 1.0
    assert t["stop_loss_percentage"] == 0.22 and t["mc_stop_loss"] == 0
    assert t["max_posiciones"] == 2 and t["max_posiciones_total"] == 2
    assert t["nombre_estrategia"] == "real-presion512"


def test_solo_entre_5k_y_12k(monkeypatch):
    cv = _cfg()["filters"]["curva_96"]
    f = cs.Curva96Source(
        "ws://x",
        None,
        None,
        min_mc_usd=cv["min_mc_usd"],
        max_mc_usd=cv["max_mc_usd"],
        sol_usd=lambda: 150.0,
    )
    for mc, ok in ((4000, False), (5100, True), (11900, True), (13000, False)):
        monkeypatch.setattr(
            cs.volumen, "precio_en_vivo", lambda _m, _e, mc=mc: mc / 150 / 1e9
        )
        assert f._llega_al_mc("M") is ok, mc


def test_mira_desde_el_principio_de_la_curva(monkeypatch):
    cv = _cfg()["filters"]["curva_96"]
    f = cs.Curva96Source("ws://x", None, None, umbral=cv["umbral"], tope=cv["tope"])
    lanzadas = []
    monkeypatch.setattr(f, "_lanzar", lambda m, _cb: lanzadas.append(m))
    avances = {"A": 0.0, "B": 0.12, "C": 0.6}
    monkeypatch.setattr(cs.volumen, "progreso_curva", lambda m: (avances[m], 0.0))
    f._revisar(list(avances), None)
    assert lanzadas == ["A", "B", "C"]


def test_vende_al_45_o_stop_a_menos_22(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "MC_ENTRADA", 6000.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.2, 1.46, 2.0])
    assert [v[0] for v in ventas] == ["tp_tier1_+45%"]
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.0, 0.9, 0.77])
    assert ventas and ventas[0][0] == "stop_loss" and ventas[0][2] == 0.77
