"""La nota de `COMO_FUNCIONA.md` tiene que decir los numeros de verdad.

POR QUE SE TESTEA UNA NOTA. Esta escrita para alguien que llega nuevo y que NO
va a abrir el yaml: se la va a creer entera. Una nota con numeros viejos es
peor que no tenerla -- manda a buscar el fallo donde no esta, y quien la lee
no tiene forma de saber que esta desfasada.

Y la configuracion de este bot ha cambiado NUEVE veces en un solo dia. Una
nota escrita a mano se queda atras en horas.

LO QUE NO COMPRUEBA: la redaccion. Solo que las cifras que aparecen sigan
siendo las que corren. Si alguien cambia un numero en el yaml y no toca la
nota, esto se pone rojo y dice cual.
"""

# ruff: noqa: S101

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
NOTA = RAIZ / "COMO_FUNCIONA.md"
CONFIG = RAIZ / "bots/bot-paper-ai-entry.yaml"


def _texto() -> str:
    return NOTA.read_text(encoding="utf-8")


def _cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_la_nota_existe() -> None:
    assert NOTA.exists(), "la nota se ha borrado"


def test_el_punto_de_entrada_es_el_de_verdad() -> None:
    """El numero que mas ha cambiado hoy: once veces."""
    c = _cfg()["filters"]["curva_96"]
    assert f"{c['umbral'] * 100:.1f}%".replace(".", ",") in _texto()


def test_el_tamano_de_compra_es_el_de_verdad() -> None:
    """EL PORCENTAJE Y SU BASE, porque uno sin la otra no dice nada.

    "El 25%" significa cosas muy distintas segun sobre que se calcule: el 25%
    del saldo LIBRE mengua con cada compra y el 25% de la CARTERA no. La nota
    tiene que decir las dos cosas, y el test las saca del yaml para que no se
    puedan separar.
    """
    t = _cfg()["trade"]
    base = str(t.get("fraccion_base", "cartera")).strip().lower()
    etiqueta = "del SOL libre" if base == "libre" else "de la cartera"
    assert f"{t['fraccion_saldo'] * 100:.0f}% {etiqueta}" in _texto(), (
        f"la nota tiene que decir '{t['fraccion_saldo'] * 100:.0f}% {etiqueta}'"
    )
    assert f"{t['max_posiciones']} posiciones abiertas" in _texto()


def test_el_stop_es_el_de_verdad() -> None:
    """Es la cifra que decide cuanto se pierde. Si la nota miente aqui, quien
    la lea creera que arriesga menos de lo que arriesga."""
    t = _cfg()["trade"]
    assert f"-{t['stop_loss_percentage'] * 100:.0f}%" in _texto()
    assert f"+{t['margen_stop_tras_tp'] * 100:.0f}%" in _texto()


def _peldanos_de_porcentaje(t: dict) -> list[tuple[float, float]]:
    """Los peldanos que HAYA, sin dar por hecho cuantos son.

    La primera version de esto pedia `tp4_gain` a mano. El 24-sep se quito el
    cuarto peldano y el test se cayo con un KeyError -- que avisa, si, pero
    del fichero equivocado: parecia un fallo de la nota cuando lo que habia
    cambiado era la escalera. Leyendo los que existan, quitar o anadir un
    peldano sigue poniendo el test en rojo (la nota ya no los lista todos),
    pero el error senala la tabla de la nota, que es donde hay que mirar.
    """
    pares = [(t["take_profit_percentage"], t["take_profit_sell_percentage"])]
    for i in range(2, 10):
        if f"tp{i}_gain" not in t:
            break
        pares.append((t[f"tp{i}_gain"], t[f"tp{i}_sell"]))
    return pares


def test_la_escalera_de_porcentaje_esta_entera() -> None:
    t = _cfg()["trade"]
    texto = _texto()
    peldanos = _peldanos_de_porcentaje(t)
    for g, v in peldanos:
        assert f"+{g * 100:.0f}%" in texto, f"falta el peldano +{g * 100:.0f}%"
        assert f"| {v * 100:.0f}% |" in texto, f"falta que vende {v * 100:.0f}%"
    # Y que la nota no liste peldanos que ya no existen: el cuarto (+125%) se
    # quito el 24-sep y la tabla se quedo con el una version entera.
    #
    # SOLO DENTRO DE LA TABLA DE PELDANOS, que se localiza por su cabecera.
    # Dos versiones anteriores de esta comprobacion dieron falso positivo:
    #   1. buscando la cifra en TODA la nota, por un "+31,25% y no en +30%"
    #      de un parrafo explicativo;
    #   2. buscando filas "| +X% |" en cualquier tabla, porque la nota tiene
    #      ahora una segunda tabla --la de "que pasa si la moneda llega a
    #      +30%"-- cuya primera columna tiene la misma forma.
    # Un falso positivo aqui manda a corregir una tabla que esta bien, que es
    # peor que no avisar.
    tabla = _tabla_de_peldanos(texto)
    vivos = [f"| +{g * 100:.0f}% |" for g, _ in peldanos]
    for muerto in ("+125%", "+90%", "+85%", "+50%", "+38%", "+30%", "+18%", "+15%"):
        fila = f"| {muerto} |"
        assert fila in vivos or fila not in tabla, (
            f"la tabla de peldanos de la nota sigue listando {muerto}"
        )


