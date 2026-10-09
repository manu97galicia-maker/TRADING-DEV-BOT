"""La escalera de ventas configurada, fijada para que no cambie por descuido.

Los numeros van a pelo a proposito: quien los toque ve el test en rojo y
tiene que mirar que ganancia gana o pierde antes de seguir.
"""

# ruff: noqa: S101, PLR2004

from itertools import pairwise
from pathlib import Path

import yaml
from modelo_tamano import SOL_USD_REF

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"


def _trade() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["trade"]


def _peldanos() -> list[tuple[float, float]]:
    t = _trade()
    pares = [
        (t["take_profit_percentage"], t["take_profit_sell_percentage"]),
        # CON `.get` EN TODOS, no con corchetes. Cada vez que un peldano se
        # quita del yaml, leerlo con corchetes revienta con KeyError -- un
        # rojo que solo dice "falta una clave", no "la escalera ha cambiado".
        # Paso con tp4/tp5/tp6 el 24-sep por la manana y con tp2/tp3 esa
        # misma noche, al quedarse la configuracion en UN SOLO take profit.
        (t.get("tp2_gain"), t.get("tp2_sell")),
        (t.get("tp3_gain"), t.get("tp3_sell")),
        (t.get("tp4_gain"), t.get("tp4_sell")),
        (t.get("tp5_gain"), t.get("tp5_sell")),
        (t.get("tp6_gain"), t.get("tp6_sell")),
        *((e["gain"], e["sell"]) for e in t.get("tp_extra") or []),
    ]
    # SE ACEPTA `v == 0`, igual que `build_tiers`. Este helper filtraba
    # `v > 0` y con el TP1 a 0% se dejaba fuera el primer escalon, asi que los
    # tests miraban una escalera distinta de la que corre. Un escalon que no
    # vende SI existe: dispara la subida del stop.
    return [
        (g, v) for g, v in pares if g is not None and v is not None and g > 0 and v >= 0
    ]


def test_los_peldanos_son_los_pedidos() -> None:
    """LA ESCALERA ENTERA, fijada para que cualquier cambio sea deliberado.

    05-oct: SEIS peldanos de porcentaje y NINGUNA cola de market cap.
    Viene del commit "Restrict paper strategy to Telegram alpha channels",
    que reescribio la escalera entera. Es mas nuevo que la version del
    26-sep (enfocada en el x3, con cola hasta 80k), asi que manda este.

    EL x3 SOLO CABE PORQUE LA ENTRADA BAJO AL 59%. Entrando al 71% caia al
    98,2% de curva --practicamente la migracion-- y habria sido un peldano
    que casi nunca cobra; al 59% cae al 91,2%. Los dos cambios del 26-sep
    (bajar la curva y enfocar el take profit) son el mismo cambio. Lo
    comprueba `test_los_peldanos_caben_dentro_de_la_curva`.

    EL PESO SE MUEVE HACIA ARRIBA: el +25% pasa de vender el 20% al 10%, el
    +60% del 40% al 20%, el +100% del 25% al 20%, y los 35 puntos liberados
    van al x3. El +25% se queda casi sin cobrar a proposito: su trabajo es
    apagar el stop, no recoger dinero.
    """
    assert _peldanos() == [
        (0.35, 0.25),
        (0.65, 0.40),
        (1.00, 0.15),
        (1.50, 0.10),
        (2.00, 0.05),
        (4.00, 0.03),
    ]


def _mc_de_compra() -> float:
    """El market cap al que se compra, con la curva del yaml y SOL a 220 $.

    La curva de pump.fun es un producto constante: con `avance` vendido, los
    tokens virtuales valen 1073e6 - 793,1e6 x avance, y el precio es
    k/vt^2 con k = 30 x 1073e6. El market cap es precio x supply x precio
    del SOL.

    EL PRECIO DEL SOL ES FIJO AQUI (220 $) y el bot usa el de verdad, que
    refresca desde DexScreener. Un test que saliera a internet daria
    distinto cada dia. Lo que se comprueba es que los peldanos APUNTAN a
    donde se penso, no el dolar exacto.
    """
    avance = yaml.safe_load(CONFIG.read_text())["filters"]["curva_96"]["umbral"]
    vt = 1073e6 - 793.1e6 * avance
    precio = (30 * 1073e6 / vt) / vt
    return precio * 1e9 * SOL_USD_REF


