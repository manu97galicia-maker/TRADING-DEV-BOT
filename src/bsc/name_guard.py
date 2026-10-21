"""Persistent duplicate-name guard shared by BSC launchpad sources."""

# The validation messages are intentionally explicit operator diagnostics.
# ruff: noqa: TRY003

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


def normalize_token_name(name: str) -> str:
    """Normalize cosmetic differences without changing the actual name."""
    normalized = unicodedata.normalize("NFKC", name)
    return " ".join(normalized.casefold().split())


@dataclass(frozen=True)
class NameDecision:
    """Result of observing one launch name."""

    allowed: bool
    normalized_name: str
    previous_seen_at: datetime | None


class RecentNameRegistry:
    """Remember names across restarts and enforce a two-hour cooldown."""

    def __init__(self, state_path: Path, cooldown_seconds: int = 7_200) -> None:
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        self.state_path = state_path
        self.cooldown = timedelta(seconds=cooldown_seconds)

    def observe(self, name: str, *, now: datetime | None = None) -> NameDecision:
        """Record a launch and reject it if the normalized name is recent."""
        normalized = normalize_token_name(name)
        if not normalized:
            raise ValueError("token name must not be empty")
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        entries = self._load()
        previous_raw = entries.get(normalized)
        previous = datetime.fromisoformat(previous_raw) if previous_raw else None
        repeated = previous is not None and observed_at - previous < self.cooldown

        cutoff = observed_at - self.cooldown
        entries = {
            key: value
            for key, value in entries.items()
            if datetime.fromisoformat(value) >= cutoff
        }
        entries[normalized] = observed_at.isoformat()
        self._save(entries)
        return NameDecision(
            allowed=not repeated,
            normalized_name=normalized,
            previous_seen_at=previous,
        )

    def was_seen_recently(self, name: str, *, now: datetime | None = None) -> bool:
        """Check the cooldown without recording a transiently failed candidate."""
        normalized = normalize_token_name(name)
        if not normalized:
            raise ValueError("token name must not be empty")
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        previous_raw = self._load().get(normalized)
        if not previous_raw:
            return False
        return observed_at - datetime.fromisoformat(previous_raw) < self.cooldown

    def remember(self, name: str, *, now: datetime | None = None) -> None:
        """Record a successfully accepted launch name."""
        self.observe(name, now=now)

    def _load(self) -> dict[str, str]:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                "duplicate-name state is unreadable; fail closed"
            ) from exc
        if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in data.items()
        ):
            raise RuntimeError("duplicate-name state has an invalid format")
        return data

    def _save(self, entries: dict[str, str]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name = ""
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.state_path.parent,
                delete=False,
            ) as temporary:
                json.dump(entries, temporary, ensure_ascii=False, sort_keys=True)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            Path(temporary_name).replace(self.state_path)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)


PersistentNameGuard = RecentNameRegistry
