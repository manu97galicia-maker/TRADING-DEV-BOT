"""6-nov: "El dev es el q mint".

Si una plataforma crea la moneda, pump.fun apunta como `user` una cuenta de
programa. El dev pasa a ser la cartera que pago y firmo la creacion, y sus
monedas minteadas a traves de la plataforma cuentan como suyas.
"""

# ruff: noqa: ANN001, ANN201, ANN202, S101, SLF001

import asyncio
from types import SimpleNamespace

import pytest
import test_second_block_source as log_fixtures
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from monitoring import pumpfun_dev, volumen
from trading.universal_trader import UniversalTrader

PLATAFORMA = Pubkey.find_program_address([b"plataforma"], Pubkey.new_unique())[0]


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    monkeypatch.setattr(pumpfun_dev, "_dev_por_mint", {})
    monkeypatch.setattr(pumpfun_dev, "_buscando_dev", {})
    monkeypatch.setattr(pumpfun_dev, "_PAUSA_FIRMA", 0.0)
    precalentados = []
    monkeypatch.setattr(pumpfun_dev, "precalentar", lambda d, m=None: precalentados.append((d, m)))
    yield precalentados
    volumen._firma_creacion.clear()


def _nodo(monkeypatch, respuestas):
    """Nodo falso: devuelve las transacciones de la lista, una por llamada."""
    llamadas = []

    async def rpc(payload):
        llamadas.append(payload)
        return {"result": respuestas.pop(0) if respuestas else None}

    monkeypatch.setattr(pumpfun_dev, "_rpc_respaldo", rpc)
    return llamadas


def _tx_de_creacion(pagador):
    return {"transaction": {"message": {"accountKeys": [str(pagador), str(PLATAFORMA)]}}}


def test_una_cuenta_de_programa_no_es_una_cartera():
    assert pumpfun_dev.es_cartera(str(Keypair().pubkey())) is True
    assert pumpfun_dev.es_cartera(str(PLATAFORMA)) is False
    assert pumpfun_dev.es_cartera("no-es-una-direccion") is False


def test_el_dev_es_la_cartera_que_pago_la_creacion(monkeypatch, _limpio):
    persona, mint = Keypair().pubkey(), str(Pubkey.new_unique())
    volumen.registrar_bloque_inicial(mint, None, dev=str(PLATAFORMA), firma="firma-1")
    # Recien creada el nodo aun no la tiene: primero None, luego la tx.
    llamadas = _nodo(monkeypatch, [None, _tx_de_creacion(persona)])
    assert asyncio.run(pumpfun_dev.dev_que_mintea(mint)) == str(persona)
    assert llamadas[0]["method"] == "getTransaction"
    assert llamadas[0]["params"][0] == "firma-1"
    assert len(llamadas) == 2
    # Y su historial empieza a leerse ya.
    assert _limpio == [(str(persona), mint)]


def test_si_paga_otra_cuenta_de_programa_no_hay_dev(monkeypatch):
    mint = str(Pubkey.new_unique())
    volumen.registrar_bloque_inicial(mint, None, firma="firma-2")
    _nodo(monkeypatch, [_tx_de_creacion(PLATAFORMA)])
    assert asyncio.run(pumpfun_dev.dev_que_mintea(mint)) is None


def test_sin_firma_de_creacion_no_hay_dev(monkeypatch):
    _nodo(monkeypatch, [])
    assert asyncio.run(pumpfun_dev.dev_que_mintea(str(Pubkey.new_unique()))) is None


def test_el_trader_usa_la_cartera_que_minteo(monkeypatch):
    persona, mint = Keypair().pubkey(), Pubkey.new_unique()
    volumen.registrar_bloque_inicial(str(mint), None, firma="firma-3")
    _nodo(monkeypatch, [_tx_de_creacion(persona)])
    ficha = SimpleNamespace(mint=mint, user=PLATAFORMA, creator=PLATAFORMA, symbol="P")
    yo = object.__new__(UniversalTrader)
    assert asyncio.run(UniversalTrader._dev_que_mintea(yo, ficha)) == str(persona)


def test_una_persona_normal_no_gasta_ninguna_consulta(monkeypatch):
    persona, mint = Keypair().pubkey(), Pubkey.new_unique()
    llamadas = _nodo(monkeypatch, [])
    ficha = SimpleNamespace(mint=mint, user=persona, creator=persona, symbol="N")
    yo = object.__new__(UniversalTrader)
    assert asyncio.run(UniversalTrader._dev_que_mintea(yo, ficha)) == str(persona)
    assert llamadas == []


def test_sus_monedas_por_plataforma_cuentan_como_lanzadas():
    persona, mint = Keypair().pubkey(), Pubkey.new_unique()
    tx = {
        "transaction": {"message": {
            "accountKeys": [str(persona), str(PLATAFORMA)],
            "header": {"numRequiredSignatures": 1},
        }},
        "meta": {"err": None, "logMessages": [
            f"Program {PLATAFORMA} invoke [1]",
            *log_fixtures.logs(log_fixtures.create(mint, PLATAFORMA, user=PLATAFORMA)),
            f"Program {PLATAFORMA} success",
        ]},
    }
    # Los logs de pump van un nivel por dentro de la plataforma.
    tx["meta"]["logMessages"][1] = tx["meta"]["logMessages"][1].replace("[1]", "[2]")
    assert pumpfun_dev._minteadas_por_plataforma(tx, str(persona)) == {str(mint)}
    # Quien no firmo no se la lleva.
    assert pumpfun_dev._minteadas_por_plataforma(tx, str(Keypair().pubkey())) == set()
