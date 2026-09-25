"""El panel `bot` tiene que contestar la pregunta que dice contestar.

EL HUECO QUE TAPA, del 25-sep. El panel imprimia su cabecera "== HA COMPRADO?
==", luego "intentos de compra (6 h): 16" y ahi se callaba. Todo el detalle
--candidatas, 429, precio de SOL, "escribe porque"-- estaba dentro de la rama
del CERO intentos.

O sea que en el unico caso en que el bot SI esta operando, con dinero real, el
panel no decia si alguna de esas 16 habia entrado. Y "intento" no es "compra":
el bot escribe `Buying ... SOL worth of X` ANTES de firmar, asi que un intento
cuenta igual si la transaccion se confirma que si falla.

ES EL TERCER FALLO DE LA MISMA FAMILIA en este panel, y por eso tiene test:
  1. el contador de candidatas miraba solo "PRESION —" cuando la fuente ya era
     otra, asi que salia 0 pasara lo que pasara (21-sep);
  2. "compras: ACTIVAS" con "candidatas: 0" mientras el nodo devolvia 429: dos
     frases ciertas que juntas hacian pensar en filtros exigentes en vez de en
     un bot ciego (dos dias perdidos);
  3. este.
Todos son lo mismo: decir una cosa cierta y dejar creyendo otra.

ESTE FICHERO LEE EL SCRIPT, no lo ejecuta. Ejecutarlo necesitaria systemd y
journalctl, que no existen aqui. Lo que se fija es que las preguntas caras
tengan una linea que las conteste, que es donde estuvo el fallo las tres veces.
"""

# ruff: noqa: S101

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / "deploy/instalar_comandos.sh"


def _texto() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _bloque_ha_comprado() -> str:
    """Solo el trozo del panel que va bajo '== HA COMPRADO? =='.

    Se acota a proposito: buscar en el script entero daria verde por una
    cadena que aparezca en otro bloque, que es como el fallo 1 de la cabecera
    sobrevivio a su propia comprobacion.
    """
    t = _texto()
    i = t.index("== HA COMPRADO? ==")
    j = t.index("== MEDICIONES ==", i)
    return t[i:j]


def _solo_codigo(bloque: str) -> str:
    """El bloque sin sus comentarios.

    HACE FALTA porque los comentarios de este panel citan a proposito los
    nombres equivocados de versiones anteriores, para que no vuelvan. Un
    guardian que prohiba una cadena tiene que mirar lo que el script HACE, no
    lo que explica: si no, la propia explicacion del fallo lo da por presente.
    Paso al escribir este fichero.
    """
    return "\n".join(
        linea for linea in bloque.splitlines() if not linea.lstrip().startswith("#")
    )


def test_con_intentos_dice_cuantas_entraron() -> None:
    """Lo que faltaba: 16 intentos y ni una palabra sobre en que quedaron."""
    bloque = _bloque_ha_comprado()
    assert "Successfully bought" in bloque, (
        "el panel no cuenta las compras que SI entraron: con intentos > 0 "
        "vuelve a quedarse mudo"
    )
    assert "Failed to buy" in bloque, (
        "el panel no cuenta las compras que fallaron, asi que no se puede "
        "distinguir 'no compra' de 'no le entra la transaccion'"
    )


def test_el_detalle_no_vive_solo_en_la_rama_del_cero() -> None:
    """El fallo exacto: todo el diagnostico estaba tras `-eq 0`.

    Tiene que haber un `else`, o sea una rama para cuando SI hubo intentos.
    Sin el, el panel se calla justo cuando hay dinero en juego.
    """
    bloque = _bloque_ha_comprado()
    assert "-eq 0" in bloque, "ha desaparecido la rama de cero intentos"
    corte = bloque.index("-eq 0")
    assert "else" in bloque[corte:], (
        "no hay rama para cuando SI hubo intentos: el panel se vuelve a callar "
        "en el unico caso en que el bot esta gastando dinero"
    )


def test_dice_cuantas_posiciones_hay_abiertas() -> None:
    """Con el 50% de la cartera por compra, es la pregunta cara.

    Y el nombre del fichero importa: los marcadores son `open_position*.json`
    sueltos en la raiz (`_open_position_paths` en universal_trader.py), no una
    lista dentro de un json. La primera version de esta linea busco un
    `trades/posiciones.json` inexistente, que habria dicho 0 para siempre.
    """
    codigo = _solo_codigo(_bloque_ha_comprado())
    assert "open_position*.json" in codigo, (
        "el panel no mira los marcadores de posicion de verdad"
    )
    assert "posiciones.json" not in codigo, (
        "ha vuelto el nombre de fichero inventado: diria 0 para siempre"
    )


def test_el_marcador_que_busca_el_panel_es_el_que_escribe_el_bot() -> None:
    """Ata el panel al codigo: si el bot renombra el marcador, esto sale rojo.

    Sin esto, el test de arriba solo comprueba que el panel es coherente
    consigo mismo, que es justo lo que no sirve para nada.
    """
    trader = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    assert 'Path(f"open_position{_sfx}.json")' in trader, (
        "el bot ha cambiado el nombre del marcador de posicion abierta: hay "
        "que actualizar el panel `bot` en deploy/instalar_comandos.sh"
    )


def test_las_frases_del_bot_que_cuenta_el_panel_siguen_existiendo() -> None:
    """Un panel que cuenta frases del log caduca en silencio si el log cambia.

    Es el fallo 1 de la cabecera: contaba 'PRESION —' cuando la fuente ya era
    otra, y salia 0 sin que nada lo dijera. Aqui se comprueba que las tres
    frases que cuenta el bloque siguen saliendo del trader.
    """
    trader = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")
    for frase in ("Buying ", "Successfully bought ", "Failed to buy "):
        assert frase in trader, (
            f"el bot ya no escribe {frase!r} en el log, asi que el panel "
            f"contaria 0 pasara lo que pasara"
        )
