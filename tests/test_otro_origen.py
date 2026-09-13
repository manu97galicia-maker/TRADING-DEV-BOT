"""Regimen aparte para monedas que NO nacieron en pump.fun.

No se les puede aplicar el mismo rasero: alli el supply no es el fijo de
pump.fun, no hay curva ni creador que mirar, y aparecen cosas que en
pump.fun no pueden pasar (congelacion, comision por transferencia, supply
mintable). Se prueban con cupo y corte rapido, SIN tocar a las de casa.
"""

# ruff: noqa: S101, SLF001, PLR2004, PLC0415

from pathlib import Path
from types import SimpleNamespace

import yaml

from monitoring.dexpaid_source import DexPaidSource
from trading.universal_trader import UniversalTrader

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _trader(cupo: int = 3, perdidas: int = 2) -> UniversalTrader:
    t = UniversalTrader.__new__(UniversalTrader)
    t.buy_on_dex_paid = {
        "otros_max_compras": cupo,
        "otros_parar_tras_perdidas": perdidas,
    }
    t._compras_otro_origen = 0
    t._perdidas_otro_origen = 0
    return t


def _moneda(origen: str | None) -> SimpleNamespace:
    datos = {"origen": origen} if origen else {}
    return SimpleNamespace(symbol="TEST", additional_data=datos)


def test_una_de_pumpfun_no_pasa_por_este_regimen() -> None:
    """Lo que se pidio: las de casa siguen exactamente como estaban."""
    trader = _trader()
    for _ in range(20):
        assert trader._otro_origen_agotado(_moneda(None)) is False
    assert trader._compras_otro_origen == 0


def test_el_cupo_deja_pasar_tres_y_corta_la_cuarta() -> None:
    trader = _trader(cupo=3)
    assert [trader._otro_origen_agotado(_moneda("otro")) for _ in range(4)] == [
        False,
        False,
        False,
        True,
    ]


def test_dos_perdidas_cortan_las_de_fuera() -> None:
    trader = _trader(cupo=99, perdidas=2)
    trader._otro_origen_agotado(_moneda("otro"))
    for _ in range(2):
        trader._apunta_cierre_otro_origen(_moneda("otro"), -0.05)
    assert trader._otro_origen_agotado(_moneda("otro")) is True


def test_una_perdida_sola_no_corta() -> None:
    trader = _trader(cupo=99, perdidas=2)
    trader._apunta_cierre_otro_origen(_moneda("otro"), -0.05)
    assert trader._otro_origen_agotado(_moneda("otro")) is False


def test_las_ganancias_no_cuentan_como_perdida() -> None:
    trader = _trader(cupo=99, perdidas=2)
    for _ in range(5):
        trader._apunta_cierre_otro_origen(_moneda("otro"), +0.10)
    assert trader._perdidas_otro_origen == 0


def test_una_perdida_de_pumpfun_no_corta_el_otro_regimen() -> None:
    """Los dos contadores son independientes: es el punto del cambio."""
    trader = _trader(cupo=99, perdidas=2)
    for _ in range(10):
        trader._apunta_cierre_otro_origen(_moneda(None), -0.50)
    assert trader._perdidas_otro_origen == 0
    assert trader._otro_origen_agotado(_moneda("otro")) is False


def test_parado_por_perdidas_manda_sobre_el_cupo() -> None:
    """Aunque queden compras de cupo, dos perdidas cierran el grifo."""
    trader = _trader(cupo=99, perdidas=2)
    for _ in range(2):
        trader._apunta_cierre_otro_origen(_moneda("otro"), -0.01)
    assert trader._otro_origen_agotado(_moneda("otro")) is True


def test_la_config_pide_mas_a_las_de_fuera_que_a_las_de_casa() -> None:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]
    de_casa = cfg["max_holder_pct"]
    de_fuera = cfg["buy_on_dex_paid"]["otros_max_holder_pct"]
    assert de_fuera == 0.05
    assert de_fuera < de_casa


