"""La estrategia de los titulares: las DOS condiciones, y lo que se perdio.

LO PEDIDO, literal: "que me compre cuando haya 99 holders en una moneda y que
tenga menos de cinco minutos de creacion, solo esas dos condiciones, no quiero
mas condiciones".

EL TEST MAS IMPORTANTE DE ESTE FICHERO es el ultimo, y no comprueba que algo
funcione: deja escrito que un bundle pasa. Un bundle se monta con muchas
carteras comprando a la vez que nace la moneda, asi que cumple LAS DOS
condiciones a la perfeccion -- muchos titulares, recien creada. Es exactamente
lo que se llevaba meses evitando. Se hizo asi porque se pidio asi, y el dia que
esta estrategia salga mal este test dice por que.
"""

# ruff: noqa: S101, SLF001, ANN003, PLR2004

import asyncio
import contextlib
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from monitoring import volumen
from monitoring.holders_source import (
    _MAX_RECORDADAS,
    HoldersSource,
    build_holders_heartbeat,
)

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
MINT = "Er1sFZm3HGuXFiKeXQLg84H9B9Vqk3dp94AjLiS9pump"


def _filtros() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]


def _fuente(**kw) -> HoldersSource:
    opciones = {
        "min_holders": 90,
        "max_edad_seg": 300.0,
        "buy_amount": 0.07,
        "heartbeat_interval": 0,
    }
    opciones.update(kw)
    return HoldersSource("wss://x", SimpleNamespace(), SimpleNamespace(), **opciones)


def _poblar(
    mint: str, compradores: int, *, creacion: bool, edad: float = 10.0
) -> None:
    """Deja `volumen` como si esas carteras hubieran comprado hace `edad` s."""
    volumen._ops.clear()
    volumen._curva.clear()
    volumen._first_slot.clear()
    volumen._first_slot_wallets.clear()
    volumen._first_slot_sol.clear()
    nacimiento = time.time() - edad
    volumen._ops[mint] = [
        (nacimiento, 0.5, True, f"wallet{i}") for i in range(compradores)
    ]
    if creacion:
        # `vio_la_creacion` mira que el tope de tokens visto sea el inicial.
        volumen._curva[mint] = (
            time.time(), 700_000_000_000_000, 40_000_000_000,
            volumen.RESERVA_TOKENS_INICIAL,
        )
    else:
        volumen._curva[mint] = (time.time(), 700_000_000_000_000, 40_000_000_000, 1)


def _revisa(f: HoldersSource, mints: list[str]) -> list[str]:
    """Corre `_revisar` de verdad y devuelve los mints que llegaron a comprar.

    Hace falta un bucle de eventos porque `_lanzar` crea una tarea. Antes del
    freno del cruce ninguna moneda de estos tests llegaba tan lejos, asi que se
    podia llamar en seco; ahora si llegan, y eso es justo lo que se quiere
    comprobar.
    """
    comprados: list[str] = []

    async def _cb(token_info: object) -> None:
        comprados.append(str(getattr(token_info, "mint", "")))

    async def _corre() -> None:
        f._revisar(mints, _cb)
        # Deja terminar las tareas que `_lanzar` acaba de crear.
        for tarea in list(f._en_vuelo):
            with contextlib.suppress(Exception):
                await tarea

    asyncio.run(_corre())
    return comprados


# ------------------------------------------------- condicion 1: titulares


def test_no_dispara_con_menos_titulares_de_los_pedidos() -> None:
    f = _fuente(min_holders=90)
    _poblar(MINT, 89, creacion=True)
    assert volumen.titulares(MINT) < f.min_holders


def test_dispara_justo_al_llegar_al_corte() -> None:
    """90 significa 90, no 91: el corte es `< min_holders` descarta."""
    f = _fuente(min_holders=90)
    _poblar(MINT, 90, creacion=True)
    assert volumen.titulares(MINT) >= f.min_holders


def test_los_titulares_son_carteras_distintas_no_operaciones() -> None:
    """Una wallet comprando doscientas veces no son doscientos titulares.

    Es la unica proteccion que queda de serie contra la senal fabricada: el
    volumen y el numero de compras se falsifican con una cartera sola; las
    carteras distintas, no.
    """
    volumen._ops.clear()
    volumen._ops[MINT] = [(time.time(), 0.5, True, "la_misma") for _ in range(200)]
    assert volumen.titulares(MINT) == 1


def test_quien_vende_deja_de_contar() -> None:
    """Un titular es quien compro y NO ha soltado."""
    ahora = time.time()
    volumen._ops.clear()
    volumen._ops[MINT] = [
        (ahora, 0.5, True, "a"),
        (ahora, 0.5, True, "b"),
        (ahora, 0.5, False, "b"),   # b sale entera
    ]
    assert volumen.titulares(MINT) == 1


