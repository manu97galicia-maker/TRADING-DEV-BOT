"""Al reiniciar, las posiciones abiertas se retoman en vez de abandonarse.

EL FALLO QUE ARREGLA, que es el que hacia que "no salten" el take profit ni
el stop loss: al arrancar, el bot leia el marcador de cada posicion abierta,
avisaba de "bolsa huerfana", BORRABA el marcador y no volvia a mirarla. La
moneda se quedaba en la wallet sin stop loss y sin escalera.

Con un reinicio eso ya es malo. Con diez reinicios en una tarde --que es lo
que paso el 13 y 14 de septiembre-- significa que la estrategia no estuvo
gestionando casi nada: cada posicion abierta en el momento de cada reinicio
se quedo sola para siempre.

Lo que se temia quien lo dejo asi era reconstruir mal la venta. Eso se evita
sin renunciar al stop: la cantidad se lee de la CADENA, no del fichero.
"""

# ruff: noqa: S101, SLF001, ANN001, ANN202, TRY003

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

from trading.universal_trader import UniversalTrader

FUENTE = Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"


def _fuente() -> str:
    return FUENTE.read_text(encoding="utf-8")


def _metodo(nombre: str) -> str:
    """El cuerpo entero de un metodo, hasta el siguiente del mismo nivel.

    Antes esto cortaba a N caracteres a ojo y un test se puso rojo solo por
    anadir codigo delante de lo que buscaba. Un test no puede depender de
    cuanto ocupa el metodo.
    """
    texto = _fuente()
    inicio = texto.index(f"def {nombre}(")
    resto = texto[inicio:]
    siguiente = re.search(r"\n    (?:async )?def ", resto[1:])
    return resto[: siguiente.start() + 1] if siguiente else resto


# ------------------------------------------------------- el arranque retoma


def test_el_arranque_retoma_en_vez_de_abandonar() -> None:
    """Lo que decide todo: que `start` llame a retomar."""
    texto = _fuente()
    inicio = texto.index("async def start(self)")
    cabecera = texto[inicio : inicio + 1800]
    assert "await self._reanudar_posiciones()" in cabecera, (
        "el arranque no retoma las posiciones: se quedarian sin stop loss"
    )


def test_ya_no_se_borra_el_marcador_al_arrancar() -> None:
    """Borrarlo era lo que hacia la perdida irreversible: sin marcador, ni
    siquiera quedaba constancia de que habia una posicion que vigilar."""
    texto = _fuente()
    inicio = texto.index("async def start(self)")
    cabecera = texto[inicio : inicio + 1800]
    assert "_check_orphan_position()" not in cabecera


def test_la_cantidad_sale_de_la_cadena_no_del_fichero() -> None:
    """Si se vendio algo a mano con el bot parado, el fichero miente."""
    cuerpo = _metodo("_reanudar_una")
    assert "get_token_account_balance" in cuerpo
    # El `amount` del marcador NO se usa como cantidad a vender.
    assert 'rec.get("amount")' not in cuerpo


def test_el_precio_de_entrada_si_sale_del_marcador() -> None:
    """Es un hecho historico: no se puede consultar en ningun sitio, y sin el
    no hay stop ni escalera."""
    cuerpo = _metodo("_reanudar_una")
    assert 'rec.get("entry_price")' in cuerpo


def test_se_permiten_las_migradas() -> None:
    """Una moneda que migro con el bot parado sigue siendo una posicion."""
    cuerpo = _metodo("_reanudar_una")
    assert "permitir_migradas=True" in cuerpo


def test_la_posicion_retomada_ocupa_plaza() -> None:
    """Si no tomara el semaforo, el bot creeria tener hueco de mas y abriria
    mas posiciones de las que el saldo aguanta."""
    cuerpo = _metodo("_reanudar_una")
    assert "async with self._position_semaphore:" in cuerpo


# ---------------------------------------------------- comportamiento real


