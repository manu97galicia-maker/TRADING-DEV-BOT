"""6-nov: con todas las fuentes apagadas el bot no abria el escuchador.

El parte de descartes iba en la lista de fuentes y la lista nunca quedaba
vacia: el bot se quedaba esperando al parte, vivo y sin ver una moneda.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import yaml

from trading.universal_trader import UniversalTrader

ROOT = Path(__file__).resolve().parents[1]


def _trader_sin_fuentes(escuchado: list[str]) -> UniversalTrader:
    yo = object.__new__(UniversalTrader)
    for attr, _corto in UniversalTrader.FUENTES:
        setattr(yo, attr, {"enabled": False})
    yo.copy_wallets = {}
    yo.resumen_descartes_seg = 300.0
    yo.yolo_mode = True
    yo.match_string = None
    yo.bro_address = None

    async def _parte() -> None:
        await asyncio.sleep(3600)

    async def _cola() -> None:
        await asyncio.sleep(3600)

    async def _escuchar(_cb, _m, _b) -> None:
        escuchado.append("escuchando")

    async def _limpia() -> None:
        escuchado.append("cerrado")

    yo._vigilar_los_descartes = _parte
    yo._process_token_queue = _cola
    yo._cleanup_resources = _limpia
    yo.token_listener = SimpleNamespace(listen_for_tokens=_escuchar)
    return yo


def test_sin_fuentes_abre_el_escuchador() -> None:
    escuchado: list[str] = []
    yo = _trader_sin_fuentes(escuchado)

    async def _solo_la_decision() -> None:
        await asyncio.wait_for(UniversalTrader._fuentes_o_escuchador(yo), 2)

    asyncio.run(_solo_la_decision())
    assert escuchado == ["escuchando", "cerrado"]


def test_dev1_escucha_sin_parar_tras_la_primera_moneda() -> None:
    cfg = yaml.safe_load(
        (ROOT / "bots" / "bot-paper-dev1-bloque2.yaml").read_text(encoding="utf-8")
    )
    f = cfg["filters"]
    # Sin curva_96 las monedas llegan por el escuchador normal...
    assert (f.get("curva_96") or {}).get("enabled") is False
    # ...y sin yolo_mode miraria una sola moneda y se apagaria.
    assert f["yolo_mode"] is True
