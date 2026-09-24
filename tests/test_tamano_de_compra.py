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

from itertools import pairwise

from modelo_tamano import CONFIG, SOL_USD_REF, importe
from modelo_tamano import tamanos as _tamanos
from modelo_tamano import trade as _trade


def test_el_modo_activo_es_el_porcentaje_del_saldo_libre() -> None:
    """70% de la CARTERA ENTERA por operacion (24-sep, pedido: "sube al 70%
    del SOL total por operacion").

    LA BASE VUELVE A "CARTERA" y es el cambio que mas pesa de los dos: no es
    solo que el porcentaje suba de 20 a 25, es que se calcula sobre otra
    cosa. Con "libre" cada compra era un porcentaje de lo que SOBRABA y
    menguaban solas; con "cartera" la base no baja al comprar, asi que salen
    iguales hasta que se acaba el saldo.

    Historial del tamano: 0,12 SOL fijo -> 25% -> 0,04 SOL fijo -> 15% -> 30%
    del saldo libre -> 30% de la cartera -> 10 $ fijos -> 25% de la cartera
    -> 15% libre -> 20% libre -> 25% cartera -> 30% -> 50% -> 70% cartera.

    Y la regla de siempre: manda UNO de los tres. Dos con numero es un valor
    que parece decidir y no decide -- justo la clase de cosa que hace buscar
    el fallo donde no esta.
    """
    t = _trade()
    assert t["fraccion_saldo"] == 0.70
    # LA BASE ES PARTE DEL MODO. "15%" no dice nada sin ella: el 15% del
    # saldo libre y el 15% de la cartera son numeros distintos y menguan
    # distinto.
    assert t["fraccion_base"] == "cartera"
    encendidos = [
        k
        for k in ("buy_amount_usd", "fraccion_saldo", "buy_amount")
        if t.get(k, 0) > 0
    ]
    assert encendidos == ["fraccion_saldo"], (
        f"tiene que mandar solo el porcentaje, y mandan: {encendidos}"
    )


def _pedido_sin_tope(t: dict, saldo: float) -> float:
    """Lo que el modo activo pide, SIN pasar por `tope_compra_sol`."""
    if t.get("buy_amount_usd", 0) > 0:
        return t["buy_amount_usd"] / SOL_USD_REF
    if t["fraccion_saldo"] > 0:
        return saldo * t["fraccion_saldo"]
    return t["buy_amount"]


def test_el_tope_solo_muerde_donde_toca() -> None:
    """EL TEST QUE IMPIDE EL NO-OP, ahora que el tope vuelve a existir.

    El 24-sep se puso `tope_compra_sol: 2.0` ("maximo dos SOL por
    operacion"). Antes estaba a 0 y este test exigia que NUNCA recortara,
    porque un tope por debajo del porcentaje lo anula en silencio: el
    porcentaje deja de aplicarse y desde fuera parece que el ajuste no hace
    nada.

    LO QUE SE COMPRUEBA AHORA es el punto exacto en que empieza a mandar, que
    sale de dividir el tope entre la fraccion. Con el 70% y un tope de 2, ese
    punto esta en 2,86 SOL de cartera: por debajo manda el porcentaje, por
    encima manda el tope. Si alguien baja el tope por debajo de lo que pide
    el porcentaje con el saldo de trabajo, este test se pone rojo.
    """
    t = _trade()
    tope = float(t.get("tope_compra_sol", 0) or 0)
    if tope <= 0:
        for saldo in (0.5, 1.0, 2.0, 10.0):
            assert abs(importe(saldo, t) - _pedido_sin_tope(t, saldo)) < 1e-12
        return

    frac = float(t["fraccion_saldo"])
    assert frac > 0, "este test describe el modo porcentaje"
    corte = tope / frac
    # Por debajo del corte el tope no puede tocar nada.
    for saldo in (corte * 0.5, corte * 0.99):
        assert abs(importe(saldo, t) - _pedido_sin_tope(t, saldo)) < 1e-12, (
            f"con {saldo:.2f} SOL de cartera el tope ({tope}) ya recorta, y el "
            f"corte deberia estar en {corte:.2f}"
        )
    # Por encima manda el tope, y ni un lamport mas.
    for saldo in (corte * 1.01, corte * 5):
        assert importe(saldo, t) == tope, (
            f"con {saldo:.2f} SOL de cartera tendria que recortar a {tope}"
        )
    # Y EL RECORTE NO ES MUDO: el trader avisa en el log cuando pasa. Sin
    # esto, una compra recortada es indistinguible de una compra normal.
    fuente = (
        CONFIG.parent.parent / "src/trading/universal_trader.py"
    ).read_text(encoding="utf-8")
    assert "TOPE: se iba a comprar" in fuente, (
        "el trader ya no avisa al recortar por tope: un recorte silencioso "
        "hace que el porcentaje parezca no aplicarse"
    )


