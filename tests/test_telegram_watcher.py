"""Tests for Telegram contract extraction."""

import unittest

from monitoring.telegram_watcher import extract_contract_addresses

PUMP_MINT = "74HeafkLcU3UcVU95yJDHyCXgqrwLvNp7qLnXWRGpump"
PLAIN_MINT = "EfXFcMXzGnRqUYh1S1tETtfhM2pZ7nvfyFWxqwmCeqMe"


class TelegramWatcherTests(unittest.TestCase):
    """Verify strict and suffix-less Telegram extraction modes."""

    def test_default_requires_known_launchpad_suffix(self) -> None:
        self.assertEqual(
            extract_contract_addresses(f"{PLAIN_MINT} {PUMP_MINT}"),
            [PUMP_MINT],
        )

    def test_all_solana_mode_accepts_plain_mints_in_order(self) -> None:
        self.assertEqual(
            extract_contract_addresses(
                f"{PLAIN_MINT} {PUMP_MINT} {PLAIN_MINT}",
                accept_all_solana=True,
            ),
            [PLAIN_MINT, PUMP_MINT],
        )

    def test_all_solana_mode_rejects_non_pubkeys(self) -> None:
        self.assertEqual(
            extract_contract_addresses(
                "1111111111111111111111111111111",
                accept_all_solana=True,
            ),
            [],
        )
