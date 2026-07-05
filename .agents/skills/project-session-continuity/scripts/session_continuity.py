#!/usr/bin/env python3
"""Cross-platform project session continuity and handoff gate."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


STATE_PATH = Path("docs/delivery/current-state.md")
DEFAULT_MANIFEST_PATH = Path("project-documentation-manifest.json")
DEFAULT_ACTIVE_SLICE_MANIFEST_PATH = Path("docs/delivery/active-slice-manifest.json")
REQUIRED_STATE_FIELDS = (
    "state_version",
    "last_updated",
    "automation_mode",
    "actor_role",
    "handoff_target",
    "stop_latch",
    "workflow_state",
    "workflow_manifest",
    "permission_manifest",
    "active_area",
    "active_slice",
    "next_area",
    "next_slice",
    "next_action",
    "next_high_risk",
    "next_session_required_before_next_slice",
    "review_required",
    "review_status",
    "reviewed_sha",
    "review_applies_to_active_slice",
    "last_handoff",
)
REQUIRED_STATE_HEADINGS = {
    "## Current Verified Repository State",
    "## Active Work",
    "## Next Permitted Action",
    "## Work Explicitly Not Started",
    "## Candidate Decisions Still Not Final",
    "## Decision Record",
    "## Risks And Blockers",
    "## New Session Decision",
    "## New Session Start Instructions",
    "## Update Contract",
}
REQUIRED_HANDOFF_HEADINGS = {
    "## Session Boundary Decision",
    "## Handoff Routing",
    "## Git State",
    "## Work Completed",
    "## Validation Run",
    "## Source Documents Read Or Changed",
    "## Candidate Decisions Still Not Final",
    "## Risks And Blockers",
    "## Work Explicitly Not Done",
    "## Recommended Next Slice",
    "## Paste-Ready Next-Session Prompt",
    "## Resume Instructions For The Next Agent",
}
PARENT_CLOSEOUT_HANDOFF_HEADINGS = {
    "## Parent-Orchestrator Closeout Reconciliation",
}
READY_TO_CODE = {"approved", "completed"}
REVIEW_STATUSES = {"not_started", "pending", "approved", "changes_required", "bypassed", "not_required"}
AUTOMATION_MODES = {"off", "sequential_manual", "parent_orchestrator"}
PARENT_CLOSEOUT_STATUSES = {"not_started", "pass", "blocked", "ambiguous", "not_applicable"}
PARENT_CLOSEOUT_SIGNAL_PASS_VALUES = {
    "current_inline_comments": {
        "pass",
        "clean",
        "none",
        "none_current_head",
        "none_current_head_comments",
        "none_current_head_findings",
        "no_actionable_findings",
        "no_current_head_findings",
        "no_current_head_inline_comments",
        "no_current_head_open_comments",
        "no_current_head_open_findings",
        "no_current_head_open_inline_comments",
        "no_current_head_unresolved_comments",
        "no_current_head_unresolved_findings",
        "no_open_comments",
        "no_open_findings",
        "no_open_inline_comments",
        "no_unresolved_comments",
        "no_unresolved_findings",
        "no_unresolved_inline_comments",
        "resolved",
    },
    "issue_comments": {
        "pass",
        "clean",
        "latest_review_clean",
        "no_actionable_comments",
        "no_blocking_comments",
        "no_open_comments",
        "no_open_issue_comments",
        "no_unresolved_comments",
        "no_unresolved_issue_comments",
        "none",
        "resolved",
    },
    "required_checks": {
        "pass",
        "passed",
        "success",
        "successful",
        "all_required_checks_passed",
        "all_required_checks_success",
        "all_checks_success",
        "all_success",
        "checks_success",
        "required_checks_passed",
        "required_checks_success",
        "green",
    },
}
PARENT_CLOSEOUT_SIGNAL_BLOCKER_MARKERS = {
    "actionable",
    "block",
    "blocked",
    "blocking",
    "cancelled",
    "changes_required",
    "error",
    "fail",
    "failed",
    "failure",
    "in_progress",
    "missing",
    "not_run",
    "open",
    "pending",
    "queued",
    "review_required",
    "timed_out",
    "unresolved",
}
PUBLICATION_STABILIZATION_FIELDS = (
    "post_review_fix_reconciled",
    "pr_body_head_sha",
    "review_evidence_head_sha",
    "review_authority",
    "review_authority_count",
    "metadata_only_check_retrigger",
    "bounded_wait_result",
)
PUBLICATION_STABILIZATION_BAD_VALUES = {
    "",
    "unknown",
    "not_checked",
    "not_applicable",
    "not_required",
    "pending",
    "in_progress",
    "queued",
    "stale",
    "unstable",
    "cancelled",
    "canceled",
    "error",
    "failed",
    "failure",
    "not_run",
    "changes_required",
    "timed_out",
    "timeout",
    "blocked",
    "ambiguous",
}
PUBLICATION_STABILIZATION_AUTHORITY_BAD_VALUES = PUBLICATION_STABILIZATION_BAD_VALUES | {
    "not_applicable",
    "none",
    "zero",
}
PUBLICATION_STABILIZATION_BLOCKER_MARKERS = (
    "pending",
    "in_progress",
    "not_checked",
    "not_applicable",
    "not_required",
    "queued",
    "stale",
    "unstable",
    "cancelled",
    "canceled",
    "error",
    "failed",
    "failure",
    "not_run",
    "changes_required",
    "timed_out",
    "timeout",
    "blocked",
    "ambiguous",
    "unresolved",
)
PUBLICATION_STABILIZATION_NEGATED_CLEAN_TOKENS = {
    "metadata_only_check_retrigger": {
        "body",
        "after",
        "blocked",
        "check",
        "checks",
        "metadata",
        "only",
        "pending",
        "pr",
        "retrigger",
        "retriggered",
        "retriggering",
    },
    "bounded_wait_result": {
        "after",
        "blocked",
        "bounded",
        "check",
        "checks",
        "out",
        "pending",
        "timed",
        "timeout",
        "wait",
    },
}
PUBLICATION_STABILIZATION_NEGATED_CLEAN_NOUNS = {
    "blocked",
    "pending",
    "retrigger",
    "retriggered",
    "retriggering",
    "timed",
    "timeout",
}
PARENT_CLOSEOUT_SIGNAL_NEGATIONS = {"no", "none", "not", "zero", "without"}
PARENT_CLOSEOUT_SIGNAL_NEGATED_CLEAN_TOKENS = {
    "current_inline_comments": {
        "actionable",
        "blocker",
        "blockers",
        "blocking",
        "comment",
        "comments",
        "current",
        "finding",
        "findings",
        "head",
        "inline",
        "open",
        "review",
        "reviews",
        "unresolved",
    },
    "issue_comments": {
        "actionable",
        "blocker",
        "blockers",
        "blocking",
        "comment",
        "comments",
        "issue",
        "issues",
        "open",
        "review",
        "reviews",
        "unresolved",
    },
}
PARENT_CLOSEOUT_SIGNAL_NEGATED_CLEAN_NOUNS = {
    "comment",
    "comments",
    "finding",
    "findings",
    "issue",
    "issues",
    "review",
    "reviews",
}
RUN_ENVELOPE_FIELDS = {
    "repo",
    "objective",
    "allowed_next_slice_rule",
    "max_child_sessions",
    "child_sessions_used",
    "thread_or_worktree_creation",
    "branch_plan",
    "review_authority",
    "publication_authority",
    "handoff_target",
    "control_only_pr_authorized",
    "stop_conditions",
}
ACTOR_ROLES = {
    "single_session",
    "parent",
    "implementer_child",
    "review_child",
    "fix_child",
    "publication_child",
}
HANDOFF_TARGETS = {"manual_next_session", "user", "parent", "child", "none"}
DECISION_STATUSES = {"proposed", "approved", "rejected", "deferred", "needs_human", "superseded"}
PARENT_FORBIDDEN_ACTIONS = {
    "code",
    "implement",
    "fix",
    "merge",
    "publish",
    "deploy",
}
COORDINATION_ONLY_PATTERNS = [
    "project-documentation-manifest.json",
    ".github/pull_request_template.md",
    "templates/**",
    "docs/delivery/current-state.md",
    "docs/delivery/active-slice-manifest.json",
    "docs/history/**",
    "docs/review/**",
    "docs/reviews/**",
    "docs/delivery/*handoff*.md",
    "docs/delivery/*review*.md",
]
REQUIRED_CODE_PHASES = {
    "0_route_scope",
    "1_source_inventory",
    "2_material_decisions",
    "3_controlled_docs",
    "4_tdd_alignment",
    "5_repo_documentation",
    "6_agent_instructions",
    "7_handoff",
    "8_final_validation",
}
HIGH_RISK = re.compile(
    r"auth|iam|security|encryption|audit|payment|billing|migration|deployment|"
    r"llm|protected-data|phi|privacy|break-glass|export|production",
    re.IGNORECASE,
)
IMPLEMENTATION_ACTION = re.compile(
    r"\b(code|implement|implementation|fix|review[_-]?fix|merge|publish|deploy|release|ship|write|edit|modify|change)\b",
    re.IGNORECASE,
)
DEFAULT_STATE_TEMPLATE = """---
state_version: 1
last_updated: 1970-01-01
automation_mode: off
actor_role: single_session
handoff_target: manual_next_session
stop_latch: false
workflow_state: documentation-intake
workflow_manifest: project-documentation-manifest.json
permission_manifest: docs/delivery/active-slice-manifest.json
active_area: documentation
active_slice: project-intake
next_area: documentation
next_slice: resolve-material-decisions
next_action: resolve_material_decisions
next_high_risk: false
next_session_required_before_next_slice: false
review_required: false
review_status: not_required
reviewed_sha: none
review_applies_to_active_slice: false
last_handoff: none
---

