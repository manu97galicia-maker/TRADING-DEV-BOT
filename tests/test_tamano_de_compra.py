"""De que tamano es cada compra, en el modo que este puesto.

HAY DOS MODOS Y SE EXCLUYEN:

* importe FIJO (`buy_amount`), que es lo de ahora: 0,04 SOL por entrada.
* PORCENTAJE del saldo libre (`fraccion_saldo`), que si es mayor que cero
  SUSTITUYE al fijo dentro del cerrojo de gasto.

Se han alternado varias veces en un mismo dia, asi que estos tests estan
escritos contra `importe()`, que reproduce los dos. Escribirlos contra uno solo
obligaba a rehacer media docena cada vez que se cambiaba -- y un test que hay
que reescribir para que pase deja de avisar de nada.

LA TRAMPA QUE VIGILAN: el tope (`tope_compra_sol`) recorta el importe SIN
dejar rastro en el log de la compra. Si se queda por debajo de lo configurado,
el bot compra menos de lo pedido y desde fuera parece que el ajuste no se
aplica. Es el mismo no-op silencioso que ya paso con el `hard_cap` de las
comisiones de prioridad.
"""

# ruff: noqa: S101, PLR2004, PLC0415

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def importe(libre: float, t: dict | None = None) -> float:
    """El importe de UNA compra con ese saldo libre, como lo calcula el bot.

    Copia la logica de los DOS modos --importe fijo y porcentaje del saldo--
    porque se alternan: escribir el test contra uno solo obliga a rehacerlo
    cada vez que se cambia.

    El tope y el suelo solo se aplican si son MAYORES QUE CERO. Cero significa
    "sin limite" en toda esta configuracion, y escribir `min(x, tope)` a secas
    daria 0.
    """
    t = t or _trade()
    frac = t.get("fraccion_saldo", 0)
    imp = libre * frac if frac > 0 else float(t["buy_amount"])
    tope = t["tope_compra_sol"]
    if tope > 0 and imp > tope:
        imp = tope
    return max(0.0, imp)


def _tamanos(saldo: float, plazas: int | None = None) -> list[float]:
    """Reproduce la cuenta del trader para un saldo dado.

    `free` = saldo - reserva intocable - lo ya comprometido.

    INCLUYE EL GUARDIAN DEL SALDO, y es lo que mas importa de este helper: no
    basta con que el importe "quepa", tiene que caber la compra MAS el
    slippage MAS las comisiones. Sin esa guarda la simulacion se pasaba del
    saldo y el test acusaba al bot de algo que el bot no hace -- tiene su
    `if free < need` y se para solo.
    """
    t = _trade()
    libre = saldo - t["min_reserve_sol"]
    salen: list[float] = []
    for _ in range(plazas or t["max_posiciones"]):
        imp = importe(libre, t)
        suelo = t["compra_minima_sol"]
        if imp <= 0 or (suelo > 0 and imp < suelo):
            break
        necesita = imp * (1 + t["buy_slippage"]) + 0.003
        # SI NO CABE, EL RECORTE DEL 75%. El bot no se salta la moneda: compra
        # con esa fraccion de lo que quede libre. Sin esto el helper decia "no
        # compra" justo donde el bot SI compra, que es el caso de saldo
        # pequeno -- el mas facil de tener mal y el que mas se consulta.
        _f = t.get("fraccion_si_no_llega", 0)
        if necesita > libre and _f > 0 and libre > 0:
            recorte = libre * _f
            if t["tope_compra_sol"] > 0:
                recorte = min(recorte, t["tope_compra_sol"])
            nec_recorte = recorte * (1 + t["buy_slippage"]) + 0.003
            bajo_suelo = suelo > 0 and recorte < suelo
            if recorte > 0 and nec_recorte <= libre and not bajo_suelo:
                imp, necesita = recorte, nec_recorte
        if necesita > libre:
            break
        salen.append(round(imp, 6))
        libre -= necesita
    return salen


def test_el_modo_activo_es_el_porcentaje() -> None:
    """30% del saldo libre por entrada (23-sep, pedido).

    Historial del tamano: 0,12 fijo -> 25% -> 0,04 fijo -> 15% -> 30%.

    Y la regla de siempre: manda UNO de los dos. Un importe fijo distinto de
    cero con el porcentaje encendido es un numero que parece decidir y no
    decide -- justo la clase de cosa que hace buscar el fallo donde no esta.
    """
    t = _trade()
    assert t["fraccion_saldo"] == 0.30
    assert t["buy_amount"] == 0, "con el porcentaje encendido el fijo no manda"


