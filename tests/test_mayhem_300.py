"""deploy/mayhem_300.py: la cuenta de ganadoras y senales, con operaciones inventadas."""

# ruff: noqa: S101, PLR2004, PLR0913, ANN001, ANN201, ANN202, FBT002, C420

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import mayhem_300 as m3

VTOK0 = 1_073_000_000 * 10**6
K = 30 * 10**9 * VTOK0
DEV, AGENTE = "Dev1111", "Agente1"


def op(t, user, sol, compra=True, slot=None, vtok=VTOK0):
    vsol = K // vtok
    return {
        "timestamp": t,
        "_slot": slot if slot is not None else 100 + t,
        "user": user,
        "creator": DEV,
        "is_buy": compra,
        "sol_amount": int(sol * 1e9),
        "token_amount": 10**12,
        "virtual_sol_reserves": vsol,
        "virtual_token_reserves": vtok,
        "real_token_reserves": 1,
        "mayhem_mode": True,
        "cashback_fee_basis_points": 0,
    }


def _vtok_para(mult):
    # MC proporcional a 1/vtok^2: para multiplicar el MC por `mult`.
    return int(VTOK0 / mult**0.5)


def moneda(mult_final):
    return [
        op(0, DEV, 1.0, slot=100),
        op(0, "b1", 0.5, slot=100),
        op(2, AGENTE, 3.0),
        op(4, "c1", 0.2, vtok=_vtok_para(1.2)),
        op(6, "c2", 0.3, vtok=_vtok_para(1.3)),
        op(8, "c1", 0.1, compra=False, vtok=_vtok_para(1.25)),
        op(20, "c3", 0.5, vtok=_vtok_para(1.25 * mult_final)),
        op(30, "c4", 0.1, compra=False, vtok=_vtok_para(1.25 * mult_final * 0.9)),
    ]


def test_ganadora_se_mide_desde_el_momento_de_decision():
    f = m3.senales(moneda(5.0), 10, {AGENTE})
    assert abs(f["mult_desde_decision"] - 5.0) < 0.05
    assert f["seg_hasta_pico"] == 20
    g = m3.senales(moneda(2.0), 10, {AGENTE})
    assert abs(g["mult_desde_decision"] - 2.0) < 0.05


def test_senales_solo_con_lo_de_antes_y_sin_dev_ni_agente():
    f = m3.senales(moneda(5.0), 10, {AGENTE})
    assert f["dev_compra_sol"] == 1.0
    assert f["bloque1_compradores"] == 1 and f["bloque1_sol"] == 0.5
    # b1, c1, c2 compran; el agente y el dev no cuentan; c3 llega tarde.
    assert f["compradores"] == 3 and f["vendedores"] == 1
    assert f["sol_comprado"] == 1.0 and f["ratio_compra_venta"] == 10.0
    assert abs(f["subida_hasta_decision"] - 1.25) < 0.01
    assert f["dev_ya_vendio"] is False


def test_sin_nada_despues_de_la_decision_no_cuenta():
    assert m3.senales(moneda(5.0)[:6], 10, set()) is None
    assert m3.senales([], 10, set()) is None


def test_dev_que_vende_antes_del_pico():
    ops = moneda(5.0)
    ops.insert(6, op(15, DEV, 0.5, compra=False, vtok=_vtok_para(1.2)))
    f = m3.senales(ops, 10, {AGENTE})
    assert f["dev_vende_antes_del_pico"] is True and f["dev_ya_vendio"] is False


def test_el_agente_que_esta_en_todas_se_aparta():
    por = {
        f"m{i}": [op(0, DEV, 1), op(1, AGENTE, 1), op(2, f"u{i}", 1)] for i in range(12)
    }
    assert m3.carteras_comunes(por) == {AGENTE, DEV}
    assert m3.carteras_comunes(dict(list(por.items())[:5])) == set()


def test_compara_encuentra_la_senal_que_separa():
    filas = []
    for i in range(20):
        gana = i < 6
        filas.append(
            {
                "mult_desde_decision": 5.0 if gana else 1.5,
                **{s: 0 for s in m3.SENALES},
                "dev_ya_vendio": False,
                "cashback": False,
                "compradores": 20 + i if gana else i % 5,
            }
        )
    c = {x["senal"]: x for x in m3.compara(filas, 4.0)}
    assert c["compradores"]["mediana_ganadoras"] > c["compradores"]["mediana_resto"]
    assert c["compradores"]["mejor_corte"]["ventaja"] > 50
