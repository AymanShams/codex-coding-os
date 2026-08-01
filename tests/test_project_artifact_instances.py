"""Regression tests for target-project artifact selection and lineage."""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".agents" / "skills" / "new-project-documentation-system"
VALIDATOR_PATH = WORKFLOW_ROOT / "scripts" / "validate_workflow_manifest.py"
TEMPLATE_PATH = WORKFLOW_ROOT / "assets" / "project-documentation-manifest.template.json"
PACK_PATH = ROOT / "pack.manifest.json"
SPEC = importlib.util.spec_from_file_location("validate_workflow_manifest", VALIDATOR_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import workflow validator: {VALIDATOR_PATH}")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)

PACK = json.loads(PACK_PATH.read_text(encoding="utf-8"))
DEFINITIONS = {
    definition["artifact_id"]: definition for definition in PACK["artifact_definitions"]
}


def fresh_manifest() -> dict[str, object]:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def set_selection(
    instance: dict[str, object],
    status: str,
    matched_facts: set[str] | None = None,
) -> None:
    matched_facts = matched_facts or set()
    definition = DEFINITIONS[str(instance["artifact_id"])]
    predicates = definition["trigger"]["predicates"]
    instance["lifecycle_status"] = status
    instance["trigger_evidence"] = [
        {
            "fact": predicate["fact"],
            "value": (
                predicate["value"]
                if predicate["fact"] in matched_facts
                else not predicate["value"]
            ),
            "evidence": [f"docs/project-brief.md#{predicate['fact']}"],
        }
        for predicate in predicates
    ]


def mark_all_not_applicable(manifest: dict[str, object]) -> None:
    for instance in manifest["artifact_instances"]:
        set_selection(instance, "not_applicable")


def advance_to_phase_five(manifest: dict[str, object], status: str) -> None:
    phases = manifest["phases"]
    for phase in VALIDATOR.PHASES[:5]:
        phases[phase]["status"] = "completed"
    phases["5_repo_documentation"]["status"] = status
    manifest["approvals"]["material_decisions"] = True
    manifest["approvals"]["controlled_docs"] = True


class ProjectArtifactInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="coding-os-artifact-instances-")
        self.project = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def errors(self, manifest: dict[str, object]) -> list[str]:
        return VALIDATOR.validate(
            manifest,
            pack_manifest=PACK,
            project_root=self.project,
        )

    def test_fresh_version_1_1_template_passes_before_phase_five(self) -> None:
        self.assertEqual([], self.errors(fresh_manifest()))

    def test_legacy_version_1_0_without_instances_remains_valid(self) -> None:
        manifest = fresh_manifest()
        manifest["schema_version"] = "1.0"
        manifest.pop("artifact_instances")
        self.assertEqual(
            [],
            VALIDATOR.validate(manifest, pack_manifest=None, project_root=self.project),
        )

    def test_unknown_schema_version_cannot_bypass_version_1_1_coverage(self) -> None:
        manifest = fresh_manifest()
        manifest["schema_version"] = "1.2"
        manifest["artifact_instances"] = []
        self.assertTrue(any("unsupported schema_version" in error for error in self.errors(manifest)))

    def test_empty_caller_supplied_pack_cannot_bypass_instance_coverage(self) -> None:
        manifest = fresh_manifest()
        manifest["artifact_instances"] = []
        errors = VALIDATOR.validate(
            manifest,
            pack_manifest={"artifact_definitions": []},
            project_root=self.project,
        )
        self.assertTrue(any("must be a non-empty array" in error for error in errors))

    def test_stale_pack_contract_cannot_bypass_version_1_1_coverage(self) -> None:
        manifest = fresh_manifest()
        manifest["artifact_instances"] = []
        stale_pack = {
            "artifact_contract_version": "1.0",
            "artifact_definitions": [
                {
                    "artifact_id": "template.legacy",
                    "relationship": "canonical",
                    "consumers": ["legacy:manual"],
                    "trigger": {"trigger_id": "legacy", "kind": "manual"},
                    "generation_route": "legacy",
                }
            ],
        }
        errors = VALIDATOR.validate(
            manifest,
            pack_manifest=stale_pack,
            project_root=self.project,
        )
        self.assertTrue(any("requires pack artifact_contract_version 1.1" in error for error in errors))

    def test_unrelated_conditional_artifact_is_not_forced_into_every_project(self) -> None:
        manifest = fresh_manifest()
        extended_pack = copy.deepcopy(PACK)
        extended_pack["artifact_definitions"].append(
            {
                "artifact_id": "template.standalone-conditional",
                "relationship": "canonical",
                "consumers": ["installed-pack:standalone-conditional"],
                "trigger": {
                    "trigger_id": "standalone_condition",
                    "kind": "conditional",
                    "match": "all",
                    "predicates": [
                        {"fact": "standalone.enabled", "operator": "equals", "value": True}
                    ],
                },
                "generation_route": "standalone",
            }
        )
        self.assertEqual(
            [],
            VALIDATOR.validate(
                manifest,
                pack_manifest=extended_pack,
                project_root=self.project,
            ),
        )

    def test_template_instantiates_every_conditional_canonical_definition(self) -> None:
        manifest = fresh_manifest()
        actual = {instance["artifact_id"] for instance in manifest["artifact_instances"]}
        expected = {
            definition["artifact_id"]
            for definition in PACK["artifact_definitions"]
            if definition["relationship"] == "canonical"
            and definition["trigger"]["kind"] == "conditional"
        }
        self.assertEqual(expected, actual)

    def test_unknown_definition_and_lineage_route_mismatch_fail(self) -> None:
        manifest = fresh_manifest()
        manifest["artifact_instances"][0]["artifact_id"] = "template.unknown"
        self.assertTrue(any("is not defined" in error for error in self.errors(manifest)))

        manifest = fresh_manifest()
        manifest["artifact_instances"][0]["lineage"]["generation_route"] = "other-route"
        self.assertTrue(any("does not match" in error for error in self.errors(manifest)))

    def test_duplicate_instance_and_output_path_fail(self) -> None:
        manifest = fresh_manifest()
        manifest["artifact_instances"][1]["instance_id"] = manifest["artifact_instances"][0][
            "instance_id"
        ]
        manifest["artifact_instances"][1]["output_path"] = manifest["artifact_instances"][0][
            "output_path"
        ]
        errors = self.errors(manifest)
        self.assertTrue(any("duplicate artifact instance_id" in error for error in errors))
        self.assertTrue(any("duplicate artifact output_path" in error for error in errors))

    def test_unsafe_output_path_fails(self) -> None:
        manifest = fresh_manifest()
        manifest["artifact_instances"][0]["output_path"] = "../outside.md"
        self.assertTrue(any("safe project-relative path" in error for error in self.errors(manifest)))

    def test_phase_five_cannot_advance_before_conditions_are_evaluated(self) -> None:
        manifest = fresh_manifest()
        advance_to_phase_five(manifest, "in_progress")
        errors = self.errors(manifest)
        self.assertTrue(any("must be evaluated before 5_repo_documentation" in error for error in errors))

    def test_all_false_conditions_are_not_applicable(self) -> None:
        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        self.assertEqual([], self.errors(manifest))

    def test_trigger_evidence_must_be_complete_unique_and_declared(self) -> None:
        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        content = manifest["artifact_instances"][0]
        content["trigger_evidence"].pop()
        self.assertTrue(any("missing trigger evidence" in error for error in self.errors(manifest)))

        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        content = manifest["artifact_instances"][0]
        content["trigger_evidence"].append(copy.deepcopy(content["trigger_evidence"][0]))
        self.assertTrue(any("duplicate trigger evidence" in error for error in self.errors(manifest)))

        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        content = manifest["artifact_instances"][0]
        content["trigger_evidence"][0]["fact"] = "product.unknown_fact"
        self.assertTrue(any("is not declared" in error for error in self.errors(manifest)))

    def test_each_content_or_module_predicate_can_require_its_artifact(self) -> None:
        for artifact_index in (0, 2):
            definition = DEFINITIONS[
                fresh_manifest()["artifact_instances"][artifact_index]["artifact_id"]
            ]
            for predicate in definition["trigger"]["predicates"]:
                with self.subTest(artifact=definition["artifact_id"], fact=predicate["fact"]):
                    manifest = fresh_manifest()
                    mark_all_not_applicable(manifest)
                    instance = manifest["artifact_instances"][artifact_index]
                    set_selection(instance, "not_applicable", {predicate["fact"]})
                    self.assertTrue(any("required by its trigger" in error for error in self.errors(manifest)))

    def test_search_documentation_is_excluded_without_a_public_surface(self) -> None:
        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        search_instance = manifest["artifact_instances"][1]
        search_instance["lifecycle_status"] = "planned"
        self.assertTrue(any("must be not_applicable" in error for error in self.errors(manifest)))

    def test_public_surface_requires_search_documentation(self) -> None:
        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        search_instance = manifest["artifact_instances"][1]
        fact = DEFINITIONS[search_instance["artifact_id"]]["trigger"]["predicates"][0]["fact"]
        set_selection(search_instance, "not_applicable", {fact})
        self.assertTrue(any("required by its trigger" in error for error in self.errors(manifest)))

    def test_false_trigger_rejects_an_existing_output(self) -> None:
        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        content = manifest["artifact_instances"][0]
        target = self.project / content["output_path"]
        target.parent.mkdir(parents=True)
        target.write_text("# Unjustified output\n", encoding="utf-8")
        self.assertTrue(any("output exists before" in error for error in self.errors(manifest)))

    def test_completed_phase_five_rejects_a_planned_required_artifact(self) -> None:
        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        content = manifest["artifact_instances"][0]
        fact = DEFINITIONS[content["artifact_id"]]["trigger"]["predicates"][0]["fact"]
        set_selection(content, "planned", {fact})
        advance_to_phase_five(manifest, "completed")
        self.assertTrue(any("must be generated" in error for error in self.errors(manifest)))

    def test_generated_artifact_requires_output_and_lineage_evidence(self) -> None:
        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        content = manifest["artifact_instances"][0]
        fact = DEFINITIONS[content["artifact_id"]]["trigger"]["predicates"][0]["fact"]
        set_selection(content, "generated", {fact})
        errors = self.errors(manifest)
        self.assertTrue(any("output file to exist" in error for error in errors))
        self.assertTrue(any("requires controlling_sources" in error for error in errors))
        self.assertTrue(any("requires generation_evidence" in error for error in errors))

    def test_validated_required_artifact_passes_final_validation(self) -> None:
        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        content = manifest["artifact_instances"][0]
        fact = DEFINITIONS[content["artifact_id"]]["trigger"]["predicates"][0]["fact"]
        set_selection(content, "validated", {fact})
        content["lineage"]["controlling_sources"] = ["docs/project-brief.md"]
        content["lineage"]["generation_evidence"] = ["generated from the canonical template"]
        content["lineage"]["validation_evidence"] = ["content documentation check passed"]
        target = self.project / content["output_path"]
        target.parent.mkdir(parents=True)
        target.write_text("# Content Guidelines\n", encoding="utf-8")

        for phase in VALIDATOR.PHASES:
            manifest["phases"][phase]["status"] = "completed"
        manifest["approvals"]["material_decisions"] = True
        manifest["approvals"]["controlled_docs"] = True
        self.assertEqual([], self.errors(manifest))

    def test_explicit_deferral_requires_decision_evidence(self) -> None:
        manifest = fresh_manifest()
        mark_all_not_applicable(manifest)
        content = manifest["artifact_instances"][0]
        fact = DEFINITIONS[content["artifact_id"]]["trigger"]["predicates"][0]["fact"]
        set_selection(content, "explicitly_deferred", {fact})
        advance_to_phase_five(manifest, "explicitly_deferred")
        self.assertTrue(any("requires decision_evidence" in error for error in self.errors(manifest)))
        content["lineage"]["decision_evidence"] = ["explicit project-owner deferral"]
        self.assertEqual([], self.errors(manifest))


if __name__ == "__main__":
    unittest.main()
