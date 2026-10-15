"""La entrada se decide en DOLARES, no en porcentaje de curva.

PEDIDO (15-oct, palabras de Manu): "este bien esta pillando buenas subidas
pero entra entorno a los 27k a ver si consigues lo mismo pero entrando
entorno 18/22k".

POR QUE EN DOLARES. El objetivo esta en dolares y un porcentaje de curva es
fijo mientras el precio de SOL se mueve. El mismo 82% de curva daba 18-21k
con SOL a 104-118 y daria 24-27k con SOL a 150. Por eso ese parametro se
habia movido ONCE veces en unos dias: no era indecision, era perseguir un
objetivo en dolares con una herramienta que no mide dolares.

QUE VIGILA ESTE FICHERO:

1. LA BANDA EXISTE Y ES LA PEDIDA.
2. EL UMBRAL DE CURVA DISPARA POR DEBAJO DEL SUELO. Si disparase dentro o
   por encima de la banda, el bot compraria al cruzar y la banda no mandaria
   -- quedaria escrita y muda, que es el fallo de familia de este repo.
3. LAS PUERTAS TRASERAS SIGUEN CERRADAS. `entrada_alternativa` y
   `dev_mediana` permiten comprar POR ENCIMA de `max_entry_mc`. Si alguien
   anade cualquiera de las dos, el techo de 22.000 deja de ser un techo.
4. EL TP SIGUE CABIENDO. Entrar antes da mas techo, pero el guardian tiene
   que seguir siendo cierto.
"""

# ruff: noqa: S101, PLR2004

from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent
C123 = RAIZ / "bots/bot-unica123-real.yaml"
FUENTE = (RAIZ / "src/trading/universal_trader.py").read_text(encoding="utf-8")

SUELO_PEDIDO = 18000
TECHO_PEDIDO = 22000

#: Matematica de la bonding curve. El MC en SOL es exacto y no depende del
#: precio de SOL; por eso todo lo estructural se comprueba en SOL.
K = 30 * 1073e6
VT0 = 1073e6
SUPPLY = 1e9
#: MC en SOL cuando la curva se completa y la moneda migra.
MC_SOL_AL_MIGRAR = 410.9

#: Los dos precios de SOL que explican la observacion de Manu ("entra entorno
#: a los 27k"), segun si el droplet corria el 88% o el 91% de curva. No se
#: sabe cual, asi que la banda tiene que funcionar con LOS DOS.
SOL_CALIBRADO = (104.0, 118.0)


def _cfg() -> dict:
    return yaml.safe_load(C123.read_text(encoding="utf-8"))


def _mc_sol(avance: float) -> float:
    vt = VT0 - 793.1e6 * avance
    return (K / vt**2) * SUPPLY


def test_la_banda_es_la_pedida() -> None:
    filtros = _cfg()["filters"]
    assert filtros["min_entry_mc"] == SUELO_PEDIDO
    assert filtros["max_entry_mc"] == TECHO_PEDIDO


def test_el_umbral_dispara_por_debajo_del_suelo_con_los_dos_precios() -> None:
    """EL INVARIANTE QUE HACE QUE LA BANDA MANDE.

    Si el umbral disparase ya dentro de la banda, el bot compraria al cruzar
    y `min_entry_mc` no decidiria nada. Y si disparase por ENCIMA del techo,
    no compraria nunca.

    Se exige con los dos precios de SOL calibrados porque no se sabe cual
    corria el droplet cuando Manu observo los 27k.
    """
    cfg = _cfg()
    umbral = cfg["filters"]["curva_96"]["umbral"]
    mc_en_sol = _mc_sol(umbral)

    for precio in SOL_CALIBRADO:
        mc_usd = mc_en_sol * precio
        assert mc_usd < SUELO_PEDIDO, (
            f"con SOL a ${precio:.0f} el umbral del {umbral * 100:.0f}% "
            f"dispara en ${mc_usd:,.0f}, que ya esta en la banda o por "
            "encima: entonces la banda no decide nada"
        )


def test_el_umbral_no_esta_tan_abajo_que_no_llegue_a_la_banda() -> None:
    """El otro lado del invariante.

    Cuanto mas abajo dispare, mas tiene que subir la moneda para entrar en
    banda y mas probable es que se agoten los 60s de `max_buy_age`. Se exige
    que la subida necesaria sea razonable (menos de un x1,5).
    """
    cfg = _cfg()
    umbral = cfg["filters"]["curva_96"]["umbral"]
    mc_en_sol = _mc_sol(umbral)

    for precio in SOL_CALIBRADO:
        falta = SUELO_PEDIDO / (mc_en_sol * precio)
        assert falta < 1.5, (
            f"con SOL a ${precio:.0f} la moneda tendria que subir "
            f"+{(falta - 1) * 100:.0f}% para entrar en banda, y solo hay "
            f"{cfg['filters']['max_buy_age']}s"
        )


