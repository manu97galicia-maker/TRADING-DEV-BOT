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
TECHO_PEDIDO = 23000

#: Matematica de la bonding curve. El MC en SOL es exacto y no depende del
#: precio de SOL; por eso todo lo estructural se comprueba en SOL.
K = 30 * 1073e6
VT0 = 1073e6
SUPPLY = 1e9
#: MC en SOL cuando la curva se completa y la moneda migra.
MC_SOL_AL_MIGRAR = 410.9

#: Precio de SOL, ya CONFIRMADO. Antes se barajaban $104 y $118 porque no se
#: sabia si el droplet corria el 88% o el 91% de curva. El 15-oct el log en
#: vivo lo zanjo: corria el 91% ("CURVA AL 91.2% / 91.9% / 97.4%"), asi que
#: de las entradas observadas en 27-28k sale 27000/260,9 = $103 y
#: 28000/260,9 = $107. Se deja un margen hasta 108.
SOL_CALIBRADO = (103.0, 108.0)

#: Cuanto puede faltar, en SOL de compras netas, entre donde el bot empieza a
#: mirar y el suelo de la banda. Es el numero que fallo el 15-oct: con el
#: umbral al 77% hacian falta 4,7 SOL en menos de 60s y el bot no compraba.
MAX_SOL_HASTA_LA_BANDA = 2.0


def _cfg() -> dict:
    return yaml.safe_load(C123.read_text(encoding="utf-8"))


def _mc_sol(avance: float) -> float:
    vt = VT0 - 793.1e6 * avance
    return (K / vt**2) * SUPPLY


def test_la_banda_es_la_pedida() -> None:
    filtros = _cfg()["filters"]
    assert filtros["min_entry_mc"] == SUELO_PEDIDO
    assert filtros["max_entry_mc"] == TECHO_PEDIDO


