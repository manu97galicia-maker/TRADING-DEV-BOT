"""El arranque en una linea de dev1-bloque2 (4-nov, "dimelo en un solo comando")."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "arranca-dev1-real.sh"
TEXTO = SCRIPT.read_text(encoding="utf-8")


def test_sintaxis_valida() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_no_arranca_dos_a_la_vez() -> None:
    # El pgrep va antes de tocar nada: un doble pegado no crea dos bots.
    guarda = 'pgrep -f "run_bot_process.*dev1-bloque2"'
    assert TEXTO.index(guarda) < TEXTO.index("git stash")


def test_lleva_apagado_automatico() -> None:
    # REGLA 1: nunca sin plazo. 9 horas por defecto.
    assert 'HORAS="${1:-9}"' in TEXTO
    assert 'timeout "$SEG"' in TEXTO


def test_solo_arranca_en_real_con_siete_plazas() -> None:
    assert 'grep -q "^  paper_trade: false"' in TEXTO
    assert 'grep -q "^  max_posiciones: 7"' in TEXTO


def test_el_bot_no_hereda_el_script_como_entrada() -> None:
    assert "< /dev/null &" in TEXTO


def test_nunca_stash_a() -> None:
    # stash -a se llevaria lo ignorado: .env y marcadores de posiciones.
    assert "stash -a" not in TEXTO and "--all" not in TEXTO
    assert "git stash push -u" in TEXTO


def test_se_puede_bajar_por_https() -> None:
    # Sin llave SSH en el droplet (4-nov), con el repo publico se pasa ORIGEN.
    assert 'ORIGEN="${ORIGEN:-origin}"' in TEXTO
    assert 'git pull --no-rebase --no-edit "$ORIGEN" "$RAMA"' in TEXTO
