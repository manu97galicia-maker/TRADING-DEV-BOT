"""Contratos de los dos experimentos nuevos.

EL 31-oct UNO DE LOS DOS PASO A REAL, pedido por Manu ("44b09e7 este ponlo
en real"). Este fichero nacio diciendo que AMBOS debian quedarse en papel;
esa regla sigue en pie para `bot-paper-mayhem-24k`, que nadie ha pedido
mover, y se levanta solo para `bot-paper-presion50-24k`.

Se deja escrito aqui porque es justo lo que estos contratos existen para
evitar: que un experimento se ponga a operar con dinero sin que nadie lo
note.
"""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent


def _cfg(name: str) -> dict:
    return yaml.safe_load((ROOT / "bots" / name).read_text(encoding="utf-8"))


def test_mayhem_usa_importe_fijo_y_lista_medida() -> None:
    cfg = _cfg("bot-paper-mayhem-24k.yaml")
    trade, filters = cfg["trade"], cfg["filters"]
    # paper_trade ya no se comprueba aqui: lo hace, y al contrario, el test
    # `test_el_mayhem_esta_en_real_y_acotado`. `paper_max_samples` se queda
    # escrito pero SIN EFECTO: solo se mira dentro de la rama
    # `if self.paper_trade:` de universal_trader.py:4763.
    assert filters["paper_max_samples"] == 5
    assert filters["require_mayhem"] is True
    assert trade["buy_amount"] == trade["mayhem_buy_amount"] == 0.03
    assert trade["fraccion_saldo"] == 0
    assert trade["tope_compra_sol"] == 0.03
    assert filters["bro_address"] == "top_devs_mayhem_66.json"
    assert filters["min_presion_ratio"] == 1.4
    assert trade["stop_loss_percentage"] == 0.70
    assert trade["final_target_mc"] == 24000
    assert "take_profit_percentage" not in trade


def test_lista_mayhem_es_el_corte_de_3_y_66_7() -> None:
    data = yaml.safe_load((ROOT / "top_devs_mayhem_66.json").read_text(encoding="utf-8"))
    assert data["_count"] == len(data["wallets"]) == 11
    assert all(row["mayhem_launches"] >= 3 for row in data["wallets"])
    assert all(row["pct_pump_30k"] >= 66.7 for row in data["wallets"])


def test_presion50_exige_ocho_holders_presion_y_antimecha() -> None:
    cfg = _cfg("bot-paper-presion50-24k.yaml")
    trade, filters = cfg["trade"], cfg["filters"]
    curve = filters["curva_96"]
    # paper_trade ya no se comprueba aqui: lo hace, y al contrario, el test
    # `test_el_presion50_esta_en_real_a_sabiendas` del final del fichero.
    assert filters["min_entry_mc"] == 8000
    assert filters["max_entry_mc"] == 13000
    assert filters["min_holders"] == 8
    assert curve["umbral"] == 0.50
    assert curve["min_compradores"] == 8
    assert curve["min_compradores_ola"] == 3
    assert curve["min_presion_ratio"] == 1.6
    assert curve["min_compra_sol"] == 0.20
    assert curve["vela_seg"] == 1.0
    assert curve["max_subida_vela"] == 0.35
    assert trade["fraccion_saldo"] == 0.15
    assert trade["fraccion_base"] == "libre"
    assert trade["tope_compra_sol"] == 0
    assert trade["max_posiciones"] == 5
    assert trade["max_real_buys"] == 0
    assert trade["stop_loss_percentage"] == 0.70
    assert trade["final_target_mc"] == 24000
    assert "take_profit_percentage" not in trade


def test_el_mayhem_esta_en_real_y_acotado() -> None:
    """LOS DOS EXPERIMENTOS ESTAN YA EN REAL. Este fichero nacio diciendo
    "ambos deben permanecer en paper"; el 31-oct se levanto para presion50 y
    el 3-nov para mayhem ("me refiero a estas 3"). No queda ninguno en papel
    que vigilar, asi que lo que se vigila es lo contrario: que no vuelvan a
    papel por accidente, y que mayhem siga ACOTADO.

    El tope es lo unico que hace esto barato, y son tres numeros a la vez:
    `max_real_buys: 5` (5 es verdadero, asi que el tope de
    universal_trader.py:4855 entra), `reiniciar_tanda: false` (las 5 son de
    por vida, no por tanda) y 0,03 SOL por compra. Si se cae cualquiera de
    los tres, deja de estar acotado.
    """
    cfg = _cfg("bot-paper-mayhem-24k.yaml")
    assert cfg["filters"]["paper_trade"] is False, (
        "bot-paper-mayhem-24k volvio a papel: se puso en real el 3-nov por "
        "orden expresa de Manu"
    )
    trade = cfg["trade"]
    assert trade["max_real_buys"] == 5, (
        "sin el tope de 5 compras esto deja de estar acotado; con 0 el tope "
        "se APAGA (universal_trader.py:4855) y las compras son ilimitadas"
    )
    assert cfg["trade"]["reiniciar_tanda"] is False, (
        "con reiniciar_tanda: true las 5 compras se renuevan y el tope deja "
        "de ser de por vida"
    )
    assert trade["tope_compra_sol"] == 0.03
    # 5 x 0,03 = 0,15 SOL. Si esta cuenta cambia, cambio el riesgo.
    # El `round` es por prudencia con la coma flotante, no porque haga falta
    # hoy: 5 * 0.03 da 0.15 exacto. Con otros valores (3 * 0.05 da
    # 0.15000000000000002) fallaria por la aritmetica y no por el riesgo.
    assert round(trade["max_real_buys"] * trade["tope_compra_sol"], 10) == 0.15

    texto = (ROOT / "bots" / "bot-paper-mayhem-24k.yaml").read_text(
        encoding="utf-8"
    )
    llano = texto.replace("\u00e1", "a").lower()
    assert "ya no lo esta" in llano, (
        "el fichero se llama bot-paper-... y opera con dinero real; tiene "
        "que decirlo dentro para que nadie se confie del nombre"
    )


def test_el_presion50_esta_en_real_a_sabiendas() -> None:
    """Y el que SI se movio, que no vuelva a papel por accidente.

    Un experimento que pasa a real deja de ser un experimento: si alguien lo
    devuelve a `paper_trade: true` creyendo que sigue siendo una prueba, el
    bot dejaria de operar sin que nada lo dijera.
    """
    cfg = _cfg("bot-paper-presion50-24k.yaml")
    assert cfg["filters"]["paper_trade"] is False, (
        "bot-paper-presion50-24k volvio a papel: se puso en real el 31-oct "
        "por orden expresa de Manu"
    )
    # Y QUE EL FICHERO AVISE DE LO QUE ES. El nombre sigue diciendo "paper".
    texto = (ROOT / "bots" / "bot-paper-presion50-24k.yaml").read_text(
        encoding="utf-8"
    )
    assert "ya NO es de papel" in texto, (
        "el fichero se llama bot-paper-... y opera con dinero real; tiene "
        "que decirlo dentro para que nadie se confie del nombre"
    )
