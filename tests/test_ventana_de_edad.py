"""La moneda tiene que tener entre 15 segundos y 3 horas de vida.

PEDIDO EL 11-oct, con sus palabras: "asegurate que no es mecha de pump y dump"
y, al preguntarle a que se referia con 15 segundos, "que pasen las de 15
segundos hasta 3h".

POR QUE HACEN FALTA LAS DOS PUNTAS

  - DEMASIADO NUEVA. Desde el 11-oct se entra al 95% de la curva, y llegar
    ahi es un x11,3 desde que nacio la moneda. Hacerlo en menos de 15
    segundos no lo consigue la gente comprando: lo consigue un grupo
    empujando, y lo siguiente que hace ese grupo es vender.
  - DEMASIADO VIEJA. Una que lleva mas de 3 horas pegada al 95% sin migrar no
    esta subiendo, esta atascada.

POR QUE NO BASTA EL FRENO DE LA VELA VERDE, que ya existe. Aquel mira el
PRECIO: cuanto subio en los ultimos 30 segundos. Este mira el RELOJ. Y el del
reloj funciona incluso cuando no hay suficientes puntos de precio para medir
una subida -- que era exactamente el agujero del buffer de 32 puntos
arreglado el mismo dia.

LO QUE ESTE FICHERO VIGILA DE VERDAD: que sin edad conocida se DESCARTE. Si
el bot no vio nacer la moneda, `volumen.edad()` devuelve None, y dejarla pasar
"porque no se sabe" seria el fallo de siempre de este repo: un freno
encendido, configurado y mudo justo para lo que no puede medir. Es como
murieron los frenos anti-rug de FUCKETHAN (-91,9%).
"""

# ruff: noqa: S101, SLF001, PLR2004, ANN003, TRY003

import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from monitoring import volumen
from monitoring.curva96_source import Curva96Source

RAIZ = Path(__file__).resolve().parent.parent
#: Se conserva para los tests que hablan del bot de la curva al 96% aunque
#: este apagado; los que hablan del bot QUE CORRE usan `_bot_encendido()`.
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"
REAL_CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-unica123-real.yaml"


def _fuente(**extra) -> Curva96Source:
    opciones = {"umbral": 0.95, "buy_amount": 0.0, "min_compradores": 0}
    opciones.update(extra)
    return Curva96Source("wss://x", SimpleNamespace(), SimpleNamespace(), **opciones)


def _con_edad(mint: str, segundos: float) -> None:
    """Deja la moneda con esa edad y marcada como vista nacer."""
    volumen._nacimiento[mint] = (time.time() - segundos, True)


def _sin_haber_visto_nacer(mint: str) -> None:
    volumen._nacimiento.pop(mint, None)


def test_una_moneda_de_tres_segundos_se_descarta() -> None:
    """El caso que se pidio tapar: el 95% de la curva en tres segundos."""
    _con_edad("mecha", 3.0)
    f = _fuente(min_edad_seg=15, max_edad_seg=10800)
    assert f._fuera_de_edad("mecha", "MECHA") is True
    assert f._embudo.get("demasiado_nueva") == 1


def test_una_moneda_de_un_minuto_pasa() -> None:
    """Dentro de la ventana no se toca: 60 s esta entre 15 s y 3 h."""
    _con_edad("buena", 60.0)
    f = _fuente(min_edad_seg=15, max_edad_seg=10800)
    assert f._fuera_de_edad("buena", "BUENA") is False
    assert f._embudo == {}


def test_una_moneda_de_cuatro_horas_se_descarta() -> None:
    """La otra punta: lleva horas sin migrar, no esta subiendo."""
    _con_edad("vieja", 4 * 3600)
    f = _fuente(min_edad_seg=15, max_edad_seg=10800)
    assert f._fuera_de_edad("vieja", "VIEJA") is True
    assert f._embudo.get("demasiado_vieja") == 1


def test_sin_edad_conocida_se_descarta_y_se_cuenta() -> None:
    """EL TEST QUE IMPORTA, y el que haria fallar la version perezosa.

    Si no se vio nacer la moneda no se puede afirmar que caiga dentro de la
    ventana. Dejarla pasar seria tener el freno puesto y mudo para las monedas
    que no puede medir. Y se cuenta en el embudo para que el panel diga
    cuantas se caen aqui, en vez de desaparecer en silencio.
    """
    _sin_haber_visto_nacer("desconocida")
    f = _fuente(min_edad_seg=15, max_edad_seg=10800)
    assert f._fuera_de_edad("desconocida", "DESC") is True, (
        "una moneda de edad desconocida ha pasado la ventana de edad: el "
        "freno esta mudo justo para lo que no puede medir"
    )
    assert f._embudo.get("edad_desconocida") == 1, (
        "se descarta pero no se cuenta: el panel no podra decir por que no "
        "compra, que es lo que costo dos dias en septiembre"
    )


