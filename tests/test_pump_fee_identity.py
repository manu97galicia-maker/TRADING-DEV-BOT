"""Pump fee recipients are protocol addresses, never substitute dev identities."""

from __future__ import annotations

import copy
import unittest

from solders.pubkey import Pubkey

from interfaces.core import Platform, TokenInfo
from trading.pump_fee_identity import (
    bind_dev_fee_creator,
    fee_creator_allowed,
    sharing_config_address,
)

PUMP = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
FEES = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
MINT = Pubkey.from_string("7UjCFv9FF4nWAB63vr6WdwAsmFr66hKK3RnLqvKapump")
ORIGINAL = Pubkey.from_string("866SrsMfwKsErATZDCLAPbxH3NuqjKn7zQ1xXMqixoz1")
SHARING = Pubkey.from_string("3MuYUdfw3beDmGvcA7RTGatGo4QHHepkFNdQd2CVbXvh")
TOKEN_PROGRAM = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")


def vault(creator: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([b"creator-vault", bytes(creator)], PUMP)[0]


def token() -> TokenInfo:
    return TokenInfo(
        name="Synthetic fee-sharing fixture", symbol="TEST", uri="", mint=MINT,
        platform=Platform.PUMP_FUN, creator=ORIGINAL, creator_vault=vault(ORIGINAL),
        user=ORIGINAL, token_program_id=TOKEN_PROGRAM,
        bonding_curve=Pubkey.find_program_address([b"bonding-curve", bytes(MINT)], PUMP)[0],
        associated_bonding_curve=Pubkey.new_unique(), is_mayhem_mode=False,
        additional_data={"developer": str(ORIGINAL), "target_slot": 102, "existing": "preserve"},
    )


def snapshot_token(info: TokenInfo) -> TokenInfo:
    snapshot = copy.copy(info)
    snapshot.additional_data = copy.deepcopy(info.additional_data)
    return snapshot


class PumpFeeIdentityTests(unittest.TestCase):
    def test_documented_seed_reproduces_the_public_space_inu_sharing_address(self) -> None:
        independent = Pubkey.find_program_address([b"sharing-config", bytes(MINT)], FEES)[0]
        self.assertEqual(independent, SHARING)
        self.assertEqual(sharing_config_address(str(MINT)), str(SHARING))
        self.assertFalse(SHARING.is_on_curve())

    def test_original_fee_recipient_is_accepted_before_migration(self) -> None:
        self.assertTrue(fee_creator_allowed(str(MINT), str(ORIGINAL), str(ORIGINAL)))

    def test_original_or_sharing_address_accepts_public_key_objects_and_strings(self) -> None:
        for mint in (MINT, str(MINT)):
            for original in (ORIGINAL, str(ORIGINAL)):
                for observed in (ORIGINAL, str(ORIGINAL), SHARING, str(SHARING)):
                    with self.subTest(mint=mint, original=original, observed=observed):
                        self.assertTrue(fee_creator_allowed(mint, original, observed))

    def test_original_fee_creator_may_be_a_program_address_without_becoming_the_dev(self) -> None:
        original_pda = Pubkey.find_program_address([b"original-fee-recipient"], FEES)[0]
        self.assertTrue(fee_creator_allowed(MINT, original_pda, original_pda))
        self.assertTrue(fee_creator_allowed(MINT, original_pda, SHARING))

    def test_another_mints_sharing_address_is_rejected(self) -> None:
        other = Pubkey.find_program_address([b"sharing-config", bytes(Pubkey.new_unique())], FEES)[0]
        self.assertNotEqual(other, SHARING)
        self.assertFalse(fee_creator_allowed(MINT, ORIGINAL, other))

    def test_another_program_or_seed_is_not_an_allowed_fee_recipient(self) -> None:
        for recipient in (PUMP, FEES, vault(SHARING), Pubkey.new_unique(),
                          Pubkey.find_program_address([b"sharing-config", bytes(MINT)], PUMP)[0]):
            with self.subTest(recipient=recipient):
                self.assertFalse(fee_creator_allowed(MINT, ORIGINAL, recipient))

    def test_missing_or_invalid_identity_cannot_be_treated_as_verified(self) -> None:
        for invalid in (None, "", "not-a-public-key", True, 1, b"bad", [], {}):
            for position in range(3):
                values = [MINT, ORIGINAL, SHARING]
                values[position] = invalid
                with self.subTest(position=position, value=invalid):
                    self.assertFalse(fee_creator_allowed(*values))

    def test_binding_shared_recipient_rederives_vault_and_preserves_dev_and_token(self) -> None:
        info = token()
        before = snapshot_token(info)
        self.assertTrue(bind_dev_fee_creator(info, ORIGINAL, SHARING))
        self.assertEqual(info.creator, SHARING)
        self.assertEqual(info.creator_vault, vault(SHARING))
        self.assertNotEqual(info.creator_vault, before.creator_vault)
        for field in ("mint", "user", "platform", "token_program_id", "bonding_curve",
                      "associated_bonding_curve", "is_mayhem_mode", "is_cashback_coin"):
            with self.subTest(field=field):
                self.assertEqual(getattr(info, field), getattr(before, field))
        self.assertEqual(info.additional_data, {
            **before.additional_data, "dev_creation_fee_creator": str(ORIGINAL),
            "dev_current_fee_creator": str(SHARING),
        })

    def test_binding_original_also_replaces_a_stale_precomputed_vault(self) -> None:
        info = token()
        info.creator_vault = Pubkey.new_unique()
        self.assertTrue(bind_dev_fee_creator(info, str(ORIGINAL), str(ORIGINAL)))
        self.assertEqual(info.creator, ORIGINAL)
        self.assertEqual(info.creator_vault, vault(ORIGINAL))

    def test_repeated_binding_is_idempotent_and_preserves_the_original_identity(self) -> None:
        info = token()
        self.assertTrue(bind_dev_fee_creator(info, ORIGINAL, SHARING))
        once = snapshot_token(info)
        self.assertTrue(bind_dev_fee_creator(info, str(ORIGINAL), str(SHARING)))
        self.assertEqual(info, once)
        self.assertEqual(info.user, ORIGINAL)

    def test_rejected_recipient_leaves_every_field_and_metadata_unchanged(self) -> None:
        for recipient in (None, "bad", Pubkey.new_unique(), vault(SHARING)):
            info = token()
            before = snapshot_token(info)
            with self.subTest(recipient=recipient):
                self.assertFalse(bind_dev_fee_creator(info, ORIGINAL, recipient))
                self.assertEqual(info, before)

    def test_existing_origin_cannot_be_rewritten_even_to_a_different_valid_address(self) -> None:
        for existing in (str(Pubkey.new_unique()), None, False, "bad"):
            info = token()
            info.additional_data["dev_creation_fee_creator"] = existing
            before = snapshot_token(info)
            with self.subTest(existing=existing):
                self.assertFalse(bind_dev_fee_creator(info, ORIGINAL, SHARING))
                self.assertEqual(info, before)

    def test_invalid_mint_or_original_cannot_partially_update_the_recipient(self) -> None:
        for bad_mint, bad_original in (("bad", ORIGINAL), (MINT, None), (MINT, "bad")):
            info = token()
            info.mint = bad_mint
            before = snapshot_token(info)
            with self.subTest(mint=bad_mint, original=bad_original):
                self.assertFalse(bind_dev_fee_creator(info, bad_original, SHARING))
                self.assertEqual(info, before)

    def test_missing_metadata_is_initialized_only_after_a_valid_binding(self) -> None:
        info = token()
        info.additional_data = None
        self.assertTrue(bind_dev_fee_creator(info, ORIGINAL, SHARING))
        self.assertEqual(info.additional_data, {
            "dev_creation_fee_creator": str(ORIGINAL), "dev_current_fee_creator": str(SHARING),
        })


if __name__ == "__main__":
    unittest.main()
