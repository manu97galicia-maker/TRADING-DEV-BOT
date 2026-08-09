"""Offline time-split optimizer for paper-trading observations."""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from pathlib import Path
from typing import Any

from paper_trading.analytics import summarize
from paper_trading.store import PaperStore

OPTIONAL_RULES = {
    "min_buyers": ("buyers", ">="),
    "min_volume": ("volume", ">="),
    "min_velocity": ("velocity", ">="),
    "max_bundle_pct": ("bundle_pct", "<="),
    "max_snipers_pct": ("snipers_pct", "<="),
    "max_concentration": ("concentration", "<="),
    "max_dev_holding": ("dev_holding", "<="),
}


def accepts(row: dict[str, Any], params: dict[str, Any]) -> bool:
    """Evaluate point-in-time fields; missing optional features are ignored."""
    if not (
        row["entry_mc"] < params["max_entry_mc"]
        and row["dev_mean_mc"] > params["min_dev_mean_mc"]
        and row["dev_launches"] >= params["min_dev_launches"]
    ):
        return False
    for name, (feature, operator) in OPTIONAL_RULES.items():
        limit = params.get(name)
        value = row.get("features", {}).get(feature)
        if limit is None or value is None:
            continue
        if operator == ">=" and value < limit:
            return False
        if operator == "<=" and value > limit:
            return False
    return True


def optimize(
    rows: list[dict[str, Any]], grid: dict[str, list[Any]], train_ratio: float
) -> dict[str, Any]:
    """Choose on the older train segment and evaluate once on newer test data."""
    closed = [row for row in rows if row.get("result")]
    split = max(1, min(len(closed) - 1, int(len(closed) * train_ratio)))
    train, test = closed[:split], closed[split:]
    candidates = []
    for values in itertools.product(*(grid[name] for name in grid)):
        params = dict(zip(grid, values, strict=True))
        selected = [row for row in train if accepts(row, params)]
        stats = summarize(selected)
        returns = [row["result"]["strategies"]["primary"]["return"] for row in selected]
        consistency = -statistics.pstdev(returns) if len(returns) > 1 else -1.0
        score = stats["ev"] + 0.25 * consistency - 0.10 * stats["max_drawdown"]
        candidates.append((score, params, stats))
    _, params, train_stats = max(candidates, key=lambda item: item[0])
    test_stats = summarize([row for row in test if accepts(row, params)])
    return {
        "params": params,
        "train": train_stats,
        "test": test_stats,
        "split_index": split,
    }


def main() -> None:
    """Run optimization from a JSON grid and print machine-readable output."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="paper_trades/paper.sqlite3")
    parser.add_argument("--grid", required=True, type=Path)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    args = parser.parse_args()
    grid = json.loads(args.grid.read_text(encoding="utf-8"))
    print(
        json.dumps(
            optimize(PaperStore(args.database).rows(), grid, args.train_ratio), indent=2
        )
    )


if __name__ == "__main__":
    main()
