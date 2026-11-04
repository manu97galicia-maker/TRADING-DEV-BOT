"""All process/service operations are mocked; fixtures never touch live /proc."""

import hashlib
import json
import signal
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import retirar_bots_antiguos as cleanup


class RemoveOldConsumersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.keys = (self.root / ".env", self.root / ".env.four-strategies")
        for key in self.keys:
            key.write_bytes(b"HELIUS_API_KEY=never-output-this-secret\n")
        self.backups = self.root / "backups"
        self.add_process(
            20,
            cleanup.MAIN_CWD,
            (
                "/root/ai-bot-four-strategies/.venv/bin/python",
                "-u",
                "run_four_strategies.py",
                "--run",
                "--only-dev",
            ),
        )
        self.code = (
            "from bot_runner import run_bot_process; "
            "run_bot_process('bots/bot-paper-dev1-bloque2.yaml')"
        )
        self.add_process(
            99,
            cleanup.OLD_CWD,
            (f"{cleanup.OLD_CWD}/.venv/bin/python", "-u", "-c", self.code),
        )
        self.raw_systemctl = cleanup.systemctl
        self.control = self.stack.enter_context(
            patch.object(cleanup, "systemctl", side_effect=self.unit_state)
        )
        self.stack.enter_context(patch.object(Path, "readlink", lambda p: p.read_text()))
        self.pidfd = self.stack.enter_context(
            patch.object(cleanup.os, "pidfd_open", return_value=800, create=True)
        )
        self.send = self.stack.enter_context(
            patch.object(cleanup.signal, "pidfd_send_signal", create=True)
        )
        self.stack.enter_context(patch.object(cleanup.os, "close"))

    def add_process(
        self, pid: int, cwd: str, argv: tuple[str, ...], start: str = "100"
    ) -> None:
        entry = self.proc / str(pid)
        entry.mkdir(exist_ok=True)
        (entry / "stat").write_text(
            f"{pid} (python) " + " ".join(["S", *(["0"] * 18), start])
        )
        (entry / "cwd").write_text(cwd)
        (entry / "cmdline").write_bytes(b"\0".join(x.encode() for x in argv) + b"\0")
        (entry / "cgroup").write_text("0::/system.slice/protected.service\n")

    @staticmethod
    def unit_state(*args: str) -> tuple[int, str]:
        return 0, {
            "show": "loaded",
            "is-enabled": "enabled",
            "is-active": "active",
            "disable": "unknown",
        }[args[0]]

    def execute(self, *, apply: bool = False) -> dict:
        return cleanup.execute(
            apply=apply, proc=self.proc, keys=self.keys, backups=self.backups
        )

    def mutation_calls(self) -> list[tuple[str, ...]]:
        return [
            call.args
            for call in self.control.call_args_list
            if call.args[0] == "disable"
        ]

    def test_preview_has_no_mutations(self) -> None:
        with patch.object(cleanup, "backup_plan") as backup:
            report = self.execute()
        self.assertEqual(report["status"], "preview")
        self.assertTrue(report["api_keys_unchanged"])
        self.assertEqual(report["manual_pids"], [99])
        self.assertEqual(self.mutation_calls(), [])
        self.assertFalse(self.backups.exists())
        backup.assert_not_called()
        self.pidfd.assert_not_called()
        self.send.assert_not_called()

    def test_absent_or_ambiguous_main_refuses_before_backup(self) -> None:
        original = cleanup.read_process(self.proc, 20)
        with patch.object(cleanup, "backup_plan") as backup:
            for found in ([], [original, original]):
                with (
                    self.subTest(found=found),
                    patch.object(cleanup, "processes", return_value=found),
                ):
                    self.assertEqual(self.execute(apply=True)["status"], "refused")
            backup.assert_not_called()
        self.assertEqual(self.mutation_calls(), [])
        self.send.assert_not_called()

    def test_only_exact_safe_manual_programs_match(self) -> None:
        valid = [
            self.code,
            "import bot_runner; bot_runner.run_bot_process('/root/ai-bot/bots/bot-paper-dev1-bloque2.yaml')",
            "import bot_runner as b; b.run_bot_process('./bots/bot-paper-dev1-bloque2.yaml')",
        ]
        invalid = [
            self.code + "; print('other work')",
            self.code.replace("bloque2", "bloque3"),
            "import os; os.system('echo unsafe')",
            "import bot_runner; other()",
            self.code.replace("run_bot_process('bots", "run_bot_process(path='bots"),
            "from bot_runner import *; run_bot_process('bots/bot-paper-dev1-bloque2.yaml')",
        ]
        for code in valid + invalid:
            process = cleanup.Process(
                99,
                "100",
                cleanup.OLD_CWD,
                (f"{cleanup.OLD_CWD}/.venv/bin/python", "-u", "-c", code),
                (),
            )
            with self.subTest(code=code):
                self.assertEqual(cleanup.is_manual(process), code in valid)
        parent = cleanup.Process(
            1175768,
            "100",
            cleanup.OLD_CWD,
            (f"{cleanup.OLD_CWD}/.venv/bin/python", "-u", "-c", self.code),
            (),
        )
        self.assertFalse(cleanup.is_manual(parent))

    def test_stale_manual_pid_is_not_signalled(self) -> None:
        def change_after_backup(*_args: object) -> str:
            (self.proc / "99" / "stat").write_text(
                "99 (python) " + " ".join(["S", *(["0"] * 18), "999"])
            )
            return "mock-backup"

        with patch.object(cleanup, "backup_plan", side_effect=change_after_backup):
            report = self.execute(apply=True)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["manual_results"], {"99": "skipped_changed_or_gone"})
        self.pidfd.assert_called_once_with(99)
        self.send.assert_not_called()

    def test_only_allowlisted_units_and_missing_units_skipped(self) -> None:
        missing = cleanup.UNITS[0]
        self.control.side_effect = lambda *args: (
            (0, "not-found")
            if args[0] == "show" and args[-1] == missing
            else self.unit_state(*args)
        )
        self.send.side_effect = lambda *_args: (self.proc / "99" / "cmdline").unlink()
        report = self.execute(apply=True)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["unit_results"][missing], "skipped_missing")
        self.assertEqual(
            self.mutation_calls(),
            [("disable", "--now", unit) for unit in cleanup.UNITS[1:]],
        )
        self.send.assert_called_once_with(800, signal.SIGTERM)
        saved = json.loads(Path(report["backup"]).read_text())
        self.assertEqual(saved["units"][missing], {"load": "not-found"})
        self.assertEqual(cleanup.read_process(self.proc, 20).start, "100")

    def test_main_in_target_cgroup_refuses(self) -> None:
        (self.proc / "20" / "cgroup").write_text(
            f"0::/system.slice/{cleanup.UNITS[0]}\n"
        )
        self.assertEqual(
            self.execute(apply=True)["error"], "protected_main_in_target_unit"
        )
        self.assertEqual(self.mutation_calls(), [])
        self.assertFalse(self.backups.exists())

    def test_stop_failure_reports_partial_without_restart(self) -> None:
        self.control.side_effect = lambda *args: (
            (1, "unknown") if args[0] == "disable" else self.unit_state(*args)
        )
        report = self.execute(apply=True)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(
            self.mutation_calls(), [("disable", "--now", cleanup.UNITS[0])]
        )
        self.send.assert_not_called()

    def test_missing_pidfd_refuses_manual_signal(self) -> None:
        process = cleanup.read_process(self.proc, 99)
        with patch.object(cleanup, "os", SimpleNamespace()):
            self.assertEqual(
                cleanup.stop_manual(process, self.proc), "skipped_pidfd_unavailable"
            )
        self.send.assert_not_called()

    def test_key_values_and_hashes_never_enter_reports_or_backups(self) -> None:
        self.send.side_effect = lambda *_args: (self.proc / "99" / "cmdline").unlink()
        report = self.execute(apply=True)
        output = json.dumps(report) + Path(report["backup"]).read_text()
        for key in self.keys:
            self.assertNotIn(key.read_text(), output)
            self.assertNotIn("never-output-this-secret", output)
            self.assertNotIn(hashlib.sha256(key.read_bytes()).hexdigest(), output)
        self.assertTrue(report["api_keys_unchanged"])

    def test_systemctl_errors_are_sanitized(self) -> None:
        # Call the original function without accessing systemctl or any service.
        with patch.object(
            cleanup.subprocess,
            "run",
            return_value=SimpleNamespace(
                returncode=1, stdout="SECRET_TOKEN", stderr="SECRET_TOKEN"
            ),
        ):
            self.assertEqual(
                self.raw_systemctl("is-active", cleanup.UNITS[0]), (1, "unknown")
            )

    def test_main_changed_after_backup_prevents_any_stop(self) -> None:
        def change_after_backup(*_args: object) -> str:
            (self.proc / "20" / "cmdline").write_bytes(b"different-program\0")
            return "mock-backup"

        with patch.object(cleanup, "backup_plan", side_effect=change_after_backup):
            report = self.execute(apply=True)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["error"], "protected_main_changed")
        self.assertEqual(self.mutation_calls(), [])
        self.send.assert_not_called()

    def test_missing_key_refuses_and_changed_key_is_reported(self) -> None:
        original = self.keys[0].read_bytes()
        self.keys[0].unlink()
        report = self.execute(apply=True)
        self.assertEqual(report["error"], "api_keys_unavailable_or_changed")
        self.assertEqual(self.mutation_calls(), [])
        self.keys[0].write_bytes(original)

        def change_after_backup(*_args: object) -> str:
            self.keys[0].write_bytes(b"changed-only-in-test")
            return "mock-backup"

        self.send.side_effect = lambda *_args: (self.proc / "99" / "cmdline").unlink()
        with patch.object(cleanup, "backup_plan", side_effect=change_after_backup):
            report = self.execute(apply=True)
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["api_keys_unchanged"])
        self.assertNotIn("changed-only-in-test", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
