"""El reparto de monedas entre los bots que comparten wallet.

Los tres bots (DEFINITIVA, ELITE, RAPIDA) corren en procesos distintos con la
MISMA wallet. Sin reparto, los tres compraban la misma moneda a la vez y la
cartera acababa con el triple de exposicion en una sola posicion creyendo que
eran tres apuestas independientes. Paso con GAMBLE: dos bots, 0,27 SOL.

Lo que se comprueba aqui es que de N bots que piden la misma moneda gana
exactamente UNO, que la moneda vuelve al bote al cerrar, y que un reclamo
huerfano (bot muerto) no bloquea la moneda para siempre.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from trading.universal_trader import UniversalTrader


def _bot(carpeta: str, sufijo: str, ttl: float = 900.0) -> UniversalTrader:
    """Un trader con lo justo para el reparto, sin tocar la red ni la wallet."""
    t = UniversalTrader.__new__(UniversalTrader)
    t._open_pos_path = Path(carpeta) / f"open_position_{sufijo}.json"
    t._state_suffix = f"_{sufijo}"
    t.coordinar_mints = True
    t.coordinar_mints_ttl = ttl
    return t


class MintClaimTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.mint = "7Gg7eLzykpyafK2aGHK8buFbFouHzMQ6KchxtKx5pump"

    def tearDown(self):
        self.dir.cleanup()

    def test_solo_gana_uno_de_tres(self):
        """Tres bots piden la misma moneda: entra uno y solo uno."""
        bots = [_bot(self.dir.name, s) for s in ("definitiva", "elite", "rapida")]
        ganados = [b._reclamar_mint(self.mint) for b in bots]
        self.assertEqual(ganados.count(True), 1, "tiene que ganar exactamente uno")
        self.assertEqual(ganados.count(False), 2)

    def test_el_ganador_es_el_primero(self):
        a = _bot(self.dir.name, "definitiva")
        b = _bot(self.dir.name, "elite")
        self.assertTrue(a._reclamar_mint(self.mint))
        self.assertFalse(b._reclamar_mint(self.mint))

    def test_monedas_distintas_no_se_estorban(self):
        """El reparto es por moneda, no un candado global de la wallet."""
        a = _bot(self.dir.name, "definitiva")
        b = _bot(self.dir.name, "elite")
        self.assertTrue(a._reclamar_mint(self.mint))
        self.assertTrue(b._reclamar_mint("OtraMoneda111111111111111111111111111pump"))

    def test_al_soltar_queda_libre(self):
        """Cerrada la posicion, otro bot puede entrar en esa moneda."""
        a = _bot(self.dir.name, "definitiva")
        b = _bot(self.dir.name, "elite")
        self.assertTrue(a._reclamar_mint(self.mint))
        self.assertFalse(b._reclamar_mint(self.mint))
        a._soltar_mint(self.mint)
        self.assertTrue(b._reclamar_mint(self.mint))

    def test_reclamo_huerfano_caduca(self):
        """Un bot que muere con la moneda pedida no la bloquea para siempre."""
        muerto = _bot(self.dir.name, "definitiva", ttl=0.2)
        vivo = _bot(self.dir.name, "elite", ttl=0.2)
        self.assertTrue(muerto._reclamar_mint(self.mint))
        self.assertFalse(vivo._reclamar_mint(self.mint))
        time.sleep(0.3)
        self.assertTrue(vivo._reclamar_mint(self.mint), "pasado el TTL se libera")

    def test_reclamo_fresco_no_caduca(self):
        """Lo contrario: dentro del TTL el reclamo aguanta."""
        a = _bot(self.dir.name, "definitiva", ttl=30.0)
        b = _bot(self.dir.name, "elite", ttl=30.0)
        self.assertTrue(a._reclamar_mint(self.mint))
        self.assertFalse(b._reclamar_mint(self.mint))

    def test_apagado_no_reparte(self):
        """Con `coordinar_mints: false` todo sigue como antes: nadie se bloquea."""
        a = _bot(self.dir.name, "definitiva")
        b = _bot(self.dir.name, "elite")
        a.coordinar_mints = b.coordinar_mints = False
        self.assertTrue(a._reclamar_mint(self.mint))
        self.assertTrue(b._reclamar_mint(self.mint))
        self.assertFalse(
            a._mint_claim_path(self.mint).exists(),
            "apagado no debe dejar ficheros de reclamo",
        )

    def test_el_reclamo_lo_comparten_los_bots(self):
        """La ruta NO lleva sufijo de estrategia: si lo llevara, cada bot
        miraria su propio fichero y el reparto no serviria de nada."""
        a = _bot(self.dir.name, "definitiva")
        b = _bot(self.dir.name, "elite")
        self.assertEqual(a._mint_claim_path(self.mint), b._mint_claim_path(self.mint))
        self.assertNotIn("definitiva", a._mint_claim_path(self.mint).name)

    def test_fallo_del_reparto_no_bloquea_la_compra(self):
        """Si el reclamo no se puede escribir, se compra igual.

        Es una optimizacion de reparto, no un filtro de seguridad: preferimos
        dos bots en la misma moneda antes que un bot que deja de operar porque
        el disco esta lleno.
        """
        a = _bot(self.dir.name, "definitiva")
        a._open_pos_path = Path("/no/existe/esta/ruta/open_position.json")
        self.assertTrue(a._reclamar_mint(self.mint))

    def test_cerrar_posicion_suelta_la_moneda(self):
        """El camino real: `_clear_open_position(mint)` libera el reclamo."""
        a = _bot(self.dir.name, "definitiva")
        b = _bot(self.dir.name, "elite")
        self.assertTrue(a._reclamar_mint(self.mint))
        a._clear_open_position(self.mint)
        self.assertTrue(b._reclamar_mint(self.mint))

    def test_arranque_no_pisa_reclamos_ajenos(self):
        """El borrado masivo del arranque NO toca reclamos.

        Son compartidos: si un bot que reinicia los borrara todos, soltaria las
        monedas que otro bot esta vigilando ahora mismo.
        """
        vivo = _bot(self.dir.name, "elite")
        self.assertTrue(vivo._reclamar_mint(self.mint))
        que_reinicia = _bot(self.dir.name, "definitiva")
        que_reinicia._clear_open_position(None)
        self.assertTrue(
            vivo._mint_claim_path(self.mint).exists(),
            "el reclamo del bot vivo tiene que seguir ahi",
        )

    def test_el_fichero_dice_quien_la_tiene(self):
        """Para depurar hace falta saber que bot se quedo la moneda."""
        a = _bot(self.dir.name, "definitiva")
        self.assertTrue(a._reclamar_mint(self.mint))
        rec = json.loads(a._mint_claim_path(self.mint).read_text(encoding="utf-8"))
        self.assertEqual(rec["bot"], "_definitiva")
        self.assertLessEqual(abs(rec["ts"] - time.time()), 5)

    def test_mint_raro_no_escapa_de_la_carpeta(self):
        """Un mint con barras no puede convertirse en una ruta de fuera."""
        a = _bot(self.dir.name, "definitiva")
        ruta = a._mint_claim_path("../../etc/passwd")
        self.assertEqual(ruta.parent, Path(self.dir.name))
        self.assertNotIn("/", ruta.name.replace("mint_claim__", ""))

    def test_no_deja_descriptores_abiertos(self):
        """Muchas reclamaciones seguidas no pueden agotar los descriptores."""
        a = _bot(self.dir.name, "definitiva")
        for i in range(200):
            a._reclamar_mint(f"moneda{i}pump")
        # Si `os.open` se quedara sin cerrar, esto reventaria mucho antes.
        self.assertEqual(len(list(Path(self.dir.name).glob("mint_claim__*.json"))), 200)


if __name__ == "__main__":
    unittest.main()
