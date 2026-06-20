#!/usr/bin/env python3
"""Regression tests for the optional capability router hook."""

from __future__ import annotations

from dataclasses import replace

import capability_index as index
import capability_index_cli as index_cli
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
            "name": "codex-coding-os-master",
            "description": "Master workflow for existing repos, new software projects, manifests, and coding gates",
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
            "kind": "plugin",
            "name": "Browser",
            "description": "In-app browser automation for local targets and web UI checks",
            "status": "active-plugin",
        },
        {
            "kind": "plugin",
            "name": "Chrome",
            "description": "Chrome profile control, existing tabs, remote debugging, browser inspection, and automation",
            "status": "active-plugin",
        },
        {
            "kind": "plugin",
            "name": "Computer Use",
            "description": "Desktop UI automation and non-browser workflows with explicit user approval",
            "status": "active-plugin",
        },
        {
            "kind": "plugin",
            "name": "Build Web Apps",
            "description": "Frontend apps, browser testing, and UI implementation guidance",
            "status": "catalogue-plugin-active",
        },
        {
            "kind": "plugin",
            "name": "Remote Browser Candidate",
            "description": "Available remote browser automation plugin that is not active locally",
            "status": "catalogue-plugin-available",
        },
        {
            "kind": "plugin",
            "name": "Creative Production",
            "description": "Campaign concepts, visual direction, images, moodboards, ads, logos, and scenes",
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
            "name": "new-project-documentation-system",
            "description": "New software project documentation, PRD, TDD, workflow manifests, and implementation plans",
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
        {
            "kind": "candidate",
            "name": "browser-session-candidate",
            "description": "Candidate browser automation helper for optional session-only support",
            "status": "install-or-run-candidate",
        },
        {
            "kind": "candidate",
            "name": "browser-reference-guide",
            "description": "Reference-only browser automation pattern library for session-only consultation",
            "status": "reference-only",
        },
    ]
}

FAMILY_FIXTURE = {
    "catalogue-router": ("capability_selection", "process", "primary-capable", "material-only"),
    "codex-coding-os-master": ("code_orchestration", "code, process, project_continuity", "primary-capable", "material-only"),
    "doc": ("document", "", "primary-capable", "material-only"),
    "Presentations": ("presentation", "", "primary-capable", "material-only"),
    "Browser": ("browser_verification", "", "primary-capable", "material-only"),
    "Chrome": ("browser_verification", "frontend, mcp", "primary-capable", "material-only"),
    "Computer Use": ("browser_verification", "process", "primary-capable", "material-only"),
    "Build Web Apps": ("frontend", "browser_verification", "primary-capable", "explicit-request-only"),
    "Remote Browser Candidate": ("browser_verification", "", "primary-capable", "explicit-request-only"),
    "Creative Production": ("creative", "", "primary-capable", "material-only"),
    "react-native-skills": ("code", "frontend", "primary-capable", "material-only"),
    "agent-browser-verify": ("browser_verification", "frontend", "primary-capable", "material-only"),
    "pre-mortem": ("critique", "business_strategy, evidence, operational_rca, quantitative", "primary-capable, master-review-support", "default-for-noncoding"),
    "ai-coding-discipline": ("code", "github, process, project_continuity, security", "primary-capable", "material-only"),
    "github": ("github", "code, process", "primary-capable", "material-only"),
    "project-session-continuity": ("project_continuity", "code, process", "primary-capable", "material-only"),
    "new-project-documentation-system": ("new_project_doc", "code_orchestration, project_continuity", "primary-capable", "material-only"),
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
    "browser-session-candidate": ("browser_verification", "", "reference-only", "explicit-request-only"),
    "browser-reference-guide": ("browser_verification", "", "reference-only", "explicit-request-only"),
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


def test_campaign_service_prompt_routes_to_creative_not_code() -> None:
    patch_index()
    prompt = "Create a campaign concept and visual direction for a new service."
    context = prompt_router.classify_prompt(prompt)
    if context.task_object != "creative_asset":
        raise AssertionError(context)
    if context.primary_family_candidates != frozenset({"creative"}):
        raise AssertionError(context)
    if "code" in context.supporting_family_candidates or "code_orchestration" in context.supporting_family_candidates:
        raise AssertionError(context)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            limit=6,
        )
    ]
    if "Creative Production" not in names or "ai-coding-discipline" in names:
        raise AssertionError(names)


