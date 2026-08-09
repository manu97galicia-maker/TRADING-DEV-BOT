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

    min_entry_mc: float = 2_800.0
    max_entry_mc: float = 0.0
    max_volume_usd: float = 0.0
    min_unique_buyers: int = 0
    min_unique_sellers: int = 0
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
        volume_usd = candidate.features.get("volume_usd")
        return (
            self.min_entry_mc <= candidate.market_cap
            and (self.max_entry_mc <= 0 or candidate.market_cap <= self.max_entry_mc)
            and (
                self.max_volume_usd <= 0
                or (
                    volume_usd is not None
                    and 0 <= float(volume_usd) <= self.max_volume_usd
                )
            )
            and dev.mean_peak_mc > self.min_dev_mean_mc
            and dev.launches >= self.min_dev_launches
            and int(candidate.features.get("unique_buyers") or 0)
            >= self.min_unique_buyers
            and int(candidate.features.get("unique_sellers") or 0)
            >= self.min_unique_sellers
        )
