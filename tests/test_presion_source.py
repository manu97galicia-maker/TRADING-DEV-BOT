"""La fuente de compra por presion compradora: lo que dispara y lo que NO.

DE DONDE SALE. Se midieron 27.241 monedas de pump.fun apuntando sus senales a
los 20 segundos de vida y siguiendolas cinco minutos. Lo que mas predijo un
pump no fue la presion como ratio (compras contra ventas) sino el NUMERO DE
COMPRADORES DISTINTOS en una ventana corta: del 14,4% de base al 27,4%.

LO QUE MAS IMPORTA DE ESTOS TESTS es el anti-bundle. Un bundle ES "muchas
carteras comprando a la vez", que es exactamente lo que esta fuente busca; la
unica diferencia es CUANDO entran. Sin ese freno, este filtro no seria un
detector de demanda: seria un iman de pump and dump, comprando justo lo que
Manu lleva pidiendo evitar desde el primer dia.
"""

# ruff: noqa: S101, SLF001, ANN003, ANN202, PLR2004, ARG005

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from monitoring import volumen
from monitoring.presion_source import PresionSource

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
MINT = "Er1sFZm3HGuXFiKeXQLg84H9B9Vqk3dp94AjLiS9pump"


def _filtros() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]


def _fuente(**kw) -> PresionSource:
    opciones = {
        "min_compradores_ola": 12,
        "ventana": 5.0,
        "buy_amount": 0.07,
        "heartbeat_interval": 0,
    }
    opciones.update(kw)
    return PresionSource("wss://x", SimpleNamespace(), SimpleNamespace(), **opciones)


def _poblar(mint: str, compradores: int, *, creacion: bool, bloque_carteras: int = 0):
    """Deja `volumen` como si esas carteras acabaran de comprar."""
    volumen._ops.clear()
    volumen._curva.clear()
    volumen._first_slot.clear()
    volumen._first_slot_wallets.clear()
    volumen._first_slot_sol.clear()
    ahora = time.time()
    volumen._ops[mint] = [(ahora, 0.5, True, f"wallet{i}") for i in range(compradores)]
    if creacion:
        # `vio_la_creacion` mira que el tope de tokens visto sea el inicial.
        volumen._curva[mint] = (
            ahora,
            700_000_000_000_000,
            40_000_000_000,
            volumen.RESERVA_TOKENS_INICIAL,
        )
    else:
        volumen._curva[mint] = (ahora, 700_000_000_000_000, 40_000_000_000, 1)
    if bloque_carteras:
        volumen._first_slot_wallets[mint] = {
            f"bundle{i}" for i in range(bloque_carteras)
        }


# ------------------------------------------------------- la senal dispara


def test_dispara_con_bastantes_compradores_distintos() -> None:
    f = _fuente(min_compradores_ola=12)
    _poblar(MINT, 15, creacion=True)
    assert f._oleada(MINT) >= f.min_compradores_ola


def test_no_dispara_con_pocos() -> None:
    f = _fuente(min_compradores_ola=12)
    _poblar(MINT, 4, creacion=True)
    assert f._oleada(MINT) < f.min_compradores_ola


def test_la_senal_cuenta_carteras_distintas_no_operaciones() -> None:
    """LA DIFERENCIA QUE LO DECIDE TODO. Una sola wallet comprando cincuenta
    veces puede fabricar volumen y puede fabricar el ratio compras/ventas.
    Lo que no puede fabricar son carteras distintas sin que se vea."""
    f = _fuente(min_compradores_ola=12)
    ahora = time.time()
    volumen._ops.clear()
    volumen._ops[MINT] = [(ahora, 0.5, True, "la_misma") for _ in range(50)]
    assert f._oleada(MINT) == 1


# --------------------------------------------------------- el anti-bundle


def test_un_bundle_en_el_bloque_de_creacion_se_descarta() -> None:
    """EL FRENO QUE JUSTIFICA LA FUENTE ENTERA."""
    f = _fuente(max_carteras_primer_bloque=3)
    _poblar(MINT, 20, creacion=True, bloque_carteras=9)
    assert f._es_bundle(MINT, "TEST") is True


def test_una_oleada_limpia_pasa() -> None:
    f = _fuente(max_carteras_primer_bloque=3)
    _poblar(MINT, 20, creacion=True, bloque_carteras=1)
    assert f._es_bundle(MINT, "TEST") is False


