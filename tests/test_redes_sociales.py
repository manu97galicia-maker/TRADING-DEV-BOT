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

from modelo_tamano import importe

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
    assert mayor > 0
    # Quien manda de verdad es el tope duro, que recorta CUALQUIER via justo
    # antes de firmar la compra.
    #
    # ANTES SE EXIGIA `min(mayor, tope) == tope`, o sea que el tope recortara
    # este caso. Se puso rojo al subir el tope a 2 SOL el 24-sep, y por una
    # razon que es buena noticia: el caso mayor de esta via son 0,568 SOL y
    # ya no llega al tope, asi que no hay nada que recortar. Exigir que
    # recorte era exigir que el tope fuera PEQUENO, que es lo contrario de
    # lo que este test deberia vigilar.
    #
    # Lo que importa es que ninguna via se cuele por encima del tope.
    assert min(mayor, tope) <= tope
    assert importe(mayor, {"fraccion_saldo": 0, "buy_amount": mayor, "tope_compra_sol": tope}) <= tope


def test_el_extra_de_redes_ya_no_cambia_nada_por_otro_motivo() -> None:
    """Sigue sin hacer nada, pero por una razon distinta y mas fuerte.

    ANTES: el tope duro estaba en el importe fijo, asi que el +0,1 SOL por
    estrenar redes quedaba entero por debajo del recorte.

    AHORA (19-sep): el tamano de la compra es el 25% del saldo libre, y ese
    calculo SUSTITUYE al importe que trae la senal -- se hace despues, dentro
    del cerrojo de gasto. Asi que da igual lo que sume el extra: nunca llega a
    aplicarse mientras `fraccion_saldo` sea mayor que cero.

    No hay dinero en riesgo. Lo que pasa es que la senal doble no significa
    nada, y recuperarla exigiria que el extra fuera un porcentaje de la compra
    en vez de una cantidad fija. Queda anotado.
    """
    import yaml

    cfg = yaml.safe_load(
        (
            Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
        ).read_text(encoding="utf-8")
    )
    # Con el importe FIJO el extra si podria sumar... pero la fuente de
    # titulares manda su propio importe en la senal (`signal_buy_amount`), y
    # ese pisa a cualquier calculo de la fuente de dexpaid --que ademas esta
    # apagada--. Asi que sigue sin cambiar nada, por una tercera via.
    assert not cfg["filters"]["buy_on_dex_paid"]["enabled"], (
        "la fuente del extra esta encendida: revisar si se queria"
    )
    assert EXTRA_REDES_NUEVAS > 0, "el extra sigue existiendo en el codigo"
