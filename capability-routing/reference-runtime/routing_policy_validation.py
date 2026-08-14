#!/usr/bin/env python3
"""Shared fail-closed routing-policy schema and semantic validation.

The deployment materializer and the live router import this exact module. Policy
identity and runtime availability are intentionally separate: a policy may name
a declared but currently suppressed capability, while route selection still
operates only on active manifest entries.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


APPROVED_WORKER_CONTRACTS = {
    ("codex_child", "read_heavy"): ("gpt-5.6-terra", "medium"),
    ("codex_child", "independent_challenger"): (
        "gemini-3.1-pro-high",
        "high",
    ),
    ("local_agent_stack", "fast"): ("qwen3.5:2b-q8_0", None),
    ("local_agent_stack", "coding"): (
        "qwen2.5-coder:7b-instruct-q6_K",
        None,
    ),
    ("local_agent_stack", "critic"): (
        "deepseek-r1:7b-qwen-distill-q4_K_M",
        None,
    ),
}
APPROVED_LOCAL_EXECUTION_CONTRACTS = {
    "runtime_status": ("runtime_status", "status", "none", False),
    "memory_recall": ("prior_continuity", "recall", "memory", False),
    "source_lookup": ("project_evidence_lookup", "research", "index", False),
    "retrieval_bundle": ("retrieval_bundle", "research", "both", False),
    "literal_extraction": (
        "literal_structured_extraction",
        "extract",
        "none",
        True,
    ),
}
ACTIVE_CAPABILITY_STATES = frozenset(
    {
        "active",
        "enabled",
        "exposed",
        "installed-active",
        "runtime-active",
        "verified-active",
    }
)
CAPABILITY_REFERENCE_RE = re.compile(
    r"^[a-z][a-z0-9-]*:[^\s:]+(?::[^\s:]+)*$"
)


class RoutingPolicyValidationError(ValueError):
    """The policy, schema, or capability identity closure is invalid."""


def validate_against_schema(instance: Any, schema: Any, label: str) -> None:
    """Validate one value against a Draft 2020-12 schema or fail closed."""

    if not isinstance(schema, dict):
        raise RoutingPolicyValidationError(f"{label} schema must be a JSON object")
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError, ValidationError
    except ImportError as exc:
        raise RoutingPolicyValidationError(
            "jsonschema is required for routing policy validation"
        ) from exc
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(instance)
    except (SchemaError, ValidationError) as exc:
        path = "/" + "/".join(
            str(item) for item in getattr(exc, "absolute_path", ())
        )
        raise RoutingPolicyValidationError(
            f"{label} failed schema validation at {path}: {exc.message}"
        ) from exc


def _normalized_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")


def _capability_reference(value: Any, label: str) -> str:
    reference = str(value or "")
    if (
        reference != reference.strip()
        or CAPABILITY_REFERENCE_RE.fullmatch(reference) is None
    ):
        raise RoutingPolicyValidationError(
            f"{label} is not an exact capability reference: {reference!r}"
        )
    return reference


def _is_active_state(value: Any) -> bool:
    state = _normalized_name(value)
    return state in ACTIVE_CAPABILITY_STATES or state.startswith("active-")


def capability_identifier_sets(
    manifest: Any,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return separate active, suppressed, and declared manifest identities."""

    if not isinstance(manifest, dict):
        raise RoutingPolicyValidationError(
            "capability validation manifest must be a JSON object"
        )
    entries = manifest.get("entries")
    suppressed = manifest.get("suppressed_capabilities", [])
    if not isinstance(entries, list) or not isinstance(suppressed, list):
        raise RoutingPolicyValidationError(
            "capability validation manifest must declare entry and suppression arrays"
        )
    identifiers: dict[str, str] = {}
    active_identifiers: set[str] = set()
    suppressed_identifiers: set[str] = set()
    for label, rows in (("entry", entries), ("suppressed capability", suppressed)):
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise RoutingPolicyValidationError(
                    f"capability manifest {label} {index} is not an object"
                )
            identifier = _capability_reference(
                row.get("id"), f"capability manifest {label} {index} id"
            )
            if label == "entry" and not _is_active_state(row.get("state")):
                raise RoutingPolicyValidationError(
                    f"capability manifest entry is not active: {identifier}"
                )
            key = identifier.casefold()
            if key in identifiers:
                raise RoutingPolicyValidationError(
                    "capability validation manifest contains duplicate identifiers: "
                    f"{identifiers[key]!r}, {identifier!r}"
                )
            identifiers[key] = identifier
            if label == "entry":
                active_identifiers.add(identifier)
            else:
                suppressed_identifiers.add(identifier)
    if not identifiers:
        raise RoutingPolicyValidationError(
            "capability validation manifest contains no capabilities"
        )
    active = frozenset(active_identifiers)
    suppressed_set = frozenset(suppressed_identifiers)
    return active, suppressed_set, active | suppressed_set