def test_github_no_merge_or_push_is_read_only() -> None:
    patch_index()
    prompt = "Review GitHub PR #8 CI status and branch protection. Do not merge or push."
    context = prompt_router.classify_prompt(prompt)
    if context.permission_mode != "read_only":
        raise AssertionError(context)
    if context.primary_family_candidates != frozenset({"github"}):
        raise AssertionError(context)
    if not {"code", "code_orchestration"} <= set(context.denied_families):
        raise AssertionError(context)
    if {"code", "code_orchestration"} & set(context.supporting_family_candidates):
        raise AssertionError(context)
    skills = {match["skill"] for match in prompt_router.matched_routes_for_prompt(prompt, context)}
    if "github:github" not in skills:
        raise AssertionError(skills)
    if "ai-coding-discipline" in skills or "codex-coding-os-master" in skills:
        raise AssertionError(skills)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            denied_families=context.denied_families,
            limit=6,
        )
    ]
    if "github" not in names or "ai-coding-discipline" in names:
        raise AssertionError(names)
    if "yeet" in names or "gh-fix-ci" in names:
        raise AssertionError(names)


def test_github_frontend_pr_review_keeps_github_primary_with_frontend_support() -> None:
    patch_index()
    prompt = "Review GitHub PR #36 for a Next.js React web scaffold in apps/web. Do not edit files."
    context = prompt_router.classify_prompt(prompt)
    if context.task_object != "github":
        raise AssertionError(context)
    if context.permission_mode != "read_only":
        raise AssertionError(context)
    if context.primary_family_candidates != frozenset({"github"}):
        raise AssertionError(context)
    if "frontend" not in context.supporting_family_candidates:
        raise AssertionError(context)
    if "critique" not in context.supporting_family_candidates:
        raise AssertionError(context)
    if "code_orchestration" not in context.denied_families:
        raise AssertionError(context)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            denied_families=context.denied_families,
            limit=8,
        )
    ]
    if "github" not in names or "Build Web Apps" not in names:
        raise AssertionError(names)
    if "codex-coding-os-master" in names:
        raise AssertionError(names)


def test_github_security_pr_review_adds_security_support_without_code_ownership() -> None:
    patch_index()
    prompt = "Review GitHub PR #7 for auth and authorization changes. Do not edit files."
    context = prompt_router.classify_prompt(prompt)
    if context.task_object != "github":
        raise AssertionError(context)
    if context.permission_mode != "read_only":
        raise AssertionError(context)
    if context.primary_family_candidates != frozenset({"github"}):
        raise AssertionError(context)
    if "security" not in context.supporting_family_candidates:
        raise AssertionError(context)
    if not {"code", "code_orchestration"} <= set(context.denied_families):
        raise AssertionError(context)


def test_generic_github_strategy_review_does_not_add_frontend_support() -> None:
    patch_index()
    prompt = "Review GitHub PR #41 for website pricing strategy copy. Do not edit files."
    context = prompt_router.classify_prompt(prompt)
    if context.task_object != "github":
        raise AssertionError(context)
    if context.primary_family_candidates != frozenset({"github"}):
        raise AssertionError(context)
    if "frontend" in context.supporting_family_candidates:
        raise AssertionError(context)


def test_existing_repo_implementation_routes_to_master_with_code_support() -> None:
    patch_index()
    prompt = "In an existing repo, implement a bug fix in the auth API and run tests."
    context = prompt_router.classify_prompt(prompt)
    if context.primary_family_candidates != frozenset({"code_orchestration"}):
        raise AssertionError(context)
    if not {"code", "security"} <= set(context.supporting_family_candidates):
        raise AssertionError(context)
    skills = {match["skill"] for match in prompt_router.matched_routes_for_prompt(prompt, context)}
    if "codex-coding-os-master" not in skills or "ai-coding-discipline" not in skills:
        raise AssertionError(skills)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            limit=6,
        )
    ]
    if not names or names[0] != "codex-coding-os-master":
        raise AssertionError(names)
    if "codex-coding-os-master" not in names or "ai-coding-discipline" not in names:
        raise AssertionError(names)


def test_new_software_project_routes_to_master_with_docs_and_continuity_support() -> None:
    patch_index()
    prompt = "Start a new software project in an unclear repo and prepare coding plans before implementation."
    context = prompt_router.classify_prompt(prompt)
    if context.task_object != "project_setup":
        raise AssertionError(context)
    if context.primary_family_candidates != frozenset({"code_orchestration"}):
        raise AssertionError(context)
    if not {"code", "new_project_doc", "project_continuity"} <= set(context.supporting_family_candidates):
        raise AssertionError(context)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            limit=8,
        )
    ]
    if not names or names[0] != "codex-coding-os-master":
        raise AssertionError(names)
    for expected in {"ai-coding-discipline", "new-project-documentation-system", "project-session-continuity"}:
        if expected not in names:
            raise AssertionError(names)


def test_business_project_documentation_does_not_route_to_coding_os() -> None:
    patch_index()
    prompt = "Create project documentation and a PRD for a business onboarding initiative."
    context = prompt_router.classify_prompt(prompt)
    if context.task_object != "project_setup":
        raise AssertionError(context)
    if context.primary_family_candidates != frozenset({"new_project_doc"}):
        raise AssertionError(context)
    if "code_orchestration" in context.primary_family_candidates:
        raise AssertionError(context)


