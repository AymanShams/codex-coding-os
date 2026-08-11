#!/usr/bin/env python3
"""Advisory UserPromptSubmit hook for the single Catalogue Router."""

from __future__ import annotations

import json

from _hook_io import emit_additional_context, get_prompt, load_input
from capability_index import (
    conservative_default_decision,
    resolve_route,
    verify_registered_route,
)


def route_for_prompt(
    prompt: str,
    classification: dict | None = None,
    task_text: str | None = None,
) -> dict:
    return resolve_route(prompt, classification=classification, task_text=task_text)


def _capability_label(entry: dict) -> str:
    name = str(entry.get("name") or entry.get("id") or "unknown")
    kind = str(entry.get("kind") or "capability")
    return f"{kind} `{name}`"


def render_route(decision: dict) -> str:
    scenario = str(decision.get("scenario") or decision.get("rule_id") or "matched policy scenario")
    lines = [
        "Capability routing advisory. This hint does not grant permission, replace the latest user request, or override global or project instructions.",
        f"- Execution: {decision.get('execution_owner', 'codex_parent')} with {decision.get('model', 'gpt-5.6-sol')} at {decision.get('reasoning_effort', 'high')} reasoning",
    ]
    primary = decision.get("primary")
    if primary:
        lines.append(f"- Primary {_capability_label(primary)}: {scenario}")
    else:
        lines.append(f"- Primary skill: none selected ({scenario})")
    for support in decision.get("supports", [])[:2]:
        lines.append(f"- Skill support {_capability_label(support)}")
    for worker in decision.get("support_workers", [])[:2]:
        required = "required" if worker.get("required") else "optional"
        lines.append(
            f"- Worker support: {worker.get('execution_owner')} / {worker.get('role')} / "
            f"{worker.get('model')} ({required}, deadline {worker.get('deadline_seconds')}s)"
        )
    for capability_fallback in decision.get("capability_fallbacks", [])[:1]:
        unavailable = ", ".join(
            str(item)
            for item in capability_fallback.get("unavailable_dependencies", [])
        )
        lines.append(
            f"- Capability fallback: {capability_fallback.get('requested_capability')} -> "
            f"{capability_fallback.get('chosen_fallback')} "
            f"({capability_fallback.get('equivalence')}; unavailable: {unavailable})"
        )
    local_execution = decision.get("local_execution", {})
    memory = local_execution.get("memory", {})
    if local_execution.get("admitted"):
        lines.append(
            f"- Local execution: {local_execution.get('recipe_id')} / "
            f"{local_execution.get('local_stack_purpose')}"
        )
        lines.append(
            f"- Local context: {local_execution.get('project_id')} / "
            f"{local_execution.get('task_type')} / source {local_execution.get('source_need')}"
        )
    lines.append(
        f"- Memory: {memory.get('mode', 'none')} / "
        f"scope {memory.get('scope') or 'none'} / capture {memory.get('capture_when', 'durable_task_outcome')}"
    )
    fallback = decision.get("fallback", {})
    lines.append(
        "- Fallback: return to Codex on unavailable, timeout, or error. "
        f"Automatic retry: {str(bool(fallback.get('automatic_retry'))).lower()}"
    )
    reasons = ", ".join(str(item) for item in decision.get("reason_codes", []))
    lines.append(f"- Reason codes: {reasons}")
    lines.append(f"- Decision digest: {decision.get('decision_digest', '')}")
    lines.append(f"- Task text SHA-256: {decision.get('task_text_sha256', '')}")
    lines.append(f"- Task input SHA-256: {decision.get('task_input_sha256', '')}")
    lines.append(f"- Task input mode: {decision.get('task_input_mode', '')}")
    issuance = decision.get("issuance", {})
    lines.append(
        f"- Issuance: {issuance.get('status', 'failed')} / "
        f"schema {issuance.get('registry_schema_version', 0)} / "
        f"failure {issuance.get('failure_code') or 'none'}"
    )
    receipt = verify_registered_route(decision)
    lines.append(f"- Route registry: {receipt.get('status', 'registry_error')}")
    authority_limit = str(decision.get("authority_limit") or "advisory-only")
    lines.append(f"- Authority limit: {authority_limit}")
    lines.append(
        "ROUTE_DECISION_JSON="
        + json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return "\n".join(lines)


def main() -> None:
    data = load_input()
    prompt = get_prompt(data)
    raw_classification = data.get("task_classification", data.get("classification"))
    classification = dict(raw_classification) if isinstance(raw_classification, dict) else {}
    for key in ("project_id", "project", "project_identity", "cwd", "workspace_root", "project_root"):
        if key not in classification and data.get(key):
            classification[key] = data[key]
    task_input = data.get("task_input")
    nested_instruction = (
        task_input.get("instruction") if isinstance(task_input, dict) else None
    )
    task_text = next(
        (
            candidate
            for candidate in (
                data.get("task_text"),
                data.get("bounded_task_text"),
                nested_instruction,
            )
            if isinstance(candidate, str)
        ),
        None,
    )
    try:
        decision = route_for_prompt(
            prompt,
            classification,
            task_text,
        )
    except Exception:
        decision = conservative_default_decision(
            "ROUTER_FAIL_OPEN",
            prompt=prompt,
            classification=classification,
            task_text=task_text,
        )
    emit_additional_context("UserPromptSubmit", render_route(decision))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        decision = conservative_default_decision("ROUTER_FAIL_OPEN")
        emit_additional_context("UserPromptSubmit", render_route(decision))