# ------------------------------------------------------ condicion 2: edad


def test_una_moneda_joven_pasa() -> None:
    f = _fuente(max_edad_seg=300.0)
    _poblar(MINT, 120, creacion=True, edad=60.0)
    edad = f._edad(MINT)
    assert edad is not None
    assert edad <= f.max_edad_seg


def test_una_moneda_vieja_se_descarta() -> None:
    f = _fuente(max_edad_seg=300.0)
    _poblar(MINT, 120, creacion=True, edad=900.0)
    edad = f._edad(MINT)
    assert edad is not None
    assert edad > f.max_edad_seg


def test_sin_ver_nacer_la_edad_es_desconocida_y_no_se_compra() -> None:
    """NO es una tercera condicion: es la unica forma de medir la segunda.

    De una moneda que ya existia cuando arranco el bot, lo unico medible es
    cuanto hace que se la mira -- que en una de dos horas pueden ser diez
    segundos. Devolver ese numero seria comprar creyendo que es nueva.
    """
    f = _fuente()
    _poblar(MINT, 300, creacion=False, edad=5.0)
    assert f._edad(MINT) is None


def test_exigir_ver_nacer_cuesta_poco_con_el_tope_que_haya() -> None:
    """Exigir haber visto nacer la moneda solo quita candidatas durante los
    primeros `max_edad_seg` segundos despues de arrancar el bot: pasado ese
    rato, cualquier moneda lo bastante joven nacio con el bot escuchando.

    Con el tope en 20 minutos (19-sep) esa ventana ciega del arranque es de 20
    minutos, no de 5. Sigue siendo una vez por arranque, pero conviene saberlo:
    justo despues de un reinicio el bot compra menos.
    """
    tope = _filtros()["holders"]["max_edad_seg"]
    assert tope <= 3600, "por encima de una hora la ventana ciega ya pesa"


# --------------------------------------------------- las dos, y solo las dos


def test_el_embudo_cuenta_cada_caida_por_separado() -> None:
    """"0 compradas" puede ser que no llegue nada o que llegue y se caiga todo
    por el camino, y son problemas opuestos con arreglos opuestos."""
    f = _fuente(min_holders=90)
    _poblar(MINT, 10, creacion=True)
    _revisa(f, [MINT])
    assert f._embudo["vistas"] == 1
    assert f._embudo.get("con_titulares", 0) == 0

    f = _fuente(min_holders=90, max_edad_seg=300.0)
    _poblar(MINT, 40, creacion=True, edad=900.0)
    _revisa(f, [MINT])                      # se ve pequena: queda apuntada
    _poblar(MINT, 120, creacion=True, edad=900.0)
    _revisa(f, [MINT])                      # cruza, pero ya es vieja
    assert f._embudo["con_titulares"] == 1
    assert f._embudo["viejas"] == 1

    f = _fuente(min_holders=90)
    _poblar(MINT, 40, creacion=False)
    _revisa(f, [MINT])
    _poblar(MINT, 120, creacion=False)
    _revisa(f, [MINT])
    assert f._embudo["sin_historia"] == 1


def test_una_moneda_se_decide_una_sola_vez() -> None:
    """Sin esto, la que cruza el corte lo seguiria cruzando en CADA operacion
    que llegue y el bot mandaria la misma compra veinte veces."""
    f = _fuente(min_holders=90)
    _poblar(MINT, 120, creacion=True)
    assert f._ya_decidida(MINT) is False
    assert f._ya_decidida(MINT) is True


def test_el_parte_no_lleva_nada_del_env() -> None:
    texto = build_holders_heartbeat(90, 300.0, 0.07, {"vistas": 4}, paper_mode=False)
    bajo = texto.lower()
    for veneno in ("private", "key", "wss://", "http", "@"):
        assert veneno not in bajo


# ---------------------------------------------------- la configuracion real


def test_es_la_unica_fuente_encendida() -> None:
    f = _filtros()
    otras = [
        n for n in ("buy_on_dex_paid", "birdeye_trending", "dextools_hotpools",
                    "curva_96", "presion", "telegram_calls", "discord_calls",
                    "gmgn_signals")
        if (f.get(n) or {}).get("enabled")
    ]
    assert not otras, f"hay otras fuentes encendidas: {otras}"
    assert f["holders"]["enabled"] is True


def test_los_dos_numeros_son_los_pedidos() -> None:
    h = _filtros()["holders"]
    # Historial de estos dos numeros, para que un cambio sea siempre
    # deliberado: 99/5min (19-sep) -> 90/5min -> 60/20min, todos pedidos.
    assert h["min_holders"] == 60
    assert h["max_edad_seg"] == 1200
    assert h["buy_amount"] == 0.12


