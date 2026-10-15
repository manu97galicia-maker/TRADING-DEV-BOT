"""Guardianes de `bots/bot-mayhem-roi50.yaml` (MAYHEM ROI50).

Pedido por Manu el 15-oct-2026: "creame una estrategia solo para mayhem donde
pueda sacar facil un roi de un 50% por operacion".

Lo que vigila este fichero, y por que cada cosa:

1. SEGURIDAD. Nace en papel y apagado porque los datos son una foto historica
   con seleccion sesgada. Que nadie lo encienda sin querer.
2. GEOMETRIA. El +50% solo es alcanzable entrando al nacer. Si alguien sube
   `max_entry_mc`, el objetivo se vuelve imposible EN SILENCIO -- el bot
   seguiria comprando, solo que el TP ya no se tocaria nunca.
3. QUE NO QUEDE MUDO. `require_mayhem` necesita el listener `logs` (Helius);
   con pumpportal la bandera mayhem no llega. Y el 50% medido viene de la
   lista de devs: sin ella no queda nada.
4. QUE EL COMENTARIO NO MIENTA. El YAML afirma que el equilibrio esta en el
   34,5% de acierto. Se recalcula aqui.
"""

import csv
import unittest
from pathlib import Path

import yaml

from trading.tiered_exit import build_tiers

RUTA = Path("bots/bot-mayhem-roi50.yaml")
RUTA_C123 = Path("bots/bot-unica123-real.yaml")
CSV_MAYHEM = Path("mayhem_v2_raw.csv")

#: Matematica de la bonding curve de pump.fun, con SOL a $150.
K = 30 * 1073e6
VT0 = 1073e6
SUPERVIVENCIA = 1e9  # supply total
PRECIO_SOL = 150.0

#: Minimo de lanzamientos para que la tasa de acierto de un dev signifique
#: algo. Con 2 lanzamientos, "50%" solo puede ser 1 de 2: es un artefacto.
MIN_LANZAMIENTOS_FIABLES = 5

#: Comisiones de prioridad sobre una compra, medidas en esta sesion.
COMISIONES = 0.0088


def _mc(avance: float) -> float:
    """Market cap en USD a un avance dado de la curva (0 = al nacer)."""
    vt = VT0 - 793.1e6 * avance
    return (K / vt**2) * SUPERVIVENCIA * PRECIO_SOL


def _escalera(trade: dict) -> list:
    """Construye la escalera de salida igual que lo hace el trader."""
    pares = [
        (trade.get("take_profit_percentage"), trade.get("take_profit_sell_percentage"))
    ]
    pares += [
        (trade.get(f"tp{n}_gain"), trade.get(f"tp{n}_sell")) for n in (2, 3, 4, 5)
    ]
    return build_tiers(pares)


