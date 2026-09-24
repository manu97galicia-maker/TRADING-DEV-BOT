"""Que una venta decidida acabe ejecutándose, o al menos reintentándose.

EL FALLO QUE ORIGINA ESTE FICHERO. La salida de emergencia anti-rug llamaba a
`_sell_amount` con tres argumentos de los cuatro que pide. Lanzaba TypeError en
cada disparo, se lo tragaba el `except Exception` del monitor, y la posición no
se vendía NUNCA -- mientras el aviso de Telegram decía "saliendo de la posición
entera". Estuvo así sin que nadie lo viera, porque un error de programación y un
fallo de red se trataban igual.

El primer test es el que lo habría cazado, y cubre la clase entera: revisa TODAS
las llamadas a `_sell_amount` del fichero, no la que ya sabemos que fallaba.
"""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN202, ARG002, ARG005, PLC0415, E402

import ast
from pathlib import Path
from types import SimpleNamespace

TRADER = Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"


def _arbol() -> ast.Module:
    return ast.parse(TRADER.read_text(encoding="utf-8"))


def _llamadas_a_vender() -> list[ast.Call]:
    return [
        n for n in ast.walk(_arbol())
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_sell_amount"
    ]


# ------------------------------------------- el test que lo habría cazado


def test_toda_llamada_a_vender_lleva_sus_cuatro_datos() -> None:
    """`_sell_amount(token_info, amount, ref_price, reason)`.

    Los cuatro son obligatorios. Una llamada corta no falla al arrancar ni en
    los tests normales: falla EN CALIENTE, la primera vez que esa rama se
    ejecuta con dinero dentro, y el monitor se traga la excepción.
    """
    malas = [
        c.lineno for c in _llamadas_a_vender()
        if len(c.args) + len(c.keywords) < 4
    ]
    assert not malas, (
        f"llamadas a _sell_amount sin el motivo, en las lineas {malas}"
    )


def test_hay_llamadas_de_verdad_que_revisar() -> None:
    """Si el nombre del metodo cambia, el test de arriba pasaria en vacio."""
    assert len(_llamadas_a_vender()) >= 10


def test_el_anti_rug_vende_y_deja_su_motivo() -> None:
    """El motivo entra en `trades.log`, así que a partir de ahora se puede
    contar cuántas veces la salida de emergencia salvó la posición. Hasta hoy
    no hay ni una."""
    fuente = TRADER.read_text(encoding="utf-8")
    assert '"anti_rug"' in fuente


# ----------------------------------- el freno ya no amordaza al stop loss


def _trader() -> object:
    """Un objeto con lo justo para probar la clasificación de motivos."""
    from trading.universal_trader import UniversalTrader

    return SimpleNamespace(
        MOTIVOS_PROTECTORES=UniversalTrader.MOTIVOS_PROTECTORES,
        _es_venta_protectora=UniversalTrader._es_venta_protectora,
    )


def _es_protectora(reason: str) -> bool:
    t = _trader()
    return t._es_venta_protectora(t, reason)


def test_lo_que_limita_perdidas_cuenta_como_protector() -> None:
    """Estos son los que no se pueden abandonar: lo que se pierde por no
    ejecutarlos es mucho mayor que la comisión de reintentarlos."""
    for motivo in (
        "stop_loss", "trailing_stop", "anti_rug", "panic_sell",
        "bajo_entrada", "suelo_por_mc", "muerta", "max_hold_time",
        "manual_100pct", "manual_50pct",
    ):
        assert _es_protectora(motivo), f"{motivo} deberia ser protector"


def test_los_take_profit_no_son_protectores() -> None:
    """Un take profit SÍ puede esperar: si no sale ahora, sale luego o no
    sale, y cada intento cuesta comisión."""
    for motivo in ("tp_tier1_+25%", "tp_tier7_+900%", "final_target_mc",
                   "mc_tier_$34000"):
        assert not _es_protectora(motivo), f"{motivo} no deberia ser protector"


def test_el_stop_loss_espera_mucho_menos_que_un_take_profit() -> None:
    """Es toda la diferencia: el take profit puede esperar cinco minutos, el
    stop loss no."""
    from trading.universal_trader import UniversalTrader as T

    assert T.ESPERA_PROTECTORA_SEG < T.ESPERA_TP_SEG
    assert T.ESPERA_PROTECTORA_SEG <= 60
    assert T.ESPERA_MAX_SEG >= T.ESPERA_TP_SEG


