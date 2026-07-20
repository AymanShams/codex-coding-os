#!/usr/bin/env python3
"""Focused contract tests for typed validation evidence."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "agent" / "validation_evidence.py"
EXAMPLE = ROOT / "templates" / "validation-evidence.example.json"


def run(command: list[str], cwd: Path, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != expected:
        raise AssertionError(
            f"expected exit {expected}, got {result.returncode}\n"
            f"command: {command}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def git(cwd: Path, *args: str) -> str:
    return run(["git", *args], cwd).stdout.strip()


def init_repo(root: Path) -> str:
    git(root, "init")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic Test")
    git(root, "remote", "add", "origin", "https://example.invalid/example/synthetic-validation.git")
    (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "synthetic baseline")
    return git(root, "rev-parse", "HEAD")


def evidence(head: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_id": "synthetic-command-pass-001",
        "proof_type": "COMMAND_EXIT",
        "result": "PASS",
        "scope": {"kind": "COMMAND", "targets": ["python -B tests/synthetic.py"]},
        "repository": "https://example.invalid/example/synthetic-validation.git",
        "exact_head": head,
        "environment": {
            "platform": "synthetic-platform",
            "runtimes": ["Python synthetic"],
            "working_tree": "clean",
        },
        "authority": {
            "source_type": "RUN_ENVELOPE",
            "reference": "synthetic-run-envelope",
            "scope": "validation only",
        },
        "observations": [
            {"command": "python -B tests/synthetic.py", "exit_code": 0, "fact": "Synthetic checks exited zero."}
        ],
        "proves": ["The named synthetic command exited zero at the exact recorded head."],
        "does_not_prove": ["It does not authorize merge, deployment, release, or lifecycle closure."],
    }


def validate(root: Path, path: Path, expected: int = 0) -> dict[str, object]:
    result = run(
        [sys.executable, "-B", str(VALIDATOR), "validate", "--file", str(path), "--repo-root", ".", "--json"],
        root,
        expected,
    )
    return json.loads(result.stdout)


class ValidationEvidenceTests(unittest.TestCase):
    def test_synthetic_example_is_contract_valid_but_never_claims_truth_or_authority(self) -> None:
        self.assertTrue(EXAMPLE.exists())
        example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertIn("example.invalid", example["repository"])
        self.assertNotIn("Ayman", json.dumps(example))

        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = init_repo(repo)
            example["exact_head"] = head
            evidence_path = repo / "evidence.json"
            evidence_path.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
            git(repo, "add", "evidence.json")
            git(repo, "commit", "-m", "add synthetic evidence")
            new_head = git(repo, "rev-parse", "HEAD")
            example["exact_head"] = new_head
            evidence_path.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
            git(repo, "add", "evidence.json")
            git(repo, "commit", "--amend", "--no-edit")
            # The evidence file cannot self-record its own commit without changing it.
            # Validate an untracked external copy against the clean committed repository.
            final_head = git(repo, "rev-parse", "HEAD")
            external = repo.parent / f"{repo.name}-evidence.json"
            try:
                example["exact_head"] = final_head
                external.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
                output = validate(repo, external)
            finally:
                external.unlink(missing_ok=True)

            self.assertTrue(output["valid"])
            self.assertTrue(output["identity_match"])
            self.assertTrue(output["head_match"])
            self.assertTrue(output["working_tree_match"])
            self.assertFalse(output["claim_truth_assessed"])
            self.assertFalse(output["lifecycle_authority"])
            self.assertEqual(output["protocol_version"], 1)

    def test_abbreviated_or_stale_head_unknown_enum_and_dirty_pass_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = init_repo(repo)
            evidence_path = repo.parent / f"{repo.name}-evidence.json"
            try:
                cases = []
                abbreviated = evidence(head)
                abbreviated["exact_head"] = head[:12]
                cases.append(("abbreviated", abbreviated, "full lowercase"))

                stale = evidence("0" * 40)
                cases.append(("stale", stale, "exact_head does not match"))

                unknown = evidence(head)
                unknown["proof_type"] = "PROSE_ASSERTION"
                cases.append(("unknown", unknown, "proof_type"))

                for name, payload, expected_error in cases:
                    with self.subTest(name=name):
                        evidence_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                        output = validate(repo, evidence_path, expected=2)
                        self.assertFalse(output["valid"])
                        self.assertIn(expected_error, "\n".join(output["errors"]))
                        self.assertFalse(output["claim_truth_assessed"])
                        self.assertFalse(output["lifecycle_authority"])

                (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
                evidence_path.write_text(json.dumps(evidence(head), indent=2) + "\n", encoding="utf-8")
                output = validate(repo, evidence_path, expected=2)
                self.assertFalse(output["working_tree_match"])
                self.assertIn("PASS evidence requires an actually clean working tree", "\n".join(output["errors"]))
            finally:
                evidence_path.unlink(missing_ok=True)

    def test_validator_never_executes_observation_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = init_repo(repo)
            marker = repo / "must-not-exist.txt"
            payload = evidence(head)
            payload["observations"] = [
                {
                    "command": f"write forbidden marker {marker.name}",
                    "exit_code": 0,
                    "fact": "This is inert recorded text.",
                }
            ]
            evidence_path = repo.parent / f"{repo.name}-evidence.json"
            try:
                evidence_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                validate(repo, evidence_path)
                self.assertFalse(marker.exists())
            finally:
                evidence_path.unlink(missing_ok=True)

    def test_credential_free_https_identity_matches_ssh_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = init_repo(repo)
            git(repo, "remote", "set-url", "origin", "git@example.invalid:example/synthetic-validation.git")
            evidence_path = repo.parent / f"{repo.name}-evidence.json"
            try:
                evidence_path.write_text(json.dumps(evidence(head), indent=2) + "\n", encoding="utf-8")
                output = validate(repo, evidence_path)
                self.assertTrue(output["identity_match"])
                self.assertNotIn("git@", output["live_repository"])
            finally:
                evidence_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
