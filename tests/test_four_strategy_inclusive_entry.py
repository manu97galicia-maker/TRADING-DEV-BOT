"""Exercise the inherited entry handler with fake I/O and no signing/network."""

# Explicit fake runtime state keeps construction free of wallet/client side effects.
# ruff: noqa: ANN001, ANN201, ARG001, PLR0915, SLF001

import asyncio
import copy
import tempfile
import time
import unittest
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import yaml

import trading.universal_trader as legacy
from interfaces.core import Platform
from trading.base import TradeResult
from trading.five_trade_evaluation import STRATEGIES
from trading.four_strategy_book import FourStrategyBook
from trading.four_strategy_runtime import FourStrategyTrader, SharedBuyer
from trading.second_block_policy import STRATEGY_ID

ROOT = Path(__file__).resolve().parents[1]
SOL = 1_000_000_000
SECOND_POLICY = yaml.safe_load(
    (ROOT / "configs/second-block-dev.yaml").read_text(encoding="utf-8")
)


class InheritedInclusiveEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.book = FourStrategyBook(
            Path(directory.name) / "fake-entry.sqlite3", "fake-owner", "fake-policy",
            max_positions=4,
        )

    def fixture(self, strategy="strong-pressure-8k", balance=SOL):
        # __init__ owns live clients and wallet loading; this fixture never calls it.
        trader = object.__new__(FourStrategyTrader)
        for name in (
            "no_repeat_name_min", "max_buy_age", "min_curve_sol", "max_entry_mc",
            "min_entry_mc", "min_volumen_sol", "max_volumen_sol", "min_holders_gmgn",
            "max_holder_pct", "min_holders", "max_real_buys", "mayhem_buy_amount",
            "buy_amount_marcados", "buy_amount", "buy_amount_tras_sl", "fee_compra",
            "tope_compra_sol", "tope_compra_total_sol", "_reserved_lamports",
            "_reserved_real_buy_slots", "buy_amount_usd", "compra_minima_sol",
            "fraccion_si_no_llega", "wait_time_before_new_token",
        ):
            setattr(trader, name, 0)
        trader.telegram_only = False
        trader.nombre_estrategia = "fake-four-strategies"
        trader.platform = Platform.PUMP_FUN
        trader.bought_mints = set()
        trader.ignorar_listas_de_devs = True
        trader.allow_mayhem = False
        trader._parado_por_perdidas = False
        trader._mayhem_cerrado = False
        trader.extreme_fast_mode = True
        trader.redes_originales = False
        trader.dev_buy = {}
        trader.pumpfun_dev = {}
        trader.paper_trade = False
        trader.paper_engine = SimpleNamespace(enabled=False)
        trader.senal_gmgn = None
        trader._filter_semaphore = asyncio.Semaphore(8)
        trader._position_semaphore = asyncio.Semaphore(4)
        trader._spend_lock = asyncio.Lock()
        trader._ultimo_cierre_fue_sl = False
        trader._mints_elite = set()
        trader._bote = None
        trader._selector = None
        trader.wallet_pool = None
        trader.pausar_compras = False
        trader.yolo_mode = False
        trader.fraccion_saldo = .90
        trader.fracciones_saldo = []
        trader.fraccion_base = "libre"
        trader.buy_slippage = .15
        trader.min_reserve_lamports = 30_000_000
        trader._fee_headroom_lamports = 3_000_000
        trader.wallet = SimpleNamespace(pubkey="fake-owner")
        trader.sol_usd, trader.sol_quote_at = 100.0, time.time()
        trader.compute_units = {"buy": 200000}
        trader.book = self.book
        trader.four = {"entry_protection": {"enabled": False}}
        trader.second_policy = {**copy.deepcopy(SECOND_POLICY), "enabled": True,
                                "entry_timing": "any_verified_time", "require_dev_initial_buy": False}
        trader.current_signal = Mock(return_value={
            "strategy": strategy, "creator": "fake-creator", "developer": "fake-dev",
            "target_slot": 101, "sol_usd": 100,
        })
        trader.raw_holdings = AsyncMock(return_value=(0, 100))
        trader.rpc = AsyncMock(return_value={"value": balance, "context": {"slot": 100}})
        trader.solana_client = SimpleNamespace(
            get_sol_balance=AsyncMock(return_value=balance),
            get_account_info=AsyncMock(return_value=SimpleNamespace(owner=legacy.TOKEN_PROGRAM)),
        )
        price = 3e-8 if strategy == STRATEGY_ID else 2.2e-7 if strategy == "strength-21k" else 1e-7
        trader.platform_implementations = SimpleNamespace(curve_manager=SimpleNamespace(
            get_pool_state=AsyncMock(return_value={
                "price_per_token": price, "complete": False, "creator": "fake-creator",
            }),
        ))
        # Unrelated filters/notifications have deterministic local substitutes.
        trader._pasa_los_frenos_de_rug = AsyncMock(return_value=True)
        trader._dentro_del_horario = Mock(return_value=True)
        trader._notify_opportunity = Mock()
        trader._dev_holds_too_much = AsyncMock(return_value=False)
        trader.dex_filter = SimpleNamespace(should_buy=AsyncMock(return_value=True))
        trader._otro_origen_agotado = Mock(return_value=False)
        trader._curva96_agotada = Mock(return_value=False)
        trader._avisa_si_el_nodo_no_da_mas = Mock()
        trader._handle_failed_buy = AsyncMock()
        trader._handle_successful_buy = AsyncMock()
        token = SimpleNamespace(
            mint="fake-mint", user="fake-dev", creator="fake-creator", symbol="FAKE",
            platform=Platform.PUMP_FUN, token_program_id=legacy.TOKEN_PROGRAM,
            bonding_curve="fake-curve", additional_data={},
        )
        broker = SimpleNamespace(execute=AsyncMock(return_value=TradeResult(
            success=False, error_message="Intentional fake broker rejection",
        )))
        trader.buyer = SharedBuyer(trader, broker)
        return trader, token, broker

    async def handle(self, trader, token):
        # The two real handlers and SharedBuyer remain in the call chain. Only
        # filesystem mint coordination is replaced; no persistent trading files.
        with (
            patch.object(legacy, "_reserva_moneda", return_value=True),
            patch.object(legacy, "_suelta_moneda") as release_mint,
            patch.object(legacy, "logger") as logger,
        ):
            logger.exception.side_effect = AssertionError("Entry handler raised unexpectedly")
            await asyncio.wait_for(trader._handle_token(token), timeout=2)
        logger.exception.assert_not_called()
        return logger, release_mint

    async def test_current_90_percent_settings_reach_broker_for_every_source(self) -> None:
        for strategy, balance in product(STRATEGIES, (SOL, 362_187_680)):
            with self.subTest(strategy=strategy, balance=balance):
                trader, token, broker = self.fixture(strategy, balance)
                during_buy = {}

                async def reject_without_broadcast(
                    token, _trader=trader, _during_buy=during_buy, **kwargs: object,
                ) -> TradeResult:
                    _during_buy.update(
                        nominal=kwargs["amount_sol"],
                        reserved=_trader._reserved_lamports,
                        slots=_trader._reserved_real_buy_slots,
                        position=self.book.positions()[0],
                    )
                    return TradeResult(success=False, error_message="Fake: never broadcast")

                broker.execute.side_effect = reject_without_broadcast
                logger, release_mint = await self.handle(trader, token)
                broker.execute.assert_awaited_once()
                fraction_tenths = 8 if strategy == STRATEGY_ID else 9
                if strategy == STRATEGY_ID:
                    self.assertGreater(during_buy["nominal"], .15)
                    self.assertLessEqual(during_buy["position"]["budget"], 200_000_000)
                else:
                    self.assertGreater(during_buy["nominal"], balance / SOL * .70)
                self.assertLess(during_buy["nominal"], balance / SOL * fraction_tenths/10)
                self.assertLessEqual(during_buy["reserved"], balance * fraction_tenths // 10)
                self.assertLessEqual(during_buy["position"]["budget"], balance * fraction_tenths // 10)
                self.assertEqual(during_buy["reserved"], during_buy["position"]["budget"])
                self.assertEqual(during_buy["slots"], 1)
                # Both coordinators must release a failed, never-signed attempt.
                self.assertEqual(trader._reserved_lamports, 0)
                self.assertEqual(trader._reserved_real_buy_slots, 0)
                self.assertFalse(self.book.positions())
                release_mint.assert_called_once_with("fake-mint")
                trader._handle_failed_buy.assert_awaited_once()
                trader._handle_successful_buy.assert_not_awaited()
                self.assertFalse(any(
                    "SOL insuficiente" in str(call) for call in logger.warning.call_args_list
                ))

    async def test_balance_that_cannot_preserve_minimum_reserve_never_reaches_broker(self) -> None:
        for balance in (29_000_000, 290_000_000):
            with self.subTest(balance=balance):
                trader, token, broker = self.fixture(balance=balance)
                logger, release_mint = await self.handle(trader, token)
                broker.execute.assert_not_awaited()
                trader.rpc.assert_not_awaited()
                self.assertEqual(trader._reserved_lamports, 0)
                self.assertEqual(trader._reserved_real_buy_slots, 0)
                self.assertFalse(self.book.positions())
                release_mint.assert_called_once_with("fake-mint")
                self.assertTrue(any(
                    "SOL insuficiente" in str(call) for call in logger.warning.call_args_list
                ))

    async def test_handler_without_inclusive_hook_keeps_legacy_spend_check(self) -> None:
        trader, token, broker = self.fixture()
        trader._inclusive_entry_budget = None
        logger, _ = await self.handle(trader, token)
        broker.execute.assert_not_awaited()
        self.assertFalse(self.book.positions())
        self.assertEqual(trader._reserved_lamports, 0)
        self.assertTrue(any(
            "SOL insuficiente" in str(call) for call in logger.warning.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
