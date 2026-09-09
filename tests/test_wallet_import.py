"""Tests for safe Solana mnemonic derivation."""

import pytest

from dashboard.wallet_import import derive_from_mnemonic

TEST_MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)


def test_phantom_derivation_path_is_preserved() -> None:
    wallet = derive_from_mnemonic(TEST_MNEMONIC)
    assert wallet["pubkey"] == "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk"


def test_invalid_mnemonic_is_rejected() -> None:
    invalid = TEST_MNEMONIC.replace("about", "abandon")
    with pytest.raises(ValueError, match="Invalid seed phrase"):
        derive_from_mnemonic(invalid)