def _require_declared_capability(
    value: Any,
    declared_capabilities: frozenset[str],
    label: str,
) -> str:
    reference = _capability_reference(value, label)
    if reference not in declared_capabilities:
        raise RoutingPolicyValidationError(
            f"{label} is not a declared capability manifest entry: {reference}"
        )
    return reference


def _validate_unique_policy_ids(policy: Mapping[str, Any]) -> None:
    identifiers: dict[str, str] = {}
    for section in (
        "rules",
        "worker_rules",
        "local_execution_rules",
        "explicit_overrides",
    ):
        for index, row in enumerate(policy.get(section, [])):
            identifier = str(row["id"])
            key = identifier.casefold()
            if key in identifiers:
                raise RoutingPolicyValidationError(
                    f"duplicate policy id {identifier!r} in {section}; first declared in "
                    f"{identifiers[key]}"
                )
            identifiers[key] = f"{section}[{index}]"


def _validate_policy_aliases(
    policy: Mapping[str, Any], declared_capabilities: frozenset[str]
) -> None:
    normalized_aliases: dict[str, tuple[str, str]] = {}
    for reference, values in policy["capability_aliases"].items():
        _require_declared_capability(
            reference,
            declared_capabilities,
            "capability alias key",
        )
        for alias in values:
            normalized = _normalized_name(alias)
            if not normalized:
                raise RoutingPolicyValidationError(
                    f"capability alias normalizes to empty text: {alias!r}"
                )
            prior = normalized_aliases.get(normalized)
            if prior is not None and prior[0] != reference:
                raise RoutingPolicyValidationError(
                    "duplicate normalized capability alias "
                    f"{alias!r} for {reference}; already declared as {prior[1]!r} "
                    f"for {prior[0]}"
                )
            normalized_aliases[normalized] = (reference, alias)


def _validate_dependency_fallback(
    rule: Mapping[str, Any],
    declared_capabilities: frozenset[str],
) -> None:
    rule_id = rule["id"]
    dependencies = rule.get("requires_live_dependencies", [])
    fallback = rule.get("dependency_fallback")
    if bool(dependencies) != (fallback is not None):
        raise RoutingPolicyValidationError(
            f"rule {rule_id} must declare live dependencies and dependency fallback together"
        )
    if fallback is None:
        return
    selected = _require_declared_capability(
        fallback["selected_capability"],
        declared_capabilities,
        f"rule {rule_id} fallback selected_capability",
    )
    chosen = _capability_reference(
        fallback["chosen_fallback"], f"rule {rule_id} chosen_fallback"
    )
    if not chosen.startswith("workflow:"):
        raise RoutingPolicyValidationError(
            f"rule {rule_id} chosen_fallback must be a workflow reference"
        )
    fallback_supports = [
        _require_declared_capability(
            value,
            declared_capabilities,
            f"rule {rule_id} fallback support",
        )
        for value in fallback["supports"]
    ]
    equivalents = [
        _require_declared_capability(
            value,
            declared_capabilities,
            f"rule {rule_id} equivalent capability",
        )
        for value in fallback.get("equivalent_capabilities", [])
    ]
    if selected in fallback_supports:
        raise RoutingPolicyValidationError(
            f"rule {rule_id} selects the same fallback as primary and support"
        )
    if chosen in {rule["primary"], selected, *fallback_supports}:
        raise RoutingPolicyValidationError(
            f"rule {rule_id} chosen fallback contradicts its capability selection"
        )
    if fallback["equivalence"] == "equivalent":
        if selected not in {rule["primary"], *equivalents}:
            raise RoutingPolicyValidationError(
                f"rule {rule_id} equivalent fallback selects an undeclared equivalent capability"
            )
    elif equivalents:
        raise RoutingPolicyValidationError(
            f"rule {rule_id} non-equivalent fallback cannot declare equivalent capabilities"
        )