def test_en_modo_estricto_sin_ver_nacer_no_se_compra() -> None:
    """Los contadores del bloque de creacion valen cero cuando no se vio
    nacer la moneda, asi que pasarian solos -- y eso es peor que no mirarlos,
    porque da un visto bueno que nadie ha dado."""
    f = _fuente(exigir_ver_creacion=True)
    _poblar(MINT, 30, creacion=False, bloque_carteras=0)
    assert f._es_bundle(MINT, "TEST") is True


def test_en_modo_flexible_sin_ver_nacer_si_se_compra() -> None:
    """LO QUE SE ACTIVO EL 18-SEP, y lo que cuesta.

    En estricto el bot arrancaba CIEGO: solo podia comprar lo que naciera
    despues del arranque, y fueron cinco minutos sin una sola compra. En
    flexible se compran tambien las que ya existian.

    Lo que se pierde: de esas NO se puede saber si el bloque de creacion
    estaba cargado de carteras, que es la firma del bundle. El unico freno
    que queda es el tope de concentracion en cadena, y por eso va mas
    apretado para ellas (test de abajo).
    """
    f = _fuente(exigir_ver_creacion=False)
    _poblar(MINT, 30, creacion=False, bloque_carteras=0)
    assert f._es_bundle(MINT, "TEST") is False


def test_sin_historia_el_tope_de_concentracion_va_mas_apretado() -> None:
    """Es lo unico que protege a las que no se vio nacer, asi que pide mas."""
    f = _fuente(
        exigir_ver_creacion=False,
        max_bundle_pct=0.5,
        max_bundle_pct_sin_historia=0.35,
    )
    assert f.max_bundle_pct_sin_historia < f.max_bundle_pct


def test_el_modo_flexible_no_se_salta_el_antibundle_de_las_que_si_vio() -> None:
    """Aflojar para las ciegas no puede aflojar para las demas: una moneda
    cuyo bloque de creacion SI se vio y venia cargado se sigue descartando."""
    f = _fuente(exigir_ver_creacion=False, max_carteras_primer_bloque=3)
    _poblar(MINT, 20, creacion=True, bloque_carteras=9)
    assert f._es_bundle(MINT, "TEST") is True


def test_demasiado_sol_en_el_bloque_de_creacion_se_descarta() -> None:
    f = _fuente(max_sol_primer_bloque=2.0)
    _poblar(MINT, 20, creacion=True, bloque_carteras=1)
    volumen._first_slot_sol[MINT] = 8.0
    assert f._es_bundle(MINT, "TEST") is True


# ------------------------------------------------------ no pisar la curva


def test_una_curva_casi_llena_la_deja_para_la_otra_fuente() -> None:
    """Comprar la misma idea dos veces con dinero distinto no es diversificar.
    Por encima del 95% manda `curva_96`, con sus propias reglas."""
    f = _fuente(min_compradores_ola=1, max_avance_curva=0.95)
    _poblar(MINT, 30, creacion=True)
    # Curva al 99%: queda poquisimo token por vender.
    volumen._curva[MINT] = (
        time.time(),
        int(volumen.RESERVA_TOKENS_INICIAL * 0.01),
        85_000_000_000,
        volumen.RESERVA_TOKENS_INICIAL,
    )
    lanzadas = []
    f._lanzar = lambda mint, cb: lanzadas.append(mint)
    f._revisar([MINT], None)
    assert lanzadas == []


# -------------------------------------------------- se decide UNA sola vez


def test_una_moneda_solo_se_decide_una_vez() -> None:
    """Sin esto, una moneda que cruza el corte lo cruzaria en CADA operacion
    posterior y se intentaria comprar cientos de veces."""
    f = _fuente(min_compradores_ola=1)
    _poblar(MINT, 30, creacion=True)
    lanzadas = []
    f._lanzar = lambda mint, cb: lanzadas.append(mint)
    for _ in range(5):
        f._revisar([MINT], None)
    assert lanzadas == [MINT]


def test_no_se_desbordan_las_evaluaciones_en_vuelo() -> None:
    """Cada evaluacion son varias llamadas al nodo. Sin tope, una racha se
    comeria el presupuesto que necesitan los stop loss de lo ya comprado."""
    f = _fuente()

    async def _corre() -> None:
        f._en_vuelo = {asyncio.create_task(asyncio.sleep(0.05)) for _ in range(4)}
        lanzadas = []
        f._evaluar = lambda m, cb: lanzadas.append(m)
        f._lanzar("otra", None)
        assert lanzadas == []
        for t in f._en_vuelo:
            t.cancel()

    asyncio.run(_corre())


# ------------------------------------------------------------ la config


