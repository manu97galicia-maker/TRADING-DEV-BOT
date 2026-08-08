"""Path-dependent exit simulation and portfolio summaries."""

from __future__ import annotations

import statistics
from typing import Any


def simulate_pair(
    samples: list[tuple[float, float]], tp: float, sl: float
) -> dict[str, Any]:
    """Simulate TP/SL in timestamp order; the first touch wins."""
    for elapsed, ret in samples:
        if ret >= tp:
            return {"return": tp, "exit": "tp", "time": elapsed}
        if ret <= -sl:
            return {"return": -sl, "exit": "sl", "time": elapsed}
    elapsed, ret = samples[-1]
    return {"return": ret, "exit": "timeout", "time": elapsed}


def summarize(
    rows: list[dict[str, Any]], result_key: str = "primary"
) -> dict[str, Any]:
    """Compute signal, return, EV and equity-curve drawdown statistics."""
    returns = [
        float(row["result"]["strategies"][result_key]["return"])
        for row in rows
        if row.get("result") and result_key in row["result"].get("strategies", {})
    ]
    equity = peak = 0.0
    max_drawdown = 0.0
    for ret in returns:
        equity += ret
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    wins = sum(ret > 0 for ret in returns)
    return {
        "signals": len(rows),
        "closed": len(returns),
        "win_rate": wins / len(returns) if returns else 0.0,
        "ev": statistics.fmean(returns) if returns else 0.0,
        "mean_return": statistics.fmean(returns) if returns else 0.0,
        "median_return": statistics.median(returns) if returns else 0.0,
        "max_drawdown": max_drawdown,
    }
