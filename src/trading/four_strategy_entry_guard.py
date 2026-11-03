"""Read-only entry checks. Observed concentration does not identify related wallets."""

from __future__ import annotations

# Reject malformed or absent evidence instead of falling back to an allowed buy.
# ruff: noqa: TRY003, PLR2004, PLR0912, PLR0915, C901
import asyncio
import math
from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING

from solders.pubkey import Pubkey
from spl.token.constants import TOKEN_2022_PROGRAM_ID, TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
STANDARD_SUPPLY_RAW = 10**15
# The MC path assumes one billion six-decimal tokens. Permit dust burns up to
# one part per million without admitting a materially different token supply.
MAX_DUST_BURN_RAW = STANDARD_SUPPLY_RAW // 1_000_000


class EntryRiskRejected(ValueError):  # noqa: N818 -- explicit rejection shared by RPC and price checks
    """Public rejection label, never containing credentials or RPC URLs."""


def validate_policy(policy: dict) -> None:
    if not isinstance(policy, dict) or type(policy.get("enabled")) is not bool:
        raise ValueError("An explicit entry-protection policy is required")
    if not policy["enabled"]:
        return
    Pubkey.from_string(policy["rpc_probe_mint"])
    for name in (
        "price_window_seconds",
        "max_price_age_seconds",
        "rpc_timeout_seconds",
        "max_rise_fraction",
        "max_drawdown_fraction",
        "max_quote_increase_fraction",
        "max_observed_owner_fraction",
        "max_top10_observed_owners_fraction",
        "max_creator_fraction",
    ):
        value = float(policy[name])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid entry-protection field: {name}")
        if name.endswith("fraction") and value >= 1:
            raise ValueError(f"Invalid entry-protection fraction: {name}")
    if not (
        policy["max_creator_fraction"]
        <= policy["max_observed_owner_fraction"]
        <= policy["max_top10_observed_owners_fraction"]
    ):
        raise ValueError("Concentration thresholds must be ordered")
    if policy["max_price_age_seconds"] > policy["price_window_seconds"]:
        raise ValueError("Price age cannot exceed the entry observation window")


def check_price(
    points: list, price: float, anchor: float, now: float, policy: dict
) -> dict:
    """Measure the observed part of a short window; never invent missing history."""
    if not all(math.isfinite(x) and x > 0 for x in (price, anchor, now)):
        raise EntryRiskRejected("invalid_entry_price")
    recent = []
    for stamp, value in points:
        if not math.isfinite(stamp) or not math.isfinite(value) or value <= 0:
            raise EntryRiskRejected("invalid_price_history")
        if now - policy["price_window_seconds"] <= stamp <= now:
            recent.append((stamp, value))
    recent.sort()
    if len(recent) < 2 or now - recent[-1][0] > policy["max_price_age_seconds"]:
        raise EntryRiskRejected("missing_fresh_price_history")
    low = min(p for _, p in recent)
    high = max([price] + [p for _, p in recent])
    rise = price / low - 1
    drawdown = 1 - price / high
    drift = price / anchor - 1
    if rise > policy["max_rise_fraction"] + 1e-12:
        raise EntryRiskRejected("sharp_price_rise")
    if drawdown > policy["max_drawdown_fraction"] + 1e-12:
        raise EntryRiskRejected("price_dump_from_recent_peak")
    if drift > policy["max_quote_increase_fraction"] + 1e-12:
        raise EntryRiskRejected("price_ran_during_validation")
    return {
        "rise_fraction": rise,
        "drawdown_fraction": drawdown,
        "quote_increase_fraction": drift,
        "observed_window_seconds": now - recent[0][0],
    }


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise EntryRiskRejected("invalid_raw_token_amount")
    result = int(value)
    if str(result) != str(value) or result < 0:
        raise EntryRiskRejected("invalid_raw_token_amount")
    return result


def _token_info(account: dict, mint: str, program: str) -> dict:
    parsed = account["data"]["parsed"]
    if account["owner"] != program or parsed["type"] != "account":
        raise EntryRiskRejected("unverified_token_account")
    info = parsed["info"]
    if info["mint"] != mint or info["state"] != "initialized":
        raise EntryRiskRejected("invalid_token_account_state")
    Pubkey.from_string(info["owner"])
    return info


