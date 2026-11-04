"""Helius creation history by actual signer, with on-chain migration proof.

The denominator is fully paginated through a fixed finalized slot. Canonical
PumpSwap pools prove a conservative numerator, independent of fee recipients.
No wallet key, signing or transaction-submission methods are used.
"""

# IDL offsets and bounds are protocol evidence, rather than trading heuristics.
# ruff: noqa: PLR2004, TRY003

from __future__ import annotations

import base64
import hashlib
import time
from functools import lru_cache
from pathlib import Path

from solders.pubkey import Pubkey

from monitoring.creator_history_index import (
    AMM,
    CURVE_DISC,
    POOL_DISC,
    PUMP,
    WSOL,
    CreatorSnapshot,
    HistoryUnavailableError,
    curve_address,
)
from monitoring.pump_event_logs import IncompletePumpLogsError, pump_events
from trading.second_block_policy import required_migrations
from utils.idl_parser import IDLParser

CREATE = hashlib.sha256(b"event:CreateEvent").digest()[:8]


@lru_cache(maxsize=1)
def event_parser() -> IDLParser:
    return IDLParser(str(Path(__file__).resolve().parents[2]/"idl/pump_fun_idl.json"))


def transaction_identity(tx: dict, anchor: int) -> tuple[tuple[int, int], str]:
    slot, index = tx.get("slot"), tx.get("transactionIndex")
    if type(slot) is not int or not 0 < slot <= anchor or type(index) is not int or index < 0:
        raise HistoryUnavailableError("Invalid historical transaction slot")
    signatures = tx["transaction"]["signatures"]
    if not isinstance(signatures, list) or not signatures or not all(isinstance(s, str) and s for s in signatures):
        raise HistoryUnavailableError("Missing historical transaction identity")
    return (slot, index), signatures[0]


def creation_mints(tx: dict, developer: str) -> set[str]:  # noqa: C901 -- each malformed proof must reject the denominator
    message, meta = tx["transaction"]["message"], tx["meta"]
    if "err" not in meta or meta["err"] is not None:
        raise HistoryUnavailableError("Unsuccessful or incomplete historical transaction")
    keys = message["accountKeys"]
    required = message["header"]["numRequiredSignatures"]
    if (not isinstance(keys, list) or not all(isinstance(k, str) for k in keys)
            or type(required) is not int or not 1 <= required <= len(keys)):
        raise HistoryUnavailableError("Incomplete signer evidence")
    loaded = meta.get("loadedAddresses") or {}
    writable, readonly = loaded.get("writable", []), loaded.get("readonly", [])
    if not isinstance(writable, list) or not isinstance(readonly, list):
        raise HistoryUnavailableError("Invalid address-table evidence")
    all_keys = [*keys, *writable, *readonly]
    if developer not in all_keys:
        raise HistoryUnavailableError("Wallet history returned an unrelated transaction")
    if str(PUMP) not in all_keys:
        return set()
    logs = meta.get("logMessages")
    if not isinstance(logs, list):
        raise HistoryUnavailableError("Pump creation logs unavailable")
    mints = set()
    for raw in pump_events(logs, strict=True):
        if raw[:8] != CREATE:
            continue
        event = event_parser().decode_event_data(raw, "CreateEvent")
        if not event or event.get("event_name") != "CreateEvent":
            raise HistoryUnavailableError("Unsupported historical creation layout")
        fields = event["fields"]
        user, mint = fields["user"], fields["mint"]
        Pubkey.from_string(user)
        Pubkey.from_string(fields["creator"])
        if fields["bonding_curve"] != curve_address(mint):
            raise HistoryUnavailableError("Historical creation has the wrong curve PDA")
        if user == developer:
            if developer not in keys[:required]:
                raise HistoryUnavailableError("Creation user did not sign this transaction")
            mints.add(mint)
    return mints


async def scan_creation_transactions(developer: str, rpc: object, policy: dict,  # noqa: C901 -- reject each incomplete page, duplicate and bound before counting launches
                                     anchor: int) -> set[str]:
    cursor, previous = None, None
    cursors, signatures, mints = set(), set(), set()
    for _ in range(policy["history_max_pages"]):
        config = {"transactionDetails": "full", "limit": policy["history_page_size"],
            "sortOrder": "desc", "encoding": "json", "maxSupportedTransactionVersion": 1,
            "commitment": "finalized", "minContextSlot": anchor,
            "filters": {"status": "succeeded", "tokenAccounts": "none", "slot": {"lte": anchor}}}
        if cursor is not None:
            config["paginationToken"] = cursor
        page = await rpc("getTransactionsForAddress", [developer, config])
        if not isinstance(page, dict) or "paginationToken" not in page or not isinstance(page.get("data"), list):
            raise HistoryUnavailableError("Incomplete historical page")
        for tx in page["data"]:
            identity, signature = transaction_identity(tx, anchor)
            if signature in signatures or (previous is not None and identity >= previous):
                raise HistoryUnavailableError("Duplicate or unordered historical transactions")
            previous = identity
            signatures.add(signature)
            if len(signatures) > policy["history_max_transactions"]:
                raise HistoryUnavailableError("Historical transaction bound reached")
            created = creation_mints(tx, developer)
            if mints & created:
                raise HistoryUnavailableError("Repeated creation of the same mint")
            mints.update(created)
            if len(mints) > policy["history_max_accounts"]:
                raise HistoryUnavailableError("Historical launch bound reached")
        cursor = page["paginationToken"]
        if cursor is None:
            return mints
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise HistoryUnavailableError("Cyclic historical cursor")
        cursors.add(cursor)
    raise HistoryUnavailableError("Historical pagination bound reached")


