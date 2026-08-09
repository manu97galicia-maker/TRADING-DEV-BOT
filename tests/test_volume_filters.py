"""Tests for on-chain volume-side counters used by live filters."""

import base64
import struct
import time
import unittest

from monitoring import volumen


class VolumeFilterTests(unittest.TestCase):
    mint = "BuyCounterMint"

    def tearDown(self):
        volumen._ops.pop(self.mint, None)
        volumen._first_slot.pop(self.mint, None)
        volumen._first_slot_buys.pop(self.mint, None)

    def test_compras_excludes_sells(self):
        now = time.time()
        volumen._ops[self.mint] = [
            (now, 1.0, True, "wallet-a"),
            (now, 0.5, False, "wallet-a"),
            (now, 2.0, True, "wallet-b"),
        ]

        self.assertEqual(volumen.compras(self.mint), 2)
        self.assertEqual(volumen.operaciones(self.mint), 3)

    def test_compras_primer_bloque_only_counts_first_slot(self):
        raw_mint = bytes(range(1, 33))
        mint = volumen._b58(raw_mint)
        wallet = bytes(range(33, 65))

        def trade_log(is_buy: bool = True) -> str:
            body = (
                raw_mint
                + struct.pack("<Q", 1_000_000_000)
                + struct.pack("<Q", 100)
                + bytes([is_buy])
                + wallet
            )
            encoded = base64.b64encode(volumen.DISC_TRADE + body).decode()
            return f"Program data: {encoded}"

        volumen.registrar_bloque_inicial(mint, 123)
        volumen.registrar_logs([trade_log()] * 4, slot=123)
        volumen.registrar_logs([trade_log()], slot=124)

        self.assertEqual(volumen.compras_primer_bloque(mint), 4)
        self.assertEqual(volumen.compras(mint), 5)

        volumen._ops.pop(mint, None)
        volumen._first_slot.pop(mint, None)
        volumen._first_slot_buys.pop(mint, None)

    def test_creation_slot_without_buy_does_not_use_next_block(self):
        volumen.registrar_bloque_inicial(self.mint, 123)
        volumen._first_slot_buys[self.mint] = 0

        self.assertEqual(volumen.compras_primer_bloque(self.mint), 0)

    def test_second_wave_metrics_exclude_creator_from_recent_demand(self):
        volumen._ops[self.mint] = [
            (100.0, 3.0, True, "creator"),
            (101.0, 1.0, True, "buyer-a"),
            (104.0, 2.0, False, "creator"),
            (105.2, 3.5, True, "buyer-b"),
            (105.4, 2.0, True, "buyer-c"),
            (105.5, 1.0, False, "seller"),
            (105.6, 9.0, True, "creator"),
        ]

        metrics = volumen.metricas_segunda_ola(
            self.mint, ventana=1.0, creador="creator", ahora=106.0
        )

        self.assertAlmostEqual(metrics["edad"], 6.0)
        self.assertEqual(metrics["compras"], 5)
        self.assertEqual(metrics["compradores"], 3)
        self.assertAlmostEqual(metrics["neto_sol"], 15.5)
        self.assertAlmostEqual(metrics["ola_compra_sol"], 5.5)
        self.assertAlmostEqual(metrics["ola_venta_sol"], 1.0)
        self.assertAlmostEqual(metrics["ola_neto_sol"], 4.5)
        self.assertEqual(metrics["ola_compradores"], 2)
        self.assertAlmostEqual(metrics["mayor_compra_sol"], 3.5)
        self.assertAlmostEqual(metrics["venta_creador_sol"], 2.0)


if __name__ == "__main__":
    unittest.main()
