from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load(
    (ROOT / "bots" / "bot-paper-bloque4-segundo-bloque.yaml").read_text(
        encoding="utf-8"
    )
)


def test_strategy_is_irreversibly_paper_only():
    """LA PROHIBICION SE LEVANTO, y queda escrito.

    El 3-nov por la noche se pidio "Si primero prohibo real", y este test la
    hacia cumplir. Esa misma noche se pidio lo contrario: "Pon en real dime
    como pongo en droplet". El nombre del test se conserva para que el
    historial se lea; lo que vigila ahora es que el paso a real fue a
    sabiendas y que la estrategia no se cuela en el arranque de "todas".
    """
    assert CFG["filters"]["paper_trade"] is False, (
        "volvio a papel: se puso en real el 3-nov por orden expresa de Manu"
    )
    # `enabled` solo lo mira el arranque de "todas" (bot_runner.py:539).
    # Esta se lanza sola con run_bot_process, que no lo mira. En false no se
    # cuela como cuarta estrategia junto a las otras tres.
    assert CFG["enabled"] is False, (
        "con enabled: true esta estrategia arrancaria tambien junto a las "
        "otras tres en `python -m bot_runner`, sobre la misma wallet"
    )
    texto = (ROOT / "bots" / "bot-paper-bloque4-segundo-bloque.yaml").read_text(
        encoding="utf-8"
    ).lower()
    assert "ya no es de papel" in texto
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
    """En real, cada uno de los cuatro puntos o se arregla o se acepta.

    Esta prueba se escribio el 3-nov para que el paso a real no pudiera darse
    en silencio. Se dio, pidiendolo, y los cuatro puntos se aceptaron como
    estan. Lo que se exige ahora es que cada punto sin arreglar lleve su linea
    "ACEPTADO: ..." en el YAML. Si se arregla el numero, la linea sobra; si
    se borra la linea sin arreglar el numero, esto falla.
    """
    if CFG["filters"]["paper_trade"] is True:
        return  # En papel no hay nada que exigir.

    trade, fees = CFG["trade"], CFG["priority_fees"]
    texto = (ROOT / "bots" / "bot-paper-bloque4-segundo-bloque.yaml").read_text(
        encoding="utf-8"
    )

    # 1. Plazas contra lo que vigila el nodo (universal_trader.py:2206).
    vigilables = int(
        float(CFG["node"]["max_rps"]) * float(trade["price_check_interval"]) / 2
    )
    if trade["max_posiciones"] > vigilables:
        marca = (
            f"ACEPTADO: {trade['max_posiciones']} PLAZAS CON UN NODO QUE "
            f"VIGILA {vigilables}"
        )
        assert marca in texto, (
            f"{trade['max_posiciones']} posiciones y el nodo solo vigila "
            f"{vigilables}: el stop del {trade['stop_loss_percentage']:.0%} "
            f"llegaria tarde en las demas. O se arregla, o el YAML dice: {marca}"
        )

    # 2. Reserva para pagar las ventas.
    if float(trade["min_reserve_sol"]) <= 0:
        assert "ACEPTADO: SIN RESERVA PARA COMISIONES" in texto, (
            "min_reserve_sol: 0 no reserva nada para la comision de vender"
        )

    # 3. Priority fee.
    if not (bool(fees["enable_dynamic"]) or bool(fees["enable_fixed"])):
        assert "ACEPTADO: SIN PRIORITY FEE" in texto, (
            "sin priority fee y con un solo intento, la compra real entra "
            "tarde o no entra"
        )

    # 4. Caducidad.
    if float(trade["max_hold_time"]) <= 0:
        assert "ACEPTADO: SIN CADUCIDAD" in texto, (
            "max_hold_time: 0 deja las posiciones abiertas para siempre"
        )
