"""Diagnostico de "por que no compra" (5-nov): mira y no ensena claves."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "porque-no-compra.sh"
MINT = "9NhPx9ywRQKcLeAmj3dij4iGW2EZEqvv8pNZaq93pump"


def _correr(tmp_path: Path, log: str, *args: str) -> str:
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "dev1_real.log").write_text(log, encoding="utf-8")
    texto = SCRIPT.read_text(encoding="utf-8").replace(
        "RAIZ=/root/ai-bot", f"RAIZ={tmp_path}", 1
    )
    copia = tmp_path / "p.sh"
    copia.write_text(texto, encoding="utf-8")
    return subprocess.run(
        ["bash", str(copia), *args], capture_output=True, text=True, timeout=60,
        check=False,
    ).stdout


def test_sintaxis_valida() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_no_cambia_nada_ni_compra() -> None:
    texto = SCRIPT.read_text(encoding="utf-8")
    for prohibido in ("pkill", "systemctl", "git ", "rm ", "stash", "run_bot_process("):
        assert prohibido not in texto


def test_nunca_ensena_la_clave_de_helius(tmp_path: Path) -> None:
    log = f"INFO:x:CURVA AL 3% — PEPE ({MINT}) wss://h.example/?api-key=SECRETO123&x=1\n"
    salida = _correr(tmp_path, log, MINT)
    assert MINT in salida
    assert "SECRETO123" not in salida


def test_dice_si_no_ha_visto_la_moneda(tmp_path: Path) -> None:
    salida = _correr(tmp_path, "INFO:x:nada\n", MINT)
    assert "NO la ha visto" in salida


def test_detecta_que_pump_fun_no_da_el_dato(tmp_path: Path) -> None:
    log = "INFO:t:DEV filtro BLOCK A: 0 migr / 0 lanz (0%) < 1 y 1%\n" * 3
    salida = _correr(tmp_path, log)
    assert "TODOS los rechazados salen con 0 lanzadas" in salida
