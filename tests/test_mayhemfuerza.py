"""8-nov: "hazme una estrategia sin devs" (mayhem arriesgada) -> "arranque
con fuerza" y "5 a la vez (0,20 SOL)"."""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN002, ANN003, ANN201, ANN202

import asyncio
from pathlib import Path
from types import SimpleNamespace

import test_tp_sl_en_vivo as tv
import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

from monitoring import curva96_source as cs

RAIZ = Path(__file__).resolve().parents[1]
YAML = "bots/bot-real-mayhemfuerza.yaml"


def _cfg():
    return yaml.safe_load((RAIZ / YAML).read_text(encoding="utf-8"))


def test_config():
    c = _cfg()
    t, fl, cv = c["trade"], c["filters"], c["filters"]["curva_96"]
    assert fl["paper_trade"] is False and cv["enabled"] is True
    assert fl["require_mayhem"] is True and cv["solo_mayhem"] is True
    assert not fl.get("bro_address")  # sin devs
    # Arranque con fuerza.
    assert cv["umbral"] == 0.0 and cv["min_mc_usd"] == 0
    assert cv["max_mc_usd"] == 10000
    assert cv["min_compradores_ola"] == 2 and cv["min_presion_ratio"] == 1.2
    assert cv["ventana_presion"] == 7.0 and cv["ultimas_compras"] == 2
    assert cv["min_compradores"] == 15 and cv["min_volumen_reciente_sol"] == 2.0
    assert fl["max_dev_hold_pct"] == 0.10
    # Venta y tamano de la mayhem 400-2000%.
    assert t["buy_amount"] == 0.04 and t["tope_compra_sol"] == 0.04
    assert t["fraccion_saldo"] == 0
    # 9-nov: +120% recupera lo invertido; el resto a x3, x5 y x7; SL a +20%.
    gan = [t["take_profit_percentage"], t["tp2_gain"], t["tp3_gain"], t["tp4_gain"]]
    assert gan == [1.2, 2.0, 4.0, 6.0]
    assert abs(t["take_profit_sell_percentage"] * 2.2 - 1) < 1e-6
    resto = [t["tp2_sell"], t["tp3_sell"], t["tp4_sell"]]
    assert max(resto) - min(resto) < 1e-9
    assert abs(t["take_profit_sell_percentage"] + sum(resto) - 1) < 1e-9
    assert t["stop_a_entrada_tras_tp"] is True and t["margen_stop_tras_tp"] == 0.20
    assert t["stop_loss_percentage"] == 0.70 and t["trailing_stop_percentage"] == 0
    assert t["venta_muerta_seg"] == 0 and t["vender_antes_de_edad_seg"] == 82800
    assert t["max_posiciones"] == 5 and t["max_posiciones_total"] == 0
    assert t["max_perdidas_seguidas"] == 0  # 9-nov: "quita" el freno


def test_la_que_no_es_mayhem_se_descarta_para_siempre(monkeypatch):
    f = cs.Curva96Source(
        "ws://x",
        None,
        None,
        umbral=0.0,
        solo_mayhem=True,
        exigir_ver_creacion=False,
        min_compradores=0,
    )
    lanzadas = []

    async def _cb(ti):
        lanzadas.append(ti)

    for nombre, valor in (
        ("_demasiado_joven", lambda _m: False),
        ("_sin_mecha_vertical", lambda *_a: True),
        ("_presion_suficiente", lambda *_a: True),
        ("_fuera_de_edad", lambda *_a: False),
        ("_es_bundle", lambda *_a, **_k: False),
    ):
        monkeypatch.setattr(f, nombre, valor)
    monkeypatch.setattr(cs.volumen, "progreso_curva", lambda _m: (0.1, 5.0))
    monkeypatch.setattr(cs.volumen, "ultimas_son_compra", lambda *_a: True)
    monkeypatch.setattr(cs.volumen, "vio_la_creacion", lambda _m: True)

    async def _normal(*_a, **_k):
        return SimpleNamespace(symbol="N", is_mayhem_mode=False, additional_data={})

    monkeypatch.setattr(cs, "build_pumpfun_token_info", _normal)
    f._probadas.add("M")
    asyncio.run(f._evaluar("M", _cb))
    assert not lanzadas and f._embudo["no_mayhem"] == 1
    assert "M" in f._probadas  # no se vuelve a reconstruir


def test_escalera_y_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "MC_ENTRADA", 4000.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 2.21, 3.01, 5.01, 7.01])
    assert [round(v[1], 4) for v in ventas] == [0.4545, 0.1818, 0.1818, 0.1818]
    # Tras el +120% el stop esta en compra +20%: a 1,19 vende el resto.
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 2.21, 1.5, 1.19])
    assert len(ventas) == 2 and ventas[1][2] == 1.19
    assert abs(ventas[1][1] - 0.5455) < 1e-3
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 0.6, 0.35, 0.29])
    assert ventas and ventas[0][0] == "stop_loss" and ventas[0][2] == 0.29
