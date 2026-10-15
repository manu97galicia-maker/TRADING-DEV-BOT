"""Por que el bot entra donde entra. Contesta en una pantalla de movil.

    banda            # lo ultimo
    banda 50         # mira las ultimas 50 entradas

POR QUE EXISTE. El 15-oct Manu pidio entrar entre 18.000 y 23.000 $ y el bot
seguia entrando en 28.000. Hay tres causas posibles y desde fuera se parecen:

  1. el droplet no ha hecho `pull` y corre el codigo viejo
  2. ha hecho pull pero el bot NO se ha reiniciado, asi que el proceso vivo
     sigue con la configuracion que leyo al arrancar
  3. la banda esta puesta y algo la esquiva

Distinguirlas pedia cuatro comandos distintos y leer un YAML de 800 lineas
desde el movil. Esto las separa solas.

NO USA PyYAML a proposito: se lanza con `python3` pelado (asi lo instala
`instalar_comandos.sh`) y fuera del entorno del bot esa libreria puede no
estar. Se leen las pocas claves que hacen falta con una busqueda de texto.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BOTS = RAIZ / "bots"
LOGS = RAIZ / "logs"

#: Claves que deciden donde entra el bot.
CLAVES = ("min_entry_mc", "max_entry_mc", "max_buy_age", "umbral", "tope")


def _config_activa() -> Path | None:
    """El unico yaml con `enabled: true`. Si hay varios, se avisa."""
    encendidos = []
    for f in sorted(BOTS.glob("*.yaml")):
        texto = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^enabled:\s*true\s*$", texto, re.MULTILINE):
            encendidos.append(f)
    if len(encendidos) > 1:
        print("*** HAY MAS DE UN BOT ENCENDIDO: ***")
        for f in encendidos:
            print(f"      {f.name}")
        print("    Comparten wallet y compiten por el saldo.\n")
    return encendidos[0] if encendidos else None


def _valor(texto: str, clave: str) -> str | None:
    """El valor de una clave, ignorando las lineas comentadas."""
    for linea in texto.splitlines():
        if linea.lstrip().startswith("#"):
            continue
        m = re.match(rf"\s*{re.escape(clave)}:\s*(\S+)", linea)
        if m:
            return m.group(1)
    return None


def _git(*args: str) -> str:
    try:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=RAIZ,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "(no se pudo preguntar a git)"


def _log_mas_nuevo() -> Path | None:
    if not LOGS.is_dir():
        return None
    ficheros = sorted(LOGS.glob("*.log"), key=lambda f: f.stat().st_mtime)
    return ficheros[-1] if ficheros else None


def _imprime_codigo() -> None:
    """Que codigo hay en la maquina, y si falta algo por traer."""
    print("\n1) CODIGO EN LA MAQUINA")
    print(f"   rama   : {_git('branch', '--show-current') or '?'}")
    print(f"   commit : {_git('log', '--oneline', '-1') or '?'}")
    detras = _git("rev-list", "--count", "HEAD..@{u}")
    if detras.isdigit() and int(detras) > 0:
        print(f"   *** FALTAN {detras} COMMITS POR TRAER ***")
        print("       -> bash /root/ai-bot/deploy/arranca.sh")
    elif detras == "0":
        print("   al dia con GitHub")


def _imprime_config(cfg: Path) -> tuple[str | None, str | None, bool]:
    """Las claves que deciden la entrada. Devuelve (suelo, techo, puesta)."""
    print(f"   {cfg.name}")
    texto = cfg.read_text(encoding="utf-8", errors="replace")
    for clave in CLAVES:
        v = _valor(texto, clave)
        print(f"   {clave:16s} = {v if v is not None else '(no esta)'}")

    suelo = _valor(texto, "min_entry_mc")
    techo = _valor(texto, "max_entry_mc")
    puesta = bool(suelo and techo and float(suelo) > 0 and float(techo) > 0)
    if not puesta:
        print("\n   *** LA BANDA EN DOLARES NO ESTA PUESTA ***")
        print("       Sin min_entry_mc / max_entry_mc, quien decide la")
        print("       entrada es el % de curva, que se mueve con el precio")
        print("       de SOL.")
    return suelo, techo, puesta


def _lineas_del_log(log: Path) -> tuple[list[str], list[str], list[str]]:
    """(aceptadas, rechazadas por techo, rechazadas por tiempo)."""
    lineas = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return (
        [x for x in lineas if "CUMPLE " in x],
        [x for x in lineas if "se paso de la banda" in x],
        [x for x in lineas if "no cumplio en" in x],
    )


def _imprime_log(
    cumple: list[str], paso: list[str], nocumple: list[str], cuantas: int
) -> None:
    print(f"\n   ENTRADAS ACEPTADAS (ultimas {cuantas} de {len(cumple)}):")
    if not cumple:
        print("     ninguna todavia")
    for x in cumple[-cuantas:]:
        m = re.search(r"MC \$([\d,]+)", x)
        sym = re.search(r"CUMPLE (\S+?):", x)
        nombre = sym.group(1) if sym else "?"
        print(f"     {nombre:>12}  MC ${m.group(1) if m else '?'}")

    print(f"\n   RECHAZADAS POR PASARSE DEL TECHO: {len(paso)}")
    for x in paso[-3:]:
        m = re.search(r"MC \$([\d,]+) > \$([\d,]+)", x)
        if m:
            print(f"     MC ${m.group(1)} contra techo ${m.group(2)}")
    print(f"   RECHAZADAS POR NO ENTRAR EN BANDA A TIEMPO: {len(nocumple)}")


def _por_encima_del_techo(cumple: list[str], techo: str) -> list[str]:
    altas = []
    for x in cumple:
        m = re.search(r"MC \$([\d,]+)", x)
        if m and float(m.group(1).replace(",", "")) > float(techo):
            altas.append(m.group(1))
    return altas


def _veredicto(
    banda: tuple[str | None, str | None, bool],
    log: tuple[list[str], list[str], list[str]],
) -> None:
    """Las tres causas posibles, separadas."""
    suelo, techo, puesta = banda
    cumple, paso, nocumple = log
    print("\n4) VEREDICTO")
    if not puesta:
        print("   El codigo de la maquina no tiene la banda puesta.")
        print("   -> bash /root/ai-bot/deploy/arranca.sh")
        return
    if not cumple and not paso and not nocumple:
        print("   La banda esta puesta pero el log no tiene NI UNA linea de")
        print("   las que escribe ese bloque. Casi seguro que el bot sigue")
        print("   corriendo con la configuracion que leyo al arrancar:")
        print("   -> hay que REINICIARLO, no solo hacer pull.")
        return
    if cumple and not paso:
        altas = _por_encima_del_techo(cumple, techo or "0")
        if altas:
            print(f"   *** HAY {len(altas)} ENTRADAS POR ENCIMA DEL TECHO")
            print(f"       (${techo}) y NINGUNA rechazada. Eso es un fallo:")
            print("       la banda no se esta aplicando. Avisa de esto.")
        else:
            print(
                f"   Todas las entradas estan dentro de la banda "
                f"(${suelo}-${techo}). Funcionando."
            )
        return
    print(f"   La banda esta aplicandose: {len(paso)} rechazos por techo.")
    print(f"   Entradas dentro de ${suelo}-${techo}.")


def main() -> None:
    cuantas = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print("=" * 56)
    print("  DONDE ESTA ENTRANDO EL BOT")
    print("=" * 56)

    _imprime_codigo()

    cfg = _config_activa()
    print("\n2) CONFIGURACION ENCENDIDA")
    if cfg is None:
        print("   *** NINGUN BOT CON enabled: true -> no va a comprar nada ***")
        return
    banda = _imprime_config(cfg)

    log = _log_mas_nuevo()
    print("\n3) LO QUE DICE EL LOG")
    if log is None:
        print("   no hay ningun log en logs/")
        return
    print(f"   {log.name}")
    lineas = _lineas_del_log(log)
    _imprime_log(*lineas, cuantas)

    _veredicto(banda, lineas)


if __name__ == "__main__":
    main()
