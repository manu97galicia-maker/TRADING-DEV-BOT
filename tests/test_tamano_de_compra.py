"""De que tamano es cada compra, en el modo que este puesto.

HAY DOS MODOS Y SE EXCLUYEN:

* importe FIJO (`buy_amount`), que es lo de ahora: 0,04 SOL por entrada.
* PORCENTAJE DE LA CARTERA (`fraccion_saldo`), que si es mayor que cero
  SUSTITUYE al fijo dentro del cerrojo de gasto. Cartera = el SOL de la
  wallet MAS lo que ya esta metido en las monedas abiertas (23-sep, pedido:
  "sobre el portfolio total"). Hasta esa fecha era sobre el saldo LIBRE, y
  eso hacia que las posiciones menguaran solas.

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

from modelo_tamano import CONFIG, importe
from modelo_tamano import tamanos as _tamanos
from modelo_tamano import trade as _trade


def test_el_modo_activo_es_el_porcentaje() -> None:
    """30% de la cartera por entrada (23-sep, pedido).

    Historial del tamano: 0,12 fijo -> 25% -> 0,04 fijo -> 15% -> 30% del
    saldo libre -> 30% de la cartera entera.

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
        pedido = saldo * frac if frac > 0 else t["buy_amount"]
        assert abs(importe(saldo, t) - pedido) < 1e-12, (
            f"con {saldo} SOL el tope ({t['tope_compra_sol']}) recorta la "
            f"compra ({pedido:.3f}) sin avisar"
        )


def test_con_el_porcentaje_las_posiciones_son_iguales() -> None:
    """EL CAMBIO DEL 23-SEP, y es el que hay que tener por escrito.

    ANTES, sobre el saldo libre, cada compra era un porcentaje de lo que
    SOBRABA: con 1 SOL salian 0,30 · 0,21 · 0,15 · 0,10 · 0,07. La quinta
    valia la cuarta parte que la primera sin que nadie lo pidiera, y las
    ultimas caian donde las comisiones fijas se comen la operacion.

    AHORA, sobre la cartera entera, las que caben salen IGUALES. El precio de
    eso --y por eso el test lo cuenta-- es que con el 30% solo caben TRES
    enteras: las tres juntas son el 90% de la cartera. La cuarta sale del
    recorte del 75% de lo que quede, y la quinta no sale.
    """
    tam = _tamanos(1.0)
    frac = _trade()["fraccion_saldo"]

    enteras = tam[:3]
    assert len(enteras) == 3, f"deberian caber tres del {frac:.0%}: {tam}"
    assert abs(enteras[0] - frac) < 0.005, f"la primera es el {frac:.0%}: {tam}"
    # IGUALES, con el margen de las comisiones que si se van de la cartera.
    # El numero que importa no es "identicas" sino que la tercera no sea la
    # mitad que la primera, que es lo que pasaba antes: alli era 0,15 de 0,30.
    assert min(enteras) > 0.95 * max(enteras), (
        f"las que caben tienen que ser iguales, no menguantes: {tam}"
    )
    # La siguiente, si la hay, es el resto: mas pequena a proposito.
    assert all(x < 0.5 * enteras[0] for x in tam[3:]), f"{tam}"
    # Y no caben las cinco plazas: el dinero se acaba antes.
    assert len(tam) < _trade()["max_posiciones"], (
        f"con el 30% de la cartera no pueden salir 5 posiciones: {tam}"
    )


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

    Con el 30% de la cartera y poco dinero, la compra sale diminuta sola.

    LO QUE CUESTA, y hay que tenerlo escrito: la comision de prioridad y la
    renta de la cuenta de token NO bajan con el tamano. Con 0,05 SOL de saldo,
    el 30% son 0,015 SOL: una compra que se deja buena parte en entrar y
    salir, o sea que nace casi perdiendo. El test no lo impide --es lo
    pedido-- pero lo deja por escrito, y el dia que moleste lo que hay que
    poner es `compra_minima_sol`.
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
