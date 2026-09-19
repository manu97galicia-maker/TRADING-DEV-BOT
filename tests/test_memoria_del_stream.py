"""La memoria del stream y el retraso: por que "el numero de holders tarda".

EL FALLO QUE SE ARREGLA AQUI. `volumen` guardaba 5 minutos por moneda
(`_VIDA = 300`) y como mucho 4.000 monedas. La ventana de compra de la
estrategia de titulares subio a 20 minutos el 19-sep y estos dos numeros se
quedaron donde estaban, asi que el bot prometia mirar veinte minutos con datos
de cinco. A una moneda que se le borraba en marcha le pasaban DOS cosas:

* el recuento de titulares le volvia a CERO y tenia que rehacerlo desde cero
  --que es literalmente "el numero de holders tarda muchisimo"--, y
* perdia el "se le vio nacer", con lo que su edad pasaba a ser desconocida y
  quedaba descartada como "sin_historia" PARA SIEMPRE, por mucho que despues
  juntara doscientos titulares.

Y LA MEDIDA QUE FALTABA. Contar titulares esta medido en 0,03 ms: no puede ser
lo lento. Cada TradeEvent trae su propia marca de tiempo DE LA CADENA, asi que
el retraso real del stream se puede saber gratis. Sin ese numero, "va lento" no
se distingue de "las monedas tardan en juntar gente", que se arreglan al reves.
"""

# ruff: noqa: S101, SLF001, PLR2004

import base64
import struct
import time

from monitoring import volumen


def _limpia_todo() -> None:
    volumen._ops.clear()
    volumen._curva.clear()
    volumen._nacimiento.clear()
    volumen._retrasos.clear()
    volumen._ultima_limpieza = 0.0
    volumen._borradas_viejas = 0
    volumen._borradas_por_tope = 0


def _evento(mint: bytes, wallet: bytes, *, ts: int, llena: bool = True) -> str:
    """Un TradeEvent con el layout real de pump.fun."""
    cuerpo = bytearray(121)
    cuerpo[0:32] = mint
    struct.pack_into("<Q", cuerpo, 32, 10_000_000)  # sol_amount
    struct.pack_into("<Q", cuerpo, 40, 2_000_000)  # token_amount
    cuerpo[48] = 1  # is_buy
    cuerpo[49:81] = wallet
    struct.pack_into("<q", cuerpo, 81, ts)  # marca de tiempo DE LA CADENA
    struct.pack_into("<QQ", cuerpo, 89, 30_000_000_000, 1_072_000_000_000_000)
    reservas = (
        volumen.RESERVA_TOKENS_INICIAL if llena else volumen.RESERVA_TOKENS_INICIAL // 2
    )
    struct.pack_into("<QQ", cuerpo, 105, 30_000_000_000, reservas)
    return (
        "Program data: " + base64.b64encode(volumen.DISC_TRADE + bytes(cuerpo)).decode()
    )


# --------------------------------------------------------------- el retraso


def test_el_retraso_del_stream_se_mide_solo() -> None:
    """LA MEDIDA QUE FALTABA, y sale gratis del propio evento."""
    _limpia_todo()
    mint, wallet = bytes(range(32)), bytes(range(32, 64))
    volumen.registrar_logs([_evento(mint, wallet, ts=int(time.time()) - 7)])
    p50, p95, muestras = volumen.retraso()
    assert muestras == 1
    assert 6.0 <= p50 <= 8.0, f"deberia decir ~7s de retraso, dijo {p50}"
    assert 6.0 <= p95 <= 8.0


def test_un_reloj_descuadrado_no_ensucia_el_retraso() -> None:
    """Un evento "del futuro" o de hace media hora no cuenta: seria ruido que
    volveria inutil justo el numero con el que se va a decidir."""
    _limpia_todo()
    mint, wallet = bytes(range(32)), bytes(range(32, 64))
    volumen.registrar_logs([_evento(mint, wallet, ts=int(time.time()) + 300)])
    volumen.registrar_logs([_evento(mint, wallet, ts=int(time.time()) - 5000)])
    assert volumen.retraso()[2] == 0


# ------------------------------------------------- la memoria y el nacimiento


