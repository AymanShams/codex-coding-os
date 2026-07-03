#!/usr/bin/env python3
"""Fail-closed parallel worktree lane orchestration for Codex Coding OS projects."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path("project-documentation-manifest.json")
CURRENT_STATE_PATH = Path("docs/delivery/current-state.md")
AUDIT_LANE_ROOT = Path("docs/delivery/parallel-worktrees")
LOCAL_LANE_ROOT = Path(".codex/parallel-worktrees")
PLAN_FILE = "planned-lanes.json"
RUNTIME_MANIFEST_FILE = "lane-manifest.json"
SUMMARY_FILE = "lane-summary.json"
READY_TO_CODE = {"approved", "completed"}
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
TRIGGER_PATTERN = re.compile(
    r"auth|authorization|payment|billing|sensitive|privacy|admin|export|"
    r"migration|database|schema|deploy|production|security|high risk|"
    r"frontend.*backend|backend.*frontend|test hardening|fresh-context|"
    r"separate review|parallel|worktree|large feature",
    re.IGNORECASE,
)
CONTROLLED_PATTERNS = [
    "AGENTS.md",
    "**/AGENTS.md",
    "CLAUDE.md",
    "**/CLAUDE.md",
    "project-documentation-manifest.json",
    "docs/delivery/current-state.md",
    "docs/delivery/**",
    "**/migrations/**",
    "**/migration/**",
    "**/schema/**",
    "**/schema.*",
    "**/*schema*",
    "package.json",
    "**/package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "bun.lockb",
    "requirements.txt",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    ".github/**",
    "vercel.json",
    "netlify.toml",
    "Dockerfile",
    "**/Dockerfile",
    "**/auth/**",
    "**/security/**",
]
IGNORED_LANE_LOCAL_FILES = {
    ".codex",
    ".codex/",
    ".codex/parallel-lane.json",
}
LOCAL_EXCLUDE_PATTERNS = [
    ".codex/parallel-worktrees/",
    ".codex/parallel-lane.json",
]
BROAD_PATTERNS = {"*", "**", ".", "./", "**/*", "src/**", "app/**", "pages/**"}
THREAD_RISK_WARNING = """FULLY AUTOMATED THREAD MODE IS ADVANCED.

Use it only when the Codex app exposes trusted thread-creation tools and the
parent/orchestrator session can still inspect every created lane prompt. Risks:
- the wrong prompt may be sent to the wrong thread;
- a new thread may miss local project context or plugin availability;
- a lane may appear independent while still depending on locked source truth;
- user approval and merge control can become unclear.

