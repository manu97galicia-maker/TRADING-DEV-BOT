"""
Pump.Fun implementation of CurveManager interface.

This module handles pump.fun-specific bonding curve operations
by implementing the CurveManager interface using IDL-based decoding.
"""

# Preserve the existing explicit IDL diagnostics and RPC recovery boundaries.
# ruff: noqa: TRY003, TRY300

import hashlib
from typing import Any

from solders.pubkey import Pubkey

from core.client import SolanaClient
from core.pubkeys import LAMPORTS_PER_SOL, TOKEN_DECIMALS
from interfaces.core import CurveManager, Platform
from platforms.pumpfun.address_provider import PumpFunAddresses
from utils.idl_parser import IDLParser
from utils.logger import get_logger

logger = get_logger(__name__)
_CURVE_DISCRIMINATOR = hashlib.sha256(b"account:BondingCurve").digest()[:8]


def _authenticated_curve_data(account: object) -> bytes | bytearray:
    if (str(getattr(account, "owner", None)) != str(PumpFunAddresses.PROGRAM)
            or getattr(account, "executable", False) is not False):
        raise ValueError("Unexpected bonding curve owner or executable account")
    data = getattr(account, "data", None)
    if (not isinstance(data, (bytes, bytearray))
            or data[:8] != _CURVE_DISCRIMINATOR):
        raise ValueError("Unexpected bonding curve account discriminator")
    return data


