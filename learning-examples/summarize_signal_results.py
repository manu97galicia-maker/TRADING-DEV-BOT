"""Summarize paper signal exits without accessing a wallet or trading."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paper_trading.exit_metrics import summarize_exit_records

RESULTS = Path("paper_trades/results.jsonl")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    """Print robust paper metrics for later TP/SL calibration."""
    if not RESULTS.is_file():
        print(f"No hay resultados todavía en {RESULTS}")
        return
    records = []
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = None
        if isinstance(record, dict):
            records.append(record)
    stats = summarize_exit_records(records)
    print(f"Trades: {stats['trades']}")
    print(f"Win rate: {stats['win_rate_pct']:.1f}%")
    print(f"Stops: {stats['stop_rate_pct']:.1f}%")
    print(f"Mediana ROI: {stats['median_roi_pct']:+.1f}%")
    print(f"Mediana pico (referencia TP): {stats['median_peak_pct']:+.1f}%")
    print(f"Mediana mínimo (referencia SL): {stats['median_trough_pct']:+.1f}%")
    print(f"Mediana salida por stop: {stats['median_stop_exit_pct']:+.1f}%")
    print(f"Mediana de TPs alcanzados: {stats['median_tp_hits']:.1f}")


if __name__ == "__main__":
    main()