Default mode is manual: Codex creates worktrees and paste-ready prompts, then the
user opens each lane thread intentionally.
"""


@dataclass
class Lane:
    name: str
    objective: str
    allowed_files: list[str]
    forbidden_files: list[str] = field(default_factory=list)
    review: str = "fresh-context"
    validation: list[str] = field(default_factory=list)
    allow_controlled_files: bool = False

    @property
    def slug(self) -> str:
        return slugify(self.name)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("value must contain a letter or number")
    return slug


def run_git(args: list[str], cwd: Path | None = None, check: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(message)
    return result.stdout.strip() if result.returncode == 0 else ""


def repo_root() -> Path:
    root = run_git(["rev-parse", "--show-toplevel"])
    if not root:
        raise RuntimeError("not inside a Git repository")
    return Path(root).resolve()


def relative_to_root(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def display_path(path: Path, root: Path) -> str:
    try:
        return relative_to_root(path, root)
    except ValueError:
        return path.as_posix()


def normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def split_csv(value: str) -> list[str]:
    return [normalize_path(item) for item in value.split(",") if item.strip()]


def parse_lane(value: str) -> Lane:
    parts = [part.strip() for part in value.split("|")]
    if len(parts) < 3:
        raise ValueError(
            "lane must use: name|objective|allowed1,allowed2|forbidden1,forbidden2|review|validation1;;validation2|flags"
        )
    while len(parts) < 7:
        parts.append("")
    flags = {flag.strip().lower() for flag in parts[6].split(",") if flag.strip()}
    lane = Lane(
        name=parts[0],
        objective=parts[1],
        allowed_files=split_csv(parts[2]),
        forbidden_files=split_csv(parts[3]),
        review=parts[4] or "fresh-context",
        validation=[item.strip() for item in parts[5].split(";;") if item.strip()],
        allow_controlled_files="allow-controlled-files" in flags,
    )
    if not lane.allowed_files:
        raise ValueError(f"lane {lane.name!r} must declare allowed files")
    return lane


def parse_lanes(values: list[str], default_validation: list[str]) -> list[Lane]:
    lanes = [parse_lane(value) for value in values]
    seen: set[str] = set()
    for lane in lanes:
        if lane.slug in seen:
            raise ValueError(f"duplicate lane name after slug normalization: {lane.name}")
        seen.add(lane.slug)
        if not lane.validation:
            lane.validation = default_validation[:]
    if len(lanes) < 2:
        raise ValueError("parallel worktree mode requires at least two lanes")
    return lanes


def lane_to_plan_record(lane: Lane) -> dict[str, Any]:
    return {
        "name": lane.name,
        "slug": lane.slug,
        "objective": lane.objective,
        "allowed_files": lane.allowed_files,
        "forbidden_files": lane.forbidden_files,
        "review": lane.review,
        "validation": lane.validation,
        "allow_controlled_files": lane.allow_controlled_files,
    }


def lane_from_record(record: dict[str, Any]) -> Lane:
    return Lane(
        name=str(record["name"]),
        objective=str(record["objective"]),
        allowed_files=[normalize_path(str(item)) for item in record.get("allowed_files", [])],
        forbidden_files=[normalize_path(str(item)) for item in record.get("forbidden_files", [])],
        review=str(record.get("review") or "fresh-context"),
        validation=[str(item) for item in record.get("validation", []) if str(item).strip()],
        allow_controlled_files=bool(record.get("allow_controlled_files")),
    )


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def default_run_id() -> str:
    return f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-parallel-run"


def read_manifest() -> tuple[dict[str, Any] | None, list[str]]:
    if not MANIFEST_PATH.exists():
        return None, [f"workflow manifest not found: {MANIFEST_PATH}"]
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8")), []
    except json.JSONDecodeError as exc:
        return None, [f"workflow manifest is not valid JSON: {exc}"]


def workflow_manifest_errors() -> list[str]:
    data, errors = read_manifest()
    if errors:
        return errors
    assert data is not None
    result: list[str] = []
    if data.get("next_action") != "code":
        result.append("workflow manifest next_action is not code")
    if data.get("code_allowed") is not True:
        result.append("workflow manifest code_allowed is not true")
    if data.get("open_material_decisions"):
        result.append("workflow manifest has open material decisions")
    if data.get("unresolved_source_conflicts"):
        result.append("workflow manifest has unresolved source conflicts")
    approvals = data.get("approvals", {})
    for approval in ("source_authority", "material_decisions", "controlled_docs", "tdd", "coding_start"):
        if approvals.get(approval) is not True:
            result.append(f"workflow manifest lacks {approval} approval")
    phases = data.get("phases", {})
    for phase in REQUIRED_CODE_PHASES:
        status = phases.get(phase, {}).get("status")
        if status not in READY_TO_CODE:
            result.append(f"workflow manifest phase {phase} is not approved or completed")
    return result


def tracking_ref() -> str:
    upstream = run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if upstream:
        return upstream
    if run_git(["show-ref", "--verify", "refs/remotes/origin/main"]):
        return "origin/main"
    return ""


def status_paths() -> list[str]:
    paths: list[str] = []
    status = run_git(["status", "--porcelain", "-uall"])
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.append(normalize_path(path))
    return paths


def dirty_path_allowed(path: str, allowed_prefixes: list[str]) -> bool:
    normalized = normalize_path(path).rstrip("/")
    for prefix in allowed_prefixes:
        clean_prefix = normalize_path(prefix).rstrip("/")
        if normalized == clean_prefix or normalized.startswith(clean_prefix + "/"):
            return True
    return False


def git_gate_errors(require_clean: bool = True, allowed_dirty_prefixes: list[str] | None = None) -> list[str]:
    errors: list[str] = []
    if require_clean and run_git(["status", "--porcelain", "-uall"]):
        allowed_dirty_prefixes = allowed_dirty_prefixes or []
        unallowed = [path for path in status_paths() if not dirty_path_allowed(path, allowed_dirty_prefixes)]
        if unallowed:
            errors.append(
                "working tree is dirty or has untracked files outside the allowed lane-plan audit path; "
                "classify or commit/stash them before creating lanes: "
                + ", ".join(unallowed[:8])
            )
    remote = tracking_ref()
    if remote:
        counts = run_git(["rev-list", "--left-right", "--count", f"HEAD...{remote}"]).split()
        if len(counts) == 2 and counts[1].isdigit() and int(counts[1]) > 0:
            errors.append(f"tracked remote {remote} has incoming commits; inspect and update before creating lanes")
    return errors


def has_parallel_trigger(task: str, risk: str) -> bool:
    return risk in {"material", "high"} or bool(TRIGGER_PATTERN.search(task))


def match_any(path: str, patterns: list[str]) -> bool:
    normalized = normalize_path(path)
    for pattern in patterns:
        pat = normalize_path(pattern)
        if fnmatch.fnmatchcase(normalized, pat) or fnmatch.fnmatchcase(normalized, f"**/{pat}"):
            return True
    return False


def is_controlled_path(path: str) -> bool:
    return match_any(path, CONTROLLED_PATTERNS)


def base_prefix(pattern: str) -> str:
    normalized = normalize_path(pattern)
    for marker in ("**", "*"):
        if marker in normalized:
            normalized = normalized[: normalized.index(marker)]
            break
    return normalized.rstrip("/")


def patterns_may_overlap(left: str, right: str) -> bool:
    left_norm = normalize_path(left)
    right_norm = normalize_path(right)
    if left_norm in BROAD_PATTERNS or right_norm in BROAD_PATTERNS:
        return True
    if left_norm == right_norm:
        return True
    left_prefix = base_prefix(left_norm)
    right_prefix = base_prefix(right_norm)
    if not left_prefix or not right_prefix:
        return True
    return left_prefix.startswith(right_prefix + "/") or right_prefix.startswith(left_prefix + "/")


def lane_definition_errors(lanes: list[Lane]) -> list[str]:
    errors: list[str] = []
    for lane in lanes:
        for pattern in lane.allowed_files:
            if normalize_path(pattern) in BROAD_PATTERNS:
                errors.append(f"lane {lane.name} uses an overly broad allowed pattern: {pattern}")
        controlled_allowed = [pattern for pattern in lane.allowed_files if is_controlled_path(pattern)]
        if controlled_allowed and not lane.allow_controlled_files:
            errors.append(
                f"lane {lane.name} includes controlled files without allow-controlled-files flag: "
                + ", ".join(controlled_allowed)
            )
        if not lane.validation:
            errors.append(f"lane {lane.name} has no validation command")
    for index, left in enumerate(lanes):
        for right in lanes[index + 1 :]:
            for left_pattern in left.allowed_files:
                for right_pattern in right.allowed_files:
                    if patterns_may_overlap(left_pattern, right_pattern):
                        errors.append(
                            f"lane file ownership may overlap: {left.name} ({left_pattern}) and "
                            f"{right.name} ({right_pattern})"
                        )
    return errors


def print_errors(prefix: str, errors: list[str]) -> None:
    print(prefix)
    for error in errors:
        print(f"- {error}")


def preflight(
    task: str,
    risk: str,
    require_clean: bool = True,
    allowed_dirty_prefixes: list[str] | None = None,
) -> tuple[str, list[str]]:
    errors = workflow_manifest_errors() + git_gate_errors(
        require_clean=require_clean,
        allowed_dirty_prefixes=allowed_dirty_prefixes,
    )
    if errors:
        return "BLOCKED", errors
    if not has_parallel_trigger(task, risk):
        return "SERIAL_RECOMMENDED", [
            "no material/high-risk or separable-work trigger was detected; use one bounded serial slice"
        ]
    return "OFFER_PARALLEL_LANES", []


def command_evaluate(args: argparse.Namespace) -> int:
    decision, errors = preflight(args.task, args.risk)
    if args.json:
        print(json.dumps({"decision": decision, "messages": errors}, indent=2))
    elif decision == "BLOCKED":
        print_errors("PARALLEL WORKTREE LANES: BLOCKED", errors)
    elif decision == "SERIAL_RECOMMENDED":
        print_errors("PARALLEL WORKTREE LANES: SERIAL_RECOMMENDED", errors)
    else:
        print("PARALLEL WORKTREE LANES: OFFER_PARALLEL_LANES")
        print("Ask the user before creating worktrees. Default thread mode is manual paste-ready prompts.")
    return 2 if decision == "BLOCKED" else 0


def audit_lane_root(run_id: str) -> Path:
    return AUDIT_LANE_ROOT / run_id


def local_lane_root(run_id: str) -> Path:
    return LOCAL_LANE_ROOT / run_id


def plan_path(run_id: str) -> Path:
    return audit_lane_root(run_id) / PLAN_FILE


def runtime_manifest_path(run_id: str) -> Path:
    return local_lane_root(run_id) / RUNTIME_MANIFEST_FILE


def summary_path(run_id: str) -> Path:
    return audit_lane_root(run_id) / SUMMARY_FILE


def default_worktree_root(run_id: str, root: Path) -> Path:
    return root.parent / f"{root.name}-worktrees" / run_id


def load_plan(run_id: str) -> tuple[dict[str, Any], list[Lane]]:
    path = plan_path(run_id)
    if not path.exists():
        raise RuntimeError(f"planned lane run not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    lanes = [lane_from_record(record) for record in data.get("lanes", [])]
    if not lanes:
        raise RuntimeError(f"planned lane run has no lanes: {path}")
    return data, lanes


def ensure_git_info_excludes(root: Path) -> None:
    git_path = run_git(["rev-parse", "--git-path", "info/exclude"], cwd=root, check=True)
    exclude_path = Path(git_path)
    if not exclude_path.is_absolute():
        exclude_path = root / exclude_path
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    additions = [pattern for pattern in LOCAL_EXCLUDE_PATTERNS if pattern not in existing]
    if additions:
        prefix = "\n" if existing and not existing.endswith("\n") else ""
        exclude_path.write_text(existing + prefix + "\n".join(additions) + "\n", encoding="utf-8")


def contract_text(lane: Lane, run_id: str, branch: str, worktree_path: Path, base_commit: str) -> str:
    allowed = "\n".join(f"- `{item}`" for item in lane.allowed_files)
    forbidden = "\n".join(f"- `{item}`" for item in lane.forbidden_files) or "- none"
    validation = "\n".join(f"- `{item}`" for item in lane.validation)
    controlled = "yes" if lane.allow_controlled_files else "no"
    return f"""# Worktree Task Contract: {lane.name}

