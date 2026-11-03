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
    assert CFG["trade"]["max_posiciones"] == 8
    assert CFG["trade"]["stop_loss_percentage"] == 0.08


def test_paper_exit_ladder_and_protected_runner():
    trade = CFG["trade"]
    assert trade["take_profit_percentage"] == 0.15
    assert trade["take_profit_sell_percentage"] == 0.9391304348
    assert trade["tp2_gain"] == 1.00
    assert trade["tp2_sell"] == 0.0456521739
    assert "tp3_gain" not in trade
    assert "tp3_sell" not in trade
    # TP1 recupera 108% bruto. TP2 vende el 75% de lo que quedó.
    assert abs(1.15 * trade["take_profit_sell_percentage"] - 1.08) < 1e-9
    left_after_tp1 = 1 - trade["take_profit_sell_percentage"]
    assert abs(trade["tp2_sell"] - left_after_tp1 * 0.75) < 1e-9
    assert abs(1 - trade["take_profit_sell_percentage"] - trade["tp2_sell"] - 0.0152173913) < 1e-9
    assert trade["stop_a_entrada_tras_tp"] is True
    assert trade["margen_stop_tras_tp"] == 0.03
    assert trade["stop_al_tp_tras_tp"] is False
    assert trade["vender_todo_en_stop"] is True


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
