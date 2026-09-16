"""Las listas de devs: juntarlas bien y puntuarlas por lo que importa.

EL PROBLEMA DE PARTIDA. En la raiz del repo habia seis listas de devs, hechas
en julio y agosto, midiendo cosas distintas, y ninguna la lee el bot que esta
corriendo. Peor: ninguna mide lo que se pidio -- que no hagan rug, que suelan
pagar DexScreener y boost, que les pongan redes y que lancen a menudo.

LAS DOS TRAMPAS QUE ESTOS TESTS VIGILAN
* Ordenar devs por su PICO. El campo `ath_market_cap` de pump.fun se infla
  solo (hay creadores con 89 millones de maximo y 1.387 $ de liquidez), asi
  que premiar picos es premiar granjas. Se ordena por migracion, que exige
  85 SOL de verdad y no se puede fingir.
* Dar por bueno a un dev que ya no esta. Es el fallo silencioso de toda lista
  vieja: un dev intachable en julio que lleva dos meses sin lanzar sigue
  saliendo el primero. Aqui saca cero.
"""

# ruff: noqa: S101, PLR2004, ANN003, ANN202, SLF001

import importlib.util
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _modulo(nombre: str):
    ruta = RAIZ / "deploy" / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dev(**kw) -> dict:
    base = {
        "activo": True, "tasa_migracion": 0.0, "pct_pago_dex": 0.0,
        "pct_boost": 0.0, "pct_redes": 0.0, "lanz_30d": 0, "pct_muertas": 0.0,
    }
    base.update(kw)
    return base


# ------------------------------------------------------------ la nota


def test_un_dev_parado_saca_cero_aunque_fuera_perfecto() -> None:
    """LA TRAMPA DE LAS LISTAS VIEJAS. Comprarle a un dev que lleva dos meses
    sin lanzar es comprarle a un fantasma."""
    md = _modulo("medir_devs")
    perfecto_pero_ido = _dev(
        activo=False, tasa_migracion=1.0, pct_pago_dex=1.0,
        pct_boost=1.0, pct_redes=1.0, lanz_30d=0,
    )
    assert md._puntuar(perfecto_pero_ido) == 0.0


def test_no_hundir_pesa_mas_que_cualquier_otra_senal_suelta() -> None:
    """Es lo primero que se pidio: que no hagan rug.

    La comparacion es contra cada senal POR SEPARADO, no contra todas juntas:
    un dev que paga dex, boostea, pone redes y lanza a diario suma mas que uno
    que solo migra bien, y esta bien que asi sea. Lo que no puede pasar es que
    una sola de esas cosas pese tanto como no hundir sus monedas.
    """
    md = _modulo("medir_devs")
    migrando = md._puntuar(_dev(tasa_migracion=0.30))
    for senal, tope in (
        ("pct_pago_dex", 1.0), ("pct_boost", 1.0),
        ("pct_redes", 1.0), ("lanz_30d", 50),
    ):
        # `activo` necesita lanzamientos: se le dan los minimos para que la
        # comparacion no se decida por eso.
        otra = md._puntuar(_dev(**{senal: tope, "lanz_30d": max(1, int(tope))}))
        assert migrando > otra, f"'{senal}' pesa tanto como no hundir"


def test_dejar_cadaveres_baja_la_nota() -> None:
    """El sintoma directo del pump and dump."""
    md = _modulo("medir_devs")
    limpio = _dev(tasa_migracion=0.3, lanz_30d=10, pct_muertas=0.0)
    cementerio = _dev(tasa_migracion=0.3, lanz_30d=10, pct_muertas=1.0)
    assert md._puntuar(limpio) > md._puntuar(cementerio)


def test_un_rugger_activo_saca_menos_que_un_dev_normal() -> None:
    md = _modulo("medir_devs")
    rugger = _dev(tasa_migracion=0.02, lanz_30d=40, pct_muertas=0.9)
    normal = _dev(tasa_migracion=0.25, lanz_30d=5, pct_redes=0.5, pct_muertas=0.3)
    assert md._puntuar(normal) > md._puntuar(rugger)


def test_la_nota_no_se_sale_de_cien() -> None:
    md = _modulo("medir_devs")
    exagerado = _dev(
        tasa_migracion=1.0, pct_pago_dex=1.0, pct_boost=1.0,
        pct_redes=1.0, lanz_30d=500, pct_muertas=0.0,
    )
    assert md._puntuar(exagerado) <= 100.0


