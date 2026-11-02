from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load(
    (ROOT / "bots" / "bot-paper-bloque4-segundo-bloque.yaml").read_text(
        encoding="utf-8"
    )
)


def test_strategy_is_irreversibly_paper_only():
    assert CFG["filters"]["paper_trade"] is True
    assert CFG["trade"]["max_real_buys"] == 0
    assert CFG["trade"]["buy_amount_usd"] == 20
    assert CFG["trade"]["stop_loss_percentage"] == 0


def test_second_block_hypothesis_contract():
    filters = CFG["filters"]
    assert filters["min_first_block_buys"] == 4
    assert filters["max_first_block_buys"] == 4
    assert filters["min_first_block_usd"] == 375
    assert filters["max_first_block_usd"] == 900
    assert filters["buy_from_second_block"] is True
    assert filters["second_block_confirmation"] == {
        "enabled": True,
        "min_buyers": 2,
        "min_buy_sol": 0.25,
        "min_net_sol": 0.25,
        "max_sells": 0,
    }