def test_la_espera_se_dobla_y_tiene_techo() -> None:
    """Reproduce la cuenta del código. Sin techo, tras unos cuantos fallos la
    espera se iría a días y sería un abandono con otro nombre."""
    from trading.universal_trader import UniversalTrader as T

    base = T.ESPERA_PROTECTORA_SEG
    esperas = [
        min(base * (2 ** (f - T.MAX_FALLOS_VENTA)), T.ESPERA_MAX_SEG)
        for f in range(T.MAX_FALLOS_VENTA, T.MAX_FALLOS_VENTA + 12)
    ]
    assert esperas[0] == base
    assert esperas[1] == base * 2
    assert max(esperas) == T.ESPERA_MAX_SEG
    # Y en 24 h de bloqueo se gastan pocos intentos, no 660.
    total, intentos = 0.0, 0
    for e in esperas:
        if total > 86400:
            break
        total += e
        intentos += 1
    assert intentos < 30


def test_ya_no_existe_el_abandono_permanente() -> None:
    """El guardia tiene que mirar un RELOJ, no un contador sin vuelta atrás.

    Antes era `if self._fallos_venta.get(mint, 0) >= MAX_FALLOS_VENTA: return
    False`, y solo lo reiniciaba una venta con éxito -- que ya no podía ocurrir.
    """
    fuente = TRADER.read_text(encoding="utf-8")
    assert "_bloqueada_hasta" in fuente
    assert "if self._fallos_venta.get(mint, 0) >= self.MAX_FALLOS_VENTA:" not in fuente


# ------------------------------------------ la regla del polvo, con freno


def test_un_precio_cero_no_abandona_la_posicion() -> None:
    """`calculate_price` devuelve 0.0 cuando las reservas virtuales están a
    cero, en vez de lanzar. Esa regla hace `break`: deja la posición sin
    vigilar y sin stop loss, para siempre. Una lectura mala no puede hacer eso.
    """
    fuente = TRADER.read_text(encoding="utf-8")
    assert "if self.valor_minimo_posicion_usd > 0 and price > 0:" in fuente


def test_hacen_falta_dos_lecturas_seguidas_para_soltar() -> None:
    fuente = TRADER.read_text(encoding="utf-8")
    assert "_polvo_seguidas >= 2" in fuente


# --------------------------------------------- el P&L deja de inflarse


def test_el_cobro_no_se_suma_cuando_no_hubo_venta_manual() -> None:
    """`_check_manual_sell` vuelve sin tocar `_ultimo_cobro` cuando no hay
    orden -- que es casi siempre--, así que sumar a ciegas repetía el cobro de
    la última venta buena cuatro veces por segundo. Es el número con el que se
    juzga si la estrategia gana dinero.
    """
    fuente = TRADER.read_text(encoding="utf-8")
    assert "if remaining < _before_manual - 1e-12:" in fuente


# ------------------------------------- un bug ya no se queda en silencio


def test_un_fallo_de_programacion_avisa() -> None:
    """Un fallo de red es normal; una llamada mal escrita es SIEMPRE un bug y
    hasta hoy era invisible. Se sigue capturando --matar el monitor dejaría la
    posición sin vigilar-- pero avisa."""
    fuente = TRADER.read_text(encoding="utf-8")
    assert "except (TypeError, AttributeError)" in fuente
    assert "_bugs_avisados" in fuente


def test_el_aviso_de_bug_sale_una_vez_por_posicion() -> None:
    """El bucle da cuatro vueltas por segundo: avisar siempre sería
    inutilizable y acabaría con Manu silenciando el bot."""
    fuente = TRADER.read_text(encoding="utf-8")
    assert "if _mint_bug not in self._bugs_avisados:" in fuente


# ============================================================================
# EJERCITANDO EL CODIGO DE VERDAD, no leyendo el fichero.
#
# Todo lo de arriba comprueba que el codigo DICE lo que tiene que decir. Esto
# comprueba que HACE lo que tiene que hacer: se llama a `_sell_amount` con un
# vendedor falso que falla, y se mira a quien deja pasar y a quien no.
# ============================================================================

import asyncio

from trading.universal_trader import UniversalTrader


