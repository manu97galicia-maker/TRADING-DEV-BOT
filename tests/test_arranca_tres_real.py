"""deploy/arranca-tres-real.sh y las dos estrategias nuevas del 7-nov."""

# ruff: noqa: S101, PLR2004

import subprocess
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "deploy" / "arranca-tres-real.sh"
TEXTO = SCRIPT.read_text(encoding="utf-8")
NUEVAS = [
    "bots/bot-real-curva92.yaml",
    # 7-nov: "elimina lo de 6k y 15k ... una unica estrategia".
    "bots/bot-real-presion.yaml",
]


def _cfg(f: str) -> dict:
    return yaml.safe_load((RAIZ / f).read_text(encoding="utf-8"))


def test_sintaxis() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_arranca_las_tres_con_apagado_y_solo_en_real() -> None:
    # 7-nov: "dejamos solo las estrategias de presion compradora".
    lista = next(x for x in TEXTO.splitlines() if x.startswith("ESTRATEGIAS="))
    # Despues: "elimina lo de 6k y 15k ... una unica estrategia".
    # Despues: "hazme una estrategia de mayhem ... arriesgando 4$".
    assert lista == 'ESTRATEGIAS="bots/bot-real-presion.yaml bots/bot-real-mayhem.yaml"'
    assert "mayhem_real.log" in TEXTO
    assert "presion_real.log" in TEXTO
    # Y las que salen de la lista tambien se paran: pkill de todas.
    assert 'pkill -f "run_bot_process" ' in TEXTO
    assert 'timeout "$SEG"' in TEXTO
    assert 'grep -q "^  paper_trade: false" "$Y"' in TEXTO
    assert "dev1_real.log" in TEXTO  # el log de siempre no cambia
    assert "git pull" not in "\n".join(
        l for l in TEXTO.splitlines() if not l.lstrip().startswith("#")
    )


def test_reglas_comunes_a_las_tres() -> None:
    """15% del saldo libre, stop a compra +5% tras el TP1, -25%, venta a los
    5 min si va en rojo, para tras 3 perdidas, sin mayhem y con listas."""
    for f in ["bots/bot-paper-dev1-bloque2.yaml", *NUEVAS]:
        c = _cfg(f)
        t, fl = c["trade"], c["filters"]
        # 7-nov: "20% por operacion" en las de presion; el resto sigue en 15%.
        # Despues: "pon 30% por operacion".
        # Despues: "44% por operacion".
        pct = 0.44 if "presion" in f else 0.15
        assert t["fraccion_saldo"] == pct and t["fraccion_base"] == "libre", f
        assert (
            t["stop_a_entrada_tras_tp"] is True and t["margen_stop_tras_tp"] == 0.05
        ), f
        # 7-nov "stop ponlo al 18%" en las de presion; el resto sigue en 25%.
        # Despues: "+50%, +85%, +120% y stop -22".
        assert t["stop_loss_percentage"] == (0.22 if "presion" in f else 0.25), f
        assert t["max_perdidas_seguidas"] == 3, f
        assert t["venta_muerta_seg"] == 300 and t["venta_muerta_gain"] == 0.0, f
        # Despues, en la de presion: "pon el primer tp en 50%".
        assert t["take_profit_percentage"] == (0.50 if "presion" in f else 0.38), f
        assert fl["allow_mayhem"] is False and fl["ignorar_listas_de_devs"] is False, f
        assert fl["paper_trade"] is False, f
        # 7-nov: "pon sin limite de operaciones simultaneas".
        # Despues, en las de presion: "maximo 3 operaciones simultaneas".
        # Despues: "maximo 4 simultaneas y maximo de posiciones abiertas 4".
        # Despues: "maximo 3".
        assert t["max_posiciones"] == (3 if "presion" in f else 0), f
        assert t.get("max_posiciones_total", 0) == (3 if "presion" in f else 0), f


