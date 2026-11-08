"""8-nov: "consulta a Dune primero si tiene mas o si esta actualizado; quiero
unos 200 lanzamientos diarios si puede ser" (estrategia mayhem)."""

# ruff: noqa: S101, PLR2004, ANN001, ANN201, ANN202

import importlib.util
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "dune_mayhem", RAIZ / "deploy/dune_mayhem.py"
)
dm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dm)


def _dev(n, l7, p100, p30=60, rug=0):
    return {
        "dev": f"D{n}",
        "lanz_30d": l7 * 4,
        "lanz_7d": l7,
        "pct_100k": p100,
        "pct_30k": p30,
        "mediana_usd": 20000,
        "pct_rug": rug,
        "ultimo_dato": "2026-11-08 10:00",
        "mayhem_24h": 900,
        "mayhem_7d": 6300,
    }


def test_suma_los_mejores_hasta_el_objetivo():
    filas = [
        _dev(1, 70, 50),  # 10/dia
        _dev(2, 700, 30),  # 100/dia
        _dev(3, 700, 20),  # 100/dia
        _dev(4, 700, 10),  # sobra
        _dev(5, 7000, 90, rug=80),  # vende pronto: fuera
        _dev(6, 0, 100),  # no ha lanzado esta semana: fuera
    ]
    lista = dm.elige(filas, 200)
    assert [f["dev"] for f in lista] == ["D1", "D2", "D3"]
    assert dm.resumen(lista)["lanz_dia"] == 210.0


def test_si_no_llega_salen_todos_los_activos():
    lista = dm.elige([_dev(1, 7, 50), _dev(2, 14, 0)], 200)
    assert [f["dev"] for f in lista] == ["D1", "D2"]


def test_sin_dune_escribe_la_lista(tmp_path, monkeypatch, capsys):
    crudo = tmp_path / "crudo.json"
    crudo.write_text(json.dumps({"sol": 140, "filas": [_dev(1, 700, 40)]}))
    monkeypatch.setattr(dm, "CRUDO", crudo)
    monkeypatch.setattr(dm, "SALIDA", tmp_path / "lista.json")
    monkeypatch.setattr(sys, "argv", ["x", "--sin-dune", "--objetivo", "50"])
    dm.main()
    out = capsys.readouterr().out
    assert "ultima creacion de pump.fun en Dune: 2026-11-08 10:00" in out
    assert "900 monedas en 24 h" in out
    assert json.loads((tmp_path / "lista.json").read_text())["wallets"] == ["D1"]


def test_la_consulta_es_solo_mayhem_y_usa_el_sol_de_hoy():
    sql = (RAIZ / "deploy/dune/mayhem_devs.sql").read_text()
    assert "is_mayhem_mode = true" in sql and "{SOL_USD}" in sql
    for col in ("lanz_7d", "pct_100k", "pct_rug", "ultimo_dato", "mayhem_24h"):
        assert col in sql