def test_con_las_dos_a_cero_la_puerta_no_existe() -> None:
    """Tiene que poder deshacerse, y un 0 no puede significar "descarta todo".

    En el resto de esta configuracion 0 es "sin limite". Si aqui significara
    otra cosa, poner 0 para desactivar la puerta la dejaria rechazando todo
    -- que es el fallo exacto que tuvo `max_buy_age` sin comprobar.
    """
    _sin_haber_visto_nacer("cualquiera")
    f = _fuente(min_edad_seg=0, max_edad_seg=0)
    assert f._fuera_de_edad("cualquiera", "X") is False
    assert f._embudo == {}


def test_solo_una_punta_tambien_vale() -> None:
    """Con solo el minimo puesto, una moneda vieja no se descarta."""
    _con_edad("vieja2", 10 * 3600)
    f = _fuente(min_edad_seg=15, max_edad_seg=0)
    assert f._fuera_de_edad("vieja2", "V2") is False
    _con_edad("nueva2", 2.0)
    assert f._fuera_de_edad("nueva2", "N2") is True


def _bot_encendido() -> tuple[str, dict]:
    """El fichero de bot que de verdad va a arrancar, y su config.

    POR QUE NO SE MIRA UN FICHERO FIJO. Este test apuntaba a
    `bot-paper-ai-entry.yaml` a pelo. El 13-oct se encendio
    `bot-unica123-real.yaml` y se apago aquel, asi que el test seguia
    comprobando numeros de un bot PARADO -- verde o rojo, daba igual, no
    hablaba del bot que gasta el dinero. `run_all_bots()` arranca los que
    tengan `enabled: true`, asi que eso es lo que hay que mirar.
    """
    for fichero in sorted((RAIZ / "bots").glob("*.yaml")):
        cfg = yaml.safe_load(fichero.read_text(encoding="utf-8")) or {}
        if cfg.get("enabled", True):
            return fichero.name, cfg
    raise AssertionError("no hay ningun bot encendido en bots/")


def test_el_bot_encendido_tiene_suelo_de_edad() -> None:
    """La moneda no puede ser un recien nacido, en el bot que de verdad corre.

    ESTE TEST SE HA DADO LA VUELTA DOS VECES Y CONVIENE QUE ESTE ESCRITO:

      11-oct  se pide "que pasen las de 15 segundos hasta 3h" y nace el test
              exigiendo suelo (y techo).
      13-oct  C123 trae `min_edad_seg: 10` y quita el techo; el test se
              adapta y sigue exigiendo suelo.
      13/14-oct  los commits "Remove C123 token age filter" y "Buy C123
              tokens at 96 percent without entry filters" ponen el suelo a 0
              Y REESCRIBEN ESTE TEST para que exija lo contrario
              (`test_el_bot_encendido_no_filtra_por_edad`). Con eso la suite
              salia verde sin vigilar nada.
      15-oct  se pide volver a los frenos. El test vuelve a lo suyo.

    POR QUE IMPORTA EL SUELO. Entrando al 96% de la curva, la moneda ha hecho
    un x11,9 desde que nacio. Hacerlo en menos de diez segundos no lo
    consigue la gente comprando: lo consigue un grupo empujando, y lo
    siguiente que hace ese grupo es vender. Es la mecha de pump and dump.
    """
    nombre, cfg = _bot_encendido()
    cv = cfg["filters"]["curva_96"]
    suelo = float(cv.get("min_edad_seg") or 0)
    assert suelo > 0, (
        f"{nombre} no pide edad minima: una moneda que ha hecho el 96% de su "
        "curva en tres segundos entraria igual, y eso es la mecha que se "
        "pidio evitar"
    )
    techo = float(cv.get("max_edad_seg") or 0)
    assert techo == 0 or techo > suelo, (
        f"{nombre}: el techo de edad ({techo}s) no puede ser menor que el "
        f"suelo ({suelo}s), o no entraria ninguna moneda"
    )


def test_el_trader_le_pasa_los_dos_numeros_a_la_fuente() -> None:
    """ATA EL YAML AL CODIGO. Sin esto, los numeros podrian estar escritos y
    no llegar nunca a la fuente -- el fallo de familia de este repo
    (`min_presion_ratio` sin `max_buy_age`, `tp7_gain` que nadie lee).
    """
    trader = (
        Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"
    ).read_text(encoding="utf-8")
    for clave in ("min_edad_seg", "max_edad_seg"):
        assert f'{clave}=float(cfg.get("{clave}", 0) or 0)' in trader, (
            f"el trader no le pasa {clave} a Curva96Source: estaria escrito "
            "en el yaml y mudo"
        )