def test_curva92() -> None:
    c = _cfg(NUEVAS[0])
    cv, t = c["filters"]["curva_96"], c["trade"]
    assert c["enabled"] is False
    assert cv["enabled"] is True and cv["umbral"] == 0.92
    assert cv["max_subida_vela"] == 0.35 and cv["vela_seg"] == 1.0  # sin mecha de 1 s
    assert cv["max_bundle_pct"] == 0.5  # anti pump and dump
    assert c["filters"]["pumpfun_dev"]["enabled"] is False  # cualquier dev
    assert t["mc_take_profit_tiers"] == []
    assert [t["tp2_gain"], t["tp3_gain"], t["tp4_gain"]] == [0.60, 1.00, 1.50]
    total = (
        t["take_profit_sell_percentage"] + t["tp2_sell"] + t["tp3_sell"] + t["tp4_sell"]
    )
    assert total >= 1.0


def test_presion_unica() -> None:
    """7-nov: "cualquier moneda de pumpfun con esa presion, una unica
    estrategia" (la presion de la de 6k, elegida por Manu)."""
    c = _cfg(NUEVAS[1])
    cv, fl = c["filters"]["curva_96"], c["filters"]
    # 7-nov "elimina el filtro de market cap".
    assert fl["min_entry_mc"] == 0
    # 7-nov "sube un poco volumen y presion": 6 en 5 s, 2x, 1 SOL, 8 en total.
    # Despues, "un poco mas de volumen y presion": 7 / 2,2x / 1,2 SOL.
    # Despues: "bajar un poco la presion" -> 6 / 2x / 1 SOL.
    assert cv["min_compradores_ola"] == 6 and cv["min_presion_ratio"] == 2.0
    # Despues: "mas gente total" -> 20 compradores en total (antes 8).
    assert cv["min_compra_sol"] == 1.0 and cv["min_compradores"] == 20
    assert cv["min_volumen_total_sol"] == 30  # "mayor volumen"
    assert cv["min_mc_usd"] == 5600  # "minimo compras monedas de 5.6k"
    assert fl["pumpfun_dev"]["enabled"] is False
    _tres_ventas_hasta_75(c["trade"])


def _tres_ventas_hasta_75(t: dict) -> None:
    """7-nov "haz 3 ventas" y "en total busco un 75% roi"."""
    assert t["mc_take_profit_tiers"] == [] and "tp4_gain" not in t
    assert [t["take_profit_percentage"], t["tp2_gain"], t["tp3_gain"]] == [
        0.50,
        0.85,
        1.20,
    ]
    total = t["take_profit_sell_percentage"] + t["tp2_sell"] + t["tp3_sell"]
    assert abs(total - 1.0) < 1e-9


def test_presion_unica_mira_cualquier_moneda_sin_inundar() -> None:
    cv = _cfg(NUEVAS[1])["filters"]["curva_96"]
    assert cv["umbral"] == 0.0 and cv["tope"] == 0.995
    assert cv["revisar_cada_seg"] == 2.0 and cv["log_presion_cada_seg"] == 30.0
    assert not (RAIZ / "bots/bot-real-presion6k.yaml").exists()
    assert not (RAIZ / "bots/bot-real-presion15k.yaml").exists()


def test_presion_sin_bundles_de_creacion() -> None:
    """7-nov "no me compres este tipo" (LAGER: el bloque de creacion la sube
    de ~2,5k a ~27k y el dev vende todo a los 2 minutos)."""
    for f in NUEVAS[1:]:
        cv = _cfg(f)["filters"]["curva_96"]
        # Despues: "mas de 5 carteras ... mas de 9 SOL, no compra".
        assert cv["max_carteras_primer_bloque"] == 5, f
        assert cv["max_sol_primer_bloque"] == 9.0, f


def test_presion_prioridad_y_deslizamiento() -> None:
    """7-nov "prioridad de compra 0.007 y deslizamiento 18%"."""
    for f in NUEVAS[1:]:
        c = _cfg(f)
        # Despues: "reducir mis costes enormemente" -> 0,002 SOL de compra.
        assert c["priority_fees"]["fixed_amount_compra"] == 20_000_000, f
        assert c["trade"]["buy_slippage"] == 0.09, f  # despues: "slippage de compra 9%"
        assert c["trade"]["sell_slippage"] == 0.10, f  # despues: venta a 10%
