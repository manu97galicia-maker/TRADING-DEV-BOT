"""Build a TokenInfo for an already-existing mint (not a fresh creation event).

Needed by the buy-on-dex-paid flow: the dex-paid feed gives a mint, and to buy
it through the normal buyer we must reconstruct the same TokenInfo the new-token
listeners build. For pump.fun that means deriving the bonding curve, reading the
curve account for the creator + mayhem/cashback flags, deriving the associated
bonding curve and creator vault, and detecting the actual token program.

IMPORTANT: enforces "not migrated" — if the bonding curve is complete (token
moved to PumpSwap), returns None so we never try a bonding-curve buy on a
migrated token.

WARNING: this path (buying an arbitrary existing mint) has NOT been verified
against a live buy in this workspace. Test with a tiny buy_amount first.
"""

from __future__ import annotations

import asyncio
from time import monotonic

from solders.pubkey import Pubkey

from core.pubkeys import TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from interfaces.core import Platform, TokenInfo
from trading.dev_entry_reads import remember_mint_program
from utils.logger import get_logger

logger = get_logger(__name__)


async def build_pumpfun_token_info(  # noqa: C901, PLR0911, PLR0913 -- independent account rejection paths
    mint_str: str,
    platform_impls: object,
    client: object,
    symbol: str = "",
    name: str = "",
    *,
    permitir_migradas: bool = False,
) -> TokenInfo | None:
    """Reconstruct a pump.fun TokenInfo for an existing mint.

    Args:
        mint_str: Token mint address.
        platform_impls: Platform implementations (address_provider,
            curve_manager) for pump.fun.
        client: SolanaClient for the mint-owner lookup.
        symbol: Optional symbol for logs.
        name: Optional name.

    Returns:
        A TokenInfo ready for the buyer, or None if the mint is invalid,
        migrated (curve complete), or the curve can't be read.
    """
    try:
        mint = Pubkey.from_string(mint_str)
    except (ValueError, TypeError):
        logger.warning("Invalid mint for dex-paid buy: %s", mint_str)
        return None

    address_provider = platform_impls.address_provider
    curve_manager = platform_impls.curve_manager
    bonding_curve = address_provider.derive_pool_address(mint)

    # Una curva completa significa que la moneda migro a PumpSwap. Por
    # defecto se rechaza, porque el comprador normal entra POR la curva y ahi
    # ya no hay nada que comprar. Con `permitir_migradas` se deja pasar y se
    # marca, para que quien compre enrute por el pool en vez de por la curva.
    # UNA SOLA VUELTA AL NODO PARA LAS DOS COSAS, y las dos a la vez.
    #
    # Esto estaba en el camino critico entre detectar la moneda y comprarla, y
    # eran TRES viajes seguidos donde basta uno:
    #
    #   1. `is_curve_complete()` -- que por dentro no hace otra cosa que
    #      llamar a `get_pool_state()`.
    #   2. `get_pool_state()` otra vez, leyendo LA MISMA cuenta que acababa de
    #      leer la anterior.
    #   3. `get_account_info(mint)`, que no depende de las otras dos y aun asi
    #      esperaba a que terminaran.
    #
    # Ahora la curva se lee una vez y a la vez que el mint. El estado trae ya
    # el campo `complete`, que es exactamente lo que miraba el paso 1.
    #
    # `exigir_precio=False` para poder mirar `complete` sin que una curva
    # vacia (migrada) lance antes de llegar a la comprobacion. Con reservas
    # normales el precio se calcula igual que siempre, asi que no se pierde
    # nada; lo unico que cambia es que un estado ilegible se detecta abajo, a
    # mano, en vez de por excepcion.
    #
    # Leer ambas cuentas con `processed`, como el resto del camino de compra.
    # El cliente puede usar `finalized` por defecto: un mint recien creado
    # puede faltar ahi aunque su curva ya sea visible con `processed`.
    mint_read_started = monotonic()
    try:
        state, mint_acct = await asyncio.gather(
            curve_manager.get_pool_state(
                bonding_curve, commitment="processed", exigir_precio=False
            ),
            client.get_account_info(mint, commitment="processed"),
        )
    except Exception:  # noqa: BLE001 -- unreadable curve => can't safely buy
        logger.warning("Could not read curve state for %s; skipping", mint_str)
        return None

    migrada = bool(state.get("complete", False))
    if migrada and not permitir_migradas:
        logger.info("Skip %s (%s): bonding curve complete (migrated)", symbol, mint_str)
        return None

    # UNA CURVA SIN PRECIO QUE NO ESTA MIGRADA ES ILEGIBLE, y antes lo decia
    # la excepcion de `exigir_precio=True`. Ahora se dice aqui: comprar por
    # una curva cuyo precio no se puede calcular es comprar a ciegas.
    if not migrada and not state.get("price_per_token"):
        logger.warning("Could not read curve state for %s; skipping", mint_str)
        return None

    creator_raw = state.get("creator")
    creator = None
    if creator_raw:
        try:
            creator = (
                creator_raw
                if isinstance(creator_raw, Pubkey)
                else Pubkey.from_string(str(creator_raw))
            )
        except (ValueError, TypeError):
            creator = None

    # Determine the actual token program (owner of the mint account). Do NOT
    # guess: a wrong program derives the wrong ATA and the buy fails. If the
    # mint account can't be read, skip this candidate.
    token_program_id = None
    if mint_acct and getattr(mint_acct, "owner", None):
        token_program_id = mint_acct.owner
    if token_program_id is None:
        logger.warning(
            "Could not read token program for %s; skipping (won't guess)", mint_str
        )
        return None
    if str(token_program_id) in {str(TOKEN_PROGRAM), str(TOKEN_2022_PROGRAM)}:
        remember_mint_program(mint, token_program_id, now=mint_read_started)

    token_info = TokenInfo(
        name=name or symbol or str(mint),
        symbol=symbol or "?",
        uri="",
        mint=mint,
        platform=Platform.PUMP_FUN,
        bonding_curve=bonding_curve,
        user=creator,
        creator=creator,
        token_program_id=token_program_id,
        is_mayhem_mode=bool(state.get("is_mayhem_mode", False)),
        is_cashback_coin=bool(state.get("is_cashback_coin", False)),
        # EL PRECIO QUE ACABA DE LEERSE, para que el comprador no vuelva a
        # pedir LA MISMA cuenta unos milisegundos despues. Era un viaje entero
        # al nodo en el camino critico entre detectar la moneda y firmar la
        # compra, duplicando el que se hace tres lineas mas arriba.
        #
        # Lleva la hora: el comprador solo lo usa si sigue fresco, y si no, lo
        # relee como siempre. Las banderas mayhem/cashback ya van puestas en
        # este mismo TokenInfo, asi que no se pierde nada al saltarse la
        # segunda lectura.
        additional_data={
            "precio_curva": float(state.get("price_per_token") or 0.0),
            "precio_curva_en": monotonic(),
        },
    )

    if migrada:
        # El comprador y el monitor de precio consultan esta marca: la curva
        # sigue existiendo pero esta vacia, asi que calcular precio con ella
        # lanza y hay que ir al DEX.
        token_info.additional_data = {
            **(token_info.additional_data or {}),
            "migrated": True,
        }

    # Derive associated bonding curve + creator vault.
    extra = address_provider.get_additional_accounts(token_info)
    token_info.associated_bonding_curve = extra.get("associated_bonding_curve")
    token_info.creator_vault = extra.get("creator_vault")

    if token_info.associated_bonding_curve is None or token_info.creator is None:
        logger.warning(
            "Incomplete accounts for %s (creator=%s); skipping", mint_str, creator
        )
        return None
    return token_info


