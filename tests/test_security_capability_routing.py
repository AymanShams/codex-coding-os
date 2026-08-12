#!/usr/bin/env python3
"""Security workflow, provider composition, fallback, and negative routing tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTING_ROOT = REPO_ROOT / "capability-routing"
RUNTIME_ROOT = ROUTING_ROOT / "reference-runtime"
_IMPORT_TEMP = tempfile.TemporaryDirectory(prefix="ccos-security-router-import-")
_IMPORT_ROOT = Path(_IMPORT_TEMP.name)
_ROUTER_ENV = {
    "CODEX_HOME": _IMPORT_ROOT,
    "CODEX_CAPABILITY_ROUTING_DIR": _IMPORT_ROOT / "routing",
    "CODEX_ACTIVE_CAPABILITIES_PATH": _IMPORT_ROOT / "active.json",
    "CODEX_ROUTING_POLICY_PATH": ROUTING_ROOT / "routing-policy.yaml",
    "CODEX_CONFIG_PATH": _IMPORT_ROOT / "config.toml",
    "CODEX_ROUTE_DECISION_SCHEMA_PATH": ROUTING_ROOT / "route-decision.schema.json",
    "CODEX_ROUTE_DECISION_REGISTRY_PATH": _IMPORT_ROOT / "routes.sqlite3",
    "CODEX_PROJECT_SCOPE_MAP_PATH": _IMPORT_ROOT / "project-map.json",
}
_OLD_ROUTER_ENV = {name: os.environ.get(name) for name in _ROUTER_ENV}
for _name, _value in _ROUTER_ENV.items():
    os.environ[str(_name)] = str(_value)
sys.path.insert(0, str(RUNTIME_ROOT))
import capability_index as index  # noqa: E402
for _name, _value in _OLD_ROUTER_ENV.items():
    if _value is None:
        os.environ.pop(_name, None)
    else:
        os.environ[_name] = _value


POLICY = json.loads((ROUTING_ROOT / "routing-policy.yaml").read_text("utf-8"))
RULES_BY_ID = {rule["id"]: rule for rule in POLICY["rules"]}


def first_matching_rule(prompt: str) -> dict[str, object] | None:
    for rule in POLICY["rules"]:
        if index._rule_matches_prompt(rule, prompt.lower(), POLICY):
            return rule
    return None


def active_entry(identifier: str, root: Path) -> dict[str, object]:
    name = identifier.split(":", 1)[1] if ":" in identifier else identifier
    return {
        "id": identifier,
        "kind": "skill",
        "name": name,
        "state": "active",
        "provider": name.split(":", 1)[0],
        "version": "1.0.0",
        "source_path": str(root / f"{name.replace(':', '_')}.md"),
        "sha256": "a" * 64,
        "families": [],
    }


def manifest_for_policy(root: Path) -> dict[str, object]:
    references: set[str] = set()
    for rule in POLICY["rules"]:
        references.add(rule["primary"])
        references.update(rule.get("supports", []))
        fallback = rule.get("dependency_fallback") or {}
        if fallback.get("selected_capability"):
            references.add(fallback["selected_capability"])
        references.update(fallback.get("supports", []))
    return {
        "freshness_status": "fresh",
        "snapshot_id": "synthetic-security-routing",
        "source_hashes_verified": True,
        "entries": [active_entry(reference, root) for reference in sorted(references)],
    }


class SecurityCapabilityRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos-security-routing-")
        self.root = Path(self.temp.name)
        self.registry = self.root / "route-decisions.sqlite3"
        self.registry_patch = mock.patch.object(
            index, "ROUTE_DECISION_REGISTRY_PATH", self.registry
        )
        self.registry_patch.start()
        self.request_counter = 0

    def tearDown(self) -> None:
        self.registry_patch.stop()
        self.temp.cleanup()

    def assertRoute(self, prompt: str, rule_id: str, primary: str) -> None:  # noqa: N802
        rule = first_matching_rule(prompt)
        self.assertIsNotNone(rule, prompt)
        self.assertEqual(rule["id"], rule_id, prompt)
        self.assertEqual(rule["primary"], primary, prompt)

    def resolveDecision(self, prompt: str) -> dict[str, object]:  # noqa: N802
        manifest = manifest_for_policy(self.root)
        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            return index.resolve_route(prompt, manifest=manifest, policy=POLICY)

    def resolveWithCallableDependency(  # noqa: N802
        self, prompt: str, dependency_id: str
    ) -> dict[str, object]:
        manifest = manifest_for_policy(self.root)
        dependency_manifest_ids = {
            "app:supabase": "tool-family:app:supabase",
            "app:neon": "tool-family:app:neon",
            "mcp:codex-security": "tool-family:mcp:codex-security",
        }
        manifest["entries"].append(
            active_entry(dependency_manifest_ids[dependency_id], self.root)
        )
        configurations = {
            "app:supabase": {
                "plugins": {
                    "supabase@openai-curated-remote": {"enabled": True}
                }
            },
            "app:neon": {
                "plugins": {
                    "neon-postgres@openai-curated-remote": {"enabled": True}
                }
            },
            "mcp:codex-security": {
                "plugins": {
                    "codex-security@openai-curated-remote": {
                        "mcp_servers": {"codex-security": {"enabled": True}}
                    }
                }
            },
        }
        self.request_counter += 1
        request_id = f"explicit-live-exclusion-{self.request_counter}"
        task_input = {
            "instruction": prompt,
            "execution_request_id": request_id,
            "live_dependency_probes": {
                dependency_id: {
                    "kind": "live_call",
                    "target": dependency_id,
                    "request_id": request_id,
                    "status": "callable",
                }
            },
        }
        with (
            mock.patch.object(index, "_entry_hash_current", return_value=True),
            mock.patch.object(
                index,
                "_load_live_config_inventory",
                return_value=configurations[dependency_id],
            ),
        ):
            return index.resolve_route(
                prompt,
                manifest=manifest,
                policy=POLICY,
                task_input=task_input,
            )

    def test_all_thirteen_codex_security_skills_have_deterministic_actions(self) -> None:
        cases = [
            (
                "Analyze the attack path and exploitability of this vulnerability",
                "security-attack-path-analysis",
                "skill:codex-security:attack-path-analysis",
            ),
            (
                "Run a deep security scan of this repository",
                "deep-security-review",
                "skill:codex-security:deep-security-scan",
            ),
            (
                "Define a security policy for this repository",
                "security-policy-definition",
                "skill:codex-security:define-security-policy",
            ),
            (
                "Find vulnerabilities in this repository",
                "security-finding-discovery",
                "skill:codex-security:finding-discovery",
            ),
            (
                "Fix this validated security finding",
                "security-finding-fix",
                "skill:codex-security:fix-finding",
            ),
            (
                "Propose security hardening for authentication in this service",
                "security-hardening-proposal",
                "skill:codex-security:propose-security-hardening",
            ),
            (
                "Review this pull request diff for authorization security regressions",
                "security-diff-review",
                "skill:codex-security:security-diff-scan",
            ),
            (
                "Run a standard security scan of this repository",
                "standard-security-review",
                "skill:codex-security:security-scan",
            ),
            (
                "Use $threat-model for the security scan artifact",
                "codex-security-threat-model",
                "skill:codex-security:threat-model",
            ),
            (
                "Track these security findings in Linear",
                "security-findings-tracking-linear",
                "skill:codex-security:track-findings",
            ),
            (
                "Triage this security alert as unconfirmed",
                "security-finding-triage",
                "skill:codex-security:triage-finding",
            ),
            (
                "Validate this vulnerability finding",
                "security-finding-validation",
                "skill:codex-security:validation",
            ),
            (
                "Write a vulnerability report for this finding",
                "security-vulnerability-writeup",
                "skill:codex-security:vulnerability-writeup",
            ),
        ]
        for prompt, rule_id, primary in cases:
            with self.subTest(rule_id=rule_id):
                self.assertRoute(prompt, rule_id, primary)

    def test_four_local_security_skills_have_distinct_routes(self) -> None:
        cases = [
            (
                "Review security best practices for this API",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Create a threat model for this application",
                "local-security-threat-model",
                "skill:security-threat-model",
            ),
            (
                "Map security ownership for security-sensitive files",
                "security-ownership-map",
                "skill:security-ownership-map",
            ),
            (
                "Run the defensive security checklist for this API",
                "defensive-security-checklist",
                "skill:defensive-security-checklist",
            ),
        ]
        for prompt, rule_id, primary in cases:
            with self.subTest(rule_id=rule_id):
                self.assertRoute(prompt, rule_id, primary)

    def test_natural_technical_security_reviews_are_bounded_to_software(self) -> None:
        positive_prompts = [
            "Review the security of this API.",
            "Review Neon security configuration.",
            "Review this API security design.",
            "Review the API security.",
            "Validate these RLS policies.",
            "Review this database security plan.",
            "Recommend security improvements for this API.",
            "Analyze authorization in this backend.",
            "Inspect RLS policies in this database.",
            "Verify the RLS policies in this database.",
            "Validate authentication in this API.",
            "Evaluate access control in this app.",
            "Assess API security.",
            "Audit backend security.",
            "Review frontend security.",
            "Review database security.",
            "Review application security.",
            "Review repository security.",
            "Review this backend for security.",
            "Review React security.",
            "Review browser security.",
        ]
        for prompt in positive_prompts:
            with self.subTest(prompt=prompt):
                self.assertRoute(
                    prompt,
                    "security-best-practices-review",
                    "skill:security-best-practices",
                )

        false_positive_prompts = [
            "Review the security of this investment.",
            "Review the job security of this role.",
            "Assess the security of this bond.",
            "Review the security of the building.",
            "Review a report titled Security of Supply Chains.",
            "Review social security policy.",
            "Analyze permissions in this HR process.",
            "Review the conference sessions.",
            "Review cookies for the event.",
            "Review employee privileges.",
            "Review the tokens in this board game.",
            "Review credentials for this applicant.",
            "Review the login page design.",
            "Review this security policy about building access.",
        ]
        for prompt in false_positive_prompts:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertNotEqual(
                    decision.get("rule_id"), "security-best-practices-review"
                )

    def test_provider_aware_scans_precede_generic_scans(self) -> None:
        cases = [
            (
                "Review the Supabase database pull request diff for RLS security regressions",
                "supabase-security-diff-review",
                "skill:supabase:supabase",
            ),
            (
                "Review the Neon Postgres pull request diff for RLS security regressions",
                "neon-security-diff-review",
                "skill:neon-postgres:neon-postgres",
            ),
            (
                "Review the PostgreSQL database pull request diff for RLS security regressions",
                "postgres-security-diff-review",
                "skill:postgres-security-best-practices",
            ),
            (
                "Run a standard security scan of this Supabase database project",
                "supabase-standard-security-review",
                "skill:supabase:supabase",
            ),
            (
                "Run a standard security scan of this Neon Postgres project",
                "neon-standard-security-review",
                "skill:neon-postgres:neon-postgres",
            ),
            (
                "Run a standard security scan of this PostgreSQL database",
                "postgres-standard-security-review",
                "skill:postgres-security-best-practices",
            ),
        ]
        for prompt, rule_id, expected_support in cases:
            with self.subTest(rule_id=rule_id):
                rule = first_matching_rule(prompt)
                self.assertIsNotNone(rule)
                self.assertEqual(rule["id"], rule_id)
                self.assertIn(expected_support, rule["supports"])

    def test_provider_alerts_and_live_validation_compose_provider_semantics(self) -> None:
        cases = [
            (
                "Triage this Supabase security advisor RLS alert",
                "supabase-security-alert-triage",
                "skill:supabase:supabase",
            ),
            (
                "Triage this Neon Postgres RLS security alert",
                "neon-security-alert-triage",
                "skill:neon-postgres:neon-postgres",
            ),
            (
                "Validate this Supabase database RLS security finding",
                "supabase-security-finding-validation",
                "skill:supabase:supabase",
            ),
            (
                "Validate this Neon Postgres RLS security finding",
                "neon-security-finding-validation",
                "skill:neon-postgres:neon-postgres",
            ),
        ]
        for prompt, rule_id, support in cases:
            with self.subTest(rule_id=rule_id):
                rule = first_matching_rule(prompt)
                self.assertEqual(rule["id"], rule_id)
                self.assertIn(support, rule["supports"])
        self.assertEqual(
            RULES_BY_ID["supabase-security-finding-validation"][
                "dependency_fallback"
            ]["equivalence"],
            "non_equivalent",
        )
        self.assertEqual(
            RULES_BY_ID["neon-security-finding-validation"]["dependency_fallback"][
                "equivalence"
            ],
            "non_equivalent",
        )

    def test_security_sensitive_implementation_keeps_coding_os_primary(self) -> None:
        cases = [
            (
                "Implement Supabase RLS policies for this database",
                "coding-supabase-security-boundary",
                "skill:supabase:supabase",
            ),
            (
                "Implement Neon Postgres RLS policies for this database",
                "coding-neon-security-boundary",
                "skill:neon-postgres:neon-postgres",
            ),
            (
                "Implement PostgreSQL RLS policies for this database",
                "coding-postgres-security-boundary",
                "skill:postgres-security-best-practices",
            ),
            (
                "Implement secure HttpOnly cookies in this React frontend",
                "frontend-security-boundary",
                "skill:security-best-practices",
            ),
        ]
        for prompt, rule_id, support in cases:
            with self.subTest(rule_id=rule_id):
                rule = first_matching_rule(prompt)
                self.assertEqual(rule["id"], rule_id)
                self.assertEqual(rule["primary"], "skill:codex-coding-os-master")
                self.assertIn(support, rule["supports"])

    def test_normal_provider_changes_and_operations_are_not_forced_through_supabase(self) -> None:
        cases = [
            ("Apply the Supabase database migration", "coding-supabase-provider-change"),
            ("Apply the Neon Postgres migration", "coding-neon-provider-change"),
            ("Apply the PostgreSQL schema migration", "coding-postgres-provider-change"),
            ("Inspect the Supabase project database configuration", "supabase-operations"),
            ("Inspect the Neon Postgres project configuration", "neon-operations"),
            ("Inspect Postgres roles and grants in this database", "postgres-security-operations"),
            ("Analyze Neon Postgres egress usage", "neon-postgres-egress-optimization"),
        ]
        for prompt, rule_id in cases:
            with self.subTest(rule_id=rule_id):
                self.assertEqual(first_matching_rule(prompt)["id"], rule_id)
        self.assertEqual(
            RULES_BY_ID["coding-neon-provider-change"]["supports"][0],
            "skill:neon-postgres:neon-postgres",
        )

    def test_deep_is_non_equivalent_while_standard_and_diff_are_equivalent(self) -> None:
        for rule_id in (
            "deep-security-review",
            "supabase-deep-security-review",
            "neon-deep-security-review",
            "postgres-deep-security-review",
        ):
            self.assertEqual(
                RULES_BY_ID[rule_id]["dependency_fallback"]["equivalence"],
                "non_equivalent",
            )
        for rule_id in (
            "security-diff-review",
            "supabase-security-diff-review",
            "neon-security-diff-review",
            "postgres-security-diff-review",
            "standard-security-review",
            "supabase-standard-security-review",
            "neon-standard-security-review",
            "postgres-standard-security-review",
        ):
            self.assertEqual(
                RULES_BY_ID[rule_id]["dependency_fallback"]["equivalence"],
                "equivalent",
            )

    def test_runtime_records_equivalent_and_non_equivalent_fallbacks(self) -> None:
        manifest = manifest_for_policy(self.root)
        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            standard = index.resolve_route(
                "Run a standard security scan of this repository",
                manifest=manifest,
                policy=POLICY,
            )
            deep = index.resolve_route(
                "Run a deep security scan of this repository",
                manifest=manifest,
                policy=POLICY,
            )
        self.assertEqual(standard["capability_fallbacks"][0]["equivalence"], "equivalent")
        self.assertEqual(deep["capability_fallbacks"][0]["equivalence"], "non_equivalent")
        self.assertEqual(deep["primary"]["id"], "skill:security-best-practices")

    def test_actual_resolution_preserves_provider_qualified_deep_scan_depth(self) -> None:
        cases = [
            (
                "Run a deep Supabase security scan of this repository",
                "supabase-deep-security-review",
            ),
            (
                "Run a deep Neon Postgres security scan of this repository",
                "neon-deep-security-review",
            ),
            (
                "Run a deep PostgreSQL security scan of this repository",
                "postgres-deep-security-review",
            ),
        ]
        for prompt, rule_id in cases:
            with self.subTest(rule_id=rule_id):
                decision = self.resolveDecision(prompt)
                self.assertEqual(decision["rule_id"], rule_id)
                self.assertEqual(
                    decision["capability_fallbacks"][0]["equivalence"],
                    "non_equivalent",
                )

    def test_common_security_phrasing_resolves_the_intended_workflow(self) -> None:
        cases = [
            (
                "Review these Supabase security alerts",
                "supabase-security-alert-triage",
            ),
            ("Check this security alert", "security-finding-triage"),
            ("Investigate this security alert", "security-finding-triage"),
            (
                "Analyze these Supabase security advisor alerts",
                "supabase-security-alert-triage",
            ),
            ("Inspect this Neon security alert", "neon-security-alert-triage"),
            ("Please do a security scan", "standard-security-review"),
            (
                "Please scan this repository for security issues",
                "standard-security-review",
            ),
            (
                "Scan this repository for security problems",
                "standard-security-review",
            ),
            ("Review this PR for security", "security-diff-review"),
            (
                "Analyze the attack paths and exploitability of this vulnerability",
                "security-attack-path-analysis",
            ),
            (
                "Review my security policy for missing authorization controls",
                "security-best-practices-review",
            ),
        ]
        for prompt, rule_id in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(self.resolveDecision(prompt)["rule_id"], rule_id)

    def test_generic_findings_do_not_activate_security_finding_phases(self) -> None:
        prompts = [
            "Triage this finding in the product roadmap",
            "Validate this finding in the usability study",
            "Fix this finding in the grammar review",
        ]
        security_phase_rules = {
            "security-finding-triage",
            "security-finding-validation",
            "security-finding-fix",
            "security-finding-discovery",
        }
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                rule = first_matching_rule(prompt)
                self.assertNotIn(rule["id"] if rule else None, security_phase_rules)

    def test_all_thirteen_security_actions_respect_object_bound_negation(self) -> None:
        cases = [
            (
                "Analyze this architecture, not the attack path of the security finding",
                "security-attack-path-analysis",
            ),
            (
                "Run unit tests, not a deep security scan of this repository",
                "deep-security-review",
            ),
            (
                "Draft the README, not a security policy for this repository",
                "security-policy-definition",
            ),
            (
                "Search the documentation, not discover vulnerabilities in this repository",
                "security-finding-discovery",
            ),
            (
                "Patch the formatting, not fix this security finding",
                "security-finding-fix",
            ),
            (
                "Prepare the roadmap, not security hardening recommendations for authentication",
                "security-hardening-proposal",
            ),
            (
                "Review this pull request without a security review",
                "security-diff-review",
            ),
            (
                "Run unit tests on this repository, not a security scan",
                "standard-security-review",
            ),
            (
                "Use the local checklist, not the threat model for this security scan artifact",
                "codex-security-threat-model",
            ),
            (
                "Update the summary, not track these security findings in Linear",
                "security-findings-tracking-linear",
            ),
            (
                "Review the release notes, not triage this security alert",
                "security-finding-triage",
            ),
            (
                "Verify the build, not validate this vulnerability finding",
                "security-finding-validation",
            ),
            (
                "Write a summary, not a vulnerability report, for this security finding",
                "security-vulnerability-writeup",
            ),
        ]
        for prompt, forbidden_rule_id in cases:
            with self.subTest(forbidden_rule_id=forbidden_rule_id):
                decision = self.resolveDecision(prompt)
                self.assertNotEqual(decision["rule_id"], forbidden_rule_id)
                primary = decision["primary"] or {}
                self.assertNotEqual(
                    primary.get("id"),
                    RULES_BY_ID[forbidden_rule_id]["primary"],
                )

    def test_skip_omit_exclude_and_except_for_negate_security_objects(self) -> None:
        prompts = [
            "Run unit tests and skip the security scan",
            "Run unit tests and omit the security scan",
            "Run unit tests and exclude the security scan",
            "Review PR except for security",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertNotIn(
                    decision["rule_id"],
                    {"standard-security-review", "security-diff-review"},
                )
                primary = decision["primary"] or {}
                self.assertNotIn(
                    primary.get("id"),
                    {
                        "skill:codex-security:security-scan",
                        "skill:codex-security:security-diff-scan",
                    },
                )

    def test_markdown_and_multiline_examples_are_not_live_directives(self) -> None:
        prompts = [
            "Quoted example:\n```text\nrun a standard security scan of this repository\n```\nSummarize it.",
            "Quoted example:\n```text\nrun a standard security scan of this repository",
            "Quoted example:\n    run a standard security scan of this repository\nSummarize it.",
            "Quoted example: `run a standard security scan of this repository`. Summarize it.",
            "Quoted example:\n> run a standard security scan of this repository\nSummarize it.",
            'Quoted example:\n"run a standard security scan\nof this repository"\nSummarize it.',
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertNotEqual(decision["rule_id"], "standard-security-review")
                primary = decision["primary"] or {}
                self.assertNotEqual(
                    primary.get("id"), "skill:codex-security:security-scan"
                )

        direct_control = self.resolveDecision(
            'Use "codex-security:threat-model" for the security scan artifact'
        )
        self.assertEqual(direct_control["rule_id"], "codex-security-threat-model")

        longer_closing_fence = self.resolveDecision(
            "Example:\n```text\nrun a deep security scan\n````\n"
            "Now run a standard security scan of this repository"
        )
        self.assertEqual(
            longer_closing_fence["rule_id"], "standard-security-review"
        )

    def test_provider_negation_selects_only_the_affirmative_provider(self) -> None:
        cases = [
            (
                "Use PostgreSQL, not Supabase, to implement RLS policies",
                "coding-postgres-security-boundary",
                "skill:postgres-security-best-practices",
            ),
            (
                "Implement Postgres RLS policies, not Neon",
                "coding-postgres-security-boundary",
                "skill:postgres-security-best-practices",
            ),
        ]
        for prompt, rule_id, support in cases:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertEqual(decision["rule_id"], rule_id)
                self.assertIn(support, [item["id"] for item in decision["supports"]])

    def test_directional_provider_migration_binds_only_the_target(self) -> None:
        cases = [
            (
                "Migrate from Supabase to Neon Postgres",
                "coding-neon-provider-change",
                "skill:neon-postgres:neon-postgres",
            ),
            (
                "Migrate from Neon Postgres to Supabase",
                "coding-supabase-provider-change",
                "skill:supabase:supabase",
            ),
            (
                "Use Neon rather than Supabase to implement RLS policies",
                "coding-neon-security-boundary",
                "skill:neon-postgres:neon-postgres",
            ),
            (
                "Replace Supabase with Neon Postgres authentication policies",
                "coding-neon-security-boundary",
                "skill:neon-postgres:neon-postgres",
            ),
            (
                "Implement PostgreSQL RLS instead of Neon",
                "coding-postgres-security-boundary",
                "skill:postgres-security-best-practices",
            ),
        ]
        for prompt, rule_id, support in cases:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertEqual(decision["rule_id"], rule_id)
                self.assertIn(support, [item["id"] for item in decision["supports"]])

    def test_provider_full_authentication_and_authorization_words_route(self) -> None:
        cases = [
            (
                "Implement Supabase authentication policies",
                "coding-supabase-security-boundary",
            ),
            (
                "Implement Neon authorization controls",
                "coding-neon-security-boundary",
            ),
            (
                "Implement Supabase login and session handling",
                "coding-supabase-security-boundary",
            ),
        ]
        for prompt, rule_id in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(self.resolveDecision(prompt)["rule_id"], rule_id)

    def test_provider_postgres_engine_context_is_not_a_second_surface(self) -> None:
        cases = [
            (
                "Implement PostgreSQL RLS for our Supabase project",
                "coding-supabase-security-boundary",
            ),
            (
                "Implement RLS in our Supabase project using PostgreSQL",
                "coding-supabase-security-boundary",
            ),
            (
                "Implement PostgreSQL RLS for our Neon project",
                "coding-neon-security-boundary",
            ),
            (
                "Implement RLS in our Neon project using PostgreSQL",
                "coding-neon-security-boundary",
            ),
            (
                "Implement RLS for our Neon database, which uses PostgreSQL",
                "coding-neon-security-boundary",
            ),
            (
                "Implement RLS for our Supabase database, which uses PostgreSQL",
                "coding-supabase-security-boundary",
            ),
        ]
        for prompt, rule_id in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(self.resolveDecision(prompt)["rule_id"], rule_id)

        independent = self.resolveDecision(
            "Implement RLS in Supabase and a separate PostgreSQL database"
        )
        self.assertEqual(independent["rule_id"], "")
        self.assertIn(
            "SECURITY_SURFACES_REQUIRE_SPLIT_TASK", independent["reason_codes"]
        )

    def test_multiple_security_surfaces_fail_closed_with_split_requirement(self) -> None:
        prompts = [
            "Run a deep security scan of Supabase and Neon Postgres in this React frontend repository",
            "Implement Supabase RLS policies and secure HttpOnly cookies in this React frontend",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertEqual(decision["rule_id"], "")
                self.assertIsNone(decision["primary"])
                self.assertIn(
                    "SECURITY_SURFACES_REQUIRE_SPLIT_TASK",
                    decision["reason_codes"],
                )

    def test_multiple_security_phases_fail_closed_instead_of_dropping_work(self) -> None:
        cases = [
            (
                "Run a deep security scan then remediate the findings",
                {"DEEP_SCAN", "FIX"},
            ),
            (
                "Run a standard security scan and fix the findings",
                {"STANDARD_SCAN", "FIX"},
            ),
            ("Discover vulnerabilities then fix them", {"DISCOVERY", "FIX"}),
            (
                "First discover security findings in this module, then fix them",
                {"DISCOVERY", "FIX"},
            ),
            (
                "Scan this repository for vulnerabilities and patch them",
                {"STANDARD_SCAN", "FIX"},
            ),
            (
                "Triage this security finding then fix it",
                {"TRIAGE", "FIX"},
            ),
            (
                "Validate this security finding then fix it",
                {"VALIDATION", "FIX"},
            ),
            (
                "Fix and validate this vulnerability finding",
                {"FIX", "VALIDATION"},
            ),
            (
                "Write a vulnerability report then track this security finding in Linear",
                {"WRITEUP", "TRACKING"},
            ),
        ]
        for prompt, expected_phases in cases:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertEqual(decision["rule_id"], "")
                self.assertIsNone(decision["primary"])
                self.assertIn(
                    "SECURITY_PHASES_REQUIRE_SPLIT_TASK",
                    decision["reason_codes"],
                )
                phase_reason = next(
                    reason
                    for reason in decision["reason_codes"]
                    if reason.startswith("SECURITY_PHASES_")
                    and reason != "SECURITY_PHASES_REQUIRE_SPLIT_TASK"
                )
                for phase in expected_phases:
                    self.assertIn(phase, phase_reason)

    def test_neon_project_branch_is_provider_scope_not_git_diff_scope(self) -> None:
        prompts = [
            "Run a standard security scan of this Neon database branch",
            "Run a security scan of the Neon project branch production",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(
                    self.resolveDecision(prompt)["rule_id"],
                    "neon-standard-security-review",
                )
        self.assertNotEqual(
            self.resolveDecision("Review how to patch this vulnerability")["rule_id"],
            "security-diff-review",
        )

    def test_explicit_live_access_exclusion_overrides_callable_probe(self) -> None:
        cases = [
            (
                "Validate this Supabase security finding without using the live Supabase connector",
                "app:supabase",
                "supabase-security-finding-validation",
            ),
            (
                "Validate this Supabase security finding statically; do not access the live project",
                "app:supabase",
                "supabase-security-finding-validation",
            ),
            (
                "Validate this Supabase security finding; do not call the Supabase app",
                "app:supabase",
                "supabase-security-finding-validation",
            ),
            (
                "Validate this Neon security finding without live provider access",
                "app:neon",
                "neon-security-finding-validation",
            ),
            (
                "Validate this Neon security finding without using the live Neon connector",
                "app:neon",
                "neon-security-finding-validation",
            ),
            (
                "Validate this Neon security finding statically; do not access the live project",
                "app:neon",
                "neon-security-finding-validation",
            ),
            (
                "Validate this Neon security finding; do not call the Neon app",
                "app:neon",
                "neon-security-finding-validation",
            ),
            (
                "Run a deep security scan of this repository without using the Codex Security MCP",
                "mcp:codex-security",
                "deep-security-review",
            ),
        ]
        for prompt, dependency_id, rule_id in cases:
            with self.subTest(prompt=prompt):
                decision = self.resolveWithCallableDependency(
                    prompt, dependency_id
                )
                self.assertEqual(decision["rule_id"], rule_id)
                fallback = decision["capability_fallbacks"][0]
                self.assertEqual(fallback["equivalence"], "non_equivalent")
                self.assertIn(
                    f"prompt:{dependency_id}:explicitly_excluded",
                    fallback["unavailable_dependencies"],
                )

    def test_repository_vulnerability_scan_uses_standard_scan_not_discovery(self) -> None:
        decision = self.resolveDecision("Scan this repository for vulnerabilities")
        self.assertEqual(decision["rule_id"], "standard-security-review")
        self.assertNotEqual(decision["rule_id"], "security-finding-discovery")

    def test_fix_requires_current_mutation_intent(self) -> None:
        negative_prompts = [
            "Explain how to fix this security finding",
            "Fix this security finding later, only explain now",
        ]
        for prompt in negative_prompts:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertNotEqual(decision["rule_id"], "security-finding-fix")
                self.assertNotEqual(decision["rule_id"], "coding-security-boundary")
        self.assertEqual(
            self.resolveDecision(
                "Implement the fix for this validated security finding"
            )["rule_id"],
            "security-finding-fix",
        )

    def test_tracker_destination_selects_matching_support(self) -> None:
        cases = [
            (
                "Track these security findings in GitHub",
                "security-findings-tracking-github",
                "skill:github:github",
            ),
            (
                "Track these security findings in Linear",
                "security-findings-tracking-linear",
                "tool-family:app:linear",
            ),
            (
                "Sync these security findings to Jira",
                "security-findings-tracking-jira",
                "tool-family:app:atlassian",
            ),
        ]
        for prompt, rule_id, support in cases:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertEqual(decision["rule_id"], rule_id)
                self.assertEqual(
                    [item["id"] for item in decision["supports"]], [support]
                )

    def test_tracker_destination_negation_selects_only_the_affirmative_target(self) -> None:
        cases = [
            (
                "Track these security findings in Linear but not GitHub and not Jira",
                "security-findings-tracking-linear",
                "tool-family:app:linear",
            ),
            (
                "Track these security findings in GitHub but not Linear and not Atlassian",
                "security-findings-tracking-github",
                "skill:github:github",
            ),
            (
                "Track these security findings in Jira but not GitHub and not Linear",
                "security-findings-tracking-jira",
                "tool-family:app:atlassian",
            ),
        ]
        for prompt, rule_id, support in cases:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertEqual(decision["rule_id"], rule_id)
                self.assertEqual(
                    [item["id"] for item in decision["supports"]], [support]
                )

    def test_tracker_contrast_selects_only_the_preferred_destination(self) -> None:
        cases = [
            (
                "Track these security findings in GitHub rather than Linear",
                "security-findings-tracking-github",
                "skill:github:github",
            ),
            (
                "Track these security findings in Linear rather than GitHub",
                "security-findings-tracking-linear",
                "tool-family:app:linear",
            ),
            (
                "Track these security findings in Jira instead of GitHub",
                "security-findings-tracking-jira",
                "tool-family:app:atlassian",
            ),
            (
                "Track these security findings in GitHub instead of Jira",
                "security-findings-tracking-github",
                "skill:github:github",
            ),
        ]
        for prompt, rule_id, support in cases:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertEqual(decision["rule_id"], rule_id)
                self.assertEqual(
                    [item["id"] for item in decision["supports"]], [support]
                )

    def test_multiple_affirmative_tracker_destinations_require_split(self) -> None:
        prompts = [
            "Track these security findings in GitHub and Linear",
            "Track these security findings in Linear and Jira",
            "Track these security findings in GitHub and Atlassian",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                decision = self.resolveDecision(prompt)
                self.assertEqual(decision["rule_id"], "")
                self.assertIsNone(decision["primary"])
                self.assertIn(
                    "TRACKER_DESTINATIONS_REQUIRE_SPLIT_TASK",
                    decision["reason_codes"],
                )

    def test_negative_examples_do_not_activate_security_or_database_provider_routes(self) -> None:
        prompts = [
            "Change the neon gradient on this button",
            "Implement the Supabase logo in the footer",
            'Quoted example: "run a standard security scan of the repository". Summarize it.',
            "The subject is security scan",
            "Do not run a security scan of this repository",
            "Do not fix this security finding; only summarize it",
            "Draft a corporate policy document",
            "Explain this database view",
            "Change the button color",
        ]
        forbidden_prefixes = (
            "security-",
            "supabase-",
            "neon-",
            "postgres-",
            "coding-supabase-",
            "coding-neon-",
            "coding-postgres-",
            "frontend-security-",
            "deep-security-",
            "standard-security-",
            "codex-security-",
            "local-security-",
            "defensive-security-",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                rule = first_matching_rule(prompt)
                if rule is not None:
                    self.assertFalse(rule["id"].startswith(forbidden_prefixes), rule)


if __name__ == "__main__":
    unittest.main()
