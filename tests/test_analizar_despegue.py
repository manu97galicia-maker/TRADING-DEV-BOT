"""Guardianes de `deploy/analizar_despegue.py`.

El script contesta a Manu "que paso cuando estas monedas cruzaron los
$20.000". Todo lo que dice sale de dos piezas que, si estan mal, producen
numeros con pinta de correctos y completamente falsos:

1. El parseo del `TradeEvent` (de ahi salen las reservas virtuales).
2. El market cap que se calcula con esas reservas.

Por eso se construye un evento sintetico con las reservas EXACTAS de
nacimiento y se comprueba que el MC que sale son $4.194, el mismo numero que
da la matematica de la curva por otro camino. Si el parseo se desalinea un
solo byte, ese numero deja de cuadrar.

Tambien se fija la tolerancia a eventos TRUNCADOS: el `TradeEvent` ha ido
creciendo con los upgrades del programa, asi que una moneda antigua emite un
evento mas corto. Descartarlos enteros seria perder justo las operaciones
viejas que hacen falta para medir presion compradora.
"""

# ruff: noqa: S101, PLR2004, SLF001, PLC0415, N812, PLR0913

import struct
import sys
from pathlib import Path

import pytest
from solders.pubkey import Pubkey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))

import analizar_despegue as A

MINT = "9pTGUwAhG3CLAcu8rZKgu8cQaeAdQRmkbbG8hfzzpump"
USUARIO = "43w6CJkHjZpAjPU3LswTGwur5RvngKoTKakhfLBkpZHV"
TERCERO = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

#: Reservas virtuales de una moneda recien nacida: 30 SOL contra 1,073e9
#: tokens. Son las constantes de la bonding curve de pump.fun.
VSOL_NACIMIENTO = 30_000_000_000
VTOKEN_NACIMIENTO = 1_073_000_000_000_000
#: El MC que esas reservas dan con SOL a $150, por la matematica de la curva.
MC_NACIMIENTO_USD = 4194
PRECIO_SOL = 150.0

#: Longitud del evento hasta `virtual_token_reserves` incluido: es lo minimo
#: que el script necesita para calcular un market cap.
BYTES_HASTA_RESERVAS = 113


def _pk(s: str) -> bytes:
    return bytes(Pubkey.from_string(s))


def _evento(
    *,
    vsol: int = VSOL_NACIMIENTO,
    vtoken: int = VTOKEN_NACIMIENTO,
    sol_amount: int = 500_000_000,
    is_buy: bool = True,
    usuario: str = USUARIO,
    marca: int = 1_760_000_000,
    mayhem: bool = True,
) -> bytes:
    """Construye un TradeEvent completo, campo a campo segun el IDL."""
    c = _pk(MINT)
    c += struct.pack("<Q", sol_amount)
    c += struct.pack("<Q", 1_234_567_000)
    c += bytes([1 if is_buy else 0])
    c += _pk(usuario)
    c += struct.pack("<q", marca)
    c += struct.pack("<Q", vsol)
    c += struct.pack("<Q", vtoken)
    c += struct.pack("<Q", 0)
    c += struct.pack("<Q", 0)
    c += _pk(TERCERO)
    c += struct.pack("<Q", 100)
    c += struct.pack("<Q", 5_000_000)
    c += _pk(TERCERO)
    c += struct.pack("<Q", 50)
    c += struct.pack("<Q", 2_500_000)
    c += bytes([1])
    c += struct.pack("<Q", 0)
    c += struct.pack("<Q", 0)
    c += struct.pack("<Q", 500_000_000)
    c += struct.pack("<q", marca)
    c += struct.pack("<I", 3) + b"buy"
    c += bytes([1 if mayhem else 0])
    c += struct.pack("<Q", 0)
    c += struct.pack("<Q", 0)
    return A.DISCRIMINADOR_TRADE + c


# ------------------------------------------------------------------- parseo
def test_el_discriminador_es_el_de_anchor() -> None:
    import hashlib

    assert A.DISCRIMINADOR_TRADE == hashlib.sha256(b"event:TradeEvent").digest()[:8]
    assert A.DISCRIMINADOR_TRADE.hex() == "bddb7fd34ee661ee"


def test_parsea_todos_los_campos_de_un_evento_completo() -> None:
    ev = A.parsea_trade(_evento())
    assert ev is not None
    assert ev["mint"] == MINT
    assert ev["sol_amount"] == 500_000_000
    assert ev["is_buy"] is True
    assert ev["user"] == USUARIO
    assert ev["timestamp"] == 1_760_000_000
    assert ev["virtual_sol_reserves"] == VSOL_NACIMIENTO
    assert ev["virtual_token_reserves"] == VTOKEN_NACIMIENTO
    assert ev["creator"] == TERCERO
    assert ev["ix_name"] == "buy"
    assert ev["mayhem_mode"] is True