class _VendedorQueFalla:
    """Un vendedor que siempre falla, como en una caída rápida."""

    def __init__(self) -> None:
        self.intentos = 0
        self.comisiones: list[int | None] = []

    async def execute(
        self, _token, token_amount=0.0, token_price=0.0,
        priority_fee_override=None,
    ):
        self.intentos += 1
        # Se APUNTA la comision, no solo se acepta. Un doble que traga el
        # argumento y lo tira deja el test verde sin comprobar nada de lo que
        # el argumento existe para hacer.
        self.comisiones.append(priority_fee_override)
        return SimpleNamespace(
            success=False, price=0.0,
            error_message="6003 TooLittleSolReceived",
        )


def _trader_minimo(vendedor: object) -> UniversalTrader:
    """Un trader con lo justo que toca `_sell_amount`, sin constructor.

    El constructor de verdad abre wallet, nodo y websockets; aqui solo hace
    falta la maquinaria del freno.
    """
    t = object.__new__(UniversalTrader)
    t._fallos_venta = {}
    t._bloqueada_hasta = {}
    t._ultimo_cobro = 0.0
    t.seller = vendedor
    t.sell_slippage = 0.10
    t.wallet = SimpleNamespace(keypair=None, pubkey=None)

    async def _saldo(_ti):
        return 1000.0

    t._saldo_real_tokens = _saldo
    t._anota_firma = lambda *a, **k: None
    t._log_trade = lambda *a, **k: None
    return t


def _moneda() -> SimpleNamespace:
    return SimpleNamespace(
        mint="Er1sFZm3HGuXFiKeXQLg84H9B9Vqk3dp94AjLiS9pump", symbol="TEST",
    )


def _falla_hasta_el_tope(t: UniversalTrader, motivo: str) -> None:
    for _ in range(UniversalTrader.MAX_FALLOS_VENTA):
        asyncio.run(t._sell_amount(_moneda(), 10.0, 0.000001, motivo))


def test_de_verdad_el_stop_loss_vuelve_a_intentarlo_tras_la_espera() -> None:
    """EL FALLO QUE ESTO ARREGLA, ejercitado.

    Antes, tres fallos seguidos dejaban la moneda sin stop loss para siempre.
    Aqui se falla tres veces, se comprueba que efectivamente frena, se adelanta
    el reloj y se comprueba que VUELVE a intentarlo.
    """
    v = _VendedorQueFalla()
    t = _trader_minimo(v)
    _falla_hasta_el_tope(t, "stop_loss")
    assert v.intentos == UniversalTrader.MAX_FALLOS_VENTA

    # Justo despues frena: no se queman comisiones a lo tonto.
    asyncio.run(t._sell_amount(_moneda(), 10.0, 0.000001, "stop_loss"))
    assert v.intentos == UniversalTrader.MAX_FALLOS_VENTA, "no debia reintentar ya"

    # Pasada la espera, lo vuelve a intentar. Esto es lo que antes NO pasaba.
    t._bloqueada_hasta[str(_moneda().mint)] = 0.0
    asyncio.run(t._sell_amount(_moneda(), 10.0, 0.000001, "stop_loss"))
    assert v.intentos == UniversalTrader.MAX_FALLOS_VENTA + 1, (
        "el stop loss se rindio para siempre, que es justo el fallo"
    )


def test_de_verdad_el_stop_loss_espera_menos_que_un_take_profit() -> None:
    """La misma racha de fallos, distinto motivo, distinta espera."""
    from time import monotonic

    t_sl = _trader_minimo(_VendedorQueFalla())
    _falla_hasta_el_tope(t_sl, "stop_loss")
    espera_sl = t_sl._bloqueada_hasta[str(_moneda().mint)] - monotonic()

    t_tp = _trader_minimo(_VendedorQueFalla())
    _falla_hasta_el_tope(t_tp, "tp_tier1_+25%")
    espera_tp = t_tp._bloqueada_hasta[str(_moneda().mint)] - monotonic()

    assert espera_sl < espera_tp
    assert espera_sl <= UniversalTrader.ESPERA_PROTECTORA_SEG + 1


def test_de_verdad_una_venta_buena_borra_el_freno() -> None:
    """Si no, una moneda que se recupera arrastraria su castigo."""
    class _VendedorQueFunciona:
        def __init__(self) -> None:
            self.comisiones: list[int | None] = []

        async def execute(
            self, _token, token_amount=0.0, token_price=0.0,
            priority_fee_override=None,
        ):
            self.comisiones.append(priority_fee_override)
            return SimpleNamespace(
                success=True, price=0.000002, tx_signature="s",
                amount=token_amount,
            )

    t = _trader_minimo(_VendedorQueFalla())
    _falla_hasta_el_tope(t, "stop_loss")
    assert t._bloqueada_hasta

    t.seller = _VendedorQueFunciona()
    t._bloqueada_hasta[str(_moneda().mint)] = 0.0
    assert asyncio.run(t._sell_amount(_moneda(), 10.0, 0.000001, "stop_loss")) is True
    assert not t._bloqueada_hasta, "el freno tenia que quedar limpio"
    assert not t._fallos_venta


