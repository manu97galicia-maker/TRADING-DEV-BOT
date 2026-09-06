"""Quality gate for copy-trading wallet candidates.

PumpPortal only streams live account trades. Weekly performance and social
reach must therefore come from an audited upstream dataset before a wallet is
allowed into the copy list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class MissingVerifiedFieldsError(ValueError):
    """Raised when an upstream row lacks a metric required by the gate."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(f"missing verified fields: {', '.join(fields)}")


@dataclass(frozen=True)
class TraderCandidate:
    """Weekly, auditable statistics for one public Solana wallet."""

    wallet: str
    median_roi_pct_7d: float
    calls_7d: int
    win_rate_pct_7d: float
    x_followers: int
    x_handle: str = ""
    source: str = ""

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> TraderCandidate:
        """Build a candidate, failing closed when a required metric is absent."""
        required = (
            "wallet",
            "median_roi_pct_7d",
            "calls_7d",
            "win_rate_pct_7d",
            "x_followers",
        )
        missing = [key for key in required if row.get(key) is None]
        if missing:
            raise MissingVerifiedFieldsError(missing)
        return cls(
            wallet=str(row["wallet"]).strip(),
            median_roi_pct_7d=float(row["median_roi_pct_7d"]),
            calls_7d=int(row["calls_7d"]),
            win_rate_pct_7d=float(row["win_rate_pct_7d"]),
            x_followers=int(row["x_followers"]),
            x_handle=str(row.get("x_handle") or "").strip(),
            source=str(row.get("source") or "").strip(),
        )


@dataclass(frozen=True)
class QualityRules:
    """Minimum evidence required before a wallet can be copied."""

    min_median_roi_pct_7d: float = 150.0
    min_calls_7d: int = 5
    min_win_rate_pct_7d: float = 60.0
    max_x_followers: int = 15_000
    preferred_x_followers: int = 3_000


def qualifies(candidate: TraderCandidate, rules: QualityRules) -> bool:
    """Return whether every mandatory weekly and social rule is satisfied."""
    return bool(
        candidate.wallet
        and candidate.source
        and candidate.median_roi_pct_7d >= rules.min_median_roi_pct_7d
        and candidate.calls_7d >= rules.min_calls_7d
        and candidate.win_rate_pct_7d >= rules.min_win_rate_pct_7d
        and 0 <= candidate.x_followers <= rules.max_x_followers
    )


def rank_candidates(
    candidates: list[TraderCandidate], rules: QualityRules | None = None
) -> list[TraderCandidate]:
    """Filter candidates and rank low-reach, repeatable traders first."""
    active_rules = rules or QualityRules()
    approved = [
        candidate for candidate in candidates if qualifies(candidate, active_rules)
    ]
    return sorted(
        approved,
        key=lambda candidate: (
            candidate.x_followers > active_rules.preferred_x_followers,
            -candidate.median_roi_pct_7d,
            -candidate.win_rate_pct_7d,
            -candidate.calls_7d,
            candidate.x_followers,
        ),
    )