def test_el_orden_de_los_campos_es_el_del_idl() -> None:
    """Si el IDL cambia el orden, el parseo se desalinea en silencio."""
    import json

    idl = json.loads(Path("idl/pump_fun_idl.json").read_text(encoding="utf-8"))
    campos = None
    for e in idl.get("events", []):
        if e["name"] == "TradeEvent":
            campos = e.get("fields")
    if campos is None:
        for t in idl.get("types", []):
            if t["name"] == "TradeEvent":
                campos = t["type"]["fields"]
    assert campos, "no se encontro TradeEvent en el IDL"
    assert [c["name"] for c in campos] == [n for n, _ in A._CAMPOS]


def test_descarta_lo_que_no_es_un_trade_event() -> None:
    cuerpo = _evento()[8:]
    assert A.parsea_trade(b"\x00" * 8 + cuerpo) is None
    assert A.parsea_trade(b"") is None


def test_descarta_un_evento_con_reservas_a_cero() -> None:
    """Dividir por `virtual_token_reserves` a cero reventaria el MC."""
    assert A.parsea_trade(_evento(vtoken=0)) is None


def test_tolera_eventos_truncados_de_monedas_viejas() -> None:
    """El TradeEvent crecio con los upgrades: los viejos son mas cortos.

    Tienen que seguir dando market cap, con los campos nuevos en None en vez
    de perderse la operacion entera.
    """
    corto = A.DISCRIMINADOR_TRADE + _evento()[8:][:BYTES_HASTA_RESERVAS]
    ev = A.parsea_trade(corto)
    assert ev is not None
    assert ev["mint"] == MINT
    assert ev["virtual_token_reserves"] == VTOKEN_NACIMIENTO
    assert ev["mayhem_mode"] is None
    assert A.mc_usd(ev, PRECIO_SOL) == pytest.approx(MC_NACIMIENTO_USD, abs=5)


# ------------------------------------------------------------- market cap
def test_el_market_cap_cuadra_con_la_matematica_de_la_curva() -> None:
    """Prueba cruzada: reservas -> MC debe dar lo mismo que k/vt**2.

    Es el guardian principal. Un desalineamiento de un byte en el parseo
    cambia las reservas y este numero deja de cuadrar.
    """
    ev = A.parsea_trade(_evento())
    por_reservas = A.mc_usd(ev, PRECIO_SOL)

    k = 30 * 1073e6
    vt = 1073e6
    por_la_curva = (k / vt**2) * 1e9 * PRECIO_SOL

    assert por_reservas == pytest.approx(por_la_curva, rel=1e-9)
    assert por_reservas == pytest.approx(MC_NACIMIENTO_USD, abs=5)


def test_el_market_cap_escala_con_el_precio_de_sol() -> None:
    ev = A.parsea_trade(_evento())
    assert A.mc_usd(ev, 300.0) == pytest.approx(A.mc_usd(ev, 150.0) * 2)


# --------------------------------------------------------------- presion
def _ops(filas: list[tuple[int, bool, float]]) -> list[dict]:
    """(marca, es_compra, sol) -> operaciones como las que usa el script."""
    return [
        {
            "timestamp": t,
            "is_buy": compra,
            "sol_amount": int(sol * 10**9),
            "user": f"cartera{i}",
            "virtual_sol_reserves": VSOL_NACIMIENTO,
            "virtual_token_reserves": VTOKEN_NACIMIENTO,
            "mayhem_mode": None,
            "creator": TERCERO,
        }
        for i, (t, compra, sol) in enumerate(filas)
    ]


def test_la_presion_es_sol_comprado_entre_sol_vendido() -> None:
    ops = _ops([(100, True, 3.0), (100, False, 1.0), (100, True, 1.0)])
    v = A.ventana(ops, 2, 3)
    assert v["comprado_sol"] == pytest.approx(4.0)
    assert v["vendido_sol"] == pytest.approx(1.0)
    assert v["ratio"] == pytest.approx(4.0)
    assert v["compradores"] == 2
    assert v["mayor_compra_sol"] == pytest.approx(3.0)


def test_la_ventana_solo_mira_hacia_atras_y_respeta_los_segundos() -> None:
    """Una compra de hace 40s no debe contar en la ventana de 3s.

    Si contara, la presion saldria inflada por el estallido inicial y el
    filtro dejaria pasar monedas que ya estan paradas.
    """
    ops = _ops([(0, True, 100.0), (100, True, 1.0), (100, False, 1.0)])
    v3 = A.ventana(ops, 2, 3)
    assert v3["comprado_sol"] == pytest.approx(1.0), "colo la compra vieja"
    v200 = A.ventana(ops, 2, 200)
    assert v200["comprado_sol"] == pytest.approx(101.0)


