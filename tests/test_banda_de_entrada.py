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

LA BANDA ESTA APAGADA DESDE EL 16-oct. Manu pidio "no compra pon la anterior
configuracion" y se volvio a la entrada por umbral de curva, que es la que si
compraba. ESTE FICHERO SE QUEDA ENTERO y cada test se salta solo cuando la
banda no esta puesta: lo que vigila no es la decision --esa cambia-- sino que,
EL DIA QUE SE VUELVA A ENCENDER, no se repitan los dos fallos que costaron la
noche del 15-oct: el umbral demasiado lejos del suelo (4,7 SOL de hueco en
60s) y la presion escrita pero muda.
"""


# ruff: noqa: S101, PLR2004

from pathlib import Path

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


def _banda_puesta() -> bool:
    """True si la entrada por banda de dolares esta activa.

    Apagada desde el 16-oct ("pon la anterior configuracion"): se volvio a la
    entrada por umbral de curva, que es la que si compraba. Los tests de este
    fichero se saltan solos mientras lo este.
    """
    f = _cfg()["filters"]
    return bool(f.get("min_entry_mc", 0)) and bool(f.get("max_entry_mc", 0))


def _mc_sol(avance: float) -> float:
    vt = VT0 - 793.1e6 * avance
    return (K / vt**2) * SUPPLY


def test_la_banda_es_la_pedida() -> None:
    """Si esta puesta, con los valores pedidos el 15-oct."""
    if not _banda_puesta():
        return  # banda apagada desde el 16-oct
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
    if not _banda_puesta():
        return  # banda apagada: nada que vigilar
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
    if not _banda_puesta():
        return  # banda apagada: nada que vigilar
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


def test_el_antirug_acompana_a_lo_temprano_que_se_entre() -> None:
    """El anti-rug se encendio (1.6) al bajar la entrada al 77% de curva:
    quedaban 23 puntos por recorrer con la moneda dentro, y ese es el tramo
    donde un dev puede tirarla. Al volver al 91% el 16-oct, el motivo
    desaparece y vuelve a mandar la orden original de Manu ("no si venden de
    repente"), que era tenerlo apagado.

    Lo que vigila este test no es el valor sino la COHERENCIA: entrar pronto
    con el freno quitado es el caso FUCKETHAN (-91,9%).
    """
    cfg = _cfg()
    umbral = cfg["filters"]["curva_96"]["umbral"]
    anti = cfg["trade"]["anti_rug_ratio"]
    if umbral < 0.85:
        assert anti > 0, (
            f"se entra al {umbral * 100:.0f}% de curva (quedan "
            f"{(1 - umbral) * 100:.0f} puntos con la moneda dentro) y el "
            "anti-rug esta apagado"
        )


def test_min_ops_es_una_llave_y_no_un_filtro() -> None:
    """`min_ops: 1` esta ahi para ABRIR el bloque de la presion, no para filtrar.

    El chequeo de presion vive dentro del bloque del `max_buy_age` y
    `min_presion_ratio` NO abre esa puerta por si solo. Con todos los
    abridores a 0 --como quedo al volver atras el 16-oct-- poner la presion
    no hace nada: queda escrita y muda. Ya paso el 15-oct.

    Se eligio `min_ops` porque es el abridor que MENOS filtra: al 75% de
    curva una moneda lleva decenas de transacciones, asi que exigir 1 no
    descarta nada. Si alguien lo sube pensando que es un filtro de calidad,
    estara descartando monedas con un mando que no se puso para eso --y
    ademas hay abridores mejores para filtrar (titulares, compras).
    """
    filtros = _cfg()["filters"]
    if filtros.get("min_presion_ratio", 0) <= 0:
        return  # presion apagada: la llave no hace falta

    assert filtros.get("min_ops", 0) >= 1, (
        "la presion esta encendida pero no hay ningun abridor: quedaria muda"
    )
    assert filtros["min_ops"] <= 3, (
        f"min_ops esta en {filtros['min_ops']}: eso ya es un filtro, no una "
        "llave. Para filtrar por calidad hay mandos con mas respaldo "
        "(min_titulares: la medicion de 27.241 monedas dice que los "
        "compradores distintos predicen el pump y el ratio de presion no)"
    )


def test_la_presion_es_exigente_pero_el_bucle_la_hace_viable() -> None:
    """ "Potente pero que no descarte demasiado" lo concilia el BUCLE, no el
    numero.

    El bloque reintenta cada `_paso` (0,15 s) durante `max_buy_age` (60 s),
    o sea unas 400 veces, y basta con que la presion se cumpla UNA VEZ. Por
    eso un 1.5 --exigente de verdad-- no se traduce en descartar casi todo.

    Si alguien bajara `max_buy_age`, el mismo 1.5 pasaria a ser muchisimo mas
    estricto sin que nadie hubiera tocado la presion. Este test lo vigila.
    """
    filtros = _cfg()["filters"]
    ratio = filtros.get("min_presion_ratio", 0)
    if ratio <= 0:
        return

    ventana = filtros["max_buy_age"]
    paso = 0.15  # `_paso` en universal_trader.py
    intentos = ventana / paso
    assert intentos >= 100, (
        f"con max_buy_age={ventana}s solo hay {intentos:.0f} oportunidades de "
        f"cumplir la presion de {ratio}: pasaria a descartar casi todo"
    )
    assert filtros["presion_seg"] > 0, "sin ventana, la presion no mide nada"
