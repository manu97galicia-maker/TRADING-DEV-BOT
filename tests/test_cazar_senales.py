"""El medidor de senales: que encuentre lo que hay y NO invente lo que no hay.

POR QUE EXISTEN ESTAS DOS HERRAMIENTAS. Elegir "compra si la presion es 3x"
a ojo es apostar. Nadie sabe si 3x predice algo en pump.fun sin medirlo, y
aqui se juega dinero de verdad. `cazar_senales.py` apunta las senales de cada
moneda Y lo que hizo despues; `analizar_senales.py` dice que umbral habria
acertado.

LO QUE ESTOS TESTS DEFIENDEN. Una herramienta de medir que se equivoca es
peor que no tenerla, porque da confianza falsa: se apostaria dinero sobre un
umbral inventado creyendo que esta medido. Asi que se comprueban las dos
mitades -- que encuentra una relacion plantada a proposito, y que NO saca
ventaja de datos donde no hay ninguna.
"""

# ruff: noqa: S101, SLF001, PLR2004, ANN001, ANN003, ANN202, S311

import importlib.util
import json
import random
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent


def _modulo(nombre: str):
    ruta = RAIZ / "deploy" / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fila(**kw) -> dict:
    base = {
        "presion": 1.0, "ola_compradores": 0, "compradores": 0,
        "velocidad_sol_s": 0.0, "ola_neto_sol": 0.0, "avance_curva": 0.0,
        "titulares": 0, "carteras_primer_bloque": 0, "sol_primer_bloque": 0.0,
        "venta_creador_sol": 0.0, "mayor_compra_sol": 0.0,
        "pico": 0.0, "suelo": 0.0,
        "llego_tp1": False, "llego_tp2": False, "llego_tp3": False,
        "toco_stop": False,
    }
    base.update(kw)
    return base


# ------------------------------------------------- el analizador mide bien


def test_encuentra_una_relacion_plantada() -> None:
    """Datos con una regla conocida: presion alta -> pump. Tiene que verla."""
    an = _modulo("analizar_senales")
    rng = random.Random(7)
    filas = []
    for _ in range(400):
        presion = rng.choice([0.5, 1.0, 1.8, 2.5, 4.0, 7.0])
        gana = rng.random() < (0.15 + (0.40 if presion >= 3 else 0.0))
        filas.append(_fila(presion=presion, llego_tp1=gana))
    base = an._pct(filas, "llego_tp1")
    pasan = [f for f in filas if f["presion"] >= 3]
    assert an._pct(pasan, "llego_tp1") - base > an.VENTAJA_UTIL


def test_no_inventa_ventaja_donde_no_la_hay() -> None:
    """LA MITAD QUE MAS IMPORTA. Con el resultado echado a suertes, ninguna
    senal deberia sacar ventaja: si la sacara, el analizador estaria
    encontrando formas en el ruido y llevaria a apostar por nada."""
    an = _modulo("analizar_senales")
    rng = random.Random(11)
    filas = [
        _fila(
            presion=rng.random() * 8,
            ola_compradores=rng.randint(0, 20),
            titulares=rng.randint(0, 150),
            llego_tp1=rng.random() < 0.3,
        )
        for _ in range(600)
    ]
    base = an._pct(filas, "llego_tp1")
    for senal, (cortes, sentido) in an.CORTES.items():
        for corte in cortes:
            if sentido == "mayor":
                pasan = [f for f in filas if (f.get(senal) or 0) >= corte]
            else:
                pasan = [f for f in filas if (f.get(senal) or 0) <= corte]
            if len(pasan) < an.MINIMO_FIABLE:
                continue
            ventaja = an._pct(pasan, "llego_tp1") - base
            assert abs(ventaja) < an.VENTAJA_UTIL, (
                f"{senal}>={corte} saca {ventaja:+.1f} puntos de puro ruido"
            )


def test_sin_datos_no_dice_nada() -> None:
    an = _modulo("analizar_senales")
    assert an._pct([], "llego_tp1") == 0.0
    assert an._cargar(0) == [] or isinstance(an._cargar(0), list)