## Lane Identity
- Run ID: `{run_id}`
- Lane: `{lane.slug}`
- Branch: `{branch}`
- Base commit: `{base_commit}`
- Worktree path: `{worktree_path.as_posix()}`
- Review requirement: `{lane.review}`
- Controlled files allowed: {controlled}

## Objective
{lane.objective}

## Non-Goals
- Do not edit files outside the allowed file list.
- Do not change controlled source truth unless this contract explicitly allows it.
- Do not merge this lane.
- Do not update `docs/delivery/current-state.md`; the parent/orchestrator session owns it.
- Do not turn this lane handoff into a user-facing new-session prompt when parent/orchestrator automation remains authorized.

## Controlling Sources
- `project-documentation-manifest.json`
- `docs/delivery/current-state.md`
- task-controlling PRD, TDD, ADR, AGENTS, and scoped docs named by the parent session

## Allowed Files
{allowed}

## Forbidden Files
{forbidden}

## Validation Commands
{validation}

## Stop Conditions
- Workflow manifest no longer permits this task.
- Required controlling source is missing or conflicts with another source.
- The lane needs a forbidden or controlled file not explicitly allowed.
- The lane discovers a material product, architecture, security, or data decision.
- Validation cannot run or fails for reasons outside this lane's contract.

## Required Lane Handoff
Before this lane is reviewed or merged, create a handoff using
`templates/parallel-lane-handoff.md` and record validation evidence. The default
handoff target is the parent/orchestrator. The parent consumes the handoff
internally unless a stop condition fired or automation tooling is unavailable.
"""


def prompt_text(lane: Lane, run_id: str, contract_path: Path, worktree_path: Path) -> str:
    return f"""Continue this project in this worktree:
{worktree_path.as_posix()}

