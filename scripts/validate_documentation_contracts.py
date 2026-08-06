#!/usr/bin/env python3
"""Validate documentation artifact identity and mutable README facts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any


SOURCE_STATUSES = {"authoritative", "projection"}
RELATIONSHIPS = {"canonical", "exact_mirror", "intentional_variant", "derived"}
TRIGGER_KINDS = {"workflow_phase", "manual", "conditional"}
WORKFLOW_PHASES = {
    "0_route_scope",
    "1_source_inventory",
    "2_material_decisions",
    "3_controlled_docs",
    "4_tdd_alignment",
    "5_repo_documentation",
    "6_agent_instructions",
    "7_handoff",
    "8_final_validation",
}
REQUIRED_ARTIFACT_FIELDS = (
    "artifact_id",
    "family_id",
    "artifact_type",
    "path",
    "source_status",
    "relationship",
    "owner",
    "consumers",
    "trigger",
    "generation_route",
)

README_REQUIRED_MARKERS = (
    "https://github.com/AymanShams/codex-coding-os/releases",
    "https://github.com/AymanShams/codex-coding-os/pulls",
    "https://github.com/AymanShams/codex-coding-os/actions",
    "pack.manifest.json",
    "install-bundle.manifest.json",
)

README_FORBIDDEN_PATTERNS = {
    "a hardcoded pull-request coverage boundary": r"(?im)^This README describes repository `main` through pull request \d+\.?$",
    "a hardcoded latest release tag": r"(?im)^\|\s*Latest published GitHub release\s*\|[^\n]*`?v\d+\.\d+\.\d+",
    "a hardcoded published archive tag": r"(?im)^The published `v\d+\.\d+\.\d+` archive",
    "hardcoded pull-request coverage counts": r"(?im)^\|\s*Pull requests (?:covered|states)\s*\|",
    "a mutable pull-request state table": r"(?im)^\|\s*PR\s*\|\s*State\s*\|",
    "a hardcoded package version in the status table": r"(?im)^\|\s*Package metadata\s*\|[^\n]*\b\d+\.\d+\.\d+\b",
    "a hardcoded commit in the status table": r"(?im)^\|\s*Functional baseline\s*\|[^\n]*\b[0-9a-f]{7,40}\b",
    "a hardcoded current-main CI verdict": r"(?im)^Current `main` is (?:not )?fully green",
    "a pull-request-specific current validation claim": r"(?im)^.*Pull request \d+ passed the complete",
}

README_COUNT_LABELS = (
    "Tracked files",
    "Bundled skills",
    "Required manifest paths",
    "Support items",
    "Templates",
    "Documentation files under `docs/`",
    "Files under `scripts/`",
    "Test files under `tests/`",
    "Install bundle entries",
)

README_INVENTORY_COUNT_PATTERN = re.compile(
    r"(?i)\b(?:\d[\d,]*|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\s*(?:-\s*)?"
    r"(?:bundled\s+)?(?:skills?|capabilit(?:y|ies)|tracked\s+files?|required\s+manifest\s+paths?|"
    r"support\s+items?|templates?|documentation\s+files?|test\s+files?|install\s+bundle\s+entries?)\b"
)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _declared_file(repo_root: Path, raw_path: Any) -> tuple[Path | None, str | None]:
    if not _nonempty_string(raw_path):
        return None, "path must be a non-empty string"
    value = str(raw_path)
    if "\\" in value:
        return None, "path must use forward slashes"
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None, "path must be repository-relative and must not traverse parents"
    repo_root = repo_root.resolve()
    candidate = (repo_root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return None, "path resolves outside the repository"
    return candidate, None


def validate_artifact_trigger(trigger: Any, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(trigger, dict):
        return [f"{label} must be a typed trigger object"]

    trigger_id = trigger.get("trigger_id")
    kind = trigger.get("kind")
    if not _nonempty_string(trigger_id):
        errors.append(f"{label}.trigger_id must be a non-empty string")
    if kind not in TRIGGER_KINDS:
        errors.append(f"{label}.kind is invalid: {kind!r}")
        return errors

    expected_fields = {
        "workflow_phase": {"trigger_id", "kind", "phase"},
        "manual": {"trigger_id", "kind"},
        "conditional": {"trigger_id", "kind", "match", "predicates"},
    }[str(kind)]
    missing_fields = expected_fields - set(trigger)
    unknown_fields = set(trigger) - expected_fields
    for field in sorted(missing_fields):
        errors.append(f"{label} is missing {field}")
    for field in sorted(unknown_fields):
        errors.append(f"{label} contains unsupported field {field}")

    if kind == "workflow_phase":
        phase = trigger.get("phase")
        if phase not in WORKFLOW_PHASES:
            errors.append(f"{label}.phase is invalid: {phase!r}")
    elif kind == "conditional":
        match = trigger.get("match")
        if match not in {"any", "all"}:
            errors.append(f"{label}.match must be any or all")

        predicates = trigger.get("predicates")
        if not isinstance(predicates, list) or not predicates:
            errors.append(f"{label}.predicates must be a non-empty array")
        else:
            seen_facts: set[str] = set()
            for index, predicate in enumerate(predicates):
                predicate_label = f"{label}.predicates[{index}]"
                if not isinstance(predicate, dict):
                    errors.append(f"{predicate_label} must be an object")
                    continue
                predicate_fields = {"fact", "operator", "value"}
                for field in sorted(predicate_fields - set(predicate)):
                    errors.append(f"{predicate_label} is missing {field}")
                for field in sorted(set(predicate) - predicate_fields):
                    errors.append(f"{predicate_label} contains unsupported field {field}")

                fact = predicate.get("fact")
                if not _nonempty_string(fact):
                    errors.append(f"{predicate_label}.fact must be a non-empty string")
                elif str(fact) in seen_facts:
                    errors.append(f"{label} contains duplicate predicate fact {fact}")
                else:
                    seen_facts.add(str(fact))
                if predicate.get("operator") != "equals":
                    errors.append(f"{predicate_label}.operator must be equals")
                if not isinstance(predicate.get("value"), bool):
                    errors.append(f"{predicate_label}.value must be Boolean")

    return errors


def validate_artifact_definitions(repo_root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    definitions = manifest.get("artifact_definitions")
    if not isinstance(definitions, list) or not definitions:
        return ["pack.manifest.json artifact_definitions must be a non-empty array"]

    required_files_value = manifest.get("required_files")
    required_files = set(required_files_value) if isinstance(required_files_value, list) else set()
    if not required_files:
        errors.append("pack.manifest.json required_files must be a non-empty array")

    by_id: dict[str, dict[str, Any]] = {}
    by_path: dict[str, str] = {}
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    resolved_paths: dict[str, Path] = {}
    triggers_by_id: dict[str, dict[str, Any]] = {}

    for index, definition in enumerate(definitions):
        label = f"artifact_definitions[{index}]"
        if not isinstance(definition, dict):
            errors.append(f"{label} must be an object")
            continue

        for field in REQUIRED_ARTIFACT_FIELDS:
            if field not in definition:
                errors.append(f"{label} is missing {field}")

        artifact_id = definition.get("artifact_id")
        family_id = definition.get("family_id")
        path = definition.get("path")
        relationship = definition.get("relationship")
        source_status = definition.get("source_status")

        for field in (
            "artifact_id",
            "family_id",
            "artifact_type",
            "path",
            "source_status",
            "relationship",
            "owner",
            "generation_route",
        ):
            if field in definition and not _nonempty_string(definition.get(field)):
                errors.append(f"{label}.{field} must be a non-empty string")

        consumers = definition.get("consumers")
        if not isinstance(consumers, list) or not consumers or not all(_nonempty_string(item) for item in consumers):
            errors.append(f"{label}.consumers must be a non-empty array of strings")
        elif len(consumers) != len(set(consumers)):
            errors.append(f"{label}.consumers must not contain duplicates")

        trigger = definition.get("trigger")
        errors.extend(validate_artifact_trigger(trigger, f"{label}.trigger"))
        if isinstance(trigger, dict) and _nonempty_string(trigger.get("trigger_id")):
            trigger_id = str(trigger["trigger_id"])
            existing_trigger = triggers_by_id.get(trigger_id)
            if existing_trigger is None:
                triggers_by_id[trigger_id] = trigger
            elif existing_trigger != trigger:
                errors.append(f"trigger_id {trigger_id} is reused with a conflicting definition")

        if source_status not in SOURCE_STATUSES:
            errors.append(f"{label}.source_status is invalid: {source_status!r}")
        if relationship not in RELATIONSHIPS:
            errors.append(f"{label}.relationship is invalid: {relationship!r}")

        if _nonempty_string(artifact_id):
            artifact_id = str(artifact_id)
            if artifact_id in by_id:
                errors.append(f"duplicate artifact_id: {artifact_id}")
            else:
                by_id[artifact_id] = definition

        if _nonempty_string(path):
            path = str(path)
            if path in by_path:
                errors.append(f"duplicate artifact path: {path}")
            elif _nonempty_string(artifact_id):
                by_path[path] = str(artifact_id)
            if path not in required_files:
                errors.append(f"artifact path is not declared in required_files: {path}")
            resolved, path_error = _declared_file(repo_root, path)
            if path_error:
                errors.append(f"{label}.{path} {path_error}")
            elif resolved is not None:
                if not resolved.is_file():
                    errors.append(f"artifact path does not exist as a file: {path}")
                elif _nonempty_string(artifact_id):
                    resolved_paths[str(artifact_id)] = resolved

        if _nonempty_string(family_id):
            families[str(family_id)].append(definition)

    for family_id, members in families.items():
        canonicals = [member for member in members if member.get("relationship") == "canonical"]
        if len(canonicals) != 1:
            errors.append(f"artifact family {family_id} must declare exactly one canonical artifact")

    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        artifact_id = definition.get("artifact_id")
        family_id = definition.get("family_id")
        relationship = definition.get("relationship")
        source_status = definition.get("source_status")
        canonical_id = definition.get("canonical_artifact_id")

        if relationship == "canonical":
            if source_status != "authoritative":
                errors.append(f"canonical artifact {artifact_id} must be authoritative")
            if canonical_id is not None:
                errors.append(f"canonical artifact {artifact_id} must not declare canonical_artifact_id")
            continue

        if relationship not in RELATIONSHIPS:
            continue
        if not _nonempty_string(canonical_id):
            errors.append(f"{relationship} artifact {artifact_id} must declare canonical_artifact_id")
            continue
        canonical = by_id.get(str(canonical_id))
        if canonical is None:
            errors.append(f"artifact {artifact_id} references unknown canonical artifact {canonical_id}")
            continue
        if canonical.get("relationship") != "canonical":
            errors.append(f"artifact {artifact_id} controller {canonical_id} is not canonical")
        if canonical.get("family_id") != family_id:
            errors.append(f"artifact {artifact_id} and canonical {canonical_id} must share family_id")

        if relationship == "exact_mirror":
            if source_status != "projection":
                errors.append(f"exact mirror {artifact_id} must use source_status projection")
            if canonical is not None and definition.get("owner") != canonical.get("owner"):
                errors.append(f"exact mirror {artifact_id} must share the canonical owner")
            mirror_path = resolved_paths.get(str(artifact_id))
            canonical_path = resolved_paths.get(str(canonical_id))
            if mirror_path is not None and canonical_path is not None:
                if mirror_path.read_bytes() != canonical_path.read_bytes():
                    errors.append(f"exact mirror differs from canonical: {artifact_id} -> {canonical_id}")
        elif relationship == "intentional_variant":
            if source_status != "authoritative":
                errors.append(f"intentional variant {artifact_id} must use source_status authoritative")
            if not _nonempty_string(definition.get("variant_reason")):
                errors.append(f"intentional variant {artifact_id} must declare variant_reason")
            if canonical is not None:
                same_consumers = definition.get("consumers") == canonical.get("consumers")
                same_trigger = definition.get("trigger") == canonical.get("trigger")
                if same_consumers and same_trigger:
                    errors.append(
                        f"intentional variant {artifact_id} must have a distinct consumer or trigger"
                    )
        elif relationship == "derived" and source_status != "projection":
            errors.append(f"derived artifact {artifact_id} must use source_status projection")

    return errors


def validate_readme_freshness(repo_root: Path) -> list[str]:
    readme_path = repo_root / "README.md"
    if not readme_path.is_file():
        return ["README.md is missing"]
    text = readme_path.read_text(encoding="utf-8")
    errors: list[str] = []

    for marker in README_REQUIRED_MARKERS:
        if marker not in text:
            errors.append(f"README.md must link or refer to mutable-fact authority: {marker}")

    for description, pattern in README_FORBIDDEN_PATTERNS.items():
        if re.search(pattern, text):
            errors.append(f"README.md contains {description}")

    for label in README_COUNT_LABELS:
        pattern = rf"(?im)^\|\s*{re.escape(label)}\s*\|\s*\d[\d,]*\s*\|"
        if re.search(pattern, text):
            errors.append(f"README.md hardcodes mutable inventory count: {label}")

    inventory_match = re.search(
        r"(?ims)^## Package inventory\s*$.*?(?=^##\s|\Z)",
        text,
    )
    if inventory_match and README_INVENTORY_COUNT_PATTERN.search(inventory_match.group(0)):
        errors.append("README.md hardcodes mutable inventory count in package inventory prose")

    return errors


def validate_repository(repo_root: Path) -> list[str]:
    repo_root = repo_root.resolve()
    manifest_path = repo_root / "pack.manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ["pack.manifest.json is missing"]
    except json.JSONDecodeError as exc:
        return [f"pack.manifest.json is invalid JSON: {exc}"]
    if not isinstance(manifest, dict):
        return ["pack.manifest.json must contain an object"]
    return validate_artifact_definitions(repo_root, manifest) + validate_readme_freshness(repo_root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    errors = validate_repository(args.repo_root)
    if errors:
        print("Documentation contract validation failed:")
        for error in errors:
            print(f" - {error}")
        return 1

    print("Documentation contract validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
