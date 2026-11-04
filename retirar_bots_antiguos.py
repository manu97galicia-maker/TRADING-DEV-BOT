"""Vista previa por defecto. El usuario puede ejecutar --apply como root en Linux.

No importa bots ni proveedores. No borra datos, edita claves ni cierra posiciones.
"""

import argparse
import ast
import hashlib
import json
import os
import posixpath
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

UNITS = (
    "pump-curva50.service",
    "pump-dev18.service",
    "pump-presion.service",
    "sniper-deck.service",
    "pump-bot.service",
    "definitiva.service",
    "lowcap-research.service",
)
MAIN_CWD = "/root/ai-bot-four-strategies"
OLD_CWD = "/root/ai-bot"
PARENT_PID = 1175768
KEYS = (Path(OLD_CWD) / ".env", Path(MAIN_CWD) / ".env.four-strategies")
BACKUPS = Path("/root/ai-bot-resource-backups")
WARNING = (
    "Parar los bots antiguos y el dashboard NO vende ni cierra posiciones y "
    "detiene su vigilancia automatica. Las salidas del bot principal se conservan."
)


@dataclass(frozen=True)
class Process:
    pid: int
    start: str
    cwd: str
    argv: tuple[str, ...]
    cgroups: tuple[str, ...]


def read_process(proc: Path, pid: int) -> Process | None:
    """Read only /proc; vanished or unreadable processes cannot be targeted."""
    entry = proc / str(pid)
    try:
        return Process(
            pid,
            entry.joinpath("stat").read_text().rsplit(")", 1)[1].split()[19],
            str((entry / "cwd").readlink()),
            tuple(
                os.fsdecode(x)
                for x in (entry / "cmdline").read_bytes().split(b"\0")
                if x
            ),
            tuple((entry / "cgroup").read_text().splitlines()),
        )
    except (OSError, IndexError, UnicodeError):
        return None


def is_main(process: Process) -> bool:
    args = process.argv
    if args and Path(args[0]).name.startswith("python"):
        args = args[1:]
        if args[:1] == ("-u",):
            args = args[1:]
    return process.cwd == MAIN_CWD and args in (
        ("run_four_strategies.py", "--run", "--only-dev"),
        (f"{MAIN_CWD}/run_four_strategies.py", "--run", "--only-dev"),
    )


def is_manual(process: Process) -> bool:
    """Accept exactly one import and one call; never substring-match Python code."""
    if (
        process.pid == PARENT_PID
        or process.cwd != OLD_CWD
        or process.argv[:3] != (f"{OLD_CWD}/.venv/bin/python", "-u", "-c")
        or len(process.argv) != 4  # noqa: PLR2004 - executable, -u, -c, code
    ):
        return False
    try:
        body = ast.parse(process.argv[3]).body
        if len(body) != 2 or not isinstance(body[1], ast.Expr):  # noqa: PLR2004 - import then call
            return False
        imported, call = body[0], body[1].value
        if isinstance(imported, ast.Import) and len(imported.names) == 1:
            alias = imported.names[0]
            if alias.name != "bot_runner":
                return False
            expected = f"{alias.asname or alias.name}.run_bot_process"
        elif (
            isinstance(imported, ast.ImportFrom)
            and imported.module == "bot_runner"
            and imported.level == 0
            and len(imported.names) == 1
            and imported.names[0].name == "run_bot_process"
        ):
            expected = imported.names[0].asname or "run_bot_process"
        else:
            return False
        return (
            isinstance(call, ast.Call)
            and ast.unparse(call.func) == expected
            and not call.keywords
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
            and posixpath.normpath(posixpath.join(OLD_CWD, call.args[0].value))
            == f"{OLD_CWD}/bots/bot-paper-dev1-bloque2.yaml"
        )
    except (SyntaxError, ValueError, TypeError):
        return False


def processes(proc: Path) -> list[Process]:
    return [
        p
        for entry in proc.iterdir()
        if entry.name.isdecimal()
        if (p := read_process(proc, int(entry.name))) is not None
    ]


