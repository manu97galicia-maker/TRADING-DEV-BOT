"""La entrada promocionada acepta pump.fun y PumpSwap sin banda de curva."""

# ruff: noqa: S101, SLF001

from types import SimpleNamespace

from monitoring.dexpaid_source import DexPaidSource
from trading.universal_trader import UniversalTrader


def _trader() -> UniversalTrader:
    trader = UniversalTrader.__new__(UniversalTrader)
    trader.paid_promotion_entry = {
        "enabled": True,
        "sources": [
            "dexscreener",
            "dextools_hotpools",
            "dextools_nitro",
            "cryptolistings",
        ],
    }
    return trader


def _token(source: str) -> SimpleNamespace:
    return SimpleNamespace(
        mint="Mint111111111111111111111111111111111pump",
        symbol="TEST",
        additional_data={"acquisition_source": source},
    )


def test_reconoce_solo_las_fuentes_pagadas_configuradas() -> None:
    trader = _trader()
    assert trader._es_promocion_pagada(_token("dexscreener"))
    assert trader._es_promocion_pagada(_token("dextools_nitro"))
    assert trader._es_promocion_pagada(_token("cryptolistings"))
    assert not trader._es_promocion_pagada(_token("Wagbocooks"))


def test_un_boost_solo_no_activa_el_feed_pagado() -> None:
    source = DexPaidSource(None, None, signal_types=("paid",))
    assert not source._interesa(
        {"mint": "boostpump", "signal_type": "boost", "boosts": 500}
    )
