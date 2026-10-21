"""Offline safety tests for the BSC real-ready runtime."""

# Pytest relies on plain assertions for rich failure output.
# ruff: noqa: S101

from pathlib import Path

import pytest
import yaml

from bsc.runtime import BscRuntime, BscSafetyError, RuntimeSettings

ROOT = Path(__file__).resolve().parents[1]
ENTRY_USD = 25
MAX_ENTRY_MC_USD = 9_000
MAX_BUY_GAS_USD = 1.30


def test_real_template_is_locked() -> None:
    config = yaml.safe_load(
        (ROOT / "bots" / "bot-bsc-four-flap-real.yaml").read_text(encoding="utf-8")
    )

    assert config["mode"]["paper_trade"] is True
    assert config["mode"]["live_transactions"] is False
    assert config["entry"]["amount_usd"] == ENTRY_USD
    assert config["entry"]["max_market_cap_usd"] == MAX_ENTRY_MC_USD
    assert config["gas"]["max_buy_gas_usd"] == MAX_BUY_GAS_USD


def test_yaml_contains_no_private_key() -> None:
    text = (ROOT / "bots" / "bot-bsc-four-flap-real.yaml").read_text(encoding="utf-8")

    assert "0xTU_CLAVE" not in text
    assert "private_key_env: BSC_PRIVATE_KEY" in text


def test_runtime_refuses_signer_while_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BSC_PRIVATE_KEY", "not-used")
    monkeypatch.setenv("BSC_ENABLE_LIVE_TRADING", "I_UNDERSTAND_BSC_LIVE_TRADING")
    runtime = BscRuntime(RuntimeSettings("https://example.invalid"))

    with pytest.raises(BscSafetyError, match="disabled"):
        runtime.unlock_live_signer()


def test_runtime_requires_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BSC_ENABLE_LIVE_TRADING", raising=False)
    settings = RuntimeSettings(
        "https://example.invalid", paper_trade=False, live_transactions=True
    )
    runtime = BscRuntime(settings)

    with pytest.raises(BscSafetyError, match="confirmation"):
        runtime.unlock_live_signer()