def test_se_exige_liquidez_bloqueada_a_las_de_fuera() -> None:
    """Sin curva ni creador, el bloqueo es la unica red que queda."""
    dp = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"][
        "buy_on_dex_paid"
    ]
    assert dp["incluir_cualquier_origen"] is True
    assert dp["min_lp_locked_pct"] >= 50


def test_a_las_de_fuera_se_les_arriesga_menos() -> None:
    """Mismo criterio de senal, apuesta mas pequena: son las que menos red
    tienen (sin creador, sin curva, con el supply sin garantizar)."""
    from monitoring.dexpaid_source import importe_por_senal

    for boosts in (0, 50, 51, 900):
        assert importe_por_senal(boosts, de_fuera=True) < importe_por_senal(boosts)


def test_el_factor_se_aplica_al_importe_de_la_senal() -> None:
    """El descuento por procedencia va DENTRO de la tabla de importes."""
    from monitoring.dexpaid_source import importe_por_senal

    assert importe_por_senal(0, de_fuera=True) < importe_por_senal(0)


def test_una_de_pumpfun_no_lleva_factor() -> None:
    """Las de casa siguen con el importe entero."""
    fuente = DexPaidSource(object(), object())
    assert fuente.otros_factor_importe == 1.0


def test_treinta_boosts_suben_la_apuesta() -> None:
    """Pagar boosts es compromiso con la moneda: se apuesta mas."""
    from monitoring.dexpaid_source import factor_por_boosts

    assert factor_por_boosts(29, 30, 1.3333) == 1.0
    assert factor_por_boosts(30, 30, 1.3333) == 1.3333
    assert factor_por_boosts(900, 30, 1.3333) == 1.3333


def test_el_premio_por_boosts_vale_para_cualquier_origen() -> None:
    """Lo pedido: sean o no de Raydium. Los dos factores se multiplican, asi
    que una de fuera con boosts sube pero sigue por debajo de una de casa."""
    from monitoring.dexpaid_source import factor_por_boosts

    premio = factor_por_boosts(100, 30, 1.3333)
    de_casa = 1.0 * premio
    de_fuera = 0.6667 * premio
    assert de_fuera > 0.6667  # sube respecto a una de fuera sin boosts
    assert de_fuera < de_casa  # pero no alcanza a una de casa


def test_sin_configurar_el_premio_no_hace_nada() -> None:
    from monitoring.dexpaid_source import factor_por_boosts

    assert factor_por_boosts(900, 0, 1.5) == 1.0
    assert factor_por_boosts(900, 30, 0) == 1.0


def test_el_premio_por_boosts_no_se_cuenta_dos_veces() -> None:
    """Los tramos ya suben el importe con los boosts. Dejar encima el premio
    del 1,33 seria contar lo mismo dos veces."""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"][
        "buy_on_dex_paid"
    ]
    assert cfg["boost_extra_umbral"] == 0


def test_una_de_raydium_necesita_market_cap_minimo() -> None:
    """Sin curva ni creador que mirar, un market cap ridiculo es la senal
    mas clara de que no hay nadie detras."""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"][
        "buy_on_dex_paid"
    ]
    assert cfg["otros_min_market_cap"] == 6000


def test_sin_market_cap_conocido_se_descarta() -> None:
    """Ante la duda no se compra: no hay motivo para asumir lo mejor en una
    moneda de la que no se sabe nada mas."""
    fuente = DexPaidSource(object(), object(), otros_min_market_cap=6000)
    assert fuente._pasa_market_cap({"mint": "x"}) is False
    assert fuente._pasa_market_cap({"mint": "x", "market_cap": 5999}) is False
    assert fuente._pasa_market_cap({"mint": "x", "market_cap": 6000}) is True


def test_sin_minimo_configurado_pasa_cualquiera() -> None:
    fuente = DexPaidSource(object(), object(), otros_min_market_cap=0)
    assert fuente._pasa_market_cap({"mint": "x"}) is True
