"""El comando que dice de cuanto esta comprando el bot de verdad.

POR QUE SE TESTEA. Es una herramienta de DIAGNOSTICO y su trabajo es
contestar "¿me esta comprando mas del 25%?". Si se equivoca, manda a cambiar
la configuracion en la direccion contraria -- o peor, dice que todo cuadra
cuando no cuadra. Ya paso dos veces con los contadores de `porque` y `bot`,
que buscaban un texto del log que ya no se escribia y daban cero pasara lo
que pasara.

LO QUE VIGILA: que reconozca las TRES formas en que el bot ha escrito la
cuenta del tamano (han cambiado tres veces en dos dias), que empareje cada
compra con la suya y no con la de otra, que marque las que se pasan, y que NO
invente un porcentaje donde no significa nada.
"""

# ruff: noqa: S101, PLR2004, SLF001

import importlib.util
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GUION = RAIZ / "deploy" / "cuanto_compra.py"


def _mod():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("cc", GUION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_empareja_la_compra_con_su_cuenta() -> None:
    """EL NUCLEO. "Compro 0,25 SOL" no dice nada solo: el 25% de 1 SOL son
    0,25 y el de 2 SOL son 0,5. Sin la base, cualquier cifra parece correcta
    o parece un disparate segun el humor del que mira."""
    filas = _mod()._compras([
        "CARTERA: 1.0000 SOL (0.7000 en wallet + 0.3000 en monedas) · libres 0.67",
        "Buying 0.250000 SOL worth of ALFA on pumpfun...",
    ])
    assert len(filas) == 1
    sym, importe, modo, pct = filas[0]
    assert sym == "ALFA"
    assert abs(importe - 0.25) < 1e-9
    assert modo == "cartera"
    assert abs(pct - 25.0) < 0.01


def test_no_arrastra_la_base_de_una_compra_a_la_siguiente() -> None:
    """LA FORMA SILENCIOSA DE MENTIR. Si la base no se consume al usarla, una
    compra sin su linea de cuenta heredaria la de la anterior y saldria un
    porcentaje inventado con toda naturalidad."""
    filas = _mod()._compras([
        "CARTERA: 1.0000 SOL (0.7000 en wallet + 0.3000 en monedas) · libres 0.67",
        "Buying 0.250000 SOL worth of ALFA on pumpfun...",
        "Buying 0.900000 SOL worth of HUERFANA on pumpfun...",
    ])
    assert len(filas) == 2
    assert filas[1][2] == "?", "sin su cuenta, la base tiene que quedar en '?'"
    assert filas[1][3] == 0.0, "y no se inventa ningun porcentaje"


def test_reconoce_las_tres_formas_de_escribir_la_cuenta() -> None:
    """Un log de hace horas puede llevar cualquiera de las tres: el tamano ha
    cambiado de modo tres veces en dos dias. Reconocer solo la de hoy daria
    "ninguna compra" justo cuando se esta comparando el antes con el despues.
    """
    filas = _mod()._compras([
        "CARTERA: 1.0000 SOL (0.7000 en wallet + 0.3000 en monedas) · libres 0.67",
        "Buying 0.250000 SOL worth of NUEVA on pumpfun...",
        "IMPORTE: 10.00 $ a 220.00 $/SOL -> compra de 0.0455 SOL · libres 0.30",
        "Buying 0.045500 SOL worth of DOLARES on pumpfun...",
        "SALDO: 0.9700 SOL libres -> compra de 0.2910 (30%)",
        "Buying 0.291000 SOL worth of VIEJA on pumpfun...",
    ])
    assert [f[2] for f in filas] == ["cartera", "dolares", "saldo libre"]


def test_no_inventa_porcentaje_donde_no_significa_nada() -> None:
    """En modo dolares la base ES el importe, asi que el cociente da 100% en
    todas y pareceria que cada compra se lleva la cartera entera. Un numero
    que no significa nada es peor que ninguno: este es el que se lee a las 3
    de la manana para decidir si hay que parar el bot."""
    filas = _mod()._compras([
        "IMPORTE: 10.00 $ a 220.00 $/SOL -> compra de 0.0455 SOL · libres 0.30",
        "Buying 0.045500 SOL worth of DOLARES on pumpfun...",
    ])
    assert filas[0][3] == 0.0


def test_la_tolerancia_no_se_come_un_exceso_de_verdad() -> None:
    """Se perdona el redondeo del log, no un tamano equivocado. Con el 25%
    configurado, un 26% es redondeo y un 50% es un fallo que hay que ver."""
    mod = _mod()
    assert mod.TOLERANCIA_PUNTOS < 5, "una tolerancia grande esconde el fallo"
    filas = mod._compras([
        "CARTERA: 0.8000 SOL (0.2000 en wallet + 0.6000 en monedas) · libres 0.17",
        "Buying 0.400000 SOL worth of RARA on pumpfun...",
    ])
    assert filas[0][3] > 25 + mod.TOLERANCIA_PUNTOS, "el 50% tiene que marcarse"


def test_el_comando_corto_queda_instalado() -> None:
    """Si no se instala, el comando no existe en la maquina y el diagnostico
    no se usa. `porque` llego al droplet el 18-sep y siguio dando "command not
    found" durante dias porque nadie volvio a correr el instalador."""
    inst = (RAIZ / "deploy/instalar_comandos.sh").read_text(encoding="utf-8")
    assert "_poner_py cuanto_compra cuanto_compra.py" in inst
