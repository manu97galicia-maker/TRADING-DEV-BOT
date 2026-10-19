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

# 22.000-28.000 desde el 19-oct: Manu pidio "vuelve a usar la estrategia de
# comprar a un 89%", y el 89% de curva son 25.075 $ con SOL a 105. La banda lo
# rodea (86%-92%) para que el techo en dolares siga existiendo: un % de curva
# no puede garantizarlo porque el MC se mueve con el precio de SOL.
#
# Antes fue 2.000-5.000, y antes 15.000-22.000, y antes 18.000-23.000.
SUELO_PEDIDO = 22000
TECHO_PEDIDO = 28000

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

    EL 18-oct `min_ops` PASO A SER TAMBIEN UN FILTRO, pedido: "que haya
    muchas operaciones frecuentes". Asi que este guardian ya no vigila que
    sea pequeno; vigila las dos cosas que siguen importando:

      1. que nunca baje de 1, porque entonces la presion se queda muda;
      2. que no se pelee con `min_volumen_sol`, que mide los mismos 300 s.
         Si uno pide 8 SOL y el otro 200 operaciones, el segundo manda y el
         primero es decorativo -- y nadie lo ve en el YAML.
    """
    filtros = _cfg()["filters"]
    if filtros.get("min_presion_ratio", 0) <= 0:
        return  # presion apagada: la llave no hace falta

    # CORREGIDO EL 19-oct: este guardian exigia `min_ops >= 1` como si fuera
    # la UNICA llave, y no lo es. La puerta real (universal_trader.py:4012)
    # se abre con cualquiera de siete mandos. Al apagar min_ops ese dia, el
    # guardian habria obligado a dejarlo encendido sin motivo: `min_entry_mc`
    # ya abre el bloque. Lo que hay que vigilar es que haya ALGUNA llave.
    llaves = {
        "min_volumen_sol": filtros.get("min_volumen_sol", 0),
        "min_entry_mc": filtros.get("min_entry_mc", 0),
        "min_buys": filtros.get("min_buys", 0),
        "min_first_block_buys": filtros.get("min_first_block_buys", 0),
        "min_ops": filtros.get("min_ops", 0),
        "min_titulares": filtros.get("min_titulares", 0),
    }
    assert filtros.get("max_buy_age", 0) > 0, (
        "max_buy_age esta en 0: el bloque entero no corre y la presion es muda"
    )
    assert any(v > 0 for v in llaves.values()), (
        f"la presion esta encendida pero ninguna llave abre el bloque "
        f"({llaves}): quedaria escrita y muda, como paso el 15-oct"
    )

    # COHERENCIA ENTRE LOS DOS MANDOS DE LA MISMA VENTANA. Con una compra
    # media de entre 0,2 y 0,5 SOL, los `min_volumen_sol` SOL equivalen a
    # entre vol/0,5 y vol/0,2 operaciones. Pedir mas que el extremo alto es
    # dejar el filtro de volumen sin efecto.
    vol = float(filtros.get("min_volumen_sol", 0) or 0)
    if vol > 0:
        techo = vol / 0.2
        assert filtros["min_ops"] <= techo, (
            f"min_ops {filtros['min_ops']} pasa de las {techo:.0f} "
            f"operaciones que como mucho representan {vol:g} SOL. El filtro "
            "de volumen se queda decorativo y el YAML no lo dice"
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


def test_el_volumen_no_vuelve_al_20_que_seco_las_compras() -> None:
    """`min_volumen_sol: 20` ya dejo al bot sin comprar el 15-oct.

    EL ERROR QUE LO PUSO, para que no se repita: se razono sobre los ~38 SOL
    que hay DENTRO de la curva cuando la moneda esta en la banda de Manu. Pero
    este filtro no mide el SOL de la curva, mide el que el bot RECUERDA, y
    `volumen.py` solo guarda 300 segundos por moneda (`_VIDA = 300`). Una
    moneda que tardo media hora en subir llega a la banda con casi todo ese
    volumen olvidado, asi que ve mucho menos de 38 y se cae por un filtro
    calibrado contra una cantidad distinta de la que se compara.

    El 18-oct, al pedir Manu "volumen decente comprador", el primer impulso
    fue otra vez 20. Este guardian existe para que ese impulso choque con la
    medicion en vez de con el dinero.
    """
    vol = float(_cfg()["filters"].get("min_volumen_sol", 0) or 0)
    if vol <= 0:
        return  # apagado: no hay nada que vigilar

    assert vol < 20, (
        f"min_volumen_sol esta en {vol:g}. El 20 ya seco las compras el "
        "15-oct porque este mando se compara contra los 300 s de memoria de "
        "volumen.py, no contra los SOL de la curva"
    )

    # Y que siga teniendo sentido contra lo que SI mide: cruzar del 80% de
    # curva hasta la banda son ~0,9 SOL, asi que el filtro tiene que pedir
    # mas que eso (si no, no filtra nada) y bastante menos que los 38 netos
    # que hay dentro (si no, exige una historia que el bot no recuerda).
    assert vol > 0.9, (
        f"min_volumen_sol {vol:g} no llega ni al ~0,9 SOL que cuesta cruzar "
        "a la banda: no esta filtrando nada"
    )


def test_la_aceleracion_pide_el_ritmo_de_la_curva_y_no_mas() -> None:
    """La ventana corta es "operaciones frecuentes", no un segundo filtro duro.

    Pedido el 18-oct junto con las operaciones. El numero sale de la curva:
    cruzar del 80% hasta la banda de 15-22k son ~0,9 SOL, asi que pedir ~1 SOL
    en la ventana es pedir ese mismo ritmo. Si alguien sube esto, deja de ser
    "que se este moviendo" y se convierte en el filtro que manda, encima de
    `min_ops`, que es el que Manu pidio para eso.
    """
    f = _cfg()["filters"]
    acel = float(f.get("min_vol_ventana_sol", 0) or 0)
    if acel <= 0:
        return  # apagada

    seg = float(f.get("vol_ventana_seg", 3.0) or 3.0)
    assert seg >= 5, (
        f"la ventana son {seg:g} s: demasiado corta para medir un ritmo. "
        "Entre dos bloques de Solana puede no haber ninguna operacion y la "
        "moneda buena se descarta por un hueco de medio segundo"
    )

    # El ritmo pedido, en SOL por segundo, no debe pasar del que hace falta
    # para recorrer la banda entera (38 -> 52 SOL dentro) en un minuto.
    ritmo = acel / seg
    assert ritmo <= (52.2 - 37.8) / 60, (
        f"se piden {ritmo:.3f} SOL/s, mas de los {(52.2 - 37.8) / 60:.3f} SOL/s "
        "que cruzan la banda completa en un minuto: eso no es 'que se mueva', "
        "es exigir un pump en marcha"
    )


def test_solo_mayhem_sigue_encendido() -> None:
    """ "Pon solo modo mayhem" (18-oct) no tenia quien lo vigilase.

    Lo descubrio una mutacion: apagar `require_mayhem` dejaba los 872
    guardianes en verde. Un mando que se puede apagar sin que nada chille es
    un mando que algun dia se apaga solo, y este decide QUE MONEDAS se
    compran con dinero real.

    LO QUE ESTE FILTRO NO DA, y queda escrito aqui para que no se confunda:
    el 50% de acierto sobre 30.000 $ de `ANALISIS_MAYHEM_DEV40K.md` NO es de
    las monedas mayhem. Es de 20 creadores seleccionados con 3+ lanzamientos
    mayhem y mediana de pico >=40.000 $, sobre 250 filas. Esto filtra "que
    sea mayhem"; para ese 50% hace falta ademas la lista
    `top_devs_mayhem40k.json`, que esta en el repo y no se ha encendido
    porque no se pidio.
    """
    # APAGADO EL 19-oct ("no mayhem"), un dia despues de encenderlo. Este
    # guardian cambia de lado: ahora vigila que siga apagado, para que no se
    # encienda solo en un cambio futuro. Si Manu lo vuelve a pedir, se
    # cambia aqui y en el YAML, y asi queda el rastro de las dos ordenes.
    filtros = _cfg()["filters"]
    assert filtros.get("require_mayhem") is False, (
        "require_mayhem esta encendido: Manu lo apago el 19-oct ('no "
        "mayhem') y esto filtraria casi todas las monedas sin que se note"
    )
    # `require_mayhem` implica `allow_mayhem` en el codigo
    # (universal_trader.py:828), asi que poner allow a False a mano no
    # apagaria nada -- pero si contradiria lo que dice el YAML.
    assert filtros.get("allow_mayhem", False) is not None


def test_el_rechazo_del_no_mayhem_llega_tarde_y_cuesta_nodo() -> None:
    """Aviso estructural, no un numero: el no-mayhem se tira al comprar.

    `require_mayhem` se comprueba en `platform_aware.py`, en el momento de
    firmar la compra, DESPUES del bucle de espera de `max_buy_age`. Una
    moneda normal que pase volumen, operaciones y presion se queda hasta 60 s
    leyendo la curva --6,7 lecturas por segundo-- y se descarta al final.

    Con `max_rps: 8` el nodo da para 1,2 monedas a la vez en ese bucle, asi
    que esto importa: es la misma saturacion que dio los 429 del 16-oct. El
    freno que lo contiene es `curva_96.tope`, que impide que el detector
    mande a evaluar monedas de toda la curva.

    Si alguien sube el tope sin subir `max_rps`, este test lo dice.
    """
    cfg = _cfg()
    f = cfg["filters"]
    if not f.get("require_mayhem"):
        return

    vigilables = cfg["node"]["max_rps"] * 0.15  # _paso = 0,15 s por lectura
    assert f["curva_96"]["tope"] <= 0.50, (
        f"el tope de curva esta en {f['curva_96']['tope']}: con el umbral en "
        f"{f['curva_96']['umbral']} el detector manda a evaluar casi toda la "
        f"curva, y el nodo solo aguanta {vigilables:.1f} monedas a la vez en "
        "el bucle de espera"
    )
    assert f["curva_96"]["umbral"] < f["curva_96"]["tope"], (
        "el umbral esta por encima del tope: no pasaria ninguna moneda"
    )


def test_la_ventana_de_curva_no_puede_quedar_vacia() -> None:
    """El umbral SIEMPRE por debajo del tope, pase lo que pase.

    `curva96_source.py:492` descarta con `progreso < umbral or progreso >
    tope`. Si el tope queda por debajo del umbral no pasa NINGUNA moneda: el
    bot no compra, no escribe ningun descarte distinto, y desde fuera parece
    que simplemente no hay monedas buenas. Es el fallo mas caro de este repo
    porque es invisible.

    Y casi pasa el 19-oct: el tope estaba en 0,40 (puesto para la banda 2-5k,
    donde protegia el nodo) y el umbral subio a 0,89. Lo encontro una
    mutacion, no una lectura.

    El guardian que ya miraba esto vivia dentro del bloque de
    `require_mayhem` y dejo de correr en cuanto Manu apago el mayhem. Por eso
    este no depende de ninguna condicion.
    """
    cur = _cfg()["filters"]["curva_96"]
    umbral, tope = float(cur["umbral"]), float(cur["tope"])
    assert umbral < tope, (
        f"el umbral ({umbral}) esta por encima o igual que el tope ({tope}): "
        "no pasa ninguna moneda y el bot se queda mudo sin decir por que"
    )
    # Y que la ventana no sea tan estrecha que no quepa nada util: menos de
    # un punto de curva entre los dos es practicamente lo mismo que cero.
    assert tope - umbral >= 0.01, (
        f"la ventana de curva es de {(tope - umbral) * 100:.2f} puntos: "
        "demasiado estrecha para que entre nada"
    )


def test_la_banda_en_dolares_cae_dentro_de_la_ventana_de_curva() -> None:
    """Los dos mandos tienen que hablar del mismo sitio.

    La ventana de curva decide QUE monedas se miran; la banda en dolares
    decide cuales se compran. Si no se solapan, el bot mira unas y compra
    otras: o sea, no compra.

    Es el fallo del 18-oct al reves. Aquel dia la banda bajo a 2-5k y el
    umbral se quedo en 0,754 (14.980 $): el detector no veia nada por debajo
    del triple del techo. Hoy el riesgo es el simetrico.
    """
    f = _cfg()["filters"]
    cur = f["curva_96"]
    suelo_usd, techo_usd = f.get("min_entry_mc", 0), f.get("max_entry_mc", 0)
    if not techo_usd:
        return  # sin techo en dolares no hay nada que cuadrar

    # La ventana de curva, traducida a dolares con el SOL calibrado.
    for sol in SOL_CALIBRADO:
        ventana_min = _mc_sol(float(cur["umbral"])) * sol
        ventana_max = _mc_sol(float(cur["tope"])) * sol
        assert ventana_min <= techo_usd, (
            f"con SOL a ${sol:.0f} el detector empieza a mirar en "
            f"${ventana_min:,.0f}, por encima del techo de ${techo_usd:,.0f}: "
            "mira monedas que nunca podra comprar"
        )
        assert ventana_max >= suelo_usd, (
            f"con SOL a ${sol:.0f} el detector deja de mirar en "
            f"${ventana_max:,.0f}, por debajo del suelo de ${suelo_usd:,.0f}"
        )
