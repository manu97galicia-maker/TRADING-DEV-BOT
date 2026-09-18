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
    volumen._ops[mint] = [
        (ahora, 0.5, True, f"wallet{i}") for i in range(compradores)
    ]
    if creacion:
        # `vio_la_creacion` mira que el tope de tokens visto sea el inicial.
        volumen._curva[mint] = (
            ahora, 700_000_000_000_000, 40_000_000_000,
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


def test_sin_haber_visto_nacer_la_moneda_no_se_compra() -> None:
    """Los contadores del bloque de creacion valen cero cuando no se vio
    nacer la moneda, asi que pasarian solos -- y eso es peor que no mirarlos,
    porque da un visto bueno que nadie ha dado. Se deja pasar la moneda."""
    f = _fuente()
    _poblar(MINT, 30, creacion=False, bloque_carteras=0)
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
        time.time(), int(volumen.RESERVA_TOKENS_INICIAL * 0.01),
        85_000_000_000, volumen.RESERVA_TOKENS_INICIAL,
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


def test_es_la_unica_fuente_encendida() -> None:
    """Lo pedido: probar SOLO esta condicion. Si alguien vuelve a encender
    otra, este test lo dice -- porque entonces las compras que se midan ya no
    serian de esta estrategia."""
    f = _filtros()
    otras = [
        n for n in ("buy_on_dex_paid", "birdeye_trending", "dextools_hotpools",
                    "curva_96", "telegram_calls", "discord_calls", "gmgn_signals")
        if (f.get(n) or {}).get("enabled")
    ]
    assert not otras, f"hay otras fuentes encendidas: {otras}"
    assert f["presion"]["enabled"] is True


def test_el_corte_esta_donde_el_ritmo_lo_permite() -> None:
    """12 y no 3. Con >=3 caen ~3 monedas por minuto y con 5 plazas el bot se
    llena en dos minutos con las PRIMERAS que pasen; a partir de ahi elige el
    reloj y no el filtro. Se pierden 1,6 puntos de acierto y se gana elegir.
    """
    assert _filtros()["presion"]["min_compradores_ola"] >= 8


def test_el_antibundle_esta_puesto_en_la_config() -> None:
    """Apagarlo convertiria esta fuente en un iman de pump and dump."""
    p = _filtros()["presion"]
    assert p["max_carteras_primer_bloque"] > 0
    assert p["max_sol_primer_bloque"] > 0


def test_compra_lo_acordado() -> None:
    assert _filtros()["presion"]["buy_amount"] == 0.07