def _trader(tmp: Path, saldo: float) -> UniversalTrader:
    t = UniversalTrader.__new__(UniversalTrader)
    t._open_pos_path = tmp / "open_position.json"
    t.bought_mints = set()
    t.solana_client = SimpleNamespace(
        get_token_account_balance=lambda _a: asyncio.sleep(0, result=saldo)
    )
    t.wallet = SimpleNamespace(get_associated_token_address=lambda _m, _p: "ATA")
    return t


TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105
#: Un mint valido de verdad. Con uno inventado, Pubkey.from_string
#: lanza y el atajo barato se salta hacia "sigue viva", que es el
#: comportamiento correcto pero no el que quieren probar estos tests.
MINT = "Er1sFZm3HGuXFiKeXQLg84H9B9Vqk3dp94AjLiS9pump"


def _marcador(tmp: Path, mint: str, entrada: float, cantidad: float) -> Path:
    p = tmp / f"open_position_{mint}.json"
    p.write_text(
        json.dumps(
            {
                "mint": mint,
                "symbol": "TEST",
                "entry_price": entrada,
                "amount": cantidad,
                "platform": "pump_fun",
                "token_program": TOKEN_PROGRAM,
            }
        ),
        encoding="utf-8",
    )
    return p


def test_el_marcador_guarda_el_programa_de_token() -> None:
    """Sin el hay que reconstruir la moneda entera solo para mirar su saldo."""
    cuerpo = _metodo("_record_open_position")
    assert '"token_program"' in cuerpo


def test_un_campo_que_falte_no_tumba_el_marcador() -> None:
    """El marcador es LA RED DE SEGURIDAD de la posicion. Si un campo
    opcional ausente hiciera saltar la excepcion, no se escribiria nada y la
    posicion se quedaria sin forma de retomarse tras un reinicio."""
    cuerpo = _metodo("_record_open_position")
    assert 'getattr(token_info, "token_program_id", "")' in cuerpo


def test_un_fallo_de_red_no_suelta_una_posicion_viva() -> None:
    """Ante la duda se retoma. Soltarla la dejaria sin stop loss, que es justo
    lo que se esta arreglando."""
    trader = UniversalTrader.__new__(UniversalTrader)

    def _explota(_m, _p):
        raise OSError("sin red")

    trader.wallet = SimpleNamespace(get_associated_token_address=_explota)
    assert asyncio.run(trader._sigue_en_la_wallet(MINT, TOKEN_PROGRAM)) is True


def test_una_vendida_a_mano_suelta_su_marcador(tmp_path: Path) -> None:
    """Saldo cero = ya no esta: no hay nada que vigilar y el marcador estorba."""
    trader = _trader(tmp_path, saldo=0.0)
    p = _marcador(tmp_path, MINT, 1e-7, 3_000_000)
    asyncio.run(trader._reanudar_una(json.loads(p.read_text()), p))
    assert not p.exists()


def test_un_marcador_sin_precio_de_entrada_se_descarta(tmp_path: Path) -> None:
    """Sin precio de entrada no hay stop ni escalera que reconstruir."""
    trader = _trader(tmp_path, saldo=1000.0)
    p = _marcador(tmp_path, MINT, 0.0, 3_000_000)
    asyncio.run(trader._reanudar_una(json.loads(p.read_text()), p))
    assert not p.exists()


def test_un_fichero_ilegible_no_tumba_el_arranque(tmp_path: Path) -> None:
    """Una posicion mal guardada no puede impedir que arranque el bot."""
    trader = _trader(tmp_path, saldo=1000.0)
    malo = tmp_path / "open_position_roto.json"
    malo.write_text("{esto no es json", encoding="utf-8")
    asyncio.run(trader._reanudar_posiciones())
    assert not malo.exists()


def test_sin_posiciones_no_hace_nada(tmp_path: Path) -> None:
    trader = _trader(tmp_path, saldo=0.0)
    asyncio.run(trader._reanudar_posiciones())  # no debe lanzar
