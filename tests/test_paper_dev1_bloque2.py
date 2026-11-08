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
TRADER = (ROOT / "src" / "trading" / "universal_trader.py").read_text(encoding="utf-8")
PAPER_LAUNCHER = (ROOT / "deploy" / "arranca-dev1-paper.sh").read_text(encoding="utf-8")
SOL_USD_REFERENCIA = 105.0


def test_esta_en_papel_y_no_se_cuela_en_todas() -> None:
    assert CFG["filters"]["paper_trade"] is True
    assert "nunca firma ni mueve sol" in FICHERO.read_text(encoding="utf-8").lower()
    # enabled solo lo mira el arranque de "todas"; en false no arranca junto
    # a las demas estrategias sobre la misma wallet.
    assert CFG["enabled"] is False


def test_el_lanzador_solo_acepta_paper_y_la_regla_completa() -> None:
    for required in (
        "paper_trade: true",
        "min_edad_seg: 1",
        "max_edad_seg: 2",
        "min_migrations: 1",
        "min_rate: 0.01",
        "strict_only: true",
    ):
        assert required in PAPER_LAUNCHER
    assert "paper_trade: false" not in PAPER_LAUNCHER


def test_dev_con_minimo_un_uno_por_ciento_de_migradas() -> None:
    """6-nov por la noche: "pon solo un proyecto minimo migrado y 10% min"."""
    dev = CFG["filters"]["pumpfun_dev"]
    assert dev["enabled"] is True
    assert dev["min_rate"] == 0.10
    # 8-nov: "minimo 2 lanzamientos migrados y 10%".
    assert dev["min_migrations"] == 2
    # No admite rutas alternativas (marcados o mediana).
    assert dev["compra_inmediata"] is True
    assert dev["timeout_seg"] >= 9
    # 7-nov: en false para que la lista de 15k+ compre sin el 10%.
    assert dev["strict_only"] is False
    # 6-nov: "pero bloc dev rugs" -> vuelven la blacklist y el auto-baneo.
    assert CFG["filters"]["ignorar_listas_de_devs"] is False


def test_condiciones_de_entrada_del_6_nov() -> None:
    """Lo que se exige para comprar, tal y como lo dejo Manu el 6-nov."""
    f = CFG["filters"]
    # "Pide minimo 3 compradores iniciales y 350 volumen en el bloque que
    # compra primero" -> "Solo dos no 3 pero mismo volumen" -> "0.15 por
    # compra y volumen 200" -> "Pon 3 compradores y 500 volumen" -> "Quita
    # holders y volumen".
    assert f["min_first_block_buys"] == 0
    assert f["min_first_block_usd"] == 0
    assert f.get("min_titulares", 0) == 0
    assert f["max_first_block_buys"] == 0
    assert f["max_first_block_usd"] == 0
    assert f["buy_from_second_block"] is False
    assert f["second_block_confirmation"]["enabled"] is False
    # La ventana se queda en 8 s: sin minimos no espera, solo vuelve a
    # descartar lo que pase de 8 s al ir a comprar.
    assert f["max_buy_age"] == 8
    # "Quita el limite de compra" -> "De capitalizacion de mercado".
    assert f["max_entry_mc"] == 0
    assert f.get("min_entry_mc", 0) == 0
    # Sin mayhem, y "bloc dev rugs".
    assert f["allow_mayhem"] is False
    assert f["require_mayhem"] is False
    assert f["ignorar_listas_de_devs"] is False
    # "Maximo 8 segundos" desde el mint.
    assert f["curva_96"]["max_edad_seg"] == 8


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
    sol = microlamports * unidades / 1e6 / 1e9
    # 6-nov por la noche: "0.011 de prioridad" (antes 12 centimos, +0,003,
    # +0,005 y +0,005).
    # 8-nov: "buy priority fee de 0.005".
    assert abs(sol - 0.005) < 1e-9, f"la compra paga {sol:.6f} SOL"
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
    # 7-nov: "pon sin limite de operaciones simultaneas" (venia de 7). Manda
    # el saldo. El nodo sigue vigilando bien solo `vigilables` a la vez, y el
    # YAML lo tiene que decir para que no se olvide.
    # 8-nov: "maximo dos operaciones a la vez" (entre las tres).
    assert t["max_posiciones"] == 2 and t["max_posiciones_total"] == 2
    assert vigilables == 7
    assert "max_rps / 2" in FICHERO.read_text(encoding="utf-8")
    assert float(t["min_reserve_sol"]) > 0


def test_salida_del_4_nov() -> None:
    """TP 38% recupera lo invertido +10%; el resto en 5 tramos hasta 19k."""
    t = CFG["trade"]
    # 6-nov: "0.15 por compra", "0.1 compra" y por la noche "compre 0.25 sol".
    # En SOL fijos no depende del precio de SOL.
    assert t["buy_amount_usd"] == 0
    # 7-nov: "compra en todas 15% del balance" (venia del 35%). Sobre el
    # saldo libre.
    # 8-nov: "30% portfolio", sobre la cartera entera.
    assert t["fraccion_saldo"] == 0.60
    assert t["fraccion_base"] == "cartera"
    assert t["stop_loss_percentage"] == 0.25
    # 7-nov: para los devs normales, "take profit como teniamos" (+38%,
    # recupera lo invertido +10%).
    assert t["take_profit_percentage"] == 0.38
    assert abs(1.38 * t["take_profit_sell_percentage"] - 1.10) < 1e-9
    # Ningun otro peldano de porcentaje: los tramos de MC solo se arman
    # cuando TODOS los de porcentaje han saltado, y un tp2 lo retrasaria.
    for n in range(2, 7):
        assert f"tp{n}_gain" not in t and f"tp{n}_sell" not in t
    assert "tp_extra" not in t
    # 7-nov: los 5 tramos hasta 19k del 6-nov por la tarde.
    tramos = t["mc_take_profit_tiers"]
    assert [x["target_mc"] for x in tramos] == [7000, 9000, 12000, 15000, 19000]
    resto = 1 - t["take_profit_sell_percentage"]
    for x, parte in zip(tramos, [0.30, 0.30, 0.15, 0.15, 0.10], strict=True):
        assert abs(x["sell"] - resto * parte) < 1e-9
    # Entre el TP1 y los tramos se vende el 100%: no queda polvo.
    assert t["take_profit_sell_percentage"] + sum(x["sell"] for x in tramos) >= 1.0


