"""Que callout se publica: manda la calidad, no el reloj.

Aqui habia una ventana de 6 horas que me invente. El FAQ oficial de
pump.fun dice "There is no limit to callouts or your earnings"; lo que si
cambiaron en agosto es el reparto, primando "Quality >>> Quantity". Asi
que el freno correcto es la senal, no el tiempo.
"""

# ruff: noqa: S101

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from monitoring.callout_outbox import build_callout, elegir_para_publicar

AHORA = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _cola(tmp_path: Path, *entradas: dict) -> Path:
    fichero = tmp_path / "outbox.jsonl"
    fichero.write_text(
        "\n".join(json.dumps(e) for e in entradas) + "\n", encoding="utf-8"
    )
    return fichero


def _entrada(
    mint: str,
    fuerza: float,
    publicado: datetime | None = None,
    creado: datetime = AHORA,
) -> dict:
    return {
        "mint": mint,
        "created_at": creado.isoformat(),
        "signal_strength": fuerza,
        "published_at": publicado.isoformat() if publicado else None,
    }


def test_el_texto_es_el_pedido() -> None:
    assert build_callout("Barron", "Mint1").startswith("AI opportunity detected:")


def test_el_texto_declara_la_posicion() -> None:
    """Decir que se tiene posicion no es opcional."""
    assert "holds a position" in build_callout("Barron", "Mint1")


def test_sin_cola_no_hay_nada_que_publicar(tmp_path: Path) -> None:
    assert elegir_para_publicar(tmp_path / "no_existe.jsonl") is None


def test_elige_la_de_mayor_senal(tmp_path: Path) -> None:
    """La senal es lo que alguien pago por promocionar la moneda."""
    cola = _cola(
        tmp_path,
        _entrada("Floja", 100),
        _entrada("Fuerte", 900),
        _entrada("Media", 500),
    )

    elegida = elegir_para_publicar(cola)

    assert elegida is not None
    assert elegida["mint"] == "Fuerte"


def test_haber_publicado_hace_un_rato_no_bloquea(tmp_path: Path) -> None:
    """Lo que antes devolvia None: pump.fun no limita la cantidad."""
    cola = _cola(
        tmp_path,
        _entrada("Antigua", 900, publicado=AHORA - timedelta(minutes=5)),
        _entrada("Nueva", 800),
    )

    elegida = elegir_para_publicar(cola)

    assert elegida is not None
    assert elegida["mint"] == "Nueva"


def test_una_moneda_ya_llamada_no_se_repite(tmp_path: Path) -> None:
    """El segundo callout de lo mismo no trae seguidores nuevos."""
    cola = _cola(
        tmp_path,
        _entrada("Repe", 900, publicado=AHORA - timedelta(hours=1)),
        _entrada("Repe", 900),
    )

    assert elegir_para_publicar(cola) is None


def test_el_suelo_de_senal_descarta_lo_flojo(tmp_path: Path) -> None:
    """Un callout flojo baja tu media sin traer volumen: mejor no gastarlo."""
    cola = _cola(tmp_path, _entrada("Floja", 100), _entrada("Otra", 200))

    assert elegir_para_publicar(cola, senal_minima=500) is None


def test_el_suelo_deja_pasar_lo_que_llega(tmp_path: Path) -> None:
    cola = _cola(tmp_path, _entrada("Floja", 100), _entrada("Cara", 900))

    elegida = elegir_para_publicar(cola, senal_minima=500)

    assert elegida is not None
    assert elegida["mint"] == "Cara"


def test_a_igual_senal_gana_la_mas_reciente(tmp_path: Path) -> None:
    cola = _cola(
        tmp_path,
        _entrada("Vieja", 900, creado=AHORA - timedelta(hours=3)),
        _entrada("Reciente", 900),
    )

    elegida = elegir_para_publicar(cola)

    assert elegida is not None
    assert elegida["mint"] == "Reciente"


def test_una_linea_rota_no_tumba_la_cola(tmp_path: Path) -> None:
    fichero = tmp_path / "outbox.jsonl"
    fichero.write_text(
        "esto no es json\n" + json.dumps(_entrada("Buena", 700)) + "\n",
        encoding="utf-8",
    )

    elegida = elegir_para_publicar(fichero)

    assert elegida is not None
    assert elegida["mint"] == "Buena"
