"""Optional second-block observer. No network work runs in stream callbacks.

The slot clock bounds a best-effort submission window, not transaction order
or guaranteed inclusion. Unavailable history, a stream gap or a late build
causes a skipped entry, never a later-block fallback.
"""

# IDL byte offsets and bounded observation limits are intentionally explicit.
# ruff: noqa: SLF001, PLR2004, BLE001

from __future__ import annotations

import asyncio
import hashlib
import math
import struct
import time
from dataclasses import dataclass, field

from solders.pubkey import Pubkey

from monitoring.creator_history_index import CreatorHistoryCache, curve_address
from monitoring.pump_event_logs import pump_events
from trading.second_block_policy import STRATEGY_ID
from trading.token_builder import build_pumpfun_token_info
from utils.logger import get_logger

logger = get_logger(__name__)
CREATE = hashlib.sha256(b"event:CreateEvent").digest()[:8]
TRADE = hashlib.sha256(b"event:TradeEvent").digest()[:8]


@dataclass
class Candidate:
    mint: str
    creator: str
    creation_slot: int
    name: str
    symbol: str
    developer: str = ""
    buyers: set[str] = field(default_factory=set)
    buy_sol: float = 0
    sell_sol: float = 0
    price: float = 0
    price_at: float = 0
    token: object | None = None
    attempted: bool = False
    invalid: bool = False