def pool_address(mint: str) -> tuple[str, Pubkey]:
    base = Pubkey.from_string(mint)
    authority = Pubkey.find_program_address([b"pool-authority", bytes(base)], PUMP)[0]
    address = Pubkey.find_program_address([b"pool", b"\0\0", bytes(authority), bytes(base), bytes(WSOL)], AMM)[0]
    return str(address), authority


def account_data(account: dict | None, owner: Pubkey, discriminator: bytes, minimum: int) -> bytes | None:
    if account is None:
        return None
    data = account.get("data")
    if (account.get("owner") != str(owner) or account.get("executable") is not False
            or not isinstance(data, list) or len(data) != 2 or data[1] != "base64"):
        raise HistoryUnavailableError("Invalid historical account envelope")
    raw = base64.b64decode(data[0], validate=True)
    if len(raw) < minimum or raw[:8] != discriminator:
        raise HistoryUnavailableError("Unsupported historical account layout")
    return raw


def migration_proven(mint: str, curve: dict | None, pool: dict | None) -> bool:
    c = account_data(curve, PUMP, CURVE_DISC, 81)
    p = account_data(pool, AMM, POOL_DISC, 243)
    if c is not None and c[48] not in {0, 1}:
        raise HistoryUnavailableError("Invalid curve completion flag")
    if c is None or p is None:
        return False
    _address, authority = pool_address(mint)
    if (p[9:11] != b"\0\0" or p[11:43] != bytes(authority)
            or p[43:75] != bytes(Pubkey.from_string(mint)) or p[75:107] != bytes(WSOL)):
        raise HistoryUnavailableError("Noncanonical migration pool")
    if c[48] != 1:
        raise HistoryUnavailableError("Pool and curve migration evidence disagree")
    return True


async def verify_migrations(mints: set[str], rpc: object, policy: dict, anchor: int) -> tuple[set[str], bool]:
    ordered, migrated = sorted(mints), set()
    # A lower bound is sufficient for the selected ratio. Extra proof covers the bounded
    # live-launch buffer while avoiding an unbounded number of RPC reads.
    sufficient = required_migrations(len(ordered)+256, policy)
    for start in range(0, len(ordered), 50):
        batch = ordered[start:start+50]
        addresses = [address for mint in batch for address in (curve_address(mint), pool_address(mint)[0])]
        result = await rpc("getMultipleAccounts", [addresses,
            {"encoding": "base64", "commitment": "finalized", "minContextSlot": anchor}])
        slot = result.get("context", {}).get("slot")
        accounts = result.get("value")
        if type(slot) is not int or slot < anchor or not isinstance(accounts, list) or len(accounts) != len(addresses):
            raise HistoryUnavailableError("Incomplete migration account batch")
        for index, mint in enumerate(batch):
            if migration_proven(mint, accounts[2*index], accounts[2*index+1]):
                migrated.add(curve_address(mint))
        if len(migrated) >= sufficient:
            return migrated, start+len(batch) == len(ordered)
    return migrated, True


async def read_transaction_history(developer: str, rpc: object, policy: dict) -> CreatorSnapshot:
    if not Pubkey.from_string(developer).is_on_curve():
        raise HistoryUnavailableError("Developer must be a creation signer wallet")
    try:
        anchor = await rpc("getSlot", [{"commitment": "finalized"}])
        if type(anchor) is not int or anchor <= 0:
            raise HistoryUnavailableError("Finalized history anchor unavailable")
        mints = await scan_creation_transactions(developer, rpc, policy, anchor)
        if not mints:
            raise HistoryUnavailableError("No verified creation history")
        migrated, complete = await verify_migrations(mints, rpc, policy, anchor)
        return CreatorSnapshot(developer, {curve_address(mint) for mint in mints}, migrated,
            anchor, time.time(), "helius_creation_signers_and_canonical_pumpswap_pools", complete)
    except (KeyError, TypeError, ValueError, IncompletePumpLogsError) as error:
        if isinstance(error, HistoryUnavailableError):
            raise
        raise HistoryUnavailableError("Unverifiable historical creation evidence") from error
