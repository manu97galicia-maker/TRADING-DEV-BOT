"""De que tamano es cada compra, en el modo que este puesto.

HAY TRES MODOS Y SE EXCLUYEN. El orden es el mismo que en el codigo:

* DOLARES (`buy_amount_usd`), que es lo de ahora: 10 $ por operacion. Los SOL
  salen de dividir por el precio de SOL EN VIVO, asi que la apuesta vale lo
  mismo suba o baje SOL.
* PORCENTAJE DE LA CARTERA (`fraccion_saldo`). Cartera = el SOL de la wallet
  MAS lo que ya esta metido en las monedas abiertas.
* importe FIJO en SOL (`buy_amount`).

Se han alternado varias veces en un mismo dia, asi que estos tests estan
escritos contra `importe()`, que reproduce los dos. Escribirlos contra uno solo
obligaba a rehacer media docena cada vez que se cambiaba -- y un test que hay
que reescribir para que pase deja de avisar de nada.

LA CUENTA VIVE EN `tests/modelo_tamano.py`, no aqui. Estaba copiada en tres
ficheros y el 23-sep dos de las copias se quedaron verdes midiendo la version
vieja mientras el bot ya hacia otra cosa.

LA TRAMPA QUE VIGILAN: el tope (`tope_compra_sol`) recorta el importe SIN
dejar rastro en el log de la compra. Si se queda por debajo de lo configurado,
el bot compra menos de lo pedido y desde fuera parece que el ajuste no se
aplica. Es el mismo no-op silencioso que ya paso con el `hard_cap` de las
comisiones de prioridad.
"""

# ruff: noqa: S101, PLR2004, PLC0415

from modelo_tamano import CONFIG, SOL_USD_REF, importe
from modelo_tamano import tamanos as _tamanos
from modelo_tamano import trade as _trade


def test_el_modo_activo_es_el_porcentaje_de_la_cartera() -> None:
    """25% de la cartera por operacion (24-sep, pedido).

    Historial del tamano: 0,12 SOL fijo -> 25% -> 0,04 SOL fijo -> 15% -> 30%
    del saldo libre -> 30% de la cartera -> 10 $ fijos -> 25% de la cartera.

    Y la regla de siempre: manda UNO de los tres. Dos con numero es un valor
    que parece decidir y no decide -- justo la clase de cosa que hace buscar
    el fallo donde no esta.
    """
    t = _trade()
    assert t["fraccion_saldo"] == 0.25
    encendidos = [
        k
        for k in ("buy_amount_usd", "fraccion_saldo", "buy_amount")
        if t.get(k, 0) > 0
    ]
    assert encendidos == ["fraccion_saldo"], (
        f"tiene que mandar solo el porcentaje, y mandan: {encendidos}"
    )


def test_el_tope_no_recorta_el_importe_configurado() -> None:
    """EL TEST QUE IMPIDE EL NO-OP.

    El tope recorta SIN dejar rastro en el log de la compra, asi que si alguien
    lo deja por debajo del 25% el porcentaje deja de aplicarse y desde fuera
    parece que el ajuste no hace nada. Hoy esta a 0 (sin tope) y el test vale
    igual para el dia que se vuelva a poner.
    """
    t = _trade()
    for saldo in (0.5, 1.0, 2.0, 10.0):
        # Lo que el modo activo pide, SIN pasar por el tope.
        if t.get("buy_amount_usd", 0) > 0:
            pedido = t["buy_amount_usd"] / SOL_USD_REF
        elif t["fraccion_saldo"] > 0:
            pedido = saldo * t["fraccion_saldo"]
        else:
            pedido = t["buy_amount"]
        assert abs(importe(saldo, t) - pedido) < 1e-12, (
            f"con {saldo} SOL el tope ({t['tope_compra_sol']}) recorta la "
            f"compra ({pedido:.4f}) sin avisar"
        )


