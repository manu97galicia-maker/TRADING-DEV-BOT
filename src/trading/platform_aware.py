"""
Platform-aware trader implementations that use the interface system.
Final cleanup removing all platform-specific hardcoding.
"""

import asyncio
from time import monotonic

from solders.pubkey import Pubkey

from core.client import SolanaClient
from core.priority_fee.manager import PriorityFeeManager
from core.pubkeys import LAMPORTS_PER_SOL, TOKEN_DECIMALS
from core.wallet import Wallet
from interfaces.core import AddressProvider, Platform, TokenInfo
from platforms import get_platform_implementations
from trading.base import Trader, TradeResult
from utils.logger import get_logger

logger = get_logger(__name__)

#: Cuanto vale el precio que dejo el constructor de la moneda antes de
#: tener que releerlo. Dos segundos: lo que tarda el camino entre detectar
#: y firmar es de decimas, asi que en la practica se reutiliza siempre; si
#: algo se atasca por el camino, se relee y no se compra a ciegas.
_PRECIO_FRESCO_SEG = 2.0


class PlatformAwareBuyer(Trader):
    """Platform-aware token buyer that works with any supported platform."""

    def __init__(
        self,
        client: SolanaClient,
        wallet: Wallet,
        priority_fee_manager: PriorityFeeManager,
        amount: float,
        slippage: float = 0.01,
        max_retries: int = 5,
        extreme_fast_token_amount: int = 0,
        extreme_fast_mode: bool = False,
        compute_units: dict | None = None,
        allow_mayhem: bool = False,
        require_mayhem: bool = False,
        # Creadores que se compran SIEMPRE. Se saltan `require_mayhem`: pedir
        # "snipea a este dev" manda sobre "solo mayhem", porque si no, de sus
        # lanzamientos solo se cogeria la fraccion que salga en mayhem.
        devs_marcados: set | None = None,
    ):
        """Initialize platform-aware token buyer."""
        self.allow_mayhem = bool(allow_mayhem)
        # "Only mayhem" strategies: buy exclusively pump.fun mayhem-mode tokens.
        self.require_mayhem = bool(require_mayhem)
        self._devs_marcados = {str(d) for d in (devs_marcados or ())}
        self.client = client
        self.wallet = wallet
        self.priority_fee_manager = priority_fee_manager
        self.amount = amount
        self.slippage = slippage
        self.max_retries = max_retries
        self.extreme_fast_mode = extreme_fast_mode
        self.extreme_fast_token_amount = extreme_fast_token_amount
        self.compute_units = compute_units or {}

    async def execute(
        self,
        token_info: TokenInfo,
        amount_sol: float | None = None,
        priority_fee_override: int | None = None,
    ) -> TradeResult:
        """Execute buy operation using platform-specific implementations.

        Args:
            token_info: la moneda a comprar.
            amount_sol: cuanto gastar en ESTA compra. Si no se pasa, se usa el
                tamano de la estrategia. Sirve para apostar menos en una
                categoria concreta (las mayhem van a 0,06 y no a 0,30) sin
                tocar el resto. Se pasa por parametro y no cambiando
                `self.amount` porque puede haber varias compras a la vez y una
                le pisaria el importe a la otra.
            priority_fee_override: comision de prioridad SOLO para esta compra,
                en microlamports por unidad de computo. Se pasa por parametro
                por el mismo motivo que el importe: el gestor de comisiones es
                compartido, y cambiarlo en el para una compra se lo cambiaria a
                las que corren a la vez. Una comision equivocada es dinero.
        """
        try:
            amount = float(amount_sol) if amount_sol else self.amount
            # Get platform-specific implementations
            implementations = get_platform_implementations(
                token_info.platform, self.client
            )
            address_provider = implementations.address_provider
            instruction_builder = implementations.instruction_builder
            curve_manager = implementations.curve_manager

            # Convert amount to lamports
            amount_lamports = int(amount * LAMPORTS_PER_SOL)

            if self.extreme_fast_mode:
                # Skip the wait and directly calculate the amount
                token_amount = self.extreme_fast_token_amount
                token_price_sol = amount / token_amount if token_amount > 0 else 0
                # Even in extreme_fast_mode, refresh mayhem/cashback/creator from
                # chain — listeners (especially pumpportal) often don't carry
                # these, and the program rejects with NotAuthorized (0x1770) /
                # ConstraintSeeds (0x7d6) when fee_recipient or creator_vault
                # is wrong. PumpPortal often notifies before the BC account is
                # readable, so retry briefly. One handful of RPC calls is cheap
                # relative to a failed buy.
                try:
                    pool_address = self._get_pool_address(
                        token_info, address_provider
                    )
                    pool_state = None
                    last_err: Exception | None = None
                    # Use processed commitment — geyser/logs fire on processed
                    # so the BC is typically readable in the same slot. Most
                    # listeners only need 1 attempt; pumpportal occasionally
                    # races the on-chain commit, so allow a few quick retries.
                    for attempt in range(4):
                        try:
                            pool_state = await curve_manager.get_pool_state(
                                pool_address, commitment="processed"
                            )
                            break
                        except Exception as inner:  # noqa: BLE001
                            last_err = inner
                            await asyncio.sleep(0.15)
                    if pool_state is None:
                        raise last_err or RuntimeError(
                            "pool_state unavailable after retries"
                        )
                    token_info.is_mayhem_mode = pool_state.get(
                        "is_mayhem_mode", token_info.is_mayhem_mode
                    )
                    token_info.is_cashback_coin = pool_state.get(
                        "is_cashback_coin", token_info.is_cashback_coin
                    )
                    fresh_creator = pool_state.get("creator")
                    if fresh_creator and hasattr(
                        address_provider, "derive_creator_vault"
                    ):
                        from solders.pubkey import Pubkey as _Pubkey

                        new_creator = (
                            _Pubkey.from_string(fresh_creator)
                            if isinstance(fresh_creator, str)
                            else fresh_creator
                        )
                        token_info.creator = new_creator
                        token_info.creator_vault = (
                            address_provider.derive_creator_vault(new_creator)
                        )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"extreme_fast_mode buy: could not refresh curve flags "
                        f"({e}); proceeding with token_info defaults"
                    )
            else:
                # Get pool address based on platform using platform-agnostic method
                pool_address = self._get_pool_address(token_info, address_provider)

                # Regular behavior with RPC call. Read at "processed" commitment:
                # the fresh bonding-curve account is visible there immediately,
                # while confirmed/finalized can lag several seconds on a brand-new
                # mint -> "Account not found" and a failed buy. Retry briefly for
                # any RPC/index lag.
                # SI YA SE ACABA DE LEER, NO SE VUELVE A LEER. El
                # constructor de la moneda (`token_builder`) lee esta MISMA
                # cuenta milisegundos antes y deja aqui el precio. Releerla era
                # un viaje entero al nodo en el camino critico entre detectar y
                # firmar -- y con la moneda moviendose, cada viaje es precio
                # que se escapa.
                #
                # Con fecha de caducidad: pasado `_PRECIO_FRESCO_SEG` se relee.
                # Un precio viejo no se usa jamas. Las banderas mayhem/cashback
                # ya vienen puestas en el TokenInfo por el mismo constructor,
                # asi que tampoco se pierden. Y el tope de gasto
                # (`max_sol_cost`, mas abajo) sigue protegiendo igual.
                _extra = token_info.additional_data or {}
                _precio_ya = float(_extra.get("precio_curva") or 0.0)
                _leido_en = float(_extra.get("precio_curva_en") or 0.0)
                pool_state = None
                if (
                    _precio_ya > 0
                    and _leido_en
                    and (monotonic() - _leido_en) <= _PRECIO_FRESCO_SEG
                ):
                    logger.info(
                        "Precio de la curva reutilizado (%.0f ms): un viaje al "
                        "nodo menos antes de comprar",
                        (monotonic() - _leido_en) * 1000,
                    )
                    pool_state = {
                        "price_per_token": _precio_ya,
                        "is_mayhem_mode": getattr(
                            token_info, "is_mayhem_mode", False
                        ),
                        "is_cashback_coin": getattr(
                            token_info, "is_cashback_coin", False
                        ),
                    }

                _last: Exception | None = None
                for _attempt in range(10 if pool_state is None else 0):
                    try:
                        pool_state = await curve_manager.get_pool_state(
                            pool_address, commitment="processed"
                        )
                        break
                    except Exception as _e:  # noqa: BLE001
                        _last = _e
                        await asyncio.sleep(0.5)
                if pool_state is None:
                    raise _last or RuntimeError(
                        "pool_state unavailable after retries"
                    )
                token_price_sol = pool_state.get("price_per_token")

                # Validate price_per_token is present and positive
                if token_price_sol is None or token_price_sol <= 0:
                    raise ValueError(
                        f"Invalid price_per_token: {token_price_sol} for pool {pool_address} "
                        f"(mint: {token_info.mint}) - cannot execute buy with zero/invalid price"
                    )

                # Set mayhem-mode and cashback flags from bonding-curve state
                # so the instruction builder picks the correct fee_recipient and
                # account-list shape (cashback sells use 17 accounts, non-cashback 16).
                token_info.is_mayhem_mode = pool_state.get("is_mayhem_mode", False)
                token_info.is_cashback_coin = pool_state.get(
                    "is_cashback_coin", token_info.is_cashback_coin
                )
                token_amount = amount / token_price_sol

            # Avoid pump.fun "mayhem mode" tokens unless this strategy allows them.
            if not self.allow_mayhem and getattr(token_info, "is_mayhem_mode", False):
                logger.info(
                    f"Evitado {token_info.symbol}: mayhem mode (guard de compra)"
                )
                return TradeResult(success=False, error_message="mayhem_mode")

            # "Only mayhem" strategies: skip anything that is NOT a mayhem-mode
            # token. Checked here (after pool_state is fetched above) because the
            # is_mayhem_mode flag is only authoritative once the bonding curve
            # has been read — earlier listeners often report it as False.
            _marcado = bool(self._devs_marcados) and bool(
                {str(token_info.user),
                 str(getattr(token_info, "creator", "") or "")}
                & self._devs_marcados
            )
            if _marcado and self.require_mayhem:
                logger.info(
                    f"{token_info.symbol}: dev marcado, se salta el filtro de "
                    f"solo-mayhem"
                )
            if self.require_mayhem and not _marcado and not getattr(
                token_info, "is_mayhem_mode", False
            ):
                logger.info(
                    f"Evitado {token_info.symbol}: no es mayhem mode (require_mayhem)"
                )
                return TradeResult(success=False, error_message="not_mayhem")

            # Tokens to request in the buy instruction.
            # pump.fun's buy `amount` is the EXACT token quantity to purchase; slippage
            # is enforced separately via max_sol_cost below, so we must pass the FULL
            # expected amount here (passing the slippage-reduced amount under-deploys
            # ~slippage of buy_amount). For AMM-style platforms (letsbonk) the same field
            # is a real min-out floor and must keep the slippage reduction.
            if token_info.platform == Platform.PUMP_FUN:
                minimum_token_amount_raw = int(token_amount * 10**TOKEN_DECIMALS)
            else:
                minimum_token_amount_raw = int(
                    token_amount * (1 - self.slippage) * 10**TOKEN_DECIMALS
                )

            # Calculate maximum SOL to spend with slippage
            max_amount_lamports = int(amount_lamports * (1 + self.slippage))

            # Build buy instructions using platform-specific builder
            instructions = await instruction_builder.build_buy_instruction(
                token_info,
                self.wallet.pubkey,
                max_amount_lamports,  # amount_in (SOL)
                minimum_token_amount_raw,  # minimum_amount_out (tokens)
                address_provider,
            )

            # Get accounts for priority fee calculation
            priority_accounts = instruction_builder.get_required_accounts_for_buy(
                token_info, self.wallet.pubkey, address_provider
            )

            logger.info(
                f"Buying {token_amount:.6f} tokens at {token_price_sol:.8f} SOL per token on {token_info.platform.value}"
            )
            logger.info(
                f"Total cost: {amount:.6f} SOL (max: {max_amount_lamports / LAMPORTS_PER_SOL:.6f} SOL)"
            )

            # skip_preflight=True EN LA COMPRA. Sin simulacion previa: la
            # transaccion sale directa, sin el viaje al nodo que cuesta
            # simularla (100-300 ms).
            #
            # HISTORIA DE ESTA LINEA, porque ha ido y vuelto:
            #   True  -> el original: aqui se compite por entrar.
            #   False -> el 3-ago. El razonamiento escrito entonces fue "esta
            #            estrategia espera 12 segundos a proposito antes de
            #            mirar la moneda, asi que no compite con nadie".
            #   True  -> el 19-sep, pedido por Manu. Aquel razonamiento ya no
            #            vale: la espera de 12 segundos no existe y la
            #            estrategia de hoy compra en cuanto una moneda cruza
            #            los titulares. Llegar tarde fue el problema que costo
            #            dinero, no la comision.
            #
            # LO QUE CUESTA, dicho claro: con preflight, una compra que iba a
            # revertir se rechazaba ANTES de aterrizar y salia gratis. Sin el,
            # aterriza y paga la comision de prioridad igual -- que ahora son
            # ~0,22 $ por intento fallido, no calderilla.
            #
            # LA VENTA NO CAMBIA: alli no hay carrera y el 1-ago se quemaron
            # 0,64 SOL en 178 ventas que reventaban y pagaban. Ver mas abajo.
            tx_signature = await self.client.build_and_send_transaction(
                instructions,
                self.wallet.keypair,
                skip_preflight=True,
                max_retries=self.max_retries,
                priority_fee=(
                    priority_fee_override
                    if priority_fee_override
                    else await self.priority_fee_manager.calculate_priority_fee(
                        priority_accounts
                    )
                ),
                compute_unit_limit=instruction_builder.get_buy_compute_unit_limit(
                    self._get_cu_override("buy", token_info.platform)
                ),
                account_data_size_limit=self._get_cu_override(
                    "account_data_size", token_info.platform
                ),
            )

            success = await self.client.confirm_transaction(tx_signature)

            if success:
                logger.info(f"Buy transaction confirmed: {tx_signature}")

                # Fetch actual tokens and SOL spent from transaction
                # Uses preBalances/postBalances to get exact amounts
                sol_destination = self._get_sol_destination(
                    token_info, address_provider
                )
                tokens_raw, sol_spent = await self.client.get_buy_transaction_details(
                    str(tx_signature), token_info.mint, sol_destination
                )

                if tokens_raw is not None and sol_spent is not None:
                    actual_amount = tokens_raw / 10**TOKEN_DECIMALS
                    actual_price = (sol_spent / LAMPORTS_PER_SOL) / actual_amount
                    logger.info(
                        f"Actual tokens received: {actual_amount:.6f} "
                        f"(expected: {token_amount:.6f})"
                    )
                    logger.info(
                        f"Actual SOL spent: {sol_spent / LAMPORTS_PER_SOL:.10f} SOL"
                    )
                    logger.info(f"Actual price: {actual_price:.10f} SOL/token")
                    token_amount = actual_amount
                    token_price_sol = actual_price
                else:
                    # C1 fix: the tx CONFIRMED on-chain, so the buy executed. Only
                    # the post-parse of exact balances failed (RPC lag/429). Do NOT
                    # treat this as a failed buy — that would leave the bought bag
                    # orphaned (no exit monitor) and let the coin be re-bought.
                    # pump.fun fills the EXACT requested token qty, so the expected
                    # amount/price are accurate enough to drive the exit monitor
                    # (the seller sells this amount, which is what we hold).
                    logger.warning(
                        f"Buy CONFIRMED but details unparsable (tx: {tx_signature}); "
                        f"treating as SUCCESS with expected amount={token_amount:.6f}, "
                        f"price={token_price_sol:.10f} so the position is managed."
                    )

                return TradeResult(
                    success=True,
                    platform=token_info.platform,
                    tx_signature=tx_signature,
                    amount=token_amount,
                    price=token_price_sol,
                )
            else:
                return TradeResult(
                    success=False,
                    platform=token_info.platform,
                    error_message=f"Transaction failed to confirm: {tx_signature}",
                )

        except Exception as e:
            logger.exception("Buy operation failed")
            return TradeResult(
                success=False, platform=token_info.platform, error_message=str(e)
            )

    def _get_pool_address(
        self, token_info: TokenInfo, address_provider: AddressProvider
    ) -> Pubkey:
        """Get the pool/curve address for price calculations using platform-agnostic method."""
        # Try to get the address from token_info first, then derive if needed
        if token_info.platform == Platform.PUMP_FUN:
            if hasattr(token_info, "bonding_curve") and token_info.bonding_curve:
                return token_info.bonding_curve
        elif token_info.platform == Platform.LETS_BONK:
            if hasattr(token_info, "pool_state") and token_info.pool_state:
                return token_info.pool_state

        # Fallback to deriving the address using platform provider
        return address_provider.derive_pool_address(token_info.mint)

    def _get_sol_destination(
        self, token_info: TokenInfo, address_provider: AddressProvider
    ) -> Pubkey:
        """Get the address where SOL is sent during a buy transaction.

        For pump.fun: SOL goes to the bonding curve
        For letsbonk: SOL goes to the quote_vault (WSOL vault)

        Args:
            token_info: Token information
            address_provider: Platform-specific address provider

        Returns:
            Address where SOL is transferred during buy

        Raises:
            NotImplementedError: If platform SOL destination is not implemented
        """
        if token_info.platform == Platform.PUMP_FUN:
            # For pump.fun, SOL goes directly to bonding curve
            if hasattr(token_info, "bonding_curve") and token_info.bonding_curve:
                return token_info.bonding_curve
            return address_provider.derive_pool_address(token_info.mint)
        elif token_info.platform == Platform.LETS_BONK:
            # For letsbonk, SOL goes to quote_vault (WSOL vault)
            if hasattr(token_info, "quote_vault") and token_info.quote_vault:
                return token_info.quote_vault
            # Derive quote_vault if not available
            return address_provider.derive_quote_vault(token_info.mint)

        raise NotImplementedError(
            f"SOL destination not implemented for platform {token_info.platform.value}. "
            f"Add platform-specific logic to _get_sol_destination() to specify where "
            f"SOL is transferred during buy transactions for this platform."
        )

    def _get_cu_override(self, operation: str, platform: Platform) -> int | None:
        """Get compute unit override from configuration.

        Args:
            operation: "buy" or "sell"
            platform: Trading platform (unused - each config is platform-specific)

        Returns:
            CU override value if configured, None otherwise
        """
        if not self.compute_units:
            return None

        # Just check for operation override (buy/sell)
        return self.compute_units.get(operation)


