#!/usr/bin/env python3
"""Repository-safe regression tests for the dormant canonical router port."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTING_ROOT = REPO_ROOT / "capability-routing"
RUNTIME_ROOT = ROUTING_ROOT / "reference-runtime"
_IMPORT_TEMP = tempfile.TemporaryDirectory(prefix="ccos-router-import-")
_IMPORT_ROOT = Path(_IMPORT_TEMP.name)

_ROUTER_ENV = {
    "CODEX_HOME": _IMPORT_ROOT,
    "CODEX_CAPABILITY_ROUTING_DIR": _IMPORT_ROOT / "routing",
    "CODEX_ACTIVE_CAPABILITIES_PATH": _IMPORT_ROOT / "active-capabilities.json",
    "CODEX_ROUTING_POLICY_PATH": ROUTING_ROOT / "routing-policy.yaml",
    "CODEX_ROUTING_POLICY_SCHEMA_PATH": ROUTING_ROOT
    / "routing-policy.schema.json",
    "CODEX_CONFIG_PATH": _IMPORT_ROOT / "config.toml",
    "CODEX_ROUTE_DECISION_SCHEMA_PATH": ROUTING_ROOT / "route-decision.schema.json",
    "CODEX_ROUTE_DECISION_REGISTRY_PATH": _IMPORT_ROOT / "route-decisions.sqlite3",
    "CODEX_PROJECT_SCOPE_MAP_PATH": _IMPORT_ROOT / "project-scope-map.json",
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


def active_entry(identifier: str) -> dict[str, object]:
    name = identifier.split(":", 1)[1] if ":" in identifier else identifier
    return {
        "id": identifier,
        "kind": "skill",
        "name": name,
        "state": "active",
        "provider": name.split(":", 1)[0],
        "version": "1.0.0",
        "source_path": str(_IMPORT_ROOT / f"{name.replace(':', '_')}.md"),
        "sha256": "a" * 64,
        "families": [],
    }


def capability_entry(identifier: str) -> dict[str, object]:
    if identifier.startswith("mcp:"):
        kind = "mcp"
    elif identifier.startswith("tool-family:"):
        kind = "tool-family"
    elif identifier.startswith("app:"):
        kind = "app"
    elif identifier.startswith("adapter:"):
        kind = "adapter"
    elif identifier.startswith("execution:"):
        kind = "execution"
    else:
        kind = "skill"
    value = active_entry(identifier)
    value["kind"] = kind
    return value


def policy_capability_entries(policy: dict[str, object]) -> list[dict[str, object]]:
    references: set[str] = set(policy.get("capability_aliases", {}))
    for control in policy.get("live_dependency_controls", {}).values():
        references.update(control.get("manifest_any", []))
    for section in ("worker_rules", "local_execution_rules"):
        for rule in policy.get(section, []):
            references.update(rule.get("requires_any_capabilities", []))
    for rule in policy["rules"]:
        references.add(rule["primary"])
        references.update(rule.get("supports", []))
        references.update(
            item.removeprefix("active:")
            for item in rule.get("requires", [])
            if not item.casefold().startswith("prompt:")
        )
        references.update(
            item.removeprefix("capability:")
            for item in rule.get("forbids", [])
            if not item.casefold().startswith("prompt:")
        )
        fallback = rule.get("dependency_fallback") or {}
        if fallback.get("selected_capability"):
            references.add(fallback["selected_capability"])
        references.update(fallback.get("supports", []))
        references.update(fallback.get("equivalent_capabilities", []))
    for override in policy.get("explicit_overrides", []):
        references.add(override["target"])
        for field in ("requires_primary", "winner"):
            if override.get(field):
                references.add(override[field])
    return [capability_entry(reference) for reference in sorted(references)]


def synthetic_manifest(
    policy: dict[str, object], *extra_identifiers: str
) -> dict[str, object]:
    entries = policy_capability_entries(policy)
    by_id = {str(item["id"]): item for item in entries}
    for identifier in extra_identifiers:
        by_id[identifier] = capability_entry(identifier)
    return {
        "schema_version": "1.0",
        "generated_at": "2026-08-11T00:00:00Z",
        "snapshot_id": "synthetic-router-snapshot",
        "freshness_status": "fresh",
        "source_hashes_verified": True,
        "entries": list(by_id.values()),
        "suppressed_capabilities": [],
    }


def execution_disposition(worker_family: str | None = None) -> dict[str, object]:
    return {
        "mode": "worker_support" if worker_family else "codex_only",
        "eligible_worker_families": [worker_family] if worker_family else [],
    }


def complete_task_input(
    instruction: str,
    *,
    request_id: str,
    worker_family: str | None = None,
    **fields: object,
) -> dict[str, object]:
    return {
        "instruction": instruction,
        "execution_request_id": request_id,
        "execution_disposition": execution_disposition(worker_family),
        **fields,
    }


def live_dependency_probe(
    dependency_id: str,
    *,
    request_id: str,
    status: str = "callable",
    target: str | None = None,
) -> dict[str, object]:
    return {
        dependency_id: {
            "kind": "live_call",
            "target": target or dependency_id,
            "status": status,
            "request_id": request_id,
        }
    }


def complete_classification(
    *flags: str,
    worker_family: str | None = None,
    task_type: str = "implement",
    complexity: str = "medium",
    local_stack_purpose: str = "focused_coding_assistance",
) -> dict[str, object]:
    return {
        "project_id": "generic",
        "task_type": task_type,
        "complexity": complexity,
        "local_stack_purpose": local_stack_purpose,
        "source_need": "none",
        "requested_source_scopes": [],
        "memory_mode": "none",
        "persistence_intent": "none",
        "flags": list(flags),
        "execution_disposition": execution_disposition(worker_family),
    }


class RepositoryCapabilityRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos-router-test-")
        self.root = Path(self.temp.name).resolve()
        self.codex_home = self.root / "codex-home"
        self.routing_dir = self.codex_home / "capability-routing"
        self.routing_dir.mkdir(parents=True)
        self.manifest_path = self.routing_dir / "active-capabilities.json"
        self.policy_path = self.routing_dir / "routing-policy.yaml"
        self.policy_schema_path = self.routing_dir / "routing-policy.schema.json"
        self.config_path = self.codex_home / "config.toml"
        self.schema_path = self.routing_dir / "route-decision.schema.json"
        self.registry = self.routing_dir / "route-decisions.sqlite3"
        self.project_map_path = self.routing_dir / "project-scope-map.json"
        self.generation_pointer_path = self.routing_dir / "current-generation.json"
        self.worker_runtime_bom_path = self.routing_dir / "worker-runtime-bom.json"
        self.worker_python_probes: dict[str, dict[str, object]] = {}
        self.worker_pth_probes: dict[str, dict[str, str]] = {}
        self.local_app_data = self.root / "localappdata"
        self.local_app_data.mkdir()
        self.policy_path.write_text(
            (ROUTING_ROOT / "routing-policy.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.policy_schema_path.write_text(
            (ROUTING_ROOT / "routing-policy.schema.json").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        raw_policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        self.manifest_path.write_text(
            json.dumps(synthetic_manifest(raw_policy), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.schema_path.write_text(
            (ROUTING_ROOT / "route-decision.schema.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.config_path.write_text("[features]\nhooks = true\n", encoding="utf-8")
        self.project_map_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "projects": {
                        "generic": {
                            "roots": [],
                            "source_scopes": [],
                            "memory_scope": None,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.patches = ExitStack()
        self.patches.enter_context(
            mock.patch.object(
                index,
                "_probe_worker_python_execution",
                side_effect=self._worker_python_probe,
            )
        )
        self.patches.enter_context(
            mock.patch.object(
                index,
                "_probe_worker_pth_import_origins",
                side_effect=self._worker_pth_probe,
            )
        )
        self.patches.enter_context(
            mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.local_app_data)})
        )
        for name, value in {
            "CODEX_HOME": self.codex_home,
            "ROUTING_DIR": self.routing_dir,
            "ACTIVE_CAPABILITIES_PATH": self.manifest_path,
            "ROUTING_POLICY_PATH": self.policy_path,
            "ROUTING_POLICY_SCHEMA_PATH": self.policy_schema_path,
            "CONFIG_PATH": self.config_path,
            "ROUTE_DECISION_SCHEMA_PATH": self.schema_path,
            "ROUTE_DECISION_REGISTRY_PATH": self.registry,
            "PROJECT_SCOPE_MAP_PATH": self.project_map_path,
            "AUTHORITY_GENERATION_POINTER_PATH": self.generation_pointer_path,
            "WORKER_RUNTIME_BOM_PATH": self.worker_runtime_bom_path,
            "GATEWAY_STARTUP_RECEIPT_PATH": self.local_app_data
            / "Codex"
            / "stability"
            / "gateway-startup-receipt.json",
        }.items():
            self.patches.enter_context(mock.patch.object(index, name, value))
        self.patches.enter_context(
            mock.patch.object(
                index, "_gateway_receipt_process_current", return_value=True
            )
        )

    def tearDown(self) -> None:
        self.patches.close()
        self.temp.cleanup()

    def _verify_with_current_authority(
        self, decision: dict[str, object], **kwargs: object
    ) -> dict[str, object]:
        manifest = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "authority_sha256": decision["manifest_authority_sha256"],
        }
        policy = {"authority_sha256": decision["policy_authority_sha256"]}
        with mock.patch.object(
            index, "load_active_capabilities", return_value=manifest
        ), mock.patch.object(index, "load_routing_policy", return_value=policy):
            return index.verify_registered_route(decision, **kwargs)

    def _enable_local_gateway(self) -> None:
        self._install_worker_runtime_fixture()

    def test_policy_and_schema_contracts_are_valid_and_public_safe(self) -> None:
        policy = json.loads((ROUTING_ROOT / "routing-policy.yaml").read_text("utf-8"))
        schema = json.loads(
            (ROUTING_ROOT / "routing-policy.schema.json").read_text("utf-8")
        )
        decision_schema = json.loads(
            (ROUTING_ROOT / "route-decision.schema.json").read_text("utf-8")
        )
        project_schema = json.loads(
            (ROUTING_ROOT / "project-scope-map.schema.json").read_text("utf-8")
        )
        ids = [rule["id"] for rule in policy["rules"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(policy["schema_version"], "2.0")
        self.assertEqual(decision_schema["properties"]["schema_version"]["const"], "3.0")
        self.assertEqual(
            decision_schema["$defs"]["issuance"]["properties"][
                "registry_schema_version"
            ]["const"],
            3,
        )
        self.assertEqual(policy["max_supports"], 2)
        self.assertLessEqual(max(len(rule["supports"]) for rule in policy["rules"]), 2)
        self.assertEqual(schema["properties"]["max_supports"]["maximum"], 2)
        self.assertIn(
            "probe_requirement",
            schema["$defs"]["live_dependency_control"]["required"],
        )
        for dependency_id, control in policy["live_dependency_controls"].items():
            with self.subTest(dependency=dependency_id):
                self.assertEqual(
                    control["probe_requirement"],
                    {
                        "kind": "live_call",
                        "target": dependency_id,
                        "success_status": "callable",
                    },
                )
        equivalent_constraint = schema["$defs"]["dependency_fallback"]["allOf"][0]
        self.assertEqual(
            equivalent_constraint["then"]["properties"]["selected_capability"][
                "minLength"
            ],
            1,
        )
        decision_equivalent_constraint = decision_schema["$defs"][
            "capability_fallback"
        ]["allOf"][0]
        self.assertEqual(
            decision_equivalent_constraint["then"]["properties"][
                "selected_capability"
            ]["minLength"],
            1,
        )
        self.assertEqual(
            decision_schema["$defs"]["local_execution"]["properties"]["project_id"][
                "pattern"
            ],
            "^[a-z][a-z0-9_]{0,63}$",
        )
        self.assertEqual(project_schema["properties"]["schema_version"]["const"], "1.0")
        public_text = "\n".join(
            path.read_text("utf-8", errors="replace")
            for path in ROUTING_ROOT.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".json", ".md", ".ps1", ".py", ".yaml", ".yml"}
        ).lower()
        self.assertNotRegex(public_text, r"[a-z]:\\(?:users|dev)\\")
        example_map = json.loads(
            (ROUTING_ROOT / "project-scope-map.example.json").read_text("utf-8")
        )
        self.assertEqual(
            set(example_map["projects"]), {"generic", "sample_project"}
        )

    def test_runtime_policy_loader_rejects_schema_and_semantic_corruption(self) -> None:
        raw = json.loads(self.policy_path.read_text(encoding="utf-8"))
        invalid_type = copy.deepcopy(raw)
        invalid_type["max_supports"] = {"not": "an integer"}
        self.policy_path.write_text(
            json.dumps(invalid_type) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(index.CapabilityDataError, "schema validation"):
            index.load_routing_policy(self.policy_path)

        missing_profile = copy.deepcopy(raw)
        missing_profile["default_execution_profile"] = "does-not-exist"
        self.policy_path.write_text(
            json.dumps(missing_profile) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(index.CapabilityDataError, "not declared"):
            index.load_routing_policy(self.policy_path)

        invalid_worker = copy.deepcopy(raw)
        invalid_worker["worker_rules"][0]["worker"]["model"] = (
            "schema-valid-but-unapproved-model"
        )
        self.policy_path.write_text(
            json.dumps(invalid_worker) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(index.CapabilityDataError, "worker contract"):
            index.load_routing_policy(self.policy_path)

    def test_runtime_policy_validation_requires_jsonschema(self) -> None:
        with mock.patch.dict(sys.modules, {"jsonschema": None}):
            with self.assertRaisesRegex(
                index.CapabilityDataError,
                "jsonschema is required",
            ):
                index.load_routing_policy(self.policy_path)

    def test_declared_suppressed_primary_is_valid_but_not_routable(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        suppressed_id = "skill:create-prd"
        manifest["entries"] = [
            row for row in manifest["entries"] if row["id"] != suppressed_id
        ]
        manifest["suppressed_capabilities"].append({"id": suppressed_id})
        self.manifest_path.write_text(
            json.dumps(manifest) + "\n",
            encoding="utf-8",
        )

        policy = index.load_routing_policy(self.policy_path)
        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            decision = index.resolve_route(
                "create a prd",
                manifest=manifest,
                policy=policy,
            )
        primary = decision.get("primary")
        self.assertNotEqual(
            primary.get("id") if isinstance(primary, dict) else None,
            suppressed_id,
        )

    def test_semantic_intent_gates_are_bounded_to_the_requested_workflow(self) -> None:
        policy = json.loads(
            (ROUTING_ROOT / "routing-policy.yaml").read_text(encoding="utf-8")
        )

        def first_rule(prompt: str) -> dict[str, object] | None:
            return next(
                (
                    rule
                    for rule in policy["rules"]
                    if index._rule_matches_prompt(rule, prompt.lower(), policy)
                ),
                None,
            )

        expected_routes = [
            (
                "Review my proposal and tell me whether it is good.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "What do you think about this recommendation?",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Review the sales strategy and identify weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Review this report and identify weak assumptions before the meeting.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Review this repository architecture for flawed dependency direction.",
                "coding-architecture-review",
                "skill:improve-codebase-architecture",
            ),
            (
                "Critique my security policy for missing authorization controls and grammar.",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Validate my analysis and challenge its assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Summarize this plan. On second thought, review it critically.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for spelling only. Actually, challenge its assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Review this operating update for decision quality.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions and correct its grammar.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Review this plan for weak assumptions and then proofread it.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions and summarize it.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo and proofread it.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions. Proofread it too.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions. Proofread it too, please.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions. Proofread it as well, please.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions. Proofread it as well, if possible.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo and proofread it for grammar.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Correct the grammar and then critique its weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions and then critique its spelling.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this plan for flawed logic and then challenge its wording.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "What do you think about this plan and then critique its spelling?",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Is this plan good and then critique its spelling?",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Should we use this recommendation and then critique its wording?",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions. Critique its spelling too.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions. Critique its spelling also.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo's grammar and argument.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Correct the grammar and then review it for weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Proofread this memo and then audit its reasoning.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Summarize this plan and instead audit its weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Correct the grammar and then only review the reasoning.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Only critique this memo for weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Just critique this memo for weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Please only critique this memo for weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            ("Only deep critique this memo.", "deep-critique", "skill:deep-critic"),
            ("Just critique this memo.", "deep-critique", "skill:deep-critic"),
            ("Please only challenge this plan.", "deep-critique", "skill:deep-critic"),
            ("Only audit this report.", "deep-critique", "skill:deep-critic"),
            ("Just stress-test this proposal.", "deep-critique", "skill:deep-critic"),
            ("Only review this plan.", "deep-critique", "skill:deep-critic"),
            (
                "Proofread this memo and then review the strategy.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Correct grammar and then critically review its assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique weak assumptions, but do not critique spelling.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Summarize this plan and also critique its weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Audit the conclusions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Summarize this report and instead audit the conclusions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique the proposal's argument and the memo's grammar.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Is this business case defensible?",
                "quantitative-model-critique",
                "skill:quant-review",
            ),
            ("Is the forecast credible?", "deep-critique", "skill:deep-critic"),
            ("Is this workflow correct?", "deep-critique", "skill:deep-critic"),
            (
                "Should we use this business case?",
                "quantitative-model-critique",
                "skill:quant-review",
            ),
            (
                "Summarize this plan. Rather, critique its weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique this memo for weak assumptions. Proofread also its grammar.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique grammar and strategy in this memo.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique my security policy for missing authorization controls and grammar only.",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Review the repository architecture for coupling and spelling only.",
                "coding-architecture-review",
                "skill:improve-codebase-architecture",
            ),
            (
                "Review weak assumptions and grammar only.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Review wording only and weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique weak assumptions and also critique grammar only.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique my security policy for authorization flaws and also audit wording only.",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Review repository architecture for coupling and also review spelling only.",
                "coding-architecture-review",
                "skill:improve-codebase-architecture",
            ),
            (
                "Review assumptions, logic, wording only.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique security flaws, grammar only.",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Critique weak assumptions along with grammar only.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique wording plus the conclusions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Review my security policy for missing authorization controls.",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Inspect the supplied PDF and explain its main findings.",
                "pdf-file-analysis",
                "skill:pdf:pdf",
            ),
            (
                "Review the supplied PDF for weak assumptions.",
                "critical-pdf-review",
                "skill:deep-critic",
            ),
            (
                "Review the supplied PDF for completeness and grammar only.",
                "critical-pdf-review",
                "skill:deep-critic",
            ),
            (
                "Review the supplied PDF for internal consistency and spelling only.",
                "critical-pdf-review",
                "skill:deep-critic",
            ),
            (
                "What do you think about this supplied PDF?",
                "critical-pdf-review",
                "skill:deep-critic",
            ),
            (
                "Is this supplied PDF accurate?",
                "critical-pdf-review",
                "skill:deep-critic",
            ),
            (
                "Should we use this supplied PDF?",
                "critical-pdf-review",
                "skill:deep-critic",
            ),
            (
                "What do you think about this repository architecture?",
                "coding-architecture-review",
                "skill:improve-codebase-architecture",
            ),
            (
                "Is this repository architecture sound?",
                "coding-architecture-review",
                "skill:improve-codebase-architecture",
            ),
            (
                "Should we use this repository architecture?",
                "coding-architecture-review",
                "skill:improve-codebase-architecture",
            ),
            (
                "What do you think about this authentication architecture?",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "What do you think about this security policy?",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Is this security policy sound?",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Should we use the security policy?",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Should I use our security policy?",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Should we use our repository architecture?",
                "coding-architecture-review",
                "skill:improve-codebase-architecture",
            ),
            (
                "Critique repository architecture for coupling and spelling, then implement the refactor.",
                "coding-deep-critique-implementation",
                "skill:codex-coding-os-master",
            ),
            (
                "Review a history essay that mentions repository architecture for weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique the argument in a history essay that mentions repository architecture.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique a sentence about access control for flawed logic.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Review an essay about market strategy for weak assumptions.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique weak assumptions together with grammar only.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Critique weak assumptions & grammar only.",
                "deep-critique",
                "skill:deep-critic",
            ),
            (
                "Review security policy and grammar only.",
                "security-best-practices-review",
                "skill:security-best-practices",
            ),
            (
                "Review repository architecture and spelling only.",
                "coding-architecture-review",
                "skill:improve-codebase-architecture",
            ),
            (
                "Review the supplied PDF and grammar only.",
                "critical-pdf-review",
                "skill:deep-critic",
            ),
            (
                "Read the supplied PDF about our API.",
                "pdf-file-analysis",
                "skill:pdf:pdf",
            ),
            (
                "Summarize the provided PDF about a Python module.",
                "pdf-file-analysis",
                "skill:pdf:pdf",
            ),
            (
                "Inspect report.pdf about source code.",
                "pdf-file-analysis",
                "skill:pdf:pdf",
            ),
            (
                "Read the PDF from the library.",
                "pdf-file-analysis",
                "skill:pdf:pdf",
            ),
            (
                "Analyze the supplied PDF describing a renderer.",
                "pdf-file-analysis",
                "skill:pdf:pdf",
            ),
            (
                "Review report.pdf for accuracy.",
                "critical-pdf-review",
                "skill:deep-critic",
            ),
            (
                "Critically review the supplied PDF and identify flaws.",
                "critical-pdf-review",
                "skill:deep-critic",
            ),
            (
                "Evaluate option A against option B and recommend which one to choose.",
                "strategy-options-war-game",
                "skill:strategy-debate-engine",
            ),
            (
                "Choose between these options.",
                "strategy-options-war-game",
                "skill:strategy-debate-engine",
            ),
            (
                "Compare these strategies and recommend one.",
                "strategy-options-war-game",
                "skill:strategy-debate-engine",
            ),
            (
                "Compare these options and recommend one.",
                "strategy-options-war-game",
                "skill:strategy-debate-engine",
            ),
            (
                "Which market should we choose?",
                "strategy-options-war-game",
                "skill:strategy-debate-engine",
            ),
            (
                "Compare our approaches and pick one.",
                "strategy-options-war-game",
                "skill:strategy-debate-engine",
            ),
            (
                "Which product should we choose?",
                "strategy-options-war-game",
                "skill:strategy-debate-engine",
            ),
            (
                "Which architecture should we choose?",
                "strategy-options-war-game",
                "skill:strategy-debate-engine",
            ),
            (
                "Develop a pricing strategy for a new market entry.",
                "strategy-pricing-analysis",
                "skill:pricing-strategy",
            ),
            (
                "Analyze willingness to pay for customers.",
                "strategy-pricing-analysis",
                "skill:pricing-strategy",
            ),
            (
                "Develop a pricing strategy for our product.",
                "strategy-pricing-analysis",
                "skill:pricing-strategy",
            ),
            (
                "Develop a pricing strategy for our consulting service.",
                "strategy-pricing-analysis",
                "skill:pricing-strategy",
            ),
            (
                "Run a Van Westendorp analysis for our new subscription plan.",
                "strategy-pricing-analysis",
                "skill:pricing-strategy",
            ),
            (
                "Design an operating model with clear decision rights and team interfaces.",
                "strategy-operating-model-design",
                "skill:operating-model-design",
            ),
            (
                "Clarify team decision rights.",
                "strategy-operating-model-design",
                "skill:operating-model-design",
            ),
            (
                "Redesign how our company operates.",
                "strategy-operating-model-design",
                "skill:operating-model-design",
            ),
            (
                "Map interfaces between our departments.",
                "strategy-operating-model-design",
                "skill:operating-model-design",
            ),
            (
                "Design decision rights across teams.",
                "strategy-operating-model-design",
                "skill:operating-model-design",
            ),
            (
                "Structure this unresolved business problem and tell me what to investigate first.",
                "strategy-situation-assessment",
                "skill:situation-assessment",
            ),
            (
                "Assess an unresolved company challenge.",
                "strategy-situation-assessment",
                "skill:situation-assessment",
            ),
            (
                "Diagnose an unclear customer problem.",
                "strategy-situation-assessment",
                "skill:situation-assessment",
            ),
            (
                "Structure our unclear market problem.",
                "strategy-situation-assessment",
                "skill:situation-assessment",
            ),
            (
                "Diagnose our unclear revenue challenge.",
                "strategy-situation-assessment",
                "skill:situation-assessment",
            ),
            (
                "Assess our unresolved organizational challenge.",
                "strategy-situation-assessment",
                "skill:situation-assessment",
            ),
            (
                "Triage an unresolved operations problem.",
                "strategy-situation-assessment",
                "skill:situation-assessment",
            ),
            ("$pricing-strategy", "strategy-pricing-analysis", "skill:pricing-strategy"),
            (
                "$operating-model-design",
                "strategy-operating-model-design",
                "skill:operating-model-design",
            ),
            (
                "$situation-assessment",
                "strategy-situation-assessment",
                "skill:situation-assessment",
            ),
            (
                "Pricing analysis gate",
                "strategy-pricing-analysis",
                "skill:pricing-strategy",
            ),
            (
                "Operating model design",
                "strategy-operating-model-design",
                "skill:operating-model-design",
            ),
            (
                "Situation assessment",
                "strategy-situation-assessment",
                "skill:situation-assessment",
            ),
        ]
        for prompt, rule_id, primary in expected_routes:
            with self.subTest(prompt=prompt):
                rule = first_rule(prompt)
                self.assertIsNotNone(rule)
                self.assertEqual(rule["id"], rule_id)
                self.assertEqual(rule["primary"], primary)

        security_rule = first_rule(
            "Review my security policy for missing authorization controls."
        )
        self.assertIn("skill:defensive-security-checklist", security_rule["supports"])
        critical_pdf_rule = first_rule(
            "Review the supplied PDF for weak assumptions."
        )
        self.assertIn("skill:pdf:pdf", critical_pdf_rule["supports"])

        rejected_routes = [
            ("Review this document and summarize key points.", {"deep-critique"}),
            ("Review my proposal and summarize it.", {"deep-critique"}),
            ("Review my document for grammar only.", {"deep-critique"}),
            ("Audit this document for grammar only.", {"deep-critique"}),
            ("Critique this memo for spelling only.", {"deep-critique"}),
            ("Challenge this plan for grammar only.", {"deep-critique"}),
            ("Stress-test this memo for spelling only.", {"deep-critique"}),
            ("Review this report before the meeting.", {"deep-critique"}),
            ("Review this report before our meeting.", {"deep-critique"}),
            ("Review this report for style only.", {"deep-critique"}),
            ("Review this report for tone only.", {"deep-critique"}),
            ("Audit this document for grammar.", {"deep-critique"}),
            ("Critique this memo for spelling.", {"deep-critique"}),
            ("Challenge this plan for grammar.", {"deep-critique"}),
            ("Audit this document's grammar.", {"deep-critique"}),
            ("Critique this memo's spelling.", {"deep-critique"}),
            ("Challenge this plan's wording.", {"deep-critique"}),
            ("Critique the PRD's spelling.", {"deep-critique"}),
            ("Audit the policy's grammar.", {"deep-critique"}),
            ("Audit grammatical errors in this document.", {"deep-critique"}),
            ("Audit document grammar.", {"deep-critique"}),
            ("Critique memo spelling.", {"deep-critique"}),
            ("Stress-test report punctuation.", {"deep-critique"}),
            (
                "Audit grammar and grammatical errors in this document.",
                {"deep-critique"},
            ),
            ("Only critique this memo's spelling.", {"deep-critique"}),
            ("Just critique this memo's spelling.", {"deep-critique"}),
            ("Please only critique this memo's spelling.", {"deep-critique"}),
            ("Audit the grammar in this document.", {"deep-critique"}),
            ("Critique the spelling of this memo.", {"deep-critique"}),
            ("Challenge the wording in this plan.", {"deep-critique"}),
            ("Stress-test the punctuation in this memo.", {"deep-critique"}),
            (
                "Review this plan critically. Actually, proofread it for grammar only.",
                {"deep-critique"},
            ),
            (
                "What do you think about this plan? On second thought, just fix spelling.",
                {"deep-critique"},
            ),
            ("Critique this memo. Actually, summarize it.", {"deep-critique"}),
            (
                "Review this plan for grammar only and do not critique its assumptions.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and only summarize it.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and instead summarize it.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and please instead proofread it.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and only summarize the section that also covers revenue.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and instead summarize the section that also covers revenue.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions. Actually, only summarize the part that also discusses costs.",
                {"deep-critique"},
            ),
            (
                "Critique this memo. Actually, proofread it because it is too long.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions. Instead, proofread it too.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions. Actually, instead proofread it too.",
                {"deep-critique"},
            ),
            ("Correct the grammar and then review it.", {"deep-critique"}),
            ("Correct the grammar and then audit it.", {"deep-critique"}),
            ("Correct the grammar and then compare wording.", {"deep-critique"}),
            ("Proofread this memo and then critique the spelling.", {"deep-critique"}),
            ("Correct the grammar and then challenge the wording.", {"deep-critique"}),
            ("Correct the grammar and then critique its punctuation.", {"deep-critique"}),
            (
                "Critique this memo for weak assumptions and instead critique its spelling.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and only critique its spelling.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and then only challenge its wording.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and instead audit its grammar.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions. Only critique its spelling.",
                {"deep-critique"},
            ),
            (
                "Critique weak assumptions and then only carefully audit its grammar.",
                {"deep-critique"},
            ),
            ("Audit the grammatical errors in this document.", {"deep-critique"}),
            (
                "Critique the grammar and also the spelling in this memo.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and then critique only its spelling.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and instead critique only its spelling.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and then review only its grammar.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and then challenge just its wording.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions. Then critique only its spelling.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and then critique its spelling only.",
                {"deep-critique"},
            ),
            ("Review this strategy for grammar only.", {"deep-critique"}),
            (
                "Review my security policy for grammar only.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Critique my security policy for grammar only.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Audit authentication documentation for spelling only.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review this repository architecture for grammar only.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Critique this repository architecture for spelling only.",
                {"deep-critique", "coding-architecture-review"},
            ),
            ("Audit module boundaries for wording only.", {"deep-critique"}),
            (
                "Review authorization wording only.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review my security policy grammar only.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Critique my security policy's grammar only.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review dependency direction punctuation only.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Review this repository architecture's spelling only.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Audit authentication documentation spelling.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Critique my security policy's grammar.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review repository architecture spelling.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Critique this repository architecture's punctuation.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Review codebase architecture spelling.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Review module architecture wording.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Review access control wording.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review RLS policy grammar.",
                {"deep-critique", "security-best-practices-review"},
            ),
            ("What do you think about this document's grammar?", {"deep-critique"}),
            ("Is this memo's wording correct?", {"deep-critique"}),
            (
                "What do you think about this repository architecture's spelling?",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Should we use this supplied PDF for grammar?",
                {"deep-critique", "critical-pdf-review"},
            ),
            (
                "Review the word authorization.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review the phrase authentication architecture.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Audit the wording of a sentence about access control.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Critique the term repository architecture.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Review a history essay that mentions repository architecture.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Review security policy grammar and spelling only.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review security policy grammar & spelling only.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review repository architecture grammar and spelling only.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Review repository architecture grammar & spelling only.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "Review codebase architecture spelling along with grammar only.",
                {"deep-critique", "coding-architecture-review"},
            ),
            (
                "What do you think about this security policy grammar and spelling only?",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review security architecture for grammar.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Audit security architecture spelling.",
                {"deep-critique", "security-best-practices-review"},
            ),
            ("Review system architecture for spelling.", {"deep-critique"}),
            (
                "Review authorization policy spelling.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review access control documentation grammar.",
                {"deep-critique", "security-best-practices-review"},
            ),
            ("Review the argument's grammar and spelling only.", {"deep-critique"}),
            ("Review the strategy's wording and grammar only.", {"deep-critique"}),
            ("Review the assumptions' wording and spelling only.", {"deep-critique"}),
            ("Review the phrase weak assumptions.", {"deep-critique"}),
            ("Critique the term weak assumptions.", {"deep-critique"}),
            ("Review the phrase flawed logic.", {"deep-critique"}),
            ("Review grammar only, not logic.", {"deep-critique"}),
            (
                "Review this repository architecture for spelling only, not coupling.",
                {"deep-critique", "coding-architecture-review"},
            ),
            ("Critique the argument for spelling only.", {"deep-critique"}),
            ("Review the conclusions for wording only.", {"deep-critique"}),
            (
                "Critique this memo for weak assumptions and summarize it instead.",
                {"deep-critique"},
            ),
            (
                "Critique this memo for weak assumptions and proofread its grammar instead.",
                {"deep-critique"},
            ),
            ("Critique this plan. Do not ever critique it.", {"deep-critique"}),
            ("Critique this plan. Never again critique it.", {"deep-critique"}),
            ("Critique this plan. Avoid critiquing it.", {"deep-critique"}),
            (
                "Critique this plan. Without critiquing it, summarize it.",
                {"deep-critique"},
            ),
            ("Critique this plan. Rather, summarize it.", {"deep-critique"}),
            (
                "Review my security policy for missing authorization controls. Actually, proofread it for grammar only.",
                {"deep-critique", "security-best-practices-review"},
            ),
            (
                "Review this repository architecture for flawed dependency direction. Instead, summarize it.",
                {"deep-critique", "coding-architecture-review"},
            ),
            ("Critique the strategy's grammar.", {"deep-critique"}),
            ("Critique the argument's spelling.", {"deep-critique"}),
            ("Audit the conclusions' wording.", {"deep-critique"}),
            ("Critique grammar in this strategy.", {"deep-critique"}),
            ("Critique the grammar of this argument.", {"deep-critique"}),
            ("What do you think about this grammar?", {"deep-critique"}),
            (
                "Critique assumptions and then audit grammar only, please.",
                {"deep-critique"},
            ),
            (
                "Critique assumptions and then audit grammar just, if possible.",
                {"deep-critique"},
            ),
            ("Review the PDF parser.", {"pdf-file-analysis", "critical-pdf-review"}),
            (
                "Compare this document with the previous version and list changes.",
                {"deep-critique", "strategy-options-war-game"},
            ),
            (
                "Compare option A with option B and list wording differences.",
                {"strategy-options-war-game", "deep-critique"},
            ),
            (
                "Compare business option A with option B and list wording differences.",
                {"strategy-options-war-game", "deep-critique"},
            ),
            (
                "Evaluate option A and option B for spelling.",
                {"strategy-options-war-game", "deep-critique"},
            ),
            (
                "Analyze the grammar of the phrase pricing strategy.",
                {"strategy-pricing-analysis"},
            ),
            (
                "Create a price tiers component in React.",
                {"strategy-pricing-analysis"},
            ),
            (
                "Design an accountability dashboard.",
                {"strategy-operating-model-design"},
            ),
            (
                "Design team interfaces in React.",
                {"strategy-operating-model-design"},
            ),
            (
                "Structure this unresolved software problem and tell me what to investigate first.",
                {"strategy-situation-assessment"},
            ),
            (
                "Do not use $pricing-strategy. Summarize the proposal only.",
                {"strategy-pricing-analysis"},
            ),
            (
                "Analyze pricing strategy in this history essay.",
                {"strategy-pricing-analysis"},
            ),
            (
                "Create a pricing plan for household chores.",
                {"strategy-pricing-analysis"},
            ),
            (
                "A memo mentions Van Westendorp analysis.",
                {"strategy-pricing-analysis"},
            ),
            (
                "Do not run a Van Westendorp analysis. Summarize the memo only.",
                {"strategy-pricing-analysis"},
            ),
            (
                "Design accountability for household chores.",
                {"strategy-operating-model-design"},
            ),
            (
                "Establish accountability in a board game.",
                {"strategy-operating-model-design"},
            ),
            (
                "Map team interfaces in a sports game.",
                {"strategy-operating-model-design"},
            ),
            (
                "Clarify decision rights for a family game night.",
                {"strategy-operating-model-design"},
            ),
        ]
        for prompt, forbidden_rules in rejected_routes:
            with self.subTest(prompt=prompt):
                rule = first_rule(prompt)
                self.assertNotIn(rule["id"] if rule else None, forbidden_rules)

        critique_state_cases = [
            ("Summarize this plan and also critique its weak assumptions.", True),
            ("Summarize this report and instead audit the conclusions.", True),
            ("Critique grammar and strategy in this memo.", True),
            ("Critique the proposal's argument and the memo's grammar.", True),
            ("Critique weak assumptions, but do not critique spelling.", True),
            ("Critique the strategy's grammar.", False),
            ("Critique the grammar of this argument.", False),
            ("What do you think about this grammar?", False),
            (
                "Critique assumptions and then audit grammar only, please.",
                False,
            ),
            (
                "Critique assumptions and then audit grammar just, if possible.",
                False,
            ),
            ("Review this strategy for grammar only.", False),
            (
                "Critique this memo for weak assumptions and summarize it instead.",
                False,
            ),
            ("Critique this plan. Avoid critiquing it.", False),
            ("Is this business case defensible?", True),
            ("Critique this plan. Rather, summarize it.", False),
            (
                "Summarize this plan. Rather, critique its weak assumptions.",
                True,
            ),
            (
                "Critique this memo for weak assumptions and then critique only its spelling.",
                False,
            ),
            (
                "Critique weak assumptions and also critique grammar only.",
                True,
            ),
            ("Audit authentication documentation spelling.", False),
            ("Critique my security policy's grammar.", False),
            ("Review repository architecture spelling.", False),
            (
                "Critique this repository architecture's punctuation.",
                False,
            ),
            ("Review assumptions, logic, wording only.", True),
            ("Critique security flaws, grammar only.", True),
            ("Critique weak assumptions along with grammar only.", True),
            ("Critique weak assumptions together with grammar only.", True),
            ("Critique weak assumptions & grammar only.", True),
            ("Review codebase architecture spelling.", False),
            ("Review module architecture wording.", False),
            ("Review access control wording.", False),
            ("Review RLS policy grammar.", False),
            ("What do you think about this document's grammar?", False),
            ("Is this memo's wording correct?", False),
            (
                "What do you think about this repository architecture's spelling?",
                False,
            ),
            ("Should we use this supplied PDF for grammar?", False),
            ("Review the word authorization.", False),
            ("Review the phrase authentication architecture.", False),
            ("Audit the wording of a sentence about access control.", False),
            ("Critique the term repository architecture.", False),
            (
                "Review a history essay that mentions repository architecture.",
                False,
            ),
            ("Review security policy grammar and spelling only.", False),
            ("Review security policy grammar & spelling only.", False),
            ("Review repository architecture grammar and spelling only.", False),
            ("Review repository architecture grammar & spelling only.", False),
            (
                "Review codebase architecture spelling along with grammar only.",
                False,
            ),
            (
                "What do you think about this security policy grammar and spelling only?",
                False,
            ),
            (
                "Review a history essay that mentions repository architecture for weak assumptions.",
                True,
            ),
            (
                "Critique the argument in a history essay that mentions repository architecture.",
                True,
            ),
            ("Critique a sentence about access control for flawed logic.", True),
            ("Review an essay about market strategy for weak assumptions.", True),
        ]
        for prompt, expected in critique_state_cases:
            with self.subTest(critique_state_prompt=prompt):
                polarity, mature, _ = index._prompt_critique_state(prompt)
                self.assertIs(polarity is True and mature, expected)

    def test_critique_event_parser_generated_boundary_matrix(self) -> None:
        policy = json.loads(
            (ROUTING_ROOT / "routing-policy.yaml").read_text(encoding="utf-8")
        )

        def first_rule(prompt: str) -> dict[str, object] | None:
            return next(
                (
                    rule
                    for rule in policy["rules"]
                    if index._rule_matches_prompt(rule, prompt.lower(), policy)
                ),
                None,
            )

        def assert_mature(prompt: str, expected: bool) -> None:
            polarity, mature, _ = index._prompt_critique_state(prompt)
            self.assertIs(polarity is True and mature, expected)

        cases_run = 0
        actions = (
            "Review",
            "Critique",
            "Audit",
            "Challenge",
            "Validate",
            "Stress-test",
            "Compare",
        )
        targets = (
            "this proposal",
            "my security policy",
            "this repository architecture",
            "the supplied PDF",
        )
        text_scopes = (
            "grammar only",
            "spelling only",
            "wording only",
            "punctuation only",
            "tone only",
        )
        critique_routes = {
            "deep-critique",
            "security-best-practices-review",
            "coding-architecture-review",
            "critical-pdf-review",
        }
        for action in actions:
            for target in targets:
                for scope in text_scopes:
                    prompt = f"{action} {target} for {scope}."
                    with self.subTest(family="explicit-text-only", prompt=prompt):
                        assert_mature(prompt, False)
                        rule = first_rule(prompt)
                        self.assertNotIn(rule["id"] if rule else None, critique_routes)
                    cases_run += 1

        domain_terms = (
            "authorization",
            "authentication",
            "access control",
            "repository architecture",
        )
        linguistic_dimensions = ("grammar", "spelling", "capitalization", "wording")
        for action in actions:
            for domain in domain_terms:
                for dimension in linguistic_dimensions:
                    prompt = f"{action} the {dimension} of {domain} only."
                    with self.subTest(family="linguistic-domain", prompt=prompt):
                        assert_mature(prompt, False)
                        rule = first_rule(prompt)
                        self.assertNotIn(rule["id"] if rule else None, critique_routes)
                    cases_run += 1

        substantive_criteria = (
            "weak assumptions",
            "flawed logic",
            "accuracy",
            "missing authorization",
            "coupling",
            "module boundaries",
        )
        mixed_text = ("grammar", "spelling", "wording")
        for action in actions:
            for criterion in substantive_criteria:
                for dimension in mixed_text:
                    prompt = (
                        f"{action} this proposal for {criterion} and {dimension}."
                    )
                    with self.subTest(family="mixed-positive", prompt=prompt):
                        assert_mature(prompt, True)
                    cases_run += 1

        exclusion_scopes = ("grammar only", "spelling only", "wording only")
        excluded_criteria = ("assumptions", "logic", "coupling", "module boundaries")
        exclusion_templates = (
            "{scope}, not {criterion}",
            "{scope}, excluding {criterion}",
            "{scope} and do not analyze {criterion}",
        )
        for action in actions:
            for scope in exclusion_scopes:
                for criterion in excluded_criteria:
                    for template in exclusion_templates:
                        criteria = template.format(scope=scope, criterion=criterion)
                        prompt = f"{action} this proposal for {criteria}."
                        with self.subTest(family="explicit-exclusion", prompt=prompt):
                            assert_mature(prompt, False)
                        cases_run += 1

        precedence_cases = (
            ("Critique weak assumptions. Do not critique them.", False),
            ("Do not critique this plan. Critique its weak assumptions.", True),
            ("Critique weak assumptions and proofread it too.", True),
            ("Critique weak assumptions and also proofread it.", True),
            ("Critique weak assumptions. Instead, proofread it.", False),
            ("Critique weak assumptions. Actually, summarize it only.", False),
            ("Critique weak assumptions. No critique, just summarize.", False),
            ("Critique weak assumptions. Don't do another critique, just summarize.", False),
            ("Do not summarize, critique weak assumptions instead.", True),
            ("Rather than summarize, critique weak assumptions.", True),
        )
        for prompt, expected in precedence_cases:
            with self.subTest(family="precedence", prompt=prompt):
                assert_mature(prompt, expected)
            cases_run += 1

        semantic_cases = (
            ("What do you think about that proposal?", "deep-critique"),
            ("Is our recommendation defensible?", "deep-critique"),
            ("Should we use the plan?", "deep-critique"),
            ("What do you think about that security policy?", "security-best-practices-review"),
            ("Is my authentication architecture secure?", "security-best-practices-review"),
            ("Should we use the security policy?", "security-best-practices-review"),
            ("What do you think about that repository architecture?", "coding-architecture-review"),
            ("Is our repository architecture sound?", "coding-architecture-review"),
            ("Should we use the repository architecture?", "coding-architecture-review"),
            ("What do you think about the attached PDF?", "critical-pdf-review"),
            ("Is the uploaded PDF accurate?", "critical-pdf-review"),
            ("Should we use the supplied PDF?", "critical-pdf-review"),
            ("Is report.pdf accurate?", "critical-pdf-review"),
            ("What do you think about this document's grammar?", None),
            ("Is this memo's wording correct?", None),
            ("Should we use this supplied PDF for grammar?", None),
            ("What do you think about the word authorization?", None),
            ("Is that proposal viable?", "deep-critique"),
            ("Is that security policy complete?", "security-best-practices-review"),
            ("Is that repository architecture consistent?", "coding-architecture-review"),
            ("Is the attached PDF credible?", "critical-pdf-review"),
        )
        for prompt, expected_rule in semantic_cases:
            with self.subTest(family="semantic-specialist", prompt=prompt):
                rule = first_rule(prompt)
                self.assertEqual(rule["id"] if rule else None, expected_rule)
            cases_run += 1

        masking_cases = (
            'Example: "Critique weak assumptions." Summarize this plan only.',
            "Example: ‘Critique weak assumptions.’ Summarize this plan only.",
            "Example: `Critique weak assumptions.` Summarize this plan only.",
            "Example:\n```text\nCritique weak assumptions.\n```\nSummarize this plan only.",
            "> Critique weak assumptions.\nSummarize this plan only.",
        )
        for prompt in masking_cases:
            with self.subTest(family="masking", prompt=prompt):
                assert_mature(prompt, False)
            cases_run += 1

        source_cases = (
            ("Do these sources support the claim?", "source-backed-critique"),
            ("Are these citations authentic?", "source-backed-critique"),
            ("Review the evidence chain for credibility and wording.", "source-backed-critique"),
            ("Review the evidence chain wording only.", None),
            ("Review citation formatting only.", None),
            ("Verify the sources. Actually, summarize them only.", None),
        )
        for prompt, expected_rule in source_cases:
            with self.subTest(family="source", prompt=prompt):
                rule = first_rule(prompt)
                self.assertEqual(rule["id"] if rule else None, expected_rule)
            cases_run += 1

        implementation_cases = (
            ("Deeply critique this code and implement the fix.", "coding-deep-critique-implementation"),
            ("Source-backed critique this patch and implement the fix.", "coding-source-critique-implementation"),
            ("Critique repository architecture for coupling, then implement the refactor.", "coding-deep-critique-implementation"),
            ("Review code grammar only and implement the fix.", "coding-project-lifecycle"),
            ("Do not critique the code. Implement the fix.", "coding-project-lifecycle"),
            ('Example: "Critique this code." Implement the fix.', "coding-project-lifecycle"),
        )
        for prompt, expected_rule in implementation_cases:
            with self.subTest(family="critique-implementation", prompt=prompt):
                rule = first_rule(prompt)
                self.assertEqual(rule["id"] if rule else None, expected_rule)
            cases_run += 1

        self.assertEqual(cases_run, 678)

    def test_critique_event_parser_metamorphic_equivalents(self) -> None:
        policy = json.loads(
            (ROUTING_ROOT / "routing-policy.yaml").read_text(encoding="utf-8")
        )

        def first_rule_id(prompt: str) -> str | None:
            rule = next(
                (
                    candidate
                    for candidate in policy["rules"]
                    if index._rule_matches_prompt(candidate, prompt.lower(), policy)
                ),
                None,
            )
            return str(rule["id"]) if rule else None

        semantic_cases = {
            "Should we use that proposal?": "deep-critique",
            "Should we use that security policy?": "security-best-practices-review",
            "Should we use that repository architecture?": "coding-architecture-review",
            "What do you think about that PDF?": "critical-pdf-review",
            "Is my PDF accurate?": "critical-pdf-review",
            "Should we use our PDF?": "critical-pdf-review",
            "Should we adopt this proposal?": "deep-critique",
            "Should we adopt this security policy?": "security-best-practices-review",
            "Should we adopt this repository architecture?": "coding-architecture-review",
            "Should we adopt this supplied PDF?": "critical-pdf-review",
            "Does this proposal look sound?": "deep-critique",
            "Does this security policy look sound?": "security-best-practices-review",
            "Does this repository architecture look sound?": "coding-architecture-review",
            "Does this PDF look sound?": "critical-pdf-review",
            "What weaknesses are in this proposal?": "deep-critique",
            "What weaknesses are in this security policy?": "security-best-practices-review",
            "What weaknesses are in this repository architecture?": "coding-architecture-review",
            "What weaknesses are in this PDF?": "critical-pdf-review",
            "Summarize these sources. Instead, verify whether they support the claim.": "source-backed-critique",
            "Rather than summarize, verify whether these sources support the claim.": "source-backed-critique",
            "Critique this code. Instead, implement the fix.": "coding-project-lifecycle",
            "Critique this code and also implement the fix.": "coding-deep-critique-implementation",
        }
        for prompt, expected_rule in semantic_cases.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

    def test_critique_event_parser_downstream_boundary_closure(self) -> None:
        policy = json.loads(
            (ROUTING_ROOT / "routing-policy.yaml").read_text(encoding="utf-8")
        )

        def first_rule_id(prompt: str) -> str | None:
            rule = next(
                (
                    candidate
                    for candidate in policy["rules"]
                    if index._rule_matches_prompt(candidate, prompt.lower(), policy)
                ),
                None,
            )
            return str(rule["id"]) if rule else None

        copyedit_questions = (
            "What flaws are in my proposal's grammar?",
            "What gaps are in that security policy's wording?",
            "What weaknesses are in our repository architecture's spelling?",
        )
        for prompt in copyedit_questions:
            with self.subTest(family="semantic-copyedit", prompt=prompt):
                self.assertEqual(index._prompt_critique_state(prompt)[:2], (None, False))
                self.assertIsNone(first_rule_id(prompt))

        for basename in (
            "report",
            "analysis",
            "plan",
            "memo",
            "policy",
            "strategy",
            "forecast",
        ):
            prompts = (
                f"What do you think about {basename}.pdf's wording?",
                f"Is {basename}.pdf's grammar correct?",
                f"Should we rely on {basename}.pdf for spelling only?",
            )
            for prompt in prompts:
                with self.subTest(family="pdf-filename-copyedit", prompt=prompt):
                    self.assertEqual(index._prompt_critique_state(prompt)[:2], (None, False))
                    self.assertIsNone(first_rule_id(prompt))

        source_cases = {
            "Summarize these sources, then instead verify whether they support the claim.":
                "source-backed-critique",
            "Verify these sources, then instead summarize them.": None,
            "Summarize these sources. Instead, verify whether they support the claim.":
                "source-backed-critique",
            "Review the sources for credibility.": "source-backed-critique",
            "Review citations for credibility and wording only.": "source-backed-critique",
            "Assess source credibility and grammar only.": "source-backed-critique",
            "Review the evidence chain for grammatical correctness only.": None,
        }
        for prompt, expected_rule in source_cases.items():
            with self.subTest(family="source-ordering", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

        implementation_cases = {
            "Critique this code, instead implement the fix.": "coding-project-lifecycle",
            "Critique this code. Implement the fix instead.": "coding-project-lifecycle",
            "Critique this code; Implement the fix instead.": "coding-project-lifecycle",
            "Implement the fix. Instead, critique this code.": "deep-critique",
            "Critique this code and also implement the fix.":
                "coding-deep-critique-implementation",
            "Review this code for grammatical correctness only and implement the typo fix.":
                "coding-project-lifecycle",
        }
        for prompt, expected_rule in implementation_cases.items():
            with self.subTest(family="implementation-ordering", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

        copyedit_only = (
            "Review this memo for grammatical correctness only.",
            "Is this memo grammatically correct?",
            "Review my security policy for grammatical correctness only.",
            "Is this security policy grammatically correct?",
            "Review this repository architecture for grammatical correctness only.",
            "Is this repository architecture grammatically correct?",
            "Review the supplied PDF for grammatical correctness only.",
            "Is this supplied PDF grammatically correct?",
        )
        for prompt in copyedit_only:
            with self.subTest(family="grammatical-correctness", prompt=prompt):
                polarity, mature, _ = index._prompt_critique_state(prompt)
                self.assertIn(polarity, (None, False))
                self.assertFalse(mature)
                self.assertIsNone(first_rule_id(prompt))

        security_mixed = (
            "Review access control design and wording only.",
            "Review authorization controls and grammar only.",
            "Review authentication flow and spelling only.",
            "Review RLS policies and grammar only.",
        )
        for prompt in security_mixed:
            with self.subTest(family="security-mixed", prompt=prompt):
                self.assertEqual(index._prompt_critique_state(prompt)[:2], (True, True))
                self.assertEqual(first_rule_id(prompt), "security-best-practices-review")

        word_document_cases = {
            "Critique this Word document, do not edit it.": "deep-critique",
            "Critique this Word file for weak assumptions.": "deep-critique",
            "Critique this Word report, do not edit it.": "deep-critique",
            "Critique this Word memo, do not edit it.": "deep-critique",
            "Critique the attached Word memo, do not edit it.": "deep-critique",
            "Critique my Word policy for weak assumptions.": "deep-critique",
            "Critique the Microsoft Word proposal for flawed logic.": "deep-critique",
            "Critique this Word document for grammar only.":
                "create-or-edit-word-document",
            "Critique this Word document. Instead, summarize it.":
                "create-or-edit-word-document",
            "Critique this Word document and also summarize it.": "deep-critique",
            "Critique the word authorization.": None,
            "Critique the word document as a verb.": None,
            "Critique the word report as a noun.": None,
            "Critique the word file in this sentence.": None,
            "Critique the word memo.": None,
            "Critique the word policy.": None,
            "Critique the phrase weak assumptions.": None,
            "Critique the phrase foo. Then create a PDF file.":
                "standard-pdf-work",
            "Critique the phrase foo and then create a Word document.":
                "create-or-edit-word-document",
            "Create a PDF file. Then critique the phrase foo.": None,
        }
        for prompt, expected_rule in word_document_cases.items():
            with self.subTest(family="word-document-vs-linguistic-mention", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)
                if "grammar only" in prompt.lower():
                    self.assertEqual(index._prompt_critique_state(prompt)[:2], (False, False))

        mention_cases = {
            "Critique the phrase supplied PDF for weak assumptions.": "deep-critique",
            "Review a history essay that mentions the supplied PDF for weak assumptions.":
                "deep-critique",
            "Critique a sentence about the attached PDF for flawed logic.": "deep-critique",
            "Review the supplied PDF about authentication for weak assumptions.":
                "critical-pdf-review",
            "Review the supplied PDF about repository architecture for weak assumptions.":
                "critical-pdf-review",
        }
        for prompt, expected_rule in mention_cases.items():
            with self.subTest(family="container-mention", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

        structural_scope_cases = {
            "Review this proposal for grammar plus commercial viability.": "deep-critique",
            "Critique this proposal for wording and strategic fit.": "deep-critique",
            "Audit this plan for tone and business impact.": "deep-critique",
            "Review authentication and grammar only.": "security-best-practices-review",
            "Review authorization and wording only.": "security-best-practices-review",
            "Review API permissions and grammar only.": "security-best-practices-review",
            "Review access control effectiveness and wording only.":
                "security-best-practices-review",
            "Review authentication robustness and spelling only.":
                "security-best-practices-review",
        }
        for prompt, expected_rule in structural_scope_cases.items():
            with self.subTest(family="structural-sibling", prompt=prompt):
                self.assertEqual(index._prompt_critique_state(prompt)[:2], (True, True))
                self.assertEqual(first_rule_id(prompt), expected_rule)

        multi_dot_cases = {
            "What do you think of security.review.final.pdf?": "critical-pdf-review",
            "Is Q3.board.pack.pdf credible?": "critical-pdf-review",
            "Review report.v3.final.pdf for grammar only.": None,
        }
        for prompt, expected_rule in multi_dot_cases.items():
            with self.subTest(family="multi-dot-filename", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

        quoted_example_cases = {
            'Example: "Critique the security policy for flaws." Now critique this memo for weak assumptions.':
                "deep-critique",
            'The prior request was "Critique this policy." Now critique this memo for weak assumptions.':
                "deep-critique",
            'Example: "Implement the fix." Critique this code.': "deep-critique",
            "Example:\n```text\nImplement the fix.\n```\nCritique this code.": "deep-critique",
        }
        for prompt, expected_rule in quoted_example_cases.items():
            with self.subTest(family="example-boundary", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

        scope_alternative_cases = {
            "Review grammar instead of weak assumptions.": None,
            "Review grammar rather than weak assumptions.": None,
            "Review the security policy's grammar instead of authorization controls.": None,
            "Review repository architecture spelling instead of coupling.": None,
            "Review supplied PDF grammar instead of weak assumptions.": None,
            "Review the sources for grammar instead of credibility.": None,
            "Review weak assumptions instead of grammar.": "deep-critique",
            "Review authorization controls rather than grammar.":
                "security-best-practices-review",
        }
        for prompt, expected_rule in scope_alternative_cases.items():
            with self.subTest(family="scope-alternative", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

        implementation_object_cases = {
            "Critique this code and implement caching instead of recomputing.":
                "coding-deep-critique-implementation",
            "Critique this code, then implement a fallback instead of retrying.":
                "coding-deep-critique-implementation",
            "Implement the fix and critique fallback behavior instead of retry behavior.":
                "coding-deep-critique-implementation",
            "Review this security policy for grammatical correctness only and implement the typo fix.":
                "coding-project-lifecycle",
            "Review authentication documentation for spelling only and implement the typo fix.":
                "coding-project-lifecycle",
        }
        for prompt, expected_rule in implementation_object_cases.items():
            with self.subTest(family="implementation-object", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

        expanded_copyedit = (
            "Is this memo grammatically sound?",
            "Is this memo grammatically accurate?",
            "Is this security policy grammatically sound?",
            "Is this repository architecture grammatically accurate?",
            "Is this supplied PDF grammatically sound?",
        )
        for prompt in expanded_copyedit:
            with self.subTest(family="copyedit-morphology", prompt=prompt):
                self.assertIsNone(first_rule_id(prompt))

        expanded_container_mentions = {
            "Review the supplied PDF regarding authentication for weak assumptions.":
                "critical-pdf-review",
            "Review the supplied PDF that discusses authentication for weak assumptions.":
                "critical-pdf-review",
            "Review the supplied PDF mentioning authentication for weak assumptions.":
                "critical-pdf-review",
            "Review the supplied PDF with a section on authentication for weak assumptions.":
                "critical-pdf-review",
            "What do you think about the supplied PDF that discusses repository architecture?":
                "critical-pdf-review",
        }
        for prompt, expected_rule in expanded_container_mentions.items():
            with self.subTest(family="container-subject-syntax", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

        expanded_source_actions = {
            "Review evidence quality and grammar only.": "source-backed-critique",
            "Check source credibility and spelling only.": "source-backed-critique",
            "Evaluate the sources for reliability and wording only.":
                "source-backed-critique",
        }
        for prompt, expected_rule in expanded_source_actions.items():
            with self.subTest(family="source-action", prompt=prompt):
                self.assertEqual(first_rule_id(prompt), expected_rule)

    def test_repository_port_provenance_hashes_are_current(self) -> None:
        provenance = json.loads(
            (ROUTING_ROOT / "provenance.json").read_text(encoding="utf-8")
        )
        repository_hashes = provenance["repository_port_sha256"]
        self.assertTrue(repository_hashes)
        for relative, expected_sha256 in repository_hashes.items():
            with self.subTest(path=relative):
                path = (REPO_ROOT / relative).resolve(strict=True)
                self.assertTrue(path.is_relative_to(REPO_ROOT))
                actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(expected_sha256, actual_sha256)

    def test_manifest_filters_inactive_and_state_artifacts(self) -> None:
        manifest_path = self.root / "active.json"
        payload = {
            "schema_version": "1.0",
            "generated_at": "2026-08-11T00:00:00Z",
            "snapshot_id": "synthetic",
            "freshness_status": "fresh",
            "source_hashes": {},
            "source_hashes_verified": True,
            "entries": [
                active_entry("skill:active-example"),
                {**active_entry("skill:inactive-example"), "state": "disabled"},
                {
                    **active_entry("state:catalogue"),
                    "kind": "state-artifact",
                },
            ],
        }
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(index, "_source_hash_mismatches", return_value=[]):
            loaded = index.load_active_capabilities(manifest_path)
        self.assertEqual(loaded["summary"]["active_entries"], 1)
        self.assertEqual(loaded["summary"]["rejected_inactive"], 1)
        self.assertEqual(loaded["summary"]["rejected_state_artifacts"], 1)

    def test_authority_hash_mismatch_fails_closed(self) -> None:
        manifest_path = self.root / "active.json"
        payload = {
            "schema_version": "1.0",
            "generated_at": "2026-08-11T00:00:00Z",
            "snapshot_id": "synthetic",
            "freshness_status": "fresh",
            "source_hashes": {"routing-policy.yaml": "0" * 64},
            "source_hashes_verified": True,
            "entries": [active_entry("skill:active-example")],
        }
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(
            index, "_source_hash_mismatches", return_value=["routing-policy.yaml"]
        ):
            loaded = index.load_active_capabilities(manifest_path)
        self.assertEqual(loaded["freshness_status"], "stale")
        self.assertFalse(loaded["source_hashes_verified"])
        self.assertEqual(loaded["entries"], [])

    def test_plugin_drift_quarantines_only_recorded_package_closure(self) -> None:
        manifest_path = self.root / "plugin-drift.json"
        baseline_rows = {
            "ROOT\tmarket/alpha/1.0.0": "A" * 64,
            "FILE\tmarket/alpha/1.0.0/.codex-plugin/plugin.json": "B" * 64,
            "ROOT\tmarket/beta/1.0.0": "C" * 64,
            "FILE\tmarket/beta/1.0.0/.codex-plugin/plugin.json": "D" * 64,
        }
        payload = {
            "schema_version": "1.2",
            "generated_at": "2026-08-14T00:00:00Z",
            "snapshot_id": "selective-quarantine",
            "freshness_status": "fresh",
            "source_hashes": {
                "plugin-cache-inventory": "1" * 64,
            },
            "authority_receipt": {
                "config_projection_sha256": "2" * 64,
                "config_leaf_hashes": {},
                "plugin_cache_inventory_sha256": "1" * 64,
                "plugin_cache_row_hashes": baseline_rows,
                "plugin_capability_surfaces": {
                    "market/alpha/1.0.0": [
                        {"id": "skill:alpha", "kind": "skill"}
                    ],
                    "market/beta/1.0.0": [
                        {"id": "skill:beta", "kind": "skill"}
                    ],
                },
            },
            "entries": [active_entry("skill:alpha"), active_entry("skill:beta")],
        }
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        current_rows = {
            "ROOT\tmarket/alpha/1.1.0": "E" * 64,
            "FILE\tmarket/alpha/1.1.0/.codex-plugin/plugin.json": "F" * 64,
            "ROOT\tmarket/beta/1.0.0": "C" * 64,
            "FILE\tmarket/beta/1.0.0/.codex-plugin/plugin.json": "D" * 64,
        }
        with mock.patch.object(
            index, "_source_hash_mismatches", return_value=["plugin-cache-inventory"]
        ), mock.patch.object(
            index,
            "_plugin_cache_row_hashes",
            return_value=(current_rows, "9" * 64),
        ):
            loaded = index.load_active_capabilities(manifest_path)

        self.assertEqual(loaded["freshness_status"], "degraded")
        self.assertTrue(loaded["source_hashes_verified"])
        self.assertEqual([entry["id"] for entry in loaded["entries"]], ["skill:beta"])
        self.assertEqual(
            loaded["dynamic_authority"]["quarantined_capability_ids"],
            ["skill:alpha"],
        )
        self.assertEqual(loaded["summary"]["rejected_quarantined"], 1)

    def test_plugin_inventory_detects_same_size_same_mtime_content_drift(self) -> None:
        package = (
            self.codex_home
            / "plugins"
            / "cache"
            / "market"
            / "alpha"
            / "1.0.0"
        )
        manifest = package / ".codex-plugin" / "plugin.json"
        skill = package / "skills" / "alpha" / "SKILL.md"
        manifest.parent.mkdir(parents=True)
        skill.parent.mkdir(parents=True)
        manifest.write_text('{"name":"alpha","version":"1.0.0"}', encoding="utf-8")
        skill.write_text("first-byte-contract", encoding="utf-8")
        before_stat = skill.stat()
        first = index._plugin_cache_inventory_hash(self.codex_home)

        skill.write_text("other-byte-contract", encoding="utf-8")
        os.utime(skill, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
        second = index._plugin_cache_inventory_hash(self.codex_home)

        self.assertEqual(skill.stat().st_size, before_stat.st_size)
        self.assertNotEqual(first, second)

    def test_entry_hash_verification_enforces_exact_hash_scope(self) -> None:
        source = self.root / "entry-source.txt"
        source.write_text("source contract", encoding="utf-8")
        original_stat = source.stat()
        file_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        url = "https://developers.openai.com/mcp"
        url_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()

        self.assertTrue(
            index._entry_hash_current(
                {
                    "source_path": str(source.resolve()),
                    "sha256": file_digest,
                    "hash_scope": "file-sha256",
                }
            )
        )
        self.assertTrue(
            index._entry_hash_current(
                {
                    "source_path": str(source.resolve()),
                    "sha256": file_digest,
                    "hash_scope": "",
                }
            )
        )
        self.assertTrue(
            index._entry_hash_current(
                {
                    "source_path": url,
                    "sha256": url_digest,
                    "hash_scope": "text-sha256",
                }
            )
        )

        source.write_text("mutate contract", encoding="utf-8")
        os.utime(
            source,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        self.assertEqual(source.stat().st_size, original_stat.st_size)
        invalid_entries = (
            {
                "source_path": str(source.resolve()),
                "sha256": file_digest,
                "hash_scope": "file-sha256",
            },
            {
                "source_path": url,
                "sha256": "0" * 64,
                "hash_scope": "text-sha256",
            },
            {
                "source_path": f"{url}/changed",
                "sha256": url_digest,
                "hash_scope": "text-sha256",
            },
            {
                "source_path": url,
                "sha256": url_digest,
                "hash_scope": "file-sha256",
            },
            {
                "source_path": "relative/source.txt",
                "sha256": file_digest,
                "hash_scope": "file-sha256",
            },
            {
                "source_path": "/unsupported/windows/path",
                "sha256": file_digest,
                "hash_scope": "file-sha256",
            },
            {
                "source_path": r"\\server\share\missing.txt",
                "sha256": file_digest,
                "hash_scope": "file-sha256",
            },
            {
                "source_path": str(self.root / "missing.txt"),
                "sha256": file_digest,
                "hash_scope": "file-sha256",
            },
        )
        for entry in invalid_entries:
            with self.subTest(entry=entry):
                self.assertFalse(index._entry_hash_current(entry))

        symlink = self.root / "entry-source-link.txt"
        try:
            symlink.symlink_to(source)
        except OSError:
            pass
        else:
            self.assertFalse(
                index._entry_hash_current(
                    {
                        "source_path": str(symlink),
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "hash_scope": "file-sha256",
                    }
                )
            )

    def test_plugin_install_metadata_backfill_does_not_change_authority(self) -> None:
        plugin_root = (
            self.codex_home
            / "plugins"
            / "cache"
            / "openai-curated-remote"
            / "alpha"
        )
        package = plugin_root / "1.0.0"
        manifest = package / ".codex-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"name":"alpha","version":"1.0.0"}', encoding="utf-8")
        first = index._plugin_cache_inventory_hash(self.codex_home)

        # The first-party Codex sync currently writes this schema-v1 receipt at
        # the plugin root, alongside version directories.
        (plugin_root / ".codex-remote-plugin-install.json").write_text(
            '{"schema_version":1,"remote_plugin_id":"plugins~alpha"}',
            encoding="utf-8",
        )
        second = index._plugin_cache_inventory_hash(self.codex_home)

        # A future version-local receipt must also remain outside the bounded
        # routing-authority file set.
        (package / ".codex-remote-plugin-install.json").write_text(
            '{"schema_version":2,"remote_plugin_id":"plugins~alpha"}',
            encoding="utf-8",
        )
        third = index._plugin_cache_inventory_hash(self.codex_home)

        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_unproven_dynamic_dependency_closure_fails_globally_closed(self) -> None:
        manifest_path = self.root / "malformed-closure.json"
        payload = {
            "schema_version": "1.2",
            "generated_at": "2026-08-14T00:00:00Z",
            "snapshot_id": "malformed-closure",
            "freshness_status": "fresh",
            "source_hashes": {"plugin-cache-inventory": "1" * 64},
            "authority_receipt": {
                "config_projection_sha256": "2" * 64,
                "config_leaf_hashes": {},
                "plugin_cache_inventory_sha256": "1" * 64,
                "plugin_cache_row_hashes": {
                    "ROOT\tmarket/alpha/1.0.0": "A" * 64
                },
                "plugin_capability_surfaces": {},
            },
            "entries": [active_entry("skill:alpha")],
        }
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(
            index, "_source_hash_mismatches", return_value=["plugin-cache-inventory"]
        ):
            loaded = index.load_active_capabilities(manifest_path)
        self.assertEqual(loaded["freshness_status"], "stale")
        self.assertFalse(loaded["source_hashes_verified"])
        self.assertEqual(loaded["dynamic_authority_status"], "unscoped")
        self.assertEqual(loaded["entries"], [])

    def test_known_app_config_drift_quarantines_only_affected_surfaces(self) -> None:
        manifest_path = self.root / "app-config-drift.json"
        changed_leaf = "/mcp_servers/node_repl/runtime/command"
        payload = {
            "schema_version": "1.2",
            "generated_at": "2026-08-14T00:00:00Z",
            "snapshot_id": "app-config-drift",
            "freshness_status": "fresh",
            "source_hashes": {
                index.CONFIG_CAPABILITY_SOURCE_HASH_KEY: "1" * 64,
            },
            "authority_receipt": {
                "config_projection_sha256": "1" * 64,
                "config_leaf_hashes": {changed_leaf: "A" * 64},
                "config_capability_surfaces": {
                    changed_leaf: {
                        "change_class": "runtime_identity",
                        "control_kind": "app_runtime",
                        "control_key": "node_repl",
                        "capability_ids": ["plugin:browser", "mcp:node_repl"],
                        "required_capability_ids": ["mcp:node_repl"],
                    }
                },
                "plugin_cache_inventory_sha256": "2" * 64,
                "plugin_cache_row_hashes": {
                    "ROOT\topenai-bundled/browser/1.0.0": "B" * 64
                },
                "plugin_capability_surfaces": {
                    "openai-bundled/browser/1.0.0": [
                        {"id": "plugin:browser", "kind": "plugin"}
                    ]
                },
            },
            "entries": [
                {**active_entry("plugin:browser"), "kind": "plugin"},
                {**active_entry("mcp:node_repl"), "kind": "mcp"},
                active_entry("skill:unaffected"),
            ],
        }
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(
            index,
            "_source_hash_mismatches",
            return_value=[index.CONFIG_CAPABILITY_SOURCE_HASH_KEY],
        ), mock.patch.object(
            index,
            "_capability_config_authority",
            return_value={
                "sha256": "9" * 64,
                "projection_leaf_hashes": {changed_leaf: "C" * 64},
            },
        ):
            loaded = index.load_active_capabilities(manifest_path)
        self.assertEqual(loaded["freshness_status"], "degraded")
        self.assertEqual(
            [entry["id"] for entry in loaded["entries"]],
            ["skill:unaffected"],
        )
        self.assertEqual(
            set(loaded["dynamic_authority"]["quarantined_capability_ids"]),
            {"plugin:browser", "mcp:node_repl"},
        )

    def test_worker_bom_drift_disables_workers_without_disabling_router(self) -> None:
        manifest_path = self.root / "worker-bom-drift.json"
        payload = {
            "schema_version": "1.2",
            "generated_at": "2026-08-14T00:00:00Z",
            "snapshot_id": "worker-bom-drift",
            "freshness_status": "fresh",
            "source_hashes": {index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY: "1" * 64},
            "authority_receipt": {
                "config_projection_sha256": "2" * 64,
                "config_leaf_hashes": {},
                "plugin_cache_inventory_sha256": "3" * 64,
                "plugin_cache_row_hashes": {
                    "ROOT\tmarket/alpha/1.0.0": "A" * 64
                },
                "plugin_capability_surfaces": {
                    "market/alpha/1.0.0": [
                        {"id": "skill:alpha", "kind": "skill"}
                    ]
                },
            },
            "entries": [active_entry("skill:alpha")],
        }
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(
            index,
            "_source_hash_mismatches",
            return_value=[index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY],
        ):
            loaded = index.load_active_capabilities(manifest_path)
        self.assertEqual(loaded["freshness_status"], "degraded")
        self.assertTrue(loaded["source_hashes_verified"])
        self.assertEqual(loaded["worker_runtime_bom_status"], "changed")
        self.assertEqual([entry["id"] for entry in loaded["entries"]], ["skill:alpha"])

    def test_authority_key_coverage_and_runtime_inventory_tamper_fail_closed(
        self,
    ) -> None:
        authority_root = self.root / "authority"
        authority_root.mkdir()
        source_paths: dict[str, Path] = {}
        source_hashes: dict[str, str] = {}
        for name in sorted(index.REQUIRED_MANIFEST_AUTHORITY_HASH_KEYS):
            if name == "plugin-cache-inventory":
                source_hashes[name] = "B" * 64
                continue
            if name == index.CONFIG_CAPABILITY_SOURCE_HASH_KEY:
                path = self.config_path
                digest = index.capability_config_fingerprint(path)
            else:
                path = authority_root / name.replace("/", "_")
                path.write_text(f"synthetic authority for {name}\n", encoding="utf-8")
                digest = index._sha256_file(path)
            source_paths[name] = path
            source_hashes[name] = digest

        with mock.patch.object(
            index, "_source_hash_path", side_effect=lambda name: source_paths.get(name)
        ), mock.patch.object(
            index, "_plugin_cache_inventory_hash", return_value="B" * 64
        ):
            self.assertEqual(index._source_hash_mismatches({"source_hashes": source_hashes}), [])

            for missing in sorted(index.REQUIRED_MANIFEST_AUTHORITY_HASH_KEYS):
                truncated = dict(source_hashes)
                truncated.pop(missing)
                with self.subTest(missing=missing):
                    mismatches = index._source_hash_mismatches(
                        {"source_hashes": truncated}
                    )
                    self.assertIn(f"source_hashes.missing:{missing}", mismatches)

            runtime_path = source_paths["capability_index.py"]
            runtime_path.write_text("synthetic runtime tamper\n", encoding="utf-8")
            self.assertIn(
                "capability_index.py",
                index._source_hash_mismatches({"source_hashes": source_hashes}),
            )

        with mock.patch.object(
            index, "_source_hash_path", side_effect=lambda name: source_paths.get(name)
        ), mock.patch.object(
            index, "_plugin_cache_inventory_hash", return_value="C" * 64
        ):
            self.assertIn(
                "plugin-cache-inventory",
                index._source_hash_mismatches({"source_hashes": source_hashes}),
            )

    def test_semantic_config_fingerprint_ignores_runtime_presentation_only(self) -> None:
        config = self.root / "config.toml"
        baseline = '[features]\nhooks = true\n[plugins."sample@example"]\nenabled = true\n'
        config.write_text(baseline, encoding="utf-8")
        first = index.capability_config_fingerprint(config)
        config.write_text(
            'model = "gpt-5.6-terra"\npersonality = "friendly"\n' + baseline,
            encoding="utf-8",
        )
        presentation_only = index.capability_config_fingerprint(config)
        config.write_text(
            '[features]\nhooks = false\n[plugins."sample@example"]\nenabled = true\n',
            encoding="utf-8",
        )
        capability_changed = index.capability_config_fingerprint(config)
        self.assertEqual(first, presentation_only)
        self.assertNotEqual(first, capability_changed)

    def test_config_projection_ignores_only_proven_non_routing_fields(self) -> None:
        config = self.root / "config.toml"
        baseline = """
