"""Contratos de la estrategia DEV 1% + PRIMER BLOQUE + ENTRADA EN BLOQUE 2.

Creada el 3-nov, pedido: "snipear las monedas con el developer que tenga un
minimo 1% de proyectos migrados y que en el primer bloque haya dos compras
minimo y por un volumen minimo de $375. Pagamos 6 centimos de priority fees
para entrar de primeros en el segundo bloque."
"""

from pathlib import Path

import yaml

from platforms.pumpfun.instruction_builder import PumpFunInstructionBuilder

ROOT = Path(__file__).resolve().parents[1]
FICHERO = ROOT / "bots" / "bot-paper-dev1-bloque2.yaml"
CFG = yaml.safe_load(FICHERO.read_text(encoding="utf-8"))
TRADER = (ROOT / "src" / "trading" / "universal_trader.py").read_text(
    encoding="utf-8"
)
SOL_USD_REFERENCIA = 105.0


def test_esta_en_papel_y_no_se_cuela_en_todas() -> None:
    assert CFG["filters"]["paper_trade"] is True
    assert "nunca firma ni mueve sol" in FICHERO.read_text(encoding="utf-8").lower()
    # enabled solo lo mira el arranque de "todas"; en false no arranca junto
    # a las demas estrategias sobre la misma wallet.
    assert CFG["enabled"] is False


def test_dev_con_minimo_un_uno_por_ciento_de_migradas() -> None:
    dev = CFG["filters"]["pumpfun_dev"]
    assert dev["enabled"] is True
    assert dev["min_rate"] == 0.01
    # "1% de migradas" exige al menos una: con 0 migradas la tasa es 0.
    assert dev["min_migrations"] == 1
    # 5-nov: el 1% es la unica condicion y no admite rutas alternativas.
    assert dev["compra_inmediata"] is True
    assert dev["timeout_seg"] >= 9
    assert dev["strict_only"] is True
    assert CFG["filters"]["ignorar_listas_de_devs"] is True


def test_el_dev_es_la_unica_condicion_de_entrada() -> None:
    """5-nov: "Solo activa la estrategia de comprar cuando el 1%... " ->
    "Claro que compre muchas mas". Fuera el primer bloque y el segundo bloque."""
    f = CFG["filters"]
    assert f["min_first_block_buys"] == 0
    assert f["max_first_block_buys"] == 0
    assert f["min_first_block_usd"] == 0
    assert f["max_first_block_usd"] == 0
    assert f["buy_from_second_block"] is False
    assert f["second_block_confirmation"]["enabled"] is False
    # Solo quedan dev 1% y edad 1-2 s; no MC, presión, Mayhem, listas ni bloques.
    assert f["max_entry_mc"] == 0
    assert f.get("min_entry_mc", 0) == 0
    assert f["max_buy_age"] == 0
    assert f["allow_mayhem"] is True
    assert f["require_mayhem"] is False
    assert f["ignorar_listas_de_devs"] is True
    assert f["curva_96"]["min_edad_seg"] == 1
    assert f["curva_96"]["max_edad_seg"] == 2
    assert f["curva_96"]["tope"] == 1.0


def test_la_compra_paga_doce_centimos_de_priority_fee() -> None:
    fees = CFG["priority_fees"]
    assert fees["enable_fixed"] is True
    assert fees["enable_dynamic"] is False
    # Las unidades salen del propio constructor de instrucciones: si algun dia
    # la compra reserva otras, los 6 centimos dejan de serlo y esto lo dice.
    # Se crea sin __init__ porque este calculo no usa el parser del IDL.
    builder = object.__new__(PumpFunInstructionBuilder)
    unidades = builder.get_buy_compute_unit_limit()
    microlamports = int(fees["fixed_amount_compra"])
    dolares = microlamports * unidades / 1e6 / 1e9 * SOL_USD_REFERENCIA
    # 5-nov: "Sube a 12 centimos" (venia de 6).
    assert abs(dolares - 0.12) < 0.0005, f"la compra paga {dolares:.4f} $"
    # Las ventas normales no se pidio subirlas: siguen como estaban.
    assert int(fees["fixed_amount"]) == 5714286
    # El tope no puede recortar la comision de entrada por debajo de lo pedido.
    assert int(fees["hard_cap"]) >= microlamports


def test_los_filtros_corren_antes_del_corte_de_papel() -> None:
    """Si el corte de papel fuera antes, la prueba en papel no mediria nada."""
    corte = TRADER.index("            if self.paper_trade:")
    assert TRADER.index('if self.pumpfun_dev.get("compra_inmediata")') < corte
    assert TRADER.index('if self.pumpfun_dev.get("enabled"):') < corte


def test_plazas_que_el_nodo_vigila_y_reserva_para_vender() -> None:
    t = CFG["trade"]
    vigilables = int(
        float(CFG["node"]["max_rps"]) * float(t["price_check_interval"]) / 2
    )
    # 7 plazas pedidas el 4-nov; max_rps 14 para que el nodo vigile las 7.
    assert t["max_posiciones"] == 7
    assert t["max_posiciones"] <= vigilables, (
        f"{t['max_posiciones']} plazas y el nodo solo vigila {vigilables}"
    )
    assert float(t["min_reserve_sol"]) > 0