def test_la_puerta_del_max_buy_age_queda_abierta() -> None:
    """Sin esto la banda seria un numero escrito y MUDO.

    El chequeo de MC vive dentro de
    `if max_buy_age > 0 and (min_volumen_sol>0 or min_entry_mc>0 or ...)`.
    Con `max_buy_age` en 0, el bucle no corre NI UNA VEZ y la banda no se
    comprueba jamas. Es el mismo fallo que tenia `min_presion_ratio`.
    """
    filtros = _cfg()["filters"]
    assert filtros["max_buy_age"] > 0, "con max_buy_age 0 la banda queda muda"
    assert filtros["min_entry_mc"] > 0, "es la llave que abre el bloque"
    assert "or self.min_entry_mc > 0" in FUENTE, (
        "min_entry_mc ya no abre la puerta: la banda quedaria muda"
    )


def test_las_puertas_traseras_del_techo_siguen_cerradas() -> None:
    """`entrada_alternativa` y `dev_mediana` compran POR ENCIMA del techo.

    El codigo lo hace a proposito (universal_trader.py:4276): si la moneda se
    paso de la banda pero sigue bajo un techo alternativo Y movio volumen, se
    compra igual. Con cualquiera de las dos puesta, el 22.000 deja de ser un
    techo y Manu entraria donde no quiere.
    """
    cfg = _cfg()
    filtros = cfg["filters"]
    assert not filtros.get("entrada_alternativa"), (
        "entrada_alternativa abre una segunda puerta por encima de "
        "max_entry_mc: el techo deja de ser duro"
    )
    assert not filtros.get("dev_mediana"), (
        "dev_mediana abre una tercera puerta por encima de max_entry_mc"
    )
    # Y que el codigo siga teniendo esas puertas detras de un 0, no abiertas
    # por defecto.
    assert "self.entrada_alt_max_mc > 0" in FUENTE


def test_el_mc_se_calcula_con_el_precio_de_sol_en_vivo() -> None:
    """Si usara un precio fijo, la banda volveria a derivar con el mercado.

    Es TODA la razon de haber cambiado de porcentaje de curva a dolares.
    """
    assert "_mc = _p * _PUMP_TOTAL_SUPPLY * _SOL_USD" in FUENTE


def test_por_debajo_del_suelo_espera_y_por_encima_del_techo_descarta() -> None:
    """Las dos mitades del comportamiento, leidas del codigo.

    Por debajo del suelo tiene que ESPERAR (sleep + continue), no descartar:
    si descartara, una moneda que esta subiendo hacia la banda se perderia.
    """
    i = FUENTE.index("if self.min_entry_mc > 0 and _mc < self.min_entry_mc:")
    bloque = FUENTE[i : i + 200]
    assert "await asyncio.sleep" in bloque, "por debajo del suelo debe ESPERAR"
    assert "continue" in bloque, "y seguir mirando, no descartar"

    j = FUENTE.index("if self.max_entry_mc > 0 and _mc > self.max_entry_mc:")
    assert "se paso de la banda" in FUENTE[j : j + 3000]


def test_el_tp_sigue_cabiendo_en_la_curva() -> None:
    """Entrar antes da mas techo, pero hay que comprobarlo, no suponerlo."""
    cfg = _cfg()
    umbral = cfg["filters"]["curva_96"]["umbral"]
    techo = MC_SOL_AL_MIGRAR / _mc_sol(umbral) - 1
    tp = cfg["trade"]["take_profit_percentage"]
    assert tp <= techo * 0.75, (
        f"el TP de +{tp * 100:.0f}% se come el {tp / techo * 100:.0f}% del "
        f"recorrido disponible (+{techo * 100:.0f}%)"
    )


def test_el_antirug_esta_encendido_al_entrar_tan_abajo() -> None:
    """Pedido el 15-oct junto con la banda: "encenderlo a 1.6".

    Entrando al 77% quedan 23 puntos de curva por recorrer con la moneda
    dentro, que es justo el tramo donde un dev puede tirarla. Apagarlo era
    defendible al 91%; al 77% no. El caso FUCKETHAN (-91,9%) fue con estos
    frenos apagados.
    """
    trade = _cfg()["trade"]
    assert trade["anti_rug_ratio"] == pytest.approx(1.6)
    assert trade["anti_rug_seg"] > 0
    assert trade["anti_rug_min_sol"] > 0
