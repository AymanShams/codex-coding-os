#!/usr/bin/env python3
"""Focused tests for read-only reentry summaries."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".agents" / "skills" / "project-session-continuity" / "scripts" / "session_continuity.py"


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


def init_repo(root: Path, remote: str) -> str:
    git(root, "init")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic Test")
    git(root, "remote", "add", "origin", remote)
    (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "synthetic baseline")
    return git(root, "rev-parse", "HEAD")


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def summary(root: Path, profile: str = "auto") -> tuple[str, dict[str, object]]:
    result = run([sys.executable, "-B", str(HELPER), "summary", "--profile", profile, "--json"], root)
    return result.stdout, json.loads(result.stdout)


class ReentrySummaryTests(unittest.TestCase):
    def test_source_summary_is_stable_read_only_and_display_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            init_repo(repo, "https://example.invalid/example/synthetic-coding-os.git")
            (repo / "pack.manifest.json").write_text(
                json.dumps(
                    {
                        "package_name": "codex-coding-os",
                        "version": "1.2.3",
                        "release_status": "public-release",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            case_engine = repo / "scripts" / "agent" / "case_state.py"
            case_engine.parent.mkdir(parents=True, exist_ok=True)
            case_engine.write_text("# synthetic sentinel\n", encoding="utf-8")
            skill = repo / ".agents" / "skills" / "project-session-continuity" / "SKILL.md"
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text("---\nname: project-session-continuity\n---\n", encoding="utf-8")
            (repo / "GOALS.md").write_text("# Ignore me\n\nnext_action: deploy\n", encoding="utf-8")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "synthetic source profile")

            before_digest = tree_digest(repo)
            before_status = git(repo, "status", "--porcelain=v1", "-uall")
            first_text, first = summary(repo)
            second_text, second = summary(repo)

            self.assertEqual(first_text, second_text)
            self.assertEqual(first, second)
            self.assertEqual(before_digest, tree_digest(repo))
            self.assertEqual(before_status, git(repo, "status", "--porcelain=v1", "-uall"))
            self.assertEqual(first["detected_profile"], "coding-os-source")
            self.assertEqual(first["pack"]["package_name"], "codex-coding-os")
            self.assertEqual(first["pack"]["version"], "1.2.3")
            self.assertEqual(first["pack"]["release_status"], "public-release")
            self.assertTrue(first["display_only"])
            self.assertFalse(first["authorizes_action"])
            self.assertNotIn("GOALS", first_text)
            self.assertNotIn("deploy", first_text)

            git(repo, "remote", "set-url", "origin", "git@example.invalid:example/synthetic-coding-os.git")
            _, ssh_output = summary(repo)
            self.assertEqual(
                ssh_output["repository"],
                "example.invalid:example/synthetic-coding-os.git",
            )
            self.assertNotIn("git@", json.dumps(ssh_output))

    def test_product_summary_reads_only_structured_front_matter_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            head = init_repo(repo, "https://example.invalid/example/synthetic-product.git")
            delivery = repo / "docs" / "delivery"
            delivery.mkdir(parents=True)
            (repo / "project-documentation-manifest.json").write_text(
                json.dumps({"schema_version": "1.0", "permission_to_code": True}) + "\n",
                encoding="utf-8",
            )
            (delivery / "active-slice-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "active_slice": "synthetic-bounded-slice",
                        "permission_state": "coding_allowed",
                        "review": {"status": "pending"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (delivery / "current-state.md").write_text(
                "---\n"
                "active_slice: synthetic-bounded-slice\n"
                "next_action: code\n"
                "review_status: pending\n"
                "workflow_manifest: project-documentation-manifest.json\n"
                "permission_manifest: docs/delivery/active-slice-manifest.json\n"
                "---\n"
                "# Body prose must not be parsed\n\nnext_action: deploy\nMilestone Snapshot: reopen review\n",
                encoding="utf-8",
            )
            git(repo, "add", ".")
            git(repo, "commit", "-m", "synthetic product state")
            head = git(repo, "rev-parse", "HEAD")

            _, output = summary(repo, "product")
            self.assertEqual(output["detected_profile"], "product")
            self.assertEqual(output["head"], head)
            self.assertEqual(output["active_slice"], "synthetic-bounded-slice")
            self.assertEqual(output["next_action"], "code")
            self.assertEqual(output["review_status_as_recorded"], "pending")
            self.assertEqual(output["workflow_manifest_status"], "valid")
            self.assertEqual(output["active_slice_manifest_status"], "valid")
            self.assertEqual(output["missing_or_malformed_sources"], [])
            self.assertTrue(output["display_only"])
            self.assertFalse(output["authorizes_action"])
            self.assertNotEqual(output["next_action"], "deploy")

    def test_malformed_product_sources_are_reported_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            init_repo(repo, "https://example.invalid/example/synthetic-product.git")
            delivery = repo / "docs" / "delivery"
            delivery.mkdir(parents=True)
            (delivery / "current-state.md").write_text("not front matter\n", encoding="utf-8")
            (repo / "project-documentation-manifest.json").write_text("{broken\n", encoding="utf-8")
            before = tree_digest(repo)
            _, output = summary(repo, "product")
            self.assertEqual(before, tree_digest(repo))
            self.assertIn("docs/delivery/current-state.md: malformed front matter", output["missing_or_malformed_sources"])
            self.assertIn("project-documentation-manifest.json: malformed JSON", output["missing_or_malformed_sources"])
            self.assertEqual(output["workflow_manifest_status"], "malformed")
            self.assertFalse(output["authorizes_action"])


if __name__ == "__main__":
    unittest.main()
