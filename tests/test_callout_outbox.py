"""Tests for post-buy callout generation."""

# ruff: noqa: S101, PLC0415

import json
from pathlib import Path

from monitoring.callout_outbox import build_callout, enqueue_callout


def test_build_callout_contains_identity_and_disclosure() -> None:
    message = build_callout("Test Coin", "MintAddress")

    assert "AI opportunity detected: Test Coin" in message
    assert "Contract address: MintAddress" in message
    assert "holds a position" in message


def test_enqueue_callout_writes_pending_jsonl(tmp_path: Path) -> None:
    outbox = tmp_path / "callouts.jsonl"

    enqueue_callout("Test Coin", "MintAddress", "Signature", outbox)

    entry = json.loads(outbox.read_text(encoding="utf-8"))
    assert entry["mint"] == "MintAddress"
    assert entry["buy_signature"] == "Signature"
    assert entry["status"] == "pending_authorized_publisher"


def test_la_cola_no_usa_apis_de_python_3_11() -> None:
    """El droplet corre 3.10 y `datetime.UTC` es de 3.11.

    Con el import roto, callout_outbox reventaba al cargarse y se perdia el
    callout de CADA compra. Se vio en produccion el 12-sep 21:00:22:

        ImportError: cannot import name 'UTC' from 'datetime'

    El proyecto declara requires-python = ">=3.10", asi que usar una API de
    3.11 es un fallo aunque en el portatil de turno funcione.
    """
    import ast
    from pathlib import Path

    ruta = Path(__file__).resolve().parent.parent / "src/monitoring/callout_outbox.py"
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    importados = {
        alias.name
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.ImportFrom) and nodo.module == "datetime"
        for alias in nodo.names
    }
    assert "UTC" not in importados, (
        "datetime.UTC es de Python 3.11; usa timezone.utc (requires-python >=3.10)"
    )
