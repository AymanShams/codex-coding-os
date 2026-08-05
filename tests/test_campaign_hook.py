from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = ROOT / "hooks" / "campaign-engine" / "campaign_hook.py"
SPEC = importlib.util.spec_from_file_location("campaign_hook", HOOK_PATH)
assert SPEC is not None and SPEC.loader is not None
hook = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hook
SPEC.loader.exec_module(hook)


EXACT_IDENTITY = {
    "CCOS_CAMPAIGN_ID": "campaign-1",
    "CCOS_ACTOR_ID": "actor-1",
    "CCOS_LEASE_ID": "lease-1",
    "CCOS_AUTHORITY_EPOCH": "7",
    "CCOS_CANCELLATION_EPOCH": "2",
    "CCOS_FENCING_EPOCH": "11",
    "CCOS_HOOK_ACTION": "APPLY_PATCH",
}


class CampaignHookTests(unittest.TestCase):
    def test_missing_or_partial_identity_is_an_unconditional_noop(self) -> None:
        for environment in ({}, {"CCOS_CAMPAIGN_ID": "campaign-1"}, EXACT_IDENTITY):
            with self.subTest(environment=sorted(environment)), mock.patch.dict(
                os.environ, environment, clear=True
            ), mock.patch.object(hook.subprocess, "run") as run:
                self.assertEqual(hook.main(), 0)
                run.assert_not_called()

    def test_complete_identity_delegates_exact_tuple_to_installed_engine(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-hook-test-") as raw:
            root = Path(raw)
            codex_home = root / "codex"
            repository = root / "repository"
            cli = (
                codex_home
                / "coding-os"
                / "scripts"
                / "agent"
                / "campaign_engine"
                / "cli.py"
            )
            cli.parent.mkdir(parents=True)
            cli.write_text("# installed engine\n", encoding="utf-8")
            repository.mkdir()
            environment = {
                **EXACT_IDENTITY,
                "CCOS_REPOSITORY_ROOT": str(repository),
                "CCOS_HOOK_PATH": "src/allowed.py",
                "CODEX_HOME": str(codex_home),
            }
            completed = subprocess.CompletedProcess([], 23)
            with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
                hook.subprocess, "run", return_value=completed
            ) as run:
                self.assertEqual(hook.main(), 23)

            arguments = run.call_args.args[0]
            self.assertEqual(arguments[:4], (sys.executable, "-B", str(cli), "--json"))
            self.assertIn("authorize-action", arguments)
            expected = {
                "--campaign-id": "campaign-1",
                "--actor-id": "actor-1",
                "--lease-id": "lease-1",
                "--authority-epoch": "7",
                "--cancellation-epoch": "2",
                "--fencing-epoch": "11",
                "--action": "APPLY_PATCH",
                "--repository-root": str(repository),
                "--path": "src/allowed.py",
            }
            for option, value in expected.items():
                index = arguments.index(option)
                self.assertEqual(arguments[index + 1], value)
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_complete_identity_fails_closed_when_installed_engine_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-hook-test-") as raw:
            repository = Path(raw) / "repository"
            repository.mkdir()
            environment = {
                **EXACT_IDENTITY,
                "CCOS_REPOSITORY_ROOT": str(repository),
                "CODEX_HOME": str(Path(raw) / "codex"),
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                self.assertEqual(hook.main(), 78)


if __name__ == "__main__":
    unittest.main()
