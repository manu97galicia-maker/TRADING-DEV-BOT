"""El stop cierra el 100% de la posicion, y la renta vuelve a la wallet.

Dos cosas pedidas que van juntas:

  1. Cuando salta el stop loss se vende TODO, moonbag incluido. Antes dejaba
     el 2% corriendo en una moneda que se caia a cero, y por eso un stop de
     -18% podia acabar en -20%.

  2. Al cerrar una posicion se recupera la renta de la cuenta de token. Cada
     cuenta de Solana retiene ~0,00204 SOL de deposito y al cerrarla vuelve
     entera a la wallet.

Van juntas porque una cuenta de token con el moonbag dentro NO se puede
cerrar: `close_account` exige saldo cero. Vendiendo el 100% en el stop, la
cuenta queda vacia y se puede cerrar; dejando el 2%, se queda abierta con sus
0,002 SOL dentro para siempre.
"""

# ruff: noqa: S101, PLR2004

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"
TRADER = RAIZ / "src/trading/universal_trader.py"

#: Renta que retiene una cuenta de token SPL, en SOL. Vuelve al cerrarla.
RENTA_POR_CUENTA = 0.00204


def _cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


# ------------------------------------------------- el stop cierra el 100%


def test_el_stop_se_lleva_la_posicion_entera() -> None:
    assert _cfg()["trade"]["vender_todo_en_stop"] is True


def test_con_el_stop_entero_no_queda_nada_que_retener() -> None:
    """La linea del codigo que lo decide: con la bandera puesta, `keep` es
    cero y se vende todo lo que quede."""
    texto = TRADER.read_text(encoding="utf-8")
    assert "keep = 0.0 if self.vender_todo_en_stop else original_qty * moonbag" in texto


def test_ya_no_queda_moonbag_porque_el_tp_vende_todo() -> None:
    """YA NO HAY MOONBAG, y este test pasa a vigilar que siga sin haberlo.

    Hasta la noche del 24-sep la escalera dejaba un resto --primero un 2%,
    luego un 3%-- que el bot no vendia nunca y que Manu vendia a mano. Al
    quedarse la configuracion en UN SOLO take profit que se lleva el 100%,
    ese resto desaparece.

    POR QUE IMPORTA QUE UN TEST LO MIRE: un resto sin dueno es lo peor de los
    dos mundos. Ni lo vende el bot ni nadie sabe que esta ahi, y ocupa la
    cuenta de token --y su renta-- hasta que alguien la cierra a mano. Si
    alguien baja el take profit por debajo del 100% sin pensarlo, esto salta.

    Se suma CON `.get` lo que haya: las claves de los peldanos que ya no
    existen darian KeyError, que es un rojo que dice "falta una clave" en vez
    de "lo que se vende ha cambiado".
    """
    t = _cfg()["trade"]
    vendido = (
        t["take_profit_sell_percentage"]
        + sum(t.get(f"tp{i}_sell") or 0 for i in (2, 3, 4, 5, 6))
        + sum(e["sell"] for e in t.get("tp_extra") or [])
        + sum(d["sell"] for d in t.get("mc_take_profit_tiers") or [])
    )
    assert round(vendido, 6) == 1.0, (
        f"se vende el {vendido * 100:.1f}% y queda un {100 - vendido * 100:.1f}% "
        f"sin dueno: ni lo vende el bot ni nadie sabe que esta ahi"
    )


# ------------------------------------------------------- recuperar la renta


def test_la_renta_se_recupera_en_cada_venta_completa() -> None:
    """`post_session` solo corre al terminar la sesion; en un bot que no para
    eso es casi nunca y la renta se queda parada en cuentas abiertas."""
    assert _cfg()["cleanup"]["mode"] == "after_sell"


def test_no_se_queman_tokens_para_recuperar_renta() -> None:
    """Quemar una bolsa de 30 centimos para recuperar 20 de renta es perder
    dinero. Solo se cierran las cuentas que ya estan a cero."""
    assert _cfg()["cleanup"]["force_close_with_burn"] is False


def test_la_renta_vuelve_a_la_wallet_y_no_a_otro_sitio() -> None:
    """El destino del cierre tiene que ser la propia wallet."""
    manager = (RAIZ / "src/cleanup/manager.py").read_text(encoding="utf-8")
    assert "dest=self.wallet.pubkey" in manager


def test_solo_se_cierra_tras_una_salida_completa() -> None:
    """Con moonbag dentro la cuenta no se puede cerrar: close_account exige
    saldo cero. Intentarlo gastaria comision para nada."""
    texto = TRADER.read_text(encoding="utf-8")
    inicio = texto.index("Close the token account only after a FULL exit")
    assert "if fully_exited:" in texto[inicio : inicio + 200]


def test_lo_que_se_recupera_merece_la_pena() -> None:
    """Contexto del numero: con 50 monedas vendidas son 0,1 SOL parados, que
    a 100 $ el SOL son ~10 $ que estaban fuera de circulacion."""
    assert round(RENTA_POR_CUENTA * 50, 4) == 0.102
