"""Derive a Solana wallet from a BIP39 mnemonic (12 or 24 words).

Uses the standard Phantom/Solflare derivation path ``m/44'/501'/{account}'/0'``
so the imported address matches what those wallets show for the same seed.

Security: the mnemonic is used only to derive the key and is never stored or
logged here. The caller decides where to persist the derived private key.
"""

from __future__ import annotations

import base58
from mnemonic import Mnemonic
from solders.keypair import Keypair

DERIVATION_PATH = "m/44'/501'/{account}'/0'"


def derive_from_mnemonic(mnemonic: str, account: int = 0) -> dict[str, str]:
    """Derive a Solana keypair from a BIP39 mnemonic.

    Args:
        mnemonic: 12- or 24-word BIP39 seed phrase.
        account: Account index (0 = the first Phantom account).

    Returns:
        ``{"pubkey": <address>, "private_key_b58": <base58 64-byte key>}``.
        The base58 private key is the format the bot expects in
        ``SOLANA_PRIVATE_KEY``.

    Raises:
        ValueError: If the word count is wrong or the mnemonic is invalid.
    """
    words = mnemonic.strip().split()
    if len(words) not in (12, 24):
        msg = f"Expected 12 or 24 words, got {len(words)}"
        raise ValueError(msg)
    normalized = " ".join(words).lower()

    bip39 = Mnemonic("english")
    if not bip39.check(normalized):
        msg = "Invalid seed phrase (check the words and their order)"
        raise ValueError(msg)

    if account < 0:
        msg = "account index must be >= 0"
        raise ValueError(msg)

    seed_bytes = bip39.to_seed(normalized, passphrase="")
    derivation_path = f"m/44'/501'/{account}'/0'"
    keypair = Keypair.from_seed_and_derivation_path(seed_bytes, derivation_path)
    return {
        "pubkey": str(keypair.pubkey()),
        "private_key_b58": base58.b58encode(bytes(keypair)).decode(),
    }
