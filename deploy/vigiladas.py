"""Que posiciones esta vigilando el bot AHORA MISMO, y si la escalera va tarde.

PARA QUE SIRVE. Cuando una moneda sube por encima del primer take profit y no
se vende nada, hay exactamente tres explicaciones posibles y esto las separa
sin tener que leer un solo log:

  1. El bot NO la esta vigilando. Pasa si se reinicio y la posicion se quedo
     fuera, o si se abandono por falta de precio. Sin vigilancia no hay ni
     take profit ni stop loss: la moneda esta a pelo en la wallet.
  2. La vigila, pero el precio que ve NO es el que ves tu en la app. La
     escalera mide en SOL desde el precio REAL pagado (con su slippage
     dentro). Una app que mide en dolares sobre el market cap puede marcar
     +35% cuando en SOL van +28%.
  3. La vigila, el precio da, y aun asi no ha vendido. Eso si es un fallo y
     hay que mirar el log de esa moneda.

COMO LO DISTINGUE. Cada posicion vigilada deja un marcador (open_position_*.json)
con el precio de entrada y los tokens comprados. Comparando ese marcador con lo
que queda DE VERDAD en la cadena:

    tokens en la wallet == tokens comprados  ->  no se ha vendido nada
    tokens en la wallet <  tokens comprados  ->  la escalera ya cobro

Y una moneda con tokens en la wallet y SIN marcador es el caso 1: nadie la mira.

    uv run deploy/vigiladas.py

Solo LEE. No vende, no cierra cuentas, no toca el bot. La clave privada no se
imprime nunca.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import time
from pathlib import Path

import aiohttp
import yaml
from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey

LAMPORTS = 1_000_000_000
CONFIG = Path("bots") / "bot-paper-ai-entry.yaml"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # noqa: S105
#: Programa de pump.fun. Direccion publica y fija de la cadena.
PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
#: Minimo de bytes de la cuenta de curva para poder leer las dos reservas.
_CURVA_MINIMA = 48
#: Respuesta correcta de DexScreener.
_HTTP_OK = 200
#: Suministro de una moneda de pump.fun. Fijo para todas.
SUMINISTRO = 1_000_000_000
#: Margen para dar por "sin vender nada": las cantidades de la cadena y del
#: marcador pueden diferir en decimales por redondeo, no por una venta.
_IGUAL = 0.005
#: Si no se puede preguntar el precio del SOL, se usa este y se avisa.
_SOL_USD_POR_DEFECTO = 91.24


def _cfg() -> dict:
    try:
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _escalera(t: dict) -> list[tuple[float, float]]:
    """La escalera de take profit tal y como la lee el bot: (subida, vende)."""
    pares: list[tuple[float, float]] = []
    primero = (t.get("take_profit_percentage"), t.get("take_profit_sell_percentage"))
    if primero[0]:
        pares.append((float(primero[0]), float(primero[1] or 1.0)))
    n = 2
    while True:
        g, v = t.get(f"tp{n}_gain"), t.get(f"tp{n}_sell")
        if not g or not v:
            break
        pares.append((float(g), float(v)))
        n += 1
    # Los peldanos lejanos (x10, x20, x50, x100) viven en una lista aparte.
    for extra in t.get("tp_extra") or []:
        g, v = extra.get("gain"), extra.get("sell")
        if g and v:
            pares.append((float(g), float(v)))
    pares.sort(key=lambda par: par[0])
    return pares


def _keypair(clave: str) -> Keypair | None:
    try:
        if clave.startswith("["):
            return Keypair.from_bytes(bytes(json.loads(clave)))
        return Keypair.from_base58_string(clave)
    except Exception as exc:  # noqa: BLE001
        print(f"  La clave del .env no se pudo interpretar: {exc}")
        return None


def _marcadores() -> dict[str, dict]:
    """Lee los marcadores de posicion abierta, indexados por mint."""
    fuera: dict[str, dict] = {}
    for p in sorted(Path().glob("open_position*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mint = str(rec.get("mint") or "").strip()
        if mint:
            rec["_fichero"] = p.name
            fuera[mint] = rec
    return fuera


async def _precio_curva(client: AsyncClient, mint: str) -> float | None:
    """Precio en SOL leyendo la bonding curve. None si ya migro o no hay."""
    try:
        curva, _ = Pubkey.find_program_address(
            [b"bonding-curve", bytes(Pubkey.from_string(mint))], PUMP_PROGRAM
        )
        info = (await client.get_account_info(curva)).value
        if info is None or len(info.data) < _CURVA_MINIMA:
            return None
        # 8 bytes de discriminador y luego, en este orden:
        # virtual_token_reserves, virtual_sol_reserves, ...
        vtr, vsr = struct.unpack_from("<QQ", bytes(info.data), 8)
        if vtr <= 0:
            return None
        # Las reservas vienen en unidades minimas: 6 decimales el token, 9 el SOL.
        return (vsr / LAMPORTS) / (vtr / 10**6)
    except Exception:  # noqa: BLE001
        return None


async def _precio_dex(mint: str) -> float | None:
    """Precio en SOL desde DexScreener, para las que ya migraron."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with aiohttp.ClientSession() as s, s.get(url, timeout=10) as r:
            if r.status != _HTTP_OK:
                return None
            pares = (await r.json()).get("pairs") or []
        for par in pares:
            if (par.get("quoteToken") or {}).get("symbol") == "SOL":
                return float(par.get("priceNative") or 0) or None
    except Exception:  # noqa: BLE001
        return None
    return None


