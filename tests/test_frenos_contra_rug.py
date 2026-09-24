"""Los frenos contra rugs valen para TODAS las fuentes, no para una.

EL FALLO QUE ARREGLAN, con nombre y fecha. FUCKETHAN, 24-sep 05:18: cinco
titulares, comprada en la punta de una vela vertical que fue de 20k a 43k de
market cap, cerrada a -91,9% cuarenta segundos despues. Se llevo 45,6 $ de una
cartera de 76.

Las dos protecciones que lo habrian parado --no entrar detras de una vela
verde enorme, y no comprar monedas movidas por cuatro carteras-- se habian
pedido el 23-sep y ESTABAN ESCRITAS. Lo que pasaba es que vivian dentro del
bloque `holders`, y cuando ese mismo dia la estrategia paso a la curva,
`holders` se apago... y ellas con el. La curva y Telegram llevaban desde
entonces comprando sin ninguna, y nada lo dijo: no hubo error, no hubo aviso,
el bot siguio funcionando perfectamente.

LA REGLA QUE SALE DE AHI y que estos tests fijan: un freno contra rugs no
puede vivir en una fuente. Las fuentes deciden QUE mirar; esto decide que no
se compra pase lo que pase y venga de donde venga.

Telegram es el caso que mas importa: no tiene NINGUN filtro propio: cualquier
direccion que alguien pegue en un canal se compra. Hasta ahora nada la miraba.
"""

# ruff: noqa: S101, SLF001, PLC0415, ANN003, TRY003

import asyncio
import types
from pathlib import Path

import yaml

from trading.universal_trader import UniversalTrader

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"


def _filters() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]


def _trader(**kw) -> UniversalTrader:
    t = UniversalTrader.__new__(UniversalTrader)
    f = _filters()
    t.max_subida_global = float(f.get("max_subida_global", 0))
    t.subida_seg_global = float(f.get("subida_seg_global", 10))
    t.min_compradores_global = int(f.get("min_compradores_global", 0))
    t._descartes = {}
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _moneda(sym: str = "FUCKETHAN") -> types.SimpleNamespace:
    return types.SimpleNamespace(mint="XTvHfakemint1111111111111111111111111pump",
                                 symbol=sym)


def _con_stream(monkeypatch, *, subida: float, compradores: int) -> None:  # noqa: ANN001
    """Pone al modulo `volumen` a contestar lo que diga el caso."""
    from monitoring import volumen

    monkeypatch.setattr(volumen, "subida", lambda *_a, **_k: subida)
    monkeypatch.setattr(
        volumen, "metricas_segunda_ola",
        lambda *_a, **_k: {"compradores": compradores},
    )


def _pasa(t: UniversalTrader, moneda) -> bool:  # noqa: ANN001
    return asyncio.run(t._pasa_los_frenos_de_rug(moneda))


# ------------------------------------------------- el caso que lo destapo


def test_fuckethan_no_se_habria_comprado(monkeypatch) -> None:  # noqa: ANN001
    """EL TEST QUE DA SENTIDO AL FICHERO.

    La moneda doblo de 20k a 43k de market cap en segundos --o sea una subida
    del 115%-- y la tenian cinco carteras. Con la configuracion de hoy tiene
    que caer, y da igual si viene de la curva o de un canal de Telegram.
    """
    _con_stream(monkeypatch, subida=1.15, compradores=5)
    assert not _pasa(_trader(), _moneda())


def test_cae_por_la_vela_aunque_haya_gente_de_sobra(monkeypatch) -> None:  # noqa: ANN001
    """Los dos frenos son independientes: cada uno tapa un agujero distinto.

    Una moneda con 200 compradores que acaba de doblar en diez segundos sigue
    siendo una mala entrada -- la presion compradora parece inmejorable justo
    en el peor momento.
    """
    _con_stream(monkeypatch, subida=1.15, compradores=200)
    t = _trader()
    assert not _pasa(t, _moneda())
    assert "detrás de una vela verde" in t._descartes


