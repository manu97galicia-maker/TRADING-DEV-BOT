"""Offline exit-plan calculation; does not load wallets or submit orders."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReviewTier:
    """One proposed exit expressed as MC and original token quantity."""

    target_mc_usd: float
    sell_original_fraction: float
    gain_from_entry: float


def build_review_ladder(entry_mc: float, settings: dict) -> list[ReviewTier]:
    """Keep TP1 +50%; raise a conflicting final target to entry +100%.

    The middle target is the midpoint of the first and last targets. This is
    an explicit review rule, not an optimization or an execution quote.
    """
    if not math.isfinite(entry_mc) or entry_mc <= 0:
        raise ValueError("A finite positive entry MC is required")
    if settings.get("ladder_policy") != "keep_first_tp_raise_later_targets_when_needed":
        raise ValueError("Unknown exit-plan policy")
    gain = float(settings["initial_tp_gain"])
    final_mc = float(settings["last_target_market_cap_usd"])
    fallback = float(settings["fallback_last_gain"])
    fractions = tuple(float(x) for x in settings["proposed_original_quantity_fractions"])
    if (
        len(fractions) != 3
        or not all(math.isfinite(x) and 0 < x <= 1 for x in fractions)
        or not math.isclose(sum(fractions), 1, rel_tol=0, abs_tol=1e-12)
        or not all(math.isfinite(x) for x in (gain, final_mc, fallback))
        or gain <= 0 or final_mc <= 0 or fallback <= gain
    ):
        raise ValueError("Invalid ladder gains, final target or quantity fractions")
    first_mc = entry_mc * (1 + gain)
    if final_mc <= first_mc:
        final_mc = entry_mc * (1 + fallback)
    middle_mc = first_mc + (final_mc - first_mc) / 2
    targets = (first_mc, middle_mc, final_mc)
    if not all(math.isfinite(x) for x in targets) or not (
        entry_mc < first_mc < middle_mc < final_mc
    ):
        raise ValueError("Exit targets must be finite and strictly increasing")
    return [
        ReviewTier(target, fraction, target / entry_mc - 1)
        for target, fraction in zip(targets, fractions, strict=True)
    ]


def main() -> None:
    """Print examples from the review request without starting a trading bot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan", type=Path, default=Path("estudios/four_strategies/request.json")
    )
    parser.add_argument("--entry-mc", type=float, nargs="+", default=[8000, 21000])
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    settings = plan["exit"]
    examples = [
        {
            "entry_mc_usd": entry,
            "initial_stop_mc_usd": entry * (1 - settings["stop_loss_fraction"]),
            "tiers": [asdict(tier) for tier in build_review_ladder(entry, settings)],
        }
        for entry in args.entry_mc
    ]
    print(json.dumps({"execution": "offline_review_only", "examples": examples}, indent=2))


if __name__ == "__main__":
    main()