def test_ningun_filtro_global_puede_descartar_por_su_cuenta() -> None:
    """"Solo esas dos condiciones" tambien vale para lo que ya estaba puesto.

    El global `max_holder_pct` valia 0,15 y se aplicaba a TODAS las fuentes:
    habria descartado monedas sin que ninguna de las dos reglas lo pidiera, y
    en silencio -- que es justo como el bot estuvo dias encendido sin comprar.
    """
    f = _filtros()
    for clave in (
        "max_holder_pct", "min_holders", "min_volumen_sol", "min_buys",
        "min_ops", "min_titulares", "min_presion_ratio", "max_dev_hold_pct",
        "min_curve_sol", "min_entry_mc", "max_entry_mc",
    ):
        assert not f.get(clave, 0), f"el filtro global '{clave}' sigue activo"


def test_queda_escrito_que_esto_no_mira_bundles() -> None:
    """EL AVISO. Un bundle cumple las dos condiciones a la perfeccion: muchas
    carteras comprando a la vez que nace la moneda es, visto desde fuera,
    exactamente lo que esta estrategia busca. No hay nada aqui que lo pare.

    El freno sigue en el repo, en la fuente de presion (apagada), con su
    `max_carteras_primer_bloque`. Volver es cambiar dos `enabled`.
    """
    fuente = Path(__file__).resolve().parent.parent / "src/monitoring/holders_source.py"
    texto = fuente.read_text(encoding="utf-8")
    assert "bundle" in texto.lower(), "el aviso del bundle tiene que estar escrito"
    assert "max_carteras_primer_bloque" not in texto.replace(
        "`max_carteras_primer_bloque`", ""
    ), "si algun dia se anade el anti-bundle, quitar este test y el aviso"


def test_no_se_mide_ningun_dev() -> None:
    """Pedido el 19-sep: "que no mida devs, solo si una moneda nueva llega a
    los 99 holders, nada mas".

    Eran CUATRO cosas distintas mirando al creador, y ninguna era una de las
    dos condiciones:
      * `ignorar_listas_de_devs: false` -> blacklist manual + auto-baneo por
        rugs. Era la unica que seguia viva.
      * `pumpfun_dev` -> reputacion en vivo (migraciones / lanzamientos).
      * `max_dev_hold_pct` -> cuanto supply guarda el dev.
      * `dev_buy.min_sol` -> cuanto se compro el dev a si mismo.

    LO QUE CUESTA: un dev al que ya se le vio hundir una moneda vuelve a ser
    comprable. Las listas siguen en disco; volver es poner el primero en false.
    """
    f = _filtros()
    assert f["ignorar_listas_de_devs"] is True
    assert f["pumpfun_dev"]["enabled"] is False
    assert not f.get("max_dev_hold_pct", 0)
    assert not f.get("min_dev_hold_pct", 0)
    assert not f.get("dev_buy", {}).get("min_sol", 0)


def test_las_mayhem_ya_no_se_descartan() -> None:
    """Era una tercera condicion que nadie pidio, y se comia monedas que
    cumplian las dos sin decir nada.

    No afloja ningun riesgo de la moneda: mayhem es un modo de pump.fun que
    cambia a quien van las comisiones, y el bot ya sabe mandarlas donde toca.
    """
    f = _filtros()
    assert f["allow_mayhem"] is True
    assert f["require_mayhem"] is False


# ------------------------------------ se compra el CRUCE, no el estado


def test_una_moneda_que_ya_los_tenia_al_verla_no_se_compra() -> None:
    """EL CASO QUE COSTO DINERO (`vuviun musk`, 19-sep).

    Subio de 3K a 12K y se desplomo a 3,16K. El bot la compro abajo. Los
    titulares SOLO SUBEN, asi que despues del desplome seguia teniendo 299 y
    cumplia "los tiene" a la perfeccion. Comprar por el estado es comprar el
    cadaver.
    """
    f = _fuente(min_holders=90)
    _poblar(MINT, 299, creacion=True, edad=90.0)
    assert _revisa(f, [MINT]) == [], "no debia comprarse"
    assert f._embudo.get("ya_venian_cumplidas") == 1
    assert f._embudo.get("con_titulares", 0) == 0


def test_la_misma_moneda_vista_antes_por_debajo_si_se_compra() -> None:
    """Y esto es lo que impide que el freno mate las buenas."""
    f = _fuente(min_holders=90)
    _poblar(MINT, 40, creacion=True, edad=20.0)
    _revisa(f, [MINT])                # la ve pequena: queda apuntada
    assert f._embudo.get("con_titulares", 0) == 0

    _poblar(MINT, 120, creacion=True, edad=40.0)
    _revisa(f, [MINT])                # ahora cruza
    assert f._embudo.get("con_titulares") == 1
    assert f._embudo.get("ya_venian_cumplidas", 0) == 0


