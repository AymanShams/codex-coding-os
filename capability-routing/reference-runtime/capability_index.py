#!/usr/bin/env python3
"""Read-only capability manifest and routing-policy consumer.

The runtime owns no capability catalogue and no pairwise overlap map. It reads
the compact global snapshot produced by the universal catalogue workflow, then
applies the ordered routing policy to active entries only.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import tomllib

from capability_config_fingerprint import (
    HASH_SCOPE as CONFIG_CAPABILITY_HASH_SCOPE,
)
from capability_config_fingerprint import (
    SOURCE_HASH_KEY as CONFIG_CAPABILITY_SOURCE_HASH_KEY,
)
from capability_config_fingerprint import (
    CapabilityConfigError,
)
from capability_config_fingerprint import (
    capability_config_fingerprint as _capability_config_fingerprint,
)


class CapabilityDataError(ValueError):
    """Raised when a present manifest or policy is structurally invalid."""


def capability_config_fingerprint(path: Path) -> str:
    """Return the shared semantic config fingerprint using router error semantics."""

    try:
        return _capability_config_fingerprint(path)
    except CapabilityConfigError as exc:
        raise CapabilityDataError(str(exc)) from exc


class RouteRegistryError(RuntimeError):
    """Raised when a schema-valid route cannot be durably registered."""


CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
ROUTING_DIR = Path(
    os.environ.get(
        "CODEX_CAPABILITY_ROUTING_DIR",
        str(CODEX_HOME / "capability-routing"),
    )
)
ACTIVE_CAPABILITIES_PATH = Path(
    os.environ.get(
        "CODEX_ACTIVE_CAPABILITIES_PATH",
        os.environ.get(
            "CODEX_ACTIVE_CAPABILITIES",
            os.environ.get(
                "CODEX_CAPABILITY_MANIFEST",
                str(ROUTING_DIR / "active-capabilities.json"),
            ),
        ),
    )
)
ROUTING_POLICY_PATH = Path(
    os.environ.get(
        "CODEX_ROUTING_POLICY_PATH",
        os.environ.get(
            "CODEX_ROUTING_POLICY",
            str(ROUTING_DIR / "routing-policy.yaml"),
        ),
    )
)
CONFIG_PATH = Path(os.environ.get("CODEX_CONFIG_PATH", str(CODEX_HOME / "config.toml")))
ROUTE_DECISION_SCHEMA_PATH = Path(
    os.environ.get(
        "CODEX_ROUTE_DECISION_SCHEMA_PATH",
        str(ROUTING_DIR / "route-decision.schema.json"),
    )
)
ROUTE_DECISION_REGISTRY_PATH = Path(
    os.environ.get(
        "CODEX_ROUTE_DECISION_REGISTRY_PATH",
        str(ROUTING_DIR / "route-decisions.sqlite3"),
    )
)
PROJECT_SCOPE_MAP_PATH = Path(
    os.environ.get(
        "CODEX_PROJECT_SCOPE_MAP_PATH",
        str(ROUTING_DIR / "project-scope-map.json"),
    )
)

ACTIVE_STATES = {
    "active",
    "enabled",
    "exposed",
    "installed-active",
    "runtime-active",
    "verified-active",
}
FRESH_STATES = {"current", "fresh", "live", "valid", "verified"}
STATE_ARTIFACT_KINDS = {
    "routing-state",
    "snapshot",
    "state",
    "state-artifact",
    "stateartifact",
}
SUPPRESS_ACTIONS = {"block", "disable", "remove", "suppress"}
TACTICAL_ACTIONS = {"tactical", "tactical-only"}
DEFAULT_MAX_SUPPORTS = 2
ABSOLUTE_MAX_SUPPORTS = 2
DEFAULT_MAX_WORKER_SUPPORTS = 2
ABSOLUTE_MAX_WORKER_SUPPORTS = 2
ROUTE_REGISTRY_SCHEMA_VERSION = 3
DEFAULT_ROUTE_TTL_SECONDS = 86400
EXPIRED_ROUTE_AUDIT_RETENTION_SECONDS = 86400
MAX_REGISTERED_ROUTES = 10000
MAX_LOCAL_INSTRUCTION_CHARACTERS = 50000
WORKER_FAMILIES = {"local_agent_stack", "terra", "antigravity"}
EXECUTION_DISPOSITION_MODES = {"codex_only", "worker_support"}
CODEX_ONLY_EXECUTION_DISPOSITION = {
    "mode": "codex_only",
    "eligible_worker_families": [],
}
EXECUTION_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
ROUTE_REGISTRY_COLUMNS = (
    "decision_id",
    "decision_digest",
    "task_text_sha256",
    "task_input_sha256",
    "route_json",
    "route_json_sha256",
    "schema_version",
    "manifest_snapshot",
    "decision_snapshot",
    "manifest_authority_sha256",
    "policy_authority_sha256",
    "issued_at",
    "expires_at",
)
DEFAULT_FALLBACK = {
    "on_unavailable": "return_to_codex",
    "on_timeout": "return_to_codex",
    "on_error": "return_to_codex",
    "automatic_retry": False,
}
DEFAULT_EXECUTION_PROFILE = {
    "execution_owner": "codex_parent",
    "model": "gpt-5.6-sol",
    "reasoning_effort": "high",
    "deadline_seconds": 1800,
    "fallback": DEFAULT_FALLBACK,
}
APPROVED_WORKER_CONTRACTS = {
    ("codex_child", "read_heavy"): ("gpt-5.6-terra", "medium"),
    ("codex_child", "independent_challenger"): ("gemini-3.1-pro-high", "high"),
    ("local_agent_stack", "fast"): ("qwen3.5:2b-q8_0", None),
    ("local_agent_stack", "coding"): ("qwen2.5-coder:7b-instruct-q6_K", None),
    ("local_agent_stack", "critic"): ("deepseek-r1:7b-qwen-distill-q4_K_M", None),
}
APPROVED_LOCAL_EXECUTION_CONTRACTS = {
    "runtime_status": ("runtime_status", "status", "none", False),
    "memory_recall": ("prior_continuity", "recall", "memory", False),
    "source_lookup": ("project_evidence_lookup", "research", "index", False),
    "retrieval_bundle": ("retrieval_bundle", "research", "both", False),
    "literal_extraction": ("literal_structured_extraction", "extract", "none", True),
}
WORKER_TASK_GATE_RECIPES = (
    {
        "family": "antigravity",
        "flags": frozenset({"antigravity_eligible", "antigravity_support_required"}),
        "roles": ("independent_challenger",),
        "task_type": "review",
        "complexity": "high",
        "purpose": "explicit_challenge",
        "source_needs": frozenset({"none"}),
    },
    {
        "family": "terra",
        "flags": frozenset({"terra_read_heavy", "terra_support_required"}),
        "roles": ("read_heavy",),
        "task_type": "review",
        "complexity": "high",
        "purpose": "read_heavy_support",
        "source_needs": frozenset({"none"}),
    },
    {
        "family": "local_agent_stack",
        "flags": frozenset(
            {
                "local_fast_eligible",
                "bounded_classification_or_transformation",
                "local_support_required",
            }
        ),
        "roles": ("fast",),
        "task_type": "transform",
        "complexity": "low",
        "purpose": "bounded_classification_or_transformation",
        "source_needs": frozenset({"none", "memory", "index", "both"}),
    },
    {
        "family": "local_agent_stack",
        "flags": frozenset(
            {
                "local_fast_eligible",
                "local_critic_eligible",
                "complex_multi_source_synthesis",
                "local_support_required",
            }
        ),
        "roles": ("fast", "critic"),
        "task_type": "synthesize",
        "complexity": "high",
        "purpose": "complex_multi_source_synthesis",
        "source_needs": frozenset({"none", "memory", "index", "both"}),
    },
    {
        "family": "local_agent_stack",
        "flags": frozenset(
            {
                "local_coding_eligible",
                "focused_coding_assistance",
                "local_support_required",
            }
        ),
        "roles": ("coding", "critic"),
        "task_type": "implement",
        "complexity": "medium",
        "purpose": "focused_coding_assistance",
        "source_needs": frozenset({"none", "index"}),
    },
    {
        "family": "local_agent_stack",
        "flags": frozenset(
            {
                "local_critic_eligible",
                "explicit_challenge",
                "local_support_required",
            }
        ),
        "roles": ("critic",),
        "task_type": "review",
        "complexity": "medium",
        "purpose": "explicit_challenge",
        "source_needs": frozenset({"none", "memory", "index", "both"}),
    },
)
LOCAL_OPERATION_TASK_GATE_RECIPES = {
    "runtime_status": {
        "flags": frozenset({"local_runtime_status", "runtime_status"}),
        "task_type": "status",
        "complexity": "low",
        "purpose": "runtime_status",
        "source_need": "none",
    },
    "memory_recall": {
        "flags": frozenset({"prior_continuity", "memory_recall"}),
        "task_type": "recall",
        "complexity": "medium",
        "purpose": "prior_continuity",
        "source_need": "memory",
    },
    "source_lookup": {
        "flags": frozenset({"project_evidence_lookup", "source_lookup"}),
        "task_type": "research",
        "complexity": "medium",
        "purpose": "project_evidence_lookup",
        "source_need": "index",
    },
    "retrieval_bundle": {
        "flags": frozenset(
            {
                "prior_continuity",
                "memory_recall",
                "project_evidence_lookup",
                "source_lookup",
            }
        ),
        "task_type": "research",
        "complexity": "medium",
        "purpose": "retrieval_bundle",
        "source_need": "both",
    },
    "literal_extraction": {
        "flags": frozenset({"literal_structured_extraction"}),
        "task_type": "extract",
        "complexity": "low",
        "purpose": "literal_structured_extraction",
        "source_need": "none",
    },
}
TASK_GATE_POSITIVE_FLAGS = frozenset(
    flag
    for recipe in (*WORKER_TASK_GATE_RECIPES, *LOCAL_OPERATION_TASK_GATE_RECIPES.values())
    for flag in recipe["flags"]
)


def normalize(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def tokenize(value: object) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9+.-]*", str(value or "").lower())
        if len(token) > 1 and not token.isdigit()
    }


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,|]", value) if item.strip()]
    if isinstance(value, dict):
        flattened: list[str] = []
        for nested in value.values():
            flattened.extend(_as_list(nested))
        return flattened
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


_DOTNET_UNIX_EPOCH_TICKS = 621355968000000000


def _plugin_cache_inventory_rows(
    codex_home: Path | None = None,
) -> tuple[str, ...] | None:
    """Return the bounded passive plugin authority inventory without subprocesses."""

    cache_root = (codex_home or CODEX_HOME) / "plugins" / "cache"
    try:
        resolved_cache_root = cache_root.resolve(strict=True)
        if not resolved_cache_root.is_dir():
            return None
        rows: list[str] = []
        for marketplace in cache_root.iterdir():
            if not marketplace.is_dir():
                continue
            for plugin in marketplace.iterdir():
                if not plugin.is_dir():
                    continue
                if plugin.name.casefold().startswith("plugin-install-"):
                    continue
                for version in plugin.iterdir():
                    if not version.is_dir():
                        continue
                    resolved_version = version.resolve(strict=True)
                    if not resolved_version.is_relative_to(resolved_cache_root):
                        return None
                    plugin_manifest = version / ".codex-plugin" / "plugin.json"
                    if not plugin_manifest.is_file():
                        continue
                    relative_root = version.relative_to(cache_root).as_posix().lower()
                    root_ticks = (
                        version.lstat().st_mtime_ns // 100 + _DOTNET_UNIX_EPOCH_TICKS
                    )
                    rows.append(f"ROOT\t{relative_root}\t0\t{root_ticks}")

                    authority_files = [
                        plugin_manifest,
                        version / ".app.json",
                        version / ".mcp.json",
                    ]
                    skills_root = version / "skills"
                    if skills_root.is_dir():
                        for skill_directory in skills_root.iterdir():
                            if skill_directory.is_dir():
                                authority_files.append(skill_directory / "SKILL.md")
                    for authority_file in authority_files:
                        if not authority_file.is_file():
                            continue
                        resolved_file = authority_file.resolve(strict=True)
                        if not resolved_file.is_relative_to(resolved_cache_root):
                            return None
                        stat = authority_file.stat()
                        relative_file = (
                            authority_file.relative_to(cache_root).as_posix().lower()
                        )
                        file_ticks = (
                            stat.st_mtime_ns // 100 + _DOTNET_UNIX_EPOCH_TICKS
                        )
                        rows.append(
                            f"FILE\t{relative_file}\t{stat.st_size}\t{file_ticks}"
                        )
    except (OSError, RuntimeError, ValueError):
        return None

    return tuple(sorted(rows))


def _plugin_cache_inventory_hash(codex_home: Path | None = None) -> str:
    """Hash the bounded passive plugin authority inventory without subprocesses."""

    rows = _plugin_cache_inventory_rows(codex_home)
    if rows is None:
        return ""
    canonical = "\n".join(rows)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper()


def _source_hash_path(name: str) -> Path | None:
    known = {
        "config.toml": CONFIG_PATH,
        CONFIG_CAPABILITY_SOURCE_HASH_KEY: CONFIG_PATH,
        "hooks.json": CODEX_HOME / "hooks.json",
        "AGENTS.md": CODEX_HOME / "AGENTS.md",
        "task-routing-gate.md": CODEX_HOME / "docs" / "context" / "task-routing-gate.md",
        "catalogue-router.SKILL.md": CODEX_HOME / "skills" / "catalogue-router" / "SKILL.md",
        "capability_index.py": CODEX_HOME / "hooks" / "capability_index.py",
        "capability_config_fingerprint.py": CODEX_HOME
        / "hooks"
        / "capability_config_fingerprint.py",
        "capability_index_cli.py": CODEX_HOME / "hooks" / "capability_index_cli.py",
        "user_prompt_skill_router.py": CODEX_HOME / "hooks" / "user_prompt_skill_router.py",
        "capability_manifest_recovery.py": CODEX_HOME
        / "hooks"
        / "capability_manifest_recovery.py",
        "capability_index_session_start.py": COïÍ7öÚ$z{-®éÜj×ack["reason_code"],
                }
            )
            capability_fallback_reasons.extend(
                [
                    "CAPABILITY_DEPENDENCY_FALLBACK",
                    dependency_fallback["reason_code"],
                ]
            )
            if fallback_reference and not fallback_usable:
                capability_fallback_reasons.append(
                    "CAPABILITY_FALLBACK_UNAVAILABLE_RETURNED_TO_CODEX"
                )
            break

        if not primary_usable:
            continue
        selected_rule = rule
        selected_primary = primary
        selected_supports = _resolve_supports(
            rule.get("supports", []),
            prompt=prompt,
            primary=primary,
            max_supports=max_supports,
            policy=policy,
            rule=rule,
            by_id=by_id,
            by_alias=by_alias,
        )
        break

    execution_disposition, disposition_error = _resolve_execution_disposition(
        classification, exact_input, input_mode
    )
    eligible_worker_families = set(
        execution_disposition["eligible_worker_families"]
        if execution_disposition["mode"] == "worker_support"
        else []
    )
    worker_attempted = _worker_rule_attempted(
        prompt_lower, classification, policy.get("worker_rules", [])
    )
    local_operation_attempted = _worker_rule_attempted(
        prompt_lower, classification, policy.get("local_execution_rules", [])
    )
    support_workers, worker_reasons = _select_support_workers(
        prompt_lower,
        classification,
        eligible_worker_families,
        policy,
        by_id,
        by_alias,
    )
    task_gate_worker_eligible = bool(eligible_worker_families) and all(
        _validated_worker_roles(classification, family) is not None
        for family in eligible_worker_families
    )
    if (
        execution_disposition["mode"] == "worker_support"
        and worker_attempted
        and not task_gate_worker_eligible
        and "WORKER_TASK_GATE_TUPLE_INVALID" not in worker_reasons
    ):
        worker_reasons.append("WORKER_ELIGIBILITY_REQUIRED")
    elif (
        execution_disposition["mode"] == "codex_only"
        and disposition_error is None
        and worker_attempted
    ):
        worker_reasons.append("CODEX_ONLY_DISPOSITION")
    local_execution, local_reasons = _derive_local_execution(
        support_workers,
        classification,
        exact_input,
        prompt_lower,
        policy,
        by_id,
        by_alias,
    )
    local_operation_eligible = (
        _validated_local_operation_recipe(classification, exact_input) is not None
    )
    if (
        local_operation_attempted
        and not local_execution.get("admitted")
        and not local_operation_eligible
        and "LOCAL_TASK_GATE_TUPLE_INVALID" not in local_reasons
    ):
        local_reasons.append("LOCAL_OPERATION_ELIGIBILITY_REQUIRED")
    complete_instruction = exact_input.get("instruction")
    local_input_too_large = (
        input_mode == "complete"
        and isinstance(complete_instruction, str)
        and len(complete_instruction) > MAX_LOCAL_INSTRUCTION_CHARACTERS
        and (
            local_execution.get("admitted")
            or any(
                worker.get("execution_owner") == "local_agent_stack"
                for worker in support_workers
            )
        )
    )
    if local_input_too_large:
        rejected_local_worker_reasons = {
            str(worker.get("reason_code") or "")
            for worker in support_workers
            if worker.get("execution_owner") == "local_agent_stack"
        }
        support_workers = [
            worker
            for worker in support_workers
            if worker.get("execution_owner") != "local_agent_stack"
        ]
        worker_reasons = [
            reason
            for reason in worker_reasons
            if reason not in rejected_local_worker_reasons
            and reason != "WORKER_SUPPORT_LIMIT_APPLIED"
        ]
        local_execution = _fail_closed_local_execution(local_execution)
        local_reasons = [
            reason
            for reason in local_reasons
            if not reason.startswith("LOCAL_RECIPE_")
            and reason not in {"MEMORY_SCOPE_MAPPED", "MEMORY_SCOPE_NONE"}
        ]
        local_reasons.append("LOCAL_INPUT_TOO_LARGE_RETURNED_TO_CODEX")
    antigravity_selected = any(
        _worker_family(worker) == "antigravity" for worker in support_workers
    )
    bound_workspace_root = _canonical_existing_workspace_root(
        exact_input.get("workspace_root")
    )
    output_schema_sha256 = exact_input.get("output_schema_sha256")
    antigravity_bindings_valid = bool(
        bound_workspace_root
        and isinstance(output_schema_sha256, str)
        and SHA256_PATTERN.fullmatch(output_schema_sha256)
    )
    raw_execution_request_id = exact_input.get("execution_request_id")
    execution_request_id = (
        raw_execution_request_id
        if isinstance(raw_execution_request_id, str)
        and EXECUTION_REQUEST_ID_PATTERN.fullmatch(raw_execution_request_id)
        else None
    )
    execution_requested = bool(support_workers) or bool(local_execution.get("admitted"))
    execution_requested = execution_requested or any(
        reason
        in {
            "ANTIGRAVITY_SUPPORT_UNAVAILABLE",
            "WORKER_SUPPORT_UNAVAILABLE",
            "LOCAL_SUPPORT_UNAVAILABLE",
            "LOCAL_RECIPE_UNRESOLVED",
            "LOCAL_EXACT_EVIDENCE_REQUIRED",
            "LOCAL_EXECUTION_SCOPE_UNAVAILABLE",
            "PROJECT_CWD_CONFLICT",
            "SOURCE_SCOPE_UNAUTHORIZED",
            "WORKER_ELIGIBILITY_REQUIRED",
            "LOCAL_OPERATION_ELIGIBILITY_REQUIRED",
            "WORKER_TASK_GATE_TUPLE_INVALID",
            "LOCAL_TASK_GATE_TUPLE_INVALID",
        }
        for reason in [*worker_reasons, *local_reasons]
    )
    execution_requested = execution_requested or bool(
        disposition_error and (worker_attempted or local_operation_attempted)
    )
    required_task_fields = list(local_execution.get("task_input_requirements", []))
    rejected_local_reason_codes: set[str] = set()
    if not local_execution.get("admitted"):
        rejected_local_reason_codes = {
            str(worker.get("reason_code") or "")
            for worker in support_workers
            if worker.get("execution_owner") == "local_agent_stack"
        }
        if rejected_local_reason_codes:
            support_workers = [
                worker
                for worker in support_workers
                if worker.get("execution_owner") != "local_agent_stack"
            ]
            worker_reasons = [
                reason
                for reason in worker_reasons
                if reason not in rejected_local_reason_codes
                and reason != "WORKER_SUPPORT_LIMIT_APPLIED"
            ]
            local_reasons.append("LOCAL_WORKERS_RETURNED_TO_CODEX")
    blocking_code = next(
        (
            reason
            for reason in ("PROJECT_CWD_CONFLICT", "SOURCE_SCOPE_UNAUTHORIZED")
            if reason in local_reasons
        ),
        None,
    )
    if blocking_code is None:
        blocking_code = next(
            (
                reason
                for reason in (
                    "WORKER_ELIGIBILITY_REQUIRED",
                    "LOCAL_OPERATION_ELIGIBILITY_REQUIRED",
                    "WORKER_TASK_GATE_TUPLE_INVALID",
                    "LOCAL_TASK_GATE_TUPLE_INVALID",
                )
                if reason in [*worker_reasons, *local_reasons]
            ),
            None,
        )
    if input_mode == "complete" and not instruction_agrees and blocking_code is None:
        blocking_code = "TASK_INPUT_INSTRUCTION_MISMATCH"
    if execution_requested and blocking_code is None and disposition_error:
        blocking_code = disposition_error
    if (
        execution_requested
        and blocking_code is None
        and execution_request_id is None
    ):
        blocking_code = "EXECUTION_REQUEST_ID_REQUIRED"
    if antigravity_selected and blocking_code is None and not antigravity_bindings_valid:
        blocking_code = "ANTIGRAVITY_BINDINGS_INVALID"
    provided_instruction = exact_input.get("instruction")
    requirements_complete = _task_input_requirements_complete(
        exact_input, required_task_fields
    )
    if execution_requested and blocking_code is None:
        if input_mode != "complete":
            blocking_code = "TASK_INPUT_REQUIRED"
        elif not isinstance(provided_instruction, str) or not provided_instruction.strip():
            blocking_code = "TASK_INPUT_FIELDS_INCOMPLETE"
        elif not instruction_agrees:
            blocking_code = "TASK_INPUT_INSTRUCTION_MISMATCH"
        elif not requirements_complete:
            blocking_code = "TASK_INPUT_FIELDS_INCOMPLETE"
    resolved_input_mode = (
        "complete"
        if input_mode == "complete"
        and instruction_agrees
        and requirements_complete
        else "conservative_instruction_only"
    )
    if execution_requested and blocking_code:
        support_workers = []
        local_execution = _fail_closed_local_execution(local_execution)
    reasons = [
        "CODEX_SOL_ORCHESTRATOR"
        if support_workers or local_execution.get("admitted")
        else "CODEX_SOL_DEFAULT"
    ]
    if selected_rule:
        reasons.append("CAPABILITY_RULE_MATCH")
        reasons.extend(selected_rule.get("reason_codes", []))
    else:
        reasons.append("NO_EXACT_CAPABILITY_MATCH")
    if normalize(manifest.get("freshness_status")) not in FRESH_STATES:
        reasons.append("CAPABILITY_SNAPSHOT_STALE")
    reasons.extend(worker_reasons)
    reasons.extend(local_reasons)
    reasons.extend(route_guard_reasons)
    reasons.extend(capability_fallback_reasons)
    if blocking_code and blocking_code not in reasons:
        reasons.append(blocking_code)
    build_args = {
        "rule": selected_rule,
        "primary": selected_primary,
        "supports": selected_supports,
        "support_workers": support_workers,
        "local_execution": local_execution,
        "execution_disposition": execution_disposition,
        "execution_request_id": (
            execution_request_id if execution_requested else None
        ),
        "task_text_sha256": compute_task_text_sha256(bounded_text),
        "task_input_sha256": compute_task_input_sha256(exact_input),
        "task_input_mode": resolved_input_mode,
        "task_fingerprint": _task_fingerprint(prompt, classification),
        "worker_execution_requested": execution_requested,
        "reason_codes": reasons,
        "capability_fallbacks": capability_fallbacks,
        "manifest": manifest,
        "policy": policy,
    }
    authority_issuable = _route_authority_issuable(manifest, policy)
    synthetic_authority = _synthetic_authority_input(manifest) and _synthetic_authority_input(
        policy
    )
    if not authority_issuable and not synthetic_authority:
        failed_args = dict(build_args)
        failed_args["support_workers"] = []
        failed_args["local_execution"] = _fail_closed_local_execution(local_execution)
        failed_args["reason_codes"] = list(
            dict.fromkeys(
                [
                    "CODEX_SOL_DEFAULT",
                    *(reason for reason in reasons if reason != "CODEX_SOL_ORCHESTRATOR"),
                    "AUTHORITY_UNAVAILABLE",
                ]
            )
        )
        return _build_decision(
            **failed_args,
            issuance_status="failed",
            issuance_failure_code="AUTHORITY_UNAVAILABLE",
        )
    decision = _build_decision(
        **build_args,
        issuance_status="registered",
        issuance_failure_code=blocking_code,
    )
    try:
        _issue_route_decision(decision)
        return decision
    except RouteRegistryError:
        failed_args = dict(build_args)
        failed_args["support_workers"] = []
        failed_args["local_execution"] = _fail_closed_local_execution(local_execution)
        failed_reasons = [
            reason
            for reason in reasons
            if reason != "CODEX_SOL_ORCHESTRATOR"
        ]
        failed_args["reason_codes"] = [
            "CODEX_SOL_DEFAULT",
            *failed_reasons,
            "ROUTE_REGISTRY_UNAVAILABLE",
        ]
        return _build_decision(
            **failed_args,
            issuance_status="failed",
            issuance_failure_code="ROUTE_REGISTRY_UNAVAILABLE",
        )


def ensure_index(force: bool = False, max_age_hours: int = 24) -> dict[str, Any]:
    """Compatibility API. The canonical manifest is read directly, never cached."""

    del force, max_age_hours
    return load_active_capabilities()


def build_index() -> dict[str, Any]:
    return load_active_capabilities()


def is_session_only_candidate(entry: dict[str, Any]) -> bool:
    del entry
    return False


def query_index(
    prompt: str,
    limit: int = 5,
    include_candidates: bool | None = None,
    primary_families: object = None,
    supporting_families: object = None,
    source_tool_requirements: object = None,
    denied_families: object = None,
    candidate_visibility: str = "active_only",
) -> list[dict[str, Any]]:
    """Search active entries only. Legacy candidate switches are intentionally ignored."""

    del include_candidates, candidate_visibility
    manifest = load_active_capabilities()
    prompt_tokens = tokenize(prompt)
    requested_families = {
        normalize(item).replace("-", "_")
        for item in _as_list(primary_families) + _as_list(supporting_families)
    }
    denied = {
        normalize(item).replace("-", "_") for item in _as_list(denied_families)
    }
    requested_tools = {
        normalize(item) for item in _as_list(source_tool_requirements)
    }
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in manifest.get("entries", []):
        families = set(entry.get("families", []))
        if denied and families & denied:
            continue
        entry_tokens = tokenize(
            " ".join(
                [
                    str(entry.get("id", "")),
                    str(entry.get("name", "")),
                    str(entry.get("provider", "")),
                    str(entry.get("kind", "")),
                    " ".join(families),
                    str(entry.get("description", "")),
                ]
            )
        )
        score = len(prompt_tokens & entry_tokens) * 4
        score += len(requested_families & families) * 10
        if normalize(entry.get("name")) in requested_tools or normalize(
            entry.get("id")
        ) in requested_tools:
            score += 20
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], normalize(item[1].get("id"))))
    return [
        entry for _, entry in scored[: max(1, min(int(limit), 8))]
    ]