# Current Delivery State

## Purpose
This file records coordination state only. Controlling product and technical sources and the workflow manifest remain authoritative.

## Active Slice Manifest
- The current permission boundary is `docs/delivery/active-slice-manifest.json`.
- A current-state update, handoff, review marker, or new chat cannot authorize work outside that manifest.
- Coordination drift alone is not a review trigger. Review need comes from actual diff risk, controlled-source risk, or explicit user instruction.
- Same-slice status is not a review waiver.
- The first-slice authorization false-negative case proves same-slice status must never waive review for authorization, role or permission enforcement, or protected-data behavior changes. Do not reopen a PR from coordination drift alone.
- Governed repo closeout must include `Recommended Next Action` and, when review, handoff, or new-session state is active or requested, the complete paste-ready prompt or an explicit statement that no prompt is required.
- In approved parent-orchestrator automation, child handoffs are internal transition artifacts for the parent unless a stop condition fires.
- A parent/orchestrator session may coordinate, verify, assign, reconcile, and report. It must not implement product code, merge, deploy, or publish directly.
- Before final parent/orchestrator closeout, run `python scripts/agent/session_continuity.py closeout-check` after recording the current PR head, current-head inline comments, issue comments, required checks, local branch state, and stale-closeout check in the active-slice manifest.
- If current-head inline findings conflict with a later no-major-issues summary, mark `conflicting_review_signals` true, classify review state as ambiguous, and stop.

## Current Verified Repository State
- Record the verified branch, remote baseline, and working-tree state.

## Active Work
- Record the bounded slice currently in progress.

## Next Permitted Action
- Resolve material project decisions before drafting controlled documents or coding.

## Work Explicitly Not Started
- Implementation is not started.

## Candidate Decisions Still Not Final
- Record candidates that must not be treated as approved decisions.

## Decision Record
- Material decisions must be approved, rejected, or deferred out of scope before implementation. Absence of a decision is not permission to choose.

## Risks And Blockers
- Record material risks, blocked checks, and unresolved conflicts.

## New Session Decision
- Continue only while work remains the same bounded slice.

## New Session Start Instructions
```text
If `automation_mode` is `parent_orchestrator` and `handoff_target` is `parent`, the parent consumes the latest handoff and starts the next authorized child task after rerunning the fresh gate. Before final parent closeout, record current PR head, current-head inline comments, issue comments, required checks, local branch state, stale-closeout status, and publication stabilization evidence in the active-slice manifest, then run `python scripts/agent/session_continuity.py closeout-check`. Otherwise paste the latest handoff's next-session prompt into a new Codex chat. First run the project session-start gate. Then read current state, its latest handoff, the workflow manifest, and controlling sources. Continue only from the exact next permitted action and stop if the workflow manifest blocks it.
```

