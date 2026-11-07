"""7-nov: presion en PumpSwap, Raydium y Meteora/Bags, con liquidez bloqueada."""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN002, ANN003, ANN201, ANN202, ARG001, PLR0913, PLC0415, TRY003

import json

import pytest

from dexscreener.rugcheck import RugReport
from monitoring import dex_presion_source as dp

WSOL = "So11111111111111111111111111111111111111112"


def _pool(base, quote, dex, buys=80, sells=20, buyers=45, vol="9000", liq="40000"):
    return {
        "id": "solana_POOL",
        "attributes": {
            "address": "POOL" + base[:4],
            "name": "PRUEBA / SOL",
            "base_token_price_usd": "0.0001",
            "reserve_in_usd": liq,
            "volume_usd": {"m5": vol},
            "transactions": {"m5": {"buys": buys, "sells": sells, "buyers": buyers}},
        },
        "relationships": {
            "base_token": {"data": {"id": "solana_" + base}},
            "quote_token": {"data": {"id": "solana_" + quote}},
            "dex": {"data": {"id": dex}},
        },
    }


CFG = {
    "min_compradores_5m": 30,
    "min_compras_5m": 50,
    "min_ratio_5m": 2.0,
    "min_volumen_5m_usd": 5000,
    "min_liquidez_usd": 10000,
}


def test_lee_pools_y_se_queda_con_la_moneda_no_con_sol():
    datos = {
        "data": [
            _pool("MonedaAAA", WSOL, "raydium"),
            _pool(WSOL, "MonedaBBB", "meteora"),
        ]
    }
    ps = dp.lee_pools(datos)
    assert [p.mint for p in ps] == ["MonedaAAA", "MonedaBBB"]
    assert ps[0].compradores == 45 and ps[0].volumen_usd == 9000.0
    assert ps[0].precio_usd == 0.0001 and ps[1].precio_usd == 0.0  # base es SOL


def test_solo_los_dex_pedidos():
    pre = ["raydium", "pumpswap", "meteora"]
    for d in ("raydium", "raydium-clmm", "pumpswap", "meteora", "meteora-dbc"):
        assert dp.dex_permitido(d, pre), d
    for d in ("orca", "pump-fun", "lifinity"):
        assert not dp.dex_permitido(d, pre), d


def test_presion_de_5_minutos():
    ok = dp.lee_pools({"data": [_pool("M", WSOL, "raydium")]})[0]
    assert dp.presion_ok(ok, CFG)[0]
    flojas = [
        _pool("M", WSOL, "raydium", buyers=20),
        _pool("M", WSOL, "raydium", buys=40),
        _pool("M", WSOL, "raydium", sells=60),
        _pool("M", WSOL, "raydium", vol="3000"),
        _pool("M", WSOL, "raydium", liq="5000"),
    ]
    for f in flojas:
        assert not dp.presion_ok(dp.lee_pools({"data": [f]})[0], CFG)[0]


def test_libre_de_rugs():
    bien = RugReport(found=True, rugged=False, lp_locked_pct=100.0, risks=[])
    assert dp.rug_ok(bien, 90)[0]
    assert not dp.rug_ok(None, 90)[0]
    assert not dp.rug_ok(RugReport(found=False), 90)[0]
    assert not dp.rug_ok(RugReport(found=True, rugged=True, lp_locked_pct=100), 90)[0]
    assert not dp.rug_ok(RugReport(found=True, lp_locked_pct=60.0), 90)[0]
    congela = RugReport(
        found=True,
        lp_locked_pct=100,
        risks=[("Freeze Authority still enabled", "danger")],
    )
    assert not dp.rug_ok(congela, 90)[0]
    aviso = RugReport(
        found=True, lp_locked_pct=100, risks=[("Low amount of LP Providers", "warn")]
    )
    assert dp.rug_ok(aviso, 90)[0]


def _sim(**kw):
    base = {
        "entrada": 1.0,
        "tramos": [(0.50, 0.7971014493), (0.85, 0.1014492754), (1.20, 0.1014492753)],
        "stop": 0.22,
        "trailing": 0.22,
        "margen_tras_tp": 0.05,
        "muerta_seg": 300,
        "muerta_gain": 0.0,
        "t0": 0.0,
    }
    base.update(kw)
    return dp.Simulacion(**base)


