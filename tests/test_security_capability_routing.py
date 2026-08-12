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
                "Validate this Neon Postgres RLS security findinçNú¶‰žËkºwµçE0I1L¥¹ÍÑ•…½˜9•½¸ˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµÁ½ÍÑÉ•ÌµÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€€€€€‰Í­¥±°éÁ½ÍÑÉ•ÌµÍ•ÕÉ¥Ñäµ‰•ÍÐµÁÉ…Ñ¥•Ìˆ°(€€€€€€€€€€€€¤°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ°ÉÕ±•}¥°ÍÕÁÁ½ÉÐ¥¸…Í•Ìè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°ÉÕ±•}¥¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸¡ÍÕÁÁ½ÉÐ°m¥Ñ•µl‰¥‰t™½È¥Ñ•´¥¸‘•¥Í¥½¹l‰ÍÕÁÁ½ÉÑÌ‰ut¤((€€€‘•˜Ñ•ÍÑ}ÁÉ½Ù¥‘•É}™Õ±±}…ÕÑ¡•¹Ñ¥…Ñ¥½¹}…¹‘}…ÕÑ¡½É¥é…Ñ¥½¹}Ý½É‘Í}É½ÕÑ”¡Í•±˜¤€´ø9½¹”è(€€€€€€€…Í•Ì€ôl(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹ÐMÕÁ…‰…Í”…ÕÑ¡•¹Ñ¥…Ñ¥½¸Á½±¥¥•Ìˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµÍÕÁ…‰…Í”µÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹Ð9•½¸…ÕÑ¡½É¥é…Ñ¥½¸½¹ÑÉ½±Ìˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµ¹•½¸µÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹ÐMÕÁ…‰…Í”±½¥¸…¹Í•ÍÍ¥½¸¡…¹‘±¥¹œˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµÍÕÁ…‰…Í”µÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€¤°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ°ÉÕ±•}¥¥¸…Í•Ìè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡Í•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¥l‰ÉÕ±•}¥‰t°ÉÕ±•}¥¤((€€€‘•˜Ñ•ÍÑ}ÁÉ½Ù¥‘•É}Á½ÍÑÉ•Í}•¹¥¹•}½¹Ñ•áÑ}¥Í}¹½Ñ}…}Í•½¹‘}ÍÕÉ™…”¡Í•±˜¤€´ø9½¹”è(€€€€€€€…Í•Ì€ôl(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹ÐA½ÍÑÉ•ME0I1L™½È½ÕÈMÕÁ…‰…Í”ÁÉ½©•Ðˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµÍÕÁ…‰…Í”µÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹ÐI1L¥¸½ÕÈMÕÁ…‰…Í”ÁÉ½©•ÐÕÍ¥¹œA½ÍÑÉ•ME0ˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµÍÕÁ…‰…Í”µÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹ÐA½ÍÑÉ•ME0I1L™½È½ÕÈ9•½¸ÁÉ½©•Ðˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµ¹•½¸µÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹ÐI1L¥¸½ÕÈ9•½¸ÁÉ½©•ÐÕÍ¥¹œA½ÍÑÉ•ME0ˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµ¹•½¸µÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹ÐI1L™½È½ÕÈ9•½¸‘…Ñ…‰…Í”°Ý¡¥ ÕÍ•ÌA½ÍÑÉ•ME0ˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµ¹•½¸µÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹ÐI1L™½È½ÕÈMÕÁ…‰…Í”‘…Ñ…‰…Í”°Ý¡¥ ÕÍ•ÌA½ÍÑÉ•ME0ˆ°(€€€€€€€€€€€€€€€€‰½‘¥¹œµÍÕÁ…‰…Í”µÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ°(€€€€€€€€€€€€¤°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ°ÉÕ±•}¥¥¸…Í•Ìè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡Í•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¥l‰ÉÕ±•}¥‰t°ÉÕ±•}¥¤((€€€€€€€¥¹‘•Á•¹‘•¹Ð€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸ (€€€€€€€€€€€€‰%µÁ±•µ•¹ÐI1L¥¸MÕÁ…‰…Í”…¹„Í•Á…É…Ñ”A½ÍÑÉ•ME0‘…Ñ…‰…Í”ˆ(€€€€€€€€¤(€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡¥¹‘•Á•¹‘•¹Ñl‰ÉÕ±•}¥‰t°€ˆˆ¤(€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸ (€€€€€€€€€€€€‰MUI%Qe}MUIM}IEU%I}MA1%Q}QM,ˆ°¥¹‘•Á•¹‘•¹Ñl‰É•…Í½¹}½‘•Ì‰t(€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}µÕ±Ñ¥Á±•}Í•ÕÉ¥Ñå}ÍÕÉ™…•Í}™…¥±}±½Í•‘}Ý¥Ñ¡}ÍÁ±¥Ñ}É•ÅÕ¥É•µ•¹Ð¡Í•±˜¤€´ø9½¹”è(€€€€€€€ÁÉ½µÁÑÌ€ôl(€€€€€€€€€€€€‰IÕ¸„‘••ÀÍ•ÕÉ¥ÑäÍ…¸½˜MÕÁ…‰…Í”…¹9•½¸A½ÍÑÉ•Ì¥¸Ñ¡¥ÌI•…Ð™É½¹Ñ•¹É•Á½Í¥Ñ½Éäˆ°(€€€€€€€€€€€€‰%µÁ±•µ•¹ÐMÕÁ…‰…Í”I1LÁ½±¥¥•Ì…¹Í•ÕÉ”!ÑÑÁ=¹±ä½½­¥•Ì¥¸Ñ¡¥ÌI•…Ð™É½¹Ñ•¹ˆ°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ¥¸ÁÉ½µÁÑÌè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°€ˆˆ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%Í9½¹”¡‘•¥Í¥½¹l‰ÁÉ¥µ…Éä‰t¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸ (€€€€€€€€€€€€€€€€€€€€‰MUI%Qe}MUIM}IEU%I}MA1%Q}QM,ˆ°(€€€€€€€€€€€€€€€€€€€‘•¥Í¥½¹l‰É•…Í½¹}½‘•Ì‰t°(€€€€€€€€€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}µÕ±Ñ¥Á±•}Í•ÕÉ¥Ñå}Á¡…Í•Í}™…¥±}±½Í•‘}¥¹ÍÑ•…‘}½™}‘É½ÁÁ¥¹}Ý½É¬¡Í•±˜¤€´ø9½¹”è(€€€€€€€…Í•Ì€ôl(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰IÕ¸„‘••ÀÍ•ÕÉ¥ÑäÍ…¸Ñ¡•¸É•µ•‘¥…Ñ”Ñ¡”™¥¹‘¥¹Ìˆ°(€€€€€€€€€€€€€€€ì‰A}M8ˆ°€‰%`‰ô°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰IÕ¸„ÍÑ…¹‘…ÉÍ•ÕÉ¥ÑäÍ…¸…¹™¥àÑ¡”™¥¹‘¥¹Ìˆ°(€€€€€€€€€€€€€€€ì‰MQ9I}M8ˆ°€‰%`‰ô°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ ‰¥Í½Ù•ÈÙÕ±¹•É…‰¥±¥Ñ¥•ÌÑ¡•¸™¥àÑ¡•´ˆ°ì‰%M=YIdˆ°€‰%`‰ô¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰¥ÉÍÐ‘¥Í½Ù•ÈÍ•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸Ñ¡¥Ìµ½‘Õ±”°Ñ¡•¸™¥àÑ¡•´ˆ°(€€€€€€€€€€€€€€€ì‰%M=YIdˆ°€‰%`‰ô°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰M…¸Ñ¡¥ÌÉ•Á½Í¥Ñ½Éä™½ÈÙÕ±¹•É…‰¥±¥Ñ¥•Ì…¹Á…Ñ Ñ¡•´ˆ°(€€€€€€€€€€€€€€€ì‰MQ9I}M8ˆ°€‰%`‰ô°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ¥…”Ñ¡¥ÌÍ•ÕÉ¥Ñä™¥¹‘¥¹œÑ¡•¸™¥à¥Ðˆ°(€€€€€€€€€€€€€€€ì‰QI%ˆ°€‰%`‰ô°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰Y…±¥‘…Ñ”Ñ¡¥ÌÍ•ÕÉ¥Ñä™¥¹‘¥¹œÑ¡•¸™¥à¥Ðˆ°(€€€€€€€€€€€€€€€ì‰Y1%Q%=8ˆ°€‰%`‰ô°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰¥à…¹Ù…±¥‘…Ñ”Ñ¡¥ÌÙÕ±¹•É…‰¥±¥Ñä™¥¹‘¥¹œˆ°(€€€€€€€€€€€€€€€ì‰%`ˆ°€‰Y1%Q%=8‰ô°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰]É¥Ñ”„ÙÕ±¹•É…‰¥±¥ÑäÉ•Á½ÉÐÑ¡•¸ÑÉ…¬Ñ¡¥ÌÍ•ÕÉ¥Ñä™¥¹‘¥¹œ¥¸1¥¹•…Èˆ°(€€€€€€€€€€€€€€€ì‰]I%QU@ˆ°€‰QI-%9‰ô°(€€€€€€€€€€€€¤°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ°•áÁ•Ñ•‘}Á¡…Í•Ì¥¸…Í•Ìè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°€ˆˆ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%Í9½¹”¡‘•¥Í¥½¹l‰ÁÉ¥µ…Éä‰t¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸ (€€€€€€€€€€€€€€€€€€€€‰MUI%Qe}A!MM}IEU%I}MA1%Q}QM,ˆ°(€€€€€€€€€€€€€€€€€€€‘•¥Í¥½¹l‰É•…Í½¹}½‘•Ì‰t°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€Á¡…Í•}É•…Í½¸€ô¹•áÐ (€€€€€€€€€€€€€€€€€€€É•…Í½¸(€€€€€€€€€€€€€€€€€€€™½ÈÉ•…Í½¸¥¸‘•¥Í¥½¹l‰É•…Í½¹}½‘•Ì‰t(€€€€€€€€€€€€€€€€€€€¥˜É•…Í½¸¹ÍÑ…ÉÑÍÝ¥Ñ  ‰MUI%Qe}A!MM|ˆ¤(€€€€€€€€€€€€€€€€€€€…¹É•…Í½¸€„ô€‰MUI%Qe}A!MM}IEU%I}MA1%Q}QM,ˆ(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€™½ÈÁ¡…Í”¥¸•áÁ•Ñ•‘}Á¡…Í•Ìè(€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸¡Á¡…Í”°Á¡…Í•}É•…Í½¸¤((€€€‘•˜Ñ•ÍÑ}¹•½¹}ÁÉ½©•Ñ}‰É…¹¡}¥Í}ÁÉ½Ù¥‘•É}Í½Á•}¹½Ñ}¥Ñ}‘¥™™}Í½Á”¡Í•±˜¤€´ø9½¹”è(€€€€€€€ÁÉ½µÁÑÌ€ôl(€€€€€€€€€€€€‰IÕ¸„ÍÑ…¹‘…ÉÍ•ÕÉ¥ÑäÍ…¸½˜Ñ¡¥Ì9•½¸‘…Ñ…‰…Í”‰É…¹ ˆ°(€€€€€€€€€€€€‰IÕ¸„Í•ÕÉ¥ÑäÍ…¸½˜Ñ¡”9•½¸ÁÉ½©•Ð‰É…¹ ÁÉ½‘ÕÑ¥½¸ˆ°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ¥¸ÁÉ½µÁÑÌè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…° (€€€€€€€€€€€€€€€€€€€Í•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¥l‰ÉÕ±•}¥‰t°(€€€€€€€€€€€€€€€€€€€€‰¹•½¸µÍÑ…¹‘…ÉµÍ•ÕÉ¥ÑäµÉ•Ù¥•Üˆ°(€€€€€€€€€€€€€€€€¤(€€€€€€€Í•±˜¹…ÍÍ•ÉÑ9½ÑÅÕ…° (€€€€€€€€€€€Í•±˜¹É•Í½±Ù••¥Í¥½¸ ‰I•Ù¥•Ü¡½ÜÑ¼Á…Ñ Ñ¡¥ÌÙÕ±¹•É…‰¥±¥Ñäˆ¥l‰ÉÕ±•}¥‰t°(€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ‘¥™˜µÉ•Ù¥•Üˆ°(€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}•áÁ±¥¥Ñ}±¥Ù•}…•ÍÍ}•á±ÕÍ¥½¹}½Ù•ÉÉ¥‘•Í}…±±…‰±•}ÁÉ½‰”¡Í•±˜¤€´ø9½¹”è(€€€€€€€…Í•Ì€ôl(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰Y…±¥‘…Ñ”Ñ¡¥ÌMÕÁ…‰…Í”Í•ÕÉ¥Ñä™¥¹‘¥¹œÝ¥Ñ¡½ÕÐÕÍ¥¹œÑ¡”±¥Ù”MÕÁ…‰…Í”½¹¹•Ñ½Èˆ°(€€€€€€€€€€€€€€€€‰…ÁÀéÍÕÁ…‰…Í”ˆ°(€€€€€€€€€€€€€€€€‰ÍÕÁ…‰…Í”µÍ•ÕÉ¥Ñäµ™¥¹‘¥¹œµÙ…±¥‘…Ñ¥½¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰Y…±¥‘…Ñ”Ñ¡¥ÌMÕÁ…‰…Í”Í•ÕÉ¥Ñä™¥¹‘¥¹œÍÑ…Ñ¥…±±äì‘¼¹½Ð…•ÍÌÑ¡”±¥Ù”ÁÉ½©•Ðˆ°(€€€€€€€€€€€€€€€€‰…ÁÀéÍÕÁ…‰…Í”ˆ°(€€€€€€€€€€€€€€€€‰ÍÕÁ…‰…Í”µÍ•ÕÉ¥Ñäµ™¥¹‘¥¹œµÙ…±¥‘…Ñ¥½¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰Y…±¥‘…Ñ”Ñ¡¥ÌMÕÁ…‰…Í”Í•ÕÉ¥Ñä™¥¹‘¥¹œì‘¼¹½Ð…±°Ñ¡”MÕÁ…‰…Í”…ÁÀˆ°(€€€€€€€€€€€€€€€€‰…ÁÀéÍÕÁ…‰…Í”ˆ°(€€€€€€€€€€€€€€€€‰ÍÕÁ…‰…Í”µÍ•ÕÉ¥Ñäµ™¥¹‘¥¹œµÙ…±¥‘…Ñ¥½¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰Y…±¥‘…Ñ”Ñ¡¥Ì9•½¸Í•ÕÉ¥Ñä™¥¹‘¥¹œÝ¥Ñ¡½ÕÐ±¥Ù”ÁÉ½Ù¥‘•È…•ÍÌˆ°(€€€€€€€€€€€€€€€€‰…ÁÀé¹•½¸ˆ°(€€€€€€€€€€€€€€€€‰¹•½¸µÍ•ÕÉ¥Ñäµ™¥¹‘¥¹œµÙ…±¥‘…Ñ¥½¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰Y…±¥‘…Ñ”Ñ¡¥Ì9•½¸Í•ÕÉ¥Ñä™¥¹‘¥¹œÝ¥Ñ¡½ÕÐÕÍ¥¹œÑ¡”±¥Ù”9•½¸½¹¹•Ñ½Èˆ°(€€€€€€€€€€€€€€€€‰…ÁÀé¹•½¸ˆ°(€€€€€€€€€€€€€€€€‰¹•½¸µÍ•ÕÉ¥Ñäµ™¥¹‘¥¹œµÙ…±¥‘…Ñ¥½¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰Y…±¥‘…Ñ”Ñ¡¥Ì9•½¸Í•ÕÉ¥Ñä™¥¹‘¥¹œÍÑ…Ñ¥…±±äì‘¼¹½Ð…•ÍÌÑ¡”±¥Ù”ÁÉ½©•Ðˆ°(€€€€€€€€€€€€€€€€‰…ÁÀé¹•½¸ˆ°(€€€€€€€€€€€€€€€€‰¹•½¸µÍ•ÕÉ¥Ñäµ™¥¹‘¥¹œµÙ…±¥‘…Ñ¥½¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰Y…±¥‘…Ñ”Ñ¡¥Ì9•½¸Í•ÕÉ¥Ñä™¥¹‘¥¹œì‘¼¹½Ð…±°Ñ¡”9•½¸…ÁÀˆ°(€€€€€€€€€€€€€€€€‰…ÁÀé¹•½¸ˆ°(€€€€€€€€€€€€€€€€‰¹•½¸µÍ•ÕÉ¥Ñäµ™¥¹‘¥¹œµÙ…±¥‘…Ñ¥½¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰IÕ¸„‘••ÀÍ•ÕÉ¥ÑäÍ…¸½˜Ñ¡¥ÌÉ•Á½Í¥Ñ½ÉäÝ¥Ñ¡½ÕÐÕÍ¥¹œÑ¡”½‘•àM•ÕÉ¥Ñä5@ˆ°(€€€€€€€€€€€€€€€€‰µÀé½‘•àµÍ•ÕÉ¥Ñäˆ°(€€€€€€€€€€€€€€€€‰‘••ÀµÍ•ÕÉ¥ÑäµÉ•Ù¥•Üˆ°(€€€€€€€€€€€€¤°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ°‘•Á•¹‘•¹å}¥°ÉÕ±•}¥¥¸…Í•Ìè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù•]¥Ñ¡…±±…‰±••Á•¹‘•¹ä (€€€€€€€€€€€€€€€€€€€ÁÉ½µÁÐ°‘•Á•¹‘•¹å}¥(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°ÉÕ±•}¥¤(€€€€€€€€€€€€€€€™…±±‰…¬€ô‘•¥Í¥½¹l‰…Á…‰¥±¥Ñå}™…±±‰…­Ì‰ulÁt(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡™…±±‰…­l‰•ÅÕ¥Ù…±•¹”‰t°€‰¹½¹}•ÅÕ¥Ù…±•¹Ðˆ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸ (€€€€€€€€€€€€€€€€€€€˜‰ÁÉ½µÁÐéí‘•Á•¹‘•¹å}¥‘ôé•áÁ±¥¥Ñ±å}•á±Õ‘•ˆ°(€€€€€€€€€€€€€€€€€€€™…±±‰…­l‰Õ¹…Ù…¥±…‰±•}‘•Á•¹‘•¹¥•Ì‰t°(€€€€€€€€€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}É•Á½Í¥Ñ½Éå}ÙÕ±¹•É…‰¥±¥Ñå}Í…¹}ÕÍ•Í}ÍÑ…¹‘…É‘}Í…¹}¹½Ñ}‘¥Í½Ù•Éä¡Í•±˜¤€´ø9½¹”è(€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸ ‰M…¸Ñ¡¥ÌÉ•Á½Í¥Ñ½Éä™½ÈÙÕ±¹•É…‰¥±¥Ñ¥•Ìˆ¤(€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°€‰ÍÑ…¹‘…ÉµÍ•ÕÉ¥ÑäµÉ•Ù¥•Üˆ¤(€€€€€€€Í•±˜¹…ÍÍ•ÉÑ9½ÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹œµ‘¥Í½Ù•Éäˆ¤((€€€‘•˜Ñ•ÍÑ}™¥á}É•ÅÕ¥É•Í}ÕÉÉ•¹Ñ}µÕÑ…Ñ¥½¹}¥¹Ñ•¹Ð¡Í•±˜¤€´ø9½¹”è(€€€€€€€¹•…Ñ¥Ù•}ÁÉ½µÁÑÌ€ôl(€€€€€€€€€€€€‰áÁ±…¥¸¡½ÜÑ¼™¥àÑ¡¥ÌÍ•ÕÉ¥Ñä™¥¹‘¥¹œˆ°(€€€€€€€€€€€€‰¥àÑ¡¥ÌÍ•ÕÉ¥Ñä™¥¹‘¥¹œ±…Ñ•È°½¹±ä•áÁ±…¥¸¹½Üˆ°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ¥¸¹•…Ñ¥Ù•}ÁÉ½µÁÑÌè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ9½ÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹œµ™¥àˆ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ9½ÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°€‰½‘¥¹œµÍ•ÕÉ¥Ñäµ‰½Õ¹‘…Éäˆ¤(€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…° (€€€€€€€€€€€Í•±˜¹É•Í½±Ù••¥Í¥½¸ (€€€€€€€€€€€€€€€€‰%µÁ±•µ•¹ÐÑ¡”™¥à™½ÈÑ¡¥ÌÙ…±¥‘…Ñ•Í•ÕÉ¥Ñä™¥¹‘¥¹œˆ(€€€€€€€€€€€€¥l‰ÉÕ±•}¥‰t°(€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹œµ™¥àˆ°(€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}ÑÉ…­•É}‘•ÍÑ¥¹…Ñ¥½¹}Í•±•ÑÍ}µ…Ñ¡¥¹}ÍÕÁÁ½ÉÐ¡Í•±˜¤€´ø9½¹”è(€€€€€€€…Í•Ì€ôl(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸¥Ñ!Õˆˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ¥Ñ¡Õˆˆ°(€€€€€€€€€€€€€€€€‰Í­¥±°é¥Ñ¡Õˆé¥Ñ¡Õˆˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸1¥¹•…Èˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ±¥¹•…Èˆ°(€€€€€€€€€€€€€€€€‰Ñ½½°µ™…µ¥±äé…ÁÀé±¥¹•…Èˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰Må¹ŒÑ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹ÌÑ¼)¥É„ˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ©¥É„ˆ°(€€€€€€€€€€€€€€€€‰Ñ½½°µ™…µ¥±äé…ÁÀé…Ñ±…ÍÍ¥…¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ°ÉÕ±•}¥°ÍÕÁÁ½ÉÐ¥¸…Í•Ìè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°ÉÕ±•}¥¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…° (€€€€€€€€€€€€€€€€€€€m¥Ñ•µl‰¥‰t™½È¥Ñ•´¥¸‘•¥Í¥½¹l‰ÍÕÁÁ½ÉÑÌ‰ut°mÍÕÁÁ½ÉÑt(€€€€€€€€€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}ÑÉ…­•É}‘•ÍÑ¥¹…Ñ¥½¹}¹•…Ñ¥½¹}Í•±•ÑÍ}½¹±å}Ñ¡•}…™™¥Éµ…Ñ¥Ù•}Ñ…É•Ð¡Í•±˜¤€´ø9½¹”è(€€€€€€€…Í•Ì€ôl(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸1¥¹•…È‰ÕÐ¹½Ð¥Ñ!Õˆ…¹¹½Ð)¥É„ˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ±¥¹•…Èˆ°(€€€€€€€€€€€€€€€€‰Ñ½½°µ™…µ¥±äé…ÁÀé±¥¹•…Èˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸¥Ñ!Õˆ‰ÕÐ¹½Ð1¥¹•…È…¹¹½ÐÑ±…ÍÍ¥…¸ˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ¥Ñ¡Õˆˆ°(€€€€€€€€€€€€€€€€‰Í­¥±°é¥Ñ¡Õˆé¥Ñ¡Õˆˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸)¥É„‰ÕÐ¹½Ð¥Ñ!Õˆ…¹¹½Ð1¥¹•…Èˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ©¥É„ˆ°(€€€€€€€€€€€€€€€€‰Ñ½½°µ™…µ¥±äé…ÁÀé…Ñ±…ÍÍ¥…¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ°ÉÕ±•}¥°ÍÕÁÁ½ÉÐ¥¸…Í•Ìè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°ÉÕ±•}¥¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…° (€€€€€€€€€€€€€€€€€€€m¥Ñ•µl‰¥‰t™½È¥Ñ•´¥¸‘•¥Í¥½¹l‰ÍÕÁÁ½ÉÑÌ‰ut°mÍÕÁÁ½ÉÑt(€€€€€€€€€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}ÑÉ…­•É}½¹ÑÉ…ÍÑ}Í•±•ÑÍ}½¹±å}Ñ¡•}ÁÉ•™•ÉÉ•‘}‘•ÍÑ¥¹…Ñ¥½¸¡Í•±˜¤€´ø9½¹”è(€€€€€€€…Í•Ì€ôl(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸¥Ñ!ÕˆÉ…Ñ¡•ÈÑ¡…¸1¥¹•…Èˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ¥Ñ¡Õˆˆ°(€€€€€€€€€€€€€€€€‰Í­¥±°é¥Ñ¡Õˆé¥Ñ¡Õˆˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸1¥¹•…ÈÉ…Ñ¡•ÈÑ¡…¸¥Ñ!Õˆˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ±¥¹•…Èˆ°(€€€€€€€€€€€€€€€€‰Ñ½½°µ™…µ¥±äé…ÁÀé±¥¹•…Èˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸)¥É„¥¹ÍÑ•…½˜¥Ñ!Õˆˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ©¥É„ˆ°(€€€€€€€€€€€€€€€€‰Ñ½½°µ™…µ¥±äé…ÁÀé…Ñ±…ÍÍ¥…¸ˆ°(€€€€€€€€€€€€¤°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸¥Ñ!Õˆ¥¹ÍÑ•…½˜)¥É„ˆ°(€€€€€€€€€€€€€€€€‰Í•ÕÉ¥Ñäµ™¥¹‘¥¹ÌµÑÉ…­¥¹œµ¥Ñ¡Õˆˆ°(€€€€€€€€€€€€€€€€‰Í­¥±°é¥Ñ¡Õˆé¥Ñ¡Õˆˆ°(€€€€€€€€€€€€¤°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ°ÉÕ±•}¥°ÍÕÁÁ½ÉÐ¥¸…Í•Ìè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°ÉÕ±•}¥¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…° (€€€€€€€€€€€€€€€€€€€m¥Ñ•µl‰¥‰t™½È¥Ñ•´¥¸‘•¥Í¥½¹l‰ÍÕÁÁ½ÉÑÌ‰ut°mÍÕÁÁ½ÉÑt(€€€€€€€€€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}µÕ±Ñ¥Á±•}…™™¥Éµ…Ñ¥Ù•}ÑÉ…­•É}‘•ÍÑ¥¹…Ñ¥½¹Í}É•ÅÕ¥É•}ÍÁ±¥Ð¡Í•±˜¤€´ø9½¹”è(€€€€€€€ÁÉ½µÁÑÌ€ôl(€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸¥Ñ!Õˆ…¹1¥¹•…Èˆ°(€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸1¥¹•…È…¹)¥É„ˆ°(€€€€€€€€€€€€‰QÉ…¬Ñ¡•Í”Í•ÕÉ¥Ñä™¥¹‘¥¹Ì¥¸¥Ñ!Õˆ…¹Ñ±…ÍÍ¥…¸ˆ°(€€€€€€€t(€€€€€€€™½ÈÁÉ½µÁÐ¥¸ÁÉ½µÁÑÌè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€‘•¥Í¥½¸€ôÍ•±˜¹É•Í½±Ù••¥Í¥½¸¡ÁÉ½µÁÐ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑÅÕ…°¡‘•¥Í¥½¹l‰ÉÕ±•}¥‰t°€ˆˆ¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%Í9½¹”¡‘•¥Í¥½¹l‰ÁÉ¥µ…Éä‰t¤(€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ%¸ (€€€€€€€€€€€€€€€€€€€€‰QI-I}MQ%9Q%=9M}IEU%I}MA1%Q}QM,ˆ°(€€€€€€€€€€€€€€€€€€€‘•¥Í¥½¹l‰É•…Í½¹}½‘•Ì‰t°(€€€€€€€€€€€€€€€€¤((€€€‘•˜Ñ•ÍÑ}¹•…Ñ¥Ù•}•á…µÁ±•Í}‘½}¹½Ñ}…Ñ¥Ù…Ñ•}Í•ÕÉ¥Ñå}½É}‘…Ñ…‰…Í•}ÁÉ½Ù¥‘•É}É½ÕÑ•Ì¡Í•±˜¤€´ø9½¹”è(€€€€€€€ÁÉ½µÁÑÌ€ôl(€€€€€€€€€€€€‰¡…¹”Ñ¡”¹•½¸É…‘¥•¹Ð½¸Ñ¡¥Ì‰ÕÑÑ½¸ˆ°(€€€€€€€€€€€€‰%µÁ±•µ•¹ÐÑ¡”MÕÁ…‰…Í”±½¼¥¸Ñ¡”™½½Ñ•Èˆ°(€€€€€€€€€€€€EÕ½Ñ••á…µÁ±”è€‰ÉÕ¸„ÍÑ…¹‘…ÉÍ•ÕÉ¥ÑäÍ…¸½˜Ñ¡”É•Á½Í¥Ñ½Éäˆ¸MÕµµ…É¥é”¥Ð¸œ°(€€€€€€€€€€€€‰Q¡”ÍÕ‰©•Ð¥ÌÍ•ÕÉ¥ÑäÍ…¸ˆ°(€€€€€€€€€€€€‰¼¹½ÐÉÕ¸„Í•ÕÉ¥ÑäÍ…¸½˜Ñ¡¥ÌÉ•Á½Í¥Ñ½Éäˆ°(€€€€€€€€€€€€‰¼¹½Ð™¥àÑ¡¥ÌÍ•ÕÉ¥Ñä™¥¹‘¥¹œì½¹±äÍÕµµ…É¥é”¥Ðˆ°(€€€€€€€€€€€€‰É…™Ð„½ÉÁ½É…Ñ”Á½±¥ä‘½Õµ•¹Ðˆ°(€€€€€€€€€€€€‰áÁ±…¥¸Ñ¡¥Ì‘…Ñ…‰…Í”Ù¥•Üˆ°(€€€€€€€€€€€€‰¡…¹”Ñ¡”‰ÕÑÑ½¸½±½Èˆ°(€€€€€€€t(€€€€€€€™½É‰¥‘‘•¹}ÁÉ•™¥á•Ì€ô€ (€€€€€€€€€€€€‰Í•ÕÉ¥Ñä´ˆ°(€€€€€€€€€€€€‰ÍÕÁ…‰…Í”´ˆ°(€€€€€€€€€€€€‰¹•½¸´ˆ°(€€€€€€€€€€€€‰Á½ÍÑÉ•Ì´ˆ°(€€€€€€€€€€€€‰½‘¥¹œµÍÕÁ…‰…Í”´ˆ°(€€€€€€€€€€€€‰½‘¥¹œµ¹•½¸´ˆ°(€€€€€€€€€€€€‰½‘¥¹œµÁ½ÍÑÉ•Ì´ˆ°(€€€€€€€€€€€€‰™É½¹Ñ•¹µÍ•ÕÉ¥Ñä´ˆ°(€€€€€€€€€€€€‰‘••ÀµÍ•ÕÉ¥Ñä´ˆ°(€€€€€€€€€€€€‰ÍÑ…¹‘…ÉµÍ•ÕÉ¥Ñä´ˆ°(€€€€€€€€€€€€‰½‘•àµÍ•ÕÉ¥Ñä´ˆ°(€€€€€€€€€€€€‰±½…°µÍ•ÕÉ¥Ñä´ˆ°(€€€€€€€€€€€€‰‘•™•¹Í¥Ù”µÍ•ÕÉ¥Ñä´ˆ°(€€€€€€€€¤(€€€€€€€™½ÈÁÉ½µÁÐ¥¸ÁÉ½µÁÑÌè(€€€€€€€€€€€Ý¥Ñ Í•±˜¹ÍÕ‰Q•ÍÐ¡ÁÉ½µÁÐõÁÉ½µÁÐ¤è(€€€€€€€€€€€€€€€ÉÕ±”€ô™¥ÉÍÑ}µ…Ñ¡¥¹}ÉÕ±”¡ÁÉ½µÁÐ¤(€€€€€€€€€€€€€€€¥˜ÉÕ±”¥Ì¹½Ð9½¹”è(€€€€€€€€€€€€€€€€€€€Í•±˜¹…ÍÍ•ÉÑ…±Í”¡ÉÕ±•l‰¥‰t¹ÍÑ…ÉÑÍÝ¥Ñ ¡™½É‰¥‘‘•¹}ÁÉ•™¥á•Ì¤°ÉÕ±”¤(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€Õ¹¥ÑÑ•ÍÐ¹µ…¥¸ ¤(