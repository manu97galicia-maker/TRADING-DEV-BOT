"""Data models used by paper trading."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DevStats:
    """Developer history known strictly before a candidate's signal time."""

    launches: int
    mean_peak_mc: float
    best_peak_mc: float
    best_symbol: str = ""


@dataclass(frozen=True)
class Candidate:
    """A point-in-time candidate; optional fields may be absent upstream."""

    signal_ts: float
    mint: str
    symbol: str
    dev: str
    market_cap: float
    features: dict[str, float | int | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Strategy:
    """Signal and exit parameters."""

    max_entry_mc: float = 4_000.0
    min_dev_mean_mc: float = 12_000.0
    min_dev_launches: int = 5
    checkpoints: tuple[int, ...] = (60, 180, 300, 600, 1_800)
    tp_sl_pairs: tuple[tuple[float, float], ...] = (
        (0.25, 0.15),
        (0.50, 0.20),
        (1.00, 0.30),
    )
    poll_seconds: float = 5.0

    def accepts(self, candidate: Candidate, dev: DevStats) -> bool:
        """Return whether only information available at signal time passes."""
        return (
            0 < candidate.market_cap < self.max_entry_mc
            and dev.mean_peak_mc > self.min_dev_mean_mc
            and dev.launches >= self.min_dev_launches
        )
