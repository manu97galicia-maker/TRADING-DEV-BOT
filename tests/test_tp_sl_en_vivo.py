"""El TP y el SL de las cuatro estrategias en real, pasados por el vigilante DE VERDAD.

Pedido el 7-nov: "asegurate que el sl y tp funcionan". Cada estrategia se
construye igual que en el droplet (bot_runner.start_bot con su YAML) y se le
pasa una posicion por `_monitor_tiered_exit` con precios inventados. Lo unico
falso es el precio, el reloj y la venta (que se apunta en vez de enviarse).
"""

# ruff: noqa: S101, PLR2004, SLF001, ANN001, ANN002, ANN003, ANN201, ANN202, PLC0415, PLR0913

import asyncio
from pathlib import Path
from types import SimpleNamespace

import base58
import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

RAIZ = Path(__file__).resolve().parents[1]
ESTRATEGIAS = [
    "bots/bot-paper-dev1-bloque2.yaml",
    "bots/bot-real-curva92.yaml",
    "bots/bot-real-presion.yaml",
]
#: Market cap de entrada de la prueba (en $), para los tramos por market cap.
MC_ENTRADA = 4000.0


def _construir(yaml_rel: str, tmp: Path, monkeypatch):
    """El trader tal cual lo monta el droplet, sin arrancarlo."""
    import bot_runner
    from trading import universal_trader as ut

    for f in ("devs_buenos_15k.json", "devs_30k.json", "blacklist_devs.json"):
        if not (tmp / f).exists():
            (tmp / f).symlink_to(RAIZ / f)
    monkeypatch.chdir(tmp)
    monkeypatch.setenv("SOLANA_NODE_RPC_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("SOLANA_NODE_WSS_ENDPOINT", "ws://127.0.0.1:9")
    monkeypatch.setenv(
        "SOLANA_PRIVATE_KEY", base58.b58encode(bytes(Keypair())).decode()
    )
    cap = {}

    async def _sin_arrancar(self):
        cap["t"] = self

    monkeypatch.setattr(ut.UniversalTrader, "start", _sin_arrancar)
    asyncio.run(bot_runner.start_bot(str(RAIZ / yaml_rel)))
    return cap["t"], ut


def _recorrer(t, ut, monkeypatch, multiplicadores, *, dev=None, max_s=30):
    """Pasa una posicion de 0,1 SOL por el vigilante. Devuelve las ventas
    como (motivo, fraccion de la posicion, precio / entrada)."""
    from interfaces.core import Platform

    entrada = MC_ENTRADA / (1e9 * ut._SOL_USD)
    reloj = [1000.0]
    monkeypatch.setattr(ut, "monotonic", lambda: reloj[0])
    camino = list(multiplicadores)
    ventas = []

    async def _precio(*_a):
        reloj[0] += 1.0
        return entrada * (camino.pop(0) if len(camino) > 1 else camino[0])

    async def _vende(_ti, cantidad, precio, motivo, *_a, **_k):
        ventas.append(
            (motivo, round(cantidad * entrada / 0.1, 4), round(precio / entrada, 3))
        )
        t._ultimo_cobro = cantidad * precio
        return True

    async def _nada(*_a, **_k):
        return None

    t._precio_rapido = _precio
    t._sell_amount = _vende
    t._precio_dex = _nada
    t.price_check_interval = 0
    usuario = dev or Keypair().pubkey()
    ficha = SimpleNamespace(
        mint=Pubkey.new_unique(),
        symbol="PRUEBA",
        bonding_curve=Pubkey.new_unique(),
        associated_bonding_curve=Pubkey.new_unique(),
        user=usuario,
        creator=usuario,
        platform=Platform.PUMP_FUN,
        additional_data={},
        is_mayhem_mode=False,
        is_cashback_coin=False,
        token_program_id=None,
        creator_vault=None,
    )
    compra = SimpleNamespace(
        price=entrada, amount=0.1 / entrada, success=True, tx_signature="x"
    )
    try:
        asyncio.run(asyncio.wait_for(t._monitor_tiered_exit(ficha, compra), max_s))
    except TimeoutError:
        pass
    return ventas, reloj[0] - 1000.0


@pytest.fixture(params=ESTRATEGIAS)
def bot(request, tmp_path, monkeypatch):
    t, ut = _construir(request.param, tmp_path, monkeypatch)
    return request.param, t, ut


def test_un_rug_se_vende_entero_en_el_stop(bot, monkeypatch):
    _, t, ut = bot
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 0.9, 0.7])
    assert ventas == [("stop_loss", 1.0, 0.7)]


def test_por_encima_del_stop_no_vende(bot, monkeypatch):
    """Justo por encima del stop aguanta; solo vende cuando lo cruza.
    Presion: -18% (7-nov); dev1 y curva92: -25%."""
    yaml_rel, t, ut = bot
    import yaml

    stop = yaml.safe_load((RAIZ / yaml_rel).read_text())["trade"][
        "stop_loss_percentage"
    ]
    aguanta, cae = round(1 - stop + 0.03, 2), round(1 - stop - 0.01, 2)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, aguanta, aguanta, cae])
    assert ventas == [("stop_loss", 1.0, cae)]


@pytest.mark.parametrize("yaml_rel", ["bots/bot-real-presion.yaml"])
def test_presion_stop_al_18(yaml_rel, tmp_path, monkeypatch):
    """7-nov "stop ponlo al 18%": a -16% aguanta, a -19% vende todo."""
    t, ut = _construir(yaml_rel, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 0.84, 0.84, 0.81])
    assert ventas == [("stop_loss", 1.0, 0.81)]