def test_el_umbral_deja_la_banda_al_alcance() -> None:
    """EL FALLO DEL 15-oct, convertido en guardian.

    La version anterior de este test solo exigia que el umbral disparase POR
    DEBAJO del suelo de la banda. Eso lo cumplia el 77%... y el bot NO
    COMPRABA, porque entre el 77% y el suelo de 18.000 $ (el 80,7% con SOL a
    105) hay 3,7 puntos de curva = 4,7 SOL de compras netas, y habia que
    cruzarlos dentro de los 60s de `max_buy_age`.

    Lo que de verdad importa no es "estar por debajo" sino ESTAR CERCA:

        umbral   MC        hueco hasta la banda
          77%    $15.814   4,7 SOL   <- no compraba
          79%    $16.958   2,2 SOL
          80%    $17.576   0,9 SOL   <- puesto

    Se mide en SOL dentro de la curva (k/vt - 30) y no en puntos de curva,
    porque los puntos no son equivalentes entre si: la curva es un producto
    constante y cada punto cuesta mas SOL que el anterior.
    """
    cfg = _cfg()
    umbral = cfg["filters"]["curva_96"]["umbral"]

    def dentro(avance: float) -> float:
        return K / (VT0 - 793.1e6 * avance) - 30

    def avance_de(mc_usd: float, precio: float) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(200):
            m = (lo + hi) / 2
            if _mc_sol(m) * precio < mc_usd:
                lo = m
            else:
                hi = m
        return (lo + hi) / 2

    for precio in SOL_CALIBRADO:
        mc_al_mirar = _mc_sol(umbral) * precio
        assert mc_al_mirar < TECHO_PEDIDO, (
            f"con SOL a ${precio:.0f} el umbral dispara en "
            f"${mc_al_mirar:,.0f}, por encima del techo de la banda: no "
            "compraria nunca"
        )
        if mc_al_mirar >= SUELO_PEDIDO:
            continue  # ya dispara dentro de la banda: compra al instante

        falta = dentro(avance_de(SUELO_PEDIDO, precio)) - dentro(umbral)
        assert falta <= MAX_SOL_HASTA_LA_BANDA, (
            f"con SOL a ${precio:.0f} hacen falta {falta:.1f} SOL de compras "
            f"para ir del umbral ({umbral * 100:.0f}%) al suelo de la banda, "
            f"y solo hay {cfg['filters']['max_buy_age']}s: el bot se queda "
            "sin comprar (paso el 15-oct con el 77%)"
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


# ------------------------------------------- volumen y presion (15-oct)
def test_el_volumen_y_la_presion_estan_puestos() -> None:
    """Pedido: "un poco mas de volumen y ligeramente mayor presion"."""
    filtros = _cfg()["filters"]
    assert filtros["min_volumen_sol"] == 8
    assert filtros["min_presion_ratio"] == pytest.approx(1.2)
    assert filtros["presion_seg"] > 0, "sin ventana, la presion no mide nada"


def test_la_presion_ya_no_esta_muda() -> None:
    """EL FALLO QUE TENIA ESTE NUMERO HASTA HOY.

    El chequeo de presion vive dentro del bloque del `max_buy_age`, y la
    puerta de ese bloque NO incluye `min_presion_ratio`:

        if max_buy_age > 0 and (min_volumen_sol>0 or min_entry_mc>0
                                or min_buys>0 or min_first_block_buys>0
                                or min_ops>0 or min_titulares>0
                                or second_wave.enabled)

    Con todos esos a cero, poner `min_presion_ratio` no hacia NADA. Es el
    mismo fallo de familia que la REGLA 0 lleva escrito. Ahora la puerta la
    abren `min_entry_mc` y `min_volumen_sol`.

    Este test se pone rojo si alguien apaga los dos a la vez dejando la
    presion encendida: quedaria escrita y muda otra vez.
    """
    filtros = _cfg()["filters"]
    if filtros["min_presion_ratio"] <= 0:
        return  # apagada a proposito: no hay nada que vigilar

    abridores = (
        filtros.get("min_volumen_sol", 0),
        filtros.get("min_entry_mc", 0),
        filtros.get("min_buys", 0),
        filtros.get("min_first_block_buys", 0),
        filtros.get("min_ops", 0),
        filtros.get("min_titulares", 0),
    )
    segunda_ola = bool((filtros.get("second_wave") or {}).get("enabled"))
    assert filtros["max_buy_age"] > 0, (
        "con max_buy_age 0 el bucle no corre y la presion queda muda"
    )
    assert any(x > 0 for x in abridores) or segunda_ola, (
        "min_presion_ratio esta encendida pero NINGUNO de los filtros que "
        "abren el bloque lo esta: quedaria escrita y sin efecto"
    )


def test_sin_datos_la_presion_deja_pasar() -> None:
    """ "Ventana vacia" no es "esta cayendo", y confundirlo veta todo.

    Medido el 10-ago: las 6 monedas que cruzaron el volumen pedido lo
    hicieron con la ventana de presion vacia (0,00 / 0,00) y se descartaron
    las seis. Por eso el codigo trata el silencio como "pasa".
    """
    assert "_ok_presion = True" in FUENTE
    i = FUENTE.index("if _pc <= 0 and _pv <= 0:")
    assert "_ok_presion = True" in FUENTE[i : i + 1200]


def test_si_falta_volumen_espera_en_vez_de_descartar() -> None:
    """Como la banda de market cap: una moneda que esta subiendo hacia el
    minimo no debe perderse por mirarla un segundo antes de tiempo."""
    i = FUENTE.index("if self.min_vol_ventana_sol > 0:")
    bloque = FUENTE[i : i + 320]
    assert "await asyncio.sleep" in bloque
    assert "continue" in bloque


def test_el_volumen_pedido_cabe_en_lo_que_mueve_la_curva() -> None:
    """El riesgo real de este filtro es DEJAR AL BOT SIN COMPRAR.

    Ya paso en este repo: `min_compradores` quedo en 30 calibrado para una
    entrada al 96% mientras la entrada bajaba al 71%, y el bot dejo de
    comprar durante dias sin que nadie supiera por que.

    Los SOL que hay DENTRO de la curva en cada punto salen de k/vt - 30. El
    minimo de volumen tiene que quedar holgadamente por debajo de lo que una
    moneda ya ha movido para llegar a la banda, o no entrara ninguna.
    """
    cfg = _cfg()
    minimo = cfg["filters"]["min_volumen_sol"]
    if minimo <= 0:
        return

    def sol_dentro(avance: float) -> float:
        vt = VT0 - 793.1e6 * avance
        return K / vt - 30

    umbral = cfg["filters"]["curva_96"]["umbral"]
    dentro_al_mirar = sol_dentro(umbral)
    assert minimo < dentro_al_mirar * 0.75, (
        f"el minimo de volumen ({minimo} SOL) se acerca demasiado a los "
        f"{dentro_al_mirar:.1f} SOL que ya hay en la curva al "
        f"{umbral * 100:.0f}%: riesgo de quedarse sin comprar"
    )
