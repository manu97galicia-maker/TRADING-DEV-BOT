"""Opt-in four-source runtime. Importing this module never starts trading."""

# This adapter deliberately overrides legacy private hooks. Broad catches are
# confined to retry/recovery loops; lazy imports keep optional routes isolated.
# ruff: noqa: SLF001, PLC0415, PLR2004, TRY003, BLE001

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

import trading.universal_trader as legacy
from monitoring import volumen
from monitoring.four_strategy_signals import choose_signal
from monitoring.pressure_trial_source import PressureTrialSource
from monitoring.telegram_source import TelegramSource
from trading.base import TradeResult
from trading.four_strategy_book import FourStrategyBook
from trading.four_strategy_entry_guard import (
    EntryRiskRejected,
    check_price,
    concentration_snapshot,
    validate_policy,
)
from trading.four_strategy_inactivity import fresh_inactivity, inactive_curve_evidence
from trading.second_block_policy import (
    STRATEGY_ID,
    position_exit_plan,
    recovery_allocations,
    validate_second_policy,
)
from trading.strategy_loss_guard import (
    priority_fee_cap_lamports,
    priority_price_micro_lamports,
)
from trading.transaction_observer import (
    BUY_PRICE_CEILING,
    ENTRY_WINDOW,
    OBSERVER,
    PRIORITY_CAP,
    SUBMISSIONS,
)
from utils.logger import get_logger

logger = get_logger(__name__)
if TYPE_CHECKING:
    from collections.abc import Callable
_PROCEEDS: ContextVar[float] = ContextVar("four_strategy_sale_proceeds", default=0)