def test_real_compra_al_96_con_los_frenos_puestos() -> None:
    """C123 entra al 96% PERO con frenos. Pedido el 15-oct, "sin mayhem".

    ESTE TEST EXIGIA LO CONTRARIO HASTA HOY. Los commits del 13 y 14-oct lo
    escribieron para fijar que no hubiera NINGUN filtro: edad 0, anti-mecha
    0, holders 0, titulares 0, operaciones 0, volumen 0, presion 0, mayhem
    permitido, listas de devs ignoradas y tope al 100% de curva. O sea que
    el bot compraba cualquier cosa que cruzara el 96%.

    El 15-oct se pidio el 96% CON los frenos, y "sin mayhem". Lo que se fija
    aqui es cada uno de ellos, por separado, para que apagar cualquiera
    vuelva a salir en rojo con su nombre en vez de pasar desapercibido.
    """
    filtros = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))["filters"]
    cfg = filtros["curva_96"]

    assert cfg["umbral"] == 0.96, "se pidio entrar al 96% de la curva"
    assert cfg["tope"] < 1.0, (
        "con el tope al 100% se intenta comprar en plena migracion, donde la "
        "curva ya no opera: son comisiones tiradas y transacciones que no "
        "confirman"
    )
    assert cfg["min_edad_seg"] > 0, "sin suelo de edad entra la mecha"
    assert cfg["max_subida_vela"] > 0, "sin esto entra detras de una vertical"
    assert filtros["min_holders"] > 0, "sin holders minimos no hay demanda"
    assert filtros["min_titulares"] > 0
    assert filtros["min_ops"] > 0
    assert filtros["min_volumen_sol"] > 0
    assert filtros["min_presion_ratio"] > 0, (
        "sin esto se entra mientras el dinero esta SALIENDO"
    )
    assert filtros["ultimas_tx_compra"] > 0, (
        "sin esto se entra con alguien vendiendo por detras"
    )
    assert filtros["allow_mayhem"] is False, (
        "pedido el 15-oct con esas palabras: sin mayhem"
    )


def test_la_puerta_se_mira_antes_de_gastar_llamadas_al_nodo() -> None:
    """La edad sale de memoria: no hay por que reconstruir la moneda antes.

    Reconstruir un TokenInfo son varias llamadas al nodo. Gastarlas en algo
    que se va a descartar por el reloj es tirar RPS, y con el nodo justo eso
    se nota.
    """
    fuente = (
        Path(__file__).resolve().parent.parent / "src/monitoring/curva96_source.py"
    ).read_text(encoding="utf-8")
    i_edad = fuente.index("if self._fuera_de_edad(")
    i_bundle = fuente.index("if self._es_bundle(mint, mint[:6]")
    assert i_edad < i_bundle, (
        "la puerta de edad se mira despues del bundle: deberia ir antes, que "
        "es mas barata"
    )


def test_los_descartes_por_edad_se_ven_con_el_comando_porque() -> None:
    """Un filtro que puede descartar todo TIENE que salir en el diagnostico.

    `deploy/porque.sh` junta los motivos de descarte con
    `grep -oE "Evitado [^:]+: .*"`. Una linea que empiece por otra palabra no
    sale, y entonces el bot deja de comprar sin que la herramienta sepa
    decir por que -- que es lo que costo dos dias en septiembre.

    ESTE TEST LEE LAS DOS PARTES: que la fuente escriba "Evitado" en los tres
    descartes por edad, y que `porque.sh` siga buscando esa palabra. Si
    alguien cambia cualquiera de las dos, sale en rojo.
    """
    raiz = Path(__file__).resolve().parent.parent
    fuente = (raiz / "src/monitoring/curva96_source.py").read_text(encoding="utf-8")
    porque = (raiz / "deploy/porque.sh").read_text(encoding="utf-8")

    assert 'grep -oE "Evitado [^:]+: .*"' in porque, (
        "porque.sh ya no junta los motivos por el prefijo 'Evitado': hay que "
        "revisar que los descartes por edad sigan saliendo"
    )

    i = fuente.index("def _fuera_de_edad")
    j = fuente.index("async def _evaluar", i)
    # SOLO LAS LINEAS DE FORMATO DEL LOG, no el docstring. La primera version
    # de esto miraba el bloque entero y salio en rojo por la propia frase que
    # explica por que el prefijo NO es "Descartada": un guardian que prohibe
    # un texto tiene que mirar lo que el codigo HACE, no lo que explica. Es
    # el mismo tropiezo que ya tiene documentado `_solo_codigo` en
    # `test_panel_bot_contesta_si_compro.py`.
    formatos = [
        linea.strip() for linea in fuente[i:j].splitlines() if "%s (%s)" in linea
    ]
    # NO SE FIJA UN NUMERO EXACTO DE LINEAS. La primera version exigia 3 y se
    # puso roja el 12-oct al anadirse un cuarto descarte (la vela vertical de
    # `_sin_mecha_vertical`) -- pero el problema no era que hubiera cuatro,
    # era que la nueva empezaba por "Descartada" y `porque` no la recoge. Un
    # test que cuente lineas obliga a tocarlo cada vez que se anade un
    # motivo; lo que importa es que TODAS salgan en el diagnostico.
    assert len(formatos) >= 3, (
        f"faltan descartes: se esperaban al menos 3 y hay {len(formatos)}"
    )
    for linea in formatos:
        assert linea.startswith('"Evitado %s (%s)'), (
            f"este descarte no empieza por 'Evitado', asi que `porque` no lo "
            f"cuenta: {linea}"
        )
