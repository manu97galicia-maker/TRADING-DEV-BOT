"""Tests for paper exit metrics used to tune TP and SL levels."""

import pytest

from paper_trading.exit_metrics import summarize_exit_records


def test_exit_summary_uses_medians_and_explicit_outcomes() -> None:
    records = [
        {
            "roi_pct": 17.5,
            "peak_pct": 31.0,
            "trough_pct": -5.0,
            "stop_exit_pct": None,
            "tp_hits": 1,
            "exit_reason": "partial_take_profit",
            "win": True,
        },
        {
            "roi_pct": -18.0,
            "peak_pct": 4.0,
            "trough_pct": -24.0,
            "stop_exit_pct": -18.0,
            "tp_hits": 0,
            "exit_reason": "stop",
            "win": False,
        },
        {
            "roi_pct": 22.0,
            "peak_pct": 65.0,
            "trough_pct": -2.0,
            "stop_exit_pct": 25.0,
            "tp_hits": 2,
            "exit_reason": "stop",
            "win": True,
        },
    ]

    summary = summarize_exit_records(records)

    assert summary["trades"] == 3
    assert summary["wins"] == 2
    assert summary["win_rate_pct"] == pytest.approx(66.6666667)
    assert summary["stop_rate_pct"] == pytest.approx(66.6666667)
    assert summary["median_roi_pct"] == pytest.approx(17.5)
    assert summary["median_peak_pct"] == pytest.approx(31.0)
    assert summary["median_trough_pct"] == pytest.approx(-5.0)
    assert summary["median_stop_exit_pct"] == pytest.approx(3.5)
    assert summary["median_tp_hits"] == pytest.approx(1.0)


def test_empty_exit_summary_is_zeroed() -> None:
    summary = summarize_exit_records([])

    assert summary["trades"] == 0
    assert summary["win_rate_pct"] == 0.0
    assert summary["median_peak_pct"] == 0.0
