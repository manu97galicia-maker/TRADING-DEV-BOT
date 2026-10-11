"""No comprar el ultimo escalon de una subida vertical.

EL CASO REAL, CATO, 23-sep: el bot entro a 26K de market cap justo despues de
que la moneda subiera de 7,5K a 25K en medio minuto. Medio minuto mas tarde
valia 12,4K. Comprar detras de una vela verde enorme es comprar el techo: la
gente que la empujo ya esta dentro, y lo siguiente que hace es vender.

POR QUE NO BASTABA CON LA PRESION COMPRADORA, que es lo que ya habia: la
presion mira si AHORA entra mas dinero del que sale, y en plena vela verde la
presion es inmejorable -- justo en el peor momento para entrar. Miran cosas
distintas: una el dinero de ahora, otra el precio de hace un rato.
"""

# ruff: noqa: S101, SLF001, PLR2004

import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from monitoring import volumen
from monitoring.holders_source import HoldersSource, build_holders_heartbeat


def _precios(mint: str, precios: list[float], *, hace: float = 5.0) -> None:
    """Deja `precios` en la historia, repartidos en los ultimos `hace` s."""
    volumen._historia_precio.pop(mint, None)
    ahora = time.time()
    paso = hace / max(len(precios), 1)
    for i, p in enumerate(precios):
        volumen._historia_precio[mint].append((ahora - hace + i * paso, p))


def test_una_moneda_que_ha_triplicado_sale_marcada() -> None:
    """El caso de CATO: 7,5 -> 25 es un x3,3."""
    _precios("m", [7.5, 12.0, 25.0])
    assert volumen.subida("m", 10.0) > 2.0


def test_una_moneda_plana_no_sale_marcada() -> None:
    _precios("m", [10.0, 10.1, 9.9, 10.05])
    assert volumen.subida("m", 10.0) < 0.05


def test_sin_datos_no_se_inventa_una_subida() -> None:
    """No tener datos NO es lo mismo que estar plana, y bloquear una compra
    por falta de datos seria inventarse un motivo."""
    volumen._historia_precio.pop("nueva", None)
    assert volumen.subida("nueva", 10.0) == 0.0
    _precios("solo_uno", [10.0])
    assert volumen.subida("solo_uno", 10.0) == 0.0


def test_solo_mira_la_ventana_pedida() -> None:
    """Una subida de hace un minuto ya no es una vela verde: es el precio."""
    mint = "m"
    volumen._historia_precio.pop(mint, None)
    ahora = time.time()
    volumen._historia_precio[mint].append((ahora - 120, 1.0))
    volumen._historia_precio[mint].append((ahora - 1, 10.0))
    volumen._historia_precio[mint].append((ahora, 10.1))
    assert volumen.subida(mint, 10.0) < 0.05, "cuenta un salto de hace dos minutos"


def test_una_bajada_no_cuenta_como_subida() -> None:
    _precios("m", [25.0, 18.0, 12.0])
    assert volumen.subida("m", 10.0) == 0.0


def test_la_fuente_descarta_la_vela_verde() -> None:
    """De punta a punta, con la condicion encendida."""
    f = HoldersSource(
        "wss://x",
        SimpleNamespace(),
        SimpleNamespace(),
        min_holders=3,
        max_edad_seg=0.0,
        ultimas_compras=0,
        presion_seg=0,
        subida_seg=10.0,
        max_subida=1.0,
        heartbeat_interval=0,
    )
    mint = "Er1sFZm3HGuXFiKeXQLg84H9B9Vqk3dp94AjLiS9pump"
    ahora = time.time()
    volumen._ops.pop(mint, None)
    volumen._ops[mint] = [(ahora, 0.5, True, "w1"), (ahora, 0.5, True, "w2")]
    f._revisar([mint], None)  # se ve por debajo del corte
    volumen._ops[mint] = [(ahora, 0.5, True, f"w{i}") for i in range(5)]
    _precios(mint, [7.5, 12.0, 25.0])
    f._revisar([mint], None)
    assert f._embudo.get("vela_verde") == 1, f"embudo: {f._embudo}"


def test_el_parte_ensena_cuantas_se_descartan_asi() -> None:
    """Si descarta de mas hay que poder verlo: el numero a mover es
    `max_subida`, pero primero hay que saber que esta actuando."""
    texto = build_holders_heartbeat(23, 0.0, 0.04, {"vela_verde": 7}, paper_mode=False)
    assert "vela verde" in texto
    assert "7" in texto


# ---------------------- que haya dinero de verdad, no solo buena proporcion


def _fuente_con_volumen(minimo: float) -> HoldersSource:
    return HoldersSource(
        "wss://x",
        SimpleNamespace(),
        SimpleNamespace(),
        min_holders=3,
        max_edad_seg=0.0,
        ultimas_compras=0,
        presion_seg=3.0,
        max_subida=0,
        volumen_min_sol=minimo,
        heartbeat_interval=0,
    )


