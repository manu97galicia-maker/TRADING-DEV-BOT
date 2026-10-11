"""El sitio por donde se entra tiene que dar para cobrar el primer peldano.

EL DESCUBRIMIENTO (23-sep), al bajar el umbral de entrada a peticion ("entra
un 1,6% antes"). Son dos ajustes que siempre se habian tocado por separado y
resulta que tienen que cuadrar entre si:

* `filters.curva_96.umbral` dice por donde se ENTRA.
* `trade.take_profit_percentage` dice cuanto tiene que subir para cobrar el
  primer peldano.

Con el umbral al 96% quedaba un +24% de recorrido hasta migrar y el TP1 estaba
en +30%. O sea que la escalera NO PODIA cobrar ni un peldano mientras la moneda
siguiera en la curva: hacia falta que migrara Y siguiera subiendo ya en
PumpSwap, que es otro mercado y otra liquidez. Nada fallaba, no habia ningun
error en el log: simplemente los dos numeros no encajaban.

LA CUENTA. La curva de pump.fun es un producto constante entre reservas
virtuales, asi que el precio va como 1/(tokens_virtuales)^2 y sube cada vez mas
rapido segun se llena. Por eso entrar "1,6 puntos antes" no es entrar un 1,6%
mas barato: es entrar un 7,7% mas abajo y con un 43% mas de recorrido.

Este test no exige un umbral concreto --ese es una decision, y se cambia-- sino
que los dos numeros sigan cuadrando. Si alguien sube el umbral o el TP1 hasta
que dejen de hacerlo, lo dice aqui en vez de descubrirse en las operaciones.

EL 11-oct SE PIDIO EXACTAMENTE LO QUE ESTE FICHERO VIGILABA: "deja solo la
regla de comprar al 95% de la bonding curve". Al 95% quedan +30,3% de
recorrido y el TP1 pide +35%, asi que NINGUN peldano de la escalera cobra
mientras la moneda siga en la curva. Es la misma situacion del 23-sep que
creo este fichero, pero ahora es una decision del dueno del bot, no un
descuido.

ASI QUE EL CONTRATO CAMBIA DE FORMA, NO SE QUITA. Un test no puede vetar una
decision del dueno; lo que SI puede es no dejar que la consecuencia pase
desapercibida. Cuando los peldanos no caben en la curva, lo que tiene que
seguir siendo cierto es que la posicion se pueda vender DESPUES de migrar
--en PumpSwap o Raydium-- porque si no, la escalera no cobra en ningun sitio
y la unica salida seria el suelo. Eso es lo que se comprueba ahora, y es una
propiedad del CODIGO, que no se puede decidir desde el yaml.
"""

# ruff: noqa: S101, PLR2004

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"

#: Reservas con las que nace una curva de pump.fun.
V_TOKENS = 1_073_000_000.0
V_SOL = 30.0
#: Tokens que la curva pone a la venta. Cuando se agotan, la moneda migra.
TOKENS_A_LA_VENTA = 793_100_000.0
_K = V_TOKENS * V_SOL


def _cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def precio(avance: float) -> float:
    """Precio relativo con la curva llena al `avance` (0 a 1).

    `avance` es lo mismo que mide el bot en `volumen.progreso_curva`: cuanto
    del tramo a la venta se ha vendido ya.
    """
    tokens_virtuales = V_TOKENS - TOKENS_A_LA_VENTA * avance
    return _K / tokens_virtuales**2


def recorrido_hasta_migrar(avance: float) -> float:
    """Cuanto sube el precio desde ese punto hasta que la moneda migra."""
    return precio(1.0) / precio(avance) - 1.0


def test_la_cuenta_de_la_curva_da_los_85_sol_conocidos() -> None:
    """PRIMERO, QUE EL MODELO SEA EL DE VERDAD.

    Un test construido sobre una formula equivocada da veredictos con total
    seguridad y manda a cambiar cosas que estaban bien. La comprobacion
    barata: una curva de pump.fun migra con unos 85 SOL dentro, y eso es un
    hecho publico que no depende de esta configuracion.
    """
    sol_dentro = _K / (V_TOKENS - TOKENS_A_LA_VENTA) - V_SOL
    assert 84.0 < sol_dentro < 86.0, f"la formula da {sol_dentro:.1f} SOL"


def test_la_nota_dice_el_recorrido_de_verdad_que_queda_desde_la_entrada() -> None:
    """LA CUENTA TIENE QUE ESTAR EN LA NOTA, no solo en este test.

    Quien lee `COMO_FUNCIONA.md` para saber que hace el bot con su dinero
    tiene que encontrar ahi cuanto puede subir una moneda comprada en el
    punto de entrada de HOY. Si alguien mueve el umbral y no toca la nota,
    esto sale en rojo y senala la nota.

    POR QUE ASI Y NO CON UN VETO. La version anterior de este fichero exigia
    que el primer peldano cupiera en la curva, y el dueno del bot cruzo ese
    veto el 11-oct a proposito ("deja solo la regla de comprar al 95%"). Un
    test no puede vetar esa decision; lo que si puede es que la consecuencia
    quede escrita donde se lee, en vez de descubrirse operando.
    """
    c = _cfg()
    umbral = c["filters"]["curva_96"]["umbral"]
    queda = recorrido_hasta_migrar(umbral)
    cifra = f"+{queda * 100:.1f}%".replace(".", ",")
    nota = (Path(__file__).resolve().parent.parent / "COMO_FUNCIONA.md").read_text(
        encoding="utf-8"
    )
    assert cifra in nota, (
        f"entrando al {umbral:.1%} quedan {cifra} de subida hasta migrar, y la "
        f"nota no lo dice en ningun sitio. Si se ha movido el umbral, hay que "
        f"actualizar COMO_FUNCIONA.md con la cifra nueva."
    )