class SharedBuyer:
    """Put every source behind the same durable admission transaction."""

    def __init__(self, runtime: FourStrategyTrader, buyer: object) -> None:
        self.runtime, self.buyer = runtime, buyer

    async def execute(  # noqa: C901, PLR0911, PLR0912, PLR0915 -- reject each invalid state before signing
        self,
        token_info: object,
        amount_sol: float | None = None,
        priority_fee_override: int | None = None,
    ) -> TradeResult:
        runtime = self.runtime
        del amount_sol  # Untrusted signal amounts cannot override shared sizing.
        signal = runtime.current_signal(token_info)
        if not signal or runtime.reference_age() > 30:
            return TradeResult(
                success=False, error_message="Four-strategy signal expired"
            )
        if (token_info.additional_data or {}).get("migrated"):
            return TradeResult(
                success=False,
                error_message="This bundle accepts unmigrated Pump.fun entries",
            )
        existing_raw, _ = await runtime.raw_holdings(str(token_info.mint))
        if existing_raw:
            return TradeResult(
                success=False,
                error_message="Existing holdings in this mint cannot be mixed with this bundle",
            )
        balance = await runtime.rpc(
            "getBalance", [str(runtime.wallet.pubkey), {"commitment": "finalized"}]
        )
        # Re-evaluate after the balance read: a slow RPC must not validate old flow.
        signal = runtime.current_signal(token_info)
        if not signal:
            return TradeResult(
                success=False, error_message="Entry expired while reading balance"
            )
        second = signal["strategy"] == STRATEGY_ID
        priority_cap = None
        if second:
            policy = getattr(runtime, "second_policy", {"enabled": False})
            if not policy.get("enabled"):
                return TradeResult(success=False, error_message="Second-block strategy is disabled")
            validate_second_policy(policy)
            priority_cap = priority_fee_cap_lamports(runtime.sol_usd,
                quote_age_seconds=runtime.reference_age(), cap_usd=policy["buy_priority_fee_cap_usd"])
        reservation = runtime.book.reserve(
            str(token_info.mint),
            signal["strategy"],
            int(balance["value"]),
            int(balance["context"]["slot"]),
            runtime.min_reserve_lamports,
            runtime.buy_slippage,
            runtime._fee_headroom_lamports + (priority_cap or 0),
            signal,
            fraction=policy["balance_fraction"] if second else runtime.fraccion_saldo,
        )
        if not reservation:
            return TradeResult(
                success=False,
                error_message="Shared slots, budget or strategy pause blocks entry",
            )
        nonce = reservation["nonce"]
        if second:
            # Persist before any possible signed submission; recovery after an
            # unknown acknowledgement must not fall back to the old exits.
            runtime.update_details(nonce, {"entry_exit_policy": policy["exit"],
                "entry_policy_id": STRATEGY_ID, "target_slot": signal["target_slot"]})
        token_info.additional_data = {
            **(token_info.additional_data or {}),
            "four_strategy_id": signal["strategy"],
            "four_strategy_nonce": nonce,
            "learning_entry": signal,
        }
        # Unreadable/finalized-wrong curve is rejected before signing.
        try:
            state = await runtime.platform_implementations.curve_manager.get_pool_state(
                token_info.bonding_curve, commitment="processed"
            )
            if runtime.four.get("entry_protection", {}).get("enabled"):
                state, evidence = await runtime.check_entry_risk(token_info, state)
                token_info.additional_data["entry_protection"] = evidence
                runtime.update_details(nonce, {"entry_protection": evidence})
            chain_mc = float(state.get("price_per_token") or 0) * 1e9 * runtime.sol_usd
            bounds_ok = (
                0 < chain_mc < policy["max_entry_mc_usd"]
                if second else 8000 <= chain_mc < 21000
                if signal["strategy"] == "strong-pressure-8k"
                else chain_mc >= 21000
                if signal["strategy"] == "strength-21k"
                else chain_mc > 0
            )
            if (
                state.get("complete")
                or not math.isfinite(chain_mc)
                or not bounds_ok
                or runtime.reference_age() > 30
                or not runtime.current_signal(token_info)
                or (second and str(state.get("creator")) != signal["creator"])
            ):
                runtime.book.cancel_unsubmitted(nonce)
                return TradeResult(
                    success=False,
                    error_message="Curve or signal changed before submission",
                )
        except EntryRiskRejected as error:
            runtime.book.cancel_unsubmitted(nonce)
            logger.info("Entry rejected for %s: %s", token_info.mint, error)
            return TradeResult(success=False, error_message=f"Entry protection: {error}")
        except Exception:
            runtime.book.cancel_unsubmitted(nonce)
            raise
        if not runtime.book.submitting(nonce):
            return TradeResult(
                success=False, error_message="Missing shared spend reservation"
            )
        observer_token = OBSERVER.set(runtime.receipt_writer(nonce, "buy"))
        cap_token = window_token = price_token = None
        try:
            if second:
                priority_cap = min(priority_cap, priority_fee_cap_lamports(runtime.sol_usd,
                    quote_age_seconds=runtime.reference_age(), cap_usd=policy["buy_priority_fee_cap_usd"]))
                priority_fee_override = priority_price_micro_lamports(
                    priority_cap, runtime.compute_units.get("buy", 200000))
                cap_token = PRIORITY_CAP.set(priority_cap)
                price_token = BUY_PRICE_CEILING.set(policy["max_entry_mc_usd"] / (runtime.sol_usd*1e9))
                target_slot = signal["target_slot"]
                def in_window() -> bool:
                    latest = runtime.current_signal(token_info)
                    return bool(latest and latest.get("target_slot") == target_slot)
                window_token = ENTRY_WINDOW.set(in_window)
            result = await self.buyer.execute(
                token_info,
                amount_sol=reservation["amount_sol"],
                priority_fee_override=priority_fee_override,
            )
        except BaseException:
            runtime.book.release_unbroadcast(nonce)
            raise
        finally:
            OBSERVER.reset(observer_token)
            if window_token is not None:
                ENTRY_WINDOW.reset(window_token)
            if cap_token is not None:
                PRIORITY_CAP.reset(cap_token)
            if price_token is not None:
                BUY_PRICE_CEILING.reset(price_token)
        # A failed/unknown submission remains occupied until reconciled, never
        # released on a timeout or a missing acknowledgement.
        if result.tx_signature and str(result.tx_signature) not in {
            s for s, _ in runtime.book.signatures(nonce)
        }:
            runtime.book.receipt(nonce, str(result.tx_signature), "buy")
        if result.success and result.tx_signature:
            entry = {
                "entry_price": result.price,
                "original_qty": result.amount,
                "entry_sol_usd": runtime.sol_usd,
                "symbol": token_info.symbol,
                "executed": [],
                "pending_tier": None,
                "stopping": False,
            }
            runtime.book.opened(nonce, 2**63 - 1, entry)
        elif not runtime.book.signatures(nonce):
            runtime.book.release_unbroadcast(nonce)
        return result