def test_no_quedan_peldanos_lejanos() -> None:
    """La escalera llega hasta el tercer peldano y ahi se acaba.

    Historial: siete peldanos con market cap hasta 400k (24-sep tarde); UN
    SOLO take profit al +55,13% (esa noche, para pagar menos comisiones); dos
    peldanos (+30%/+60%); y desde el 25-sep tres, con cola de market cap.

    EL TP3 Y EL TP4 VOLVIERON A PROPOSITO y por eso ya no se prohiben aqui.
    Las dos cosas que su vuelta obligaba a comprobar estan comprobadas en
    este mismo fichero, que es lo que pedia el texto anterior:
      - que caben en la curva -> test_los_peldanos_caben_dentro_de_la_curva
      - que el suelo no los alcanza -> test_el_suelo_nunca_alcanza_al_peldano...

    LO QUE DECIA ANTES DEL CUARTO, y por que ya no vale: "con cuatro peldanos
    la cola de market cap se vuelve inalcanzable". Eso era cierto pensando en
    OCHO peldanos, no en cuatro. La cola se arma cuando se han cobrado todos
    los de porcentaje, o sea al llegar al x3 -- mas tarde que antes, pero
    llegar al x3 es justo lo que esta escalera busca.

    EL 05-oct VOLVIERON EL QUINTO Y EL SEXTO (+200% y +400%), y con ellos se
    quito la cola de market cap. Las dos cosas van juntas: la cola solo se
    arma cuando se han cobrado TODOS los peldanos de porcentaje, asi que con
    seis arriba habria quedado puesta sin llegar a armarse casi nunca. Al
    vaciarla, esa contradiccion desaparece.

    LO QUE SIGUE PROHIBIDO es `tp_extra`: esos peldanos no tienen test que
    compruebe donde caen.
    """
    assert _trade().get("tp_extra") in (None, []), (
        "han vuelto los peldanos lejanos: hay que recuperar la comprobacion "
        "de que caen en las cifras de market cap pedidas"
    )
    # Y si vuelve la cola de market cap con seis peldanos arriba, se avisa:
    # las dos cosas a la vez es tener una via puesta que no se arma.
    if _trade().get("mc_take_profit_tiers"):
        assert len(_peldanos()) <= 4, (
            "con mas de cuatro peldanos de porcentaje la cola de market cap "
            "casi nunca llega a armarse: o se quitan peldanos, o se vacia"
        )


def test_el_suelo_nunca_alcanza_al_peldano_que_acaba_de_cobrarse() -> None:
    """EL FALLO DE AGOSTO, que ahora vigila su propio test.

    Si dos peldanos quedan a menos de un `margen_stop_tras_tp` uno de otro,
    el suelo que sube tras cobrar el primero cae ENCIMA del segundo y la
    posicion se cierra sola en el instante de tocar su objetivo. Paso con el
    margen al 12% y obligo a subir el TP2 de +40% a +50%.

    Estaba metido dentro del test de la cadena geometrica --que daba un 35%
    de separacion de regalo-- y al cambiar los lejanos a cifras de market cap
    esa garantia desaparecio. Por eso se saca aparte: es una propiedad que
    tiene que cumplirse siempre, venga la escalera de donde venga.
    """
    margen = float(_trade()["margen_stop_tras_tp"])
    peldanos = _peldanos()
    for (g_antes, _), (g_ahora, _) in pairwise(peldanos):
        suelo = (1 + g_antes) * (1 + margen)
        assert suelo < 1 + g_ahora, (
            f"tras cobrar el peldano de x{1 + g_ahora:.3f} el suelo queda en "
            f"x{suelo:.3f}, por encima: la posicion se cerraria sola"
        )


def test_los_escalones_de_market_cap_son_los_pedidos() -> None:
    """LA COLA DE LA ESCALERA (25-sep): "el restante lo repartes hasta 80k".

    Cuatro escalones que reparten el 15% que sobra de los tres peldanos de
    porcentaje, de mas a menos: cuanto mas arriba, menos monedas llegan.

    ESTE TEST SUSTITUYE A `test_no_quedan_escalones_de_market_cap`, que
    exigia la lista vacia. No se ha relajado nada: la via vuelve a estar
    encendida a peticion y ahora se fija lo que tiene dentro.

    SE ARMAN SOLO TRAS LOS TRES PELDANOS DE PORCENTAJE (en el codigo:
    `if tiers and all(executed) and mc_tiers`). O sea que una moneda a 80k de
    market cap que solo haya subido un 80% desde la compra no vende nada por
    aqui. Son una COLA, no una via paralela.
    """
    # VACIA DESDE EL 05-oct. La lista se deja puesta en vez de borrar la
    # clave para que se vea que la via existe y esta apagada.
    assert _trade()["mc_take_profit_tiers"] == []