def systemctl(*args: str) -> tuple[int, str]:
    """Only return a small state vocabulary, never raw stderr or exception text."""
    states = {
        "loaded",
        "not-found",
        "masked",
        "enabled",
        "enabled-runtime",
        "disabled",
        "static",
        "indirect",
        "generated",
        "transient",
        "linked",
        "linked-runtime",
        "alias",
        "active",
        "inactive",
        "failed",
        "activating",
        "deactivating",
        "reloading",
        "maintenance",
        "refreshing",
    }
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable; no shell
            ["/usr/bin/systemctl", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return -1, "command_unavailable_or_timeout"
    else:
        output = result.stdout.strip()
        return result.returncode, output if output in states else "unknown"


def unit_states() -> dict[str, dict[str, str]]:
    result = {}
    for unit in UNITS:
        _, loaded = systemctl("show", "--property=LoadState", "--value", unit)
        if loaded == "not-found":
            result[unit] = {"load": "not-found"}
        else:
            result[unit] = {
                "load": loaded,
                "enabled": systemctl("is-enabled", unit)[1],
                "active": systemctl("is-active", unit)[1],
            }
    return result


def key_fingerprints(keys: tuple[Path, ...]) -> tuple[bytes, ...]:
    # Deliberately memory-only: neither bytes nor hashes enter a report or backup.
    return tuple(hashlib.sha256(key.read_bytes()).digest() for key in keys)


def backup_plan(plan: dict, root: Path) -> str:
    directory = root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(mode=0o700, parents=True)
    target = directory / "retirada-consumidores.json"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
    return str(target)


def stop_manual(process: Process, proc: Path) -> str:
    # There is no racy os.kill fallback and no signal to a shell or parent.
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return "skipped_pidfd_unavailable"
    try:
        descriptor = os.pidfd_open(process.pid)
    except OSError:
        return "skipped_pidfd_open_failed"
    try:
        if read_process(proc, process.pid) != process:
            return "skipped_changed_or_gone"
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        for _ in range(30):
            if read_process(proc, process.pid) != process:
                return "stopped"
            time.sleep(0.1)
    except OSError:
        return "signal_failed"
    else:
        return "termination_pending"
    finally:
        os.close(descriptor)


# Keep the ordered safety checks and partial-result handling visible in one place.
def execute(  # noqa: C901, PLR0912, PLR0915
    *,
    apply: bool = False,
    proc: Path = Path("/proc"),
    keys: tuple[Path, ...] = KEYS,
    backups: Path = BACKUPS,
) -> dict:
    report = {
        "mode": "apply" if apply else "preview",
        "warning": WARNING,
        "status": "refused",
        "unit_results": {},
        "manual_results": {},
    }
    fingerprints = None
    try:
        fingerprints = key_fingerprints(keys)
        found = processes(proc)
        mains = [p for p in found if is_main(p)]
        if len(mains) != 1:
            report["error"] = "protected_main_count_must_be_exactly_one"
            return report
        main = mains[0]
        manual = [p for p in found if is_manual(p)]
        states = unit_states()
        report.update(
            main={"pid": main.pid, "starttime": main.start, "cwd": main.cwd},
            manual_pids=[p.pid for p in manual],
            units=states,
        )
        if not apply:
            report["status"] = "preview"
            return report
        if any(unit in group.split("/") for group in main.cgroups for unit in UNITS):
            report["error"] = "protected_main_in_target_unit"
            return report
        if any(
            state not in {"not-found", "loaded", "masked"}
            if name == "load"
            else state in {"unknown", "command_unavailable_or_timeout"}
            for unit in states.values()
            for name, state in unit.items()
        ):
            report["error"] = "unit_state_unavailable"
            return report
        if read_process(proc, main.pid) != main:
            report["error"] = "protected_main_changed"
            return report
        report["backup"] = backup_plan(report, backups)
        report["status"] = "complete"
        for unit, state in states.items():
            if state["load"] == "not-found":
                report["unit_results"][unit] = "skipped_missing"
                continue
            if read_process(proc, main.pid) != main:
                report.update(status="partial", error="protected_main_changed")
                break
            code, _ = systemctl("disable", "--now", unit)
            report["unit_results"][unit] = (
                "stopped_disabled" if code == 0 else "stop_failed"
            )
            if code != 0:
                report["status"] = "partial"
                break
        if report["status"] == "complete":
            for process in manual:
                if read_process(proc, main.pid) != main:
                    report.update(status="partial", error="protected_main_changed")
                    break
                outcome = stop_manual(process, proc)
                report["manual_results"][str(process.pid)] = outcome
                if outcome != "stopped":
                    report["status"] = "partial"
        if read_process(proc, main.pid) != main:
            report.update(status="partial", error="protected_main_changed")
    except (OSError, ValueError):
        report.update(
            status="partial" if "backup" in report else "refused",
            error="local_read_or_backup_failed",
        )
    finally:
        try:
            unchanged = key_fingerprints(keys) == fingerprints
        except OSError:
            unchanged = False
        report["api_keys_unchanged"] = unchanged
        if not unchanged:
            report.update(
                status="partial" if "backup" in report else "refused",
                error="api_keys_unavailable_or_changed",
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Ejecucion manual: parar y deshabilitar solo la lista fija",
    )
    args = parser.parse_args()
    if args.apply and (sys.platform != "linux" or os.geteuid() != 0):
        print(json.dumps({"status": "refused", "error": "apply_requires_linux_root"}))
        return 2
    report = execute(apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"preview", "complete"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