def test_sin_tope_de_market_cap() -> None:
    """6-nov: "Quita el limite de compra" -> "De capitalizacion de mercado"."""
    assert CFG["filters"]["max_entry_mc"] == 0


def test_devs_con_rugs_bloqueados() -> None:
    """6-nov: "pero bloc dev rugs". Vuelven la blacklist y el auto-baneo."""
    assert CFG["filters"]["ignorar_listas_de_devs"] is False
    assert CFG["trade"].get("rugs_para_bloquear", 2) == 2


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
        if (
            c.get("enabled", True)
            and (c.get("filters") or {}).get("paper_trade") is False
        ):
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
    # 6-nov: "Pon buy slippage y sell 35%".
    assert t["sell_slippage"] == 0.35
    assert t["buy_slippage"] == 0.35


def test_edad_maxima_ocho_segundos_desde_el_mint() -> None:
    """6-nov: "Maximo 8 segundos"."""
    c96 = CFG["filters"]["curva_96"]
    assert c96["max_edad_seg"] == 8
    assert c96.get("min_edad_seg", 0) == 0


def test_ventana_de_edad_rechaza_pasados_ocho_segundos() -> None:
    from test_ventana_de_edad import _con_edad, _fuente

    c96 = CFG["filters"]["curva_96"]
    f = _fuente(
        min_edad_seg=c96.get("min_edad_seg", 0), max_edad_seg=c96["max_edad_seg"]
    )
    _con_edad("nueva", 0.5)
    assert f._fuera_de_edad("nueva", "NUEVA") is False
    _con_edad("justa", 7.5)
    assert f._fuera_de_edad("justa", "JUSTA") is False
    _con_edad("pasada", 8.5)
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

    _con_edad("rapida", 7.4)
    assert comprobar(yo, SimpleNamespace(mint="rapida", symbol="R")) is False
    _con_edad("lenta", 8.6)
    assert comprobar(yo, SimpleNamespace(mint="lenta", symbol="L")) is True
    assert "ya tiene 8.6 s al ir a comprar (máx 8 s)" in motivos[-1]
    assert comprobar(yo, SimpleNamespace(mint="nunca-vista", symbol="N")) is True
    assert "edad desconocida" in motivos[-1]


def test_ni_marcados_ni_mediana_pueden_saltarse_el_uno_por_ciento() -> None:
    assert TRADER.count('self.pumpfun_dev.get("strict_only", False)') >= 3
    assert "or not self._es_marcado(token_info)" in TRADER
    assert 'not self.pumpfun_dev.get("strict_only", False)' in TRADER


def test_vende_la_moneda_muerta_al_minuto() -> None:
    """6-nov: "vender si a los cinco minutos esta por debajo del precio de
    compra" (venia de 60 s)."""
    t = CFG["trade"]
    assert t["venta_muerta_seg"] == 300
    assert t["venta_muerta_gain"] == 0.0


def test_tras_el_tp1_el_stop_va_al_precio_de_compra() -> None:
    """7-nov: tras el TP1 el stop va a compra +5% ("mas algo de ganancias");
    y 3 perdidas seguidas paran la estrategia."""
    t = CFG["trade"]
    assert t["stop_a_entrada_tras_tp"] is True
    assert t["margen_stop_tras_tp"] == 0.05
    assert t["max_perdidas_seguidas"] == 3


def test_devs_buenos_compran_y_los_de_30k_salen_distinto() -> None:
    """7-nov: lista de 15k+ que compra sin el 10%; los de 30k+ recuperan al
    +45% y dejan correr el resto en 4 ventas (30k/45k/60k/80k)."""
    import json

    t, f = CFG["trade"], CFG["filters"]
    assert t["devs_marcados"] == "devs_buenos_15k.json"
    assert t["marcados_saltan_baneo"] is False
    raiz = FICHERO.parent.parent
    buenos = {
        x["wallet"]
        for x in json.loads((raiz / t["devs_marcados"]).read_text())["wallets"]
    }
    elite = {
        x["wallet"]
        for x in json.loads((raiz / f["dev_elite"]["lista"]).read_text())["wallets"]
    }
    assert elite <= buenos and len(elite) < len(buenos)
    e = f["dev_elite"]
    assert e["min_rate"] > 1  # nadie entra por su tasa, solo por la lista
    assert e["take_profit_percentage"] == 0.45
    assert abs(1.45 * e["take_profit_sell_percentage"] - 1.0) < 1e-9
    assert [x["target_mc"] for x in e["mc_take_profit_tiers"]] == [
        30000,
        45000,
        60000,
        80000,
    ]
    assert (
        e["take_profit_sell_percentage"]
        + sum(x["sell"] for x in e["mc_take_profit_tiers"])
        >= 1.0
    )
    assert e["stop_loss_percentage"] == t["stop_loss_percentage"]
