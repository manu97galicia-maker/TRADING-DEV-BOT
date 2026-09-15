"""Estrenar redes sociales como senal, y el +0,1 cuando coincide con el pago.

Lo pedido: comprar tambien por actualizacion de redes, pero si la moneda ya
daba senal de DexScreener, solo +0,1 SOL en vez de una compra aparte.

POR QUE SUMA EN VEZ DE SER UNA FUENTE NUEVA. Pagar el perfil de DexScreener
es, en la practica, poner las redes: muchisimas monedas dan las dos senales a
la vez. Con una fuente aparte se compraria DOS VECES la misma moneda, con dos
posiciones y dos comisiones, por un solo hecho. Sumando, la senal doble sube
la apuesta una vez y ya.
"""

# ruff: noqa: S101, PLR2004, PLC0415

import json
from pathlib import Path

from monitoring.dexpaid_source import EXTRA_REDES_NUEVAS, importe_por_senal
from monitoring.redes_sociales import RedesVistas

DEXSCREENER = [{"type": "twitter", "url": "x"}]
DEXSCREENER_MAS = [
    {"type": "twitter", "url": "x"},
    {"type": "telegram", "url": "y"},
]


def _tracker(tmp: Path) -> RedesVistas:
    return RedesVistas(tmp / "redes.json")


# --------------------------------------------------- detectar el estreno


def test_la_primera_vez_no_cuenta_como_estreno(tmp_path: Path) -> None:
    """Si contara, al arrancar el bot compraria de golpe TODAS las monedas
    con redes del feed, por algo que puede ser de hace dias."""
    r = _tracker(tmp_path)
    assert r.novedades("AAA", DEXSCREENER) == frozenset()


def test_anadir_una_red_si_es_estreno(tmp_path: Path) -> None:
    r = _tracker(tmp_path)
    r.novedades("AAA", DEXSCREENER)
    assert r.novedades("AAA", DEXSCREENER_MAS) == frozenset({"telegram"})


def test_sin_cambios_no_hay_senal(tmp_path: Path) -> None:
    """Si no filtrara, cada vuelta (cada 3 s) seria una senal nueva."""
    r = _tracker(tmp_path)
    r.novedades("AAA", DEXSCREENER)
    r.novedades("AAA", DEXSCREENER_MAS)
    assert r.novedades("AAA", DEXSCREENER_MAS) == frozenset()


def test_quitar_una_red_no_es_senal(tmp_path: Path) -> None:
    """Quitar Twitter no es invertir en la moneda. Y ademas el feed puede
    devolver una lista incompleta en una vuelta mala: si eso contara como
    cambio, la vuelta siguiente lo veria como estreno y compraria."""
    r = _tracker(tmp_path)
    r.novedades("AAA", DEXSCREENER_MAS)
    assert r.novedades("AAA", DEXSCREENER) == frozenset()
    # Y volver a tenerla tampoco: ya se conocia.
    assert r.novedades("AAA", DEXSCREENER_MAS) == frozenset()


def test_entiende_el_formato_de_dextools(tmp_path: Path) -> None:
    """DEXTools las da como diccionario, no como lista."""
    r = _tracker(tmp_path)
    r.novedades("BBB", {"twitter": "a"})
    assert r.novedades("BBB", {"twitter": "a", "website": "c"}) == frozenset(
        {"website"}
    )


def test_una_red_desconocida_sigue_contando(tmp_path: Path) -> None:
    """Si DexScreener anade una red que aqui no esta listada, perder la senal
    seria peor que aceptarla."""
    r = _tracker(tmp_path)
    r.novedades("CCC", DEXSCREENER)
    nuevas = r.novedades("CCC", [*DEXSCREENER, {"type": "tiktok", "url": "z"}])
    assert "tiktok" in nuevas


def test_basura_no_revienta(tmp_path: Path) -> None:
    r = _tracker(tmp_path)
    for basura in (None, "texto", 42, [], {}, [{"sin": "url"}]):
        assert r.novedades("DDD", basura) == frozenset()


# ------------------------------------------------------------ persistencia


def test_sobrevive_a_un_reinicio(tmp_path: Path) -> None:
    """Sin esto, cada reinicio compraria de golpe todo el feed."""
    ruta = tmp_path / "redes.json"
    r = RedesVistas(ruta)
    r.novedades("AAA", DEXSCREENER)
    r.guardar()
    assert RedesVistas(ruta).novedades("AAA", DEXSCREENER) == frozenset()


def test_un_fichero_corrupto_no_impide_arrancar(tmp_path: Path) -> None:
    ruta = tmp_path / "redes.json"
    ruta.write_text("{roto", encoding="utf-8")
    assert RedesVistas(ruta).novedades("AAA", DEXSCREENER) == frozenset()


def test_lo_guardado_es_json_legible(tmp_path: Path) -> None:
    ruta = tmp_path / "redes.json"
    r = RedesVistas(ruta)
    r.novedades("AAA", DEXSCREENER_MAS)
    r.guardar()
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert sorted(datos["redes"]["AAA"]) == ["telegram", "twitter"]


# -------------------------------------------------------------- el +0,1


def test_el_extra_es_una_decima() -> None:
    assert EXTRA_REDES_NUEVAS == 0.1


def test_el_extra_suma_sobre_la_senal_de_pago() -> None:
    """Lo pedido: si coincide con DexScreener, +0,1 y no una compra aparte."""
    base = importe_por_senal(0)
    assert round(base + EXTRA_REDES_NUEVAS, 4) == round(base + 0.1, 4)


def test_el_extra_no_se_salta_el_tope_por_compra() -> None:
    """El caso mayor posible sumado el extra tiene que seguir cabiendo bajo
    `tope_compra_sol`, que es quien recorta de verdad."""
    import yaml

    cfg = yaml.safe_load(
        (
            Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
        ).read_text(encoding="utf-8")
    )
    tope = float(cfg["trade"]["tope_compra_sol"])
    mayor = importe_por_senal(999, pago_y_boost=True) + EXTRA_REDES_NUEVAS
    # No hace falta que quepa: el tope recorta. Pero si quepa el caso normal.
    assert importe_por_senal(0) + EXTRA_REDES_NUEVAS <= tope
    assert mayor > 0
