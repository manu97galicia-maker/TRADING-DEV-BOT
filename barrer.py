"""Barre las carteras: vende lo que quede, recupera la renta y manda el SOL fuera.

Hace las tres cosas, en este orden y por cada wallet:

  1. VENDER. Busca TODOS los tokens que tenga la cartera (SPL y Token-2022) y
     los vende por SOL con Jupiter, al precio que haya.
  2. RECLAMAR LA RENTA. Cada cuenta de token que abrio el bot tiene ~0,00204
     SOL inmovilizados (el "alquiler" que Solana exige). Al cerrarla vuelven.
     Con muchas monedas operadas esto no es calderilla: 200 cuentas son 0,4 SOL.
  3. ENVIAR. Lo que queda de SOL nativo se manda a la direccion de destino,
     dejando solo el colchon de la comision.

Uso:
    python barrer.py                       # ENSAYO: dice que haria, no firma nada
    python barrer.py --ejecutar            # lo hace de verdad
    python barrer.py --ejecutar --quemar   # ademas quema el polvo invendible
                                           # para poder cerrar tambien su cuenta
    python barrer.py --incluir-generadas   # barre tambien wallets_generadas.json
    python barrer.py --destino <pubkey>    # otro destino
    python barrer.py --dejar 0.02          # deja ese SOL en cada cartera

ESTO NO SE PUEDE DESHACER. Una transferencia de Solana es definitiva: no hay
soporte al que llamar ni forma de revertirla. Por eso, por defecto, el script
NO FIRMA NADA: enseña la lista de lo que vendería, lo que cerraría y cuanto
mandaría, y hay que volver a llamarlo con --ejecutar.

ANTES DE EJECUTAR, PARA LOS BOTS. Si un bot esta operando con esta misma
cartera mientras el barrido corre, los dos se pisan: el bot puede estar
vendiendo la misma bolsa (una de las dos ventas falla y paga comision), abrir
posiciones nuevas justo detras del barrido, o quedarse sin SOL para firmar la
venta de algo que tenia abierto. El script lo comprueba solo -- si encuentra un
bot_runner vivo se planta -- y se puede forzar con --aunque-corra.

    systemctl stop sniper-bot        # o el nombre real de la unidad
    python barrer.py --ejecutar
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv

AQUI = Path(__file__).parent
load_dotenv(AQUI / ".env")

from solders.hash import Hash  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.message import MessageV0  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.system_program import TransferParams, transfer  # noqa: E402
from solders.transaction import VersionedTransaction  # noqa: E402
from spl.token.instructions import burn, close_account  # noqa: E402

try:  # solana-py <= 0.36, que es la que pinea el bot
    from spl.token.instructions import BurnParams, CloseAccountParams
except ImportError:  # 0.37+ movio los parametros a spl.token.models
    from spl.token.models import BurnParams, CloseAccountParams

LAMPORTS = 1_000_000_000
SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")

#: Destino que pidio Manu. Se puede cambiar con --destino.
DESTINO = "94ZSpiQKTZAwy8C8jvuE4kvjsUuHUmp3XDQA1pAzYcU3"

RPC = os.getenv("SOLANA_NODE_RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")
JUPITER = os.getenv("JUPITER_API", "https://lite-api.jup.ag")

#: Margen de slippage al vender restos. Alto a proposito: aqui no se busca el
#: mejor precio, se busca SALIR. Una bolsa muerta con margen corto no se vende y
#: se queda dentro, que es justo lo que se intenta evitar.
SLIPPAGE_BPS = int(os.getenv("BARRIDO_SLIPPAGE_BPS", "3000"))
#: Por debajo de esto no merece la pena firmar: la comision se come la venta.
#: La cuenta se cierra igual y se recupera su renta, que suele valer mas.
MIN_VENTA_SOL = float(os.getenv("BARRIDO_MIN_VENTA_SOL", "0.0008"))
#: Cuantos cierres caben comodos en una transaccion.
CIERRES_POR_TX = 10
#: Colchon para la comision del envio final.
COLCHON_LAMPORTS = 10_000
#: Pausas para que el RPC se entere de lo que se acaba de firmar antes de
#: releer saldos. Constantes para poder ponerlas a cero en los tests.
ESPERA_TRAS_VENDER = 3
ESPERA_ANTES_DE_ENVIAR = 2


# --------------------------------------------------------------------------- #
# RPC                                                                          #
# --------------------------------------------------------------------------- #


async def rpc(sesion: aiohttp.ClientSession, metodo: str, params: list) -> dict | None:
    """Una llamada al RPC. Devuelve el `result`, o None si el nodo da error."""
    async with sesion.post(
        RPC,
        json={"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as r:
        cuerpo = await r.json(content_type=None)
    if "error" in cuerpo:
        print(f"    ! RPC {metodo}: {cuerpo['error'].get('message', cuerpo['error'])}")
        return None
    return cuerpo.get("result")


async def enviar(sesion: aiohttp.ClientSession, tx: VersionedTransaction) -> str | None:
    """Manda una transaccion firmada y espera a que la red la confirme."""
    firma = await rpc(
        sesion,
        "sendTransaction",
        [
            base64.b64encode(bytes(tx)).decode(),
            {"encoding": "base64", "skipPreflight": True, "maxRetries": 3},
        ],
    )
    if not firma:
        return None
    # Confirmar de verdad, no solo mandar: una firma devuelta no significa que
    # la transaccion haya entrado. Sin esta espera, el resumen final mentiria.
    limite = time.time() + 45
    while time.time() < limite:
        await asyncio.sleep(1.5)
        estado = await rpc(sesion, "getSignatureStatuses", [[firma], {"searchTransactionHistory": True}])
        valor = ((estado or {}).get("value") or [None])[0]
        if not valor:
            continue
        if valor.get("err"):
            print(f"    ! la transaccion {firma[:12]}… fallo en la cadena: {valor['err']}")
            return None
        if valor.get("confirmationStatus") in ("confirmed", "finalized"):
            return str(firma)
    print(f"    ! sin confirmacion de {firma[:12]}… en 45 s (puede entrar mas tarde)")
    return None


async def blockhash(sesion: aiohttp.ClientSession) -> Hash:
    r = await rpc(sesion, "getLatestBlockhash", [{"commitment": "finalized"}])
    return Hash.from_string(r["value"]["blockhash"])


# --------------------------------------------------------------------------- #
# Claves                                                                       #
# --------------------------------------------------------------------------- #


def _keypair(crudo: str) -> Keypair:
    crudo = crudo.strip()
    if crudo.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(crudo)))
    return Keypair.from_base58_string(crudo)


def cargar_claves(*, incluir_generadas: bool) -> list[Keypair]:
    """La clave del bot (.env) y, si se pide, las de wallets_generadas.json."""
    claves: list[Keypair] = []
    vistas: set[str] = set()

    principal = os.getenv("SOLANA_PRIVATE_KEY", "").strip()
    if principal:
        kp = _keypair(principal)
        claves.append(kp)
        vistas.add(str(kp.pubkey()))
    else:
        print("! No hay SOLANA_PRIVATE_KEY en el .env: no puedo barrer la principal.")

    fichero = AQUI / "wallets_generadas.json"
    if incluir_generadas and fichero.exists():
        datos = json.loads(fichero.read_text(encoding="utf-8"))
        entradas = datos if isinstance(datos, list) else datos.get("wallets", [])
        for entrada in entradas:
            secreto = entrada.get("secreto") or entrada.get("private_key") or entrada.get("clave")
            if not secreto:
                continue
            kp = _keypair(secreto)
            if str(kp.pubkey()) not in vistas:
                claves.append(kp)
                vistas.add(str(kp.pubkey()))
    elif incluir_generadas:
        print(f"! No existe {fichero.name}; solo se barre la cartera principal.")
    return claves


# --------------------------------------------------------------------------- #
# Inventario                                                                   #
# --------------------------------------------------------------------------- #


async def inventario(sesion: aiohttp.ClientSession, duenyo: str) -> list[dict]:
    """Todas las cuentas de token de esa cartera, de los dos programas."""
    cuentas: list[dict] = []
    for programa in (TOKEN_PROGRAM, TOKEN_2022):
        r = await rpc(
            sesion,
            "getTokenAccountsByOwner",
            [duenyo, {"programId": str(programa)}, {"encoding": "jsonParsed"}],
        )
        for c in (r or {}).get("value", []):
            info = c["account"]["data"]["parsed"]["info"]
            cantidad = info["tokenAmount"]
            cuentas.append({
                "ata": c["pubkey"],
                "mint": info["mint"],
                "raw": int(cantidad["amount"]),
                "decimales": int(cantidad["decimals"]),
                "ui": float(cantidad["uiAmount"] or 0),
                "programa": programa,
                # La renta real de ESTA cuenta, leida de la cadena. No se estima:
                # Token-2022 con extensiones ocupa mas y paga mas.
                "renta": int(c["account"]["lamports"]),
            })
    return cuentas


# --------------------------------------------------------------------------- #
# 1) Vender                                                                    #
# --------------------------------------------------------------------------- #


async def cotizar(sesion: aiohttp.ClientSession, mint: str, raw: int) -> dict | None:
    url = (
        f"{JUPITER}/swap/v1/quote?inputMint={mint}&outputMint={SOL_MINT}"
        f"&amount={raw}&slippageBps={SLIPPAGE_BPS}&swapMode=ExactIn"
    )
    try:
        async with sesion.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            datos = await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError):
        return None
    return datos if isinstance(datos, dict) and "outAmount" in datos else None


async def vender(
    sesion: aiohttp.ClientSession, kp: Keypair, cuenta: dict, *, ejecutar: bool
) -> float:
    """Vende esa bolsa por SOL. Devuelve el SOL esperado (o 0 si no se vende)."""
    cot = await cotizar(sesion, cuenta["mint"], cuenta["raw"])
    if cot is None:
        print(f"    · {cuenta['mint'][:8]}… sin ruta en Jupiter (sin migrar o muerta)")
        return 0.0
    sol = int(cot.get("outAmount", 0)) / LAMPORTS
    if sol < MIN_VENTA_SOL:
        print(f"    · {cuenta['mint'][:8]}… vale {sol:.6f} SOL: no compensa la comision")
        return 0.0
    print(f"    · {cuenta['mint'][:8]}… {cuenta['ui']:.4g} uds -> ~{sol:.5f} SOL")
    if not ejecutar:
        return sol
    async with sesion.post(
        f"{JUPITER}/swap/v1/swap",
        json={
            "quoteResponse": cot,
            "userPublicKey": str(kp.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": 100_000,
        },
        timeout=aiohttp.ClientTimeout(total=20),
    ) as r:
        datos = await r.json(content_type=None)
    tx_b64 = datos.get("swapTransaction") if isinstance(datos, dict) else None
    if not tx_b64:
        print(f"    ! Jupiter no devolvio transaccion para {cuenta['mint'][:8]}…")
        return 0.0
    sin_firmar = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    firma = await enviar(sesion, VersionedTransaction(sin_firmar.message, [kp]))
    if not firma:
        return 0.0
    print(f"      vendida: https://solscan.io/tx/{firma}")
    return sol


# --------------------------------------------------------------------------- #
# 2) Reclamar la renta                                                         #
# --------------------------------------------------------------------------- #


async def cerrar_cuentas(
    sesion: aiohttp.ClientSession,
    kp: Keypair,
    cuentas: list[dict],
    *,
    quemar: bool,
    ejecutar: bool,
) -> float:
    """Cierra las cuentas vacias (y con --quemar, tambien las que tengan polvo).

    Devuelve el SOL de renta recuperado. Los cierres van en tandas: cada uno es
    una instruccion pequena y meter varias en la misma transaccion ahorra tantas
    comisiones como cuentas haya.
    """
    cerrables = [c for c in cuentas if c["raw"] == 0 or quemar]
    if not cerrables:
        return 0.0
    recuperado = sum(c["renta"] for c in cerrables) / LAMPORTS
    con_polvo = sum(1 for c in cerrables if c["raw"] > 0)
    print(
        f"    cierro {len(cerrables)} cuentas"
        + (f" ({con_polvo} quemando el polvo)" if con_polvo else "")
        + f" -> +{recuperado:.5f} SOL de renta"
    )
    if not ejecutar:
        return recuperado

    logrado = 0.0
    for i in range(0, len(cerrables), CIERRES_POR_TX):
        tanda = cerrables[i : i + CIERRES_POR_TX]
        firma = await _cerrar_tanda(sesion, kp, tanda)
        if firma:
            logrado += sum(c["renta"] for c in tanda) / LAMPORTS
            print(f"      cerradas {len(tanda)}: https://solscan.io/tx/{firma}")
            continue
        # UNA CUENTA MALA NO PUEDE LLEVARSE A LAS OTRAS NUEVE. Basta un
        # Token-2022 con extensiones raras para que la transaccion entera
        # revierta; entonces se reintenta cuenta a cuenta y solo se pierde esa.
        if len(tanda) == 1:
            print(f"      ! no pude cerrar {tanda[0]['ata'][:8]}…")
            continue
        print(f"      la tanda de {len(tanda)} fallo; lo intento una a una")
        for cuenta in tanda:
            firma = await _cerrar_tanda(sesion, kp, [cuenta])
            if firma:
                logrado += cuenta["renta"] / LAMPORTS
            else:
                print(f"      ! no pude cerrar {cuenta['ata'][:8]}…")
    return logrado


async def _cerrar_tanda(
    sesion: aiohttp.ClientSession, kp: Keypair, tanda: list[dict]
) -> str | None:
    """Quema (si hace falta) y cierra estas cuentas en una sola transaccion."""
    ixs = []
    for c in tanda:
        ata = Pubkey.from_string(c["ata"])
        mint = Pubkey.from_string(c["mint"])
        # El wSOL NO se quema: el programa de tokens rechaza `burn` sobre una
        # cuenta nativa y con eso reventaria la transaccion entera, cierres
        # ajenos incluidos. Cerrarla ya devuelve el SOL que lleva dentro.
        if c["raw"] > 0 and c["mint"] != SOL_MINT:
            ixs.append(burn(BurnParams(
                account=ata, mint=mint, owner=kp.pubkey(),
                amount=c["raw"], program_id=c["programa"],
            )))
        ixs.append(close_account(CloseAccountParams(
            account=ata, dest=kp.pubkey(), owner=kp.pubkey(),
            program_id=c["programa"],
        )))
    msg = MessageV0.try_compile(kp.pubkey(), ixs, [], await blockhash(sesion))
    return await enviar(sesion, VersionedTransaction(msg, [kp]))


# --------------------------------------------------------------------------- #
# 3) Enviar                                                                    #
# --------------------------------------------------------------------------- #


async def enviar_sol(
    sesion: aiohttp.ClientSession,
    kp: Keypair,
    destino: Pubkey,
    dejar: float,
    *,
    ejecutar: bool,
) -> float:
    saldo = ((await rpc(sesion, "getBalance", [str(kp.pubkey())])) or {}).get("value", 0)
    enviable = saldo - int(dejar * LAMPORTS) - COLCHON_LAMPORTS
    if enviable <= 0:
        print(f"    saldo {saldo / LAMPORTS:.6f} SOL: no hay nada que mandar")
        return 0.0
    print(f"    envio {enviable / LAMPORTS:.6f} SOL -> {destino}")
    if not ejecutar:
        return enviable / LAMPORTS
    ix = transfer(TransferParams(
        from_pubkey=kp.pubkey(), to_pubkey=destino, lamports=enviable
    ))
    msg = MessageV0.try_compile(kp.pubkey(), [ix], [], await blockhash(sesion))
    firma = await enviar(sesion, VersionedTransaction(msg, [kp]))
    if not firma:
        return 0.0
    print(f"      enviado: https://solscan.io/tx/{firma}")
    return enviable / LAMPORTS


# --------------------------------------------------------------------------- #
# Guardia: no barrer con los bots operando                                     #
# --------------------------------------------------------------------------- #


def bot_vivo() -> int | None:
    """PID del bot_runner que este corriendo, si lo hay (lo deja en .bot.pid)."""
    fichero = AQUI / ".bot.pid"
    try:
        pid = int(fichero.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    except OSError:
        return None
    return pid


# --------------------------------------------------------------------------- #


async def barrer(args: argparse.Namespace) -> None:
    destino = Pubkey.from_string(args.destino)
    claves = cargar_claves(incluir_generadas=args.incluir_generadas)
    if not claves:
        print("No hay ninguna clave que barrer.")
        return

    print("EJECUTANDO DE VERDAD" if args.ejecutar else "ENSAYO (no se firma nada)")
    print(f"Destino: {destino}")
    print(f"Carteras: {len(claves)}\n")

    total_ventas = total_renta = total_enviado = 0.0
    async with aiohttp.ClientSession() as sesion:
        for kp in claves:
            duenyo = str(kp.pubkey())
            print(f"  {duenyo}")
            cuentas = await inventario(sesion, duenyo)
            # El wSOL no se vende: cerrar su cuenta ya devuelve el SOL de dentro.
            con_saldo = [c for c in cuentas if c["raw"] > 0 and c["mint"] != SOL_MINT]
            print(f"    {len(cuentas)} cuentas de token, {len(con_saldo)} con saldo")

            for cuenta in con_saldo:
                total_ventas += await vender(sesion, kp, cuenta, ejecutar=args.ejecutar)

            # Se relee: despues de vender, las cuentas se quedan a cero y ya son
            # cerrables. Sin releer se cerrarian solo las que ya estaban vacias.
            if args.ejecutar and con_saldo:
                await asyncio.sleep(ESPERA_TRAS_VENDER)
                cuentas = await inventario(sesion, duenyo)
            total_renta += await cerrar_cuentas(
                sesion, kp, cuentas, quemar=args.quemar, ejecutar=args.ejecutar
            )

            if args.ejecutar:
                await asyncio.sleep(ESPERA_ANTES_DE_ENVIAR)
            total_enviado += await enviar_sol(
                sesion, kp, destino, args.dejar, ejecutar=args.ejecutar
            )
            print()

    print("─" * 60)
    verbo = "" if args.ejecutar else " (estimado, no se ha firmado nada)"
    print(f"Ventas:        {total_ventas:.5f} SOL{verbo}")
    print(f"Renta:        +{total_renta:.5f} SOL{verbo}")
    print(f"Enviado:       {total_enviado:.5f} SOL{verbo}")
    if not args.ejecutar:
        print(
            "\nOjo con la renta del ensayo: solo cuenta las cuentas que YA estan\n"
            "vacias. Al ejecutar, las de lo que se venda tambien se cierran, asi\n"
            "que la cifra real sera mayor."
        )
        print("\nPara hacerlo de verdad: python barrer.py --ejecutar")


def main() -> None:
    p = argparse.ArgumentParser(description="Vende, recupera la renta y manda el SOL fuera.")
    p.add_argument("--ejecutar", action="store_true", help="firma y manda de verdad")
    p.add_argument("--destino", default=DESTINO, help="direccion que recibe el SOL")
    p.add_argument("--dejar", type=float, default=0.0, help="SOL a dejar en cada cartera")
    p.add_argument("--quemar", action="store_true",
                   help="quema el polvo invendible para poder cerrar tambien su cuenta")
    p.add_argument("--incluir-generadas", action="store_true",
                   help="barre tambien las wallets de wallets_generadas.json")
    p.add_argument("--aunque-corra", action="store_true",
                   help="barre aunque haya un bot operando (no recomendado)")
    args = p.parse_args()

    pid = bot_vivo()
    if pid and args.ejecutar and not args.aunque_corra:
        print(
            f"Hay un bot_runner vivo (PID {pid}) usando esta cartera.\n"
            "Parar primero: systemctl stop <unidad> (o matar el proceso).\n"
            "Si de verdad quieres barrer con el operando: --aunque-corra"
        )
        raise SystemExit(1)

    asyncio.run(barrer(args))


if __name__ == "__main__":
    main()
