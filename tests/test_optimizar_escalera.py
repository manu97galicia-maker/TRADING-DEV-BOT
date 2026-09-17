"""Buscar la escalera que mas deja, sin enganarse con los propios datos.

PARA QUE. Con 27.241 monedas medidas salio que el mejor filtro sube el
acierto del 14,4% al 27,4% -- y aun asi la cuenta no sale, porque el TP1 de
hoy cobra +5% de la posicion contra un stop de -18%. El problema no es el
filtro, es la escalera, y cada moneda apuntada trae su pico y su suelo: se
puede probar cualquier escalera contra esos recorridos sin arriesgar nada.

LAS DOS FORMAS DE MENTIRSE QUE ESTOS TESTS VIGILAN
* Optimizar contra la punta OPTIMISTA. De cada moneda se sabe el pico y el
  suelo pero no en que orden pasaron. Elegir por la optimista es elegir una
  escalera que solo funciona si la suerte acompana en el orden.
* Dar por bueno un ganador que esta en el BORDE de lo probado. Si el mejor
  stop es el mas apretado de la lista, el optimo real esta fuera y ese
  numero no es el mejor: es el ultimo que se miro. Con el stop ademas es
  peligroso, porque uno apretado parece gratis con estos datos y en la
  realidad salta con el ruido y saca de las que iban a subir.
"""

# ruff: noqa: S101, SLF001, PLR2004, ANN001, ANN202, S311

import importlib.util
import random
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _op():
    ruta = RAIZ / "deploy" / "optimizar_escalera.py"
    spec = importlib.util.spec_from_file_location("op", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------- el reparto


def test_la_escalera_construida_nunca_vende_mas_del_100() -> None:
    """Vender mas de lo que se tiene es una orden imposible."""
    op = _op()
    for g in op.TP1_OBJETIVOS:
        for v in op.TP1_VENTAS:
            total = sum(x for _g, x in op._lejanos(g, v))
            assert total <= 1.0 + 1e-9, f"TP1 +{g} vendiendo {v} suma {total}"


def test_se_respeta_el_moonbag() -> None:
    """El 2% que Manu vende a mano no lo toca ninguna escalera probada."""
    op = _op()
    for g in op.TP1_OBJETIVOS:
        for v in op.TP1_VENTAS:
            if v >= 1.0:
                continue  # vendiendo todo en el TP1 no queda moonbag, y esta bien
            total = sum(x for _g, x in op._lejanos(g, v))
            assert total <= 0.98 + 1e-9


def test_los_peldanos_van_en_orden_creciente() -> None:
    """Un peldano por debajo del anterior no se dispararia nunca."""
    op = _op()
    for g in op.TP1_OBJETIVOS:
        objetivos = [x for x, _v in op._lejanos(g, 0.3)]
        assert objetivos == sorted(objetivos)


# ------------------------------------------------------- la cuenta


def test_la_punta_peor_nunca_supera_a_la_mejor() -> None:
    op = _op()
    rng = random.Random(4)
    filas = [
        {"pico": rng.random() * 3, "suelo": -rng.random() * 0.6,
         "ultimo": rng.random() * 2 - 1}
        for _ in range(300)
    ]
    peor, mejor = op._cuenta(filas, op._lejanos(0.25, 0.20), 0.18)
    assert peor <= mejor


def test_una_escalera_que_cobra_antes_gana_mas_en_las_que_suben_poco() -> None:
    """Comprobacion de sentido: si todas suben justo un +30% y se dan la
    vuelta, un TP1 al +25% cobra y uno al +50% no llega."""
    op = _op()
    filas = [{"pico": 0.30, "suelo": -0.05, "ultimo": 0.0}] * 100
    cobra = op._cuenta(filas, op._lejanos(0.25, 1.0), 0.18)[0]
    no_llega = op._cuenta(filas, op._lejanos(0.50, 1.0), 0.18)[0]
    assert cobra > no_llega


def test_con_la_tasa_real_medida_ninguna_escalera_gana_seguro() -> None:
    """EL RESULTADO QUE IMPORTA, fijado para que no se olvide.

    Con el 14,4% de acierto que se midio de verdad, NINGUNA escalera de las
    probadas sale positiva en su punta PESIMISTA. Dicho claro: no se arregla
    vendiendo distinto, hay que comprar mejor.

    OJO CON LA PUNTA OPTIMISTA, que si llega a rozar el positivo (+1,8% con
    el TP1 al +15%, vendiendo todo y stop al -8%). Eso NO es una escalera
    ganadora: es el artefacto que esta punta existe para ensenar. Supone que
    en TODAS las monedas se cobro el peldano antes de que saltara el stop, y
    con un TP1 tan bajo eso significa suponer que ninguna perdedora se
    desplomo de golpe. Ademas cae justo en los tres bordes de la busqueda, y
    por eso salta el aviso. La punta pesimista, que es la que no depende de
    suponer nada, sigue en -6,7%.
    """
    op = _op()
    rng = random.Random(11)
    filas = []
    for _ in range(2000):
        if rng.random() < 0.144:
            pico = rng.choice([0.28, 0.35, 0.55, 0.9, 1.8, 4.0])
            filas.append({"pico": pico, "suelo": -rng.random() * 0.2,
                          "ultimo": pico * rng.random()})
        else:
            suelo = -0.20 - rng.random() * 0.5
            filas.append({"pico": rng.random() * 0.22, "suelo": suelo,
                          "ultimo": suelo})
    peores = []
    for g in op.TP1_OBJETIVOS:
        for v in op.TP1_VENTAS:
            esc = op._lejanos(g, v)
            for stop in op.STOPS:
                peores.append(op._cuenta(filas, esc, stop)[0])
    assert max(peores) < 0, "ahora SI hay escalera que gane seguro: mirarlo"


# --------------------------------------------- no fiarse del borde


def test_avisa_cuando_el_ganador_esta_en_el_borde(capsys) -> None:
    """Sin este aviso, el numero del borde se copia al YAML como si fuera
    un optimo, y no lo es."""
    op = _op()
    op._avisar_si_toca_el_borde(max(op.TP1_OBJETIVOS), 1.0, min(op.STOPS))
    salida = capsys.readouterr().out
    assert "BORDE" in salida
    assert "stop mas apretado" in salida
    # Y el aviso concreto del stop, que es el peligroso.
    assert "ruido" in salida


def test_no_avisa_cuando_el_ganador_esta_en_medio(capsys) -> None:
    """Un aviso que sale siempre deja de leerse."""
    op = _op()
    medio_tp = sorted(op.TP1_OBJETIVOS)[len(op.TP1_OBJETIVOS) // 2]
    medio_stop = sorted(op.STOPS)[len(op.STOPS) // 2]
    op._avisar_si_toca_el_borde(medio_tp, 0.30, medio_stop)
    assert capsys.readouterr().out == ""


def test_el_optimizador_no_cambia_la_configuracion() -> None:
    """Solo calcula: los numeros los pasa una persona al YAML a mano."""
    texto = (RAIZ / "deploy" / "optimizar_escalera.py").read_text(encoding="utf-8")
    for prohibido in ("write_text", "yaml.dump", "systemctl", "git "):
        assert prohibido not in texto, f"el optimizador hace '{prohibido}'"
