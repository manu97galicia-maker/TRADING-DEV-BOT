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


def test_el_log_da_el_motivo_pero_nunca_la_clave(caplog):
    import logging

    dev = Keypair().pubkey()

    async def nodo_que_revienta(body):
        raise OSError("fallo en https://mainnet.helius-rpc.com/?api-key=SECRETO999")

    pumpfun_dev.usar_rpc_de_respaldo(nodo_que_revienta)
    caplog.set_level(logging.WARNING)
    for nombre in ("monitoring.pumpfun_dev", "pumpfun_dev"):
        logging.getLogger(nombre).propagate = True
    with _pumpfun(403):
        asyncio.run(pumpfun_dev.creator_migration_stats(str(dev)))
    texto = caplog.text
    assert "SECRETO999" not in texto


def test_un_dev_sin_monedas_previas_queda_dicho_en_el_log(caplog):
    import logging

    dev = Keypair().pubkey()

    async def nodo_vacio(body):
        if body["method"] == "getSlot":
            return {"result": 100}
        return {"result": {"data": [], "paginationToken": None}}

    pumpfun_dev.usar_rpc_de_respaldo(nodo_vacio)
    caplog.set_level(logging.WARNING)
    with _pumpfun(403):
        assert asyncio.run(pumpfun_dev.creator_migration_stats(str(dev))) == (0, 0, 0.0)
    assert "No verified creation history" in caplog.text


# --- Tolerancia (5-nov): 42 de 67 fallos eran logs cortados en UNA tx ---------

import base58 as _b58  # noqa: E402

_CREAR_V2 = bytes.fromhex("d6904cec5f8b31b4")
_COMPRAR = bytes.fromhex("66063d1201daebea")