def test_el_tope_no_recorta_el_importe_configurado() -> None:
    """EL TEST QUE IMPIDE EL NO-OP.

    El tope recorta SIN dejar rastro en el log de la compra, asi que si alguien
    lo deja por debajo del 25% el porcentaje deja de aplicarse y desde fuera
    parece que el ajuste no hace nada. Hoy esta a 0 (sin tope) y el test vale
    igual para el dia que se vuelva a poner.
    """
    t = _trade()
    frac = t["fraccion_saldo"]
    for saldo in (0.5, 1.0, 2.0, 10.0):
        libre = saldo - t["min_reserve_sol"]
        pedido = libre * frac if frac > 0 else t["buy_amount"]
        assert abs(importe(libre, t) - pedido) < 1e-12, (
            f"con {saldo} SOL el tope ({t['tope_compra_sol']}) recorta la "
            f"compra ({pedido:.3f}) sin avisar"
        )


def test_con_el_porcentaje_las_posiciones_menguan() -> None:
    """LA DIFERENCIA PRACTICA ENTRE LOS DOS MODOS, y conviene tenerla escrita.

    Cada compra es el 15% de lo que QUEDA libre, no del saldo inicial: la
    primera es la mas grande y la quinta la mas pequena. Con importe fijo las
    cinco valdrian lo mismo. Por eso cinco plazas al 15% no comprometen el 75%
    de la cartera.
    """
    tam = _tamanos(1.0)
    assert len(tam) == 5
    assert len(set(round(x, 9) for x in tam)) == 5, f"deberian menguar: {tam}"
    assert tam[0] > tam[-1]


def test_nunca_se_gasta_mas_de_lo_que_hay() -> None:
    """Lo que de verdad importa: que quede SOL para vender lo comprado."""
    cfg = _trade()
    for saldo in (0.2, 0.5, 1.0, 2.0, 5.0):
        gastado = sum(_tamanos(saldo))
        assert gastado <= saldo - cfg["min_reserve_sol"], (
            f"con {saldo} SOL se gastarian {gastado:.3f} y hay que dejar "
            f"{cfg['min_reserve_sol']} de reserva"
        )


def test_sin_suelo_compra_con_lo_que_haya() -> None:
    """EL SUELO SE QUITO el 19-sep, pedido: el bot ya no se para por saldo bajo.

    Con el 15% del saldo y poco dinero, la compra sale diminuta sola.

    LO QUE CUESTA, y hay que tenerlo escrito: la comision de prioridad y la
    renta de la cuenta de token NO bajan con el tamano. Con 0,05 SOL de saldo,
    el 15% de lo libre son ~0,003 SOL: una compra que se deja la mayor parte
    en entrar y salir, o sea que nace perdiendo. El test no lo impide --es lo
    pedido-- pero lo deja por escrito, y el dia que moleste lo que hay que
    poner es `compra_minima_sol`.
    """
    t = _trade()
    assert t["compra_minima_sol"] == 0
    assert t["fraccion_si_no_llega"] > 0, "sin esto, con poco saldo no compraria"
    assert len(_tamanos(0.10)) >= 1, "con el suelo a 0 tiene que comprar igual"
    assert len(_tamanos(0.05)) >= 1
    # Con poco saldo la compra sale diminuta: menos de una centesima de SOL.
    assert max(_tamanos(0.05)) < 0.01


def test_la_reserva_sigue_siendo_intocable() -> None:
    """Lo unico que queda protegiendo: sin esto no habria gas para VENDER lo
    comprado, y una posicion sin salida vale cero."""
    t = _trade()
    assert t["min_reserve_sol"] > 0
    # Con un saldo igual a la reserva no se compra nada.
    assert _tamanos(t["min_reserve_sol"]) == []


def test_no_quedan_importes_fijos_sueltos() -> None:
    """Pedido el 19-sep: "elimina lo del 0.12, solo el porcentaje".

    Todos a cero, y el cero es la decision importante: si alguien apaga el
    porcentaje, el importe sale 0, cae por debajo de `compra_minima_sol` y la
    compra se salta con un aviso. El bot se PARA en vez de gastar una cantidad
    vieja que nadie ha pedido. Es el fallo seguro.
    """
    t = _trade()
    # Los importes ESPECIALES siguen a cero: ninguna via compra distinto.
    assert t["buy_amount_tras_sl"] == 0, "compra reducida tras stop: apagada"
    assert t["mayhem_buy_amount"] == 0
    # Y el de la fuente tiene que decir lo mismo que el global, porque el
    # importe de la SENAL pisa al global. Ya paso una vez que no coincidian.
    import yaml
    h = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]["holders"]
    assert h["buy_amount"] == t["buy_amount"]