## Update Contract
Update this file when active work, next action, risks, blockers, latest handoff, or session-boundary decisions change.
"""

DEFAULT_ACTIVE_SLICE_MANIFEST = {
    "schema_version": 1,
    "last_updated": "1970-01-01",
    "active_area": "documentation",
    "active_slice": "project-intake",
    "permission_state": "documentation_only",
    "permission_summary": "Resolve material decisions and documentation phases. Coding is not allowed until the workflow manifest and this active-slice manifest both permit it.",
    "automation_mode": "off",
    "actor_role": "single_session",
    "actor_role_options": [
        "single_session",
        "parent",
        "implementer_child",
        "review_child",
        "fix_child",
        "publication_child",
    ],
    "handoff_target": "manual_next_session",
    "run_envelope": {
        "repo": "not_approved",
        "objective": "none",
        "allowed_next_slice_rule": "none",
        "max_child_sessions": 0,
        "child_sessions_used": 0,
        "thread_or_worktree_creation": "not_approved",
        "branch_plan": "not_approved",
        "review_authority": "not_approved",
        "publication_authority": "not_approved",
        "handoff_target": "manual_next_session",
        "control_only_pr_authorized": False,
        "stop_conditions": [
            "Stop if the approved run objective is complete.",
            "Stop if a human decision is required.",
            "Stop if the maximum child session count is reached.",
            "Stop if validation, review, or source authority blocks continuation.",
            "Stop immediately if the user says to stop.",
        ],
    },
    "decision_record_required_fields": [
        "decision",
        "alternatives_rejected",
        "reason",
        "owner",
        "approver",
        "revisit_trigger",
        "evidence_test",
        "status",
        "authority_source",
    ],
    "decision_records": [],
    "scope_review": {
        "acceptance_criteria": [],
        "explicit_non_goals": [],
        "unrequested_behavior": [],
        "hidden_dependencies": [],
        "assumptions_that_entered_code": [],
    },
    "parent_closeout_reconciliation": {
        "required_before_parent_closeout": True,
        "status": "not_started",
        "pr_head_sha": "not_checked",
        "local_head_sha": "not_checked",
        "local_branch_state": "not_checked",
        "current_inline_comments": "not_checked",
        "issue_comments": "not_checked",
        "required_checks": "not_checked",
        "conflicting_review_signals": False,
        "stale_closeout_detected": False,
        "publication_stabilization": {
            "post_review_fix_reconciled": False,
            "pr_body_head_sha": "not_checked",
            "review_evidence_head_sha": "not_checked",
            "review_authority": "not_checked",
            "review_authority_count": "not_checked",
            "metadata_only_check_retrigger": "not_checked",
            "bounded_wait_result": "not_checked",
        },
        "evidence": [],
    },
    "source_authority": [
        "project-documentation-manifest.json",
        "docs/delivery/current-state.md",
        "docs/project-brief.md",
        "docs/prd.md",
        "docs/tdd.md",
    ],
    "allowed_files": [
        "project-documentation-manifest.json",
        "docs/**",
        "AGENTS.md",
        "CLAUDE.md",
        "scripts/agent/session_continuity.py",
    ],
    "forbidden_actions": [
        "Do not start implementation.",
        "Do not deploy.",
        "Do not add paid services, providers, databases, auth systems, or production credentials without explicit approval.",
        "Do not treat a handoff, review marker, notification, or new chat as permission to bypass the workflow manifest.",
        "Do not create a docs-only slice-selection or current-state PR unless explicitly authorized.",
        "Do not treat unresolved decisions as permission for the agent to choose.",
    ],
    "validation_commands": [
        "python scripts/agent/session_continuity.py start --start-new",
        "python scripts/agent/session_continuity.py validate",
    ],
    "review": {
        "required": False,
        "status": "not_required",
        "reviewed_sha": "none",
        "applies_to_active_slice": False,
    },
    "stop_conditions": [
        "Stop if the workflow manifest blocks the requested action.",
        "Stop if requested work is outside allowed_files.",
        "Stop if source authority is missing or conflicting.",
        "Stop if coding is requested before coding_start approval.",
        "Stop immediately if the user says to stop.",
    ],
}


def run_git(*args: str, check: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip() if result.returncode == 0 else ""


def run_git_stdout(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}, normalized
    attributes: dict[str, str] = {}
    for line in normalized[4:end].splitlines():
        match = re.match(r"^([a-z0-9_]+):\s*(.*)$", line)
        if match:
            attributes[match.group(1)] = match.group(2).strip().strip('"')
    return attributes, normalized[end + 5 :]


def read_state() -> tuple[dict[str, str], str, str]:
    if not STATE_PATH.exists():
        return {}, "", ""
    content = STATE_PATH.read_text(encoding="utf-8")
    attributes, body = parse_frontmatter(content)
    return attributes, body, content


def write_state(attributes: dict[str, str], body: str) -> None:
    lines = [f"{key}: {value}" for key, value in attributes.items()]
    STATE_PATH.write_text(f"---\n{chr(10).join(lines)}\n---\n{body}", encoding="utf-8")


def tracking_ref() -> str:
    upstream = run_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream:
        return upstream
    if run_git("show-ref", "--verify", "refs/remotes/origin/main"):
        return "origin/main"
    return ""


def repo_state() -> dict[str, object]:
    remote_ref = tracking_ref()
    counts = run_git("rev-list", "--left-right", "--count", f"HEAD...{remote_ref}").split() if remote_ref else []
    ahead = int(counts[0]) if len(counts) == 2 and counts[0].isdigit() else 0
    behind = int(counts[1]) if len(counts) == 2 and counts[1].isdigit() else 0
    return {
        "branch": run_git("branch", "--show-current") or "(detached)",
        "head": run_git("rev-parse", "HEAD") or "(unavailable)",
        "remote_ref": remote_ref or "(unavailable)",
        "remote_head": run_git("rev-parse", remote_ref) if remote_ref else "(unavailable)",
        "status": run_git("status", "-sb") or "(unavailable)",
        "dirty": bool(run_git("status", "--porcelain")),
        "ahead": ahead,
        "behind": behind,
        "recent": run_git("log", "--oneline", "--decorate", "-10") or "(unavailable)",
        "graph": run_git("log", "--oneline", "--decorate", "--graph", "--all", "-20")
        or "(unavailable)",
    }


def normalize_repo_path(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def changed_repo_paths() -> list[str]:
    paths: list[str] = []
    for line in run_git_stdout("status", "--porcelain").splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            paths.extend(normalize_repo_path(part) for part in path.split(" -> ", 1))
        else:
            paths.append(normalize_repo_path(path))
    return sorted({path for path in paths if path})


def path_matches_allowed(path: str, patterns: list[str]) -> bool:
    normalized = normalize_repo_path(path)
    for raw_pattern in patterns:
        pattern = normalize_repo_path(raw_pattern)
        if not pattern:
            continue
        if pattern.endswith("/**"):
            root = pattern[:-3].rstrip("/")
            if normalized == root or normalized.startswith(f"{root}/"):
                return True
        if fnmatch.fnmatchcase(normalized, pattern):
            return True
    return False


def manifest_allows_code(path: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not path.exists():
        return False, [f"workflow manifest not found: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"workflow manifest cannot be read: {exc}"]

    if data.get("next_action") != "code":
        errors.append("workflow manifest next_action is not code")
    if data.get("code_allowed") is not True:
        errors.append("workflow manifest code_allowed is not true")
    if data.get("open_material_decisions"):
        errors.append("workflow manifest has open material decisions")
    if data.get("unresolved_source_conflicts"):
        errors.append("workflow manifest has unresolved source conflicts")

    approvals = data.get("approvals", {})
    for key in ("source_authority", "material_decisions", "controlled_docs", "tdd", "coding_start"):
        if approvals.get(key) is not True:
            errors.append(f"workflow manifest lacks {key} approval")

    phases = data.get("phases", {})
    for phase in REQUIRED_CODE_PHASES:
        entry = phases.get(phase)
        if not isinstance(entry, dict):
            errors.append(f"workflow manifest is missing phase {phase}")
            continue
        if entry.get("status") not in READY_TO_CODE:
            errors.append(f"workflow manifest phase {phase} is not approved or completed")
    return not errors, errors


def read_active_slice_manifest(path: Path) -> tuple[dict[str, object] | None, list[str]]:
    if not path.exists():
        return None, [f"active-slice manifest not found: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"active-slice manifest cannot be read: {exc}"]
    if not isinstance(data, dict):
        return None, ["active-slice manifest must be a JSON object"]
    return data, []


def validate_active_slice_manifest(path: Path, attributes: dict[str, str] | None = None) -> list[str]:
    errors: list[str] = []
    attributes = attributes or {}
    data, read_errors = read_active_slice_manifest(path)
    if read_errors:
        return read_errors
    assert data is not None

    for field in (
        "schema_version",
        "active_area",
        "active_slice",
        "permission_state",
        "automation_mode",
        "actor_role",
        "handoff_target",
        "run_envelope",
        "decision_records",
        "scope_review",
        "source_authority",
        "allowed_files",
        "forbidden_actions",
        "validation_commands",
        "stop_conditions",
    ):
        if field not in data or data.get(field) in (None, ""):
            errors.append(f"active-slice manifest is missing {field}")
        elif field != "decision_records" and data.get(field) == []:
            errors.append(f"active-slice manifest is missing {field}")
    for field in ("source_authority", "allowed_files", "forbidden_actions", "validation_commands", "stop_conditions"):
        if not isinstance(data.get(field), list) or not data[field]:
            errors.append(f"active-slice manifest {field} must be a non-empty list")
    errors.extend(validate_run_envelope(data, attributes))
    errors.extend(validate_decision_records(data, implementation_action_requested(attributes)))
    scope_review = data.get("scope_review", {})
    if not isinstance(scope_review, dict):
        errors.append("active-slice manifest scope_review must be an object")
    else:
        for field in (
            "acceptance_criteria",
            "explicit_non_goals",
            "unrequested_behavior",
            "hidden_dependencies",
            "assumptions_that_entered_code",
        ):
            if not isinstance(scope_review.get(field), list):
                errors.append(f"active-slice manifest scope_review.{field} must be a list")
    errors.extend(validate_parent_closeout_reconciliation(data, attributes, require_complete=False))
    review = data.get("review", {})
    if not isinstance(review, dict):
        errors.append("active-slice manifest review must be an object")
    else:
        if not isinstance(review.get("required"), bool):
            errors.append("active-slice manifest review.required must be true or false")
        if review.get("status") not in REVIEW_STATUSES:
            errors.append("active-slice manifest review.status is invalid")
        if review.get("status") in {"approved", "changes_required"} and review.get("reviewed_sha") in {None, "", "none"}:
            errors.append("active-slice manifest review.reviewed_sha is required for completed reviews")
        if not isinstance(review.get("applies_to_active_slice"), bool):
            errors.append("active-slice manifest review.applies_to_active_slice must be true or false")
    if attributes.get("active_area") and data.get("active_area") != attributes["active_area"]:
        errors.append("active-slice manifest active_area must match current state")
    if attributes.get("active_slice") and data.get("active_slice") != attributes["active_slice"]:
        errors.append("active-slice manifest active_slice must match current state")
    if isinstance(review, dict) and attributes.get("review_required"):
        expected = attributes["review_required"] == "true"
        if isinstance(review.get("required"), bool) and review.get("required") is not expected:
            errors.append("active-slice manifest review.required must match current state")
    if isinstance(review, dict) and attributes.get("review_status") and review.get("status") != attributes["review_status"]:
        errors.append("active-slice manifest review.status must match current state")
    if isinstance(review, dict) and attributes.get("reviewed_sha") and str(review.get("reviewed_sha")) != attributes["reviewed_sha"]:
        errors.append("active-slice manifest review.reviewed_sha must match current state")
    if isinstance(review, dict) and attributes.get("review_applies_to_active_slice"):
        expected = attributes["review_applies_to_active_slice"] == "true"
        if isinstance(review.get("applies_to_active_slice"), bool) and review.get("applies_to_active_slice") is not expected:
            errors.append("active-slice manifest review.applies_to_active_slice must match current state")
    return errors


def validate_parent_closeout_reconciliation(
    data: dict[str, object],
    attributes: dict[str, str],
    require_complete: bool,
) -> list[str]:
    errors: list[str] = []
    if "parent_closeout_reconciliation" not in data:
        if require_complete or parent_mode_active(attributes):
            return ["active-slice manifest is missing parent_closeout_reconciliation"]
        return []

    reconciliation = data.get("parent_closeout_reconciliation", {})
    if not isinstance(reconciliation, dict):
        return ["active-slice manifest parent_closeout_reconciliation must be an object"]

    required_fields = (
        "required_before_parent_closeout",
        "status",
        "pr_head_sha",
        "local_head_sha",
        "local_branch_state",
        "current_inline_comments",
        "issue_comments",
        "required_checks",
        "conflicting_review_signals",
        "stale_closeout_detected",
        "evidence",
    )
    for field in required_fields:
        if field not in reconciliation:
            errors.append(f"active-slice manifest parent_closeout_reconciliation is missing {field}")

    if not isinstance(reconciliation.get("required_before_parent_closeout"), bool):
        errors.append("active-slice manifest parent_closeout_reconciliation.required_before_parent_closeout must be true or false")
    if reconciliation.get("status") not in PARENT_CLOSEOUT_STATUSES:
        errors.append("active-slice manifest parent_closeout_reconciliation.status is invalid")
    if not isinstance(reconciliation.get("conflicting_review_signals"), bool):
        errors.append("active-slice manifest parent_closeout_reconciliation.conflicting_review_signals must be true or false")
    if not isinstance(reconciliation.get("stale_closeout_detected"), bool):
        errors.append("active-slice manifest parent_closeout_reconciliation.stale_closeout_detected must be true or false")
    if not isinstance(reconciliation.get("evidence"), list):
        errors.append("active-slice manifest parent_closeout_reconciliation.evidence must be a list")

    if not require_complete:
        return errors

    if not parent_mode_active(attributes):
        return errors
    if reconciliation.get("required_before_parent_closeout") is not True:
        errors.append("parent closeout reconciliation must be required before parent closeout")
    if reconciliation.get("status") != "pass":
        errors.append("parent closeout reconciliation must pass before parent closeout")
    for field in (
        "pr_head_sha",
        "local_head_sha",
        "local_branch_state",
        "current_inline_comments",
        "issue_comments",
        "required_checks",
    ):
        if reconciliation.get(field) in (None, "", "not_checked", "unknown"):
            errors.append(f"parent closeout reconciliation must record {field}")
    if reconciliation.get("conflicting_review_signals") is True:
        errors.append("conflicting GitHub review signals make review-state ambiguous; stop instead of closing out or merging")
    if reconciliation.get("stale_closeout_detected") is True:
        errors.append("stale parent closeout detected; re-check current PR head, review signals, required checks, and local branch state")
    evidence = reconciliation.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("parent closeout reconciliation must include evidence")
    errors.extend(validate_parent_closeout_review_check_signals(reconciliation))
    errors.extend(validate_parent_closeout_live_git_state(reconciliation))
    return errors


def infer_recorded_dirty_state(recorded_state: object, live_status: str, live_dirty: bool) -> bool | None:
    if not isinstance(recorded_state, str):
        return None
    normalized = recorded_state.strip().lower()
    if not normalized:
        return None
    if recorded_state.strip() == live_status.strip():
        return live_dirty
    status_prefixes = (" M", "M ", " A", "A ", " D", "D ", " R", "R ", " C", "C ", "UU", "??")
    if any(line.startswith(status_prefixes) for line in recorded_state.splitlines()):
        return True
    dirty_markers = (
        "dirty",
        "uncommitted",
        "modified",
        "untracked",
        "changes not staged",
        "changes to be committed",
        "??",
        "\n m ",
        "\nm ",
        "\n a ",
        "\na ",
        "\n d ",
        "\nd ",
    )
    clean_markers = ("clean", "nothing to commit", "working tree clean")
    if any(marker in normalized for marker in dirty_markers):
        return True
    if any(marker in normalized for marker in clean_markers):
        return False
    return None


def field_is_not_applicable(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() == "not_applicable"


def normalize_closeout_signal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def closeout_signal_is_negated_clean(field: str, normalized: str) -> bool:
    allowed_tokens = PARENT_CLOSEOUT_SIGNAL_NEGATED_CLEAN_TOKENS.get(field)
    if not allowed_tokens:
        return False
    tokens = tuple(token for token in normalized.split("_") if token)
    if len(tokens) < 2 or tokens[0] not in PARENT_CLOSEOUT_SIGNAL_NEGATIONS:
        return False
    signal_tokens = tokens[1:]
    if any(token not in allowed_tokens for token in signal_tokens):
        return False
    return any(token in PARENT_CLOSEOUT_SIGNAL_NEGATED_CLEAN_NOUNS for token in signal_tokens)


def publication_stabilization_value_is_negated_clean(field: str, normalized: str) -> bool:
    allowed_tokens = PUBLICATION_STABILIZATION_NEGATED_CLEAN_TOKENS.get(field)
    if not allowed_tokens:
        return False
    tokens = tuple(token for token in normalized.split("_") if token)
    if len(tokens) < 2 or tokens[0] not in PARENT_CLOSEOUT_SIGNAL_NEGATIONS:
        return False
    signal_tokens = tokens[1:]
    if any(token not in allowed_tokens for token in signal_tokens):
        return False
    return any(token in PUBLICATION_STABILIZATION_NEGATED_CLEAN_NOUNS for token in signal_tokens)


def publication_stabilization_authority_value_is_blocking(value: object) -> bool:
    normalized = normalize_closeout_signal(str(value or ""))
    return normalized in PUBLICATION_STABILIZATION_AUTHORITY_BAD_VALUES or "not_applicable" in normalized or any(
        marker in normalized for marker in PUBLICATION_STABILIZATION_BLOCKER_MARKERS
    )


def publication_stabilization_review_count_is_valid(value: str) -> bool:
    normalized = normalize_closeout_signal(value)
    return re.match(r"^[1-9]\d*_(current_head_)?reviews?(_|$)", normalized) is not None


def validate_parent_closeout_review_check_signals(reconciliation: dict[str, object]) -> list[str]:
    errors: list[str] = []
    pr_head_not_applicable = field_is_not_applicable(reconciliation.get("pr_head_sha"))
    for field, pass_values in PARENT_CLOSEOUT_SIGNAL_PASS_VALUES.items():
        value = reconciliation.get(field)
        if value in (None, "", "not_checked", "unknown"):
            continue
        if not isinstance(value, str):
            errors.append(f"parent closeout reconciliation {field} must be text")
            continue
        normalized = normalize_closeout_signal(value)
        if normalized == "not_applicable":
            if not pr_head_not_applicable:
                errors.append(f"parent closeout reconciliation {field} may be not_applicable only for non-PR closeout")
            continue
        if normalized in pass_values or closeout_signal_is_negated_clean(field, normalized):
            continue
        if any(marker in normalized for marker in PARENT_CLOSEOUT_SIGNAL_BLOCKER_MARKERS):
            errors.append(f"parent closeout reconciliation {field} records blocker state: {value}")
        else:
            errors.append(f"parent closeout reconciliation {field} must record pass or not_applicable")
    return errors


def evidence_has_non_pr_closeout(evidence: object) -> bool:
    if not isinstance(evidence, list):
        return False
    evidence_text = "\n".join(str(item).lower() for item in evidence)
    normalized_evidence = normalize_closeout_signal(evidence_text)
    has_non_pr = any(marker in evidence_text for marker in ("non-pr", "non pr", "no pr", "no open pr"))
    return has_non_pr and "not_applicable" in normalized_evidence


def validate_publication_stabilization(reconciliation: dict[str, object], live_head: str) -> list[str]:
    errors: list[str] = []
    stabilization = reconciliation.get("publication_stabilization")
    if not isinstance(stabilization, dict):
        return ["parent closeout reconciliation publication_stabilization must be an object"]

    for field in PUBLICATION_STABILIZATION_FIELDS:
        if field not in stabilization:
            errors.append(f"parent closeout reconciliation publication_stabilization is missing {field}")

    pr_head_not_applicable = field_is_not_applicable(reconciliation.get("pr_head_sha"))
    if pr_head_not_applicable:
        return errors

    if stabilization.get("post_review_fix_reconciled") is not True:
        errors.append("parent closeout reconciliation publication_stabilization.post_review_fix_reconciled must be true")

    for field in ("pr_body_head_sha", "review_evidence_head_sha"):
        value = stabilization.get(field)
        if not isinstance(value, str):
            errors.append(f"parent closeout reconciliation publication_stabilization.{field} must be text")
        elif value.strip().lower() != live_head.lower():
            errors.append(f"parent closeout reconciliation publication_stabilization.{field} must match live HEAD")

    review_authority = stabilization.get("review_authority")
    if not isinstance(review_authority, str):
        errors.append("parent closeout reconciliation publication_stabilization.review_authority must be text")
    elif publication_stabilization_authority_value_is_blocking(review_authority):
        errors.append("parent closeout reconciliation publication_stabilization.review_authority must record the exact current-head review authority")

    review_authority_count = stabilization.get("review_authority_count")
    if not isinstance(review_authority_count, str):
        errors.append("parent closeout reconciliation publication_stabilization.review_authority_count must be text")
    elif (
        publication_stabilization_authority_value_is_blocking(review_authority_count)
        or not publication_stabilization_review_count_is_valid(review_authority_count)
    ):
        errors.append("parent closeout reconciliation publication_stabilization.review_authority_count must record the exact required review count")

    for field in ("metadata_only_check_retrigger", "bounded_wait_result"):
        value = stabilization.get(field)
        if not isinstance(value, str):
            errors.append(f"parent closeout reconciliation publication_stabilization.{field} must be text")
            continue
        normalized = normalize_closeout_signal(value)
        if publication_stabilization_value_is_negated_clean(field, normalized):
            continue
        if normalized in PUBLICATION_STABILIZATION_BAD_VALUES or any(
            marker in normalized for marker in PUBLICATION_STABILIZATION_BLOCKER_MARKERS
        ):
            errors.append(f"parent closeout reconciliation publication_stabilization.{field} records blocker state: {value}")

    return errors


def validate_parent_closeout_pr_head(reconciliation: dict[str, object], live_head: str) -> list[str]:
    errors: list[str] = []
    pr_head = reconciliation.get("pr_head_sha")
    if not isinstance(pr_head, str):
        return ["parent closeout reconciliation pr_head_sha must be text"]
    normalized = pr_head.strip().lower()
    if normalized in ("", "not_checked", "unknown"):
        return errors
    if normalized == "not_applicable":
        for field in ("current_inline_comments", "issue_comments", "required_checks"):
            if not field_is_not_applicable(reconciliation.get(field)):
                errors.append(f"parent closeout reconciliation {field} must be not_applicable when pr_head_sha is not_applicable")
        if not evidence_has_non_pr_closeout(reconciliation.get("evidence")):
            errors.append("parent closeout reconciliation must include explicit non-PR evidence when pr_head_sha is not_applicable")
        return errors
    if normalized != live_head.lower():
        errors.append("parent closeout reconciliation pr_head_sha must match live HEAD for PR closeout")
    return errors


def validate_parent_closeout_live_git_state(reconciliation: dict[str, object]) -> list[str]:
    errors: list[str] = []
    live_head = run_git("rev-parse", "HEAD")
    recorded_head = reconciliation.get("local_head_sha")
    if live_head:
        if not isinstance(recorded_head, str):
            errors.append("parent closeout reconciliation local_head_sha must be text")
        elif recorded_head not in ("", "not_checked", "unknown"):
            if recorded_head.strip().lower() != live_head.lower():
                errors.append("parent closeout reconciliation local_head_sha must match live HEAD")
        errors.extend(validate_parent_closeout_pr_head(reconciliation, live_head))
        errors.extend(validate_publication_stabilization(reconciliation, live_head))
    else:
        errors.append("parent closeout reconciliation cannot verify live HEAD")

    live_status = run_git("status", "-sb")
    live_dirty = bool(run_git("status", "--porcelain"))
    recorded_dirty = infer_recorded_dirty_state(reconciliation.get("local_branch_state"), live_status, live_dirty)
    if recorded_dirty is None:
        errors.append("parent closeout reconciliation local_branch_state must record clean, dirty, or exact live git status")
    elif recorded_dirty is not live_dirty:
        expected = "dirty" if live_dirty else "clean"
        errors.append(f"parent closeout reconciliation local_branch_state must match live working tree state: {expected}")
    return errors


def validate_required_review_for_closeout(
    data: dict[str, object],
    attributes: dict[str, str],
    head: str | None = None,
) -> list[str]:
    errors: list[str] = []
    review = data.get("review", {})
    manifest_review_required = isinstance(review, dict) and review.get("required") is True
    state_review_required = attributes.get("review_required") == "true"
    if not (manifest_review_required or state_review_required):
        return errors

    statuses: list[object] = []
    applies_values: list[object] = []
    reviewed_shas: list[str] = []
    if manifest_review_required and isinstance(review, dict):
        statuses.append(review.get("status"))
        applies_values.append(review.get("applies_to_active_slice"))
        reviewed_shas.append(str(review.get("reviewed_sha") or "none"))
    if state_review_required:
        statuses.append(attributes.get("review_status"))
        applies_values.append(attributes.get("review_applies_to_active_slice") == "true")
        reviewed_shas.append(attributes.get("reviewed_sha", "none"))

    if any(status != "approved" for status in statuses):
        errors.append("required active-slice review must be approved before parent closeout")
    if any(applies is not True for applies in applies_values):
        errors.append("required active-slice review must apply to the active slice before parent closeout")
    if any(reviewed_sha in {"", "none"} for reviewed_sha in reviewed_shas):
        errors.append("required active-slice review must record reviewed_sha before parent closeout")
    elif head and head != "(unavailable)":
        if any(reviewed_sha != head for reviewed_sha in reviewed_shas):
            errors.append("required active-slice review reviewed_sha must match current HEAD before parent closeout")
    else:
        errors.append("required active-slice review cannot be verified without current HEAD before parent closeout")
    return errors


def active_slice_allows_code(path: Path, attributes: dict[str, str] | None = None, head: str | None = None) -> tuple[bool, list[str]]:
    data, read_errors = read_active_slice_manifest(path)
    if read_errors:
        return False, read_errors
    assert data is not None
    attributes = attributes or {}
    errors: list[str] = []
    if data.get("permission_state") not in {"coding_allowed", "same_slice_only"}:
        errors.append("active-slice manifest permission_state does not allow coding")

    review = data.get("review", {})
    manifest_review_required = isinstance(review, dict) and review.get("required") is True
    state_review_required = attributes.get("review_required") == "true"
    if manifest_review_required or state_review_required:
        review_status = review.get("status") if isinstance(review, dict) else attributes.get("review_status")
        if review_status != "approved":
            errors.append("required active-slice review must be approved before coding")
        applies = review.get("applies_to_active_slice") if isinstance(review, dict) else None
        if applies is not True or attributes.get("review_applies_to_active_slice") != "true":
            errors.append("required active-slice review must apply to the active slice")
        reviewed_sha = str(review.get("reviewed_sha") or attributes.get("reviewed_sha") or "none") if isinstance(review, dict) else attributes.get("reviewed_sha", "none")
        if reviewed_sha in {"", "none"}:
            errors.append("required active-slice review must record reviewed_sha")
        elif head and head != "(unavailable)" and reviewed_sha != head:
            errors.append("required active-slice review reviewed_sha must match current HEAD")
        elif not head or head == "(unavailable)":
            errors.append("required active-slice review cannot be verified without current HEAD")
    return not errors, errors


def active_slice_allows_paths(path: Path, paths: list[str]) -> tuple[bool, list[str]]:
    data, read_errors = read_active_slice_manifest(path)
    if read_errors:
        return False, read_errors
    assert data is not None
    patterns = data.get("allowed_files")
    if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
        return False, ["active-slice manifest allowed_files must be a list of strings"]
    outside = [repo_path for repo_path in paths if not path_matches_allowed(repo_path, patterns)]
    if outside:
        return False, [f"changed file is outside active-slice allowed_files: {repo_path}" for repo_path in outside]
    return True, []


def validate_decision_records(data: dict[str, object], implementation_requested: bool = False) -> list[str]:
    errors: list[str] = []
    records = data.get("decision_records", [])
    if not isinstance(records, list):
        return ["active-slice manifest decision_records must be a list"]
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append(f"decision record {index} must be an object")
            continue
        status = str(record.get("status", "")).strip()
        if status not in DECISION_STATUSES:
            errors.append(f"decision record {index} status is invalid")
        if implementation_requested and record.get("material") is True and status in {"proposed", "needs_human"}:
            errors.append(f"implementation is blocked: material decision is unresolved: {record.get('decision', f'record {index}')}")
        for field in (
            "decision",
            "alternatives_rejected",
            "reason",
            "owner",
            "approver",
            "revisit_trigger",
            "authority_source",
            "evidence_test",
        ):
            if record.get(field) in (None, ""):
                errors.append(f"decision record {index} is missing {field}")
    return errors


def validate_run_envelope(data: dict[str, object], attributes: dict[str, str]) -> list[str]:
    errors: list[str] = []
    mode = str(data.get("automation_mode", attributes.get("automation_mode", "off")))
    actor = str(data.get("actor_role", attributes.get("actor_role", "single_session")))
    handoff_target = str(data.get("handoff_target", attributes.get("handoff_target", "manual_next_session")))
    if mode not in AUTOMATION_MODES:
        errors.append("active-slice manifest automation_mode is invalid")
    if actor not in ACTOR_ROLES:
        errors.append("active-slice manifest actor_role is invalid")
    if handoff_target not in HANDOFF_TARGETS:
        errors.append("active-slice manifest handoff_target is invalid")
    if attributes.get("automation_mode") and mode != attributes["automation_mode"]:
        errors.append("active-slice manifest automation_mode must match current state")
    if attributes.get("actor_role") and actor != attributes["actor_role"]:
        errors.append("active-slice manifest actor_role must match current state")
    if attributes.get("handoff_target") and handoff_target != attributes["handoff_target"]:
        errors.append("active-slice manifest handoff_target must match current state")
    if mode != "parent_orchestrator" and handoff_target == "parent":
        errors.append("handoff_target parent requires parent_orchestrator automation_mode")
    if mode == "parent_orchestrator" and handoff_target == "manual_next_session":
        errors.append("parent_orchestrator automation must route child handoffs to parent unless a stop condition fired")

    envelope = data.get("run_envelope", {})
    if not isinstance(envelope, dict):
        return errors + ["active-slice manifest run_envelope must be an object"]
    for field in RUN_ENVELOPE_FIELDS:
        if envelope.get(field) in (None, "", []):
            errors.append(f"active-slice manifest run_envelope is missing {field}")
    if envelope.get("handoff_target") and str(envelope.get("handoff_target")) != handoff_target:
        errors.append("active-slice manifest run_envelope.handoff_target must match handoff_target")
    if not isinstance(envelope.get("stop_conditions"), list) or not envelope["stop_conditions"]:
        errors.append("active-slice manifest run_envelope.stop_conditions must be a non-empty list")
    if not isinstance(envelope.get("control_only_pr_authorized"), bool):
        errors.append("active-slice manifest run_envelope.control_only_pr_authorized must be true or false")
    if mode == "parent_orchestrator":
        max_children = envelope.get("max_child_sessions")
        used_children = envelope.get("child_sessions_used", 0)
        if not isinstance(max_children, int) or max_children < 1:
            errors.append("parent_orchestrator run_envelope max_child_sessions must be a positive integer")
        if not isinstance(used_children, int) or used_children < 0:
            errors.append("parent_orchestrator run_envelope child_sessions_used must be a non-negative integer")
        if isinstance(max_children, int) and isinstance(used_children, int) and max_children >= 0 and used_children > max_children:
            errors.append("parent_orchestrator run_envelope child_sessions_used exceeds max_child_sessions")
    return errors


def docs_only_control_change(paths: list[str]) -> bool:
    return bool(paths) and all(
        any(path_matches_allowed(path, [pattern]) for pattern in COORDINATION_ONLY_PATTERNS)
        for path in paths
    )


def implementation_action_requested(attributes: dict[str, str]) -> bool:
    action = attributes.get("next_action", "")
    return action in PARENT_FORBIDDEN_ACTIONS or bool(IMPLEMENTATION_ACTION.search(action))


def parent_mode_active(attributes: dict[str, str]) -> bool:
    return (
        attributes.get("automation_mode") == "parent_orchestrator"
        and attributes.get("actor_role") == "parent"
    )


def parent_handoff_required(attributes: dict[str, str]) -> bool:
    return parent_mode_active(attributes) and attributes.get("handoff_target") == "parent"


def parent_changed_file_errors(paths: list[str], attributes: dict[str, str]) -> list[str]:
    if not parent_mode_active(attributes):
        return []
    if docs_only_control_change(paths):
        return []
    return [f"parent/orchestrator actor cannot change implementation file: {path}" for path in paths]


def diff_paths(base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or "unknown git error"
        raise RuntimeError(f"git diff failed for base ref {base_ref}: {detail}")
    output = result.stdout
    return sorted({normalize_repo_path(line) for line in output.splitlines() if line.strip()})


def control_only_pr_authorized(active_manifest: Path) -> bool:
    data, read_errors = read_active_slice_manifest(active_manifest)
    if read_errors or data is None:
        return False
    envelope = data.get("run_envelope", {})
    return isinstance(envelope, dict) and envelope.get("control_only_pr_authorized") is True


def validate_state() -> list[str]:
    errors: list[str] = []
    attributes, _, content = read_state()
    if not content:
        return [f"required current-state file is missing: {STATE_PATH}"]
    for field in REQUIRED_STATE_FIELDS:
        if not attributes.get(field):
            errors.append(f"current state is missing frontmatter field: {field}")
    for heading in REQUIRED_STATE_HEADINGS:
        if heading not in content:
            errors.append(f"current state is missing heading: {heading}")
    for field in ("next_high_risk", "next_session_required_before_next_slice"):
        if attributes.get(field) not in {"true", "false"}:
            errors.append(f"current state {field} must be true or false")
    if attributes.get("automation_mode") not in AUTOMATION_MODES:
        errors.append("current state automation_mode is invalid")
    if attributes.get("actor_role") not in ACTOR_ROLES:
        errors.append("current state actor_role is invalid")
    if attributes.get("handoff_target") not in HANDOFF_TARGETS:
        errors.append("current state handoff_target is invalid")
    if attributes.get("stop_latch") not in {"true", "false"}:
        errors.append("current state stop_latch must be true or false")
    if attributes.get("stop_latch") == "true":
        errors.append("stop latch is active; no further automation or implementation is permitted")
    if attributes.get("automation_mode") != "parent_orchestrator" and attributes.get("handoff_target") == "parent":
        errors.append("current state handoff_target parent requires parent_orchestrator automation_mode")
    if (
        parent_mode_active(attributes)
        and implementation_action_requested(attributes)
    ):
        errors.append(f"parent/orchestrator actor cannot perform next_action: {attributes.get('next_action')}")
    for field in ("review_required", "review_applies_to_active_slice"):
        if attributes.get(field) not in {"true", "false"}:
            errors.append(f"current state {field} must be true or false")
    if attributes.get("review_status") not in REVIEW_STATUSES:
        errors.append("current state review_status is invalid")
    if attributes.get("review_status") in {"approved", "changes_required"} and attributes.get("reviewed_sha") == "none":
        errors.append("current state reviewed_sha is required for completed reviews")

    active_manifest = Path(attributes.get("permission_manifest", str(DEFAULT_ACTIVE_SLICE_MANIFEST_PATH)))
    errors.extend(validate_active_slice_manifest(active_manifest, attributes))

    handoff_path = attributes.get("last_handoff", "none")
    if handoff_path != "none":
        handoff = Path(handoff_path)
        if not handoff.exists():
            errors.append(f"latest handoff does not exist: {handoff}")
        else:
            handoff_text = handoff.read_text(encoding="utf-8")
            if "[Agent must" in handoff_text:
                errors.append(f"latest handoff contains generated placeholders: {handoff}")
            required_handoff_headings = set(REQUIRED_HANDOFF_HEADINGS)
            if parent_handoff_required(attributes):
                required_handoff_headings.update(PARENT_CLOSEOUT_HANDOFF_HEADINGS)
            for heading in required_handoff_headings:
                if heading not in handoff_text:
                    errors.append(f"latest handoff is missing '{heading}': {handoff}")

    if attributes.get("next_action") == "code":
        manifest_path = Path(attributes.get("workflow_manifest", str(DEFAULT_MANIFEST_PATH)))
        allowed, manifest_errors = manifest_allows_code(manifest_path)
        if not allowed:
            errors.extend(f"implementation is blocked: {error}" for error in manifest_errors)
        active_allowed, active_errors = active_slice_allows_code(
            active_manifest,
            attributes,
            repo_state()["head"],
        )
        if not active_allowed:
            errors.extend(f"implementation is blocked: {error}" for error in active_errors)
        changed_paths = changed_repo_paths()
        if changed_paths:
            paths_allowed, path_errors = active_slice_allows_paths(active_manifest, changed_paths)
            if not paths_allowed:
                errors.extend(f"implementation is blocked: {error}" for error in path_errors)
    return errors


def load_active_slice_template() -> dict[str, object]:
    active_template = Path(__file__).resolve().parent.parent / "assets" / "active-slice-manifest.template.json"
    if active_template.exists():
        return json.loads(active_template.read_text(encoding="utf-8"))
    return dict(DEFAULT_ACTIVE_SLICE_MANIFEST)


def merge_missing_defaults(data: dict[str, object], defaults: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    merged = dict(data)
    added: list[str] = []
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
            added.append(key)
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            child, child_added = merge_missing_defaults(merged[key], value)
            merged[key] = child
            added.extend(f"{key}.{item}" for item in child_added)
    return merged, added


def write_default_active_slice_manifest(path: Path, attributes: dict[str, str]) -> None:
    active_manifest = load_active_slice_template()
    active_manifest["last_updated"] = dt.date.today().isoformat()
    if attributes.get("active_area"):
        active_manifest["active_area"] = attributes["active_area"]
    if attributes.get("active_slice"):
        active_manifest["active_slice"] = attributes["active_slice"]
    if attributes.get("automation_mode"):
        active_manifest["automation_mode"] = attributes["automation_mode"]
    if attributes.get("actor_role"):
        active_manifest["actor_role"] = attributes["actor_role"]
    if attributes.get("handoff_target"):
        active_manifest["handoff_target"] = attributes["handoff_target"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(active_manifest, indent=2) + "\n", encoding="utf-8")


def command_init(_: argparse.Namespace) -> int:
    if STATE_PATH.exists():
        print(f"Refusing to overwrite existing current state: {STATE_PATH}")
        return 1
    if DEFAULT_ACTIVE_SLICE_MANIFEST_PATH.exists():
        print(f"Refusing to overwrite existing active-slice manifest: {DEFAULT_ACTIVE_SLICE_MANIFEST_PATH}")
        return 1
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).resolve().parent.parent / "assets" / "current-state-template.md"
    if template.exists():
        shutil.copyfile(template, STATE_PATH)
    else:
        STATE_PATH.write_text(DEFAULT_STATE_TEMPLATE, encoding="utf-8")
    attributes, body, _ = read_state()
    attributes["last_updated"] = dt.date.today().isoformat()
    write_state(attributes, body)
    write_default_active_slice_manifest(DEFAULT_ACTIVE_SLICE_MANIFEST_PATH, attributes)
    print(f"Created {STATE_PATH}")
    print(f"Created {DEFAULT_ACTIVE_SLICE_MANIFEST_PATH}")
    return 0


def command_repair(_: argparse.Namespace) -> int:
    attributes, body, content = read_state()
    if not content:
        print(f"ERROR: required current-state file is missing: {STATE_PATH}")
        print("Run `python scripts/agent/session_continuity.py init` for a new project.")
        return 1

    default_attributes, _ = parse_frontmatter(DEFAULT_STATE_TEMPLATE)
    changed_fields: list[str] = []
    for field in REQUIRED_STATE_FIELDS:
        if not attributes.get(field):
            attributes[field] = default_attributes.get(field, "none")
            changed_fields.append(field)
    if changed_fields:
        attributes["last_updated"] = dt.date.today().isoformat()
        write_state(attributes, body)
        print(f"Updated missing current-state fields: {', '.join(changed_fields)}")

    active_manifest = Path(attributes.get("permission_manifest", str(DEFAULT_ACTIVE_SLICE_MANIFEST_PATH)))
    if active_manifest.exists():
        print(f"Active-slice manifest already exists: {active_manifest}")
        data, read_errors = read_active_slice_manifest(active_manifest)
        if read_errors:
            print(f"ERROR: {read_errors[0]}")
            return 1
        assert data is not None
        merged, added = merge_missing_defaults(data, load_active_slice_template())
        if added:
            merged["last_updated"] = dt.date.today().isoformat()
            active_manifest.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
            print(f"Updated missing active-slice manifest fields: {', '.join(added)}")
    else:
        write_default_active_slice_manifest(active_manifest, attributes)
        print(f"Created {active_manifest}")
    return 0


def command_start(args: argparse.Namespace) -> int:
    if args.start_new and args.continue_slice:
        print("ERROR: choose only one of --start-new or --continue-slice")
        return 1
    mode = "continue-slice" if args.continue_slice else "start-new"
    if not args.no_fetch and run_git("remote", "get-url", "origin"):
        try:
            subprocess.run(["git", "fetch", "origin", "--prune"], check=True)
        except subprocess.CalledProcessError:
            print("START GATE: BLOCKED")
            print("git fetch origin failed. Remote state is unverified.")
            return 2

    state = repo_state()
    if args.pull_after_inspection and state["remote_ref"] != "(unavailable)" and not state["dirty"] and state["ahead"] == 0 and state["behind"] > 0:
        subprocess.run(["git", "pull", "--ff-only"], check=True)
        state = repo_state()

    attributes, _, _ = read_state()
    print(
        "Project session-start report\n"
        "============================\n"
        f"Mode: {mode}\nBranch: {state['branch']}\nHEAD: {state['head']}\nRemote baseline: {state['remote_ref']} at {state['remote_head']}\n"
        f"Ahead/behind remote baseline: {state['ahead']}/{state['behind']}\n"
        f"Working tree dirty: {'yes' if state['dirty'] else 'no'}\n\n"
        f"Git status:\n{state['status']}\n\n"
        f"Coordination state:\n- active: {attributes.get('active_area', '(missing)')} / {attributes.get('active_slice', '(missing)')}\n"
        f"- next: {attributes.get('next_area', '(missing)')} / {attributes.get('next_slice', '(missing)')}\n"
        f"- next_action: {attributes.get('next_action', '(missing)')}\n"
        f"- automation_mode: {attributes.get('automation_mode', '(missing)')}\n"
        f"- actor_role: {attributes.get('actor_role', '(missing)')}\n"
        f"- handoff_target: {attributes.get('handoff_target', '(missing)')}\n"
        f"- workflow_manifest: {attributes.get('workflow_manifest', '(missing)')}\n\n"
        f"- permission_manifest: {attributes.get('permission_manifest', '(missing)')}\n"
        f"- review_status: {attributes.get('review_status', '(missing)')}\n\n"
        "Required reading order:\n1. AGENTS.md and closest scoped instructions\n2. CLAUDE.md when applicable\n"
        "3. docs/delivery/current-state.md\n4. active-slice manifest\n5. latest handoff\n6. workflow manifest\n7. task-controlling sources"
    )

    errors = validate_state()
    if state["dirty"] and state["behind"] > 0:
        errors.append("working tree is dirty while the tracked remote has incoming commits")
    if mode == "start-new" and state["dirty"]:
        errors.append("new-session start requires a clean working tree; use --continue-slice only for the same bounded active slice after inspecting local changes")
    if mode == "continue-slice" and state["dirty"]:
        active_manifest = Path(attributes.get("permission_manifest", str(DEFAULT_ACTIVE_SLICE_MANIFEST_PATH)))
        changed_paths = changed_repo_paths()
        paths_allowed, path_errors = active_slice_allows_paths(active_manifest, changed_paths)
        if not paths_allowed:
            errors.extend(path_errors)
        errors.extend(parent_changed_file_errors(changed_paths, attributes))
    if errors:
        print("START GATE: BLOCKED")
        for error in errors:
            print(f"- {error}")
        return 2
    if state["behind"] > 0:
        print("START GATE: INSPECTION_REQUIRED")
        print("Inspect incoming commits and relevant diffs before pulling or editing.")
        return 2
    print("START GATE: PASS")
    return 0


def command_decide(args: argparse.Namespace) -> int:
    attributes, _, _ = read_state()
    state = repo_state()
    triggers: list[str] = []
    area_changed = attributes.get("active_area") != attributes.get("next_area")
    high_risk_next = attributes.get("next_high_risk") == "true" or HIGH_RISK.search(
        f"{attributes.get('next_area', '')} {attributes.get('next_slice', '')}"
    )
    if attributes.get("next_session_required_before_next_slice") == "true":
        triggers.append("current state requires a new session")
    if args.event in {"post-merge", "post-rewrite"}:
        triggers.append(f"Git lifecycle event '{args.event}' changed the baseline")
    if args.corrections >= 2:
        triggers.append("two or more context-related corrections occurred")
    if args.context_stale:
        triggers.append("current context is stale")
    if args.parallel:
        triggers.append("work will split across agents")
    if state["behind"] > 0:
        triggers.append("the tracked remote has incoming commits")

    if not triggers:
        print("SESSION DECISION: CONTINUE_CURRENT_SESSION")
        print("Continue only for the same bounded slice and its review, fixes, or validation.")
        print("Coordination drift alone is not a review trigger. Classify review need from the actual diff, controlled-source risk, or explicit user instruction.")
        if attributes.get("automation_mode") == "parent_orchestrator" and attributes.get("handoff_target") == "parent":
            print("Recommended Next Action: parent may consume child handoffs internally and continue only to the next authorized child task.")
        else:
            print("Recommended Next Action: continue the current bounded slice; do not request review, handoff, or a new session from coordination drift alone.")
        if area_changed:
            print("Area change detected. Rerun the start gate, reread controlling docs, update the active-slice manifest, and get explicit authorization before implementation.")
        if high_risk_next:
            print("High-risk next slice detected. Fresh controls and explicit authorization are required, but risk alone does not require another chat.")
        return 0
    if attributes.get("automation_mode") == "parent_orchestrator" and attributes.get("handoff_target") == "parent":
        print("SESSION DECISION: FRESH_CHILD_OR_GATE_REQUIRED")
        for trigger in triggers:
            print(f"- {trigger}")
        print("Recommended Next Action: parent/orchestrator consumes the handoff internally, reruns the fresh gate, and starts only the next authorized child task unless a stop condition or missing approval blocks continuation.")
        return 0
    print("SESSION DECISION: NEW_SESSION_REQUIRED")
    for trigger in triggers:
        print(f"- {trigger}")
    print("Recommended Next Action: create or update the persistent handoff and include its complete paste-ready prompt in the final response.")
    return 0


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("topic must contain a letter or number")
    return slug


def command_handoff(args: argparse.Namespace) -> int:
    attributes, body, _ = read_state()
    state = repo_state()
    target = args.target or attributes.get("handoff_target", "manual_next_session")
    if target not in HANDOFF_TARGETS:
        print(f"ERROR: invalid handoff target: {target}")
        return 1
    if target == "parent" and attributes.get("automation_mode") != "parent_orchestrator":
        print("ERROR: handoff target parent requires automation_mode parent_orchestrator")
        return 1
    boundary_decision = (
        "Parent/orchestrator consumes this handoff internally. No user-facing next-session prompt is required unless a stop condition fired."
        if target == "parent"
        else "New session required before the next slice."
    )
    output = Path("docs/history") / f"{dt.date.today().isoformat()}-project-session-handoff-{slugify(args.topic)}.md"
    content = f"""# Project Session Handoff: {args.topic}