def test_sobre_el_saldo_libre_las_posiciones_menguan() -> None:
    """LA DIFERENCIA ENTRE LAS DOS BASES, y es el motivo de que haya una clave
    para elegirla en vez de estar escrita en el codigo.

    SOBRE EL SALDO LIBRE (lo de ahora) cada compra es un porcentaje de lo que
    SOBRA, asi que menguan solas: con 1 SOL y el 20% salen 0,194 · 0,155 ·
    0,123 · 0,098 · 0,078. Eso NO es un defecto, es el freno automatico -- por
    muchas plazas que haya nunca se compromete la cartera entera.

    SOBRE LA CARTERA salen iguales, porque la base no baja al comprar: el
    dinero cambia de sitio, no desaparece. A cambio tres del 25% comprometian
    el 75% del dinero de golpe.

    CABEN LAS CINCO PLAZAS, que es lo pedido, pero no salen iguales de
    utiles en los dos modos: con "cartera" las tres primeras son compras de
    verdad y las dos ultimas son lo que quedaba.

    EL TOPE DE LAS CINCO JUNTAS fue subiendo con el porcentaje: 60% con el
    15%, 70% con el 20%. Con el 25% de la CARTERA se van al 95%, y ahi ese
    numero deja de ser un freno y pasa a ser la constatacion de que no queda
    freno. Por eso el test se parte en dos: con base "libre" sigue exigiendo
    que quede casi un tercio sin comprometer; con base "cartera" exige lo
    contrario, que este escrito que se compromete casi todo.
    """
    from modelo_tamano import base_es_libre

    tam = _tamanos(1.0)
    # CUANTAS CABEN LO DECIDE EL DINERO, no `max_posiciones`: con el 30% de
    # la cartera el saldo se acaba en la cuarta. Lo fija en detalle
    # test_posiciones_por_saldo.py; aqui basta con no darlo por hecho.
    assert 1 <= len(tam) <= _trade()["max_posiciones"], f"nunca mas del techo: {tam}"
    for antes, despues in pairwise(tam):
        assert despues <= antes + 1e-9, f"nunca crecen: {tam}"
    if base_es_libre():
        # Menguan suave y siempre: la ultima no llega a tres cuartos de la
        # primera, y nunca se compromete todo.
        assert tam[-1] < 0.75 * tam[0], f"deberian menguar: {tam}"
        for antes, despues in pairwise(tam):
            assert despues < antes, f"cada una mas pequena que la anterior: {tam}"
        assert sum(tam) < 0.70, f"las cinco juntas no pueden llevarse todo: {tam}"
    else:
        # Base "cartera": salen IGUALES hasta que se agota el saldo libre.
        # La primera es la fraccion del saldo ENTERO: eso es lo que
        # distingue esta base. Que la segunda salga igual solo pasa mientras
        # la fraccion es pequena; con el 50% ya sale recortada.
        frac = float(_trade()["fraccion_saldo"])
        assert abs(tam[0] - frac) < 1e-6, f"la primera es el {frac:.0%}: {tam}"
        # Y AQUI NO HAY FRENO AUTOMATICO, que es lo que hay que saber: con el
        # 25% o con el 30% se llevan el mismo 95% del saldo -- el tope lo
        # pone el saldo libre, no el porcentaje. Subirlo no arriesga mas
        # dinero: reparte el mismo en menos posiciones y mas gordas. Lo unico que para
        # es el guardian del saldo libre, no el tamano de la compra. El tope
        # se deja en 0,97 --que es lo que queda tras la reserva y los
        # colchones-- porque poner uno mas bajo seria fingir un freno que no
        # existe en este modo.
        assert sum(tam) < 0.97, f"no se puede gastar mas de lo que hay: {tam}"
        assert sum(tam) > 0.90, (
            f"con el 25% de la cartera se compromete casi todo, y conviene "
            f"que este escrito: {tam}"
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
