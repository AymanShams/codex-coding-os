#!/usr/bin/env python3
"""Regression tests for documentation, placeholder, and session-continuity gates."""

from __future__ import annotations

import importlib.util
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


def git_stdout(project: Path, *args: str) -> str:
    return run(["git", *args], project, 0).stdout.strip()


def assert_review_state_collector_fixture() -> None:
    spec = importlib.util.spec_from_file_location("session_continuity_under_test", CONTINUITY)
    if spec is None or spec.loader is None:
        raise AssertionError("Cannot import session continuity helper for collector fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    head = "a" * 40
    stale_head = "b" * 40
    pr = {
        "headRefOid": head,
        "reviews": [
            {
                "commit": {"oid": head},
                "submittedAt": "2026-07-06T00:00:00Z",
                "state": "COMMENTED",
                "author": {"login": "chatgpt-codex-connector"},
            }
        ],
        "comments": [
            {
                "body": "Codex Review: Didn't find any major issues.\n\n**Reviewed commit:** `aaaaaaaaaa`",
                "createdAt": "2026-07-06T00:02:00Z",
                "author": {"login": "chatgpt-codex-connector"},
                "url": "https://example.invalid/comment",
            }
        ],
        "statusCheckRollup": [
            {"name": "validate", "conclusion": "SUCCESS"},
            {"context": "Vercel", "state": "PENDING"},
        ],
    }
    inline_comments = [
        {
            "path": "scripts/agent/session_continuity.py",
            "line": 1,
            "original_commit_id": head,
            "commit_id": head,
            "created_at": "2026-07-06T00:01:00Z",
            "url": "https://example.invalid/inline",
        }
    ]
    summary = module.summarize_current_head_review_state(pr, inline_comments)
    if summary["pr_head"] != head:
        raise AssertionError(summary)
    if summary["review_commit"] != head:
        raise AssertionError(summary)
    if summary["current_head_review_records"] != 1:
        raise AssertionError(summary)
    if summary["clean_summary_commit"] != "aaaaaaaaaa":
        raise AssertionError(summary)
    if summary["current_head_original_commit_comments"] != 1 or summary["current_head_commit_comments"] != 1:
        raise AssertionError(summary)
    if len(summary["required_checks_blocking"]) != 1:
        raise AssertionError(summary)
    if summary["ambiguous"] is not True:
        raise AssertionError(summary)
    paginated_reviews = module.flatten_gh_paginated_json(
        [
            [
                {
                    "commit_id": stale_head,
                    "submitted_at": "2026-07-05T23:59:00Z",
                    "state": "COMMENTED",
                    "user": {"login": "chatgpt-codex-connector"},
                }
            ],
            [
                {
                    "commit_id": head,
                    "submitted_at": "2026-07-06T00:03:00Z",
                    "state": "COMMENTED",
                    "user": {"login": "chatgpt-codex-connector"},
                }
            ],
        ]
    )
    paginated_inline = module.flatten_gh_paginated_json([[inline_comments[0]]])
    if len(paginated_reviews) != 2 or len(paginated_inline) != 1:
        raise AssertionError("paginated gh API fixtures were not flattened")
    pr_without_graphql_reviews = dict(pr)
    pr_without_graphql_reviews.pop("reviews", None)
    pr_without_graphql_reviews["comments"] = [
        {
            "body": "Codex Review: Didn't find any major issues.\n\n**Reviewed commit:** `aaaaaaaaaa`",
            "created_at": "2026-07-06T00:02:00Z",
            "user": {"login": "chatgpt-codex-connector"},
            "url": "https://example.invalid/comment",
        }
    ]
    api_summary = module.summarize_current_head_review_state(
        pr_without_graphql_reviews,
        paginated_inline,
        paginated_reviews,
    )
    if api_summary["current_head_review_records"] != 1:
        raise AssertionError(api_summary)
    if api_summary["review_commit"] != head:
        raise AssertionError(api_summary)
    if api_summary["clean_summary_commit"] != "aaaaaaaaaa":
        raise AssertionError(api_summary)


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
                "repo": project.as_posix(),
                "objective": "smoke fixture",
                "allowed_next_slice_rule": "none",
                "max_child_sessions": 0,
                "child_sessions_used": 0,
                "thread_or_worktree_creation": "not_approved",
                "branch_plan": "not_approved",
                "review_authority": "not_approved",
                "publication_authority": "not_approved",
                "handoff_target": "manual_next_session",
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
            "handoff_target": "parent",
            "control_only_pr_authorized": False,
            "stop_conditions": [
                "Stop if a human decision is required.",
                "Stop immediately if the user says to stop.",
            ],
        },
    }