class PlatformAwareSeller(Trader):
    """Platform-aware token seller that works with any supported platform."""

    def __init__(
        self,
        client: SolanaClient,
        wallet: Wallet,
        priority_fee_manager: PriorityFeeManager,
        slippage: float = 0.25,
        max_retries: int = 5,
        compute_units: dict | None = None,
    ):
        """Initialize platform-aware token seller."""
        self.client = client
        self.wallet = wallet
        self.priority_fee_manager = priority_fee_manager
        self.slippage = slippage
        self.max_retries = max_retries
        self.compute_units = compute_units or {}

    async def execute(
        self, token_info: TokenInfo, token_amount: float, token_price: float
    ) -> TradeResult:
        """Execute sell operation using platform-specific implementations.

        Args:
            token_info: Token information for the sell operation
            token_amount: Token amount to sell (from buy result). Required to avoid
                         RPC balance query delays.
            token_price: Token price in SOL (from buy result). Required to avoid
                        RPC pool state query delays.

        Returns:
            TradeResult with operation outcome

        Raises:
            ValueError: If required parameters are not provided
        """
        if token_amount is None:
            raise ValueError(
                "token_amount is required for sell operation. "
                "Pass the amount from buy result to avoid RPC delays."
            )
        if token_price is None or token_price <= 0:
            raise ValueError(
                "token_price is required for sell operation and must be positive. "
                "Pass the price from buy result to avoid RPC delays."
            )

        try:
            # Get platform-specific implementations
            implementations = get_platform_implementations(
                token_info.platform, self.client
            )
            address_provider = implementations.address_provider
            instruction_builder = implementations.instruction_builder
            curve_manager = implementations.curve_manager

            # Refresh mayhem-mode and cashback flags from curve state.
            # The sell account list is 16 (non-cashback) vs 17 (cashback), and
            # fee_recipient differs in mayhem mode — both can change between
            # buy and sell, so re-read from chain instead of trusting create-time
            # flags carried in token_info.
            try:
                pool_address = self._get_pool_address(token_info, address_provider)
                pool_state = await curve_manager.get_pool_state(pool_address)
                token_info.is_mayhem_mode = pool_state.get(
                    "is_mayhem_mode", token_info.is_mayhem_mode
                )
                token_info.is_cashback_coin = pool_state.get(
                    "is_cashback_coin", token_info.is_cashback_coin
                )
                # Refresh creator/creator_vault from current BC state. Post
                # 2026-04-28 the program may delegate BC.creator to a PFEE-owned
                # PDA after the initial creator buy, so the create-time vault
                # cached on token_info goes stale before the sell lands. Failing
                # to refresh manifests as ConstraintSeeds (0x7d6) on Sell.
                fresh_creator = pool_state.get("creator")
                if fresh_creator:
                    from solders.pubkey import Pubkey as _Pubkey

                    new_creator = (
                        _Pubkey.from_string(fresh_creator)
                        if isinstance(fresh_creator, str)
                        else fresh_creator
                    )
                    token_info.creator = new_creator
                    token_info.creator_vault = address_provider.derive_creator_vault(
                        new_creator
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"Could not refresh curve flags before sell ({e}); "
                    f"using token_info values is_mayhem_mode={token_info.is_mayhem_mode}, "
                    f"is_cashback_coin={token_info.is_cashback_coin}"
                )

            # Use pre-known amount and price (no RPC delay)
            token_balance_decimal = token_amount
            token_balance = int(token_amount * 10**TOKEN_DECIMALS)
            token_price_sol = token_price

            logger.info(f"Token balance: {token_balance_decimal:.6f}")
            logger.info(f"Price per Token (from buy): {token_price_sol:.8f} SOL")

            if token_balance == 0:
                logger.info("No tokens to sell.")
                return TradeResult(
                    success=False,
                    platform=token_info.platform,
                    error_message="No tokens to sell",
                )

            # Calculate expected SOL output with slippage protection
            expected_sol_output = token_balance_decimal * token_price_sol
            min_sol_output = max(
                1,
                int((expected_sol_output * (1 - self.slippage)) * LAMPORTS_PER_SOL),
            )
            logger.info(
                f"Selling {token_balance_decimal} tokens on {token_info.platform.value}"
            )
            logger.info(f"Expected SOL output: {expected_sol_output:.10f} SOL")
            logger.info(
                f"Minimum SOL output (with {self.slippage * 100:.1f}% slippage): "
                f"{min_sol_output / LAMPORTS_PER_SOL:.10f} SOL ({min_sol_output} lamports)"
            )

            # Build sell instructions using platform-specific builder
            instructions = await instruction_builder.build_sell_instruction(
                token_info,
                self.wallet.pubkey,
                token_balance,  # amount_in (tokens)
                min_sol_output,  # minimum_amount_out (SOL)
                address_provider,
            )

            # Get accounts for priority fee calculation
            priority_accounts = instruction_builder.get_required_accounts_for_sell(
                token_info, self.wallet.pubkey, address_provider
            )

            # Send transaction
            #
            # skip_preflight=False EN LA VENTA (1-ago). Una transaccion que
            # falla solo cuesta dinero si LLEGA a la cadena: la comision de
            # prioridad se paga aunque revierta. Con preflight, el RPC la
            # simula antes y la rechaza sin enviarla, asi que un fallo sale
            # gratis.
            #
            # Ese dia esto costo 0,64 SOL: 178 ventas que reventaban con 6023
            # NotEnoughTokensToSell, todas aterrizando y todas pagando.
            #
            # En la COMPRA va en True a proposito: ahi se compite por entrar
            # y los 100-300 ms de la simulacion se notan. En la venta no hay
            # carrera --la posicion ya es nuestra y el precio se defiende con
            # los reintentos, no con la prisa-- asi que aqui compensa pagar el
            # viaje a cambio de que un fallo salga gratis. `jupiter_seller.py`
            # llego a la misma conclusion el 31-jul por su cuenta.
            tx_signature = await self.client.build_and_send_transaction(
                instructions,
                self.wallet.keypair,
                skip_preflight=False,
                max_retries=self.max_retries,
                priority_fee=await self.priority_fee_manager.calculate_priority_fee(
                    priority_accounts
                ),
                compute_unit_limit=instruction_builder.get_sell_compute_unit_limit(
                    self._get_cu_override("sell", token_info.platform)
                ),
                account_data_size_limit=self._get_cu_override(
                    "account_data_size", token_info.platform
                ),
            )

            success = await self.client.confirm_transaction(tx_signature)

            if success:
                logger.info(f"Sell transaction confirmed: {tx_signature}")
                # LO QUE DE VERDAD ENTRO, no lo que la curva prometia. Sin esto
                # se devolvia `token_price_sol`, que es el precio calculado
                # ANTES de vender: el P&L del bot llego a decir +0,3233 SOL en
                # una operacion que la cadena cifro en +0,0419, porque entre el
                # calculo y la ejecucion el precio se habia movido y el
                # deslizamiento se habia comido la diferencia.
                precio_real = token_price_sol
                try:
                    lamports = await self.client.get_sell_transaction_details(
                        str(tx_signature), self.wallet.pubkey
                    )
                    if lamports and token_balance_decimal > 0:
                        precio_real = (lamports / LAMPORTS_PER_SOL) / token_balance_decimal
                        if token_price_sol > 0:
                            _desv = precio_real / token_price_sol - 1
                            if abs(_desv) > 0.10:
                                logger.warning(
                                    "Venta de %s ejecutada a %.10f SOL/token, "
                                    "un %+.0f%% de lo calculado (%.10f)",
                                    token_info.symbol, precio_real, _desv * 100,
                                    token_price_sol,
                                )
                except Exception:  # noqa: BLE001
                    # Si no se puede leer, se sigue con la estimacion: es peor
                    # devolver un cero y dar la operacion por ruinosa.
                    logger.debug("no pude leer el cobro real de la venta")
                return TradeResult(
                    success=True,
                    platform=token_info.platform,
                    tx_signature=tx_signature,
                    amount=token_balance_decimal,
                    price=precio_real,
                )
            else:
                return TradeResult(
                    success=False,
                    platform=token_info.platform,
                    error_message=f"Transaction failed to confirm: {tx_signature}",
                )

        except Exception as e:
            logger.exception("Sell operation failed")
            return TradeResult(
                success=False, platform=token_info.platform, error_message=str(e)
            )

    def _get_pool_address(
        self, token_info: TokenInfo, address_provider: AddressProvider
    ) -> Pubkey:
        """Get the pool/curve address for price calculations using platform-agnostic method."""
        # Try to get the address from token_info first, then derive if needed
        if token_info.platform == Platform.PUMP_FUN:
            if hasattr(token_info, "bonding_curve") and token_info.bonding_curve:
                return token_info.bonding_curve
        elif token_info.platform == Platform.LETS_BONK:
            if hasattr(token_info, "pool_state") and token_info.pool_state:
                return token_info.pool_state

        # Fallback to deriving the address using platform provider
        return address_provider.derive_pool_address(token_info.mint)

    def _get_cu_override(self, operation: str, platform: Platform) -> int | None:
        """Get compute unit override from configuration.

        Args:
            operation: "buy" or "sell"
            platform: Trading platform (unused - each config is platform-specific)

        Returns:
            CU override value if configured, None otherwise
        """
        if not self.compute_units:
            return None

        # Just check for operation override (buy/sell)
        return self.compute_units.get(operation)