async def concentration_snapshot(
    token: object,
    creator: str,
    rpc: Callable[[str, list], Awaitable[object]],
    policy: dict,
) -> dict:
    """Read top accounts plus every creator account, then refresh them together.

    Public top-20 accounts only give a lower bound on holdings for other owners;
    multiple small accounts or related wallets can remain outside that sample.
    """
    mint = Pubkey.from_string(str(token.mint))
    program = Pubkey.from_string(str(token.token_program_id))
    creator_key = Pubkey.from_string(creator)
    curve, _ = Pubkey.find_program_address(
        [b"bonding-curve", bytes(mint)], PUMP_PROGRAM
    )
    if str(curve) != str(token.bonding_curve):
        raise EntryRiskRejected("wrong_curve_address")
    if program not in {TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID}:
        raise EntryRiskRejected("unsupported_token_program")
    vault = get_associated_token_address(curve, mint, program)
    if str(vault) != str(token.associated_bonding_curve):
        raise EntryRiskRejected("wrong_curve_reserve_address")
    if creator_key in {curve, mint, Pubkey.default()}:
        raise EntryRiskRejected("unverified_creator")
    largest, owned = await asyncio.gather(
        rpc("getTokenLargestAccounts", [str(mint), {"commitment": "processed"}]),
        rpc(
            "getTokenAccountsByOwner",
            [
                str(creator_key),
                {"mint": str(mint)},
                {"encoding": "jsonParsed", "commitment": "processed"},
            ],
        ),
    )
    if not 1 <= len(largest["value"]) <= 20:
        raise EntryRiskRejected("missing_largest_accounts")
    top = [row["address"] for row in largest["value"]]
    if len(set(top)) != len(top):
        raise EntryRiskRejected("duplicate_largest_accounts")
    creator_accounts = []
    for row in owned["value"]:
        info = _token_info(row["account"], str(mint), str(program))
        if info["owner"] != str(creator_key):
            raise EntryRiskRejected("creator_account_owner_mismatch")
        creator_accounts.append(row["pubkey"])
    addresses = list(
        dict.fromkeys([str(mint), str(curve), str(vault), *top, *creator_accounts])
    )
    if len(addresses) > 100:
        raise EntryRiskRejected("excessive_creator_accounts")
    for address in addresses:
        Pubkey.from_string(address)
    minimum_slot = max(
        _integer(largest["context"]["slot"]), _integer(owned["context"]["slot"])
    )
    snapshot = await rpc(
        "getMultipleAccounts",
        [
            addresses,
            {
                "encoding": "jsonParsed",
                "commitment": "processed",
                "minContextSlot": minimum_slot,
            },
        ],
    )
    if _integer(snapshot["context"]["slot"]) < minimum_slot or len(
        snapshot["value"]
    ) != len(addresses):
        raise EntryRiskRejected("incomplete_or_stale_account_snapshot")
    if any(account is None for account in snapshot["value"]):
        raise EntryRiskRejected("missing_account_snapshot")
    accounts = dict(zip(addresses, snapshot["value"], strict=True))
    mint_account = accounts[str(mint)]
    mint_data = mint_account["data"]["parsed"]
    if mint_account["owner"] != str(program) or mint_data["type"] != "mint":
        raise EntryRiskRejected("invalid_mint_snapshot")
    mint_info = mint_data["info"]
    supply = _integer(mint_info["supply"])
    if (
        mint_info["decimals"] != 6
        or mint_info["isInitialized"] is not True
        or not STANDARD_SUPPLY_RAW - MAX_DUST_BURN_RAW <= supply <= STANDARD_SUPPLY_RAW
    ):
        raise EntryRiskRejected("unsupported_mint_supply_or_decimals")
    if accounts[str(curve)]["owner"] != str(PUMP_PROGRAM):
        raise EntryRiskRejected("wrong_curve_program")
    owners: dict[str, int] = defaultdict(int)
    curve_balance = 0
    observed_total = 0
    for address in addresses[2:]:
        info = _token_info(accounts[address], str(mint), str(program))
        amount = _integer(info["tokenAmount"]["amount"])
        if info["tokenAmount"]["decimals"] != 6:
            raise EntryRiskRejected("token_decimals_mismatch")
        observed_total += amount
        if address == str(vault):
            if info["owner"] != str(curve):
                raise EntryRiskRejected("wrong_curve_reserve_owner")
            curve_balance = amount
        else:
            owners[info["owner"]] += amount
            if address in creator_accounts and info["owner"] != str(creator_key):
                raise EntryRiskRejected("creator_owner_changed")
    if observed_total > supply or not any(owners.values()):
        raise EntryRiskRejected("inconsistent_or_empty_holder_snapshot")
    ordered = sorted(owners.values(), reverse=True)
    creator_amount = owners.get(str(creator_key), 0)
    for name, amount, limit in (
        (
            "concentrated_observed_owner",
            ordered[0],
            policy["max_observed_owner_fraction"],
        ),
        (
            "concentrated_top10_observed_owners",
            sum(ordered[:10]),
            policy["max_top10_observed_owners_fraction"],
        ),
        ("concentrated_creator", creator_amount, policy["max_creator_fraction"]),
    ):
        if Decimal(amount) > Decimal(supply) * Decimal(str(limit)):
            raise EntryRiskRejected(name)
    return {
        "slot": snapshot["context"]["slot"],
        "supply_raw": supply,
        "curve_reserve_excluded_raw": curve_balance,
        "max_observed_owner_fraction": ordered[0] / supply,
        "top10_observed_owners_fraction": sum(ordered[:10]) / supply,
        "creator_fraction": creator_amount / supply,
        "coverage": "top20_accounts_plus_creator_accounts; related_wallets_not_identified",
    }
