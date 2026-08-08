"""Tests for on-chain volume-side counters used by live filters."""

import time
import unittest

from monitoring import volumen


class VolumeFilterTests(unittest.TestCase):
    mint = "BuyCounterMint"

    def tearDown(self):
        volumen._ops.pop(self.mint, None)

    def test_compras_excludes_sells(self):
        now = time.time()
        volumen._ops[self.mint] = [
            (now, 1.0, True, "wallet-a"),
            (now, 0.5, False, "wallet-a"),
            (now, 2.0, True, "wallet-b"),
        ]

        self.assertEqual(volumen.compras(self.mint), 2)
        self.assertEqual(volumen.operaciones(self.mint), 3)


if __name__ == "__main__":
    unittest.main()
