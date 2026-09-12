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

from solders.pubkey import Pubkey

from interfaces.core import Platform, TokenInfo
from utils.logger import get_logger

logger = get_logger(__name__)


async def build_pumpfun_token_info(  # noqa: PLR0913
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
    migrada = False
    try:
        migrada = await curve_manager.is_curve_complete(bonding_curve)
    except Exception:  # noqa: BLE001 -- unreadable curve => can't safely buy
        logger.warning("Could not read curve for %s; skipping", mint_str)
        return None
    if migrada and not permitir_migradas:
        logger.info("Skip %s (%s): bonding curve complete (migrated)", symbol, mint_str)
        return None

    try:
        state = await curve_manager.get_pool_state(bonding_curve)
    except Exception:  # noqa: BLE001
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
    try:
        mint_acct = await client.get_account_info(mint)
        if mint_acct and getattr(mint_acct, "owner", None):
            token_program_id = mint_acct.owner
    except Exception:  # noqa: BLE001
        token_program_id = None
    if token_program_id is None:
        logger.warning(
            "Could not read token program for %s; skipping (won't guess)", mint_str
        )
        return None

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
