"""Compra al final de la bonding curve, sin pagar a nadie por la señal.

Lo que se pidió: entrar cuando la curva de pump.fun va por el 96%, con 0,2 SOL,
solo pump.fun, aunque no hayan pagado DexScreener; probar 3 y parar si dos
salen mal; y NO comprar bundles de pump and dump.

Lo que se comprueba aquí:
  - que el avance de la curva se lee bien del propio TradeEvent (sin RPC),
  - que un bundle se reconoce y se descarta,
  - que sin haber visto nacer la moneda no se compra a ciegas,
  - que el cupo de 3 y el corte a las 2 pérdidas funcionan y NO tocan al resto.
"""

# ruff: noqa: S101, SLF001, PLR2004, ANN003, ARG001, PLW0108

import asyncio
import base64
import struct
from pathlib import Path
from types import SimpleNamespace

import yaml

from monitoring import volumen
from monitoring.curva96_source import Curva96Source
from trading.universal_trader import UniversalTrader

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
INICIAL = volumen.RESERVA_TOKENS_INICIAL


def _evento(mint: bytes, sol: float, wallet: bytes, tokens_restantes: int) -> str:
    """Un TradeEvent de pump.fun tal y como sale en el log de la cadena."""
    cuerpo = mint + struct.pack("<QQ", int(sol * 1e9), 10**12) + b"\x01" + wallet
    cuerpo += struct.pack("<q", 0) + struct.pack("<QQ", 30 * 10**9, 900 * 10**12)
    cuerpo += struct.pack("<QQ", 80 * 10**9, tokens_restantes)
    cuerpo += bytes(32) + struct.pack("<QQ", 0, 0)
    return "Program data: " + base64.b64encode(volumen.DISC_TRADE + cuerpo).decode()


def _nueva_moneda(semilla: int) -> tuple[bytes, str]:
    mint = bytes([semilla]) * 32
    clave = volumen._b58(mint)
    volumen._olvidar(clave)
    return mint, clave


def _nace(mint: bytes, clave: str, *, carteras: int, sol: float, slot: int) -> None:
    """La creación y las compras que entran en su MISMO bloque."""
    logs = [
        _evento(mint, sol, bytes([100 + i]) * 32, INICIAL - (i + 1) * 10**12)
        for i in range(carteras)
    ]
    volumen.registrar_logs(logs, slot=slot)
    assert volumen.vio_la_creacion(clave)


