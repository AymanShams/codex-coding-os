#!/usr/bin/env python3
"""Smoke tests for fail-closed parallel worktree lane orchestration."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent" / "worktree_lanes.py"
TEMPLATE = ROOT / ".agents" / "skills" / "new-project-documentation-system" / "assets" / "project-documentation-manifest.template.json"


def run(args: list[str], cwd: Path, expected: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != expected:
        raise AssertionError(
            f"Expected exit {expected}, got {result.returncode}: {' '.join(args)}\n{result.stdout}"
        )
    return result


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, stdout=subprocess.DEVNULL)


def ready_manifest(project: Path) -> None:
    manifest = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    manifest["code_allowed"] = True
    manifest["next_action"] = "code"
    manifest["approvals"] = {
        "source_authority": True,
        "material_decisions": True,
        "controlled_docs": True,
        "tdd": True,
        "coding_start": True,
    }
    for phase in manifest["phases"].values():
        phase["status"] = "completed"
    (project / "project-documentation-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> int:
    python = sys.executable
    with tempfile.TemporaryDirectory(prefix="coding-os-worktree-lanes-") as temp:
        project = Path(temp) / "project"
        project.mkdir()
        git(project, "init")
        git(project, "config", "user.email", "codex@example.invalid")
        git(project, "config", "user.name", "Codex Smoke Test")

        shutil.copyfile(TEMPLATE, project / "project-documentation-manifest.json")
        (project / "src").mkdir()
        (project / "tests").mkdir()
        (project / "src" / "feature.py").write_text("print('feature')\n", encoding="utf-8")
        (project / "tests" / "test_feature.py").write_text("def test_feature():\n    assert True\n", encoding="utf-8")
        git(project, "add", ".")
        git(project, "commit", "-m", "initial")

        blocked = run(
            [python, str(SCRIPT), "evaluate", "--task", "parallel auth feature", "--risk", "high"],
            project,
            2,
        )
        if "workflow manifest" not in blocked.stdout:
            raise AssertionError(blocked.stdout)

        ready_manifest(project)
        (project / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        dirty = run(
            [python, str(SCRIPT), "evaluate", "--task", "parallel auth feature", "--risk", "high"],
            project,
            2,
        )
        if "dirty" not in dirty.stdout:
            raise AssertionError(dirty.stdout)
        (project / "dirty.txt").unlink()
        git(project, "add", "project-documentation-manifest.json")
        git(project, "commit", "-m", "allow coding")

        missing_approval = run(
            [
                python,
                str(SCRIPT),
                "create",
                "--task",
                "parallel auth feature",
                "--risk",
                "high",
                "--lane",
                "code|Implement code|src/feature.py||fresh-context|python -m pytest",
                "--lane",
                "tests|Harden tests|tests/test_feature.py||fresh-context|python -m pytest",
            ],
            project,
            2,
        )
        if "user approval" not in missing_approval.stdout:
            raise AssertionError(missing_approval.stdout)

        overlap = run(
            [
                python,
                str(SCRIPT),
                "create",
                "--task",
                "parallel auth feature",
                "--risk",
                "high",
                "--user-approved",
                "--lane",
                "code-a|Implement code A|src/feature.py||fresh-context|python -m pytest",
                "--lane",
                "code-b|Implement code B|src/feature.py||fresh-context|python -m pytest",
            ],
            project,
            2,
        )
        if "overlap" not in overlap.stdout:
            raise AssertionError(overlap.stdout)

        auto_without_ack = run(
            [
                python,
                str(SCRIPT),
                "create",
                "--task",
                "parallel auth feature",
                "--risk",
                "high",
                "--user-approved",
                "--thread-mode",
                "auto",
                "--lane",
                "code|Implement code|src/feature.py||fresh-context|python -m pytest",
                "--lane",
                "tests|Harden tests|tests/test_feature.py||fresh-context|python -m pytest",
            ],
            project,
            2,
        )
        if "auto thread mode" not in auto_without_ack.stdout:
            raise AssertionError(auto_without_ack.stdout)

        created = run(
            [
                python,
                str(SCRIPT),
                "create",
                "--task",
                "parallel auth feature",
                "--risk",
                "high",
                "--run-id",
                "smoke",
                "--user-approved",
                "--lane",
                "code|Implement code|src/feature.py||fresh-context|python -m pytest",
                "--lane",
                "tests|Harden tests|tests/test_feature.py||fresh-context|python -m pytest",
            ],
            project,
            0,
        )
        if "Created parallel worktree run" not in created.stdout:
            raise AssertionError(created.stdout)

        lane_manifest = project / "docs" / "delivery" / "parallel-worktrees" / "smoke" / "lane-manifest.json"
        manifest = json.loads(lane_manifest.read_text(encoding="utf-8"))
        code_lane = next(lane for lane in manifest["lanes"] if lane["slug"] == "code")
        code_tree = Path(code_lane["worktree_path"])
        (code_tree / "src" / "feature.py").write_text("print('changed')\n", encoding="utf-8")
        run([python, str(SCRIPT), "validate", "--run-id", "smoke"], project, 0)

        (code_tree / "project-documentation-manifest.json").write_text("{}", encoding="utf-8")
        controlled = run([python, str(SCRIPT), "validate", "--run-id", "smoke"], project, 1)
        if "controlled source" not in controlled.stdout:
            raise AssertionError(controlled.stdout)

    print("Worktree lane smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
