"""Unit tests for last-moment BSC tax guards."""

# Pytest relies on plain assertions for rich failure output.
# ruff: noqa: S101

from typing import Any

import pytest

from bsc.flap import FlapAdapter
from bsc.four_meme import FourMemeAdapter
from bsc.runtime import BscSafetyError


@pytest.mark.parametrize("adapter_type", [FourMemeAdapter, FlapAdapter])
def test_exactly_six_percent_is_allowed(
    adapter_type: type[FourMemeAdapter] | type[FlapAdapter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter: Any = object.__new__(adapter_type)
    monkeypatch.setattr(adapter, "token_taxes_bps", lambda _token: (300, 300))

    assert adapter.assert_tax_limit("unused") == (300, 300)


@pytest.mark.parametrize("adapter_type", [FourMemeAdapter, FlapAdapter])
def test_more_than_six_percent_is_rejected(
    adapter_type: type[FourMemeAdapter] | type[FlapAdapter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter: Any = object.__new__(adapter_type)
    monkeypatch.setattr(adapter, "token_taxes_bps", lambda _token: (301, 300))

    with pytest.raises(BscSafetyError, match="exceeds 6%"):
        adapter.assert_tax_limit("unused")