def test_los_escalones_de_market_cap_van_de_mas_a_menos() -> None:
    """De mas a menos porque cuanto mas arriba, menos monedas llegan.

    El trozo gordo se cobra pronto. Un reparto creciente estaria apostando a
    que la mayoria llega al ultimo escalon, que es justo lo contrario de lo
    que pasa.
    """
    mc = _trade()["mc_take_profit_tiers"]
    objetivos = [d["target_mc"] for d in mc]
    ventas = [d["sell"] for d in mc]
    assert objetivos == sorted(objetivos), f"tienen que ir subiendo: {objetivos}"
    assert len(set(objetivos)) == len(objetivos), f"hay dos iguales: {objetivos}"
    for antes, despues in pairwise(ventas):
        assert despues <= antes, f"de mas a menos: {ventas}"


def test_lo_que_sobra_de_los_peldanos_es_el_moonbag() -> None:
    """Los seis peldanos venden el 98%; el 2% que queda se deja correr.

    ESTE TEST SE LLAMABA `test_la_cola_de_market_cap_recoge_todo_lo_que_sobra`
    y exigia que la cola repartiera EXACTAMENTE el resto. Valia mientras habia
    cola; el 05-oct se vacio y el resto pasa a ser moonbag otra vez.

    LO QUE ARRASTRA UN MOONBAG, y por eso importa que este escrito: mientras
    exista, la cuenta de token sigue abierta con su renta (~0,00204 SOL)
    dentro, y el bot sigue vigilando una posicion casi vacia. Con el 2% es
    poco, pero no es cero.
    """
    t = _trade()
    porcentaje = sum(v for _, v in _peldanos())
    cola = sum(d["sell"] for d in t.get("mc_take_profit_tiers") or [])
    assert cola == 0, "si vuelve la cola, este test tiene que volver al reparto"
    sobra = round(1.0 - porcentaje, 6)
    assert sobra == 0.02, (
        f"los peldanos venden el {porcentaje:.0%} y sobra el {sobra:.0%}: "
        f"el moonbag acordado el 05-oct es del 2%"
    )


def test_las_ganancias_van_en_orden_creciente() -> None:
    """Un peldano por debajo del anterior no llegaria a dispararse nunca."""
    ganancias = [g for g, _ in _peldanos()]
    assert ganancias == sorted(ganancias)


def test_nunca_se_vende_mas_del_100() -> None:
    """Vender mas de lo que se tiene es una orden imposible."""
    assert sum(v for _, v in _peldanos()) <= 1.0


def test_el_moonbag_perpetuo_es_del_dos_por_ciento() -> None:
    """VUELVE EL MOONBAG (05-oct), y este test ha ido y venido con el.

    Historial: `test_ya_no_queda_moonbag` (take profit unico al 100%) ->
    `test_el_moonbag_es_lo_que_sobra` (10%, escalera de dos peldanos) ->
    `test_ya_no_queda_moonbag_perpetuo` (la cola de market cap recogia el
    15%) -> este, con el 2% que dejan los seis peldanos del 05-oct.

    No se afloja nada: cada version fija lo que de verdad corre, y lo que no
    puede pasar nunca es que sobre un trozo SIN que nadie sepa que sobra.
    """
    t = _trade()
    porcentaje = sum(v for _, v in _peldanos())
    cola = sum(d["sell"] for d in t.get("mc_take_profit_tiers") or [])
    assert round(porcentaje + cola, 6) == 0.98, (
        f"peldanos {porcentaje:.0%} + cola {cola:.0%}: el 05-oct se acordo "
        f"vender el 98% y dejar el 2% corriendo"
    )


def test_el_stop_sube_a_la_entrada_tras_el_primer_tp() -> None:
    """Es lo que hace que despues del primer TP ya no se pueda perder."""
    assert _trade()["stop_a_entrada_tras_tp"] is True


def test_no_se_cierra_ninguna_posicion_por_reloj() -> None:
    """0 = sin reloj. Con 7200 los peldanos de x10 a x20 no se disparaban
    nunca: el reloj vendia antes de que la moneda llegara."""
    assert _trade()["max_hold_time"] == 0