class SecondBlockSource:
    def __init__(self, policy: dict, rpc: object, implementations: object,  # noqa: PLR0913, PLR0917 -- I/O and policy callbacks
                 client: object, sol_reference: object, reference_age: object,
                 allowed: object, callback: object) -> None:
        self.policy, self.implementations, self.client = policy, implementations, client
        self.sol_reference, self.reference_age = sol_reference, reference_age
        self.allowed, self.callback = allowed, callback
        self.history = CreatorHistoryCache(rpc, policy)
        self.candidates: dict[str, Candidate] = {}
        self.tasks: set[asyncio.Task] = set()
        self.building: set[str] = set()
        self.slot, self.parent, self.slot_at = 0, 0, 0.0
        self.available = False
        self.generation = 0
        self.signatures: set[tuple[int, str]] = set()

    def _track(self, awaitable: object) -> None:
        task = asyncio.create_task(awaitable)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def on_slot(self, value: dict) -> None:
        slot, parent = value.get("slot"), value.get("parent")
        if type(slot) is not int or type(parent) is not int or not 0 <= parent < slot:
            self.disconnect()
            return
        if slot <= self.slot:
            return  # Repeated/stale notifications cannot renew a deadline.
        self.slot, self.parent, self.slot_at = slot, parent, time.monotonic()
        self.available = True
        if getattr(self.history, "coverage_start_slot", None) is None:
            self.history.coverage_start_slot = slot
        for mint, candidate in list(self.candidates.items()):
            if candidate.creation_slot < parent:
                self.candidates.pop(mint, None)
            else:
                self.consider(candidate)

    def on_logs(self, logs: list[str], slot: int, signature: str) -> None:
        if type(slot) is not int or not signature:
            return
        identity = (slot, signature)
        if identity in self.signatures:
            return
        self.signatures.add(identity)
        if len(self.signatures) > 4000:
            self.signatures = {item for item in self.signatures if item[0] >= slot-2}
            if len(self.signatures) > 4000:
                self.disconnect()
                return
        if self.available and slot > self.slot:
            # A newer trade arriving before its slot notification already
            # proves the preceding submission window has passed.
            self.available = False
        events = pump_events(logs)
        for raw in events:
            if raw[:8] == CREATE:
                self.creation(raw, slot)
        touched = {mint for raw in events if (mint := self.trade(raw, slot))}
        for mint in touched:
            self.consider(self.candidates[mint])

    def creation(self, raw: bytes, slot: int) -> None:
        try:
            decoded = self.implementations.event_parser._idl_parser.decode_event_data(raw, "CreateEvent")
            fields = decoded["fields"]
            mint, creator, user = (str(fields[k]) for k in ("mint", "creator", "user"))
            Pubkey.from_string(creator)
            if (str(fields["bonding_curve"]) != curve_address(mint) or not Pubkey.from_string(user).is_on_curve()
                    or fields.get("token_total_supply") != 1000000000000000):
                return
            self.history.observe(user, mint)
            self.history.warm(user)
            if mint in self.candidates or slot < self.parent or len(self.candidates) >= 256:
                return
            candidate = Candidate(mint, creator, slot, str(fields.get("name") or ""),
                                  str(fields.get("symbol") or ""), developer=user)
            self.candidates[mint] = candidate
            if len(self.building) < 2:
                self.building.add(mint)
                self._track(self.prepare(candidate, self.generation))
        except (ValueError, TypeError, KeyError, struct.error):
            return

    def trade(self, raw: bytes, slot: int) -> str | None:
        if raw[:8] != TRADE or len(raw) < 113 or raw[56] not in {0, 1}:
            return None
        mint = str(Pubkey.from_bytes(raw[8:40]))
        candidate = self.candidates.get(mint)
        if candidate is None or slot != candidate.creation_slot:
            return None
        sol = struct.unpack_from("<Q", raw, 40)[0] / 1e9
        vsol, vtokens = struct.unpack_from("<QQ", raw, 97)
        if sol <= 0 or vsol == 0 or vtokens == 0:
            return None
        if raw[56]:
            if len(candidate.buyers) >= 1024:
                candidate.invalid = True
                return None
            candidate.buyers.add(str(Pubkey.from_bytes(raw[57:89])))
            candidate.buy_sol += sol
        else:
            candidate.sell_sol += sol
        candidate.price, candidate.price_at = vsol / vtokens / 1000, time.monotonic()
        return mint

    def signal(self, mint: str) -> dict | None:
        candidate = self.candidates.get(mint)
        sol_usd = self.sol_reference()
        if (not self.available or candidate is None or candidate.invalid or not self.allowed()
                or self.parent != candidate.creation_slot or self.slot <= candidate.creation_slot
                or not 0 <= time.monotonic()-self.slot_at <= self.policy["max_slot_age_seconds"]
                or not 0 <= time.monotonic()-candidate.price_at <= 1
                or not 0 <= self.reference_age() <= 30
                or not math.isfinite(sol_usd) or sol_usd <= 0):
            return None
        volume = (candidate.buy_sol+candidate.sell_sol)*sol_usd
        mc = candidate.price*1e9*sol_usd
        history = self.history.evidence(candidate.developer, mint)
        if (len(candidate.buyers) < self.policy["min_distinct_first_block_buyers"]
                or volume < self.policy["min_first_block_volume_usd"]
                or not 0 < mc < self.policy["max_entry_mc_usd"] or history is None
                or history["migration_fraction"] < self.policy["min_creator_migration_fraction"]):
            return None
        return {"strategy": STRATEGY_ID, "sol_usd": sol_usd, "creator": candidate.creator,
            "developer": candidate.developer,
            "observed_at": time.time(), "creation_slot": candidate.creation_slot,
            "target_slot": self.slot, "buyers": len(candidate.buyers),
            "buy_sol": candidate.buy_sol, "sell_sol": candidate.sell_sol,
            "volume_usd": volume, "observed_mc_usd": mc, "creator_history": history,
            "inclusion_guaranteed": False}

    def consider(self, candidate: Candidate) -> None:
        if candidate.token is not None and not candidate.attempted and self.signal(candidate.mint):
            candidate.attempted = True
            self._track(self.deliver(candidate))

    async def deliver(self, candidate: Candidate) -> None:
        try:
            if self.signal(candidate.mint):
                await self.callback(candidate.token)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Second-block candidate skipped (%s)", type(error).__name__)

    async def prepare(self, candidate: Candidate, generation: int) -> None:
        try:
            token = await asyncio.wait_for(build_pumpfun_token_info(
                candidate.mint, self.implementations, self.client,
                symbol=candidate.symbol, name=candidate.name), timeout=2)
            if (token is None or generation != self.generation
                    or self.candidates.get(candidate.mint) is not candidate
                    or str(token.creator) != candidate.creator):
                return
            token.additional_data = {**(token.additional_data or {}),
                "acquisition_kind": "second-block-dev", "four_strategy_id": STRATEGY_ID,
                "acquisition_source": "observed_creation_second_block"}
            candidate.token = token
            self.consider(candidate)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.debug("Second-block prebuild unavailable (%s)", type(error).__name__)
        finally:
            self.building.discard(candidate.mint)

    def disconnect(self) -> None:
        self.generation += 1
        self.available = False
        self.slot, self.parent, self.slot_at = 0, 0, 0
        self.candidates.clear()
        self.signatures.clear()
        self.history.invalidate()
        # Do not cancel a delivery that may already have broadcast. Its shared
        # coordinator keeps the receipt/position and the normal exit monitor.

    async def close(self) -> None:
        self.disconnect()
        await self.history.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
