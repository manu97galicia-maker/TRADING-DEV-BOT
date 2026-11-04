"""Reuse recent process-owned reads inside one timed dev-entry attempt.

This module performs no RPC, signing, ledger writes or transaction submission.
Mint proofs are recorded by the builder after a real account-owner read; an
entry wallet view is task-local, bounded in lifetime and consumable only once.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass, field, replace

from solders.pubkey import Pubkey

_TOKEN_PROGRAMS = frozenset({
    Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
    Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
})
_MINT_CACHE_LIMIT = 256
_MINT_PROOF_SECONDS = 1.0
_ENTRY_VIEW_SECONDS = 0.6
_HOLDINGS_READ_FIELDS = 2
_MINT_PROGRAM_READS: OrderedDict[str, tuple[Pubkey, float]] = OrderedDict()


def _finite_time(now: float | None) -> float | None:
    value = time.monotonic() if now is None else now
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _pubkey(value: str | Pubkey) -> Pubkey | None:
    try:
        return value if isinstance(value, Pubkey) else Pubkey.from_string(value)
    except (ValueError, TypeError):
        return None


def remember_mint_program(mint: str | Pubkey, program: str | Pubkey, *, now: float | None = None) -> None:
    """Record the start time of an authoritative owner read, never JSON claims."""
    mint_key, owner, timestamp = _pubkey(mint), _pubkey(program), _finite_time(now)
    if mint_key is None:
        return
    key = str(mint_key)
    if owner not in _TOKEN_PROGRAMS or timestamp is None:
        _MINT_PROGRAM_READS.pop(key, None)
        return
    _MINT_PROGRAM_READS[key] = (owner, timestamp)
    _MINT_PROGRAM_READS.move_to_end(key)
    while len(_MINT_PROGRAM_READS) > _MINT_CACHE_LIMIT:
        _MINT_PROGRAM_READS.popitem(last=False)


def cached_mint_program(mint: str | Pubkey, program: str | Pubkey, *, now: float | None = None) -> Pubkey | None:
    """Return only a matching, recent owner proof recorded in this process."""
    mint_key, owner, timestamp = _pubkey(mint), _pubkey(program), _finite_time(now)
    if mint_key is None or owner not in _TOKEN_PROGRAMS or timestamp is None:
        return None
    key = str(mint_key)
    proof = _MINT_PROGRAM_READS.get(key)
    if proof is None:
        return None
    if not 0 <= timestamp-proof[1] <= _MINT_PROOF_SECONDS:
        _MINT_PROGRAM_READS.pop(key, None)
        return None
    if proof[0] != owner:
        return None
    _MINT_PROGRAM_READS.move_to_end(key)
    return owner


def _current_task_or_none() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass(frozen=True)
class DevEntryReadView:
    """One attempt's native-balance and processed token-holdings read."""

    mint: str
    wallet: str
    target_slot: int
    source_generation: int
    creation_fee_creator: str | None
    created_at: float
    holdings: tuple[int, int]
    balance: dict
    owner_task: object | None = field(default_factory=_current_task_or_none, repr=False, compare=False)


DEV_ENTRY_READ_VIEW: ContextVar[DevEntryReadView | None] = ContextVar("dev_entry_read_view", default=None)


def _valid_read_shapes(view: DevEntryReadView) -> bool:
    return (type(view.holdings) is tuple and len(view.holdings) == _HOLDINGS_READ_FIELDS
            and all(type(value) is int and value >= 0 for value in view.holdings)
            and type(view.balance) is dict and type(view.balance.get("value")) is int
            and view.balance["value"] >= 0 and type(view.balance.get("context")) is dict
            and type(view.balance["context"].get("slot")) is int
            and view.balance["context"]["slot"] >= 0)


def consume_dev_read_view(token: object, signal: dict, wallet: str | Pubkey, *, now: float | None = None) -> DevEntryReadView | None:
    """Take once, rejecting stale or mismatched views before any financial use."""
    view = DEV_ENTRY_READ_VIEW.get()
    DEV_ENTRY_READ_VIEW.set(None)
    timestamp = _finite_time(now)
    if (not isinstance(view, DevEntryReadView) or view.owner_task is not _current_task_or_none()
            or type(signal) is not dict or timestamp is None):
        return None
    read_started = _finite_time(view.created_at) if view.created_at is not None else None
    if (type(view.mint) is not str or not view.mint or view.mint != str(getattr(token, "mint", None))
            or type(view.wallet) is not str or not view.wallet or view.wallet != str(wallet)
            or type(view.target_slot) is not int or view.target_slot <= 0
            or type(view.source_generation) is not int or view.source_generation < 0
            or type(signal.get("target_slot")) is not int or signal["target_slot"] != view.target_slot
            or type(signal.get("source_generation")) is not int or signal["source_generation"] != view.source_generation
            or (view.creation_fee_creator is not None and type(view.creation_fee_creator) is not str)
            or signal.get("creation_fee_creator") != view.creation_fee_creator
            or read_started is None or not 0 <= timestamp-read_started <= _ENTRY_VIEW_SECONDS
            or not _valid_read_shapes(view)):
        return None
    data = getattr(token, "additional_data", None) or {}
    if (type(data) is not dict
            or ("dev_entry_target_slot" in data and (
                type(data["dev_entry_target_slot"]) is not int or data["dev_entry_target_slot"] != view.target_slot))
            or ("dev_entry_generation" in data and (
                type(data["dev_entry_generation"]) is not int or data["dev_entry_generation"] != view.source_generation))):
        return None
    return replace(view, balance={"value": view.balance["value"], "context": {"slot": view.balance["context"]["slot"]}})
