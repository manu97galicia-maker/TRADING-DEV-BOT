"""Point-in-time developer history loader.

The file is deliberately local/offline. Each JSONL row represents one historical
launch and must include ``dev``, ``launched_at`` and ``peak_mc``. Rows at or after
the candidate timestamp are excluded, which prevents look-ahead.
"""

from __future__ import annotations

import json
from pathlib import Path

from paper_trading.models import DevStats


class DevHistory:
    """Read developer launch outcomes from an append-only JSONL export."""

    def __init__(self, path: str | Path, aggregate_peak_field: str = "") -> None:
        self.path = Path(path)
        self.aggregate_peak_field = aggregate_peak_field

    def before(self, dev: str, timestamp: float, exclude_mint: str = "") -> DevStats:
        """Aggregate only launches strictly older than ``timestamp``."""
        if self.path.suffix.lower() == ".json" and self.aggregate_peak_field:
            return self._aggregate_profile(dev)
        rows: list[dict] = []
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if (
                    str(row.get("dev", "")) == dev
                    and float(row.get("launched_at", 0)) < timestamp
                    and str(row.get("mint", "")) != exclude_mint
                    and float(row.get("peak_mc", 0)) > 0
                ):
                    rows.append(row)
        peaks = [float(row["peak_mc"]) for row in rows]
        if not peaks:
            return DevStats(0, 0.0, 0.0)
        best = max(rows, key=lambda row: float(row["peak_mc"]))
        return DevStats(
            launches=len(peaks),
            mean_peak_mc=sum(peaks) / len(peaks),
            best_peak_mc=float(best["peak_mc"]),
            best_symbol=str(best.get("symbol", "")),
        )

    def _aggregate_profile(self, dev: str) -> DevStats:
        """Read a frozen pre-run developer profile without fabricating launches."""
        if not self.path.is_file():
            return DevStats(0, 0.0, 0.0)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        items = data.get("wallets", []) if isinstance(data, dict) else data
        for row in items if isinstance(items, list) else []:
            wallet = row.get("wallet") or row.get("address") or row.get("creator")
            if str(wallet or "") != dev:
                continue
            launches = int(row.get("launches_7d", row.get("launches", 0)) or 0)
            peak = float(row.get(self.aggregate_peak_field, 0) or 0)
            return DevStats(launches, peak, 0.0)
        return DevStats(0, 0.0, 0.0)
