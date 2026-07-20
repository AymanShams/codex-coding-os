#!/usr/bin/env python3
"""Focused tests for deterministic templates and the Coding OS source profile."""

from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".agents" / "skills" / "project-session-continuity" / "scripts" / "session_continuity.py"
ASSET_ROOT = ROOT / ".agents" / "skills" / "project-session-continuity" / "assets"


def load_helper():
    spec = importlib.util.spec_from_file_location("session_continuity_source_profile_test", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def init_git_repo(root: Path) -> None:
    git(root, "init")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic Test")
    marker = root / "tracked.txt"
    marker.write_text("tracked\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "synthetic baseline")


def write_source_sentinels(root: Path, *, include_case_engine: bool = True, include_skill: bool = True) -> None:
    (root / "pack.manifest.json").write_text(
        json.dumps(
            {
                "package_name": "codex-coding-os",
                "release_status": "public-release",
                "version": "1.2.3",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if include_case_engine:
        case_engine = root / "scripts" / "agent" / "case_state.py"
        case_engine.parent.mkdir(parents=True, exist_ok=True)
        case_engine.write_text("# synthetic sentinel\n", encoding="utf-8")
    if include_skill:
        skill = root / ".agents" / "skills" / "project-session-continuity" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text("---\nname: project-session-continuity\n---\n", encoding="utf-8")


def normalize_state_template(text: str) -> str:
    normalized = text.replace("\r\n", "\n").rstrip("\n") + "\n"
    lines = ["last_updated: <generated>" if line.startswith("last_updated: ") else line for line in normalized.splitlines()]
    return "\n".join(lines).rstrip("\n") + "\n"


def normalize_active_manifest(data: dict[str, object]) -> dict[str, object]:
    normalized = json.loads(json.dumps(data))
    normalized["last_updated"] = "<generated>"
    return normalized


@contextlib.contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class SourceRepositoryProfileTests(unittest.TestCase):
    def test_embedded_assets_and_generated_product_files_have_parity(self) -> None:
        helper = load_helper()
        state_asset = (ASSET_ROOT / "current-state-template.md").read_text(encoding="utf-8")
        active_asset = json.loads((ASSET_ROOT / "active-slice-manifest.template.json").read_text(encoding="utf-8"))

        self.assertEqual(normalize_state_template(helper.DEFAULT_STATE_TEMPLATE), normalize_state_template(state_asset))
        self.assertEqual(normalize_active_manifest(helper.DEFAULT_ACTIVE_SLICE_MANIFEST), normalize_active_manifest(active_asset))

        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            result = run([sys.executable, "-B", str(HELPER), "init", "--profile", "product"], project)
            self.assertIn("Detected profile: product", result.stdout)
            generated_state = (project / "docs" / "delivery" / "current-state.md").read_text(encoding="utf-8")
            generated_active = json.loads(
                (project / "docs" / "delivery" / "active-slice-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(normalize_state_template(state_asset), normalize_state_template(generated_state))
            self.assertEqual(normalize_active_manifest(active_asset), normalize_active_manifest(generated_active))
            self.assertEqual(generated_active["last_updated"], dt.date.today().isoformat())

    def test_auto_source_profile_requires_all_sentinels_and_never_creates_product_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            init_git_repo(source)
            write_source_sentinels(source)
            git(source, "add", ".")
            git(source, "commit", "-m", "add synthetic source sentinels")

            start = run(
                [sys.executable, "-B", str(HELPER), "start", "--profile", "auto", "--no-fetch", "--start-new"],
                source,
            )
            self.assertIn("Detected profile: coding-os-source", start.stdout)
            self.assertIn("START GATE: PASS", start.stdout)
            for forbidden in (
                source / "project-documentation-manifest.json",
                source / "docs" / "delivery" / "current-state.md",
                source / "docs" / "delivery" / "active-slice-manifest.json",
            ):
                self.assertFalse(forbidden.exists(), forbidden)

            refused = run(
                [sys.executable, "-B", str(HELPER), "init", "--profile", "coding-os-source"],
                source,
                expected=2,
            )
            self.assertIn("refuses product coordination files", refused.stdout.lower())

            (source / "tracked.txt").write_text("dirty\n", encoding="utf-8")
            blocked = run(
                [sys.executable, "-B", str(HELPER), "start", "--profile", "auto", "--no-fetch", "--start-new"],
                source,
                expected=2,
            )
            self.assertIn("clean working tree", blocked.stdout)
            continued = run(
                [sys.executable, "-B", str(HELPER), "start", "--profile", "auto", "--no-fetch", "--continue-slice"],
                source,
            )
            self.assertIn("START GATE: PASS", continued.stdout)
            self.assertFalse((source / "docs" / "delivery" / "current-state.md").exists())

    def test_partial_source_sentinels_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            init_git_repo(source)
            write_source_sentinels(source, include_case_engine=False, include_skill=False)
            result = run(
                [sys.executable, "-B", str(HELPER), "summary", "--profile", "auto", "--json"],
                source,
                expected=2,
            )
            self.assertIn("partial coding os source sentinels", result.stdout.lower())
            self.assertFalse((source / "docs").exists())

    def test_commented_is_never_approval_and_legacy_loop_policy_is_absent(self) -> None:
        helper = load_helper()
        self.assertEqual(helper.ACCEPTED_CURRENT_HEAD_REVIEW_STATES, {"APPROVED"})

        policy_paths = [
            ROOT / "AGENTS.md",
            ROOT / ".agents" / "skills" / "ai-coding-discipline" / "SKILL.md",
            ROOT / ".agents" / "skills" / "codex-coding-os-master" / "SKILL.md",
            ROOT / ".agents" / "skills" / "project-session-continuity" / "SKILL.md",
            ROOT / ".agents" / "skills" / "project-session-continuity" / "assets" / "current-state-template.md",
            ROOT / ".agents" / "skills" / "new-project-documentation-system" / "assets" / "AGENTS.md",
            ROOT / ".agents" / "skills" / "new-project-documentation-system" / "assets" / "CLAUDE.md",
            ROOT / ".agents" / "skills" / "new-project-documentation-system" / "references" / "repo-agent-instructions.md",
            ROOT / ".agents" / "skills" / "new-project-documentation-system" / "references" / "workflow-modes-and-gates.md",
            ROOT / "templates" / "CLAUDE.md",
            ROOT / "templates" / "repo-AGENTS.md",
            ROOT / "templates" / "parent-orchestrator-prompt.md",
            ROOT / "templates" / "sequential-manual-prompt.md",
            ROOT / "templates" / "fresh-context-review.md",
            ROOT / "templates" / "handoff-note.md",
            ROOT / "templates" / "review-checklist.md",
            ROOT / "docs" / "review-doctrine.md",
        ]
        old_phrases = (
            "after two automated review-fix rounds",
            "after three findings in the same validator area",
            "exactly one further automated review",
            "exactly one more automated review",
        )
        for path in policy_paths:
            text = path.read_text(encoding="utf-8").lower()
            for phrase in old_phrases:
                self.assertNotIn(phrase, text, f"{phrase!r} remains in {path}")

        script = HELPER.read_text(encoding="utf-8")
        active_asset = (ASSET_ROOT / "active-slice-manifest.template.json").read_text(encoding="utf-8")
        for duplicate in (
            "automated_review_fix_rounds",
            "max_automated_review_fix_rounds",
            "validator_area_findings",
            "next_review_authorized",
            "body_reports_no_major_issues",
        ):
            self.assertNotIn(duplicate, script)
            self.assertNotIn(duplicate, active_asset)


if __name__ == "__main__":
    unittest.main()
