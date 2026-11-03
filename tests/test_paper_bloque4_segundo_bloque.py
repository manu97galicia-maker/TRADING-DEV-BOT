from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load(
    (ROOT / "bots" / "bot-paper-bloque4-segundo-bloque.yaml").read_text(
        encoding="utf-8"
    )
)


def test_strategy_is_irreversibly_paper_only():
    """REAL PROHIBIDO, pedido el 3-nov: "Si primero prohibo real".

    Esta prueba se apoya SOLO en los dos frenos que de verdad paran una
    compra, porque son los unicos que estan en el camino del dinero:

      `paper_trade: true`  -> universal_trader.py:4763 corta la compra ahi
                              mismo y solo sigue el precio. No se firma nada.
      `enabled: false`     -> el bot no carga la estrategia.

    El tercer "cerrojo" que figuraba aqui NO frena nada: ver
    `test_max_real_buys_en_cero_no_frena_nada`, mas abajo.
    """
    assert CFG["filters"]["paper_trade"] is True, (
        "REAL PROHIBIDO en esta estrategia por orden del 3-nov. Si se quiere "
        "levantar, hay que pedirlo y resolver antes lo que exige "
        "test_lo_que_falta_antes_de_poder_ir_a_real"
    )
    assert CFG["enabled"] is False, (
        "REAL PROHIBIDO: la estrategia tampoco se activa. Es el segundo freno"
    )
    assert CFG["trade"]["buy_amount_usd"] == 20
    assert CFG["trade"]["stop_loss_percentage"] == 0.08


def test_max_real_buys_en_cero_no_frena_nada():
    """Que nadie vuelva a contar el 0 como un freno.

    `max_real_buys: 0` NO significa "cero compras". En
    universal_trader.py:4855 el tope se comprueba asi:

        if (self.max_real_buys and self._compras_de_la_tanda >= ...)

    El 0 es falso en Python, asi que la condicion no entra nunca y el tope
    queda APAGADO: compras ilimitadas. El numero se deja como esta (lo puso
    Manu), pero aqui queda escrito lo que hace de verdad, para que no se
    apunte en la lista de seguridades.
    """
    assert CFG["trade"]["max_real_buys"] == 0


def test_lo_que_falta_antes_de_poder_ir_a_real():
    """Mientras siga prohibido, no exige nada. El dia que se levante, si.

    No bloquea el paso a real: lo que hace es que ese paso no pueda darse en
    silencio. El dia que alguien ponga `paper_trade: false`, esta prueba se
    enciende y dice exactamente lo que hay que resolver. Los cuatro puntos
    salen de leer el codigo el 3-nov, no de una opinion.
    """
    if CFG["filters"]["paper_trade"] is True:
        return  # Sigue prohibido: no hay nada que exigir todavia.

    trade, fees = CFG["trade"], CFG["priority_fees"]

    # 1. EL STOP TIENE QUE PODER LLEGAR A TIEMPO. La cuenta es la de
    #    universal_trader.py:2206, que deja media cuota para comprar:
    #        vigilables = max_rps * price_check_interval / 2
    #    Con max_rps 12 e intervalo 1s salen 6, y el fichero pide 20
    #    posiciones: 14 sin vigilar. Con un stop del 8% eso es lo mas grave,
    #    porque un stop tan corto solo sirve si reacciona al instante.
    vigilables = int(
        float(CFG["node"]["max_rps"]) * float(trade["price_check_interval"]) / 2
    )
    assert trade["max_posiciones"] <= vigilables, (
        f"{trade['max_posiciones']} posiciones y el nodo solo vigila "
        f"{vigilables}: el stop del {trade['stop_loss_percentage']:.0%} "
        f"llegaria tarde en las demas. Sube node.max_rps o "
        f"price_check_interval, o baja max_posiciones"
    )

    # 2. CON QUE PAGAR LAS VENTAS. min_reserve_sol: 0 gasta hasta el ultimo
    #    SOL y deja la wallet sin comision para VENDER lo que compro.
    assert float(trade["min_reserve_sol"]) > 0, (
        "min_reserve_sol: 0 no reserva nada para comisiones; sin SOL no se "
        "puede vender lo comprado"
    )

    # 3. PRIORITY FEE. En papel no se firma, asi que da igual. En real se
    #    compite por entrar en un bloque, y con retries.max_attempts: 1 no
    #    hay segundo intento.
    assert bool(fees["enable_dynamic"]) or bool(fees["enable_fixed"]), (
        "sin priority fee y con un solo intento, la compra real entra tarde "
        "o no entra"
    )

    # 4. QUE LAS POSICIONES CADUQUEN. max_hold_time: 0 las deja abiertas
    #    indefinidamente ocupando plaza de las que si se mueven.
    assert float(trade["max_hold_time"]) > 0, (
        "max_hold_time: 0 deja las posiciones abiertas para siempre"
    )


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
