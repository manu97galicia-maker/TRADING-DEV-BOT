"""Lo que NUNCA puede acabar en GitHub, comprobado en cada vuelta de tests.

POR QUE ESTE FICHERO EXISTE. El `.env` de este proyecto ya se subio una vez y
sigue en el historial, en el commit 229d705: con la clave privada de la wallet
dentro. Borrarlo del arbol no lo quita del historial, asi que ese error no se
arregla, solo se evita el siguiente.

El 18-sep-2026, revisando un `git status` del droplet, aparecio
`tg_wagbo.session` sin seguir y SIN ignorar. Un fichero de sesion de Telegram
no es un fichero de datos: es una llave de la cuenta -- quien la tenga entra
sin contrasena ni SMS, y ademas lee los canales de senales. Estaba a un
`git add -A` de repetir la historia del .env. No habia llegado al repo, y
estos tests son para que siga sin llegar.
"""

# ruff: noqa: S101

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["/usr/bin/git", "-C", str(RAIZ), *args],
        capture_output=True, text=True, timeout=30, check=False,
    ).stdout


def _ignorado(nombre: str) -> bool:
    return subprocess.run(  # noqa: S603
        ["/usr/bin/git", "-C", str(RAIZ), "check-ignore", "-q", nombre],
        capture_output=True, timeout=30, check=False,
    ).returncode == 0


# ------------------------------------------------- las sesiones de Telegram


def test_una_sesion_de_telegram_esta_ignorada() -> None:
    """La llave de la cuenta. Sin esto, un `git add -A` desde el droplet la
    sube y hay que revocar la sesion desde el telefono."""
    assert _ignorado("tg_wagbo.session")
    assert _ignorado("cualquiera.session")
    assert _ignorado("moontok_watcher.session")


def test_tambien_el_diario_de_la_sesion() -> None:
    """Telethon deja un `-journal` al lado; solo tiene sentido con su sesion,
    pero no hay razon para subirlo y si para no hacerlo."""
    assert _ignorado("tg_wagbo.session-journal")


def test_ninguna_sesion_esta_versionada_hoy() -> None:
    """Lo que de verdad importa: que no haya ninguna YA dentro. Un
    `.gitignore` no afecta a lo que git ya sigue."""
    seguidas = [x for x in _git("ls-files").splitlines() if ".session" in x]
    assert not seguidas, f"hay sesiones versionadas: {seguidas}"


def test_ninguna_sesion_llego_nunca_al_historial() -> None:
    """Y que no lo estuviera antes. Si este test se pone rojo algun dia, NO
    basta con borrar el fichero: hay que revocar la sesion en Telegram
    (Ajustes -> Dispositivos), porque la llave ya estaria publicada.
    """
    historial = _git("log", "--all", "--oneline", "--", "*.session").strip()
    assert not historial, (
        "una sesion de Telegram esta en el historial: revocarla en el "
        f"telefono, no basta con borrarla.\n{historial}"
    )


# ------------------------------------- las copias viejas del trader


def test_las_copias_del_trader_estan_ignoradas() -> None:
    """No corren, pero estan a un `uv run` de mover dinero con codigo de hace
    meses, y ensuciaban `git status` hasta hacerlo tardar cuatro segundos."""
    assert _ignorado("ut3_new.py")
    assert _ignorado("universal_trader.new.py")


def test_el_patron_no_tapa_ningun_fichero_que_git_ya_siga() -> None:
    """LA COMPROBACION QUE EVITA EL ERROR SILENCIOSO. Un patron nuevo puede
    ocultar algo que ya se versionaba, y entonces los cambios de ese fichero
    dejan de verse sin que nadie lo note. Se comprueba que ninguno de los
    seguidos cae en los patrones que se acaban de anadir.
    """
    seguidos = _git("ls-files").splitlines()
    for nombre in seguidos:
        base = nombre.rsplit("/", 1)[-1]
        assert not base.endswith(("_new.py", ".new.py")), (
            f"{nombre} esta versionado y el patron nuevo lo ocultaria"
        )


# -------------------------------------------- los otros secretos de siempre


def test_los_secretos_de_siempre_siguen_ignorados() -> None:
    """Que anadir patrones nuevos no haya roto los que ya estaban."""
    for nombre in (".env", "wallets_generadas.json", "algo.key"):
        assert _ignorado(nombre), f"{nombre} ha dejado de estar ignorado"
