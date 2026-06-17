#!/usr/bin/env python3
"""Cross-platform project session continuity and handoff gate."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


STATE_PATH = Path("docs/delivery/current-state.md")
DEFAULT_MANIFEST_PATH = Path("project-documentation-manifest.json")
DEFAULT_ACTIVE_SLICE_MANIFEST_PATH = Path("docs/delivery/active-slice-manifest.json")
REQUIRED_STATE_FIELDS = {
    "state_version",
    "last_updated",
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
}
REQUIRED_STATE_HEADINGS = {
    "## Current Verified Repository State",
    "## Active Work",
    "## Next Permitted Action",
    "## Work Explicitly Not Started",
    "## Candidate Decisions Still Not Final",
    "## Risks And Blockers",
    "## New Session Decision",
    "## New Session Start Instructions",
    "## Update Contract",
}
REQUIRED_HANDOFF_HEADINGS = {
    "## Session Boundary Decision",
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
READY_TO_CODE = {"approved", "completed"}
REVIEW_STATUSES = {"not_started", "pending", "approved", "changes_required", "bypassed", "not_required"}
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
DEFAULT_STATE_TEMPLATE = """---
state_version: 1
last_updated: 1970-01-01
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

## Risks And Blockers
- Record material risks, blocked checks, and unresolved conflicts.

## New Session Decision
- Continue only while work remains the same bounded slice.

## New Session Start Instructions
```text
Paste the latest handoff's next-session prompt into a new Codex chat. First run the project session-start gate. Then read current state, its latest handoff, the workflow manifest, and controlling sources. Continue only from the exact next permitted action and stop if the workflow manifest blocks it.
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


def validate_active_slice_manifest(path: Path, attributes: dict[str, str] | None = None) -> list[str]:
    errors: list[str] = []
    attributes = attributes or {}
    if not path.exists():
        return [f"active-slice manifest not found: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"active-slice manifest cannot be read: {exc}"]

    for field in (
        "schema_version",
        "active_area",
        "active_slice",
        "permission_state",
        "source_authority",
        "allowed_files",
        "forbidden_actions",
        "validation_commands",
        "stop_conditions",
    ):
        if data.get(field) in (None, "", []):
            errors.append(f"active-slice manifest is missing {field}")
    for field in ("source_authority", "allowed_files", "forbidden_actions", "validation_commands", "stop_conditions"):
        if not isinstance(data.get(field), list) or not data[field]:
            errors.append(f"active-slice manifest {field} must be a non-empty list")
    review = data.get("review", {})
    if not isinstance(review, dict):
        errors.append("active-slice manifest review must be an object")
    else:
        if review.get("status") not in REVIEW_STATUSES:
            errors.append("active-slice manifest review.status is invalid")
        if review.get("status") in {"approved", "changes_required"} and review.get("reviewed_sha") in {None, "", "none"}:
            errors.append("active-slice manifest review.reviewed_sha is required for completed reviews")
    if attributes.get("active_area") and data.get("active_area") != attributes["active_area"]:
        errors.append("active-slice manifest active_area must match current state")
    if attributes.get("active_slice") and data.get("active_slice") != attributes["active_slice"]:
        errors.append("active-slice manifest active_slice must match current state")
    if isinstance(review, dict) and attributes.get("review_status") and review.get("status") != attributes["review_status"]:
        errors.append("active-slice manifest review.status must match current state")
    return errors


def active_slice_allows_code(path: Path) -> tuple[bool, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"active-slice manifest cannot be read: {exc}"]
    if data.get("permission_state") not in {"coding_allowed", "same_slice_only"}:
        return False, ["active-slice manifest permission_state does not allow coding"]
    return True, []


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
            for heading in REQUIRED_HANDOFF_HEADINGS:
                if heading not in handoff_text:
                    errors.append(f"latest handoff is missing '{heading}': {handoff}")

    if attributes.get("next_action") == "code":
        manifest_path = Path(attributes.get("workflow_manifest", str(DEFAULT_MANIFEST_PATH)))
        allowed, manifest_errors = manifest_allows_code(manifest_path)
        if not allowed:
            errors.extend(f"implementation is blocked: {error}" for error in manifest_errors)
        active_allowed, active_errors = active_slice_allows_code(active_manifest)
        if not active_allowed:
            errors.extend(f"implementation is blocked: {error}" for error in active_errors)
    return errors


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
    active_template = Path(__file__).resolve().parent.parent / "assets" / "active-slice-manifest.template.json"
    if active_template.exists():
        active_manifest = json.loads(active_template.read_text(encoding="utf-8"))
    else:
        active_manifest = DEFAULT_ACTIVE_SLICE_MANIFEST
    active_manifest["last_updated"] = dt.date.today().isoformat()
    DEFAULT_ACTIVE_SLICE_MANIFEST_PATH.write_text(json.dumps(active_manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Created {STATE_PATH}")
    print(f"Created {DEFAULT_ACTIVE_SLICE_MANIFEST_PATH}")
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
        if area_changed:
            print("Area change detected. Rerun the start gate, reread controlling docs, update the active-slice manifest, and get explicit authorization before implementation.")
        if high_risk_next:
            print("High-risk next slice detected. Fresh controls and explicit authorization are required, but risk alone does not require another chat.")
        return 0
    print("SESSION DECISION: NEW_SESSION_REQUIRED")
    for trigger in triggers:
        print(f"- {trigger}")
    return 0


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("topic must contain a letter or number")
    return slug


def command_handoff(args: argparse.Namespace) -> int:
    attributes, body, _ = read_state()
    state = repo_state()
    output = Path("docs/history") / f"{dt.date.today().isoformat()}-project-session-handoff-{slugify(args.topic)}.md"
    content = f"""# Project Session Handoff: {args.topic}

Date: {dt.date.today().isoformat()}
Handoff reason: {args.reason}

## Session Boundary Decision
- Decision: New session required before the next slice.
- Trigger: {args.reason}
- Next slice: {args.next}

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

## Paste-Ready Next-Session Prompt
```text
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
    attributes["next_session_required_before_next_slice"] = "true"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init").set_defaults(func=command_init)
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
    handoff.add_argument("--write", action="store_true")
    handoff.set_defaults(func=command_handoff)
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