def _sube_hasta(mint: bytes, clave: str, avance: float, *, compradores: int) -> None:
    """Gente distinta comprando hasta dejar la curva en ese punto."""
    restantes = int(INICIAL * (1 - avance))
    logs = [
        _evento(mint, 0.3, bytes([(i % 200) + 20]) * 32, max(restantes, INICIAL // 50))
        for i in range(compradores)
    ]
    logs[-1] = _evento(mint, 0.3, bytes([19]) * 32, restantes)
    volumen.registrar_logs(logs, slot=9000)


# --------------------------------------------------------------- leer la curva


def test_el_avance_de_la_curva_sale_del_propio_evento_sin_preguntar_al_nodo() -> None:
    mint, clave = _nueva_moneda(31)
    _nace(mint, clave, carteras=1, sol=0.5, slot=10)
    volumen.registrar_logs([_evento(mint, 1.0, bytes([5]) * 32, int(INICIAL * 0.04))])
    progreso, sol_en_curva = volumen.progreso_curva(clave)
    assert round(progreso, 4) == 0.96
    assert round(sol_en_curva, 2) == 80.0


def test_una_moneda_que_el_bot_no_vio_nacer_no_pasa_por_vista() -> None:
    """Sin el arranque de la curva no hay forma de saber si fue un bundle."""
    mint, clave = _nueva_moneda(32)
    # Solo se la ve ya avanzada: nunca tuvo las reservas llenas a la vista.
    volumen.registrar_logs([_evento(mint, 1.0, bytes([5]) * 32, int(INICIAL * 0.04))])
    assert volumen.vio_la_creacion(clave) is False


def test_un_evento_corto_no_inventa_una_curva() -> None:
    """Si el evento no trae reservas, no se responde con un avance falso."""
    mint, clave = _nueva_moneda(33)
    corto = (
        volumen.DISC_TRADE + mint + struct.pack("<QQ", 10**9, 1) + b"\x01" + bytes(32)
    )
    volumen.registrar_logs(["Program data: " + base64.b64encode(corto).decode()])
    assert volumen.progreso_curva(clave) is None
    assert volumen.operaciones(clave) == 1


# --------------------------------------------------------------------- bundles


def _fuente(**extra) -> Curva96Source:
    opciones = {
        "umbral": 0.96,
        "buy_amount": 0.2,
        "max_carteras_primer_bloque": 3,
        "max_sol_primer_bloque": 2.0,
        "min_compradores": 30,
    }
    opciones.update(extra)
    return Curva96Source("wss://x", SimpleNamespace(), SimpleNamespace(), **opciones)


def test_muchas_carteras_en_el_bloque_de_creacion_son_un_bundle() -> None:
    """La firma clásica: seis carteras comprando a la vez que nace la moneda."""
    mint, clave = _nueva_moneda(34)
    _nace(mint, clave, carteras=6, sol=0.2, slot=20)
    _sube_hasta(mint, clave, 0.96, compradores=60)
    assert _fuente()._es_bundle(clave, "BUNDLE", con_historia=True) is True


def test_un_dev_buy_normal_no_es_un_bundle() -> None:
    mint, clave = _nueva_moneda(35)
    _nace(mint, clave, carteras=1, sol=0.5, slot=21)
    _sube_hasta(mint, clave, 0.96, compradores=60)
    assert _fuente()._es_bundle(clave, "LIMPIA", con_historia=True) is False


def test_cargar_demasiado_sol_al_nacer_es_un_bundle() -> None:
    """Dos carteras pueden parecer poco; cinco SOL en el bloque cero no."""
    mint, clave = _nueva_moneda(36)
    _nace(mint, clave, carteras=2, sol=3.0, slot=22)
    _sube_hasta(mint, clave, 0.96, compradores=60)
    assert _fuente()._es_bundle(clave, "CARGADA", con_historia=True) is True


def test_una_curva_llena_por_cuatro_gatos_se_descarta() -> None:
    """96% de curva con 5 compradores distintos no es demanda, es un grupo."""
    mint, clave = _nueva_moneda(37)
    _nace(mint, clave, carteras=1, sol=0.5, slot=23)
    _sube_hasta(mint, clave, 0.96, compradores=5)
    assert _fuente()._es_bundle(clave, "POCOS", con_historia=True) is True


def test_sin_historia_el_primer_bloque_visto_no_es_el_de_creacion() -> None:
    """La trampa del modo flexible, y es peor que un contador a cero.

    En una moneda que el bot no vio nacer, «carteras del primer bloque» NO
    vale cero: vale las del primer bloque que el bot casualmente miró, que
    puede ser cualquiera de su vida. Ese número no dice nada sobre si hubo
    bundle, y sin embargo dispararía el filtro igual que si lo dijera. Por eso
    a ciegas esos dos avisos se saltan en vez de dejar que opinen.
    """
    mint, clave = _nueva_moneda(51)
    # Solo se la ve ya avanzada, y el primer vistazo pilla muchas carteras.
    _sube_hasta(mint, clave, 0.96, compradores=60)
    assert volumen.vio_la_creacion(clave) is False
    # El contador tiene datos, pero son de un bloque cualquiera, no del de
    # creación: exactamente por eso no se le puede hacer caso.
    assert volumen.carteras_primer_bloque(clave) > 0

    fuente = _fuente()
    # Con historia opinaría; a ciegas se salta y manda el recuento de
    # compradores, que sí funciona con historia parcial.
    assert fuente._es_bundle(clave, "CIEGA", con_historia=False) is False


def test_sin_historia_los_pocos_compradores_siguen_cortando() -> None:
    """Es el único aviso del stream que sigue valiendo a ciegas."""
    mint, clave = _nueva_moneda(52)
    _sube_hasta(mint, clave, 0.96, compradores=4)
    assert _fuente()._es_bundle(clave, "CIEGA", con_historia=False) is True


def test_a_ciegas_el_tope_en_cadena_va_mas_apretado() -> None:
    """Compensa lo que se deja de mirar: si no se vio nacer la moneda, los
    mayores titulares tienen que concentrar menos para dejarla pasar."""
    fuente = _fuente()
    assert fuente.max_bundle_pct_sin_historia < fuente.max_bundle_pct


# ------------------------------------------------------------ umbral y disparo


def test_solo_dispara_dentro_de_la_ventana() -> None:
    """Por debajo del 96% aún no toca; pasado el tope ya está migrando."""
    fuente = _fuente()
    lanzadas: list[str] = []
    fuente._lanzar = lambda mint, _cb: lanzadas.append(mint)

    claves = {}
    # 0,90 esta por debajo del umbral (0,95), 0,97 dentro, 0,999 pasado el
    # tope: la de en medio es la unica que debe disparar.
    for semilla, avance in ((41, 0.90), (42, 0.97), (43, 0.999)):
        mint, clave = _nueva_moneda(semilla)
        claves[avance] = clave
        _nace(mint, clave, carteras=1, sol=0.5, slot=30 + semilla)
        _sube_hasta(mint, clave, avance, compradores=40)
        fuente._revisar([clave], None)

    # Solo la del 97%: la del 90% aun no llega y la del 99,9% ya esta migrando.
    assert lanzadas == [claves[0.97]]


def test_cada_moneda_se_decide_una_sola_vez() -> None:
    """Cruzado el umbral, sigue cruzado en cada operación: no vale repetir."""
    fuente = _fuente()
    lanzadas: list[str] = []
    fuente._lanzar = lambda mint, _cb: lanzadas.append(mint)
    mint, clave = _nueva_moneda(44)
    _nace(mint, clave, carteras=1, sol=0.5, slot=44)
    _sube_hasta(mint, clave, 0.97, compradores=40)
    for _ in range(10):
        fuente._revisar([clave], None)
    assert lanzadas == [clave]


def test_sin_ver_la_creacion_no_se_compra() -> None:
    """Se deja pasar en vez de comprar sin poder mirar si es un bundle."""
    mint, clave = _nueva_moneda(45)
    volumen.registrar_logs([_evento(mint, 1.0, bytes([5]) * 32, int(INICIAL * 0.03))])
    fuente = _fuente()
    comprada: list[object] = []
    asyncio.run(fuente._evaluar(clave, lambda ti: comprada.append(ti)))
    assert comprada == []
    assert fuente._embudo.get("sin_historia") == 1


# ------------------------------------------------------- cupo y corte de pérdidas


def _trader(cupo: int = 3, perdidas: int = 2) -> UniversalTrader:
    t = UniversalTrader.__new__(UniversalTrader)
    t.curva_96 = {"max_compras": cupo, "parar_tras_perdidas": perdidas}
    t._compras_curva96 = 0
    t._perdidas_curva96 = 0
    return t


def _moneda(origen: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        symbol="TEST", additional_data={"origen": origen} if origen else {}
    )


def test_el_cupo_deja_pasar_tres_y_corta_la_cuarta() -> None:
    trader = _trader(cupo=3)
    abiertas = []
    for _ in range(4):
        moneda = _moneda("curva96")
        if trader._curva96_agotada(moneda):
            abiertas.append("cortada")
            continue
        trader._apunta_compra_por_origen(moneda)  # la compra entra
        abiertas.append("comprada")
    assert abiertas == ["comprada", "comprada", "comprada", "cortada"]


def test_una_compra_que_no_llega_a_entrar_no_gasta_cupo() -> None:
    """Al 96% la moneda puede migrar y la transacción falla.

    Contando intentos, tres fallos se comían la prueba entera sin haber
    abierto una sola posición. El cupo cuenta posiciones ABIERTAS.
    """
    trader = _trader(cupo=3)
    for _ in range(50):
        assert trader._curva96_agotada(_moneda("curva96")) is False
    assert trader._compras_curva96 == 0


def test_el_cupo_solo_cuenta_las_de_su_procedencia() -> None:
    trader = _trader(cupo=3)
    trader.buy_on_dex_paid = {"otros_max_compras": 3}
    trader._compras_otro_origen = 0
    for origen in (None, "otro"):
        trader._apunta_compra_por_origen(_moneda(origen))
    assert trader._compras_curva96 == 0


def test_dos_perdidas_paran_la_curva() -> None:
    trader = _trader(cupo=99, perdidas=2)
    trader._curva96_agotada(_moneda("curva96"))
    for _ in range(2):
        trader._apunta_cierre_curva96(_moneda("curva96"), -0.05)
    assert trader._curva96_agotada(_moneda("curva96")) is True


def test_las_ganancias_no_cuentan_como_perdida() -> None:
    trader = _trader(cupo=99, perdidas=2)
    for _ in range(5):
        trader._apunta_cierre_curva96(_moneda("curva96"), +0.08)
    assert trader._perdidas_curva96 == 0


def test_este_regimen_no_toca_a_las_demas_fuentes() -> None:
    """Lo pedido: las de pump.fun de siempre siguen exactamente igual."""
    trader = _trader()
    for origen in (None, "otro"):
        for _ in range(20):
            assert trader._curva96_agotada(_moneda(origen)) is False
    assert trader._compras_curva96 == 0


# ---------------------------------------------------------------- configuracion


def test_la_config_del_bot_pide_lo_que_se_acordo() -> None:
    """Los ajustes de la curva, que desde el 23-sep es LA estrategia.

    Historial: encendida, apagada el 18-sep para probar solo la de presion,
    y encendida otra vez el 23-sep sustituyendo a la de titulares ("cambia la
    estrategia a las que estan al 96% de migracion").
    """
    cfg = yaml.safe_load(CONFIG.read_text())["filters"]["curva_96"]
    assert cfg["enabled"] is True
    assert cfg["umbral"] == 0.96
    # La ventana de disparo: entre el umbral y el tope. Por encima del tope
    # la moneda ya esta migrando y comprar ahi es tirar la comision.
    assert cfg["tope"] > cfg["umbral"]
    # A cero: manda el 15% del saldo, igual que en todas las demas fuentes.
    assert cfg["buy_amount"] == 0
    # SIN TOPE DE PRUEBA. "3 compras y si dos salen mal se para sola" era lo
    # correcto para una prueba acotada; siendo LA estrategia, dejaria el bot
    # mudo tras el tercer intento sin que nada lo gritara.
    assert cfg["max_compras"] == 0
    assert cfg["parar_tras_perdidas"] == 0
    # Modo flexible: se compran tambien las que no se vieron nacer, pero con
    # el tope de concentracion en cadena mas apretado para compensar.
    assert cfg["exigir_ver_creacion"] is False
    assert cfg["max_bundle_pct_sin_historia"] < cfg["max_bundle_pct"]
