"""7-nov: "de papel no, asegurate que en real va bien". La parte de los DEX
en REAL, por el bot de verdad montado desde su YAML: compra por Jupiter con
una moneda de Raydium de 9 decimales y vende por TP o por stop. Solo se
falsea lo de fuera (saldo, Jupiter, DexScreener, RugCheck, GeckoTerminal)."""

# ruff: noqa: S101, SLF001, ANN001, ANN002, ANN003, ANN201, ANN202, ARG001, ANN204, PLR0913, PLR0915, C901, PLC0415, S105

import asyncio
import contextlib

import aiohttp
import pytest
from solders.pubkey import Pubkey
from test_tp_sl_en_vivo import _construir

WSOL = "So11111111111111111111111111111111111111112"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
DEC = 9
PRECIO = 1e-6  # SOL por moneda REAL


class _SinCache(dict):
    def __setitem__(self, *_a):
        return None


def _corre(monkeypatch, tmp_path, ruta, segundos):
    from dexscreener.rugcheck import RugReport
    from interfaces.core import Platform, TokenInfo
    from monitoring import dex_presion_source as dp
    from trading import jupiter_seller as js
    from trading import reserva
    from trading import token_builder as tb

    t, _ = _construir("bots/bot-real-presion.yaml", tmp_path, monkeypatch)
    monkeypatch.setattr(reserva, "CARPETA", tmp_path / "reservas")
    t.dex_presion["en_papel"] = False
    mint = str(Pubkey.new_unique())
    e = {"compras": [], "ventas": [], "i": 0, "raw": 0, "vendido": 0}

    async def _get(self, _s, url):
        if "geckoterminal" not in url:
            return []
        return {
            "data": [
                {
                    "attributes": {
                        "address": "POOL",
                        "name": "RAY / SOL",
                        "base_token_price_usd": "0.0001",
                        "reserve_in_usd": "40000",
                        "volume_usd": {"m5": "9000"},
                        "transactions": {"m5": {"buys": 90, "sells": 20, "buyers": 50}},
                    },
                    "relationships": {
                        "base_token": {"data": {"id": "solana_" + mint}},
                        "quote_token": {"data": {"id": "solana_" + WSOL}},
                        "dex": {"data": {"id": "raydium"}},
                    },
                }
            ]
        }

    async def _informe(self, _m):
        return RugReport(found=True, rugged=False, lp_locked_pct=100.0)

    async def _generica(m, client, symbol="", name=""):
        return TokenInfo(
            name="RAY",
            symbol="RAY",
            uri="",
            mint=Pubkey.from_string(m),
            platform=Platform.PUMP_FUN,
            token_program_id=Pubkey.from_string(TOKEN),
            additional_data={"migrated": True, "origen_externo": True},
        )

    async def _compra(kp, m, sol, slippage_bps=0, rpc=None):
        from trading.transaction_observer import PRIORITY_CAP

        e["compras"].append((sol, slippage_bps, PRIORITY_CAP.get()))
        e["raw"] = int(sol / PRECIO * 10**DEC)
        return True, "SIGBUY", None, e["raw"] / 1e6  # el bot cuenta crudo/10**6

    async def _venta(kp, m, ui, decimals=6, slippage_bps=0, rpc=None):
        from trading.transaction_observer import PRIORITY_CAP

        raw = int(ui * 10**decimals)
        e["ventas"].append((round(raw / e["raw"], 4), slippage_bps, PRIORITY_CAP.get()))
        e["vendido"] += raw
        return True, "SIGSELL", None, 0.01

    class _Cuenta:
        owner = Pubkey.from_string(TOKEN)

    async def _cuenta(*_a, **_k):
        return _Cuenta()

    async def _saldo(*_a, **_k):
        return int(0.5e9)

    async def _rpc(body):
        if body.get("method") == "getTokenSupply":
            return {"result": {"value": {"decimals": DEC}}}
        return {}

    async def _tokens(_ata):
        return e["raw"] - e["vendido"]

    class _Resp:
        status = 200

        def __init__(self, d):
            self.d = d

        async def json(self, **_k):
            return self.d

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _Sesion:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        def get(self, _url, **_k):
            i = min(e["i"], len(ruta) - 1)
            e["i"] += 1
            return _Resp(
                {
                    "pairs": [
                        {
                            "quoteToken": {"symbol": "SOL"},
                            "liquidity": {"usd": 40000},
                            "priceNative": str(PRECIO * ruta[i]),
                        }
                    ]
                }
            )

    monkeypatch.setattr(dp.DexPresionSource, "_get", _get)
    monkeypatch.setattr(dp.RugCheckClient, "get_report", _informe)
    monkeypatch.setattr(tb, "build_generic_token_info", _generica)
    monkeypatch.setattr(js, "jupiter_buy", _compra)
    monkeypatch.setattr(js, "jupiter_sell", _venta)
    monkeypatch.setattr(t.solana_client, "get_account_info", _cuenta)
    monkeypatch.setattr(t.solana_client, "get_sol_balance", _saldo)
    monkeypatch.setattr(t.solana_client, "post_rpc", _rpc)
    monkeypatch.setattr(t.solana_client, "get_token_account_balance", _tokens)
    t.price_check_interval = 0.05
    t._precio_dex_cache = _SinCache()

    async def _un_rato():
        real = aiohttp.ClientSession
        aiohttp.ClientSession = _Sesion
        try:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(t._run_dex_presion_mode(), segundos)
        finally:
            aiohttp.ClientSession = real

    asyncio.run(_un_rato())
    return e


def test_en_real_compra_y_vende_los_tres_tp(monkeypatch, tmp_path):
    e = _corre(monkeypatch, tmp_path, [1.0, 1.0, 1.2, 1.55, 1.9, 2.3, 2.3], 4)
    # 30% de 0,47 SOL libres, slippage 9%, prioridad 0,002 SOL.
    assert e["compras"] == [(pytest.approx(0.141), 900, 2_000_000)]
    # 79,71% / 10,14% / 10,14% de LO COMPRADO (9 decimales bien contados),
    # slippage de venta 10%, prioridad de TP 0,001 SOL.
    assert [v[0] for v in e["ventas"]] == [0.7971, 0.1014, 0.1014]
    assert all(v[1:] == (1000, 1_000_000) for v in e["ventas"])


def test_en_real_el_stop_vende_todo_con_su_prioridad(monkeypatch, tmp_path):
    e = _corre(monkeypatch, tmp_path, [1.0, 1.0, 0.9, 0.8, 0.77, 0.77], 4)
    assert len(e["compras"]) == 1
    # Stop -22%: todo, slippage de proteccion 50%, prioridad 0,002 SOL.
    assert e["ventas"] == [(1.0, 5000, 2_000_000)]
