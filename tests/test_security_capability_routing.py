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
                "Validate this Neon Postgres RLS security findinçNù¶‰žËkºwµçTÔS“È[œÝXYÙˆ™[Ûˆ‹ˆ˜ÛÙ[™Ë\ÜÝÜ™\Ë\ÙXÝ\š]KX›Ý[™\žH‹ˆœÚÚ[œÜÝÜ™\Ë\ÙXÝ\š]KX™\Ý\˜XÝXÙ\È‹ˆ
KˆBˆ›Üˆ›Û\[WÚYÝ\Ü[ˆØ\Ù\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
BˆÙ[‹˜\ÜÙ\\]X[
XÚ\Ú[Û–Èœ[WÚY—K[WÚY
BˆÙ[‹˜\ÜÙ\[ŠÝ\ÜÚ][VÈšY—H›Üˆ][H[ˆXÚ\Ú[Û–ÈœÝ\ÜÈ—WJB‚ˆYˆ\ÝÜ›ÝšY\—Ù[Ø]][XØ][Û—Ø[™Ø]]Üš^˜][Û—ÝÛÜ™×Ü›Ý]JÙ[ŠHOˆ›Û™N‚ˆØ\Ù\ÈHÂˆ
ˆ’[\[Y[Ý\X˜\ÙH]][XØ][ÛˆÛXÚY\È‹ˆ˜ÛÙ[™Ë\Ý\X˜\ÙK\ÙXÝ\š]KX›Ý[™\žH‹ˆ
Kˆ
ˆ’[\[Y[™[Ûˆ]]Üš^˜][ÛˆÛÛ›ÛÈ‹ˆ˜ÛÙ[™Ë[™[Û‹\ÙXÝ\š]KX›Ý[™\žH‹ˆ
Kˆ
ˆ’[\[Y[Ý\X˜\ÙHÙÚ[ˆ[™Ù\ÜÚ[Ûˆ[™[™È‹ˆ˜ÛÙ[™Ë\Ý\X˜\ÙK\ÙXÝ\š]KX›Ý[™\žH‹ˆ
KˆBˆ›Üˆ›Û\[WÚY[ˆØ\Ù\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆÙ[‹˜\ÜÙ\\]X[
Ù[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
VÈœ[WÚY—K[WÚY
B‚ˆYˆ\ÝÜ›ÝšY\—ÜÜÝÜ™\×Ù[™Ú[™WØÛÛ^Ú\×Û›ÝØWÜÙXÛÛ™ÜÝ\™˜XÙJÙ[ŠHOˆ›Û™N‚ˆØ\Ù\ÈHÂˆ
ˆ’[\[Y[ÜÝÜ™TÔS“È›ÜˆÝ\ˆÝ\X˜\ÙH›Ú™XÝ‹ˆ˜ÛÙ[™Ë\Ý\X˜\ÙK\ÙXÝ\š]KX›Ý[™\žH‹ˆ
Kˆ
ˆ’[\[Y[“È[ˆÝ\ˆÝ\X˜\ÙH›Ú™XÝ\Ú[™ÈÜÝÜ™TÔS‹ˆ˜ÛÙ[™Ë\Ý\X˜\ÙK\ÙXÝ\š]KX›Ý[™\žH‹ˆ
Kˆ
ˆ’[\[Y[ÜÝÜ™TÔS“È›ÜˆÝ\ˆ™[Ûˆ›Ú™XÝ‹ˆ˜ÛÙ[™Ë[™[Û‹\ÙXÝ\š]KX›Ý[™\žH‹ˆ
Kˆ
ˆ’[\[Y[“È[ˆÝ\ˆ™[Ûˆ›Ú™XÝ\Ú[™ÈÜÝÜ™TÔS‹ˆ˜ÛÙ[™Ë[™[Û‹\ÙXÝ\š]KX›Ý[™\žH‹ˆ
Kˆ
ˆ’[\[Y[“È›ÜˆÝ\ˆ™[Ûˆ]X˜\ÙKÚXÚ\Ù\ÈÜÝÜ™TÔS‹ˆ˜ÛÙ[™Ë[™[Û‹\ÙXÝ\š]KX›Ý[™\žH‹ˆ
Kˆ
ˆ’[\[Y[“È›ÜˆÝ\ˆÝ\X˜\ÙH]X˜\ÙKÚXÚ\Ù\ÈÜÝÜ™TÔS‹ˆ˜ÛÙ[™Ë\Ý\X˜\ÙK\ÙXÝ\š]KX›Ý[™\žH‹ˆ
KˆBˆ›Üˆ›Û\[WÚY[ˆØ\Ù\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆÙ[‹˜\ÜÙ\\]X[
Ù[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
VÈœ[WÚY—K[WÚY
B‚ˆ[™\[™[HÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠˆ’[\[Y[“È[ˆÝ\X˜\ÙH[™HÙ\\˜]HÜÝÜ™TÔS]X˜\ÙH‚ˆ
BˆÙ[‹˜\ÜÙ\\]X[
[™\[™[Èœ[WÚY—KˆŠBˆÙ[‹˜\ÜÙ\[Šˆ”ÑPÕT’UWÔÕT‘PÑT×Ô‘TURT‘WÔÔUÕTÒÈ‹[™\[™[Èœ™X\ÛÛ—ØÛÙ\È—Bˆ
B‚ˆYˆ\ÝÛ][\WÜÙXÝ\š]WÜÝ\™˜XÙ\×Ù˜Z[ØÛÜÙYÝÚ]ÜÜ]Ü™\]Z\™[Y[
Ù[ŠHOˆ›Û™N‚ˆ›Û\ÈHÂˆ”[ˆHY\ÙXÝ\š]HØØ[ˆÙˆÝ\X˜\ÙH[™™[ÛˆÜÝÜ™\È[ˆ\È™XXÝœ›Û[™™\ÜÚ]ÜžH‹ˆ’[\[Y[Ý\X˜\ÙH“ÈÛXÚY\È[™ÙXÝ\™HÛ›HÛÛÚÚY\È[ˆ\È™XXÝœ›Û[™‹ˆBˆ›Üˆ›Û\[ˆ›Û\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
BˆÙ[‹˜\ÜÙ\\]X[
XÚ\Ú[Û–Èœ[WÚY—KˆŠBˆÙ[‹˜\ÜÙ\\Ó›Û™JXÚ\Ú[Û–Èœš[X\žH—JBˆÙ[‹˜\ÜÙ\[Šˆ”ÑPÕT’UWÔÕT‘PÑT×Ô‘TURT‘WÔÔUÕTÒÈ‹ˆXÚ\Ú[Û–Èœ™X\ÛÛ—ØÛÙ\È—Kˆ
B‚ˆYˆ\ÝÛ][\WÜÙXÝ\š]WÜ\Ù\×Ù˜Z[ØÛÜÙYÚ[œÝXYÛÙ—Ù›Ü[™×ÝÛÜšÊÙ[ŠHOˆ›Û™N‚ˆØ\Ù\ÈHÂˆ
ˆ”[ˆHY\ÙXÝ\š]HØØ[ˆ[ˆ™[YYX]HHš[™[™ÜÈ‹ˆÈ‘QTÔÐÐSˆ‹‘’VŸKˆ
Kˆ
ˆ”[ˆHÝ[™\™ÙXÝ\š]HØØ[ˆ[™š^Hš[™[™ÜÈ‹ˆÈ”ÕS‘T‘ÔÐÐSˆ‹‘’VŸKˆ
Kˆ
‘\ØÛÝ™\ˆ[™\˜Xš[]Y\È[ˆš^[H‹È‘TÐÓÕ‘T–H‹‘’VŸJKˆ
ˆ‘š\œÝ\ØÛÝ™\ˆÙXÝ\š]Hš[™[™ÜÈ[ˆ\È[Ù[K[ˆš^[H‹ˆÈ‘TÐÓÕ‘T–H‹‘’VŸKˆ
Kˆ
ˆ”ØØ[ˆ\È™\ÜÚ]ÜžH›Üˆ[™\˜Xš[]Y\È[™]Ú[H‹ˆÈ”ÕS‘T‘ÔÐÐSˆ‹‘’VŸKˆ
Kˆ
ˆ•šXYÙH\ÈÙXÝ\š]Hš[™[™È[ˆš^]‹ˆÈ•’PQÑH‹‘’VŸKˆ
Kˆ
ˆ•˜[Y]H\ÈÙXÝ\š]Hš[™[™È[ˆš^]‹ˆÈ•SQUSÓˆ‹‘’VŸKˆ
Kˆ
ˆ‘š^[™˜[Y]H\È[™\˜Xš[]Hš[™[™È‹ˆÈ‘’V‹•SQUSÓˆŸKˆ
Kˆ
ˆ•Üš]HH[™\˜Xš[]H™\Ü[ˆ˜XÚÈ\ÈÙXÝ\š]Hš[™[™È[ˆ[™X\ˆ‹ˆÈ•Ô’UUT‹•PÒÒS‘ÈŸKˆ
KˆBˆ›Üˆ›Û\^XÝYÜ\Ù\È[ˆØ\Ù\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
BˆÙ[‹˜\ÜÙ\\]X[
XÚ\Ú[Û–Èœ[WÚY—KˆŠBˆÙ[‹˜\ÜÙ\\Ó›Û™JXÚ\Ú[Û–Èœš[X\žH—JBˆÙ[‹˜\ÜÙ\[Šˆ”ÑPÕT’UWÔTÑT×Ô‘TURT‘WÔÔUÕTÒÈ‹ˆXÚ\Ú[Û–Èœ™X\ÛÛ—ØÛÙ\È—Kˆ
Bˆ\ÙWÜ™X\ÛÛˆH™^
ˆ™X\ÛÛ‚ˆ›Üˆ™X\ÛÛˆ[ˆXÚ\Ú[Û–Èœ™X\ÛÛ—ØÛÙ\È—BˆYˆ™X\ÛÛ‹œÝ\ÝÚ]
”ÑPÕT’UWÔTÑT×ÈŠBˆ[™™X\ÛÛˆOH”ÑPÕT’UWÔTÑT×Ô‘TURT‘WÔÔUÕTÒÈ‚ˆ
Bˆ›Üˆ\ÙH[ˆ^XÝYÜ\Ù\Î‚ˆÙ[‹˜\ÜÙ\[Š\ÙK\ÙWÜ™X\ÛÛŠB‚ˆYˆ\ÝÛ™[Û—Ü›Ú™XÝØœ˜[˜ÚÚ\×Ü›ÝšY\—ÜØÛÜWÛ›ÝÙÚ]ÙY™—ÜØÛÜJÙ[ŠHOˆ›Û™N‚ˆ›Û\ÈHÂˆ”[ˆHÝ[™\™ÙXÝ\š]HØØ[ˆÙˆ\È™[Ûˆ]X˜\ÙHœ˜[˜Ú‹ˆ”[ˆHÙXÝ\š]HØØ[ˆÙˆH™[Ûˆ›Ú™XÝœ˜[˜Ú›ÙXÝ[Ûˆ‹ˆBˆ›Üˆ›Û\[ˆ›Û\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆÙ[‹˜\ÜÙ\\]X[
ˆÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
VÈœ[WÚY—Kˆ›™[Û‹\Ý[™\™\ÙXÝ\š]K\™]šY]È‹ˆ
BˆÙ[‹˜\ÜÙ\›Ý\]X[
ˆÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ”™]šY]ÈÝÈÈ]Ú\È[™\˜Xš[]HŠVÈœ[WÚY—KˆœÙXÝ\š]KYY™‹\™]šY]È‹ˆ
B‚ˆYˆ\ÝÙ^XÚ]Û]™WØXØÙ\Ü×Ù^Û\Ú[Û—ÛÝ™\œšY\×ØØ[X›WÜ›Ø™JÙ[ŠHOˆ›Û™N‚ˆØ\Ù\ÈHÂˆ
ˆ•˜[Y]H\ÈÝ\X˜\ÙHÙXÝ\š]Hš[™[™ÈÚ]Ý]\Ú[™ÈH]™HÝ\X˜\ÙHÛÛ›™XÝÜˆ‹ˆ˜\œÝ\X˜\ÙH‹ˆœÝ\X˜\ÙK\ÙXÝ\š]KYš[™[™Ë]˜[Y][Ûˆ‹ˆ
Kˆ
ˆ•˜[Y]H\ÈÝ\X˜\ÙHÙXÝ\š]Hš[™[™ÈÝ]XØ[NÈÈ›ÝXØÙ\ÜÈH]™H›Ú™XÝ‹ˆ˜\œÝ\X˜\ÙH‹ˆœÝ\X˜\ÙK\ÙXÝ\š]KYš[™[™Ë]˜[Y][Ûˆ‹ˆ
Kˆ
ˆ•˜[Y]H\ÈÝ\X˜\ÙHÙXÝ\š]Hš[™[™ÎÈÈ›ÝØ[HÝ\X˜\ÙH\‹ˆ˜\œÝ\X˜\ÙH‹ˆœÝ\X˜\ÙK\ÙXÝ\š]KYš[™[™Ë]˜[Y][Ûˆ‹ˆ
Kˆ
ˆ•˜[Y]H\È™[ÛˆÙXÝ\š]Hš[™[™ÈÚ]Ý]]™H›ÝšY\ˆXØÙ\ÜÈ‹ˆ˜\›™[Ûˆ‹ˆ›™[Û‹\ÙXÝ\š]KYš[™[™Ë]˜[Y][Ûˆ‹ˆ
Kˆ
ˆ•˜[Y]H\È™[ÛˆÙXÝ\š]Hš[™[™ÈÚ]Ý]\Ú[™ÈH]™H™[ÛˆÛÛ›™XÝÜˆ‹ˆ˜\›™[Ûˆ‹ˆ›™[Û‹\ÙXÝ\š]KYš[™[™Ë]˜[Y][Ûˆ‹ˆ
Kˆ
ˆ•˜[Y]H\È™[ÛˆÙXÝ\š]Hš[™[™ÈÝ]XØ[NÈÈ›ÝXØÙ\ÜÈH]™H›Ú™XÝ‹ˆ˜\›™[Ûˆ‹ˆ›™[Û‹\ÙXÝ\š]KYš[™[™Ë]˜[Y][Ûˆ‹ˆ
Kˆ
ˆ•˜[Y]H\È™[ÛˆÙXÝ\š]Hš[™[™ÎÈÈ›ÝØ[H™[Ûˆ\‹ˆ˜\›™[Ûˆ‹ˆ›™[Û‹\ÙXÝ\š]KYš[™[™Ë]˜[Y][Ûˆ‹ˆ
Kˆ
ˆ”[ˆHY\ÙXÝ\š]HØØ[ˆÙˆ\È™\ÜÚ]ÜžHÚ]Ý]\Ú[™ÈHÛÙ^ÙXÝ\š]HPÔ‹ˆ›XÜ˜ÛÙ^\ÙXÝ\š]H‹ˆ™Y\\ÙXÝ\š]K\™]šY]È‹ˆ
KˆBˆ›Üˆ›Û\\[™[˜ÞWÚY[WÚY[ˆØ\Ù\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™UÚ]Ø[X›Q\[™[˜ÞJˆ›Û\\[™[˜ÞWÚYˆ
BˆÙ[‹˜\ÜÙ\\]X[
XÚ\Ú[Û–Èœ[WÚY—K[WÚY
Bˆ˜[˜XÚÈHXÚ\Ú[Û–È˜Ø\Xš[]WÙ˜[˜XÚÜÈ—VÌBˆÙ[‹˜\ÜÙ\\]X[
˜[˜XÚÖÈ™\]Z]˜[[˜ÙH—K››Û—Ù\]Z]˜[[ŠBˆÙ[‹˜\ÜÙ\[Šˆˆœ›Û\žÙ\[™[˜ÞWÚYN™^XÚ]WÙ^ÛYY‹ˆ˜[˜XÚÖÈ[˜]˜Z[X›WÙ\[™[˜ÚY\È—Kˆ
B‚ˆYˆ\ÝÜ™\ÜÚ]ÜžWÝ[™\˜Xš[]WÜØØ[—Ý\Ù\×ÜÝ[™\™ÜØØ[—Û›ÝÙ\ØÛÝ™\žJÙ[ŠHOˆ›Û™N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ”ØØ[ˆ\È™\ÜÚ]ÜžH›Üˆ[™\˜Xš[]Y\ÈŠBˆÙ[‹˜\ÜÙ\\]X[
XÚ\Ú[Û–Èœ[WÚY—KœÝ[™\™\ÙXÝ\š]K\™]šY]ÈŠBˆÙ[‹˜\ÜÙ\›Ý\]X[
XÚ\Ú[Û–Èœ[WÚY—KœÙXÝ\š]KYš[™[™ËY\ØÛÝ™\žHŠB‚ˆYˆ\ÝÙš^Ü™\]Z\™\×ØÝ\œ™[Û]]][Û—Ú[[
Ù[ŠHOˆ›Û™N‚ˆ™YØ]]™WÜ›Û\ÈHÂˆ‘^Z[ˆÝÈÈš^\ÈÙXÝ\š]Hš[™[™È‹ˆ‘š^\ÈÙXÝ\š]Hš[™[™È]\‹Û›H^Z[ˆ›ÝÈ‹ˆBˆ›Üˆ›Û\[ˆ™YØ]]™WÜ›Û\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
BˆÙ[‹˜\ÜÙ\›Ý\]X[
XÚ\Ú[Û–Èœ[WÚY—KœÙXÝ\š]KYš[™[™ËYš^ŠBˆÙ[‹˜\ÜÙ\›Ý\]X[
XÚ\Ú[Û–Èœ[WÚY—K˜ÛÙ[™Ë\ÙXÝ\š]KX›Ý[™\žHŠBˆÙ[‹˜\ÜÙ\\]X[
ˆÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠˆ’[\[Y[Hš^›Üˆ\È˜[Y]YÙXÝ\š]Hš[™[™È‚ˆ
VÈœ[WÚY—KˆœÙXÝ\š]KYš[™[™ËYš^‹ˆ
B‚ˆYˆ\ÝÝ˜XÚÙ\—Ù\Ý[˜][Û—ÜÙ[XÝ×ÛX]Ú[™×ÜÝ\Ü
Ù[ŠHOˆ›Û™N‚ˆØ\Ù\ÈHÂˆ
ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆÚ]Xˆ‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™ËYÚ]Xˆ‹ˆœÚÚ[™Ú]XŽ™Ú]Xˆ‹ˆ
Kˆ
ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆ[™X\ˆ‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™Ë[[™X\ˆ‹ˆÛÛY˜[Z[N˜\›[™X\ˆ‹ˆ
Kˆ
ˆ”Þ[˜È\ÙHÙXÝ\š]Hš[™[™ÜÈÈš\˜H‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™ËZš\˜H‹ˆÛÛY˜[Z[N˜\˜]\ÜÚX[ˆ‹ˆ
KˆBˆ›Üˆ›Û\[WÚYÝ\Ü[ˆØ\Ù\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
BˆÙ[‹˜\ÜÙ\\]X[
XÚ\Ú[Û–Èœ[WÚY—K[WÚY
BˆÙ[‹˜\ÜÙ\\]X[
ˆÚ][VÈšY—H›Üˆ][H[ˆXÚ\Ú[Û–ÈœÝ\ÜÈ—WKÜÝ\ÜBˆ
B‚ˆYˆ\ÝÝ˜XÚÙ\—Ù\Ý[˜][Û—Û™YØ][Û—ÜÙ[XÝ×ÛÛ›WÝWØY™š\›X]]™WÝ\™Ù]
Ù[ŠHOˆ›Û™N‚ˆØ\Ù\ÈHÂˆ
ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆ[™X\ˆ]›ÝÚ]Xˆ[™›Ýš\˜H‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™Ë[[™X\ˆ‹ˆÛÛY˜[Z[N˜\›[™X\ˆ‹ˆ
Kˆ
ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆÚ]Xˆ]›Ý[™X\ˆ[™›Ý]\ÜÚX[ˆ‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™ËYÚ]Xˆ‹ˆœÚÚ[™Ú]XŽ™Ú]Xˆ‹ˆ
Kˆ
ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆš\˜H]›ÝÚ]Xˆ[™›Ý[™X\ˆ‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™ËZš\˜H‹ˆÛÛY˜[Z[N˜\˜]\ÜÚX[ˆ‹ˆ
KˆBˆ›Üˆ›Û\[WÚYÝ\Ü[ˆØ\Ù\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
BˆÙ[‹˜\ÜÙ\\]X[
XÚ\Ú[Û–Èœ[WÚY—K[WÚY
BˆÙ[‹˜\ÜÙ\\]X[
ˆÚ][VÈšY—H›Üˆ][H[ˆXÚ\Ú[Û–ÈœÝ\ÜÈ—WKÜÝ\ÜBˆ
B‚ˆYˆ\ÝÝ˜XÚÙ\—ØÛÛ˜\ÝÜÙ[XÝ×ÛÛ›WÝWÜ™Y™\œ™YÙ\Ý[˜][ÛŠÙ[ŠHOˆ›Û™N‚ˆØ\Ù\ÈHÂˆ
ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆÚ]Xˆ˜]\ˆ[ˆ[™X\ˆ‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™ËYÚ]Xˆ‹ˆœÚÚ[™Ú]XŽ™Ú]Xˆ‹ˆ
Kˆ
ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆ[™X\ˆ˜]\ˆ[ˆÚ]Xˆ‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™Ë[[™X\ˆ‹ˆÛÛY˜[Z[N˜\›[™X\ˆ‹ˆ
Kˆ
ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆš\˜H[œÝXYÙˆÚ]Xˆ‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™ËZš\˜H‹ˆÛÛY˜[Z[N˜\˜]\ÜÚX[ˆ‹ˆ
Kˆ
ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆÚ]Xˆ[œÝXYÙˆš\˜H‹ˆœÙXÝ\š]KYš[™[™ÜË]˜XÚÚ[™ËYÚ]Xˆ‹ˆœÚÚ[™Ú]XŽ™Ú]Xˆ‹ˆ
KˆBˆ›Üˆ›Û\[WÚYÝ\Ü[ˆØ\Ù\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
BˆÙ[‹˜\ÜÙ\\]X[
XÚ\Ú[Û–Èœ[WÚY—K[WÚY
BˆÙ[‹˜\ÜÙ\\]X[
ˆÚ][VÈšY—H›Üˆ][H[ˆXÚ\Ú[Û–ÈœÝ\ÜÈ—WKÜÝ\ÜBˆ
B‚ˆYˆ\ÝÛ][\WØY™š\›X]]™WÝ˜XÚÙ\—Ù\Ý[˜][Ûœ×Ü™\]Z\™WÜÜ]
Ù[ŠHOˆ›Û™N‚ˆ›Û\ÈHÂˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆÚ]Xˆ[™[™X\ˆ‹ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆ[™X\ˆ[™š\˜H‹ˆ•˜XÚÈ\ÙHÙXÝ\š]Hš[™[™ÜÈ[ˆÚ]Xˆ[™]\ÜÚX[ˆ‹ˆBˆ›Üˆ›Û\[ˆ›Û\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆXÚ\Ú[ÛˆHÙ[‹œ™\ÛÛ™QXÚ\Ú[ÛŠ›Û\
BˆÙ[‹˜\ÜÙ\\]X[
XÚ\Ú[Û–Èœ[WÚY—KˆŠBˆÙ[‹˜\ÜÙ\\Ó›Û™JXÚ\Ú[Û–Èœš[X\žH—JBˆÙ[‹˜\ÜÙ\[Šˆ•PÒÑT—ÑTÕSUSÓ”×Ô‘TURT‘WÔÔUÕTÒÈ‹ˆXÚ\Ú[Û–Èœ™X\ÛÛ—ØÛÙ\È—Kˆ
B‚ˆYˆ\ÝÛ™YØ]]™WÙ^[\\×Ù×Û›ÝØXÝ]˜]WÜÙXÝ\š]WÛÜ—Ù]X˜\ÙWÜ›ÝšY\—Ü›Ý]\ÊÙ[ŠHOˆ›Û™N‚ˆ›Û\ÈHÂˆÚ[™ÙHH™[ÛˆÜ˜YY[Ûˆ\È]Ûˆ‹ˆ’[\[Y[HÝ\X˜\ÙHÙÛÈ[ˆH›ÛÝ\ˆ‹ˆ	Ô][ÝY^[\Nˆœ[ˆHÝ[™\™ÙXÝ\š]HØØ[ˆÙˆH™\ÜÚ]ÜžH‹ˆÝ[[X\š^™H]‰Ëˆ•HÝXš™XÝ\ÈÙXÝ\š]HØØ[ˆ‹ˆ‘È›Ý[ˆHÙXÝ\š]HØØ[ˆÙˆ\È™\ÜÚ]ÜžH‹ˆ‘È›Ýš^\ÈÙXÝ\š]Hš[™[™ÎÈÛ›HÝ[[X\š^™H]‹ˆ‘˜YHÛÜœÜ˜]HÛXÞHØÝ[Y[‹ˆ‘^Z[ˆ\È]X˜\ÙHšY]È‹ˆÚ[™ÙHH]ÛˆÛÛÜˆ‹ˆBˆ›Ü˜šY[—Ü™Yš^\ÈH
ˆœÙXÝ\š]KH‹ˆœÝ\X˜\ÙKH‹ˆ›™[Û‹H‹ˆœÜÝÜ™\ËH‹ˆ˜ÛÙ[™Ë\Ý\X˜\ÙKH‹ˆ˜ÛÙ[™Ë[™[Û‹H‹ˆ˜ÛÙ[™Ë\ÜÝÜ™\ËH‹ˆ™œ›Û[™\ÙXÝ\š]KH‹ˆ™Y\\ÙXÝ\š]KH‹ˆœÝ[™\™\ÙXÝ\š]KH‹ˆ˜ÛÙ^\ÙXÝ\š]KH‹ˆ›ØØ[\ÙXÝ\š]KH‹ˆ™Y™[œÚ]™K\ÙXÝ\š]KH‹ˆ
Bˆ›Üˆ›Û\[ˆ›Û\Î‚ˆÚ]Ù[‹œÝX•\Ý
›Û\\›Û\
N‚ˆ[HHš\œÝÛX]Ú[™×Ü[J›Û\
BˆYˆ[H\È›Ý›Û™N‚ˆÙ[‹˜\ÜÙ\˜[ÙJ[VÈšY—KœÝ\ÝÚ]
›Ü˜šY[—Ü™Yš^\ÊK[JB‚‚šYˆ×Û˜[YW×ÈOH—×ÛXZ[—×ÈŽ‚ˆ[š]\Ý›XZ[Š
B