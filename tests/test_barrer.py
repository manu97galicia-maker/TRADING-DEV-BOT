"""Tests del barredor: vender, reclamar la renta y mandar el SOL fuera.

Barrer mueve DINERO REAL y es irreversible, asi que lo que se comprueba aqui es
justo lo que no se puede probar dos veces en produccion: que el ensayo no firme
nada, que se mande exactamente lo que toca y a quien toca, que el wSOL no se
queme (el programa de tokens lo rechaza y tumbaria la transaccion entera) y que
una cuenta mala no se lleve por delante los cierres de las otras nueve.

No toca la red: se sustituyen el RPC, Jupiter y el envio.
"""

import asyncio
import base64
import contextlib
import io
import os
import struct
import sys
import types
import unittest
from pathlib import Path

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.null_signer import NullSigner
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

KP = Keypair()
os.environ.setdefault("SOLANA_NODE_RPC_ENDPOINT", "https://ejemplo.invalido")
os.environ["SOLANA_PRIVATE_KEY"] = str(KP)

import barrer  # noqa: E402

RENTA = 2_039_280
SALDO = 500_000_000  # 0,5 SOL
BLOCKHASH = "11111111111111111111111111111111"


def _pk() -> str:
    return str(Keypair().pubkey())


class BarridoTests(unittest.TestCase):
    def setUp(self):
        self.atas = {n: _pk() for n in ["buena", "polvo", "wsol", "vacia0", "vacia1"]}
        self.mints = {n: _pk() for n in ["bueno", "polvo", "vacio0", "vacio1"]}
        self.enviadas: list[VersionedTransaction] = []
        self.cotizados: list[str] = []
        self.rechazar_tandas = False

        cuentas = [
            self._cuenta("vacia0", "vacio0", 0),
            self._cuenta("vacia1", "vacio1", 0),
            self._cuenta("buena", "bueno", 1_000_000),
            self._cuenta("polvo", "polvo", 5),
            self._cuenta("wsol", None, 7, mint=barrer.SOL_MINT),
        ]

        async def rpc(_sesion, metodo, params):
            if metodo == "getTokenAccountsByOwner":
                es_spl = params[1]["programId"] == str(barrer.TOKEN_PROGRAM)
                return {"value": cuentas if es_spl else []}
            if metodo == "getBalance":
                return {"value": SALDO}
            if metodo == "getLatestBlockhash":
                return {"value": {"blockhash": BLOCKHASH}}
            raise AssertionError(f"RPC inesperado: {metodo}")

        async def cotizar(_sesion, mint, _raw):
            self.cotizados.append(mint)
            if mint == self.mints["bueno"]:
                return {"outAmount": str(int(0.05 * 1e9))}
            if mint == self.mints["polvo"]:
                return {"outAmount": "300"}  # no cubre ni la comision
            return None

        async def enviar(_sesion, tx):
            if self.rechazar_tandas and len(tx.message.instructions) > 1:
                return None
            self.enviadas.append(tx)
            return f"FIRMA{len(self.enviadas)}"

        async def blockhash(_sesion):
            return Hash.from_string(BLOCKHASH)

        # Sin esperas: en produccion dan tiempo al RPC a ver lo firmado, aqui
        # solo harian que la bateria tardase medio minuto.
        self._esperas = (barrer.ESPERA_TRAS_VENDER, barrer.ESPERA_ANTES_DE_ENVIAR)
        barrer.ESPERA_TRAS_VENDER = barrer.ESPERA_ANTES_DE_ENVIAR = 0
        self._originales = (barrer.rpc, barrer.cotizar, barrer.enviar, barrer.blockhash)
        barrer.rpc, barrer.cotizar, barrer.enviar, barrer.blockhash = (
            rpc, cotizar, enviar, blockhash
        )

        # Jupiter devuelve una transaccion de verdad, sin firmar.
        msg = MessageV0.try_compile(
            KP.pubkey(),
            [transfer(TransferParams(
                from_pubkey=KP.pubkey(), to_pubkey=KP.pubkey(), lamports=1
            ))],
            [],
            Hash.from_string(BLOCKHASH),
        )
        sin_firmar = base64.b64encode(
            bytes(VersionedTransaction(msg, [NullSigner(KP.pubkey())]))
        ).decode()

        prueba = self

        class Sesion:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def post(self, *_a, **_k):
                class Respuesta:
                    async def __aenter__(s):
                        return s

                    async def __aexit__(s, *_):
                        return False

                    async def json(s, **_k):
                        return {"swapTransaction": sin_firmar}

                return Respuesta()

        prueba._sesion_original = barrer.aiohttp.ClientSession
        barrer.aiohttp.ClientSession = lambda *a, **k: Sesion()

    def tearDown(self):
        barrer.rpc, barrer.cotizar, barrer.enviar, barrer.blockhash = self._originales
        barrer.aiohttp.ClientSession = self._sesion_original
        barrer.ESPERA_TRAS_VENDER, barrer.ESPERA_ANTES_DE_ENVIAR = self._esperas

    def _cuenta(self, ata, mint_nombre, raw, mint=None):
        return {
            "pubkey": self.atas[ata],
            "account": {
                "lamports": RENTA,
                "data": {"parsed": {"info": {
                    "mint": mint or self.mints[mint_nombre],
                    "tokenAmount": {
                        "amount": str(raw), "decimals": 6, "uiAmount": raw / 1e6
                    },
                }}},
            },
        }

    def _barrer(self, **kw):
        opciones = dict(
            destino=barrer.DESTINO, ejecutar=False, dejar=0.0,
            quemar=False, incluir_generadas=False,
        )
        opciones.update(kw)
        # El barredor informa por pantalla; en los tests solo estorba.
        with contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(barrer.barrer(types.SimpleNamespace(**opciones)))

    def _burns(self):
        """Cuentas sobre las que se emitio un Burn (instruccion 8 del token)."""
        cuentas = []
        for tx in self.enviadas:
            claves = tx.message.account_keys
            for ix in tx.message.instructions:
                programa = str(claves[ix.program_id_index])
                if programa == str(barrer.TOKEN_PROGRAM) and ix.data[0] == 8:
                    cuentas.append(str(claves[ix.accounts[0]]))
        return cuentas

    def _lamports_enviados(self, tx):
        # SystemInstruction::Transfer -> u32 de indice + u64 de lamports.
        return struct.unpack_from("<Q", bytes(tx.message.instructions[0].data), 4)[0]

    # -- ensayo -------------------------------------------------------------

    def test_el_ensayo_no_firma_nada(self):
        self._barrer()
        self.assertEqual(self.enviadas, [])

    def test_el_wsol_no_se_manda_a_jupiter(self):
        self._barrer()
        self.assertNotIn(barrer.SOL_MINT, self.cotizados)
        self.assertEqual(
            set(self.cotizados), {self.mints["bueno"], self.mints["polvo"]}
        )

    # -- ejecucion ----------------------------------------------------------

    def test_vende_cierra_en_tanda_y_envia(self):
        self._barrer(ejecutar=True)
        formas = [len(tx.message.instructions) for tx in self.enviadas]
        # venta + los 2 cierres de las cuentas vacias + el envio
        self.assertEqual(formas, [1, 2, 1])
        self.assertNotEqual(
            self.enviadas[0].signatures[0], Signature.default(),
            "la transaccion de Jupiter tiene que ir firmada",
        )

    def test_quemar_cierra_tambien_las_que_tienen_polvo(self):
        self._barrer(ejecutar=True, quemar=True)
        quemadas = self._burns()
        self.assertIn(self.atas["polvo"], quemadas)
        self.assertIn(self.atas["buena"], quemadas)
        self.assertNotIn(
            self.atas["wsol"], quemadas,
            "quemar wSOL revierte la transaccion entera: solo se cierra",
        )

    def test_envia_el_saldo_menos_el_colchon_al_destino(self):
        self._barrer(ejecutar=True)
        ultima = self.enviadas[-1]
        self.assertEqual(
            self._lamports_enviados(ultima), SALDO - barrer.COLCHON_LAMPORTS
        )
        claves = ultima.message.account_keys
        self.assertEqual(str(claves[0]), str(KP.pubkey()))
        self.assertEqual(str(claves[1]), barrer.DESTINO)

    def test_dejar_reserva_ese_saldo(self):
        self._barrer(ejecutar=True, dejar=0.1)
        self.assertEqual(
            self._lamports_enviados(self.enviadas[-1]),
            SALDO - int(0.1 * 1e9) - barrer.COLCHON_LAMPORTS,
        )

    def test_una_tanda_rechazada_se_reintenta_cuenta_a_cuenta(self):
        self.rechazar_tandas = True
        self._barrer(ejecutar=True)
        # Con la tanda rechazada, cada cuenta vacia se cierra por su cuenta:
        # quedan la venta, los dos cierres sueltos y el envio.
        formas = [len(tx.message.instructions) for tx in self.enviadas]
        self.assertEqual(formas, [1, 1, 1, 1])


class CargarClavesTests(unittest.TestCase):
    """El formato que lee tiene que ser el que escribe crear_wallets.py."""

    def test_lee_el_fichero_de_crear_wallets(self):
        import json
        import tempfile

        extra = Keypair()
        with tempfile.TemporaryDirectory() as carpeta:
            (Path(carpeta) / "wallets_generadas.json").write_text(json.dumps({
                "creadas": "2026-08-30 10:00 UTC",
                "wallets": [
                    {"n": 1, "address": str(extra.pubkey()), "private_key": str(extra)}
                ],
            }), encoding="utf-8")
            original, barrer.AQUI = barrer.AQUI, Path(carpeta)
            try:
                solo_principal = barrer.cargar_claves(incluir_generadas=False)
                con_generadas = barrer.cargar_claves(incluir_generadas=True)
            finally:
                barrer.AQUI = original

        self.assertEqual([str(k.pubkey()) for k in solo_principal], [str(KP.pubkey())])
        self.assertEqual(
            [str(k.pubkey()) for k in con_generadas],
            [str(KP.pubkey()), str(extra.pubkey())],
        )


if __name__ == "__main__":
    unittest.main()
