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

# ruff: noqa: S101, SLF001, PLR2004, ANN003

import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from monitoring import volumen
from monitoring.curva96_source import Curva96Source

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
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


def test_el_yaml_pide_los_quince_segundos_y_las_tres_horas() -> None:
    """Los numeros pedidos, atados al fichero que de verdad corre."""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]["curva_96"]
    assert cfg["min_edad_seg"] == 15, "se pidio 15 segundos como suelo"
    assert cfg["max_edad_seg"] == 0, "sin edad maxima: solo se pidio el suelo"


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


def test_real_edad_10_y_bundle_no_bloquea() -> None:
    """La config real respeta edad 10, 20 holders y no rechaza bundles."""
    filtros = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))["filters"]
    cfg = filtros["curva_96"]
    assert cfg["min_edad_seg"] == 10
    assert cfg["max_edad_seg"] == 0
    assert filtros["min_holders"] == 20
    assert cfg["min_compradores"] == 0
    assert cfg["exigir_ver_creacion"] is False
    assert cfg["max_carteras_primer_bloque"] == 0
    assert cfg["max_sol_primer_bloque"] == 0
    assert cfg["max_bundle_pct"] == 0
    assert cfg["max_bundle_pct_sin_historia"] == 0


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
    assert len(formatos) == 3, (
        f"se esperaban 3 lineas de descarte por edad y hay {len(formatos)}"
    )
    for linea in formatos:
        assert linea.startswith('"Evitado %s (%s)'), (
            f"este descarte no empieza por 'Evitado', asi que `porque` no lo "
            f"cuenta: {linea}"
        )