def _pasa(f: HoldersSource, mint: str, compras: list[float]) -> bool:
    """Deja la moneda cruzando el corte con esas compras y devuelve si pasa.

    `_lanzar` se sustituye por un apuntador: crea una tarea de asyncio y estos
    tests no tienen bucle -- y lo que se comprueba aqui son los filtros, no la
    evaluacion contra el nodo.
    """
    llegaron: list[str] = []
    f._lanzar = lambda m, _t, _e, _cb: bool(llegaron.append(m)) or True  # type: ignore[method-assign]
    ahora = time.time()
    volumen._historia_precio.pop(mint, None)
    volumen._ops[mint] = [(ahora, 0.1, True, "a"), (ahora, 0.1, True, "b")]
    f._revisar([mint], None)  # se la ve por debajo del corte
    volumen._ops[mint] = [(ahora, s, True, f"w{i}") for i, s in enumerate(compras)]
    f._revisar([mint], None)
    return bool(llegaron)


def test_calderilla_no_basta_aunque_la_proporcion_sea_perfecta() -> None:
    """LA RAZON DE SER DE ESTE FILTRO.

    "Entra mas de lo que sale" lo cumple igual de bien una moneda en la que
    entran dos centimos y sale uno: la proporcion no dice NADA del tamano. Una
    moneda sin dinero detras no se mueve por perfecta que sea la proporcion.
    """
    f = _fuente_con_volumen(0.5)
    assert not _pasa(f, "M" + "1" * 30, [0.01, 0.02, 0.01, 0.01, 0.02])
    assert f._embudo.get("poco_volumen") == 1


def test_con_dinero_de_verdad_pasa() -> None:
    f = _fuente_con_volumen(0.5)
    assert _pasa(f, "M" + "2" * 30, [0.4, 0.3, 0.5, 0.2, 0.3])
    assert f._embudo.get("poco_volumen", 0) == 0


def test_a_cero_no_se_mira() -> None:
    """El ajuste nuevo no puede cambiar nada por defecto."""
    f = _fuente_con_volumen(0)
    assert _pasa(f, "M" + "3" * 30, [0.01, 0.01, 0.01, 0.01, 0.01])


def test_el_parte_ensena_cuantas_se_caen_por_volumen() -> None:
    texto = build_holders_heartbeat(
        23, 0.0, 0.04, {"poco_volumen": 12}, paper_mode=False
    )
    assert "poco volumen" in texto
    assert "12" in texto


# ------------------------- los numeros que corren de verdad, y que rechazan


def _filtros() -> dict:
    raiz = Path(__file__).resolve().parent.parent
    return yaml.safe_load(
        (raiz / "bots/bot-paper-ai-entry.yaml").read_text(encoding="utf-8")
    )["filters"]


def test_los_numeros_de_la_vela_verde_son_los_pedidos() -> None:
    """0,80 en 30 s (25-sep). Estaban en 1,0 en 10 s.

    Pedido: "no compres una moneda que acaba de salir con solo una tremenda
    vela verde".

    LO QUE FALLABA ERA LA VENTANA. El bot guarda los ultimos 32 precios de
    cada moneda, que en una recien nacida son casi toda su vida -- pero la
    ventana de 10 SEGUNDOS solo miraba el ultimo trozo de esos puntos. Si la
    vela duro 25 s, el filtro veia nada mas el final, que suele estar ya
    plano arriba del todo, y la dejaba pasar.
    """
    f = _filtros()
    assert f["max_subida_global"] == 0.80
    assert f["subida_seg_global"] == 30.0


def test_los_dos_desastres_del_repo_se_rechazarian_hoy() -> None:
    """La prueba que importa: que el numero sirva para algo concreto.

    Un test que solo fije "0,80" se queda verde con cualquier numero que
    alguien escriba. Este comprueba que los dos casos que de verdad costaron
    dinero caen del lado de "no se compra".

    CATO (23-sep): 7,5K -> 25K de market cap en medio minuto. Se entro a 26K
    y medio minuto despues valia 12,4K.
    FUCKETHAN (24-sep): 20K -> 43K en una vela vertical, comprada en la
    punta y cerrada a -91,9% cuarenta segundos despues.
    """
    f = _filtros()
    tope = float(f["max_subida_global"])
    for nombre, desde, hasta in (
        ("CATO", 7_500, 25_000),
        ("FUCKETHAN", 20_000, 43_000),
    ):
        subida = hasta / desde - 1
        assert subida > tope, (
            f"{nombre} subio un {subida:.0%} y el tope esta en {tope:.0%}: "
            f"hoy se volveria a comprar"
        )


