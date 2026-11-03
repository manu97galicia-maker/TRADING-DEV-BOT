"""Audit manual disposals using finalized public receipts, without a signer."""

# Explicit evidence failures must remain understandable; all amounts are raw integers.
# ruff: noqa: TRY003

from __future__ import annotations

from collections import defaultdict


class IncompleteManualAuditError(ValueError):
    """Keep the position occupied when attribution cannot be proven."""


def account_keys(tx: dict) -> list[str]:
    return [str(k.get("pubkey") if isinstance(k, dict) else k)
            for k in tx["transaction"]["message"]["accountKeys"]]


def balances(tx: dict, owner: str, side: str) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for row in tx["meta"].get(side + "TokenBalances", []):
        if row.get("owner") == owner:
            result[row["mint"]] += int(row["uiTokenAmount"]["amount"])
    return dict(result)


def attributed_flow(tx: dict, owner: str, mint: str) -> tuple[int, int]:
    """Reject bundles changing another owned asset, including unwrapped WSOL."""
    keys = account_keys(tx)
    if not keys or keys[0] != owner:
        raise IncompleteManualAuditError("Wallet is not the fee payer")
    pre, post = balances(tx, owner, "pre"), balances(tx, owner, "post")
    if any(pre.get(m, 0) != post.get(m, 0) for m in pre.keys() | post.keys() if m != mint):
        raise IncompleteManualAuditError("Multiple assets changed in one transaction")
    meta = tx["meta"]
    return (
        post.get(mint, 0) - pre.get(mint, 0),
        int(meta["postBalances"][0]) - int(meta["preBalances"][0]),
    )


def audit_manual_sale(  # noqa: C901, PLR0912, PLR0915 -- reject each incomplete attribution before releasing a slot
    position: dict, known: list[tuple[str, str]], owner: str, rpc: object
) -> dict:
    """Read the original token account history through the identified buy.

    The RPC callback is read-only. Version 1 is needed for current Phantom
    disposals; no serialized transaction is decoded, signed or broadcast here.
    """
    mint = position["mint"]
    held = rpc("getTokenAccountsByOwner", [owner, {"mint": mint},
        {"encoding": "jsonParsed", "commitment": "finalized"}])
    raw = sum(int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
              for a in held["value"])
    if raw:
        return {"mint": mint, "state": "still_held", "raw_holdings": raw}
    known_map = dict(known)
    cache = {}

    def transaction(signature: str) -> dict:
        if signature not in cache:
            value = rpc("getTransaction", [signature, {"encoding": "jsonParsed",
                "commitment": "finalized", "maxSupportedTransactionVersion": 1}])
            if not value or not isinstance(value.get("meta"), dict):
                raise IncompleteManualAuditError("Missing finalized transaction")
            if (value["transaction"].get("signatures") or [None])[0] != signature:
                raise IncompleteManualAuditError("Transaction signature differs")
            cache[signature] = value
        return cache[signature]

    buys = []
    for signature, kind in known:
        t = transaction(signature)
        flow, _ = attributed_flow(t, owner, mint)
        if kind == "buy" and t["meta"].get("err") is None and flow > 0:
            buys.append((signature, t))
    if len(buys) != 1:
        raise IncompleteManualAuditError("Exactly one successful identified buy is required")
    buy_signature, buy = buys[0]
    if balances(buy, owner, "pre").get(mint, 0):
        raise IncompleteManualAuditError("Preexisting holdings cannot be mixed")
    keys = account_keys(buy)
    accounts = {keys[b["accountIndex"]] for b in buy["meta"].get("postTokenBalances", [])
                if b.get("owner") == owner and b.get("mint") == mint}
    if len(accounts) != 1:
        raise IncompleteManualAuditError("Expected one original token account")
    token_account = next(iter(accounts))
    history = rpc("getSignaturesForAddress", [token_account,
        {"commitment": "finalized", "limit": 100}])
    if not isinstance(history, list):
        raise IncompleteManualAuditError("Token account history is unavailable")
    window = []
    for item in history:
        if item.get("confirmationStatus") != "finalized":
            raise IncompleteManualAuditError("History contains an unfinalized transaction")
        window.append(item)
        if item["signature"] == buy_signature:
            break
    if not window or window[-1]["signature"] != buy_signature:
        raise IncompleteManualAuditError("History does not reach the identified buy")
    additional = []
    for item in reversed(window):
        signature = item["signature"]
        t = transaction(signature)
        if int(t["slot"]) != int(item["slot"]) or t["meta"].get("err") != item.get("err"):
            raise IncompleteManualAuditError("History and receipt disagree")
        if signature in known_map:
            continue
        flow, native = attributed_flow(t, owner, mint)
        if flow > 0:
            raise IncompleteManualAuditError("Untracked acquisition mixed with this position")
        if flow < 0 and (t["meta"].get("err") is not None or native <= 0):
            raise IncompleteManualAuditError("Transfer or non-native disposal cannot be valued")
        if flow == 0:
            # Only fees or rent refunds from this specific original account
            # can be attributed without changing token holdings.
            if token_account not in account_keys(t):
                raise IncompleteManualAuditError("Unattributed account movement")
        additional.append((signature, "failed" if t["meta"].get("err") is not None else "sell"))
    all_receipts = known + additional
    if not any(kind == "sell" for _, kind in all_receipts):
        raise IncompleteManualAuditError("No attributed disposal found")
    total_tokens, net, max_slot, buy_debit, buy_debit_verified = 0, 0, 0, 0, True
    for signature, kind in all_receipts:
        t = transaction(signature)
        flow, native = attributed_flow(t, owner, mint)
        total_tokens += flow
        net += native
        if kind == "buy":
            buy_debit_verified = buy_debit_verified and native < 0
            buy_debit += max(0, -native)
        max_slot = max(max_slot, int(t["slot"]))
    if total_tokens != 0:
        raise IncompleteManualAuditError("Receipt token quantities do not close to zero")
    final = rpc("getTokenAccountsByOwner", [owner, {"mint": mint},
        {"encoding": "jsonParsed", "commitment": "finalized", "minContextSlot": max_slot}])
    if int(final["context"]["slot"]) < max_slot or any(
        int(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
        for a in final["value"]
    ):
        raise IncompleteManualAuditError("Final zero holdings could not be verified")
    return {"mint": mint, "nonce": position["nonce"], "state": "ready",
            "net_lamports": net, "context_slot": int(final["context"]["slot"]),
            "buy_debit_lamports": buy_debit, "buy_debit_verified": buy_debit_verified and buy_debit > 0,
            "known_receipts": known, "additional_receipts": additional,
            "signatures": [s for s, _ in all_receipts]}
