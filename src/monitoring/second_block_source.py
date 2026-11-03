"""Optional developer-entry observer. No network work runs in stream callbacks.

History-only admission keeps bounded creations while preparation/history loads.
Its explicit second-produced-block selector expires instead of chasing a token.
Classic admission uses a best-effort second-block window; one explicitly armed,
unbroadcast sharp-rise rejection may retry in the third produced block. The
slot clock cannot guarantee transaction order or inclusion.
"""

# IDL byte offsets and bounded observation limits are intentionally explicit.
# ruff: noqa: SLF001, PLR2004, BLE001

from __future__ import annotations

import asyncio
import copy
import hashlib
import math
import struct
import time
from dataclasses import dataclass, field

from solders.pubkey import Pubkey

from monitoring.creator_history_index import CreatorHistoryCache, curve_address
from monitoring.pump_event_logs import pump_events
from trading.pump_fee_identity import fee_creator_allowed
from trading.second_block_policy import (
    STRATEGY_ID,
    creator_history_only,
    second_produced_block,
)
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
    creation_signature: str | None = None
    dev_initial_buy_verified: bool | None = None
    buyers: set[str] = field(default_factory=set)
    buy_sol: float = 0
    sell_sol: float = 0
    price: float = 0
    price_at: float = 0
    token: object | None = None
    attempted: bool = False
    invalid: bool = False
    second_slot: int | None = None
    third_slot: int | None = None
    attempted_slots: set[int] = field(default_factory=set)
    inflight_slot: int | None = None
    retry_armed: bool = False
    price_slot: int = 0
    observed_at: float = 0
    prepare_after: float = 0
    creation_timestamp: float | None = None
    first_seen_wall_at: float = 0
    prepared_wall_at: float = 0
    first_signal_wall_at: float = 0


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
        self.latest_log_slot = 0
        self.generation = 0
        self.signatures: set[tuple[int, str]] = set()
        self.attempted_mints: dict[str, float] = {}
        self.history_refreshes: set[asyncio.Future] = set()

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
        self.available = slot >= self.latest_log_slot
        if getattr(self.history, "coverage_start_slot", None) is None:
            self.history.coverage_start_slot = slot
        if creator_history_only(self.policy):
            if second_produced_block(self.policy):
                self.advance_second_block_candidates(slot, parent)
            self.refresh_candidates()
            return
        self.advance_second_block_candidates(slot, parent)

    def advance_second_block_candidates(self, slot: int, parent: int) -> None:
        strict = second_produced_block(self.policy)
        for mint, candidate in list(self.candidates.items()):
            if parent == candidate.creation_slot:
                if candidate.second_slot not in {None, slot}:
                    self.candidates.pop(mint, None)
                    continue
                candidate.second_slot = slot
            elif candidate.second_slot is not None and parent == candidate.second_slot:
                if strict:
                    self.candidates.pop(mint, None)
                    continue
                if candidate.third_slot not in {None, slot}:
                    self.candidates.pop(mint, None)
                    continue
                candidate.third_slot = slot
            elif candidate.creation_slot < parent or (strict and slot > candidate.creation_slot):
                self.candidates.pop(mint, None)
                continue
            if strict:
                if self.second_window_expired(candidate):
                    self.candidates.pop(mint, None)
            else:
                self.consider(candidate)

    def second_window_expired(self, candidate: Candidate) -> bool:
        """A known later block or an expired immutable target cannot be chased."""
        if candidate.second_slot is None:
            return self.slot > candidate.creation_slot and self.parent != candidate.creation_slot
        return (max(self.slot, self.latest_log_slot, candidate.price_slot) > candidate.second_slot
                or (self.slot > candidate.creation_slot and
                    (self.slot != candidate.second_slot or self.parent != candidate.creation_slot))
                or (self.slot == candidate.second_slot and
                    not 0 <= time.monotonic()-self.slot_at <= self.policy["max_slot_age_seconds"]))

    def refresh_candidates(self) -> None:
        for candidate in self.ordered_history_candidates(list(self.candidates.values())):
            mint = candidate.mint
            if second_produced_block(self.policy) and self.second_window_expired(candidate):
                self.candidates.pop(mint, None)
                continue
            if not 0 <= time.monotonic()-candidate.observed_at <= 600:
                if candidate.inflight_slot is None:
                    self.candidates.pop(mint, None)
                continue
            self.refresh(candidate)
            self.consider(candidate)

    def on_logs(self, logs: list[str], slot: int, signature: str) -> None:
        if type(slot) is not int or not signature:
            return
        identity = (slot, signature)
        if identity in self.signatures:
            return
        self.signatures.add(identity)
        self.latest_log_slot = max(self.latest_log_slot, slot)
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
                self.creation(raw, slot, signature)
        touched = {mint for raw in events if (mint := self.trade(raw, slot, signature))}
        self.consider_touched(touched)

    def consider_touched(self, mints: set[str]) -> None:
        candidates = [self.candidates[mint] for mint in mints]
        if creator_history_only(self.policy):
            candidates = self.ordered_history_candidates(candidates)
        for candidate in candidates:
            if creator_history_only(self.policy):
                self.refresh(candidate)
            self.consider(candidate)

    def creation(self, raw: bytes, slot: int, signature: str | None = None) -> None:
        try:
            decoded = self.implementations.event_parser._idl_parser.decode_event_data(raw, "CreateEvent")
            fields = decoded["fields"]
            mint, creator, user = (str(fields[k]) for k in ("mint", "creator", "user"))
            Pubkey.from_string(creator)
            if (str(fields["bonding_curve"]) != curve_address(mint) or not Pubkey.from_string(user).is_on_curve()
                    or fields.get("token_total_supply") != 1000000000000000):
                return
            self.history.observe(user, mint)
            history_only = creator_history_only(self.policy)
            if not history_only:
                self.history.warm(user)
            if history_only and (
                    (second_produced_block(self.policy) and self.slot > slot and self.parent != slot)
                    or not self.admit_history_candidate(mint)):
                return
            if (mint in self.candidates or (not history_only and slot < self.parent)
                    or len(self.candidates) >= 256):
                return
            candidate = Candidate(mint, creator, slot, str(fields.get("name") or ""),
                                  str(fields.get("symbol") or ""), developer=user,
                                  creation_signature=signature,
                                  observed_at=time.monotonic(), first_seen_wall_at=time.time())
            timestamp = fields.get("timestamp")
            if type(timestamp) in (int, float) and math.isfinite(timestamp) and timestamp > 0:
                candidate.creation_timestamp = float(timestamp)
            if self.available and self.parent == slot:
                candidate.second_slot = self.slot
            self.candidates[mint] = candidate
            if history_only:
                self.refresh(candidate)
                return
            if len(self.building) < 2:
                self.building.add(mint)
                self._track(self.prepare(candidate, self.generation))
        except (ValueError, TypeError, KeyError, struct.error):
            return

    def admit_history_candidate(self, mint: str) -> bool:
        self.prune_history_attempts()
        if mint in self.attempted_mints:
            return False
        if mint not in self.candidates and len(self.candidates) >= 256:
            oldest = next((key for key, value in self.candidates.items()
                           if value.inflight_slot is None), None)
            if oldest is None:
                return False
            self.candidates.pop(oldest)
        return True

    def refresh(self, candidate: Candidate) -> None:
        """Retry technical preparation and revisit a completed history refresh."""
        if second_produced_block(self.policy) and (
                self.candidates.get(candidate.mint) is not candidate or self.second_window_expired(candidate)):
            return
        if (creator_history_only(self.policy) and
                (not self.history_activity_ready(candidate) or candidate.mint in self.attempted_mints)):
            return
        self.history.warm(candidate.developer)
        pending = getattr(self.history, "pending", {}).get(candidate.developer)
        if pending is not None and not pending.done() and pending not in self.history_refreshes:
            self.history_refreshes.add(pending)
            generation = self.generation
            developer = candidate.developer

            def ready(completed: asyncio.Future) -> None:
                self.history_refreshes.discard(completed)
                if not completed.cancelled() and generation == self.generation:
                    matching = [current for current in self.candidates.values() if current.developer == developer]
                    for current in self.ordered_history_candidates(matching):
                        self.refresh(current)
                        self.consider(current)

            pending.add_done_callback(ready)
        if creator_history_only(self.policy):
            history = self.history.evidence(candidate.developer, candidate.mint)
            if history is None or not history["migration_fraction"] >= self.policy["min_creator_migration_fraction"]:
                return
        now = time.monotonic()
        if (candidate.token is None and candidate.mint not in self.building
                and len(self.building) < 2 and now >= candidate.prepare_after):
            candidate.prepare_after = now+1
            self.building.add(candidate.mint)
            self._track(self.prepare(candidate, self.generation))

    def history_activity_ready(self, candidate: Candidate, sol_usd: float | None = None) -> bool:
        """Use the same creation-block requirement before consuming network work."""
        if (second_produced_block(self.policy) and self.policy.get("require_dev_initial_buy", False)
                and candidate.dev_initial_buy_verified is not True):
            return False
        if not self.policy.get("require_first_block_activity", False):
            return True
        sol_usd = self.sol_reference() if sol_usd is None else sol_usd
        return (math.isfinite(sol_usd) and sol_usd > 0
                and len(candidate.buyers) >= self.policy["min_distinct_first_block_buyers"]
                and (candidate.buy_sol+candidate.sell_sol)*sol_usd >= self.policy["min_first_block_volume_usd"])

    def ordered_history_candidates(self, candidates: list[Candidate]) -> list[Candidate]:
        """Prefer qualifying recent creations over delayed delivery of older logs."""
        return sorted(candidates, key=lambda candidate: (
            self.history_activity_ready(candidate), candidate.creation_slot, candidate.observed_at), reverse=True)

    def timing_features(self, candidate: Candidate) -> dict:
        now = time.time()
        if not candidate.first_signal_wall_at:
            candidate.first_signal_wall_at = now
        return {"creation_timestamp": candidate.creation_timestamp,
                "source_seen_at": candidate.first_seen_wall_at,
                "token_prepared_at": candidate.prepared_wall_at,
                "signal_ready_at": candidate.first_signal_wall_at,
                "signal_age_seconds": now-candidate.creation_timestamp if candidate.creation_timestamp is not None else None}

    def reject_unidentifiable_initial_trade(self, slot: int, signature: str | None) -> None:
        """An unreadable earlier trade cannot establish a later trade was first."""
        for candidate in self.candidates.values():
            if (signature and candidate.creation_signature == signature
                    and candidate.creation_slot == slot and candidate.dev_initial_buy_verified is None):
                candidate.dev_initial_buy_verified = False

    def decode_trade(self, raw: bytes, slot: int, signature: str | None) -> tuple[Candidate, float, int, int] | None:
        if raw[:8] != TRADE:
            return None
        if len(raw) < 40:
            self.reject_unidentifiable_initial_trade(slot, signature)
            return None
        mint = str(Pubkey.from_bytes(raw[8:40]))
        candidate = self.candidates.get(mint)
        if candidate is None or slot < candidate.creation_slot:
            return None
        initial_trade = bool(signature and signature == candidate.creation_signature
                             and slot == candidate.creation_slot and candidate.dev_initial_buy_verified is None)
        if initial_trade:
            candidate.dev_initial_buy_verified = False
        if len(raw) < 113 or raw[56] not in {0, 1}:
            return None
        sol = struct.unpack_from("<Q", raw, 40)[0] / 1e9
        vsol, vtokens = struct.unpack_from("<QQ", raw, 97)
        if sol <= 0 or vsol == 0 or vtokens == 0:
            return None
        if initial_trade:
            candidate.dev_initial_buy_verified = bool(raw[56] and
                str(Pubkey.from_bytes(raw[57:89])) == candidate.developer)
        return candidate, sol, vsol, vtokens

    def trade(self, raw: bytes, slot: int, signature: str | None = None) -> str | None:
        decoded = self.decode_trade(raw, slot, signature)
        if decoded is None:
            return None
        candidate, sol, vsol, vtokens = decoded
        if slot == candidate.creation_slot:
            if raw[56]:
                if len(candidate.buyers) >= 1024 and not creator_history_only(self.policy):
                    candidate.invalid = True
                    return None
                if len(candidate.buyers) < 1024:
                    candidate.buyers.add(str(Pubkey.from_bytes(raw[57:89])))
                candidate.buy_sol += sol
            else:
                candidate.sell_sol += sol
        if slot >= candidate.price_slot:
            candidate.price, candidate.price_at = vsol / vtokens / 1000, time.monotonic()
            candidate.price_slot = slot
        return candidate.mint

    def signal(self, mint: str) -> dict | None:
        candidate = self.candidates.get(mint)
        sol_usd = self.sol_reference()
        if creator_history_only(self.policy):
            return self.history_signal(candidate, sol_usd)
        entry_block = (2 if candidate and self.slot == candidate.second_slot and self.parent == candidate.creation_slot
                       else 3 if candidate and self.policy.get("third_block_retry_on_sharp_rise", False)
                       and candidate.retry_armed and self.slot == candidate.third_slot
                       and self.parent == candidate.second_slot else None)
        if (not self.available or candidate is None or candidate.invalid or not self.allowed()
                or entry_block is None or candidate.price_slot > self.slot
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
            "creation_fee_creator": candidate.creator,
            "prepared_fee_creator": str(candidate.token.creator) if candidate.token is not None else None,
            "developer": candidate.developer,
            "observed_at": time.time(), "creation_slot": candidate.creation_slot,
            "target_slot": self.slot, "buyers": len(candidate.buyers),
            "entry_block": entry_block, "source_generation": self.generation,
            "retry_reason": "sharp_price_rise" if entry_block == 3 else None,
            "buy_sol": candidate.buy_sol, "sell_sol": candidate.sell_sol,
            "volume_usd": volume, "observed_mc_usd": mc, "creator_history": history,
            "inclusion_guaranteed": False, **self.timing_features(candidate)}

    def history_signal(self, candidate: Candidate | None, sol_usd: float) -> dict | None:
        """Verified developer history with the explicitly selected first-block activity."""
        now = time.monotonic()
        strict = second_produced_block(self.policy)
        if (not self.available or candidate is None or candidate.token is None or not self.allowed()
                or not 0 <= now-candidate.observed_at <= 600
                or not 0 <= now-self.slot_at <= 30
                or not 0 <= self.reference_age() <= 30
                or not math.isfinite(sol_usd) or sol_usd <= 0):
            return None
        if strict and (candidate.invalid or candidate.second_slot is None
                       or self.slot != candidate.second_slot or self.parent != candidate.creation_slot
                       or self.second_window_expired(candidate)):
            return None
        history = self.history.evidence(candidate.developer, candidate.mint)
        if history is None or not history["migration_fraction"] >= self.policy["min_creator_migration_fraction"]:
            return None
        volume = (candidate.buy_sol+candidate.sell_sol)*sol_usd
        activity_required = self.policy.get("require_first_block_activity", False)
        if not self.history_activity_ready(candidate, sol_usd):
            return None
        return {"strategy": STRATEGY_ID, "entry_rule": "creator_migration_only",
            "entry_timing": "second_produced_block" if strict else None,
            "first_block_activity_required": activity_required,
            "dev_initial_buy_required": strict and self.policy.get("require_dev_initial_buy", False),
            "dev_initial_buy_verified": candidate.dev_initial_buy_verified,
            "dev_initial_buy_signature": candidate.creation_signature if candidate.dev_initial_buy_verified else None,
            "sol_usd": sol_usd, "creator": candidate.creator, "developer": candidate.developer,
            "creation_fee_creator": candidate.creator, "prepared_fee_creator": str(candidate.token.creator),
            "observed_at": time.time(), "creation_slot": candidate.creation_slot,
            "target_slot": candidate.second_slot if strict else None,
            "entry_block": 2 if strict else None, "source_generation": self.generation,
            "retry_reason": None, "buyers": len(candidate.buyers),
            "buy_sol": candidate.buy_sol, "sell_sol": candidate.sell_sol,
            "volume_usd": volume,
            "observed_mc_usd": candidate.price*1e9*sol_usd, "creator_history": history,
            "inclusion_guaranteed": False, **self.timing_features(candidate)}

    def consider(self, candidate: Candidate) -> None:
        history_only = creator_history_only(self.policy)
        if history_only:
            self.prune_history_attempts()
            if candidate.mint in self.attempted_mints or len(self.attempted_mints) >= 4096:
                return
        signal = self.signal(candidate.mint)
        attempt_key = signal["target_slot"] if signal and signal["target_slot"] is not None else candidate.creation_slot
        if (candidate.token is not None and candidate.inflight_slot is None and signal
                and attempt_key not in candidate.attempted_slots):
            candidate.attempted = True
            candidate.inflight_slot = attempt_key
            candidate.attempted_slots.add(attempt_key)
            if history_only:
                self.attempted_mints[candidate.mint] = time.monotonic()
            self._track(self.deliver(candidate))

    def prune_history_attempts(self) -> None:
        now = time.monotonic()
        while self.attempted_mints:
            mint = next(iter(self.attempted_mints))
            if now-self.attempted_mints[mint] <= 600:
                break
            self.attempted_mints.pop(mint)

    def arm_third_block_retry(self, mint: str, second_slot: int, generation: int) -> bool:
        """Caller proved a sharp-rise rejection happened before any submission."""
        candidate = self.candidates.get(mint)
        if (creator_history_only(self.policy) or not self.policy.get("third_block_retry_on_sharp_rise", False)
                or generation != self.generation or not self.available or candidate is None
                or candidate.retry_armed or candidate.second_slot != second_slot
                or candidate.inflight_slot != second_slot or second_slot not in candidate.attempted_slots
                or self.slot not in {candidate.second_slot, candidate.third_slot}
                or (self.slot == candidate.third_slot and self.parent != second_slot)
                or not 0 <= time.monotonic()-self.slot_at <= self.policy["max_slot_age_seconds"]):
            return False
        candidate.retry_armed = True
        return True

    async def deliver(self, candidate: Candidate) -> None:
        target = candidate.inflight_slot
        try:
            if self.candidates.get(candidate.mint) is not candidate:
                return
            signal = self.signal(candidate.mint)
            attempt_key = signal["target_slot"] if signal and signal["target_slot"] is not None else candidate.creation_slot
            if signal and attempt_key == target:
                token = copy.copy(candidate.token)
                token.additional_data = dict(candidate.token.additional_data or {})
                token.additional_data.update(dev_entry_target_slot=signal["target_slot"],
                                             dev_entry_generation=signal["source_generation"])
                completion = await self.callback(token)
                if isinstance(completion, asyncio.Future):
                    await completion
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Second-block candidate skipped (%s)", type(error).__name__)
        finally:
            if self.candidates.get(candidate.mint) is candidate and candidate.inflight_slot == target:
                candidate.inflight_slot = None
                self.consider(candidate)

    async def prepare(self, candidate: Candidate, generation: int) -> None:
        try:
            token = await asyncio.wait_for(build_pumpfun_token_info(
                candidate.mint, self.implementations, self.client,
                symbol=candidate.symbol, name=candidate.name), timeout=2)
            if (token is None or generation != self.generation
                    or self.candidates.get(candidate.mint) is not candidate
                    or not fee_creator_allowed(candidate.mint, candidate.creator, token.creator)):
                return
            if str(token.creator) != candidate.creator:
                logger.info("DEV official fee-sharing recipient prepared for %s", candidate.mint)
            token.additional_data = {**(token.additional_data or {}),
                "acquisition_kind": "second-block-dev", "four_strategy_id": STRATEGY_ID,
                "acquisition_source": ("observed_creation_creator_history"
                    if creator_history_only(self.policy) else "observed_creation_second_block")}
            candidate.token = token
            candidate.prepared_wall_at = time.time()
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
        self.latest_log_slot = 0
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
