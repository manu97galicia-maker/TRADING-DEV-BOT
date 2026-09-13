"""Cuanto tarda el bot en llegar a una moneda recien pagada.

El fallo: lo caro de cada candidata --consultar sus ordenes y sus datos de
mercado-- se hacia sobre las 90 que devuelven los feeds, y quien llamaba
descartaba DESPUES ~85 por repetidas o sin senal nueva. O sea: se pagaba el
precio completo por monedas que ya se sabia que no se iban a comprar.

Con el limitador a 0,8 peticiones/s eso convertia una vuelta de 5 segundos
en una de 25-80, y una moneda recien pagada podia esperar mas de un minuto.
"""

# ruff: noqa: S101, SLF001, PLR2004

import asyncio
from collections.abc import Callable
from typing import Any

from dashboard.dexpaid_feed import _fusionar_feeds, _resumen, get_candidates
from dexscreener.client import DexScreenerClient
from monitoring.dexpaid_source import DexPaidSource


class _FeedEspia(DexScreenerClient):
    """Cuenta las consultas CARAS: las que se hacen una por candidata."""

    def __init__(self, n: int) -> None:
        super().__init__()
        self._n = n
        self.consultas_caras = 0

    async def get_latest_boosts(self) -> list:
        return [
            {"chainId": "solana", "tokenAddress": f"m{i}pump", "totalAmount": 100}
            for i in range(self._n)
        ]

    async def get_latest_token_profiles(self) -> list:
        return []

    async def get_market_data(self, _mint: str, _chain: str = "solana") -> None:
        self.consultas_caras += 1

    async def close(self) -> None:
        pass


def _caras(interesa: Callable[[dict[str, Any]], bool] | None) -> int:
    espia = _FeedEspia(30)
    asyncio.run(
        get_candidates(limit=30, dex=espia, check_locked=False, interesa=interesa)
    )
    return espia.consultas_caras


def test_sin_criba_se_consultan_todas() -> None:
    """El comportamiento de antes, que es el que hay que batir."""
    assert _caras(None) == 30


def test_la_criba_evita_las_consultas_caras() -> None:
    """Lo que se descarta no debe costar ni una peticion."""
    assert _caras(lambda _r: False) == 0


def test_la_criba_deja_pasar_lo_que_interesa() -> None:
    def interesa(resumen: dict[str, Any]) -> bool:
        return resumen["mint"] in {"m1pump", "m2pump"}

    assert _caras(interesa) == 2


def test_la_criba_recibe_lo_que_necesita_sin_pedir_nada() -> None:
    """Decidir no puede costar una llamada: todo sale ya del feed."""
    resumen = _resumen("m1pump", {"_from_boosts": True, "totalAmount": 750})
    assert resumen["mint"] == "m1pump"
    assert resumen["signal_type"] == "boost"
    assert resumen["boosts"] == 750


def test_un_pago_sin_boost_se_marca_como_tal() -> None:
    resumen = _resumen("m2pump", {"_from_profiles": True})
    assert resumen["signal_type"] == "paid"


def test_la_fuente_criba_las_ya_vistas() -> None:
    fuente = DexPaidSource(object(), object())
    fuente._seen.add("yavista")
    assert fuente._interesa({"mint": "yavista", "signal_type": "boost"}) is False


def test_la_fuente_deja_pasar_un_boost_nuevo() -> None:
    fuente = DexPaidSource(object(), object())
    resumen = {"mint": "nueva", "signal_type": "boost", "boosts": 500}
    assert fuente._interesa(resumen) is True


def test_el_mismo_boost_repetido_no_es_senal() -> None:
    """Seguir teniendo 500 boosts es un estado, no un suceso."""
    fuente = DexPaidSource(object(), object())
    resumen = {"mint": "nueva", "signal_type": "boost", "boosts": 500}
    fuente._interesa(resumen)
    assert fuente._interesa(dict(resumen)) is False


def test_un_boost_que_sube_si_es_senal() -> None:
    fuente = DexPaidSource(object(), object())
    fuente._interesa({"mint": "n", "signal_type": "boost", "boosts": 300})
    assert fuente._interesa({"mint": "n", "signal_type": "boost", "boosts": 900})


def test_fusionar_se_queda_con_el_boost_mas_alto() -> None:
    fusion = _fusionar_feeds(
        [
            {"tokenAddress": "m1", "totalAmount": 100, "_from_boosts": True},
            {"tokenAddress": "m1", "totalAmount": 900, "_from_profiles": True},
        ]
    )
    assert fusion["m1"]["totalAmount"] == 900
    assert fusion["m1"]["_from_boosts"] is True
    assert fusion["m1"]["_from_profiles"] is True
