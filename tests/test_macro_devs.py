"""deploy/macro_devs.py: lee bien GMGN, DexScreener y DEXTools."""

# ruff: noqa: S101, PLR2004

import importlib.util
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "deploy"))
_spec = importlib.util.spec_from_file_location("macro_devs", RAIZ / "deploy/macro_devs.py")
md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(md)


def test_gmgn() -> None:
    r = md.leer_gmgn({"inner_count": 18, "open_count": 2, "last_create_timestamp": str(int(time.time()) - 86400),
                      "creator_ath_info": {"ath_mc": "45000", "creator": "x"}})
    assert r["gmgn_lanzadas"] == 20 and r["gmgn_migradas"] == 2 and r["gmgn_pct_migradas"] == 10.0
    assert 0.9 < r["gmgn_ultima_lanzada_hace_dias"] < 1.1
    assert r["gmgn_ath_mc"] == 45000.0
    assert md.leer_gmgn("raro") is None


def test_dex() -> None:
    datos = {"pairs": [
        {"baseToken": {"address": "A"}, "liquidity": {"usd": 10}, "info": {"websites": [{"url": "x"}]}},
        {"baseToken": {"address": "A"}, "liquidity": {"usd": 1}, "info": {}},
        {"baseToken": {"address": "B"}, "liquidity": {"usd": 5}, "boosts": {"active": 2}},
    ]}
    r = md.leer_dex(datos)
    assert r["A"]["pago_dex"] is True and r["A"]["boost"] is False
    assert r["B"]["pago_dex"] is False and r["B"]["boost"] is True
    assert md.leer_dex(None) == {}


def test_siempre_pide_al_menos_dos() -> None:
    assert md.resumen_pago([True])["siempre"] is False
    assert md.resumen_pago([True, True])["siempre"] is True
    assert md.resumen_pago([True, False])["siempre"] is False


def test_dextools() -> None:
    assert md.leer_dextools({"data": {"socialInfo": {"twitter": "https://x.com/a", "web": ""}}}) is True
    assert md.leer_dextools({"data": {"socialInfo": {"twitter": "", "web": ""}}}) is False
    assert md.leer_dextools(None) is None


def test_devs_activos_saca_los_mints() -> None:
    import base58
    from solders.keypair import Keypair

    dev = str(Keypair().pubkey())
    data = base58.b58encode(bytes.fromhex("d6904cec5f8b31b4") + b"x" * 8).decode()
    tx = {"blockTime": time.time(), "meta": {"err": None},
          "transaction": {"message": {"accountKeys": [dev, md.da.PUMP, "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"],
                                      "header": {"numRequiredSignatures": 1},
                                      "instructions": [{"programIdIndex": 1, "accounts": [2, 0], "data": data}]}}}
    assert md.da.lanzamientos([tx], dev, time.time())["mints_recientes"] == ["MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"]


def test_regla_del_bot() -> None:
    assert md.cumple_la_regla({"migradas_total": 1, "pct_migradas": 10.0}) is True
    assert md.cumple_la_regla({"migradas_total": 1, "pct_migradas": 9.1}) is False
    assert md.cumple_la_regla({"migradas_total": 0, "pct_migradas": 0}) is False
    assert md.cumple_la_regla(None) is False


def test_la_regla_es_la_del_yaml() -> None:
    import yaml

    dev = yaml.safe_load((RAIZ / "bots/bot-paper-dev1-bloque2.yaml").read_text())["filters"]["pumpfun_dev"]
    assert md.MIN_MIGRADAS == dev["min_migrations"]
    assert md.MIN_PCT == dev["min_rate"] * 100


def test_historial_usa_el_lector_del_bot(monkeypatch) -> None:
    from monitoring import pumpfun_dev

    async def falso(w):
        if w == "MALO":
            raise RuntimeError("sin respuesta del nodo")
        return 20, 2

    monkeypatch.setattr(pumpfun_dev, "_historial_tolerante", falso)
    monkeypatch.setattr(md.da, "PAUSA", 0)
    r = md.historial_con_helius(["BUENO", "MALO"], "http://nodo.invalid/?api-key=SECRETO")
    assert r["BUENO"] == {"lanzadas_total": 20, "migradas_total": 2, "pct_migradas": 10.0}
    assert "SECRETO" not in str(r["MALO"])
    assert md.cumple_la_regla(r["BUENO"]) is True
