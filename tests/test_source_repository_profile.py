#!/usr/bin/env python3
"""Source-only contracts for the Coding OS campaign-engine adapter."""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = ROOT / "scripts" / "agent" / "campaign_engine"
INCIDENT_ROOT = ROOT / "tests" / "fixtures" / "incidents"


class SourceRepositoryProfileTests(unittest.TestCase):
    """These checks inspect committed code and fixtures, never host state."""

    def test_single_campaign_engine_and_retired_legacy_cli_are_explicit(self) -> None:
        expected_modules = {
            "__init__.py",
            "__main__.py",
            "admission.py",
            "cli.py",
            "ed25519.py",
            "effects.py",
            "evidence.py",
            "host.py",
            "legacy.py",
            "model.py",
            "reducer.py",
            "runtime_bootstrap.py",
            "store.py",
            "supervisor.py",
        }
        self.assertEqual(expected_modules, {path.name for path in ENGINE_ROOT.glob("*.py")})

        reducers = []
        for path in (ROOT / "scripts" / "agent").rglob("*.py"):
            module = ast.parse(path.read_text(encoding="utf-8"))
            if any(isinstance(node, ast.FunctionDef) and node.name == "reduce" for node in ast.walk(module)):
                reducers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(["scripts/agent/campaign_engine/reducer.py"], reducers)

        legacy_cli = (ROOT / "scripts" / "agent" / "case_state.py").read_text(encoding="utf-8")
        self.assertIn('LEGACY_ENGINE_RETIRED = "LEGACY_ENGINE_RETIRED"', legacy_cli)
        self.assertIn('"read_only_legacy_command": "campaign_engine.cli legacy inspect"', legacy_cli)
        self.assertIn("return 78", legacy_cli)
        self.assertNotIn("def reduce(", legacy_cli)

    def test_cli_exposes_current_public_commands_and_no_retired_mutations(self) -> None:
        cli = (ENGINE_ROOT / "cli.py").read_text(encoding="utf-8")
        for command in ("admit", "approve", "run", "status", "cancel", "reconcile", "doctor", "legacy"):
            self.assertIn(f'sub.add_parser("{command}")', cli)
        self.assertIn('legacy_sub.add_parser("inspect")', cli)
        for retired in ("action-check", "transition", "case-runtime", "activate_anti_loop"):
            self.assertNotIn(retired, cli)

    def test_formal_model_and_paired_incident_corpus_are_complete(self) -> None:
        formal = (ROOT / "formal" / "Campaign.tla").read_text(encoding="utf-8")
        config = (ROOT / "formal" / "Campaign.cfg").read_text(encoding="utf-8")
        self.assertIn("MODULE Campaign", formal)
        self.assertIn("Spec == Init /\\ [][Next]_vars", formal)
        self.assertIn("CancelledIsTerminal", formal)
        self.assertIn("INVARIANTS", config)
        self.assertIn("TypeInvariant", config)

        corpus = json.loads((INCIDENT_ROOT / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(1, corpus["schema_version"])
        incidents = corpus["incidents"]
        self.assertGreaterEqual(len(incidents), 1)
        self.assertEqual(len(incidents), len({incident["incident_id"] for incident in incidents}))
        for incident in incidents:
            with self.subTest(incident=incident["incident_id"]):
                self.assertTrue((INCIDENT_ROOT / incident["historical_fixture"]).is_file())
                self.assertTrue((INCIDENT_ROOT / incident["opposite_fixture"]).is_file())
                self.assertTrue(incident["requirement"])

    def test_repository_mirrors_are_informational_only(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        volatile_fixture = json.loads(
            (INCIDENT_ROOT / "volatile-state-gate" / "historical.json").read_text(encoding="utf-8")
        )
        self.assertIn("do not authorize or block work", readme)
        self.assertIn(
            "Repository mirrors, handoffs, current-state files, and manifests are informational only.",
            " ".join(agents.split()),
        )
        self.assertTrue(volatile_fixture["expected"]["repository_state_ignored"])

    def test_retired_runtime_files_are_not_present(self) -> None:
        for path in (
            "hooks/anti-loop-runtime/anti_loop_runtime.py",
            "hooks/worktree-lane-pre-commit.py",
            "hooks/worktree-lane-pre-push.py",
            "scripts/activate_anti_loop.py",
            "scripts/agent/case_runtime_supervisor.py",
            "scripts/agent/worktree_lanes.py",
        ):
            self.assertFalse((ROOT / path).exists(), path)


if __name__ == "__main__":
    unittest.main()
