"""Explicit fifth-strategy contract and offline exit calculations."""

# Literal values are the user's separate strategy, not global trading defaults.
# ruff: noqa: PLR2004, TRY003

from __future__ import annotations

import math
from pathlib import Path

import yaml

from trading.review_exit_ladder import ReviewTier, build_review_ladder

STRATEGY_ID = "dev-second-block"


def creator_history_only(settings: dict) -> bool:
    return settings.get("entry_rule", "second_block") == "creator_migration_only"


def validate_second_policy(settings: dict) -> None:
    """Unresolved exits cannot be enabled by a configuration typo."""
    if not isinstance(settings, dict) or type(settings.get("enabled")) is not bool:
        raise ValueError("Second-block strategy needs an explicit enabled boolean")
    if not settings["enabled"]:
        return
    expected = {
        "strategy_id": STRATEGY_ID, "balance_fraction": .90,
        "min_creator_migration_fraction": .01, "developer_identity": "creation_signer",
        "history_provider": "helius_transactions",
    }
    expected.update(
        {"buy_priority_fee_cap_usd": .07, "sell_priority_fee_cap_usd": .03}
        if creator_history_only(settings) else
        {"max_entry_mc_usd": 4200, "min_first_block_volume_usd": 275,
         "min_distinct_first_block_buyers": 2, "buy_priority_fee_cap_usd": .06})
    expected.update({"min_first_block_volume_usd": 250, "min_distinct_first_block_buyers": 2}
                    if creator_history_only(settings) and settings.get("require_first_block_activity", False) else {})
    if (any(settings.get(k) != v for k, v in expected.items())
            or settings.get("entry_rule", "second_block") not in {"second_block", "creator_migration_only"}
            or type(settings.get("third_block_retry_on_sharp_rise", False)) is not bool
            or type(settings.get("require_first_block_activity", False)) is not bool
            or type(settings.get("reject_sharp_price_rise", True)) is not bool):
        raise ValueError("Second-block settings differ from the requested entry policy")
    for key in ["max_slot_age_seconds", "creator_cache_seconds", "history_timeout_seconds"]:
        if not isinstance(settings.get(key), (int, float)) or not math.isfinite(settings[key]) or settings[key] <= 0:
            raise ValueError("Second-block observation limits must be finite and positive")
    for key in ["history_max_pages", "history_page_size", "history_max_accounts", "history_max_transactions"]:
        if type(settings.get(key)) is not int or not 1 <= settings[key] <= 10000:
            raise ValueError("History page limits are invalid")
    if settings["history_page_size"] > 1000:
        raise ValueError("Helius transaction pages are limited to 1000 records")
    exit_policy = settings["exit"]
    if (exit_policy.get("initial_gain") != .35 or exit_policy.get("later_step_gain") != .40
            or exit_policy.get("later_sales") != 3):
        raise ValueError("Expected +35%, then three multiplicative +40% targets")
    second_block_ladder(4000, exit_policy)


def load_second_policy(config_path: Path, plan: dict) -> dict:
    relative = plan.get("second_block_dev_config")
    if relative is None:
        return {"enabled": False}
    directory = Path(config_path).resolve().parent
    path = (directory / str(relative)).resolve()
    if path.parent != directory or path.suffix != ".yaml":
        raise ValueError("Additional strategy configuration must be a sibling YAML")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_second_policy(value)
    return value


def second_block_ladder(
    entry_mc: float, policy: dict, allocations: list[float] | None = None
) -> list[ReviewTier]:
    """Plan nominal capital recovery; fees/slippage prevent guaranteed proceeds."""
    if not math.isfinite(entry_mc) or entry_mc <= 0:
        raise ValueError("A positive entry MC is required")
    if policy.get("kind") != "second-block-capital-recovery":
        raise ValueError("Unknown second-block exit policy")
    stop = policy.get("stop_loss_fraction")
    if not isinstance(stop, (int, float)) or not math.isfinite(stop) or not 0 < stop < 1:
        raise ValueError("Confirm the new strategy stop loss before enabling it")
    initial = float(policy["initial_gain"])
    step = float(policy["later_step_gain"])
    count = policy["later_sales"]
    if initial != .35 or step != .40 or count != 3:
        raise ValueError("Invalid second-block target ladder")
    first = first_sale_fraction(policy, initial)
    fractions = allocations if allocations is not None else [first, *((1-first)/count for _ in range(count))]
    if (len(fractions) != count+1 or not all(math.isfinite(x) and 0 < x < 1 for x in fractions)
            or not math.isclose(sum(fractions), 1, abs_tol=1e-12)):
        raise ValueError("Invalid persisted second-block allocations")
    return [ReviewTier(entry_mc * (1+initial) * (1+step)**i, fraction,
                       (1+initial) * (1+step)**i - 1)
            for i, fraction in enumerate(fractions)]


def first_sale_fraction(policy: dict, initial: float) -> float:
    mode = policy.get("first_sale_mode")
    if mode == "recover_capital":
        if policy.get("recovery_profit_fraction") not in (None, 0):
            raise ValueError("Capital-only recovery must not include an extra profit")
        first = 1 / (1 + initial)
    elif mode == "recover_capital_plus_profit":
        profit = policy.get("recovery_profit_fraction")
        if not isinstance(profit, (int, float)) or not 0 <= profit < initial:
            raise ValueError("Invalid first recovery profit")
        first = (1 + profit) / (1 + initial)
    elif mode == "original_fraction":
        first = policy.get("first_sale_original_fraction")
        if not isinstance(first, (int, float)) or not 0 < first < 1:
            raise ValueError("Invalid original-token fraction")
    else:
        raise ValueError("Confirm what the first sale recovers")
    return first


def recovery_allocations(policy: dict, entry_price: float, price: float) -> list[float]:
    """Freeze a recovery allocation at the first observed trigger, not every retry."""
    second_block_ladder(1, policy)
    if not math.isfinite(price) or not math.isfinite(entry_price) or not 0 < entry_price <= price:
        raise ValueError("Invalid recovery quote")
    if policy["first_sale_mode"] == "original_fraction":
        first = policy["first_sale_original_fraction"]
    else:
        profit = policy.get("recovery_profit_fraction") or 0
        first = entry_price * (1+profit) / price
    return [first, *((1-first)/3 for _ in range(3))]


def position_exit_plan(details: dict, default_exit: dict) -> tuple[list[ReviewTier], float]:
    entry_mc = float(details["entry_price"]) * 1e9 * float(details["entry_sol_usd"])
    policy = details.get("entry_exit_policy")
    if policy is None:
        return build_review_ladder(entry_mc, default_exit), float(default_exit["stop_loss_fraction"])
    # A newly tightened global stop also protects older persisted positions;
    # never loosen a position whose saved stop was already more restrictive.
    stop = min(float(default_exit["stop_loss_fraction"]), float(policy["stop_loss_fraction"]))
    return second_block_ladder(entry_mc, policy, details.get("exit_allocations")), stop
