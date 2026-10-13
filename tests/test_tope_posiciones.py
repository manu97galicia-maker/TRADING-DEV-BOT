"""El tope duro de posiciones y el aviso de que el nodo no da mas.

Con `max_posiciones: 0` manda el saldo. El numero que queda dentro del
codigo es solo un tope de seguridad, y estaba en 20 sin nombre: con "0"
configurado se convertia en un limite invisible.
"""

# ruff: noqa: S101, SLF001, PLR2004

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from trading.universal_trader import TOPE_DURO_POSICIONES, UniversalTrader

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-unica123-real.yaml"


def _trader(abiertas: int, intervalo: float, max_rps: float) -> UniversalTrader:
    t = UniversalTrader.__new__(UniversalTrader)
    t.price_check_interval = intervalo
    t.solana_client = SimpleNamespace(max_rps=max_rps)
    t._open_position_paths = lambda: [Path(f"p{i}.json") for i in range(abiertas)]
    return t


def test_el_tope_duro_deja_sitio_de_sobra() -> None:
    """Con 0,28 por compra, 40 posiciones son ~11 SOL: no estorba a nadie."""
    assert TOPE_DURO_POSICIONES > 20


def test_sin_max_posiciones_se_usa_el_tope_duro() -> None:
    """Es lo que hace que "0" signifique "lo que aguante la cartera"."""
    assert asyncio.Semaphore(int(0 or TOPE_DURO_POSICIONES))._value == (
        TOPE_DURO_POSICIONES
    )


def test_un_max_posiciones_explicito_sigue_mandando() -> None:
    assert asyncio.Semaphore(int(3 or TOPE_DURO_POSICIONES))._value == 3


def test_c123_sin_techo_mc_y_con_dos_posiciones() -> None:
    """DOS A LA VEZ, NO DOS EN TOTAL. Pedido el 13-oct con esas palabras:
    "maximo de compras simultaneas son dos pero no que solo dos".

    HABIA TRES MANDOS Y DOS ERAN CUPOS TOTALES, que es lo que se confundia:

      trade.max_posiciones  -> cuantas abiertas A LA VEZ. Este es el pedido.
      trade.max_real_buys   -> cupo TOTAL del proceso. Con `reiniciar_tanda`
                               sin poner (= False) es un tope de POR VIDA:
                               dos compras y el bot no vuelve a comprar
                               hasta reiniciarse.
      curva_96.max_compras  -> cupo TOTAL de la fuente, misma trampa.

    Los dos cupos pasan a 0 (= sin limite; el codigo hace `if cupo > 0` y
    `if self.max_real_buys and ...`). Queda uno solo decidiendo.

    POR QUE ESTE TEST IMPORTA: los dos cupos paran EN SILENCIO. El de la
    fuente escribe "cupo agotado" una vez y el del proceso "tanda
    completada"; despues el bot parece vivo y no compra nada. Este repo
    tiene esa parada documentada como "ya costo dias".
    """
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert cfg["trade"]["max_posiciones"] == 2, "dos posiciones a la vez"
    assert cfg["trade"]["max_real_buys"] == 0, (
        "max_real_buys es un cupo TOTAL, no simultaneas: a 0 o el bot se "
        "para solo tras esas compras y no lo vuelve a decir"
    )
    assert cfg["filters"]["curva_96"]["max_compras"] == 0, (
        "max_compras es el cupo total de la fuente: a 0 o se calla tras "
        "agotarlo"
    )
    assert cfg["trade"]["min_reserve_sol"] == 0.03
    assert cfg["filters"]["max_entry_mc"] == 0


def test_avisa_cuando_hay_mas_posiciones_que_nodo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """15 consultas/s a 1s dan para vigilar ~7; con 8 abiertas hay que avisar."""
    trader = _trader(abiertas=8, intervalo=1, max_rps=15)
    trader._avisa_si_el_nodo_no_da_mas()
    assert "NODO AL LIMITE" in caplog.text


def test_no_avisa_cuando_el_nodo_va_sobrado(
    caplog: pytest.LogCaptureFixture,
) -> None:
    trader = _trader(abiertas=2, intervalo=1, max_rps=15)
    trader._avisa_si_el_nodo_no_da_mas()
    assert "NODO AL LIMITE" not in caplog.text


def test_un_intervalo_mas_largo_permite_mas_posiciones(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """La salida recomendada: vigilar cada 5s da para ~37 posiciones."""
    trader = _trader(abiertas=8, intervalo=5, max_rps=15)
    trader._avisa_si_el_nodo_no_da_mas()
    assert "NODO AL LIMITE" not in caplog.text


def test_sin_datos_del_nodo_no_se_inventa_un_aviso(
    caplog: pytest.LogCaptureFixture,
) -> None:
    trader = _trader(abiertas=50, intervalo=0, max_rps=0)
    trader._avisa_si_el_nodo_no_da_mas()
    assert "NODO AL LIMITE" not in caplog.text


def test_contar_posiciones_no_revienta_sin_disco() -> None:
    """El contador no puede tumbar una compra si el disco falla."""
    trader = UniversalTrader.__new__(UniversalTrader)

    def _explota() -> list:
        raise OSError

    trader._open_position_paths = _explota
    assert trader._posiciones_abiertas() == 0