async def build_generic_token_info(
    mint_str: str,
    client: object,
    symbol: str = "",
    name: str = "",
) -> TokenInfo | None:
    """TokenInfo minimo para una moneda que NO nacio en pump.fun.

    El constructor de arriba deriva la curva de pump.fun a partir del mint;
    una moneda de otro origen no tiene esa cuenta y ahi se acaba. Para entrar
    y salir por Jupiter no hace falta curva: basta el mint y saber con que
    programa de token se creo, que es lo que decide la ATA.

    Se marca ``migrated`` para que el comprador ruteee por el pool, y
    ``origen_externo`` para poder distinguirla despues: en una moneda asi no
    hay creador de pump.fun, asi que el historial del dev y el anti-rug de la
    curva no dicen nada de ella.

    Args:
        mint_str: Direccion del token.
        client: SolanaClient, para leer el dueno de la cuenta del mint.
        symbol: Ticker, si el feed lo trajo.
        name: Nombre, si el feed lo trajo.

    Returns:
        El TokenInfo, o None si el mint no es valido o no se puede saber su
        programa (adivinarlo deriva la ATA equivocada y la compra falla fijo).
    """
    try:
        mint = Pubkey.from_string(mint_str)
    except (ValueError, TypeError):
        logger.warning("Mint invalido para compra por pool: %s", mint_str)
        return None

    token_program_id = None
    try:
        mint_acct = await client.get_account_info(mint)
        if mint_acct and getattr(mint_acct, "owner", None):
            token_program_id = mint_acct.owner
    except Exception:  # noqa: BLE001 -- sin programa no se puede comprar
        token_program_id = None
    if token_program_id is None:
        logger.warning(
            "No pude leer el programa de %s; lo salto (no se adivina)", mint_str
        )
        return None

    return TokenInfo(
        name=name or symbol or mint_str,
        symbol=symbol or "?",
        uri="",
        mint=mint,
        platform=Platform.PUMP_FUN,
        token_program_id=token_program_id,
        additional_data={"migrated": True, "origen_externo": True},
    )