def test_cae_por_poca_gente_aunque_no_haya_subido(monkeypatch) -> None:  # noqa: ANN001
    """Y al reves: una moneda tranquila movida por cuatro carteras tampoco."""
    _con_stream(monkeypatch, subida=0.0, compradores=4)
    t = _trader()
    assert not _pasa(t, _moneda())
    assert "pocos compradores distintos" in t._descartes


def test_una_moneda_normal_si_pasa(monkeypatch) -> None:  # noqa: ANN001
    """LO QUE NO PUEDE PASAR: que el freno lo pare todo.

    Un filtro que descarta siempre deja el bot mudo, y eso se lee igual que
    "no llega nada" -- son problemas opuestos y ya costaron dos dias.
    """
    _con_stream(monkeypatch, subida=0.10, compradores=45)
    assert _pasa(_trader(), _moneda("NORMAL"))


# ------------------------------------------------- que no rompa nada


def test_sin_dato_de_compradores_no_se_bloquea(monkeypatch) -> None:  # noqa: ANN001
    """Quedarse sin dato NO es lo mismo que tener un dato malo.

    Si el contador falla y esto bloqueara, el bot dejaria de comprar entero
    por una averia del contador, no por las monedas. Ante la duda se compra,
    que es el comportamiento de antes, y queda en el log.
    """
    from monitoring import volumen

    monkeypatch.setattr(volumen, "subida", lambda *_a, **_k: 0.0)
    def _revienta(*_a, **_k):  # noqa: ANN002, ANN202
        raise RuntimeError("contador roto")
    monkeypatch.setattr(volumen, "metricas_segunda_ola", _revienta)
    assert _pasa(_trader(), _moneda())


def test_a_cero_el_freno_se_apaga(monkeypatch) -> None:  # noqa: ANN001
    """Cero significa apagado en toda esta configuracion. Si significara
    "cero de subida permitida" o "cero compradores minimos", poner 0 pasaria
    de desactivar el freno a dejarlo pasar todo o a bloquearlo todo."""
    _con_stream(monkeypatch, subida=5.0, compradores=1)
    t = _trader(max_subida_global=0.0, min_compradores_global=0)
    assert _pasa(t, _moneda())


# ------------------------------------------------- donde vive la config


def test_los_frenos_estan_en_el_nivel_comun_no_en_una_fuente() -> None:
    """LA REGLA, por escrito. Si alguien los vuelve a meter dentro de una
    fuente, el dia que esa fuente se apague se apagaran con ella y nadie se
    enterara -- que es exactamente lo que paso con FUCKETHAN."""
    f = _filters()
    assert f.get("max_subida_global", 0) > 0
    assert f.get("min_compradores_global", 0) > 0
    # Y no pueden estar SOLO dentro de una fuente que se pueda apagar.
    for fuente in ("curva_96", "telegram_calls", "holders"):
        bloque = f.get(fuente, {})
        assert "max_subida_global" not in bloque, fuente


def test_el_freno_se_aplica_en_la_puerta_de_todas_las_compras() -> None:
    """`_handle_token` es la unica por la que pasan todas las fuentes. Si la
    llamada se moviera dentro de una fuente concreta, las demas volverian a
    comprar sin mirar."""
    fuente = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    trozo = fuente[fuente.index("async def _handle_token"):]
    assert "await self._pasa_los_frenos_de_rug(token_info)" in trozo[:4000]


def test_el_yaml_llega_al_trader() -> None:
    """El no-op mas caro: una clave nueva que nadie lee. Todo pasa, el bot
    arranca sin quejarse, y sigue comprando exactamente igual que antes."""
    runner = (RAIZ / "src/bot_runner.py").read_text(encoding="utf-8")
    for clave in ("max_subida_global", "subida_seg_global", "min_compradores_global"):
        assert f'{clave}=cfg["filters"].get("{clave}"' in runner, clave