Date: {dt.date.today().isoformat()}
Handoff reason: {args.reason}

## Session Boundary Decision
- Decision: {boundary_decision}
- Trigger: {args.reason}
- Next slice: {args.next}

## Handoff Routing
- Target: {target}
- Automation mode: {attributes.get('automation_mode', 'off')}
- Actor role: {attributes.get('actor_role', 'single_session')}
- Parent consumes next: {'yes' if target == 'parent' else 'no'}
- Prompt is fallback only: {'yes' if target == 'parent' else 'no'}

## Git State
- Branch: `{state['branch']}`
- Local HEAD: `{state['head']}`
- Remote baseline: `{state['remote_ref']}` at `{state['remote_head']}`
- Ahead / behind remote baseline: {state['ahead']} / {state['behind']}

```text
{state['status']}
```

## Work Completed
- [Agent must replace this line with completed work grouped by area.]

## Validation Run
- [Agent must list every validation command and result.]
- [Agent must list checks not run, missing, or blocked and why.]

## Source Documents Read Or Changed
- [Agent must list task-controlling sources.]

## Candidate Decisions Still Not Final
- [Agent must list candidates or state that none were identified.]

## Risks And Blockers
- [Agent must list material risks, blockers, and unavailable checks.]

## Work Explicitly Not Done
- [Agent must list excluded or deferred work.]

