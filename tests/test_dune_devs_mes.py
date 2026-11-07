"""deploy/dune_devs_mes.py: los cortes de Manu sobre filas como las de Dune."""

# ruff: noqa: S101, PLR2004, ANN001, ANN003, ANN201, ANN202, E741, SLF001, PLC0415, ARG001

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import dune_devs_mes as dd


def _dev(**kw):
    base = {
        "dev": "D",
        "lanzamientos": 8,
        "semanas_activas": 4,
        "lanz_7d": 2,
        "pct_migra": 75.0,
        "mediana_usd": 30000,
        "moda_usd": 30000,
        "frec_20k": 80.0,
        "frec_80k": 10.0,
        "n_300k": 0,
        "n_1m": 0,
        "rugs": 0,
        "mechas": 0,
        "misma_wallet": 0,
        "trades_dia": 120,
        "mints": ["m1"],
    }
    base.update(kw)
    return base


def test_activos_de_calidad():
    assert len(dd.clasifica([_dev()])["activos_calidad"]) == 1
    for malo in (
        _dev(semanas_activas=3),  # no saca uno por semana
        _dev(lanz_7d=0),  # no activo
        _dev(pct_migra=60.0),  # tiene que ser MAS del 60%
        _dev(mediana_usd=19000),
        _dev(moda_usd=10000),
        _dev(frec_20k=50.0),
    ):
        assert dd.clasifica([malo])["activos_calidad"] == [], malo


def test_ninguno_con_rugs_mechas_ni_misma_wallet():
    for malo in (_dev(rugs=1), _dev(mechas=1), _dev(misma_wallet=1)):
        listas = dd.clasifica([{**malo, "n_300k": 2, "n_1m": 1, "frec_80k": 90}])
        assert all(v == [] for v in listas.values()), malo


def test_300k_1m_y_80k():
    l = dd.clasifica(
        [
            _dev(dev="A", n_300k=1),
            _dev(dev="B", n_300k=2, n_1m=1),
            _dev(dev="C", n_1m=1, lanz_7d=0),  # inactivo: fuera de 1m
            _dev(dev="E", frec_80k=60.0, lanz_7d=0),  # 80k no exige actividad
        ]
    )
    assert [f["dev"] for f in l["superaron_300k"]] == ["A", "B"]
    assert [f["dev"] for f in l["superaron_1m"]] == ["B"]
    assert [f["dev"] for f in l["suelen_80k"]] == ["E"]


def test_operaciones_al_dia():
    devs = [_dev(trades_dia=120, lanzamientos=30), _dev(trades_dia=80, lanzamientos=15)]
    assert dd.trades_al_dia(devs) == 200
    assert dd.lanzamientos_al_dia(devs) == 1.5


def test_la_consulta_lleva_el_precio_de_sol_y_las_definiciones():
    sql = dd.SQL.read_text(encoding="utf-8")
    assert "{SOL_USD}" in sql
    for palabra in (
        "pump_evt_createevent",
        "pump_evt_tradeevent",
        "dex_solana.trades",
        "dev_vende_30m",
        "mc_10s",
        "top_share",
    ):
        assert palabra in sql


def test_la_clave_no_se_imprime(monkeypatch, capsys):
    monkeypatch.setenv("DUNE_API_KEY", "secreta123")
    assert dd._clave() == "secreta123"
    assert "secreta123" not in capsys.readouterr().out


def test_si_dune_rechaza_una_via_prueba_la_siguiente(monkeypatch, capsys):
    """7-nov: /sql/execute dio HTTP 400 en el droplet."""
    import io
    import urllib.error

    llamadas = []

    def _pide(url, clave=None, cuerpo=None):
        llamadas.append(url.rsplit("/v1/", 1)[1])
        if url.endswith("/sql/execute"):
            raise urllib.error.HTTPError(
                url, 400, "Bad Request", {}, io.BytesIO(b"nope")
            )
        if url.endswith("/query"):
            return {"query_id": 77}
        return {"execution_id": "E1"}

    monkeypatch.setattr(dd, "_pide", _pide)
    assert dd._lanza("SELECT 1", "k", "medium") == {"execution_id": "E1"}
    assert llamadas == ["sql/execute", "sql/execute", "query", "query/77/execute"]
    assert "HTTP 400: nope" in capsys.readouterr().out
