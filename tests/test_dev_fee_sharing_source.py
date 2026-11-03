"""Official creator-fee routing never substitutes the verified launch signer."""

# ruff: noqa: ANN001, ANN201

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_dev_second_window_source as strict
import test_second_block_source as stream
from solders.pubkey import Pubkey

FEE_PROGRAM = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
SI_MINT = "7UjCFv9FF4nWAB63vr6WdwAsmFr66hKK3RnLqvKapump"
SI_DEVELOPER = "866SrsMfwKsErATZDCLAPbxH3NuqjKn7zQ1xXMqixoz1"
SI_SHARING = "3MuYUdfw3beDmGvcA7RTGatGo4QHHepkFNdQd2CVbXvh"


def sharing_config(mint):
    return Pubkey.find_program_address([b"sharing-config", bytes(mint)], FEE_PROGRAM)[0]


class DevFeeSharingSourceTests(unittest.IsolatedAsyncioTestCase):
    settle = strict.DevSecondWindowSourceTests.settle
    created = strict.DevSecondWindowSourceTests.created

    async def asyncSetUp(self):
        await strict.DevSecondWindowSourceTests.asyncSetUp(self)
        self.policy["require_first_block_activity"] = False
        self.source.history.evidence = Mock(return_value={"migration_fraction": .01})

    async def enter_second(self):
        self.source.on_slot({"slot": 105, "parent": 100})
        await self.settle()

    async def test_ordinary_fee_creator_remains_eligible_with_explicit_identity_metadata(self):
        await self.created(stream.trade(self.mint, self.creator, sol=.001))
        await self.enter_second()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["creator"], str(self.creator))
        self.assertEqual(signal["creation_fee_creator"], str(self.creator))
        self.assertEqual(signal["prepared_fee_creator"], str(self.creator))
        self.assertEqual(signal["developer"], str(self.creator))
        self.assertTrue(signal["dev_initial_buy_verified"])

    async def test_canonical_sharing_configuration_is_accepted_without_changing_dev_history(self):
        original = self.creator
        canonical = sharing_config(self.mint)
        self.token.creator = canonical
        await self.created(stream.trade(self.mint, original, sol=.001))
        self.callback.assert_not_awaited()
        candidate = self.source.candidates[str(self.mint)]
        self.assertEqual(candidate.creator, str(original))
        self.assertEqual(candidate.developer, str(original))
        self.assertIs(candidate.token, self.token)
        await self.enter_second()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["creator"], str(original))
        self.assertEqual(signal["creation_fee_creator"], str(original))
        self.assertEqual(signal["prepared_fee_creator"], str(canonical))
        self.assertEqual(signal["developer"], str(original))
        self.assertTrue(signal["dev_initial_buy_verified"])
        self.source.history.observe.assert_called_once_with(str(original), str(self.mint))
        self.source.history.evidence.assert_any_call(str(original), str(self.mint))
        for args in self.source.history.evidence.call_args_list:
            self.assertEqual(args.args[0], str(original))
        delivered = self.callback.await_args.args[0]
        self.assertEqual(delivered.creator, canonical)
        self.assertEqual(delivered.additional_data["dev_entry_target_slot"], 105)

    async def test_space_inu_public_address_fixture_accepts_its_official_sharing_pda(self):
        self.mint = Pubkey.from_string(SI_MINT)
        self.creator = Pubkey.from_string(SI_DEVELOPER)
        self.token.mint = self.mint
        self.token.creator = Pubkey.from_string(SI_SHARING)
        self.assertEqual(sharing_config(self.mint), self.token.creator)
        await self.created(stream.trade(self.mint, self.creator, sol=3))
        await self.enter_second()
        self.callback.assert_awaited_once()
        signal = self.source.signal(SI_MINT)
        self.assertEqual(signal["creation_fee_creator"], SI_DEVELOPER)
        self.assertEqual(signal["prepared_fee_creator"], SI_SHARING)
        self.assertEqual(signal["developer"], SI_DEVELOPER)
        self.assertTrue(signal["dev_initial_buy_verified"])

    async def test_unrelated_fee_recipient_does_not_prepare_a_candidate(self):
        self.token.creator = self.a
        await self.created(stream.trade(self.mint, self.creator, sol=.001))
        await self.enter_second()
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.candidates[str(self.mint)].token)
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_another_mints_canonical_sharing_pda_is_rejected(self):
        self.token.creator = sharing_config(Pubkey.new_unique())
        await self.created(stream.trade(self.mint, self.creator, sol=.001))
        await self.enter_second()
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.candidates[str(self.mint)].token)
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_canonical_fee_recipient_cannot_bypass_below_one_percent_history(self):
        self.token.creator = sharing_config(self.mint)
        self.source.history.evidence.return_value = {"migration_fraction": .009999}
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock) as builder:
            await self.created(stream.trade(self.mint, self.creator, sol=.001))
            await self.enter_second()
            builder.assert_not_awaited()
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_canonical_fee_recipient_cannot_replace_missing_initial_dev_buy(self):
        self.token.creator = sharing_config(self.mint)
        with patch("monitoring.second_block_source.build_pumpfun_token_info", new_callable=AsyncMock) as builder:
            await self.created(stream.trade(self.mint, self.a, sol=.001))
            await self.enter_second()
            builder.assert_not_awaited()
        self.callback.assert_not_awaited()
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_distinct_original_fee_creator_and_developer_keep_the_actual_launch_signer(self):
        fee_creator, developer = self.creator, self.a
        self.token.creator = sharing_config(self.mint)
        await self.created(stream.trade(self.mint, developer, sol=.001), user=developer)
        await self.enter_second()
        self.callback.assert_awaited_once()
        candidate = self.source.candidates[str(self.mint)]
        signal = self.source.signal(str(self.mint))
        self.assertEqual(candidate.creator, str(fee_creator))
        self.assertEqual(candidate.developer, str(developer))
        self.assertEqual(signal["creation_fee_creator"], str(fee_creator))
        self.assertEqual(signal["developer"], str(developer))
        self.assertTrue(signal["dev_initial_buy_verified"])
        self.source.history.observe.assert_called_once_with(str(developer), str(self.mint))
        self.source.history.evidence.assert_any_call(str(developer), str(self.mint))

    async def test_late_sharing_preparation_cannot_reopen_the_second_block_window(self):
        self.token.creator = sharing_config(self.mint)
        release = asyncio.Event()

        async def prepare(*_args: object, **_kwargs: object) -> object:
            await release.wait()
            return self.token

        with patch("monitoring.second_block_source.build_pumpfun_token_info", side_effect=prepare):
            await self.created(stream.trade(self.mint, self.creator, sol=.001))
            self.source.on_slot({"slot": 105, "parent": 100})
            self.source.on_slot({"slot": 106, "parent": 105})
            release.set()
            await self.settle()
        self.callback.assert_not_awaited()
        self.assertNotIn(str(self.mint), self.source.candidates)
        self.assertIsNone(self.source.signal(str(self.mint)))

    async def test_classic_profile_also_records_the_original_and_prepared_fee_recipient(self):
        self.policy["entry_rule"] = "second_block"
        self.policy.pop("entry_timing")
        self.policy.pop("require_dev_initial_buy")
        self.token.creator = sharing_config(self.mint)
        await self.created(stream.trade(self.mint, self.creator, sol=1.5),
                           stream.trade(self.mint, self.b, sol=1.5))
        await self.enter_second()
        self.callback.assert_awaited_once()
        signal = self.source.signal(str(self.mint))
        self.assertEqual(signal["creator"], str(self.creator))
        self.assertEqual(signal["creation_fee_creator"], str(self.creator))
        self.assertEqual(signal["prepared_fee_creator"], str(self.token.creator))


if __name__ == "__main__":
    unittest.main()