def test_esta_apagada_y_sigue_entera() -> None:
    """APAGADA EL 11-oct, pedido: "deja solo la regla de comprar al 95% de la
    bonding curve". Encendida estuvo solo del 09 al 11-oct.

    ESTE TEST SE HA DADO LA VUELTA DOS VECES, y el nombre va con ella: fijaba
    que la fuente no se borrara al apagarla (19-sep), paso a fijar que era la
    unica encendida (09-oct) y vuelve a lo primero. Es el mismo contrato en
    los dos sentidos: apagar una fuente NO es borrarla, porque lo que cuesta
    dias es tener que reconstruir los numeros al volver.

    LO QUE SE PERDIO AL APAGARLA, y esta escrito aqui para que no se olvide:
    esta era la UNICA parte del bot con medicion detras. Sobre 27.241 monedas
    seguidas cinco minutos, la probabilidad de hacer +25% pasaba del 14,4%
    de base al 27,4% con 3+ compradores distintos en la ventana. La fuente
    que corre ahora --la de la curva-- no tiene ninguna medicion detras.
    """
    f = _filtros()
    assert f["presion"]["enabled"] is False
    # Y SIGUE ENTERA: volver tiene que ser cambiar un `enabled`.
    assert f["presion"]["min_compradores_ola"] == 8
    assert f["presion"]["max_carteras_primer_bloque"] > 0
    assert f["presion"]["ventana"] > 0
    assert f["presion"]["buy_amount"] == 0


def test_la_curva_es_la_unica_fuente_encendida() -> None:
    """POR QUE IMPORTA QUE HAYA UNA SOLA. Las fuentes se reparten las mismas
    plazas de `max_posiciones`. Con dos encendidas, una movida llena la
    cartera antes de que la otra vea nada, y luego no se sabe cual de las dos
    lo hizo. Lo pedido desde el 24-sep es tener UNA, para poder medirla.
    """
    f = _filtros()
    fuentes = ("presion", "curva_96", "holders", "telegram_calls", "buy_on_dex_paid")
    encendidas = [k for k in fuentes if (f.get(k) or {}).get("enabled")]
    assert encendidas == ["curva_96"], f"fuentes encendidas: {encendidas}"


def test_el_corte_es_el_pedido() -> None:
    """8, pedido por Manu el 19-sep. Ha cambiado tres veces y el test lo fija
    para que cualquier cambio futuro sea deliberado.

    LO QUE DICEN LOS DATOS y no cambia por elegir otro numero: sobre 27.241
    monedas medidas, el 8 es el que PEOR acierta de los tres que se han usado
    -- 24,5%, contra el 27,4% del 3 y el 25,8% del 12. El 12 se habia elegido
    por el RITMO (con 5 plazas, 3 monedas por minuto llenan el bot en dos
    minutos con las PRIMERAS que pasen, no con las mejores); el 3, por acierto,
    tras cinco minutos sin una sola compra.

    Queda anotado para que el dia que se revise no haya que volver a medirlo.
    """
    assert _filtros()["presion"]["min_compradores_ola"] == 8


def test_el_antibundle_esta_puesto_en_la_config() -> None:
    """Apagarlo convertiria esta fuente en un iman de pump and dump."""
    p = _filtros()["presion"]
    assert p["max_carteras_primer_bloque"] > 0
    assert p["max_sol_primer_bloque"] > 0


def test_al_ir_a_ciegas_la_config_aprieta_la_concentracion() -> None:
    """La config esta en modo flexible desde el 18-sep, asi que el unico
    freno que le queda a esas monedas tiene que pedir MAS que el normal. Si
    alguien iguala los dos numeros, se queda sin proteccion ninguna."""
    p = _filtros()["presion"]
    if p.get("exigir_ver_creacion") is False:
        assert p["max_bundle_pct_sin_historia"] < p["max_bundle_pct"]


def test_el_importe_lo_decide_el_porcentaje_de_cartera() -> None:
    """A CERO, o el 30% de la cartera no se aplicaria (09-oct).

    El importe de la SENAL pisa al global: con 0,07 aqui, cada compra serian
    0,07 SOL y `fraccion_saldo: 0.30` no haria nada. Ya paso una vez con la
    fuente de titulares, y por eso este test empareja los dos numeros en vez
    de fijar uno suelto.
    """
    raiz = Path(__file__).resolve().parent.parent
    cfg = yaml.safe_load((raiz / "bots/bot-paper-ai-entry.yaml").read_text())
    assert _filtros()["presion"]["buy_amount"] == 0
    assert cfg["trade"]["buy_amount"] == 0, "el global tambien tiene que estar a 0"
    assert cfg["trade"]["fraccion_saldo"] > 0, "si no, no manda nadie y no compra"
