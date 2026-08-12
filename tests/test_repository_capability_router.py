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
                "Validate my analysis and challenge its assumptions.",
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
                "Analyze the ×­7îÚ$z{-®éÜj×)],
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
        ), mock.patch.object(index, "load_routing_policy", return_value=matching_policy):
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