def validate_policy_semantics(
    policy: Mapping[str, Any],
    active_capabilities: frozenset[str],
    declared_capabilities: frozenset[str],
) -> None:
    """Validate cross-object policy meaning without conflating current activity."""

    if not active_capabilities.issubset(declared_capabilities):
        raise RoutingPolicyValidationError(
            "active capability identities must be part of the declared identity set"
        )
    _validate_unique_policy_ids(policy)
    _validate_policy_aliases(policy, declared_capabilities)
    profiles = policy["execution_profiles"]
    default_profile = policy["default_execution_profile"]
    if default_profile not in profiles:
        raise RoutingPolicyValidationError(
            f"default execution profile is not declared: {default_profile}"
        )
    for profile_id, profile in profiles.items():
        if (
            profile["execution_owner"] != "codex_parent"
            or profile["model"] != "gpt-5.6-sol"
        ):
            raise RoutingPolicyValidationError(
                f"execution profile {profile_id} contradicts the Codex parent contract"
            )

    controls = policy["live_dependency_controls"]
    for dependency_id, control in controls.items():
        _capability_reference(dependency_id, "live dependency control id")
        if control["probe_requirement"]["target"] != dependency_id:
            raise RoutingPolicyValidationError(
                f"live dependency control {dependency_id} probe target contradicts its id"
            )
        for value in control["manifest_any"]:
            _capability_reference(
                value,
                f"live dependency control {dependency_id} manifest reference",
            )

    max_supports = policy["max_supports"]
    for rule in policy["rules"]:
        rule_id = rule["id"]
        primary = _require_declared_capability(
            rule["primary"],
            declared_capabilities,
            f"rule {rule_id} primary",
        )
        supports = [
            _require_declared_capability(
                value,
                declared_capabilities,
                f"rule {rule_id} support",
            )
            for value in rule["supports"]
        ]
        if primary in supports:
            raise RoutingPolicyValidationError(
                f"rule {rule_id} repeats its primary as a support"
            )
        if len(supports) > max_supports:
            raise RoutingPolicyValidationError(
                f"rule {rule_id} declares more supports than max_supports"
            )
        profile = rule.get("execution_profile")
        if profile and profile not in profiles:
            raise RoutingPolicyValidationError(
                f"rule {rule_id} references an unknown execution profile: {profile}"
            )
        for dependency_id in rule.get("requires_live_dependencies", []):
            if dependency_id not in controls:
                raise RoutingPolicyValidationError(
                    f"rule {rule_id} references an unknown live dependency: {dependency_id}"
                )
        required_prompts: set[str] = set()
        forbidden_prompts: set[str] = set()
        required_capabilities: set[str] = set()
        forbidden_capabilities: set[str] = set()
        for value in rule["requires"]:
            if value.casefold().startswith("prompt:"):
                required_prompts.add(value.split(":", 1)[1].strip().casefold())
            else:
                reference = value.removeprefix("active:")
                required_capabilities.add(
                    _require_declared_capability(
                        reference,
                        declared_capabilities,
                        f"rule {rule_id} required capability",
                    )
                )
        for value in rule["forbids"]:
            if value.casefold().startswith("prompt:"):
                forbidden_prompts.add(value.split(":", 1)[1].strip().casefold())
            else:
                reference = value.removeprefix("capability:")
                forbidden_capabilities.add(
                    _require_declared_capability(
                        reference,
                        declared_capabilities,
                        f"rule {rule_id} forbidden capability",
                    )
                )
        if (
            not all(required_prompts)
            or not all(forbidden_prompts)
            or required_prompts & forbidden_prompts
            or required_capabilities & forbidden_capabilities
        ):
            raise RoutingPolicyValidationError(
                f"rule {rule_id} requires and forbids the same condition"
            )
        _validate_dependency_fallback(rule, declared_capabilities)

    for section in ("worker_rules", "local_execution_rules"):
        priorities: set[int] = set()
        for rule in policy[section]:
            if rule["priority"] in priorities:
                raise RoutingPolicyValidationError(
                    f"{section} contains duplicate priority {rule['priority']}"
                )
            priorities.add(rule["priority"])
            for value in rule["requires_any_capabilities"]:
                _require_declared_capability(
                    value,
                    declared_capabilities,
                    f"{section} rule {rule['id']} capability alternative",
                )
            if section == "worker_rules":
                worker = rule["worker"]
                contract = (worker["model"], worker["reasoning_effort"])
                owner_role = (worker["execution_owner"], worker["role"])
                if APPROVED_WORKER_CONTRACTS.get(owner_role) != contract:
                    raise RoutingPolicyValidationError(
                        f"worker rule {rule['id']} contradicts its approved worker contract"
                    )
                expected_upstream = (
                    "local-agent-stack"
                    if worker["execution_owner"] == "local_agent_stack"
                    else (
                        "antigravity-adapter"
                        if worker["role"] == "independent_challenger"
                        else None
                    )
                )
                if rule.get("gateway_managed_upstream") != expected_upstream:
                    raise RoutingPolicyValidationError(
                        f"worker rule {rule['id']} contradicts its gateway ownership"
                    )
            else:
                contract = (
                    rule["local_stack_purpose"],
                    rule["task_type"],
                    rule["source_need"],
                    rule["exact_evidence"],
                )
                if (
                    APPROVED_LOCAL_EXECUTION_CONTRACTS.get(rule["recipe_id"])
                    != contract
                ):
                    raise RoutingPolicyValidationError(
                        f"local execution rule {rule['id']} contradicts its recipe contract"
                    )

    override_targets: set[str] = set()
    for override in policy.get("explicit_overrides", []):
        target = _require_declared_capability(
            override["target"],
            declared_capabilities,
            f"override {override['id']} target",
        )
        target_key = target.casefold()
        if target_key in override_targets:
            raise RoutingPolicyValidationError(
                f"duplicate explicit override target: {override['target']}"
            )
        override_targets.add(target_key)
        winner = override.get("winner", "")
        required_primary = override.get("requires_primary", "")
        if winner:
            _require_declared_capability(
                winner,
                declared_capabilities,
                f"override {override['id']} winner",
            )
        if required_primary:
            _require_declared_capability(
                required_primary,
                declared_capabilities,
                f"override {override['id']} requires_primary",
            )
        if winner and winner.casefold() == target_key:
            raise RoutingPolicyValidationError(
                f"override {override['id']} cannot select its suppressed target as winner"
            )


