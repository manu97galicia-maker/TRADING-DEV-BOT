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
    "bots/bot-real-presion6k.yaml",
    "bots/bot-real-presion15k.yaml",
]


def _cfg(f: str) -> dict:
    return yaml.safe_load((RAIZ / f).read_text(encoding="utf-8"))


def test_sintaxis() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_arranca_las_tres_con_apagado_y_solo_en_real() -> None:
    for f in ["bots/bot-paper-dev1-bloque2.yaml", *NUEVAS]:
        assert f in TEXTO
    assert 'timeout "$SEG"' in TEXTO
    assert 'grep -q "^  paper_trade: false" "$Y"' in TEXTO
    assert "dev1_real.log" in TEXTO  # el log de siempre no cambia
    assert "git pull" not in "\n".join(l for l in TEXTO.splitlines() if not l.lstrip().startswith("#"))


def test_reglas_comunes_a_las_tres() -> None:
    """15% del saldo libre, stop a compra +5% tras el TP1, -25%, venta a los
    5 min si va en rojo, para tras 3 perdidas, sin mayhem y con listas."""
    for f in ["bots/bot-paper-dev1-bloque2.yaml", *NUEVAS]:
        c = _cfg(f)
        t, fl = c["trade"], c["filters"]
        assert t["fraccion_saldo"] == 0.15 and t["fraccion_base"] == "libre", f
        assert t["stop_a_entrada_tras_tp"] is True and t["margen_stop_tras_tp"] == 0.05, f
        assert t["stop_loss_percentage"] == 0.25, f
        assert t["max_perdidas_seguidas"] == 3, f
        assert t["venta_muerta_seg"] == 300 and t["venta_muerta_gain"] == 0.0, f
        assert t["take_profit_percentage"] == 0.38, f
        assert fl["allow_mayhem"] is False and fl["ignorar_listas_de_devs"] is False, f
        assert fl["paper_trade"] is False, f
        # 7-nov: "pon sin limite de operaciones simultaneas".
        assert t["max_posiciones"] == 0, f


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
    total = t["take_profit_sell_percentage"] + t["tp2_sell"] + t["tp3_sell"] + t["tp4_sell"]
    assert total >= 1.0


def test_presion6k() -> None:
    c = _cfg(NUEVAS[1])
    cv, fl = c["filters"]["curva_96"], c["filters"]
    assert fl["min_entry_mc"] == 6000 and fl["max_buy_age"] > 0  # sin ventana no se mira el MC
    # 7-nov "afloja un poco": 4 compradores en 5 s, 1,5x, 0,5 SOL, 8 en total.
    assert cv["min_compradores_ola"] == 4 and cv["min_presion_ratio"] == 1.5
    assert cv["min_compra_sol"] == 0.5 and cv["min_compradores"] == 8
    assert fl["pumpfun_dev"]["enabled"] is False
    assert [x["target_mc"] for x in c["trade"]["mc_take_profit_tiers"]] == [7000, 9000, 12000, 15000, 19000]


def test_presion15k() -> None:
    """7-nov: "presion compradora pero no tan estricta a partir de los 15.3k"."""
    c = _cfg(NUEVAS[2])
    cv, fl, t = c["filters"]["curva_96"], c["filters"], c["trade"]
    p6 = _cfg(NUEVAS[1])["filters"]["curva_96"]
    assert c["enabled"] is False
    assert fl["min_entry_mc"] == 15300 and fl["max_buy_age"] > 0
    assert fl["pumpfun_dev"]["enabled"] is False
    # Menos estricta que la de 6k en las tres medidas de presion.
    assert cv["min_compradores_ola"] < p6["min_compradores_ola"]
    assert cv["min_presion_ratio"] < p6["min_presion_ratio"]
    assert cv["min_compra_sol"] < p6["min_compra_sol"]
    assert cv["max_bundle_pct"] == 0.5 and cv["max_subida_vela"] == 0.35
    # Entrando a 15,3k los tramos de 7k-19k saltarian todos juntos: van por %.
    assert t["mc_take_profit_tiers"] == []
    assert [t["tp2_gain"], t["tp3_gain"], t["tp4_gain"]] == [0.60, 1.00, 1.50]
    assert "presion15k_real.log" in TEXTO
