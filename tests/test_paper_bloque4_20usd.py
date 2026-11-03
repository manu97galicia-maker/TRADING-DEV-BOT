from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load(
    (ROOT / "bots" / "bot-paper-bloque4-20usd.yaml").read_text(encoding="utf-8")
)


def test_experiment_is_paper_only_and_uses_twenty_dollars():
    assert CFG["filters"]["paper_trade"] is True
    assert CFG["trade"]["max_real_buys"] == 0
    assert CFG["trade"]["buy_amount_usd"] == 20
    assert CFG["trade"]["stop_loss_percentage"] == 0
    assert "take_profit_percentage" not in CFG["trade"]


def test_exact_first_block_entry_contract():
    filters = CFG["filters"]
    assert filters["min_first_block_buys"] == 4
    assert filters["max_first_block_buys"] == 4
    assert filters["min_first_block_usd"] == 375
    assert filters["max_first_block_usd"] == 900
    assert filters["buy_from_second_block"] is True
    assert filters["paper_watch_secs"] == 600
