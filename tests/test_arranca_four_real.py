"""Arranque en una linea del sistema de Codex en real (4-nov)."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "arranca-four-real.sh"
TEXTO = SCRIPT.read_text(encoding="utf-8")


def test_sintaxis_valida() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_no_arranca_dos_a_la_vez() -> None:
    assert TEXTO.index('pgrep -f "run_four_strategies.py --run"') < TEXTO.index("git stash")


def test_avisa_si_la_otra_esta_corriendo_pero_no_la_para() -> None:
    assert 'pgrep -f "run_bot_process.*dev1-bloque2"' in TEXTO
    assert "\npkill" not in TEXTO


def test_arranca_con_run_y_sin_heredar_el_script() -> None:
    assert "run_four_strategies.py --run" in TEXTO
    assert "< /dev/null &" in TEXTO


def test_nunca_stash_a() -> None:
    assert "stash -a" not in TEXTO and "--all" not in TEXTO


def test_el_lanzador_de_codex_se_apaga_a_la_hora() -> None:
    # REGLA 1: el plazo lo pone run_four_strategies.py; si cambiara, esto lo dice.
    runner = (ROOT / "run_four_strategies.py").read_text(encoding="utf-8")
    assert "MAX_RUN_SECONDS = 3600" in runner


def test_la_configuracion_esta_encendida_y_solo_el_primer_bloque() -> None:
    plan = yaml.safe_load((ROOT / "configs" / "four-strategies.yaml").read_text(encoding="utf-8"))
    assert plan["enabled"] is True
    assert plan["four_strategies"]["active_strategies"] == ["first-block-four-buyers"]
    gate = plan["four_strategies"]["creator_migration_gate"]
    assert gate["enabled"] is True and gate["min_rate"] == 0.01