## Recommended Next Slice
{args.next}

## Recommended Next Action
{args.next}

## Paste-Ready Next-Session Prompt
```text
{"Fallback only if parent automation tooling is unavailable or the parent run envelope is no longer authorized." if target == "parent" else "Use this prompt to continue manually."}

Continue this project in the repository:
{Path.cwd().as_posix()}

First run:
python scripts/agent/session_continuity.py start --start-new

Then read:
1. AGENTS.md and the closest scoped AGENTS.md files
2. docs/delivery/current-state.md
3. docs/delivery/active-slice-manifest.json
4. the latest handoff referenced by current state
5. project-documentation-manifest.json
6. the task-controlling docs

Continue only from this exact next permitted slice:
{args.next}

Stop if the session-start gate blocks, the workflow manifest blocks the requested action, required sources are missing, or coding is not explicitly permitted.
```

## Resume Instructions For The Next Agent
1. Start a new session in this repository.
2. Paste the prompt above.
3. Run `python scripts/agent/session_continuity.py start --start-new` as the first command inside that session.
4. Read agent instructions, current state, active-slice manifest, latest handoff, workflow manifest, and controlling sources.
5. Confirm the exact next permitted action before editing.
6. Do not duplicate completed work or bypass a blocked documentation phase.

## Parent-Orchestrator Closeout Reconciliation
Complete this section before a parent/orchestrator final closeout.

