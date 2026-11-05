"""El filtro de migradas mira a QUIEN CREO la moneda, no a quien cobra (5-nov).

Pedido: "Arreglarlo". 23 de 23 devs salian rechazados mientras en pump.fun
habia monedas de devs con migradas. Las monedas que entran por la curva
traian como dev el `creator` de la BondingCurve, que pump.fun puede cambiar a
la cuenta de reparto de comisiones (PDA "sharing-config"): una direccion que
no ha creado nada, asi que pump.fun contestaba 0 lanzadas y se descartaba.
"""

from types import SimpleNamespace
from unittest.mock import patch

from solders.pubkey import Pubkey

from monitoring import volumen
from trading.pump_fee_identity import sharing_config_address
from trading.token_builder import poner_dev_de_la_creacion
from trading.universal_trader import identidad_del_dev

MINT = str(Pubkey.new_unique())
DEV = str(Pubkey.new_unique())
REPARTO = sharing_config_address(MINT)


def _ficha(user: str, creator: str) -> SimpleNamespace:
    vault = Pubkey.new_unique()
    return SimpleNamespace(
        mint=Pubkey.from_string(MINT),
        user=Pubkey.from_string(user),
        creator=Pubkey.from_string(creator),
        creator_vault=vault,
    )


def setup_function() -> None:
    volumen._olvidar(MINT)


def test_al_nacer_se_apunta_quien_firmo_la_creacion() -> None:
    volumen.registrar_bloque_inicial(MINT, 123, dev=DEV)
    assert volumen.dev_de_la_creacion(MINT) == DEV


def test_se_apunta_aunque_no_haya_slot() -> None:
    volumen.registrar_bloque_inicial(MINT, None, dev=DEV)
    assert volumen.dev_de_la_creacion(MINT) == DEV


def test_olvidar_la_moneda_borra_tambien_su_dev() -> None:
    volumen.registrar_bloque_inicial(MINT, 1, dev=DEV)
    volumen._olvidar(MINT)
    assert volumen.dev_de_la_creacion(MINT) is None


def test_la_memoria_tiene_tope() -> None:
    with patch.object(volumen, "_MAX_DEV_CREACION", 3):
        otros = [str(Pubkey.new_unique()) for _ in range(5)]
        for m in otros:
            volumen.registrar_bloque_inicial(m, 1, dev=DEV)
        assert len([m for m in otros if volumen.dev_de_la_creacion(m)]) == 3
        assert volumen.dev_de_la_creacion(otros[0]) is None
        for m in otros:
            volumen._olvidar(m)


def test_la_averia_la_curva_apunta_a_la_cuenta_de_reparto() -> None:
    """EL CASO REAL: la curva dice que el creator es la cuenta de reparto."""
    ficha = _ficha(user=REPARTO, creator=REPARTO)
    vault_antes, creator_antes = ficha.creator_vault, ficha.creator
    volumen.registrar_bloque_inicial(MINT, 1, dev=DEV)
    poner_dev_de_la_creacion(ficha)
    # Al filtro le llega el dev de verdad...
    assert identidad_del_dev(ficha) == DEV
    # ...y la compra sigue pagando la comision a quien dice la curva.
    assert ficha.creator == creator_antes
    assert ficha.creator_vault == vault_antes


def test_sin_ver_nacer_y_con_la_cuenta_de_reparto_el_dev_es_desconocido() -> None:
    ficha = _ficha(user=REPARTO, creator=REPARTO)
    poner_dev_de_la_creacion(ficha)  # no se registro nada
    assert identidad_del_dev(ficha) is None


def test_moneda_normal_sin_reparto_sigue_igual() -> None:
    ficha = _ficha(user=DEV, creator=DEV)
    assert identidad_del_dev(ficha) == DEV


def test_si_user_es_el_reparto_pero_creator_no_vale_creator() -> None:
    ficha = _ficha(user=REPARTO, creator=DEV)
    assert identidad_del_dev(ficha) == DEV


def test_el_filtro_de_migradas_ya_no_pregunta_por_creator() -> None:
    texto = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src" / "trading" / "universal_trader.py"
    ).read_text(encoding="utf-8")
    assert texto.count("identidad_del_dev(token_info)") >= 2
    assert '_dev0 = getattr(token_info, "creator", None)' not in texto


def test_el_dev_es_quien_firma_aunque_cobre_otra_wallet() -> None:
    """Si las comisiones van a otra wallet normal, el dev sigue siendo `user`."""
    otra = str(Pubkey.new_unique())
    ficha = _ficha(user=DEV, creator=otra)
    assert identidad_del_dev(ficha) == DEV
