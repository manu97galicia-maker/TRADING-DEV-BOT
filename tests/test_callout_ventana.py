"""Solo cabe un callout cada 6 horas: hay que elegir, no vaciar la cola."""

# ruff: noqa: S101

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from monitoring.callout_outbox import build_callout, elegir_para_publicar

AHORA = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _cola(tmp_path: Path, *entradas: dict) -> Path:
    fichero = tmp_path / "outbox.jsonl"
    fichero.write_text(
        "\n".join(json.dumps(e) for e in entradas) + "\n", encoding="utf-8"
    )
    return fichero


def _entrada(mint: str, fuerza: float, publicado: datetime | None = None) -> dict:
    return {
        "mint": mint,
        "created_at": AHORA.isoformat(),
        "signal_strength": fuerza,
        "published_at": publicado.isoformat() if publicado else None,
    }


def test_el_texto_es_el_pedido() -> None:
    assert build_callout("Barron", "Mint1").startswith("AI opportunity detected:")


def test_el_texto_declara_la_posicion() -> None:
    """Decir que se tiene posicion no es opcional."""
    assert "holds a position" in build_callout("Barron", "Mint1")


def test_sin_cola_no_hay_nada_que_publicar(tmp_path: Path) -> None:
    assert elegir_para_publicar(tmp_path / "no_existe.jsonl", AHORA) is None


def test_elige_la_de_mayor_senal(tmp_path: Path) -> None:
    cola = _cola(
        tmp_path,
        _entrada("Floja", 100),
        _entrada("Fuerte", 900),
        _entrada("Media", 500),
    )

    elegida = elegir_para_publicar(cola, AHORA)

    assert elegida is not None
    assert elegida["mint"] == "Fuerte"


def test_si_ya_se_publico_en_la_ventana_no_toca(tmp_path: Path) -> None:
    cola = _cola(
        tmp_path,
        _entrada("Antigua", 900, publicado=AHORA - timedelta(hours=2)),
        _entrada("Nueva", 800),
    )

    assert elegir_para_publicar(cola, AHORA) is None


def test_pasadas_las_6_horas_vuelve_a_tocar(tmp_path: Path) -> None:
    cola = _cola(
        tmp_path,
        _entrada("Antigua", 900, publicado=AHORA - timedelta(hours=7)),
        _entrada("Nueva", 800),
    )

    elegida = elegir_para_publicar(cola, AHORA)

    assert elegida is not None
    assert elegida["mint"] == "Nueva"


def test_una_linea_rota_no_tumba_la_cola(tmp_path: Path) -> None:
    fichero = tmp_path / "outbox.jsonl"
    fichero.write_text(
        "esto no es json\n" + json.dumps(_entrada("Buena", 700)) + "\n",
        encoding="utf-8",
    )

    elegida = elegir_para_publicar(fichero, AHORA)

    assert elegida is not None
    assert elegida["mint"] == "Buena"