def _tabla_de_peldanos(texto: str) -> str:
    """El bloque de la tabla que lista los peldanos, y solo ese.

    Se localiza por su cabecera --la fila que empieza por "| sube |"-- y
    acaba en la primera linea en blanco. Si la nota deja de tener esa
    cabecera, este helper devuelve cadena vacia y la comprobacion de arriba
    pasa a no comprobar nada, asi que tambien se exige que exista.
    """
    lineas = texto.splitlines()
    ini = next(
        (i for i, linea in enumerate(lineas) if linea.startswith("| sube |")),
        None,
    )
    assert ini is not None, (
        "la nota ya no tiene la tabla de peldanos (cabecera '| sube |'): "
        "si ha cambiado de formato, hay que actualizar este test"
    )
    fin = next((i for i in range(ini, len(lineas)) if not lineas[i].strip()), len(lineas))
    return "\n".join(lineas[ini:fin])


def test_los_escalones_de_market_cap_estan_enteros() -> None:
    """Y si no hay ninguno, que la nota no los prometa.

    La tarde del 24-sep `mc_take_profit_tiers` se quedo vacia y este test se
    volvio VACUO: el bucle no daba ni una vuelta y se quedaba verde con una
    nota que seguia listando cuatro escalones de market cap inexistentes.
    Por eso hay ahora una rama para la lista vacia.
    """
    texto = _texto()
    mc = _cfg()["trade"]["mc_take_profit_tiers"]
    if not mc:
        assert "Por market cap" not in texto, (
            "la nota anuncia una escalera de market cap que ya no existe"
        )
        return
    for d in mc:
        assert f"{d['target_mc'] / 1000:.0f}k" in texto


def test_los_peldanos_lejanos_estan_en_la_nota() -> None:
    """Los de `tp_extra`. Son el 22% de la posicion: quien lea la nota tiene
    que poder ver donde se vende ese trozo, no solo los tres de cerca."""
    texto = _texto()
    for d in _cfg()["trade"].get("tp_extra") or []:
        multiplo = f"x{1 + d['gain']:.2f}".replace(".", ",")
        assert multiplo in texto, f"falta el escalon {multiplo}"


def test_el_moonbag_es_el_de_verdad() -> None:
    """Lo que el bot NO vende. Quien lea la nota tiene que saber que ese resto
    se queda en la cartera esperando a que alguien lo venda a mano."""
    t = _cfg()["trade"]
    vendido = sum(v for _, v in _peldanos_de_porcentaje(t)) + sum(
        d["sell"] for d in t["mc_take_profit_tiers"]
    )
    assert f"{(1 - vendido) * 100:.0f}%" in _texto()


def test_el_anti_rug_es_el_de_verdad() -> None:
    t = _cfg()["trade"]
    texto = _texto()
    assert f"{t['anti_rug_seg']:.0f} segundos" in texto
    assert f"{t['anti_rug_min_sol']:.0f} SOL" in texto


def test_no_promete_plataformas_que_no_hay() -> None:
    """La nota dice "pump.fun, solo". Si algun dia se enciende otra y no se
    actualiza, quien la lea buscara compras que no van a existir."""
    c = _cfg()
    assert c["platform"] == "pump_fun"
    encendidas = [
        k for k, v in c["filters"].items()
        if isinstance(v, dict) and v.get("enabled") and k != "dexscreener"
    ]
    assert encendidas == ["curva_96"], (
        f"la nota dice que solo esta la curva, pero hay: {encendidas}"
    )


def test_avisa_de_que_no_hay_mediciones() -> None:
    """EL PARRAFO QUE NO SE PUEDE CAER. Todo lo demas de la nota son numeros
    bien razonados, y suenan a certeza. Sin este aviso, quien la lea creeria
    que esta configuracion esta probada -- y no lo esta."""
    texto = _texto().lower()
    assert "no hay ni una medición todavía" in texto
    assert "optimizar_escalera" in texto
