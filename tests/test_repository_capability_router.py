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
                "Analyze the ×­7òÚ$z{-®éÜj×ÒÀ¢ ¢6÷VçFW&fV—E÷F‚Ò6VÆbç&ö÷Bò&6÷VçFW&fV—B×c2ç7Æ—FS2 ¢v—F‚6Æ÷6–ær‡7Æ—FS2æ6öææV7B†6÷VçFW&fV—E÷F‚’’26öææV7F–öã ¢6öÇVÖç2Ò°¢b'¶æÖWÒDU…B"²‚"$”Ô%’´U’"–bæÖRÓÒ&FV6—6–öåö–B"VÇ6R""¢f÷"æÖR–â–æFW‚å$õUDUõ$Tt•5E%•ô4ôÅTÔå0¢Ð¢6öææV7F–öâæW†V7WFR†b$5$TDRD$ÄR&÷WFUöFV6—6–öç2‡²rÂræ¦ö–â†6öÇVÖç2—Ò’"¢6öææV7F–öâæW†V7WFR‚%$tÔW6W%÷fW'6–öâÒ2"¢6öææV7F–öâæW†V7WFR€¢$”å4U%B”åDò&÷WFUöFV6—6–öç2dÅTU2‚ ¢²"Â"æ¦ö–â‚#ò"f÷"ò–â6öÇVÖç2¢²"’"À¢GWÆR‚&§Væ²"f÷"ò–â6öÇVÖç2’À¢¢6öææV7F–öâæ6öÖÖ—B‚¢–æFW‚åö—77VU÷&÷WFUöFV6—6–öâ€¢FV6—6–öç5³5ÒÂ&Vv—7G'•÷FƒÖ6÷VçFW&fV—E÷F‚Â—77VVEöCÓ ¢¢v—F‚6Æ÷6–ær‡7Æ—FS2æ6öææV7B†6÷VçFW&fV—E÷F‚’’26öææV7F–öã ¢6VÆbæ76W'EG'VR†–æFW‚å÷&Vv—7G'•÷66†VÖö—5öW†7B†6öææV7F–öâ’¢6VÆbæ76W'DWVÂ€¢6öææV7F–öâæW†V7WFR‚%4TÄT5BFV6—6–öåö–Be$ôÒ&÷WFUöFV6—6–öç2"’æfWF6†ÆÂ‚’À¢²†FV6—6–öç5³5Õ²&FV6—6–öåö–B%ÒÂ•ÒÀ¢ ¢ÖÆf÷&ÖVE÷F‚Ò6VÆbç&ö÷Bò&ÖÆf÷&ÖVB×&÷rç7Æ—FS2 ¢–æFW‚åö—77VU÷&÷WFUöFV6—6–öâ€¢FV6—6–öç5³ÒÂ&Vv—7G'•÷FƒÖÖÆf÷&ÖVE÷F‚Â—77VVEöCÓ ¢¢v—F‚6Æ÷6–ær‡7Æ—FS2æ6öææV7B†ÖÆf÷&ÖVE÷F‚’’26öææV7F–öã ¢6öææV7F–öâæW†V7WFR‚%$tÔ–væ÷&Uö6†V6µö6öç7G&–çG2Òôâ"¢6öææV7F–öâæW†V7WFR€¢%UDDR&÷WFUöFV6—6–öç24UB—77VVEöBÒòt„U$RFV6—6–öåö–BÒò"À¢‚&æ÷BÖâÖ–çFVvW""ÂFV6—6–öç5³Õ²&FV6—6–öåö–B%Ò’À¢¢6öææV7F–öâæ6öÖÖ—B‚¢6VÆbæ76W'DWVÂ€¢6VÆbå÷fW&–g•÷v—F…ö7W'&VçEöWF†÷&—G’€¢FV6—6–öç5³ÒÂ&Vv—7G'•÷FƒÖÖÆf÷&ÖVE÷F‚Âæ÷sÓ ¢•²'7FGW2%ÒÀ¢'&Vv—7G'•öW'&÷""À¢ ¢FVbFW7E÷v÷&¶W%öæVvF–öåöæEövVæW&–5÷66÷Uöf–Åö6Æ÷6VB‡6VÆb’ÓâæöæS ¢6VÆbæ76W'EG'VR†–æFW‚å÷&ö×EöæVvFW5öç’‚$Fòæ÷BW6RFW'&"Â²'FW'&%Ò’¢6VÆbæ76W'DfÇ6R†–æFW‚å÷&ö×EöæVvFW5öç’‚%W6RFW'&"Â²'FW'&%Ò’¢v—F‚Öö6²çF6‚æö&¦V7B†–æFW‚Â%$ô¤T5Eõ4õU$4Uõ44õU2"Â²&vVæW&–2#¢µ×Ò“ ¢66÷W2ÂfÆ–BÂ&V6öâÒ–æFW‚å÷7G'V7GW&VE÷6÷W&6U÷66÷W2€¢°¢'6÷W&6UöæVVB#¢&–æFW‚"À¢'&WVW7FVE÷6÷W&6U÷66÷W2#¢²'6×ÆU÷&ö¦V7B%ÒÀ¢ÒÀ¢&vVæW&–2"À¢¢6VÆbæ76W'DWVÂ‡66÷W2ÂµÒ¢6VÆbæ76W'DfÇ6R‡fÆ–B¢6VÆbæ76W'DWVÂ‡&V6öâÂ%4õU$4Uõ44õUõTäUD„õ$•¤TB" ¢FVbFW7E÷v÷&¶W%öFÖ—76–öå÷&WV—&W5ööæUöW†7Eö6ö×ÆWFU÷F6µövFU÷GWÆR‡6VÆb’ÓâæöæS ¢6VÆbåöVæ&ÆUöÆö6ÅövFWv’‚¢öÆ–7’Ò–æFW‚æÆöE÷&÷WF–æu÷öÆ–7’‡6VÆbçöÆ–7•÷F‚¢Öæ–fW7BÒ7–çF†WF–5öÖæ–fW7B‡öÆ–7’Â&Ö7¦6öFW‚×7F&–Æ—G’ÖvFWv’"¢6Æ76–f–6F–öâÒ6ö×ÆWFUö6Æ76–f–6F–öâ€¢&Æö6Åö6öF–æuöVÆ–v–&ÆR"À¢&fö7W6VEö6öF–æuö76—7Fæ6R"À¢&Æö6Å÷7W÷'E÷&WV—&VB"À¢v÷&¶W%öfÖ–Ç“Ò&Æö6ÅövVçE÷7F6²"À¢¢F6µö–çWBÒ6ö×ÆWFU÷F6µö–çWB€¢&&÷VæFVB6öFRvVæW&F–öâ"À¢&WVW7Eö–CÒ&6ö×ÆWFRÖÆö6Â×F6²ÖvFR"À¢v÷&¶W%öfÖ–Ç“Ò&Æö6ÅövVçE÷7F6²"À¢¢–æ6ö×ÆWFRÒ6÷’æFVW6÷’†6Æ76–f–6F–öâ¢–æ6ö×ÆWFRç÷‚'W'6—7FVæ6Uö–çFVçB" ¢v—F‚Öö6²çF6‚æö&¦V7B†–æFW‚Â%öVçG'•ö†6…ö7W'&VçB"Â&WGW&å÷fÇVSÕG'VR“ ¢FÖ—GFVBÒ–æFW‚ç&W6öÇfU÷&÷WFR€¢&&÷VæFVB6öFRvVæW&F–öâ"À¢Öæ–fW7CÖÖæ–fW7BÀ¢öÆ–7“×öÆ–7’À¢6Æ76–f–6F–öãÖ6Æ76–f–6F–öâÀ¢F6µö–çWC×F6µö–çWBÀ¢¢&V¦V7FVBÒ–æFW‚ç&W6öÇfU÷&÷WFR€¢&&÷VæFVB6öFRvVæW&F–öâ"À¢Öæ–fW7CÖÖæ–fW7BÀ¢öÆ–7“×öÆ–7’À¢6Æ76–f–6F–öãÖ–æ6ö×ÆWFRÀ¢F6µö–çWC×²¢§F6µö–çWBÂ&W†V7WF–öå÷&WVW7Eö–B#¢&–æ6ö×ÆWFR×F6²ÖvFR'ÒÀ¢ ¢6VÆbæ76W'EG'VR†FÖ—GFVE²&Æö6ÅöW†V7WF–öâ%Õ²&FÖ—GFVB%Ò¢6VÆbæ76W'DWVÂ€¢·v÷&¶W%²'&öÆR%Òf÷"v÷&¶W"–âFÖ—GFVE²'7W÷'E÷v÷&¶W'2%ÕÒÀ¢²&6öF–ær"Â&7&—F–2%ÒÀ¢¢6VÆbæ76W'DWVÂ†FÖ—GFVE²'F6µö–çWEöÖöFR%ÒÂ&6ö×ÆWFR"¢6VÆbæ76W'DWVÂ†FÖ—GFVE²&—77Væ6R%Õ²'7FGW2%ÒÂ'&Vv—7FW&VB"¢ÖF6†–æuöÖæ–fW7BÒ°¢&g&W6†æW75÷7FGW2#¢&g&W6‚"À¢'6÷W&6Uö†6†W5÷fW&–f–VB#¢G'VRÀ¢&WF†÷&—G•÷6†#Sb#¢FÖ—GFVE²&Öæ–fW7EöWF†÷&—G•÷6†#Sb%ÒÀ¢Ð¢ÖF6†–æu÷öÆ–7’Ò°¢&WF†÷&—G•÷6†#Sb#¢FÖ—GFVE²'öÆ–7•öWF†÷&—G•÷6†#Sb%ÒÀ¢Ð¢v—F‚Öö6²çF6‚æö&¦V7B€¢–æFW‚Â&ÆöEö7F—fUö6&–Æ—F–W2"Â&WGW&å÷fÇVSÖÖF6†–æuöÖæ–fW7@¢’ÂÖö6²çF6‚æö&¦V7B†–æFW‚Â&ÆöE÷&÷WF–æu÷öÆ–7’"Â&WGW&å÷fÇVSÖÖF6†–æu÷öÆ–7’“ ¢6VÆbæ76W'EG'VR€¢–æFW‚å÷&÷WFUöW†V7WF–öå÷&VG•÷v—F…÷'VçF–ÖR€¢FÖ—GFVBÀ¢F6µ÷FW‡C×F6µö–çWE²&–ç7G'V7F–öâ%ÒÀ¢F6µö–çWC×F6µö–çWBÀ¢&Vv—7G'•÷Fƒ×6VÆbç&Vv—7G'’À¢¢¢6VÆbæ76W'DfÇ6R†–æFW‚ç&÷WFUöW†V7WF–öå÷&VG’†FÖ—GFVB’¢w&öæuö–çWBÒ²¢§F6µö–çWBÂ&W†V7WF–öå÷&WVW7Eö–B#¢&F–ffW&VçB×&WVW7B'Ð¢6VÆbæ76W'DfÇ6R€¢–æFW‚å÷&÷WFUöW†V7WF–öå÷&VG•÷v—F…÷'VçF–ÖR€¢FÖ—GFVBÀ¢F6µ÷FW‡C×F6µö–çWE²&–ç7G'V7F–öâ%ÒÀ¢F6µö–çWC×w&öæuö–çWBÀ¢&Vv—7G'•÷Fƒ×6VÆbç&Vv—7G'’À¢¢¢F×W&VBÒ6÷’æFVW6÷’†FÖ—GFVB¢F×W&VE²&FV6—6–öåöF–vW7B%ÒÒ#"¢c@¢6VÆbæ76W'DfÇ6R€¢–æFW‚å÷&÷WFUöW†V7WF–öå÷&VG•÷v—F…÷'VçF–ÖR€¢F×W&VBÀ¢F6µ÷FW‡C×F6µö–çWE²&–ç7G'V7F–öâ%ÒÀ¢F6µö–çWC×F6µö–çWBÀ¢&Vv—7G'•÷Fƒ×6VÆbç&Vv—7G'’À¢¢¢V×G•öWF†÷&—G’Ò6÷’æFVW6÷’†FÖ—GFVB¢V×G•öWF†÷&—G•²&Öæ–fW7EöWF†÷&—G•÷6†#Sb%ÒÒ" ¢V×G•öWF†÷&—G•²&FV6—6–öåö–B%ÒÒ" ¢V×G•öWF†÷&—G•²&FV6—6–öåöF–vW7B%ÒÒ" ¢V×G•öF–vW7BÒ–æFW‚åöFV6—6–öåöF–vW7B†V×G•öWF†÷&—G’¢V×G•öWF†÷&—G•²&FV6—6–öåö–B%ÒÒV×G•öF–vW7@¢V×G•öWF†÷&—G•²&FV6—6–öåöF–vW7B%ÒÒV×G•öF–vW7@¢v—F‚6VÆbæ76W'E&—6W5&VvW‚€¢–æFW‚ä6&–Æ—G”FFW'&÷"Â'66†VÖfÆ–FF–öâf–ÆVB ¢“ ¢–æFW‚çfÆ–FFU÷&÷WFUöFV6—6–öâ†V×G•öWF†÷&—G’¢6æöæ–6Å÷&Vv—7G'’Ò–æFW‚å$õUDUôDT4•4”ôåõ$Tt•5E%•õD€¢v—F‚Öö6²çF6‚æö&¦V7B€¢–æFW‚Â%$õUDUôDT4•4”ôåõ$Tt•5E%•õD‚"Â6VÆbç&ö÷Bò&Ö—76–ærÖ6æöæ–6Âç7Æ—FS2 ¢“ ¢6VÆbæ76W'DfÇ6R€¢–æFW‚ç&÷WFUöW†V7WF–öå÷&VG’€¢FÖ—GFVBÀ¢F6µ÷FW‡C×F6µö–çWE²&–ç7G'V7F–öâ%ÒÀ¢F6µö–çWC×F6µö–çWBÀ¢¢¢6VÆbæ76W'DWVÂ†–æFW‚å$õUDUôDT4•4”ôåõ$Tt•5E%•õD‚Â6æöæ–6Å÷&Vv—7G'’¢6VÆbæ76W'DfÇ6R‡&V¦V7FVE²&Æö6ÅöW†V7WF–öâ%Õ²&FÖ—GFVB%Ò¢6VÆbæ76W'DWVÂ‡&V¦V7FVE²'7W÷'E÷v÷&¶W'2%ÒÂµÒ¢6VÆbæ76W'D–â‚%tõ$´U%õD4µôtDUõEUÄUô”ådÄ”B"Â&V¦V7FVE²'&V6öåö6öFW2%Ò ¢FVbFW7EöÆö6Åö–ç7G'V7F–öå÷6—¦Uö&÷VæF'•÷&WGW&ç5ööæÇ•ö÷fW'6—¦U÷Fõö6öFW‚€¢6VÆbÀ¢’ÓâæöæS ¢6VÆbåöVæ&ÆUöÆö6ÅövFWv’‚¢öÆ–7’Ò–æFW‚æÆöE÷&÷WF–æu÷öÆ–7’‡6VÆbçöÆ–7•÷F‚¢Öæ–fW7BÒ7–çF†WF–5öÖæ–fW7B‡öÆ–7’Â&Ö7¦6öFW‚×7F&–Æ—G’ÖvFWv’"¢6Æ76–f–6F–öâÒ6ö×ÆWFUö6Æ76–f–6F–öâ€¢&Æö6Åö6öF–æuöVÆ–v–&ÆR"À¢&fö7W6VEö6öF–æuö76—7Fæ6R"À¢&Æö6Å÷7W÷'E÷&WV—&VB"À¢v÷&¶W%öfÖ–Ç“Ò&Æö6ÅövVçE÷7F6²"À¢¢66WFVEö–ç7G'V7F–öâÒ'‚"¢–æFW‚äÔ…ôÄô4Åô”å5E%T5D”ôåô4„$5DU%0¢&V¦V7FVEö–ç7G'V7F–öâÒ66WFVEö–ç7G'V7F–öâ²'’  ¢v—F‚Öö6²çF6‚æö&¦V7B†–æFW‚Â%öVçG'•ö†6…ö7W'&VçB"Â&WGW&å÷fÇVSÕG'VR“ ¢66WFVBÒ–æFW‚ç&W6öÇfU÷&÷WFR€¢66WFVEö–ç7G'V7F–öâÀ¢Öæ–fW7CÖÖæ–fW7BÀ¢öÆ–7“×öÆ–7’À¢6Æ76–f–6F–öãÖ6Æ76–f–6F–öâÀ¢F6µö–çWCÖ6ö×ÆWFU÷F6µö–çWB€¢66WFVEö–ç7G'V7F–öâÀ¢&WVW7Eö–CÒ&Æö6Â×6—¦RÖ66WFVB"À¢v÷&¶W%öfÖ–Ç“Ò&Æö6ÅövVçE÷7F6²"À¢’À¢¢&V¦V7FVBÒ–æFW‚ç&W6öÇfU÷&÷WFR€¢&V¦V7FVEö–ç7G'V7F–öâÀ¢Öæ–fW7CÖÖæ–fW7BÀ¢öÆ–7“×öÆ–7’À¢6Æ76–f–6F–öãÖ6Æ76–f–6F–öâÀ¢F6µö–çWCÖ6ö×ÆWFU÷F6µö–çWB€¢&V¦V7FVEö–ç7G'V7F–öâÀ¢&WVW7Eö–CÒ&Æö6Â×6—¦R×&V¦V7FVB"À¢v÷&¶W%öfÖ–Ç“Ò&Æö6ÅövVçE÷7F6²"À¢’À¢ ¢6VÆbæ76W'EG'VR†66WFVE²&Æö6ÅöW†V7WF–öâ%Õ²&FÖ—GFVB%Ò¢6VÆbæ76W'Dæ÷D–â€¢$Äô4Åô”åUEõDôõôÄ$tUõ$UEU$äTEõDõô4ôDU‚"Â66WFVE²'&V6öåö6öFW2%Ð¢¢6VÆbæ76W'DfÇ6R‡&V¦V7FVE²&Æö6ÅöW†V7WF–öâ%Õ²&FÖ—GFVB%Ò¢6VÆbæ76W'DWVÂ‡&V¦V7FVE²'7W÷'E÷v÷&¶W'2%ÒÂµÒ¢6VÆbæ76W'D–â€¢$Äô4Åô”åUEõDôõôÄ$tUõ$UEU$äTEõDõô4ôDU‚"Â&V¦V7FVE²'&V6öåö6öFW2%Ð¢¢6VÆbæ76W'DWVÂ‡&V¦V7FVE²&W†V7WF–öåö÷væW"%ÒÂ&6öFW…÷&VçB" ¢FVbFW7E÷&ö×Eö†ööµöæõöÖF6…öæEöÖÆf÷&ÖVE÷öÆ–7•÷7F•ö6öç6W'fF—fR‡6VÆb’ÓâæöæS ¢†öö²Ò%TåD”ÔUõ$ôõBò'W6W%÷&ö×E÷6¶–ÆÅ÷&÷WFW"ç’ ¢fÆ–E÷öÆ–7’Ò6VÆbçöÆ–7•÷F‚ç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"¢VçbÒ÷2æVçf—&öâæ6÷’‚¢VçbçWFFR€¢°¢%•D„ôäDôåEu$•DT%•DT4ôDR#¢#"À¢$4ôDU…ô„ôÔR#¢7G"‡6VÆbæ6öFW…ö†öÖR’À¢$4ôDU…ô4$”Ä•E•õ$õUD”äuôD•"#¢7G"‡6VÆbç&÷WF–æuöF—"’À¢$4ôDU…ô5D•dUô4$”Ä•D”U5õD‚#¢7G"‡6VÆbæÖæ–fW7E÷F‚’À¢$4ôDU…õ$õUD”äuõôÄ”5•õD‚#¢7G"‡6VÆbçöÆ–7•÷F‚’À¢$4ôDU…ô4ôäd”uõD‚#¢7G"‡6VÆbæ6öæf–u÷F‚’À¢$4ôDU…õ$õUDUôDT4•4”ôåõ44„TÔõD‚#¢7G"‡6VÆbç66†VÖ÷F‚’À¢$4ôDU…õ$õUDUôDT4•4”ôåõ$Tt•5E%•õD‚#¢7G"‡6VÆbç&Vv—7G'’’À¢$4ôDU…õ$ô¤T5Eõ44õUôÔõD‚#¢7G"‡6VÆbç&ö¦V7EöÖ÷F‚’À¢Ð¢ ¢f÷"Æ&VÂÂöÆ–7•÷FW‡B–â€¢‚&æòÖÖF6‚"ÂfÆ–E÷öÆ–7’’À¢‚&ÖÆf÷&ÖVB×öÆ–7’"Â'¶æ÷BfÆ–B§6öâ"’À¢“ ¢6VÆbçöÆ–7•÷F‚çw&—FU÷FW‡B‡öÆ–7•÷FW‡BÂVæ6öF–æsÒ'WFbÓ‚"¢6ö×ÆWFVBÒ7V'&ö6W72ç'Vâ€¢·7—2æW†V7WF&ÆRÂ"Ô""Â7G"††öö²•ÒÀ¢–çWCÖ§6öâæGV×2‡²'&ö×B#¢'7–çF†WF–2VæÖF6†VB&WVW7B'Ò’À¢FW‡CÕG'VRÀ¢6GW&Uö÷WGWCÕG'VRÀ¢VçcÖVçbÀ¢6†V6³ÔfÇ6RÀ¢¢v—F‚6VÆbç7V%FW7B†Æ&VÃÖÆ&VÂ“ ¢6VÆbæ76W'DWVÂ†6ö×ÆWFVBç&WGW&æ6öFRÂÂ6ö×ÆWFVBç7FFW'"¢–ÆöBÒ§6öâæÆöG2†6ö×ÆWFVBç7FF÷WB¢†ööµö÷WGWBÒ–ÆöE²&†ööµ7V6–f–4÷WGWB%Ð¢6VÆbæ76W'DWVÂ††ööµö÷WGWE²&†öö´WfVçDæÖR%ÒÂ%W6W%&ö×E7V&Ö—B"¢Ö&¶W"Ò%$õUDUôDT4•4”ôåô¥4ôãÒ ¢6VÆbæ76W'D–â†Ö&¶W"Â†ööµö÷WGWE²&FF—F–öæÄ6öçFW‡B%Ò¢FV6—6–öâÒ§6öâæÆöG2€¢†ööµö÷WGWE²&FF—F–öæÄ6öçFW‡B%Òç7Æ—B†Ö&¶W"Â•³Ð¢¢6VÆbæ76W'DWVÂ€¢FV6—6–öå²'F6µö–çWEöÖöFR%ÒÂ&6öç6W'fF—fUö–ç7G'V7F–öåööæÇ’ ¢¢6VÆbæ76W'DWVÂ†FV6—6–öå²&W†V7WF–öåö÷væW"%ÒÂ&6öFW…÷&VçB"¢6VÆbæ76W'DWVÂ†FV6—6–öå²'7W÷'E÷v÷&¶W'2%ÒÂµÒ¢6VÆbæ76W'DfÇ6R†FV6—6–öå²&Æö6ÅöW†V7WF–öâ%Õ²&FÖ—GFVB%Ò ¢6VÆbçöÆ–7•÷F‚çw&—FU÷FW‡B‡fÆ–E÷öÆ–7’ÂVæ6öF–æsÒ'WFbÓ‚" ¢FVbFW7E÷&VfW&Væ6U÷'VçF–ÖUö—5öæWfW%ö–ç7FÆÆVEö÷%ö7F—fFVB‡6VÆb’ÓâæöæS ¢6²Ò§6öâæÆöG2‚…$Uõõ$ôõBò'6²æÖæ–fW7Bæ§6öâ"’ç&VE÷FW‡B‚'WFbÓ‚"’¢6W&–Æ—¦VBÒ§6öâæGV×2‡6²’æÆ÷vW"‚¢6VÆbæ76W'Dæ÷D–â‚'&VfW&Væ6R×'VçF–ÖR"Â6W&–Æ—¦VB¢6VÆbæ76W'Dæ÷D–â‚&†öö·2ö6&–Æ—G’×&÷WFW""Â6W&–Æ—¦VB¢6VÆbæ76W'Dæ÷D–â‚&6&–Æ—G•÷&Vg&W6…ö6Æ’"Â6W&–Æ—¦VB¢†öö·2Ò$Uõõ$ôõBò&†öö·2æ§6öâ ¢–b†öö·2æW†—7G2‚“ ¢6VÆbæ76W'Dæ÷D–â€¢'&VfW&Væ6R×'VçF–ÖR"Â†öö·2ç&VE÷FW‡B‚'WFbÓ‚"ÂW'&÷'3Ò'&WÆ6R"’æÆ÷vW"‚¢ ¢FVbFW7Eö6•ö–ç7FÆÇ5÷F†UöW†7E÷&÷WFW%÷FW7EöFWVæFVæ7’‡6VÆb’ÓâæöæS ¢&WV—&VÖVçG2Ò€¢$Uõõ$ôõBò&6&–Æ—G’×&÷WF–ær"ò'&WV—&VÖVçG2×FW7BçG‡B ¢’ç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"¢6VÆbæ76W'DWVÂ‡&WV—&VÖVçG2Â&§6öç66†VÖÓÓBã#bãÆâ"¢v÷&¶fÆ÷rÒ€¢$Uõõ$ôõBò"æv—F‡V""ò'v÷&¶fÆ÷w2"ò'fÆ–FFRç–ÖÂ ¢’ç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"¢–ç7FÆÅö6öÖÖæBÒ€¢'—F†öâÖÒ—–ç7FÆÂ×"6&–Æ—G’×&÷WF–ær÷&WV—&VÖVçG2×FW7BçG‡B ¢¢6VÆbæ76W'DWVÂ‡v÷&¶fÆ÷ræ6÷VçB†–ç7FÆÅö6öÖÖæB’Â"¢–ç7FÆÅö'VæFÆRÒ§6öâæÆöG2€¢…$Uõõ$ôõBò&–ç7FÆÂÖ'VæFÆRæÖæ–fW7Bæ§6öâ"’ç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚"¢¢6VÆbæ76W'Dæ÷D–â€¢&6&–Æ—G’×&÷WF–ær÷&WV—&VÖVçG2×FW7BçG‡B"À¢¶VçG'•²'F‚%Òf÷"VçG'’–â–ç7FÆÅö'VæFÆU²&VçG&–W2%×ÒÀ¢¢6¶vU÷67&—BÒ…$Uõõ$ôõBò'67&—G2"ò'6¶vRç3"’ç&VE÷FW‡B€¢Væ6öF–æsÒ'WFbÓ‚ ¢¢6VÆbæ76W'D–â€¢r&6&–Æ—G’×&÷WF–ær÷&WV—&VÖVçG2×FW7BçG‡B"rÂ6¶vU÷67&—@¢ ¢FVbFW7Eö6Æ•ö66WG5÷7FF–åöæEöVçf—&öæÖVçE÷F…ö÷fW'&–FW5÷v—F†÷WEöÆ—fU÷7FFR‡6VÆb’ÓâæöæS ¢VçbÒ÷2æVçf—&öâæ6÷’‚¢6Æ•÷&ö÷BÒ6VÆbç&ö÷Bò&6Æ’ ¢VçbçWFFR€¢°¢$4ôDU…ô„ôÔR#¢7G"†6Æ•÷&ö÷B’À¢$4ôDU…ô4$”Ä•E•õ$õUD”äuôD•"#¢7G"†6Æ•÷&ö÷Bò'&÷WF–ær"’À¢$4ôDU…ô5D•dUô4$”Ä•D”U5õD‚#¢7G"†6Æ•÷&ö÷Bò&Ö—76–æræ§6öâ"’À¢$4ôDU…õ$õUD”äuõôÄ”5•õD‚#¢7G"…$õUD”äuõ$ôõBò'&÷WF–ær×öÆ–7’ç–ÖÂ"’À¢$4ôDU…ô4ôäd”uõD‚#¢7G"†6Æ•÷&ö÷Bò&6öæf–rçFöÖÂ"’À¢$4ôDU…õ$õUDUôDT4•4”ôåõ44„TÔõD‚#¢7G"€¢$õUD”äuõ$ôõBò'&÷WFRÖFV6—6–öâç66†VÖæ§6öâ ¢’À¢$4ôDU…õ$õUDUôDT4•4”ôåõ$Tt•5E%•õD‚#¢7G"†6Æ•÷&ö÷Bò'&÷WFW2ç7Æ—FS2"’À¢$4ôDU…õ$ô¤T5Eõ44õUôÔõD‚#¢7G"†6Æ•÷&ö÷Bò'&ö¦V7BÖÖæ§6öâ"’À¢Ð¢¢6Æ’Ò%TåD”ÔUõ$ôõBò&6&–Æ—G•ö–æFW…ö6Æ’ç’ ¢&W7VÇBÒ7V'&ö6W72ç'Vâ€¢·7—2æW†V7WF&ÆRÂ"Ô""Â7G"†6Æ’’Â"Ò×VW'’"Â'‚"Â"Ò×F6²Ö–çWBÖ§6öâ"Â"Ò%ÒÀ¢–çWCÒ%µÒ"À¢FW‡CÕG'VRÀ¢6GW&Uö÷WGWCÕG'VRÀ¢VçcÖVçbÀ¢6†V6³ÔfÇ6RÀ¢¢6VÆbæ76W'DWVÂ‡&W7VÇBç&WGW&æ6öFRÂ"¢6VÆbæ76W'DWVÂ†§6öâæÆöG2‡&W7VÇBç7FF÷WB•²'7FGW2%ÒÂ'F6µö–çWEö–çfÆ–B"¢6VÆbæ76W'DfÇ6R‚†6Æ•÷&ö÷Bò'&÷WFW2ç7Æ—FS2"’æW†—7G2‚’  ¦–bõöæÖUõòÓÒ%õöÖ–åõò# ¢Væ—GFW7BæÖ–â‚