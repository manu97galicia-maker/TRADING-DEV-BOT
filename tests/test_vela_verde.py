"""No comprar el ultimo escalon de una subida vertical.

EL CASO REAL, CATO, 23-sep: el bot entro a 26K de market cap justo despues de
que la moneda subiera de 7,5K a 25K en medio minuto. Medio minuto mas tarde
valia 12,4K. Comprar detras de una vela verde enorme es comprar el techo: la
gente que la empujo ya esta dentro, y lo siguiente que hace es vender.

POR QUE NO BASTABA CON LA PRESION COMPRADORA, que es lo que ya habia: la
presion mira si AHORA entra mas dinero del que sale, y en plena vela verde la
presion es inmejorable -- justo en el peor momento para entrar. Miran cosas
distintas: una el dinero de ahora, otra el precio de hace un rato.
"""

# ruff: noqa: S101, SLF001, PLR2004

import time
from types import SimpleNamespace

from monitoring import volumen
from monitoring.holders_source import HoldersSource, build_holders_heartbeat


def _precios(mint: str, precios: list[float], *, hace: float = 5.0) -> None:
    """Deja `precios` en la historia, repartidos en los ultimos `hace` s."""
    volumen._historia_precio.pop(mint, None)
    ahora = time.time()
    paso = hace / max(len(precios), 1)
    for i, p in enumerate(precios):
        volumen._historia_precio[mint].append((ahora - hace + i * paso, p))


def test_una_moneda_que_ha_triplicado_sale_marcada() -> None:
    """El caso de CATO: 7,5 -> 25 es un x3,3."""
    _precios("m", [7.5, 12.0, 25.0])
    assert volumen.subida("m", 10.0) > 2.0


def test_una_moneda_plana_no_sale_marcada() -> None:
    _precios("m", [10.0, 10.1, 9.9, 10.05])
    assert volumen.subida("m", 10.0) < 0.05


def test_sin_datos_no_se_inventa_una_subida() -> None:
    """No tener datos NO es lo mismo que estar plana, y bloquear una compra
    por falta de datos seria inventarse un motivo."""
    volumen._historia_precio.pop("nueva", None)
    assert volumen.subida("nueva", 10.0) == 0.0
    _precios("solo_uno", [10.0])
    assert volumen.subida("solo_uno", 10.0) == 0.0


def test_solo_mira_la_ventana_pedida() -> None:
    """Una subida de hace un minuto ya no es una vela verde: es el precio."""
    mint = "m"
    volumen._historia_precio.pop(mint, None)
    ahora = time.time()
    volumen._historia_precio[mint].append((ahora - 120, 1.0))
    volumen._historia_precio[mint].append((ahora - 1, 10.0))
    volumen._historia_precio[mint].append((ahora, 10.1))
    assert volumen.subida(mint, 10.0) < 0.05, "cuenta un salto de hace dos minutos"


def test_una_bajada_no_cuenta_como_subida() -> None:
    _precios("m", [25.0, 18.0, 12.0])
    assert volumen.subida("m", 10.0) == 0.0


def test_la_fuente_descarta_la_vela_verde() -> None:
    """De punta a punta, con la condicion encendida."""
    f = HoldersSource(
        "wss://x", SimpleNamespace(), SimpleNamespace(),
        min_holders=3, max_edad_seg=0.0, ultimas_compras=0, presion_seg=0,
        subida_seg=10.0, max_subida=1.0, heartbeat_interval=0,
    )
    mint = "Er1sFZm3HGuXFiKeXQLg84H9B9Vqk3dp94AjLiS9pump"
    ahora = time.time()
    volumen._ops.pop(mint, None)
    volumen._ops[mint] = [(ahora, 0.5, True, "w1"), (ahora, 0.5, True, "w2")]
    f._revisar([mint], None)  # se ve por debajo del corte
    volumen._ops[mint] = [
        (ahora, 0.5, True, f"w{i}") for i in range(5)
    ]
    _precios(mint, [7.5, 12.0, 25.0])
    f._revisar([mint], None)
    assert f._embudo.get("vela_verde") == 1, f"embudo: {f._embudo}"


def test_el_parte_ensena_cuantas_se_descartan_asi() -> None:
    """Si descarta de mas hay que poder verlo: el numero a mover es
    `max_subida`, pero primero hay que saber que esta actuando."""
    texto = build_holders_heartbeat(23, 0.0, 0.04, {"vela_verde": 7}, paper_mode=False)
    assert "vela verde" in texto
    assert "7" in texto
