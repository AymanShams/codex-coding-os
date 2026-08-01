#!/usr/bin/env python3
"""Validate the fail-closed workflow manifest for new project documentation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


PHASES = [
    "0_route_scope",
    "1_source_inventory",
    "2_material_decisions",
    "3_controlled_docs",
    "4_tdd_alignment",
    "5_repo_documentation",
    "6_agent_instructions",
    "7_handoff",
    "8_final_validation",
]

STATUSES = {
    "not_started",
    "in_progress",
    "blocked",
    "awaiting_approval",
    "approved",
    "completed",
    "explicitly_deferred",
}

DONE = {"approved", "completed", "explicitly_deferred"}
READY_TO_CODE = {"approved", "completed"}
ADVANCED = {"in_progress", "awaiting_approval", *DONE}
ARTIFACT_INSTANCE_STATUSES = {
    "not_evaluated",
    "not_applicable",
    "planned",
    "generated",
    "validated",
    "explicitly_deferred",
}
ARTIFACT_INSTANCE_FIELDS = {
    "instance_id",
    "artifact_id",
    "output_path",
    "lifecycle_status",
    "trigger_evidence",
    "lineage",
}
LINEAGE_FIELDS = {
    "generation_route",
    "controlling_sources",
    "decision_evidence",
    "generation_evidence",
    "validation_evidence",
}
TRIGGER_EVIDENCE_FIELDS = {"fact", "value", "evidence"}
SUPPORTED_ARTIFACT_CONTRACT_VERSION = "1.1"
PROJECT_DOCUMENTATION_CONSUMER = "new-project-documentation-system:phase-5"


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def nonempty_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(nonempty_string(item) for item in value)


def safe_relative_path(value: Any) -> bool:
    if not nonempty_string(value) or "\\" in str(value):
        return False
    text = str(value)
    if re.match(r"^[A-Za-z]:", text):
        return False
    path = PurePosixPath(text)
    return not path.is_absolute() and ".." not in path.parts and path != PurePosixPath(".")


def output_file(project_root: Path, output_root: Any, output_path: str) -> tuple[Path | None, str | None]:
    project_root = project_root.resolve()
    if nonempty_string(output_root):
        declared_root = Path(str(output_root))
        base = declared_root.resolve() if declared_root.is_absolute() else (project_root / declared_root).resolve()
    else:
        base = project_root
    try:
        base.relative_to(project_root)
    except ValueError:
        return None, "output_root resolves outside the target project"

    relative = PurePosixPath(output_path)
    candidate = (base / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None, "output_path resolves outside output_root"
    return candidate, None


def evaluate_trigger_evidence(
    trigger: dict[str, Any], evidence: Any, label: str, errors: list[str]
) -> bool | None:
    if not isinstance(evidence, list):
        fail(errors, f"{label}.trigger_evidence must be a list")
        return None
    if not evidence:
        return None

    predicates = trigger.get("predicates")
    if not isinstance(predicates, list) or not predicates:
        fail(errors, f"{label} references a conditional trigger without predicates")
        return None

    expected = {
        predicate.get("fact"): predicate.get("value")
        for predicate in predicates
        if isinstance(predicate, dict) and nonempty_string(predicate.get("fact"))
    }
    evidence_by_fact: dict[str, bool] = {}
    for index, entry in enumerate(evidence):
        entry_label = f"{label}.trigger_evidence[{index}]"
        if not isinstance(entry, dict):
            fail(errors, f"{entry_label} must be an object")
            continue
        for field in sorted(TRIGGER_EVIDENCE_FIELDS - set(entry)):
            fail(errors, f"{entry_label} is missing {field}")
        for field in sorted(set(entry) - TRIGGER_EVIDENCE_FIELDS):
            fail(errors, f"{entry_label} contains unsupported field {field}")

        fact = entry.get("fact")
        value = entry.get("value")
        proof = entry.get("evidence")
        if not nonempty_string(fact):
            fail(errors, f"{entry_label}.fact must be a non-empty string")
            continue
        fact = str(fact)
        if fact not in expected:
            fail(errors, f"{entry_label}.fact is not declared by the referenced trigger: {fact}")
        if fact in evidence_by_fact:
            fail(errors, f"{label} contains duplicate trigger evidence for {fact}")
        if not isinstance(value, bool):
            fail(errors, f"{entry_label}.value must be Boolean")
        if not nonempty_string_list(proof) or not proof:
            fail(errors, f"{entry_label}.evidence must be a non-empty array of strings")
        if fact in expected and isinstance(value, bool) and fact not in evidence_by_fact:
            evidence_by_fact[fact] = value

    missing = set(expected) - set(evidence_by_fact)
    for fact in sorted(missing):
        fail(errors, f"{label} is missing trigger evidence for {fact}")
    if missing or set(evidence_by_fact) - set(expected):
        return None

    results = [evidence_by_fact[fact] == expected[fact] for fact in expected]
    return any(results) if trigger.get("match") == "any" else all(results)


def validate_artifact_instances(
    data: dict[str, Any],
    pack_manifest: dict[str, Any] | None,
    project_root: Path,
) -> list[str]:
    errors: list[str] = []
    schema_version = data.get("schema_version")
    if schema_version not in {None, "1.0", "1.1"}:
        errors.append(f"unsupported schema_version: {schema_version!r}")
    instances = data.get("artifact_instances")
    if instances is None:
        if schema_version == "1.1":
            errors.append("schema_version 1.1 requires artifact_instances")
        return errors
    if not isinstance(instances, list):
        return ["artifact_instances must be an array"]
    if pack_manifest is None:
        return ["artifact_instances require the canonical pack.manifest.json"]

    if (
        schema_version == "1.1"
        and pack_manifest.get("artifact_contract_version") != SUPPORTED_ARTIFACT_CONTRACT_VERSION
    ):
        errors.append(
            "schema_version 1.1 requires pack artifact_contract_version "
            f"{SUPPORTED_ARTIFACT_CONTRACT_VERSION}"
        )

    definitions_value = pack_manifest.get("artifact_definitions")
    if not isinstance(definitions_value, list) or not definitions_value:
        return errors + ["pack.manifest.json artifact_definitions must be a non-empty array"]
    definitions = {
        definition.get("artifact_id"): definition
        for definition in definitions_value
        if isinstance(definition, dict) and nonempty_string(definition.get("artifact_id"))
    }
    required_conditional_ids = {
        str(artifact_id)
        for artifact_id, definition in definitions.items()
        if definition.get("relationship") == "canonical"
        and isinstance(definition.get("trigger"), dict)
        and definition["trigger"].get("kind") == "conditional"
        and PROJECT_DOCUMENTATION_CONSUMER in definition.get("consumers", [])
    }

    instance_ids: set[str] = set()
    output_paths: set[str] = set()
    instantiated_artifacts: set[str] = set()
    phase5_status = data.get("phases", {}).get("5_repo_documentation", {}).get("status")
    final_status = data.get("phases", {}).get("8_final_validation", {}).get("status")

    for index, instance in enumerate(instances):
        label = f"artifact_instances[{index}]"
        if not isinstance(instance, dict):
            fail(errors, f"{label} must be an object")
            continue
        for field in sorted(ARTIFACT_INSTANCE_FIELDS - set(instance)):
            fail(errors, f"{label} is missing {field}")
        for field in sorted(set(instance) - ARTIFACT_INSTANCE_FIELDS):
            fail(errors, f"{label} contains unsupported field {field}")

        instance_id = instance.get("instance_id")
        artifact_id = instance.get("artifact_id")
        output_path_value = instance.get("output_path")
        lifecycle_status = instance.get("lifecycle_status")
        if not nonempty_string(instance_id):
            fail(errors, f"{label}.instance_id must be a non-empty string")
        elif str(instance_id) in instance_ids:
            fail(errors, f"duplicate artifact instance_id: {instance_id}")
        else:
            instance_ids.add(str(instance_id))

        if not nonempty_string(artifact_id):
            fail(errors, f"{label}.artifact_id must be a non-empty string")
            definition = None
        else:
            artifact_id = str(artifact_id)
            instantiated_artifacts.add(artifact_id)
            definition = definitions.get(artifact_id)
            if definition is None:
                fail(errors, f"{label}.artifact_id is not defined by pack.manifest.json: {artifact_id}")

        if not safe_relative_path(output_path_value):
            fail(errors, f"{label}.output_path must be a safe project-relative path using forward slashes")
            resolved_output = None
        else:
            output_path_value = str(output_path_value)
            if output_path_value in output_paths:
                fail(errors, f"duplicate artifact output_path: {output_path_value}")
            else:
                output_paths.add(output_path_value)
            resolved_output, path_error = output_file(
                project_root, data.get("output_root"), output_path_value
            )
            if path_error:
                fail(errors, f"{label}.{path_error}")

        if lifecycle_status not in ARTIFACT_INSTANCE_STATUSES:
            fail(errors, f"{label}.lifecycle_status is invalid: {lifecycle_status!r}")

        lineage = instance.get("lineage")
        if not isinstance(lineage, dict):
            fail(errors, f"{label}.lineage must be an object")
            lineage = {}
        else:
            for field in sorted(LINEAGE_FIELDS - set(lineage)):
                fail(errors, f"{label}.lineage is missing {field}")
            for field in sorted(set(lineage) - LINEAGE_FIELDS):
                fail(errors, f"{label}.lineage contains unsupported field {field}")

        generation_route = lineage.get("generation_route")
        if not nonempty_string(generation_route):
            fail(errors, f"{label}.lineage.generation_route must be a non-empty string")
        elif definition is not None and generation_route != definition.get("generation_route"):
            fail(errors, f"{label}.lineage.generation_route does not match the pack definition")

        evidence_lists: dict[str, list[str]] = {}
        for field in (
            "controlling_sources",
            "decision_evidence",
            "generation_evidence",
            "validation_evidence",
        ):
            value = lineage.get(field)
            if not nonempty_string_list(value):
                fail(errors, f"{label}.lineage.{field} must be an array of non-empty strings")
                evidence_lists[field] = []
            else:
                evidence_lists[field] = value

        trigger = definition.get("trigger") if definition is not None else None
        trigger_kind = trigger.get("kind") if isinstance(trigger, dict) else None
        trigger_evidence = instance.get("trigger_evidence")
        if trigger_kind == "conditional":
            applicability = evaluate_trigger_evidence(trigger, trigger_evidence, label, errors)
            if lifecycle_status == "not_evaluated":
                if isinstance(trigger_evidence, list) and trigger_evidence:
                    fail(errors, f"{label}.not_evaluated requires empty trigger_evidence")
            elif applicability is None:
                fail(errors, f"{label} requires complete trigger evidence before leaving not_evaluated")
            elif applicability and lifecycle_status == "not_applicable":
                fail(errors, f"{label} is required by its trigger and cannot be not_applicable")
            elif not applicability and lifecycle_status != "not_applicable":
                fail(errors, f"{label} is not required by its trigger and must be not_applicable")

            if phase5_status in ADVANCED and lifecycle_status == "not_evaluated":
                fail(errors, f"{label} must be evaluated before 5_repo_documentation advances")
            if applicability and phase5_status in READY_TO_CODE and lifecycle_status not in {"generated", "validated"}:
                fail(errors, f"{label} must be generated before 5_repo_documentation is approved or completed")
            if (
                applicability
                and final_status in READY_TO_CODE
                and lifecycle_status not in {"validated", "explicitly_deferred"}
            ):
                fail(errors, f"{label} must be validated before 8_final_validation is approved or completed")
            if lifecycle_status == "explicitly_deferred":
                if not applicability or phase5_status != "explicitly_deferred":
                    fail(errors, f"{label} may be explicitly_deferred only with an applicable trigger and deferred phase 5")
        else:
            applicability = None
            if not isinstance(trigger_evidence, list):
                fail(errors, f"{label}.trigger_evidence must be a list")
            elif trigger_evidence:
                fail(errors, f"{label}.trigger_evidence is only valid for conditional triggers")
            if lifecycle_status == "not_applicable":
                fail(errors, f"{label}.not_applicable is only valid for conditional triggers")

        if lifecycle_status in {"not_evaluated", "not_applicable"}:
            if resolved_output is not None and resolved_output.exists():
                fail(errors, f"{label} output exists before the artifact is applicable and generated")
        if lifecycle_status in {"generated", "validated"}:
            if resolved_output is None or not resolved_output.is_file():
                fail(errors, f"{label} requires its generated output file to exist")
            if not evidence_lists.get("controlling_sources"):
                fail(errors, f"{label} requires controlling_sources once generated")
            if not evidence_lists.get("generation_evidence"):
                fail(errors, f"{label} requires generation_evidence once generated")
        if lifecycle_status == "validated" and not evidence_lists.get("validation_evidence"):
            fail(errors, f"{label} requires validation_evidence once validated")
        if lifecycle_status == "explicitly_deferred" and not evidence_lists.get("decision_evidence"):
            fail(errors, f"{label} requires decision_evidence when explicitly deferred")

    if schema_version == "1.1":
        for artifact_id in sorted(required_conditional_ids - instantiated_artifacts):
            fail(errors, f"schema_version 1.1 requires an artifact instance for {artifact_id}")

    return errors


def validate(
    data: dict[str, Any],
    *,
    pack_manifest: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    mode = data.get("mode")
    if mode not in {"full_run", "review_only", "single_phase", "resume"}:
        fail(errors, "mode must be full_run, review_only, single_phase, or resume")

    phases = data.get("phases")
    if not isinstance(phases, dict):
        return errors + ["phases must be an object"]

    in_progress = 0
    for phase in PHASES:
        entry = phases.get(phase)
        if not isinstance(entry, dict):
            fail(errors, f"missing phase: {phase}")
            continue
        status = entry.get("status")
        if status not in STATUSES:
            fail(errors, f"{phase}: invalid status {status!r}")
        if status == "in_progress":
            in_progress += 1
        evidence = entry.get("evidence")
        if not isinstance(evidence, list):
            fail(errors, f"{phase}: evidence must be a list")

    if in_progress > 1:
        fail(errors, "only one phase may be in_progress")

    errors.extend(
        validate_artifact_instances(
            data,
            pack_manifest,
            project_root or Path.cwd(),
        )
    )

    for index, phase in enumerate(PHASES[1:], start=1):
        status = phases.get(phase, {}).get("status")
        previous_phase = PHASES[index - 1]
        previous_status = phases.get(previous_phase, {}).get("status")
        if status in ADVANCED and previous_status not in DONE:
            fail(errors, f"{phase} cannot advance while {previous_phase} is {previous_status!r}")

    open_decisions = data.get("open_material_decisions")
    conflicts = data.get("unresolved_source_conflicts")
    if not isinstance(open_decisions, list):
        fail(errors, "open_material_decisions must be a list")
        open_decisions = []
    if not isinstance(conflicts, list):
        fail(errors, "unresolved_source_conflicts must be a list")
        conflicts = []

    approvals = data.get("approvals")
    if not isinstance(approvals, dict):
        fail(errors, "approvals must be an object")
        approvals = {}

    controlled_status = phases.get("3_controlled_docs", {}).get("status")
    if controlled_status in ADVANCED:
        if open_decisions:
            fail(errors, "controlled docs cannot start while material decisions remain open")
        if conflicts:
            fail(errors, "controlled docs cannot start while source conflicts remain unresolved")
        if not approvals.get("material_decisions"):
            fail(errors, "controlled docs cannot start without material_decisions approval")

    tdd_status = phases.get("4_tdd_alignment", {}).get("status")
    if tdd_status in DONE and not approvals.get("controlled_docs"):
        fail(errors, "TDD/alignment completion requires controlled_docs approval")

    next_action = data.get("next_action")
    if next_action == "code":
        if not data.get("code_allowed"):
            fail(errors, "next_action code requires code_allowed true")
        permission_manifest_path = data.get("permission_manifest_path")
        if not isinstance(permission_manifest_path, str) or not permission_manifest_path.strip():
            fail(errors, "next_action code requires permission_manifest_path")
        else:
            permission_path = Path(permission_manifest_path)
            candidates = [permission_path] if permission_path.is_absolute() else [
                Path.cwd() / permission_path,
                Path(data.get("output_root") or ".") / permission_path,
            ]
            if not any(candidate.exists() for candidate in candidates):
                fail(errors, f"next_action code requires permission manifest to exist: {permission_manifest_path}")
        if open_decisions or conflicts:
            fail(errors, "next_action code requires no open material decisions or source conflicts")
        for approval in ("source_authority", "material_decisions", "controlled_docs", "tdd", "coding_start"):
            if not approvals.get(approval):
                fail(errors, f"next_action code requires {approval} approval")
        for phase in PHASES:
            status = phases.get(phase, {}).get("status")
            if status not in READY_TO_CODE:
                fail(errors, f"next_action code requires {phase} approved or completed, got {status!r}")

    if mode == "full_run" and next_action == "complete":
        for phase in PHASES:
            status = phases.get(phase, {}).get("status")
            if status not in DONE:
                fail(errors, f"full_run completion requires {phase} done, got {status!r}")

    for field in ("coordination_state_path", "permission_manifest_path", "session_continuity_command"):
        if not isinstance(data.get(field), str) or not data.get(field, "").strip():
            fail(errors, f"{field} must be a non-empty string")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--pack-manifest", type=Path)
    args = parser.parse_args()

    try:
        manifest_path = args.manifest.resolve()
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: manifest not found: {args.manifest}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"FAIL: invalid JSON: {exc}")
        return 2

    if not isinstance(data, dict):
        print("FAIL: workflow manifest must contain a JSON object")
        return 2

    pack_manifest: dict[str, Any] | None = None
    pack_errors: list[str] = []
    if data.get("schema_version") == "1.1" or "artifact_instances" in data:
        default_pack_path = Path(__file__).resolve().parents[4] / "pack.manifest.json"
        pack_path = (args.pack_manifest or default_pack_path).resolve()
        try:
            loaded_pack = json.loads(pack_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_pack, dict):
                pack_errors.append(f"pack manifest must contain an object: {pack_path}")
            else:
                pack_manifest = loaded_pack
        except FileNotFoundError:
            pack_errors.append(f"pack manifest not found: {pack_path}")
        except json.JSONDecodeError as exc:
            pack_errors.append(f"invalid pack manifest JSON: {exc}")

    errors = pack_errors + validate(
        data,
        pack_manifest=pack_manifest,
        project_root=manifest_path.parent,
    )
    if errors:
        print("FAIL: workflow manifest is not ready")
        for error in errors:
            print(f"- {error}")
        return 1

    print("PASS: workflow manifest is valid for its current state")
    return 0


if __name__ == "__main__":
    sys.exit(main())
