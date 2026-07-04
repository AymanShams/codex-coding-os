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
FRESH_CONTEXT_REVIEW = ROOT / "templates" / "fresh-context-review.md"
PHASES = [
    "0_route_scope",
    "1_source_inventory",
    "2_material_decisions",
    "3_controlled_docs",
    "4_tdd_alignment",
    "5_repo_documentation",
    "6_agent_instructions",
    "7_handoff",
    "8_final_validation",
]
APPROVALS = ["source_authority", "material_decisions", "controlled_docs", "tdd", "coding_start"]


def run(args: list[str], cwd: Path, expected: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != expected:
        raise AssertionError(
            f"Expected exit {expected}, got {result.returncode}: {' '.join(args)}\n{result.stdout}"
        )
    return result


def update_frontmatter(path: Path, key: str, value: str) -> None:
    update_frontmatter_values(path, {key: value})


def update_frontmatter_values(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    missing = set(values)
    for index, line in enumerate(lines):
        for key, value in values.items():
            if line.startswith(f"{key}:"):
                lines[index] = f"{key}: {value}"
                missing.discard(key)
                break
    if missing:
        raise AssertionError(f"Frontmatter key not found: {', '.join(sorted(missing))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_code_ready_manifest(project: Path) -> None:
    manifest = {
        "mode": "resume",
        "output_root": ".",
        "coordination_state_path": "docs/delivery/current-state.md",
        "permission_manifest_path": "docs/delivery/active-slice-manifest.json",
        "session_continuity_command": "python scripts/agent/session_continuity.py start --start-new",
        "next_action": "code",
        "code_allowed": True,
        "open_material_decisions": [],
        "unresolved_source_conflicts": [],
        "approvals": {approval: True for approval in APPROVALS},
        "phases": {phase: {"status": "completed", "evidence": ["smoke test"]} for phase in PHASES},
    }
    (project / "project-documentation-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def write_active_slice(
    project: Path,
    allowed_files: list[str],
    review: dict[str, object] | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    active_path = project / "docs" / "delivery" / "active-slice-manifest.json"
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active.update(
        {
            "active_area": "implementation",
            "active_slice": "feature-a",
            "permission_state": "same_slice_only",
            "automation_mode": "off",
            "actor_role": "single_session",
            "handoff_target": "manual_next_session",
            "run_envelope": {
                "repo": "",
                "objective": "",
                "allowed_next_slice_rule": "none",
                "max_child_sessions": 0,
                "child_sessions_used": 0,
                "thread_or_worktree_creation": "not_approved",
                "branch_plan": "not_approved",
                "review_authority": "not_approved",
                "publication_authority": "not_approved",
                "control_only_pr_authorized": False,
                "stop_conditions": ["Stop immediately if the user says to stop."],
            },
            "decision_records": [],
            "scope_review": {
                "acceptance_criteria": [],
                "explicit_non_goals": [],
                "unrequested_behavior": [],
                "hidden_dependencies": [],
                "assumptions_that_entered_code": [],
            },
            "allowed_files": allowed_files,
            "review": review
            or {
                "required": False,
                "status": "not_required",
                "reviewed_sha": "none",
                "applies_to_active_slice": False,
            },
        }
    )
    if extra:
        active.update(extra)
    active_path.write_text(json.dumps(active, indent=2) + "\n", encoding="utf-8")


def configure_code_ready_state(project: Path, review_required: bool = False, review_status: str = "not_required", reviewed_sha: str = "none") -> None:
    update_frontmatter_values(
        project / "docs" / "delivery" / "current-state.md",
        {
            "active_area": "implementation",
            "active_slice": "feature-a",
            "next_area": "implementation",
            "next_slice": "feature-a",
            "next_action": "code",
            "review_required": "true" if review_required else "false",
            "review_status": review_status,
            "reviewed_sha": reviewed_sha,
            "review_applies_to_active_slice": "true" if review_required else "false",
        },
    )


def parent_automation_fields() -> dict[str, str]:
    return {
        "automation_mode": "parent_orchestrator",
        "actor_role": "parent",
        "handoff_target": "parent",
    }


def parent_automation_manifest_fields(project: Path, actor_role: str = "parent") -> dict[str, object]:
    return {
        "automation_mode": "parent_orchestrator",
        "actor_role": actor_role,
        "handoff_target": "parent",
        "run_envelope": {
            "repo": project.as_posix(),
            "objective": "smoke parent automation",
            "allowed_next_slice_rule": "continue approved smoke flow only",
            "max_child_sessions": 2,
            "child_sessions_used": 1,
            "thread_or_worktree_creation": "approved",
            "branch_plan": "approved",
            "review_authority": "approved",
            "publication_authority": "not_approved",
            "control_only_pr_authorized": False,
            "stop_conditions": [
                "Stop if a human decision is required.",
                "Stop immediately if the user says to stop.",
            ],
        },
    }


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
        if not (project / "docs" / "delivery" / "active-slice-manifest.json").exists():
            raise AssertionError("active-slice manifest was not created")
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
        if "## Recommended Next Action" not in handoff.stdout:
            raise AssertionError(handoff.stdout)
        if "First run:" not in handoff.stdout or "python scripts/agent/session_continuity.py start --start-new" not in handoff.stdout:
            raise AssertionError(handoff.stdout)

        configure_code_ready_state(project)
        write_code_ready_manifest(project)
        write_active_slice(project, ["docs/**"])
        run(["git", "add", "."], project, 0)
        run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "baseline"], project, 0)

        docs_only_update = project / "docs" / "delivery" / "current-state.md"
        docs_only_update.write_text(
            docs_only_update.read_text(encoding="utf-8").replace(
                "Resolve material project decisions before drafting controlled documents or coding.",
                "Resolve material project decisions before drafting controlled documents or coding. Smoke update.",
            ),
            encoding="utf-8",
        )
        run(["git", "add", "."], project, 0)
        run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "docs only coordination update"], project, 0)
        docs_only = run([python, str(local_continuity), "diff-check", "--base", "HEAD~1"], project, 1)
        if "docs-only slice-selection/current-state PR is blocked" not in docs_only.stdout:
            raise AssertionError(docs_only.stdout)
        bad_base = run([python, str(local_continuity), "diff-check", "--base", "missing-base-ref"], project, 1)
        if "git diff failed for base ref" not in bad_base.stdout:
            raise AssertionError(bad_base.stdout)

        outside_slice = project / "src" / "outside_slice.py"
        outside_slice.parent.mkdir(parents=True, exist_ok=True)
        outside_slice.write_text("print('outside allowed files')\n", encoding="utf-8")
        blocked_files = run([python, str(local_continuity), "start", "--continue-slice", "--no-fetch"], project, 2)
        if "outside active-slice allowed_files" not in blocked_files.stdout:
            raise AssertionError(blocked_files.stdout)

        write_active_slice(project, ["docs/**", "src/**"])
        run([python, str(local_continuity), "start", "--continue-slice", "--no-fetch"], project, 0)
        run(["git", "add", "."], project, 0)
        run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "allowed slice work"], project, 0)

        update_frontmatter_values(project / "docs" / "delivery" / "current-state.md", parent_automation_fields())
        write_active_slice(project, ["docs/**", "src/**"], extra=parent_automation_manifest_fields(project))
        parent_block = run([python, str(local_continuity), "start", "--start-new", "--no-fetch"], project, 2)
        if "parent/orchestrator actor cannot perform next_action" not in parent_block.stdout:
            raise AssertionError(parent_block.stdout)
        parent_handoff = run(
            [
                python,
                str(local_continuity),
                "handoff",
                "--topic",
                "parent",
                "--reason",
                "child-complete",
                "--next",
                "start review child",
                "--target",
                "parent",
            ],
            project,
            0,
        )
        if "Parent/orchestrator consumes this handoff internally" not in parent_handoff.stdout:
            raise AssertionError(parent_handoff.stdout)
        if "Fallback only if parent automation tooling is unavailable" not in parent_handoff.stdout:
            raise AssertionError(parent_handoff.stdout)

        parent_impl = project / "src" / "parent_impl.py"
        parent_impl.write_text("print('parent implementation drift')\n", encoding="utf-8")
        run(["git", "add", "."], project, 0)
        run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "parent implementation drift"], project, 0)
        parent_diff_block = run([python, str(local_continuity), "diff-check", "--base", "HEAD~1"], project, 1)
        if "parent/orchestrator actor cannot change implementation file" not in parent_diff_block.stdout:
            raise AssertionError(parent_diff_block.stdout)

        stop_latch = run([python, str(local_continuity), "stop-latch", "--reason", "user stop"], project, 0)
        if "STOP LATCH: ACTIVE" not in stop_latch.stdout:
            raise AssertionError(stop_latch.stdout)
        stopped = run([python, str(local_continuity), "start", "--start-new", "--no-fetch"], project, 2)
        if "stop latch is active" not in stopped.stdout:
            raise AssertionError(stopped.stdout)

        update_frontmatter_values(
            project / "docs" / "delivery" / "current-state.md",
            {
                "automation_mode": "off",
                "actor_role": "single_session",
                "handoff_target": "manual_next_session",
                "stop_latch": "false",
                "next_session_required_before_next_slice": "false",
                "last_handoff": "none",
            },
        )
        configure_code_ready_state(project)
        write_active_slice(project, ["docs/**", "src/**"])

        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                "decision_records": [
                    {
                        "decision": "Choose smoke implementation owner",
                        "alternatives_rejected": ["Let the agent choose silently"],
                        "reason": "Owner affects implementation authority",
                        "owner": "Ayman",
                        "approver": "Ayman",
                        "revisit_trigger": "Owner changes",
                        "evidence_test": "Decision is approved before coding",
                        "status": "needs_human",
                        "authority_source": "smoke test",
                        "material": True,
                    }
                ]
            },
        )
        run(["git", "add", "."], project, 0)
        run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "unresolved material decision"], project, 0)
        unresolved_decision = run([python, str(local_continuity), "start", "--start-new", "--no-fetch"], project, 2)
        if "material decision is unresolved" not in unresolved_decision.stdout:
            raise AssertionError(unresolved_decision.stdout)
        write_active_slice(project, ["docs/**", "src/**"])

        configure_code_ready_state(project, review_required=True, review_status="pending")
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            {
                "required": True,
                "status": "pending",
                "reviewed_sha": "none",
                "applies_to_active_slice": True,
            },
        )
        run(["git", "add", "."], project, 0)
        run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "pending review"], project, 0)
        pending_review = run([python, str(local_continuity), "start", "--start-new", "--no-fetch"], project, 2)
        if "required active-slice review must be approved before coding" not in pending_review.stdout:
            raise AssertionError(pending_review.stdout)

        configure_code_ready_state(project, review_required=True, review_status="approved", reviewed_sha="deadbeef")
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            {
                "required": True,
                "status": "approved",
                "reviewed_sha": "deadbeef",
                "applies_to_active_slice": True,
            },
        )
        run(["git", "add", "."], project, 0)
        run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "stale review"], project, 0)
        stale_review = run([python, str(local_continuity), "start", "--start-new", "--no-fetch"], project, 2)
        if "reviewed_sha must match current HEAD" not in stale_review.stdout:
            raise AssertionError(stale_review.stdout)

        review_template = FRESH_CONTEXT_REVIEW.read_text(encoding="utf-8")
        for required in (
            "Original requested outcome",
            "Final changed files",
            "Scope-Creep And Hidden-Dependency Check",
            "hidden dependency",
            "Coordination updates did not become a docs-only PR loop",
        ):
            if required not in review_template:
                raise AssertionError(f"fresh-context-review template is missing {required}")

    with tempfile.TemporaryDirectory(prefix="coding-os-session-repair-") as temp:
        project = Path(temp)
        subprocess.run(["git", "init"], cwd=project, check=True, stdout=subprocess.DEVNULL)
        local_continuity = project / "scripts" / "agent" / "session_continuity.py"
        local_continuity.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CONTINUITY, local_continuity)
        run([python, str(local_continuity), "init"], project, 0)
        (project / "docs" / "delivery" / "active-slice-manifest.json").unlink()
        state_path = project / "docs" / "delivery" / "current-state.md"
        legacy_lines = [
            line
            for line in state_path.read_text(encoding="utf-8").splitlines()
            if not line.startswith(("permission_manifest:", "review_required:", "review_status:", "reviewed_sha:", "review_applies_to_active_slice:"))
        ]
        state_path.write_text("\n".join(legacy_lines) + "\n", encoding="utf-8")
        repaired = run([python, str(local_continuity), "repair"], project, 0)
        if "Created docs" not in repaired.stdout or "permission_manifest" not in repaired.stdout:
            raise AssertionError(repaired.stdout)
        run([python, str(local_continuity), "validate"], project, 0)

    print("Workflow gate smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