def test_el_abandono_no_depende_del_reloj_de_posicion() -> None:
    """La trampa que casi cuela: `abandon_after` salia de
    `(max_hold_time or 120) * 3 + 120`, asi que poner el reloj a 0 para
    dejar correr las posiciones hacia que el bot ABANDONASE cada una a los
    8 minutos, dejando la bolsa huerfana en la wallet.
    """
    t = _trade()
    assert t["abandonar_tras_seg"] >= 3600
    # Y con la formula vieja habria dado esto:
    assert (t["max_hold_time"] or 120) * 3 + 120 < t["abandonar_tras_seg"]


def test_el_primer_peldano_apaga_el_stop_antes_que_el_stop_muerda() -> None:
    """LO QUE VUELVE CON LA ESCALERA CORTA, y es su razon de ser.

    El primer peldano (+30%) no esta solo para cobrar: al tocarlo el suelo
    sube a compra + margen y la operacion deja de poder acabar en perdida.
    Con el take profit unico eso no existia y una moneda que subia un 45% y
    se daba la vuelta costaba el stop ENTERO.

    LO QUE SE COMPRUEBA: que el primer peldano quede por debajo del segundo
    (o no apagaria nada antes), y que vender en el no deje la operacion en
    rojo si la moneda se da la vuelta justo despues.
    """
    t = _trade()
    peldanos = _peldanos()
    assert len(peldanos) >= 2, f"esta escalera necesita al menos dos: {peldanos}"
    (g1, v1), (g2, _) = peldanos[0], peldanos[1]
    assert g1 < g2
    margen = float(t["margen_stop_tras_tp"])
    # Cobrado el primero, el resto sale en compra + margen si se cae.
    resultado = v1 * (1 + g1) + (1 - v1) * (1 + margen) - 1
    assert resultado > 0, (
        f"tocar el primer peldano y darse la vuelta deja {resultado * 100:+.1f}%: "
        f"el peldano no esta cumpliendo su trabajo de apagar el stop"
    )


def test_el_primer_peldano_cabe_desde_la_entrada_mas_alta_posible() -> None:
    """Que al menos el PRIMER peldano se pueda cobrar sin migrar.

    POR QUE CAMBIA LA PREGUNTA (09-oct). Antes la entrada era fija --el
    umbral de la curva-- y se comprobaba que el peldano MAS ALTO cupiera.
    Con la fuente de presion encendida no hay entrada fija: compra la moneda
    donde este, hasta el `max_avance_curva`. Asi que "cabe la escalera" deja
    de tener una respuesta sola, y pedir que quepa el +400% desde cualquier
    punto seria imposible de cumplir.

    DONDE CAE CADA PELDANO SEGUN DONDE SE ENTRE, calculado con la curva:

        entra al   +35%    +65%   +100%   +150%   +200%   +400%
           20%    36,1%   45,5%   53,8%   62,4%   68,7%   83,7%
           40%    53,3%   61,1%   67,9%   75,0%   80,3%   92,7%
           60%    70,5%   76,7%   82,1%   87,7%   91,8%   MIGRA
           80%    87,7%   92,2%   96,2%   MIGRA   MIGRA   MIGRA
           95%    MIGRA   MIGRA   MIGRA   MIGRA   MIGRA   MIGRA

    O sea: los peldanos altos SOLO pagan en monedas compradas pronto en su
    curva. Eso no es un fallo --es lo que hay-- pero significa que en una
    moneda comprada arriba la escalera efectiva son uno o dos peldanos y
    luego el suelo.

    LO QUE SI SE PUEDE EXIGIR, y es lo que fija este test: que desde la
    entrada mas alta que la configuracion permite, el PRIMER peldano siga
    cobrandose dentro de la curva. Si ni ese cabe, la posicion no tiene
    ninguna salida por arriba y solo puede cerrarse por el suelo.
    """
    f = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]
    if not (f.get("presion") or {}).get("enabled"):
        return  # este test describe la fuente de presion

    def recorrido(avance: float) -> float:
        """Cuanto puede subir una moneda desde `avance` hasta migrar."""
        vt = 1073e6 - 793.1e6 * avance
        return (vt / (1073e6 - 793.1e6)) ** 2 - 1

    tope = float(f["presion"]["max_avance_curva"])
    primero = min(g for g, _ in _peldanos())

    # El punto de curva donde el primer peldano deja de caber, por biseccion.
    bajo, alto = 0.0, 0.999
    for _ in range(60):
        medio = (bajo + alto) / 2
        if recorrido(medio) > primero:
            bajo = medio
        else:
            alto = medio
    corte = bajo

    # HOY: corte 94,3% y tope 95% -> una franja de 0,7 puntos donde ningun
    # peldano paga antes de migrar. Es estrecha y se acepta; lo que no puede
    # pasar es que crezca sin que nadie lo note.
    franja = max(0.0, tope - corte)
    assert franja < 0.05, (
        f"el primer peldano (+{primero * 100:.0f}%) deja de caber a partir "
        f"del {corte:.1%} de curva, y se compra hasta el {tope:.0%}: hay "
        f"{franja * 100:.1f} puntos de entradas sin ninguna salida por "
        f"arriba antes de migrar. O baja max_avance_curva, o baja el primer "
        f"peldano"
    )