def parent_closeout_reconciliation(
    status: str = "pass",
    conflict: bool = False,
    stale: bool = False,
    pr_head_sha: str = "abc123",
    local_head_sha: str = "abc123",
    local_branch_state: str = "clean",
    current_inline_comments: str = "none_current_head",
    issue_comments: str = "latest_review_clean",
    required_checks: str = "success",
    publication_stabilization: dict[str, object] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, object]:
    if publication_stabilization is None:
        if pr_head_sha == "not_applicable":
            publication_stabilization = {
                "post_review_fix_reconciled": False,
                "pr_body_head_sha": "not_applicable",
                "review_evidence_head_sha": "not_applicable",
                "review_authority": "not_applicable",
                "review_authority_count": "not_applicable",
                "metadata_only_check_retrigger": "not_applicable",
                "bounded_wait_result": "not_applicable",
            }
        else:
            publication_stabilization = {
                "post_review_fix_reconciled": True,
                "pr_body_head_sha": pr_head_sha,
                "review_evidence_head_sha": pr_head_sha,
                "review_authority": "current-head Codex review plus configured independent review",
                "review_authority_count": "2 current-head reviews",
                "metadata_only_check_retrigger": "not_retriggered",
                "bounded_wait_result": "not_required_no_retrigger",
            }
    return {
        "parent_closeout_reconciliation": {
            "required_before_parent_closeout": True,
            "status": status,
            "pr_head_sha": pr_head_sha,
            "local_head_sha": local_head_sha,
            "local_branch_state": local_branch_state,
            "current_inline_comments": current_inline_comments,
            "issue_comments": issue_comments,
            "required_checks": required_checks,
            "conflicting_review_signals": conflict,
            "stale_closeout_detected": stale,
            "publication_stabilization": publication_stabilization,
            "review_loop_breaker": {
                "status": "not_started",
                "automated_review_fix_rounds": 0,
                "max_automated_review_fix_rounds": 2,
                "validator_area_findings": {},
                "batch_rca_completed": False,
                "adversarial_test_matrix_completed": False,
                "next_review_authorized": False,
            },
            "evidence": evidence or ["smoke closeout evidence"],
        }
    }


def write_legacy_handoff(project: Path, name: str = "legacy-handoff.md") -> Path:
    handoff = project / "docs" / "history" / name
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(
        """# Project Session Handoff: Legacy

## Session Boundary Decision
Continue.

## Handoff Routing
Manual next session.

## Git State
Clean.

## Work Completed
None.

## Validation Run
None.

## Source Documents Read Or Changed
None.

## Candidate Decisions Still Not Final
None.

## Risks And Blockers
None.

## Work Explicitly Not Done
None.

## Recommended Next Slice
Continue.

## Paste-Ready Next-Session Prompt
Continue.

## Resume Instructions For The Next Agent
Run the session start gate.
""",
        encoding="utf-8",
    )
    return handoff


