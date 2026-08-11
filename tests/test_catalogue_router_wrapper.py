#!/usr/bin/env python3
"""Contract tests for the public Catalogue Router skill and CLI wrapper."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / ".agents" / "skills" / "catalogue-router"
SKILL_PATH = SKILL_ROOT / "SKILL.md"
WRAPPER_PATH = SKILL_ROOT / "scripts" / "query-catalogue.ps1"


class CatalogueRouterWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos-catalogue-wrapper-")
        self.root = Path(self.temp.name)
        self.codex_home = self.root / "codex-home"
        self.hooks_dir = self.codex_home / "hooks"
        self.hooks_dir.mkdir(parents=True)
        self.workspace = self.root / "sample-workspace"
        self.workspace.mkdir()
        self.task_input_path = self.root / "task-input.json"
        self.task_input_path.write_text(
            json.dumps(
                {
                    "instruction": "Route this synthetic bounded task.",
                    "execution_request_id": "synthetic-wrapper-request",
                    "execution_disposition": {
                        "mode": "codex_only",
                        "eligible_worker_families": [],
                    },
                }
            ),
            encoding="utf-8",
        )
        self.fake_cli = self.hooks_dir / "capability_index_cli.py"
        self.fake_cli.write_text(
            "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n",
            encoding="utf-8",
        )
        self.shell = shutil.which("pwsh") or shutil.which("powershell")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run_wrapper(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        if self.shell is None:
            self.skipTest("PowerShell is not available")
        env = os.environ.copy()
        env.update(
            {
                "CODEX_HOME": str(self.codex_home),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return subprocess.run(
            [
                self.shell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(WRAPPER_PATH),
                *arguments,
            ],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_skill_declares_single_canonical_authority_and_fail_closed_contract(
        self,
    ) -> None:
        skill = SKILL_PATH.read_text(encoding="utf-8")
        for required in (
            "active-capabilities.json",
            "routing-policy.yaml",
            "route-decisions.sqlite3",
            "task_input_sha256",
            "execution_disposition",
            "requires_live_dependencies",
            "retired non-live",
        ):
            self.assertIn(required, skill)

        wrapper = WRAPPER_PATH.read_text(encoding="utf-8")
        for required in (
            "TaskInputPath",
            "CODEX_HOME",
            "capability_index_cli.py",
            "--task-input-json",
            "ExecutionDisposition",
            "EligibleWorkerFamily",
            "BacklogOnly",
        ):
            self.assertIn(required, wrapper)
        self.assertNotIn(
            "Select-String -LiteralPath $Catalogue -Pattern $Query", wrapper
        )

    def test_wrapper_forwards_complete_task_gate_input_to_canonical_cli(self) -> None:
        completed = self._run_wrapper(
            "-Query",
            "synthetic wrapper routing",
            "-TaskInputPath",
            str(self.task_input_path),
            "-Json",
            "-ProjectId",
            "sample_project",
            "-Cwd",
            str(self.workspace),
            "-TaskType",
            "review",
            "-Complexity",
            "high",
            "-SourceNeed",
            "index",
            "-SourceScope",
            "sample_project",
            "-ClassificationFlag",
            "synthetic_review",
            "-ExecutionDisposition",
            "codex_only",
            "-ExactEvidence",
            "-MemoryMode",
            "none",
            "-PersistenceIntent",
            "none",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        forwarded = json.loads(completed.stdout)["argv"]
        expected_pairs = {
            "--query": "synthetic wrapper routing",
            "--task-input-json": str(self.task_input_path),
            "--project-id": "sample_project",
            "--cwd": str(self.workspace),
            "--task-type": "review",
            "--complexity": "high",
            "--source-need": "index",
            "--source-scope": "sample_project",
            "--classification-flag": "synthetic_review",
            "--execution-disposition": "codex_only",
            "--memory-mode": "none",
            "--persistence-intent": "none",
        }
        for option, value in expected_pairs.items():
            position = forwarded.index(option)
            self.assertEqual(forwarded[position + 1], value)
        self.assertIn("--json", forwarded)
        self.assertIn("--exact-evidence", forwarded)

    def test_wrapper_rejects_legacy_text_with_complete_task_input(self) -> None:
        completed = self._run_wrapper(
            "-Query",
            "synthetic wrapper routing",
            "-TaskInputPath",
            str(self.task_input_path),
            "-TaskText",
            "legacy task text",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("cannot be combined", completed.stderr)


if __name__ == "__main__":
    unittest.main()
