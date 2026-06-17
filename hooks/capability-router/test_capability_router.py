#!/usr/bin/env python3
"""Regression tests for the optional capability router hook."""

from __future__ import annotations

import capability_index as index
import user_prompt_skill_router as prompt_router


FIXTURE_INDEX = {
    "entries": [
        {
            "kind": "skill",
            "name": "catalogue-router",
            "description": "Route tasks through the skills and plugins catalogue",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "doc",
            "description": "Read DOCX and document source files",
            "status": "active-pack",
        },
        {
            "kind": "plugin",
            "name": "Presentations",
            "description": "Create and edit slide decks and PPTX files",
            "status": "active-plugin",
        },
        {
            "kind": "skill",
            "name": "react-native-skills",
            "description": "React Native mobile implementation guidance",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "agent-browser-verify",
            "description": "Browser screenshot and visual verification",
            "status": "active-plugin",
        },
        {
            "kind": "skill",
            "name": "pre-mortem",
            "description": "Failure-first planning and risk stress test",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "ai-coding-discipline",
            "description": "Bounded coding in existing repos, bug fixes, tests, and implementation discipline",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "cli-creator",
            "description": "Create durable command-line tools and scripts",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "grill-me",
            "description": "Hard planning interview and questions",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "deep-critic",
            "description": "Critique, audit, challenge, validate, and stress test work",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "skill-creator",
            "description": "Create, improve, test, and verify Codex skills",
            "status": "active-pack",
        },
        {
            "kind": "candidate",
            "name": "example-external-tool",
            "description": "Candidate external tool for optional installation",
            "status": "install-or-run-candidate",
        },
    ]
}


def patch_index() -> None:
    index.ensure_index = lambda *args, **kwargs: FIXTURE_INDEX


def names_for(prompt: str) -> list[str]:
    patch_index()
    return [entry["name"] for entry in index.query_index(prompt, limit=5)]


def assert_not_present(prompt: str, forbidden: set[str]) -> None:
    names = set(names_for(prompt))
    overlap = names & forbidden
    if overlap:
        raise AssertionError(f"Unexpected capability matches for {prompt!r}: {sorted(overlap)}")


def test_generic_words_do_not_route_noisy_capabilities() -> None:
    assert_not_present(
        "Check why that file edit created an issue in the workflow.",
        {"doc", "Presentations", "react-native-skills", "agent-browser-verify", "pre-mortem"},
    )


def test_explicit_pptx_routes_to_presentations() -> None:
    names = names_for("Create a PPTX deck from this outline")
    if "Presentations" not in names:
        raise AssertionError(names)


def test_skill_edit_routes_without_browser_noise() -> None:
    names = names_for("Edit this Codex skill and add negative trigger tests")
    if "skill-creator" not in names:
        raise AssertionError(names)
    if "agent-browser-verify" in names:
        raise AssertionError(names)
    if "example-external-tool" in names:
        raise AssertionError(names)


def test_capability_selection_routes_to_catalogue_router() -> None:
    names = names_for("Which skill or plugin should route this task?")
    if "catalogue-router" not in names:
        raise AssertionError(names)


def test_audit_log_does_not_trigger_deep_critic() -> None:
    matches = prompt_router.matched_routes_for_prompt("Read the audit log and summarize the changed files")
    skills = {match["skill"] for match in matches}
    if "deep-critic" in skills:
        raise AssertionError(skills)
    names = names_for("Read the audit log and summarize the changed files")
    if "deep-critic" in names:
        raise AssertionError(names)
    names = names_for("Read the audit log and summarize the changed files. Do not critique it.")
    if names:
        raise AssertionError(names)


def test_existing_repo_bugfix_routes_to_coding_discipline() -> None:
    names = names_for("Fix a bug in an existing repo and run tests before finishing")
    if "ai-coding-discipline" not in names:
        raise AssertionError(names)
    for noisy in ("cli-creator", "grill-me"):
        if noisy in names:
            raise AssertionError(names)


def test_verify_skill_does_not_trigger_evidence_checker() -> None:
    matches = prompt_router.matched_routes_for_prompt(
        "Edit this Codex skill and verify it does not fire on unrelated prompts"
    )
    skills = {match["skill"] for match in matches}
    if "evidence-checker" in skills:
        raise AssertionError(skills)


def test_pptx_has_explicit_presentation_route() -> None:
    matches = prompt_router.matched_routes_for_prompt("Create a PPTX deck from this outline")
    skills = {match["skill"] for match in matches}
    if "Presentations or document-skills:pptx" not in skills:
        raise AssertionError(skills)


def main() -> int:
    tests = [
        test_generic_words_do_not_route_noisy_capabilities,
        test_explicit_pptx_routes_to_presentations,
        test_skill_edit_routes_without_browser_noise,
        test_capability_selection_routes_to_catalogue_router,
        test_audit_log_does_not_trigger_deep_critic,
        test_existing_repo_bugfix_routes_to_coding_discipline,
        test_verify_skill_does_not_trigger_evidence_checker,
        test_pptx_has_explicit_presentation_route,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} capability router tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
