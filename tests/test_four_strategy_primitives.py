"""Exercise shipped budget/precision code without deployment snapshot fixtures."""

import ast
import logging
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from trading.buy_budget import inclusive_balance_entry
from trading.sell_precision import align_full_sell, raw_sell_amount


class BudgetTests(unittest.TestCase):
    def test_twenty_percent_includes_slippage_and_costs(self):
        for balance in (50_000_000, 224_361_565, 1_000_000_000):
            for margin in (0, .15, .25):
                amount, reserved = inclusive_balance_entry(balance, .20, margin, 3_000_000)
                limit = int(Decimal(balance) * Decimal('.20'))
                self.assertGreater(amount, 0)
                self.assertLessEqual(reserved, limit)
                self.assertLessEqual(int(int(amount * 1e9) * (1 + margin)) + 3_000_000, limit)

    def test_fee_only_entry_is_rejected(self):
        self.assertEqual(inclusive_balance_entry(1_000_000, .20, .15, 3_000_000), (0, 0))

    def test_invalid_budget_is_rejected(self):
        for args in ((-1, .20, .15, 1), (100, 1.1, .15, 1), (100, .20, -.1, 1)):
            with self.assertRaises(ValueError):
                inclusive_balance_entry(*args)

    def test_optional_absolute_cap_is_preserved(self):
        amount, reserved = inclusive_balance_entry(1_000_000_000, .20, .15, 3_000_000, .05)
        self.assertEqual(amount, .05)
        self.assertEqual(reserved, 60_500_000)


class PrecisionTests(unittest.TestCase):
    def test_final_sale_sweeps_one_atom_residue(self):
        bought = 672131134266
        partial = raw_sell_amount(537704.9074128, 6)
        remaining = 134426.2268532
        self.assertEqual(bought - partial - raw_sell_amount(remaining, 6), 1)
        final, complete = align_full_sell(remaining, (bought - partial) / 1e6, 6)
        self.assertTrue(complete)
        self.assertEqual(bought - partial - raw_sell_amount(final, 6), 0)

    def test_partial_preserves_remaining_position(self):
        self.assertEqual(align_full_sell(80, 100, 6), (80, False))
        self.assertEqual(raw_sell_amount(537704.9074128, 6), 537704907412)

    def test_explicit_full_exit_uses_all_current_tokens(self):
        self.assertEqual(align_full_sell(90, 100, 6, full_exit=True), (100, True))


class InheritedSellerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'src/trading/universal_trader.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'UniversalTrader')
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_sell_amount')
        future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
        namespace = {'logger': logging.getLogger('four_strategy_precision'), 'TOKEN_DECIMALS': 6}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future, method], type_ignores=[])), str(source), 'exec'), namespace)
        self.method = namespace['_sell_amount']
        result = SimpleNamespace(success=True, amount=100, price=.01, tx_signature='fake-sale')
        self.seller = SimpleNamespace(execute=AsyncMock(return_value=result))
        self.runtime = SimpleNamespace(
            _bloqueada_hasta={}, _fallos_venta={}, _saldo_real_tokens=AsyncMock(return_value=100),
            _vendedor_para=Mock(return_value=self.seller), _fee_de_venta_para=Mock(return_value=None),
            _log_trade=Mock(), _anota_firma=Mock(),
        )
        self.token = SimpleNamespace(mint='mint', symbol='TOKEN')

    async def test_full_exit_passes_real_balance_to_seller(self):
        self.assertTrue(await self.method(self.runtime, self.token, 90, .01, 'stop_loss', full_exit=True))
        self.assertEqual(self.seller.execute.call_args.kwargs['token_amount'], 100)

    async def test_partial_exit_keeps_original_quantity(self):
        self.assertTrue(await self.method(self.runtime, self.token, 50, .01, 'take_profit_1'))
        self.assertEqual(self.seller.execute.call_args.kwargs['token_amount'], 50)


if __name__ == '__main__':
    unittest.main()