def validate_routing_policy(
    policy: Any,
    schema: Any,
    capability_manifest: Any,
    *,
    label: str = "routing policy",
) -> dict[str, frozenset[str]]:
    """Validate the complete policy and return its immutable identity context."""

    required_fields = {
        "schema_version",
        "decision_snapshot",
        "max_supports",
        "max_worker_supports",
        "capability_aliases",
        "default_execution_profile",
        "execution_profiles",
        "live_dependency_controls",
        "local_execution_rules",
        "worker_rules",
        "rules",
    }
    schema_required = schema.get("required") if isinstance(schema, dict) else None
    if (
        not isinstance(schema, dict)
        or schema.get("$schema")
        != "https://json-schema.org/draft/2020-12/schema"
        or schema.get("type") != "object"
        or not isinstance(schema_required, list)
        or not required_fields.issubset(set(schema_required))
    ):
        raise RoutingPolicyValidationError(
            "routing policy schema contract is incomplete"
        )
    active, suppressed, declared = capability_identifier_sets(capability_manifest)
    validate_against_schema(policy, schema, label)
    if not isinstance(policy, Mapping):
        raise RoutingPolicyValidationError(f"{label} must be a JSON object")
    validate_policy_semantics(policy, active, declared)
    return {
        "active_capabilities": active,
        "declared_capabilities": declared,
        "suppressed_capabilities": suppressed,
    }