def main() -> int:
    python = sys.executable
    assert_review_state_collector_fixture()
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
        legacy_handoff = write_legacy_handoff(project)
        update_frontmatter(project / "docs" / "delivery" / "current-state.md", "last_handoff", legacy_handoff.as_posix())
        run([python, str(local_continuity), "validate"], project, 0)
        update_frontmatter(project / "docs" / "delivery" / "current-state.md", "last_handoff", "none")
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
        parent_legacy_handoff = write_legacy_handoff(project, "legacy-parent-handoff.md")
        update_frontmatter(project / "docs" / "delivery" / "current-state.md", "last_handoff", parent_legacy_handoff.as_posix())
        parent_legacy_block = run([python, str(local_continuity), "validate"], project, 1)
        if "Parent-Orchestrator Closeout Reconciliation" not in parent_legacy_block.stdout:
            raise AssertionError(parent_legacy_block.stdout)
        update_frontmatter(project / "docs" / "delivery" / "current-state.md", "last_handoff", "none")
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
        update_frontmatter(project / "docs" / "delivery" / "current-state.md", "next_action", "closeout")
        parent_closeout_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "parent closeout reconciliation must pass before parent closeout" not in parent_closeout_block.stdout:
            raise AssertionError(parent_closeout_block.stdout)
        parent_live_head = git_stdout(project, "rev-parse", "HEAD")
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha="stale-head",
                    local_branch_state="dirty",
                ),
            },
        )
        parent_stale_head_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "local_head_sha must match live HEAD" not in parent_stale_head_block.stdout:
            raise AssertionError(parent_stale_head_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="clean",
                ),
            },
        )
        parent_stale_branch_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "local_branch_state must match live working tree state: dirty" not in parent_stale_branch_block.stdout:
            raise AssertionError(parent_stale_branch_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha="stale-pr-head",
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                ),
            },
        )
        parent_stale_pr_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "pr_head_sha must match live HEAD for PR closeout" not in parent_stale_pr_block.stdout:
            raise AssertionError(parent_stale_pr_block.stdout)
        missing_publication_stabilization = parent_closeout_reconciliation(
            pr_head_sha=parent_live_head,
            local_head_sha=parent_live_head,
            local_branch_state="dirty",
        )["parent_closeout_reconciliation"]
        missing_publication_stabilization.pop("publication_stabilization")
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                "parent_closeout_reconciliation": missing_publication_stabilization,
            },
        )
        parent_missing_publication_stabilization_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization must be an object" not in parent_missing_publication_stabilization_block.stdout:
            raise AssertionError(parent_missing_publication_stabilization_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "current-head Codex review plus configured independent review",
                        "review_authority_count": "2 current-head reviews",
                        "bounded_wait_result": False,
                    },
                ),
            },
        )
        parent_malformed_publication_stabilization_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "PARENT CLOSEOUT CHECK: FAIL" not in parent_malformed_publication_stabilization_block.stdout:
            raise AssertionError(parent_malformed_publication_stabilization_block.stdout)
        if "publication_stabilization is missing metadata_only_check_retrigger" not in parent_malformed_publication_stabilization_block.stdout:
            raise AssertionError(parent_malformed_publication_stabilization_block.stdout)
        if "publication_stabilization.bounded_wait_result must be text" not in parent_malformed_publication_stabilization_block.stdout:
            raise AssertionError(parent_malformed_publication_stabilization_block.stdout)
        if "Traceback" in parent_malformed_publication_stabilization_block.stdout:
            raise AssertionError(parent_malformed_publication_stabilization_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": "stale-pr-body-head",
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "current-head Codex review plus configured independent review",
                        "review_authority_count": "2 current-head reviews",
                        "metadata_only_check_retrigger": "not_retriggered",
                        "bounded_wait_result": "not_required_no_retrigger",
                    },
                ),
            },
        )
        parent_stale_pr_body_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.pr_body_head_sha must match live HEAD" not in parent_stale_pr_body_block.stdout:
            raise AssertionError(parent_stale_pr_body_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "current-head Codex review plus configured independent review",
                        "review_authority_count": "2 current-head reviews",
                        "metadata_only_check_retrigger": "documentation-governance pending after metadata-only PR body edit",
                        "bounded_wait_result": "checks cancelled",
                    },
                ),
            },
        )
        parent_metadata_retrigger_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.metadata_only_check_retrigger records blocker state" not in parent_metadata_retrigger_block.stdout:
            raise AssertionError(parent_metadata_retrigger_block.stdout)
        if "publication_stabilization.bounded_wait_result records blocker state" not in parent_metadata_retrigger_block.stdout:
            raise AssertionError(parent_metadata_retrigger_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "current-head Codex review plus configured independent review",
                        "review_authority_count": "2 current-head reviews",
                        "metadata_only_check_retrigger": "not_applicable",
                        "bounded_wait_result": "stale required checks",
                    },
                ),
            },
        )
        parent_stale_publication_evidence_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.metadata_only_check_retrigger records blocker state" not in parent_stale_publication_evidence_block.stdout:
            raise AssertionError(parent_stale_publication_evidence_block.stdout)
        if "publication_stabilization.bounded_wait_result records blocker state" not in parent_stale_publication_evidence_block.stdout:
            raise AssertionError(parent_stale_publication_evidence_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "current-head Codex review plus configured independent review",
                        "review_authority_count": "2 current-head reviews",
                        "metadata_only_check_retrigger": "see above",
                        "bounded_wait_result": "unchecked",
                    },
                ),
            },
        )
        parent_unknown_publication_evidence_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.metadata_only_check_retrigger must use accepted state" not in parent_unknown_publication_evidence_block.stdout:
            raise AssertionError(parent_unknown_publication_evidence_block.stdout)
        if "publication_stabilization.bounded_wait_result must use accepted state" not in parent_unknown_publication_evidence_block.stdout:
            raise AssertionError(parent_unknown_publication_evidence_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha="not_applicable",
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    current_inline_comments="not_applicable",
                    issue_comments="not_applicable",
                    required_checks="not_applicable",
                ),
            },
        )
        parent_non_pr_evidence_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "explicit non-PR evidence" not in parent_non_pr_evidence_block.stdout:
            raise AssertionError(parent_non_pr_evidence_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha="not_applicable",
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    current_inline_comments="not_applicable",
                    issue_comments="not_applicable",
                    required_checks="not_applicable",
                    evidence=["non-PR closeout: no open PR; PR head not applicable"],
                ),
            },
        )
        parent_non_pr_pass = run([python, str(local_continuity), "closeout-check"], project, 0)
        if "PARENT CLOSEOUT CHECK: PASS" not in parent_non_pr_pass.stdout:
            raise AssertionError(parent_non_pr_pass.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    required_checks="pending",
                ),
            },
        )
        parent_pending_checks_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "required_checks records blocker state: pending" not in parent_pending_checks_block.stdout:
            raise AssertionError(parent_pending_checks_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    current_inline_comments="open actionable findings",
                ),
            },
        )
        parent_actionable_findings_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "current_inline_comments records blocker state: open actionable findings" not in parent_actionable_findings_block.stdout:
            raise AssertionError(parent_actionable_findings_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    current_inline_comments="no open current-head inline comments",
                    issue_comments="no open issue comments",
                    required_checks="all required checks passed",
                ),
            },
        )
        parent_no_open_comments_pass = run([python, str(local_continuity), "closeout-check"], project, 0)
        if "PARENT CLOSEOUT CHECK: PASS" not in parent_no_open_comments_pass.stdout:
            raise AssertionError(parent_no_open_comments_pass.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "current-head Codex review plus configured independent review",
                        "review_authority_count": "2 current-head reviews",
                        "metadata_only_check_retrigger": "PR body edit did not retrigger checks",
                        "bounded_wait_result": "bounded wait completed with no pending required checks",
                    },
                ),
            },
        )
        parent_negated_publication_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.metadata_only_check_retrigger must use accepted state" not in parent_negated_publication_block.stdout:
            raise AssertionError(parent_negated_publication_block.stdout)
        if "publication_stabilization.bounded_wait_result must use accepted state" not in parent_negated_publication_block.stdout:
            raise AssertionError(parent_negated_publication_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "current-head Codex review plus configured independent review",
                        "review_authority_count": "2 current-head reviews",
                        "metadata_only_check_retrigger": "retriggered_required_checks_passed",
                        "bounded_wait_result": "completed_required_checks_success",
                    },
                ),
            },
        )
        parent_enum_publication_pass = run([python, str(local_continuity), "closeout-check"], project, 0)
        if "PARENT CLOSEOUT CHECK: PASS" not in parent_enum_publication_pass.stdout:
            raise AssertionError(parent_enum_publication_pass.stdout)
        loop_triggered_without_rca = parent_closeout_reconciliation(
            pr_head_sha=parent_live_head,
            local_head_sha=parent_live_head,
            local_branch_state="dirty",
        )
        loop_triggered_without_rca["parent_closeout_reconciliation"]["review_loop_breaker"] = {
            "status": "blocked",
            "automated_review_fix_rounds": 2,
            "max_automated_review_fix_rounds": 2,
            "validator_area_findings": {"publication_stabilization": 3},
            "batch_rca_completed": False,
            "adversarial_test_matrix_completed": False,
            "next_review_authorized": False,
        }
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **loop_triggered_without_rca,
            },
        )
        parent_review_loop_without_rca_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "review loop breaker triggered; batch RCA is required before another automated review" not in parent_review_loop_without_rca_block.stdout:
            raise AssertionError(parent_review_loop_without_rca_block.stdout)
        if "review loop breaker triggered; adversarial test matrix is required before another automated review" not in parent_review_loop_without_rca_block.stdout:
            raise AssertionError(parent_review_loop_without_rca_block.stdout)
        loop_triggered_with_rca = parent_closeout_reconciliation(
            pr_head_sha=parent_live_head,
            local_head_sha=parent_live_head,
            local_branch_state="dirty",
        )
        loop_triggered_with_rca["parent_closeout_reconciliation"]["review_loop_breaker"] = {
            "status": "pass",
            "automated_review_fix_rounds": 2,
            "max_automated_review_fix_rounds": 2,
            "validator_area_findings": {"publication_stabilization": 3},
            "batch_rca_completed": True,
            "adversarial_test_matrix_completed": True,
            "next_review_authorized": True,
        }
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **loop_triggered_with_rca,
            },
        )
        parent_review_loop_with_rca_pass = run([python, str(local_continuity), "closeout-check"], project, 0)
        if "PARENT CLOSEOUT CHECK: PASS" not in parent_review_loop_with_rca_pass.stdout:
            raise AssertionError(parent_review_loop_with_rca_pass.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "not checked",
                        "review_authority_count": "in progress 2",
                        "metadata_only_check_retrigger": "not_retriggered",
                        "bounded_wait_result": "not_required_no_retrigger",
                    },
                ),
            },
        )
        parent_authority_placeholder_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.review_authority must record the exact current-head review authority" not in parent_authority_placeholder_block.stdout:
            raise AssertionError(parent_authority_placeholder_block.stdout)
        if "publication_stabilization.review_authority_count must record the exact required review count" not in parent_authority_placeholder_block.stdout:
            raise AssertionError(parent_authority_placeholder_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "not applicable",
                        "review_authority_count": "not applicable 1",
                        "metadata_only_check_retrigger": "not_retriggered",
                        "bounded_wait_result": "not_required_no_retrigger",
                    },
                ),
            },
        )
        parent_authority_not_applicable_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.review_authority must record the exact current-head review authority" not in parent_authority_not_applicable_block.stdout:
            raise AssertionError(parent_authority_not_applicable_block.stdout)
        if "publication_stabilization.review_authority_count must record the exact required review count" not in parent_authority_not_applicable_block.stdout:
            raise AssertionError(parent_authority_not_applicable_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": True,
                        "review_authority_count": "0",
                        "metadata_only_check_retrigger": "not_retriggered",
                        "bounded_wait_result": "not_required_no_retrigger",
                    },
                ),
            },
        )
        parent_authority_type_and_zero_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.review_authority must be text" not in parent_authority_type_and_zero_block.stdout:
            raise AssertionError(parent_authority_type_and_zero_block.stdout)
        if "publication_stabilization.review_authority_count must record the exact required review count" not in parent_authority_type_and_zero_block.stdout:
            raise AssertionError(parent_authority_type_and_zero_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "none",
                        "review_authority_count": "0",
                        "metadata_only_check_retrigger": "not_retriggered",
                        "bounded_wait_result": "not_required_no_retrigger",
                    },
                ),
            },
        )
        parent_authority_zero_review_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.review_authority must record the exact current-head review authority" not in parent_authority_zero_review_block.stdout:
            raise AssertionError(parent_authority_zero_review_block.stdout)
        if "publication_stabilization.review_authority_count must record the exact required review count" not in parent_authority_zero_review_block.stdout:
            raise AssertionError(parent_authority_zero_review_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    publication_stabilization={
                        "post_review_fix_reconciled": True,
                        "pr_body_head_sha": parent_live_head,
                        "review_evidence_head_sha": parent_live_head,
                        "review_authority": "current-head Codex review plus configured independent review",
                        "review_authority_count": "abc123",
                        "metadata_only_check_retrigger": "not_retriggered",
                        "bounded_wait_result": "not_required_no_retrigger",
                    },
                ),
            },
        )
        parent_authority_copied_id_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "publication_stabilization.review_authority_count must record the exact required review count" not in parent_authority_copied_id_block.stdout:
            raise AssertionError(parent_authority_copied_id_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    current_inline_comments="no open comments but unresolved finding",
                ),
            },
        )
        parent_mixed_clean_blocker_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "current_inline_comments records blocker state: no open comments but unresolved finding" not in parent_mixed_clean_blocker_block.stdout:
            raise AssertionError(parent_mixed_clean_blocker_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                    issue_comments="not_applicable",
                ),
            },
        )
        parent_pr_not_applicable_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "issue_comments may be not_applicable only for non-PR closeout" not in parent_pr_not_applicable_block.stdout:
            raise AssertionError(parent_pr_not_applicable_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra=parent_closeout_reconciliation(
                pr_head_sha=parent_live_head,
                local_head_sha=parent_live_head,
                local_branch_state="dirty",
            ),
        )
        parent_manifest_drift_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "active-slice manifest automation_mode must match current state" not in parent_manifest_drift_block.stdout:
            raise AssertionError(parent_manifest_drift_block.stdout)
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                ),
            },
        )
        parent_closeout_pass = run([python, str(local_continuity), "closeout-check"], project, 0)
        if "PARENT CLOSEOUT CHECK: PASS" not in parent_closeout_pass.stdout:
            raise AssertionError(parent_closeout_pass.stdout)
        update_frontmatter_values(
            project / "docs" / "delivery" / "current-state.md",
            {
                "review_required": "true",
                "review_status": "pending",
                "reviewed_sha": "none",
                "review_applies_to_active_slice": "true",
            },
        )
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            {
                "required": True,
                "status": "pending",
                "reviewed_sha": "none",
                "applies_to_active_slice": True,
            },
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                ),
            },
        )
        parent_pending_review_closeout_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "required active-slice review must be approved before parent closeout" not in parent_pending_review_closeout_block.stdout:
            raise AssertionError(parent_pending_review_closeout_block.stdout)
        update_frontmatter_values(
            project / "docs" / "delivery" / "current-state.md",
            {
                "review_required": "true",
                "review_status": "approved",
                "reviewed_sha": parent_live_head,
                "review_applies_to_active_slice": "true",
            },
        )
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            {
                "required": True,
                "status": "approved",
                "reviewed_sha": parent_live_head,
                "applies_to_active_slice": True,
            },
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                ),
            },
        )
        parent_approved_review_closeout_pass = run([python, str(local_continuity), "closeout-check"], project, 0)
        if "PARENT CLOSEOUT CHECK: PASS" not in parent_approved_review_closeout_pass.stdout:
            raise AssertionError(parent_approved_review_closeout_pass.stdout)
        update_frontmatter_values(
            project / "docs" / "delivery" / "current-state.md",
            {
                "review_required": "false",
                "review_status": "not_required",
                "reviewed_sha": "none",
                "review_applies_to_active_slice": "false",
            },
        )
        write_active_slice(
            project,
            ["docs/**", "src/**"],
            extra={
                **parent_automation_manifest_fields(project),
                **parent_closeout_reconciliation(
                    status="ambiguous",
                    conflict=True,
                    pr_head_sha=parent_live_head,
                    local_head_sha=parent_live_head,
                    local_branch_state="dirty",
                ),
            },
        )
        parent_conflict_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "conflicting GitHub review signals make review-state ambiguous" not in parent_conflict_block.stdout:
            raise AssertionError(parent_conflict_block.stdout)
        write_active_slice(project, ["docs/**", "src/**"], extra=parent_automation_manifest_fields(project))

        parent_impl = project / "src" / "parent_impl.py"
        parent_impl.write_text("print('parent implementation drift')\n", encoding="utf-8")
        parent_continue_block = run([python, str(local_continuity), "start", "--continue-slice", "--no-fetch"], project, 2)
        if "parent/orchestrator actor cannot change implementation file" not in parent_continue_block.stdout:
            raise AssertionError(parent_continue_block.stdout)
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
        sequential_prompt = (ROOT / "templates" / "sequential-manual-prompt.md").read_text(encoding="utf-8")
        parent_prompt = (ROOT / "templates" / "parent-orchestrator-prompt.md").read_text(encoding="utf-8")
        for required in ("sequential_manual", "handoff_target: manual_next_session", "exactly one paste-ready next prompt"):
            if required not in sequential_prompt:
                raise AssertionError(f"sequential manual prompt template is missing {required}")
        for required in (
            "parent_orchestrator",
            "closeout-check",
            "current PR head",
            "conflicting_review_signals",
            "publication stabilization evidence",
            "metadata-only PR body edit",
        ):
            if required not in parent_prompt:
                raise AssertionError(f"parent orchestrator prompt template is missing {required}")
        continuity_skill = (ROOT / ".agents" / "skills" / "project-session-continuity" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "final parent/orchestrator closeout",
            "intermediate child handoff",
            "do not run `closeout-check`",
            "parent consumes the handoff internally",
            "stabilization evidence",
        ):
            if required not in continuity_skill:
                raise AssertionError(f"project-session-continuity skill is missing {required}")
        active_template = json.loads(
            (ROOT / ".agents" / "skills" / "project-session-continuity" / "assets" / "active-slice-manifest.template.json").read_text(
                encoding="utf-8"
            )
        )
        breaker = active_template.get("parent_closeout_reconciliation", {}).get("review_loop_breaker")
        if not isinstance(breaker, dict):
            raise AssertionError("active-slice manifest template is missing review_loop_breaker")
        for required in (
            "status",
            "automated_review_fix_rounds",
            "max_automated_review_fix_rounds",
            "validator_area_findings",
            "batch_rca_completed",
            "adversarial_test_matrix_completed",
            "next_review_authorized",
        ):
            if required not in breaker:
                raise AssertionError(f"active-slice manifest template review_loop_breaker is missing {required}")

    with tempfile.TemporaryDirectory(prefix="coding-os-legacy-non-parent-") as temp:
        project = Path(temp)
        subprocess.run(["git", "init"], cwd=project, check=True, stdout=subprocess.DEVNULL)
        local_continuity = project / "scripts" / "agent" / "session_continuity.py"
        local_continuity.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CONTINUITY, local_continuity)
        run([python, str(local_continuity), "init"], project, 0)
        active_path = project / "docs" / "delivery" / "active-slice-manifest.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active.pop("parent_closeout_reconciliation", None)
        active_path.write_text(json.dumps(active, indent=2) + "\n", encoding="utf-8")

        run([python, str(local_continuity), "validate"], project, 0)
        run(["git", "add", "."], project, 0)
        run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "legacy non-parent manifest"], project, 0)
        run([python, str(local_continuity), "start", "--start-new", "--no-fetch"], project, 0)
        legacy_closeout_block = run([python, str(local_continuity), "closeout-check"], project, 1)
        if "active-slice manifest is missing parent_closeout_reconciliation" not in legacy_closeout_block.stdout:
            raise AssertionError(legacy_closeout_block.stdout)

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