[features]
hooks = true
js_repl = false
[marketplaces.openai-bundled]
last_updated = 2026-08-14T10:00:00Z
source_type = "local"
source = "C:\\\\bundle"
[apps.connector_example.tools."github.create_branch"]
approval_mode = "approve"
[hooks.state.router]
trusted_hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"""
        config.write_text(baseline, encoding="utf-8")
        first = index.capability_config_fingerprint(config)
        config.write_text(
            baseline.replace("10:00:00Z", "11:00:00Z").replace(
                'approval_mode = "approve"', 'approval_mode = "never"'
            ),
            encoding="utf-8",
        )
        non_routing = index.capability_config_fingerprint(config)
        self.assertEqual(first, non_routing)

        config.write_text(
            baseline.replace(
                'approval_mode = "approve"',
                'approval_mode = "approve"\nenabled = false',
            ),
            encoding="utf-8",
        )
        self.assertNotEqual(first, index.capability_config_fingerprint(config))

        config.write_text(
            baseline.replace("sha256:aaaa", "sha256:bbbb").replace(
                'source = "C:\\\\bundle"', 'source = "C:\\\\other-bundle"'
            ),
            encoding="utf-8",
        )
        self.assertEqual(first, index.capability_config_fingerprint(config))

        config.write_text(
            baseline + "\n[hooks.inline_router]\nenabled = true\n",
            encoding="utf-8",
        )
        self.assertNotEqual(first, index.capability_config_fingerprint(config))

    def test_unclassified_feature_fails_config_authority_closed(self) -> None:
        config = self.root / "config.toml"
        config.write_text("[features]\nfuture_unknown = true\n", encoding="utf-8")
        with self.assertRaises(index.CapabilityDataError):
            index.capability_config_fingerprint(config)

    def test_current_app_capability_gates_are_projected_but_approvals_are_not(
        self,
    ) -> None:
        config = self.root / "config.toml"
        baseline = """