def test_una_moneda_que_sube_despacio_hasta_la_entrada_sigue_pasando() -> None:
    """El filtro no puede cerrar la puerta a todo: corta lo vertical, no lo que sube.

    Entrando al 71% de curva, una moneda que venia del 60% ha subido un 37%.
    Esa tiene que pasar, o el filtro deja al bot mudo -- que es la trampa
    documentada del 21 y 22-sep.
    """
    k = 30 * 1073e6

    def precio(avance: float) -> float:
        vt = 1073e6 - 793.1e6 * avance
        return k / vt**2

    tope = float(_filtros()["max_subida_global"])
    subida_desde_60 = precio(0.71) / precio(0.60) - 1
    assert subida_desde_60 < tope, (
        f"una moneda que venia del 60% de curva sube un {subida_desde_60:.0%} "
        f"y el tope esta en {tope:.0%}: el filtro cortaria demasiado"
    )
    # Y la recien nacida (del 0% al 71%, +343%) tiene que caer del otro lado.
    assert precio(0.71) / precio(0.0) - 1 > tope


def test_la_ventana_de_la_vela_verde_cabe_en_el_buffer() -> None:
    """EL FRENO DECIA 30 SEGUNDOS Y MIRABA 6. Arreglado el 11-oct.

    EL FALLO. `subida()` solo puede mirar los puntos que haya en
    `_historia_precio`, que es un deque con tope. Estaba en 32 puntos. Una
    moneda caliente --justo la que este filtro tiene que cazar-- hace varias
    operaciones por segundo, asi que 32 puntos eran unos 6 segundos de
    historia. La vela de CATO duro medio minuto: con el buffer corto, el
    arranque de la vela se caia del buffer y lo que quedaba dentro era solo
    el tramo plano de arriba, que no dispara nada.

    O SEA QUE EL FILTRO ESTABA ENCENDIDO, CONFIGURADO A 30 s Y MUDO para las
    monedas rapidas. Es el mismo fallo de familia que `min_presion_ratio` sin
    `max_buy_age`: pedido, escrito, y sin la pieza que lo hace medir.

    POR QUE IMPORTA AHORA MAS QUE ANTES. Desde el 11-oct se entra al 95% de
    la curva, donde TODA moneda ha hecho ya un x11 desde que nacio. Si el
    freno anti-mecha solo ve los ultimos 6 segundos, no distingue la que
    subio despacio de la que acaba de pegar el tiron.

    ESTE TEST SIMULA UNA MONEDA RAPIDA: la vela ocurre al principio de la
    ventana y despues llegan 100 operaciones mas, planas. Con 32 puntos la
    vela ya no esta en el buffer y `subida()` devuelve ~0; con 128 se ve.
    """
    mint = "rapida"
    volumen._historia_precio.pop(mint, None)
    ahora = time.time()
    # La vela: x3,3 en los primeros 3 segundos de la ventana de 30 s.
    for i, precio in enumerate((7.5, 10.0, 15.0, 20.0, 25.0)):
        volumen._historia_precio[mint].append((ahora - 29 + i * 0.6, precio))
    # Y despues, 100 operaciones planas arriba -- lo normal en una moneda
    # caliente, y lo que empujaba la vela fuera de un buffer de 32.
    for i in range(100):
        volumen._historia_precio[mint].append((ahora - 25 + i * 0.25, 25.0))

    sub = volumen.subida(mint, 30.0)
    assert sub > 0.80, (
        f"la vela de x3,3 que ocurrio hace 29 s ya no se ve: subida={sub:.1%}. "
        "El buffer de precios no da para la ventana configurada en "
        "`subida_seg_global`, asi que el freno anti-mecha esta mudo para las "
        "monedas rapidas, que son justo las que tiene que cazar."
    )


def test_el_buffer_de_precios_no_vuelve_a_quedarse_corto() -> None:
    """Ata el tamano del buffer a la ventana configurada.

    La cuenta: una moneda caliente hace del orden de 4 operaciones por
    segundo, asi que para cubrir `subida_seg_global` hacen falta unos
    `4 x segundos` puntos. Con la ventana en 30 s son 120, y el buffer tiene
    128. Si alguien sube la ventana sin subir el buffer, esto sale en rojo
    en vez de dejar el filtro medio ciego sin que nada lo diga.
    """
    ventana = float(_filtros()["subida_seg_global"])
    buffer_real = volumen._historia_precio["x"].maxlen
    volumen._historia_precio.pop("x", None)
    necesarios = 4 * ventana
    assert buffer_real >= necesarios, (
        f"la vela verde mira {ventana:.0f} s pero el buffer solo guarda "
        f"{buffer_real} puntos (~{buffer_real / 4:.0f} s en una moneda "
        f"caliente). Hacen falta {necesarios:.0f}. Subir `maxlen` en "
        "`volumen._historia_precio`."
    )
