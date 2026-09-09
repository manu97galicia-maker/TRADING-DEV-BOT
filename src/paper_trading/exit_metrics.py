"""Aggregate persisted paper exits for TP/SL calibration."""

from __future__ import annotations

import statistics
from typing import Any


def _median(records: list[dict[str, Any]], field: str) -> float:
    """Return the median numeric value for a result field, or zero."""
    values = [
        float(record[field]) for record in records if record.get(field) is not None
    ]
    return statistics.median(values) if values else 0.0


def summarize_exit_records(records: list[dict[str, Any]]) -> dict[str, float | int]:
    """Summarize paper result rows used to tune take-profit and stop levels."""
    completed = [record for record in records if record.get("roi_pct") is not None]
    wins = sum(bool(record.get("win")) for record in completed)
    stops = sum(record.get("exit_reason") == "stop" for record in completed)
    return {
        "trades": len(completed),
        "wins": wins,
        "losses": len(completed) - wins,
        "win_rate_pct": 100 * wins / len(completed) if completed else 0.0,
        "stop_rate_pct": 100 * stops / len(completed) if completed else 0.0,
        "median_roi_pct": _median(completed, "roi_pct"),
        "median_peak_pct": _median(completed, "peak_pct"),
        "median_trough_pct": _median(completed, "trough_pct"),
        "median_stop_exit_pct": _median(completed, "stop_exit_pct"),
        "median_tp_hits": _median(completed, "tp_hits"),
    }
