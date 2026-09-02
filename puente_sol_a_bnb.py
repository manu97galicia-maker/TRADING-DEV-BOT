"""Pasa el SOL de la wallet del bot a BNB en BSC, via deBridge.

POR QUE PIDE LA ORDEN AL VUELO. La transaccion que devuelve deBridge lleva un
blockhash dentro y caduca en algo mas de un minuto. No sirve construirla antes
y firmarla despues: hay que pedirla y firmarla seguido, y por eso todo pasa
dentro de este script en vez de traerse una transaccion ya hecha.

Deja `RESERVA_SOL` sin tocar en la wallet: la propia transaccion del puente
paga comision y abre cuentas, y si se manda el saldo entero no queda con que
pagarlo.

    ./.venv/bin/python puente_sol_a_bnb.py            # solo enseña la cotizacion
    ./.venv/bin/python puente_sol_a_bnb.py --enviar   # firma y manda
"""

import base64
import json
import sys
import urllib.parse
import urllib.request

import base58
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

#: A donde llega el BNB, en BSC.
DESTINO_BSC = "0x9d02F13bab8fcAabbE431FB90AA286eCf20F0822"

#: Lo que se queda en la wallet para comisiones y rentas de cuenta.
RESERVA_SOL = 0.010

SOLANA = 7565164
BSC = 56
SOL_NATIVO = "11111111111111111111111111111111"
BNB_NATIVO = "0x0000000000000000000000000000000000000000"
DLN = "https://dln.debridge.finance/v1.0/dln/order/create-tx"


def env() -> dict:
    with open(".env", encoding="utf-8") as fh:
        return dict(
            l.strip().split("=", 1) for l in fh
            if "=" in l and not l.lstrip().startswith("#")
        )


def rpc(url: str, metodo: str, params: list):
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": metodo,
                         "params": params}).encode()
    req = urllib.request.Request(url, data=cuerpo,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30)).get("result")


def main() -> None:
    e = env()
    kp = Keypair.from_bytes(base58.b58decode(e["SOLANA_PRIVATE_KEY"].strip()))
    yo = str(kp.pubkey())
    url = e["SOLANA_NODE_RPC_ENDPOINT"].strip()

    saldo = rpc(url, "getBalance", [yo])["value"]
    manda = saldo - int(RESERVA_SOL * 1e9)
    print(f"wallet {yo}")
    print(f"saldo  {saldo / 1e9:.6f} SOL · se envian {manda / 1e9:.6f}, "
          f"se quedan {RESERVA_SOL} de reserva")
    if manda <= 0:
        print("No hay saldo suficiente por encima de la reserva.")
        return

    q = urllib.parse.urlencode({
        "srcChainId": SOLANA,
        "srcChainTokenIn": SOL_NATIVO,
        "srcChainTokenInAmount": manda,
        "dstChainId": BSC,
        "dstChainTokenOut": BNB_NATIVO,
        "dstChainTokenOutRecipient": DESTINO_BSC,
        "srcChainOrderAuthorityAddress": yo,
        "dstChainOrderAuthorityAddress": DESTINO_BSC,
        "senderAddress": yo,
    })
    d = json.load(urllib.request.urlopen(f"{DLN}?{q}", timeout=40))
    if d.get("errorId"):
        print("deBridge dice:", d.get("errorId"), "-", d.get("errorMessage"))
        return

    est = d["estimation"]
    entra = est["srcChainTokenIn"]
    sale = est["dstChainTokenOut"]
    print(f"\nentra  {int(entra['amount']) / 1e9:.6f} SOL "
          f"(~${entra.get('approximateUsdValue', 0):.2f})")
    print(f"sale   {int(sale['amount']) / 1e18:.6f} BNB "
          f"(~${sale.get('approximateUsdValue', 0):.2f})  ->  {DESTINO_BSC}")
    perdida = entra.get("approximateUsdValue", 0) - sale.get("approximateUsdValue", 0)
    print(f"coste del puente: ${perdida:.2f}")

    if "--enviar" not in sys.argv:
        print("\n(solo cotizacion; añade --enviar para firmarlo de verdad)")
        return

    crudo = d["tx"]["data"]
    if crudo.startswith("0x"):
        crudo = bytes.fromhex(crudo[2:])
    else:
        crudo = base64.b64decode(crudo)

    tx = VersionedTransaction.from_bytes(crudo)
    firmada = VersionedTransaction(tx.message, [kp])
    firma = rpc(url, "sendTransaction", [
        base64.b64encode(bytes(firmada)).decode(),
        {"encoding": "base64", "skipPreflight": False, "maxRetries": 3},
    ])
    if not firma:
        print("El RPC no devolvio firma; NO se ha enviado.")
        return
    print(f"\nENVIADO. firma {firma}")
    print(f"seguimiento: https://app.debridge.finance/orders?s={firma}")
    print(f"el BNB llega a {DESTINO_BSC} en ~1 minuto")


if __name__ == "__main__":
    main()
