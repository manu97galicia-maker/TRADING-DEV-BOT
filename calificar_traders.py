"""Create the PumpPortal copy list from independently verified weekly metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from monitoring.trader_quality import QualityRules, TraderCandidate, rank_candidates


def build_copy_list(payload: dict[str, Any]) -> tuple[list[TraderCandidate], list[str]]:
    """Validate, filter and rank candidate rows without guessing missing data."""
    rules = QualityRules(**(payload.get("rules") or {}))
    rejected: list[str] = []
    candidates: list[TraderCandidate] = []
    for index, row in enumerate(payload.get("candidates") or []):
        try:
            candidates.append(TraderCandidate.from_dict(row))
        except (TypeError, ValueError) as exc:
            rejected.append(f"row {index}: {exc}")
    return rank_candidates(candidates, rules), rejected


def main() -> int:
    """Write approved public wallet addresses, one per line."""
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("copy_traders_verified.txt")
    )
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    approved, rejected = build_copy_list(payload)
    args.output.write_text(
        "".join(f"{candidate.wallet}\n" for candidate in approved), encoding="utf-8"
    )
    print(f"approved={len(approved)} rejected_invalid={len(rejected)}")
    for candidate in approved:
        print(
            f"{candidate.wallet} @{candidate.x_handle or '-'} "
            f"median={candidate.median_roi_pct_7d:.1f}% "
            f"wins={candidate.win_rate_pct_7d:.1f}% calls={candidate.calls_7d} "
            f"followers={candidate.x_followers}"
        )
    for reason in rejected:
        print(f"rejected: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