def test_salida_del_4_nov() -> None:
    """TP 38% recupera iniciales +10%; el resto en tres tramos hasta 14k."""
    t = CFG["trade"]
    # 4-nov: "Pon 0.2 sol". En SOL fijos no depende del precio de SOL.
    assert t["buy_amount"] == 0.2
    assert t["buy_amount_usd"] == 0
    assert t.get("fraccion_saldo", 0) == 0
    assert t["stop_loss_percentage"] == 0.25
    assert t["take_profit_percentage"] == 0.38
    # Lo vendido en el TP1 vale lo invertido +10%: 1,38 x fraccion = 1,10.
    assert abs(1.38 * t["take_profit_sell_percentage"] - 1.10) < 1e-9
    # Ningun otro peldano de porcentaje: los tramos de MC solo se arman
    # cuando TODOS los de porcentaje han saltado, y un tp2 lo retrasaria.
    for n in range(2, 7):
        assert f"tp{n}_gain" not in t and f"tp{n}_sell" not in t
    assert "tp_extra" not in t
    tramos = t["mc_take_profit_tiers"]
    assert [x["target_mc"] for x in tramos] == [8000, 11000, 14000]
    resto = 1 - t["take_profit_sell_percentage"]
    for x in tramos:
        assert abs(x["sell"] - resto / 3) < 1e-9
    # Entre el TP1 y los tres tramos se vende el 100%: no queda polvo.
    assert t["take_profit_sell_percentage"] + sum(x["sell"] for x in tramos) >= 1.0


def test_sin_tope_de_market_cap() -> None:
    """5-nov: "Elimina lo del mcap". Sin tope no se lee la curva para eso."""
    assert CFG["filters"]["max_entry_mc"] == 0

def test_mayhem_no_es_un_filtro_adicional() -> None:
    assert CFG["filters"]["allow_mayhem"] is True
    assert CFG["filters"]["require_mayhem"] is False


def test_ninguna_otra_estrategia_real_arranca_sola() -> None:
    """Solo opera esta (4-nov: "Solo quiero la de comprar bloque 2 con dev
    migrado 1%").

    El arranque de "todas" levanta cada YAML con enabled: true. Si alguno de
    esos esta en real, compraria junto a esta sobre la misma wallet. Esta se
    arranca con su propio comando y tambien lleva enabled: false, asi que el
    arranque de "todas" no debe levantar NINGUNA estrategia en real. Las de
    BSC no cuentan: son otro bot, con sus propias tres puertas.
    """
    encendidas_en_real = []
    for f in sorted((ROOT / "bots").glob("*.yaml")):
        if "bsc" in f.name:
            continue
        c = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if c.get("enabled", True) and (c.get("filters") or {}).get(
            "paper_trade"
        ) is False:
            encendidas_en_real.append(f.name)
    assert encendidas_en_real == [], (
        f"arrancarian en real junto a dev1-bloque2: {encendidas_en_real}"
    )


def test_el_stop_sale_rapido_como_en_el_bot_real_anterior() -> None:
    """4-nov, "Si": las reglas del stop de bot-unica123-real."""
    fees, t = CFG["priority_fees"], CFG["trade"]
    assert int(fees["fixed_amount_venta_proteccion"]) == 42_424_243
    # Si el tope fuera menor, recortaria la del stop y no serviria de nada.
    assert int(fees["hard_cap"]) >= int(fees["fixed_amount_venta_proteccion"])
    assert t["sell_slippage_proteccion"] == 0.50
    assert t["sell_slippage"] == 0.25


def test_ventana_de_edad_exacta_de_uno_a_dos_segundos() -> None:
    c96 = CFG["filters"]["curva_96"]
    assert c96["max_edad_seg"] == 2
    assert c96.get("min_edad_seg", 0) == 1


def test_ventana_de_edad_rechaza_fuera_de_uno_a_dos_segundos() -> None:
    from test_ventana_de_edad import _con_edad, _fuente

    c96 = CFG["filters"]["curva_96"]
    f = _fuente(min_edad_seg=c96.get("min_edad_seg", 0), max_edad_seg=c96["max_edad_seg"])
    _con_edad("nueva", 1.5)
    assert f._fuera_de_edad("nueva", "NUEVA") is False
    _con_edad("temprana", 0.5)
    assert f._fuera_de_edad("temprana", "TEMPRANA") is True
    _con_edad("pasada", 2.5)
    assert f._fuera_de_edad("pasada", "PASADA") is True
    assert f._fuera_de_edad("nunca-vista-nacer", "TARDE") is True


def test_la_edad_se_revalida_justo_antes_de_comprar() -> None:
    from types import SimpleNamespace

    from test_ventana_de_edad import _con_edad

    from trading.universal_trader import UniversalTrader

    motivos: list[str] = []
    yo = SimpleNamespace(
        curva_96=CFG["filters"]["curva_96"],
        _descarta=lambda _t, m: motivos.append(m),
    )
    comprobar = UniversalTrader._demasiado_vieja_para_comprar

    _con_edad("rapida", 1.4)
    assert comprobar(yo, SimpleNamespace(mint="rapida", symbol="R")) is False
    _con_edad("lenta", 2.6)
    assert comprobar(yo, SimpleNamespace(mint="lenta", symbol="L")) is True
    assert "ya tiene 2.6 s al ir a comprar (máx 2 s)" in motivos[-1]
    assert comprobar(yo, SimpleNamespace(mint="nunca-vista", symbol="N")) is True
    assert "edad desconocida" in motivos[-1]


def test_ni_marcados_ni_mediana_pueden_saltarse_el_uno_por_ciento() -> None:
    assert TRADER.count('self.pumpfun_dev.get("strict_only", False)') >= 3
    assert 'or not self._es_marcado(token_info)' in TRADER
    assert 'not self.pumpfun_dev.get("strict_only", False)' in TRADER
