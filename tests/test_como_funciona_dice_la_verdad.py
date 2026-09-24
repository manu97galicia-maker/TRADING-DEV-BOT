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
    t = _cfg()["trade"]
    assert f"{t['fraccion_saldo'] * 100:.0f}% del SOL libre" in _texto()
    assert f"{t['max_posiciones']} posiciones abiertas" in _texto()


def test_el_stop_es_el_de_verdad() -> None:
    """Es la cifra que decide cuanto se pierde. Si la nota miente aqui, quien
    la lea creera que arriesga menos de lo que arriesga."""
    t = _cfg()["trade"]
    assert f"-{t['stop_loss_percentage'] * 100:.0f}%" in _texto()
    assert f"+{t['margen_stop_tras_tp'] * 100:.0f}%" in _texto()


def test_la_escalera_de_porcentaje_esta_entera() -> None:
    t = _cfg()["trade"]
    texto = _texto()
    peldanos = [
        (t["take_profit_percentage"], t["take_profit_sell_percentage"]),
        (t["tp2_gain"], t["tp2_sell"]),
        (t["tp3_gain"], t["tp3_sell"]),
        (t["tp4_gain"], t["tp4_sell"]),
    ]
    for g, v in peldanos:
        assert f"+{g * 100:.0f}%" in texto, f"falta el peldano +{g * 100:.0f}%"
        assert f"| {v * 100:.0f}% |" in texto, f"falta que vende {v * 100:.0f}%"


def test_los_escalones_de_market_cap_estan_enteros() -> None:
    texto = _texto()
    for d in _cfg()["trade"]["mc_take_profit_tiers"]:
        assert f"{d['target_mc'] / 1000:.0f}k" in texto


def test_el_moonbag_es_el_de_verdad() -> None:
    """Lo que el bot NO vende. Quien lea la nota tiene que saber que ese resto
    se queda en la cartera esperando a que alguien lo venda a mano."""
    t = _cfg()["trade"]
    vendido = sum(
        [t["take_profit_sell_percentage"], t["tp2_sell"], t["tp3_sell"], t["tp4_sell"]]
    ) + sum(d["sell"] for d in t["mc_take_profit_tiers"])
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