[apps.connector_example]
enabled = true
approvals_reviewer = "user"
default_tools_approval_mode = "approve"
default_tools_enabled = true
destructive_enabled = false
open_world_enabled = false
[apps.connector_example.tools.read]
approval_mode = "approve"
enabled = true
"""
        config.write_text(baseline, encoding="utf-8")
        first = index.capability_config_fingerprint(config)
        config.write_text(
            baseline.replace(
                'approvals_reviewer = "user"', 'approvals_reviewer = "admin"'
            )
            .replace(
                'default_tools_approval_mode = "approve"',
                'default_tools_approval_mode = "never"',
            )
            .replace('approval_mode = "approve"', 'approval_mode = "never"'),
            encoding="utf-8",
        )
        self.assertEqual(first, index.capability_config_fingerprint(config))

        for source, replacement in (
            ("default_tools_enabled = true", "default_tools_enabled = false"),
            ("destructive_enabled = false", "destructive_enabled = true"),
            ("open_world_enabled = false", "open_world_enabled = true"),
        ):
            with self.subTest(gate=source.split(" =", 1)[0]):
                config.write_text(
                    baseline.replace(source, replacement), encoding="utf-8"
                )
                self.assertNotEqual(first, index.capability_config_fingerprint(config))

        config.write_text(
            baseline.replace(
                "destructive_enabled = false", 'destructive_enabled = "no"'
            ),
            encoding="utf-8",
        )
        with self.assertRaises(index.CapabilityDataError):
            index.capability_config_fingerprint(config)

    def test_mcp_tool_enablement_is_projected_but_approval_mode_is_not(self) -> None:
        config = self.root / "config.toml"
        baseline = """