def test_sin_ventas_la_presion_es_infinita_y_sin_nada_es_cero() -> None:
    """Dos silencios distintos que no se pueden confundir.

    Solo compras = presion inmejorable. Ninguna operacion = sin dato. Si las
    dos dieran 0, una moneda que solo sube se descartaria por "sin presion".
    """
    solo_compras = _ops([(100, True, 2.0)])
    assert A.ventana(solo_compras, 0, 3)["ratio"] == float("inf")

    nada = _ops([(0, True, 2.0), (100, False, 0.0)])
    v = A.ventana(nada, 1, 3)
    assert v["comprado_sol"] == 0
    assert v["vendido_sol"] == 0
    assert v["ratio"] == 0.0


# ---------------------------------------------------------------- caidas
def test_la_peor_caida_bajo_el_cruce_es_la_que_dispara_un_stop() -> None:
    """Es el numero que contesta "me habria sacado el stop antes del x10"."""
    mcs = [20_000, 15_000, 40_000, 30_000, 200_000]
    c = A._caidas(mcs, 0, 4)
    assert c["peor_caida_bajo_el_cruce_pct"] == pytest.approx(-25.0)
    # desde el maximo de 40.000 bajo a 30.000 -> -25%
    assert c["peor_caida_desde_maximo_pct"] == pytest.approx(-25.0)


def test_sin_caida_los_dos_numeros_son_cero() -> None:
    c = A._caidas([20_000, 30_000, 50_000], 0, 2)
    assert c["peor_caida_bajo_el_cruce_pct"] == pytest.approx(0.0)
    assert c["peor_caida_desde_maximo_pct"] == pytest.approx(0.0)


# --------------------------------------------------------------- analisis
def test_detecta_el_cruce_y_lo_que_vino_despues() -> None:
    ops = _ops([(0, True, 1.0), (10, True, 1.0), (20, True, 1.0), (30, True, 1.0)])
    # MC creciente a mano: 5k, 19k, 21k, 65k
    for op, vsol in zip(
        ops,
        [
            VSOL_NACIMIENTO,
            int(VSOL_NACIMIENTO * 19_000 / 4194),
            int(VSOL_NACIMIENTO * 21_000 / 4194),
            int(VSOL_NACIMIENTO * 65_000 / 4194),
        ],
        strict=True,
    ):
        op["virtual_sol_reserves"] = vsol

    r = A.analiza(MINT, ops, 20_000, PRECIO_SOL)
    assert r["cruzo_objetivo"] is True
    assert r["cruce"]["mc"] == pytest.approx(21_000, rel=0.01)
    assert r["cruce"]["segundos_desde_primera_op"] == 20
    assert r["cruce"]["ops_antes"] == 2
    assert r["segundos_hasta_x2"] == 10  # 21k -> 65k es mas de x2, en la op de t=30
    assert r["segundos_hasta_x5"] is None


def test_avisa_cuando_no_se_vio_el_nacimiento() -> None:
    """Si el nodo no guarda historico, las ventanas estan incompletas y el
    informe NO debe presentarlas como si lo hubiera visto todo."""
    ops = _ops([(0, True, 1.0)])
    ops[0]["virtual_sol_reserves"] = int(VSOL_NACIMIENTO * 30_000 / 4194)
    r = A.analiza(MINT, ops, 20_000, PRECIO_SOL)
    assert r["vio_el_nacimiento"] is False

    jovenes = _ops([(0, True, 1.0)])
    assert A.analiza(MINT, jovenes, 20_000, PRECIO_SOL)["vio_el_nacimiento"] is True


def test_detecta_la_migracion_con_el_mc_de_la_curva() -> None:
    """Si migro, lo de despues no esta en estos datos y hay que decirlo."""
    ops = _ops([(0, True, 1.0), (10, True, 1.0)])
    ops[1]["virtual_sol_reserves"] = int(
        VSOL_NACIMIENTO * (A.MC_SOL_AL_MIGRAR * PRECIO_SOL) / 4194
    )
    r = A.analiza(MINT, ops, 20_000, PRECIO_SOL)
    assert r["migro"] is True
    assert r["mc_al_migrar"] == pytest.approx(A.MC_SOL_AL_MIGRAR * PRECIO_SOL)

    cortas = _ops([(0, True, 1.0)])
    assert A.analiza(MINT, cortas, 20_000, PRECIO_SOL)["migro"] is False


def test_sin_operaciones_no_revienta() -> None:
    assert "error" in A.analiza(MINT, [], 20_000, PRECIO_SOL)


# --------------------------------------------------------------------- PDA
def test_la_pda_de_la_curva_es_la_del_programa() -> None:
    """Si esta mal, el script no encuentra NINGUNA operacion y dice
    "sin firmas", que parece un problema del nodo y no lo es."""
    for mint in (
        MINT,
        "HUDmsj3BJ6EkbRBWpi2j8tzRLbv4xhho1x9AGw2Ypump",
        "8EZDxabmGJxSmefanMBQXqUabCEEPvbjVxQ3bw52pump",
    ):
        esperada, _ = Pubkey.find_program_address(
            [b"bonding-curve", bytes(Pubkey.from_string(mint))], A.PROGRAMA_PUMP
        )
        assert A.pda_curva(mint) == str(esperada)
