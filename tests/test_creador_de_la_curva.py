"""6-nov: la compra de coolcoin fallo con 2006 ConstraintSeeds en creator_vault.

El bot saco `creator_vault` del creador que trae el aviso de creacion, pero
pump.fun ya habia cambiado el creador de la curva (reparto de comisiones). La
compra tiene que usar el de la curva, como ya hacia la venta.
"""

# ruff: noqa: S101

from pathlib import Path
from types import SimpleNamespace

from solders.pubkey import Pubkey

from platforms.pumpfun.address_provider import PumpFunAddressProvider
from trading.platform_aware import actualizar_creador_de_la_curva

ROOT = Path(__file__).resolve().parents[1]


def _ficha(creador: Pubkey) -> SimpleNamespace:
    prov = PumpFunAddressProvider()
    return SimpleNamespace(
        symbol="coolcoin",
        creator=creador,
        creator_vault=prov.derive_creator_vault(creador),
    )


def test_usa_el_creador_de_la_curva() -> None:
    prov = PumpFunAddressProvider()
    aviso, curva = Pubkey.new_unique(), Pubkey.new_unique()
    ficha = _ficha(aviso)
    assert actualizar_creador_de_la_curva(ficha, {"creator": str(curva)}, prov) is True
    assert ficha.creator == curva
    assert ficha.creator_vault == prov.derive_creator_vault(curva)


def test_si_coinciden_no_cambia_nada() -> None:
    prov = PumpFunAddressProvider()
    creador = Pubkey.new_unique()
    ficha = _ficha(creador)
    antes = ficha.creator_vault
    assert actualizar_creador_de_la_curva(ficha, {"creator": str(creador)}, prov) is False
    assert ficha.creator_vault == antes


def test_sin_creador_en_la_curva_no_toca_nada() -> None:
    prov = PumpFunAddressProvider()
    ficha = _ficha(Pubkey.new_unique())
    antes = ficha.creator_vault
    assert actualizar_creador_de_la_curva(ficha, {"price_per_token": 1.0}, prov) is False
    assert actualizar_creador_de_la_curva(ficha, None, prov) is False
    assert ficha.creator_vault == antes


def test_la_compra_lo_hace_antes_de_montar_la_instruccion() -> None:
    texto = (ROOT / "src" / "trading" / "platform_aware.py").read_text(encoding="utf-8")
    compra = texto[texto.index("class PlatformAwareBuyer") : texto.index("class PlatformAwareSeller")]
    llamada = "actualizar_creador_de_la_curva(token_info, pool_state, address_provider)"
    assert llamada in compra
    assert compra.index(llamada) < compra.index("build_buy_instruction(")