def test_de_verdad_el_anti_rug_llega_al_vendedor() -> None:
    """Antes esta llamada lanzaba TypeError antes de tocar al vendedor."""
    v = _VendedorQueFalla()
    t = _trader_minimo(v)
    asyncio.run(t._sell_amount(_moneda(), 10.0, 0.000001, "anti_rug"))
    assert v.intentos == 1


# ------------------------------------------------- la prisa donde hace falta


class _VendedorQueApunta:
    """Un vendedor que funciona y APUNTA con que comision le llamaron."""

    def __init__(self) -> None:
        self.comisiones: list[int | None] = []

    async def execute(
        self, _token, token_amount=0.0, token_price=0.0,
        priority_fee_override=None,
    ):
        self.comisiones.append(priority_fee_override)
        return SimpleNamespace(
            success=True, price=0.000002, tx_signature="s", amount=token_amount,
        )


def _trader_con_prisa(vendedor: object, fee: int = 41_666_667) -> UniversalTrader:
    t = _trader_minimo(vendedor)
    t.fee_venta_proteccion = fee
    return t


def test_el_stop_loss_paga_la_comision_rapida() -> None:
    """EL ARREGLO DEL 24-SEP, pedido: "los stop loss y take profit se demoran
    mucho".

    La comision de venta normal era 42 veces menor que la de compra (0,00012
    contra 0,005 SOL). Con la red cargada eso son segundos de cola -- justo
    mientras la moneda se cae, que es cuando cada segundo es precio perdido.

    Se comprueba que la comision LLEGA AL VENDEDOR, no solo que este en el
    yaml. Entre la configuracion y la transaccion hay tres saltos, y ya paso
    una vez que una comision se quedara por el camino: la de compra la
    recortaba el `hard_cap` en silencio y subirla no hacia nada.
    """
    v = _VendedorQueApunta()
    t = _trader_con_prisa(v)
    asyncio.run(t._sell_amount(_moneda(), 100.0, 0.000002, "stop_loss"))
    assert v.comisiones == [41_666_667]


def test_un_take_profit_no_la_paga() -> None:
    """LA OTRA MITAD, y es la que hace que esto no salga caro.

    Una posicion se compra UNA vez y se vende hasta SIETE --cuatro peldanos
    de porcentaje y tres escalones de market cap--. Si la comision rapida
    fuera para todas, se pagaria siete veces por posicion: 0,035 SOL, mas del
    20% de una compra de 0,146.

    Un take profit puede esperar: si no sale en esta vuelta sale en la
    siguiente y la moneda sigue arriba.
    """
    v = _VendedorQueApunta()
    t = _trader_con_prisa(v)
    asyncio.run(t._sell_amount(_moneda(), 100.0, 0.000002, "tp_tier1_+25%"))
    assert v.comisiones == [None], "un take profit paga la comision de siempre"


def test_todos_los_motivos_protectores_llevan_prisa() -> None:
    """La lista sale de `MOTIVOS_PROTECTORES`, no escrita a mano aqui.

    Es la misma que decide el suelo de venta ancho, asi que las dos
    protecciones se activan siempre juntas. Copiarla a mano aqui haria que
    anadir un motivo nuevo al codigo dejara este test verde sin cubrirlo.
    """
    for motivo in UniversalTrader.MOTIVOS_PROTECTORES:
        v = _VendedorQueApunta()
        t = _trader_con_prisa(v)
        asyncio.run(t._sell_amount(_moneda(), 100.0, 0.000002, motivo))
        assert v.comisiones == [41_666_667], f"{motivo} tendria que llevar prisa"


def test_a_cero_se_apaga_y_todas_pagan_igual() -> None:
    """Cero significa apagado, como en toda esta configuracion. Si significara
    "comision cero" la venta se quedaria sin prioridad ninguna."""
    v = _VendedorQueApunta()
    t = _trader_con_prisa(v, fee=0)
    asyncio.run(t._sell_amount(_moneda(), 100.0, 0.000002, "stop_loss"))
    assert v.comisiones == [None]
