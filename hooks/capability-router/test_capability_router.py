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
            "name": "github",
            "description": "GitHub pull request, CI, merge, review protection, and branch protection workflows",
            "status": "active-plugin",
        },
        {
            "kind": "skill",
            "name": "project-session-continuity",
            "description": "Session starts, handoffs, blockers, manifests, and project continuity",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "security-best-practices",
            "description": "Secure coding, authentication, authorization, secrets, privacy, and vulnerability review",
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
            "kind": "skill",
            "name": "contract-review",
            "description": "Review contracts, clauses, agreements, MSA, SLA, handover, and termination terms",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "quant-review",
            "description": "Calculations, forecasts, models, statistics, ROI, and sensitivity analysis",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "public-equity-investing",
            "description": "Public equity investment research and market analysis",
            "status": "active-plugin",
        },
        {
            "kind": "skill",
            "name": "suggest-sales-next-step",
            "description": "Suggest sales next steps for deals, buyers, leads, and pipeline",
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


def test_implementation_validate_does_not_trigger_deep_critic() -> None:
    prompt = "Fix the Supabase row-level security policies, add tests, and validate the migration."
    matches = prompt_router.matched_routes_for_prompt(prompt)
    skills = {match["skill"] for match in matches}
    if "deep-critic" in skills:
        raise AssertionError(skills)
    names = names_for(prompt)
    if "deep-critic" in names:
        raise AssertionError(names)


def test_pr_ci_merge_rules_route_to_github_without_contract_noise() -> None:
    prompt = "Review PR #5 CI status and merge rules. Do not admin-merge without approval."
    matches = prompt_router.matched_routes_for_prompt(prompt)
    skills = {match["skill"] for match in matches}
    if "github:github" not in skills or "ai-coding-discipline" not in skills:
        raise AssertionError(skills)
    names = names_for(prompt)
    if "github" not in names or "ai-coding-discipline" not in names:
        raise AssertionError(names)
    for noisy in ("contract-review", "quant-review"):
        if noisy in names:
            raise AssertionError(names)


def test_repo_health_permission_error_routes_to_coding_not_security() -> None:
    prompt = (
        "In daily-intelligence-os, run the health check, preserve generated Markdown, "
        "and stop if WinError 10013 socket permission blocks the run."
    )
    matches = prompt_router.matched_routes_for_prompt(prompt)
    skills = {match["skill"] for match in matches}
    if "security-best-practices or security-threat-model" in skills:
        raise AssertionError(skills)
    names = names_for(prompt)
    if "ai-coding-discipline" not in names:
        raise AssertionError(names)
    if "security-best-practices" in names:
        raise AssertionError(names)


def test_router_failure_analysis_does_not_route_to_sales() -> None:
    matches = prompt_router.matched_routes_for_prompt(
        "Analyze the Codex router reasoning failure, run a simulation, report findings, and do not fix anything yet."
    )
    skills = {match["skill"] for match in matches}
    if "catalogue-router" not in skills:
        raise AssertionError(skills)
    names = names_for(
        "Analyze the Codex router reasoning failure, run a simulation, report findings, and do not fix anything yet."
    )
    if "suggest-sales-next-step" in names:
        raise AssertionError(names)


def test_public_repo_release_hygiene_does_not_route_to_public_equity() -> None:
    names = names_for(
        "Proceed with accepted public-release hygiene recommendations in the codex-coding-os repo."
    )
    if "public-equity-investing" in names:
        raise AssertionError(names)


def test_code_allowed_docs_prompt_routes_to_master_workflow() -> None:
    matches = prompt_router.matched_routes_for_prompt(
        "For ClientProject, review the full Docs corpus and coding plans. code_allowed is false."
    )
    skills = {match["skill"] for match in matches}
    if "codex-coding-os-master" not in skills:
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
        test_implementation_validate_does_not_trigger_deep_critic,
        test_pr_ci_merge_rules_route_to_github_without_contract_noise,
        test_repo_health_permission_error_routes_to_coding_not_security,
        test_router_failure_analysis_does_not_route_to_sales,
        test_public_repo_release_hygiene_does_not_route_to_public_equity,
        test_code_allowed_docs_prompt_routes_to_master_workflow,
        test_pptx_has_explicit_presentation_route,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} capability router tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