def test_plugin_installability_query_suppresses_review_skill_noise() -> None:
    patch_index()
    prompt = "Which plugins are available to install for browser automation and should we install one?"
    context = prompt_router.classify_prompt(prompt)
    if context.primary_family_candidates != frozenset({"capability_selection"}):
        raise AssertionError(context)
    if context.candidate_visibility != "include_install_candidates":
        raise AssertionError(context)
    if context.supporting_family_candidates:
        raise AssertionError(context)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            candidate_visibility=context.candidate_visibility,
            include_candidates=True,
            limit=8,
        )
    ]
    for expected in {"catalogue-router", "Browser", "Chrome", "Computer Use"}:
        if expected not in names:
            raise AssertionError(names)
    gated_names = {"browser-session-candidate", "Remote Browser Candidate"}
    selected_gated = [name for name in names if name in gated_names]
    if len(selected_gated) != 1:
        raise AssertionError(names)
    gated_name = selected_gated[0]
    if names.index(gated_name) < names.index("Browser"):
        raise AssertionError(names)
    by_name = {entry["name"]: entry for entry in index.query_index(
        prompt,
        primary_families=context.primary_family_candidates,
        supporting_families=context.supporting_family_candidates,
        candidate_visibility=context.candidate_visibility,
        include_candidates=True,
        limit=8,
    )}
    role = prompt_router.entry_role(by_name[gated_name], context)
    if role != "Gated session-only support":
        raise AssertionError(role)
    for noisy in {
        "deep-critic",
        "pre-mortem",
        "quant-review",
        "artifact-validation-workflow",
        "example-external-tool",
        "ai-coding-discipline",
        "yeet",
    }:
        if noisy in names:
            raise AssertionError(names)


def test_reference_only_candidate_requires_explicit_session_authorization() -> None:
    patch_index()
    prompt = "Which reference-only browser automation material could support this session after active tools?"
    context = prompt_router.classify_prompt(prompt)
    if context.primary_family_candidates != frozenset({"capability_selection"}):
        raise AssertionError(context)
    if context.candidate_visibility != "include_reference":
        raise AssertionError(context)
    results = index.query_index(
        prompt,
        primary_families=context.primary_family_candidates,
        supporting_families=context.supporting_family_candidates,
        candidate_visibility=context.candidate_visibility,
        include_candidates=True,
        limit=8,
    )
    names = [entry["name"] for entry in results]
    if "Browser" not in names or "browser-reference-guide" not in names:
        raise AssertionError(names)
    if names.index("browser-reference-guide") < names.index("Browser"):
        raise AssertionError(names)
    role = prompt_router.entry_role({entry["name"]: entry for entry in results}["browser-reference-guide"], context)
    if role != "Gated session-only support":
        raise AssertionError(role)


def test_active_explicit_support_is_not_session_only_gated() -> None:
    patch_index()
    context = prompt_router.classify_prompt(
        "Which plugins are available to install for browser automation and should we install one?"
    )
    by_name = {entry["name"]: entry for entry in FIXTURE_INDEX["entries"]}
    active_explicit = by_name["Build Web Apps"]
    if index.is_session_only_candidate(active_explicit):
        raise AssertionError(active_explicit)
    if prompt_router.entry_role(active_explicit, context) == "Gated session-only support":
        raise AssertionError(active_explicit)

    available_remote = by_name["Remote Browser Candidate"]
    if not index.is_session_only_candidate(available_remote):
        raise AssertionError(available_remote)
    role = prompt_router.entry_role(available_remote, context)
    if role != "Gated session-only support":
        raise AssertionError(role)


def test_cli_inactive_mode_uses_prompt_specific_visibility() -> None:
    plugin_visibility = index_cli.candidate_visibility_for_query(
        "Which plugins are available to install for browser automation?"
    )
    if plugin_visibility != "include_install_candidates":
        raise AssertionError(plugin_visibility)
    reference_visibility = index_cli.candidate_visibility_for_query(
        "Which reference-only browser automation material could support this session?"
    )
    if reference_visibility != "include_reference":
        raise AssertionError(reference_visibility)


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
    if context.primary_family_candidates != frozenset({"code_orchestration"}):
        raise AssertionError(context)
    if not {"code", "security"} <= set(context.supporting_family_candidates):
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
    if not names or names[0] != "codex-coding-os-master":
        raise AssertionError(names)
    if "codex-coding-os-master" not in names or "ai-coding-discipline" not in names:
        raise AssertionError(names)
    if "deep-critic" in names or "pre-mortem" in names:
        raise AssertionError(names)