class PumpFunCurveManager(CurveManager):
    """Pump.Fun implementation of CurveManager interface using IDL-based decoding."""

    def __init__(self, client: SolanaClient, idl_parser: IDLParser) -> None:
        """Initialize pump.fun curve manager with injected IDL parser.

        Args:
            client: Solana RPC client
            idl_parser: Pre-loaded IDL parser for pump.fun platform
        """
        self.client = client
        self._idl_parser = idl_parser

        logger.info("Pump.Fun curve manager initialized with injected IDL parser")

    @property
    def platform(self) -> Platform:
        """Get the platform this manager serves."""
        return Platform.PUMP_FUN

    async def get_pool_state(
        self,
        pool_address: Pubkey,
        commitment: str | None = None,
        *,
        exigir_precio: bool = True,
    ) -> dict[str, Any]:
        """Get the current state of a pump.fun bonding curve.

        Args:
            pool_address: Address of the bonding curve
            commitment: Optional commitment override. Pass "processed" right
                after a geyser/logs event so the BC is readable in the same
                slot it was created (default: confirmed, ~1-2 slot lag).
            exigir_precio: Si es False, unas reservas a cero dejan de ser un
                error y el precio simplemente no se calcula. Una curva MIGRADA
                tiene las reservas vacias: decodifica bien y su campo
                ``complete`` es legible, pero el precio ya no vive ahi. Con
                esto en True (lo normal) quien pida el estado para operar en
                la curva sigue recibiendo el error de siempre.

        Returns:
            Dictionary containing bonding curve state data
        """
        try:
            account = await self.client.get_account_info(
                pool_address, commitment=commitment
            )
            data = _authenticated_curve_data(account)

            # Decode bonding curve state using injected IDL parser
            curve_state_data = self._decode_curve_state_with_idl(
                data, exigir_precio=exigir_precio
            )

            return curve_state_data

        except Exception as e:
            logger.exception("Failed to get curve state")
            raise ValueError(f"Invalid bonding curve state: {e!s}") from e

    async def calculate_price(self, pool_address: Pubkey) -> float:
        """Calculate current token price from bonding curve state.

        Args:
            pool_address: Address of the bonding curve

        Returns:
            Current token price in SOL
        """
        # "processed" tambien aqui: esto es lo que alimenta el take-profit y el
        # stop-loss. Con el "finalized" por defecto de solana-py, el precio que
        # se compara llega 13-30 segundos tarde, y en una moneda que se mueve un
        # 35% en medio minuto eso es vender en un sitio distinto del que marca
        # la regla.
        pool_state = await self.get_pool_state(pool_address, commitment="processed")

        # Use virtual reserves for price calculation
        virtual_token_reserves = pool_state["virtual_token_reserves"]
        virtual_sol_reserves = pool_state["virtual_sol_reserves"]

        if virtual_token_reserves <= 0:
            return 0.0

        # Divide the SOL reserves by the token reserves to obtain the price.
        price_lamports = virtual_sol_reserves / virtual_token_reserves
        return price_lamports * (10**TOKEN_DECIMALS) / LAMPORTS_PER_SOL

    async def calculate_buy_amount_out(
        self, pool_address: Pubkey, amount_in: int
    ) -> int:
        """Calculate expected tokens received for a buy operation.

        Uses the pump.fun bonding curve formula to calculate token output.

        Args:
            pool_address: Address of the bonding curve
            amount_in: Amount of SOL to spend (in lamports)

        Returns:
            Expected amount of tokens to receive (in raw token units)
        """
        pool_state = await self.get_pool_state(pool_address)

        virtual_token_reserves = pool_state["virtual_token_reserves"]
        virtual_sol_reserves = pool_state["virtual_sol_reserves"]

        # Use virtual reserves for bonding curve calculation
        # Multiply the input by token reserves and divide by the resulting SOL reserves.
        numerator = amount_in * virtual_token_reserves
        denominator = virtual_sol_reserves + amount_in

        if denominator == 0:
            return 0

        tokens_out = numerator // denominator
        return tokens_out

    async def calculate_sell_amount_out(
        self, pool_address: Pubkey, amount_in: int
    ) -> int:
        """Calculate expected SOL received for a sell operation.

        Uses the pump.fun bonding curve formula to calculate SOL output.

        Args:
            pool_address: Address of the bonding curve
            amount_in: Amount of tokens to sell (in raw token units)

        Returns:
            Expected amount of SOL to receive (in lamports)
        """
        pool_state = await self.get_pool_state(pool_address)

        virtual_token_reserves = pool_state["virtual_token_reserves"]
        virtual_sol_reserves = pool_state["virtual_sol_reserves"]

        # Use virtual reserves for bonding curve calculation
        # Multiply the input by SOL reserves and divide by the resulting token reserves.
        numerator = amount_in * virtual_sol_reserves
        denominator = virtual_token_reserves + amount_in

        if denominator == 0:
            return 0

        sol_out = numerator // denominator
        return sol_out

    async def get_reserves(self, pool_address: Pubkey) -> tuple[int, int]:
        """Get current bonding curve reserves.

        Args:
            pool_address: Address of the bonding curve

        Returns:
            Tuple of (token_reserves, sol_reserves) in raw units
        """
        pool_state = await self.get_pool_state(pool_address)
        return (
            pool_state["virtual_token_reserves"],
            pool_state["virtual_sol_reserves"],
        )

    def _decode_curve_state_with_idl(
        self, data: bytes, *, exigir_precio: bool = True
    ) -> dict[str, Any]:
        """Decode bonding curve state data using injected IDL parser.

        Args:
            data: Raw account data
            exigir_precio: Ver :meth:`get_pool_state`. Con False, unas reservas
                vacias (curva migrada) devuelven el estado sin precio en vez de
                lanzar.

        Returns:
            Dictionary with decoded bonding curve state

        Raises:
            ValueError: If IDL parsing fails
        """
        # Use injected IDL parser to decode BondingCurve account data
        decoded_curve_state = self._idl_parser.decode_account_data(
            data, "BondingCurve", skip_discriminator=True
        )

        if not decoded_curve_state:
            raise ValueError("Failed to decode bonding curve state with IDL parser")

        # Extract the fields we need for trading calculations
        # Based on the BondingCurve structure from the IDL
        curve_data = {
            "virtual_token_reserves": decoded_curve_state.get(
                "virtual_token_reserves", 0
            ),
            "virtual_sol_reserves": decoded_curve_state.get("virtual_sol_reserves", 0),
            "real_token_reserves": decoded_curve_state.get("real_token_reserves", 0),
            "real_sol_reserves": decoded_curve_state.get("real_sol_reserves", 0),
            "token_total_supply": decoded_curve_state.get("token_total_supply", 0),
            "complete": decoded_curve_state.get("complete", False),
            "creator": decoded_curve_state.get("creator", ""),
            "is_mayhem_mode": decoded_curve_state.get("is_mayhem_mode", False),
            "is_cashback_coin": decoded_curve_state.get("is_cashback_coin", False),
        }

        # Calculate additional metrics
        # Validate reserves are positive before calculating price
        _sin_reservas = (
            curve_data["virtual_token_reserves"] <= 0
            or curve_data["virtual_sol_reserves"] <= 0
        )
        if _sin_reservas and not exigir_precio:
            # Migrada: el resto del estado (complete, creator, flags) es
            # valido y es justo lo que necesita quien va a comprar por el pool.
            curve_data["price_per_token"] = 0.0
            return curve_data
        if curve_data["virtual_token_reserves"] <= 0:
            raise ValueError(
                f"Invalid virtual_token_reserves: {curve_data['virtual_token_reserves']} - cannot calculate price"
            )
        if curve_data["virtual_sol_reserves"] <= 0:
            raise ValueError(
                f"Invalid virtual_sol_reserves: {curve_data['virtual_sol_reserves']} - cannot calculate price"
            )

        curve_data["price_per_token"] = (
            (curve_data["virtual_sol_reserves"] / curve_data["virtual_token_reserves"])
            * (10**TOKEN_DECIMALS)
            / LAMPORTS_PER_SOL
        )

        # Add convenience decimal fields
        curve_data["token_reserves_decimal"] = (
            curve_data["virtual_token_reserves"] / 10**TOKEN_DECIMALS
        )
        curve_data["sol_reserves_decimal"] = (
            curve_data["virtual_sol_reserves"] / LAMPORTS_PER_SOL
        )

        logger.debug(
            f"Decoded curve state: virtual_token_reserves={curve_data['virtual_token_reserves']}, "
            f"virtual_sol_reserves={curve_data['virtual_sol_reserves']}, "
            f"price={curve_data['price_per_token']:.8f} SOL"
        )

        return curve_data

    # Additional convenience methods for pump.fun specific operations
    async def calculate_expected_tokens(
        self, pool_address: Pubkey, sol_amount: float
    ) -> float:
        """Calculate the expected token amount for a given SOL input.

        This is a convenience method that converts between decimal and raw units.

        Args:
            pool_address: Address of the bonding curve
            sol_amount: Amount of SOL to spend (in decimal SOL)

        Returns:
            Expected token amount (in decimal tokens)
        """
        sol_lamports = int(sol_amount * LAMPORTS_PER_SOL)
        tokens_raw = await self.calculate_buy_amount_out(pool_address, sol_lamports)
        return tokens_raw / 10**TOKEN_DECIMALS

    async def calculate_expected_sol(
        self, pool_address: Pubkey, token_amount: float
    ) -> float:
        """Calculate the expected SOL amount for a given token input.

        This is a convenience method that converts between decimal and raw units.

        Args:
            pool_address: Address of the bonding curve
            token_amount: Amount of tokens to sell (in decimal tokens)

        Returns:
            Expected SOL amount (in decimal SOL)
        """
        tokens_raw = int(token_amount * 10**TOKEN_DECIMALS)
        sol_lamports = await self.calculate_sell_amount_out(pool_address, tokens_raw)
        return sol_lamports / LAMPORTS_PER_SOL

    async def is_curve_complete(self, pool_address: Pubkey) -> bool:
        """Check if the bonding curve is complete (migrated to Raydium).

        Args:
            pool_address: Address of the bonding curve

        Returns:
            True if curve is complete, False otherwise
        """
        # Sin exigir precio: una curva completa tiene las reservas a cero y
        # preguntar "esta completa?" no puede fallar justamente en el caso que
        # se esta preguntando.
        pool_state = await self.get_pool_state(pool_address, exigir_precio=False)
        return bool(pool_state.get("complete", False))

    async def get_curve_progress(self, pool_address: Pubkey) -> dict[str, Any]:
        """Get bonding curve completion progress information.

        Args:
            pool_address: Address of the bonding curve

        Returns:
            Dictionary with progress information
        """
        pool_state = await self.get_pool_state(pool_address)

        # Calculate progress based on SOL raised vs target
        # This is approximate since the exact target isn't stored in the curve state
        sol_raised = pool_state["real_sol_reserves"] / LAMPORTS_PER_SOL

        # Estimate progress based on typical pump.fun graduation requirements
        # (This could be made more accurate with additional on-chain data)
        estimated_target_sol = 85.0  # Typical pump.fun graduation target
        progress_percentage = min((sol_raised / estimated_target_sol) * 100, 100.0)

        return {
            "complete": pool_state.get("complete", False),
            "sol_raised": sol_raised,
            "estimated_target_sol": estimated_target_sol,
            "progress_percentage": progress_percentage,
            "tokens_available": pool_state["virtual_token_reserves"]
            / 10**TOKEN_DECIMALS,
            "market_cap_sol": sol_raised,  # Approximate market cap
        }

    def validate_curve_state_structure(self, pool_address: Pubkey) -> bool:
        """Validate that the curve state structure matches IDL expectations.

        Args:
            pool_address: Address of the bonding curve

        Returns:
            True if structure is valid, False otherwise
        """
        try:
            # This would be used during development/testing to ensure
            # the IDL parsing is working correctly
            pool_state = self.get_pool_state(pool_address)

            required_fields = [
                "virtual_token_reserves",
                "virtual_sol_reserves",
                "real_token_reserves",
                "real_sol_reserves",
                "token_total_supply",
                "complete",
            ]

            for field in required_fields:
                if field not in pool_state:
                    logger.error(f"Missing required field: {field}")
                    return False

                if field != "complete" and not isinstance(pool_state[field], int):
                    logger.error(
                        f"Field {field} is not an integer: {type(pool_state[field])}"
                    )
                    return False

            return True

        except Exception:
            logger.exception("Curve state validation failed")
            return False
