"""Live observation engine. This module contains no transaction code."""

from __future__ import annotations

import asyncio
import html
import time
from collections.abc import Awaitable, Callable
from typing import Any

from monitoring.notifier import notify
from paper_trading.analytics import simulate_pair, summarize
from paper_trading.history import DevHistory
from paper_trading.models import Candidate, DevStats, Strategy
from paper_trading.store import PaperStore
from utils.logger import get_logger

logger = get_logger(__name__)
PriceReader = Callable[[], Awaitable[float]]


class UnsafePaperConfigError(ValueError):
    """Raised when a paper configuration requests real trading."""


class PaperTradingEngine:
    """Filter, deduplicate, observe, persist and report simulated positions."""

    def __init__(self, config: dict[str, Any] | None) -> None:
        cfg = config or {}
        if bool(cfg.get("real_trading", False)):
            message = "paper_trading.real_trading is forbidden"
            raise UnsafePaperConfigError(message)
        self.enabled = bool(cfg.get("enabled", False))
        pairs = tuple(
            (float(item["tp"]), float(item["sl"]))
            for item in cfg.get(
                "tp_sl_pairs",
                [{"tp": 0.25, "sl": 0.15}, {"tp": 0.5, "sl": 0.2}],
            )
        )
        self.strategy = Strategy(
            max_entry_mc=float(cfg.get("max_entry_mc", 4_000)),
            min_dev_mean_mc=float(cfg.get("min_dev_mean_mc", 12_000)),
            min_dev_launches=int(cfg.get("min_dev_launches", 5)),
            checkpoints=tuple(
                int(x) for x in cfg.get("checkpoints", [60, 180, 300, 600, 1800])
            ),
            tp_sl_pairs=pairs,
            poll_seconds=float(cfg.get("poll_seconds", 5)),
        )
        self.store = PaperStore(cfg.get("database", "paper_trades/paper.sqlite3"))
        self.history = DevHistory(
            cfg.get("dev_history", "paper_trades/dev_history.jsonl"),
            aggregate_peak_field=str(cfg.get("aggregate_peak_field", "")),
        )
        self.history_metric_label = str(cfg.get("history_metric_label", "media"))
        self._report_task: asyncio.Task | None = None
        self.starting_capital = float(cfg.get("starting_capital_sol", 2.0))
        self.trade_size = float(cfg.get("trade_size_sol", 0.25))
        if self.starting_capital <= 0 or not 0 < self.trade_size <= self.starting_capital:
            message = "paper capital and trade size must be positive and coherent"
            raise ValueError(message)
        self._capital_lock = asyncio.Lock()
        self._reserved = 0.0
        self._realized_pnl = sum(
            float(row["result"].get("pnl_sol", 0.0))
            for row in self.store.rows()
            if row.get("result")
        )

    def start(self) -> None:
        """Start reporting and announce that the simulator is online."""
        if not self.enabled:
            return
        self._ensure_reporter()
        notify(
            "🟢 <b>SIMULACIÓN PAPER ACTIVA</b>\n"
            f"Banca: {self.starting_capital + self._realized_pnl:.4f} SOL · "
            f"entrada {self.trade_size:.2f} SOL\n"
            "Trading real bloqueado · resumen cada hora"
        )

    async def consider(self, candidate: Candidate, price_reader: PriceReader) -> bool:
        """Record and start observing a passing candidate, without trading."""
        if not self.enabled:
            return False
        dev = self.history.before(candidate.dev, candidate.signal_ts, candidate.mint)
        if not self.strategy.accepts(candidate, dev):
            return False
        async with self._capital_lock:
            available = self.starting_capital + self._realized_pnl - self._reserved
            if available + 1e-9 < self.trade_size:
                logger.info("Paper signal skipped: insufficient simulated capital")
                return False
            created = self.store.create(
                {
                    "mint": candidate.mint,
                    "signal_ts": candidate.signal_ts,
                    "symbol": candidate.symbol,
                    "dev": candidate.dev,
                    "entry_mc": candidate.market_cap,
                    "dev_mean_mc": dev.mean_peak_mc,
                    "dev_launches": dev.launches,
                    "best_peak_mc": dev.best_peak_mc,
                    "best_symbol": dev.best_symbol,
                    "features": candidate.features,
                }
            )
            if created:
                self._reserved += self.trade_size
        if not created:
            return True
        self._ensure_reporter()
        notify(self._entry_message(candidate, dev))
        asyncio.create_task(self._follow(candidate, price_reader))  # noqa: RUF006
        return True

    def _ensure_reporter(self) -> None:
        if self._report_task is None or self._report_task.done():
            self._report_task = asyncio.create_task(self._report_loop())

    async def _follow(self, candidate: Candidate, price_reader: PriceReader) -> None:
        start = time.time()
        samples = [(0.0, 0.0)]
        checkpoints: dict[str, float | None] = {
            str(value): None for value in self.strategy.checkpoints
        }
        max_seconds = max(self.strategy.checkpoints)
        try:
            while time.time() - start < max_seconds:
                await asyncio.sleep(self.strategy.poll_seconds)
                market_cap = await price_reader()
                if market_cap <= 0:
                    continue
                elapsed = time.time() - start
                ret = market_cap / candidate.market_cap - 1
                samples.append((elapsed, ret))
                for seconds in self.strategy.checkpoints:
                    key = str(seconds)
                    if checkpoints[key] is None and elapsed >= seconds:
                        checkpoints[key] = ret
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Paper price observation failed for %s", candidate.mint)
        result = self._result(samples, checkpoints)
        primary_return = float(result["strategies"]["primary"]["return"])
        result["stake_sol"] = self.trade_size
        result["pnl_sol"] = self.trade_size * primary_return
        async with self._capital_lock:
            self._reserved = max(0.0, self._reserved - self.trade_size)
            self._realized_pnl += result["pnl_sol"]
            result["capital_sol"] = self.starting_capital + self._realized_pnl
        self.store.close(candidate.mint, time.time(), result)
        notify(self._result_message(candidate, result))

    def _result(
        self, samples: list[tuple[float, float]], checkpoints: dict[str, float | None]
    ) -> dict[str, Any]:
        strategies = {}
        for index, (tp, sl) in enumerate(self.strategy.tp_sl_pairs):
            key = "primary" if index == 0 else f"tp{tp:g}_sl{sl:g}"
            strategies[key] = {"tp": tp, "sl": sl, **simulate_pair(samples, tp, sl)}
        returns = [ret for _, ret in samples]
        return {
            "checkpoints": checkpoints,
            "mfe": max(returns),
            "mae": min(returns),
            "strategies": strategies,
            "samples": len(samples),
        }

    async def _report_loop(self) -> None:
        last_day = ""
        while True:
            await asyncio.sleep(3_600)
            now = time.time()
            notify(self._summary_message("HORARIO", self.store.rows(now - 3_600)))
            day = time.strftime("%Y-%m-%d", time.localtime(now))
            if day != last_day:
                local = time.localtime(now)
                midnight = now - (
                    local.tm_hour * 3600 + local.tm_min * 60 + local.tm_sec
                )
                notify(self._summary_message("DIARIO", self.store.rows(midnight)))
                last_day = day

    def _entry_message(self, candidate: Candidate, dev: DevStats) -> str:
        mint = html.escape(candidate.mint)
        return (
            f"🧪 <b>ENTRADA SIMULADA</b> · <b>{html.escape(candidate.symbol)}</b>\n"
            f"MC ${candidate.market_cap:,.0f} · dev <code>{html.escape(candidate.dev)}</code>\n"
            f"{html.escape(self.history_metric_label).capitalize()} histórica "
            f"${dev.mean_peak_mc:,.0f} · {dev.launches} lanzamientos\n"
            f"Mejor: {html.escape(dev.best_symbol) or '—'} ${dev.best_peak_mc:,.0f}\n"
            f"Entrada: {self.trade_size:.2f} SOL · banca inicial {self.starting_capital:.2f} SOL\n"
            f'<a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
            f'<a href="https://pump.fun/{mint}">Pump.fun</a>\nCA: <code>{mint}</code>'
        )

    @staticmethod
    def _result_message(candidate: Candidate, result: dict[str, Any]) -> str:
        primary = result["strategies"]["primary"]
        points = " · ".join(
            f"{int(seconds) // 60}m {value:+.1%}"
            if value is not None
            else f"{int(seconds) // 60}m —"
            for seconds, value in result["checkpoints"].items()
        )
        return (
            f"📋 <b>RESULTADO PAPER</b> · <b>{html.escape(candidate.symbol)}</b>\n"
            f"{points}\nMFE {result['mfe']:+.1%} · MAE {result['mae']:+.1%}\n"
            f"TP {primary['tp']:.0%}/SL {primary['sl']:.0%}: "
            f"{primary['exit']} {primary['return']:+.1%} en {primary['time']:.0f}s\n"
            f"PnL {result['pnl_sol']:+.4f} SOL · banca {result['capital_sol']:.4f} SOL"
        )

    def _summary_message(self, label: str, rows: list[dict[str, Any]]) -> str:
        stats = summarize(rows)
        period_pnl = sum(
            float(row["result"].get("pnl_sol", 0.0))
            for row in rows
            if row.get("result")
        )
        return (
            f"📊 <b>RESUMEN {label}</b>\nSeñales {stats['signals']} · cerradas {stats['closed']}\n"
            f"Win rate {stats['win_rate']:.1%} · EV {stats['ev']:+.2%}\n"
            f"Media {stats['mean_return']:+.2%} · mediana {stats['median_return']:+.2%} · "
            f"drawdown {stats['max_drawdown']:.2%}\n"
            f"PnL periodo {period_pnl:+.4f} SOL · banca "
            f"{self.starting_capital + self._realized_pnl:.4f} SOL"
        )