def _cortada(dev, *, instruccion, firma="sig-cortada", slot=70):
    """Una tx del dev con los logs cortados por Solana."""
    t = tx(dev, Pubkey.new_unique(), slot=slot)
    t["transaction"]["signatures"] = [firma]
    t["meta"]["logMessages"] = ["Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                                "Log truncated"]
    t["transaction"]["message"]["instructions"] = [
        {"programIdIndex": 1, "accounts": [], "data": _b58.b58encode(instruccion + b"x" * 8).decode()}
    ]
    return t


def _nodo(dev, paginas, cuentas=None, fallos_primero=0):
    estado = {"fallos": fallos_primero}

    async def post_rpc(body):
        metodo, params = body["method"], body["params"]
        if metodo == "getSlot":
            return {"result": 100}
        if metodo == "getTransactionsForAddress":
            if estado["fallos"]:
                estado["fallos"] -= 1
                return {"error": {"code": -32000, "message": "temporal"}}
            return {"result": paginas.pop(0)}
        return {"result": {"context": {"slot": 100},
                           "value": [(cuentas or {}).get(a) for a in params[0]]}}

    return post_rpc


def _con_migrada(dev):
    buena = Pubkey.new_unique()
    curva, pool = migration_accounts(dev, buena)
    return tx(dev, buena), {curve_address(str(buena)): curva, pool_address(str(buena))[0]: pool}


def test_una_creacion_ilegible_cuenta_como_lanzada_no_migrada():
    dev = Keypair().pubkey()
    buena, cuentas = _con_migrada(dev)
    paginas = [{"data": [buena, _cortada(dev, instruccion=_CREAR_V2)], "paginationToken": None}]
    pumpfun_dev.usar_rpc_de_respaldo(_nodo(dev, paginas, cuentas))
    with _pumpfun(403):
        res = asyncio.run(pumpfun_dev.creator_migration_stats(str(dev)))
    # Antes: TODO el historial invalido -> (0, 0, 0). Ahora: 2 lanzadas, 1 migrada.
    assert res == (2, 1, 0.5)


def test_una_compra_ilegible_se_ignora():
    dev = Keypair().pubkey()
    buena, cuentas = _con_migrada(dev)
    paginas = [{"data": [buena, _cortada(dev, instruccion=_COMPRAR)], "paginationToken": None}]
    pumpfun_dev.usar_rpc_de_respaldo(_nodo(dev, paginas, cuentas))
    with _pumpfun(403):
        assert asyncio.run(pumpfun_dev.creator_migration_stats(str(dev))) == (1, 1, 1.0)


def test_lo_ilegible_nunca_sube_el_porcentaje():
    """La propiedad que protege el dinero: con dudas, el % solo baja."""
    dev = Keypair().pubkey()
    buena, cuentas = _con_migrada(dev)
    cortadas = [_cortada(dev, instruccion=_CREAR_V2, firma=f"s{i}", slot=60 - i) for i in range(9)]
    paginas = [{"data": [buena, *cortadas], "paginationToken": None}]
    pumpfun_dev.usar_rpc_de_respaldo(_nodo(dev, paginas, cuentas))
    with _pumpfun(403):
        lanzadas, migradas, tasa = asyncio.run(pumpfun_dev.creator_migration_stats(str(dev)))
    assert (lanzadas, migradas) == (10, 1)
    assert tasa == 0.1  # 1 de 10, aunque las 9 dudosas pudieran haber migrado


def test_un_fallo_puntual_de_helius_se_reintenta():
    dev = Keypair().pubkey()
    buena, cuentas = _con_migrada(dev)
    paginas = [{"data": [buena], "paginationToken": None}]
    pumpfun_dev.usar_rpc_de_respaldo(_nodo(dev, paginas, cuentas, fallos_primero=1))
    with _pumpfun(403):
        assert asyncio.run(pumpfun_dev.creator_migration_stats(str(dev))) == (1, 1, 1.0)


def test_dos_fallos_seguidos_dan_sin_dato_y_dicen_el_motivo(caplog):
    import logging

    dev = Keypair().pubkey()
    pumpfun_dev.usar_rpc_de_respaldo(_nodo(dev, [], fallos_primero=2))
    caplog.set_level(logging.WARNING)
    with _pumpfun(403):
        assert asyncio.run(pumpfun_dev.creator_migration_stats(str(dev))) == (0, 0, 0.0)
    assert "getTransactionsForAddress: temporal" in caplog.text


def test_pagina_sin_marca_de_siguiente_es_el_final():
    dev = Keypair().pubkey()
    buena, cuentas = _con_migrada(dev)
    paginas = [{"data": [buena]}]  # sin "paginationToken"
    pumpfun_dev.usar_rpc_de_respaldo(_nodo(dev, paginas, cuentas))
    with _pumpfun(403):
        assert asyncio.run(pumpfun_dev.creator_migration_stats(str(dev))) == (1, 1, 1.0)


# --- Mas rapido (5-nov): el historial empieza a leerse al ver la creacion -----

def test_precalentar_empieza_ya_y_el_trader_se_engancha():
    dev = Keypair().pubkey()
    post_rpc, llamadas = _cadena(dev, Pubkey.new_unique(), Pubkey.new_unique())
    pumpfun_dev.usar_rpc_de_respaldo(post_rpc)

    async def escena():
        pumpfun_dev.precalentar(str(dev))      # el escuchador, al ver nacer
        assert str(dev) in pumpfun_dev._en_vuelo  # ya esta corriendo
        return await pumpfun_dev.creator_migration_stats(str(dev))  # el trader

    with _pumpfun(403):
        assert asyncio.run(escena()) == (2, 1, 0.5)
    # Una sola lectura de la cadena, no dos.
    assert llamadas.count("getTransactionsForAddress") == 1


def test_precalentar_no_gasta_nada_sin_estrategia_con_filtro_de_dev():
    async def escena():
        pumpfun_dev.precalentar(str(Keypair().pubkey()))
        return dict(pumpfun_dev._en_vuelo)

    assert asyncio.run(escena()) == {}


def test_precalentar_no_repite_un_dev_ya_guardado():
    dev = Keypair().pubkey()
    post_rpc, llamadas = _cadena(dev, Pubkey.new_unique(), Pubkey.new_unique())
    pumpfun_dev.usar_rpc_de_respaldo(post_rpc)

    async def escena():
        await pumpfun_dev.creator_migration_stats(str(dev))
        antes = len(llamadas)
        pumpfun_dev.precalentar(str(dev))
        await asyncio.sleep(0)
        return antes, len(llamadas)

    with _pumpfun(403):
        antes, despues = asyncio.run(escena())
    assert antes == despues


def test_el_escuchador_precalienta_al_ver_la_creacion():
    from pathlib import Path

    src = (Path(pumpfun_dev.__file__).parent / "universal_logs_listener.py").read_text(encoding="utf-8")
    i_registro = src.index("registrar_bloque_inicial(\n")
    assert "precalentar(str(_dev))" in src[i_registro:i_registro + 900]
