"""Si pump.fun no contesta al droplet (403), el historial sale de la cadena.

5-nov, "Arreglarlo": deploy/porque-no-compra.sh mostro que pump.fun contesta
403 al droplet y que los 12 devs rechazados salian con 0 lanzadas. Se prueba
de punta a punta con las transacciones de ejemplo de Codex.
"""

# ruff: noqa: ANN001, ANN201, ANN202, S101, PLR2004

import asyncio
from unittest.mock import patch

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from test_creator_transaction_history import migration_accounts, tx

from monitoring import pumpfun_dev
from monitoring.creator_history_index import curve_address
from monitoring.creator_transaction_history import pool_address


class _Resp:
    def __init__(self, status, data=None):
        self.status, self._data = status, data

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Sesion:
    llamadas = 0

    def __init__(self, status, data=None, **_):
        self.status, self.data = status, data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url, headers=None):
        _Sesion.llamadas += 1
        return _Resp(self.status, self.data)


def _pumpfun(status, data=None):
    _Sesion.llamadas = 0
    return patch.object(
        pumpfun_dev.aiohttp, "ClientSession",
        lambda **kw: _Sesion(status, data, **kw),
    )


@pytest.fixture(autouse=True)
def _limpio():
    pumpfun_dev._cache.clear()
    pumpfun_dev._en_vuelo.clear()
    pumpfun_dev._pumpfun_bloqueado_hasta = 0.0
    pumpfun_dev._rpc_respaldo = None
    yield
    pumpfun_dev._rpc_respaldo = None
    pumpfun_dev._pumpfun_bloqueado_hasta = 0.0


def _cadena(dev, migrada, sin_migrar):
    """Un nodo falso: dos creaciones firmadas por `dev`, una migrada."""
    paginas = [{"data": [tx(dev, migrada), tx(dev, sin_migrar, slot=80)],
                "paginationToken": None}]
    curva, pool = migration_accounts(dev, migrada)
    cuentas = {curve_address(str(migrada)): curva, pool_address(str(migrada))[0]: pool}
    llamadas = []

    async def post_rpc(body):
        metodo, params = body["method"], body["params"]
        llamadas.append(metodo)
        if metodo == "getSlot":
            return {"result": 100}
        if metodo == "getTransactionsForAddress":
            return {"result": paginas[0]}
        return {"result": {"context": {"slot": 100},
                           "value": [cuentas.get(a) for a in params[0]]}}

    return post_rpc, llamadas


def test_pumpfun_403_va_a_la_cadena_y_cuenta_bien():
    dev = Keypair().pubkey()
    post_rpc, llamadas = _cadena(dev, Pubkey.new_unique(), Pubkey.new_unique())
    pumpfun_dev.usar_rpc_de_respaldo(post_rpc)
    with _pumpfun(403):
        lanzadas, migradas, tasa = asyncio.run(
            pumpfun_dev.creator_migration_stats(str(dev))
        )
    assert (lanzadas, migradas, tasa) == (2, 1, 0.5)
    assert "getTransactionsForAddress" in llamadas


def test_tras_un_403_no_vuelve_a_preguntar_a_pumpfun():
    dev1, dev2 = Keypair().pubkey(), Keypair().pubkey()
    rpc1, _ = _cadena(dev1, Pubkey.new_unique(), Pubkey.new_unique())
    pumpfun_dev.usar_rpc_de_respaldo(rpc1)
    with _pumpfun(403):
        asyncio.run(pumpfun_dev.creator_migration_stats(str(dev1)))
        assert _Sesion.llamadas == 1
        rpc2, _ = _cadena(dev2, Pubkey.new_unique(), Pubkey.new_unique())
        pumpfun_dev.usar_rpc_de_respaldo(rpc2)
        asyncio.run(pumpfun_dev.creator_migration_stats(str(dev2)))
        assert _Sesion.llamadas == 1  # el segundo dev ya no paso por pump.fun


def test_si_pumpfun_contesta_no_se_gasta_helius():
    dev = Keypair().pubkey()
    post_rpc, llamadas = _cadena(dev, Pubkey.new_unique(), Pubkey.new_unique())
    pumpfun_dev.usar_rpc_de_respaldo(post_rpc)
    monedas = [{"complete": True}] + [{"complete": False}] * 9
    with _pumpfun(200, monedas):
        res = asyncio.run(pumpfun_dev.creator_migration_stats(str(dev)))
    assert res == (10, 1, 0.1)
    assert llamadas == []


def test_sin_pumpfun_y_sin_cadena_no_compra():
    dev = Keypair().pubkey()

    async def nodo_roto(body):
        return {"error": {"code": -32601, "message": "Method not found"}}

    pumpfun_dev.usar_rpc_de_respaldo(nodo_roto)
    with _pumpfun(403):
        assert asyncio.run(pumpfun_dev.creator_migration_stats(str(dev))) == (0, 0, 0.0)


def test_sin_nodo_registrado_sigue_como_antes():
    with _pumpfun(403):
        assert asyncio.run(
            pumpfun_dev.creator_migration_stats(str(Keypair().pubkey()))
        ) == (0, 0, 0.0)


def test_el_trader_registra_el_nodo_si_el_filtro_esta_encendido():
    texto = (pumpfun_dev.__file__.replace("monitoring/pumpfun_dev.py",
             "trading/universal_trader.py"))
    with open(texto, encoding="utf-8") as f:
        src = f.read()
    assert "usar_rpc_de_respaldo(self.solana_client.post_rpc)" in src