| Check | Evidence | Status |
|---|---|---|
| Current PR head was re-checked |  | Pass, Block, Ambiguous, or Not applicable |
| Current-head inline comments were checked |  | Pass, Block, Ambiguous, or Not applicable |
| Issue comments were checked |  | Pass, Block, Ambiguous, or Not applicable |
| Required checks were checked |  | Pass, Block, Ambiguous, or Not applicable |
| Local branch, local HEAD, and working tree were checked |  | Pass, Block, Ambiguous, or Not applicable |
| Stale closeout was ruled out |  | Pass, Block, Ambiguous, or Not applicable |

If current-head inline findings conflict with a later no-major-issues summary, classify review state as ambiguous and stop.
"""
    if not args.write:
        print(content)
        return 0
    if output.exists():
        print(f"Refusing to overwrite existing handoff: {output}")
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    attributes["last_updated"] = dt.date.today().isoformat()
    attributes["last_handoff"] = output.as_posix()
    attributes["handoff_target"] = target
    attributes["next_session_required_before_next_slice"] = "false" if target == "parent" else "true"
    attributes["next_slice"] = args.next
    write_state(attributes, body)
    print(f"Created {output}")
    print("Replace every generated placeholder before treating the handoff as complete.")
    return 0


def command_validate(_: argparse.Namespace) -> int:
    errors = validate_state()
    if errors:
        print("SESSION CONTINUITY: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("SESSION CONTINUITY: PASS")
    return 0


def command_closeout_check(_: argparse.Namespace) -> int:
    attributes, _, content = read_state()
    if not content:
        print(f"ERROR: required current-state file is missing: {STATE_PATH}")
        return 1
    active_manifest = Path(attributes.get("permission_manifest", str(DEFAULT_ACTIVE_SLICE_MANIFEST_PATH)))
    errors = validate_state()
    data, read_errors = read_active_slice_manifest(active_manifest)
    if read_errors:
        for error in read_errors:
            if error not in errors:
                errors.append(error)
    else:
        assert data is not None
        for error in validate_required_review_for_closeout(data, attributes, run_git("rev-parse", "HEAD")):
            if error not in errors:
                errors.append(error)
        for error in validate_parent_closeout_reconciliation(data, attributes, require_complete=True):
            if error not in errors:
                errors.append(error)
    if errors:
        print("PARENT CLOSEOUT CHECK: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    if parent_mode_active(attributes):
        print("PARENT CLOSEOUT CHECK: PASS")
        print("Final parent closeout may report only after the recorded live PR head, review signals, required checks, and local branch state are still current.")
    else:
        print("PARENT CLOSEOUT CHECK: NOT_APPLICABLE")
        print("Parent/orchestrator mode is not active.")
    return 0


def command_diff_check(args: argparse.Namespace) -> int:
    attributes, _, _ = read_state()
    active_manifest = Path(attributes.get("permission_manifest", str(DEFAULT_ACTIVE_SLICE_MANIFEST_PATH)))
    paths = diff_paths(args.base)
    errors: list[str] = []
    if not paths:
        print("DIFF CHECK: PASS")
        print(f"No changed files since {args.base}.")
        return 0
    paths_allowed, path_errors = active_slice_allows_paths(active_manifest, paths)
    if not paths_allowed:
        errors.extend(path_errors)
    errors.extend(parent_changed_file_errors(paths, attributes))
    if docs_only_control_change(paths) and not (args.control_pr_authorized or control_only_pr_authorized(active_manifest)):
        errors.append("docs-only slice-selection/current-state PR is blocked unless explicitly authorized")
    if errors:
        print("DIFF CHECK: FAIL")
        for path in paths:
            print(f"- changed: {path}")
        for error in errors:
            print(f"- {error}")
        return 1
    print("DIFF CHECK: PASS")
    for path in paths:
        print(f"- changed: {path}")
    return 0


def command_stop_latch(args: argparse.Namespace) -> int:
    attributes, body, _ = read_state()
    if not body:
        print(f"ERROR: required current-state file is missing: {STATE_PATH}")
        return 1
    attributes["stop_latch"] = "true"
    attributes["next_action"] = "stop"
    attributes["next_session_required_before_next_slice"] = "false"
    if args.reason:
        attributes["workflow_state"] = f"stopped-{slugify(args.reason)}"
    write_state(attributes, body)
    print("STOP LATCH: ACTIVE")
    print("No further automation or implementation is permitted until a human explicitly clears the latch.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init").set_defaults(func=command_init)
    subparsers.add_parser("repair").set_defaults(func=command_repair)
    start = subparsers.add_parser("start")
    start.add_argument("--no-fetch", action="store_true")
    start.add_argument("--start-new", action="store_true")
    start.add_argument("--continue-slice", action="store_true")
    start.add_argument("--pull-after-inspection", action="store_true")
    start.set_defaults(func=command_start)
    decide = subparsers.add_parser("decide")
    decide.add_argument("--event", default="manual-check")
    decide.add_argument("--corrections", type=int, default=0)
    decide.add_argument("--context-stale", action="store_true")
    decide.add_argument("--parallel", action="store_true")
    decide.set_defaults(func=command_decide)
    handoff = subparsers.add_parser("handoff")
    handoff.add_argument("--topic", required=True)
    handoff.add_argument("--reason", required=True)
    handoff.add_argument("--next", required=True)
    handoff.add_argument("--target", choices=sorted(HANDOFF_TARGETS))
    handoff.add_argument("--write", action="store_true")
    handoff.set_defaults(func=command_handoff)
    diff_check = subparsers.add_parser("diff-check")
    diff_check.add_argument("--base", required=True)
    diff_check.add_argument("--control-pr-authorized", action="store_true")
    diff_check.set_defaults(func=command_diff_check)
    stop_latch = subparsers.add_parser("stop-latch")
    stop_latch.add_argument("--reason", default="user-stop")
    stop_latch.set_defaults(func=command_stop_latch)
    subparsers.add_parser("closeout-check").set_defaults(func=command_closeout_check)
    subparsers.add_parser("validate").set_defaults(func=command_validate)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