You are working only on parallel lane `{lane.slug}` in run `{run_id}`.

First run:
python scripts/agent/session_continuity.py start

Then read:
1. AGENTS.md and closest scoped AGENTS.md files
2. project-documentation-manifest.json
3. docs/delivery/current-state.md
4. {contract_path.as_posix()}
5. the task-controlling PRD, TDD, ADRs, and docs listed by the parent session

Lane objective:
{lane.objective}

Allowed files:
{chr(10).join("- " + item for item in lane.allowed_files)}

Forbidden files:
{chr(10).join("- " + item for item in lane.forbidden_files) if lane.forbidden_files else "- none"}

Stop if the task needs files outside the contract, the workflow manifest blocks coding,
source truth conflicts, validation is unavailable, or a material decision is discovered.
Do not merge. End with a lane handoff using templates/parallel-lane-handoff.md.
Set the handoff target to the parent/orchestrator unless a stop condition requires
human judgment or automation tooling is unavailable.
"""


def write_offer(run_root: Path, task: str, lanes: list[Lane], thread_mode: str) -> Path:
    offer_path = run_root / "parallel-worktree-offer.md"
    lanes_text = "\n".join(f"- `{lane.slug}`: {lane.objective}" for lane in lanes)
    warning = THREAD_RISK_WARNING if thread_mode == "auto" else "Manual thread mode is the default and lowest-risk path."
    offer_path.write_text(
        f"""# Parallel Worktree Offer

Codex can split this work into bounded worktree lanes only after the workflow
manifest permits coding and you approve the split.

## Task
{task}

## Proposed Lanes
{lanes_text}

## Thread Mode
{thread_mode}

{warning}