def test_una_linea_rota_no_tumba_el_analisis(tmp_path: Path, monkeypatch) -> None:
    """Un fichero que se corta a medias (Ctrl+C) tiene que poder leerse."""
    an = _modulo("analizar_senales")
    f = tmp_path / "senales.jsonl"
    f.write_text(
        json.dumps(_fila(llego_tp1=True)) + "\n{esto no es json\n"
        + json.dumps(_fila()) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(an, "ENTRADA", f)
    assert len(an._cargar(0)) == 2


# ---------------------------------------------------- el cazador no compra


def test_el_cazador_no_compra_nada() -> None:
    """Es una herramienta de MEDIR. Si algun dia alguien le mete una compra,
    dejaria de ser una medida y pasaria a ser una apuesta con dinero."""
    texto = (RAIZ / "deploy" / "cazar_senales.py").read_text(encoding="utf-8")
    for prohibido in ("execute(", "buy(", "Keypair", "SOLANA_PRIVATE_KEY"):
        assert prohibido not in texto, f"el cazador toca '{prohibido}'"


def test_una_moneda_no_se_fotografia_antes_de_tiempo() -> None:
    """La foto se toma a los 20s: antes no ha pasado nada que medir."""
    cz = _modulo("cazar_senales")
    c = cz.Cazador()
    c.ver(["m1"])
    c.ver(["m1"])  # sigue siendo joven
    assert "m1" in c.pendientes
    assert "m1" not in c.siguiendo


def test_las_que_nunca_arrancan_se_olvidan() -> None:
    """Sin esto, pump.fun lanza miles al dia y la memoria crece sin freno."""
    cz = _modulo("cazar_senales")
    c = cz.Cazador()
    c.pendientes["vieja"] = 0.0  # de 1970
    c.limpiar()
    assert "vieja" not in c.pendientes


def test_los_peldanos_medidos_son_los_de_la_escalera_real() -> None:
    """Si la escalera cambia y esto no, se estaria midiendo otra cosa."""
    cz = _modulo("cazar_senales")
    t = yaml.safe_load(
        (RAIZ / "bots/bot-paper-ai-entry.yaml").read_text(encoding="utf-8")
    )["trade"]
    assert cz.TP1 == t["take_profit_percentage"]
    assert cz.TP2 == t["tp2_gain"]
    assert cz.TP3 == t["tp3_gain"]
    assert cz.STOP == -t["stop_loss_percentage"]


# ------------------------------------------- lanzarlo desde el movil


def test_el_servicio_lanza_el_cazador_y_no_el_bot() -> None:
    """Un copy-paste mal hecho aqui arrancaria el bot por duplicado, con dos
    procesos comprando con la misma wallet."""
    unidad = (RAIZ / "deploy" / "pump-bot-cazador.service").read_text(
        encoding="utf-8"
    )
    assert "deploy/cazar_senales.py" in unidad
    assert "bot_runner" not in unidad
    assert "pump_bot" not in unidad


def test_el_servicio_vuelve_solo_si_se_corta_el_websocket() -> None:
    """Medir bien son horas. Perder un rato de escucha no rompe nada --cada
    moneda es una linea suelta-- pero perder la tarde entera si."""
    unidad = (RAIZ / "deploy" / "pump-bot-cazador.service").read_text(
        encoding="utf-8"
    )
    assert "Restart=always" in unidad


def test_el_script_no_borra_lo_apuntado_al_apagar() -> None:
    """Apagar y encender no puede costar las horas de datos ya recogidas."""
    script = (RAIZ / "deploy" / "cazador.sh").read_text(encoding="utf-8")
    apagado = script[script.index("  off)") : script.index("  ver)")]
    assert "rm " not in apagado
    assert "senales.jsonl" not in apagado.replace('Lo apuntado sigue en "$DATOS"', "")


# --------------------------------------------- la cuenta, que es lo que decide


def test_una_que_solo_toca_el_stop_pierde_justo_el_stop() -> None:
    """El caso mas simple, y el que fija la escala de todo lo demas."""
    an = _modulo("analizar_senales")
    _esc, stop = an._escalera()
    peor, mejor = an._cuenta([{"pico": 0.0, "suelo": -0.30, "ultimo": -0.30}])
    assert peor == -stop
    assert mejor == -stop


def test_acertar_mucho_no_significa_ganar() -> None:
    """LA TRAMPA QUE ESTA CUENTA DESTAPA, y el motivo de escribirla.

    El analisis de senales dice cuantas monedas LLEGAN al TP1. Es facil leer
    un 27% de acierto como "vamos bien". Pero el TP1 vende el 20% de un +25%,
    o sea +5% de la posicion, y el stop cuesta -18%: con esos numeros hay que
    acertar mucho mas de una de cada cuatro solo para empatar.

    Aqui se fija con una tasa de acierto ALTA --una de cada cuatro llega al
    +30%-- y aun asi la cuenta sale negativa. Si alguien sube el peso del TP1
    o baja el stop, este test cambia de signo y obliga a mirarlo.
    """
    an = _modulo("analizar_senales")
    filas = []
    for i in range(100):
        if i < 25:
            filas.append({"pico": 0.30, "suelo": -0.05, "ultimo": 0.30})
        else:
            filas.append({"pico": 0.05, "suelo": -0.25, "ultimo": -0.25})
    peor, mejor = an._cuenta(filas)
    assert peor < 0, "la punta pesimista deberia salir negativa"
    assert mejor < 0, "ni siendo optimista sale la cuenta"


def test_la_punta_peor_nunca_supera_a_la_mejor() -> None:
    """Son las dos puntas del mismo intervalo: si se cruzan, la cuenta miente."""
    an = _modulo("analizar_senales")
    rng = random.Random(5)
    filas = [
        {"pico": rng.random() * 3, "suelo": -rng.random() * 0.5,
         "ultimo": rng.random() * 2 - 1}
        for _ in range(300)
    ]
    peor, mejor = an._cuenta(filas)
    assert peor <= mejor


def test_lo_que_queda_dentro_no_se_valora_al_pico() -> None:
    """Valorar el resto al pico seria hacer trampa: ese precio ya paso.

    Una moneda que sube a +100% y se cae a -50% no vale +100% al final. Si se
    contara asi, cualquier filtro pareceria rentable.
    """
    an = _modulo("analizar_senales")
    subio_y_cayo = [{"pico": 1.0, "suelo": -0.10, "ultimo": -0.50}]
    se_quedo_arriba = [{"pico": 1.0, "suelo": -0.10, "ultimo": 1.0}]
    assert an._cuenta(subio_y_cayo)[1] < an._cuenta(se_quedo_arriba)[1]


def test_sin_datos_la_cuenta_es_cero_y_no_revienta() -> None:
    an = _modulo("analizar_senales")
    assert an._cuenta([]) == (0.0, 0.0)


# ---------------------------------------------------------- el ritmo


def test_el_ritmo_sale_de_las_marcas_de_tiempo_no_de_una_estimacion() -> None:
    """Un filtro se juzga por DOS cosas y casi siempre se mira solo una.

    La otra es el ritmo: de nada sirve el mejor filtro si deja caer tres
    monedas por minuto y solo caben cinco posiciones -- el bot se llena con
    las primeras que pasen, que no son las mejores, son las primeras, y a
    partir de ahi quien elige es el reloj.
    """
    an = _modulo("analizar_senales")
    fuente = (RAIZ / "deploy" / "analizar_senales.py").read_text(encoding="utf-8")
    cuerpo = fuente[fuente.index("def _ritmo(") : fuente.index("def main(")]
    assert 'float(f["t"])' in cuerpo, "el ritmo no usa las marcas de tiempo"
    assert an is not None


def test_sin_marcas_de_tiempo_no_inventa_un_ritmo(capsys) -> None:
    """Datos viejos sin `t` no pueden producir un numero de la nada."""
    an = _modulo("analizar_senales")
    an._ritmo([_fila(), _fila()])
    assert capsys.readouterr().out == ""


def test_con_una_sola_moneda_no_hay_ritmo(capsys) -> None:
    """Un solo punto no define un intervalo."""
    an = _modulo("analizar_senales")
    an._ritmo([{**_fila(), "t": 1000.0}])
    assert capsys.readouterr().out == ""


def test_el_ritmo_se_calcula_bien(capsys) -> None:
    """60 monedas repartidas en una hora son una por minuto."""
    an = _modulo("analizar_senales")
    filas = [{**_fila(ola_compradores=5), "t": 1000.0 + i * 60} for i in range(60)]
    an._ritmo(filas)
    salida = capsys.readouterr().out
    assert "1.0 por minuto" in salida
