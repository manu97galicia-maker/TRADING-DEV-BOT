"""deploy/devs_100k_dextools.py: lee bien DEXTools y saca el dev."""

# ruff: noqa: S101

import importlib.util
import sys
from pathlib import Path

from solders.keypair import Keypair
from solders.pubkey import Pubkey

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "deploy"))
_spec = importlib.util.spec_from_file_location("d100", RAIZ / "deploy/devs_100k_dextools.py")
d100 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(d100)


def test_filas_y_mints() -> None:
    filas = d100.lista_de({"data": [{"mainToken": {"address": "AbcPump"}}, {"address": "Xpump"}, "raro"]})
    assert [d100.mint_de(f) for f in filas] == ["AbcPump", "Xpump"]
    assert d100.lista_de({"data": {"results": [{"address": "a"}]}}) == [{"address": "a"}]
    assert d100.lista_de(None) == []


def test_market_cap() -> None:
    assert d100.market_cap({"data": {"mcap": 150000}}) == 150000
    assert d100.market_cap({"data": {"fdv": "120000.5"}}) == 120000.5
    assert d100.market_cap({"data": {}}) is None
    assert d100.market_cap(None) is None


def test_solo_pumpfun() -> None:
    assert d100.es_de_pumpfun("2mEumtDRD1LmqRgxeJWSHLKSyikm33N3kU2ACLrCpump")
    assert not d100.es_de_pumpfun("So11111111111111111111111111111111111111112")


def test_dev_es_quien_pago_la_creacion() -> None:
    persona = str(Keypair().pubkey())
    assert d100.dev_de({"transaction": {"message": {"accountKeys": [persona, "x"]}}}) == persona
    pda = str(Pubkey.find_program_address([b"x"], Pubkey.new_unique())[0])
    assert d100.dev_de({"transaction": {"message": {"accountKeys": [pda]}}}) is None
    assert d100.dev_de({}) is None