def test_la_memoria_cubre_la_ventana_de_compra() -> None:
    """20 minutos de ventana exigen al menos 20 minutos de memoria."""
    antes_vida, antes_tope = volumen._VIDA, volumen._MAX_MINTS
    try:
        volumen.configurar_memoria(1200.0)
        assert volumen._VIDA >= 1200.0, "no se puede comprar lo que ya se olvido"
        assert volumen._MAX_MINTS > antes_tope, (
            "recordar mas tiempo en el mismo hueco no recuerda mas tiempo: el "
            "tope de monedas pasaria a ser EL que borra"
        )
    finally:
        volumen._VIDA, volumen._MAX_MINTS = antes_vida, antes_tope


def test_una_moneda_borrada_no_pierde_su_edad_ni_su_nacimiento() -> None:
    """EL FALLO QUE LA DEJABA SIN COMPRAR PARA SIEMPRE.

    Se la ve nacer, se le borra el historial por falta de sitio y vuelve a
    operar. Antes: edad desconocida -> "sin_historia" -> descartada de por
    vida. Ahora el nacimiento sobrevive al borrado.
    """
    _limpia_todo()
    mint, wallet = bytes(range(32)), bytes(range(32, 64))
    nacio_hace = 400
    volumen.registrar_logs([_evento(mint, wallet, ts=int(time.time()) - nacio_hace)])
    clave = volumen._b58(mint)
    assert volumen.vio_la_creacion(clave) is True
    edad_antes = volumen.edad(clave)
    assert edad_antes is not None

    volumen._olvidar(clave)  # lo que hace `_limpiar` cuando no cabe

    assert volumen.vio_la_creacion(clave) is True, (
        "borrar el historial no deshace haberla visto nacer"
    )
    edad_despues = volumen.edad(clave)
    assert edad_despues is not None, "sin edad queda descartada para siempre"
    assert abs(edad_despues - edad_antes) < 2.0, (
        f"tras el borrado parecia recien nacida: {edad_despues:.0f}s "
        f"contra {edad_antes:.0f}s"
    )


def test_la_escoba_no_corre_en_cada_notificacion() -> None:
    """Medido: 0,46 ms por pasada con la memoria llena. Corriendo una vez por
    operacion de pump.fun eso es ~14% de un nucleo tirado."""
    _limpia_todo()
    antes_tope = volumen._MAX_MINTS
    try:
        volumen._MAX_MINTS = 2
        ahora = time.time()
        for i in range(10):
            volumen._ops["M%d" % i].append((ahora, 0.1, True, "w"))
        volumen._limpiar()
        primera = volumen._ultima_limpieza
        assert primera > 0.0
        for i in range(10, 20):
            volumen._ops["M%d" % i].append((ahora, 0.1, True, "w"))
        volumen._limpiar()
        assert volumen._ultima_limpieza == primera, (
            "se ha vuelto a barrer antes de tiempo"
        )
    finally:
        volumen._MAX_MINTS = antes_tope
        _limpia_todo()


def test_se_ve_cuando_la_memoria_se_queda_corta() -> None:
    """`por_tope` distinto de cero = el bot esta olvidando monedas que todavia
    miraba. Es el dato que dice si la ventana esta bien puesta."""
    _limpia_todo()
    antes_tope = volumen._MAX_MINTS
    try:
        volumen._MAX_MINTS = 2
        ahora = time.time()
        for i in range(6):
            volumen._ops["M%d" % i].append((ahora, 0.1, True, "w"))
        volumen._limpiar()
        estado = volumen.estado_memoria()
        assert estado["por_tope"] == 4, estado
        assert estado["monedas"] == 2
    finally:
        volumen._MAX_MINTS = antes_tope
        _limpia_todo()


# --------------------------------------------------------------- el base58


def test_el_cache_de_base58_no_cambia_el_resultado() -> None:
    """El cache es por memoria, no por velocidad: si mintiera, los titulares se
    contarian sobre wallets equivocadas."""
    _limpia_todo()
    for datos in (bytes(range(32)), bytes([0, 0, 1] + [7] * 29), bytes(32)):
        assert volumen._b58(datos) == volumen._b58_calcula(datos)
        assert volumen._b58(datos) is volumen._b58(datos), "deberia reusar la cadena"


