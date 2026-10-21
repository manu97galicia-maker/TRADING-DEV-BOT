"""End-to-end paper tests for the BSC coordinator."""

# Pytest assertions and concrete counts make these state-machine checks legible.
# ruff: noqa: S101, PLR2004, TC003

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from bsc.coordinator import BscCoordinator, Fill
from bsc.gmgn_client import LaunchCandidate
from bsc.name_guard import PersistentNameGuard
from bsc.position_store import BscPosition, PositionStore
from bsc.strategy import DeveloperHistory


def candidate(index: int, name: str = "Unique") -> LaunchCandidate:
    """Create a complete deterministic launch candidate."""
    return LaunchCandidate(
        address=f"0x{index:040x}",
        name=f"{name}{index}",
        symbol=f"T{index}",
        creator=f"0x{index + 100:040x}",
        platform="fourmeme" if index % 2 else "flap",
        market_cap_usd=Decimal("8000"),
        price_usd=Decimal("0.01"),
        created_at=1_700_000_000,
    )


class FakeClient:
    """In-memory discovery and pricing source."""

    def __init__(self, candidates: tuple[LaunchCandidate, ...]) -> None:
        self.candidates = candidates
        self.market = {
            item.address.lower(): (item.price_usd, item.market_cap_usd)
            for item in candidates
        }

    async def launch_candidates(self) -> tuple[LaunchCandidate, ...]:
        return self.candidates

    async def creator_history(self, wallet: str) -> DeveloperHistory:
        del wallet
        return DeveloperHistory(launches=25, migrations=2)

    async def token_market(self, address: str) -> tuple[Decimal, Decimal]:
        return self.market[address.lower()]


class FakeBroker:
    """Auditable fake broker with exact quantities."""

    paper = True

    def __init__(self) -> None:
        self.buys: list[str] = []
        self.sells: list[tuple[str, Decimal]] = []

    async def token_taxes_bps(self, candidate: LaunchCandidate) -> tuple[int, int]:
        del candidate
        return 200, 300

    async def buy(self, item: LaunchCandidate, amount_usd: Decimal) -> Fill:
        self.buys.append(item.address)
        return Fill(amount_usd / item.price_usd, item.price_usd, "paper-buy")

    async def sell(self, position: BscPosition, quantity: Decimal) -> Fill:
        self.sells.append((position.address, quantity))
        return Fill(quantity, position.entry_price_usd, "paper-sell")


@pytest.mark.asyncio
async def test_limits_only_simultaneous_positions_and_allows_later_entries(
    tmp_path: Path,
) -> None:
    items = (candidate(1), candidate(2), candidate(3))
    client = FakeClient(items)
    broker = FakeBroker()
    store = PositionStore(tmp_path / "positions.json")
    coordinator = BscCoordinator(
        client=client,  # type: ignore[arg-type]
        broker=broker,
        store=store,
        name_guard=PersistentNameGuard(tmp_path / "names.json"),
    )

    await coordinator.run_once()
    assert len(store.open_positions()) == 2
    assert len(broker.buys) == 2

    first = store.open_positions()[0]
    client.market[first.address.lower()] = (Decimal("0.006"), Decimal("5000"))
    await coordinator.run_once()

    assert len(store.open_positions()) == 2
    assert len(broker.buys) == 3
    assert broker.sells[0][0] == first.address


@pytest.mark.asyncio
async def test_crossed_exit_tiers_execute_once_and_survive_restart(
    tmp_path: Path,
) -> None:
    item = candidate(11)
    client = FakeClient((item,))
    broker = FakeBroker()
    state_path = tmp_path / "positions.json"
    coordinator = BscCoordinator(
        client=client,  # type: ignore[arg-type]
        broker=broker,
        store=PositionStore(state_path),
        name_guard=PersistentNameGuard(tmp_path / "names.json"),
    )
    await coordinator.run_once()

    client.market[item.address.lower()] = (Decimal("0.016"), Decimal("36000"))
    await coordinator.run_once()
    assert len(broker.sells) == 7
    persisted = PositionStore(state_path).open_positions()[0]
    expected = persisted.original_quantity * Decimal("0.1")
    assert abs(persisted.remaining_quantity - expected) < Decimal("1e-20")

    await coordinator.run_once()
    assert len(broker.sells) == 7


def test_position_store_rejects_corruption(tmp_path: Path) -> None:
    path = tmp_path / "positions.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        PositionStore(path)