def test_las_posiciones_que_caben_valen_lo_mismo() -> None:
    """LA DIFERENCIA ENTRE LOS TRES MODOS, y conviene tenerla escrita.

    SOBRE EL SALDO LIBRE (hasta el 23-sep) las posiciones menguaban solas:
    cada compra era un porcentaje de lo que SOBRABA, asi que con 1 SOL salian
    0,30 · 0,21 · 0,15 · 0,10 · 0,07.

    SOBRE LA CARTERA salen iguales, porque la base no baja al comprar -- el
    dinero cambia de sitio, no desaparece. Con el 25% y 1 SOL: 0,250 · 0,249 ·
    0,248 · 0,160 · 0,038. Las tres primeras iguales, la cuarta con lo que
    queda y la quinta ya es calderilla.

    CON UN IMPORTE FIJO (los 10 $ del 23-sep) las cinco valian lo mismo y el
    saldo no pintaba nada; a cambio la apuesta no crecia con las ganancias.
    """
    tam = _tamanos(1.0)
    frac = _trade()["fraccion_saldo"]

    enteras = [x for x in tam if x > 0.9 * frac]
    assert len(enteras) == 3, f"deberian caber tres del {frac:.0%}: {tam}"
    assert abs(enteras[0] - frac) < 0.005, f"la primera es el {frac:.0%}: {tam}"
    # IGUALES, con el margen de las comisiones que si se van de la cartera. El
    # numero que importa no es "identicas" sino que la tercera no sea la mitad
    # que la primera, que es lo que pasaba sobre el saldo libre.
    assert min(enteras) > 0.95 * max(enteras), (
        f"las que caben tienen que ser iguales, no menguantes: {tam}"
    )
    # Y las de despues salen del recorte: mas pequenas a proposito.
    assert all(x < enteras[0] for x in tam[3:]), f"{tam}"


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

    Con un saldo por debajo de lo que cuesta una compra entera, entra el
    recorte y se compra con lo que quede.

    LO QUE CUESTA, y hay que tenerlo escrito: la comision de prioridad y la
    renta de la cuenta de token NO bajan con el tamano. Con 0,05 SOL de saldo
    la compra sale de 0,015 SOL: se deja buena parte en entrar y salir, o sea
    que nace casi perdiendo. El test no lo impide --es lo pedido-- pero lo
    deja por escrito, y el dia que moleste lo que hay que poner es
    `compra_minima_sol`.
    """
    t = _trade()
    assert t["compra_minima_sol"] == 0
    assert t["fraccion_si_no_llega"] > 0, "sin esto, con poco saldo no compraria"
    assert len(_tamanos(0.10)) >= 1, "con el suelo a 0 tiene que comprar igual"
    assert len(_tamanos(0.05)) >= 1
    # Con poco saldo la compra sale diminuta: menos de dos centesimas de SOL.
    assert max(_tamanos(0.05)) < 0.02


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


def test_la_cuenta_del_tamano_no_esta_copiada_en_ningun_sitio() -> None:
    """EL TEST QUE IMPIDE QUE VUELVA A PASAR LO DEL 23-SEP.

    Esta cuenta estaba escrita a mano en TRES ficheros de test. Al cambiar la
    base del porcentaje --de saldo libre a cartera entera-- se actualizo una y
    las otras dos siguieron VERDES midiendo el comportamiento viejo. Un test
    verde sobre algo que el bot ya no hace es peor que no tener test, porque
    se confia en el; ese mismo fallo ya habia costado tres depuraciones hoy.

    Ahora la cuenta vive en `tests/modelo_tamano.py`, y esto comprueba que los
    tres ficheros usan ESA y no una copia propia. Se mira de que modulo viene
    la funcion, no el texto: una comprobacion por texto cazaba de paso los
    tests que re-derivan el numero a proposito --que es justo su trabajo-- y
    un test que hay que ir apaciguando con excepciones deja de avisar.
    """
    import test_posicion_polvo
    import test_posiciones_por_saldo

    assert importe.__module__ == "modelo_tamano"
    assert _tamanos.__module__ == "modelo_tamano"
    assert test_posiciones_por_saldo.tamanos.__module__ == "modelo_tamano"
    assert test_posicion_polvo.importe_de_compra.__module__ == "modelo_tamano"
