"""Keep Pump's immutable launch identity separate from its creator-fee vault.

Only the original recipient or this mint's canonical Pump Fees sharing config
may replace that fee identity. No network, signer, or trading I/O is performed.
Derivations: pump-fun/pump-public-docs/docs/instructions/CREATOR_FEE_SHARING.md.
"""

from __future__ import annotations

from solders.pubkey import Pubkey

PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
PUMP_FEES_PROGRAM = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")


class UnexpectedDevFeeRecipientError(ValueError):
    """Reject a noncanonical fee identity before instruction construction."""

    def __init__(self) -> None:
        super().__init__("Unexpected dev fee recipient")


def sharing_config_address(mint: str | Pubkey) -> str:
    address, _ = Pubkey.find_program_address(
        [b"sharing-config", bytes(Pubkey.from_string(str(mint)))], PUMP_FEES_PROGRAM)
    return str(address)


def fee_creator_allowed(mint: str | Pubkey, original: str | Pubkey, observed: str | Pubkey) -> bool:
    """Accept only identities tied to this authenticated Pump creation and mint."""
    try:
        mint_key = Pubkey.from_string(str(mint))
        original_key = Pubkey.from_string(str(original))
        observed_key = Pubkey.from_string(str(observed))
        return observed_key == original_key or str(observed_key) == sharing_config_address(mint_key)
    except (ValueError, TypeError):
        return False


def bind_dev_fee_creator(token: object, original: str | Pubkey, observed: str | Pubkey) -> bool:
    """Bind a verified curve's fee recipient and vault without changing the dev.

    Compute all values before mutation. A delivered token's recorded origin
    cannot be replaced, including after a later fee-state recheck.
    """
    if not fee_creator_allowed(token.mint, original, observed):
        return False
    data = dict(token.additional_data or {})
    if data.get("dev_creation_fee_creator", str(original)) != str(original):
        return False
    creator = Pubkey.from_string(str(observed))
    vault, _ = Pubkey.find_program_address([b"creator-vault", bytes(creator)], PUMP_PROGRAM)
    data.update(dev_creation_fee_creator=str(original), dev_current_fee_creator=str(creator))
    token.creator, token.creator_vault, token.additional_data = creator, vault, data
    return True


def refresh_bound_dev_fee_creator(token: object, state: dict) -> None:
    """Revalidate dev-only tokens just before building, including cached states."""
    original = (token.additional_data or {}).get("dev_creation_fee_creator")
    if original is not None and not bind_dev_fee_creator(token, original, state.get("creator", token.creator)):
        raise UnexpectedDevFeeRecipientError
