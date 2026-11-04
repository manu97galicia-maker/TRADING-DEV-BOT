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
        # LOS YAML DEL BOT BSC NO SON DE ESTE BOT. Entraron en `bots/`
        # con el commit 490a05f y no tienen bloque `trade`: son de otra
        # cadena, otro motor y otro formato. Recorrerlos aqui rompia
        # cinco guardianes de Solana sin que nada estuviera mal en
        # Solana.
        if fichero.name.startswith("bot-bsc-"):
            continue
        # Y LOS DE PAPEL TAMPOCO. El 31-oct entraron varios
        # (`bot-paper-presion*`, `bot-paper-mayhem-24k`) que no llevan
        # algunos bloques de un bot real. Estos guardianes existen para
        # proteger DINERO, y en papel no hay ninguno que proteger: lo que
        # vigilan --la comision de la venta protectora, los frenos de
        # calidad-- solo cuesta dinero donde se opera de verdad.
        if "paper" in fichero.name:
            continue
        cfg = yaml.safe_load(fichero.read_text(encoding="utf-8")) or {}
        if cfg.get("enabled", True):
            return fichero.name, cfg
    # NINGUNO SE ARRANCA CON "TODAS" (4-nov: "Solo quiero la de comprar
    # bloque 2 con dev migrado 1%"). El que opera con dinero es el que lanza
    # su propio arranque, deploy/arranca-dev1-real.sh, aunque su nombre diga
    # `paper` y lleve enabled: false. Estos guardianes existen para proteger
    # el dinero, asi que vigilan a ese.
    arranque = (RAIZ / "deploy" / "arranca-dev1-real.sh").read_text(encoding="utf-8")
    linea = next(x for x in arranque.splitlines() if x.startswith("YAML="))
    fichero = RAIZ / linea.split("=", 1)[1].strip()
    cfg = yaml.safe_load(fichero.read_text(encoding="utf-8")) or {}
    if (cfg.get("filters") or {}).get("paper_trade") is False:
        return fichero.name, cfg
    raise AssertionError("no hay ningun bot en real en bots/")


def test_los_frenos_de_calidad_son_una_decision_y_se_ven_de_un_vistazo() -> None:
    """QUE FRENOS HAY PUESTOS HOY. No exige ninguno: los ENSENA.

    POR QUE ESTE TEST NO VETA NADA. Los frenos de calidad --edad minima,
    anti-mecha, holders, titulares, operaciones, volumen, presion-- han
    cambiado de sentido CUATRO veces en cuatro dias:

      11-oct  se pide suelo de edad "15 segundos hasta 3h": se enciende.
      13-oct  C123 lo baja a 10 s y quita el techo.
      13/14-oct  dos commits los apagan TODOS y reescriben los tests que los
              vigilaban para que exijan lo contrario. La suite seguia verde.
      15-oct  se piden de vuelta ("sin mayhem")... y una hora despues se
              piden fuera otra vez ("solo que compre al llegar al 96%").

    Un test que exija un valor concreto de estos hay que reescribirlo en
    cada vuelta, y reescribir un guardian es como se pierde lo que
    vigilaba. Asi que este deja de opinar sobre CUANTO y se queda con lo
    unico que de verdad hace falta: que el estado sea visible y coherente.
    Lo que SI se sigue vigilando con dientes esta en el test de abajo.
    """
    nombre, cfg = _bot_encendido()
    fl = cfg["filters"]
    cv = fl["curva_96"]
    frenos = {
        "edad minima (s)": cv.get("min_edad_seg") or 0,
        "anti-mecha": cv.get("max_subida_vela") or 0,
        "compradores distintos": cv.get("min_compradores") or 0,
        "holders": fl.get("min_holders") or 0,
        "titulares": fl.get("min_titulares") or 0,
        "operaciones": fl.get("min_ops") or 0,
        "volumen (SOL)": fl.get("min_volumen_sol") or 0,
        "presion compradora": fl.get("min_presion_ratio") or 0,
        "ultimas tx compra": fl.get("ultimas_tx_compra") or 0,
    }
    # COHERENCIA, que es lo unico que se exige: un freno no puede estar en
    # negativo, y si la edad tiene techo tiene que ser mayor que el suelo.
    for etiqueta, valor in frenos.items():
        assert float(valor) >= 0, f"{nombre}: {etiqueta} en negativo ({valor})"
    suelo = float(cv.get("min_edad_seg") or 0)
    techo = float(cv.get("max_edad_seg") or 0)
    assert techo == 0 or techo > suelo, (
        f"{nombre}: techo de edad ({techo}s) por debajo del suelo ({suelo}s): "
        "no entraria ninguna moneda"
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


def test_lo_que_nunca_se_ha_pedido_apagar_sigue_encendido() -> None:
    """LOS TRES QUE NO SE TOCAN. Aqui si hay dientes.

    Los frenos de calidad se encienden y se apagan segun el dia (ver el test
    de arriba). Estos tres no: se pidieron una vez, no se han desdicho nunca,
    y los tres se apagaron solos en los commits del 13 y 14-oct sin que nadie
    lo pidiera. Por eso tienen guardian propio.

      umbral 0,96        pedido el 14-oct y repetido el 15: "solo que compre
                         al llegar al 96%".
      tope por debajo    con el tope al 100% se intenta comprar en plena
      del 100%           migracion, donde la curva ya no opera: comisiones
                         tiradas y transacciones que no confirman. Lo paso a
                         1,0 el commit "...without entry filters".
      sin mayhem         pedido el 15-oct con esas palabras. El mismo commit
                         lo habia puesto en true.
    """
    filtros = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))["filters"]
    cfg = filtros["curva_96"]

    # 95% desde el 15-oct, pedido ("que compre en 95% curva"). Este numero
    # lo mueve Manu y se mueve mucho (septima vez); lo que este guardian
    # vigila no es el valor sino que NO se vaya solo, como paso el 13-oct.
    # 88% desde el 15-oct, pedido ("mejor pero compra algo antes"). Decimo
    # movimiento de este numero. Lo que vigila este guardian NO es el valor
    # --lo mueve Manu y lo mueve mucho-- sino que no se vaya SOLO, como paso
    # el 13-oct en dos commits que nadie pidio.
    # 77% desde el 15-oct. A partir de ahora este numero NO decide la
    # entrada: solo dice cuando se empieza a mirar la moneda. Quien decide es
    # la banda en dolares (min_entry_mc / max_entry_mc).
    # 0.89 DESDE EL 19-oct, pedido: "vuelve a usar la estrategia de comprar
    # a un 89%". Antes fue 0,01 (banda 2-5k), y antes 0,754 (banda 15-22k).
    assert cfg["umbral"] == 0.93, "desde el 93% de curva (23-oct)"
    assert cfg["tope"] < 1.0, (
        "con el tope al 100% se compra en plena migracion: la curva ya no "
        "opera ahi, son comisiones tiradas y transacciones que no confirman"
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