def test_tp1_cobra_y_el_stop_sube_a_compra_mas_5(bot, monkeypatch):
    """TP1 +38% vende el 79,71%; el resto sale en lo que llegue antes: compra
    +5% o el trailing (pico menos el % del stop). Con stop -25% (dev1, curva92)
    manda el +5%: a 1,06 aguanta y vende a 1,04. Con -18% (presion, 7-nov)
    manda el trailing: pico 1,4 x 0,82 = 1,148, asi que vende a 1,06."""
    yaml_rel, t, ut = bot
    if "presion" in yaml_rel:
        # 7-nov "pon el primer tp en 50%": TP1 a 1,5; trailing 1,5 x 0,82 = 1,23.
        ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.2, 1.4, 1.5, 1.25, 1.22])
        assert ventas[0] == ("tp_tier1_+50%", 0.7971, 1.5)
        assert len(ventas) == 2 and ventas[1][2] == 1.22
        assert abs(ventas[1][1] - 0.2029) < 1e-3
        return
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.2, 1.4, 1.2, 1.06, 1.06, 1.04])
    assert ventas[0] == ("tp_tier1_+38%", 0.7971, 1.4)
    assert len(ventas) == 2
    motivo, parte, precio = ventas[1]
    assert precio == 1.04
    assert abs(parte - 0.2029) < 1e-3


@pytest.mark.parametrize("yaml_rel", ["bots/bot-real-presion.yaml"])
def test_presion_trailing_al_18_antes_del_tp1(yaml_rel, tmp_path, monkeypatch):
    """Sube a +30% sin tocar el TP1 y cae un 18% desde ahi (1,3 x 0,82 =
    1,066): vende todo en verde, por trailing, sin esperar al stop."""
    t, ut = _construir(yaml_rel, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.3, 1.07, 1.06])
    assert ventas == [("trailing_stop", 1.0, 1.06)]


def test_la_moneda_muerta_se_vende_a_los_5_minutos(bot, monkeypatch):
    _, t, ut = bot
    ventas, segundos = _recorrer(t, ut, monkeypatch, [1.0, 0.97], max_s=30)
    assert len(ventas) == 1
    assert ventas[0][1] == 1.0 and "muerta" in ventas[0][0]
    assert 299 <= segundos <= 302


def test_en_verde_a_los_5_minutos_no_se_vende(bot, monkeypatch):
    """+5% a los 5 minutos: viva, se deja correr (solo vende si esta en rojo)."""
    _, t, ut = bot
    ventas, segundos = _recorrer(
        t, ut, monkeypatch, [1.0] + [1.05] * 400 + [0.7], max_s=30
    )
    assert ventas == [("stop_loss", 1.0, 0.7)]
    assert segundos > 400


@pytest.mark.parametrize("yaml_rel", ["bots/bot-real-presion.yaml"])
def test_presion_tres_ventas_hasta_el_75(yaml_rel, tmp_path, monkeypatch):
    """7-nov "haz 3 ventas" y "busco un 75% de roi": +38% / +55% / +75% y
    no hay mas ventas aunque siga subiendo."""
    t, ut = _construir(yaml_rel, tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.4, 1.51, 1.56, 1.76, 2.5, 4.0])
    assert [v[0] for v in ventas] == ["tp_tier1_+50%", "tp_tier2_+55%", "tp_tier3_+75%"]
    assert abs(sum(v[1] for v in ventas) - 1.0) < 1e-3


def test_tramos_por_porcentaje_venden_todo(tmp_path, monkeypatch):
    t, ut = _construir("bots/bot-real-curva92.yaml", tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.4, 1.7, 2.1, 2.6])
    assert [v[0] for v in ventas] == [
        "tp_tier1_+38%",
        "tp_tier2_+60%",
        "tp_tier3_+100%",
        "tp_tier4_+150%",
    ]
    assert abs(sum(v[1] for v in ventas) - 1.0) < 1e-3


def test_tramos_por_market_cap_venden_todo(tmp_path, monkeypatch):
    """dev1, entrada a 4k: TP1 a 5,5k y los tramos a 7k/9k/12k/15k/19k."""
    t, ut = _construir("bots/bot-paper-dev1-bloque2.yaml", tmp_path, monkeypatch)
    ventas, _ = _recorrer(t, ut, monkeypatch, [1.0, 1.4, 1.8, 2.3, 3.1, 3.8, 4.8])
    assert ventas[0][0] == "tp_tier1_+38%"
    assert len(ventas) == 6
    assert abs(sum(v[1] for v in ventas) - 1.0) < 1e-3


def test_dev_de_30k_recupera_al_45_y_tramos_hasta_80k(tmp_path, monkeypatch):
    import json

    t, ut = _construir("bots/bot-paper-dev1-bloque2.yaml", tmp_path, monkeypatch)
    dev = Pubkey.from_string(
        json.loads((RAIZ / "devs_30k.json").read_text())["wallets"][0]["wallet"]
    )
    # 4k de entrada: TP1 a +45% (5,8k); 30k=x7,5 45k=x11,25 60k=x15 80k=x20.
    ventas, _ = _recorrer(
        t, ut, monkeypatch, [1.0, 1.4, 1.46, 7.6, 11.3, 15.1, 20.1], dev=dev
    )
    assert ventas[0] == ("tp_tier1_+45%", 0.6897, 1.46)
    assert len(ventas) == 5
    assert abs(sum(v[1] for v in ventas) - 1.0) < 1e-3
