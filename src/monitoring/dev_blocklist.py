"""Pure helpers for automatic developer blocking."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def any_dev_blocked(
    counts: Mapping[str, int], devs: Iterable[str], threshold: int
) -> bool:
    """Return whether any non-empty developer reached the rug threshold."""
    required = max(1, int(threshold))
    return any(dev and int(counts.get(dev, 0)) >= required for dev in devs)
