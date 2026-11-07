"""7-nov: "compra todas las monedas que rompan los 10.1k mc y las ultimas 3
tx sean compras, nueva estrategia"."""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN201, ANN202

import asyncio
from pathlib import Path
from types import SimpleNamespace

import test_tp_sl_en_vivo as tv
import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

from monitoring import curva96_source as cs

RAIZ = Path(__file__).resolve().parents[1]
YAML = "bots/bot-real-rompe10k.yaml"


def _cfg(f):
    return yaml.safe_load((RAIZ / f).read_text(encoding="utf-8"))


def test_config_rompe_10k():
    c = _cfg(YAML)
    cv, fl, t = c["filters"]["curva_96"], c["filters"], c["trade"]
    # 7-nov, despues: "que rompa el 71% de la bonding", solo hasta el 76%,
    # sin filtro de market cap; "con fuerza" = presion de 5 s (4 y 1,5x).
    assert cv["umbral"] == 0.71 and cv["tope"] == 0.76
    assert cv["min_mc_usd"] == 0 and cv["max_mc_usd"] == 0
    assert cv["ultimas_compras"] == 3
    assert cv["min_compradores_ola"] == 4 and cv["min_presion_ratio"] == 1.5
    assert cv["ventana_presion"] == 5.0
    # "sin limite de operaciones", pero "cuidame de rugs": el freno de 3
    # perdidas seguidas se queda.
    assert t["max_real_buys"] == 0 and t["max_posiciones"] == 3
    assert t["max_perdidas_seguidas"] == 3
    # Nada mas: sin volumen, compradores ni edad.
    for k in (
        "min_compra_sol",
        "min_compradores",
        "min_compras_total",
        "min_volumen_total_sol",
        "min_volumen_reciente_sol",
        "min_edad_seg",
    ):
        assert cv[k] == 0, k
    # Los frenos contra rugs se quedan.
    assert fl["max_dev_hold_pct"] == 0.10 and fl["allow_mayhem"] is False
    assert cv["max_carteras_primer_bloque"] == 5 and cv["max_sol_primer_bloque"] == 9.0
    assert cv["max_subida_vela"] == 0.35 and cv["max_subida_vela2"] == 0.60
    # Despues: "50% del total balance", TP +40% (recupera) / +90% / +140%,
    # SL en 7,4k de market cap y el trailing fijo en 22%.
    assert t["fraccion_saldo"] == 0.50 and t["fraccion_base"] == "cartera"
    assert t["stop_loss_percentage"] == 0 and t["mc_stop_loss"] == 7400
    assert t["trailing_stop_percentage"] == 0.22
    assert [t["take_profit_percentage"], t["tp2_gain"], t["tp3_gain"]] == [
        0.40,
        0.90,
        1.40,
    ]
    total = t["take_profit_sell_percentage"] + t["tp2_sell"] + t["tp3_sell"]
    assert abs(total - 1.0) < 1e-9
    assert (
        abs(t["take_profit_sell_percentage"] * 1.40 - 1.0) < 1e-6
    )  # recupera lo invertido
    assert t["stop_a_entrada_tras_tp"] is True
    assert t["max_posiciones_total"] == 3
    assert fl["paper_trade"] is False and fl["dex_presion"]["enabled"] is False


def test_sin_presion_pasa_cualquiera(monkeypatch):
    f = cs.Curva96Source(
        "ws://x",
        None,
        None,
        min_compradores_ola=0,
        min_presion_ratio=0,
        min_compra_sol=0,
    )
    monkeypatch.setattr(cs.volumen, "metricas_segunda_ola", lambda *_a, **_k: {})
    assert f._presion_suficiente("M", "M") is True


def test_solo_justo_al_romper(monkeypatch):
    f = cs.Curva96Source(
        "ws://x", None, None, min_mc_usd=10100, max_mc_usd=12000, sol_usd=lambda: 90.0
    )
    for mc, ok in ((9000, False), (10200, True), (11900, True), (15000, False)):
        monkeypatch.setattr(
            cs.volumen, "precio_en_vivo", lambda _m, _e, mc=mc: mc / 90 / 1e9
        )
        assert f._llega_al_mc("M") is ok, mc
    assert f._embudo["sobre_mc_maximo"] == 1 and f._embudo["bajo_mc_minimo"] == 1


