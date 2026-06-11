#!/usr/bin/env python3
"""Validate the fail-closed workflow manifest for new project documentation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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

STATUSES = {
    "not_started",
    "in_progress",
    "blocked",
    "awaiting_approval",
    "approved",
    "completed",
    "explicitly_deferred",
}

DONE = {"approved", "completed", "explicitly_deferred"}
READY_TO_CODE = {"approved", "completed"}


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    mode = data.get("mode")
    if mode not in {"full_run", "review_only", "single_phase", "resume"}:
        fail(errors, "mode must be full_run, review_only, single_phase, or resume")

    phases = data.get("phases")
    if not isinstance(phases, dict):
        return errors + ["phases must be an object"]

    in_progress = 0
    for phase in PHASES:
        entry = phases.get(phase)
        if not isinstance(entry, dict):
            fail(errors, f"missing phase: {phase}")
            continue
        status = entry.get("status")
        if status not in STATUSES:
            fail(errors, f"{phase}: invalid status {status!r}")
        if status == "in_progress":
            in_progress += 1
        evidence = entry.get("evidence")
        if not isinstance(evidence, list):
            fail(errors, f"{phase}: evidence must be a list")

    if in_progress > 1:
        fail(errors, "only one phase may be in_progress")

    open_decisions = data.get("open_material_decisions")
    conflicts = data.get("unresolved_source_conflicts")
    if not isinstance(open_decisions, list):
        fail(errors, "open_material_decisions must be a list")
        open_decisions = []
    if not isinstance(conflicts, list):
        fail(errors, "unresolved_source_conflicts must be a list")
        conflicts = []

    approvals = data.get("approvals")
    if not isinstance(approvals, dict):
        fail(errors, "approvals must be an object")
        approvals = {}

    controlled_status = phases.get("3_controlled_docs", {}).get("status")
    if controlled_status in DONE:
        if open_decisions:
            fail(errors, "controlled docs cannot be done while material decisions remain open")
        if conflicts:
            fail(errors, "controlled docs cannot be done while source conflicts remain unresolved")
        if not approvals.get("material_decisions"):
            fail(errors, "controlled docs require material_decisions approval")

    tdd_status = phases.get("4_tdd_alignment", {}).get("status")
    if tdd_status in DONE and not approvals.get("controlled_docs"):
        fail(errors, "TDD/alignment completion requires controlled_docs approval")

    next_action = data.get("next_action")
    if next_action == "code":
        if not data.get("code_allowed"):
            fail(errors, "next_action code requires code_allowed true")
        if open_decisions or conflicts:
            fail(errors, "next_action code requires no open material decisions or source conflicts")
        for approval in ("source_authority", "material_decisions", "controlled_docs", "tdd", "coding_start"):
            if not approvals.get(approval):
                fail(errors, f"next_action code requires {approval} approval")
        for phase in PHASES:
            status = phases.get(phase, {}).get("status")
            if status not in READY_TO_CODE:
                fail(errors, f"next_action code requires {phase} approved or completed, got {status!r}")

    if mode == "full_run" and next_action == "complete":
        for phase in PHASES:
            status = phases.get(phase, {}).get("status")
            if status not in DONE:
                fail(errors, f"full_run completion requires {phase} done, got {status!r}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: manifest not found: {args.manifest}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"FAIL: invalid JSON: {exc}")
        return 2

    errors = validate(data)
    if errors:
        print("FAIL: workflow manifest is not ready")
        for error in errors:
            print(f"- {error}")
        return 1

    print("PASS: workflow manifest is valid for its current state")
    return 0


if __name__ == "__main__":
    sys.exit(main())