def test_todos_los_peldanos_son_fraccion_de_la_compra_original() -> None:
    """LA CONFUSION QUE YA ESTUVO A PUNTO DE COSTAR DINERO.

    El bot NO sabe vender fracciones de lo que queda dentro: todos sus
    peldanos son fraccion de la COMPRA ORIGINAL. Cuando el 24-sep se pidio
    "al +85% vende el 60% de lo que queda", eso se tradujo a 0,33 -- poner
    0,60 habria vendido el 60% de la compra entera, casi el doble.

    Ahora los peldanos se piden ya como fraccion del original, asi que no hay
    traduccion que hacer. Lo que este test impide es que vuelva a colarse una:
    si la suma de las ventas pasara de 1, alguien habria escrito fracciones
    del resto creyendo que eran del original.
    """
    suma = sum(v for _, v in _peldanos())
    assert suma <= 1.0, (
        f"los peldanos suman {suma * 100:.0f}% de la compra: alguien ha "
        "escrito fracciones de lo que QUEDA como si fueran del original"
    )
    # Y con los escalones de market cap tampoco puede pasar de 1.
    t = _trade()
    suma += sum(d["sell"] for d in t.get("mc_take_profit_tiers") or [])
    assert suma <= 1.0


def test_no_hay_peldanos_escritos_que_el_codigo_no_lea() -> None:
    """UN PELDANO EN EL YAML QUE NO SE EJECUTA, y ahora mismo hay uno.

    `bot_runner.py` pasa al trader `tp2_gain`..`tp6_gain` y para ahi. Un
    `tp7_gain` en el yaml NO llega al codigo: se queda escrito, suma en la
    cabeza de quien lee el fichero, y no vende nada.

    LO QUE CUESTA HOY. El commit del 05-oct escribio siete peldanos cuyas
    ventas suman el 100%, o sea "se vende todo". Pero el septimo (+1000%,
    vende 2%) es inerte, asi que lo que de verdad corre son SEIS peldanos
    que venden el 98%, y el 2% restante se queda como moonbag permanente
    arrastrando la renta de la cuenta de token.

    Es la misma familia de fallo que este repo lleva semanas documentando:
    un numero que parece decidir y no decide. No se borra sin pedirlo --
    cambiar la escalera es decision de Manu-- pero deja de ser invisible.

    PARA ARREGLARLO hay dos caminos: borrar `tp7_*` (y asumir el moonbag del
    2%), o moverlo a `tp_extra`, que es la via que SI escala y que existe
    justo para no ir anadiendo tp8, tp9...
    """
    t = _trade()
    LEIDOS = 6  # noqa: N806 -- hasta tp6_gain, ver bot_runner.py
    inertes = [
        f"tp{i}_gain" for i in range(LEIDOS + 1, 12) if f"tp{i}_gain" in t
    ]
    vivos = sum(v for _, v in _peldanos())
    muertos = sum(
        t.get(f"tp{i}_sell") or 0 for i in range(LEIDOS + 1, 12) if f"tp{i}_gain" in t
    )
    assert inertes == ["tp7_gain"], (
        f"ha cambiado la lista de peldanos inertes: {inertes}. Si se ha "
        f"borrado tp7, este test tiene que pasar a exigir la lista vacia; si "
        f"se ha anadido otro, hay que decirlo aqui"
    )
    # Y que quede clavado lo que eso significa de verdad.
    assert round(vivos, 6) == 0.98, f"lo que SE VENDE son seis peldanos: {vivos}"
    assert round(muertos, 6) == 0.02, (
        f"lo que esta escrito y no se ejecuta: {muertos}"
    )