## Approval Required
Do not create or start lane threads unless the user explicitly approves this plan.
""",
        encoding="utf-8",
    )
    return offer_path


def write_plan(
    run_root: Path,
    run_id: str,
    task: str,
    risk: str,
    lanes: list[Lane],
    thread_mode: str,
) -> Path:
    path = run_root / PLAN_FILE
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "task": task,
                "risk": risk,
                "thread_mode": thread_mode,
                "created_at": utc_timestamp(),
                "lanes": [lane_to_plan_record(lane) for lane in lanes],
                "next_command": f"python scripts/agent/worktree_lanes.py create --from-run {run_id} --user-approved",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def audit_lane_record(lane: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": lane["name"],
        "slug": lane["slug"],
        "objective": lane["objective"],
        "branch": lane["branch"],
        "allowed_files": lane["allowed_files"],
        "forbidden_files": lane["forbidden_files"],
        "validation": lane["validation"],
        "review": lane["review"],
        "allow_controlled_files": lane["allow_controlled_files"],
        "status": lane["status"],
        "runtime_contract": lane["runtime_contract"],
        "runtime_prompt": lane["runtime_prompt"],
    }


def write_audit_summary(audit_root: Path, manifest: dict[str, Any]) -> Path:
    path = audit_root / SUMMARY_FILE
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "run_id": manifest["run_id"],
                "task": manifest["task"],
                "risk": manifest["risk"],
                "thread_mode": manifest["thread_mode"],
                "base_commit": manifest["base_commit"],
                "created_at": manifest["created_at"],
                "integration_owner": manifest["integration_owner"],
                "current_state_owner": manifest["current_state_owner"],
                "merge_policy": manifest["merge_policy"],
                "local_runtime_manifest": f"{LOCAL_LANE_ROOT.as_posix()}/{manifest['run_id']}/{RUNTIME_MANIFEST_FILE}",
                "local_runtime_note": "Local runtime files are intentionally excluded from Git and may contain machine-specific paths.",
                "lanes": [audit_lane_record(lane) for lane in manifest.get("lanes", [])],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def command_plan(args: argparse.Namespace) -> int:
    lanes = parse_lanes(args.lane, args.validation)
    decision, errors = preflight(args.task, args.risk)
    errors.extend(lane_definition_errors(lanes))
    if decision == "SERIAL_RECOMMENDED" and not args.force:
        errors.extend(errors or ["parallel lanes are not recommended for this task"])
    if errors:
        print_errors("PARALLEL WORKTREE PLAN: BLOCKED", errors)
        return 2
    root = repo_root()
    run_id = args.run_id or default_run_id()
    run_root = audit_lane_root(run_id)
    run_root.mkdir(parents=True, exist_ok=False)
    offer_path = write_offer(run_root, args.task, lanes, args.thread_mode)
    plan_file = write_plan(run_root, run_id, args.task, args.risk, lanes, args.thread_mode)
    print(f"Planned parallel lanes under {run_root.as_posix()}")
    print(f"Offer: {offer_path.as_posix()}")
    print(f"Plan: {plan_file.as_posix()}")
    print(f"Create after explicit user approval: python scripts/agent/worktree_lanes.py create --from-run {run_id} --user-approved")
    print(f"Repository: {root.as_posix()}")
    return 0


def ensure_user_approval(user_approved: bool, thread_mode: str, acknowledge_auto_thread_risk: bool) -> list[str]:
    errors: list[str] = []
    if not user_approved:
        errors.append("user approval is required before creating worktrees; rerun with --user-approved after explicit yes")
    if thread_mode == "auto" and not acknowledge_auto_thread_risk:
        errors.append("auto thread mode requires --acknowledge-auto-thread-risk after reading the risk warning")
    return errors


def resolve_create_inputs(args: argparse.Namespace) -> tuple[str, str, str, str, list[Lane], list[str]]:
    errors: list[str] = []
    if args.from_run:
        if args.run_id and args.run_id != args.from_run:
            errors.append("--run-id must match --from-run when both are supplied")
        try:
            plan, lanes = load_plan(args.from_run)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            return args.from_run, "", "", "manual", [], [str(exc)]
        thread_mode = args.thread_mode or str(plan.get("thread_mode") or "manual")
        return (
            args.from_run,
            str(plan.get("task") or ""),
            str(plan.get("risk") or "material"),
            thread_mode,
            lanes,
            errors,
        )

    if not args.task:
        errors.append("--task is required unless --from-run is used")
    if not args.lane:
        errors.append("--lane is required unless --from-run is used")
    lanes: list[Lane] = []
    if args.lane:
        try:
            lanes = parse_lanes(args.lane, args.validation)
        except ValueError as exc:
            errors.append(str(exc))
    return args.run_id or default_run_id(), args.task or "", args.risk, args.thread_mode or "manual", lanes, errors


def command_create(args: argparse.Namespace) -> int:
    run_id, task, risk, thread_mode, lanes, errors = resolve_create_inputs(args)
    allowed_dirty = [audit_lane_root(run_id).as_posix()] if args.from_run else []
    decision, preflight_errors = preflight(task, risk, allowed_dirty_prefixes=allowed_dirty)
    errors.extend(preflight_errors)
    errors.extend(lane_definition_errors(lanes))
    errors.extend(ensure_user_approval(args.user_approved, thread_mode, args.acknowledge_auto_thread_risk))
    if decision == "SERIAL_RECOMMENDED" and not args.force:
        errors.extend(errors or ["parallel lanes are not recommended for this task"])
    if errors:
        if thread_mode == "auto":
            print(THREAD_RISK_WARNING.strip())
        print_errors("PARALLEL WORKTREE CREATE: BLOCKED", errors)
        return 2

    root = repo_root()
    base_commit = run_git(["rev-parse", "HEAD"], check=True)
    audit_root = audit_lane_root(run_id)
    runtime_root = local_lane_root(run_id)
    contracts_root = runtime_root / "contracts"
    prompts_root = runtime_root / "prompts"
    handoffs_root = runtime_root / "handoffs"
    worktree_root = Path(args.worktree_root).resolve() if args.worktree_root else default_worktree_root(run_id, root)
    if runtime_manifest_path(run_id).exists():
        raise RuntimeError(f"lane runtime already exists: {runtime_manifest_path(run_id)}")
    if audit_root.exists() and not args.from_run:
        raise RuntimeError(f"lane audit run already exists; use --from-run {run_id} or choose a new --run-id")
    if not audit_root.exists():
        audit_root.mkdir(parents=True)
        write_offer(audit_root, task, lanes, thread_mode)
        write_plan(audit_root, run_id, task, risk, lanes, thread_mode)
    ensure_git_info_excludes(root)
    contracts_root.mkdir(parents=True)
    prompts_root.mkdir(parents=True)
    handoffs_root.mkdir(parents=True)
    worktree_root.mkdir(parents=True, exist_ok=True)

    lane_records: list[dict[str, Any]] = []
    created_worktrees: list[Path] = []
    try:
        for lane in lanes:
            branch = f"lane/{run_id}/{lane.slug}"
            worktree_path = worktree_root / lane.slug
            run_git(["worktree", "add", "-b", branch, str(worktree_path), base_commit], check=True)
            created_worktrees.append(worktree_path)
            ensure_git_info_excludes(worktree_path)
            contract_path = contracts_root / f"{lane.slug}.md"
            prompt_path = prompts_root / f"{lane.slug}-prompt.md"
            contract_path.write_text(contract_text(lane, run_id, branch, worktree_path, base_commit), encoding="utf-8")
            prompt_path.write_text(prompt_text(lane, run_id, contract_path.resolve(), worktree_path), encoding="utf-8")
            marker_path = worktree_path / ".codex" / "parallel-lane.json"
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "lane": lane.slug,
                        "lane_manifest": runtime_manifest_path(run_id).resolve().as_posix(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            lane_records.append(
                {
                    "name": lane.name,
                    "slug": lane.slug,
                    "objective": lane.objective,
                    "branch": branch,
                    "worktree_path": worktree_path.as_posix(),
                    "contract_path": contract_path.resolve().as_posix(),
                    "prompt_path": prompt_path.resolve().as_posix(),
                    "runtime_contract": display_path(contract_path, root),
                    "runtime_prompt": display_path(prompt_path, root),
                    "handoff_path": "",
                    "allowed_files": lane.allowed_files,
                    "forbidden_files": lane.forbidden_files,
                    "validation": lane.validation,
                    "review": lane.review,
                    "allow_controlled_files": lane.allow_controlled_files,
                    "status": "created",
                }
            )
    except Exception:
        for path in reversed(created_worktrees):
            subprocess.run(["git", "worktree", "remove", "--force", str(path)], check=False)
        raise

    offer_path = audit_root / "parallel-worktree-offer.md"
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "task": task,
        "risk": risk,
        "thread_mode": thread_mode,
        "base_commit": base_commit,
        "created_at": utc_timestamp(),
        "integration_owner": "parent-orchestrator-session",
        "current_state_owner": "parent-orchestrator-session",
        "merge_policy": "one-lane-at-a-time-after-validation-and-review",
        "offer_path": offer_path.as_posix(),
        "audit_root": audit_root.as_posix(),
        "runtime_root": runtime_root.resolve().as_posix(),
        "worktree_root": worktree_root.as_posix(),
        "lanes": lane_records,
    }
    manifest_path = runtime_manifest_path(run_id)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    summary = write_audit_summary(audit_root, manifest)
    if thread_mode == "auto":
        (runtime_root / "auto-thread-requests.json").write_text(
            json.dumps(
                {
                    "warning": THREAD_RISK_WARNING,
                    "default_mode": "manual",
                    "requests": [
                        {
                            "lane": lane["slug"],
                            "worktree_path": lane["worktree_path"],
                            "prompt_path": lane["prompt_path"],
                        }
                        for lane in lane_records
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    print(f"Created parallel worktree run: {run_id}")
    print(f"Lane manifest: {manifest_path.as_posix()}")
    print(f"Commit-safe summary: {summary.as_posix()}")
    print("Default next step: open a separate Codex chat for each prompt under the prompts folder.")
    return 0


def load_lane_manifest(run_id: str | None, current: bool = False) -> tuple[Path, dict[str, Any]]:
    if current:
        cwd = Path.cwd().resolve()
        for base in [cwd, *cwd.parents]:
            marker = base / ".codex" / "parallel-lane.json"
            if marker.exists():
                data = json.loads(marker.read_text(encoding="utf-8"))
                manifest_path = Path(data["lane_manifest"])
                if manifest_path.exists():
                    return manifest_path, json.loads(manifest_path.read_text(encoding="utf-8"))
    candidates = sorted(LOCAL_LANE_ROOT.glob(f"*/{RUNTIME_MANIFEST_FILE}"))
    if run_id:
        candidates = [runtime_manifest_path(run_id)]
    if not candidates:
        raise RuntimeError("no lane manifest found")
    cwd = Path.cwd().resolve()
    for path in candidates:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not current:
            return path, data
        for lane in data.get("lanes", []):
            worktree = Path(lane["worktree_path"]).resolve()
            if cwd == worktree or cwd.is_relative_to(worktree):
                return path, data
    raise RuntimeError("no matching lane manifest found for the current working directory")


def changed_files(worktree_path: Path, base_commit: str) -> list[str]:
    files: set[str] = set()
    diff = run_git(["-C", str(worktree_path), "diff", "--name-only", base_commit])
    files.update(normalize_path(line) for line in diff.splitlines() if line.strip())
    diff_cached = run_git(["-C", str(worktree_path), "diff", "--cached", "--name-only", base_commit])
    files.update(normalize_path(line) for line in diff_cached.splitlines() if line.strip())
    status = run_git(["-C", str(worktree_path), "status", "--porcelain", "-uall"])
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[2:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.add(normalize_path(path))
    return sorted(
        file_path
        for file_path in files
        if file_path not in IGNORED_LANE_LOCAL_FILES
        and not file_path.startswith(".codex/parallel-lane.json")
    )


def validate_lane(lane: dict[str, Any], base_commit: str) -> list[str]:
    errors: list[str] = []
    worktree_path = Path(lane["worktree_path"])
    if not worktree_path.exists():
        return [f"lane {lane['slug']} worktree is missing: {worktree_path}"]
    if not Path(lane["contract_path"]).exists():
        errors.append(f"lane {lane['slug']} contract is missing")
    files = changed_files(worktree_path, base_commit)
    for file_path in files:
        if match_any(file_path, lane.get("forbidden_files", [])):
            errors.append(f"lane {lane['slug']} changed forbidden file: {file_path}")
        if not match_any(file_path, lane.get("allowed_files", [])):
            errors.append(f"lane {lane['slug']} changed file outside allowed list: {file_path}")
        if is_controlled_path(file_path) and not lane.get("allow_controlled_files"):
            errors.append(f"lane {lane['slug']} changed controlled source without permission: {file_path}")
    if lane.get("status") in {"ready", "merged"} and not lane.get("handoff_path"):
        errors.append(f"lane {lane['slug']} is {lane['status']} but has no handoff path")
    if lane.get("handoff_path") and not Path(lane["handoff_path"]).exists():
        errors.append(f"lane {lane['slug']} handoff does not exist: {lane['handoff_path']}")
    return errors


def command_validate(args: argparse.Namespace) -> int:
    _, manifest = load_lane_manifest(args.run_id, current=args.current)
    errors = workflow_manifest_errors()
    for lane in manifest.get("lanes", []):
        if args.lane and lane["slug"] != slugify(args.lane):
            continue
        errors.extend(validate_lane(lane, manifest["base_commit"]))
    if errors:
        print_errors("PARALLEL WORKTREE LANES: CONTRACT FAIL", errors)
        return 1
    print("PARALLEL WORKTREE LANES: CONTRACT PASS")
    return 0


def command_status(args: argparse.Namespace) -> int:
    path, manifest = load_lane_manifest(args.run_id)
    print(f"Lane manifest: {path.as_posix()}")
    print(f"Task: {manifest.get('task')}")
    print(f"Base commit: {manifest.get('base_commit')}")
    print(f"Thread mode: {manifest.get('thread_mode')}")
    for lane in manifest.get("lanes", []):
        print(f"- {lane['slug']}: {lane['status']} at {lane['worktree_path']}")
    return 0


def command_close(args: argparse.Namespace) -> int:
    path, manifest = load_lane_manifest(args.run_id)
    target = slugify(args.lane)
    found = False
    for lane in manifest.get("lanes", []):
        if lane["slug"] != target:
            continue
        found = True
        if args.status in {"ready", "merged"} and not args.handoff:
            print("PARALLEL WORKTREE CLOSE: BLOCKED")
            print("- ready or merged lanes require --handoff")
            return 2
        if args.handoff:
            handoff = Path(args.handoff)
            if not handoff.exists():
                print("PARALLEL WORKTREE CLOSE: BLOCKED")
                print(f"- handoff does not exist: {handoff}")
                return 2
            lane["handoff_path"] = handoff.as_posix()
        lane["status"] = args.status
    if not found:
        raise RuntimeError(f"lane not found: {args.lane}")
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    audit_root = Path(manifest.get("audit_root", audit_lane_root(manifest["run_id"]).as_posix()))
    if audit_root.exists():
        write_audit_summary(audit_root, manifest)
    print(f"Updated {target} to {args.status}")
    return 0


def command_cleanup(args: argparse.Namespace) -> int:
    path, manifest = load_lane_manifest(args.run_id)
    removable = {"merged", "abandoned", "closed"}
    if not args.yes:
        print("PARALLEL WORKTREE CLEANUP: DRY RUN")
    for lane in manifest.get("lanes", []):
        if lane.get("status") not in removable:
            print(f"Keeping {lane['slug']}: status {lane.get('status')}")
            continue
        worktree_path = Path(lane["worktree_path"])
        if args.yes and worktree_path.exists():
            run_git(["worktree", "remove", str(worktree_path)], check=True)
            print(f"Removed worktree: {worktree_path}")
        else:
            print(f"Would remove worktree: {worktree_path}")
    if args.yes:
        root = Path(manifest.get("worktree_root", ""))
        if root.exists() and not any(root.iterdir()):
            root.rmdir()
    print(f"Lane manifest retained for audit: {path.as_posix()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail-closed Codex parallel worktree lanes")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--task", required=True)
    evaluate.add_argument("--risk", choices=["routine", "material", "high"], default="material")
    evaluate.add_argument("--json", action="store_true")
    evaluate.set_defaults(func=command_evaluate)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--task", required=True)
    plan.add_argument("--risk", choices=["routine", "material", "high"], default="material")
    plan.add_argument("--lane", action="append", required=True)
    plan.add_argument("--validation", action="append", default=[])
    plan.add_argument("--run-id")
    plan.add_argument("--thread-mode", choices=["manual", "auto"], default="manual")
    plan.add_argument("--force", action="store_true")
    plan.set_defaults(func=command_plan)

    create = subparsers.add_parser("create")
    create.add_argument("--from-run", help="create worktrees from a prior plan run id")
    create.add_argument("--task")
    create.add_argument("--risk", choices=["routine", "material", "high"], default="material")
    create.add_argument("--lane", action="append")
    create.add_argument("--validation", action="append", default=[])
    create.add_argument("--run-id")
    create.add_argument("--worktree-root")
    create.add_argument("--thread-mode", choices=["manual", "auto"])
    create.add_argument("--user-approved", action="store_true")
    create.add_argument("--acknowledge-auto-thread-risk", action="store_true")
    create.add_argument("--force", action="store_true")
    create.set_defaults(func=command_create)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--run-id")
    validate.add_argument("--lane")
    validate.add_argument("--current", action="store_true")
    validate.set_defaults(func=command_validate)

    status = subparsers.add_parser("status")
    status.add_argument("--run-id")
    status.set_defaults(func=command_status)

    close = subparsers.add_parser("close")
    close.add_argument("--run-id", required=True)
    close.add_argument("--lane", required=True)
    close.add_argument("--status", choices=["ready", "blocked", "abandoned", "merged", "closed"], required=True)
    close.add_argument("--handoff")
    close.set_defaults(func=command_close)

    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--yes", action="store_true")
    cleanup.set_defaults(func=command_cleanup)
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