async def _sol_usd() -> float | None:
    try:
        url = "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
        async with aiohttp.ClientSession() as s, s.get(url, timeout=10) as r:
            if r.status != _HTTP_OK:
                return None
            pares = (await r.json()).get("pairs") or []
        for par in pares:
            precio = float(par.get("priceUsd") or 0)
            if precio > 0:
                return precio
    except Exception:  # noqa: BLE001
        return None
    return None


async def _cuentas(client: AsyncClient, duenyo: Pubkey) -> dict[str, float]:
    """Mint -> cantidad, de todo lo que hay en la wallet con saldo."""
    fuera: dict[str, float] = {}
    for programa in (TOKEN_PROGRAM, TOKEN_2022):
        try:
            resp = await client.get_token_accounts_by_owner_json_parsed(
                duenyo, {"programId": Pubkey.from_string(programa)}
            )
        except Exception:  # noqa: BLE001, S112
            continue
        for c in resp.value:
            info = c.account.data.parsed["info"]
            cantidad = float(info["tokenAmount"].get("uiAmount") or 0)
            if cantidad > 0:
                fuera[info["mint"]] = cantidad
    return fuera


def _deberia_haber_vendido(subida: float, escalera: list[tuple[float, float]]) -> float:
    """Fraccion de la compra que la escalera ya tendria que haber cobrado."""
    return sum(vende for gana, vende in escalera if subida >= gana)


def _pinta_vigilada(
    rec: dict, en_wallet: float, precio: float | None,
    escalera: list[tuple[float, float]], sol_usd: float,
) -> bool:
    """Imprime una posicion vigilada. Devuelve True si algo no cuadra."""
    sym = rec.get("symbol") or rec.get("mint", "")[:6]
    entrada = float(rec.get("entry_price") or 0)
    comprados = float(rec.get("amount") or 0)
    edad = time.time() - float(rec.get("ts") or 0)
    print(f"\n{sym}  {rec.get('mint')}")
    print(f"  VIGILADA (marcador {rec['_fichero']}, abierta hace {edad / 60:.0f} min)")
    print(f"  entrada   {entrada:.10f} SOL   (MC ${entrada * SUMINISTRO * sol_usd:,.0f})")

    if precio is None:
        print("  ahora     sin precio (ni en la curva ni en el DEX)")
        print("  *** OJO: si sigue asi, el bot la abandona a los 40 segundos.")
        return True

    subida = (precio / entrada - 1) if entrada > 0 else 0.0
    print(f"  ahora     {precio:.10f} SOL   (MC ${precio * SUMINISTRO * sol_usd:,.0f})"
          f"   {subida * 100:+.1f}%  <- esto es lo que mira la escalera")

    vendido = max(0.0, comprados - en_wallet)
    frac_vendida = (vendido / comprados) if comprados > 0 else 0.0
    toca = _deberia_haber_vendido(subida, escalera)
    print(f"  comprados {comprados:,.0f} tokens; en la wallet {en_wallet:,.0f}"
          f"  -> vendido el {frac_vendida * 100:.0f}%")

    if toca <= 0:
        print(f"  La escalera todavia no toca: el primer escalon es "
              f"+{escalera[0][0] * 100:.0f}%." if escalera else "  Sin escalera.")
        return False

    peldanyos = ", ".join(
        f"+{g * 100:.0f}% ({v * 100:.0f}%)" for g, v in escalera if subida >= g
    )
    print(f"  Ya toca cobrar: {peldanyos}  = {toca * 100:.0f}% de la compra")
    if frac_vendida + _IGUAL >= toca:
        print("  OK: la escalera ya ha cobrado lo que le toca.")
        return False
    print(f"  *** FALLO: deberia haber vendido el {toca * 100:.0f}% y solo va "
          f"por el {frac_vendida * 100:.0f}%.")
    print(f"  *** Mira el log de esta moneda:  journalctl -u pump-bot --no-pager "
          f"| grep -i '{sym}' | tail -40")
    return True