class MayhemRoi50Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = yaml.safe_load(RUTA.read_text(encoding="utf-8"))
        cls.trade = cls.cfg["trade"]
        cls.filtros = cls.cfg["filters"]

    # ---- 1. seguridad ----
    def test_nace_en_papel_y_apagado(self) -> None:
        self.assertFalse(self.cfg["enabled"])
        self.assertTrue(self.filtros["paper_trade"])

    def test_el_cinturon_es_paper_trade_no_max_real_buys(self) -> None:
        """`max_real_buys: 0` es falsy en universal_trader.py:4741 -> SIN limite.

        Si algun dia alguien lo toma por "cero compras", este test recuerda
        que el freno de verdad es `paper_trade`.
        """
        self.assertEqual(self.trade["max_real_buys"], 0)
        self.assertTrue(
            self.filtros["paper_trade"],
            "max_real_buys=0 NO frena nada; el freno es paper_trade",
        )

    # ---- 2. geometria: el +50% tiene que ser alcanzable ----
    def test_entra_al_nacer_para_que_el_50_sea_posible(self) -> None:
        """MC(0% curva) = $4.194. Entrando ahi, +50% es MC $6.291.

        Las monedas de estos devs llegan a $30.000, asi que cruzan $6.291 de
        paso. Si `max_entry_mc` subiera, dejaria de ser asi.
        """
        self.assertLessEqual(self.filtros["max_entry_mc"], 4200)
        self.assertGreater(self.filtros["min_entry_mc"], 0)

        self.assertAlmostEqual(_mc(0.0), 4194, delta=5)

        tp = self.trade["take_profit_percentage"]
        objetivo = self.filtros["max_entry_mc"] * (1 + tp)
        self.assertLess(
            objetivo,
            30_000,
            "el objetivo del TP tiene que quedar MUY por debajo de los "
            "$30.000 que estas monedas alcanzan; si no, el +50% es una apuesta",
        )

    def test_el_91_por_ciento_de_curva_no_daria_el_50(self) -> None:
        """Por que esta estrategia no reusa la entrada del C123.

        Al 91% de curva quedan +57,6% hasta migrar: un TP de +50% exigiria
        que casi migre. Este test fija esa cuenta para que la justificacion
        del YAML no se quede sin respaldo.
        """
        self.assertAlmostEqual(_mc(0.91), 39_130, delta=50)
        techo = 410.9 / 260.8 - 1
        self.assertAlmostEqual(techo, 0.576, delta=0.005)
        self.assertGreater(
            self.trade["take_profit_percentage"],
            techo * 0.8,
            "si el TP pedido no estuviera cerca del techo del 91%, no haria "
            "falta cambiar de punto de entrada y esta estrategia no tendria "
            "razon de existir",
        )

    # ---- 3. que nada quede escrito y mudo ----
    def test_solo_mayhem_y_con_el_listener_que_lo_detecta(self) -> None:
        self.assertTrue(self.filtros["require_mayhem"])
        self.assertTrue(self.filtros["allow_mayhem"])
        self.assertEqual(
            self.filtros["listener_type"],
            "logs",
            "pumpportal NO reporta la bandera mayhem: require_mayhem quedaria mudo",
        )

    def test_la_lista_de_devs_esta_encendida(self) -> None:
        """El 50% medido viene de la lista. Sin ella no queda nada medido."""
        self.assertFalse(self.filtros["ignorar_listas_de_devs"])
        self.assertEqual(self.filtros["bro_address"], "top_devs_mayhem40k.json")
        self.assertTrue(Path(self.filtros["bro_address"]).exists())

    def test_la_salida_vende_todo_en_un_solo_escalon(self) -> None:
        """ "+50% por operacion" = un escalon al +50% que cierra la posicion."""
        self.assertEqual(self.trade["take_profit_percentage"], 0.50)
        self.assertEqual(self.trade["take_profit_sell_percentage"], 1.0)
        self.assertEqual(self.trade["stop_loss_percentage"], 0.25)

    def test_los_tp_de_sobra_no_estan_escritos_ni_a_cero(self) -> None:
        """Escribir `tp2_gain: 0` NO apaga el escalon: lo pone al +0%.

        `build_tiers` descarta con `if gain is None or sell is None`, o sea
        solo si la clave NO EXISTE. Un 0 explicito pasa el filtro y crea un
        `Tier(gain=0, sell=0)` que se cumple EN EL INSTANTE DE COMPRAR
        (`tiers_due` exige `price >= entry * 1.0`).

        Este fichero se escribio primero con esos ceros. Salian cuatro
        escalones fantasma antes del de verdad. Danos: el escalon real se iba
        al indice 4; cada fantasma ponia `had_tp = True` (la senal "el dev
        entrego un pump", que alimenta la clasificacion rug/win -> se marcaria
        TODA moneda como pump); y con `stop_a_entrada_tras_tp` encendido el
        suelo habria quedado POR ENCIMA del precio de compra.

        El C123 lo hace bien por omision: tampoco escribe tp3..tp5.
        """
        for n in (2, 3, 4, 5):
            self.assertNotIn(
                f"tp{n}_gain",
                self.trade,
                f"tp{n}_gain escrito: un 0 ahi crea un escalon al +0%, no lo apaga",
            )
            self.assertNotIn(f"tp{n}_sell", self.trade)

    def test_la_escalera_que_se_construye_de_verdad_es_un_solo_peldano(self) -> None:
        """No basta leer el YAML: hay que construir la escalera y contarla.

        Es el guardian que habria pillado los escalones fantasma, porque el
        YAML "parecia" correcto (ceros = apagado) y la escalera no lo era.
        """
        escalera = _escalera(self.trade)
        self.assertEqual(len(escalera), 1, f"se esperaba 1 escalon, hay {escalera}")
        self.assertEqual(escalera[0].gain, 0.50)
        self.assertEqual(escalera[0].sell, 1.0)
        self.assertTrue(
            all(t.gain > 0 for t in escalera),
            "un escalon al +0% se cumple al comprar: nunca debe existir",
        )

    def test_el_c123_no_tiene_escalones_fantasma(self) -> None:
        """Comprobado al encontrar el fallo en este fichero: el bot REAL de
        Manu esta limpio. Sus escalones son +50% (vende 0%, solo sube el
        stop) y +70% (vende 100%). Si alguien anadiera `tp3_gain: 0` al C123,
        el escalon 0 pasaria a ser +0% y, con `stop_a_entrada_tras_tp: true`
        y `margen_stop_tras_tp: 0.12`, el suelo saltaria a 1,12x la compra EN
        EL MOMENTO DE COMPRAR: cada posicion se cerraria sola al primer tick.
        Este test vigila eso con dinero real de por medio.
        """
        c123 = yaml.safe_load(RUTA_C123.read_text(encoding="utf-8"))["trade"]
        escalera = _escalera(c123)
        self.assertTrue(
            all(t.gain > 0 for t in escalera),
            f"el C123 tiene un escalon al +0%: {escalera}",
        )
        # CAMBIADO EL 15-oct: un solo peldano al +30% que vende el 80% (el
        # capital entero) y deja un moonbag del 20% corriendo. El tp2 de
        # +70% se quito porque era INALCANZABLE al 91% de curva --quedaban
        # +57,5%-- y porque vendia el 100%, matando al corredor.
        self.assertEqual([t.gain for t in escalera], [0.30])
        self.assertEqual([t.sell for t in escalera], [0.80])

    def test_los_frenos_antirug_estan_activos(self) -> None:
        """Comprando al nacer, el rug es el riesgo principal.

        En el C123 estan apagados porque alli se entra al 91% de curva. Aqui
        apagarlos seria repetir el caso FUCKETHAN (-91,9%).
        """
        self.assertGreater(self.trade["anti_rug_ratio"], 0)
        self.assertGreater(self.trade["anti_rug_seg"], 0)
        self.assertGreater(self.trade["venta_bajo_entrada_seg"], 0)

    def test_las_posiciones_caben_en_el_presupuesto_del_nodo(self) -> None:
        """vigilables = max_rps * price_check_interval / 2."""
        vigilables = (
            self.cfg["node"]["max_rps"] * self.trade["price_check_interval"] / 2
        )
        self.assertLessEqual(self.trade["max_posiciones"], vigilables)

    # ---- 4. que el comentario no mienta ----
    def test_el_equilibrio_escrito_en_el_yaml_es_el_de_verdad(self) -> None:
        """El YAML afirma 34,5% de acierto para empatar. Se recalcula."""
        tp = self.trade["take_profit_percentage"]
        sl = self.trade["stop_loss_percentage"]
        equilibrio = (sl + COMISIONES) / (tp + sl)
        self.assertAlmostEqual(equilibrio * 100, 34.5, delta=0.1)

        texto = RUTA.read_text(encoding="utf-8")
        self.assertIn("34,5%", texto)

    def test_la_media_por_operacion_escrita_es_la_de_verdad(self) -> None:
        """El YAML avisa de que la media es +11,6%, no +50%. Se recalcula
        con la tasa medida de 50,0% (42 aciertos de 84 lanzamientos)."""
        tp = self.trade["take_profit_percentage"]
        sl = self.trade["stop_loss_percentage"]
        p = 0.50
        media = p * tp - (1 - p) * sl - COMISIONES
        self.assertAlmostEqual(media * 100, 11.6, delta=0.1)
        self.assertIn("+11,6%", RUTA.read_text(encoding="utf-8"))

    def test_la_tasa_medida_sale_del_csv_de_verdad(self) -> None:
        """50,0% = 42 de 84, sobre los devs con 5+ lanzamientos.

        Si el CSV cambia, este test avisa en vez de dejar el YAML afirmando
        un numero que ya no sale de ningun sitio.
        """
        with CSV_MAYHEM.open(encoding="utf-8") as fh:
            filas = list(csv.DictReader(fh))
        buenos = [
            f for f in filas if int(f["mayhem_launches"]) >= MIN_LANZAMIENTOS_FIABLES
        ]
        lanz = sum(int(f["mayhem_launches"]) for f in buenos)
        aciertos = sum(
            int(f["mayhem_launches"]) * float(f["pct_pump_30k"]) / 100 for f in buenos
        )
        self.assertEqual(lanz, 84)
        self.assertAlmostEqual(aciertos, 42, delta=0.5)
        self.assertAlmostEqual(aciertos / lanz, 0.50, delta=0.01)

    def test_el_sesgo_del_csv_queda_documentado(self) -> None:
        """Ningun dev del CSV tiene 0% de acierto -> la lista esta sesgada.

        Es el aviso mas importante del YAML: que no se lea el 50% como una
        probabilidad limpia. Si el aviso desaparece, este test se pone rojo.
        """
        with CSV_MAYHEM.open(encoding="utf-8") as fh:
            filas = list(csv.DictReader(fh))
        self.assertEqual(
            sum(1 for f in filas if float(f["pct_pump_30k"]) == 0),
            0,
            "si apareciesen devs con 0%, el sesgo habria desaparecido y el "
            "aviso del YAML habria que reescribirlo",
        )
        texto = RUTA.read_text(encoding="utf-8")
        self.assertIn("CERO devs con 0% de acierto", texto)

    def test_avisa_de_que_reintroduce_la_lista_de_devs(self) -> None:
        """REGLA 0 parte 2: Manu pidio quitar la lista de devs. Esto la
        vuelve a usar, y el YAML tiene que decirlo en voz alta."""
        texto = RUTA.read_text(encoding="utf-8")
        self.assertIn("quita la lista de devs", texto)

    def test_el_c123_no_se_toco(self) -> None:
        """Esta estrategia es un bot aparte. El bot real de Manu no cambia."""
        c123 = yaml.safe_load(RUTA_C123.read_text(encoding="utf-8"))
        self.assertTrue(c123["enabled"])
        self.assertFalse(c123["filters"]["paper_trade"])
        self.assertTrue(c123["filters"]["ignorar_listas_de_devs"])
        self.assertEqual(c123["filters"]["curva_96"]["umbral"], 0.80)


if __name__ == "__main__":
    unittest.main()
