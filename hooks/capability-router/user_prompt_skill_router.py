#!/usr/bin/env python3
"""Optional UserPromptSubmit hook for conservative capability-routing hints."""

from __future__ import annotations

from _hook_io import emit_additional_context, fail_open, get_prompt, load_input, no_output
from capability_index import query_index


ROUTES = [
    {
        "skill": "humanizer",
        "triggers": (
            "humanize",
            "make this sound natural",
            "rewrite naturally",
            "assignment",
            "essay",
            "less ai",
            "less robotic",
        ),
        "guidance": "For rewriting, assignments, or natural voice work, use humanizer and preserve the user's structure unless asked to restructure.",
    },
    {
        "skill": "evidence-checker",
        "triggers": (
            "fact check",
            "sources",
            "citation",
            "latest",
            "documentation",
            "is this true",
            "source check",
            "source verification",
            "verify claims",
            "verify sources",
        ),
        "guidance": "For factual claims, verification, current docs, or source-quality review, use evidence-checker or the narrow official-doc skill when one applies.",
    },
    {
        "skill": "quant-review",
        "triggers": (
            "calculate",
            "forecast",
            "pricing model",
            "unit economics",
            "statistic",
            "roi",
            "sensitivity",
            "market size",
        ),
        "guidance": "For numbers, forecasts, or models, use quant-review and show formula, inputs, units, assumptions, and sensitivity when material.",
    },
    {
        "skill": "deep-critic",
        "triggers": (
            "critique",
            "audit this",
            "audit the",
            "challenge",
            "validate",
            "review this",
            "what is wrong",
            "stress test",
        ),
        "exclude_phrases": (
            "audit log",
            "audit trail",
        ),
        "guidance": "For critique or audit requests, use deep-critic and challenge evidence quality, assumptions, recency, and failure modes.",
    },
    {
        "skill": "ssot-drafter or ssot-auditor",
        "triggers": (
            "sop",
            "policy",
            "playbook",
            "operating model",
            "workflow",
            "governance",
            "raci",
            "daci",
            "process map",
        ),
        "guidance": "For controlled operating artifacts, use ssot-drafter or ssot-auditor and write as the responsible party.",
    },
    {
        "skill": "security-best-practices or security-threat-model",
        "triggers": (
            "security",
            "threat model",
            "vulnerability",
            "secret",
            "api key",
            "permission",
            "auth",
            "privacy",
        ),
        "guidance": "For security or privacy work, use the narrow security skill. For code diffs, include security-diff-scan when available.",
    },
    {
        "skill": "new-project-documentation-system or technical-docs-pack",
        "triggers": (
            "new project",
            "project documentation",
            "prd",
            "tdd",
            "technical documentation",
            "repo docs",
            "agents.md",
            "handoff note",
        ),
        "guidance": "For new-project documentation, route orchestration to new-project-documentation-system and detailed repo templates to technical-docs-pack.",
    },
    {
        "skill": "Presentations or document-skills:pptx",
        "triggers": (
            "deck",
            "powerpoint",
            "presentation",
            "ppt",
            "pptx",
            "slide deck",
            "slides",
        ),
        "guidance": "For presentation or PPTX work, use the presentation capability and avoid document or product-planning skills unless source extraction or PRD work is explicitly requested.",
    },
    {
        "skill": "playwright",
        "triggers": (
            "browser test",
            "screenshot",
            "localhost",
            "visual check",
            "ui verification",
            "playwright",
        ),
        "guidance": "For browser or UI verification, use Playwright or browser tooling and report exact rendering or browser errors.",
    },
]


def route_matches(route: dict, lowered: str) -> bool:
    if any(phrase in lowered for phrase in route.get("exclude_phrases", ())):
        return False
    return any(trigger in lowered for trigger in route["triggers"])


def matched_routes_for_prompt(prompt: str) -> list[dict]:
    lowered = prompt.lower()
    return [route for route in ROUTES if route_matches(route, lowered)]


def main() -> None:
    data = load_input()
    prompt = get_prompt(data)
    if not prompt:
        no_output()

    matched = matched_routes_for_prompt(prompt)
    indexed_limit = 2 if matched else 4
    indexed = query_index(prompt, limit=indexed_limit) if indexed_limit else []

    candidate_matches = [entry for entry in indexed if entry.get("kind") == "candidate"]
    if candidate_matches:
        active_matches = [entry for entry in indexed if entry.get("kind") != "candidate"]
        indexed = active_matches[: max(0, indexed_limit - 1)] + candidate_matches[:1]
    if not matched and not indexed:
        no_output()

    lines = [
        "Capability routing hints detected. Choose one narrow primary capability, and load full skill bodies only after selection."
    ]
    for route in matched[:2]:
        lines.append(f"- {route['skill']}: {route['guidance']}")

    seen = {route["skill"] for route in matched}
    for entry in indexed:
        name = entry.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)
        description = entry.get("description", "").replace("\n", " ")[:220]
        status = entry.get("status", "")
        kind = entry.get("kind", "capability")
        if kind == "candidate":
            lines.append(
                f"- Candidate `{name}` [{status}]: {description} This is not installed. Do not install automatically."
            )
        else:
            lines.append(f"- {kind} `{name}` [{status}]: {description}")
        if len(lines) >= 5:
            break

    emit_additional_context("UserPromptSubmit", "\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        fail_open("user_prompt_skill_router", exc)
