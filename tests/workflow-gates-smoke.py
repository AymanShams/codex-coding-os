#!/usr/bin/env python3
"""Regression tests for documentation, placeholder, and session-continuity gates."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".agents" / "skills" / "new-project-documentation-system"
CONTINUITY = ROOT / ".agents" / "skills" / "project-session-continuity" / "scripts" / "session_continuity.py"


def run(args: list[str], cwd: Path, expected: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != expected:
        raise AssertionError(
            f"Expected exit {expected}, got {result.returncode}: {' '.join(args)}\n{result.stdout}"
        )
    return result


def update_frontmatter(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            lines[index] = f"{key}: {value}"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    raise AssertionError(f"Frontmatter key not found: {key}")


def main() -> int:
    python = sys.executable
    with tempfile.TemporaryDirectory(prefix="coding-os-workflow-gates-") as temp:
        project = Path(temp)
        subprocess.run(["git", "init"], cwd=project, check=True, stdout=subprocess.DEVNULL)

        manifest = json.loads(
            (WORKFLOW_ROOT / "assets" / "project-documentation-manifest.template.json").read_text(encoding="utf-8")
        )
        manifest_path = project / "project-documentation-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        validator = WORKFLOW_ROOT / "scripts" / "validate_workflow_manifest.py"

        run([python, str(validator), str(manifest_path)], project, 0)

        manifest["open_material_decisions"] = ["Who approves the primary workflow?"]
        manifest["phases"]["3_controlled_docs"]["status"] = "in_progress"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        blocked = run([python, str(validator), str(manifest_path)], project, 1)
        if "controlled docs cannot start while material decisions remain open" not in blocked.stdout:
            raise AssertionError(blocked.stdout)

        good_doc = project / "good.md"
        bad_doc = project / "bad.md"
        good_doc.write_text("# Approved Scope\n\nThe first release supports one reviewed workflow.\n", encoding="utf-8")
        bad_doc.write_text("# Draft\n\nTODO: decide the workflow.\n", encoding="utf-8")
        placeholder_validator = WORKFLOW_ROOT / "scripts" / "validate_filled_artifacts.py"
        run([python, str(placeholder_validator), str(good_doc)], project, 0)
        run([python, str(placeholder_validator), str(bad_doc)], project, 1)

        local_continuity = project / "scripts" / "agent" / "session_continuity.py"
        local_continuity.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CONTINUITY, local_continuity)
        run([python, str(local_continuity), "init"], project, 0)
        run([python, str(local_continuity), "validate"], project, 0)
        update_frontmatter(project / "docs" / "delivery" / "current-state.md", "next_action", "code")
        blocked_start = run([python, str(local_continuity), "start", "--no-fetch"], project, 2)
        if "workflow manifest" not in blocked_start.stdout:
            raise AssertionError(blocked_start.stdout)
        handoff = run(
            [
                python,
                str(local_continuity),
                "handoff",
                "--topic",
                "smoke",
                "--reason",
                "test",
                "--next",
                "resolve next slice",
            ],
            project,
            0,
        )
        if "## Paste-Ready Next-Session Prompt" not in handoff.stdout:
            raise AssertionError(handoff.stdout)
        if "First run:" not in handoff.stdout or "python scripts/agent/session_continuity.py start" not in handoff.stdout:
            raise AssertionError(handoff.stdout)

    print("Workflow gate smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