def test_no_se_puntua_por_el_pico() -> None:
    """`ath_market_cap` se infla solo: ordenar por el premia a las granjas."""
    fuente = (RAIZ / "deploy" / "medir_devs.py").read_text(encoding="utf-8")
    cuerpo = fuente[fuente.index("def _puntuar("):]
    cuerpo = cuerpo[: cuerpo.index("\nasync def ")]
    for prohibido in ("ath", "peak", "pico"):
        assert prohibido not in cuerpo.lower(), f"la nota usa '{prohibido}'"


# ------------------------------------------------------- unir las listas


def test_un_dev_vetado_no_sale_en_la_lista_buena() -> None:
    """La blacklist manda sobre todo lo demas."""
    datos = json.loads(
        (RAIZ / "devs_unificados.json").read_text(encoding="utf-8")
    )
    vetados = {v["wallet"] for v in datos["vetados"]}
    buenos = {d["wallet"] for d in datos["wallets"]}
    assert not (vetados & buenos)


def test_cada_dev_sabe_de_que_listas_viene() -> None:
    """Sin la procedencia no se puede juzgar el dato: una lista de julio y
    una de agosto no valen lo mismo."""
    datos = json.loads(
        (RAIZ / "devs_unificados.json").read_text(encoding="utf-8")
    )
    for d in datos["wallets"]:
        assert d["listas"], f"{d['wallet']} sin procedencia"
        assert d["n_listas"] == len(d["listas"])


def test_los_que_coinciden_en_varias_van_primero() -> None:
    """Es la unica senal que se puede sacar sin red: coincidir en listas
    hechas con criterios distintos es pasar varios examenes."""
    datos = json.loads(
        (RAIZ / "devs_unificados.json").read_text(encoding="utf-8")
    )
    n = [d["n_listas"] for d in datos["wallets"]]
    assert n == sorted(n, reverse=True)


def test_la_union_no_pierde_devs_por_el_camino() -> None:
    """Los formatos de origen son tres distintos; uno mal leido perderia
    una lista entera sin avisar."""
    ul = _modulo("unir_listas_devs")
    total = 0
    for fichero in ul.FUENTES:
        ruta = RAIZ / fichero
        if ruta.exists():
            n = len(ul._wallets(json.loads(ruta.read_text(encoding="utf-8"))))
            assert n > 0, f"{fichero} se lee como vacio"
            total += n
    datos = json.loads((RAIZ / "devs_unificados.json").read_text(encoding="utf-8"))
    # Hay devs repetidos entre listas, asi que la union es menor que la suma,
    # pero no puede ser mayor.
    assert 0 < len(datos["wallets"]) <= total


# ------------------------------------------- lanzarlo desde el movil


def _script() -> str:
    return (RAIZ / "deploy" / "devs.sh").read_text(encoding="utf-8")


def test_la_medicion_sobrevive_a_que_se_caiga_el_ssh() -> None:
    """EL FALLO REAL DEL 16-SEP. Medir 180 devs son varios minutos, y la
    sesion de SSH del movil se corta al bloquear la pantalla: la medicion
    murio a mitad y no quedo nada. `setsid` la saca de la sesion para que
    siga viva cuando esta muera.
    """
    s = _script()
    assert "setsid" in s
    assert "nohup" in s


def test_no_se_lanzan_dos_mediciones_a_la_vez() -> None:
    """Dos procesos pidiendo a las mismas APIs es la forma mas rapida de que
    te corten por exceso de peticiones."""
    s = _script()
    assert 'pgrep -f "medir_devs.py"' in s


def test_subir_no_manda_nada_si_no_hay_medicion() -> None:
    """Un commit vacio no dice nada y confunde: parece que hay datos."""
    s = _script()
    subir = s[s.index("  subir)") :]
    assert 'if [ ! -f "$SALIDA" ]' in subir


def test_solo_se_suben_los_ficheros_de_devs() -> None:
    """Nada de `git add -A` aqui: el droplet tiene el .env y los logs, y un
    add a ciegas podria mandar a GitHub lo que no debe."""
    s = _script()
    assert "git add devs_medidos.json devs_unificados.json" in s
    assert "git add -A" not in s
    assert "git add ." not in s
