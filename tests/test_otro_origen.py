"""Regimen aparte para monedas que NO nacieron en pump.fun.

No se les puede aplicar el mismo rasero: alli el supply no es el fijo de
pump.fun, no hay curva ni creador que mirar, y aparecen cosas que en
pump.fun no pueden pasar (congelacion, comision por transferencia, supply
mintable). Se prueban con cupo y corte rapido, SIN tocar a las de casa.
"""

# ruff: noqa: S101, SLF001, PLR2004

from pathlib import Path
from types import SimpleNamespace

import yaml

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