def test_generic_next_steps_does_not_surface_frontend_candidates() -> None:
    patch_index()
    prompt = "Suggest next steps for this work."
    context = prompt_router.classify_prompt(prompt)
    if "frontend" in context.primary_family_candidates or "frontend" in context.supporting_family_candidates:
        raise AssertionError(context)
    skills = {match["skill"] for match in prompt_router.matched_routes_for_prompt(prompt, context)}
    if "build-web-apps:frontend-app-builder" in skills or "vercel:nextjs" in skills:
        raise AssertionError(skills)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            limit=8,
        )
    ]
    if "Build Web Apps" in names or "react-native-skills" in names:
        raise AssertionError(names)


def test_bare_next_steps_pr_review_does_not_add_frontend_support() -> None:
    patch_index()
    prompt = "Review GitHub PR #41 and suggest next steps. Do not edit files."
    context = prompt_router.classify_prompt(prompt)
    if context.primary_family_candidates != frozenset({"github"}):
        raise AssertionError(context)
    if "frontend" in context.supporting_family_candidates:
        raise AssertionError(context)
    skills = {match["skill"] for match in prompt_router.matched_routes_for_prompt(prompt, context)}
    if "build-web-apps:frontend-app-builder / frontend-testing-debugging / react-best-practices" in skills:
        raise AssertionError(skills)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            denied_families=context.denied_families,
            limit=8,
        )
    ]
    if "Build Web Apps" in names or "react-best-practices" in names:
        raise AssertionError(names)


def test_contextual_next_app_keeps_frontend_support() -> None:
    patch_index()
    prompt = "Review GitHub PR #42 for a Next app scaffold in apps/web. Do not edit files."
    context = prompt_router.classify_prompt(prompt)
    if context.primary_family_candidates != frozenset({"github"}):
        raise AssertionError(context)
    if "frontend" not in context.supporting_family_candidates:
        raise AssertionError(context)
    skills = {match["skill"] for match in prompt_router.matched_routes_for_prompt(prompt, context)}
    if "build-web-apps:frontend-app-builder / frontend-testing-debugging / react-best-practices" not in skills:
        raise AssertionError(skills)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            denied_families=context.denied_families,
            limit=8,
        )
    ]
    if "Build Web Apps" not in names:
        raise AssertionError(names)


def test_explicit_nextjs_prompt_keeps_frontend_support() -> None:
    patch_index()
    prompt = "Review GitHub PR #42 for a Next.js React web scaffold in apps/web. Do not edit files."
    context = prompt_router.classify_prompt(prompt)
    if "frontend" not in context.supporting_family_candidates:
        raise AssertionError(context)
    names = [
        entry["name"]
        for entry in index.query_index(
            prompt,
            primary_families=context.primary_family_candidates,
            supporting_families=context.supporting_family_candidates,
            denied_families=context.denied_families,
            limit=8,
        )
    ]
    if "Build Web Apps" not in names:
        raise AssertionError(names)


def test_app_router_phrase_is_frontend_support_not_capability_selection() -> None:
    patch_index()
    prompt = "Review GitHub PR #43 for a Next.js App Router web scaffold in apps/web. Do not edit files."
    context = prompt_router.classify_prompt(prompt)
    if context.primary_family_candidates != frozenset({"github"}):
        raise AssertionError(context)
    if "frontend" not in context.supporting_family_candidates:
        raise AssertionError(context)
    if "capability_selection" in context.primary_family_candidates:
        raise AssertionError(context)


def main() -> int:
    tests = [
        test_generic_words_do_not_route_noisy_capabilities,
        test_explicit_pptx_routes_to_presentations,
        test_skill_edit_routes_without_browser_noise,
        test_capability_selection_routes_to_catalogue_router,
        test_campaign_service_prompt_routes_to_creative_not_code,
        test_github_no_merge_or_push_is_read_only,
        test_github_frontend_pr_review_keeps_github_primary_with_frontend_support,
        test_github_security_pr_review_adds_security_support_without_code_ownership,
        test_generic_github_strategy_review_does_not_add_frontend_support,
        test_existing_repo_implementation_routes_to_master_with_code_support,
        test_new_software_project_routes_to_master_with_docs_and_continuity_support,
        test_business_project_documentation_does_not_route_to_coding_os,
        test_plugin_installability_query_suppresses_review_skill_noise,
        test_reference_only_candidate_requires_explicit_session_authorization,
        test_active_explicit_support_is_not_session_only_gated,
        test_cli_inactive_mode_uses_prompt_specific_visibility,
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
        test_generic_next_steps_does_not_surface_frontend_candidates,
        test_bare_next_steps_pr_review_does_not_add_frontend_support,
        test_contextual_next_app_keeps_frontend_support,
        test_explicit_nextjs_prompt_keeps_frontend_support,
        test_app_router_phrase_is_frontend_support_not_capability_selection,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} capability router tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
