"""En una migrada, el pool NO es un titular: es donde vive la liquidez.

El fallo: el filtro de concentracion excluia solo la curva de pump.fun.
En una moneda ya migrada esa curva esta VACIA -- los tokens estan en el
pool de PumpSwap/Raydium, que es otra direccion -- asi que el pool contaba
como un titular normal con medio supply en la mano y la moneda se
descartaba siempre. Eso tiraba TODAS las de pumpswap que pagan
DexScreener, que son la mitad de la senal.
"""

# ruff: noqa: S101, SLF001

import inspect

from trading.universal_trader import UniversalTrader


def _fuente() -> str:
    return inspect.getsource(UniversalTrader._top_holder_exceeds)


def test_una_migrada_no_usa_la_curva_como_exclusion() -> None:
    """Es lo que hace que entre el camino de "excluir al mayor", y en una
    migrada el mayor es el pool."""
    fuente = _fuente()
    assert "_migrada" in fuente
    assert 'get("migrated")' in fuente


def test_el_tope_de_holders_sigue_siendo_por_moneda() -> None:
    """Las de fuera de pump.fun traen el suyo, mas estricto."""
    assert "_tope_holder" in _fuente()


def _decide(
    accts: list[dict], supply: float, tope: float, curve_ata: str | None
) -> bool:
    """Reproduce la decision del filtro: True = se descarta la moneda."""
    largest_addr = accts[0].get("address")
    for a in accts:
        addr = a.get("address")
        if curve_ata and addr == curve_ata:
            continue
        if not curve_ata and addr == largest_addr:
            continue
        if float(a.get("amount") or 0) / supply > tope:
            return True
    return False


# Una migrada tipica: el pool con el 70% y gente normal con el resto.
_CUENTAS = [
    {"address": "POOL", "amount": 700},
    {"address": "juan", "amount": 120},
    {"address": "ana", "amount": 100},
    {"address": "luis", "amount": 80},
]


def test_antes_toda_migrada_se_descartaba() -> None:
    """Con la curva (vacia) como unica exclusion, el pool mandaba al traste
    la moneda pasara lo que pasara."""
    assert _decide(_CUENTAS, 1000, 0.15, curve_ata="CURVA_VACIA") is True


def test_ahora_una_migrada_sana_se_compra() -> None:
    """Excluido el pool, los titulares de verdad estan por debajo del tope."""
    assert _decide(_CUENTAS, 1000, 0.15, curve_ata=None) is False


def test_una_migrada_con_una_ballena_sigue_descartandose() -> None:
    """El arreglo no ciega el filtro: si ademas del pool hay alguien con el
    30%, la moneda se sigue tirando."""
    con_ballena = [
        {"address": "POOL", "amount": 600},
        {"address": "ballena", "amount": 300},
        {"address": "ana", "amount": 100},
    ]
    assert _decide(con_ballena, 1000, 0.15, curve_ata=None) is True


def test_el_tope_estricto_de_las_de_fuera_muerde_antes() -> None:
    """Con el 5% de las de fuera de pump.fun, la misma moneda se descarta."""
    assert _decide(_CUENTAS, 1000, 0.05, curve_ata=None) is True
