"""Restart-safe BSC paper position and processed-token storage."""

# Persistence errors are intentionally explicit operator diagnostics.
# ruff: noqa: TRY003

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path


@dataclass
class BscPosition:
    """One position and its idempotent exit progress."""

    address: str
    name: str
    symbol: str
    platform: str
    original_quantity: Decimal
    remaining_quantity: Decimal
    entry_price_usd: Decimal
    entry_market_cap_usd: Decimal
    entry_notional_usd: Decimal
    opened_at: int
    paper: bool
    completed_exit_triggers: set[str] = field(default_factory=set)

    @property
    def is_open(self) -> bool:
        """Return whether a sellable quantity remains."""
        return self.remaining_quantity > 0

    def to_json(self) -> dict[str, object]:
        """Serialize decimals without binary floating-point loss."""
        result = asdict(self)
        for key in (
            "original_quantity",
            "remaining_quantity",
            "entry_price_usd",
            "entry_market_cap_usd",
            "entry_notional_usd",
        ):
            result[key] = str(result[key])
        result["completed_exit_triggers"] = sorted(self.completed_exit_triggers)
        return result

    @classmethod
    def from_json(cls, value: object) -> BscPosition:
        """Load and validate one serialized position."""
        if not isinstance(value, dict):
            raise TypeError("position must be an object")
        return cls(
            address=str(value["address"]),
            name=str(value["name"]),
            symbol=str(value["symbol"]),
            platform=str(value["platform"]),
            original_quantity=Decimal(str(value["original_quantity"])),
            remaining_quantity=Decimal(str(value["remaining_quantity"])),
            entry_price_usd=Decimal(str(value["entry_price_usd"])),
            entry_market_cap_usd=Decimal(str(value["entry_market_cap_usd"])),
            entry_notional_usd=Decimal(str(value["entry_notional_usd"])),
            opened_at=int(value["opened_at"]),
            paper=bool(value["paper"]),
            completed_exit_triggers=set(value.get("completed_exit_triggers", [])),
        )


class PositionStore:
    """Persist positions and seen tokens with atomic file replacement."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.positions: dict[str, BscPosition] = {}
        self.seen_tokens: set[str] = set()
        self.load()

    def load(self) -> None:
        """Load prior state; reject corrupt files instead of trading blind."""
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("BSC position state is unreadable") from exc
        if not isinstance(payload, dict):
            raise TypeError("BSC position state root is invalid")
        positions = payload.get("positions", [])
        seen = payload.get("seen_tokens", [])
        if not isinstance(positions, list) or not isinstance(seen, list):
            raise TypeError("BSC position state collections are invalid")
        loaded = (BscPosition.from_json(item) for item in positions)
        self.positions = {item.address.lower(): item for item in loaded}
        self.seen_tokens = {str(address).lower() for address in seen}

    def save(self) -> None:
        """Atomically write current state."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "positions": [item.to_json() for item in self.positions.values()],
            "seen_tokens": sorted(self.seen_tokens),
        }
        handle, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            Path(temporary).replace(self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def open_positions(self) -> tuple[BscPosition, ...]:
        """Return all currently open positions."""
        return tuple(
            position for position in self.positions.values() if position.is_open
        )

    def add_position(self, position: BscPosition) -> None:
        """Persist a newly filled entry."""
        key = position.address.lower()
        if key in self.positions and self.positions[key].is_open:
            raise RuntimeError("token already has an open BSC position")
        self.positions[key] = position
        self.seen_tokens.add(key)
        self.save()

    def mark_seen(self, address: str) -> None:
        """Persist a terminally evaluated token address."""
        self.seen_tokens.add(address.lower())
        self.save()

    def update_position(self, position: BscPosition) -> None:
        """Persist an updated position after a reconciled exit."""
        self.positions[position.address.lower()] = position
        self.save()