def test_una_moneda_nacida_con_el_bot_mirando_siempre_ve_su_cruce() -> None:
    """Por eso el freno no cuesta candidatas.

    `_revisar` corre en CADA operacion de la moneda, y una nacida con el bot
    escuchando empieza en 1 titular. Se simula el crecimiento operacion a
    operacion y tiene que acabar comprandose.
    """
    f = _fuente(min_holders=90)
    for cuantos in range(1, 140, 7):
        _poblar(MINT, cuantos, creacion=True, edad=float(cuantos))
        _revisa(f, [MINT])
    assert f._embudo.get("con_titulares") == 1
    assert f._embudo.get("ya_venian_cumplidas", 0) == 0
    # Y una sola moneda cuenta UNA vez, aunque se la haya mirado veinte.
    assert f._embudo["vistas"] == 1


def test_la_memoria_del_cruce_esta_acotada() -> None:
    """El bot ve cientos de monedas por minuto: sin tope, esto crece sin fin."""
    f = _fuente(min_holders=90)
    for i in range(_MAX_RECORDADAS + 50):
        f._apunta_por_debajo(f"mint{i}")
    assert len(f._vistas_por_debajo) <= _MAX_RECORDADAS


def test_el_parte_dice_cuantas_se_cayeron_por_el_cruce() -> None:
    """Si el freno se pasa de listo, tiene que verse en Telegram y no en un
    log que desde el movil no se lee."""
    texto = build_holders_heartbeat(
        90, 300.0, 0.12, {"vistas": 10, "ya_venian_cumplidas": 7},
        paper_mode=False,
    )
    assert "7" in texto
    assert "cruce" in texto.lower()


def test_el_embudo_cuenta_monedas_y_no_veces() -> None:
    """El parte de Telegram es lo unico que se lee desde el movil.

    `_revisar` corre en CADA operacion de la moneda, asi que sin esto una sola
    moneda activa aparecia decenas de veces y el parte decia "llegaron a 90
    titulares: 47" con tres monedas detras. Con ese numero se decide si el
    corte esta bien puesto.
    """
    f = _fuente(min_holders=90)
    for _ in range(25):
        _poblar(MINT, 10, creacion=True)
        _revisa(f, [MINT])
    assert f._embudo["vistas"] == 1


def test_no_queda_ningun_filtro_que_pueda_descartar() -> None:
    """"Que no haya mas restricciones", pedido el 19-sep.

    No basta con no anadir ninguna: hay que comprobar que no quede viva una de
    las que ya estaban. Asi estuvo el bot dias encendido sin comprar --por
    `max_buy_age: 0` y por el tope global de titular-- sin que nada lo dijera.

    Este test recorre TODOS los ajustes del camino de compra y exige que esten
    a cero o apagados. Si alguien enciende uno, salta y tiene que decirlo.
    """
    f = _filtros()
    numericos = (
        "no_repeat_name_min", "min_volumen_sol", "max_volumen_sol", "min_buys",
        "min_ops", "min_presion_ratio", "max_first_block_pct",
        "ultimas_tx_compra", "min_titulares", "max_titulares",
        "min_vol_ventana_sol", "max_mc_por_sol_curva", "max_entry_mc",
        "min_entry_mc", "min_buy_age", "max_buy_age", "min_curve_sol",
        "min_holders_gmgn", "max_holder_pct", "min_holders",
        "max_dev_hold_pct", "min_dev_hold_pct", "max_bundle_pct",
        "min_supply_en_manos",
    )
    vivos = [k for k in numericos if f.get(k, 0)]
    assert not vivos, f"estos filtros pueden descartar monedas: {vivos}"

    assert f["ignorar_listas_de_devs"] is True, "las listas de devs descartan"
    assert f["allow_mayhem"] is True, "las mayhem se descartarian"
    assert f.get("require_mayhem") is False
    assert f.get("redes_originales") is False
    assert not f.get("senal_gmgn")
    for bloque in ("pumpfun_dev", "dev_elite", "second_wave"):
        assert not (f.get(bloque) or {}).get("enabled"), f"{bloque} activo"
    assert not (f.get("dev_buy") or {}).get("min_sol")


def test_dexscreener_no_puede_bloquear_aunque_este_encendido() -> None:
    """Está `enabled: true` y `enforce: true`, que asusta al leerlo.

    No bloquea: con TODOS sus criterios a cero, `_needs_market_data` es False,
    así que ni llega a consultar la API y no hay nada que pueda fallar. Queda
    escrito aquí para no volver a perseguirlo cada vez que se revisa por qué no
    compra.
    """
    dx = _filtros()["dexscreener"]
    criterios = (
        dx.get("require_listed"), dx.get("require_dex_paid"),
        dx.get("min_boosts"), dx.get("min_liquidity_usd"),
        dx.get("min_market_cap"), dx.get("max_market_cap"),
    )
    assert not any(criterios), f"DexScreener tiene criterios vivos: {criterios}"
