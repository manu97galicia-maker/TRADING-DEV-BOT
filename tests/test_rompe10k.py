"""7-nov: "compra todas las monedas que rompan los 10.1k mc y las ultimas 3
tx sean compras, nueva estrategia"."""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN201, ANN202

from pathlib import Path

import yaml
from test_tp_sl_en_vivo import _construir, _recorrer

from monitoring import curva96_source as cs

RAIZ = Path(__file__).resolve().parents[1]
YAML = "bots/bot-real-rompe10k.yaml"


def _cfg(f):
    return yaml.safe_load((RAIZ / f).read_text(encoding="utf-8"))


def test_config_rompe_10k():
    c, n = _cfg(YAML), _cfg("bots/bot-real-presion.yaml")
    cv, fl, t = c["filters"]["curva_96"], c["filters"], c["trade"]
    assert cv["min_mc_usd"] == 10100 and cv["max_mc_usd"] == 12000
    assert cv["ultimas_compras"] == 3
    # Nada mas: sin presion, volumen, compradores ni edad.
    for k in (
        "min_compradores_ola",
        "min_presion_ratio",
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
    # Tamano y venta como la normal; comparte el tope de 3 con ella.
    for k in (
        "fraccion_saldo",
        "stop_loss_percentage",
        "take_profit_percentage",
        "take_profit_sell_percentage",
        "tp2_gain",
        "tp2_sell",
        "venta_muerta_seg",
    ):
        assert t[k] == n["trade"][k], k
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


def test_vende_como_la_normal(tmp_path, monkeypatch):
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.51, 1.81, 2.5])
    assert [v[0] for v in ventas] == ["tp_tier1_+50%", "tp_tier2_+80%"]
    t, ut = _construir(YAML, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 0.9, 0.77])
    assert ventas == [("stop_loss", 1.0, 0.77)]
