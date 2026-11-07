"""7-nov: "hazme una estrategia de mayhem tambien con volumen y presion
buscando un roi de un 200-500% arriesgando 4$ por operacion"."""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN002, ANN003, ANN201, ANN202, ANN204, PLC0415

import os
import time
from pathlib import Path

import pytest
import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

RAIZ = Path(__file__).resolve().parents[1]
MAYHEM = "bots/bot-real-mayhem.yaml"


def _cfg(f) -> dict:
    return yaml.safe_load((RAIZ / f).read_text(encoding="utf-8"))


def test_config_de_la_mayhem():
    c, p = _cfg(MAYHEM), _cfg("bots/bot-real-presion.yaml")
    t, fl = c["trade"], c["filters"]
    assert fl["allow_mayhem"] is True and fl["require_mayhem"] is True
    assert fl["paper_trade"] is False and c["enabled"] is False
    assert t["buy_amount_usd"] == 4 and t["fraccion_saldo"] == 0
    assert t["stop_loss_percentage"] == 0.50 and t["venta_muerta_seg"] == 900
    assert [t["take_profit_percentage"], t["tp2_gain"], t["tp3_gain"]] == [
        2.0,
        3.5,
        5.0,
    ]
    total = t["take_profit_sell_percentage"] + t["tp2_sell"] + t["tp3_sell"]
    assert total == pytest.approx(1.0)
    assert t["max_posiciones"] == 3 and t["max_posiciones_total"] == 0
    assert fl["dex_presion"]["enabled"] is False
    # Los mismos filtros de entrada que la de presion.
    for k in (
        "min_compradores_ola",
        "min_presion_ratio",
        "min_compra_sol",
        "min_compradores",
        "min_compras_total",
        "min_volumen_total_sol",
        "min_mc_usd",
        "min_edad_seg",
        "ultimas_compras",
        "max_carteras_primer_bloque",
        "max_sol_primer_bloque",
        "umbral",
        "vela2_seg",
        "max_subida_vela2",
    ):
        assert fl["curva_96"][k] == p["filters"]["curva_96"][k], k
    # Y la de presion sigue sin comprar mayhem.
    assert p["filters"]["allow_mayhem"] is False


def test_salida_mayhem_por_el_vigilante_de_verdad(tmp_path, monkeypatch):
    t, ut = _construir(MAYHEM, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 2.5, 3.01, 4.51, 6.01, 8.0])
    assert [v[0] for v in ventas] == [
        "tp_tier1_+200%",
        "tp_tier2_+350%",
        "tp_tier3_+500%",
    ]
    assert [round(v[1], 2) for v in ventas] == [0.40, 0.30, 0.30]


def test_stop_mayhem_al_50(tmp_path, monkeypatch):
    t, ut = _construir(MAYHEM, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 0.7, 0.55, 0.55, 0.49])
    assert ventas == [("stop_loss", 1.0, 0.49)]


def test_mayhem_muerta_a_los_15_minutos(tmp_path, monkeypatch):
    t, ut = _construir(MAYHEM, tmp_path, monkeypatch)
    ventas, segundos = _recorrer(t, ut, monkeypatch, [1.0, 0.95], max_s=60)
    assert len(ventas) == 1 and "muerta" in ventas[0][0]
    assert 899 <= segundos <= 902


def test_las_mayhem_no_cuentan_en_las_3_de_presion(tmp_path, monkeypatch):
    t, _ = _construir("bots/bot-real-presion.yaml", tmp_path, monkeypatch)
    for i in range(3):
        (tmp_path / f"open_position_bot-real-mayhem__M{i}.json").write_text("{}")
    assert t._abiertas_entre_todos() == 0 and not t._tope_comun_lleno()
    for i in range(3):
        (tmp_path / f"open_position_bot-real-presion__P{i}.json").write_text("{}")
    assert t._tope_comun_lleno()
    viejo = time.time() - 3600
    for f in tmp_path.glob("open_position_bot-real-presion__*.json"):
        os.utime(f, (viejo, viejo))
    assert not t._tope_comun_lleno()


