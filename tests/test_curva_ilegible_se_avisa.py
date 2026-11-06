"""6-nov: si no se puede leer la curva mientras se espera, el log lo dice.

Antes el fallo se reintentaba en silencio hasta agotar la ventana ("no
cumplio en 8s" sin motivo). Paso con GTA 6 Coin: 85 SOL en el primer bloque,
la curva ya completa.
"""

# ruff: noqa: S101

from pathlib import Path

FUENTE = (Path(__file__).resolve().parents[1] / "src/trading/universal_trader.py").read_text(
    encoding="utf-8"
)


def test_el_fallo_de_la_curva_se_apunta_una_vez_y_sin_claves() -> None:
    tramo = FUENTE[FUENTE.index("except Exception as _e_curva"):][:1200]
    assert "if not _motivo_curva:" in tramo
    assert "no se pudo leer su curva" in tramo
    assert "***" in tramo  # la clave de una URL se tapa


def test_el_descarte_por_ventana_dice_si_la_curva_fallo() -> None:
    assert 'f", curva ilegible: {_motivo_curva}" if _motivo_curva else ""' in FUENTE


def test_la_tapa_de_claves_funciona() -> None:
    import re

    tramo = FUENTE[FUENTE.index("_motivo_curva = re.sub("):][:300]
    patron = re.search(r'r"(\(api.*?)",', tramo).group(1)
    texto = re.sub(patron, r"\1***", "Error: https://n/?api-key=SECRETO&x=1", flags=re.IGNORECASE)
    assert "SECRETO" not in texto