# ------------------------------------------------------- de punta a punta


def test_una_moneda_parada_seis_minutos_sigue_siendo_comprable() -> None:
    """EL CASO REAL. Ventana de 20 minutos, moneda que se calla 6 y vuelve.

    Antes se la borraba a los 5 (`_VIDA = 300`) y al volver era una
    desconocida: 0 titulares y edad desconocida. Ahora vuelve con su edad.
    """
    import asyncio
    from types import SimpleNamespace

    from monitoring.holders_source import HoldersSource

    _limpia_todo()
    mint, wallet = bytes(range(32)), bytes(range(32, 64))
    nacio_hace = 420  # siete minutos: dentro de la ventana de 20
    volumen.registrar_logs([_evento(mint, wallet, ts=int(time.time()) - nacio_hace)])
    clave = volumen._b58(mint)

    volumen._olvidar(clave)  # el silencio le costo el historial

    fuente = HoldersSource(
        "wss://x",
        SimpleNamespace(),
        SimpleNamespace(),
        min_holders=2,
        max_edad_seg=1200.0,
        heartbeat_interval=0,
    )
    edad = fuente._edad(clave)
    assert edad is not None, "descartada como 'sin_historia' pese a verla nacer"
    assert edad <= fuente.max_edad_seg, "dentro de la ventana de 20 minutos"
    assert abs(edad - nacio_hace) < 5.0, f"su edad de verdad, no {edad:.0f}s"
    assert asyncio.get_event_loop_policy() is not None  # el test no usa red


def test_el_embudo_sale_por_el_log_con_telegram_apagado(caplog) -> None:  # noqa: ANN001
    """El embudo es el unico diagnostico de por que no se compra, y se estaba
    calculando SOLO para mandarlo a Telegram... que esta apagado a peticion.
    O sea que se tiraba justo cuando hacia falta."""
    import logging
    from types import SimpleNamespace

    from monitoring import notifier
    from monitoring.holders_source import HoldersSource

    _limpia_todo()
    notifier.configurar_avisos(False)  # Telegram en silencio, como en produccion
    fuente = HoldersSource(
        "wss://x",
        SimpleNamespace(),
        SimpleNamespace(),
        min_holders=23,
        max_edad_seg=1200.0,
        heartbeat_interval=0.001,
    )
    fuente._embudo = {"vistas": 41, "con_titulares": 3, "comprables": 0}
    with caplog.at_level(logging.INFO):
        fuente._heartbeat_si_toca()
    texto = caplog.text
    assert "Parte de titulares" in texto, "el parte no llego al log"
    assert "41" in texto and "comprables" in texto
    assert "Retraso del stream" in texto
    assert "Memoria" in texto
    assert "<b>" not in texto, "el log no deberia llevar etiquetas de Telegram"


def test_el_tope_de_memoria_es_por_operaciones_no_por_monedas() -> None:
    """Contar monedas no acota la memoria: una moneda de moda guarda cuarenta
    veces mas que una tranquila. En un droplet de 1 GB, quedarse sin memoria
    mata el bot CON POSICIONES ABIERTAS."""
    _limpia_todo()
    antes_tope, antes_ops = volumen._MAX_MINTS, volumen._MAX_OPS_TOTAL
    try:
        volumen._MAX_MINTS = 100  # el tope de monedas NO se pasa
        volumen._MAX_OPS_TOTAL = 500
        ahora = time.time()
        for i in range(10):  # 10 monedas de 200 operaciones = 2.000
            volumen._ops["M%02d" % i].extend(
                [(ahora + i, 0.1, True, "w")] * 200
            )
        volumen._limpiar()
        total = sum(len(v) for v in volumen._ops.values())
        assert total <= volumen._MAX_OPS_TOTAL, f"no bajo del presupuesto: {total}"
        assert volumen.estado_memoria()["por_tope"] > 0, "deberia quedar constancia"
        assert "M09" in volumen._ops, "se sueltan las PARADAS, no las activas"
    finally:
        volumen._MAX_MINTS, volumen._MAX_OPS_TOTAL = antes_tope, antes_ops
        _limpia_todo()