def main() -> None:  # noqa: C901, PLR0915
    """Cruza los marcadores del bot con lo que hay en la cadena."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(str(Path(".env").resolve()))

    clave = os.getenv("SOLANA_PRIVATE_KEY", "").strip()
    rpc = os.getenv("SOLANA_NODE_RPC_ENDPOINT", "").strip()
    if not clave or not rpc:
        print("Faltan SOLANA_PRIVATE_KEY o SOLANA_NODE_RPC_ENDPOINT en el .env.")
        return
    par = _keypair(clave)
    if par is None:
        return

    t = _cfg().get("trade", {})
    escalera = _escalera(t)
    marcadores = _marcadores()

    async def _todo() -> tuple[dict[str, float], dict[str, float | None], float]:
        client = AsyncClient(rpc)
        try:
            en_wallet = await _cuentas(client, par.pubkey())
            sol = await _sol_usd() or _SOL_USD_POR_DEFECTO
            precios: dict[str, float | None] = {}
            for mint in set(marcadores) | set(en_wallet):
                p = await _precio_curva(client, mint)
                precios[mint] = p if p else await _precio_dex(mint)
            return en_wallet, precios, sol
        finally:
            await client.close()

    try:
        en_wallet, precios, sol_usd = asyncio.run(_todo())
    except Exception as exc:  # noqa: BLE001
        print(f"No pude consultar el nodo: {exc}")
        return

    print("=" * 66)
    print(f"WALLET {par.pubkey()}   ·   SOL a ${sol_usd:,.2f}")
    print("=" * 66)
    if escalera:
        print("Escalera configurada: " + ", ".join(
            f"+{g * 100:.0f}% vende {v * 100:.0f}%" for g, v in escalera
        ))
    print(f"Stop loss: -{float(t.get('stop_loss_percentage') or 0) * 100:.0f}% "
          f"desde la entrada")

    print(f"\nVIGILADAS AHORA MISMO: {len(marcadores)} "
          f"(el tope son {t.get('max_posiciones')})")
    problemas = 0
    for mint, rec in marcadores.items():
        saldo = en_wallet.get(mint, 0.0)
        if saldo <= 0:
            sym = rec.get("symbol") or mint[:6]
            print(f"\n{sym}  {mint}")
            print("  Marcador puesto pero NO hay tokens en la wallet: ya se vendio "
                  "entera. El marcador se limpia solo al cerrar.")
            continue
        if _pinta_vigilada(rec, saldo, precios.get(mint), escalera, sol_usd):
            problemas += 1

    huerfanas = {m: c for m, c in en_wallet.items() if m not in marcadores}
    print(f"\n{'=' * 66}")
    print(f"EN LA WALLET SIN VIGILAR: {len(huerfanas)}")
    print("Estas NO tienen ni take profit ni stop loss. Pueden ser restos de")
    print("monedas ya cerradas (moonbag, polvo) o posiciones que se quedaron")
    print("fuera en un reinicio. Las que valgan algo, mirar una por una.")
    for mint, cantidad in sorted(huerfanas.items(), key=lambda x: -x[1])[:20]:
        precio = precios.get(mint)
        valor = f"${cantidad * precio * sol_usd:,.2f}" if precio else "sin precio"
        print(f"  {mint}  {cantidad:,.0f} tokens  ({valor})")

    print(f"\n{'=' * 66}")
    if problemas:
        print(f"HAY {problemas} POSICION(ES) EN LAS QUE LA ESCALERA VA TARDE.")
    else:
        print("Ninguna posicion vigilada tiene la escalera atrasada.")
    print("Esto solo lee. No vende nada.")


if __name__ == "__main__":
    main()
