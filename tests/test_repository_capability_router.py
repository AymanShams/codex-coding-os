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
    references: set[str] = set()
    for rule in policy["rules"]:
        references.add(rule["primary"])
        references.update(rule.get("supports", []))
        references.update(
            item for item in rule.get("requires", []) if not item.startswith("prompt:")
        )
        references.update(rule.get("requires_live_dependencies", []))
        fallback = rule.get("dependency_fallback") or {}
        if fallback.get("selected_capability"):
            references.add(fallback["selected_capability"])
        references.update(fallback.get("supports", []))
        references.update(fallback.get("equivalent_capabilities", []))
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
        self.root = Path(self.temp.name)
        self.codex_home = self.root / "codex-home"
        self.routing_dir = self.codex_home / "capability-routing"
        self.routing_dir.mkdir(parents=True)
        self.manifest_path = self.routing_dir / "active-capabilities.json"
        self.policy_path = self.routing_dir / "routing-policy.yaml"
        self.config_path = self.codex_home / "config.toml"
        self.schema_path = self.routing_dir / "route-decision.schema.json"
        self.registry = self.routing_dir / "route-decisions.sqlite3"
        self.project_map_path = self.routing_dir / "project-scope-map.json"
        self.policy_path.write_text(
            (ROUTING_ROOT / "routing-policy.yaml").read_text(encoding="utf-8"),
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
        for name, value in {
            "CODEX_HOME": self.codex_home,
            "ROUTING_DIR": self.routing_dir,
            "ACTIVE_CAPABILITIES_PATH": self.manifest_path,
            "ROUTING_POLICY_PATH": self.policy_path,
            "CONFIG_PATH": self.config_path,
            "ROUTE_DECISION_SCHEMA_PATH": self.schema_path,
            "ROUTE_DECISION_REGISTRY_PATH": self.registry,
            "PROJECT_SCOPE_MAP_PATH": self.project_map_path,
        }.items():
            self.patches.enter_context(mock.patch.object(index, name, value))

    def tearDown(self) -> None:
        self.patches.close()
        self.temp.cleanup()

    def _enable_local_gateway(self) -> None:
        command = self.root / "synthetic-gateway.exe"
        command.write_bytes(b"synthetic gateway")
        gateway_cwd = self.root / "gateway-cwd"
        gateway_cwd.mkdir()
        self.config_path.write_text(
            "\n".join(
                [
                    "[mcp_servers.local-agent-stack]",
                    "gateway_managed = true",
                    f"command = '{command}'",
                    f"cwd = '{gateway_cwd}'",
                    "",
                ]
            ),
            encoding="utf-8",
        )

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
        receipt = index.verify_registered_route(decision, registry_path=self.registry)
        self.assertTrue(receipt["valid"])
        with closing(sqlite3.connect(self.registry)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        tampered = copy.deepcopy(decision)
        tampered["reason_codes"].append("TAMPERED")
        self.assertFalse(
            index.verify_registered_route(tampered, registry_path=self.registry)["valid"]
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

    def test_registry_v2_is_exact_concurrent_bounded_expiring_and_purges_v1(
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
            index.verify_registered_route(
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
            index.verify_registered_route(
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
            index.verify_registered_route(
                decisions[1],
                registry_path=capacity_path,
                now=1000 + index.DEFAULT_ROUTE_TTL_SECONDS + 1,
            )["valid"]
        )
        self.assertEqual(
            index.verify_registered_route(
                decisions[0],
                registry_path=capacity_path,
                now=1000 + index.DEFAULT_ROUTE_TTL_SECONDS + 1,
            )["status"],
            "expired",
        )

        v1_path = self.root / "v1.sqlite3"
        with closing(sqlite3.connect(v1_path)) as connection:
            connection.execute(
                """
                CREATE TABLE route_decisions (
                    decision_id TEXT PRIMARY KEY,
                    decision_digest TEXT NOT NULL,
                    task_text_sha256 TEXT NOT NULL,
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
            connection.execute("PRAGMA user_version = 1")
            connection.execute(
                "INSERT INTO route_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "1" * 64,
                    "1" * 64,
                    "2" * 64,
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
        index._issue_route_decision(decisions[2], registry_path=v1_path, issued_at=1000)
        with closing(sqlite3.connect(v1_path)) as connection:
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
            REPO_ROOT / "tests" / "requirements-capability-router.txt"
        ).read_text(encoding="utf-8")
        self.assertEqual(requirements, "jsonschema==4.26.0\n")
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "validate.yml"
        ).read_text(encoding="utf-8")
        install_command = (
            "python -m pip install -r tests/requirements-capability-router.txt"
        )
        self.assertEqual(workflow.count(install_command), 2)

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