def test_simula_la_misma_salida_que_el_bot():
    s = _sim()
    for t, p in enumerate([1.0, 1.4, 1.51, 1.86, 2.21, 3.0]):
        s.paso(p, t)
    assert [v[0] for v in s.ventas] == ["tp1_+50%", "tp2_+85%", "tp3_+120%"]
    assert s.cerrada and s.resultado() == pytest.approx(
        0.7971014493 * 0.51 + 0.1014492754 * 0.86 + 0.1014492753 * 1.21
    )


def test_simula_stop_trailing_suelo_y_muerta():
    s = _sim()
    for t, p in enumerate([1.0, 0.9, 0.79, 0.77]):
        s.paso(p, t)
    assert s.ventas == [("stop_loss", 1.0, 0.77)]
    s = _sim()
    for t, p in enumerate([1.0, 1.3, 1.02, 1.01]):
        s.paso(p, t)
    assert s.ventas == [("trailing_stop", 1.0, 1.01)]
    s = _sim()
    for t, p in enumerate([1.0, 1.5, 1.2, 1.04]):
        s.paso(p, t)
    assert s.ventas[0][0] == "tp1_+50%" and s.ventas[-1][0] in (
        "trailing_stop",
        "suelo_tras_tp",
    )
    s = _sim()
    s.paso(1.0, 0)
    s.paso(0.97, 301)
    assert s.ventas == [("moneda_muerta", 1.0, 0.97)]


def test_cierre_de_papel_apunta_bruto_y_neto(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "PAPEL_JSONL", tmp_path / "papel.jsonl")
    f = dp.DexPresionSource(None, None, {"importe_papel_sol": 0.15}, {}, en_papel=True)
    s = _sim()
    for t, p in enumerate([1.0, 1.51, 1.86, 2.21]):
        s.paso(p, t)
    f._abiertas["M"] = s
    f._cierra_papel("M", s)
    fila = json.loads((tmp_path / "papel.jsonl").read_text())
    # 3 ventas: 0,007 + 3 x 0,006 = 0,025 SOL sobre 0,15 -> 16,7 puntos menos.
    assert fila["neto"] == pytest.approx(fila["bruto"] - 0.025 / 0.15)
    assert "M" not in f._abiertas


def test_la_estrategia_lo_tiene_en_papel():
    from pathlib import Path

    import yaml

    c = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "bots/bot-real-presion.yaml").read_text()
    )["filters"]["dex_presion"]
    assert c["enabled"] is True and c["en_papel"] is True
    assert c["dex"] == ["raydium", "pumpswap", "meteora"]
    assert c["min_lp_bloqueada_pct"] == 90


def test_dentro_del_bot_real_compra_en_papel_con_su_salida(tmp_path, monkeypatch):
    """El bot montado desde el YAML arranca la fuente con SU salida y, con una
    candidata limpia, la "compra" en papel sin llamar al comprador real."""
    import asyncio

    from test_tp_sl_en_vivo import _construir

    t, _ = _construir("bots/bot-real-presion.yaml", tmp_path, monkeypatch)
    assert "dex_presion" in t._fuentes_activas() and "curva96" in t._fuentes_activas()
    creadas = []
    original = dp.DexPresionSource.__init__

    def _init(self, *a, **k):
        original(self, *a, **k)
        creadas.append(self)

    async def _get(self, _s, url):
        if "geckoterminal" in url:
            return {"data": [_pool("MonedaLimpia", WSOL, "pumpswap")]}
        return []

    async def _informe(self, _mint):
        return RugReport(found=True, rugged=False, lp_locked_pct=100.0)

    async def _no_compres(_ti):
        raise AssertionError("en papel no se compra")

    monkeypatch.setattr(dp.DexPresionSource, "__init__", _init)
    monkeypatch.setattr(dp.DexPresionSource, "_get", _get)
    monkeypatch.setattr(dp.RugCheckClient, "get_report", _informe)
    monkeypatch.setattr(t, "_on_dexpaid_token", _no_compres)

    async def _un_rato():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(t._run_dex_presion_mode(), 1.0)

    import contextlib

    asyncio.run(_un_rato())
    f = creadas[0]
    assert f.en_papel is True
    assert f.salida["stop"] == 0.22 and f.salida["trailing"] == 0.22
    assert [g for g, _ in f.salida["tramos"]] == [0.50, 0.85, 1.20]
    assert f.salida["margen_tras_tp"] == 0.05 and f.salida["muerta_seg"] == 300
    assert "MonedaLimpia" in f._abiertas
