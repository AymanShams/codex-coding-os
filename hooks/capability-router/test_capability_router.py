#!/usr/bin/env python3
"""Regression tests for the optional capability router hook."""

from __future__ import annotations

from dataclasses import replace

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
            "name": "market-sizing",
            "description": "Market size, go-to-market, startup strategy, pricing, and business strategy",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "ssot-drafter",
            "description": "Draft formal SOPs, policies, workflows, playbooks, and governance documents",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "ssot-auditor",
            "description": "Audit formal SOPs, policies, workflows, playbooks, and governance documents",
            "status": "active-pack",
        },
        {
            "kind": "skill",
            "name": "artifact-validation-workflow",
            "description": "Validate controlled documents, readiness, acceptance checks, and handoff artifacts",
            "status": "active-pack",
        },
        {
            "kind": "mcp",
            "name": "project-doc-search",
            "description": "Project document search and source retrieval MCP",
            "status": "active-mcp",
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

FAMILY_FIXTURE = {
    "catalogue-router": ("capability_selection", "process", "primary-capable", "material-only"),
    "doc": ("document", "", "primary-capable", "material-only"),
    "Presentations": ("presentation", "", "primary-capable", "material-only"),
    "react-native-skills": ("code", "frontend", "primary-capable", "material-only"),
    "agent-browser-verify": ("browser_verification", "frontend", "primary-capable", "material-only"),
    "pre-mortem": ("critique", "business_strategy, evidence, operational_rca, quantitative", "primary-capable, master-review-support", "default-for-noncoding"),
    "ai-coding-discipline": ("code", "github, process, project_continuity, security", "primary-capable", "material-only"),
    "github": ("github", "code, process", "primary-capable", "material-only"),
    "project-session-continuity": ("project_continuity", "code, process", "primary-capable", "material-only"),
    "security-best-practices": ("security", "code", "primary-capable", "material-only"),
    "cli-creator": ("code", "", "primary-capable", "material-only"),
    "grill-me": ("critique", "", "primary-capable", "material-only"),
    "deep-critic": ("critique", "business_strategy, controlled_document, creative, document, evidence, operational_rca, process, quantitative", "primary-capable, master-review-support", "default-for-noncoding"),
    "skill-creator": ("skill_authoring", "critique", "primary-capable", "material-only"),
    "contract-review": ("contract", "controlled_document", "primary-capable", "material-only"),
    "quant-review": ("quantitative", "business_strategy, finance, spreadsheet", "primary-capable, master-review-support", "default-for-noncoding"),
    "market-sizing": ("business_strategy", "quantitative", "primary-capable", "material-only"),
    "ssot-drafter": ("controlled_document", "document, evidence, process", "primary-capable, master-review-support", "default-for-noncoding"),
    "ssot-auditor": ("controlled_document", "critique, document, evidence, process", "primary-capable, master-review-support", "default-for-noncoding"),
    "artifact-validation-workflow": ("controlled_document", "critique, document, evidence, process", "primary-capable, master-review-support", "default-for-noncoding"),
    "project-doc-search": ("", "evidence, rag", "source-tool", "source-only"),
    "public-equity-investing": ("finance", "business_strategy", "primary-capable", "material-only"),
    "suggest-sales-next-step": ("sales", "business_strategy", "primary-capable", "material-only"),
    "example-external-tool": ("reference", "", "reference-only", "explicit-request-only"),
}

for entry in FIXTURE_INDEX["entries"]:
    primary, support, role, bias = FAMILY_FIXTURE.get(entry["name"], ("", "", "", ""))
    entry["primary_families"] = primary
    entry["support_families"] = support
    entry["all_families"] = ", ".join(filter(None, [primary, support]))
    entry["routing_role"] = role
    entry["support_bias"] = bias


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


def test_strategy_prompt_gets_master_review_support_without_losing_primary() -> None:
    patch_index()
    context = prompt_router.classify_prompt("Build a pricing strategy and challenge the assumptions.")
    if context.primary_family_candidates != frozenset({"business_strategy"}):
        raise AssertionError(context)
    if not {"critique", "evidence"} <= set(context.supporting_family_candidates):
        raise AssertionError(context)
    names = [
        entry["name"]
        for entry in index.query_index(
            "Build a pricing strategy and challenge the assumptions.",
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            limit=6,
        )
    ]
    if "market-sizing" not in names or "deep-critic" not in names:
        raise AssertionError(names)


def test_sop_policy_surfaces_controlled_document_candidates() -> None:
    patch_index()
    context = prompt_router.classify_prompt("Draft an SOP policy and audit workflow for approvals.")
    if context.primary_family_candidates != frozenset({"controlled_document"}):
        raise AssertionError(context)
    matches = prompt_router.matched_routes_for_prompt("Draft an SOP policy and audit workflow for approvals.", context)
    route_text = {prompt_router.route_candidate_text(match) for match in matches}
    if not any("ssot-drafter" in item and "ssot-auditor" in item for item in route_text):
        raise AssertionError(route_text)
    names = [
        entry["name"]
        for entry in index.query_index(
            "Draft an SOP policy and audit workflow for approvals.",
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            limit=6,
        )
    ]
    for expected in {"ssot-drafter", "ssot-auditor", "artifact-validation-workflow"}:
        if expected not in names:
            raise AssertionError(names)


def test_source_tool_is_source_access_not_skill_owner() -> None:
    patch_index()
    results = index.query_index(
        "Use project source documents to verify the implementation plan.",
        primary_families={"document"},
        supporting_families={"evidence", "rag"},
        source_tool_requirements={"project-doc-search"},
        limit=5,
    )
    by_name = {entry["name"]: entry for entry in results}
    source_tool = by_name.get("project-doc-search")
    if not source_tool or source_tool.get("routing_role") != "source-tool":
        raise AssertionError(results)
    context = prompt_router.classify_prompt("Use project source documents to verify the implementation plan.")
    role = prompt_router.entry_role(
        source_tool,
        replace(context, source_tool_requirements=frozenset({"project-doc-search"})),
    )
    if role != "Source/data access":
        raise AssertionError(role)


def test_coding_prompt_does_not_pull_noncoding_master_review_by_default() -> None:
    patch_index()
    context = prompt_router.classify_prompt("Fix the auth bug in the existing repo and run tests.")
    if context.primary_family_candidates != frozenset({"code"}):
        raise AssertionError(context)
    if "critique" in context.supporting_family_candidates:
        raise AssertionError(context)
    names = [
        entry["name"]
        for entry in index.query_index(
            "Fix the auth bug in the existing repo and run tests.",
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            limit=6,
        )
    ]
    if "deep-critic" in names or "pre-mortem" in names:
        raise AssertionError(names)


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
        test_strategy_prompt_gets_master_review_support_without_losing_primary,
        test_sop_policy_surfaces_controlled_document_candidates,
        test_source_tool_is_source_access_not_skill_owner,
        test_coding_prompt_does_not_pull_noncoding_master_review_by_default,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} capability router tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
