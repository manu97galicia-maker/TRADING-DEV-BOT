"""Tests del vigia que avisa cuando el bot se para.

El 12-sep el bot llevaba ocho dias parado y no lo supimos hasta mirarlo a
mano. `Restart=always` cubre que el proceso reviente, pero no que alguien
lo pare ni que se quede caido tras agotar reintentos.
"""

# ruff: noqa: S101, E402, PLR2004

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "deploy"))

from avisar_caida import construir_aviso


def test_el_aviso_dice_que_esta_parado_y_por_que_importa() -> None:
    texto = construir_aviso("pump-bot", "failed", "")
    assert "PARADO" in texto
    assert "failed" in texto
    # Lo que de verdad hay que entender leyendo el movil: que hay dinero
    # abierto que nadie esta vigilando.
    assert "vender" in texto


def test_el_aviso_lleva_las_ultimas_lineas_del_log() -> None:
    """Para no tener que entrar al droplet a mirar que paso."""
    texto = construir_aviso("pump-bot", "failed", "ERROR algo reventó")
    assert "ERROR algo reventó" in texto
    assert "<pre>" in texto


def test_sin_log_el_aviso_sigue_saliendo() -> None:
    """Que no haya diario no puede dejarte sin enterarte de la caida."""
    texto = construir_aviso("pump-bot", "inactive", "")
    assert "PARADO" in texto
    assert "<pre>" not in texto


def test_el_log_se_recorta_para_que_telegram_lo_acepte() -> None:
    """Telegram corta sobre 4096 caracteres: un log largo tumbaria el aviso."""
    texto = construir_aviso("pump-bot", "failed", "x" * 50_000)
    assert len(texto) < 4096


def test_el_log_no_puede_inyectar_html() -> None:
    """El log lleva texto de fuera; sin escapar, romperia el formato."""
    texto = construir_aviso("pump-bot", "failed", "<b>no</b> & <script>")
    assert "<script>" not in texto
    assert "&lt;script&gt;" in texto


def test_hay_unidades_de_systemd_para_instalarlo() -> None:
    """Sin el timer solo se cubre 'failed', no 'lo pararon a mano'."""
    assert (RAIZ / "deploy/pump-bot-vigia.service").exists()
    assert (RAIZ / "deploy/pump-bot-vigia.timer").exists()


def test_el_timer_comprueba_de_forma_periodica() -> None:
    texto = (RAIZ / "deploy/pump-bot-vigia.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=" in texto
    assert "WantedBy=timers.target" in texto