@pytest.mark.parametrize("yaml_rel", [MAYHEM, "bots/bot-real-presion.yaml"])
def test_dev_con_mas_del_10_no_se_compra(yaml_rel, tmp_path, monkeypatch):
    """7-nov "asegurate no rugs" -> "dev con poco": max 10% en el creador."""
    import asyncio
    from types import SimpleNamespace

    import aiohttp
    from solders.pubkey import Pubkey

    t, _ = _construir(yaml_rel, tmp_path, monkeypatch)
    assert t.max_dev_hold_pct == 0.10
    tiene = {"n": 0.0}

    class _Resp:
        async def json(self, **_k):
            return {
                "result": {
                    "value": [
                        {
                            "account": {
                                "data": {
                                    "parsed": {
                                        "info": {
                                            "tokenAmount": {"uiAmount": tiene["n"]}
                                        }
                                    }
                                }
                            }
                        }
                    ]
                }
            }

    class _Sesion:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        def post(self, *_a, **_k):
            r = _Resp()

            class _Ctx:
                async def __aenter__(self):
                    return r

                async def __aexit__(self, *_a):
                    return False

            return _Ctx()

    monkeypatch.setattr(aiohttp, "ClientSession", _Sesion)
    ficha = SimpleNamespace(
        symbol="X",
        creator=Pubkey.new_unique(),
        mint=Pubkey.new_unique(),
        token_total_supply=0,
    )
    tiene["n"] = 150_000_000.0  # 15% de 1.000 millones
    assert asyncio.run(t._dev_holds_too_much(ficha)) is True
    tiene["n"] = 50_000_000.0  # 5%
    assert asyncio.run(t._dev_holds_too_much(ficha)) is False


def test_mayhem_tandas_de_5_solo_si_gana(tmp_path, monkeypatch):
    """7-nov: "prueba cinco veces y si ganamos otras cinco"."""
    c = _cfg(MAYHEM)["trade"]
    assert c["max_real_buys"] == 5 and c["reiniciar_tanda"] is True
    assert c["tanda_solo_si_gana"] is True
    t, _ = _construir(MAYHEM, tmp_path, monkeypatch)
    # A medias: no se toca la tanda.
    t._compras_de_la_tanda, t._pnl_tanda = 3, 0.5
    assert t._decide_tanda_que_gana() is True
    # Cinco hechas y en ganancia: se abre otra y el resultado vuelve a cero.
    t._compras_de_la_tanda, t._pnl_tanda = 5, 0.01
    assert t._decide_tanda_que_gana() is False and t._pnl_tanda == 0.0
    assert t.reiniciar_tanda is True
    # Cinco hechas y en perdida: se para.
    t._compras_de_la_tanda, t._pnl_tanda = 5, -0.02
    assert t._decide_tanda_que_gana() is True and t.reiniciar_tanda is False


def test_sin_el_modo_las_tandas_siguen_como_antes(tmp_path, monkeypatch):
    t, _ = _construir("bots/bot-real-presion.yaml", tmp_path, monkeypatch)
    assert t.tanda_solo_si_gana is False
    t._compras_de_la_tanda, t._pnl_tanda = 5, -1.0
    assert t._decide_tanda_que_gana() is False


def test_mayhem_vende_todo_a_las_23_horas(tmp_path, monkeypatch):
    """7-nov "asegurate que en mayhem vendes antes de que se nos bloqueen o
    congelen los tokens": el mayhem dura 24 h; a las 23 h se vende todo."""
    t, ut = _construir(MAYHEM, tmp_path, monkeypatch)
    assert t.vender_antes_de_edad_seg == 82800
    assert _cfg(MAYHEM)["filters"]["curva_96"]["max_edad_seg"] == 72000
    monkeypatch.setattr(t, "_nacio_ts", lambda _ti: time.time() - 82801)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.1, 1.2])
    assert ventas == [("fin_mayhem", 1.0, 1.0)]  # en la primera lectura
    assert t._es_venta_protectora("fin_mayhem")  # prisa y slippage de stop


def test_mayhem_antes_de_23_horas_no_vende_por_edad(tmp_path, monkeypatch):
    t, ut = _construir(MAYHEM, tmp_path, monkeypatch)
    monkeypatch.setattr(t, "_nacio_ts", lambda _ti: time.time() - 3600)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.1, 0.4])
    assert ventas == [("stop_loss", 1.0, 0.4)]


def test_la_ficha_guarda_cuando_nacio(tmp_path, monkeypatch):
    """Para vender a tiempo tambien despues de un reinicio."""
    import json
    from types import SimpleNamespace

    from solders.pubkey import Pubkey

    from interfaces.core import Platform

    t, _ = _construir(MAYHEM, tmp_path, monkeypatch)
    nacio = time.time() - 5000
    ficha = SimpleNamespace(
        mint=Pubkey.new_unique(),
        symbol="MAY",
        platform=Platform.PUMP_FUN,
        token_program_id=None,
        additional_data={"nacio_ts": nacio},
    )
    t._record_open_position(ficha, SimpleNamespace(price=1e-9, amount=1.0))
    rec = json.loads(t._open_position_path(str(ficha.mint)).read_text())
    assert rec["nacio_ts"] == pytest.approx(nacio)