[mcp_servers.sample]
enabled = true
command = "sample.exe"
[mcp_servers.sample.tools.read]
approval_mode = "approve"
enabled = true
"""
        config.write_text(baseline, encoding="utf-8")
        first = index.capability_config_fingerprint(config)
        config.write_text(
            baseline.replace('approval_mode = "approve"', 'approval_mode = "never"'),
            encoding="utf-8",
        )
        self.assertEqual(first, index.capability_config_fingerprint(config))

        config.write_text(
            baseline.replace(
                'approval_mode = "approve"\nenabled = true',
                'approval_mode = "approve"\nenabled = false',
            ),
            encoding="utf-8",
        )
        self.assertNotEqual(first, index.capability_config_fingerprint(config))

        config.write_text(
            baseline.replace(
                'approval_mode = "approve"',
                'approval_mode = "approve"\nfuture_gate = true',
            ),
            encoding="utf-8",
        )
        with self.assertRaises(index.CapabilityDataError):
            index.capability_config_fingerprint(config)

    def test_gateway_managed_mcp_must_remain_disabled_for_direct_registration(
        self,
    ) -> None:
        config = self.root / "config.toml"
        valid = """
[mcp_servers.worker]
enabled = false
gateway_managed = true
command = "worker.exe"
"""
        config.write_text(valid, encoding="utf-8")
        index.capability_config_fingerprint(config)

        for invalid in (
            valid.replace("enabled = false", "enabled = true"),
            valid.replace("enabled = false\n", ""),
        ):
            with self.subTest(invalid=invalid):
                config.write_text(invalid, encoding="utf-8")
                with self.assertRaises(index.CapabilityDataError):
                    index.capability_config_fingerprint(config)

    def test_hook_carrier_status_is_separate_and_requires_trust_state(self) -> None:
        home = self.root / "hook-home"
        hooks_dir = home / "hooks"
        hooks_dir.mkdir(parents=True)
        for name in (
            "user_prompt_skill_router.py",
            "capability_index_session_start.py",
        ):
            (hooks_dir / name).write_text("# fixture\n", encoding="utf-8")
        hooks_path = home / "hooks.json"
        hook_config = {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(
                                    (hooks_dir / "user_prompt_skill_router.py").resolve()
                                ),
                                "commandWindows": str(
                                    (hooks_dir / "user_prompt_skill_router.py").resolve()
                                ),
                                "timeout": 10,
                                "statusMessage": "Checking skill routing hints",
                            }
                        ]
                    }
                ],
                "SessionStart": [
                    {
                        "matcher": "startup|resume|clear|compact",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(
                                    (
                                        hooks_dir
                                        / "capability_index_session_start.py"
                                    ).resolve()
                                ),
                                "commandWindows": str(
                                    (
                                        hooks_dir
                                        / "capability_index_session_start.py"
                                    ).resolve()
                                ),
                                "timeout": 180,
                                "statusMessage": "Refreshing capability index",
                            }
                        ],
                    }
                ],
            }
        }
        hooks_path.write_text(json.dumps(hook_config), encoding="utf-8")
        prompt_key = f"{hooks_path.resolve()}:user_prompt_submit:0:0"
        session_key = f"{hooks_path.resolve()}:session_start:0:0"
        prompt_hash = index._command_hook_trust_hash(
            "UserPromptSubmit",
            hook_config["hooks"]["UserPromptSubmit"][0],
            hook_config["hooks"]["UserPromptSubmit"][0]["hooks"][0],
        )
        session_hash = index._command_hook_trust_hash(
            "SessionStart",
            hook_config["hooks"]["SessionStart"][0],
            hook_config["hooks"]["SessionStart"][0]["hooks"][0],
        )
        config = home / "config.toml"

        def write_config(
            *,
            feature_enabled: bool = True,
            prompt_digest: str | None = prompt_hash,
            session_digest: str | None = session_hash,
            prompt_enabled: bool | None = None,
        ) -> None:
            rows = [
                "[features]",
                f"hooks = {'true' if feature_enabled else 'false'}",
                "[hooks.state]",
            ]
            if prompt_digest is not None:
                enabled = (
                    ""
                    if prompt_enabled is None
                    else f", enabled = {'true' if prompt_enabled else 'false'}"
                )
                rows.append(
                    f"'{prompt_key}' = {{ trusted_hash = '{prompt_digest}'{enabled} }}"
                )
            if session_digest is not None:
                rows.append(
                    f"'{session_key}' = {{ trusted_hash = '{session_digest}' }}"
                )
            config.write_text("\n".join(rows), encoding="utf-8")

        write_config()

        current = index.hook_carrier_status(
            config_path=config, hooks_path=hooks_path, codex_home=home
        )
        self.assertEqual(current["status"], "current")
        self.assertEqual(current["trust_state_status"], "current")

        write_config(feature_enabled=False)
        unavailable = index.hook_carrier_status(
            config_path=config, hooks_path=hooks_path, codex_home=home
        )
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertFalse(unavailable["feature_enabled"])

        write_config(prompt_enabled=False)
        disabled = index.hook_carrier_status(
            config_path=config, hooks_path=hooks_path, codex_home=home
        )
        self.assertEqual(disabled["status"], "unavailable")
        self.assertEqual(disabled["user_prompt_router"], "unavailable")

        write_config(prompt_digest=None)
        missing_trust = index.hook_carrier_status(
            config_path=config, hooks_path=hooks_path, codex_home=home
        )
        self.assertEqual(missing_trust["trust_state_status"], "unavailable")

        for mutate in (
            lambda value: value["hooks"]["UserPromptSubmit"][0]["hooks"][0].update(
                {"commandWindows": str((hooks_dir / "user_prompt_skill_router.py").resolve()) + " --changed"}
            ),
            lambda value: value["hooks"]["SessionStart"][0].update(
                {"matcher": "startup|resume"}
            ),
            lambda value: value["hooks"]["SessionStart"][0]["hooks"][0].update(
                {"timeout": 181}
            ),
        ):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(hook_config)
                mutate(changed)
                hooks_path.write_text(json.dumps(changed), encoding="utf-8")
                write_config()
                status = index.hook_carrier_status(
                    config_path=config, hooks_path=hooks_path, codex_home=home
                )
                self.assertEqual(status["status"], "unavailable")
                self.assertEqual(status["trust_state_status"], "unavailable")

        duplicate = copy.deepcopy(hook_config)
        duplicate["hooks"]["UserPromptSubmit"].append(
            copy.deepcopy(duplicate["hooks"]["UserPromptSubmit"][0])
        )
        hooks_path.write_text(json.dumps(duplicate), encoding="utf-8")
        write_config()
        duplicate_status = index.hook_carrier_status(
            config_path=config, hooks_path=hooks_path, codex_home=home
        )
        self.assertEqual(duplicate_status["status"], "unavailable")
        self.assertEqual(duplicate_status["user_prompt_router"], "unavailable")

    def test_hook_trust_hash_matches_first_party_windows_reference(self) -> None:
        prompt_group = {
            "hooks": [
                {
                    "type": "command",
                    "command": 'python3 -B "$HOME/.codex/hooks/user_prompt_skill_router.py"',
                    "commandWindows": '"C:\\Users\\Ayman Shams\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" -B "C:\\Users\\Ayman Shams\\.codex\\hooks\\user_prompt_skill_router.py"',
                    "statusMessage": "Checking skill routing hints",
                    "timeout": 10,
                }
            ]
        }
        session_group = {
            "matcher": "startup|resume|clear|compact",
            "hooks": [
                {
                    "type": "command",
                    "command": 'python3 -B "$HOME/.codex/hooks/capability_index_session_start.py"',
                    "commandWindows": '"C:\\Users\\Ayman Shams\\AppData\\Local\\Programs\\Python\\Python314\\python.exe" -B "C:\\Users\\Ayman Shams\\.codex\\hooks\\capability_index_session_start.py"',
                    "statusMessage": "Refreshing capability index",
                    "timeout": 180,
                }
            ],
        }
        with mock.patch.object(index.os, "name", "nt"):
            self.assertEqual(
                index._command_hook_trust_hash(
                    "UserPromptSubmit", prompt_group, prompt_group["hooks"][0]
                ),
                "sha256:b50ef0b4535b927cb91ce2981ede9a5882fedb244de27315b55dd0c6e495519e",
            )
            self.assertEqual(
                index._command_hook_trust_hash(
                    "SessionStart", session_group, session_group["hooks"][0]
                ),
                "sha256:e2b1080df8e723ac2aa05408e7ba3113169fc49bfc9b220d880d6282d5e5e358",
            )

    def test_project_scope_map_is_external_bounded_and_fail_closed(self) -> None:
        project_root = self.root / "sample-project"
        project_root.mkdir()
        map_path = self.root / "project-map.json"
        map_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "projects": {
                        "generic": {
                            "roots": [],
                            "source_scopes": [],
                            "memory_scope": None,
                        },
                        "sample_project": {
                            "roots": [str(project_root)],
                            "source_scopes": ["sample_project"],
                            "memory_scope": "sample_project",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        loaded = index._load_project_scope_map(map_path)
        self.assertIn("sample_project", loaded)
        self.assertEqual(loaded["sample_project"]["source_scopes"], ["sample_project"])

        nested = project_root / "nested"
        nested.mkdir()
        map_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "projects": {
                        "generic": {
                            "roots": [],
                            "source_scopes": [],
                            "memory_scope": None,
                        },
                        "project_alpha": {
                            "roots": [str(project_root)],
                            "source_scopes": ["project_alpha"],
                            "memory_scope": None,
                        },
                        "project_beta": {
                            "roots": [str(nested)],
                            "source_scopes": ["project_beta"],
                            "memory_scope": None,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        loaded = index._load_project_scope_map(map_path)
        self.assertIn("project_alpha", loaded)
        self.assertIn("project_beta", loaded)
        project_roots = tuple(
            sorted(
                (
                    (project_id, root)
                    for project_id, config in loaded.items()
                    for root in config["roots"]
                ),
                key=lambda item: (-len(item[1]), item[0], item[1]),
            )
        )
        with mock.patch.object(index, "PROJECT_ROOTS", project_roots):
            self.assertEqual(index._project_from_cwd(project_root), "project_alpha")
            self.assertEqual(index._project_from_cwd(nested), "project_beta")

        map_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "projects": {
                        "generic": {
                            "roots": [],
                            "source_scopes": [],
                            "memory_scope": None,
                        },
                        "project_alpha": {
                            "roots": [str(project_root)],
                            "source_scopes": ["project_alpha"],
                            "memory_scope": None,
                        },
                        "project_beta": {
                            "roots": [str(project_root)],
                            "source_scopes": ["project_beta"],
                            "memory_scope": None,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            index._load_project_scope_map(map_path),
            {"generic": {"roots": [], "source_scopes": [], "memory_scope": None}},
        )

        missing = self.root / "missing-project"
        map_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "projects": {
                        "generic": {
                            "roots": [],
                            "source_scopes": [],
                            "memory_scope": None,
                        },
                        "missing_project": {
                            "roots": [str(missing)],
                            "source_scopes": ["missing_project"],
                            "memory_scope": None,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            index._load_project_scope_map(map_path),
            {"generic": {"roots": [], "source_scopes": [], "memory_scope": None}},
        )

    def test_task_hashes_bind_normalized_instruction_and_complete_json(self) -> None:
        text_hash = index.compute_task_text_sha256("  line one\r\nline two\r  ")
        self.assertEqual(
            text_hash,
            hashlib.sha256("line one\nline two".encode("utf-8")).hexdigest(),
        )
        left = {"instruction": "x", "nested": {"b": 2, "a": 1}}
        right = {"nested": {"a": 1, "b": 2}, "instruction": "x"}
        self.assertEqual(
            index.compute_task_input_sha256(left),
            index.compute_task_input_sha256(right),
        )
        changed = copy.deepcopy(right)
        changed["nested"]["b"] = 3
        self.assertNotEqual(
            index.compute_task_input_sha256(left),
            index.compute_task_input_sha256(changed),
        )

    def test_ordered_selection_support_limit_and_exact_registry_verification(self) -> None:
        policy = json.loads((ROUTING_ROOT / "routing-policy.yaml").read_text("utf-8"))
        manifest = {
            "freshness_status": "fresh",
            "snapshot_id": "synthetic",
            "source_hashes_verified": True,
            "entries": policy_capability_entries(policy),
        }
        prompt = "Implement Supabase RLS policies for this database"
        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            decision = index.resolve_route(prompt, manifest=manifest, policy=policy)
        self.assertEqual(decision["rule_id"], "coding-supabase-security-boundary")
        self.assertEqual(decision["primary"]["id"], "skill:codex-coding-os-master")
        self.assertLessEqual(len(decision["supports"]), 2)
        receipt = self._verify_with_current_authority(
            decision, registry_path=self.registry
        )
        self.assertTrue(receipt["valid"])
        with closing(sqlite3.connect(self.registry)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                index.ROUTE_REGISTRY_SCHEMA_VERSION,
            )
        tampered = copy.deepcopy(decision)
        tampered["reason_codes"].append("TAMPERED")
        self.assertFalse(
            self._verify_with_current_authority(
                tampered, registry_path=self.registry
            )["valid"]
        )

    def test_registered_route_is_bound_to_current_manifest_and_policy_bytes(self) -> None:
        policy = index.load_routing_policy(self.policy_path)
        manifest = synthetic_manifest(policy)
        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            decision = index.resolve_route(
                "Implement Supabase RLS policies for this database",
                manifest=manifest,
                policy=policy,
            )
        self.assertEqual(decision["schema_version"], "3.0")
        self.assertRegex(decision["manifest_authority_sha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(decision["policy_authority_sha256"], r"^[a-f0-9]{64}$")

        matching_manifest = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "authority_sha256": decision["manifest_authority_sha256"],
        }
        matching_policy = {
            "authority_sha256": decision["policy_authority_sha256"],
        }
        with mock.patch.object(
            index, "load_active_capabilities", return_value=matching_manifest
        ), mock.patch.object(
            index, "load_routing_policy", return_value=matching_policy
        ):
            self.assertEqual(
                index.verify_registered_route(
                    decision, registry_path=self.registry
                )["status"],
                "registered",
            )

        quarantined_manifest = {
            **matching_manifest,
            "freshness_status": "degraded",
            "dynamic_authority": {
                "quarantined_capability_ids": [decision["primary"]["id"]]
            },
        }
        with mock.patch.object(
            index, "load_active_capabilities", return_value=quarantined_manifest
        ), mock.patch.object(
            index, "load_routing_policy", return_value=matching_policy
        ):
            self.assertEqual(
                index.verify_registered_route(
                    decision, registry_path=self.registry
                )["status"],
                "capability_quarantined",
            )

        changed_manifest = {
            **matching_manifest,
            "authority_sha256": "b" * 64,
        }
        with mock.patch.object(
            index, "load_active_capabilities", return_value=changed_manifest
        ), mock.patch.object(
            index, "load_routing_policy", return_value=matching_policy
        ):
            self.assertEqual(
                index.verify_registered_route(
                    decision, registry_path=self.registry
                )["status"],
                "manifest_mismatch",
            )

        changed_policy = {"authority_sha256": "c" * 64}
        with mock.patch.object(
            index, "load_active_capabilities", return_value=matching_manifest
        ), mock.patch.object(
            index, "load_routing_policy", return_value=changed_policy
        ):
            self.assertEqual(
                index.verify_registered_route(
                    decision, registry_path=self.registry
                )["status"],
                "policy_mismatch",
            )

        stale_manifest = {**matching_manifest, "source_hashes_verified": False}
        with mock.patch.object(
            index, "load_active_capabilities", return_value=stale_manifest
        ), mock.patch.object(
            index, "load_routing_policy", return_value=matching_policy
        ):
            self.assertEqual(
                index.verify_registered_route(
                    decision, registry_path=self.registry
                )["status"],
                "authority_unavailable",
            )

    def test_provenance_bearing_authorities_cannot_be_mutated_or_rebound(self) -> None:
        policy = index.load_routing_policy(self.policy_path)
        manifest = synthetic_manifest(policy)

        injected_policy = copy.deepcopy(policy)
        injected_policy["rules"].insert(
            0,
            {
                "id": "injected-rule",
                "scenario": "Injected rule",
                "match_any": ["xyzzy-authority-probe"],
                "match_all": [],
                "primary": "skill:deep-critic",
                "supports": [],
                "requires": [],
                "forbids": [],
                "authority_limit": "advisory-only",
                "evidence_ids": [],
                "execution_profile": "",
                "reason_codes": [],
                "intent_gate": "",
                "requires_live_dependencies": [],
                "dependency_fallback": None,
                "position": -1,
            },
        )
        with self.assertRaisesRegex(index.CapabilityDataError, "mutated"):
            index.resolve_route(
                "xyzzy-authority-probe", manifest=manifest, policy=injected_policy
            )

        rebound = copy.deepcopy(policy)
        rebound["source"] = str(self.root / "not-the-policy.yaml")
        with self.assertRaisesRegex(index.CapabilityDataError, "not canonical|unavailable"):
            index.resolve_route("x", manifest=manifest, policy=rebound)

        unicode_source = self.root / "unicode-authority.json"
        unicode_source.write_text("{}\n", encoding="utf-8")
        current = {
            "source": str(unicode_source.resolve()),
            "authority_sha256": "a" * 64,
            "description": "bounded résumé – security route",
        }
        supplied = copy.deepcopy(current)
        rebound_unicode = index._rebind_supplied_authority(
            supplied,
            canonical_path=unicode_source,
            loader=lambda _path: copy.deepcopy(current),
            label="Unicode test",
        )
        self.assertEqual(rebound_unicode, current)

        supplied["description"] = "mutated résumé – security route"
        with self.assertRaisesRegex(index.CapabilityDataError, "mutated"):
            index._rebind_supplied_authority(
                supplied,
                canonical_path=unicode_source,
                loader=lambda _path: copy.deepcopy(current),
                label="Unicode test",
            )

    def test_loaded_authority_hashes_bind_exact_source_bytes(self) -> None:
        with mock.patch.object(index, "_source_hash_mismatches", return_value=[]):
            manifest_payload = {
                "schema_version": "1.0",
                "generated_at": "2026-08-12T00:00:00Z",
                "snapshot_id": "exact-byte-test",
                "freshness_status": "fresh",
                "source_hashes": {},
                "entries": policy_capability_entries(
                    json.loads(self.policy_path.read_text(encoding="utf-8"))
                ),
                "suppressed_capabilities": [],
            }
            self.manifest_path.write_text(
                json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8"
            )
            loaded_manifest = index.load_active_capabilities(self.manifest_path)
        loaded_policy = index.load_routing_policy(self.policy_path)
        self.assertEqual(
            loaded_manifest["authority_sha256"],
            hashlib.sha256(self.manifest_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            loaded_policy["authority_sha256"],
            hashlib.sha256(self.policy_path.read_bytes()).hexdigest(),
        )

    def test_generation_pointer_ignores_compatibility_copy_and_detects_tamper(self) -> None:
        generation_id = ""
        generation_dir = self.routing_dir / "generations"
        generation_dir.mkdir()
        generation_path = generation_dir / f"generation-{generation_id}.json"
        generation_payload = {
            "schema_version": "1.3",
            "generated_at": "2026-08-14T00:00:00Z",
            "snapshot_id": f"authority-generation:{generation_id}",
            "freshness_status": "fresh",
            "authority_generation": {
                "id": "0" * 64,
                "sequence": 1,
                "previous_id": None,
                "transaction_id": "test-generation",
                "promoted_at": "2026-08-14T00:00:00Z",
                "promotion_reason": "operator_rebaseline",
                "static_authority_sha256": "b" * 64,
                "dynamic_authority_sha256": "c" * 64,
                "config_projection_sha256": "d" * 64,
                "plugin_inventory_sha256": "e" * 64,
                "worker_runtime_bom_sha256": "f" * 64,
                "authority_snapshot_sha256": "1" * 64,
            },
            "source_hashes": {},
            "entries": [active_entry("skill:immutable")],
        }
        generation_id = index.authority_generation_id(
            generation_payload["authority_generation"]
        )
        generation_payload["authority_generation"]["id"] = generation_id
        generation_payload["snapshot_id"] = f"authority-generation:{generation_id}"
        generation_path = generation_dir / f"generation-{generation_id}.json"
        generation_path.write_text(json.dumps(generation_payload), encoding="utf-8")
        manifest_sha256 = hashlib.sha256(generation_path.read_bytes()).hexdigest()
        self.generation_pointer_path.write_text(
            json.dumps(
                {
                    "schema_version": "capability-authority-pointer-v1",
                    "generation_id": generation_id,
                    "sequence": 1,
                    "previous_generation_id": None,
                    "manifest_path": generation_path.relative_to(self.routing_dir).as_posix(),
                    "manifest_sha256": manifest_sha256,
                    "transaction_id": "test-generation",
                    "promoted_at": "2026-08-14T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        self.manifest_path.write_text('{"tampered":true}\n', encoding="utf-8")
        with mock.patch.object(index, "_source_hash_mismatches", return_value=[]):
            loaded = index.load_active_capabilities()
        self.assertEqual(loaded["generation_pointer_status"], "current")
        self.assertEqual(loaded["source"], str(generation_path.resolve()))
        self.assertEqual([entry["id"] for entry in loaded["entries"]], ["skill:immutable"])

        generation_path.write_text('{"tampered":true}\n', encoding="utf-8")
        failed = index.load_active_capabilities()
        self.assertEqual(failed["freshness_status"], "missing")
        self.assertEqual(failed["generation_pointer_status"], "invalid")
        self.assertIn("generation_pointer_invalid", failed["source_hash_mismatches"])

    def _worker_python_probe(
        self, command: Path, package: str, pycache_prefix: Path
    ) -> dict[str, object]:
        value = self.worker_python_probes.get(package)
        if value is None:
            raise index.CapabilityDataError("synthetic worker probe is unavailable")
        return copy.deepcopy(value)

    def _worker_pth_probe(
        self, command: Path, modules: list[str], pycache_prefix: Path
    ) -> dict[str, str]:
        package = (
            "local_agent_stack"
            if "local-agent-stack" in str(command)
            else "antigravity_adapter"
        )
        value = self.worker_pth_probes.get(package)
        if value is None or set(value) != set(modules):
            raise index.CapabilityDataError("synthetic .pth probe is unavailable")
        return copy.deepcopy(value)

    def _install_worker_python_closure(
        self,
        root: Path,
        command: Path,
        package: str,
        base_python_home: Path,
        base_python: Path,
    ) -> dict[str, object]:
        pyvenv = root / ".venv" / "pyvenv.cfg"
        pyvenv.write_text(
            "\n".join(
                (
                    f"home = {base_python_home}",
                    "implementation = CPython",
                    "version_info = 3.11",
                    "include-system-site-packages = false",
                    "",
                )
            ),
            encoding="utf-8",
        )
        site_packages = root / ".venv" / "Lib" / "site-packages"
        site_packages.mkdir(parents=True, exist_ok=True)
        editable = site_packages / f"__editable__.{package}-test.pth"
        source_root = (root / "src").resolve()
        editable.write_text(str(source_root) + "\n", encoding="utf-8")
        virtualenv_pth = site_packages / "_virtualenv.pth"
        virtualenv_module = site_packages / "_virtualenv.py"
        installed_module = site_packages / "fixture_dependency.py"
        virtualenv_pth.write_text("import _virtualenv\n", encoding="utf-8")
        virtualenv_module.write_text("# synthetic bootstrap\n", encoding="utf-8")
        installed_module.write_text("VALUE = 'trusted'\n", encoding="utf-8")
        dist_info = site_packages / f"fixture_{package}-1.0.0.dist-info"
        dist_info.mkdir(exist_ok=True)
        (dist_info / "METADATA").write_text(
            f"Name: fixture-{package}\nVersion: 1.0.0\n",
            encoding="utf-8",
        )
        (dist_info / "RECORD").write_text(
            "\n".join(
                (
                    f"{editable.name},,",
                    f"{installed_module.name},,",
                    f"{dist_info.name}/METADATA,,",
                    f"{dist_info.name}/RECORD,,",
                    "",
                )
            ),
            encoding="utf-8",
        )
        origin = source_root / package / "__init__.py"
        server_id = (
            "local-agent-stack"
            if package == "local_agent_stack"
            else "antigravity-adapter"
        )
        spec = index.WORKER_SERVER_SPECS[server_id]
        pycache_prefix = root.joinpath(
            *str(spec["pycache_relative_path"]).split("/")
        )
        pycache_prefix.mkdir(parents=True, exist_ok=True)
        self.worker_pth_probes[package] = {
            "_virtualenv": str(virtualenv_module.resolve())
        }
        distributions = index._worker_installed_distributions_identity(
            site_packages,
            root / ".venv",
            source_root,
            command,
            pycache_prefix,
        )
        base_runtime = index._worker_base_runtime_tree_identity(base_python_home)
        site_packages_tree = index._worker_site_packages_tree_identity(site_packages)
        closure: dict[str, object] = {
            "schema_version": index.PYTHON_EXECUTION_CLOSURE_SCHEMA,
            "venv_python_path": str(command.resolve()),
            "venv_python_sha256": hashlib.sha256(
                command.read_bytes()
            ).hexdigest(),
            "pyvenv_config_path": str(pyvenv.resolve()),
            "pyvenv_config_sha256": hashlib.sha256(
                pyvenv.read_bytes()
            ).hexdigest(),
            "include_system_site_packages": False,
            "base_interpreter_path": str(base_python.resolve()),
            "base_interpreter_version": "3.11.15",
            "base_interpreter_sha256": hashlib.sha256(
                base_python.read_bytes()
            ).hexdigest(),
            "base_runtime_tree_path": str(base_python_home.resolve()),
            **base_runtime,
            "site_packages_path": str(site_packages.resolve()),
            **site_packages_tree,
            **distributions,
            "editable_pth_path": str(editable.resolve()),
            "editable_pth_sha256": hashlib.sha256(
                editable.read_bytes()
            ).hexdigest(),
            "editable_source_root": str(source_root),
            "import_package": package,
            "import_origin": str(origin.resolve(strict=False)),
            "isolated_mode": True,
            "user_site_enabled": False,
            "dont_write_bytecode": True,
            "pycache_prefix_path": str(pycache_prefix.resolve()),
            "pycache_prefix_empty": True,
            "forbidden_environment_variables": list(
                index.PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
            ),
            "child_environment_policy_id": (
                index.WORKER_CHILD_ENVIRONMENT_POLICY_ID
            ),
        }
        self.worker_python_probes[package] = {
            "executable": str(command.resolve()),
            "base_prefix": str(base_python_home.resolve()),
            "version": "3.11.15",
            "origin": str(origin.resolve(strict=False)),
            "locations": [str((source_root / package).resolve(strict=False))],
            "isolated": 1,
            "no_user_site": 1,
            "user_site_enabled": False,
            "dont_write_bytecode": True,
            "pycache_prefix": str(pycache_prefix.resolve()),
        }
        return closure

    def _install_gateway_runtime_fixture(
        self, base_python_home: Path, base_python: Path
    ) -> dict[str, object]:
        gateway_root = self.codex_home / "tools" / "codex-stability"
        gateway_root.mkdir(parents=True, exist_ok=True)
        for relative in index.GATEWAY_SOURCE_RELATIVE_PATHS:
            gateway_root.joinpath(*relative.split("/")).write_text(
                f"# {relative}\n", encoding="utf-8"
            )
        gateway_site = gateway_root / ".venv" / "Lib" / "site-packages"
        gateway_site.mkdir(parents=True, exist_ok=True)
        (gateway_site / "gateway_dependency.py").write_text(
            "# gateway dependency\n", encoding="utf-8"
        )
        dependency_lock = gateway_root / "uv.lock"
        dependency_lock.write_text("fixture gateway lock\n", encoding="utf-8")
        pycache_prefix = (
            self.local_app_data / "Codex" / "stability" / "pycache" / "gateway"
        )
        pycache_prefix.mkdir(parents=True, exist_ok=True)
        source_sha256, source_files = index._gateway_source_identity(gateway_root)
        base_identity = index._gateway_runtime_tree_identity(
            base_python_home,
            domain=index.GATEWAY_PYTHON_BASE_RUNTIME_DOMAIN,
        )
        site_identity = index._gateway_runtime_tree_identity(
            gateway_site,
            domain=index.GATEWAY_SITE_PACKAGES_DOMAIN,
        )
        windowless = base_python_home / "pythonw.exe"
        identity: dict[str, object] = {
            "child_environment_policy_id": (
                index.WORKER_CHILD_ENVIRONMENT_POLICY_ID
            ),
            "component": index.GATEWAY_COMPONENT,
            "gateway_startup_environment_policy_id": (
                index.GATEWAY_STARTUP_ENVIRONMENT_POLICY_ID
            ),
            "gateway_startup_python_flags": dict(
                index.GATEWAY_REQUIRED_PYTHON_FLAGS
            ),
            "python_bytecode_cache": {
                "must_be_empty": True,
                "prefix_path": str(pycache_prefix.resolve()),
            },
            "python_injection_environment_keys": list(
                index.PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
            ),
            "python_runtime": {
                "base_root": str(base_python_home.resolve()),
                "base_runtime_file_count": base_identity["file_count"],
                "base_runtime_sha256": base_identity["sha256"],
                "console_executable_path": str(base_python.resolve()),
                "console_executable_sha256": hashlib.sha256(
                    base_python.read_bytes()
                ).hexdigest(),
                "dependency_lock_path": str(dependency_lock.resolve()),
                "dependency_lock_sha256": hashlib.sha256(
                    dependency_lock.read_bytes()
                ).hexdigest(),
                "site_packages_file_count": site_identity["file_count"],
                "site_packages_path": str(gateway_site.resolve()),
                "site_packages_sha256": site_identity["sha256"],
                "version": "3.11.15",
                "windowless_executable_path": str(windowless.resolve()),
                "windowless_executable_sha256": hashlib.sha256(
                    windowless.read_bytes()
                ).hexdigest(),
            },
            "release_id": index.GATEWAY_RELEASE_ID,
            "schema_version": index.GATEWAY_RUNTIME_IDENTITY_SCHEMA,
            "source_files": source_files,
            "source_sha256": source_sha256,
        }
        identity_path = gateway_root / "runtime-identity.json"
        identity_path.write_text(json.dumps(identity) + "\n", encoding="utf-8")
        return {
            "config_server_id": index.GATEWAY_CONFIG_SERVER_ID,
            "identity_relative_path": index.GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH,
            "identity_sha256": hashlib.sha256(identity_path.read_bytes()).hexdigest(),
            "runtime_identity": identity,
            "server_config_sha256": index._worker_projection_sha256(
                {"url": index.GATEWAY_CONFIG_URL}
            ),
        }

    def _install_worker_runtime_fixture(self) -> tuple[dict, dict[str, Path]]:
        roots = {
            "local-agent-stack": self.root / "local-agent-stack",
            "antigravity-adapter": self.root / "antigravity-adapter",
        }
        commands: dict[str, Path] = {}
        for server_id, root in roots.items():
            command = root / ".venv" / "Scripts" / "python.exe"
            command.parent.mkdir(parents=True, exist_ok=True)
            command.write_bytes(server_id.encode("utf-8"))
            commands[server_id] = command
        base_python_home = self.root / "base-python"
        base_python_home.mkdir(exist_ok=True)
        base_python = base_python_home / "python.exe"
        base_python.write_bytes(b"base Python")
        (base_python_home / "pythonw.exe").write_bytes(b"base Python windowless")
        agy = self.root / "agy.exe"
        agy.write_bytes(b"agy")
        hermes_api = self.root / "hermes" / "api_server.py"
        hermes_metadata = self.root / "hermes" / "METADATA"
        hermes_api.parent.mkdir(exist_ok=True)
        hermes_api.write_bytes(b"hermes api")
        hermes_metadata.write_bytes(b"Version: 0.19.0\n")
        las_root = roots["local-agent-stack"]
        for path, payload in (
            (las_root / "pyproject.toml", b"[project]\nname='las'\n"),
            (las_root / "uv.lock", b"fixture\n"),
            (las_root / "vendor" / "versions.json", b"{}\n"),
            (las_root / "src" / "local_agent_stack" / "__init__.py", b"# las\n"),
            (las_root / "src" / "local_agent_stack" / "server.py", b"# server\n"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        for directory in (las_root / "config" / "schemas", las_root / "scripts"):
            directory.mkdir(parents=True, exist_ok=True)
        preliminary_agy_package = (
            roots["antigravity-adapter"] / "src" / "antigravity_adapter"
        )
        preliminary_agy_package.mkdir(parents=True, exist_ok=True)
        (preliminary_agy_package / "__init__.py").write_text(
            "# antigravity\n", encoding="utf-8"
        )
        python_closures = {
            "local-agent-stack": self._install_worker_python_closure(
                las_root,
                commands["local-agent-stack"],
                "local_agent_stack",
                base_python_home,
                base_python,
            ),
            "antigravity-adapter": self._install_worker_python_closure(
                roots["antigravity-adapter"],
                commands["antigravity-adapter"],
                "antigravity_adapter",
                base_python_home,
                base_python,
            ),
        }
        hermes_lock = {
            "distribution_version": "0.19.0",
            "distribution_metadata_path": str(hermes_metadata),
            "distribution_metadata_sha256": hashlib.sha256(
                hermes_metadata.read_bytes()
            ).hexdigest(),
            "api_source_path": str(hermes_api),
            "api_source_sha256": hashlib.sha256(hermes_api.read_bytes()).hexdigest(),
            "overlay_id": "test-overlay",
        }
        (las_root / "runtime-dependencies.lock.json").write_text(
            json.dumps(
                {
                    "schema_version": "local-agent-stack-runtime-dependencies-v2",
                    "release_id": "local-agent-stack-test",
                    "python_execution_closure": python_closures[
                        "local-agent-stack"
                    ],
                    "files": [],
                    "executables": {},
                    "ollama": {},
                    "hermes": hermes_lock,
                    "agent_memory": {},
                    "scheduler_contract": {},
                    "startup_receipts": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        las_source_sha = index._worker_source_inventory_sha256(
            las_root.resolve(),
            [
                las_root / "pyproject.toml",
                las_root / "uv.lock",
                las_root / "runtime-dependencies.lock.json",
                las_root / "vendor" / "versions.json",
                las_root / "src" / "local_agent_stack" / "__init__.py",
                las_root / "src" / "local_agent_stack" / "server.py",
            ],
        )
        agy_root = roots["antigravity-adapter"]
        agy_lock = {
            "schema_version": "antigravity-adapter-dependency-lock-v2",
            "python_execution_closure": python_closures[
                "antigravity-adapter"
            ],
            "agy": {
                "version": "1.1.13",
                "executable_sha256": hashlib.sha256(agy.read_bytes()).hexdigest(),
                "model_efforts": {"gemini-3.1-pro-high": "high"},
            },
        }
        agy_lock_path = agy_root / "dependency-lock.json"
        agy_lock_path.write_text(json.dumps(agy_lock, indent=2) + "\n", encoding="utf-8")
        (agy_root / "pyproject.toml").write_bytes(b"[project]\nname='agy'\n")
        agy_package = agy_root / "src" / "antigravity_adapter"
        agy_package.mkdir(parents=True, exist_ok=True)
        antigravity_fixture_files = (
            "__init__.py",
            "config.py",
            "dependency_identity.py",
            "locking.py",
            "receipts.py",
            "route_authority.py",
            "runner.py",
            "runtime_identity.py",
            "server.py",
            "service.py",
            "source_integrity.py",
            "windows_job.py",
        )
        for name in antigravity_fixture_files:
            (agy_package / name).write_text(f"# {name}\n", encoding="utf-8")
        agy_source_sha = index._worker_source_inventory_sha256(
            agy_root.resolve(),
            [
                *(agy_package / name for name in antigravity_fixture_files),
                agy_lock_path,
                agy_root / "pyproject.toml",
            ],
        )
        model_contract_sha = hashlib.sha256(
            json.dumps(
                agy_lock["agy"]["model_efforts"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        identities = {
            "local-agent-stack": {
                "schema_version": "local-agent-stack-runtime-identity-v2",
                "component": "local-agent-stack",
                "runtime_version": "0.2.0",
                "release_id": "local-agent-stack-test",
                "catalogue_router_compatibility": {
                    "route_schema_version": "3.0",
                    "route_registry_schema_version": 3,
                    "authority_pointer_schema_version": "capability-authority-pointer-v1",
                    "manifest_schema_versions": ["1.2", "1.3"],
                },
                "nested_dependencies": {
                    "hermes": {
                        "distribution_version": "0.19.0",
                        "overlay_id": "test-overlay",
                        "api_source_sha256": hermes_lock["api_source_sha256"],
                    }
                },
                "python_execution_closure": python_closures[
                    "local-agent-stack"
                ],
                "source_sha256": las_source_sha,
            },
            "antigravity-adapter": {
                "schema_version": "antigravity-adapter-runtime-identity-v3",
                "component": "antigravity-adapter",
                "runtime_version": "2.1.0",
                "release_id": "antigravity-adapter-test",
                "route_schema_version": "3.0",
                "route_registry_schema_version": 3,
                "authority_pointer_schema_version": "capability-authority-pointer-v1",
                "supported_manifest_schema_versions": ["1.2", "1.3"],
                "agy_version": "1.1.13",
                "agy_executable_sha256": agy_lock["agy"]["executable_sha256"],
                "agy_model_contract_sha256": model_contract_sha,
                "dependency_lock_schema_version": "antigravity-adapter-dependency-lock-v2",
                "dependency_lock_sha256": hashlib.sha256(
                    agy_lock_path.read_bytes()
                ).hexdigest(),
                "python_execution_closure": python_closures[
                    "antigravity-adapter"
                ],
                "source_sha256": agy_source_sha,
            },
        }
        identity_paths: dict[str, Path] = {}
        for server_id, identity in identities.items():
            path = roots[server_id] / "runtime-identity.json"
            path.write_text(json.dumps(identity) + "\n", encoding="utf-8")
            identity_paths[server_id] = path
        self.config_path.write_text(
            "\n".join(
                [
                    "[mcp_servers.local-agent-stack]",
                    "enabled = false",
                    "gateway_managed = true",
                    f"command = '{commands['local-agent-stack']}'",
                    "args = ['-I', '-B', '-X', "
                    f"'pycache_prefix={python_closures['local-agent-stack']['pycache_prefix_path']}', "
                    "'-m', 'local_agent_stack.server']",
                    f"cwd = '{roots['local-agent-stack']}'",
                    "startup_timeout_sec = 60.0",
                    "tool_timeout_sec = 660.0",
                    f"env = {{ LOCAL_AGENT_STACK_ROOT = '{roots['local-agent-stack']}' }}",
                    "",
                    "[mcp_servers.antigravity-adapter]",
                    "enabled = false",
                    "gateway_managed = true",
                    f"command = '{commands['antigravity-adapter']}'",
                    "args = ['-I', '-B', '-X', "
                    f"'pycache_prefix={python_closures['antigravity-adapter']['pycache_prefix_path']}', "
                    "'-m', 'antigravity_adapter.server']",
                    f"cwd = '{roots['antigravity-adapter']}'",
                    "startup_timeout_sec = 30.0",
                    "tool_timeout_sec = 620.0",
                    f"env = {{ ANTIGRAVITY_ADAPTER_ROOT = '{roots['antigravity-adapter']}', ANTIGRAVITY_AGY_EXECUTABLE = '{agy}' }}",
                    "",
                    "[mcp_servers.codex-stability-gateway]",
                    f"url = '{index.GATEWAY_CONFIG_URL}'",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        import tomllib

        config = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
        runtimes = {}
        for server_id in sorted(roots):
            projection, _ = index._worker_server_projection(config, server_id)
            identity_path = identity_paths[server_id]
            runtimes[server_id] = {
                "config_server_id": server_id,
                "identity_relative_path": "runtime-identity.json",
                "identity_sha256": hashlib.sha256(identity_path.read_bytes()).hexdigest(),
                "command_sha256": hashlib.sha256(commands[server_id].read_bytes()).hexdigest(),
                "python_execution_closure": python_closures[server_id],
                "server_config_sha256": index._worker_projection_sha256(projection),
                "release_id": identities[server_id]["release_id"],
                "route_schema_version": "3.0",
                "route_registry_schema_version": 3,
            }
        gateway_runtime = self._install_gateway_runtime_fixture(
            base_python_home, base_python
        )
        bom = {
            "schema_version": index.WORKER_RUNTIME_BOM_SCHEMA,
            "gateway_runtime": gateway_runtime,
            "runtimes": runtimes,
        }
        self.worker_runtime_bom_path.write_text(json.dumps(bom) + "\n", encoding="utf-8")
        receipt_path = index.GATEWAY_STARTUP_RECEIPT_PATH
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        (receipt_path.parent / "task-supervisor.sqlite3").write_bytes(b"fixture")
        gateway_identity = gateway_runtime["runtime_identity"]
        runtime_identity_path = self.codex_home.joinpath(
            *index.GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH.split("/")
        ).resolve(strict=True)
        bom_sha256 = hashlib.sha256(
            self.worker_runtime_bom_path.read_bytes()
        ).hexdigest()
        upstream = {
            server_id: hashlib.sha256(server_id.encode("utf-8")).hexdigest()
            for server_id in sorted(index.REQUIRED_WORKER_RUNTIME_SERVER_IDS)
        }
        receipt = {
            "schema_version": index.GATEWAY_STARTUP_RECEIPT_SCHEMA,
            "release_id": gateway_identity["release_id"],
            "process_role": "scheduled_windowless",
            "process_id": 4242,
            "process_start_time_utc": "2026-08-15T00:00:00.0000000+00:00",
            "executable_path": gateway_identity["python_runtime"][
                "windowless_executable_path"
            ],
            "executable_sha256": gateway_identity["python_runtime"][
                "windowless_executable_sha256"
            ],
            "runtime_identity_path": str(runtime_identity_path),
            "runtime_identity_sha256": gateway_runtime["identity_sha256"],
            "source_sha256": gateway_identity["source_sha256"],
            "worker_runtime_bom_path": str(
                self.worker_runtime_bom_path.resolve(strict=True)
            ),
            "worker_runtime_bom_sha256": bom_sha256,
            "loaded_upstream_config_sha256": "d" * 64,
            "upstream_config_sha256_by_server": upstream,
            "task_action_sha256": index._gateway_task_action_sha256(
                gateway_identity
            ),
            "child_environment_policy_id": gateway_identity[
                "child_environment_policy_id"
            ],
            "gateway_startup_environment_policy_id": gateway_identity[
                "gateway_startup_environment_policy_id"
            ],
            "managed_upstreams_absent_at_start": True,
            "recorded_at_utc": "2026-08-15T00:00:01.0000000+00:00",
        }
        receipt["binding_sha256"] = index._gateway_receipt_binding_sha256(
            receipt
        )
        receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        return bom, identity_paths

    def test_gateway_process_generation_receipt_is_required_and_bound(self) -> None:
        self._install_worker_runtime_fixture()
        bom_sha256 = hashlib.sha256(
            self.worker_runtime_bom_path.read_bytes()
        ).hexdigest()
        self.assertTrue(
            index._gateway_managed_upstream_configured(
                "local-agent-stack", expected_bom_sha256=bom_sha256
            )
        )
        receipt = index.GATEWAY_STARTUP_RECEIPT_PATH
        original = receipt.read_bytes()
        receipt.unlink()
        self.assertFalse(
            index._gateway_managed_upstream_configured(
                "local-agent-stack", expected_bom_sha256=bom_sha256
            )
        )
        receipt.write_bytes(original)
        value = json.loads(original)
        value["worker_runtime_bom_sha256"] = "0" * 64
        receipt.write_text(json.dumps(value) + "\n", encoding="utf-8")
        self.assertFalse(
            index._gateway_managed_upstream_configured(
                "local-agent-stack", expected_bom_sha256=bom_sha256
            )
        )

    def test_worker_runtime_bom_binds_config_and_family_identity_bytes(self) -> None:
        bom, identity_paths = self._install_worker_runtime_fixture()
        bom_sha256 = hashlib.sha256(self.worker_runtime_bom_path.read_bytes()).hexdigest()
        for server_id in index.REQUIRED_WORKER_RUNTIME_SERVER_IDS:
            self.assertTrue(
                index._gateway_managed_upstream_configured(
                    server_id,
                    expected_bom_sha256=bom_sha256,
                    verify_current_bytes=True,
                )
            )
        fake_identity = json.loads(
            identity_paths["local-agent-stack"].read_text(encoding="utf-8")
        )
        fake_identity["source_sha256"] = "f" * 64
        identity_paths["local-agent-stack"].write_text(
            json.dumps(fake_identity) + "\n", encoding="utf-8"
        )
        bom["runtimes"]["local-agent-stack"]["identity_sha256"] = hashlib.sha256(
            identity_paths["local-agent-stack"].read_bytes()
        ).hexdigest()
        self.worker_runtime_bom_path.write_text(
            json.dumps(bom) + "\n", encoding="utf-8"
        )
        fake_bom_sha256 = hashlib.sha256(
            self.worker_runtime_bom_path.read_bytes()
        ).hexdigest()
        self.assertFalse(
            index._gateway_managed_upstream_configured(
                "local-agent-stack",
                expected_bom_sha256=fake_bom_sha256,
                verify_current_bytes=True,
            )
        )
        _, identity_paths = self._install_worker_runtime_fixture()
        bom_sha256 = hashlib.sha256(self.worker_runtime_bom_path.read_bytes()).hexdigest()
        identity_paths["local-agent-stack"].write_text(
            '{"tampered":true}\n', encoding="utf-8"
        )
        self.assertFalse(
            index._gateway_managed_upstream_configured(
                "local-agent-stack",
                expected_bom_sha256=bom_sha256,
                verify_current_bytes=True,
            )
        )
        self._install_worker_runtime_fixture()
        bom_sha256 = hashlib.sha256(self.worker_runtime_bom_path.read_bytes()).hexdigest()
        self.config_path.write_text(
            self.config_path.read_text(encoding="utf-8").replace(
                "enabled = false", "enabled = true", 1
            ),
            encoding="utf-8",
        )
        self.assertFalse(
            index._gateway_managed_upstream_configured(
                "local-agent-stack",
                expected_bom_sha256=bom_sha256,
                verify_current_bytes=True,
            )
        )

    def test_worker_and_gateway_runtime_tree_tamper_fail_closed(self) -> None:
        self._install_worker_runtime_fixture()
        bom_sha256 = hashlib.sha256(
            self.worker_runtime_bom_path.read_bytes()
        ).hexdigest()

        def assert_byte_tamper_rejected(path: Path, label: str) -> None:
            original = path.read_bytes()
            metadata = path.stat()
            changed = bytes([original[0] ^ 1]) + original[1:]
            path.write_bytes(changed)
            os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            try:
                self.assertFalse(
                    index._gateway_managed_upstream_configured(
                        "local-agent-stack",
                        expected_bom_sha256=bom_sha256,
                        verify_current_bytes=True,
                    ),
                    label,
                )
            finally:
                path.write_bytes(original)
                os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))

        assert_byte_tamper_rejected(
            self.root
            / "local-agent-stack"
            / ".venv"
            / "Lib"
            / "site-packages"
            / "fixture_dependency.py",
            "same-size worker dependency tamper must be detected",
        )
        assert_byte_tamper_rejected(
            self.codex_home
            / "tools"
            / "codex-stability"
            / ".venv"
            / "Lib"
            / "site-packages"
            / "gateway_dependency.py",
            "same-size gateway dependency tamper must be detected",
        )
        assert_byte_tamper_rejected(
            self.root / "base-python" / "python.exe",
            "same-size base interpreter tamper must be detected",
        )

        worker_site = (
            self.root
            / "local-agent-stack"
            / ".venv"
            / "Lib"
            / "site-packages"
        )
        for suffix in (".py", ".pyc"):
            with self.subTest(suffix=suffix):
                shadow = worker_site / f"unowned_shadow{suffix}"
                shadow.write_bytes(b"unowned executable bytes")
                try:
                    self.assertFalse(
                        index._gateway_managed_upstream_configured(
                            "local-agent-stack",
                            expected_bom_sha256=bom_sha256,
                            verify_current_bytes=True,
                        )
                    )
                finally:
                    shadow.unlink()

        gateway_cache = (
            self.local_app_data / "Codex" / "stability" / "pycache" / "gateway"
        )
        cached = gateway_cache / "unexpected.pyc"
        cached.write_bytes(b"cached bytecode")
        try:
            self.assertFalse(
                index._gateway_managed_upstream_configured(
                    "local-agent-stack",
                    expected_bom_sha256=bom_sha256,
                    verify_current_bytes=True,
                )
            )
        finally:
            cached.unlink()

    def test_route_time_binding_defers_full_tree_scan_to_ingress(self) -> None:
        self._install_worker_runtime_fixture()
        bom_sha256 = hashlib.sha256(
            self.worker_runtime_bom_path.read_bytes()
        ).hexdigest()
        with mock.patch.object(
            index,
            "_gateway_runtime_binding_current",
            side_effect=AssertionError("route-time gateway tree rescan"),
        ), mock.patch.object(
            index,
            "_validate_worker_family_identity",
            side_effect=AssertionError("route-time worker tree rescan"),
        ):
            self.assertTrue(
                index._gateway_managed_upstream_configured(
                    "local-agent-stack", expected_bom_sha256=bom_sha256
                )
            )

    def test_worker_runtime_bom_rejects_malformed_document_closure(self) -> None:
        valid, _ = self._install_worker_runtime_fixture()
        loaded, _ = index._load_worker_runtime_bom(self.worker_runtime_bom_path)
        self.assertEqual(loaded, valid)

        binding = valid["runtimes"]["local-agent-stack"]
        antigravity_binding = valid["runtimes"]["antigravity-adapter"]
        malformed = [
            {**valid, "unexpected": True},
            {"schema_version": index.WORKER_RUNTIME_BOM_SCHEMA, "runtimes": {}},
            {
                "schema_version": index.WORKER_RUNTIME_BOM_SCHEMA,
                "runtimes": {
                    "local-agent-stack": {
                        **binding,
                        "identity_relative_path": "../runtime-identity.json",
                    },
                    "antigravity-adapter": antigravity_binding,
                },
            },
            {
                "schema_version": index.WORKER_RUNTIME_BOM_SCHEMA,
                "runtimes": {
                    "local-agent-stack": {**binding, "extra": "unbound"},
                    "antigravity-adapter": antigravity_binding,
                },
            },
        ]
        for value in malformed:
            with self.subTest(value=value):
                self.worker_runtime_bom_path.write_text(
                    json.dumps(value), encoding="utf-8"
                )
                with self.assertRaises(index.CapabilityDataError):
                    index._load_worker_runtime_bom(self.worker_runtime_bom_path)

    def test_stale_or_missing_authority_fails_before_registry_issuance(self) -> None:
        policy = index.load_routing_policy(self.policy_path)
        stale_manifest = synthetic_manifest(policy)
        stale_manifest["freshness_status"] = "stale"
        stale_manifest["source_hashes_verified"] = False
        stale_manifest["authority_sha256"] = "a" * 64
        stale_manifest["source"] = str(self.manifest_path)
        with mock.patch.object(index, "load_active_capabilities", return_value=stale_manifest):
            decision = index.resolve_route("stale authority probe", policy=policy)
        self.assertEqual(decision["issuance"]["status"], "failed")
        self.assertEqual(
            decision["issuance"]["failure_code"], "AUTHORITY_UNAVAILABLE"
        )
        self.assertFalse(self.registry.exists())

        conservative = index.conservative_default_decision(prompt="missing authority")
        self.assertEqual(conservative["issuance"]["status"], "failed")
        self.assertEqual(
            conservative["issuance"]["failure_code"], "AUTHORITY_UNAVAILABLE"
        )

    def test_live_dependency_fallbacks_bind_equivalence_and_fail_closed(self) -> None:
        policy = index.load_routing_policy(self.policy_path)
        manifest = synthetic_manifest(policy)

        def set_dependency(enabled: bool) -> None:
            value = "true" if enabled else "false"
            self.config_path.write_text(
                "\n".join(
                    [
                        '[plugins."codex-security@openai-curated-remote".mcp_servers.codex-security]',
                        f"enabled = {value}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            set_dependency(False)
            standard = index.resolve_route(
                "Run a standard security scan of this repository",
                manifest=manifest,
                policy=policy,
            )
            deep = index.resolve_route(
                "Run a deep security scan of this repository",
                manifest=manifest,
                policy=policy,
            )
            self.assertEqual(
                standard["primary"]["id"], "skill:codex-security:security-scan"
            )
            self.assertEqual(
                standard["capability_fallbacks"][0]["equivalence"], "equivalent"
            )
            self.assertEqual(
                deep["primary"]["id"], "skill:security-best-practices"
            )
            self.assertEqual(
                deep["capability_fallbacks"][0]["equivalence"], "non_equivalent"
            )

            set_dependency(True)
            enabled = index.resolve_route(
                "Run a deep security scan of this repository",
                manifest=manifest,
                policy=policy,
                task_input=complete_task_input(
                    "Run a deep security scan of this repository",
                    request_id="live-dependency-enabled",
                    live_dependency_probes=live_dependency_probe(
                        "mcp:codex-security",
                        request_id="live-dependency-enabled",
                    ),
                ),
            )
            self.assertEqual(
                enabled["primary"]["id"],
                "skill:codex-security:deep-security-scan",
            )
            self.assertEqual(enabled["capability_fallbacks"], [])

            self.config_path.write_text("not valid toml = [", encoding="utf-8")
            unreadable = index.resolve_route(
                "Run a deep security scan of this repository",
                manifest=manifest,
                policy=policy,
            )
            self.assertEqual(
                unreadable["primary"]["id"], "skill:security-best-practices"
            )
            self.assertIn(
                "inventory:config",
                unreadable["capability_fallbacks"][0]["unavailable_dependencies"],
            )

    def test_equivalent_fallback_mutations_fail_closed_or_match_emitted_primary(
        self,
    ) -> None:
        prompt = "Run a standard security scan of this repository"
        base_policy = json.loads(
            (ROUTING_ROOT / "routing-policy.yaml").read_text(encoding="utf-8")
        )

        def mutate_fallback(**updates: object) -> dict[str, object]:
            mutated = copy.deepcopy(base_policy)
            rule = next(
                item
                for item in mutated["rules"]
                if item["id"] == "standard-security-review"
            )
            rule["dependency_fallback"].update(updates)
            return mutated

        self.config_path.write_text(
            '\n'.join(
                [
                    '[plugins."codex-security@openai-curated-remote".mcp_servers.codex-security]',
                    "enabled = false",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        mutations = (
            {"selected_capability": ""},
            {"selected_capability": "skill:security-best-practices"},
        )
        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            for position, mutation in enumerate(mutations):
                with self.subTest(mutation=mutation):
                    policy = mutate_fallback(**mutation)
                    decision = index.resolve_route(
                        prompt,
                        manifest=synthetic_manifest(
                            policy, "skill:security-best-practices"
                        ),
                        policy=policy,
                        task_input={
                            "instruction": prompt,
                            "execution_request_id": f"invalid-equivalent-{position}",
                        },
                    )
                    self.assertIsNone(decision["primary"])
                    self.assertEqual(decision["capability_fallbacks"], [])
                    self.assertIn(
                        "CAPABILITY_FALLBACK_SEMANTIC_INVALID",
                        decision["reason_codes"],
                    )

            allowlisted = mutate_fallback(
                selected_capability="skill:security-best-practices",
                equivalent_capabilities=["skill:security-best-practices"],
            )
            allowed = index.resolve_route(
                prompt,
                manifest=synthetic_manifest(
                    allowlisted, "skill:security-best-practices"
                ),
                policy=allowlisted,
                task_input={
                    "instruction": prompt,
                    "execution_request_id": "allowlisted-equivalent",
                },
            )
        self.assertEqual(allowed["primary"]["id"], "skill:security-best-practices")
        fallback = allowed["capability_fallbacks"][0]
        self.assertEqual(fallback["equivalence"], "equivalent")
        self.assertEqual(fallback["selected_capability"], allowed["primary"]["id"])

    def test_configured_dependency_requires_request_bound_callable_probe(
        self,
    ) -> None:
        policy = index.load_routing_policy(self.policy_path)
        manifest = synthetic_manifest(policy)
        self.config_path.write_text(
            '\n'.join(
                [
                    '[plugins."codex-security@openai-curated-remote".mcp_servers.codex-security]',
                    "enabled = true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        deep_prompt = "Run a deep security scan of this repository"
        standard_prompt = "Run a standard security scan of this repository"

        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            configured_only = index.resolve_route(
                deep_prompt,
                manifest=manifest,
                policy=policy,
                task_input={
                    "instruction": deep_prompt,
                    "execution_request_id": "configured-without-probe",
                },
            )
            self.assertEqual(
                configured_only["primary"]["id"], "skill:security-best-practices"
            )
            self.assertIn(
                "probe:mcp:codex-security:missing",
                configured_only["capability_fallbacks"][0][
                    "unavailable_dependencies"
                ],
            )

            callable_route = index.resolve_route(
                deep_prompt,
                manifest=manifest,
                policy=policy,
                task_input={
                    "instruction": deep_prompt,
                    "execution_request_id": "callable-live-probe",
                    "live_dependency_probes": live_dependency_probe(
                        "mcp:codex-security",
                        request_id="callable-live-probe",
                    ),
                },
            )
            self.assertEqual(
                callable_route["primary"]["id"],
                "skill:codex-security:deep-security-scan",
            )
            self.assertEqual(callable_route["capability_fallbacks"], [])

            for status in ("auth_failed", "tool_failed", "target_failed"):
                for prompt, expected_primary, expected_equivalence in (
                    (deep_prompt, "skill:security-best-practices", "non_equivalent"),
                    (
                        standard_prompt,
                        "skill:codex-security:security-scan",
                        "equivalent",
                    ),
                ):
                    request_id = f"{status}-{expected_equivalence}"
                    with self.subTest(status=status, prompt=prompt):
                        decision = index.resolve_route(
                            prompt,
                            manifest=manifest,
                            policy=policy,
                            task_input={
                                "instruction": prompt,
                                "execution_request_id": request_id,
                                "live_dependency_probes": live_dependency_probe(
                                    "mcp:codex-security",
                                    request_id=request_id,
                                    status=status,
                                ),
                            },
                        )
                        self.assertEqual(
                            decision["primary"]["id"], expected_primary
                        )
                        fallback = decision["capability_fallbacks"][0]
                        self.assertEqual(
                            fallback["equivalence"], expected_equivalence
                        )
                        self.assertEqual(
                            fallback["selected_capability"],
                            decision["primary"]["id"],
                        )
                        self.assertIn(
                            f"probe:mcp:codex-security:{status}",
                            fallback["unavailable_dependencies"],
                        )

            mismatched_target = index.resolve_route(
                deep_prompt,
                manifest=manifest,
                policy=policy,
                task_input={
                    "instruction": deep_prompt,
                    "execution_request_id": "probe-target-mismatch",
                    "live_dependency_probes": live_dependency_probe(
                        "mcp:codex-security",
                        request_id="probe-target-mismatch",
                        target="mcp:different-target",
                    ),
                },
            )
            mismatched_request = index.resolve_route(
                deep_prompt,
                manifest=manifest,
                policy=policy,
                task_input={
                    "instruction": deep_prompt,
                    "execution_request_id": "probe-request-expected",
                    "live_dependency_probes": live_dependency_probe(
                        "mcp:codex-security",
                        request_id="probe-request-different",
                    ),
                },
            )
        self.assertIn(
            "probe:mcp:codex-security:invalid",
            mismatched_target["capability_fallbacks"][0]["unavailable_dependencies"],
        )
        self.assertIn(
            "probe:mcp:codex-security:invalid",
            mismatched_request["capability_fallbacks"][0][
                "unavailable_dependencies"
            ],
        )

    def test_callable_probe_cannot_be_rebound_to_another_routing_prompt(
        self,
    ) -> None:
        policy = index.load_routing_policy(self.policy_path)
        manifest = synthetic_manifest(policy)
        self.config_path.write_text(
            "\n".join(
                [
                    '[plugins."codex-security@openai-curated-remote".mcp_servers.codex-security]',
                    "enabled = true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        prompt = "Run a deep security scan of this repository"
        request_id = "probe-rebinding-attempt"
        callable_probe = live_dependency_probe(
            "mcp:codex-security",
            request_id=request_id,
        )
        cases = (
            (
                "Summarize an unrelated document",
                None,
            ),
            (
                prompt,
                "Summarize an unrelated document",
            ),
        )

        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            for position, (instruction, task_text) in enumerate(cases):
                with self.subTest(instruction=instruction, task_text=task_text):
                    decision = index.resolve_route(
                        prompt,
                        manifest=manifest,
                        policy=policy,
                        task_text=task_text,
                        task_input={
                            "instruction": instruction,
                            "execution_request_id": f"{request_id}-{position}",
                            "live_dependency_probes": {
                                "mcp:codex-security": {
                                    **callable_probe["mcp:codex-security"],
                                    "request_id": f"{request_id}-{position}",
                                }
                            },
                        },
                    )
                    self.assertEqual(
                        decision["primary"]["id"],
                        "skill:security-best-practices",
                    )
                    self.assertEqual(
                        decision["capability_fallbacks"][0]["equivalence"],
                        "non_equivalent",
                    )
                    self.assertIn(
                        "probe:mcp:codex-security:request_unbound",
                        decision["capability_fallbacks"][0][
                            "unavailable_dependencies"
                        ],
                    )
                    self.assertEqual(
                        decision["task_input_mode"],
                        "conservative_instruction_only",
                    )
                    self.assertIn(
                        "TASK_INPUT_INSTRUCTION_MISMATCH",
                        decision["reason_codes"],
                    )

    def test_registry_v3_is_exact_concurrent_bounded_expiring_and_purges_v2(
        self,
    ) -> None:
        policy = index.load_routing_policy(self.policy_path)
        manifest = synthetic_manifest(policy)
        decisions: list[dict[str, object]] = []
        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            for item in range(4):
                decisions.append(
                    index.resolve_route(
                        f"synthetic unmatched request {item}",
                        manifest=manifest,
                        policy=policy,
                        task_input=complete_task_input(
                            f"bounded synthetic task {item}",
                            request_id=f"registry-task-{item}",
                        ),
                    )
                )

        concurrent_path = self.root / "concurrent.sqlite3"
        with ThreadPoolExecutor(max_workers=4) as executor:
            receipts = list(
                executor.map(
                    lambda pair: index._issue_route_decision(
                        pair[1],
                        registry_path=concurrent_path,
                        issued_at=1000 + pair[0],
                        max_records=10,
                    ),
                    enumerate(decisions),
                )
            )
        self.assertEqual(len(receipts), 4)
        with closing(sqlite3.connect(concurrent_path)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal"
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM route_decisions").fetchone()[0],
                4,
            )

        tamper_path = self.root / "tampered.sqlite3"
        index._issue_route_decision(
            decisions[0], registry_path=tamper_path, issued_at=1000
        )
        with closing(sqlite3.connect(tamper_path)) as connection:
            connection.execute(
                "UPDATE route_decisions SET route_json = ? WHERE decision_id = ?",
                ("{}", decisions[0]["decision_id"]),
            )
            connection.commit()
        self.assertEqual(
            self._verify_with_current_authority(
                decisions[0], registry_path=tamper_path, now=1000
            )["status"],
            "route_mismatch",
        )

        capacity_path = self.root / "capacity.sqlite3"
        index._issue_route_decision(
            decisions[0], registry_path=capacity_path, issued_at=1000, max_records=1
        )
        with self.assertRaisesRegex(index.RouteRegistryError, "active issuance capacity"):
            index._issue_route_decision(
                decisions[1],
                registry_path=capacity_path,
                issued_at=1001,
                max_records=1,
            )
        self.assertTrue(
            self._verify_with_current_authority(
                decisions[0], registry_path=capacity_path, now=1001
            )["valid"]
        )
        index._issue_route_decision(
            decisions[1],
            registry_path=capacity_path,
            issued_at=1000 + index.DEFAULT_ROUTE_TTL_SECONDS + 1,
            max_records=1,
        )
        self.assertTrue(
            self._verify_with_current_authority(
                decisions[1],
                registry_path=capacity_path,
                now=1000 + index.DEFAULT_ROUTE_TTL_SECONDS + 1,
            )["valid"]
        )
        self.assertEqual(
            self._verify_with_current_authority(
                decisions[0],
                registry_path=capacity_path,
                now=1000 + index.DEFAULT_ROUTE_TTL_SECONDS + 1,
            )["status"],
            "expired",
        )

        v2_path = self.root / "v2.sqlite3"
        with closing(sqlite3.connect(v2_path)) as connection:
            connection.execute(
                """
                CREATE TABLE route_decisions (
                    decision_id TEXT PRIMARY KEY,
                    decision_digest TEXT NOT NULL,
                    task_text_sha256 TEXT NOT NULL,
                    task_input_sha256 TEXT NOT NULL,
                    route_json TEXT NOT NULL,
                    route_json_sha256 TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    manifest_snapshot TEXT NOT NULL,
                    decision_snapshot TEXT NOT NULL,
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )
            connection.execute("PRAGMA user_version = 2")
            connection.execute(
                "INSERT INTO route_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "1" * 64,
                    "1" * 64,
                    "2" * 64,
                    "4" * 64,
                    "{}",
                    "3" * 64,
                    "2.0",
                    "obsolete-manifest",
                    "obsolete-policy",
                    1,
                    2,
                ),
            )
            connection.commit()
        index._issue_route_decision(decisions[2], registry_path=v2_path, issued_at=1000)
        with closing(sqlite3.connect(v2_path)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                index.ROUTE_REGISTRY_SCHEMA_VERSION,
            )
            rows = connection.execute(
                "SELECT decision_id, task_input_sha256 FROM route_decisions"
            ).fetchall()
        self.assertEqual(
            rows,
            [(decisions[2]["decision_id"], decisions[2]["task_input_sha256"])],
        )

        counterfeit_path = self.root / "counterfeit-v3.sqlite3"
        with closing(sqlite3.connect(counterfeit_path)) as connection:
            columns = [
                f"{name} TEXT" + (" PRIMARY KEY" if name == "decision_id" else "")
                for name in index.ROUTE_REGISTRY_COLUMNS
            ]
            connection.execute(f"CREATE TABLE route_decisions ({','.join(columns)})")
            connection.execute("PRAGMA user_version = 3")
            connection.execute(
                "INSERT INTO route_decisions VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                tuple("junk" for _ in columns),
            )
            connection.commit()
        index._issue_route_decision(
            decisions[3], registry_path=counterfeit_path, issued_at=1000
        )
        with closing(sqlite3.connect(counterfeit_path)) as connection:
            self.assertTrue(index._registry_schema_is_exact(connection))
            self.assertEqual(
                connection.execute("SELECT decision_id FROM route_decisions").fetchall(),
                [(decisions[3]["decision_id"],)],
            )

        malformed_path = self.root / "malformed-row.sqlite3"
        index._issue_route_decision(
            decisions[0], registry_path=malformed_path, issued_at=1000
        )
        with closing(sqlite3.connect(malformed_path)) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE route_decisions SET issued_at = ? WHERE decision_id = ?",
                ("not-an-integer", decisions[0]["decision_id"]),
            )
            connection.commit()
        self.assertEqual(
            self._verify_with_current_authority(
                decisions[0], registry_path=malformed_path, now=1000
            )["status"],
            "registry_error",
        )

    def test_worker_negation_and_generic_scope_fail_closed(self) -> None:
        self.assertTrue(index._prompt_negates_any("Do not use Terra", ["terra"]))
        self.assertFalse(index._prompt_negates_any("Use Terra", ["terra"]))
        with mock.patch.object(index, "PROJECT_SOURCE_SCOPES", {"generic": []}):
            scopes, valid, reason = index._structured_source_scopes(
                {
                    "source_need": "index",
                    "requested_source_scopes": ["sample_project"],
                },
                "generic",
            )
        self.assertEqual(scopes, [])
        self.assertFalse(valid)
        self.assertEqual(reason, "SOURCE_SCOPE_UNAUTHORIZED")

    def test_worker_admission_requires_one_exact_complete_task_gate_tuple(self) -> None:
        self._enable_local_gateway()
        policy = index.load_routing_policy(self.policy_path)
        manifest = synthetic_manifest(policy, "mcp:codex-stability-gateway")
        classification = complete_classification(
            "local_coding_eligible",
            "focused_coding_assistance",
            "local_support_required",
            worker_family="local_agent_stack",
        )
        task_input = complete_task_input(
            "bounded code generation",
            request_id="complete-local-task-gate",
            worker_family="local_agent_stack",
        )
        incomplete = copy.deepcopy(classification)
        incomplete.pop("persistence_intent")

        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            admitted = index.resolve_route(
                "bounded code generation",
                manifest=manifest,
                policy=policy,
                classification=classification,
                task_input=task_input,
            )
            rejected = index.resolve_route(
                "bounded code generation",
                manifest=manifest,
                policy=policy,
                classification=incomplete,
                task_input={**task_input, "execution_request_id": "incomplete-task-gate"},
            )

        self.assertTrue(admitted["local_execution"]["admitted"])
        self.assertEqual(
            [worker["role"] for worker in admitted["support_workers"]],
            ["coding", "critic"],
        )
        self.assertEqual(admitted["task_input_mode"], "complete")
        self.assertEqual(admitted["issuance"]["status"], "registered")
        matching_manifest = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "authority_sha256": admitted["manifest_authority_sha256"],
        }
        matching_policy = {
            "authority_sha256": admitted["policy_authority_sha256"],
        }
        with mock.patch.object(
            index, "load_active_capabilities", return_value=matching_manifest
        ), mock.patch.object(
            index, "load_routing_policy", return_value=matching_policy
        ), mock.patch.object(
            index, "_selected_route_skill_hashes_current", return_value=True
        ), mock.patch.object(
            index, "_route_worker_identity_current", return_value=True
        ):
            self.assertTrue(
                index._route_execution_ready_with_runtime(
                    admitted,
                    task_text=task_input["instruction"],
                    task_input=task_input,
                    registry_path=self.registry,
                )
            )
            self.assertFalse(index.route_execution_ready(admitted))
            wrong_input = {**task_input, "execution_request_id": "different-request"}
            self.assertFalse(
                index._route_execution_ready_with_runtime(
                    admitted,
                    task_text=task_input["instruction"],
                    task_input=wrong_input,
                    registry_path=self.registry,
                )
            )

        changed_bom_manifest = {
            **matching_manifest,
            "worker_runtime_bom_status": "changed",
            "source_hashes": {
                index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY: "a" * 64
            },
        }
        with mock.patch.object(
            index, "load_active_capabilities", return_value=changed_bom_manifest
        ), mock.patch.object(
            index, "load_routing_policy", return_value=matching_policy
        ), mock.patch.object(
            index, "_selected_route_skill_hashes_current", return_value=True
        ):
            self.assertEqual(
                index.verify_registered_route(
                    admitted, registry_path=self.registry
                )["status"],
                "capability_quarantined",
            )

        identity_drift_manifest = {
            **matching_manifest,
            "worker_runtime_bom_status": "current",
            "source_hashes": {
                index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY: "a" * 64
            },
        }
        with mock.patch.object(
            index, "load_active_capabilities", return_value=identity_drift_manifest
        ), mock.patch.object(
            index, "load_routing_policy", return_value=matching_policy
        ), mock.patch.object(
            index, "_selected_route_skill_hashes_current", return_value=True
        ), mock.patch.object(
            index, "_gateway_managed_upstream_configured", return_value=False
        ):
            self.assertEqual(
                index.verify_registered_route(
                    admitted, registry_path=self.registry
                )["status"],
                "capability_quarantined",
            )

        with mock.patch.object(
            index, "load_active_capabilities", return_value=identity_drift_manifest
        ), mock.patch.object(
            index, "load_routing_policy", return_value=matching_policy
        ), mock.patch.object(
            index, "_selected_route_skill_hashes_current", return_value=False
        ), mock.patch.object(
            index, "_route_worker_identity_current", return_value=True
        ):
            self.assertEqual(
                index.verify_registered_route(
                    admitted, registry_path=self.registry
                )["status"],
                "capability_quarantined",
            )
            tampered = copy.deepcopy(admitted)
            tampered["decision_digest"] = "0" * 64
            self.assertFalse(
                index._route_execution_ready_with_runtime(
                    tampered,
                    task_text=task_input["instruction"],
                    task_input=task_input,
                    registry_path=self.registry,
                )
            )
            empty_authority = copy.deepcopy(admitted)
            empty_authority["manifest_authority_sha256"] = ""
            empty_authority["decision_id"] = ""
            empty_authority["decision_digest"] = ""
            empty_digest = index._decision_digest(empty_authority)
            empty_authority["decision_id"] = empty_digest
            empty_authority["decision_digest"] = empty_digest
            with self.assertRaisesRegex(
                index.CapabilityDataError, "schema validation failed"
            ):
                index.validate_route_decision(empty_authority)
            canonical_registry = index.ROUTE_DECISION_REGISTRY_PATH
            with mock.patch.object(
                index, "ROUTE_DECISION_REGISTRY_PATH", self.root / "missing-canonical.sqlite3"
            ):
                self.assertFalse(
                    index.route_execution_ready(
                        admitted,
                        task_text=task_input["instruction"],
                        task_input=task_input,
                    )
                )
            self.assertEqual(index.ROUTE_DECISION_REGISTRY_PATH, canonical_registry)
        self.assertFalse(rejected["local_execution"]["admitted"])
        self.assertEqual(rejected["support_workers"], [])
        self.assertIn("WORKER_TASK_GATE_TUPLE_INVALID", rejected["reason_codes"])

    def test_local_instruction_size_boundary_returns_only_oversize_to_codex(
        self,
    ) -> None:
        self._enable_local_gateway()
        policy = index.load_routing_policy(self.policy_path)
        manifest = synthetic_manifest(policy, "mcp:codex-stability-gateway")
        classification = complete_classification(
            "local_coding_eligible",
            "focused_coding_assistance",
            "local_support_required",
            worker_family="local_agent_stack",
        )
        accepted_instruction = "x" * index.MAX_LOCAL_INSTRUCTION_CHARACTERS
        rejected_instruction = accepted_instruction + "y"

        with mock.patch.object(index, "_entry_hash_current", return_value=True):
            accepted = index.resolve_route(
                accepted_instruction,
                manifest=manifest,
                policy=policy,
                classification=classification,
                task_input=complete_task_input(
                    accepted_instruction,
                    request_id="local-size-accepted",
                    worker_family="local_agent_stack",
                ),
            )
            rejected = index.resolve_route(
                rejected_instruction,
                manifest=manifest,
                policy=policy,
                classification=classification,
                task_input=complete_task_input(
                    rejected_instruction,
                    request_id="local-size-rejected",
                    worker_family="local_agent_stack",
                ),
            )

        self.assertTrue(accepted["local_execution"]["admitted"])
        self.assertNotIn(
            "LOCAL_INPUT_TOO_LARGE_RETURNED_TO_CODEX", accepted["reason_codes"]
        )
        self.assertFalse(rejected["local_execution"]["admitted"])
        self.assertEqual(rejected["support_workers"], [])
        self.assertIn(
            "LOCAL_INPUT_TOO_LARGE_RETURNED_TO_CODEX", rejected["reason_codes"]
        )
        self.assertEqual(rejected["execution_owner"], "codex_parent")

    def test_prompt_hook_no_match_and_malformed_policy_stay_conservative(self) -> None:
        hook = RUNTIME_ROOT / "user_prompt_skill_router.py"
        valid_policy = self.policy_path.read_text(encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "CODEX_HOME": str(self.codex_home),
                "CODEX_CAPABILITY_ROUTING_DIR": str(self.routing_dir),
                "CODEX_ACTIVE_CAPABILITIES_PATH": str(self.manifest_path),
                "CODEX_ROUTING_POLICY_PATH": str(self.policy_path),
                "CODEX_CONFIG_PATH": str(self.config_path),
                "CODEX_ROUTE_DECISION_SCHEMA_PATH": str(self.schema_path),
                "CODEX_ROUTE_DECISION_REGISTRY_PATH": str(self.registry),
                "CODEX_PROJECT_SCOPE_MAP_PATH": str(self.project_map_path),
            }
        )

        for label, policy_text in (
            ("no-match", valid_policy),
            ("malformed-policy", "{not valid json"),
        ):
            self.policy_path.write_text(policy_text, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-B", str(hook)],
                input=json.dumps({"prompt": "synthetic unmatched request"}),
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            with self.subTest(label=label):
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                hook_output = payload["hookSpecificOutput"]
                self.assertEqual(hook_output["hookEventName"], "UserPromptSubmit")
                marker = "ROUTE_DECISION_JSON="
                self.assertIn(marker, hook_output["additionalContext"])
                decision = json.loads(
                    hook_output["additionalContext"].split(marker, 1)[1]
                )
                self.assertEqual(
                    decision["task_input_mode"], "conservative_instruction_only"
                )
                self.assertEqual(decision["execution_owner"], "codex_parent")
                self.assertEqual(decision["support_workers"], [])
                self.assertFalse(decision["local_execution"]["admitted"])

        self.policy_path.write_text(valid_policy, encoding="utf-8")

    def test_reference_runtime_is_never_installed_or_activated(self) -> None:
        pack = json.loads((REPO_ROOT / "pack.manifest.json").read_text("utf-8"))
        serialized = json.dumps(pack).lower()
        self.assertNotIn("reference-runtime", serialized)
        self.assertNotIn("hooks/capability-router", serialized)
        self.assertNotIn("capability_refresh_cli", serialized)
        hooks = REPO_ROOT / "hooks.json"
        if hooks.exists():
            self.assertNotIn(
                "reference-runtime", hooks.read_text("utf-8", errors="replace").lower()
            )

    def test_ci_installs_the_exact_router_test_dependency(self) -> None:
        requirements = (
            REPO_ROOT / "capability-routing" / "requirements-test.txt"
        ).read_text(encoding="utf-8")
        self.assertEqual(requirements, "jsonschema==4.26.0\n")
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "validate.yml"
        ).read_text(encoding="utf-8")
        install_command = (
            "python -m pip install -r capability-routing/requirements-test.txt"
        )
        self.assertEqual(workflow.count(install_command), 2)
        install_bundle = json.loads(
            (REPO_ROOT / "install-bundle.manifest.json").read_text(encoding="utf-8")
        )
        self.assertNotIn(
            "capability-routing/requirements-test.txt",
            {entry["path"] for entry in install_bundle["entries"]},
        )
        package_script = (REPO_ROOT / "scripts" / "package.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '"capability-routing/requirements-test.txt"', package_script
        )

    def test_cli_accepts_stdin_and_environment_path_overrides_without_live_state(self) -> None:
        env = os.environ.copy()
        cli_root = self.root / "cli"
        env.update(
            {
                "CODEX_HOME": str(cli_root),
                "CODEX_CAPABILITY_ROUTING_DIR": str(cli_root / "routing"),
                "CODEX_ACTIVE_CAPABILITIES_PATH": str(cli_root / "missing.json"),
                "CODEX_ROUTING_POLICY_PATH": str(ROUTING_ROOT / "routing-policy.yaml"),
                "CODEX_CONFIG_PATH": str(cli_root / "config.toml"),
                "CODEX_ROUTE_DECISION_SCHEMA_PATH": str(
                    ROUTING_ROOT / "route-decision.schema.json"
                ),
                "CODEX_ROUTE_DECISION_REGISTRY_PATH": str(cli_root / "routes.sqlite3"),
                "CODEX_PROJECT_SCOPE_MAP_PATH": str(cli_root / "project-map.json"),
            }
        )
        cli = RUNTIME_ROOT / "capability_index_cli.py"
        result = subprocess.run(
            [sys.executable, "-B", str(cli), "--query", "x", "--task-input-json", "-"],
            input="[]",
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "task_input_invalid")
        self.assertFalse((cli_root / "routes.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
