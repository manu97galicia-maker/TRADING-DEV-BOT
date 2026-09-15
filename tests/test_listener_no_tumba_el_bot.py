"""Un `listener_type` mal configurado no puede impedir que el bot arranque.

EL RIESGO QUE CIERRA. `ListenerFactory` exige endpoint y token para montar
geyser, y lanzaba ValueError desde el CONSTRUCTOR del trader. Poner
`listener_type: geyser` sin esas dos variables en el .env dejaba el bot sin
arrancar -- y un bot que no arranca es un bot que no vigila las posiciones
que ya estan abiertas: ni stop loss ni take profit. Es un precio absurdo por
una linea mal puesta en un YAML, teniendo `logs` configurado y funcionando.

Y LA OTRA MITAD, que es la que sorprende: con fuentes de adquisicion activas
el listener principal NI SIQUIERA ARRANCA. `start` lanza las fuentes y hace
`return` antes de tocarlo. Asi que cambiar `listener_type` a geyser, con la
configuracion de hoy, no acelera nada.
"""

# ruff: noqa: S101

from pathlib import Path

import pytest
import yaml

from monitoring.listener_factory import ListenerFactory

TRADER = Path(__file__).resolve().parent.parent / "src/trading/universal_trader.py"
CONFIG = Path(__file__).resolve().parent.parent / "bots/bot-paper-ai-entry.yaml"
#: Las mismas fuentes que mira `start` para decidir si lanza el listener.
FUENTES = (
    "buy_on_dex_paid",
    "birdeye_trending",
    "dextools_hotpools",
    "curva_96",
    "telegram_calls",
    "discord_calls",
    "gmgn_signals",
)


def _fuente() -> str:
    return TRADER.read_text(encoding="utf-8")


def test_geyser_sin_credenciales_sigue_lanzando_en_la_factoria() -> None:
    """La factoria hace bien en negarse: es quien sabe que falta."""
    with pytest.raises(ValueError, match="Geyser"):
        ListenerFactory.create_listener(
            listener_type="geyser", wss_endpoint="wss://ejemplo"
        )


def test_el_trader_no_se_cae_por_eso_y_baja_a_logs() -> None:
    """Quien decide que hacer con ese fallo es el trader, no la factoria."""
    texto = _fuente()
    inicio = texto.index("# Initialize the appropriate listener")
    trozo = texto[inicio : inicio + 2000]
    assert "except ValueError" in trozo
    assert 'listener_type="logs"' in trozo


def test_sin_wss_no_hay_a_donde_caer_y_se_propaga() -> None:
    """Sin endpoint de websocket no hay respaldo: callarse el fallo dejaria
    el bot corriendo sin escuchar nada, que es peor que no arrancar."""
    texto = _fuente()
    inicio = texto.index("# Initialize the appropriate listener")
    trozo = texto[inicio : inicio + 2000]
    assert "or not wss_endpoint:" in trozo
    assert "raise" in trozo


def test_con_fuentes_activas_el_listener_principal_no_arranca() -> None:
    """La linea exacta que lo decide, para que nadie asuma lo contrario."""
    texto = _fuente()
    inicio = texto.index("acquisition_tasks: list[asyncio.Task] = []")
    trozo = texto[inicio : inicio + 3000]
    assert "if acquisition_tasks:" in trozo
    assert "await asyncio.gather(*acquisition_tasks)" in trozo
    # El `return` es lo que hace que el listener de abajo no se use nunca.
    cierre = trozo.index("await asyncio.gather(*acquisition_tasks)")
    assert "return" in trozo[cierre : cierre + 120]


def test_la_config_de_hoy_tiene_fuentes_activas() -> None:
    """Consecuencia practica: hoy `listener_type` es config muerta.

    Si algun dia se apagan todas las fuentes, este test se pone rojo y avisa
    de que `listener_type` vuelve a mandar -- y entonces si merece la pena
    mirar si el plan del nodo trae geyser.
    """
    f = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["filters"]
    activas = [n for n in FUENTES if (f.get(n) or {}).get("enabled")]
    assert activas, "sin fuentes activas, listener_type vuelve a mandar"
    assert f["listener_type"] == "logs"
