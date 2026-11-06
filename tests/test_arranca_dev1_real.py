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
    assert 'git fetch "$ORIGEN" "$RAMA"' in TEXTO


def test_baja_main_que_es_donde_manu_cambia_la_config() -> None:
    # 6-nov: los cambios hechos en GitHub (main) no llegaban al droplet.
    assert "RAMA=main" in TEXTO


def test_deja_el_servidor_igual_que_github_sin_mezclar() -> None:
    # 6-nov: `git pull` mezclaba y el droplet quedo con 4 ficheros distintos.
    ordenes = [l.strip() for l in TEXTO.splitlines() if not l.lstrip().startswith("#")]
    assert not any(l.startswith("git pull") for l in ordenes)
    assert 'git checkout -B "$RAMA" FETCH_HEAD' in TEXTO
    assert "git diff --quiet FETCH_HEAD" in TEXTO
    # Lo que tuviera el droplet de su cosecha queda en una rama de respaldo,
    # guardada ANTES de cambiar nada.
    assert TEXTO.index('git branch -f "respaldo-$F" HEAD') < TEXTO.index("git checkout -B")


def test_deshace_una_actualizacion_a_medias() -> None:
    assert "git merge --abort" in TEXTO
    assert TEXTO.index("git merge --abort") < TEXTO.index("git stash push -u")


def test_para_pump_bot_antes_de_mirar_si_ya_corre() -> None:
    # pump-bot arranca esta estrategia con bot_runner y el pgrep no lo ve:
    # sin pararlo habria dos bots en real sobre la misma wallet.
    para = "systemctl stop pump-bot-autoactualiza.timer pump-bot"
    assert TEXTO.index(para) < TEXTO.index('pgrep -f "run_bot_process.*dev1-bloque2"')
    assert "git reset --hard" not in TEXTO
