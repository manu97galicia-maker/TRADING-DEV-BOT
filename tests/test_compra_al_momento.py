"""Cuanto tarda el bot desde que ve la senal hasta que manda la compra.

LO PEDIDO: "cuando lo detecte, compra al momento".

NO se mide con un cronometro --eso dependeria de la maquina y del nodo, y un
test asi se pone rojo solo--. Se mide lo unico que de verdad manda: CUANTAS
VUELTAS AL NODO hay entre detectar y comprar. Cada vuelta a un RPC de Solana
son decenas de milisegundos, y van en serie: tres seguidas es tres veces la
espera de una.

LO QUE HABIA: `is_curve_complete()` (que por dentro solo llama a
`get_pool_state`), luego `get_pool_state()` otra vez sobre LA MISMA cuenta, y
luego `get_account_info(mint)`, que no dependia de ninguna de las dos y aun asi
esperaba su turno. Tres viajes en serie donde basta uno.
"""

# ruff: noqa: S101, ANN001, ANN202, ARG002

import asyncio
from pathlib import Path

import yaml

from trading.token_builder import build_pumpfun_token_info

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
MINT = "DBDqhnAi5MjaHsk1JyUonAtUUrmRBJPjJi3GdiBLpump"


class _Curva:
    """Curva viva y normal: con reservas y sin migrar."""

    def __init__(self) -> None:
        self.lecturas = 0
        self.orden: list[str] = []

    async def is_curve_complete(self, _pool):
        msg = "lectura duplicada de la curva"
        raise AssertionError(msg)

    async def get_pool_state(self, _pool, commitment=None, *, exigir_precio=True):
        self.lecturas += 1
        self.orden.append("curva:empieza")
        # Cede el control: si el otro viaje se lanzo a la vez, entra AQUI.
        await asyncio.sleep(0)
        self.orden.append("curva:acaba")
        return {
            "complete": False,
            "creator": "11111111111111111111111111111111",
            "is_mayhem_mode": False,
            "is_cashback_coin": False,
            "price_per_token": 0.000000031,
            "virtual_token_reserves": 1_000_000_000_000,
            "virtual_sol_reserves": 30_000_000_000,
            "token_total_supply": 1_000_000_000_000_000,
        }


class _Cliente:
    def __init__(self, orden: list[str]) -> None:
        self.orden = orden
        self.lecturas = 0

    async def get_account_info(self, _mint):
        self.lecturas += 1
        self.orden.append("mint:empieza")
        await asyncio.sleep(0)
        self.orden.append("mint:acaba")
        return type("Cuenta", (), {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"})()


class _Impls:
    def __init__(self, curva) -> None:
        self.curve_manager = curva
        self.address_provider = type(
            "AP", (), {
                "derive_pool_address": staticmethod(lambda _m: object()),
                "get_additional_accounts": staticmethod(lambda _t: {}),
            },
        )()


def _construye() -> tuple[_Curva, _Cliente]:
    curva = _Curva()
    orden: list[str] = []
    cliente = _Cliente(orden)
    curva.orden = orden
    asyncio.run(
        build_pumpfun_token_info(MINT, _Impls(curva), cliente, permitir_migradas=False)
    )
    return curva, cliente


def test_la_curva_se_lee_una_sola_vez() -> None:
    """Dos lecturas de la misma cuenta es un viaje al nodo tirado a la basura.

    El `is_curve_complete` de arriba salta si alguien lo vuelve a llamar: no
    hace falta, porque el estado que se lee ya trae el campo `complete`.
    """
    curva, _ = _construye()
    assert curva.lecturas == 1


def test_la_curva_y_el_mint_se_piden_a_la_vez() -> None:
    """No dependen el uno del otro, asi que no tienen por que esperarse.

    Si fueran en serie, el orden seria empieza/acaba, empieza/acaba. Yendo a la
    vez, los dos empiezan antes de que acabe ninguno.
    """
    curva, cliente = _construye()
    assert cliente.lecturas == 1
    empiezan = curva.orden[:2]
    assert set(empiezan) == {"curva:empieza", "mint:empieza"}, (
        f"van en serie, no a la vez: {curva.orden}"
    )


def test_no_hay_esperas_artificiales_antes_de_comprar() -> None:
    """La pausa "para que se estabilice la curva" tiene que estar a cero.

    Es un `asyncio.sleep` literal entre detectar y comprar: con el valor por
    defecto (15 s) la moneda que se detecta a los 90 titulares se compraria
    quince segundos despues, que en pump.fun es otra moneda distinta.
    """


    cfg = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml")
        .read_text(encoding="utf-8")
    )
    assert cfg.get("retries", {}).get("wait_after_creation", 15) == 0
    assert cfg["filters"]["max_buy_age"] == 0
    assert cfg.get("retries", {}).get("wait_before_new_token", 0) == 0