class FourStrategyTrader(legacy.UniversalTrader):
    """One feed, one wallet coordinator and per-position exit state."""

    @property
    def _ultimo_cobro(self) -> float:
        return _PROCEEDS.get()

    @_ultimo_cobro.setter
    def _ultimo_cobro(self, value: float) -> None:
        _PROCEEDS.set(value)

    def configure_four(self, settings: dict, *, no_volume_exit_seconds: float = 0,
                       second_block_policy: dict | None = None,
                       evaluation_policy: dict | None = None) -> None:
        if not math.isfinite(no_volume_exit_seconds) or no_volume_exit_seconds < 0:
            raise ValueError("Inactivity exit requires a nonnegative finite duration")
        self.no_volume_exit_seconds = no_volume_exit_seconds
        self.second_policy = second_block_policy or {"enabled": False}
        validate_second_policy(self.second_policy)
        self.second_source = None
        self._inactive_exit_candidates: dict[str, dict] = {}
        self._inactive_exit_checked: dict[str, float] = {}
        self._purchase_notices: set[str] = set()
        validate_policy(settings["entry_protection"])
        if str(self.wallet.pubkey) != settings["expected_wallet"]:
            raise ValueError(
                "Configured private key does not match the requested public wallet"
            )
        if self.paper_trade or self.wallet_pool is not None:
            raise ValueError(
                "The opt-in runtime requires one wallet; use fake brokers in tests"
            )
        if (
            self.max_posiciones != 4
            or self.fraccion_saldo != 0.30
            or self.stop_loss_percentage != 0.25
        ):
            raise ValueError(
                "Four-strategy risk settings differ from the requested policy"
            )
        self.four = settings
        policy = json.dumps(settings, sort_keys=True, allow_nan=False)
        self.book = FourStrategyBook(
            Path(settings["database"]),
            str(self.wallet.pubkey),
            hashlib.sha256(policy.encode()).hexdigest(),
            max_positions=self.max_posiciones,
            evaluation_policy=evaluation_policy,
        )
        self.buyer = SharedBuyer(self, self.buyer)
        self.sol_usd, self.sol_quote_at = 0.0, 0.0
        self._four_tasks: set[asyncio.Task] = set()
        self._four_monitored: set[str] = set()

    async def start(self) -> None:
        publication = self.four.get("callout_publication", {})
        if publication.get("enabled"):
            from monitoring.callout_publisher import optional_browser_service

            self._four_tasks.add(asyncio.create_task(
                optional_browser_service(self.four, self.solana_client.rpc_endpoint)
            ))
        try:
            await super().start()
        finally:
            for task in list(self._four_tasks):
                task.cancel()
            await asyncio.gather(*self._four_tasks, return_exceptions=True)

    def reference_age(self) -> float:
        return time.time() - self.sol_quote_at

    async def check_entry_risk(self, token: object, initial_state: dict) -> tuple[dict, dict]:
        policy = self.four["entry_protection"]

        async def inspect() -> tuple[dict, dict]:
            anchor = float(initial_state.get("price_per_token") or 0)
            if not math.isfinite(anchor) or anchor <= 0 or initial_state.get("complete"):
                raise EntryRiskRejected("invalid_curve_before_risk_check")
            creator = str(initial_state.get("creator") or "")
            if creator != str(getattr(token, "creator", "")):
                raise EntryRiskRejected("creator_metadata_mismatch")
            entry = (token.additional_data or {}).get("learning_entry") or {}
            if entry.get("strategy") == STRATEGY_ID:
                concentration = await concentration_snapshot(token, creator, self.rpc, policy,
                                                             developer=entry["developer"])
            else:
                concentration = await concentration_snapshot(token, creator, self.rpc, policy)
            current = await self.platform_implementations.curve_manager.get_pool_state(
                token.bonding_curve, commitment="processed"
            )
            if current.get("complete") or str(current.get("creator") or "") != creator:
                raise EntryRiskRejected("curve_changed_during_risk_check")
            price = float(current.get("price_per_token") or 0)
            price_evidence = check_price(
                list(volumen._historia_precio.get(str(token.mint), [])), price,
                anchor, time.time(), policy,
            )
            # Use exactly the curve price that passed these checks in the buyer's
            # existing short-lived quote cache, instead of an older builder quote.
            token.additional_data["precio_curva"] = price
            token.additional_data["precio_curva_en"] = time.monotonic()
            return current, {"price": price_evidence, "concentration": concentration}

        try:
            return await asyncio.wait_for(inspect(), timeout=policy["rpc_timeout_seconds"])
        except EntryRiskRejected:
            raise
        except asyncio.TimeoutError as error:
            raise EntryRiskRejected("entry_risk_check_timeout") from error
        except Exception as error:
            raise EntryRiskRejected("unreadable_entry_risk_evidence") from error

    def _cartel_de_configuracion(self) -> str:
        message = (
            f"CONFIG ACTIVA · FOUR STRATEGIES · {self.fraccion_saldo:.0%} del SOL liquido confirmado "
            "por presupuesto, incluidos gastos · 4 plazas compartidas · "
            "presion 8K / Telegram / primer bloque / cruce 21K"
        )
        if getattr(self, "second_policy", {}).get("enabled"):
            message += " · QUINTA: dev/segundo bloque, presupuesto propio 23%"
        return message

    def _cartel_de_salidas(self) -> str:
        message = (
            "SALIDAS FOUR · SL -25% · TP1 +50% vende 50% original; "
            "TP2 punto medio vende 25%; TP3 28K o entrada x2 vende 25% · "
            "pausa independiente con 3 perdidas en los ultimos 5 cierres"
        )
        if getattr(self, "second_policy", {}).get("enabled"):
            policy = self.second_policy["exit"]
            message += (f" · QUINTA: SL -{policy['stop_loss_fraction']:.0%}, "
                f"primera venta {policy['first_sale_mode']}; objetivos +35/+89/+164.6/+270.44%")
        evaluation = getattr(getattr(self, "book", None), "evaluation", None)
        if evaluation:
            message += " · TANDAS: 5 cierres por estrategia; siguiente tanda solo con ROI neto SOL >0"
        return message

    def receipt_writer(self, nonce: str, kind: str) -> Callable[[str, str], None]:
        def write(event: str, signature: str) -> None:
            if event == "signed":
                self.book.receipt(nonce, signature, kind)
            elif event == "preflight_rejected":
                self.book.rejected(nonce, signature)

        return write

    async def rpc(self, method: str, params: list) -> object:
        data = await self.solana_client.post_rpc(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        if not data or data.get("error") or data.get("result") is None:
            raise RuntimeError(f"Required chain evidence unavailable: {method}")
        return data["result"]

    def signal_for(self, mint: str) -> dict | None:
        if self.reference_age() > 30:
            return None
        block = {
            "creation_seen": volumen.vio_la_creacion(mint),
            "age_seconds": volumen.edad(mint),
            "buyers": volumen.carteras_primer_bloque(mint),
            "buy_sol": volumen.sol_primer_bloque(mint),
        }
        signal = choose_signal(
            volumen.metricas_segunda_ola(mint, ventana=8),
            list(volumen._historia_precio.get(mint, [])),
            block,
            volumen.precio_en_vivo(mint, 1),
            self.sol_usd,
            time.time(),
            self.four["strategies"],
        )
        return {**signal, "sol_usd": self.sol_usd} if signal else None

    def current_signal(self, token: object) -> dict | None:
        data = token.additional_data or {}
        if data.get("acquisition_kind") == "second-block-dev":
            source = getattr(self, "second_source", None)
            return source.signal(str(token.mint)) if source else None
        if data.get("acquisition_kind") == "telegram":
            received = float(data.get("signal_received_at", 0))
            if not 0 <= time.time() - received <= 30:
                return None
            return {
                "strategy": "telegram-immediate",
                "source": data.get("acquisition_source"),
                "observed_at": received,
                "sol_usd": self.sol_usd,
            }
        signal = self.signal_for(str(token.mint))
        expected = data.get("four_strategy_id")
        return (
            signal
            if signal and (not expected or signal["strategy"] == expected)
            else None
        )

    async def _run_curva96_mode(self) -> None:
        if self.second_policy.get("enabled"):
            from monitoring.second_block_source import SecondBlockSource

            self.second_source = SecondBlockSource(self.second_policy, self.rpc,
                self.platform_implementations, self.solana_client, lambda: self.sol_usd,
                self.reference_age, lambda: not self.book.paused(STRATEGY_ID), self._on_dexpaid_token)
            logger.info("SECOND BLOCK enabled: 23%% SOL, MC <4200, 2 wallets, $275 first-block volume; inclusion not guaranteed")
        source = PressureTrialSource(
            self.wss_endpoint,
            self.platform_implementations,
            self.solana_client,
            self.signal_for,
            lambda mint: mint in self.bought_mints,
            lambda: True,
            event_observer=self.second_source,
        )

        # Source logs use these fields without imposing its historical policy.
        def read(mint: str) -> dict | None:
            signal = self.signal_for(mint)
            if signal:
                signal["buyers_seen"] = signal["buyers"]
            return signal

        source.signal_reader = read
        pressure = self.four["strategies"]["strong-pressure-8k"]
        source.description = (
            "FOUR STRATEGIES: one on-chain collector; first-block 4 buyers/450 USD, "
            f"pressure {pressure['min_buyers']} buyers/{pressure['min_mc']} USD MC+, "
            "upward crossing 21K; recent flow window 8s"
        )
        telegram = TelegramSource(
            self.telegram_calls,
            self.platform_implementations,
            self.solana_client,
            self.platform,
        )

        async def optional_telegram() -> None:
            try:
                await telegram.run(self._on_dexpaid_token)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "Telegram unavailable (%s); on-chain strategies remain active",
                    type(error).__name__,
                )

        await asyncio.gather(
            source.run(self._on_dexpaid_token), optional_telegram(), self.audit_loop()
        )

    async def _on_dexpaid_token(self, token_info: object) -> None:
        task = asyncio.create_task(self._handle_token(token_info))
        self._four_tasks.add(task)
        task.add_done_callback(self._four_tasks.discard)

    async def _handle_token(self, token_info: object) -> None:
        signal = self.current_signal(token_info)
        if not signal or not self.book.can_admit(signal["strategy"]):
            return
        if (token_info.additional_data or {}).get("migrated"):
            return
        token_info.additional_data = {
            **(token_info.additional_data or {}),
            "four_strategy_id": signal["strategy"],
        }
        await super()._handle_token(token_info)

    async def _precio_sol_loop(self) -> None:
        url = "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            while True:
                try:
                    async with session.get(url) as response:
                        response.raise_for_status()
                        pairs = (await response.json()).get("pairs") or []
                    pairs = [
                        p
                        for p in pairs
                        if (p.get("baseToken") or {}).get("address")
                        == "So11111111111111111111111111111111111111112"
                    ]
                    best = max(
                        pairs,
                        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
                    )
                    price = float(best["priceUsd"])
                    if not math.isfinite(price) or price <= 0:
                        raise ValueError("Invalid SOL/USD price")  # noqa: TRY301
                    self.sol_usd, self.sol_quote_at = price, time.time()
                    legacy._SOL_USD = price
                except Exception as error:
                    logger.warning(
                        "SOL reference unavailable (%s)", type(error).__name__
                    )
                await asyncio.sleep(10)

    def _fee_de_venta_para(self, reason: str) -> int | None:
        if not self._es_venta_protectora(reason):
            return super()._fee_de_venta_para(reason)
        try:
            cap = priority_fee_cap_lamports(
                self.sol_usd, quote_age_seconds=self.reference_age()
            )
            return priority_price_micro_lamports(
                cap, int(self.compute_units.get("sell", 120000))
            )
        except ValueError:
            return 0  # Still allow protective exits, without an unpriced priority fee.

    def _anota_firma(self, mint: str, sig: str | None) -> None:
        super()._anota_firma(mint, sig)
        if sig:
            position = next(
                (p for p in self.book.positions() if p["mint"] == mint), None
            )
            if position and str(sig) not in {
                s for s, _ in self.book.signatures(position["nonce"])
            }:
                self.book.receipt(position["nonce"], str(sig), "sell")

    async def _sell_amount(
        self,
        token_info: object,
        amount: float,
        ref_price: float,
        reason: str,
        *,
        full_exit: bool = False,
    ) -> bool:
        position = next(
            (p for p in self.book.positions() if p["mint"] == str(token_info.mint)),
            None,
        )
        if position is None:
            return False
        observer_token = OBSERVER.set(self.receipt_writer(position["nonce"], "sell"))
        submissions_token = SUBMISSIONS.set(set())
        cap = 0
        if self._es_venta_protectora(reason):
            try:
                cap = priority_fee_cap_lamports(
                    self.sol_usd, quote_age_seconds=self.reference_age()
                )
            except ValueError:
                pass
        cap_token = PRIORITY_CAP.set(cap if self._es_venta_protectora(reason) else None)
        try:
            return await super()._sell_amount(
                token_info, amount, ref_price, reason, full_exit=full_exit
            )
        finally:
            OBSERVER.reset(observer_token)
            PRIORITY_CAP.reset(cap_token)
            SUBMISSIONS.reset(submissions_token)

    def _log_trade(  # noqa: PLR0913, PLR0917 -- legacy override signature
        self,
        action: str,
        token_info: object,
        price: float,
        amount: float,
        tx_hash: str | None,
        reason: str | None = None,
    ) -> None:
        # Separate a durable structured research log from the legacy notifier.
        path = Path(self.four["database"]).parent / "events.jsonl"
        data = token_info.additional_data or {}
        entry = {
            "timestamp": time.time(),
            "strategy_name": data.get("four_strategy_id"),
            "mint": str(token_info.mint),
            "action": action,
            "price": price,
            "amount": amount,
            "signature": str(tx_hash or ""),
            "reason": reason,
            "entry_features": data.get("learning_entry"),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, allow_nan=False) + "\n")
        super()._log_trade(action, token_info, price, amount, tx_hash, reason)

    async def _handle_successful_buy(
        self,
        token_info: object,
        buy_result: TradeResult,
        buy_reason: str | None = None,
        **kwargs: object,
    ) -> None:
        # Start exits immediately; optional labels/Telegram run separately.
        token_info.additional_data["four_purchase_notice"] = True
        signature = str(buy_result.tx_signature or "")
        if signature and signature not in self._purchase_notices:
            self._purchase_notices.add(signature)
            task = asyncio.create_task(self._notify_four_purchase(token_info, buy_result))
            self._four_tasks.add(task)
            task.add_done_callback(self._four_tasks.discard)
        await super()._handle_successful_buy(
            token_info, buy_result, buy_reason, **kwargs
        )

    async def _notify_four_purchase(self, token: object, result: TradeResult) -> None:
        from monitoring.confirmed_callouts import schedule_confirmed_callout
        from monitoring.four_strategy_notifications import (
            purchase_message,
            token_labels,
        )
        from monitoring.notifier import notify

        try:
            token.name, token.symbol = await token_labels(token, self.rpc)
            position = next(
                (p for p in self.book.positions() if p["mint"] == str(token.mint)), None
            )
            details = json.loads(position["details"]) if position else {
                "features": token.additional_data.get("learning_entry", {}),
                "entry_sol_usd": self.sol_usd,
            }
            if position:
                self.update_details(position["nonce"], {"name": token.name, "symbol": token.symbol})
            notify(purchase_message(token, result, details, self.four["exit"]), tipo="compra")
            schedule_confirmed_callout(
                token.name, str(token.mint), str(result.tx_signature),
                outbox=Path(self.four["callout_outbox"]),
                strategy_name=token.additional_data["four_strategy_id"],
                publisher_account=self.four.get("callout_account", ""),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Purchase notice unavailable (%s)", type(error).__name__)

    async def _reanudar_posiciones(self) -> None:
        for position in self.book.positions():
            await self.resume_one(position)

    async def resume_one(self, position: dict) -> None:
        from trading.token_builder import build_pumpfun_token_info

        details = json.loads(position["details"])
        if (
            position["mint"] in self._four_monitored
            or not details.get("entry_price")
            or not details.get("original_qty")
        ):
            return
        token = await build_pumpfun_token_info(
            position["mint"],
            self.platform_implementations,
            self.solana_client,
            permitir_migradas=True,
        )
        if token is None:
            logger.error(
                "Recovery pending for %s; its shared slot remains occupied",
                position["mint"],
            )
            return
        token.additional_data = {
            **(token.additional_data or {}),
            "four_strategy_nonce": position["nonce"],
            "four_strategy_id": position["strategy"],
        }
        result = TradeResult(
            success=True, amount=details["original_qty"], price=details["entry_price"]
        )
        task = asyncio.create_task(self._monitor_tiered_exit(token, result))
        self._four_tasks.add(task)
        task.add_done_callback(self._four_tasks.discard)

    async def raw_holdings(
        self, mint: str, commitment: str = "finalized"
    ) -> tuple[int, int]:
        result = await self.rpc(
            "getTokenAccountsByOwner",
            [
                str(self.wallet.pubkey),
                {"mint": mint},
                {"encoding": "jsonParsed", "commitment": commitment},
            ],
        )
        raw = sum(
            int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
            for a in result["value"]
        )
        return raw, int(result["context"]["slot"])

    async def _monitor_tiered_exit(
        self, token_info: object, buy_result: TradeResult
    ) -> None:
        del buy_result  # The durable ledger owns fills and recovery state.
        mint = str(token_info.mint)
        if mint in self._four_monitored:
            return
        self._four_monitored.add(mint)
        try:
            await self.monitor_position(token_info)
        finally:
            self._four_monitored.discard(mint)

    async def market_price(self, token: object, quantity: float) -> float:
        mint = str(token.mint)
        live = volumen.precio_en_vivo(mint, 1)
        if live and math.isfinite(live) and live > 0:
            return live
        try:
            state = await self.platform_implementations.curve_manager.get_pool_state(
                token.bonding_curve, commitment="processed"
            )
            if not state.get("complete"):
                return float(state.get("price_per_token") or 0)
        except Exception:
            logger.debug(
                "Curve price unavailable for %s; checking routed exit quote", mint
            )
        # Migration must not strand an existing position without a price.
        from trading.jupiter_seller import _cotizar

        raw = max(1, int(quantity * 0.01 * 1e6))
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            quote = await _cotizar(session, mint, raw, 2500)
        return int(quote["outAmount"]) / 1e9 / (raw / 1e6) if quote else 0.0

    async def monitor_position(self, token: object) -> None:  # noqa: C901, PLR0912, PLR0915
        mint = str(token.mint)
        while True:
            position = next(
                (p for p in self.book.positions() if p["mint"] == mint), None
            )
            if position is None:
                getattr(self, "_inactive_exit_candidates", {}).pop(mint, None)
                getattr(self, "_inactive_exit_checked", {}).pop(mint, None)
                self._clear_open_position(mint)
                return
            try:
                details = json.loads(position["details"])
                if not details.get("entry_price") or not details.get("entry_sol_usd"):
                    await asyncio.sleep(1)
                    continue
                quantity = float(details["original_qty"])
                entry = float(details["entry_price"])
                tiers, stop_fraction = position_exit_plan(details, self.four["exit"])
                price = await self.market_price(token, quantity)
                if not price or not math.isfinite(price) or price <= 0:
                    await asyncio.sleep(0.25)
                    continue
                raw, _ = await self.raw_holdings(mint, commitment="confirmed")
                remaining = raw / 1e6
                if raw == 0:
                    await self.audit_position(position)
                    await asyncio.sleep(1)
                    continue
                # Never issue a second sell against holdings that predate a
                # submitted exit. Acknowledged partial fallback also waits here.
                signatures = self.book.signatures(position["nonce"])
                if signatures:
                    statuses = await self.rpc(
                        "getSignatureStatuses",
                        [
                            [s for s, _ in signatures],
                            {"searchTransactionHistory": True},
                        ],
                    )
                    if any(
                        not s
                        or s.get("confirmationStatus") not in {"confirmed", "finalized"}
                        for s in statuses["value"]
                    ):
                        await asyncio.sleep(0.5)
                        continue
                    if len(statuses["value"]) != len(signatures):
                        await asyncio.sleep(0.5)
                        continue
                    raw, context = await self.raw_holdings(mint, commitment="confirmed")
                    if context < max(int(s["slot"]) for s in statuses["value"]):
                        continue
                    remaining = raw / 1e6
                rug = False
                if self.anti_rug_ratio > 0:
                    bought, sold = volumen.presion(mint, self.anti_rug_seg)
                    rug = (
                        sold >= self.anti_rug_min_sol
                        and sold > bought * self.anti_rug_ratio
                    )
                inactive = price < entry and fresh_inactivity(
                    getattr(self, "_inactive_exit_candidates", {}).get(mint),
                    time.time(), getattr(self, "no_volume_exit_seconds", 0),
                )
                stop_price = entry * (1-stop_fraction)
                stopping = details.get("stopping", False) or price <= stop_price or rug or inactive
                if stopping:
                    reason = details.get("stopping_reason") or (
                        "anti_rug" if rug else "stop_loss" if price <= stop_price
                        else "muerta_sin_volumen" if inactive else "stop_loss"
                    )
                    changes = {"stopping": True, "stopping_reason": reason}
                    if inactive:
                        changes["inactivity_exit_evidence"] = self._inactive_exit_candidates[mint]
                    self.update_details(position["nonce"], changes)
                    await self._sell_amount(
                        token,
                        remaining,
                        price,
                        reason,
                        full_exit=True,
                    )
                else:
                    completed = set(details.get("executed", []))
                    index = next((i for i in range(len(tiers)) if i not in completed), None)
                    if index is not None:
                        trigger = entry * (1 + tiers[index].gain_from_entry)
                        pending = details.get("pending_tier") == index
                        if price >= trigger or pending:
                            if index == 0 and details.get("entry_exit_policy") and "exit_allocations" not in details:
                                allocations = recovery_allocations(details["entry_exit_policy"], entry, price)
                                self.update_details(position["nonce"], {"exit_allocations": allocations})
                                details["exit_allocations"] = allocations
                                tiers, stop_fraction = position_exit_plan(details, self.four["exit"])
                            target_remaining = quantity * (
                                1
                                - sum(
                                    t.sell_original_fraction for t in tiers[: index + 1]
                                )
                            )
                            amount = max(0, remaining - target_remaining)
                            if amount <= 0.000001:
                                self.update_details(
                                    position["nonce"],
                                    {
                                        "executed": sorted(completed | {index}),
                                        "pending_tier": None,
                                    },
                                )
                            else:
                                self.update_details(
                                    position["nonce"], {"pending_tier": index}
                                )
                                await self._sell_amount(
                                    token,
                                    amount,
                                    price,
                                    f"take_profit_{index + 1}",
                                    full_exit=index == len(tiers)-1,
                                )
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "Position %s still protected; retry after %s",
                    mint,
                    type(error).__name__,
                )
                await asyncio.sleep(1)

    def update_details(self, nonce: str, changes: dict) -> None:
        from contextlib import closing

        with closing(self.book.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT details FROM positions WHERE nonce=?", (nonce,)
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE positions SET details=? WHERE nonce=?",
                    (
                        json.dumps({**json.loads(row[0]), **changes}, allow_nan=False),
                        nonce,
                    ),
                )

    async def audit_position(self, position: dict) -> None:  # noqa: C901, PLR0911, PLR0912
        signatures = self.book.signatures(position["nonce"])
        if not signatures or not any(
            kind in {"buy", "failed"} for _, kind in signatures
        ):
            return
        statuses = await self.rpc(
            "getSignatureStatuses",
            [[s for s, _ in signatures], {"searchTransactionHistory": True}],
        )
        values = statuses["value"]
        if len(values) != len(signatures) or any(
            not s or s.get("confirmationStatus") != "finalized" for s in values
        ):
            return
        buys = [
            status
            for (_, kind), status in zip(signatures, values, strict=True)
            if kind == "buy"
        ]
        if buys and buys[0].get("err") is None:
            # Once this buy is finalized its debit may be reflected in newer
            # balance snapshots; never subtract the pending budget twice.
            from contextlib import closing

            with closing(self.book.connect()) as db, db:
                db.execute(
                    "UPDATE positions SET debit_slot=? WHERE nonce=?",
                    (int(buys[0]["slot"]), position["nonce"]),
                )
        raw, slot = await self.raw_holdings(position["mint"])
        details = json.loads(position["details"])
        if details.get("entry_policy_id") == STRATEGY_ID and buys and "actual_buy_slot" not in details:
            actual_slot = int(buys[0]["slot"])
            self.update_details(position["nonce"], {"actual_buy_slot": actual_slot,
                "target_slot_matched": actual_slot == details.get("target_slot")})
        if slot < max(int(s["slot"]) for s in values):
            return
        if raw > 0:
            if buys and buys[0].get("err") is None and not details.get("entry_price"):
                signature = next(s for s, kind in signatures if kind == "buy")
                tx = await self.rpc(
                    "getTransaction",
                    [
                        signature,
                        {
                            "encoding": "jsonParsed",
                            "commitment": "finalized",
                            "maxSupportedTransactionVersion": 1,
                        },
                    ],
                )
                owner = str(self.wallet.pubkey)
                quantity_raw = sum(
                    int(r["uiTokenAmount"]["amount"])
                    for r in tx["meta"].get("postTokenBalances", [])
                    if r.get("owner") == owner and r.get("mint") == position["mint"]
                )
                keys = [
                    str(k.get("pubkey") if isinstance(k, dict) else k)
                    for k in tx["transaction"]["message"]["accountKeys"]
                ]
                owner_index = keys.index(owner)
                spent = int(tx["meta"]["preBalances"][owner_index]) - int(
                    tx["meta"]["postBalances"][owner_index]
                )
                if quantity_raw > 0 and spent > 0:
                    self.book.opened(
                        position["nonce"],
                        int(buys[0]["slot"]),
                        {
                            "entry_price": spent / 1e9 / (quantity_raw / 1e6),
                            "original_qty": quantity_raw / 1e6,
                            "entry_sol_usd": details["features"]["sol_usd"],
                            "executed": [],
                            "pending_tier": None,
                            "stopping": False,
                        },
                    )
            updated = next(
                (p for p in self.book.positions() if p["nonce"] == position["nonce"]),
                None,
            )
            if updated:
                await self.resume_one(updated)
            return
        if (
            buys
            and buys[0].get("err") is None
            and not any(kind == "sell" for _, kind in signatures)
        ):
            return  # An unrelated manual exit cannot supply attributed P&L.
        net, buy_debit, buy_debit_verified = 0, 0, True
        for (signature, kind), status in zip(signatures, values, strict=True):
            tx = await self.rpc(
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "commitment": "finalized",
                        "maxSupportedTransactionVersion": 1,
                    },
                ],
            )
            if int(tx["slot"]) != int(status["slot"]):
                return
            if tx["meta"].get("err") != status.get("err"):
                return
            keys = [
                str(k.get("pubkey") if isinstance(k, dict) else k)
                for k in tx["transaction"]["message"]["accountKeys"]
            ]
            owner_index = keys.index(str(self.wallet.pubkey))
            native = int(tx["meta"]["postBalances"][owner_index]) - int(
                tx["meta"]["preBalances"][owner_index]
            )
            net += native
            if kind == "buy":
                buy_debit_verified = buy_debit_verified and native < 0
                buy_debit += max(0, -native)
        # Serialize against receipt writers: new receipt means the audit must
        # be repeated before a slot or a loss-window outcome can be released.
        if self.book.signatures(position["nonce"]) != signatures:
            return
        self.book.settle(
            position["nonce"],
            net,
            slot,
            {
                "verified_zero_holdings": True,
                "commitment": "finalized",
                "signatures": [s for s, _ in signatures],
                "buy_debit_lamports": buy_debit,
                "buy_debit_verified": buy_debit_verified and buy_debit > 0,
            },
        )
        report = Path(self.four["database"]).parent / "report.json"
        temporary = report.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.book.research(), indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(report)

    async def audit_loop(self) -> None:
        while True:
            for position in self.book.positions():
                try:
                    await self.audit_position(position)
                    await self.refresh_inactivity(position)
                except Exception as error:
                    logger.warning("Closure audit pending (%s)", type(error).__name__)
            await asyncio.sleep(5)

    async def refresh_inactivity(self, position: dict) -> None:
        """Probe in the audit task so slow history reads never block TP/SL."""
        seconds = self.no_volume_exit_seconds
        mint = position["mint"]
        now = time.time()
        if seconds <= 0 or not seconds <= now - position["created"]:
            return
        if now - self._inactive_exit_checked.get(mint, 0) < 15:
            return
        self._inactive_exit_checked[mint] = now
        self._inactive_exit_candidates.pop(mint, None)
        details = json.loads(position["details"])
        entry = float(details.get("entry_price") or 0)
        derive_curve = self.platform_implementations.address_provider.derive_pool_address
        # Resolve the same verified curve PDA used by the platform, not a DEX feed.
        from solders.pubkey import Pubkey

        curve = derive_curve(Pubkey.from_string(mint))
        if entry <= 0:
            return
        try:
            evidence = await asyncio.wait_for(
                inactive_curve_evidence(
                    curve, entry, seconds, self.rpc, self.platform_implementations.curve_manager
                ), timeout=3,
            )
            if evidence:
                self._inactive_exit_candidates[mint] = evidence
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.debug("Inactivity evidence unavailable (%s)", type(error).__name__)
