"""8-nov: "revisa cada poco que no queden pendientes saldos pequenos" ->
barrido cada 10 min. Lo que se cierra, lo que se quema y lo que NO se toca."""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN003, ANN201, ANN202, PLC0415

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from trading import barrido_restos as br

RAIZ = Path(__file__).resolve().parents[1]


def _c(mint, crudo=1_000_000):
    return br.Cuenta("ATA" + mint, mint, crudo, 6, "TP")


def test_decisiones():
    prot = {"ABIERTA"}
    assert br.decide(_c("VACIA", 0), prot, None, 1.0) == "cerrar"
    assert br.decide(_c("POLVO"), prot, 0.40, 1.0) == "quemar"
    assert br.decide(_c("VALE"), prot, 1.0, 1.0) == "dejar"  # 1 $ justo: no
    assert br.decide(_c("VALE"), prot, 25.0, 1.0) == "dejar"
    assert br.decide(_c("SIN_PRECIO"), prot, None, 1.0) == "dejar"
    # Posicion abierta de cualquier estrategia: nunca, ni vacia ni con polvo.
    assert br.decide(_c("ABIERTA"), prot, 0.01, 1.0) == "dejar"
    assert br.decide(_c("ABIERTA", 0), prot, None, 1.0) == "dejar"
    # El SOL envuelto jamas.
    assert br.decide(_c(br.WSOL), set(), 0.0, 1.0) == "dejar"
    assert br.decide(_c(br.WSOL, 0), set(), None, 1.0) == "dejar"


def test_protege_las_fichas_de_todas_las_estrategias(tmp_path):
    (tmp_path / "open_position_bot-real-devs27__MINTA.json").write_text(
        json.dumps({"mint": "MINTA"})
    )
    (tmp_path / "open_position_bot-paper-dev1-bloque2__MINTB.json").write_text("{")
    (tmp_path / "open_position_viejo.json").write_text(json.dumps({"mint": "MINTC"}))
    assert br.mints_protegidos(tmp_path) == {"MINTA", "MINTB", "MINTC"}


def test_lee_la_respuesta_del_nodo():
    r = {
        "result": {
            "value": [
                {
                    "pubkey": "ATA1",
                    "account": {
                        "data": {
                            "parsed": {
                                "info": {
                                    "mint": "M1",
                                    "tokenAmount": {"amount": "1500", "decimals": 6},
                                }
                            }
                        }
                    },
                },
                {"pubkey": "ROTA", "account": {}},
            ]
        }
    }
    assert br.cuentas_de_la_respuesta(r, "TP") == [
        br.Cuenta("ATA1", "M1", 1500, 6, "TP")
    ]
    assert br.cuentas_de_la_respuesta(None, "TP") == []


def test_una_pasada_del_trader(tmp_path):
    """Con el trader de verdad: cierra la vacia, quema el polvo y deja el resto."""
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey

    from trading import universal_trader as ut

    m = {k: str(Pubkey.new_unique()) for k in ("vacia", "polvo", "vale", "abierta")}
    (tmp_path / f"open_position_x__{m['abierta']}.json").write_text("{}")

    def _cuenta(mint, crudo):
        return {
            "pubkey": str(Pubkey.new_unique()),
            "account": {
                "data": {
                    "parsed": {
                        "info": {
                            "mint": mint,
                            "tokenAmount": {"amount": str(crudo), "decimals": 6},
                        }
                    }
                }
            },
        }

    respuestas = [
        {
            "result": {
                "value": [
                    _cuenta(m["vacia"], 0),
                    _cuenta(m["polvo"], 1_000_000),
                    _cuenta(m["vale"], 1_000_000),
                    _cuenta(m["abierta"], 5),
                ]
            }
        },
        {"result": {"value": []}},
    ]
    enviadas = []

    async def _rpc(_body):
        return respuestas.pop(0)

    async def _envia(ixs, _kp, **_k):
        enviadas.append(len(ixs))
        return "firma"

    async def _confirma(_sig):
        return True

    precios = {m["polvo"]: 0.5 / ut._SOL_USD, m["vale"]: 5.0 / ut._SOL_USD}

    async def _precio(mint):
        return precios.get(mint)

    t = object.__new__(ut.UniversalTrader)
    t.wallet = SimpleNamespace(pubkey=Keypair().pubkey(), keypair=Keypair())
    t.solana_client = SimpleNamespace(
        post_rpc=_rpc, build_and_send_transaction=_envia, confirm_transaction=_confirma
    )
    t._open_pos_path = tmp_path / "open_position_yo.json"
    t._precio_resto = _precio
    hechas = asyncio.run(t._barrer_restos(1.0))
    assert hechas == {"cerrar": 1, "quemar": 1}
    assert sorted(enviadas) == [1, 2]  # cerrar = 1 instruccion; quemar+cerrar = 2


def test_config():
    cfg = yaml.safe_load((RAIZ / "bots/bot-real-devs27.yaml").read_text("utf-8"))
    assert cfg["trade"]["barrido_restos"] == {"cada_seg": 600, "max_usd": 1.0}
    # Solo una estrategia barre, para que no se pisen.
    for f in ("bots/bot-real-presion512.yaml", "bots/bot-paper-dev1-bloque2.yaml"):
        c = yaml.safe_load((RAIZ / f).read_text("utf-8"))
        assert not c["trade"].get("barrido_restos"), f
    for f in (
        "bots/bot-real-devs27.yaml",
        "bots/bot-real-presion512.yaml",
        "bots/bot-paper-dev1-bloque2.yaml",
    ):
        t = yaml.safe_load((RAIZ / f).read_text("utf-8"))["trade"]
        assert t["fraccion_saldo"] == 0.60 and t["fraccion_base"] == "cartera", f
        assert t["max_posiciones"] == 2 and t["max_posiciones_total"] == 2, f
