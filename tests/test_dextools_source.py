"""Tests de la fuente DEXTools (DEXTboard) y del parseo de su API."""

# ruff: noqa: S101

import pytest

from dextools.client import (
    DextoolsClient,
    DextoolsHotPool,
    _parse_hotpools,
    _parse_token_safety,
)
from monitoring.dextools_source import (
    DexBoardEntrantTracker,
    build_dextools_heartbeat,
    importe_por_rango,
)


def _pool(mint: str, rank: int = 1) -> DextoolsHotPool:
    return DextoolsHotPool(mint=mint, rank=rank, symbol="TEST")


def test_la_foto_del_arranque_no_es_senal() -> None:
    """Lo que ya estaba en el ranking al arrancar no se compra."""
    tracker = DexBoardEntrantTracker()
    tracker.prime([_pool("aaa"), _pool("bbb")])
    assert tracker.accept(_pool("aaa")) is False
    assert tracker.accept(_pool("bbb")) is False


def test_entrar_en_el_ranking_es_senal_una_sola_vez() -> None:
    """La senal es ENTRAR, no seguir dentro: un mint dispara una vez."""
    tracker = DexBoardEntrantTracker()
    tracker.prime([_pool("aaa")])
    assert tracker.accept(_pool("ccc")) is True
    assert tracker.accept(_pool("ccc")) is False


def test_un_mint_vacio_nunca_se_acepta() -> None:
    tracker = DexBoardEntrantTracker()
    assert tracker.accept(DextoolsHotPool(mint="")) is False


def test_el_puesto_alto_compra_mas() -> None:
    """Subir en el DEXTboard cuesta mas NITRO, asi que se apuesta mas."""
    assert importe_por_rango(3, 0.15, 10, 0.20) == 0.20
    assert importe_por_rango(10, 0.15, 10, 0.20) == 0.20


def test_el_puesto_bajo_compra_lo_normal() -> None:
    assert importe_por_rango(25, 0.15, 10, 0.20) == 0.15


def test_sin_puesto_conocido_se_compra_lo_normal() -> None:
    """No saber cuanto pagaron no es motivo para apostar mas."""
    assert importe_por_rango(None, 0.15, 10, 0.20) == 0.15
    assert importe_por_rango(3, 0.15, 0, 0.20) == 0.15


def test_hotpools_acepta_las_dos_formas_de_la_api() -> None:
    """La v2 devuelve unas veces mainToken y otras token; valen las dos."""
    envuelto = {
        "data": [
            {
                "rank": 1,
                "address": "pool1",
                "mainToken": {"address": "m1", "symbol": "A"},
            },
            {"rank": 2, "address": "pool2", "token": {"address": "m2", "symbol": "B"}},
        ]
    }
    pools = _parse_hotpools(envuelto)
    assert [p.mint for p in pools] == ["m1", "m2"]
    assert pools[0].pool == "pool1"
    assert pools[1].symbol == "B"


def test_hotpools_acepta_lista_pelada_y_pone_puesto_por_orden() -> None:
    """Sin campo rank, la posicion en la lista ES el puesto."""
    pools = _parse_hotpools(
        [{"mainToken": {"address": "m1"}}, {"token": {"address": "m2"}}]
    )
    assert [p.rank for p in pools] == [1, 2]


def test_hotpools_descarta_entradas_sin_mint_sin_romperse() -> None:
    """Un token sin direccion no es comprable, pero no debe tumbar la vuelta."""
    pools = _parse_hotpools(
        {"data": [{"rank": 1}, "basura", {"mainToken": {"address": "m1"}}]}
    )
    assert [p.mint for p in pools] == ["m1"]


def test_hotpools_respeta_el_limite() -> None:
    entradas = [{"mainToken": {"address": f"m{i}"}} for i in range(10)]
    assert len(_parse_hotpools(entradas, limit=3)) == 3


def test_seguridad_lee_bloqueo_y_auditoria() -> None:
    seguridad = _parse_token_safety(
        {"data": {"lockedPercentage": 87.5}},
        {"data": {"isHoneypot": "no", "isMintable": "yes"}},
    )
    assert seguridad.lp_locked_pct == 87.5
    assert seguridad.is_honeypot is False
    assert seguridad.mint_authority_active is True


def test_lo_desconocido_se_queda_en_none_no_en_false() -> None:
    """ "unknown" tiene que seguir siendo None: convertirlo en False seria
    aprobar una moneda de la que no se sabe nada."""
    seguridad = _parse_token_safety({}, {"data": {"isHoneypot": "unknown"}})
    assert seguridad.is_honeypot is None
    assert seguridad.lp_locked_pct is None


def test_la_url_lleva_el_plan_dentro() -> None:
    """Una clave de un plan contra la ruta de otro devuelve 401."""
    cliente = DextoolsClient(api_key="x", tier="standard")
    assert cliente.tier == "standard"
    assert cliente.configured is True


def test_un_plan_inventado_se_rechaza_al_construir() -> None:
    """Mejor reventar al arrancar que quedarse mudo dando 401 cada vuelta."""
    with pytest.raises(ValueError, match="Unsupported DEXTools API tier"):
        DextoolsClient(api_key="x", tier="megaplan")


def test_sin_clave_la_fuente_se_declara_no_configurada() -> None:
    cliente = DextoolsClient(api_key="   ", tier="trial")
    assert cliente.configured is False


def test_el_heartbeat_avisa_de_que_falta_la_clave() -> None:
    texto = build_dextools_heartbeat(
        15, 0, 0, "trial", configured=False, paper_mode=True
    )
    assert "SIN CLAVE" in texto


def test_el_heartbeat_ensena_el_embudo_y_el_modo() -> None:
    texto = build_dextools_heartbeat(
        15,
        12,
        3,
        "standard",
        configured=True,
        paper_mode=False,
        embudo={"sin_construir": 7, "sin_bloqueo": 2},
    )
    assert "REAL" in texto
    assert "standard" in texto
    assert "no se pudo construir: 7" in texto
    assert "sin liquidez bloqueada: 2" in texto