def test_la_nota_no_promete_peldanos_que_no_caben_en_la_curva() -> None:
    """Y AL REVES: que no diga que cobra en la curva algo que necesita migrar.

    Es el fallo de familia de esta nota (tres veces ya): decir una cosa cierta
    y dejar creyendo otra. Aqui lo caro seria leer la escalera de seis
    peldanos y pensar que se cobran sin que la moneda migre.
    """
    c = _cfg()
    queda = recorrido_hasta_migrar(c["filters"]["curva_96"]["umbral"])
    peldanos = _peldanos(c["trade"])
    nota = (Path(__file__).resolve().parent.parent / "COMO_FUNCIONA.md").read_text(
        encoding="utf-8"
    )
    if queda < peldanos[0]:
        assert "sólo puede cobrar después de que la moneda migre" in nota, (
            f"ningun peldano cabe en la curva (quedan +{queda:.1%} y el "
            f"primero pide +{peldanos[0]:.0%}), y la nota no avisa de que "
            "toda la escalera depende de migrar"
        )


def test_si_no_cabe_ninguno_la_posicion_se_puede_vender_tras_migrar() -> None:
    """EL FRENO DE VERDAD cuando se entra tan arriba que no cabe un peldano.

    Si la escalera solo puede cobrar despues de migrar, entonces la salida
    post-migracion es la UNICA via de beneficio que queda. Que exista no es
    una opcion de configuracion: es codigo, y por eso se comprueba leyendo
    el trader en vez del yaml.

    ES EL FALLO DE FUCKETHAN AL REVES. Alli los frenos anti-rug vivian dentro
    de una fuente, se apago la fuente y se apagaron con ella sin que nada lo
    dijera (-91,9%). Aqui la via de salida vive en el trader; si alguien la
    quita mientras la entrada siga al 95%, esto sale en rojo y nombra el
    motivo, en vez de descubrirse con una posicion que no se puede cerrar.
    """
    c = _cfg()
    umbral = c["filters"]["curva_96"]["umbral"]
    queda = recorrido_hasta_migrar(umbral)
    if queda > _peldanos(c["trade"])[0]:
        return  # cabe al menos un peldano en la curva: no aplica

    trader = (
        Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"
    ).read_text(encoding="utf-8")
    assert "the token migrated" in trader and "PumpSwap" in trader, (
        f"entrando al {umbral:.1%} ningun peldano cabe en la curva, asi que "
        "la venta tras migrar es la unica via de beneficio -- y ha "
        "desaparecido del trader. Sin ella estas posiciones solo pueden "
        "cerrarse por el suelo."
    )


def test_el_umbral_no_tiene_un_cero_de_mas() -> None:
    """LO QUE QUEDA DEL SUELO VIEJO, que cruzo cinco veces y ya no veta.

    ESTE SUELO HA SIDO CUATRO NUMEROS Y NINGUNO SALIO DE UNA MEDICION. Empezo
    en 0,90, bajo a 0,85 el 24-sep, se cruzo esa misma noche al pedirse el
    84%, otra vez el 26-sep al pedirse el 59%, y la comprobacion que lo
    sustituyo --que el peldano mas alto cupiera en la curva-- se cruzo
    tambien el 11-oct al pedirse el 95%. Repetir un veto que el dueno cruza
    cada vez que decide algo no informa de nada, asi que no queda veto.

    QUEDA SOLO EL LIMITE ABSOLUTO (0,30), que NO es un veto: caza un cero de
    mas al teclear (0,059 en vez de 0,59), que es un fallo que nadie
    detectaria leyendo y que compraria monedas recien nacidas.
    """
    c = _cfg()["filters"]["curva_96"]
    assert c["umbral"] < c["tope"] <= 1.0
    assert 0.30 <= c["umbral"] <= 1.0, (
        f"umbral {c['umbral']}: parece un cero de mas al teclear"
    )


def _peldanos(t: dict) -> list[float]:
    """Las subidas de la escalera, en orden, hasta donde lee el codigo.

    SOLO HASTA TP6: `bot_runner.py` no pasa el tp7, asi que un `tp7_gain` en
    el yaml esta escrito y no vende nada. Contarlo aqui como peldano haria
    creer que hay una salida mas de las que hay.
    """
    ganancias = [float(t["take_profit_percentage"])]
    for i in range(2, 7):
        if f"tp{i}_gain" not in t:
            break
        ganancias.append(float(t[f"tp{i}_gain"]))
    return sorted(ganancias)