def test_vende_40_90_140_y_stop_en_7400(tmp_path, monkeypatch):
    """Con el vigilante de verdad y una entrada a 10.000 $ (compra al romper
    10,1k): TP +40% / +90% / +140%, y el stop salta al bajar de 7.400 $."""
    monkeypatch.setattr(tv, "MC_ENTRADA", 10000.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.41, 1.91, 2.41, 3.0])
    assert [v[0] for v in ventas] == [
        "tp_tier1_+40%",
        "tp_tier2_+90%",
        "tp_tier3_+140%",
    ]
    assert abs(sum(v[1] for v in ventas) - 1.0) < 1e-3
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 0.80, 0.75, 0.75, 0.73])
    assert ventas == [("stop_loss", 1.0, 0.73)]  # 7.300 $ < 7.400 $


def test_tras_el_tp1_el_stop_sube(tmp_path, monkeypatch):
    """ "Subes sl cuando toque tp1": tras +40% el resto sale en compra +5%."""
    monkeypatch.setattr(tv, "MC_ENTRADA", 10000.0)
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.41, 1.2, 1.06, 1.04])
    assert ventas[0][0] == "tp_tier1_+40%" and len(ventas) == 2
    # Sale por el trailing (pico 1,41 x 0,78 = 1,10) o por el suelo de compra
    # +5%: nunca por debajo de compra +5%.
    assert ventas[1][2] >= 1.05


def test_el_resto_de_menos_de_1_dolar_se_vende(tmp_path, monkeypatch):
    """7-nov "que no queden saldos menores a un dolar": antes se dejaba en la
    wallet; ahora se vende (y la posicion cuenta como cerrada)."""
    c = _cfg(YAML)
    assert c["trade"]["vender_polvo"] is True
    assert c["cleanup"]["mode"] == "after_sell"
    assert c["cleanup"]["force_close_with_burn"] is True
    monkeypatch.setattr(tv, "MC_ENTRADA", 10000.0)  # por encima del stop 7,4k
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    t.valor_minimo_posicion_usd = 10_000.0  # todo cuenta como resto
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.0, 1.0])
    assert ventas and ventas[0][0] == "polvo" and ventas[0][1] == 1.0


def test_solo_se_queman_restos_de_menos_de_1_dolar(tmp_path, monkeypatch):
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ficha = SimpleNamespace(symbol="X")
    precio = 1.0 / (1e6 * ut._SOL_USD)  # 1.000.000 tokens = 1 $
    queda = {"n": 500_000.0}

    async def _saldo(_ti):
        return queda["n"]

    monkeypatch.setattr(t, "_saldo_real_tokens", _saldo)
    assert asyncio.run(t._puede_quemar(ficha, precio)) is True  # 0,50 $
    queda["n"] = 3_000_000.0  # 3 $: vale dinero, no se quema
    assert asyncio.run(t._puede_quemar(ficha, precio)) is False
    queda["n"] = None  # sin saldo leido, no se quema
    assert asyncio.run(t._puede_quemar(ficha, precio)) is False
    queda["n"] = 10.0
    t.cleanup_force_close_with_burn = False  # sin la opcion, nunca
    assert asyncio.run(t._puede_quemar(ficha, precio)) is False


def test_solo_entre_71_y_76_por_ciento(monkeypatch):
    """Solo entra justo al romper el 71% de la curva; al 76% ya no."""
    c = _cfg(YAML)["filters"]["curva_96"]
    f = cs.Curva96Source("ws://x", None, None, umbral=c["umbral"], tope=c["tope"])
    lanzadas = []
    monkeypatch.setattr(f, "_lanzar", lambda m, _cb: lanzadas.append(m))
    avances = {"A": 0.70, "B": 0.71, "C": 0.74, "D": 0.77, "E": 0.95}
    monkeypatch.setattr(cs.volumen, "progreso_curva", lambda m: (avances[m], 0.0))
    f._revisar(list(avances), None)
    assert lanzadas == ["B", "C"]
