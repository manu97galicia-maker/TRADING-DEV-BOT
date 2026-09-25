"""Que pasa cuando el saldo NO llega para la compra entera.

LO PEDIDO: "pon 0,12 SOL y si no hay suficiente saldo, el 75% del saldo que
quede". Antes de esto la moneda se saltaba entera y el aviso se quedaba en un
log que desde el movil no se lee.

LA CUENTA QUE HAY QUE TENER CLARA, porque es la que decide si el recorte cabe:

    need = importe * (1 + slippage) + 0,003 de comisiones y renta

`free` ya viene limpio (saldo - reserva minima - lo comprometido). Cogiendo el
75% de `free`, lo que hace falta es 0,75 * free * 1,10 + 0,003 = 0,825 * free +
0,003, que cabe en `free` mientras queden mas de ~0,017 SOL libres. Por debajo
manda el suelo de compra, no esta regla.
"""

# ruff: noqa: S101, PLR2004, PLR0913

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
LAMPORTS = 1_000_000_000
COMISIONES = 3_000_000  # _fee_headroom_lamports: ~0,003 SOL


def _cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _decide(
    libre_sol: float, importe: float, *,
    fraccion: float = 0.75, slippage: float = 0.10,
    tope: float = 0.12, minimo: float = 0.02,
) -> float | None:
    """Reproduce la decision del trader. Devuelve lo que se compra, o None.

    Es una copia de la logica, no la logica: sirve para fijar la CUENTA con
    numeros concretos y que se vea a simple vista si un cambio la rompe.
    """
    free = int(libre_sol * LAMPORTS)
    need = int(importe * (1 + slippage) * LAMPORTS) + COMISIONES
    if free < need and fraccion > 0 and free > 0:
        recorte = min(free / LAMPORTS * fraccion, tope)
        need_recorte = int(recorte * (1 + slippage) * LAMPORTS) + COMISIONES
        if recorte > 0 and need_recorte <= free and recorte >= minimo:
            importe, need = recorte, need_recorte
    if free < need:
        return None
    return importe


def test_con_saldo_de_sobra_se_compra_el_importe_entero() -> None:
    """La red de seguridad NO puede cambiar la compra normal. Este es el test
    que separa esto de `fraccion_saldo`: aquella compraria el 75% de 2 SOL."""
    assert _decide(2.0, 0.12) == 0.12


def test_justo_sin_llegar_se_compra_el_75_por_ciento() -> None:
    libre = 0.10
    comprado = _decide(libre, 0.12)
    assert comprado is not None
    assert abs(comprado - 0.075) < 1e-9


def test_el_recorte_siempre_cabe_en_lo_que_queda() -> None:
    """Lo importante no es el 75%, es que el 25% restante cubra slippage y
    comisiones. Se comprueba en toda la franja util, no en un punto."""
    for centesimas in range(3, 12):
        libre = centesimas / 100
        comprado = _decide(libre, 0.12)
        if comprado is None:
            continue
        need = comprado * 1.10 + 0.003
        assert need <= libre + 1e-9, f"con {libre} libres no cabe {comprado}"


def test_por_debajo_del_suelo_no_se_compra_polvo() -> None:
    """0,02 SOL de suelo: con 0,025 libres saldrian 0,019 y no compensa.

    El motivo es que la comision de prioridad y la renta de la cuenta de token
    no bajan con el tamano de la compra: por debajo del suelo la operacion nace
    perdiendo y ni un x2 la recupera.
    """
    assert _decide(0.025, 0.12) is None


def test_sin_saldo_no_se_inventa_nada() -> None:
    assert _decide(0.0, 0.12) is None


def test_el_recorte_nunca_supera_el_tope_autorizado() -> None:
    """Si alguien sube el saldo y baja el tope, manda el tope."""
    assert _decide(1.0, 0.12, tope=0.05) == 0.12  # ni entra: 0,12 cabia
    assert _decide(0.10, 0.30, tope=0.05) == 0.05


def test_apagado_es_el_comportamiento_de_antes() -> None:
    """Con la fraccion a 0 se vuelve a saltar la moneda, como toda la vida."""
    assert _decide(0.10, 0.12, fraccion=0.0) is None


# ------------------------------------------------------- la config real


def test_la_config_pide_lo_que_se_pidio() -> None:
    t = _cfg()["trade"]
    # Manda el porcentaje del saldo libre: 15% (24-sep). Los otros dos modos
    # van a cero porque solo uno puede mandar a la vez.
    assert t["fraccion_saldo"] == 0.80
    assert t["fraccion_base"] == "cartera"
    assert t["buy_amount_usd"] == 0
    assert t["buy_amount"] == 0
    # EL RECORTE ES LO QUE HACE QUE SALGAN CUATRO POSICIONES Y NO TRES: cuando
    # el 25% ya no cabe en lo que queda libre, el bot compra con el 75% del
    # resto en vez de saltarse la moneda.
    assert t["fraccion_si_no_llega"] == 0.75


def test_la_fuente_manda_el_mismo_importe_que_el_global() -> None:
    """El importe de la SENAL pisa al global, asi que los dos tienen que decir
    lo mismo o el de arriba no sirve de nada. Ya paso una vez.

    Hoy los dos estan a 0 y manda el porcentaje; el test sigue valiendo para
    el dia que se vuelva al importe fijo."""
    c = _cfg()
    assert c["filters"]["holders"]["buy_amount"] == c["trade"]["buy_amount"]


def test_el_tope_duro_no_recorta_el_importe_configurado() -> None:
    """El tope recorta TODO lo que pase por encima, y SIN dejar rastro en el
    log de la compra: si se queda por debajo del importe, el bot compra menos
    de lo pedido y desde fuera parece que el ajuste no se aplica.

    Hoy esta a 0 --sin tope-- y la comprobacion vale igual para el dia que se
    vuelva a poner.
    """
    t = _cfg()["trade"]
    assert t["tope_compra_sol"] == 0 or t["tope_compra_sol"] >= t["buy_amount"]


def test_el_suelo_de_compra_esta_quitado() -> None:
    """Se quito el 19-sep, pedido. Queda escrito lo que eso significa: con el
    75% de un saldo casi vacio el bot comprara polvo, y las comisiones fijas
    --que no bajan con el tamano-- se llevaran una parte enorme.

    No es un descuido: es la decision que se tomo. Si algun dia se quiere
    recuperar, este test dice donde mirar.
    """
    assert _cfg()["trade"]["compra_minima_sol"] == 0
