"""deploy/devs_activos.py: lee bien las listas y cuenta bien los lanzamientos."""

# ruff: noqa: S101, PLR2004

import importlib.util
import time
from pathlib import Path

import base58
from solders.keypair import Keypair

RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("devs_activos", RAIZ / "deploy/devs_activos.py")
da = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(da)

PUMP = da.PUMP
CREAR = base58.b58encode(bytes.fromhex("d6904cec5f8b31b4") + b"x" * 8).decode()
OTRA = base58.b58encode(b"compra-o-venta").decode()


def _tx(firmante: str, *, crea: bool, hace_dias: float, interna: bool = False) -> dict:
    ix = {"programIdIndex": 1, "data": CREAR if crea else OTRA}
    msg = {"accountKeys": [firmante, PUMP, "Plataforma11111111111111111111111"],
           "header": {"numRequiredSignatures": 1},
           "instructions": [] if interna else [ix]}
    meta = {"err": None, "innerInstructions": [{"instructions": [ix]}] if interna else []}
    return {"blockTime": time.time() - hace_dias * 86400, "transaction": {"message": msg}, "meta": meta}


def test_cuenta_lanzamientos_propios_y_por_plataforma() -> None:
    dev = str(Keypair().pubkey())
    txs = [_tx(dev, crea=True, hace_dias=1), _tx(dev, crea=True, hace_dias=3, interna=True),
           _tx(dev, crea=False, hace_dias=2), _tx(dev, crea=True, hace_dias=20)]
    r = da.lanzamientos(txs, dev, time.time())
    assert r["lanzadas_7d"] == 2
    assert r["lanzadas_30d"] == 3
    assert 0.9 < r["ultima_lanzada_hace_dias"] < 1.1


def test_no_cuenta_lo_que_firmo_otro() -> None:
    dev, otro = str(Keypair().pubkey()), str(Keypair().pubkey())
    assert da.lanzamientos([_tx(otro, crea=True, hace_dias=1)], dev, time.time())["lanzadas_30d"] == 0


def test_estados() -> None:
    assert da.estado(None, None) == "sin datos"
    assert da.estado(2, {"lanzadas_7d": 1, "lanzadas_30d": 1}) == "LANZA (7 dias)"
    assert da.estado(10, {"lanzadas_7d": 0, "lanzadas_30d": 2}) == "lanza (30 dias)"
    assert da.estado(10, {"lanzadas_7d": 0, "lanzadas_30d": 0}) == "se mueve, sin lanzar"
    assert da.estado(90, None) == "PARADO"


def test_lee_todas_las_listas_del_repo() -> None:
    devs = da.cargar_devs()
    assert len(devs) > 700
    assert any(d["en_blacklist"] for d in devs.values())
    assert devs["yHCxHBEaJW5tbndqC8JciSThr7U1cqLpdcsvHcx6PRe"]["mediana_pico"] >= 40000


def test_tapa_la_clave() -> None:
    assert "SECRETO" not in da._tapa("x https://n/?api-key=SECRETO&a=1")  # noqa: SLF001
