#!/usr/bin/env python3
"""Read-only capability manifest and routing-policy consumer.

The runtime owns no capability catalogue and no pairwise overlap map. It reads
the compact global snapshot produced by the universal catalogue workflow, then
applies the ordered routing policy to active entries only.
"""

from __future__ import annotations

import ast
import datetime as dt
import csv
import fnmatch
import hashlib
import hmac
import importlib.metadata
import importlib.util
import io
import json
import os
import re
import sqlite3
import stat
import subprocess
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
from capability_config_fingerprint import (
    capability_config_authority as _capability_config_authority,
)
from routing_policy_validation import (
    ACTIVE_CAPABILITY_STATES,
    APPROVED_LOCAL_EXECUTION_CONTRACTS,
    APPROVED_WORKER_CONTRACTS,
    RoutingPolicyValidationError,
)
from routing_policy_validation import (
    validate_routing_policy as _validate_routing_policy,
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
ROUTING_POLICY_SCHEMA_PATH = Path(
    os.environ.get(
        "CODEX_ROUTING_POLICY_SCHEMA_PATH",
        str(ROUTING_DIR / "routing-policy.schema.json"),
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
AUTHORITY_GENERATION_POINTER_PATH = Path(
    os.environ.get(
        "CODEX_AUTHORITY_GENERATION_POINTER_PATH",
        str(ROUTING_DIR / "current-generation.json"),
    )
)
PROJECT_SCOPE_MAP_PATH = Path(
    os.environ.get(
        "CODEX_PROJECT_SCOPE_MAP_PATH",
        str(ROUTING_DIR / "project-scope-map.json"),
    )
)
WORKER_RUNTIME_BOM_PATH = Path(
    os.environ.get(
        "CODEX_WORKER_RUNTIME_BOM_PATH",
        str(ROUTING_DIR / "worker-runtime-bom.json"),
    )
)

ACTIVE_STATES = set(ACTIVE_CAPABILITY_STATES)
FRESH_STATES = {"current", "degraded", "fresh", "live", "valid", "verified"}
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
AUTHORITY_GENERATION_ID_PATTERN = re.compile(r"^[a-f0-9]{64}$")
AUTHORITY_GENERATION_PROMOTION_REASONS = frozenset(
    {
        "coherent_app_update",
        "compound_app_primary_runtime_update",
        "primary_runtime_bundle_update",
        "worker_runtime_bom_update",
        "scoped_config_update",
        "updater_bound_plugin_update",
        "operator_rebaseline",
    }
)
WORKER_RUNTIME_BOM_SCHEMA = "capability-worker-runtime-bom-v1"
WORKER_RUNTIME_BOM_SOURCE_HASH_KEY = "worker-runtime-bom.json"
WORKER_RUNTIME_BOM_PROMOTER_SOURCE_HASH_KEY = "promote_worker_runtime_bom.py"
WORKER_FAMILY_SERVER_IDS = {
    "local_agent_stack": "local-agent-stack",
    "antigravity": "antigravity-adapter",
}
REQUIRED_WORKER_RUNTIME_SERVER_IDS = frozenset(WORKER_FAMILY_SERVER_IDS.values())
WORKER_SERVER_SPECS = {
    "antigravity-adapter": {
        "module": "antigravity_adapter.server",
        "pycache_relative_path": "state/python-cache/antigravity-adapter-2.1.1",
        "env": {
            "ANTIGRAVITY_ADAPTER_ROOT",
            "ANTIGRAVITY_AGY_EXECUTABLE",
        },
    },
    "local-agent-stack": {
        "module": "local_agent_stack.server",
        "pycache_relative_path": "run/python-cache/local-agent-stack-v5",
        "env": {"LOCAL_AGENT_STACK_ROOT"},
    },
}
WORKER_EXECUTION_KEYS = frozenset(
    {
        "args",
        "command",
        "cwd",
        "enabled",
        "env",
        "gateway_managed",
        "startup_timeout_sec",
        "tool_timeout_sec",
    }
)
WORKER_PRESENTATION_KEYS = frozenset({"description", "display_name", "name"})
PYTHON_EXECUTION_CLOSURE_SCHEMA = "python-venv-execution-closure-v1"
WORKER_IMPORT_PACKAGES = {
    "antigravity-adapter": "antigravity_adapter",
    "local-agent-stack": "local_agent_stack",
}
PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES = [
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONPYCACHEPREFIX",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
]
WORKER_CHILD_ENVIRONMENT_POLICY_ID = "codex-stability-child-env-v1"
GATEWAY_CONFIG_SERVER_ID = "codex-stability-gateway"
GATEWAY_CONFIG_URL = "http://127.0.0.1:8765/mcp"
GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH = (
    "tools/codex-stability/runtime-identity.json"
)
GATEWAY_RUNTIME_IDENTITY_SCHEMA = "codex-stability-gateway-runtime-identity-v1"
GATEWAY_COMPONENT = "codex-stability-gateway"
GATEWAY_RELEASE_ID = "codex-stability-gateway-1.0.1"
GATEWAY_STARTUP_RECEIPT_SCHEMA = "codex-stability-gateway-startup-receipt-v2"
GATEWAY_STARTUP_RECEIPT_BINDING_DOMAIN = (
    "codex-stability-gateway-startup-receipt-binding-v1"
)
GATEWAY_STARTUP_RECEIPT_KEYS = {
    "binding_sha256",
    "child_environment_policy_id",
    "executable_path",
    "executable_sha256",
    "gateway_startup_environment_policy_id",
    "loaded_upstream_config_sha256",
    "managed_upstreams_absent_at_start",
    "process_id",
    "process_role",
    "process_start_time_utc",
    "recorded_at_utc",
    "release_id",
    "runtime_identity_path",
    "runtime_identity_sha256",
    "schema_version",
    "source_sha256",
    "task_action_sha256",
    "upstream_config_sha256_by_server",
    "worker_runtime_bom_path",
    "worker_runtime_bom_sha256",
}
GATEWAY_STARTUP_RECEIPT_BINDING_FIELDS = (
    "schema_version",
    "release_id",
    "process_role",
    "process_id",
    "process_start_time_utc",
    "executable_path",
    "executable_sha256",
    "runtime_identity_path",
    "runtime_identity_sha256",
    "source_sha256",
    "worker_runtime_bom_path",
    "worker_runtime_bom_sha256",
    "loaded_upstream_config_sha256",
    "task_action_sha256",
    "child_environment_policy_id",
    "gateway_startup_environment_policy_id",
    "managed_upstreams_absent_at_start",
)
GATEWAY_STARTUP_RECEIPT_PATH = Path(
    os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
) / "Codex" / "stability" / "gateway-startup-receipt.json"
GATEWAY_STARTUP_ENVIRONMENT_POLICY_ID = "codex-stability-gateway-startup-v1"
GATEWAY_REQUIRED_PYTHON_FLAGS = {
    "dont_write_bytecode": 1,
    "isolated": 1,
    "no_site": 1,
    "no_user_site": 1,
}
GATEWAY_SOURCE_DOMAIN = b"CODEX-STABILITY-GATEWAY-SOURCE-v1\0"
GATEWAY_SITE_PACKAGES_DOMAIN = b"CODEX-STABILITY-GATEWAY-SITE-PACKAGES-v1\0"
GATEWAY_PYTHON_BASE_RUNTIME_DOMAIN = (
    b"CODEX-STABILITY-GATEWAY-PYTHON-BASE-RUNTIME-v1\0"
)
GATEWAY_SOURCE_RELATIVE_PATHS = (
    "codex_mcp_gateway.py",
    "gateway_bootstrap.py",
)
GATEWAY_RUNTIME_BINDING_KEYS = {
    "config_server_id",
    "identity_relative_path",
    "identity_sha256",
    "runtime_identity",
    "server_config_sha256",
}
GATEWAY_RUNTIME_IDENTITY_KEYS = {
    "child_environment_policy_id",
    "component",
    "gateway_startup_environment_policy_id",
    "gateway_startup_python_flags",
    "python_bytecode_cache",
    "python_injection_environment_keys",
    "python_runtime",
    "release_id",
    "schema_version",
    "source_files",
    "source_sha256",
}
GATEWAY_PYTHON_RUNTIME_KEYS = {
    "base_root",
    "base_runtime_file_count",
    "base_runtime_sha256",
    "console_executable_path",
    "console_executable_sha256",
    "dependency_lock_path",
    "dependency_lock_sha256",
    "site_packages_file_count",
    "site_packages_path",
    "site_packages_sha256",
    "version",
    "windowless_executable_path",
    "windowless_executable_sha256",
}
PYTHON_EXECUTION_CLOSURE_KEYS = {
    "schema_version",
    "venv_python_path",
    "venv_python_sha256",
    "pyvenv_config_path",
    "pyvenv_config_sha256",
    "include_system_site_packages",
    "base_interpreter_path",
    "base_interpreter_version",
    "base_interpreter_sha256",
    "base_runtime_tree_path",
    "base_runtime_tree_file_count",
    "base_runtime_tree_sha256",
    "editable_pth_path",
    "editable_pth_sha256",
    "editable_source_root",
    "import_package",
    "import_origin",
    "isolated_mode",
    "user_site_enabled",
    "dont_write_bytecode",
    "pycache_prefix_path",
    "pycache_prefix_empty",
    "forbidden_environment_variables",
    "child_environment_policy_id",
    "site_packages_path",
    "site_packages_tree_file_count",
    "site_packages_tree_sha256",
    "installed_distributions_count",
    "installed_distributions_sha256",
    "pth_files_count",
    "pth_files_sha256",
    "pth_imports_count",
    "pth_imports_sha256",
}
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
DYNAMIC_AUTHORITY_HASH_KEYS = frozenset(
    {
        CONFIG_CAPABILITY_SOURCE_HASH_KEY,
        "plugin-cache-inventory",
        WORKER_RUNTIME_BOM_SOURCE_HASH_KEY,
    }
)
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
                    rows.append(
                        f"ROOT\t{relative_root}\t0\tauthority-files-content-v2"
                    )

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
                        content_sha256 = _sha256_file(authority_file)
                        verified_stat = authority_file.stat()
                        if (
                            stat.st_size != verified_stat.st_size
                            or stat.st_mtime_ns != verified_stat.st_mtime_ns
                        ):
                            return None
                        relative_file = (
                            authority_file.relative_to(cache_root).as_posix().lower()
                        )
                        rows.append(
                            f"FILE\t{relative_file}\t{verified_stat.st_size}\t"
                            f"{content_sha256}"
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


def _plugin_cache_row_hashes(
    codex_home: Path | None = None,
) -> tuple[dict[str, str], str] | None:
    """Return the same bounded row authority used by recovery plus its digest."""

    rows = _plugin_cache_inventory_rows(codex_home)
    if rows is None:
        return None
    row_hashes: dict[str, str] = {}
    for row in rows:
        parts = row.split("\t", 3)
        if len(parts) != 4 or parts[0] not in {"ROOT", "FILE"}:
            return None
        key = f"{parts[0]}\t{parts[1]}"
        if key in row_hashes:
            return None
        row_hashes[key] = hashlib.sha256(row.encode("utf-8")).hexdigest().upper()
    inventory_sha256 = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest().upper()
    return dict(sorted(row_hashes.items())), inventory_sha256


def _plugin_package_from_row_key(value: object) -> str | None:
    try:
        kind, relative = str(value).split("\t", 1)
    except ValueError:
        return None
    if kind not in {"ROOT", "FILE"}:
        return None
    parts = relative.replace("\\", "/").strip("/").split("/")
    if len(parts) < 3 or any(not part or part in {".", ".."} for part in parts[:3]):
        return None
    return "/".join(parts[:3]).casefold()


def _plugin_package_name(package: str) -> str | None:
    parts = package.replace("\\", "/").strip("/").split("/")
    if len(parts) != 3 or any(not part or part in {".", ".."} for part in parts):
        return None
    return parts[1].casefold()


def _config_capability_surface_bindings(
    receipt: dict[str, Any],
) -> dict[str, dict[str, Any]] | None:
    leaves = receipt.get("config_leaf_hashes")
    surfaces = receipt.get("config_capability_surfaces")
    if (
        not isinstance(leaves, dict)
        or not isinstance(surfaces, dict)
        or set(surfaces) != set(leaves)
    ):
        return None
    normalized: dict[str, dict[str, Any]] = {}
    for pointer, raw in surfaces.items():
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            return None
        if not isinstance(raw, dict):
            return None
        identifiers = raw.get("capability_ids")
        required = raw.get("required_capability_ids")
        if (
            raw.get("change_class")
            not in {"availability_toggle", "runtime_identity"}
            or raw.get("control_kind")
            not in {
                "app",
                "app_tool",
                "app_runtime",
                "global_runtime",
                "mcp",
                "mcp_runtime",
                "plugin",
                "plugin_mcp",
                "skill",
            }
            or not isinstance(raw.get("control_key"), str)
            or not raw["control_key"]
            or not isinstance(identifiers, list)
            or not identifiers
            or len(identifiers) != len(set(identifiers))
            or any(not isinstance(item, str) or not item for item in identifiers)
            or not isinstance(required, list)
            or not required
            or not set(required).issubset(identifiers)
        ):
            return None
        if raw["change_class"] == "availability_toggle":
            if not isinstance(raw.get("enabled"), bool):
                return None
        elif "enabled" in raw:
            return None
        normalized[pointer] = {
            "change_class": raw["change_class"],
            "control_kind": raw["control_kind"],
            "control_key": raw["control_key"],
            "capability_ids": sorted(set(identifiers)),
            "required_capability_ids": sorted(set(required)),
            **({"enabled": raw["enabled"]} if "enabled" in raw else {}),
        }
    return normalized


def _dynamic_authority_assessment(
    data: dict[str, Any], mismatches: Iterable[str]
) -> dict[str, Any]:
    """Scope mutable deployment drift without disabling the static router.

    Plugin bytes are bounded by the manifest-recorded package closure. Known
    Codex Desktop runtime-path changes are bounded to the browser and node_repl
    surfaces. A worker BOM change disables external workers at selection time.
    Anything outside those provable boundaries remains globally fail closed.
    """

    unavailable = {
        "status": "unscoped",
        "observed_config_sha256": "",
        "observed_inventory_sha256": "",
        "worker_runtime_bom_status": "unavailable",
        "assessment_digest": "",
        "changed_config_leaves": [],
        "changed_packages": [],
        "quarantined_packages": [],
        "quarantined_capability_ids": [],
        "reason_code": "DYNAMIC_DEPENDENCY_CLOSURE_UNPROVEN",
    }
    mismatch_set = {str(item) for item in mismatches}
    if not mismatch_set or not mismatch_set.issubset(DYNAMIC_AUTHORITY_HASH_KEYS):
        return unavailable
    receipt = data.get("authority_receipt")
    if not isinstance(receipt, dict):
        return unavailable
    baseline_rows = receipt.get("plugin_cache_row_hashes")
    surfaces = receipt.get("plugin_capability_surfaces")
    if not isinstance(baseline_rows, dict) or not isinstance(surfaces, dict):
        return unavailable

    normalized_surfaces: dict[str, list[dict[str, Any]]] = {}
    for raw_package, raw_capabilities in surfaces.items():
        package = str(raw_package).replace("\\", "/").strip("/").casefold()
        if (
            _plugin_package_name(package) is None
            or package in normalized_surfaces
            or not isinstance(raw_capabilities, list)
            or not raw_capabilities
        ):
            return unavailable
        normalized_capabilities: list[dict[str, Any]] = []
        for raw_capability in raw_capabilities:
            if not isinstance(raw_capability, dict):
                return unavailable
            identifier = raw_capability.get("id")
            kind = raw_capability.get("kind")
            if not isinstance(identifier, str) or not identifier or not isinstance(kind, str) or not kind:
                return unavailable
            normalized_capabilities.append({"id": identifier, "kind": kind})
        normalized_surfaces[package] = normalized_capabilities

    quarantined_packages: set[str] = set()
    quarantined_ids: set[str] = set()
    changed_packages: set[str] = set()
    inventory_sha256 = str(receipt.get("plugin_cache_inventory_sha256") or "").upper()
    if "plugin-cache-inventory" in mismatch_set:
        normalized_baseline_rows: dict[str, str] = {}
        baseline_roots: set[str] = set()
        for raw_key, raw_digest in baseline_rows.items():
            key = str(raw_key)
            package = _plugin_package_from_row_key(key)
            digest = str(raw_digest or "").upper()
            if package is None or not re.fullmatch(r"[A-F0-9]{64}", digest):
                return unavailable
            normalized_baseline_rows[key] = digest
            if key.startswith("ROOT\t"):
                baseline_roots.add(package)
        if baseline_roots != set(normalized_surfaces):
            return unavailable
        current = _plugin_cache_row_hashes()
        if current is None:
            return unavailable
        current_rows, inventory_sha256 = current
        changed_keys = {
            key
            for key in set(normalized_baseline_rows) | set(current_rows)
            if normalized_baseline_rows.get(key) != current_rows.get(key)
        }
        for key in changed_keys:
            package = _plugin_package_from_row_key(key)
            if package is None:
                return unavailable
            changed_packages.add(package)
        if not changed_packages:
            return unavailable
        changed_names = {
            name
            for package in changed_packages
            if (name := _plugin_package_name(package)) is not None
        }
        quarantined_packages.update(
            package
            for package in normalized_surfaces
            if package in changed_packages
            or _plugin_package_name(package) in changed_names
        )

    changed_config_leaves: set[str] = set()
    observed_config_sha256 = str(receipt.get("config_projection_sha256") or "").upper()
    if CONFIG_CAPABILITY_SOURCE_HASH_KEY in mismatch_set:
        config_surfaces = _config_capability_surface_bindings(receipt)
        if config_surfaces is None:
            return unavailable
        baseline_leaves = receipt.get("config_leaf_hashes")
        if not isinstance(baseline_leaves, dict):
            return unavailable
        try:
            current_config = _capability_config_authority(CONFIG_PATH)
        except (CapabilityConfigError, OSError):
            return unavailable
        current_leaves = current_config.get("projection_leaf_hashes")
        observed_config_sha256 = str(current_config.get("sha256") or "").upper()
        if not isinstance(current_leaves, dict):
            return unavailable
        changed_config_leaves = {
            key
            for key in set(baseline_leaves) | set(current_leaves)
            if baseline_leaves.get(key) != current_leaves.get(key)
        }
        if not changed_config_leaves:
            return unavailable
        for pointer in changed_config_leaves:
            binding = config_surfaces.get(pointer)
            if binding is None:
                return unavailable
            if binding["control_kind"] == "global_runtime":
                return unavailable
            quarantined_ids.update(binding["capability_ids"])

    for package in quarantined_packages:
        quarantined_ids.update(
            capability["id"] for capability in normalized_surfaces[package]
        )
    worker_bom_status = (
        "changed"
        if WORKER_RUNTIME_BOM_SOURCE_HASH_KEY in mismatch_set
        else "current"
    )
    assessment_payload = {
        "baseline_snapshot": str(data.get("snapshot_id") or ""),
        "observed_config_sha256": observed_config_sha256,
        "observed_inventory_sha256": inventory_sha256,
        "worker_runtime_bom_status": worker_bom_status,
        "dynamic_mismatches": sorted(mismatch_set),
        "changed_config_leaves": sorted(changed_config_leaves),
        "changed_packages": sorted(changed_packages),
        "quarantined_packages": sorted(quarantined_packages),
        "quarantined_capability_ids": sorted(quarantined_ids),
    }
    assessment_digest = hashlib.sha256(
        json.dumps(
            assessment_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if mismatch_set == {"plugin-cache-inventory"}:
        reason_code = "PLUGIN_PACKAGE_QUARANTINED"
    elif mismatch_set == {CONFIG_CAPABILITY_SOURCE_HASH_KEY}:
        reason_code = "CONFIG_SURFACE_QUARANTINED"
    elif mismatch_set == {WORKER_RUNTIME_BOM_SOURCE_HASH_KEY}:
        reason_code = "WORKER_RUNTIME_BINDING_CHANGED"
    else:
        reason_code = "DYNAMIC_AUTHORITY_SCOPED"
    return {
        "status": "degraded",
        **assessment_payload,
        "assessment_digest": assessment_digest,
        "reason_code": reason_code,
    }


def _source_hash_path(name: str) -> Path | None:
    known = {
        "config.toml": CONFIG_PATH,
        CONFIG_CAPABILITY_SOURCE_HASH_KEY: CONFIG_PATH,
        "catalogue-router.SKILL.md": CODEX_HOME / "skills" / "catalogue-router" / "SKILL.md",
        "capability_index.py": CODEX_HOME / "hooks" / "capability_index.py",
        "routing_policy_validation.py": (
            CODEX_HOME / "hooks" / "routing_policy_validation.py"
        ),
        "capability_config_fingerprint.py": CODEX_HOME
        / "hooks"
        / "capability_config_fingerprint.py",
        "capability_index_cli.py": CODEX_HOME / "hooks" / "capability_index_cli.py",
        "user_prompt_skill_router.py": CODEX_HOME / "hooks" / "user_prompt_skill_router.py",
        "capability_manifest_recovery.py": CODEX_HOME
        / "hooks"
        / "capability_manifest_recovery.py",
        "capability_index_session_start.py": CODEX_HOME
        / "hooks"
        / "capability_index_session_start.py",
        "_hook_io.py": CODEX_HOME / "hooks" / "_hook_io.py",
        "capability-manifest-builder.ps1": CODEX_HOME
        / "capability-routing"
        / "builder"
        / "build_canonical_capability_manifest.ps1",
        "authority-receipt.schema.json": CODEX_HOME
        / "capability-routing"
        / "authority-receipt.schema.json",
        "query-catalogue.ps1": CODEX_HOME
        / "skills"
        / "catalogue-router"
        / "scripts"
        / "query-catalogue.ps1",
        "dependency-readiness.README.md": CODEX_HOME
        / "tools"
        / "dependency-readiness"
        / "README.md",
        "routing-policy.yaml": ROUTING_POLICY_PATH,
        "routing-policy.schema.json": CODEX_HOME
        / "capability-routing"
        / "routing-policy.schema.json",
        "active-capabilities.schema.json": CODEX_HOME
        / "capability-routing"
        / "active-capabilities.schema.json",
        "project-scope-map.json": PROJECT_SCOPE_MAP_PATH,
        "project-scope-map.schema.json": CODEX_HOME
        / "capability-routing"
        / "project-scope-map.schema.json",
        "route-decision.schema.json": CODEX_HOME
        / "capability-routing"
        / "route-decision.schema.json",
        WORKER_RUNTIME_BOM_SOURCE_HASH_KEY: WORKER_RUNTIME_BOM_PATH,
        WORKER_RUNTIME_BOM_PROMOTER_SOURCE_HASH_KEY: CODEX_HOME
        / "capability-routing"
        / "promote_worker_runtime_bom.py",
        "worker-runtime-bom.schema.json": CODEX_HOME
        / "capability-routing"
        / "worker-runtime-bom.schema.json",
    }
    return known.get(name)


REQUIRED_MANIFEST_AUTHORITY_HASH_KEYS = frozenset(
    {
        CONFIG_CAPABILITY_SOURCE_HASH_KEY,
        "catalogue-router.SKILL.md",
        "capability_index.py",
        "routing_policy_validation.py",
        "capability_config_fingerprint.py",
        "capability_index_cli.py",
        "user_prompt_skill_router.py",
        "capability_manifest_recovery.py",
        "capability_index_session_start.py",
        "_hook_io.py",
        "capability-manifest-builder.ps1",
        "authority-receipt.schema.json",
        "query-catalogue.ps1",
        "routing-policy.yaml",
        "routing-policy.schema.json",
        "active-capabilities.schema.json",
        "project-scope-map.json",
        "project-scope-map.schema.json",
        "route-decision.schema.json",
        WORKER_RUNTIME_BOM_SOURCE_HASH_KEY,
        WORKER_RUNTIME_BOM_PROMOTER_SOURCE_HASH_KEY,
        "worker-runtime-bom.schema.json",
        "plugin-cache-inventory",
    }
)
MANIFEST_AUDIT_HASH_KEYS = frozenset(
    {
        "config.toml",
        "dependency-readiness.README.md",
        "universal-skills-2026-07-25.csv",
        "universal-plugins-2026-07-25.csv",
        "universal-tool-families-and-mcps-2026-07-25.csv",
        "live-mcp-list",
        "live-plugin-list",
        "passive-skill-list",
    }
)


def _source_hash_mismatches(data: dict[str, Any]) -> list[str]:
    raw_hashes = data.get("source_hashes")
    if not isinstance(raw_hashes, dict):
        return ["source_hashes"]
    supplied_keys = {str(name) for name in raw_hashes}
    missing_authorities = sorted(REQUIRED_MANIFEST_AUTHORITY_HASH_KEYS - supplied_keys)
    unexpected_keys = sorted(
        supplied_keys
        - REQUIRED_MANIFEST_AUTHORITY_HASH_KEYS
        - MANIFEST_AUDIT_HASH_KEYS
    )
    mismatches: list[str] = [
        *(f"source_hashes.missing:{name}" for name in missing_authorities),
        *(f"source_hashes.unknown:{name}" for name in unexpected_keys),
    ]
    for name, expected in raw_hashes.items():
        source_name = str(name)
        expected_text = str(expected or "").strip().upper()
        if source_name not in REQUIRED_MANIFEST_AUTHORITY_HASH_KEYS:
            if not re.fullmatch(r"[A-F0-9]{64}", expected_text):
                mismatches.append(source_name)
            continue
        if source_name == "plugin-cache-inventory":
            actual = _plugin_cache_inventory_hash()
            if not expected_text or actual != expected_text:
                mismatches.append(source_name)
            continue
        if source_name == CONFIG_CAPABILITY_SOURCE_HASH_KEY:
            path = _source_hash_path(source_name)
            try:
                actual = capability_config_fingerprint(path) if path else ""
            except CapabilityDataError:
                actual = ""
            if not expected_text or actual != expected_text:
                mismatches.append(source_name)
            continue
        path = _source_hash_path(source_name)
        if path is None:
            continue
        try:
            actual = _sha256_file(path) if path.is_file() else ""
        except OSError:
            actual = ""
        if not expected_text or actual != expected_text:
            mismatches.append(source_name)
    return mismatches


def _entry_hash_current(entry: dict[str, Any]) -> bool:
    source = str(entry.get("source_path") or "").strip()
    expected = str(entry.get("sha256") or "").strip().upper()
    hash_scope = str(
        entry.get("hash_scope")
        or (entry.get("raw") or {}).get("hash_scope")
        or ""
    ).strip()
    if not source or not re.fullmatch(r"[A-F0-9]{64}", expected):
        return False
    if hash_scope == CONFIG_CAPABILITY_HASH_SCOPE:
        try:
            source_path = Path(source).resolve(strict=True)
            configured_path = CONFIG_PATH.resolve(strict=True)
            return (
                source_path == configured_path
                and capability_config_fingerprint(configured_path) == expected
            )
        except (CapabilityDataError, OSError):
            return False
    if hash_scope == "text-sha256":
        actual = hashlib.sha256(source.encode("utf-8")).hexdigest().upper()
        return hmac.compare_digest(actual, expected)
    if hash_scope not in {"", "file-sha256"}:
        return False

    # Empty scope is retained only as a constrained migration path for pre-1.3
    # manifests. It never permits a URL, relative path, or missing local source.
    if os.name == "nt":
        if re.fullmatch(r"[A-Za-z]:[\\/].+", source) is None:
            return False
    elif not source.startswith("/"):
        return False
    path = Path(source)
    if not path.is_absolute() or path.is_symlink():
        return False
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            return False
        before = resolved.stat()
        raw = resolved.read_bytes()
        after = resolved.stat()
    except OSError:
        return False
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        return False
    if len(raw) != after.st_size:
        return False
    actual = hashlib.sha256(raw).hexdigest().upper()
    return hmac.compare_digest(actual, expected)


def _load_json_compatible_yaml_with_authority(
    path: Path, label: str
) -> tuple[dict[str, Any], str]:
    """Load one authority source and hash the exact bytes that were parsed."""

    if not path.is_file():
        return {}, ""
    try:
        raw_bytes = path.read_bytes()
        raw = raw_bytes.decode("utf-8-sig")
    except OSError as exc:
        raise CapabilityDataError(f"cannot read {label}: {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise CapabilityDataError(f"cannot decode {label}: {path}: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CapabilityDataError(
            f"{label} must be JSON-compatible YAML: {path}: line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, dict):
        raise CapabilityDataError(f"{label} root must be an object: {path}")
    return value, hashlib.sha256(raw_bytes).hexdigest()


def authority_generation_id(value: object) -> str:
    """Recompute the builder's immutable generation identity exactly."""

    if not isinstance(value, dict) or set(value) != {
        "id",
        "sequence",
        "previous_id",
        "transaction_id",
        "promoted_at",
        "promotion_reason",
        "static_authority_sha256",
        "dynamic_authority_sha256",
        "config_projection_sha256",
        "plugin_inventory_sha256",
        "worker_runtime_bom_sha256",
        "authority_snapshot_sha256",
    }:
        return ""
    sequence = value.get("sequence")
    previous_id = value.get("previous_id")
    transaction_id = str(value.get("transaction_id") or "")
    promoted_at = str(value.get("promoted_at") or "")
    promotion_reason = value.get("promotion_reason")
    digest_fields = (
        "static_authority_sha256",
        "dynamic_authority_sha256",
        "config_projection_sha256",
        "plugin_inventory_sha256",
        "worker_runtime_bom_sha256",
        "authority_snapshot_sha256",
    )
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 1
        or (
            previous_id is not None
            and AUTHORITY_GENERATION_ID_PATTERN.fullmatch(str(previous_id)) is None
        )
        or EXECUTION_REQUEST_ID_PATTERN.fullmatch(transaction_id) is None
        or not promoted_at
        or promotion_reason not in AUTHORITY_GENERATION_PROMOTION_REASONS
        or any(
            SHA256_PATTERN.fullmatch(str(value.get(name) or "")) is None
            for name in digest_fields
        )
    ):
        return ""
    payload = {
        "sequence": sequence,
        "previous_id": previous_id,
        "transaction_id": transaction_id,
        "promoted_at": promoted_at,
        "promotion_reason": promotion_reason,
        "static_authority_sha256": value["static_authority_sha256"],
        "dynamic_authority_sha256": value["dynamic_authority_sha256"],
        "config_projection_sha256": value["config_projection_sha256"],
        "plugin_inventory_sha256": value["plugin_inventory_sha256"],
        "worker_runtime_bom_sha256": value["worker_runtime_bom_sha256"],
        "authority_snapshot_sha256": value["authority_snapshot_sha256"],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_manifest_generation_authority(
    requested_path: Path,
) -> tuple[dict[str, Any], str, Path, str, list[str]]:
    """Resolve the current immutable generation through one atomic pointer.

    Legacy and synthetic manifests remain directly readable for migration and
    isolated tests. Once a manifest declares the 1.3 generation contract, a
    missing or invalid pointer is an authority error rather than an implicit
    fallback to the mutable compatibility copy.
    """

    is_active_path = requested_path.resolve(strict=False) == ACTIVE_CAPABILITIES_PATH.resolve(
        strict=False
    )
    pointer_path = AUTHORITY_GENERATION_POINTER_PATH
    if is_active_path and pointer_path.is_file():
        try:
            if pointer_path.is_symlink():
                raise CapabilityDataError("capability generation pointer must not be a symlink")
            pointer, _ = _load_json_compatible_yaml_with_authority(
                pointer_path, "capability authority generation pointer"
            )
            generation_id = str(pointer.get("generation_id") or "").lower()
            relative_manifest = str(pointer.get("manifest_path") or "")
            expected_sha256 = str(pointer.get("manifest_sha256") or "").lower()
            sequence = pointer.get("sequence")
            previous_generation_id = pointer.get("previous_generation_id")
            transaction_id = str(pointer.get("transaction_id") or "")
            expected_relative_manifest = (
                Path("generations") / f"generation-{generation_id}.json"
            ).as_posix()
            if (
                pointer.get("schema_version") != "capability-authority-pointer-v1"
                or AUTHORITY_GENERATION_ID_PATTERN.fullmatch(generation_id) is None
                or SHA256_PATTERN.fullmatch(expected_sha256) is None
                or relative_manifest != expected_relative_manifest
                or not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or sequence < 1
                or (
                    previous_generation_id is not None
                    and AUTHORITY_GENERATION_ID_PATTERN.fullmatch(
                        str(previous_generation_id).lower()
                    )
                    is None
                )
                or EXECUTION_REQUEST_ID_PATTERN.fullmatch(transaction_id) is None
            ):
                raise CapabilityDataError("capability generation pointer is structurally invalid")
            routing_root = pointer_path.parent.resolve(strict=True)
            generation_path = (routing_root / relative_manifest).resolve(strict=True)
            generations_root = (routing_root / "generations").resolve(strict=True)
            if (
                (routing_root / "generations").is_symlink()
                or (routing_root / relative_manifest).is_symlink()
                or generation_path.parent != generations_root
                or not generation_path.is_relative_to(routing_root)
                or not generation_path.is_file()
            ):
                raise CapabilityDataError("capability generation path escapes or is missing")
            data, authority_sha256 = _load_json_compatible_yaml_with_authority(
                generation_path, "immutable capability generation"
            )
            if not hmac.compare_digest(expected_sha256, authority_sha256):
                raise CapabilityDataError("immutable capability generation hash mismatch")
            generation = data.get("authority_generation")
            if (
                not isinstance(generation, dict)
                or str(generation.get("id") or "").lower() != generation_id
                or authority_generation_id(generation) != generation_id
                or generation.get("sequence") != sequence
                or generation.get("previous_id") != previous_generation_id
                or generation.get("transaction_id") != transaction_id
                or str(data.get("snapshot_id") or "")
                != f"authority-generation:{generation_id}"
            ):
                raise CapabilityDataError("immutable capability generation identity mismatch")
            return data, authority_sha256, generation_path, "current", []
        except (CapabilityDataError, OSError, RuntimeError, ValueError):
            return {}, "", requested_path, "invalid", ["generation_pointer_invalid"]

    data, authority_sha256 = _load_json_compatible_yaml_with_authority(
        requested_path, "active capability manifest"
    )
    generation_declared = str(data.get("schema_version") or "") == "1.3" or isinstance(
        data.get("authority_generation"), dict
    )
    if is_active_path and generation_declared:
        return data, authority_sha256, requested_path, "missing", ["generation_pointer_missing"]
    return data, authority_sha256, requested_path, "legacy", []


def _load_json_compatible_yaml(path: Path, label: str) -> dict[str, Any]:
    """Load JSON or JSON-compatible YAML without a third-party YAML parser."""

    return _load_json_compatible_yaml_with_authority(path, label)[0]


def _authority_sha256(value: dict[str, Any]) -> str:
    """Return an exact source hash or a deterministic synthetic-authority hash."""

    if "authority_sha256" in value:
        supplied = str(value.get("authority_sha256") or "").lower()
        return supplied if SHA256_PATTERN.fullmatch(supplied) else ""
    payload = {
        key: item
        for key, item in value.items()
        if key not in {"source", "authority_sha256"}
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entry_state(raw: dict[str, Any]) -> str:
    return normalize(
        raw.get("state")
        or raw.get("status")
        or raw.get("current_status")
        or raw.get("installed_state")
    )


def is_active_state(state: object) -> bool:
    normalized = normalize(state)
    return normalized in ACTIVE_STATES or normalized.startswith("active-")


def is_state_artifact(entry: dict[str, Any]) -> bool:
    kind = normalize(entry.get("kind"))
    return kind in STATE_ARTIFACT_KINDS or entry.get("is_state_artifact") is True


def _normalize_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(raw.get("name") or raw.get("display_name") or "").strip()
    identifier = str(raw.get("id") or raw.get("capability_id") or "").strip()
    kind = str(raw.get("kind") or raw.get("type") or "").strip()
    state = _entry_state(raw)
    if not identifier:
        identifier = f"{kind}:{name}" if kind and name else name
    if not identifier or not name or not kind or not state:
        return None
    families = []
    for family in _as_list(raw.get("families") or raw.get("all_families")):
        normalized_family = normalize(family).replace("-", "_")
        if normalized_family and normalized_family not in families:
            families.append(normalized_family)
    return {
        "id": identifier,
        "kind": kind,
        "name": name,
        "state": state,
        "status": state,
        "provider": str(raw.get("provider") or raw.get("owner") or "").strip(),
        "version": str(raw.get("version") or "").strip(),
        "source_path": str(raw.get("source_path") or raw.get("path") or "").strip(),
        "sha256": str(raw.get("sha256") or raw.get("hash") or "").strip(),
        "hash_scope": str(raw.get("hash_scope") or "").strip(),
        "families": families,
        "description": str(raw.get("description") or "").strip(),
        "raw": raw,
    }


def load_active_capabilities(path: Path | None = None) -> dict[str, Any]:
    source_path = path or ACTIVE_CAPABILITIES_PATH
    (
        data,
        authority_sha256,
        authority_source_path,
        generation_pointer_status,
        generation_mismatches,
    ) = _load_manifest_generation_authority(
        source_path
    )
    if not data:
        return {
            "schema_version": "",
            "generated_at": "",
            "snapshot_id": "",
            "freshness_status": "missing",
            "source_hashes": {},
            "source_hashes_verified": False,
            "static_source_hashes_verified": False,
            "source_hash_mismatches": generation_mismatches or ["manifest_missing"],
            "static_source_hash_mismatches": generation_mismatches or ["manifest_missing"],
            "dynamic_source_hash_mismatches": [],
            "dynamic_authority_status": "unavailable",
            "dynamic_authority": {},
            "worker_runtime_bom_status": "unavailable",
            "authority_generation": {},
            "generation_pointer_status": generation_pointer_status,
            "authority_sha256": "",
            "entries": [],
            "summary": {
                "total_entries": 0,
                "active_entries": 0,
                "rejected_inactive": 0,
                "rejected_state_artifacts": 0,
                "rejected_invalid": 0,
            },
            "source": str(authority_source_path),
            "compatibility_source": str(source_path),
        }

    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise CapabilityDataError("active capability manifest entries must be an array")

    declared_freshness = normalize(data.get("freshness_status") or "fresh")
    source_hash_mismatches = [*_source_hash_mismatches(data), *generation_mismatches]
    static_source_mismatches = [
        mismatch
        for mismatch in source_hash_mismatches
        if mismatch not in DYNAMIC_AUTHORITY_HASH_KEYS
    ]
    dynamic_source_mismatches = [
        mismatch
        for mismatch in source_hash_mismatches
        if mismatch in DYNAMIC_AUTHORITY_HASH_KEYS
    ]
    dynamic_authority = {
        "status": "current",
        "observed_config_sha256": str(
            (data.get("source_hashes") or {}).get(
                CONFIG_CAPABILITY_SOURCE_HASH_KEY
            )
            or ""
        ).upper(),
        "observed_inventory_sha256": str(
            (data.get("source_hashes") or {}).get("plugin-cache-inventory") or ""
        ).upper(),
        "worker_runtime_bom_status": "current",
        "assessment_digest": "",
        "changed_config_leaves": [],
        "changed_packages": [],
        "quarantined_packages": [],
        "quarantined_capability_ids": [],
        "reason_code": "DYNAMIC_AUTHORITY_CURRENT",
    }
    if not static_source_mismatches and dynamic_source_mismatches:
        dynamic_authority = _dynamic_authority_assessment(
            data, dynamic_source_mismatches
        )
    dynamic_scoped = dynamic_authority.get("status") in {"current", "degraded"}
    if static_source_mismatches or not dynamic_scoped:
        freshness = "stale"
    elif dynamic_source_mismatches:
        freshness = "degraded"
    else:
        freshness = declared_freshness
    quarantined_capability_ids = {
        str(item)
        for item in dynamic_authority.get("quarantined_capability_ids", [])
        if str(item)
    }
    active_entries: list[dict[str, Any]] = []
    rejected_inactive = 0
    rejected_state_artifacts = 0
    rejected_invalid = 0
    rejected_quarantined = 0
    for raw in raw_entries:
        if not isinstance(raw, dict):
            rejected_invalid += 1
            continue
        entry = _normalize_entry(raw)
        if not entry:
            rejected_invalid += 1
            continue
        if is_state_artifact(entry):
            rejected_state_artifacts += 1
            continue
        if entry["id"] in quarantined_capability_ids:
            rejected_quarantined += 1
            continue
        if freshness not in FRESH_STATES or not is_active_state(entry["state"]):
            rejected_inactive += 1
            continue
        active_entries.append(entry)

    return {
        "schema_version": str(data.get("schema_version") or ""),
        "generated_at": str(data.get("generated_at") or ""),
        "snapshot_id": str(data.get("snapshot_id") or ""),
        "freshness_status": freshness,
        "declared_freshness_status": declared_freshness,
        "source_hashes": data.get("source_hashes") if isinstance(data.get("source_hashes"), dict) else {},
        "source_hashes_verified": not static_source_mismatches and dynamic_scoped,
        "static_source_hashes_verified": not static_source_mismatches,
        "source_hash_mismatches": source_hash_mismatches,
        "static_source_hash_mismatches": static_source_mismatches,
        "dynamic_source_hash_mismatches": dynamic_source_mismatches,
        "dynamic_authority_status": str(dynamic_authority.get("status") or "unavailable"),
        "dynamic_authority": dynamic_authority,
        "worker_runtime_bom_status": str(
            dynamic_authority.get("worker_runtime_bom_status") or "unavailable"
        ),
        "authority_generation": (
            data.get("authority_generation")
            if isinstance(data.get("authority_generation"), dict)
            else {}
        ),
        "generation_pointer_status": generation_pointer_status,
        "authority_sha256": authority_sha256,
        "entries": active_entries,
        "summary": {
            "total_entries": len(raw_entries),
            "active_entries": len(active_entries),
            "rejected_inactive": rejected_inactive,
            "rejected_state_artifacts": rejected_state_artifacts,
            "rejected_invalid": rejected_invalid,
            "rejected_quarantined": rejected_quarantined,
        },
        "source": str(authority_source_path),
        "compatibility_source": str(source_path),
    }


def _normalize_override(raw: dict[str, Any], fallback_target: str = "") -> dict[str, Any] | None:
    target = str(raw.get("target") or raw.get("capability") or fallback_target).strip()
    action = normalize(raw.get("action") or raw.get("role") or raw.get("decision"))
    if not target or not action:
        return None
    return {
        "id": str(raw.get("id") or f"override:{target}").strip(),
        "target": target,
        "action": action,
        "requires_primary": str(raw.get("requires_primary") or raw.get("under") or "").strip(),
        "winner": str(raw.get("winner") or raw.get("preferred") or "").strip(),
        "reason": str(raw.get("reason") or "").strip(),
    }


def _normalize_overrides(value: object) -> list[dict[str, Any]]:
    overrides: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for target, raw in value.items():
            if isinstance(raw, str):
                raw = {"action": raw}
            if isinstance(raw, dict):
                normalized = _normalize_override(raw, fallback_target=str(target))
                if normalized:
                    overrides.append(normalized)
    elif isinstance(value, list):
        for raw in value:
            if isinstance(raw, dict):
                normalized = _normalize_override(raw)
                if normalized:
                    overrides.append(normalized)
    return overrides


def _normalize_fallback(value: object) -> dict[str, Any]:
    del value
    return dict(DEFAULT_FALLBACK)


def _normalize_execution_profile(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    try:
        deadline = int(raw.get("deadline_seconds", DEFAULT_EXECUTION_PROFILE["deadline_seconds"]))
    except (TypeError, ValueError):
        deadline = int(DEFAULT_EXECUTION_PROFILE["deadline_seconds"])
    return {
        "execution_owner": "codex_parent",
        "model": "gpt-5.6-sol",
        "reasoning_effort": (
            raw.get("reasoning_effort")
            if raw.get("reasoning_effort") in {"low", "medium", "high", "xhigh", "max", "ultra"}
            else "high"
        ),
        "deadline_seconds": max(1, min(deadline, 7200)),
        "fallback": _normalize_fallback(raw.get("fallback")),
    }


def _normalize_live_dependency_controls(value: object) -> dict[str, dict[str, Any]]:
    """Normalize exact live-inventory controls used by capability rules.

    A dependency is usable only when the canonical manifest contains one of its
    declared live entries, the exact config value agrees, and a request-bound
    live call probe succeeds. Keeping all three pieces in policy prevents the
    router from treating configured or historical inventory as callability.
    """

    controls: dict[str, dict[str, Any]] = {}
    if not isinstance(value, dict):
        return controls
    for raw_id, raw in value.items():
        dependency_id = str(raw_id or "").strip()
        if not dependency_id or not isinstance(raw, dict):
            continue
        manifest_any = _as_list(raw.get("manifest_any"))
        config_path = _as_list(raw.get("config_path"))
        expected_value = raw.get("expected_value")
        raw_probe = raw.get("probe_requirement")
        probe = raw_probe if isinstance(raw_probe, dict) else {}
        probe_kind = str(probe.get("kind") or "").strip()
        probe_target = str(probe.get("target") or "").strip()
        probe_success_status = str(probe.get("success_status") or "").strip()
        if (
            not manifest_any
            or not config_path
            or not isinstance(expected_value, (bool, str, int, float))
            or probe_kind != "live_call"
            or probe_target != dependency_id
            or probe_success_status != "callable"
        ):
            continue
        controls[dependency_id] = {
            "manifest_any": manifest_any,
            "config_path": config_path,
            "expected_value": expected_value,
            "probe_requirement": {
                "kind": probe_kind,
                "target": probe_target,
                "success_status": probe_success_status,
            },
        }
    return controls


def _normalize_dependency_fallback(
    value: object,
    requested_capability: str = "",
) -> dict[str, Any] | None:
    raw = value if isinstance(value, dict) else {}
    chosen_fallback = str(raw.get("chosen_fallback") or "").strip()
    equivalence = str(raw.get("equivalence") or "").strip()
    reason_code = str(raw.get("reason_code") or "").strip()
    selected_capability = str(raw.get("selected_capability") or "").strip()
    equivalent_capabilities = _as_list(raw.get("equivalent_capabilities"))
    if (
        not chosen_fallback
        or equivalence not in {"equivalent", "non_equivalent"}
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", reason_code)
    ):
        return None
    if equivalence == "equivalent":
        allowed_equivalents = {
            item for item in [requested_capability, *equivalent_capabilities] if item
        }
        if not selected_capability or selected_capability not in allowed_equivalents:
            return None
    try:
        max_passes = int(raw.get("max_passes", 1))
        deadline_seconds = int(raw.get("deadline_seconds", 1800))
    except (TypeError, ValueError):
        return None
    if not 1 <= max_passes <= 4 or not 1 <= deadline_seconds <= 7200:
        return None
    return {
        "selected_capability": selected_capability,
        "supports": _as_list(raw.get("supports"))[:ABSOLUTE_MAX_SUPPORTS],
        "equivalent_capabilities": equivalent_capabilities,
        "chosen_fallback": chosen_fallback,
        "equivalence": equivalence,
        "max_passes": max_passes,
        "deadline_seconds": deadline_seconds,
        "reason_code": reason_code,
    }


def _normalize_worker_rules(value: object) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return rules
    for position, raw in enumerate(value):
        if not isinstance(raw, dict) or not isinstance(raw.get("worker"), dict):
            continue
        worker = raw["worker"]
        try:
            priority = int(raw.get("priority", position + 1))
        except (TypeError, ValueError):
            priority = position + 1
        try:
            deadline = int(worker.get("deadline_seconds", 600))
        except (TypeError, ValueError):
            deadline = 600
        execution_owner = str(worker.get("execution_owner") or "codex_child")
        role = str(worker.get("role") or "support")
        model = str(worker.get("model") or "inherit")
        reasoning_effort = worker.get("reasoning_effort")
        if APPROVED_WORKER_CONTRACTS.get((execution_owner, role)) != (
            model,
            reasoning_effort,
        ):
            continue
        rules.append(
            {
                "id": str(raw.get("id") or f"worker-rule-{position + 1}").strip(),
                "priority": priority,
                "match_any": _as_list(raw.get("match_any")),
                "match_all": _as_list(raw.get("match_all")),
                "classification_any": _as_list(raw.get("classification_any")),
                "classification_all": _as_list(raw.get("classification_all")),
                "exclusive": bool(raw.get("exclusive", False)),
                "gateway_managed_upstream": str(raw.get("gateway_managed_upstream") or "").strip(),
                "requires_any_capabilities": _as_list(raw.get("requires_any_capabilities")),
                "worker": {
                    "execution_owner": execution_owner,
                    "role": role,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                    "deadline_seconds": max(1, min(deadline, 3600)),
                    "required": bool(worker.get("required", False)),
                },
                "reason_code": str(raw.get("reason_code") or "EXACT_SUPPORT_MATCH").strip(),
            }
        )
    return sorted(rules, key=lambda item: (item["priority"], item["id"]))


def _normalize_local_execution_rules(value: object) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return rules
    for position, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        try:
            priority = int(raw.get("priority", position + 1))
        except (TypeError, ValueError):
            priority = position + 1
        recipe_id = str(raw.get("recipe_id") or "").strip()
        contract = (
            str(raw.get("local_stack_purpose") or "").strip(),
            str(raw.get("task_type") or "answer").strip(),
            str(raw.get("source_need") or "none").strip(),
            bool(raw.get("exact_evidence", False)),
        )
        if APPROVED_LOCAL_EXECUTION_CONTRACTS.get(recipe_id) != contract:
            continue
        rules.append(
            {
                "id": str(raw.get("id") or f"local-execution-rule-{position + 1}").strip(),
                "priority": priority,
                "match_any": _as_list(raw.get("match_any")),
                "match_all": _as_list(raw.get("match_all")),
                "classification_any": _as_list(raw.get("classification_any")),
                "classification_all": _as_list(raw.get("classification_all")),
                "gateway_managed_upstream": str(raw.get("gateway_managed_upstream") or "").strip(),
                "requires_any_capabilities": _as_list(raw.get("requires_any_capabilities")),
                "recipe_id": recipe_id,
                "local_stack_purpose": contract[0],
                "task_type": contract[1],
                "source_need": contract[2],
                "exact_evidence": contract[3],
                "reason_code": str(raw.get("reason_code") or "LOCAL_EXECUTION_EXACT").strip(),
            }
        )
    return sorted(rules, key=lambda item: (item["priority"], item["id"]))


def _normalize_capability_aliases(value: object) -> dict[str, list[str]]:
    """Return a deterministic, de-duplicated policy alias map."""

    if not isinstance(value, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for raw_reference, raw_aliases in sorted(
        value.items(), key=lambda item: (normalize(item[0]), str(item[0]).casefold())
    ):
        reference = str(raw_reference or "").strip()
        if not reference:
            continue
        aliases = {
            str(alias).strip()
            for alias in (raw_aliases if isinstance(raw_aliases, list) else [])
            if str(alias).strip()
        }
        if aliases:
            normalized[reference] = sorted(
                aliases, key=lambda alias: (normalize(alias), alias.casefold())
            )
    return normalized


def load_routing_policy(
    path: Path | None = None,
    *,
    capability_manifest_path: Path | None = None,
    policy_schema_path: Path | None = None,
) -> dict[str, Any]:
    source_path = path or ROUTING_POLICY_PATH
    data, authority_sha256 = _load_json_compatible_yaml_with_authority(
        source_path, "routing policy"
    )
    if not data:
        return {
            "schema_version": "",
            "decision_snapshot": "",
            "max_supports": DEFAULT_MAX_SUPPORTS,
            "max_worker_supports": DEFAULT_MAX_WORKER_SUPPORTS,
            "default_execution_profile": "codex-sol-default",
            "execution_profiles": {"codex-sol-default": dict(DEFAULT_EXECUTION_PROFILE)},
            "local_execution_rules": [],
            "worker_rules": [],
            "rules": [],
            "live_dependency_controls": {},
            "capability_aliases": {},
            "explicit_overrides": [],
            "authority_sha256": "",
            "source": str(source_path),
        }

    schema_source = policy_schema_path or ROUTING_POLICY_SCHEMA_PATH
    policy_schema = _load_json_compatible_yaml(
        schema_source, "routing policy schema"
    )
    manifest_source = capability_manifest_path or ACTIVE_CAPABILITIES_PATH
    manifest_data, _, _, generation_status, generation_mismatches = (
        _load_manifest_generation_authority(manifest_source)
    )
    if not policy_schema:
        raise CapabilityDataError(
            f"routing policy schema is unavailable: {schema_source}"
        )
    if not manifest_data:
        details = ",".join(generation_mismatches) or generation_status
        raise CapabilityDataError(
            f"capability identity authority is unavailable for policy validation: {details}"
        )
    try:
        _validate_routing_policy(
            data,
            policy_schema,
            manifest_data,
            label="routing policy",
        )
    except RoutingPolicyValidationError as exc:
        raise CapabilityDataError(f"routing policy validation failed: {exc}") from exc

    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list):
        raise CapabilityDataError("routing policy rules must be an array")
    try:
        requested_max = int(data.get("max_supports", data.get("max_support_capabilities", DEFAULT_MAX_SUPPORTS)))
    except (TypeError, ValueError):
        requested_max = DEFAULT_MAX_SUPPORTS
    max_supports = max(0, min(requested_max, ABSOLUTE_MAX_SUPPORTS))
    try:
        requested_worker_max = int(data.get("max_worker_supports", DEFAULT_MAX_WORKER_SUPPORTS))
    except (TypeError, ValueError):
        requested_worker_max = DEFAULT_MAX_WORKER_SUPPORTS
    max_worker_supports = max(
        0,
        min(requested_worker_max, ABSOLUTE_MAX_WORKER_SUPPORTS),
    )

    raw_profiles = data.get("execution_profiles")
    execution_profiles = {
        str(profile_id): _normalize_execution_profile(profile)
        for profile_id, profile in (raw_profiles.items() if isinstance(raw_profiles, dict) else [])
    }
    if not execution_profiles:
        execution_profiles = {"codex-sol-default": dict(DEFAULT_EXECUTION_PROFILE)}
    default_execution_profile = str(
        data.get("default_execution_profile") or next(iter(execution_profiles))
    )
    if default_execution_profile not in execution_profiles:
        default_execution_profile = next(iter(execution_profiles))

    rules: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            continue
        primary = str(raw.get("primary") or raw.get("primary_id") or "").strip()
        if not primary:
            continue
        rules.append(
            {
                "id": str(raw.get("id") or f"rule-{position + 1}").strip(),
                "scenario": str(raw.get("scenario") or raw.get("description") or "").strip(),
                "match_any": _as_list(raw.get("match_any") or raw.get("match")),
                "match_all": _as_list(raw.get("match_all")),
                "primary": primary,
                "supports": _as_list(raw.get("supports") or raw.get("supporting") or raw.get("support")),
                "requires": _as_list(raw.get("requires") or raw.get("required")),
                "forbids": _as_list(raw.get("forbids") or raw.get("forbidden")),
                "authority_limit": str(raw.get("authority_limit") or "advisory-only").strip(),
                "evidence_ids": _as_list(raw.get("evidence_ids") or raw.get("evidence")),
                "execution_profile": str(raw.get("execution_profile") or "").strip(),
                "reason_codes": _as_list(raw.get("reason_codes")),
                "intent_gate": str(raw.get("intent_gate") or "").strip(),
                "requires_live_dependencies": _as_list(
                    raw.get("requires_live_dependencies")
                ),
                "dependency_fallback": _normalize_dependency_fallback(
                    raw.get("dependency_fallback"),
                    primary,
                ),
                "position": position,
            }
        )
    return {
        "schema_version": str(data.get("schema_version") or ""),
        "decision_snapshot": str(data.get("decision_snapshot") or data.get("snapshot_id") or ""),
        "max_supports": max_supports,
        "max_worker_supports": max_worker_supports,
        "default_execution_profile": default_execution_profile,
        "execution_profiles": execution_profiles,
        "local_execution_rules": _normalize_local_execution_rules(data.get("local_execution_rules")),
        "worker_rules": _normalize_worker_rules(data.get("worker_rules")),
        "live_dependency_controls": _normalize_live_dependency_controls(
            data.get("live_dependency_controls")
        ),
        "capability_aliases": _normalize_capability_aliases(
            data.get("capability_aliases")
        ),
        "rules": rules,
        "explicit_overrides": _normalize_overrides(data.get("explicit_overrides") or data.get("overrides")),
        "authority_sha256": authority_sha256,
        "source": str(source_path),
    }


def _prompt_contains(prompt_lower: str, phrase: str) -> bool:
    phrase = phrase.strip().lower()
    if not phrase:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, prompt_lower) is not None


_NON_CODE_BUILD_OBJECT = (
    r"(?:business\s+case|(?:(?:analytical|data\s+analytics)\s+)?dashboard|"
    r"slide\s+deck|presentation|word\s+document|"
    r"document|spreadsheet|workbook|image|illustration|(?:100|hundred)[- ]day\s+plan|"
    r"report|memo|budget|communications?\s+plan|marketing\s+plan|hiring\s+plan|"
    r"operating\s+review|hypothesis\s+tree)"
)
_BUILD_IMPLEMENTATION_VERB = (
    rf"build(?!\s+(?:(?:a|an|the)\s+)?{_NON_CODE_BUILD_OBJECT}\b)"
)
_IMPLEMENTATION_VERB = (
    rf"(?:implement|{_BUILD_IMPLEMENTATION_VERB}|code|refactor|restructure|fix|"
    r"repair|migrate|modify|change\s+(?:code\s+)?module\s+boundaries)"
)
_IMPLEMENTATION_GERUND = r"(?:implementing|building|coding|refactoring|restructuring)"
_IMPLEMENTATION_ADVERB = r"(?:(?:fully|now|finally|actually|[a-z]+ly)\s+)?"
_IMPLEMENTATION_PARTICIPLE = (
    r"(?:implemented|built|coded|refactored|restructured|fixed|repaired|migrated|modified)"
)
_AFFIRMATIVE_IMPLEMENTATION_PATTERNS = (
    re.compile(
        rf"^(?:(?:please|let'?s)\s+)?{_IMPLEMENTATION_ADVERB}{_IMPLEMENTATION_VERB}\b"
    ),
    re.compile(
        rf"(?:[:,;.!?]\s*|\s+-\s+|\b(?:and(?:\s+(?:then|also))?|but|then|to|please|"
        rf"should|must|will|need\s+to|want\s+to|can\s+you|could\s+you|let'?s)\s+)"
        rf"{_IMPLEMENTATION_ADVERB}{_IMPLEMENTATION_VERB}\b"
    ),
    re.compile(
        rf"\b(?:before|by|start|begin|continue)\s+{_IMPLEMENTATION_GERUND}\b"
    ),
    re.compile(r"\bfollowed\s+by\s+(?:an?\s+)?implementation\b"),
    re.compile(r"\b(?:documentation|planning)\s+and\s+implementation\b"),
    re.compile(
        rf"\b(?:it|this|that|the\s+[a-z0-9_-]+)\s+"
        rf"(?:should|must|will|needs?\s+to|has\s+to)\s+be\s+"
        rf"{_IMPLEMENTATION_PARTICIPLE}\b"
    ),
    re.compile(
        r"\bimplementation\s+(?:should|must|will|needs?\s+to|has\s+to)\s+"
        r"be\s+(?:completed|performed|done)\b"
    ),
    re.compile(
        r"\b(?:architecture\s+change\s+in\s+(?:this|the)\s+"
        r"(?:repo|repository|codebase)|architecture\s+refactor\s+implementation)\b"
    ),
)
_DEFERRED_IMPLEMENTATION_PATTERN = re.compile(
    rf"\b(?:will|would|may|might|plan(?:s|ned)?\s+to|intend(?:s|ed)?\s+to)\s+"
    rf"(?:be\s+)?(?:{_IMPLEMENTATION_VERB}|{_IMPLEMENTATION_PARTICIPLE})\b"
    r"[^.!?;]{0,80}\b(?:later|in\s+the\s+future|subsequently|afterwards)\b"
)
_COPYEDIT_IMPLEMENTATION_CONTEXT = re.compile(
    r"\b(?:implement|apply|make|fix|correct)\b[^.!?;]{0,60}"
    r"\b(?:typos?|grammar|spelling|punctuation|wording|formatting|"
    r"grammatical\s+correctness)\b|"
    r"\b(?:typo|grammar|spelling|punctuation|wording|formatting)\s+"
    r"(?:fix|correction|change)\b"
)


def _prompt_has_affirmative_implementation(prompt_lower: str) -> bool:
    raw = _prompt_without_quoted_text(prompt_lower).lower().replace("’", "'")
    text = re.sub(r"[ \t\f\v]+", " ", raw)
    text = re.sub(r"\s*\n+\s*", "; ", text).strip()
    text = _DEFERRED_IMPLEMENTATION_PATTERN.sub(" ", text)
    events: list[tuple[int, bool]] = []
    for pattern in _AFFIRMATIVE_IMPLEMENTATION_PATTERNS:
        events.extend((match.start(), True) for match in pattern.finditer(text))
    events.extend(
        (match.start(), True)
        for match in _CRITIQUE_REPLACEMENT_IMPLEMENTATION.finditer(text)
    )
    if not events:
        return False
    events.extend(
        (match.start(), False)
        for match in _IMPLEMENTATION_REPLACED_BY_CRITIQUE.finditer(text)
    )
    events.sort(key=lambda event: event[0])
    return events[-1][1]


_DIRECT_QUOTED_CAPABILITY_CONTROL = re.compile(
    r"\b(?:use|invoke|run|execute|apply|select|load|route\s+to)\s+"
    r"(?:(?:the|this)\s+)?(?:(?:skill|capability|plugin)\s+)?$",
    re.IGNORECASE,
)


_MARKDOWN_FENCE_OPEN = re.compile(
    r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[^\n]*$"
)


def _mask_markdown_fences(text: str) -> str:
    """Mask closed or unterminated CommonMark fenced code blocks."""

    masked: list[str] = []
    active_fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        line_break = line[len(content) :]
        if active_fence is None:
            opening = _MARKDOWN_FENCE_OPEN.match(content)
            if opening:
                fence = opening.group("fence")
                active_fence = (fence[0], len(fence))
                masked.append(line_break or " ")
            else:
                masked.append(line)
            continue

        fence_character, minimum_length = active_fence
        closing = re.fullmatch(
            rf"[ \t]{{0,3}}{re.escape(fence_character)}{{{minimum_length},}}[ \t]*",
            content,
        )
        masked.append(line_break or " ")
        if closing:
            active_fence = None
    return "".join(masked)


def _prompt_without_quoted_text(prompt: str) -> str:
    """Mask examples and quoted material before intent classification.

    Security examples are commonly pasted as Markdown. Treating their contents as
    live directives lets a code sample silently select a capability. Quoted text is
    preserved only when the surrounding prose directly invokes that quoted skill or
    capability name.
    """

    text = (
        str(prompt or "")
        .replace("’", "'")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    text = _mask_markdown_fences(text)
    text = re.sub(r"(?m)^(?:[ ]{4}|\t).*$", " ", text)
    text = re.sub(r"(?m)^[ \t]*>.*$", " ", text)

    def mask_quoted(match: re.Match[str]) -> str:
        prefix = text[max(0, match.start() - 100) : match.start()]
        if _DIRECT_QUOTED_CAPABILITY_CONTROL.search(prefix):
            return match.group("content")
        content = match.group("content").rstrip()
        return content[-1] if content.endswith((".", "!", "?", ";")) else " "

    quote_patterns = (
        r"“(?P<content>.*?)”",
        r"„(?P<content>.*?)“",
        r'"(?P<content>.*?)"',
        r"(?<!`)`(?P<content>[^`\n]+)`(?!`)",
    )
    for pattern in quote_patterns:
        text = re.sub(pattern, mask_quoted, text, flags=re.DOTALL)
    return text


_CRITIQUE_ACTION = (
    r"(?:deep\s+critique|source[- ]backed\s+critique|critique|audit|challenge|"
    r"review|validate|compare|stress[- ]test|pressure[- ]test|poke\s+holes\s+in|"
    r"find\s+flaws\s+in|tear\s+apart)"
)
_SOURCE_EVALUATION_ACTION = (
    r"(?:source[- ]backed\s+critique|fact[- ]check|"
    r"validate\s+(?:(?:the|these)\s+)?(?:sources?|claims?)|"
    r"check\s+(?:the\s+)?evidence|"
    r"verify\s+(?:whether\s+)?(?:(?:the|these)\s+)?"
    r"(?:sources?|citations?|they|them)|"
    r"confirm\s+(?:(?:the|these)\s+)?(?:sources?|citations?)|"
    r"run\s+(?:a\s+)?citation\s+audit|"
    r"review\s+(?:(?:the|these)\s+)?(?:sources?|citations?|evidence\s+chain|"
    r"evidence\s+quality)"
    r"(?:\s+for\s+(?:credibility|authenticity|reliability|sufficiency))?|"
    r"check\s+(?:the\s+)?(?:source|citation)\s+credibility|"
    r"evaluate\s+(?:(?:the|these)\s+)?(?:sources?|citations?)"
    r"(?:\s+for\s+(?:credibility|authenticity|reliability|sufficiency))?|"
    r"assess\s+(?:(?:the|these)\s+)?(?:sources?|citations?|source\s+credibility|"
    r"citation\s+credibility|evidence\s+quality))"
)
_DIRECTIVE_SPLIT = re.compile(r"(?:[.!?;]+|\bbut\b)")
_LEADING_DISCOURSE = re.compile(
    r"^(?:(?:actually|then|however|instead|finally|on\s+second\s+thought)\s*,?\s*)+"
)


def _directive_clauses(prompt: str) -> list[str]:
    raw = _prompt_without_quoted_text(prompt).lower()
    text = re.sub(r"[ \t\f\v]+", " ", raw)
    text = re.sub(r"\s*\n+\s*", "; ", text).strip()
    file_dot = "\ue000"
    text = text.replace(file_dot, " ")
    text = re.sub(
        r"\b[a-z0-9_-]+(?:\.[a-z0-9_-]+)*\."
        r"(?:pdf|docx?|xlsx?|csv|json|ya?ml|py|js|ts|tsx|jsx|md)\b",
        lambda match: match.group(0).replace(".", file_dot),
        text,
    )
    return [
        clause.replace(file_dot, ".").strip(" ,")
        for clause in _DIRECTIVE_SPLIT.split(text)
        if clause.strip(" ,")
    ]


def _clause_directive_polarity(
    clause: str,
    action_pattern: str,
    *,
    special_positive: Iterable[re.Pattern[str]] = (),
) -> bool | None:
    text = _LEADING_DISCOURSE.sub("", clause.strip())
    if not text:
        return None
    negative = re.match(
        rf"^(?:please\s+)?(?:do\s+not|don'?t|dont|never)\s+"
        rf"(?:please\s+)?{action_pattern}\b",
        text,
    )
    if negative:
        return False
    affirmative_patterns = (
        rf"^(?:(?:please|now|carefully|deeply|critically)\s+)*{action_pattern}\b",
        rf"^(?:can|could|would|will)\s+you\s+(?:please\s+)?{action_pattern}\b",
        rf"^(?:i|we)\s+(?:want|need|would\s+like|prefer)\s+"
        rf"(?:you\s+)?to\s+{action_pattern}\b",
    )
    if any(re.match(pattern, text) for pattern in affirmative_patterns):
        return True
    if any(pattern.search(text) for pattern in special_positive):
        return True
    return None


_CRITIQUE_SPECIAL_POSITIVE = (
    re.compile(
        r"^(?:please\s+)?give\s+(?:me\s+)?(?:this|the|a|an)?\s*"
        r"[^.!?;]{0,100}\b(?:hard\s+second\s+opinion|skeptical\s+review)\b"
    ),
    re.compile(
        r"^analy[sz]e\b[^.!?;]{0,160}\bdeeply\b"
        r"[^.!?;]{0,160}\bchallenge\b"
    ),
    re.compile(r"^(?:please\s+)?be\s+critical\b"),
    re.compile(
        r"^what\s+do\s+you\s+think\s+(?:of|about)\s+"
        r"(?:this|that|my|our|the)\s+(?:proposal|recommendation|analysis|plan|"
        r"strategy|argument|decision|operating\s+model|business\s+case|"
        r"forecast|model|workflow|report|memo|draft|document|policy|"
        r"slide\s+deck|presentation|prd|concept|operating\s+update|"
        r"security\s+policy|"
        r"(?:repository|software|application|authentication)\s+architecture|"
        r"architecture|approach)\b"
    ),
    re.compile(
        r"^is\s+(?:this|that|my|our|the)\s+"
        r"(?:proposal|analysis|recommendation|plan|strategy|argument|decision|"
        r"operating\s+model|business\s+case|forecast|model|workflow|report|"
        r"memo|draft|document|policy|slide\s+deck|presentation|prd|concept|"
        r"operating\s+update|security\s+policy|"
        r"(?:repository|software|application|authentication)\s+"
        r"architecture|architecture|approach)\b"
        r"[^.!?;]{0,100}\b(?:good|correct|right|sound|strong|ready|accurate|"
        r"valid|credible|defensible|secure|safe|complete|consistent|viable)\b"
    ),
    re.compile(
        r"^should\s+(?:we|i)\s+use\s+(?:this|the|our|my)\s+"
        r"(?:recommendation|proposal|analysis|plan|strategy|approach|decision|"
        r"operating\s+model|business\s+case|forecast|model|workflow|report|"
        r"memo|draft|document|policy|slide\s+deck|presentation|prd|concept|"
        r"operating\s+update|security\s+policy|"
        r"(?:repository|software|application|authentication)\s+"
        r"architecture|architecture)\b"
    ),
    re.compile(
        r"^should\s+(?:we|i)\s+(?:use|adopt|keep|approve|rely\s+on)\s+"
        r"(?:this|that|the|our|my)\s+(?:proposal|recommendation|analysis|plan|"
        r"strategy|argument|decision|operating\s+model|business\s+case|"
        r"forecast|model|workflow|report|memo|draft|document|security\s+policy|"
        r"(?:repository|software|application|authentication)\s+architecture|"
        r"architecture|approach)\b"
    ),
    re.compile(
        r"^does\s+(?:this|that|the|our|my)\s+(?:proposal|recommendation|"
        r"analysis|plan|strategy|argument|decision|workflow|report|memo|"
        r"security\s+policy|(?:repository|software|application|authentication)\s+"
        r"architecture|architecture|approach)\b[^.!?;]{0,80}\blook\s+"
        r"(?:good|correct|right|sound|strong|ready|accurate|credible|"
        r"defensible|secure|safe|complete|consistent|viable)\b"
    ),
    re.compile(
        r"^what\s+(?:are\s+)?(?:the\s+)?(?:weaknesses?|flaws?|gaps?)\s+"
        r"(?:are\s+)?(?:in|of)\s+(?:this|that|the|our|my)\s+"
        r"(?:proposal|recommendation|analysis|plan|strategy|argument|decision|"
        r"workflow|report|memo|security\s+policy|(?:repository|software|"
        r"application|authentication)\s+architecture|architecture|approach)\b"
    ),
    re.compile(
        r"^(?:use|ask)\s+(?:terra|antigravity)\b[^.!?;]{0,100}\bto\s+"
        rf"{_CRITIQUE_ACTION}\b"
    ),
)

_CRITIQUE_PDF_SPECIAL_POSITIVE = (
    re.compile(
        r"^what\s+do\s+you\s+think\s+(?:of|about)\s+"
        r"(?:(?:this|that|my|our|the|a|an)\s+)?"
        r"(?:attached|uploaded|supplied|provided)?\s*"
        r"(?:pdf|pdf\s+file|[a-z0-9._-]+\.pdf)\b"
    ),
    re.compile(
        r"^is\s+(?:(?:this|that|my|our|the|a|an)\s+)?"
        r"(?:attached|uploaded|supplied|provided)?\s*"
        r"(?:pdf|pdf\s+file|[a-z0-9._-]+\.pdf)\b[^.!?;]{0,100}"
        r"\b(?:good|correct|right|sound|strong|ready|accurate|credible|"
        r"defensible|complete|consistent)\b"
    ),
    re.compile(
        r"^should\s+(?:we|i)\s+(?:use|adopt|keep|approve|rely\s+on)\s+"
        r"(?:(?:this|that|my|our|the|a|an)\s+)?"
        r"(?:attached|uploaded|supplied|provided)?\s*"
        r"(?:pdf|pdf\s+file|[a-z0-9._-]+\.pdf)\b"
    ),
    re.compile(
        r"^does\s+(?:(?:this|that|my|our|the|a|an)\s+)?"
        r"(?:attached|uploaded|supplied|provided)?\s*"
        r"(?:pdf|pdf\s+file|[a-z0-9._-]+\.pdf)\b[^.!?;]{0,80}\blook\s+"
        r"(?:good|correct|right|sound|strong|ready|accurate|credible|"
        r"defensible|complete|consistent)\b"
    ),
    re.compile(
        r"^what\s+(?:are\s+)?(?:the\s+)?(?:weaknesses?|flaws?|gaps?)\s+"
        r"(?:are\s+)?(?:in|of)\s+"
        r"(?:(?:this|that|my|our|the|a|an)\s+)?"
        r"(?:attached|uploaded|supplied|provided)?\s*"
        r"(?:pdf|pdf\s+file|[a-z0-9._-]+\.pdf)\b"
    ),
)


_CRITIQUE_STRONG_ACTION = (
    r"(?:\$?deep[- ]critic|deep\s+critique|source[- ]backed\s+critique|"
    r"critique|audit|challenge|validate|stress[- ]test|pressure[- ]test|"
    r"poke\s+holes\s+in|find\s+flaws\s+in|tear\s+apart)"
)
_CRITIQUE_WEAK_ACTION = r"(?:review|compare)"
_CRITIQUE_EVENT_ACTION = rf"(?:{_CRITIQUE_STRONG_ACTION}|{_CRITIQUE_WEAK_ACTION})"
_NONCRITIQUE_EVENT_ACTION = (
    r"(?:summari[sz]e|extract|transcribe|rewrite|translate|list|proofread|"
    r"fix|correct)"
)
_CRITIQUE_LEADING_DISCOURSE = re.compile(
    r"^(?:(?:actually|then|however|rather(?!\s+than)|finally|"
    r"on\s+second\s+thought)\s*,?\s*)+"
)
_CRITIQUE_REQUEST_WRAPPER = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"(?:i|we)\s+(?:want|need|would\s+like|prefer)\s+"
    r"(?:you\s+)?to\s+)"
)
_CRITIQUE_DIRECTIVE_INTRO = (
    r"(?:^|\b(?P<connector>and\s+also|and\s+then|and|then|also)\s+|"
    r"(?P<comma>,)\s*)"
)
_CRITIQUE_DIRECTIVE_MODIFIERS = (
    r"(?P<modifiers>(?:(?:please|now|only|just|instead|carefully|deeply|"
    r"critically)\s*,?\s+)*)"
)
_CRITIQUE_NEGATION = (
    r"(?P<negative>(?:do\s+not|don'?t|dont|"
    r"don'?t\s+do(?:\s+(?:another|any|more))?|"
    r"no(?:\s+(?:more|another))?|never|avoid(?:ing)?|without)\s+)?"
)
_CRITIQUE_POST_NEGATION_MODIFIERS = (
    r"(?P<post_modifiers>(?:(?:please|any|ever|again|further|only|just|instead|carefully|"
    r"deeply|critically)\s*,?\s+)*)"
)
_CRITIQUE_EVENT_FINDER = re.compile(
    rf"{_CRITIQUE_DIRECTIVE_INTRO}{_CRITIQUE_DIRECTIVE_MODIFIERS}"
    rf"{_CRITIQUE_NEGATION}{_CRITIQUE_POST_NEGATION_MODIFIERS}"
    rf"(?P<action>{_CRITIQUE_EVENT_ACTION})(?=\b|$)"
)
_NONCRITIQUE_EVENT_FINDER = re.compile(
    rf"{_CRITIQUE_DIRECTIVE_INTRO}{_CRITIQUE_DIRECTIVE_MODIFIERS}"
    rf"{_CRITIQUE_NEGATION}{_CRITIQUE_POST_NEGATION_MODIFIERS}"
    rf"(?P<action>{_NONCRITIQUE_EVENT_ACTION})(?=\b|$)"
)
_DELIVERABLE_EVENT_ACTION = (
    r"(?:create|build|generate|prepare|write|edit|read|inspect|analy[sz]e|"
    r"calculate|convert|extract|transcribe)"
)
_DELIVERABLE_EVENT_FINDER = re.compile(
    rf"{_CRITIQUE_DIRECTIVE_INTRO}{_CRITIQUE_DIRECTIVE_MODIFIERS}"
    rf"{_CRITIQUE_NEGATION}{_CRITIQUE_POST_NEGATION_MODIFIERS}"
    rf"(?P<action>{_DELIVERABLE_EVENT_ACTION})(?=\b|$)"
)
_CRITIQUE_REPLACEMENT_IMPLEMENTATION = re.compile(
    rf"(?:^|[,;:.!?]\s*|\b(?:and(?:\s+then)?|then)\s+)"
    rf"(?:(?:actually|please)\s*,?\s+)*(?:"
    rf"instead\s*,?\s*{_IMPLEMENTATION_ADVERB}{_IMPLEMENTATION_VERB}\b|"
    rf"{_IMPLEMENTATION_ADVERB}{_IMPLEMENTATION_VERB}\b"
    rf"[^,;.!?]{{0,100}}\binstead\b(?!\s+of\b))"
)
_IMPLEMENTATION_REPLACED_BY_CRITIQUE = re.compile(
    rf"(?:^|[,;:.!?]\s*|\b(?:and(?:\s+then)?|then)\s+)"
    rf"(?:(?:actually|please)\s*,?\s+)*(?:"
    rf"instead\s*,?\s*{_CRITIQUE_EVENT_ACTION}\b|"
    rf"{_CRITIQUE_EVENT_ACTION}\b[^,;.!?]{{0,100}}\binstead\b(?!\s+of\b))"
)
_CRITIQUE_TEXT_MARKER = re.compile(
    r"\b(?:grammar|grammatical\s+errors?|spelling|spelling\s+errors?|"
    r"punctuation|punctuation\s+errors?|wording|capitalization|formatting|"
    r"typos?|typographical\s+errors?|grammatical\s+correctness|"
    r"grammatically\s+(?:correct|sound|accurate)|style|tone)\b"
)
_CRITIQUE_EXPLICIT_TEXT_ONLY_SCOPE = re.compile(
    r"\b(?:for|focused\s+on|limited\s+to)\s+(?:(?:the|its|this)\s+)?"
    r"(?:grammar|grammatical\s+errors?|spelling|spelling\s+errors?|"
    r"punctuation|punctuation\s+errors?|wording|capitalization|formatting|"
    r"typos?|typographical\s+errors?|grammatical\s+correctness|"
    r"grammatically\s+(?:correct|sound|accurate)|style|tone)\b"
    r"[^.!?;]{0,24}\b(?:only|just)\b|"
    r"\b(?:only|just)\s+(?:(?:the|its|this)\s+)?"
    r"(?:grammar|grammatical\s+errors?|spelling|spelling\s+errors?|"
    r"punctuation|punctuation\s+errors?|wording|capitalization|formatting|"
    r"typos?|typographical\s+errors?|grammatical\s+correctness|"
    r"grammatically\s+(?:correct|sound|accurate)|style|tone)\b"
    r"|\b(?:grammar|grammatical\s+errors?|spelling|spelling\s+errors?|"
    r"punctuation|punctuation\s+errors?|wording|capitalization|formatting|"
    r"typos?|typographical\s+errors?|grammatical\s+correctness|"
    r"grammatically\s+(?:correct|sound|accurate)|style|tone)\b"
    r"[^.!?;]{0,16}\b(?:only|just)\b"
)
_CRITIQUE_SUBSTANTIVE_MARKER = re.compile(
    r"\b(?:assumptions?|reasoning|logic|arguments?|strategy|conclusions?|"
    r"evidence|methodology|accuracy|validity|credibility|flaws?|gaps?|"
    r"failure\s+modes?|decision\s+quality|recommendations?|"
    r"security|auth(?:entication|orization)?|access\s+controls?|permissions?|"
    r"vulnerabilit(?:y|ies)|rls|row[- ]level\s+security|"
    r"dependency\s+direction|module\s+boundaries|coupling|architecture|"
    r"what(?:'s|\s+is)\s+wrong)\b"
)
_CRITIQUE_MIXED_SUBSTANTIVE_MARKER = re.compile(
    r"\b(?:assumptions?|reasoning|logic|arguments?|strategy|conclusions?|"
    r"evidence|methodology|accuracy|validity|credibility|flaws?|gaps?|"
    r"failure\s+modes?|decision\s+quality|recommendations?|coupling|"
    r"dependency\s+direction|module\s+boundaries|"
    r"(?:missing|weak|flawed|incorrect|unsafe)\s+(?:authentication|"
    r"authorization|access\s+controls?|permissions?|security|rls))\b"
)
_CRITIQUE_MIXED_TEXT_SUBSTANTIVE_SCOPE = re.compile(
    rf"(?:{_CRITIQUE_MIXED_SUBSTANTIVE_MARKER.pattern})[^.!?;]{{0,80}}"
    rf"(?:\s*(?:,|&)\s*|\s+\b(?:and|plus|along\s+with|together\s+with|"
    rf"as\s+well\s+as)\b\s+)"
    rf"[^.!?;]{{0,40}}"
    rf"(?:{_CRITIQUE_TEXT_MARKER.pattern})|"
    rf"(?:{_CRITIQUE_TEXT_MARKER.pattern})[^.!?;]{{0,80}}"
    rf"(?:\s*(?:,|&)\s*|\s+\b(?:and|plus|along\s+with|together\s+with|"
    rf"as\s+well\s+as)\b\s+)"
    rf"[^.!?;]{{0,40}}"
    rf"(?:{_CRITIQUE_MIXED_SUBSTANTIVE_MARKER.pattern})"
)
_CRITIQUE_EXPLICIT_SCOPE_PREFIX = re.compile(
    r"^\s*(?:,\s*)?(?:only|just|instead)\b"
)
_CRITIQUE_EXPLICIT_SCOPE_SUFFIX = re.compile(
    r"\b(?:only|just|instead)"
    r"(?:\s*,?\s*(?:please|if\s+possible|if\s+you\s+can))?\s*$"
)
_CRITIQUE_ADDITIVE_SUFFIX = re.compile(
    r"\b(?:also|too|as\s+well)"
    r"(?:\s*,?\s*(?:please|if\s+possible|if\s+you\s+can))?\s*$"
)
_CRITIQUE_AMBIGUOUS_PRONOUN_TARGET = re.compile(
    r"^\s*(?:it|this|that|them|these|those)\s*$"
)
_CRITIQUE_TEXT_QUALIFIER_NOUN = (
    r"(?:assumptions?|reasoning|logic|arguments?|strategy|conclusions?|"
    r"evidence|methodology|recommendations?|security\s+policy|"
    r"authentication\s+documentation|access\s+control|rls\s+policy|"
    r"(?:repository|codebase|module|software|application|authentication)\s+"
    r"architecture|module\s+boundaries|dependency\s+direction|"
    r"document|memo|report|plan|proposal|prd|policy|"
    r"(?:supplied|provided)\s+pdf|pdf)"
)
_CRITIQUE_LINGUISTIC_MENTION_CONTEXT = re.compile(
    r"\b(?:word|phrase|term)\b[^.!?;]{0,100}\b(?:security|authentication|"
    r"authorization|access\s+control|rls|repository\s+architecture|"
    r"authentication\s+architecture|architecture)\b|"
    r"\b(?:wording|grammar|spelling|punctuation)\s+of\s+(?:a|the)\s+"
    r"sentence\b|"
    r"\b(?:sentence|essay)\b[^.!?;]{0,120}\b(?:about|mentions?|containing)\b"
)
_CRITIQUE_EVALUATION_MARKER = re.compile(
    r"\b(?:assumptions?|reasoning|logic|arguments?|conclusions?|evidence|"
    r"methodology|accuracy|validity|credibility|completeness|consistency|"
    r"coherence|feasibility|rigou?r|reliability|soundness|quality|"
    r"flaws?|gaps?|weakness(?:es)?|failure\s+modes?|decision\s+quality|"
    r"recommendations?|coupling|dependency\s+direction|module\s+boundaries|"
    r"(?:missing|weak|flawed|incorrect|unsafe|incomplete|inconsistent)\s+"
    r"(?:authentication|authorization|access\s+controls?|permissions?|"
    r"security|rls|evidence|logic|reasoning|assumptions?)|"
    r"access\s+control\s+design|authorization\s+controls?|"
    r"authentication\s+flow|rls\s+polic(?:y|ies))\b"
)
_CRITIQUE_ARTIFACT_TARGET = re.compile(
    r"(?:\b(?:attached|uploaded|provided|supplied)?\s*pdf(?:\s+file)?\b|"
    r"\b[a-z0-9._-]+\.pdf\b|"
    r"\b(?:proposal|recommendation|analysis|plan|strategy|argument|decision|"
    r"operating\s+model|business\s+case|forecast|model|workflow|report|memo|"
    r"draft|document|policy|slide\s+deck|presentation|prd|concept|"
    r"operating\s+update|security\s+(?:policy|architecture)|"
    r"authorization\s+policy|access\s+control\s+documentation|rls\s+policy|"
    r"(?:repository|codebase|module|system|software|application|"
    r"authentication)\s+architecture|module\s+boundaries|"
    r"evidence\s+chain|sources?|citations?)\b)"
)
_CRITIQUE_SCOPE_SEPARATOR = re.compile(
    r"\s*(?:,|&)\s*|\s+\b(?:and|plus|along\s+with|together\s+with|"
    r"as\s+well\s+as)\b\s+"
)
_CRITIQUE_ALTERNATIVE_SCOPE = re.compile(r"\b(?:instead\s+of|rather\s+than)\b")
_CRITIQUE_NON_SCOPE_ACTION = re.compile(
    rf"^(?:please\s+)?(?:{_IMPLEMENTATION_ADVERB}{_IMPLEMENTATION_VERB}|"
    rf"{_NONCRITIQUE_EVENT_ACTION})\b"
)
_CRITIQUE_SCOPE_FILLER = re.compile(
    r"^(?:also|then|please|now|carefully|deeply|critically|"
    r"if\s+possible|if\s+you\s+can|the|this|that|my|our|its?)$"
)
_CRITIQUE_NEGATED_SCOPE_PREFIX = re.compile(
    r"^(?:please\s+)?(?:not|no|without|excluding|except(?:\s+for)?|"
    r"omit(?:ting)?|skip(?:ping)?|avoid(?:ing)?|do\s+not|don'?t|dont)\b"
)
_CRITIQUE_NEGATED_SCOPE_SUFFIX = re.compile(
    r"\b(?:not|without|excluding|except(?:\s+for)?|omit(?:ting)?|"
    r"skip(?:ping)?|avoid(?:ing)?|do\s+not|don'?t|dont)\b[^,;.!?]*$"
)
_CRITIQUE_ARTIFACT_QUALIFIED_TEXT = re.compile(
    rf"\b[a-z0-9._-]+\.pdf(?:'s|’s|['’])?\s+"
    rf"(?:{_CRITIQUE_TEXT_MARKER.pattern})|"
    rf"(?:{_CRITIQUE_ARTIFACT_TARGET.pattern})(?:'s|’s|['’])?\s+"
    rf"(?:{_CRITIQUE_TEXT_MARKER.pattern})|"
    rf"(?:{_CRITIQUE_ARTIFACT_TARGET.pattern})\s+for\s+"
    rf"(?:{_CRITIQUE_TEXT_MARKER.pattern})|"
    rf"(?:{_CRITIQUE_TEXT_MARKER.pattern})\s+(?:in|of|for)\s+"
    rf"(?:(?:this|the|my|our|a|an)\s+)?(?:{_CRITIQUE_ARTIFACT_TARGET.pattern})"
)
_CRITIQUE_SEMANTIC_COPYEDIT_SUBJECT = re.compile(
    rf"(?:{_CRITIQUE_ARTIFACT_TARGET.pattern})(?:'s|’s|['’])?\s+"
    rf"(?:{_CRITIQUE_TEXT_MARKER.pattern})|"
    rf"(?:{_CRITIQUE_ARTIFACT_TARGET.pattern})\s+for\s+"
    rf"(?:{_CRITIQUE_TEXT_MARKER.pattern})(?:\s+only)?"
)
_CRITIQUE_SEMANTIC_OPERATOR = re.compile(
    r"^(?:what\s+(?:do\s+you\s+think\s+(?:of|about)|(?:are\s+)?"
    r"(?:the\s+)?(?:weaknesses?|flaws?|gaps?)\s+(?:are\s+)?(?:in|of))|"
    r"is|does|should\s+(?:we|i))\b"
)


def _semantic_question_is_copyedit_only(text: str) -> bool:
    if not _CRITIQUE_SEMANTIC_COPYEDIT_SUBJECT.search(text):
        return False
    residual = _CRITIQUE_SEMANTIC_COPYEDIT_SUBJECT.sub(" ", text)
    residual = _CRITIQUE_SEMANTIC_OPERATOR.sub(" ", residual)
    residual = re.sub(
        r"\b(?:good|correct|right|sound|strong|ready|accurate|credible|"
        r"defensible|secure|safe|complete|consistent|viable|use|adopt|keep|"
        r"approve|rely\s+on)\b",
        " ",
        residual,
    )
    return not _CRITIQUE_EVALUATION_MARKER.search(residual)


_CRITIQUE_CONTAINER_SUBJECT_MENTION = re.compile(
    rf"(?P<container>\b(?:(?:this|that|my|our|the|a|an)\s+)?"
    rf"(?:attached|uploaded|provided|supplied)?\s*"
    rf"(?:pdf(?:\s+file)?|[a-z0-9._-]+\.pdf|report|document)\b)"
    rf"\s+(?:about|describing|covering|concerning|regarding|mentioning|on|"
    rf"that\s+discusses|with\s+(?:a\s+)?section\s+on)\s+[^,;.!?]*?"
    rf"(?=(?:\s+(?:for|with)\s+[^,;.!?]*?"
    rf"(?:{_CRITIQUE_EVALUATION_MARKER.pattern}))|$)"
)

_CRITIQUE_WORD_PRODUCT_REFERENCE = re.compile(
    r"\b(?P<prefix>(?:this|that|my|our|attached|uploaded|provided|supplied|"
    r"microsoft)\s+)(?P<product>word)\b"
)
_CRITIQUE_WORD_PRODUCT_MARKER = "\ue001"


def _strip_critique_linguistic_mentions(text: str) -> tuple[str, bool]:
    """Remove quoted-domain mentions while preserving later evaluation scope."""

    value = _CRITIQUE_WORD_PRODUCT_REFERENCE.sub(
        lambda match: f"{match.group('prefix')}{_CRITIQUE_WORD_PRODUCT_MARKER}",
        text,
    )
    found = False
    marker = _CRITIQUE_EVALUATION_MARKER.pattern
    patterns = (
        re.compile(
            rf"\b(?:word|phrase|term)\b[^,;.!?]*?"
            rf"(?=(?:\s+\b(?:for|with)\b\s+[^,;.!?]*?(?:{marker}))|$)"
        ),
        re.compile(
            rf"\b(?:(?:history\s+)?essay|sentence)\b[^,;.!?]*?"
            rf"\b(?:about|mentions?|containing)\b[^,;.!?]*?"
            rf"(?=(?:\s+\b(?:for|with)\b\s+[^,;.!?]*?(?:{marker}))|$)"
        ),
        re.compile(
            rf"\b(?:{_CRITIQUE_TEXT_MARKER.pattern})\s+(?:in|of|for)\s+"
            rf"[^,;.!?]*?(?=(?:\s+\b(?:and|plus|along\s+with|"
            rf"together\s+with|as\s+well\s+as)\b\s+[^,;.!?]*?"
            rf"(?:{marker}))|$)"
        ),
    )
    for pattern in patterns:
        value, count = pattern.subn(" ", value)
        found = found or bool(count)
    value = value.replace(_CRITIQUE_WORD_PRODUCT_MARKER, "word")
    return re.sub(r"\s+", " ", value).strip(), found


def _critique_positive_scope_atoms(segment: str) -> tuple[list[str], bool]:
    """Return affirmative scope atoms after exclusions and linguistic mentions."""

    atoms: list[str] = []
    mention_seen = False
    for raw_atom in _CRITIQUE_SCOPE_SEPARATOR.split(segment):
        atom = raw_atom.strip(" ,")
        atom = re.sub(r"^(?:also|then)\s+", "", atom)
        atom = _CRITIQUE_ALTERNATIVE_SCOPE.split(atom, maxsplit=1)[0].strip(" ,")
        if (
            not atom
            or _CRITIQUE_NEGATED_SCOPE_PREFIX.search(atom)
            or _CRITIQUE_NON_SCOPE_ACTION.search(atom)
            or _CRITIQUE_SCOPE_FILLER.fullmatch(atom)
        ):
            continue
        atom = _CRITIQUE_NEGATED_SCOPE_SUFFIX.sub(" ", atom).strip(" ,")
        if not atom:
            continue
        atom, found = _strip_critique_linguistic_mentions(atom)
        mention_seen = mention_seen or found
        if _CRITIQUE_SCOPE_FILLER.fullmatch(atom):
            continue
        if atom and re.search(r"[a-z0-9]", atom):
            atoms.append(atom)
    return atoms, mention_seen


def _critique_atom_is_textual(atom: str) -> bool:
    if not _CRITIQUE_TEXT_MARKER.search(atom):
        return False
    qualified = _CRITIQUE_ARTIFACT_QUALIFIED_TEXT.search(atom)
    residual = _CRITIQUE_ARTIFACT_QUALIFIED_TEXT.sub(" ", atom)
    residual = _CRITIQUE_QUALIFIED_TEXT_SCOPE.sub(" ", residual)
    return bool(
        qualified
        or not _CRITIQUE_EVALUATION_MARKER.search(residual)
    )


def _critique_atom_is_substantive(atom: str) -> bool:
    residual = _CRITIQUE_ARTIFACT_QUALIFIED_TEXT.sub(" ", atom)
    residual = _CRITIQUE_QUALIFIED_TEXT_SCOPE.sub(" ", residual)
    if _CRITIQUE_EVALUATION_MARKER.search(residual):
        return True
    return bool(
        _CRITIQUE_ARTIFACT_TARGET.search(residual)
        and not _CRITIQUE_TEXT_MARKER.search(residual)
    )


def _critique_segment_has_evaluation_criterion(segment: str) -> bool:
    atoms, _ = _critique_positive_scope_atoms(segment)
    for atom in atoms:
        residual = _CRITIQUE_ARTIFACT_QUALIFIED_TEXT.sub(" ", atom)
        residual = _CRITIQUE_QUALIFIED_TEXT_SCOPE.sub(" ", residual)
        if _CRITIQUE_EVALUATION_MARKER.search(residual):
            return True
    textual_atoms = [_critique_atom_is_textual(atom) for atom in atoms]
    if any(textual_atoms) and any(not textual for textual in textual_atoms):
        return True
    return False


def _critique_live_domain_text(prompt: str, effective_clause: str = "") -> str:
    """Return affirmative, non-copyediting scope for specialist ownership."""

    source = (
        effective_clause
        if effective_clause and _prompt_has_affirmative_critique_intent(prompt)
        else _normalized_unquoted_prompt(prompt)
    )
    atoms, _ = _critique_positive_scope_atoms(source)
    live_atoms: list[str] = []
    for atom in atoms:
        atom, _ = _strip_critique_linguistic_mentions(atom)
        atom = _CRITIQUE_CONTAINER_SUBJECT_MENTION.sub(
            lambda match: match.group("container"),
            atom,
        )
        atom = _CRITIQUE_ARTIFACT_QUALIFIED_TEXT.sub(" ", atom)
        atom = _CRITIQUE_QUALIFIED_TEXT_SCOPE.sub(" ", atom)
        atom = re.sub(r"\s+", " ", atom).strip(" ,")
        if atom:
            live_atoms.append(atom)
    return " ".join(live_atoms)
_CRITIQUE_QUALIFIED_TEXT_SCOPE = re.compile(
    rf"\b{_CRITIQUE_TEXT_QUALIFIER_NOUN}(?:'s|’s|['’])\s+"
    rf"(?:grammar|grammatical\s+errors?|spelling|punctuation|wording|"
    rf"capitalization|formatting|typos?|typographical\s+errors?|"
    rf"grammatical\s+correctness|grammatically\s+(?:correct|sound|accurate)|style|tone)\b|"
    rf"\b(?:grammar|grammatical\s+errors?|spelling|punctuation|wording|"
    rf"capitalization|formatting|typos?|typographical\s+errors?|"
    rf"grammatical\s+correctness|grammatically\s+(?:correct|sound|accurate)|style|tone)"
    rf"\s+(?:in|of|for)\s+(?:(?:this|the|my|our)\s+)?"
    rf"{_CRITIQUE_TEXT_QUALIFIER_NOUN}\b|"
    rf"\b(?:(?:this|the|my|our)\s+)?{_CRITIQUE_TEXT_QUALIFIER_NOUN}"
    rf"\s+(?:for|focused\s+on|limited\s+to)\s+(?:(?:the|its)\s+)?"
    rf"(?:grammar|grammatical\s+errors?|spelling|punctuation|wording|"
    rf"capitalization|formatting|typos?|typographical\s+errors?|"
    rf"grammatical\s+correctness|grammatically\s+(?:correct|sound|accurate)|style|tone)\b|"
    rf"\b(?:(?:this|the|my|our)\s+)?{_CRITIQUE_TEXT_QUALIFIER_NOUN}\s+"
    rf"(?:grammar|grammatical\s+errors?|spelling|punctuation|wording|"
    rf"capitalization|formatting|typos?|typographical\s+errors?|"
    rf"grammatical\s+correctness|grammatically\s+(?:correct|sound|accurate)|style|tone)\b"
)


def _critique_scope_has_instead(match: re.Match[str], segment: str) -> bool:
    modifiers = f"{match.groupdict().get('modifiers') or ''} " \
        f"{match.groupdict().get('post_modifiers') or ''}"
    return bool(
        re.search(r"\binstead\b(?!\s+of\b)", modifiers)
        or re.match(r"^\s*(?:,\s*)?instead\b(?!\s+of\b)", segment)
        or re.search(
            r"\binstead\b(?!\s+of\b)"
            r"(?:\s*,?\s*(?:please|if\s+possible|if\s+you\s+can))?\s*$",
            segment,
        )
    )


def _prepared_critique_clause(clause: str) -> str:
    text = _CRITIQUE_LEADING_DISCOURSE.sub("", clause.strip())
    text = _CRITIQUE_REQUEST_WRAPPER.sub("", text)
    text = re.sub(
        rf"^rather\s+than\s+(?={_NONCRITIQUE_EVENT_ACTION}\b)",
        "do not ",
        text,
    )
    gerunds = {
        "critiquing": "critique",
        "auditing": "audit",
        "challenging": "challenge",
        "reviewing": "review",
        "validating": "validate",
        "comparing": "compare",
        "stress-testing": "stress-test",
        "pressure-testing": "pressure-test",
    }
    for gerund, action in gerunds.items():
        text = re.sub(
            rf"\b(?P<negative>avoid(?:ing)?|without)\s+{gerund}\b",
            rf"\g<negative> {action}",
            text,
        )
    return text


def _critique_owned_segment(
    text: str,
    events: list[tuple[str, re.Match[str]]],
    event_index: int,
) -> str:
    match = events[event_index][1]
    end = events[event_index + 1][1].start() if event_index + 1 < len(events) else len(text)
    return text[match.end() : end].strip(" ,")


def _critique_scope_is_explicit(match: re.Match[str], segment: str) -> bool:
    modifiers = f"{match.groupdict().get('modifiers') or ''} " \
        f"{match.groupdict().get('post_modifiers') or ''}"
    return bool(
        re.search(r"\b(?:only|just|instead)\b", modifiers)
        or _CRITIQUE_EXPLICIT_SCOPE_PREFIX.search(segment)
        or _CRITIQUE_EXPLICIT_SCOPE_SUFFIX.search(segment)
    )


def _critique_action_is_additive(segment: str) -> bool:
    return bool(
        _CRITIQUE_ADDITIVE_SUFFIX.search(segment)
        or re.match(r"^\s*also\b", segment)
    )


def _critique_segment_is_text_only(segment: str) -> bool:
    atoms, mention_seen = _critique_positive_scope_atoms(segment)
    if not atoms:
        return mention_seen
    if any(_critique_atom_is_substantive(atom) for atom in atoms):
        return False
    textual_atoms = [_critique_atom_is_textual(atom) for atom in atoms]
    if any(textual_atoms) and any(not textual for textual in textual_atoms):
        return False
    return bool(
        mention_seen
        or any(textual_atoms)
    )


def _critique_segment_is_substantive(segment: str) -> bool:
    return _critique_segment_has_evaluation_criterion(segment)


def _critique_clause_events(clause: str) -> list[tuple[int, bool, bool]]:
    """Return ordered (position, polarity, mature) critique intent events."""

    text = _prepared_critique_clause(clause)
    if not text:
        return []
    actions: list[tuple[str, re.Match[str]]] = [
        ("critique", match) for match in _CRITIQUE_EVENT_FINDER.finditer(text)
    ]
    actions.extend(
        ("noncritique", match) for match in _NONCRITIQUE_EVENT_FINDER.finditer(text)
    )
    actions.sort(key=lambda item: (item[1].start(), item[1].end()))
    events: list[tuple[int, bool, bool]] = []
    replacement_implementation = _CRITIQUE_REPLACEMENT_IMPLEMENTATION.search(text)
    if replacement_implementation is not None:
        events.append((replacement_implementation.start(), False, False))
    for pattern in (*_CRITIQUE_SPECIAL_POSITIVE, *_CRITIQUE_PDF_SPECIAL_POSITIVE):
        special_match = pattern.search(text)
        if special_match is None:
            continue
        if _semantic_question_is_copyedit_only(text):
            break
        next_action_start = next(
            (
                action_match.start()
                for _, action_match in actions
                if action_match.start() >= special_match.end()
            ),
            len(text),
        )
        special_segment = text[special_match.start() : next_action_start].strip(" ,")
        if not _critique_segment_is_text_only(special_segment):
            events.append((special_match.start(), True, True))
        break

    for index, (kind, match) in enumerate(actions):
        segment = _critique_owned_segment(text, actions, index)
        connector = match.groupdict().get("connector")
        if connector is None and match.groupdict().get("comma"):
            connector = "comma"
        modifiers = match.groupdict().get("modifiers") or ""
        explicit = _critique_scope_is_explicit(match, segment)
        additive = _critique_action_is_additive(segment)
        additive_connector = connector in {"and also", "also"}
        instead = _critique_scope_has_instead(match, segment)
        if kind == "noncritique":
            action = match.group("action")
            if match.groupdict().get("negative"):
                continue
            if action in {"fix", "correct"} and not _CRITIQUE_TEXT_MARKER.search(segment):
                continue
            if additive_connector and not instead:
                continue
            if explicit:
                events.append((match.start(), False, False))
            elif connector is None and not additive:
                events.append((match.start(), False, False))
            continue

        text_only = _critique_segment_is_text_only(segment)
        negative = bool(match.groupdict().get("negative"))
        if negative:
            if not text_only:
                events.append((match.start(), False, False))
            continue
        if text_only:
            if additive_connector and not instead:
                continue
            if explicit or (connector is None and not additive):
                events.append((match.start(), False, False))
            continue

        action = match.group("action")
        strong = re.fullmatch(_CRITIQUE_STRONG_ACTION, action) is not None
        modifier_is_mature = re.search(
            r"\b(?:deeply|critically)\b", f"{modifiers} {segment}"
        ) is not None
        substantive = _critique_segment_is_substantive(segment)
        semantic_target = _DEEP_CRITIQUE_SEMANTIC_TARGET.search(segment) is not None
        later_noncritique = any(
            later_kind == "noncritique" for later_kind, _ in actions[index + 1 :]
        )
        if strong:
            prior_noncritique = any(
                earlier_kind == "noncritique" for earlier_kind, _ in actions[:index]
            )
            if (
                connector is not None
                and prior_noncritique
                and _CRITIQUE_AMBIGUOUS_PRONOUN_TARGET.fullmatch(segment)
                and not modifier_is_mature
            ):
                continue
            events.append((match.start(), True, True))
            continue
        if substantive or modifier_is_mature or (semantic_target and not later_noncritique):
            events.append((match.start(), True, True))

    events.sort(key=lambda event: event[0])
    return events


def _critique_clause_polarity(clause: str) -> bool | None:
    """Return the final effective critique event in one bounded clause."""

    events = _critique_clause_events(clause)
    return events[-1][1] if events else None


def _prompt_critique_state(prompt: str) -> tuple[bool | None, bool, str]:
    polarity: bool | None = None
    mature = False
    effective_clause = ""
    for clause in _directive_clauses(prompt):
        events = _critique_clause_events(clause)
        if events:
            _, polarity, mature = events[-1]
            effective_clause = clause
    return polarity, mature, effective_clause


def _prompt_has_affirmative_critique_intent(prompt: str) -> bool:
    """Return the last direct critique instruction after removing quoted text."""

    polarity, _, _ = _prompt_critique_state(prompt)
    return polarity is True


def _effective_affirmative_critique_clause(prompt: str) -> str:
    """Return the final clause that leaves critique intent affirmative."""

    polarity, _, effective_clause = _prompt_critique_state(prompt)
    return effective_clause if polarity is True else ""


_DEEP_CRITIQUE_SEMANTIC_TARGET = re.compile(
    r"\b(?:proposal|recommendation|analysis|plan|strategy|argument|decision|"
    r"operating\s+model|business\s+case|forecast|model|workflow|report|memo|"
    r"draft|document|policy|slide\s+deck|presentation|prd|concept|"
    r"operating\s+update|security\s+policy|"
    r"(?:repository|software|application|authentication)\s+"
    r"architecture|architecture|approach|(?:attached|uploaded|provided|"
    r"supplied)?\s*pdf(?:\s+file)?|[a-z0-9._-]+\.pdf)\b"
)
_DEEP_CRITIQUE_EVALUATION_CONTEXT = re.compile(
    r"\b(?:good|correct|right|sound|strong|ready|accurate|accuracy|validity|credible|defensible|"
    r"weak(?:ness|nesses)?|weak\s+assumptions?|assumptions?|flaws?|gaps?|"
    r"failure\s+modes?|methodology|reasoning|logic|evidence\s+quality|"
    r"decision\s+quality|what(?:'s|\s+is)\s+wrong|should\s+(?:we|i)\s+use)\b"
)
_NON_CRITIQUE_REVIEW_WORKFLOW = re.compile(
    r"\breview\s+(?:the\s+)?(?:feedback|comments?|notes?)\b"
    r"[^.!?;]{0,120}\bbefore\s+(?:applying|addressing|incorporating|making)\b|"
    r"\breview\s+(?:the\s+)?exact\s+diff\b|"
    r"\breview\s+(?:the\s+)?(?:workshop|meeting)\s+agenda\b|"
    r"\breview\b[^.!?;]{0,80}\breport\b[^.!?;]{0,80}"
    r"\bbefore\s+(?:(?:the|our|a)\s+)?meeting\b"
)
_TEXT_ONLY_REVIEW_CONTEXT = re.compile(
    r"\b(?:summari[sz]e|read|extract|transcribe|rewrite|translate|proofread|"
    r"grammar|spelling|punctuation|wording|capitalization|formatting|typos?|"
    r"grammatical\s+correctness|grammatically\s+(?:correct|sound|accurate)|"
    r"style\s+only|tone\s+only|"
    r"key\s+points?|main\s+points?|list\s+(?:the\s+)?(?:changes?|differences?)|"
    r"change\s+list)\b"
)
_EXPLICIT_TEXT_ONLY_LIMITATION = re.compile(
    r"\b(?:only|just)\b[^.!?;]{0,64}\b(?:summari[sz]e|extract|transcribe|"
    r"rewrite|translate|list|proofread|grammar|spelling|punctuation|wording|"
    r"capitalization|formatting|typos?|grammatical\s+correctness|"
    r"grammatically\s+(?:correct|sound|accurate)|style|tone)\b|"
    r"\b(?:summari[sz]e|extract|transcribe|rewrite|translate|list|proofread|"
    r"grammar|spelling|punctuation|wording|capitalization|formatting|typos?|"
    r"grammatical\s+correctness|grammatically\s+(?:correct|sound|accurate)|style|tone)\b"
    r"[^.!?;]{0,32}\b(?:only|just)\b"
)
_DEEP_CRITIQUE_SEMANTIC_QUESTION = re.compile(
    r"^(?:what\s+do\s+you\s+think\s+(?:of|about)|"
    r"is\s+(?:this|my|our|the)\s+[^.!?;]{0,100}\b"
    r"(?:good|correct|right|sound|strong|ready)\b|"
    r"should\s+(?:we|i)\s+use\s+this|(?:please\s+)?be\s+critical\b)"
)


def _prompt_has_mature_deep_critique_intent(prompt: str) -> bool:
    """Require evaluative critique intent, not ordinary document handling."""

    text = _normalized_unquoted_prompt(prompt)
    polarity, mature, effective_clause = _prompt_critique_state(prompt)
    if (
        not text
        or polarity is not True
        or not mature
        or (
            _NON_CRITIQUE_REVIEW_WORKFLOW.search(text)
            and not _DEEP_CRITIQUE_EVALUATION_CONTEXT.search(text)
        )
    ):
        return False
    return bool(effective_clause)


def _prompt_is_linguistic_critique_mention(prompt: str) -> bool:
    """Return whether the final active directive critiques a linguistic mention."""

    if _prompt_has_affirmative_critique_intent(prompt):
        return False
    final_is_linguistic_critique = False
    for clause in _directive_clauses(prompt):
        text = _prepared_critique_clause(clause)
        _, mention_seen = _strip_critique_linguistic_mentions(text)
        events: list[tuple[int, bool]] = []
        if mention_seen:
            events.extend(
                (match.start(), True)
                for match in _CRITIQUE_EVENT_FINDER.finditer(text)
                if not match.groupdict().get("negative")
            )
        events.extend(
            (match.start(), False)
            for match in _DELIVERABLE_EVENT_FINDER.finditer(text)
            if not match.groupdict().get("negative")
        )
        if events:
            events.sort(key=lambda event: event[0])
            final_is_linguistic_critique = events[-1][1]
    return final_is_linguistic_critique


_SOURCE_SPECIAL_POSITIVE = (
    re.compile(r"^are\s+these\s+sources\s+strong\s+enough\b"),
    re.compile(
        r"^(?:are|is)\s+(?:these|the)\s+(?:sources?|citations?)\b"
        r"[^.!?;]{0,100}\b(?:authentic|credible|valid|reliable|sufficient)\b"
    ),
    re.compile(
        r"^(?:do|does)\s+(?:these|the)\s+(?:sources|citations)\b"
        r"[^.!?;]{0,140}\bsupport\b"
    ),
)
_SOURCE_EVENT_FINDER = re.compile(
    rf"{_CRITIQUE_DIRECTIVE_INTRO}{_CRITIQUE_DIRECTIVE_MODIFIERS}"
    rf"{_CRITIQUE_NEGATION}{_CRITIQUE_POST_NEGATION_MODIFIERS}"
    rf"(?P<action>{_SOURCE_EVALUATION_ACTION})(?=\b|$)"
)


def _prompt_has_affirmative_source_evaluation_intent(prompt: str) -> bool:
    """Return the last direct source-evaluation instruction."""

    polarity: bool | None = None
    for clause in _directive_clauses(prompt):
        text = _prepared_critique_clause(clause)
        actions: list[tuple[str, re.Match[str]]] = [
            ("source", match) for match in _SOURCE_EVENT_FINDER.finditer(text)
        ]
        actions.extend(
            ("non-source", match)
            for match in _NONCRITIQUE_EVENT_FINDER.finditer(text)
        )
        actions.sort(key=lambda item: (item[1].start(), item[1].end()))
        events: list[tuple[int, bool]] = []
        for pattern in _SOURCE_SPECIAL_POSITIVE:
            special_match = pattern.search(text)
            if special_match is not None:
                events.append((special_match.start(), True))
                break
        for action_index, (kind, match) in enumerate(actions):
            segment = _critique_owned_segment(text, actions, action_index)
            connector = match.groupdict().get("connector")
            if connector is None and match.groupdict().get("comma"):
                connector = "comma"
            explicit = _critique_scope_is_explicit(match, segment)
            additive = _critique_action_is_additive(segment)
            if kind == "source":
                events.append(
                    (
                        match.start(),
                        not bool(match.groupdict().get("negative"))
                        and not _critique_segment_is_text_only(
                            f"{match.group('action')} {segment}"
                        ),
                    )
                )
                continue
            if match.groupdict().get("negative"):
                continue
            if explicit or (connector is None and not additive):
                events.append((match.start(), False))
        if events:
            events.sort(key=lambda event: event[0])
            polarity = events[-1][1]
    return polarity is True


def _normalized_unquoted_prompt(prompt: str) -> str:
    return re.sub(
        r"\s+", " ", _prompt_without_quoted_text(prompt).lower().replace("’", "'")
    ).strip()


_SECURITY_TECHNICAL_SYSTEM = (
    r"(?:application|app|service|system|architecture|repository|repo|codebase|"
    r"api|frontend|backend|database|schema|data\s+flow|trust\s+boundary|"
    r"tool|agent|react|browser|supabase|neon(?:\s+postgres)?|postgres(?:ql)?)"
)
_SECURITY_TECHNICAL_CONTEXT = re.compile(
    r"\b(?:auth(?:entication|orization)?|access\s+control|permissions?|privileges?|"
    r"secrets?|credentials?|api\s+keys?|tokens?|jwts?|login|sign[- ]in|sessions?|cookies?|rls|"
    r"row[- ]level\s+security|grants?|default\s+privileges|bypassrls|"
    r"security[- ](?:invoker|definer)|vulnerabilit(?:y|ies)|cve|exploit(?:ability)?|"
    r"attack\s+(?:paths?|surface)|threat\s+model|sql\s+injection|xss|csrf|ssrf|idor|"
    r"csp|cross[- ]site\s+scripting|privilege\s+escalation|data\s+exposure|"
    r"security\s+(?:configuration|settings?|posture|alerts?|advisors?|findings?|issues?|"
    r"problems?|warnings?|flaws?|boundary|regression|scan|audit|review|policy|baseline|"
    r"hardening|checklist|best\s+practices))\b|"
    rf"\b{_SECURITY_TECHNICAL_SYSTEM}\s+security\b|"
    rf"\bsecurity\s+(?:of|for|in)\s+(?:(?:this|the|our|an?)\s+)?"
    rf"{_SECURITY_TECHNICAL_SYSTEM}\b|"
    rf"\bsecurity\b[^.!?;]{{0,100}}\b{_SECURITY_TECHNICAL_SYSTEM}\b|"
    rf"\b{_SECURITY_TECHNICAL_SYSTEM}\b[^.!?;]{{0,80}}\bfor\s+security\b"
)
_NONTECHNICAL_SECURITY_CONTEXT = re.compile(
    r"\b(?:social\s+security|job\s+security|security\s+of\s+(?:this|the|an?)\s+"
    r"(?:investment|bond|building)|permissions?\s+in\s+(?:this|the|an?)\s+"
    r"(?:hr|human\s+resources?)\s+process|security\s+policy\b[^.!?;]{0,80}"
    r"\bbuilding\s+access)\b"
)
_AMBIGUOUS_SECURITY_TERM_CONTEXT = re.compile(
    r"\b(?:permissions?|privileges?|credentials?|tokens?|login|sign[- ]in|"
    r"sessions?|cookies?|grants?)\b"
)
_STRONG_SECURITY_TERM_CONTEXT = re.compile(
    r"\b(?:security|auth(?:entication|orization)?|access\s+control|secrets?|"
    r"api\s+keys?|jwts?|rls|row[- ]level\s+security|default\s+privileges|"
    r"bypassrls|security[- ](?:invoker|definer)|vulnerabilit(?:y|ies)|cve|"
    r"exploit(?:ability)?|attack\s+(?:paths?|surface)|threat\s+model|"
    r"sql\s+injection|xss|csrf|ssrf|idor|csp|cross[- ]site\s+scripting|"
    r"privilege\s+escalation|data\s+exposure)\b"
)


def _technical_security_context_is_bounded(text: str) -> bool:
    if not _has_security_context(text) or _NONTECHNICAL_SECURITY_CONTEXT.search(text):
        return False
    if not _AMBIGUOUS_SECURITY_TERM_CONTEXT.search(text):
        return True
    return bool(
        _STRONG_SECURITY_TERM_CONTEXT.search(text)
        or re.search(rf"\b{_SECURITY_TECHNICAL_SYSTEM}\b", text)
    )
_SECURITY_FINDING_CONTEXT = re.compile(
    r"\b(?:security\s+(?:alerts?|advisors?|findings?|issues?|warnings?)|findings?|vulnerabilit(?:y|ies)|"
    r"cve|exploit|attack\s+paths?|misconfiguration|exposure|bypass|regression)\b"
)
_SECURITY_DIFF_CONTEXT = re.compile(
    r"\b(?:diff|pull\s+request|pr\s+#?\d*|commit|branch|working\s+tree|"
    r"change\s*set|changed\s+files?|candidate\s+head)\b"
)
_SECURITY_SCAN_SCOPE = re.compile(
    r"\b(?:repository|repo|codebase|project|application|app|service|api|backend|"
    r"frontend|database|package|module|directory|folder|scoped\s+path|source\s+code)\b"
)
_SECURITY_SYSTEM_CONTEXT = re.compile(
    r"\b(?:application|app|service|system|architecture|repository|repo|codebase|"
    r"api|frontend|backend|data\s+flow|trust\s+boundary|tool|agent)\b"
)
_SECURITY_TRACKER_CONTEXT = re.compile(
    r"\b(?:github|linear|atlassian|jira|issue|ticket|tracker|backlog)\b"
)
_SUPABASE_DATABASE_CONTEXT = re.compile(
    r"\bsupabase\b[^.!?;]{0,140}\b(?:project|postgres|database|sql|schema|migration|"
    r"auth(?:entication|orization)?|login|sign[- ]in|sessions?|jwts?|users?|storage|edge\s+function|function|rls|row[- ]level\s+security|policy|grant|"
    r"role|service[_ -]role|anon|authenticated|security\s+(?:alerts?|advisors?|findings?|scan|review|audit))\b|"
    r"\b(?:rls|row[- ]level\s+security|migration|database|postgres|auth(?:entication|orization)?|login|sign[- ]in|sessions?|jwts?|users?|storage|"
    r"security\s+(?:alerts?|advisors?|findings?|scan|review|audit))\b[^.!?;]{0,140}\bsupabase\b"
)
_NEON_DATABASE_CONTEXT = re.compile(
    r"\bneon\s+(?:project|postgres|database|data\s+api|auth(?:entication|orization)?|login|sign[- ]in|sessions?|jwts?|users?|security\s+(?:alerts?|advisors?|findings?)|migration|branch(?:ing)?|rls|"
    r"row[- ]level\s+security|role|grant|schema|sql|egress)\b|"
    r"\b(?:neondb_owner|neon_auth|neon\s+data\s+api)\b|"
    r"\b(?:postgres|database|migration|branch(?:ing)?|rls|role|grant|schema|sql|egress|"
    r"auth(?:entication|orization)?|login|sign[- ]in|sessions?|jwts?|users?)"
    r"\b[^.!?;]{0,100}\bneon\b"
)
_POSTGRES_DATABASE_CONTEXT = re.compile(
    r"\b(?:postgres|postgresql)\b[^.!?;]{0,120}\b(?:database|sql|schema|migration|"
    r"role|owner|grant|privilege|rls|row[- ]level\s+security|view|function|trigger|"
    r"extension|connection|query|security\s+(?:scan|review|audit))\b|"
    r"\b(?:rls|row[- ]level\s+security|bypassrls|default\s+privileges|"
    r"security[- ](?:invoker|definer))\b[^.!?;]{0,120}\b(?:postgres|postgresql)\b"
)
_FRONTEND_SECURITY_CONTEXT = re.compile(
    r"\b(?:frontend|client[- ]side|browser|react|next\.?js|vue|svelte|web\s+app)\b"
    r"[^.!?;]{0,180}\b(?:auth|authorization|token|session|cookie|csp|xss|csrf|"
    r"secret|credential|permission|access\s+control|security\s+boundary)\b|"
    r"\b(?:csp|xss|csrf|secure\s+cookie|http\s*only|same\s*site|client[- ]side\s+secret)"
    r"\b[^.!?;]{0,180}\b(?:frontend|browser|react|next\.?js|web\s+app)\b"
)
_SECURITY_ACTION_NEGATION = re.compile(
    r"\b(?:do\s+not|don'?t|dont|never|avoid(?:ing)?|without|not|"
    r"skip(?:ping)?|omit(?:ting)?|exclude|excluding|except(?:\s+|-)for)\s+"
    r"(?:running\s+|performing\s+|conducting\s+|creating\s+|writing\s+|"
    r"proposing\s+|applying\s+|implementing\s+|fixing\s+|)"
    r"(?:a\s+|the\s+|any\s+)?(?:security\s+)?(?:scan|audit|review|triage|validation|"
    r"fix|remediation|threat\s+model|policy|hardening|writeup|report|tracking|checklist|"
    r"finding\s+discovery|attack\s+paths?)\b"
)
_SECURITY_SUBJECT_ONLY = re.compile(
    r"\b(?:is|means|refers\s+to|was\s+mentioned|is\s+called|is\s+an?\s+example)\b"
)

_SECURITY_DIRECT_ACTION_PREFIX = (
    r"(?:^|\b(?:and(?:\s+then)?|then|also|instead)\s+|,\s*)"
    r"(?:(?:first\s*,?|please|now|carefully|deeply)\s+)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"(?:i|we)\s+(?:want|need|would\s+like|prefer)\s+(?:you\s+)?to\s+)?"
)
_SECURITY_NEGATOR = (
    r"(?:do\s+not|don'?t|dont|never|avoid(?:ing)?|without|not|no|"
    r"skip(?:ping)?|omit(?:ting)?|exclude|excluding|except(?:\s+|-)for)"
)


def _security_clause_action_polarity(
    clause: str,
    action_pattern: str,
    object_pattern: str,
) -> bool | None:
    """Return the last action/object polarity expressed in one directive clause."""

    text = _LEADING_DISCOURSE.sub("", clause.strip())
    if not text:
        return None
    object_matches = list(re.finditer(rf"\b(?:{object_pattern})\b", text))
    if not object_matches:
        return None

    events: list[tuple[int, bool]] = []
    object_qualifier = r"(?:(?:deep|standard|exhaustive|multi[- ]pass|repeated)\s+)?"
    negative_patterns = (
        rf"\b{_SECURITY_NEGATOR}\s+(?:(?:a|an|the|any)\s+)?"
        rf"{object_qualifier}(?:(?:{action_pattern})\b[^.!?;]{{0,100}}\b)?"
        rf"{object_qualifier}(?:{object_pattern})\b",
        rf"\b{_SECURITY_NEGATOR}\s+(?:{action_pattern})\b[^.!?;]{{0,140}}"
        rf"\b(?:{object_pattern})\b",
        rf"\b(?:{action_pattern})\b[^.!?;]{{0,140}}\b{_SECURITY_NEGATOR}\s+"
        rf"(?:(?:a|an|the|any)\s+)?{object_qualifier}(?:{object_pattern})\b",
        rf"\b(?:{object_pattern})\b[^.!?;]{{0,60}}"
        rf"\b(?:is|are|was|were)?\s*(?:not\s+(?:requested|needed|required)|excluded|out\s+of\s+scope)\b",
    )
    for pattern in negative_patterns:
        events.extend((match.start(), False) for match in re.finditer(pattern, text))

    direct_pattern = re.compile(
        rf"{_SECURITY_DIRECT_ACTION_PREFIX}(?P<action>{action_pattern})\b(?!\s+not\b)"
    )
    for action_match in direct_pattern.finditer(text):
        action_start = action_match.start("action")
        action_end = action_match.end("action")
        if any(
            object_match.start() <= action_end + 240
            and object_match.end() >= max(0, action_start - 180)
            for object_match in object_matches
        ):
            events.append((action_start, True))

    if not events:
        return None
    events.sort(key=lambda item: item[0])
    return events[-1][1]


def _security_action_requested(
    prompt: str,
    action_pattern: str,
    *,
    object_pattern: str = "",
    telegraphic_pattern: str = "",
) -> bool:
    """Require an affirmative action bound to its security object."""

    text = _normalized_unquoted_prompt(prompt)
    if not text:
        return False
    if object_pattern:
        polarity: bool | None = None
        for clause in _directive_clauses(prompt):
            clause_polarity = _security_clause_action_polarity(
                clause, action_pattern, object_pattern
            )
            if clause_polarity is not None:
                polarity = clause_polarity
        if polarity is not None:
            return polarity
        if _SECURITY_ACTION_NEGATION.search(text):
            return False
    elif _SECURITY_ACTION_NEGATION.search(text):
        return False
    elif _prompt_has_affirmative_direct_action(prompt, action_pattern):
        return True
    if telegraphic_pattern and re.match(telegraphic_pattern, text):
        return _SECURITY_SUBJECT_ONLY.search(text) is None
    return False


def _has_security_context(prompt: str) -> bool:
    return _SECURITY_TECHNICAL_CONTEXT.search(_normalized_unquoted_prompt(prompt)) is not None


_PROVIDER_TERMS = {
    "supabase": r"supabase",
    "neon": r"neon(?:\s+(?:postgres|postgresql|database))?",
    "postgres": r"(?:postgres|postgresql)",
    "frontend": r"(?:frontend|client[- ]side|browser|react|next\.?js|vue|svelte|web\s+app)",
}

_PROVIDER_DIRECTION_TERM = (
    r"(?:supabase|neon(?:\s+(?:postgres|postgresql|database))?|"
    r"postgres(?:ql)?(?:\s+database)?)"
)


def _provider_migration_target(prompt: str) -> str | None:
    """Return the explicit destination in a provider replacement or contrast."""

    text = _normalized_unquoted_prompt(prompt)
    patterns = (
        rf"\b(?:migrate|move|switch|transition)\b[^.!?;]{{0,100}}"
        rf"\bfrom\s+(?P<source>{_PROVIDER_DIRECTION_TERM})\s+"
        rf"to\s+(?P<target>{_PROVIDER_DIRECTION_TERM})\b",
        rf"\breplace\s+(?P<source>{_PROVIDER_DIRECTION_TERM})\s+"
        rf"with\s+(?P<target>{_PROVIDER_DIRECTION_TERM})\b",
        rf"\b(?:use|select|choose|implement|apply|configure)\s+"
        rf"(?P<target>{_PROVIDER_DIRECTION_TERM})\b[^.!?;]{{0,100}}"
        rf"\b(?:rather\s+than|instead\s+of)\s+"
        rf"(?P<source>{_PROVIDER_DIRECTION_TERM})\b",
    )
    match = next((match for pattern in patterns if (match := re.search(pattern, text))), None)
    if not match:
        return None
    target = match.group("target")
    if target.startswith("supabase"):
        return "supabase"
    if target.startswith("neon"):
        return "neon"
    return "postgres"


def _postgres_is_underlying_provider_engine(prompt: str, provider: str) -> bool:
    """Recognize PostgreSQL as the named provider's engine, not a second DB."""

    text = _normalized_unquoted_prompt(prompt)
    provider_term = r"supabase" if provider == "supabase" else r"neon"
    patterns = (
        rf"\b(?:postgres|postgresql)\b[^.!?;]{{0,120}}\b(?:for|in|of)\s+"
        rf"(?:(?:our|the|this|a)\s+)?{provider_term}\s+(?:project|database)\b",
        rf"\b{provider_term}\s+(?:project|database)\b[^.!?;]{{0,120}}"
        rf"\b(?:using|on|backed\s+by|powered\s+by|which\s+uses)\s+"
        rf"(?:postgres|postgresql)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _surface_explicitly_negated(prompt: str, surface: str) -> bool:
    term = _PROVIDER_TERMS[surface]
    text = _normalized_unquoted_prompt(prompt)
    patterns = (
        rf"\b(?:not|without|avoid(?:ing)?|exclude|excluding)\s+"
        rf"(?:(?:using|use|targeting|target)\s+)?(?:{term})\b",
        rf"\b(?:do\s+not|don'?t|dont|never)\s+"
        rf"(?:use|select|target|route\s+to|apply\s+to|scan|review)\s+"
        rf"(?:(?:the|this)\s+)?(?:{term})\b",
        rf"\b(?:{term})\b[^.!?;]{{0,40}}\b(?:is\s+)?"
        rf"(?:not\s+(?:used|selected|targeted|included)|excluded|out\s+of\s+scope)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


_TRACKER_TERMS = {
    "github": r"github",
    "linear": r"linear",
    "jira": r"(?:jira|atlassian)",
}


def _tracker_explicitly_negated(prompt: str, tracker: str) -> bool:
    term = _TRACKER_TERMS[tracker]
    text = _normalized_unquoted_prompt(prompt)
    patterns = (
        rf"\b(?:not|without|avoid(?:ing)?|exclude|excluding)\s+"
        rf"(?:(?:using|use|targeting|target)\s+)?(?:{term})\b",
        rf"\b(?:do\s+not|don'?t|dont|never)\s+"
        rf"(?:use|select|target|route\s+to|publish\s+to|sync\s+to|track\s+in)\s+"
        rf"(?:{term})\b",
        rf"\b(?:{term})\b[^.!?;]{{0,40}}\b(?:is\s+)?"
        rf"(?:not\s+(?:used|selected|targeted|included)|excluded|out\s+of\s+scope)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _mentioned_tracker_destinations(prompt: str) -> list[str]:
    text = _normalized_unquoted_prompt(prompt)
    return [
        tracker
        for tracker, term in _TRACKER_TERMS.items()
        if re.search(rf"\b(?:{term})\b", text)
    ]


def _tracker_selection_target(prompt: str) -> str | None:
    text = _normalized_unquoted_prompt(prompt)
    match = re.search(
        r"\b(?P<target>github|linear|jira|atlassian)\b[^.!?;]{0,60}"
        r"\b(?:rather\s+than|instead\s+of)\s+"
        r"(?P<source>github|linear|jira|atlassian)\b",
        text,
    )
    if not match:
        return None
    target = match.group("target")
    return "jira" if target in {"jira", "atlassian"} else target


def _affirmative_tracker_destinations(prompt: str) -> list[str]:
    selection_target = _tracker_selection_target(prompt)
    if selection_target:
        return [selection_target]
    return [
        tracker
        for tracker in _mentioned_tracker_destinations(prompt)
        if not _tracker_explicitly_negated(prompt, tracker)
    ]


def _has_supabase_database_context(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        not _surface_explicitly_negated(text, "supabase")
        and _SUPABASE_DATABASE_CONTEXT.search(text)
    )


def _has_neon_database_context(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        not _surface_explicitly_negated(text, "neon")
        and _NEON_DATABASE_CONTEXT.search(text)
    )


def _has_postgres_database_context(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    provider_neutral_text = re.sub(
        r"\b(?:neon|supabase)\s+(?:postgres|postgresql)\b", " ", text
    )
    return bool(
        not _surface_explicitly_negated(text, "postgres")
        and _POSTGRES_DATABASE_CONTEXT.search(provider_neutral_text)
    )


def _has_provider_database_context(prompt: str) -> bool:
    return bool(
        _has_supabase_database_context(prompt)
        or _has_neon_database_context(prompt)
        or _has_postgres_database_context(prompt)
    )


_FRONTEND_SURFACE_CONTEXT = re.compile(
    r"\b(?:frontend|client[- ]side|browser|react|next\.?js|vue|svelte|web\s+app)\b"
)


def _affirmative_security_surfaces(prompt: str) -> list[str]:
    """Return independently requested provider/frontend security surfaces."""

    text = _normalized_unquoted_prompt(prompt)
    surfaces: list[str] = []
    explicit_target = _provider_migration_target(text)
    if explicit_target == "supabase" or (
        explicit_target is None and _has_supabase_database_context(text)
    ):
        surfaces.append("supabase")
    if explicit_target == "neon" or (
        explicit_target is None and _has_neon_database_context(text)
    ):
        surfaces.append("neon")
    standalone_postgres_text = re.sub(
        r"\b(?:neon|supabase)\s+(?:postgres|postgresql)\b", " ", text
    )
    provider_engine_context = any(
        _postgres_is_underlying_provider_engine(text, provider)
        for provider in ("supabase", "neon")
    )
    if explicit_target == "postgres" or (
        explicit_target is None
        and not provider_engine_context
        and _has_postgres_database_context(standalone_postgres_text)
    ):
        surfaces.append("postgres")
    if (
        _FRONTEND_SURFACE_CONTEXT.search(text)
        and not _surface_explicitly_negated(text, "frontend")
    ):
        surfaces.append("frontend")
    return list(dict.fromkeys(surfaces))


def _security_surfaces_requiring_split(prompt: str) -> list[str]:
    text = _normalized_unquoted_prompt(prompt)
    if not _has_security_context(text):
        return []
    surfaces = _affirmative_security_surfaces(text)
    return surfaces if len(surfaces) > 1 else []


def _tracker_destinations_requiring_split(prompt: str) -> list[str]:
    destinations = _affirmative_tracker_destinations(prompt)
    if len(destinations) <= 1:
        return []
    return (
        destinations
        if _prompt_has_finding_phase_intent(prompt, "tracking")
        else []
    )


_SECURITY_DIFF_OBJECT = (
    r"(?:security(?:\s+(?:scan|review|audit|regression))?|"
    r"(?:auth(?:entication|orization)?|permission|access[- ]control)\s+security|"
    r"vulnerabilit(?:y|ies))"
)
_SECURITY_SCAN_OBJECT = (
    r"(?:security\s+(?:scan|review|audit)|(?:scan|review|audit)\s+for\s+"
    r"(?:security|vulnerabilit(?:y|ies))|security\s+(?:issues?|problems?)|"
    r"vulnerabilit(?:y|ies))"
)
_SECURITY_FINDING_OBJECT = (
    r"(?:security\s+(?:alerts?|advisors?|findings?|issues?|warnings?)|"
    r"vulnerabilit(?:y|ies)|findings?|cve|exploit|misconfiguration|exposure)"
)
_SECURITY_ATTACK_PATH_OBJECT = r"(?:attack[- ]paths?|reachability|exploitability)"
_SECURITY_DISCOVERY_OBJECT = (
    r"(?:finding\s+discovery|vulnerabilit(?:y|ies)|security\s+(?:findings?|issues?))"
)
_SECURITY_FIX_OBJECT = (
    r"(?:security\s+(?:findings?|issues?)|vulnerabilit(?:y|ies)|"
    r"finding|remediation|security\s+fix)"
)
_SECURITY_HARDENING_OBJECT = r"(?:security\s+hardening|hardening\s+(?:proposal|recommendations?))"
_SECURITY_WRITEUP_OBJECT = (
    r"(?:vulnerability\s+(?:writeup|report)|security\s+finding\s+(?:writeup|report)|"
    r"finding\s+report|security\s+writeup)"
)
_SECURITY_TRACKING_OBJECT = (
    r"(?:security\s+(?:findings?|tickets?|issues?)|vulnerabilit(?:y|ies)|"
    r"finding\s+(?:tickets?|issues?))"
)
_SECURITY_POLICY_OBJECT = r"(?:security\s+(?:policy|baseline|ruleset))"
_SECURITY_THREAT_MODEL_OBJECT = r"(?:threat[- ]model|\$threat-model|codex-security:threat-model)"
_SECURITY_OWNERSHIP_OBJECT = (
    r"(?:security\s+ownership|security[- ]sensitive\s+(?:files?|code)|"
    r"security\s+bus\s+factor|risk\s+hotspots?)"
)
_SECURITY_CHECKLIST_OBJECT = r"(?:(?:defensive\s+)?security\s+checklist)"
_SECURITY_BEST_PRACTICES_OBJECT = r"(?:security\s+best\s+practices)"
_SECURITY_IMPLEMENTATION_OBJECT = (
    r"(?:security|auth(?:entication|orization)?|access\s+control|permissions?|"
    r"privileges?|rls|row[- ]level\s+security|secrets?|credentials?|tokens?|"
    r"jwts?|login|sign[- ]in|users?|sessions?|cookies?|csp|xss|csrf|ssrf|idor|"
    r"vulnerabilit(?:y|ies))"
)


def _has_security_diff_context(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    text_without_neon_branch = re.sub(
        r"\bneon\b[^.!?;]{0,100}\bbranch(?:\s+[a-z0-9_-]+)?\b",
        " ",
        text,
    )
    if _SECURITY_DIFF_CONTEXT.search(text_without_neon_branch):
        return True
    return bool(
        re.search(
            r"\b(?:review|audit|inspect|scan|check)\s+"
            r"(?:(?:this|the|a)\s+)?patch\b",
            text,
        )
        or re.search(r"\bpatch\s+(?:diff|file|changes?|change\s*set)\b", text)
    )


def _prompt_has_security_diff_review_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        _has_security_diff_context(text)
        and (
            _has_security_context(text)
            or re.search(r"\b(?:for|about|regarding)\s+security\b", text)
        )
        and not _prompt_has_affirmative_implementation(text)
        and _security_action_requested(
            prompt,
            r"(?:run|perform|conduct|scan|audit|review|inspect|check|do)",
            object_pattern=_SECURITY_DIFF_OBJECT,
            telegraphic_pattern=r"^(?:security\s+)?(?:diff\s+scan|review\s+(?:the\s+)?(?:pr|diff|patch))\b",
        )
    )


_DEEP_SECURITY_SCAN = re.compile(
    r"\b(?:deep|exhaustive|multi[- ]pass|repeated)\s+"
    r"(?:(?:supabase|neon|postgres(?:ql)?|database|provider[- ]neutral|"
    r"frontend|react|repository|project|application|app)\s+){0,5}"
    r"security\s+(?:scan|review|audit)\b"
)


def _prompt_has_security_scan_intent(prompt: str, *, deep: bool) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    is_deep = _DEEP_SECURITY_SCAN.search(text) is not None
    if deep != is_deep or _has_security_diff_context(text):
        return False
    telegraphic_scope = bool(
        re.match(
            r"^(?:(?:please\s+)?(?:run|perform|conduct|do)\s+(?:a\s+)?)?"
            r"(?:(?:deep|exhaustive|multi[- ]pass|standard|repository)\s+)?"
            r"(?:(?:supabase|neon|postgres(?:ql)?|database|frontend|react)\s+){0,4}"
            r"security\s+(?:scan|review|audit)\b",
            text,
        )
    )
    return bool(
        (_SECURITY_SCAN_SCOPE.search(text) or telegraphic_scope)
        and _has_security_context(text)
        and not _prompt_has_affirmative_implementation(text)
        and _security_action_requested(
            prompt,
            r"(?:run|perform|conduct|scan|audit|review|do)",
            object_pattern=_SECURITY_SCAN_OBJECT,
            telegraphic_pattern=(
                r"^(?:deep|exhaustive|multi[- ]pass)\s+"
                r"(?:(?:supabase|neon|postgres(?:ql)?|database|frontend|react)\s+){0,4}"
                r"security\s+(?:scan|review|audit)\b"
                if deep
                else r"^(?:standard\s+|repository\s+)?security\s+(?:scan|review|audit)\b"
            ),
        )
    )


def _prompt_defers_or_explains_security_fix(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        re.search(
            r"\b(?:fix|remediate|patch|resolve|repair|mitigate)\b[^.!?;]{0,80}"
            r"\b(?:later|in\s+the\s+future|subsequently|afterwards)\b",
            text,
        )
        or re.search(
            r"\b(?:only|just)\s+(?:explain|summari[sz]e|document|describe)\s+now\b",
            text,
        )
        or re.match(
            r"^(?:please\s+)?explain\b[^.!?;]{0,100}\bhow\s+to\s+"
            r"(?:fix|remediate|patch)\b",
            text,
        )
    )


def _prompt_has_finding_phase_intent(prompt: str, phase: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    if phase == "hardening":
        context_present = _has_security_context(text)
    else:
        context_present = bool(
            _SECURITY_FINDING_CONTEXT.search(text) is not None
            and _has_security_context(text)
        )
    if not context_present:
        return False
    phase_patterns = {
        "triage": (r"(?:triage|classify|prioritize|assess|review|check|investigate|analy[sz]e|inspect)", r"^(?:security\s+)?(?:alert|finding)\s+triage\b"),
        "validation": (r"(?:validate|verify|reproduce|confirm|disprove)", r"^(?:finding|vulnerability)\s+validation\b"),
        "attack_path": (r"(?:analy[sz]e|map|trace|assess|test)", r"^(?:attack[- ]paths?|exploitability)\s+analysis\b"),
        "discovery": (r"(?:discover|find|identify|search|hunt)", r"^(?:finding|vulnerability)\s+discovery\b"),
        "fix": (r"(?:fix|remediate|patch|resolve|repair|mitigate|implement|apply)", r"^(?:(?:implement|apply)\s+(?:the\s+)?(?:fix|remediation)|(?:fix|remediate)\s+(?:the\s+)?(?:finding|vulnerability))\b"),
        "hardening": (r"(?:propose|recommend|design|prepare)", r"^(?:security\s+)?hardening\s+(?:proposal|recommendations?)\b"),
        "writeup": (r"(?:write|document|draft|prepare|report)", r"^(?:vulnerability|security\s+finding)\s+(?:writeup|report)\b"),
        "tracking": (r"(?:track|file|create|publish|sync|update)", r"^(?:track|file)\s+(?:the\s+)?(?:security\s+)?findings?\b"),
    }
    action_pattern, telegraphic = phase_patterns[phase]
    if phase == "triage" and re.search(
        r"\b(?:attack\s+paths?|reachability|exploitability)\b", text
    ):
        return False
    if phase == "attack_path" and not re.search(r"\b(?:attack\s+paths?|reachability|exploitability)\b", text):
        return False
    if phase == "tracking" and not _SECURITY_TRACKER_CONTEXT.search(text):
        return False
    if phase == "hardening" and _prompt_has_affirmative_implementation(text):
        return False
    if phase == "fix" and _prompt_defers_or_explains_security_fix(prompt):
        return False
    phase_objects = {
        "triage": _SECURITY_FINDING_OBJECT,
        "validation": _SECURITY_FINDING_OBJECT,
        "attack_path": _SECURITY_ATTACK_PATH_OBJECT,
        "discovery": _SECURITY_DISCOVERY_OBJECT,
        "fix": _SECURITY_FIX_OBJECT,
        "hardening": _SECURITY_HARDENING_OBJECT,
        "writeup": _SECURITY_WRITEUP_OBJECT,
        "tracking": _SECURITY_TRACKING_OBJECT,
    }
    return _security_action_requested(
        prompt,
        action_pattern,
        object_pattern=phase_objects[phase],
        telegraphic_pattern=telegraphic,
    )


def _security_phases_requiring_split(prompt: str) -> list[str]:
    """Return every independently requested Codex Security workflow phase."""

    text = _normalized_unquoted_prompt(prompt)
    phases: list[str] = []

    def requested(action_pattern: str, object_pattern: str) -> bool:
        return _security_action_requested(
            prompt,
            action_pattern,
            object_pattern=object_pattern,
        )

    attack_path_requested = requested(
        r"(?:analy[sz]e|map|trace|assess|test)",
        _SECURITY_ATTACK_PATH_OBJECT,
    )
    if attack_path_requested:
        phases.append("attack_path")

    scan_requested = requested(
        r"(?:run|perform|conduct|scan|audit|review|do)",
        _SECURITY_SCAN_OBJECT,
    )
    if scan_requested and (_SECURITY_SCAN_SCOPE.search(text) or "security scan" in text):
        if _DEEP_SECURITY_SCAN.search(text):
            phases.append("deep_scan")
        if not _DEEP_SECURITY_SCAN.search(text) or re.search(
            r"\bstandard\s+security\s+(?:scan|review|audit)\b", text
        ):
            phases.append("standard_scan")

    if requested(
        r"(?:define|draft|create|write|update|revise|establish)",
        _SECURITY_POLICY_OBJECT,
    ):
        phases.append("policy")
    if requested(
        r"(?:discover|find|identify|search|hunt)",
        _SECURITY_DISCOVERY_OBJECT,
    ):
        phases.append("discovery")

    fix_requested = requested(
        r"(?:fix|remediate|patch|resolve|repair|mitigate|implement|apply)",
        _SECURITY_FIX_OBJECT,
    )
    if not fix_requested and _SECURITY_FINDING_CONTEXT.search(text):
        fix_requested = bool(
            re.search(
                r"\b(?:and|then)\s+(?:fix|remediate|patch|resolve|repair|mitigate)"
                r"(?:\s+(?:them|it|these|those|the\s+findings?))?\b",
                text,
            )
        )
    if fix_requested and not _prompt_defers_or_explains_security_fix(prompt):
        phases.append("fix")

    if requested(
        r"(?:propose|recommend|design|prepare)",
        _SECURITY_HARDENING_OBJECT,
    ):
        phases.append("hardening")
    if _has_security_diff_context(text) and requested(
        r"(?:run|perform|conduct|scan|audit|review|inspect|check|do)",
        _SECURITY_DIFF_OBJECT,
    ):
        phases.append("diff_scan")
    if requested(
        r"(?:use|invoke|run|create|build|develop|draft|map|review|update|threat[- ]model)",
        _SECURITY_THREAT_MODEL_OBJECT,
    ):
        phases.append("threat_model")
    if requested(
        r"(?:track|file|create|publish|sync|update)",
        _SECURITY_TRACKING_OBJECT,
    ) and _SECURITY_TRACKER_CONTEXT.search(text):
        phases.append("tracking")
    if (
        not attack_path_requested
        and requested(
            r"(?:triage|classify|prioritize|assess|review|check|investigate|analy[sz]e|inspect)",
            _SECURITY_FINDING_OBJECT,
        )
    ):
        phases.append("triage")
    if requested(
        r"(?:validate|verify|reproduce|confirm|disprove)",
        _SECURITY_FINDING_OBJECT,
    ):
        phases.append("validation")
    if requested(
        r"(?:write|document|draft|prepare|report)",
        _SECURITY_WRITEUP_OBJECT,
    ):
        phases.append("writeup")

    unique_phases = list(dict.fromkeys(phases))
    return unique_phases if len(unique_phases) > 1 else []


def _prompt_has_security_policy_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        re.search(r"\bsecurity\s+(?:policy|baseline|ruleset)\b", text)
        and _SECURITY_SYSTEM_CONTEXT.search(text)
        and _security_action_requested(
            prompt,
            r"(?:define|draft|create|write|update|revise|establish)",
            object_pattern=_SECURITY_POLICY_OBJECT,
            telegraphic_pattern=r"^security\s+(?:policy|baseline)\s+(?:definition|draft|update)\b",
        )
    )


def _prompt_has_codex_security_threat_model_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    explicit_plugin = bool(
        re.search(r"\b(?:codex\s+security|security\s+scan)\b", text)
        or "$threat-model" in text
        or "codex-security:threat-model" in text
    )
    artifact_context = bool(
        re.search(r"\b(?:scan\s+artifact|persist(?:ed)?|stored|update\s+the\s+scan)\b", text)
    )
    return bool(
        (explicit_plugin or artifact_context)
        and re.search(r"\bthreat[- ]model\b", text)
        and _security_action_requested(
            prompt,
            r"(?:use|invoke|run|create|build|develop|draft|map|review|update|threat[- ]model)",
            object_pattern=_SECURITY_THREAT_MODEL_OBJECT,
            telegraphic_pattern=r"^(?:codex\s+security\s+)?threat[- ]model\b",
        )
    )


def _prompt_has_local_security_threat_model_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    if _prompt_has_codex_security_threat_model_intent(prompt):
        return False
    return bool(
        re.search(r"\bthreat\s+model\b", text)
        and _SECURITY_SYSTEM_CONTEXT.search(text)
        and _security_action_requested(
            prompt,
            r"(?:create|build|develop|draft|map|review|update|threat[- ]model)",
            object_pattern=_SECURITY_THREAT_MODEL_OBJECT,
            telegraphic_pattern=r"^threat\s+model\s+(?:for|of)\b",
        )
    )


def _prompt_has_security_implementation_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    if _prompt_defers_or_explains_security_fix(prompt):
        return False
    critique_polarity, critique_mature, _ = _prompt_critique_state(prompt)
    copyedit_mutation = bool(
        critique_polarity is False
        and not critique_mature
        and _COPYEDIT_IMPLEMENTATION_CONTEXT.search(text)
    )
    if copyedit_mutation:
        return False
    mutation_requested = _security_action_requested(
        prompt,
        r"(?:implement|fix|remediate|patch|apply|enforce|harden|secure|add|remove|update|change|create|alter|revoke|grant|enable|disable)",
        object_pattern=_SECURITY_IMPLEMENTATION_OBJECT,
    )
    return bool(
        _technical_security_context_is_bounded(text)
        and mutation_requested
        and not _prompt_explicitly_excludes_implementation(text)
    )


def _prompt_has_provider_implementation_intent(prompt: str, *, security: bool) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    if (
        _prompt_has_security_diff_review_intent(prompt)
        or _prompt_has_security_scan_intent(prompt, deep=False)
        or _prompt_has_security_scan_intent(prompt, deep=True)
    ):
        return False
    mutation_requested = bool(
        (
            not security
            and _prompt_has_affirmative_implementation(text)
        )
        or (
            security
            and _prompt_has_affirmative_implementation(text)
            and re.search(rf"\b{_SECURITY_IMPLEMENTATION_OBJECT}\b", text)
            and not _SECURITY_ACTION_NEGATION.search(text)
        )
        or _security_action_requested(
            prompt,
            r"(?:implement|build|create|apply|run|migrate|replace|update|change|add|remove|alter|deploy|configure|fix|remediate|patch|enforce|harden|secure|revoke|grant|enable|disable)",
            object_pattern=(
                _SECURITY_IMPLEMENTATION_OBJECT if security else ""
            ),
        )
    )
    return bool(
        _has_provider_database_context(text)
        and mutation_requested
        and not _prompt_explicitly_excludes_implementation(text)
        and (_has_security_context(text) if security else True)
    )


def _prompt_has_postgres_implementation_intent(prompt: str, *, security: bool) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    if (
        _prompt_has_security_diff_review_intent(prompt)
        or _prompt_has_security_scan_intent(prompt, deep=False)
        or _prompt_has_security_scan_intent(prompt, deep=True)
    ):
        return False
    mutation_requested = bool(
        (
            not security
            and _prompt_has_affirmative_implementation(text)
        )
        or (
            security
            and _prompt_has_affirmative_implementation(text)
            and re.search(rf"\b{_SECURITY_IMPLEMENTATION_OBJECT}\b", text)
            and not _SECURITY_ACTION_NEGATION.search(text)
        )
        or _security_action_requested(
            prompt,
            r"(?:implement|build|create|apply|run|migrate|replace|update|change|add|remove|alter|deploy|configure|fix|remediate|patch|enforce|harden|secure|revoke|grant|enable|disable)",
            object_pattern=(
                _SECURITY_IMPLEMENTATION_OBJECT if security else ""
            ),
        )
    )
    return bool(
        _has_postgres_database_context(text)
        and mutation_requested
        and not _prompt_explicitly_excludes_implementation(text)
        and (_has_security_context(text) if security else True)
    )


def _prompt_has_frontend_security_implementation_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    mutation_requested = _security_action_requested(
        prompt,
        r"(?:implement|build|create|apply|update|change|add|remove|configure|fix|remediate|patch|enforce|harden|secure|enable|disable)",
        object_pattern=_SECURITY_IMPLEMENTATION_OBJECT,
    )
    return bool(
        _FRONTEND_SECURITY_CONTEXT.search(text)
        and mutation_requested
        and not _prompt_explicitly_excludes_implementation(text)
    )


def _prompt_has_provider_operations_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        _has_provider_database_context(text)
        and not _prompt_has_affirmative_implementation(text)
        and _security_action_requested(
            prompt,
            r"(?:inspect|list|show|read|query|check|validate|compare|explain|operate|manage)",
            telegraphic_pattern=r"^(?:supabase|neon\s+(?:postgres|database|data\s+api))\s+(?:project|operation|status|configuration)\b",
        )
    )


def _prompt_has_neon_egress_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        _has_neon_database_context(text)
        and re.search(r"\b(?:egress|data\s+transfer|bandwidth|query\s+payload)\b", text)
        and _security_action_requested(
            prompt,
            r"(?:analy[sz]e|audit|optimi[sz]e|reduce|review|measure)",
            telegraphic_pattern=r"^neon\s+(?:postgres\s+)?egress\s+(?:analysis|optimization|review)\b",
        )
    )


def _prompt_has_security_ownership_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        re.search(r"\b(?:security\s+ownership|security[- ]sensitive\s+(?:files?|code)|"
                  r"security\s+bus\s+factor|risk\s+hotspots?)\b", text)
        and _security_action_requested(
            prompt,
            r"(?:map|identify|analy[sz]e|create|review)",
            object_pattern=_SECURITY_OWNERSHIP_OBJECT,
            telegraphic_pattern=r"^security\s+ownership\s+map\b",
        )
    )


def _prompt_has_defensive_checklist_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        re.search(r"\b(?:defensive\s+security\s+checklist|security\s+checklist)\b", text)
        and _security_action_requested(
            prompt,
            r"(?:run|apply|use|review|check|validate)",
            object_pattern=_SECURITY_CHECKLIST_OBJECT,
            telegraphic_pattern=r"^(?:defensive\s+)?security\s+checklist\b",
        )
    )


def _prompt_has_security_best_practices_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    critique_polarity, _, effective_clause = _prompt_critique_state(prompt)
    live_security_text = _critique_live_domain_text(prompt, effective_clause)
    explicit_best_practices = bool(
        re.search(r"\bsecurity\s+best\s+practices\b", text)
        and not _prompt_has_affirmative_implementation(text)
        and _security_action_requested(
            prompt,
            r"(?:review|assess|check|explain|recommend|apply)",
            object_pattern=_SECURITY_BEST_PRACTICES_OBJECT,
            telegraphic_pattern=r"^security\s+best\s+practices\s+(?:review|assessment|guidance)\b",
        )
    )
    technical_review = bool(
        _technical_security_context_is_bounded(live_security_text)
        and not _SECURITY_FINDING_CONTEXT.search(text)
        and not _SECURITY_DIFF_CONTEXT.search(text)
        and (
            not _TEXT_ONLY_REVIEW_CONTEXT.search(text)
            or bool(
                _prompt_has_affirmative_critique_intent(prompt)
                and
                (
                    _STRONG_SECURITY_TERM_CONTEXT.search(live_security_text)
                    or (
                        _AMBIGUOUS_SECURITY_TERM_CONTEXT.search(live_security_text)
                        and re.search(
                            rf"\b{_SECURITY_TECHNICAL_SYSTEM}\b",
                            live_security_text,
                        )
                    )
                )
                and _CRITIQUE_SUBSTANTIVE_MARKER.search(live_security_text)
            )
        )
        and not _prompt_has_affirmative_implementation(text)
        and not _prompt_has_provider_operations_intent(text)
        and (
            _prompt_has_affirmative_direct_action(
                prompt,
                r"(?:review|assess|audit|check|analy[sz]e|inspect|verify|validate|"
                r"evaluate|recommend)",
            )
            or _prompt_has_affirmative_critique_intent(prompt)
        )
        and critique_polarity is not False
    )
    return explicit_best_practices or technical_review


def _prompt_has_affirmative_direct_action(
    prompt: str,
    action_pattern: str,
    *,
    special_positive: Iterable[re.Pattern[str]] = (),
) -> bool:
    """Return the last direct action instruction after removing quoted text."""

    polarity: bool | None = None
    for clause in _directive_clauses(prompt):
        clause_polarity = _clause_directive_polarity(
            clause,
            action_pattern,
            special_positive=special_positive,
        )
        if clause_polarity is not None:
            polarity = clause_polarity
    return polarity is True


_ADVERSARIAL_REVIEW_CONTEXT = re.compile(
    r"\b(?:critique|challenge\s+(?:this|that|the|my|our|its?)|audit|stress[- ]test|pressure[- ]test|"
    r"poke\s+holes\s+in|find\s+flaws?(?:\s+in)?|critically|weak\s+logic|"
    r"expose\s+(?:the\s+)?flaws?|"
    r"skeptical\s+review|hard\s+second\s+opinion)\b"
)
_OPERATIONAL_METHOD_CONTEXT = re.compile(
    r"\b(?:root[- ]cause(?:\s+analysis)?|rca|capa|5\s*whys?|fishbone|ishikawa|"
    r"8d|a3|pdca|kaizen)\b"
)
_OPERATIONAL_RECURRENCE_CONTEXT = re.compile(
    r"\b(?:recurr(?:ing|ence|ent)|repeat(?:ed|ing)|"
    r"keeps?\s+(?:happening|recurring|failing)|every\s+(?:day|week|month))\b"
)
_OPERATIONAL_ISSUE_CONTEXT = re.compile(
    r"\b(?:errors?|defects?|fail(?:ing|ures?)|incidents?|problems?|issues?|rework|"
    r"bottlenecks?|backlogs?|sla\s+miss(?:es)?|service\s+failure|process\s+waste)\b"
)
_OPERATIONAL_ANALYSIS_SPECIAL_POSITIVE = (
    re.compile(r"^find\s+(?:the\s+)?(?:underlying|root)\s+cause\b"),
    re.compile(
        r"^why\b[^.!?;]{0,180}\b(?:recurr(?:ing|ent|ence)|root[- ]cause|"
        r"keeps?\s+(?:happening|recurring|failing)|every\s+(?:day|week|month))\b"
    ),
    re.compile(
        r"^what\b[^.!?;]{0,100}\b(?:causes?|caused|is\s+causing)\b"
        r"[^.!?;]{0,120}\b(?:recurr(?:ing|ent)|repeat(?:ed|ing)|root[- ]cause)\b"
    ),
)


def _prompt_has_operational_problem_analysis_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    if not text or _ADVERSARIAL_REVIEW_CONTEXT.search(text):
        return False
    context_matches = bool(
        _OPERATIONAL_METHOD_CONTEXT.search(text)
        or (
            _OPERATIONAL_RECURRENCE_CONTEXT.search(text)
            and _OPERATIONAL_ISSUE_CONTEXT.search(text)
        )
        or re.search(
            r"\b(?:rework|bottlenecks?|backlogs?|sla\s+miss(?:es)?|"
            r"service\s+failure|process\s+waste)\b",
            text,
        )
    )
    return context_matches and _prompt_has_affirmative_direct_action(
        prompt,
        r"(?:run|perform|conduct|investigate|diagnose|analy[sz]e|solve|reduce|prevent)",
        special_positive=_OPERATIONAL_ANALYSIS_SPECIAL_POSITIVE,
    )


def _prompt_has_operational_problem_review_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        text
        and _OPERATIONAL_METHOD_CONTEXT.search(text)
        and not _ADVERSARIAL_REVIEW_CONTEXT.search(text)
        and _prompt_has_affirmative_direct_action(prompt, r"review")
    )


_METRIC_CONTEXT = re.compile(
    r"\b(?:metric|kpi|rate|revenue|sales|conversion|retention|churn|traffic|"
    r"volume|count|margin|profit|cost|cycle\s+time|lead\s+time|sla|yield)\b"
)
_METRIC_MOVEMENT_CONTEXT = re.compile(
    r"\b(?:declin(?:e|ed|ing)|drop(?:ped|ping)?|fall|falls|fell|fallen|"
    r"decreas(?:e|ed|ing)|"
    r"increas(?:e|ed|ing)|rise|rose|rising|chang(?:e|ed|ing)|movement|variance|"
    r"gap|discrepancy|anomal(?:y|ies)|spike|regression|above\s+target|"
    r"below\s+target|against\s+target|versus\s+target|vs\.?\s+target|"
    r"month\s+over\s+month|actual\s+(?:and|versus|vs\.?|to)\s+target)\b"
)
_METRIC_DIAGNOSTIC_SPECIAL_POSITIVE = (
    re.compile(r"^why\b[^.!?;]{0,200}"),
    re.compile(
        r"^what\b[^.!?;]{0,100}\b(?:explains?|drove|drives?|caused?|is\s+causing)\b"
    ),
)


def _prompt_has_metric_diagnostics_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        text
        and _METRIC_CONTEXT.search(text)
        and _METRIC_MOVEMENT_CONTEXT.search(text)
        and not _ADVERSARIAL_REVIEW_CONTEXT.search(text)
        and _prompt_has_affirmative_direct_action(
            prompt,
            r"(?:diagnose|analy[sz]e|investigate|explain|reconcile)",
            special_positive=_METRIC_DIAGNOSTIC_SPECIAL_POSITIVE,
        )
    )


_DATA_QUALITY_CONTEXT = re.compile(
    r"\b(?:data\s+quality|dataset|query\s+results?|table|dashboard|csv|tsv|spreadsheet)\b"
)
_DATA_QUALITY_ISSUE_CONTEXT = re.compile(
    r"\b(?:missing(?:ness)?|nulls?|duplicates?|duplicate\s+keys?|schema\s+drift|"
    r"freshness|grain|join\s+coverage|orphan\s+records?|validity|completeness|"
    r"uniqueness|referential\s+integrity|distribution\s+shift|trustworthy|"
    r"safe\s+to\s+(?:cite|use)|fit\s+for\s+decision[- ]making)\b"
)
_DATA_QUALITY_SPECIAL_POSITIVE = (
    re.compile(
        r"^(?:is|are)\b[^.!?;]{0,160}\b(?:trustworthy|safe\s+to\s+(?:cite|use)|"
        r"fit\s+for\s+decision[- ]making)\b"
    ),
)


def _prompt_has_data_quality_analysis_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    context_matches = bool(
        "data quality" in text
        or (
            _DATA_QUALITY_CONTEXT.search(text)
            and _DATA_QUALITY_ISSUE_CONTEXT.search(text)
        )
    )
    return bool(
        context_matches
        and not _ADVERSARIAL_REVIEW_CONTEXT.search(text)
        and _prompt_has_affirmative_direct_action(
            prompt,
            r"(?:check|inspect|assess|profile|analy[sz]e|evaluate|reconcile)",
            special_positive=_DATA_QUALITY_SPECIAL_POSITIVE,
        )
    )


_SPREADSHEET_FILE_CONTEXT = re.compile(
    r"(?:\.(?:csv|tsv|xlsx?|xls)\b|\b(?:csv|tsv|xlsx?|xls)\s+file\b|"
    r"\b(?:attached|uploaded|provided|this|the)\s+(?:csv|tsv|xlsx?|xls)\b|"
    r"\b(?:spreadsheet|workbook)\s+file\b)"
)
_PDF_FILE_CONTEXT = re.compile(
    r"(?:\.pdf\b|\bpdf\s+file\b|"
    r"\b(?:attached|uploaded|provided|supplied|this|that|my|our|the|a|an)\s+pdf\b)"
)
_PDF_SOFTWARE_TARGET_CONTEXT = re.compile(
    r"\b(?:pdf\s+(?:parser|reader|writer|renderer|library|module|class|function|api)|"
    r"(?:parser|reader|writer|renderer|library|module|class|function|api)\s+"
    r"(?:for|that\s+handles?)\s+(?:a\s+)?pdf)\b"
)
_SOFTWARE_FILE_IMPLEMENTATION_CONTEXT = re.compile(
    r"\b(?:parser|reader|writer|renderer|rendering\s+bug|library|module|class|"
    r"function|api|source\s+code)\b"
)
_MATERIAL_ARTIFACT_CREATION_CONTEXT = re.compile(
    r"\b(?:create|build|generate|prepare|write|edit)\s+"
    r"(?:(?:a|an|the)\s+)?(?:pdf|docx|word\s+document|spreadsheet|workbook|"
    r"slide\s+deck|presentation|dashboard|image|illustration)\b"
)
_PDF_TO_SPREADSHEET_OUTPUT_CONTEXT = re.compile(
    r"\b(?:into|to|as)\s+(?:(?:a|an|the)\s+)?"
    r"(?:spreadsheet|workbook|csv|xlsx)\b"
)
_SPREADSHEET_ANALYSIS_SPECIAL_POSITIVE = (
    re.compile(
        r"^(?:what|which)\b[^.!?;]{0,160}\b(?:trends?|patterns?|findings?|"
        r"totals?|rows?|columns?)\b"
    ),
)
_PDF_ANALYSIS_SPECIAL_POSITIVE = (
    re.compile(
        r"^(?:what|which)\b[^.!?;]{0,160}\b(?:findings?|conclusions?|sections?|"
        r"tables?|figures?)\b"
    ),
)


def _prompt_has_direct_spreadsheet_analysis_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        _SPREADSHEET_FILE_CONTEXT.search(text)
        and not _SOFTWARE_FILE_IMPLEMENTATION_CONTEXT.search(text)
        and not _MATERIAL_ARTIFACT_CREATION_CONTEXT.search(text)
        and not _prompt_has_affirmative_implementation(text)
        and not _ADVERSARIAL_REVIEW_CONTEXT.search(text)
        and _prompt_has_affirmative_direct_action(
            prompt,
            r"(?:analy[sz]e|inspect|read|open|summari[sz]e|aggregate|filter|pivot)",
            special_positive=_SPREADSHEET_ANALYSIS_SPECIAL_POSITIVE,
        )
    )


def _prompt_has_direct_pdf_analysis_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    critique_polarity, _, effective_clause = _prompt_critique_state(prompt)
    live_pdf_text = (
        _critique_live_domain_text(prompt, effective_clause)
        if critique_polarity is True
        else text
    )
    copyedit_review = bool(
        critique_polarity is False
        and _prompt_has_affirmative_direct_action(
            prompt,
            r"(?:review|critique|audit|challenge|validate|compare|stress[- ]test|"
            r"pressure[- ]test)",
        )
    )
    return bool(
        _PDF_FILE_CONTEXT.search(live_pdf_text)
        and not _PDF_SOFTWARE_TARGET_CONTEXT.search(text)
        and not _MATERIAL_ARTIFACT_CREATION_CONTEXT.search(text)
        and not _PDF_TO_SPREADSHEET_OUTPUT_CONTEXT.search(text)
        and not _prompt_has_affirmative_implementation(text)
        and not copyedit_review
        and not _ADVERSARIAL_REVIEW_CONTEXT.search(text)
        and _prompt_has_affirmative_direct_action(
            prompt,
            r"(?:analy[sz]e|inspect|review|read|open|summari[sz]e|extract)",
            special_positive=_PDF_ANALYSIS_SPECIAL_POSITIVE,
        )
    )


def _prompt_has_critical_pdf_review_intent(prompt: str) -> bool:
    """Compose PDF inspection with an explicitly evaluative critique action."""

    text = _normalized_unquoted_prompt(prompt)
    _, _, effective_clause = _prompt_critique_state(prompt)
    live_pdf_text = _critique_live_domain_text(prompt, effective_clause)
    return bool(
        _PDF_FILE_CONTEXT.search(live_pdf_text)
        and not _PDF_SOFTWARE_TARGET_CONTEXT.search(text)
        and not _MATERIAL_ARTIFACT_CREATION_CONTEXT.search(text)
        and not _prompt_has_affirmative_implementation(text)
        and _prompt_has_affirmative_critique_intent(prompt)
        and (
            _prompt_has_mature_deep_critique_intent(prompt)
            or
            _DEEP_CRITIQUE_EVALUATION_CONTEXT.search(text)
            or _ADVERSARIAL_REVIEW_CONTEXT.search(text)
            or any(pattern.search(text) for pattern in _CRITIQUE_PDF_SPECIAL_POSITIVE)
        )
    )


_STRATEGIC_OPTION_CONTEXT = re.compile(
    r"\b(?:strateg(?:y|ies|ic)|markets?|market[- ]entry|products?|architecture|"
    r"repo(?:sitory)?[- ]adoption|business|pricing|operational|investments?|"
    r"initiatives?|vendors?)\b"
)
_OPTION_SET_CONTEXT = re.compile(
    r"\b(?:options?|alternatives?|approaches?|strategies|markets?|vendors?|"
    r"investments?|initiatives?|products?|architectures?)\b"
)
_EXPLICIT_OPTION_PAIR_CONTEXT = re.compile(
    r"\boption\s+[a-z0-9][a-z0-9._-]*\b[^.!?;]{0,180}"
    r"\b(?:against|versus|vs\.?|and|or|with)\s+option\s+[a-z0-9][a-z0-9._-]*\b"
)
_STRATEGIC_DECISION_SPECIAL_POSITIVE = (
    re.compile(
        r"^which\b[^.!?;]{0,180}\b(?:should|do)\s+(?:we|i)\s+"
        r"(?:choose|select|prioriti[sz]e)\b"
    ),
    re.compile(
        r"^what\b[^.!?;]{0,120}\b(?:option|alternative|approach|strategy)\b"
        r"[^.!?;]{0,80}\bshould\s+(?:we|i)\s+(?:choose|select)\b"
    ),
    re.compile(
        r"^which\s+(?:architecture|product|market|strategy|vendor|initiative)\b"
        r"[^.!?;]{0,80}\bshould\s+(?:we|i)\s+(?:choose|select|pick)\b"
    ),
)


def _prompt_has_strategic_option_decision_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    if (
        not text
        or _ADVERSARIAL_REVIEW_CONTEXT.search(text)
        or _TEXT_ONLY_REVIEW_CONTEXT.search(text)
    ):
        return False
    strategic_context = _STRATEGIC_OPTION_CONTEXT.search(text) is not None
    referenced_option_set = re.search(
        r"\b(?:these|the|our)\s+(?:options?|alternatives?|approaches?|strategies|"
        r"markets?|vendors?|investments?|initiatives?|products?)\b",
        text,
    ) is not None
    explicit_pair = _EXPLICIT_OPTION_PAIR_CONTEXT.search(text) is not None
    if not (strategic_context or referenced_option_set or explicit_pair) or not _OPTION_SET_CONTEXT.search(text):
        return False
    direct_decision = _prompt_has_affirmative_direct_action(
        prompt,
        r"(?:choose|decide|rank|prioriti[sz]e|recommend|select|debate|pick)",
        special_positive=_STRATEGIC_DECISION_SPECIAL_POSITIVE,
    )
    direct_comparison = _prompt_has_affirmative_direct_action(
        prompt, r"(?:compare|evaluate)"
    )
    pair_decision_marker = re.search(
        r"\b(?:recommend|choose|choice|decide|decision|select|rank|prioriti[sz]e|pick)\b",
        text,
    ) is not None
    if explicit_pair:
        return bool(direct_decision or (direct_comparison and pair_decision_marker))
    return bool(
        direct_decision
        or ((strategic_context or referenced_option_set) and direct_comparison and pair_decision_marker)
    )


_PRICING_STRATEGY_CONTEXT = re.compile(
    r"\b(?:pricing\s+(?:strategy|approach|architecture|structure|plan)|"
    r"price\s+(?:strategy|architecture|structure|points?|tiers?)|"
    r"packaging\s+and\s+pricing|willingness\s+to\s+pay|price\s+elasticity|"
    r"van\s+westendorp(?:\s+(?:analysis|method|study))?)\b"
)
_VAN_WESTENDORP_CONTEXT = re.compile(r"\bvan\s+westendorp\b")
_OPERATING_MODEL_DESIGN_CONTEXT = re.compile(
    r"\b(?:operating\s+model|decision\s+rights|team\s+interfaces|"
    r"accountabilit(?:y|ies)|organizational\s+interfaces|ways?\s+of\s+working|"
    r"how\s+(?:our|the)\s+company\s+operates?|"
    r"interfaces?\s+between\s+(?:our|the)\s+departments?)\b"
)
_SITUATION_ASSESSMENT_CONTEXT = re.compile(
    r"\b(?:unresolved\s+(?:(?:business|company|customer|market|operations?)\s+)?(?:problem|situation|challenge)|"
    r"unresolved\s+organizational\s+(?:problem|situation|challenge)|"
    r"unclear\s+(?:(?:business|company|customer|market|revenue)\s+)?(?:problem|situation|challenge)|"
    r"what\s+(?:(?:should|do)\s+(?:we|i)\s+)?investigate\s+first|initial\s+(?:business\s+)?diagnosis|"
    r"broad\s+(?:business\s+)?problem|hypothesis\s+tree)\b"
)
_BUSINESS_OR_ORGANIZATIONAL_CONTEXT = re.compile(
    r"\b(?:business|organization|organis[sz]ation|organizational|company|enterprise|"
    r"markets?|customers?|products?|services?|consulting|revenue|sales|commercial|operations?|"
    r"operating\s+model|departments?|team\s+decision\s+rights|"
    r"decision\s+rights\s+across\s+teams?|organizational\s+challenge|"
    r"leadership|workforce|vendor|investment|initiative|unit\s+economics)\b"
)
_NONBUSINESS_CONTEXT = re.compile(
    r"\b(?:history\s+essay|household\s+chores?|board\s+game|sports?\s+game|"
    r"family\s+game\s+night)\b"
)
_SPECIALIST_SOFTWARE_OR_ARTIFACT_CONTEXT = re.compile(
    r"\b(?:react|next\.?js|vue|svelte|frontend|backend|api|repository|repo|"
    r"codebase|source\s+code|component|module|function|class|database|schema|"
    r"migration|endpoint|typescript|javascript|python)\b|"
    r"\b(?:create|build|generate|prepare|write|edit|design)\b[^.!?;]{0,100}\b"
    r"(?:pdf|docx|word\s+document|spreadsheet|workbook|"
    r"slide\s+deck|presentation|dashboard|component|api|schema|image|illustration)\b"
)
_LINGUISTIC_OR_PROOFREADING_CONTEXT = re.compile(
    r"\b(?:grammar|spelling|punctuation|wording|capitalization|proofread|"
    r"linguistic|sentence|phrase|typos?|copyedit|copy\s+edit)\b"
)


def _prompt_has_pricing_strategy_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        _PRICING_STRATEGY_CONTEXT.search(text)
        and (
            _BUSINESS_OR_ORGANIZATIONAL_CONTEXT.search(text)
            or _VAN_WESTENDORP_CONTEXT.search(text)
        )
        and not _NONBUSINESS_CONTEXT.search(text)
        and not _ADVERSARIAL_REVIEW_CONTEXT.search(text)
        and not _MATERIAL_ARTIFACT_CREATION_CONTEXT.search(text)
        and not _SPECIALIST_SOFTWARE_OR_ARTIFACT_CONTEXT.search(text)
        and not _LINGUISTIC_OR_PROOFREADING_CONTEXT.search(text)
        and not _prompt_has_affirmative_implementation(text)
        and _prompt_has_affirmative_direct_action(
            prompt,
            r"(?:run|develop|design|define|formulate|create|recommend|optimi[sz]e|"
            r"set|assess|analy[sz]e)",
        )
    )


def _prompt_has_operating_model_design_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        _OPERATING_MODEL_DESIGN_CONTEXT.search(text)
        and _BUSINESS_OR_ORGANIZATIONAL_CONTEXT.search(text)
        and not _NONBUSINESS_CONTEXT.search(text)
        and not _ADVERSARIAL_REVIEW_CONTEXT.search(text)
        and not _MATERIAL_ARTIFACT_CREATION_CONTEXT.search(text)
        and not _SPECIALIST_SOFTWARE_OR_ARTIFACT_CONTEXT.search(text)
        and not _LINGUISTIC_OR_PROOFREADING_CONTEXT.search(text)
        and not _prompt_has_affirmative_implementation(text)
        and _prompt_has_affirmative_direct_action(
            prompt,
            r"(?:develop|design|define|redesign|create|establish|map|clarify|structure)",
        )
    )


def _prompt_has_situation_assessment_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    return bool(
        _SITUATION_ASSESSMENT_CONTEXT.search(text)
        and _BUSINESS_OR_ORGANIZATIONAL_CONTEXT.search(text)
        and not _NONBUSINESS_CONTEXT.search(text)
        and not _ADVERSARIAL_REVIEW_CONTEXT.search(text)
        and not _MATERIAL_ARTIFACT_CREATION_CONTEXT.search(text)
        and not _SPECIALIST_SOFTWARE_OR_ARTIFACT_CONTEXT.search(text)
        and not _LINGUISTIC_OR_PROOFREADING_CONTEXT.search(text)
        and not _prompt_has_affirmative_implementation(text)
        and _prompt_has_affirmative_direct_action(
            prompt,
            r"(?:assess|structure|frame|diagnose|analy[sz]e|investigate|triage|build)",
            special_positive=(
                re.compile(
                    r"^what\s+(?:(?:should|do)\s+(?:we|i)\s+)?investigate\s+first\b"
                ),
            ),
        )
    )


_SPECIALIST_NON_IMPLEMENTATION_OUTPUT = re.compile(
    r"\b(?:prd|product\s+requirements\s+document|business\s+case|slide\s+deck|"
    r"presentation|pptx|word\s+document|docx|spreadsheet|excel\s+workbook|xlsx|"
    r"dashboard\s+from\s+data|analytical\s+dashboard|data\s+analytics\s+dashboard|"
    r"(?:100|hundred)[- ]day\s+plan|first\s+100\s+days)\b|"
    r"\b(?:generate|create|edit)\s+(?:(?:a|an|the)\s+)?(?:image|illustration)\b"
)


def _prompt_has_nonexecution_subject_intent(prompt: str) -> bool:
    return _prompt_has_affirmative_direct_action(
        prompt,
        r"(?:summari[sz]e|transcribe|recap|extract)",
    )


def _prompt_requests_specialist_nonimplementation_owner(prompt: str) -> bool:
    text = re.sub(r"\s+", " ", _prompt_without_quoted_text(prompt).lower()).strip()
    return bool(
        _SPECIALIST_NON_IMPLEMENTATION_OUTPUT.search(text)
        or _prompt_has_affirmative_critique_intent(prompt)
        or _prompt_has_nonexecution_subject_intent(text)
        or _prompt_has_affirmative_software_documentation_intent(text)
    )


_SOFTWARE_CONTEXT = re.compile(
    r"\b(?:software|app|application|website|api|frontend|backend|code|coding|"
    r"codebase|repo|repository|module|package|bug|feature|service|component|"
    r"tech\s+stack|app\s+flow|architecture)\b"
)
_CODE_ARCHITECTURE_CONTEXT = re.compile(
    r"(?:\b(?:codebase|repository|repo|software|source\s+code|module|package)\b"
    r"[^.!?;]{0,180}\b(?:architecture|coupling|refactor(?:ing)?|"
    r"dependency\s+direction|module\s+boundaries)\b|"
    r"\b(?:architecture|coupling|refactor(?:ing)?|dependency\s+direction|"
    r"module\s+boundaries)\b[^.!?;]{0,180}"
    r"\b(?:codebase|repository|repo|software|source\s+code|module|package)\b)"
)
_CODE_CHANGE_CONTEXT = re.compile(
    r"\b(?:github|git|pull\s+request|repository|repo|codebase|source\s+code|"
    r"code\s+change|implementation|candidate\s+head|commit|merge|unit\s+tests?|"
    r"test\s+suite|ci|build\s+pipeline|module|function|class|bug|patch)\b"
)


def _prompt_has_code_change_context(prompt: str) -> bool:
    return _CODE_CHANGE_CONTEXT.search(
        re.sub(r"\s+", " ", _prompt_without_quoted_text(prompt).lower())
    ) is not None


_CODE_ARCHITECTURE_SPECIAL_POSITIVE = (
    re.compile(r"^(?:software|repository|codebase)\s+architecture\s+review\b"),
)


def _prompt_has_affirmative_code_architecture_review_intent(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    critique_polarity, _, effective_clause = _prompt_critique_state(prompt)
    context_text = re.sub(
        r"[-_]+", " ", _critique_live_domain_text(prompt, effective_clause)
    )
    if (
        not _CODE_ARCHITECTURE_CONTEXT.search(context_text)
        or _prompt_has_affirmative_implementation(prompt)
    ):
        return False
    if critique_polarity is False:
        return False
    return bool(
        _prompt_has_affirmative_critique_intent(prompt)
        or _prompt_has_affirmative_direct_action(
            prompt,
            r"(?:review|assess|inspect|analy[sz]e|identify|find|improve)",
            special_positive=_CODE_ARCHITECTURE_SPECIAL_POSITIVE,
        )
    )


def _policy_reference_aliases(
    references: Iterable[object], policy: dict[str, Any]
) -> tuple[str, ...]:
    aliases: set[str] = set()
    declared = policy.get("capability_aliases", {})
    for raw_reference in references:
        reference = str(raw_reference or "").strip()
        if not reference:
            continue
        without_kind = reference.removeprefix("skill:")
        candidates = {reference, without_kind}
        if ":" in without_kind:
            candidates.add(without_kind.rsplit(":", 1)[-1])
        for candidate in candidates:
            aliases.add(candidate)
            aliases.add(re.sub(r"[-_]+", " ", candidate))
        if isinstance(declared, dict):
            for declared_reference, declared_aliases in declared.items():
                if normalize(declared_reference) != normalize(reference):
                    continue
                aliases.update(str(alias).strip() for alias in declared_aliases)
    return tuple(
        sorted(
            (alias for alias in aliases if alias),
            key=lambda alias: (-len(normalize(alias)), normalize(alias), alias.casefold()),
        )
    )


def _prompt_is_concise_declared_rule_trigger(
    prompt: str, rule: dict[str, Any]
) -> bool:
    """Preserve an exact declared trigger without admitting prose mentions."""

    text = _normalized_unquoted_prompt(prompt).rstrip(" .!?")
    if not text:
        return False
    triggers = [*rule.get("match_all", []), *rule.get("match_any", [])]
    return any(
        text
        == re.sub(r"\s+", " ", str(trigger or "").strip().lower()).rstrip(
            " .!?"
        )
        for trigger in triggers
        if str(trigger or "").strip()
    )


def _prompt_affirmatively_invokes_any(prompt: str, targets: Iterable[str]) -> bool:
    text = re.sub(
        r"\s+", " ", _prompt_without_quoted_text(prompt).lower().replace("’", "'")
    ).strip()
    if not text:
        return False
    for target in targets:
        target_text = str(target or "").strip().lower()
        if not target_text:
            continue
        escaped = re.escape(target_text).replace(r"\ ", r"\s+")
        optional_sigil = "" if target_text.startswith("$") else r"\$?"
        target_pattern = (
            rf"(?<![a-z0-9]){optional_sigil}{escaped}(?![a-z0-9])"
        )
        if re.fullmatch(rf"\s*{target_pattern}\s*[.!]?\s*", text):
            return True
        patterns = (
            rf"(?:^|[;.!?]\s+)(?:(?:please|actually|explicitly)\s+)*"
            rf"(?:use|invoke|apply|select|run|follow)\s+(?:the\s+)?{target_pattern}",
            rf"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?"
            rf"(?:use|invoke|apply|select|run|follow)\s+(?:the\s+)?{target_pattern}",
            rf"\b(?:i|we)\s+(?:want|need|would\s+like|prefer)\s+"
            rf"(?:you\s+)?to\s+(?:use|invoke|apply|select|run|follow)\s+"
            rf"(?:the\s+)?{target_pattern}",
            rf"(?:^|[;.!?]\s+)(?:route|delegate|send)\s+"
            rf"(?:(?:this|that|the)\s+(?:task|request|work)\s+)?to\s+"
            rf"(?:the\s+)?{target_pattern}",
            rf"(?:^|[;.!?]\s+)using\s+(?:the\s+)?{target_pattern}(?:\s*,|\s+to\b)",
        )
        if any(re.search(pattern, text) for pattern in patterns):
            return True
    return False


def _prompt_matches_github_ci_context(prompt: str) -> bool:
    text = re.sub(r"\s+", " ", _prompt_without_quoted_text(prompt).lower()).strip()
    if any(
        phrase in text
        for phrase in ("fix ci", "failed github actions", "failing github checks")
    ):
        return True
    return "workflow failure" in text and bool(
        re.search(r"\b(?:github|actions|ci|pipeline|build|test)\b", text)
    )


def _prompt_matches_github_review_context(prompt: str) -> bool:
    text = re.sub(r"\s+", " ", _prompt_without_quoted_text(prompt).lower()).strip()
    if "address pr comments" in text:
        return True
    if re.fullmatch(
        r"(?:address|resolve)\s+review\s+comments\s+without\s+"
        r"(?:using\s+)?(?:codex[- ]coding[- ]os(?:[- ]master)?|coding\s+os)\.?",
        text,
    ):
        return True
    return bool(
        re.search(r"\b(?:address|resolve)\s+review\s+comments\b", text)
        and re.search(
            r"\b(?:github|pull\s+request|pr\s*#\d+|repository|repo|code|diff|"
            r"commit|branch|implementation)\b",
            text,
        )
    )


def _prompt_matches_coding_validation_context(prompt: str) -> bool:
    text = re.sub(r"\s+", " ", _prompt_without_quoted_text(prompt).lower()).strip(" .")
    strong = (
        "validate this implementation before completion",
        "validate implementation before completion",
        "verify this coding change before merge",
        "verify coding change before merge",
        "validate candidate head",
        "test this implementation",
        "run unit tests",
    )
    if any(phrase in text for phrase in strong):
        return True
    if "review exact diff" in text:
        return text == "review exact diff" or _prompt_has_code_change_context(text)
    return False


def _prompt_matches_coding_branch_context(prompt: str) -> bool:
    text = re.sub(r"\s+", " ", _prompt_without_quoted_text(prompt).lower()).strip(" .")
    if any(
        phrase in text
        for phrase in (
            "finish development branch",
            "finish this development branch",
            "complete development branch",
            "implementation complete and tests pass, integrate this branch",
            "finishing-a-development-branch",
            "superpowers:finishing-a-development-branch",
        )
    ):
        return True
    if "integrate this branch" in text:
        return text == "integrate this branch" or _prompt_has_code_change_context(text)
    return False


def _prompt_matches_coding_feedback_context(prompt: str) -> bool:
    text = re.sub(r"\s+", " ", _prompt_without_quoted_text(prompt).lower()).strip(" .")
    if any(
        phrase in text
        for phrase in (
            "receive code review feedback",
            "evaluate code review feedback",
            "receiving-code-review",
            "superpowers:receiving-code-review",
        )
    ):
        return True
    if "review feedback before applying changes" in text:
        return (
            text == "review feedback before applying changes"
            or _prompt_has_code_change_context(text)
        )
    return False


def _prompt_has_documentation_context(prompt: str) -> bool:
    text = re.sub(r"\s+", " ", _prompt_without_quoted_text(prompt).lower())
    return bool(
        re.search(
            r"\b(?:documentation|document\s+it|document\s+the|fully\s+documented|"
            r"project\s+brief|prd|app[- ]flow|tech\s+stack|"
            r"prepare\s+(?:this\s+)?repository\s+for\s+implementation|"
            r"repository\s+for\s+implementation)\b",
            text,
        )
    )


_SOFTWARE_DOCUMENTATION_ACTION = r"(?:create|draft|prepare|write|document|turn)"
_SOFTWARE_DOCUMENTATION_CONJOINED_ACTION = re.compile(
    rf"(?:,\s*|\b(?:and(?:\s+then)?|then|also|by)\s+)"
    rf"{_SOFTWARE_DOCUMENTATION_ACTION}\b"
)
_SOFTWARE_DOCUMENTATION_TELEGRAPHIC_REQUEST = re.compile(
    r"^(?:documentation[- ]only\s+new(?:\s+software)?\s+project|"
    r"documentation\s+only\s+for\s+(?:a\s+)?new\s+software\s+project|"
    r"new\s+software\s+project(?:\s*,)?\s+(?:with\s+)?documentation\s+only|"
    r"start\s+(?:a\s+)?new\s+software\s+project\s+with\s+documentation\s+only)\b"
)
_SOFTWARE_DOCUMENTATION_CONSTRAINED_PROJECT_START = re.compile(
    r"^(?:start\s+)?(?:a\s+)?new\s+software\s+project\s+with\s+documentation\b"
)
_SOFTWARE_DOCUMENTATION_STANDALONE_REQUESTS = {
    "create a new project documentation system",
    "create new project documentation system",
    "turn this idea into project documentation",
}


def _prompt_is_telegraphic_software_documentation_request(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt).strip(" .")
    if _SOFTWARE_DOCUMENTATION_TELEGRAPHIC_REQUEST.search(text):
        return True
    if (
        _SOFTWARE_DOCUMENTATION_CONSTRAINED_PROJECT_START.search(text)
        and _prompt_explicitly_excludes_implementation(text)
    ):
        return True
    if text.startswith("new project documentation system"):
        return re.search(
            r"\bis\s+(?:the\s+)?(?:project\s+)?(?:title|name|subject)\b|"
            r"\bnot\s+a\s+request\b",
            text,
        ) is None
    return False


def _prompt_has_affirmative_software_documentation_intent(prompt: str) -> bool:
    polarity: bool | None = None
    for clause in _directive_clauses(prompt):
        if not _prompt_has_documentation_context(clause):
            continue
        clause_polarity = _clause_directive_polarity(
            clause,
            _SOFTWARE_DOCUMENTATION_ACTION,
        )
        if (
            clause_polarity is None
            and _SOFTWARE_DOCUMENTATION_CONJOINED_ACTION.search(clause)
        ):
            clause_polarity = True
        if clause_polarity is not None:
            polarity = clause_polarity
    if polarity is not None:
        return polarity
    return _prompt_is_telegraphic_software_documentation_request(prompt)


def _software_documentation_aliases(
    rule: dict[str, Any], policy: dict[str, Any]
) -> tuple[str, ...]:
    references = [rule.get("primary"), *rule.get("supports", [])]
    documentation_references = [
        reference
        for reference in references
        if normalize(reference).endswith("new-project-documentation-system")
    ]
    return _policy_reference_aliases(documentation_references, policy)


def _prompt_has_software_documentation_scope(
    prompt: str, aliases: Iterable[str]
) -> bool:
    text = _normalized_unquoted_prompt(prompt).strip(" .")
    return bool(
        _SOFTWARE_CONTEXT.search(text)
        or _prompt_affirmatively_invokes_any(prompt, aliases)
        or text in _SOFTWARE_DOCUMENTATION_STANDALONE_REQUESTS
        or _prompt_is_telegraphic_software_documentation_request(prompt)
    )


def _prompt_requests_only_prd_deliverable(prompt: str) -> bool:
    text = _normalized_unquoted_prompt(prompt)
    if _prompt_has_affirmative_implementation(prompt):
        return False
    requests_prd = re.search(
        r"^(?:(?:please|now)\s+)*(?:create|write|draft|prepare)\s+"
        r"(?:a\s+|the\s+)?(?:prd|product\s+requirements\s+document)\b",
        text,
    ) is not None
    additional_documentation = re.search(
        r"\b(?:documentation|project\s+brief|app[- ]flow|tech\s+stack|"
        r"documentation\s+system|repository\s+documentation)\b",
        text,
    ) is not None
    return requests_prd and not additional_documentation


def _prompt_requests_software_documentation(
    prompt: str, rule: dict[str, Any], policy: dict[str, Any]
) -> bool:
    aliases = _software_documentation_aliases(rule, policy)
    return bool(
        _prompt_affirmatively_invokes_any(prompt, aliases)
        or (
            _prompt_has_software_documentation_scope(prompt, aliases)
            and _prompt_has_affirmative_software_documentation_intent(prompt)
        )
    )


def _prompt_is_documentation_only(prompt_lower: str) -> bool:
    raw = prompt_lower.lower().replace("’", "'")
    if _prompt_has_affirmative_implementation(raw):
        return False
    text = re.sub(r"\s+", " ", raw).strip()
    if not re.search(
        r"\b(?:documentation|project\s+brief|prd|app\s+flow|tech\s+stack)\b",
        text,
    ):
        return False
    return bool(
        re.search(r"\bdocumentation[- ]only\b", text)
        or _prompt_explicitly_excludes_implementation(text)
        or re.search(r"\b(?:write|create|prepare)\s+(?:the\s+)?documentation\b", text)
    )


def _prompt_explicitly_excludes_implementation(prompt: str) -> bool:
    text = re.sub(r"\s+", " ", str(prompt or "").lower().replace("’", "'")).strip()
    return bool(
        re.search(
            r"\b(?:no\s+(?:coding|implementation)|without\s+(?:coding|implementation)|"
            r"(?:do\s+not|don'?t|dont|never)\s+(?:"
            r"(?:(?:create|write|draft|document)\s+or\s+)?(?:implement|code|build)|"
            r"(?:write|change|modify)\s+(?:any\s+)?(?:source\s+)?code)|"
            r"(?:implementation|coding)\s+(?:is|are|isn'?t|aren'?t)\s+"
            r"(?:not\s+)?(?:allowed|requested))\b",
            text,
        )
    )


def _intent_gate_matches(
    rule: dict[str, Any], prompt_lower: str, policy: dict[str, Any]
) -> bool:
    gate = normalize(rule.get("intent_gate")).replace("-", "_")
    if not gate:
        return True
    rule_id = normalize(rule.get("id")).replace("-", "_")
    migration_target = _provider_migration_target(prompt_lower)
    provider_rule: str | None = None
    if rule_id.startswith("supabase_") or "_supabase_" in rule_id:
        provider_rule = "supabase"
    elif rule_id.startswith("neon_") or "_neon_" in rule_id:
        provider_rule = "neon"
    elif rule_id.startswith("postgres_") or "_postgres_" in rule_id:
        provider_rule = "postgres"
    if migration_target and provider_rule and provider_rule != migration_target:
        return False

    tracker_rule_destinations = {
        "security_findings_tracking_github": "github",
        "security_findings_tracking_linear": "linear",
        "security_findings_tracking_jira": "jira",
    }
    tracker_destination = tracker_rule_destinations.get(rule_id)
    if tracker_destination and (
        _tracker_explicitly_negated(prompt_lower, tracker_destination)
        or tracker_destination
        not in _affirmative_tracker_destinations(prompt_lower)
    ):
        return False
    if (
        rule_id == "security_findings_tracking"
        and _mentioned_tracker_destinations(prompt_lower)
    ):
        return False
    if (
        rule_id.startswith("security_findings_tracking_")
        and len(_affirmative_tracker_destinations(prompt_lower)) > 1
    ):
        return False
    if (
        rule_id.startswith("supabase_") or "_supabase_" in rule_id
    ) and migration_target != "supabase" and not _has_supabase_database_context(
        prompt_lower
    ):
        return False
    if (
        rule_id.startswith("neon_") or "_neon_" in rule_id
    ) and migration_target != "neon" and not _has_neon_database_context(prompt_lower):
        return False
    if (
        "neon" not in rule_id
        and (rule_id.startswith("postgres_") or "_postgres_" in rule_id)
    ) and migration_target != "postgres" and not _has_postgres_database_context(
        prompt_lower
    ):
        return False
    if gate == "documentation_combined":
        if _prompt_is_documentation_only(prompt_lower):
            return False
        if (
            _prompt_has_affirmative_implementation(prompt_lower)
            and _SOFTWARE_CONTEXT.search(prompt_lower)
            and _prompt_requests_software_documentation(prompt_lower, rule, policy)
        ):
            return True
        return bool(
            re.search(
                r"\b(?:(?:start\s+)?(?:a\s+)?new\s+software\s+project\s+with\s+"
                r"(?:full\s+)?documentation|start\s+(?:a\s+)?software\s+project\s+"
                r"and\s+document\s+it)\b",
                prompt_lower,
            )
        )
    if gate == "documentation_only":
        return not _prompt_has_affirmative_implementation(prompt_lower)
    if gate == "not_documentation_only":
        return not _prompt_is_documentation_only(prompt_lower)
    if gate == "implementation":
        return _prompt_has_affirmative_implementation(prompt_lower)
    if gate == "no_implementation":
        return not _prompt_has_affirmative_implementation(prompt_lower)
    if gate == "architecture_review":
        return _prompt_has_affirmative_code_architecture_review_intent(prompt_lower)
    if gate == "capability_invocation":
        aliases = _policy_reference_aliases(
            [rule.get("primary"), *rule.get("supports", [])], policy
        )
        return _prompt_affirmatively_invokes_any(prompt_lower, aliases)
    if gate == "github_ci_context":
        return _prompt_matches_github_ci_context(prompt_lower)
    if gate == "github_review_context":
        return _prompt_matches_github_review_context(prompt_lower)
    if gate == "coding_validation_context":
        return _prompt_matches_coding_validation_context(prompt_lower)
    if gate == "coding_branch_context":
        return _prompt_matches_coding_branch_context(prompt_lower)
    if gate == "coding_feedback_context":
        return _prompt_matches_coding_feedback_context(prompt_lower)
    if gate == "software_documentation_only":
        return (
            not _prompt_has_affirmative_implementation(prompt_lower)
            and not _prompt_requests_only_prd_deliverable(prompt_lower)
            and _prompt_requests_software_documentation(prompt_lower, rule, policy)
        )
    if gate == "software_project_lifecycle":
        if (
            _prompt_is_documentation_only(prompt_lower)
            or _prompt_explicitly_excludes_implementation(prompt_lower)
        ):
            return False
        if _prompt_has_affirmative_implementation(prompt_lower):
            return bool(
                _SOFTWARE_CONTEXT.search(prompt_lower)
                or _COPYEDIT_IMPLEMENTATION_CONTEXT.search(prompt_lower)
            )
        return not _prompt_requests_specialist_nonimplementation_owner(prompt_lower)
    if gate == "deep_critique":
        aliases = _policy_reference_aliases(
            [rule.get("primary")], policy
        )
        return (
            _prompt_has_mature_deep_critique_intent(prompt_lower)
            or _prompt_affirmatively_invokes_any(prompt_lower, aliases)
        )
    if gate == "source_evaluation":
        return _prompt_has_affirmative_source_evaluation_intent(prompt_lower)
    if gate == "operational_problem_analysis":
        return _prompt_has_operational_problem_analysis_intent(prompt_lower)
    if gate == "operational_problem_review":
        return _prompt_has_operational_problem_review_intent(prompt_lower)
    if gate == "metric_diagnostics":
        return _prompt_has_metric_diagnostics_intent(prompt_lower)
    if gate == "data_quality_analysis":
        return _prompt_has_data_quality_analysis_intent(prompt_lower)
    if gate == "spreadsheet_analysis":
        return _prompt_has_direct_spreadsheet_analysis_intent(prompt_lower)
    if gate == "pdf_analysis":
        return _prompt_has_direct_pdf_analysis_intent(prompt_lower)
    if gate == "critical_pdf_review":
        return _prompt_has_critical_pdf_review_intent(prompt_lower)
    if gate == "strategic_option_decision":
        aliases = _policy_reference_aliases([rule.get("primary")], policy)
        return (
            _prompt_has_strategic_option_decision_intent(prompt_lower)
            or _prompt_affirmatively_invokes_any(prompt_lower, aliases)
        )
    if gate == "pricing_strategy":
        aliases = _policy_reference_aliases([rule.get("primary")], policy)
        return (
            _prompt_has_pricing_strategy_intent(prompt_lower)
            or _prompt_affirmatively_invokes_any(prompt_lower, aliases)
            or _prompt_is_concise_declared_rule_trigger(prompt_lower, rule)
        )
    if gate == "operating_model_design":
        aliases = _policy_reference_aliases([rule.get("primary")], policy)
        return (
            _prompt_has_operating_model_design_intent(prompt_lower)
            or _prompt_affirmatively_invokes_any(prompt_lower, aliases)
            or _prompt_is_concise_declared_rule_trigger(prompt_lower, rule)
        )
    if gate == "situation_assessment":
        aliases = _policy_reference_aliases([rule.get("primary")], policy)
        return (
            _prompt_has_situation_assessment_intent(prompt_lower)
            or _prompt_affirmatively_invokes_any(prompt_lower, aliases)
            or _prompt_is_concise_declared_rule_trigger(prompt_lower, rule)
        )
    if gate == "critique_with_implementation":
        return (
            _prompt_has_affirmative_critique_intent(prompt_lower)
            and _prompt_has_affirmative_implementation(prompt_lower)
        )
    if gate == "deliverable_execution":
        text = _normalized_unquoted_prompt(prompt_lower)
        software_implementation = bool(
            _prompt_has_affirmative_implementation(text)
            and (
                _SOFTWARE_CONTEXT.search(text)
                or _SOFTWARE_FILE_IMPLEMENTATION_CONTEXT.search(text)
            )
        )
        return bool(
            not software_implementation
            and not _prompt_has_affirmative_critique_intent(prompt_lower)
            and not _prompt_is_linguistic_critique_mention(prompt_lower)
        )
    if gate == "security_diff_review":
        return _prompt_has_security_diff_review_intent(prompt_lower)
    if gate == "security_standard_scan":
        return _prompt_has_security_scan_intent(prompt_lower, deep=False)
    if gate == "security_deep_scan":
        return _prompt_has_security_scan_intent(prompt_lower, deep=True)
    if gate == "security_finding_triage":
        return _prompt_has_finding_phase_intent(prompt_lower, "triage")
    if gate == "security_finding_validation":
        return _prompt_has_finding_phase_intent(prompt_lower, "validation")
    if gate == "security_attack_path_analysis":
        return _prompt_has_finding_phase_intent(prompt_lower, "attack_path")
    if gate == "security_finding_discovery":
        return _prompt_has_finding_phase_intent(prompt_lower, "discovery")
    if gate == "security_finding_fix":
        return _prompt_has_finding_phase_intent(prompt_lower, "fix")
    if gate == "security_policy_definition":
        return _prompt_has_security_policy_intent(prompt_lower)
    if gate == "codex_security_threat_model":
        return _prompt_has_codex_security_threat_model_intent(prompt_lower)
    if gate == "local_security_threat_model":
        return _prompt_has_local_security_threat_model_intent(prompt_lower)
    if gate == "security_hardening_proposal":
        return _prompt_has_finding_phase_intent(prompt_lower, "hardening")
    if gate == "security_vulnerability_writeup":
        return _prompt_has_finding_phase_intent(prompt_lower, "writeup")
    if gate == "security_findings_tracking":
        return _prompt_has_finding_phase_intent(prompt_lower, "tracking")
    if gate == "security_ownership_mapping":
        return _prompt_has_security_ownership_intent(prompt_lower)
    if gate == "defensive_security_checklist":
        return _prompt_has_defensive_checklist_intent(prompt_lower)
    if gate == "security_best_practices_review":
        return _prompt_has_security_best_practices_intent(prompt_lower)
    if gate == "security_implementation":
        return _prompt_has_security_implementation_intent(prompt_lower)
    if gate == "provider_security_implementation":
        return _prompt_has_provider_implementation_intent(
            prompt_lower, security=True
        )
    if gate == "provider_implementation":
        return _prompt_has_provider_implementation_intent(
            prompt_lower, security=False
        )
    if gate == "postgres_security_implementation":
        return _prompt_has_postgres_implementation_intent(
            prompt_lower, security=True
        )
    if gate == "postgres_implementation":
        return _prompt_has_postgres_implementation_intent(
            prompt_lower, security=False
        )
    if gate == "frontend_security_implementation":
        return _prompt_has_frontend_security_implementation_intent(prompt_lower)
    if gate == "provider_operations":
        return _prompt_has_provider_operations_intent(prompt_lower)
    if gate == "neon_egress":
        return _prompt_has_neon_egress_intent(prompt_lower)
    return False


def _entry_aliases(entry: dict[str, Any]) -> set[str]:
    aliases = {
        str(entry.get("id") or "").lower(),
        str(entry.get("name") or "").lower(),
        f"{entry.get('kind', '')}:{entry.get('name', '')}".lower(),
        f"{entry.get('provider', '')}:{entry.get('name', '')}".lower(),
    }
    return {alias for alias in aliases if alias.strip(":")}


def _build_lookup(
    entries: Iterable[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_alias: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        identifier = str(entry.get("id") or "")
        if identifier:
            by_id[identifier.lower()] = entry
            by_id[normalize(identifier)] = entry
        for alias in _entry_aliases(entry):
            for key in {alias, normalize(alias)}:
                by_alias.setdefault(key, []).append(entry)
    return by_id, by_alias


def _resolve_reference(
    reference: object,
    by_id: dict[str, dict[str, Any]],
    by_alias: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    value = str(reference or "").strip()
    if not value:
        return None
    direct = by_id.get(value.lower()) or by_id.get(normalize(value))
    if direct:
        return direct
    matches: dict[str, dict[str, Any]] = {}
    for key in {value.lower(), normalize(value)}:
        for entry in by_alias.get(key, []):
            matches[str(entry.get("id"))] = entry
    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


def _target_matches(entry: dict[str, Any], target: str) -> bool:
    target_lower = target.strip().lower()
    if not target_lower:
        return False
    if target_lower.startswith("provider:") and "*" not in target_lower:
        return normalize(entry.get("provider")) == normalize(target_lower.split(":", 1)[1])
    aliases = _entry_aliases(entry)
    normalized_target = normalize(target_lower)
    return any(
        fnmatch.fnmatch(alias, target_lower)
        or fnmatch.fnmatch(normalize(alias), normalized_target)
        for alias in aliases
    )


def _overrides_for(entry: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        override
        for override in policy.get("explicit_overrides", [])
        if _target_matches(entry, override.get("target", ""))
    ]


def _is_suppressed(entry: dict[str, Any], policy: dict[str, Any]) -> bool:
    return any(override.get("action") in SUPPRESS_ACTIONS for override in _overrides_for(entry, policy))


def _is_superpowers(entry: dict[str, Any]) -> bool:
    provider = normalize(entry.get("provider"))
    identifier = normalize(entry.get("id"))
    name = normalize(entry.get("name"))
    return "superpowers" in provider or identifier.startswith("superpowers-") or name.startswith("superpowers-")


def _is_coding_os(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    return normalize(entry.get("name")) == "codex-coding-os-master" or normalize(entry.get("id")).endswith(
        "codex-coding-os-master"
    )


def _tactical_support_allowed(
    entry: dict[str, Any],
    primary: dict[str, Any],
    policy: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_alias: dict[str, list[dict[str, Any]]],
) -> bool:
    relevant = [
        override
        for override in _overrides_for(entry, policy)
        if override.get("action") in TACTICAL_ACTIONS
    ]
    if _is_superpowers(entry) and not _is_coding_os(primary):
        return False
    for override in relevant:
        required_ref = override.get("requires_primary")
        if not required_ref:
            continue
        required_primary = _resolve_reference(required_ref, by_id, by_alias)
        if not required_primary or required_primary.get("id") != primary.get("id"):
            return False
    return True


def _rule_matches_prompt(
    rule: dict[str, Any], prompt_lower: str, policy: dict[str, Any]
) -> bool:
    match_all = rule.get("match_all", [])
    match_any = rule.get("match_any", [])
    if match_all and not all(_prompt_contains(prompt_lower, phrase) for phrase in match_all):
        return False
    lexical_match = bool(match_all) or bool(
        match_any and any(_prompt_contains(prompt_lower, phrase) for phrase in match_any)
    )
    intent_gate = normalize(rule.get("intent_gate")).replace("-", "_")
    semantic_only_allowed = intent_gate in {
        "documentation_combined",
        "software_documentation_only",
        "architecture_review",
        "source_evaluation",
        "operational_problem_analysis",
        "operational_problem_review",
        "metric_diagnostics",
        "data_quality_analysis",
        "spreadsheet_analysis",
        "pdf_analysis",
        "critical_pdf_review",
        "strategic_option_decision",
        "pricing_strategy",
        "operating_model_design",
        "situation_assessment",
        "security_diff_review",
        "security_standard_scan",
        "security_deep_scan",
        "security_finding_triage",
        "security_finding_validation",
        "security_attack_path_analysis",
        "security_finding_discovery",
        "security_finding_fix",
        "security_policy_definition",
        "codex_security_threat_model",
        "local_security_threat_model",
        "security_hardening_proposal",
        "security_vulnerability_writeup",
        "security_findings_tracking",
        "security_ownership_mapping",
        "defensive_security_checklist",
        "security_best_practices_review",
        "security_implementation",
        "provider_security_implementation",
        "provider_implementation",
        "postgres_security_implementation",
        "postgres_implementation",
        "frontend_security_implementation",
        "provider_operations",
        "neon_egress",
    }
    if intent_gate == "deep_critique":
        semantic_only_allowed = bool(
            normalize(rule.get("id")) == "deep-critique"
            and _prompt_has_mature_deep_critique_intent(prompt_lower)
        )
    if intent_gate == "critique_with_implementation":
        semantic_only_allowed = bool(
            normalize(rule.get("id")) == "coding-deep-critique-implementation"
            and _prompt_has_affirmative_critique_intent(prompt_lower)
            and _prompt_has_affirmative_implementation(prompt_lower)
        )
    if not lexical_match and not semantic_only_allowed:
        return False
    return _intent_gate_matches(rule, prompt_lower, policy)


def _rule_requirements_met(
    rule: dict[str, Any],
    prompt_lower: str,
    by_id: dict[str, dict[str, Any]],
    by_alias: dict[str, list[dict[str, Any]]],
    policy: dict[str, Any],
) -> bool:
    for requirement in rule.get("requires", []):
        if requirement.lower().startswith("prompt:"):
            if not _prompt_contains(prompt_lower, requirement.split(":", 1)[1]):
                return False
            continue
        entry = _resolve_reference(requirement.removeprefix("active:"), by_id, by_alias)
        if not entry or _is_suppressed(entry, policy):
            return False
    for forbidden in rule.get("forbids", []):
        if forbidden.lower().startswith("prompt:") and _prompt_contains(
            prompt_lower, forbidden.split(":", 1)[1]
        ):
            return False
    return True


def _forbidden_capability(entry: dict[str, Any], rule: dict[str, Any]) -> bool:
    for forbidden in rule.get("forbids", []):
        if forbidden.lower().startswith("prompt:"):
            continue
        value = forbidden.removeprefix("capability:")
        if _target_matches(entry, value):
            return True
    return False


def _classification_flags(classification: dict[str, Any] | None) -> set[str]:
    if not isinstance(classification, dict):
        return set()
    flags = {
        normalize(item).replace("-", "_")
        for item in _as_list(classification.get("flags"))
        if normalize(item)
    }
    for key, value in classification.items():
        if value is True:
            flags.add(normalize(key).replace("-", "_"))
    for key in ("local_stack_purpose", "purpose", "execution_class", "worker_class"):
        value = classification.get(key)
        if isinstance(value, str) and value.strip():
            flags.add(normalize(value).replace("-", "_"))
    return flags


def _explicit_classification_flags(
    classification: dict[str, Any] | None,
) -> frozenset[str] | None:
    """Return only the explicit Task Gate flag array, never purpose-derived hints."""

    if not isinstance(classification, dict):
        return None
    raw_flags = classification.get("flags")
    if not isinstance(raw_flags, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_flags
    ):
        return None
    normalized = [normalize(item).replace("-", "_") for item in raw_flags]
    if len(normalized) != len(set(normalized)):
        return None
    return frozenset(normalized)


def _normalized_classification_field(
    classification: dict[str, Any] | None, key: str
) -> str | None:
    if not isinstance(classification, dict) or key not in classification:
        return None
    value = classification.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return normalize(value).replace("-", "_")


def _explicit_requested_source_scopes(
    classification: dict[str, Any] | None,
) -> list[str] | None:
    if not isinstance(classification, dict) or "requested_source_scopes" not in classification:
        return None
    raw = classification.get("requested_source_scopes")
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw
    ):
        return None
    normalized = [normalize(item).replace("-", "_") for item in raw]
    if len(normalized) != len(set(normalized)) or len(normalized) > 3:
        return None
    return normalized


def _task_gate_common_shape(
    classification: dict[str, Any] | None,
    *,
    expected_flags: frozenset[str],
    task_type: str,
    complexity: str,
    purpose: str,
    source_needs: frozenset[str],
) -> tuple[str, str, list[str]] | None:
    """Validate the explicit fields shared by every executable Task Gate tuple."""

    flags = _explicit_classification_flags(classification)
    if flags is None or flags & TASK_GATE_POSITIVE_FLAGS != expected_flags:
        return None
    if (
        _normalized_classification_field(classification, "task_type") != task_type
        or _normalized_classification_field(classification, "complexity") != complexity
        or _normalized_classification_field(classification, "local_stack_purpose")
        != purpose
    ):
        return None
    source_need = _normalized_classification_field(classification, "source_need")
    if source_need not in source_needs:
        return None
    memory_mode = _normalized_classification_field(classification, "memory_mode")
    persistence = _normalized_classification_field(
        classification, "persistence_intent"
    )
    project_id, project_valid, _ = _structured_project(classification)
    if (
        not project_valid
        or _normalized_classification_field(classification, "project_id") != project_id
    ):
        return None
    requested_scopes = _explicit_requested_source_scopes(classification)
    if requested_scopes is None:
        return None
    if source_need in {"index", "both"}:
        if not requested_scopes:
            return None
        allowed_scopes = PROJECT_SOURCE_SCOPES.get(project_id, [])
        if project_id == "generic" or any(
            scope not in allowed_scopes for scope in requested_scopes
        ):
            return None
    elif requested_scopes:
        return None
    if source_need in {"memory", "both"}:
        if _structured_memory_scope(project_id) is None:
            return None
        if memory_mode not in {"recall", "recall_and_capture"}:
            return None
        expected_persistence = (
            "requested" if memory_mode == "recall_and_capture" else "none"
        )
        if persistence != expected_persistence:
            return None
    elif memory_mode != "none" or persistence != "none":
        return None
    return project_id, source_need, requested_scopes


def _validated_worker_roles(
    classification: dict[str, Any] | None, family: str
) -> tuple[str, ...] | None:
    for recipe in WORKER_TASK_GATE_RECIPES:
        if recipe["family"] != family:
            continue
        if _task_gate_common_shape(
            classification,
            expected_flags=recipe["flags"],
            task_type=recipe["task_type"],
            complexity=recipe["complexity"],
            purpose=recipe["purpose"],
            source_needs=recipe["source_needs"],
        ) is not None:
            return recipe["roles"]
    return None


def _validated_local_operation_recipe(
    classification: dict[str, Any] | None,
    task_input: dict[str, Any],
) -> str | None:
    for recipe_id, recipe in LOCAL_OPERATION_TASK_GATE_RECIPES.items():
        shape = _task_gate_common_shape(
            classification,
            expected_flags=recipe["flags"],
            task_type=recipe["task_type"],
            complexity=recipe["complexity"],
            purpose=recipe["purpose"],
            source_needs=frozenset({recipe["source_need"]}),
        )
        if shape is None:
            continue
        if recipe_id in {"memory_recall", "source_lookup", "retrieval_bundle"}:
            query = task_input.get("query")
            if not isinstance(query, str) or not query.strip():
                continue
        if recipe_id == "literal_extraction" and (
            not isinstance(classification, dict)
            or classification.get("exact_evidence") is not True
        ):
            continue
        return recipe_id
    return None


def _rule_classification_matches(
    rule: dict[str, Any], classification_flags: set[str]
) -> bool:
    """Require an affirmative structured Task Gate signal for one worker rule."""

    classification_any = {
        normalize(item).replace("-", "_") for item in rule.get("classification_any", [])
    }
    classification_all = {
        normalize(item).replace("-", "_") for item in rule.get("classification_all", [])
    }
    classification_match = bool(classification_any) and bool(
        classification_any & classification_flags
    )
    if classification_all:
        classification_match = classification_all <= classification_flags and (
            classification_match or not classification_any
        )
    return classification_match


def _normalize_execution_disposition(value: object) -> dict[str, Any] | None:
    """Validate one exact, audit-safe generative-worker disposition."""

    if not isinstance(value, dict) or set(value) != {
        "mode",
        "eligible_worker_families",
    }:
        return None
    mode = value.get("mode")
    families = value.get("eligible_worker_families")
    if mode not in EXECUTION_DISPOSITION_MODES or not isinstance(families, list):
        return None
    if any(not isinstance(item, str) or item not in WORKER_FAMILIES for item in families):
        return None
    if len(families) != len(set(families)) or len(families) > 1:
        return None
    if mode == "codex_only" and families:
        return None
    if mode == "worker_support" and len(families) != 1:
        return None
    return {"mode": mode, "eligible_worker_families": list(families)}


def _resolve_execution_disposition(
    classification: dict[str, Any] | None,
    task_input: dict[str, Any],
    input_mode: str,
) -> tuple[dict[str, Any], str | None]:
    """Bind Task Gate disposition to the complete task input or return Codex-only."""

    if input_mode != "complete":
        return dict(CODEX_ONLY_EXECUTION_DISPOSITION), "TASK_INPUT_REQUIRED"
    task_value = task_input.get("execution_disposition")
    classified_value = (
        classification.get("execution_disposition")
        if isinstance(classification, dict)
        else None
    )
    if task_value is None or classified_value is None:
        return dict(CODEX_ONLY_EXECUTION_DISPOSITION), "EXECUTION_DISPOSITION_REQUIRED"
    task_disposition = _normalize_execution_disposition(task_value)
    classified_disposition = _normalize_execution_disposition(classified_value)
    if task_disposition is None or classified_disposition is None:
        return dict(CODEX_ONLY_EXECUTION_DISPOSITION), "EXECUTION_DISPOSITION_INVALID"
    if canonical_task_input_json(task_disposition) != canonical_task_input_json(
        classified_disposition
    ):
        return dict(CODEX_ONLY_EXECUTION_DISPOSITION), "EXECUTION_DISPOSITION_MISMATCH"
    return task_disposition, None


def _canonical_existing_workspace_root(value: object) -> str | None:
    """Return one exact canonical existing directory string or fail closed."""

    if not isinstance(value, str) or not value or value != value.strip():
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    try:
        canonical = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not canonical.is_dir() or value != str(canonical):
        return None
    return str(canonical)


_QUOTED_SEGMENT = re.compile(r'“([^”]*)”|"([^"\r\n]*)"')
_DIRECT_QUOTED_TARGET_CONTROL = re.compile(
    r"(?:\b(?:do\s+not|don'?t|dont|never)\s+"
    r"(?:use|invoke|select|call|rely\s+on|depend\s+on)|"
    r"\bwithout(?:\s+(?:using|relying\s+on|depending\s+on|invoking|selecting|calling))?|"
    r"\b(?:avoid|exclude|excluding|disable)(?:\s+(?:use\s+of|using))?|"
    r"\b(?:actually\s+|explicitly\s+)?(?:use|invoke|select|call)|"
    r"\b(?:route|delegate|send)(?:\s+(?:this|that|the)\s+"
    r"(?:task|request|work))?\s+to)"
    r"(?:\s+(?:either|both))?(?:\s+(?:the|any|this|that))?\s*$"
)


def _prompt_for_target_control(prompt: str, targets: Iterable[str]) -> str:
    """Mask reported quoted targets while preserving direct quoted controls."""

    text = str(prompt or "").lower().replace("’", "'")
    target_patterns: list[re.Pattern[str]] = []
    for target in targets:
        target_text = str(target or "").strip().lower()
        if not target_text:
            continue
        escaped = re.escape(target_text).replace(r"\ ", r"\s+")
        target_patterns.append(
            re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
        )
    for match in reversed(list(_QUOTED_SEGMENT.finditer(text))):
        content = match.group(1) if match.group(1) is not None else match.group(2)
        if content is None or not any(pattern.search(content) for pattern in target_patterns):
            continue
        before = text[: match.start()]
        replacement = (
            f" {content} "
            if _DIRECT_QUOTED_TARGET_CONTROL.search(before)
            else " " * (match.end() - match.start())
        )
        text = text[: match.start()] + replacement + text[match.end() :]
    return re.sub(r"\s+", " ", text).strip()


def _prompt_negates_any(prompt: str, targets: Iterable[str]) -> bool:
    target_values = tuple(str(target) for target in targets)
    text = _prompt_for_target_control(prompt, target_values)
    spans: set[tuple[int, int]] = set()
    for target in target_values:
        escaped = re.escape(target.lower()).replace(r"\ ", r"\s+")
        target_pattern = re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
        spans.update((match.start(), match.end()) for match in target_pattern.finditer(text))

    mentions: list[tuple[int, int]] = []
    for start, end in sorted(spans, key=lambda span: (span[0], -(span[1] - span[0]))):
        if any(start < prior_end and end > prior_start for prior_start, prior_end in mentions):
            continue
        mentions.append((start, end))
    mentions.sort()

    negative_prefixes = (
        r"\b(?:do\s+not|don'?t|dont|never)\s+"
        r"(?:use|invoke|select|call|rely\s+on|depend\s+on)"
        r"(?:\s+(?:either|both))?(?:\s+(?:the|any|this|that))?\s*$",
        r"\b(?:do\s+not|don'?t|dont|never)\s+"
        r"(?:route|delegate|send)"
        r"(?:\s+(?:(?:this|that|the)\s+(?:task|request|work|operation)|this|that|it))?"
        r"(?:\s+to)?(?:\s+(?:either|both))?(?:\s+(?:the|any))?\s*$",
        r"\b(?:i\s+)?(?:do\s+not|don'?t|dont)\s+want"
        r"(?:\s+to\s+(?:use|invoke|select|call))?"
        r"(?:\s+(?:either|both))?(?:\s+(?:the|any|this|that))?\s*$",
        r"\bwithout(?:\s+(?:using|relying\s+on|depending\s+on|invoking|selecting|calling))?"
        r"(?:\s+(?:either|both))?(?:\s+(?:the|any|this|that))?\s*$",
        r"\b(?:using\s+)?neither(?:\s+(?:the|any|this|that))?\s*$",
        r"\b(?:avoid|exclude|excluding|disable)"
        r"(?:\s+(?:use\s+of|using|invoking|selecting|calling|relying\s+on|depending\s+on))?"
        r"(?:\s+(?:either|both))?(?:\s+(?:the|any|this|that))?\s*$",
        r"\b(?:do\s+not|don'?t|dont|never)\s+"
        r"(?:use|invoke|select|call|rely\s+on|depend\s+on)"
        r"(?:\s+(?:either|both))?\s+[^;.!?]{1,160}?\s+"
        r"(?:,|,\s*(?:or|and|nor)|or|and|nor|plus|as\s+well\s+as|&|/)"
        r"(?:\s+(?:the|any|this|that))?\s*$",
        r"\bwithout(?:\s+(?:using|relying\s+on|depending\s+on|invoking|selecting|calling))?"
        r"(?:\s+(?:either|both))?\s+[^;.!?]{1,160}?\s+"
        r"(?:,|,\s*(?:or|and|nor)|or|and|nor|plus|as\s+well\s+as|&|/)"
        r"(?:\s+(?:the|any|this|that))?\s*$",
        r"\b(?:using\s+)?neither\s+[^;.!?]{1,160}?\s+"
        r"(?:nor|,|,\s*(?:nor|or|and)|plus|as\s+well\s+as|&|/)"
        r"(?:\s+(?:the|any|this|that))?\s*$",
        r"\b(?:avoid|exclude|excluding|disable)"
        r"(?:\s+(?:use\s+of|using|invoking|selecting|calling|relying\s+on|depending\s+on))?"
        r"(?:\s+(?:either|both))?\s+[^;.!?]{1,160}?\s+"
        r"(?:,|,\s*(?:or|and|nor)|or|and|nor|plus|as\s+well\s+as|&|/)"
        r"(?:\s+(?:the|any|this|that))?\s*$",
        r"\b(?:except|rather\s+than|instead\s+of)"
        r"(?:\s+(?:the|any|this|that))?\s*$",
        r"\b(?:no|not)\s+(?:any\s+|the\s+)?$",
    )
    affirmative_prefixes = (
        r"\b(?:actually\s+|explicitly\s+|instead\s+)?"
        r"(?:use|invoke|select|call)"
        r"(?:\s+(?:either|both))?(?:\s+(?:the|this|that))?\s*$",
        r"\b(?:actually\s+|explicitly\s+|instead\s+)?"
        r"(?:use|invoke|select|call)(?:\s+(?:either|both))?\s+"
        r"[^;.!?]{1,160}?\s+(?:and|or|plus|as\s+well\s+as|&|/)"
        r"(?:\s+(?:the|this|that))?\s*$",
        r"\b(?:actually\s+|explicitly\s+|instead\s+)?"
        r"(?:route|delegate|send)"
        r"(?:\s+(?:(?:this|that|the)\s+(?:task|request|work|operation)|this|that|it))?"
        r"(?:\s+to)?(?:\s+(?:the|any))?\s*$",
        r"\b(?:i\s+)?(?:want|prefer)\s+(?:to\s+)?"
        r"(?:use|invoke|select|call)(?:\s+(?:the|this|that))?\s*$",
    )
    excluded: bool | None = None
    for start, end in mentions:
        before = text[:start]
        after = text[end:]
        negative_suffix = re.search(
            r"^\s*(?:(?:,|and|or|nor|plus|as\s+well\s+as|&|/)\s+"
            r"[^;.!?]{1,160}?)?\s*"
            r"(?:(?:must|should|can|cannot|can't|is|are|was|were)?\s*"
            r"(?:not\s+(?:be\s+)?(?:used|allowed)|excluded|disabled|forbidden)|"
            r"(?:isn'?t|aren'?t|wasn'?t|weren'?t)\s+allowed)\b",
            after,
        )
        if negative_suffix or any(
            re.search(pattern, before) for pattern in negative_prefixes
        ):
            excluded = True
        elif any(re.search(pattern, before) for pattern in affirmative_prefixes):
            excluded = False
    return excluded is True


def _worker_exclusions(
    prompt: str,
    classification_flags: set[str],
) -> tuple[set[str], list[str]]:
    excluded_roles: set[str] = set()
    reasons: list[str] = []

    antigravity_flags = {
        "antigravity_excluded",
        "exclude_antigravity",
        "no_antigravity",
        "without_antigravity",
    }
    if antigravity_flags & classification_flags or _prompt_negates_any(
        prompt,
        (
            "antigravity",
            "antigravity-adapter",
            "google antigravity",
            "gemini-3.1-pro-high",
            "agy",
        ),
    ):
        excluded_roles.add("independent_challenger")
        reasons.append("ANTIGRAVITY_EXPLICITLY_EXCLUDED")

    terra_flags = {
        "terra_excluded",
        "exclude_terra",
        "no_terra",
        "without_terra",
    }
    if terra_flags & classification_flags or _prompt_negates_any(
        prompt,
        ("terra", "gpt-5.6-terra"),
    ):
        excluded_roles.add("read_heavy")
        reasons.append("TERRA_EXPLICITLY_EXCLUDED")

    local_flags = {
        "local_support_excluded",
        "local_models_excluded",
        "exclude_local_models",
        "no_local_models",
        "without_local_models",
        "local_fast_excluded",
        "local_coding_excluded",
        "local_critic_excluded",
    }
    if local_flags & classification_flags or _prompt_negates_any(
        prompt,
        (
            "local model",
            "local models",
            "local fast model",
            "local coding model",
            "local critic",
            "local agent stack",
            "local-agent-stack",
            "ollama",
            "qwen3.5:2b-q8_0",
            "qwen2.5-coder:7b-instruct-q6_k",
            "deepseek-r1:7b-qwen-distill-q4_k_m",
        ),
    ):
        excluded_roles.update({"fast", "coding", "critic"})
        reasons.append("LOCAL_SUPPORT_EXPLICITLY_EXCLUDED")

    return excluded_roles, reasons


def _worker_rule_matches(
    rule: dict[str, Any],
    prompt_lower: str,
    classification_flags: set[str],
) -> bool:
    match_any = rule.get("match_any", [])
    match_all = rule.get("match_all", [])
    prompt_match = bool(match_any) and any(
        _prompt_contains(prompt_lower, phrase) for phrase in match_any
    )
    if prompt_match and match_all:
        prompt_match = all(_prompt_contains(prompt_lower, phrase) for phrase in match_all)

    classification_match = _rule_classification_matches(rule, classification_flags)
    return prompt_match or classification_match


def _worker_capability_available(
    rule: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_alias: dict[str, list[dict[str, Any]]],
    policy: dict[str, Any],
    worker_runtime_bom_sha256: str = "",
) -> bool:
    upstream = str(rule.get("gateway_managed_upstream") or "").strip()
    if upstream and not _gateway_managed_upstream_configured(
        upstream, expected_bom_sha256=worker_runtime_bom_sha256
    ):
        return False
    requirements = rule.get("requires_any_capabilities", [])
    if not requirements:
        return True
    for reference in requirements:
        entry = _resolve_reference(reference, by_id, by_alias)
        if entry and not _is_suppressed(entry, policy) and _entry_hash_current(entry):
            return True
    return False


def _worker_runtime_binding(
    server_id: str, *, expected_bom_sha256: str
) -> dict[str, Any] | None:
    """Resolve one exact external-worker identity from the generation-bound BOM."""

    expected = str(expected_bom_sha256 or "").lower()
    if not expected:
        return {}
    try:
        value, _ = _load_worker_runtime_bom(expected_sha256=expected)
    except CapabilityDataError:
        return None
    runtimes = value["runtimes"]
    binding = runtimes.get(server_id) if isinstance(runtimes, dict) else None
    if not isinstance(binding, dict):
        return None
    return binding


def _worker_projection_sha256(value: dict[str, Any]) -> str:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    return hashlib.sha256(payload).hexdigest()


def _worker_server_projection(
    config: dict[str, Any], server_id: str
) -> tuple[dict[str, Any], Path]:
    servers = config.get("mcp_servers")
    server = servers.get(server_id) if isinstance(servers, dict) else None
    spec = WORKER_SERVER_SPECS.get(server_id)
    if not isinstance(server, dict) or spec is None:
        raise CapabilityDataError("configured worker is unavailable")
    if (
        set(server) - WORKER_EXECUTION_KEYS - WORKER_PRESENTATION_KEYS
        or not WORKER_EXECUTION_KEYS.issubset(server)
        or server.get("enabled") is not False
        or server.get("gateway_managed") is not True
    ):
        raise CapabilityDataError("configured worker execution stanza is invalid")
    env = server.get("env")
    if not isinstance(env, dict) or set(env) != spec["env"]:
        raise CapabilityDataError("configured worker environment is invalid")
    if any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(ord(character) < 32 for character in value)
        for key, value in env.items()
    ):
        raise CapabilityDataError("configured worker environment is invalid")
    for timeout_key in ("startup_timeout_sec", "tool_timeout_sec"):
        timeout = server.get(timeout_key)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < float(timeout) <= 3600
        ):
            raise CapabilityDataError("configured worker timeout is invalid")
    command = Path(str(server.get("command") or ""))
    cwd = Path(str(server.get("cwd") or ""))
    if not command.is_absolute() or not cwd.is_absolute():
        raise CapabilityDataError("configured worker paths are invalid")
    try:
        command_resolved = command.resolve(strict=True)
        root = cwd.resolve(strict=True)
        root_key = (
            "LOCAL_AGENT_STACK_ROOT"
            if server_id == "local-agent-stack"
            else "ANTIGRAVITY_ADAPTER_ROOT"
        )
        env_root = Path(env[root_key]).resolve(strict=True)
        pycache_prefix = root.joinpath(
            *str(spec["pycache_relative_path"]).split("/")
        ).resolve(strict=True)
        pycache_is_empty = not any(pycache_prefix.iterdir())
        if server_id == "antigravity-adapter":
            agy = Path(env["ANTIGRAVITY_AGY_EXECUTABLE"]).resolve(strict=True)
            if not agy.is_file():
                raise OSError("Antigravity executable is not a file")
    except OSError as exc:
        raise CapabilityDataError("configured worker path is unavailable") from exc
    if (
        not command_resolved.is_file()
        or not root.is_dir()
        or not command_resolved.is_relative_to(root)
        or env_root != root
        or server.get("args")
        != [
            "-I",
            "-B",
            "-X",
            f"pycache_prefix={pycache_prefix}",
            "-m",
            str(spec["module"]),
        ]
        or _worker_is_link_or_reparse(pycache_prefix)
        or not pycache_prefix.is_dir()
        or not pycache_is_empty
    ):
        raise CapabilityDataError("configured worker path binding is invalid")
    return {key: server[key] for key in sorted(WORKER_EXECUTION_KEYS)}, root


def _worker_stable_bytes(path: Path) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
        if (
            path.is_symlink()
            or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
            or not resolved.is_file()
        ):
            raise OSError("not a regular file")
        before = resolved.stat()
        first = resolved.read_bytes()
        second = resolved.read_bytes()
        after = resolved.stat()
    except OSError as exc:
        raise CapabilityDataError("worker artifact is unavailable") from exc
    if (
        first != second
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise CapabilityDataError("worker artifact changed during verification")
    return first


def _worker_is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def _worker_source_inventory_sha256(root: Path, paths: list[Path]) -> str:
    rows: list[dict[str, str]] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise CapabilityDataError("worker source identity is incomplete") from exc
        if (
            resolved in seen
            or not resolved.is_relative_to(root)
            or not resolved.is_file()
        ):
            raise CapabilityDataError("worker source identity escaped its root")
        seen.add(resolved)
        rows.append(
            {
                "path": resolved.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(_worker_stable_bytes(resolved)).hexdigest(),
            }
        )
    canonical = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _worker_dependency_path(value: Any, root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise CapabilityDataError("worker dependency path is invalid")
    expanded = value.replace("${RUNTIME_ROOT}", str(root))
    if "${LOCALAPPDATA}" in expanded:
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if not local_app_data:
            raise CapabilityDataError("worker dependency environment is unavailable")
        expanded = expanded.replace("${LOCALAPPDATA}", local_app_data)
    path = Path(expanded)
    return path if path.is_absolute() else root / path


def _same_worker_path(left: Path | str, right: Path | str) -> bool:
    try:
        left_value = os.path.normcase(str(Path(left).resolve(strict=True)))
        right_value = os.path.normcase(str(Path(right).resolve(strict=True)))
    except OSError:
        return False
    return left_value == right_value


def _worker_compact_json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _probe_worker_pth_import_origins(
    command: Path, modules: list[str], pycache_prefix: Path
) -> dict[str, str]:
    script = (
        "import importlib.util,json,sys;"
        "print(json.dumps({n:getattr(importlib.util.find_spec(n),'origin',None) "
        "for n in sys.argv[1:]},sort_keys=True,separators=(',',':')))"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [
                str(command),
                "-I",
                "-B",
                "-X",
                f"pycache_prefix={pycache_prefix}",
                "-c",
                script,
                *modules,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5.0,
            check=False,
            creationflags=creationflags,
        )
        value = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise CapabilityDataError("worker .pth import probe failed") from exc
    if (
        completed.returncode != 0
        or completed.stderr
        or not isinstance(value, dict)
        or set(value) != set(modules)
        or any(not isinstance(item, str) or not item for item in value.values())
    ):
        raise CapabilityDataError("worker .pth import probe is invalid")
    return value


def _worker_installed_distributions_identity(
    site_packages: Path,
    venv_root: Path,
    source_root: Path,
    command: Path,
    pycache_prefix: Path,
) -> dict[str, Any]:
    try:
        resolved_site = site_packages.resolve(strict=True)
        resolved_venv = venv_root.resolve(strict=True)
        resolved_source = source_root.resolve(strict=True)
    except OSError as exc:
        raise CapabilityDataError("worker site-packages is unavailable") from exc
    if (
        _worker_is_link_or_reparse(site_packages)
        or not resolved_site.is_dir()
        or not resolved_site.is_relative_to(resolved_venv)
        or os.path.normcase(str(Path(os.path.abspath(site_packages))))
        != os.path.normcase(str(resolved_site))
    ):
        raise CapabilityDataError("worker site-packages path is invalid")
    distributions: list[dict[str, Any]] = []
    names: set[str] = set()
    all_owned_paths: set[str] = set()
    try:
        discovered = list(importlib.metadata.distributions(path=[str(resolved_site)]))
    except (OSError, ValueError) as exc:
        raise CapabilityDataError("worker distribution inventory is unavailable") from exc
    for distribution in discovered:
        name_value = distribution.metadata.get("Name")
        name = (
            re.sub(r"[-_.]+", "-", name_value).lower()
            if isinstance(name_value, str)
            else ""
        )
        version = distribution.version
        distribution_input = Path(str(getattr(distribution, "_path", "")))
        try:
            distribution_path = distribution_input.resolve(strict=True)
        except OSError as exc:
            raise CapabilityDataError("worker distribution metadata is unavailable") from exc
        if (
            not name
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", name) is None
            or name in names
            or not isinstance(version, str)
            or not version
            or _worker_is_link_or_reparse(distribution_input)
            or not distribution_path.is_dir()
            or distribution_path.parent != resolved_site
            or distribution_path.suffix.casefold() != ".dist-info"
            or os.path.normcase(str(Path(os.path.abspath(distribution_input))))
            != os.path.normcase(str(distribution_path))
        ):
            raise CapabilityDataError("worker distribution identity is invalid")
        names.add(name)
        record_path = distribution_path / "RECORD"
        record_raw = _worker_stable_bytes(record_path)
        try:
            record_rows = csv.reader(
                io.StringIO(record_raw.decode("utf-8"), newline="")
            )
            owned_paths: set[str] = set()
            record_row_count = 0
            for row in record_rows:
                if len(row) != 3 or not row[0]:
                    raise CapabilityDataError(
                        "worker distribution RECORD row is invalid"
                    )
                located = Path(os.path.abspath(str(distribution.locate_file(row[0]))))
                relative = located.relative_to(resolved_venv).as_posix()
                key = relative.casefold()
                if key in owned_paths:
                    raise CapabilityDataError(
                        "worker distribution RECORD contains a duplicate path"
                    )
                owned_paths.add(key)
                all_owned_paths.add(key)
                record_row_count += 1
        except (UnicodeDecodeError, csv.Error, ValueError) as exc:
            raise CapabilityDataError("worker distribution RECORD is invalid") from exc
        distributions.append(
            {
                "name": name,
                "version": version,
                "record_path": record_path.relative_to(resolved_venv).as_posix(),
                "record_sha256": hashlib.sha256(record_raw).hexdigest(),
                "record_row_count": record_row_count,
            }
        )
    distributions.sort(key=lambda row: row["name"])
    if not distributions:
        raise CapabilityDataError("worker distribution inventory is empty")
    pth_rows: list[dict[str, Any]] = []
    pth_import_modules: set[str] = set()
    for pth_input in resolved_site.glob("*.pth"):
        try:
            pth = pth_input.resolve(strict=True)
        except OSError as exc:
            raise CapabilityDataError("worker .pth inventory is unavailable") from exc
        relative = pth.relative_to(resolved_venv).as_posix()
        record_owned = relative.casefold() in all_owned_paths
        if (
            _worker_is_link_or_reparse(pth_input)
            or os.path.normcase(str(Path(os.path.abspath(pth_input))))
            != os.path.normcase(str(pth))
            or not pth.is_file()
            or pth.parent != resolved_site
            or (not record_owned and pth.name.casefold() != "_virtualenv.pth")
        ):
            raise CapabilityDataError("worker .pth inventory is unowned or ambiguous")
        pth_raw = _worker_stable_bytes(pth)
        try:
            pth_text = pth_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CapabilityDataError("worker .pth file is not UTF-8") from exc
        for line in pth_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("import "):
                try:
                    parsed = ast.parse(stripped, mode="exec")
                except SyntaxError as exc:
                    raise CapabilityDataError("worker .pth import row is invalid") from exc
                for node in parsed.body:
                    if not isinstance(node, ast.Import):
                        raise CapabilityDataError(
                            "worker .pth executable row is not a plain import"
                        )
                    pth_import_modules.update(alias.name for alias in node.names)
                continue
            target_input = Path(stripped)
            try:
                target = (
                    target_input
                    if target_input.is_absolute()
                    else resolved_site / target_input
                ).resolve(strict=True)
            except OSError as exc:
                raise CapabilityDataError("worker .pth target is unavailable") from exc
            if not target.is_dir() or (
                not target.is_relative_to(resolved_site)
                and not _same_worker_path(target, resolved_source)
            ):
                raise CapabilityDataError("worker .pth target escaped its authority")
        pth_rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(pth_raw).hexdigest(),
                "record_owned": record_owned,
            }
        )
    pth_rows.sort(key=lambda row: row["path"].casefold())
    if not pth_rows or "_virtualenv" not in pth_import_modules:
        raise CapabilityDataError("worker .pth startup closure is incomplete")
    modules = sorted(pth_import_modules)
    origins = _probe_worker_pth_import_origins(command, modules, pycache_prefix)
    pth_import_rows: list[dict[str, str]] = []
    for module_name in modules:
        try:
            origin = Path(origins[module_name]).resolve(strict=True)
            relative_origin = origin.relative_to(resolved_venv).as_posix()
        except (OSError, ValueError) as exc:
            raise CapabilityDataError("worker .pth import origin escaped its venv") from exc
        pth_import_rows.append(
            {
                "module": module_name,
                "origin": relative_origin,
                "sha256": hashlib.sha256(_worker_stable_bytes(origin)).hexdigest(),
            }
        )
    return {
        "installed_distributions_count": len(distributions),
        "installed_distributions_sha256": _worker_compact_json_sha256(distributions),
        "pth_files_count": len(pth_rows),
        "pth_files_sha256": _worker_compact_json_sha256(pth_rows),
        "pth_imports_count": len(pth_import_rows),
        "pth_imports_sha256": _worker_compact_json_sha256(pth_import_rows),
    }


def _worker_base_runtime_tree_identity(root: Path) -> dict[str, Any]:
    try:
        resolved_root = root.resolve(strict=True)
        if _worker_is_link_or_reparse(root) or not resolved_root.is_dir():
            raise OSError("base runtime root is invalid")
        paths = list(resolved_root.rglob("*"))
    except OSError as exc:
        raise CapabilityDataError("worker base runtime tree is unavailable") from exc
    rows: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for path in paths:
        relative = path.relative_to(resolved_root)
        key = relative.as_posix().casefold()
        if key in seen_paths:
            raise CapabilityDataError(
                "worker base runtime tree has a case-insensitive duplicate"
            )
        seen_paths.add(key)
        if _worker_is_link_or_reparse(path):
            raise CapabilityDataError("worker base runtime tree contains a link")
        if "__pycache__" in relative.parts:
            continue
        if path.is_file():
            rows.append(
                {
                    "path": relative.as_posix(),
                    "sha256": hashlib.sha256(_worker_stable_bytes(path)).hexdigest(),
                }
            )
        elif not path.is_dir():
            raise CapabilityDataError(
                "worker base runtime tree contains an unsupported entry"
            )
    rows.sort(key=lambda row: row["path"].casefold())
    if not rows:
        raise CapabilityDataError("worker base runtime tree is empty")
    return {
        "base_runtime_tree_file_count": len(rows),
        "base_runtime_tree_sha256": _worker_compact_json_sha256(rows),
    }


def _worker_site_packages_tree_identity(root: Path) -> dict[str, Any]:
    """Bind every non-cache byte under one worker's effective site-packages."""
    try:
        resolved_root = root.resolve(strict=True)
        if (
            _worker_is_link_or_reparse(root)
            or not resolved_root.is_dir()
            or os.path.normcase(str(Path(os.path.abspath(root))))
            != os.path.normcase(str(resolved_root))
        ):
            raise OSError("site-packages root is invalid")
    except OSError as exc:
        raise CapabilityDataError("worker site-packages tree is unavailable") from exc

    rows: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    pending = [resolved_root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda path: path.name.casefold(),
            )
        except OSError as exc:
            raise CapabilityDataError(
                "worker site-packages tree is unreadable"
            ) from exc
        for path in children:
            try:
                relative = path.relative_to(resolved_root)
            except ValueError as exc:
                raise CapabilityDataError(
                    "worker site-packages entry escaped its root"
                ) from exc
            key = relative.as_posix().casefold()
            if key in seen_paths:
                raise CapabilityDataError(
                    "worker site-packages tree has a case-insensitive duplicate"
                )
            seen_paths.add(key)
            if _worker_is_link_or_reparse(path):
                raise CapabilityDataError(
                    "worker site-packages tree contains a link or reparse point"
                )
            if path.is_dir():
                if relative.name != "__pycache__":
                    pending.append(path)
                continue
            if not path.is_file():
                raise CapabilityDataError(
                    "worker site-packages tree contains an unsupported entry"
                )
            if "__pycache__" in relative.parts:
                continue
            rows.append(
                {
                    "path": relative.as_posix(),
                    "sha256": hashlib.sha256(
                        _worker_stable_bytes(path)
                    ).hexdigest(),
                }
            )
    rows.sort(key=lambda row: row["path"].casefold())
    if not rows:
        raise CapabilityDataError("worker site-packages tree is empty")
    return {
        "site_packages_tree_file_count": len(rows),
        "site_packages_tree_sha256": _worker_compact_json_sha256(rows),
    }


def _parse_worker_pyvenv_config(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CapabilityDataError("worker pyvenv.cfg is not UTF-8") from exc
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        normalized_key = key.strip().casefold()
        normalized_value = value.strip()
        if (
            not separator
            or not normalized_key
            or not normalized_value
            or normalized_key in values
        ):
            raise CapabilityDataError("worker pyvenv.cfg is ambiguous")
        values[normalized_key] = normalized_value
    if (
        not values.get("home")
        or values.get("implementation") != "CPython"
        or not values.get("version_info")
        or values.get("include-system-site-packages", "").casefold() != "false"
    ):
        raise CapabilityDataError("worker pyvenv.cfg isolation is invalid")
    return values


def _probe_worker_python_execution(
    command: Path, package: str, pycache_prefix: Path
) -> dict[str, Any]:
    script = (
        "import importlib.util,json,platform,site,sys;"
        "s=importlib.util.find_spec(sys.argv[1]);"
        "print(json.dumps({'executable':sys.executable,'base_prefix':sys.base_prefix,"
        "'version':platform.python_version(),'origin':getattr(s,'origin',None),"
        "'locations':list(getattr(s,'submodule_search_locations',[]) or []),"
        "'isolated':sys.flags.isolated,'no_user_site':sys.flags.no_user_site,"
        "'user_site_enabled':site.ENABLE_USER_SITE,"
        "'dont_write_bytecode':sys.dont_write_bytecode,"
        "'pycache_prefix':sys.pycache_prefix},"
        "sort_keys=True,separators=(',',':')))"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [
                str(command),
                "-I",
                "-B",
                "-X",
                f"pycache_prefix={pycache_prefix}",
                "-c",
                script,
                package,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5.0,
            check=False,
            creationflags=creationflags,
        )
        value = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise CapabilityDataError("worker Python import probe failed") from exc
    if (
        completed.returncode != 0
        or completed.stderr
        or not isinstance(value, dict)
        or set(value)
        != {
            "base_prefix",
            "dont_write_bytecode",
            "executable",
            "isolated",
            "locations",
            "no_user_site",
            "origin",
            "pycache_prefix",
            "user_site_enabled",
            "version",
        }
        or not isinstance(value.get("locations"), list)
    ):
        raise CapabilityDataError("worker Python import probe is invalid")
    return value


def _validate_worker_python_execution_closure(
    server_id: str,
    identity: dict[str, Any],
    root: Path,
    command: Path,
) -> dict[str, Any]:
    closure = identity.get("python_execution_closure")
    package = WORKER_IMPORT_PACKAGES.get(server_id)
    if (
        not isinstance(closure, dict)
        or set(closure) != PYTHON_EXECUTION_CLOSURE_KEYS
        or closure.get("schema_version") != PYTHON_EXECUTION_CLOSURE_SCHEMA
        or package is None
        or closure.get("import_package") != package
        or closure.get("include_system_site_packages") is not False
        or closure.get("isolated_mode") is not True
        or closure.get("user_site_enabled") is not False
        or closure.get("dont_write_bytecode") is not True
        or closure.get("pycache_prefix_empty") is not True
        or not isinstance(closure.get("pycache_prefix_path"), str)
        or not Path(closure["pycache_prefix_path"]).is_absolute()
        or closure.get("forbidden_environment_variables")
        != PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
        or closure.get("child_environment_policy_id")
        != WORKER_CHILD_ENVIRONMENT_POLICY_ID
        or SEMVER_PATTERN.fullmatch(
            str(closure.get("base_interpreter_version") or "")
        )
        is None
        or any(
            SHA256_PATTERN.fullmatch(str(closure.get(key) or "")) is None
            for key in (
                "venv_python_sha256",
                "pyvenv_config_sha256",
                "base_interpreter_sha256",
                "editable_pth_sha256",
                "base_runtime_tree_sha256",
                "site_packages_tree_sha256",
                "installed_distributions_sha256",
                "pth_files_sha256",
                "pth_imports_sha256",
            )
        )
        or any(
            isinstance(closure.get(key), bool)
            or not isinstance(closure.get(key), int)
            or closure[key] < 1
            for key in (
                "base_runtime_tree_file_count",
                "site_packages_tree_file_count",
                "installed_distributions_count",
                "pth_files_count",
                "pth_imports_count",
            )
        )
        or any(
            not isinstance(closure.get(key), str)
            or not Path(closure[key]).is_absolute()
            for key in ("base_runtime_tree_path", "site_packages_path")
        )
    ):
        raise CapabilityDataError("worker Python execution closure is invalid")

    command = command.resolve(strict=True)
    venv_root = command.parent.parent
    expected_venv_root = (root / ".venv").resolve(strict=True)
    pyvenv_path = (venv_root / "pyvenv.cfg").resolve(strict=True)
    spec = WORKER_SERVER_SPECS[server_id]
    expected_pycache_prefix = root.joinpath(
        *str(spec["pycache_relative_path"]).split("/")
    ).resolve(strict=True)
    try:
        pycache_is_empty = not any(expected_pycache_prefix.iterdir())
    except OSError as exc:
        raise CapabilityDataError("worker Python cache prefix is unavailable") from exc
    if (
        command.name.casefold() != "python.exe"
        or command.parent.name.casefold() != "scripts"
        or not _same_worker_path(venv_root, expected_venv_root)
        or not _same_worker_path(closure["venv_python_path"], command)
        or not _same_worker_path(closure["pyvenv_config_path"], pyvenv_path)
        or not _same_worker_path(
            closure["pycache_prefix_path"], expected_pycache_prefix
        )
        or _worker_is_link_or_reparse(expected_pycache_prefix)
        or not expected_pycache_prefix.is_dir()
        or not pycache_is_empty
    ):
        raise CapabilityDataError("worker Python virtual environment path is invalid")
    command_raw = _worker_stable_bytes(command)
    pyvenv_raw = _worker_stable_bytes(pyvenv_path)
    pyvenv = _parse_worker_pyvenv_config(pyvenv_raw)
    expected_version_info = ".".join(
        str(closure["base_interpreter_version"]).split(".")[:2]
    )
    if pyvenv.get("version_info") != expected_version_info:
        raise CapabilityDataError("worker pyvenv.cfg version is invalid")
    try:
        base_interpreter = (Path(pyvenv["home"]) / "python.exe").resolve(strict=True)
        base_runtime_root = Path(str(closure["base_runtime_tree_path"])).resolve(
            strict=True
        )
    except OSError as exc:
        raise CapabilityDataError("worker base interpreter is unavailable") from exc
    if (
        not base_interpreter.is_file()
        or not _same_worker_path(
            closure["base_interpreter_path"], base_interpreter
        )
        or not _same_worker_path(base_runtime_root, base_interpreter.parent)
        or hashlib.sha256(command_raw).hexdigest()
        != closure["venv_python_sha256"]
        or hashlib.sha256(pyvenv_raw).hexdigest()
        != closure["pyvenv_config_sha256"]
        or hashlib.sha256(_worker_stable_bytes(base_interpreter)).hexdigest()
        != closure["base_interpreter_sha256"]
    ):
        raise CapabilityDataError("worker Python interpreter bytes do not match identity")

    site_packages = (venv_root / "Lib" / "site-packages").resolve(strict=True)
    editable_path = Path(str(closure["editable_pth_path"])).resolve(strict=True)
    source_root = (root / "src").resolve(strict=True)
    import_origin = (source_root / package / "__init__.py").resolve(strict=True)
    if (
        editable_path.is_symlink()
        or not editable_path.is_file()
        or editable_path.parent != site_packages
        or not editable_path.name.casefold().startswith("__editable__.")
        or editable_path.suffix.casefold() != ".pth"
        or not _same_worker_path(closure["editable_source_root"], source_root)
        or not _same_worker_path(closure["import_origin"], import_origin)
        or not _same_worker_path(closure["site_packages_path"], site_packages)
        or not import_origin.is_file()
    ):
        raise CapabilityDataError("worker editable import path is invalid")
    editable_raw = _worker_stable_bytes(editable_path)
    try:
        editable_lines = [
            line.strip()
            for line in editable_raw.decode("utf-8-sig").splitlines()
            if line.strip()
        ]
    except UnicodeDecodeError as exc:
        raise CapabilityDataError("worker editable .pth is not UTF-8") from exc
    if (
        len(editable_lines) != 1
        or editable_lines[0].casefold().startswith("import ")
        or not Path(editable_lines[0]).is_absolute()
        or not _same_worker_path(editable_lines[0], source_root)
        or hashlib.sha256(editable_raw).hexdigest()
        != closure["editable_pth_sha256"]
    ):
        raise CapabilityDataError("worker editable .pth target does not match identity")

    distribution_identity = _worker_installed_distributions_identity(
        site_packages,
        venv_root,
        source_root,
        command,
        expected_pycache_prefix,
    )
    base_runtime_identity = _worker_base_runtime_tree_identity(base_runtime_root)
    site_packages_tree_identity = _worker_site_packages_tree_identity(site_packages)
    if any(
        closure.get(key) != value
        for key, value in {
            **distribution_identity,
            **base_runtime_identity,
            **site_packages_tree_identity,
        }.items()
    ):
        raise CapabilityDataError(
            "worker Python dependency closure does not match current bytes"
        )

    probe = _probe_worker_python_execution(
        command, package, expected_pycache_prefix
    )
    locations = probe.get("locations")
    package_root = (source_root / package).resolve(strict=True)
    if (
        not _same_worker_path(probe.get("executable", ""), command)
        or not _same_worker_path(
            Path(str(probe.get("base_prefix") or "")) / "python.exe",
            base_interpreter,
        )
        or probe.get("version") != closure["base_interpreter_version"]
        or probe.get("isolated") != 1
        or probe.get("no_user_site") != 1
        or probe.get("user_site_enabled") is not False
        or probe.get("dont_write_bytecode") is not True
        or not _same_worker_path(
            probe.get("pycache_prefix", ""), expected_pycache_prefix
        )
        or not _same_worker_path(probe.get("origin", ""), import_origin)
        or not isinstance(locations, list)
        or len(locations) != 1
        or not _same_worker_path(locations[0], package_root)
    ):
        raise CapabilityDataError("worker Python import resolution does not match identity")
    return dict(closure)


def _antigravity_worker_source_paths(root: Path) -> list[Path]:
    package_root = root / "src" / "antigravity_adapter"
    try:
        resolved_package = package_root.resolve(strict=True)
        if package_root.is_symlink() or not resolved_package.is_dir():
            raise OSError("package root is invalid")
        entries = sorted(
            package_root.rglob("*"),
            key=lambda path: path.relative_to(package_root).as_posix().casefold(),
        )
    except OSError as exc:
        raise CapabilityDataError("Antigravity source inventory is unavailable") from exc
    seen: set[str] = set()
    sources: list[Path] = []
    for path in entries:
        relative_path = path.relative_to(package_root)
        if "__pycache__" in relative_path.parts:
            continue
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise CapabilityDataError("Antigravity source inventory contains a link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise CapabilityDataError(
                "Antigravity source inventory contains a non-regular entry"
            )
        relative = relative_path.as_posix().casefold()
        if relative in seen:
            raise CapabilityDataError("Antigravity source inventory is ambiguous")
        seen.add(relative)
        sources.append(path)
    if not sources or not {
        "__init__.py",
        "server.py",
        "source_integrity.py",
    }.issubset(seen):
        raise CapabilityDataError("Antigravity source inventory is incomplete")
    return sources


def _validate_las_worker_artifacts(root: Path, identity: dict[str, Any]) -> None:
    try:
        paths = [
            root / "pyproject.toml",
            root / "uv.lock",
            root / "runtime-dependencies.lock.json",
            root / "vendor" / "versions.json",
        ]
        paths.extend(sorted((root / "config").glob("*.json")))
        paths.extend(sorted((root / "config").glob("*.yaml")))
        paths.extend(sorted((root / "config" / "schemas").glob("*.json")))
        paths.extend(
            sorted(
                path
                for path in (root / "scripts").iterdir()
                if path.is_file() and path.suffix.lower() in {".ps1", ".py", ".vbs"}
            )
        )
        paths.extend(sorted((root / "src" / "local_agent_stack").rglob("*.py")))
    except OSError as exc:
        raise CapabilityDataError("local-agent-stack source inventory is unavailable") from exc
    if _worker_source_inventory_sha256(root, paths) != identity.get("source_sha256"):
        raise CapabilityDataError("local-agent-stack source identity mismatch")
    try:
        lock = json.loads(
            _worker_stable_bytes(root / "runtime-dependencies.lock.json").decode(
                "utf-8-sig"
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityDataError("LAS dependency lock is invalid") from exc
    if (
        not isinstance(lock, dict)
        or set(lock)
        != {
            "agent_memory",
            "executables",
            "files",
            "hermes",
            "ollama",
            "python_execution_closure",
            "release_id",
            "scheduler_contract",
            "schema_version",
            "startup_receipts",
        }
        or lock.get("schema_version")
        != "local-agent-stack-runtime-dependencies-v2"
        or lock.get("release_id") != identity.get("release_id")
        or lock.get("python_execution_closure")
        != identity.get("python_execution_closure")
    ):
        raise CapabilityDataError("LAS dependency lock is invalid")
    hermes = lock.get("hermes") if isinstance(lock, dict) else None
    identity_hermes = identity["nested_dependencies"]["hermes"]
    if not isinstance(hermes, dict) or any(
        hermes.get(key) != identity_hermes.get(key)
        for key in ("distribution_version", "overlay_id", "api_source_sha256")
    ):
        raise CapabilityDataError("LAS Hermes identity mismatch")
    for path_key, hash_key in (
        ("api_source_path", "api_source_sha256"),
        ("distribution_metadata_path", "distribution_metadata_sha256"),
    ):
        actual = hashlib.sha256(
            _worker_stable_bytes(_worker_dependency_path(hermes.get(path_key), root))
        ).hexdigest()
        if actual != hermes.get(hash_key):
            raise CapabilityDataError("LAS Hermes artifact mismatch")


def _validate_antigravity_worker_artifacts(
    root: Path, identity: dict[str, Any], projection: dict[str, Any]
) -> None:
    source_paths = _antigravity_worker_source_paths(root)
    source_paths.extend([root / "dependency-lock.json", root / "pyproject.toml"])
    if _worker_source_inventory_sha256(root, source_paths) != identity.get(
        "source_sha256"
    ):
        raise CapabilityDataError("Antigravity source identity mismatch")
    lock_raw = _worker_stable_bytes(root / "dependency-lock.json")
    try:
        lock = json.loads(lock_raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityDataError("Antigravity dependency lock is invalid") from exc
    agy = lock.get("agy") if isinstance(lock, dict) else None
    if (
        not isinstance(lock, dict)
        or set(lock) != {"schema_version", "python_execution_closure", "agy"}
        or lock.get("schema_version") != "antigravity-adapter-dependency-lock-v2"
        or lock.get("python_execution_closure")
        != identity.get("python_execution_closure")
        or not isinstance(agy, dict)
        or set(agy) != {"version", "executable_sha256", "model_efforts"}
        or not isinstance(agy.get("model_efforts"), dict)
        or not agy["model_efforts"]
    ):
        raise CapabilityDataError("Antigravity dependency lock is invalid")
    agy_raw = _worker_stable_bytes(
        Path(projection["env"]["ANTIGRAVITY_AGY_EXECUTABLE"])
    )
    model_hash = hashlib.sha256(
        json.dumps(
            dict(sorted(agy["model_efforts"].items())),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if (
        identity.get("dependency_lock_sha256") != hashlib.sha256(lock_raw).hexdigest()
        or identity.get("agy_version") != agy.get("version")
        or identity.get("agy_executable_sha256") != hashlib.sha256(agy_raw).hexdigest()
        or identity.get("agy_executable_sha256") != agy.get("executable_sha256")
        or identity.get("agy_model_contract_sha256") != model_hash
    ):
        raise CapabilityDataError("Antigravity dependency identity mismatch")


def _validate_worker_family_identity_document(
    server_id: str,
    identity: dict[str, Any],
) -> dict[str, Any]:
    compatibility = identity.get("catalogue_router_compatibility")
    if server_id == "local-agent-stack":
        if set(identity) != {
            "schema_version",
            "component",
            "runtime_version",
            "release_id",
            "catalogue_router_compatibility",
            "nested_dependencies",
            "python_execution_closure",
            "source_sha256",
        } or not isinstance(compatibility, dict):
            raise CapabilityDataError("local-agent-stack identity shape is invalid")
        if set(compatibility) != {
            "route_schema_version",
            "route_registry_schema_version",
            "authority_pointer_schema_version",
            "manifest_schema_versions",
        }:
            raise CapabilityDataError("local-agent-stack compatibility is invalid")
        nested = identity.get("nested_dependencies")
        hermes = nested.get("hermes") if isinstance(nested, dict) else None
        if not isinstance(nested, dict) or set(nested) != {"hermes"} or not isinstance(hermes, dict) or set(hermes) != {
            "distribution_version",
            "overlay_id",
            "api_source_sha256",
        }:
            raise CapabilityDataError("local-agent-stack Hermes identity is invalid")
        if (
            identity.get("schema_version") != "local-agent-stack-runtime-identity-v2"
            or identity.get("component") != server_id
            or SEMVER_PATTERN.fullmatch(str(identity.get("runtime_version") or ""))
            is None
            or SEMVER_PATTERN.fullmatch(
                str(hermes.get("distribution_version") or "")
            )
            is None
            or not isinstance(hermes.get("overlay_id"), str)
            or not hermes["overlay_id"]
            or SHA256_PATTERN.fullmatch(
                str(hermes.get("api_source_sha256") or "")
            )
            is None
        ):
            raise CapabilityDataError("local-agent-stack identity values are invalid")
        manifests = compatibility.get("manifest_schema_versions")
    elif server_id == "antigravity-adapter":
        if set(identity) != {
            "agy_executable_sha256",
            "agy_model_contract_sha256",
            "agy_version",
            "authority_pointer_schema_version",
            "component",
            "dependency_lock_schema_version",
            "dependency_lock_sha256",
            "release_id",
            "route_registry_schema_version",
            "route_schema_version",
            "runtime_version",
            "schema_version",
            "python_execution_closure",
            "source_sha256",
            "supported_manifest_schema_versions",
        }:
            raise CapabilityDataError("Antigravity adapter identity shape is invalid")
        compatibility = identity
        if (
            identity.get("schema_version")
            != "antigravity-adapter-runtime-identity-v3"
            or identity.get("component") != server_id
            or SEMVER_PATTERN.fullmatch(str(identity.get("runtime_version") or ""))
            is None
            or SEMVER_PATTERN.fullmatch(str(identity.get("agy_version") or ""))
            is None
            or identity.get("dependency_lock_schema_version")
            != "antigravity-adapter-dependency-lock-v2"
            or any(
                SHA256_PATTERN.fullmatch(str(identity.get(key) or "")) is None
                for key in (
                    "agy_executable_sha256",
                    "agy_model_contract_sha256",
                    "dependency_lock_sha256",
                )
            )
        ):
            raise CapabilityDataError("Antigravity adapter identity values are invalid")
        manifests = identity.get("supported_manifest_schema_versions")
    else:
        raise CapabilityDataError("worker identity family is unsupported")
    release_id = identity.get("release_id")
    if (
        SHA256_PATTERN.fullmatch(str(identity.get("source_sha256") or "")) is None
        or not isinstance(release_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", release_id) is None
        or not release_id.startswith(server_id + "-")
        or compatibility.get("route_schema_version") != "3.0"
        or compatibility.get("route_registry_schema_version")
        != ROUTE_REGISTRY_SCHEMA_VERSION
        or compatibility.get("authority_pointer_schema_version")
        != "capability-authority-pointer-v1"
        or not isinstance(manifests, list)
        or len(manifests) != len(set(manifests))
        or "1.3" not in manifests
        or any(not isinstance(item, str) or not item for item in manifests)
    ):
        raise CapabilityDataError("worker runtime compatibility is invalid")
    return compatibility


def _validate_worker_family_identity(
    server_id: str,
    identity: dict[str, Any],
    root: Path,
    projection: dict[str, Any],
) -> dict[str, Any]:
    compatibility = _validate_worker_family_identity_document(server_id, identity)
    if server_id == "local-agent-stack":
        _validate_las_worker_artifacts(root, identity)
    else:
        _validate_antigravity_worker_artifacts(root, identity, projection)
    _validate_worker_python_execution_closure(
        server_id,
        identity,
        root,
        Path(str(projection["command"])),
    )
    return compatibility


def _gateway_source_identity(root: Path) -> tuple[str, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    for relative in sorted(
        GATEWAY_SOURCE_RELATIVE_PATHS, key=lambda item: item.encode("utf-8")
    ):
        path = root.joinpath(*relative.split("/"))
        payload = _worker_stable_bytes(path)
        records.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
    digest = hashlib.sha256(GATEWAY_SOURCE_DOMAIN)
    for record in records:
        digest.update(record["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(record["sha256"]))
    return digest.hexdigest(), records


def _gateway_runtime_tree_identity(root: Path, *, domain: bytes) -> dict[str, Any]:
    try:
        resolved_root = root.resolve(strict=True)
        if _worker_is_link_or_reparse(root) or not resolved_root.is_dir():
            raise OSError("runtime tree root is invalid")
        entries = list(resolved_root.rglob("*"))
    except OSError as exc:
        raise CapabilityDataError("gateway runtime tree is unavailable") from exc
    relative_paths: list[str] = []
    seen_paths: set[str] = set()
    for path in entries:
        relative = path.relative_to(resolved_root)
        key = relative.as_posix().casefold()
        if key in seen_paths:
            raise CapabilityDataError(
                "gateway runtime tree has a case-insensitive duplicate"
            )
        seen_paths.add(key)
        if _worker_is_link_or_reparse(path):
            raise CapabilityDataError("gateway runtime tree contains a link")
        if "__pycache__" in relative.parts:
            continue
        if path.is_file():
            relative_paths.append(relative.as_posix())
        elif not path.is_dir():
            raise CapabilityDataError(
                "gateway runtime tree contains an unsupported entry"
            )
    records: list[dict[str, Any]] = []
    for relative in sorted(relative_paths, key=lambda item: item.encode("utf-8")):
        payload = _worker_stable_bytes(
            resolved_root.joinpath(*relative.split("/"))
        )
        records.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
    digest = hashlib.sha256(domain)
    for record in records:
        digest.update(record["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(record["sha256"]))
    return {"file_count": len(records), "sha256": digest.hexdigest()}


def _gateway_runtime_binding_valid(binding: Any) -> bool:
    if not isinstance(binding, dict) or set(binding) != GATEWAY_RUNTIME_BINDING_KEYS:
        return False
    identity = binding.get("runtime_identity")
    python_runtime = identity.get("python_runtime") if isinstance(identity, dict) else None
    bytecode_cache = (
        identity.get("python_bytecode_cache") if isinstance(identity, dict) else None
    )
    source_files = identity.get("source_files") if isinstance(identity, dict) else None
    if (
        binding.get("config_server_id") != GATEWAY_CONFIG_SERVER_ID
        or binding.get("identity_relative_path")
        != GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH
        or SHA256_PATTERN.fullmatch(str(binding.get("identity_sha256") or ""))
        is None
        or SHA256_PATTERN.fullmatch(
            str(binding.get("server_config_sha256") or "")
        )
        is None
        or not isinstance(identity, dict)
        or set(identity) != GATEWAY_RUNTIME_IDENTITY_KEYS
        or identity.get("schema_version") != GATEWAY_RUNTIME_IDENTITY_SCHEMA
        or identity.get("component") != GATEWAY_COMPONENT
        or identity.get("release_id") != GATEWAY_RELEASE_ID
        or identity.get("child_environment_policy_id")
        != WORKER_CHILD_ENVIRONMENT_POLICY_ID
        or identity.get("gateway_startup_environment_policy_id")
        != GATEWAY_STARTUP_ENVIRONMENT_POLICY_ID
        or identity.get("gateway_startup_python_flags")
        != GATEWAY_REQUIRED_PYTHON_FLAGS
        or not isinstance(bytecode_cache, dict)
        or set(bytecode_cache) != {"must_be_empty", "prefix_path"}
        or bytecode_cache.get("must_be_empty") is not True
        or not isinstance(bytecode_cache.get("prefix_path"), str)
        or not Path(bytecode_cache["prefix_path"]).is_absolute()
        or identity.get("python_injection_environment_keys")
        != PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
        or SHA256_PATTERN.fullmatch(str(identity.get("source_sha256") or ""))
        is None
        or not isinstance(source_files, list)
        or [item.get("path") if isinstance(item, dict) else None for item in source_files]
        != sorted(GATEWAY_SOURCE_RELATIVE_PATHS, key=lambda item: item.encode("utf-8"))
        or any(
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "size"}
            or SHA256_PATTERN.fullmatch(str(item.get("sha256") or "")) is None
            or isinstance(item.get("size"), bool)
            or not isinstance(item.get("size"), int)
            or item["size"] < 0
            for item in source_files
        )
        or not isinstance(python_runtime, dict)
        or set(python_runtime) != GATEWAY_PYTHON_RUNTIME_KEYS
        or SEMVER_PATTERN.fullmatch(str(python_runtime.get("version") or "")) is None
        or any(
            SHA256_PATTERN.fullmatch(str(python_runtime.get(key) or "")) is None
            for key in (
                "base_runtime_sha256",
                "console_executable_sha256",
                "dependency_lock_sha256",
                "site_packages_sha256",
                "windowless_executable_sha256",
            )
        )
        or any(
            isinstance(python_runtime.get(key), bool)
            or not isinstance(python_runtime.get(key), int)
            or python_runtime[key] < 1
            for key in ("base_runtime_file_count", "site_packages_file_count")
        )
        or any(
            not isinstance(python_runtime.get(key), str)
            or not Path(python_runtime[key]).is_absolute()
            for key in (
                "base_root",
                "console_executable_path",
                "dependency_lock_path",
                "site_packages_path",
                "windowless_executable_path",
            )
        )
    ):
        return False
    return True


def _gateway_runtime_binding_deployed(
    config: dict[str, Any], binding: dict[str, Any]
) -> bool:
    """Verify the immutable gateway bundle binding without rescanning large trees."""
    if not _gateway_runtime_binding_valid(binding):
        return False
    servers = config.get("mcp_servers")
    gateway_server = (
        servers.get(GATEWAY_CONFIG_SERVER_ID) if isinstance(servers, dict) else None
    )
    if gateway_server != {"url": GATEWAY_CONFIG_URL} or not hmac.compare_digest(
        _worker_projection_sha256(gateway_server),
        str(binding["server_config_sha256"]),
    ):
        return False
    identity_path = CONFIG_PATH.parent.joinpath(
        *GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH.split("/")
    )
    try:
        identity_raw = _worker_stable_bytes(identity_path)
        parsed_identity = json.loads(identity_raw.decode("utf-8-sig"))
    except (
        OSError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        CapabilityDataError,
    ):
        return False
    return bool(
        isinstance(parsed_identity, dict)
        and parsed_identity == binding["runtime_identity"]
        and hmac.compare_digest(
            hashlib.sha256(identity_raw).hexdigest(),
            str(binding["identity_sha256"]),
        )
    )


def _gateway_runtime_binding_current(
    config: dict[str, Any], binding: dict[str, Any]
) -> bool:
    if not _gateway_runtime_binding_deployed(config, binding):
        return False
    servers = config.get("mcp_servers")
    gateway_server = (
        servers.get(GATEWAY_CONFIG_SERVER_ID) if isinstance(servers, dict) else None
    )
    return _gateway_runtime_binding_current_impl(config, binding, gateway_server)


def _gateway_receipt_binding_sha256(receipt: dict[str, Any]) -> str:
    upstream = receipt.get("upstream_config_sha256_by_server")
    if not isinstance(upstream, dict):
        return ""
    lines = [GATEWAY_STARTUP_RECEIPT_BINDING_DOMAIN]
    for name in GATEWAY_STARTUP_RECEIPT_BINDING_FIELDS:
        value = receipt.get(name)
        if isinstance(value, bool):
            rendered = "True" if value else "False"
        else:
            rendered = str(value)
        lines.append(f"{name}={rendered}")
    for server_id in sorted(upstream):
        lines.append(f"upstream={server_id}|{upstream[server_id]}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _gateway_task_action_sha256(identity: dict[str, Any]) -> str:
    runtime = identity["python_runtime"]
    cache = identity["python_bytecode_cache"]
    identity_path = CONFIG_PATH.parent.joinpath(
        *GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH.split("/")
    ).resolve(strict=True)
    gateway_root = identity_path.parent
    executable = Path(runtime["windowless_executable_path"]).resolve(strict=True)
    bootstrap = (gateway_root / "gateway_bootstrap.py").resolve(strict=True)
    config = CONFIG_PATH.resolve(strict=True)
    database = (
        GATEWAY_STARTUP_RECEIPT_PATH.parent / "task-supervisor.sqlite3"
    ).resolve(strict=True)
    pycache = Path(cache["prefix_path"]).resolve(strict=True)
    arguments = (
        f'-I -S -B -X "pycache_prefix={pycache}" "{bootstrap}" '
        f'--config "{config}" --database "{database}" '
        "--host 127.0.0.1 --port 8765 --path /mcp"
    )
    payload = "\0".join((str(executable), arguments, str(gateway_root)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _gateway_receipt_process_current(receipt: dict[str, Any]) -> bool:
    """Verify the receipt still names the exact live Windows process generation."""

    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        process_id = receipt.get("process_id")
        if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id < 1:
            return False
        handle = kernel32.OpenProcess(0x1000, False, process_id)
        if not handle:
            return False
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return False
            creation = FileTime()
            exit_time = FileTime()
            kernel_time = FileTime()
            user_time = FileTime()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return False
        finally:
            kernel32.CloseHandle(handle)
        expected_executable = Path(str(receipt["executable_path"])).resolve(strict=True)
        actual_executable = Path(buffer.value).resolve(strict=True)
        if not _same_worker_path(actual_executable, expected_executable):
            return False
        timestamp = dt.datetime.fromisoformat(
            str(receipt["process_start_time_utc"]).replace("Z", "+00:00")
        )
        if timestamp.utcoffset() != dt.timedelta(0):
            return False
        epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
        delta = timestamp.astimezone(dt.timezone.utc) - epoch
        expected_microseconds = (
            (delta.days * 86400 + delta.seconds) * 1_000_000
            + delta.microseconds
        )
        actual_ticks = (creation.high << 32) | creation.low
        actual_microseconds = (actual_ticks - 116444736000000000) // 10
        return actual_microseconds == expected_microseconds
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _gateway_startup_receipt_current(
    config: dict[str, Any],
    bom: dict[str, Any],
    bom_sha256: str,
    gateway_binding: dict[str, Any],
    required_server_ids: set[str] | frozenset[str],
) -> bool:
    try:
        raw = _worker_stable_bytes(GATEWAY_STARTUP_RECEIPT_PATH)
        receipt = json.loads(raw.decode("utf-8-sig"))
        identity = gateway_binding["runtime_identity"]
        runtime = identity["python_runtime"]
        runtime_identity_path = CONFIG_PATH.parent.joinpath(
            *GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH.split("/")
        ).resolve(strict=True)
        bom_path = WORKER_RUNTIME_BOM_PATH.resolve(strict=True)
        upstream = receipt.get("upstream_config_sha256_by_server")
        servers = config.get("mcp_servers")
        expected_upstreams = {
            str(server_id)
            for server_id, server in (servers.items() if isinstance(servers, dict) else ())
            if isinstance(server, dict)
            and server.get("gateway_managed") is True
            and server.get("enabled") is False
            and isinstance(server.get("command"), str)
            and server["command"].strip()
        }
        recorded = dt.datetime.fromisoformat(
            str(receipt.get("recorded_at_utc") or "").replace("Z", "+00:00")
        )
        started = dt.datetime.fromisoformat(
            str(receipt.get("process_start_time_utc") or "").replace("Z", "+00:00")
        )
    except (
        CapabilityDataError,
        KeyError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return False
    return bool(
        isinstance(receipt, dict)
        and set(receipt) == GATEWAY_STARTUP_RECEIPT_KEYS
        and receipt.get("schema_version") == GATEWAY_STARTUP_RECEIPT_SCHEMA
        and receipt.get("release_id") == identity.get("release_id")
        and receipt.get("process_role") == "scheduled_windowless"
        and receipt.get("managed_upstreams_absent_at_start") is True
        and recorded.utcoffset() == dt.timedelta(0)
        and started.utcoffset() == dt.timedelta(0)
        and recorded >= started
        and isinstance(upstream, dict)
        and set(upstream) == expected_upstreams
        and set(required_server_ids).issubset(upstream)
        and all(
            isinstance(server_id, str)
            and SHA256_PATTERN.fullmatch(str(digest or "")) is not None
            for server_id, digest in upstream.items()
        )
        and SHA256_PATTERN.fullmatch(
            str(receipt.get("loaded_upstream_config_sha256") or "")
        )
        is not None
        and hmac.compare_digest(
            str(receipt.get("binding_sha256") or ""),
            _gateway_receipt_binding_sha256(receipt),
        )
        and _same_worker_path(Path(str(receipt["runtime_identity_path"])), runtime_identity_path)
        and hmac.compare_digest(
            str(receipt["runtime_identity_sha256"]),
            str(gateway_binding["identity_sha256"]),
        )
        and receipt.get("source_sha256") == identity.get("source_sha256")
        and _same_worker_path(Path(str(receipt["worker_runtime_bom_path"])), bom_path)
        and hmac.compare_digest(str(receipt["worker_runtime_bom_sha256"]), bom_sha256)
        and _same_worker_path(
            Path(str(receipt["executable_path"])),
            Path(str(runtime["windowless_executable_path"])),
        )
        and hmac.compare_digest(
            str(receipt["executable_sha256"]),
            str(runtime["windowless_executable_sha256"]),
        )
        and receipt.get("child_environment_policy_id")
        == identity.get("child_environment_policy_id")
        and receipt.get("gateway_startup_environment_policy_id")
        == identity.get("gateway_startup_environment_policy_id")
        and hmac.compare_digest(
            str(receipt.get("task_action_sha256") or ""),
            _gateway_task_action_sha256(identity),
        )
        and _gateway_receipt_process_current(receipt)
    )


def _gateway_runtime_binding_current_impl(
    config: dict[str, Any],
    binding: dict[str, Any],
    gateway_server: Any,
) -> bool:
    if gateway_server != {"url": GATEWAY_CONFIG_URL} or not hmac.compare_digest(
        _worker_projection_sha256(gateway_server),
        str(binding["server_config_sha256"]),
    ):
        return False
    identity = binding["runtime_identity"]
    python_runtime = identity["python_runtime"]
    bytecode_cache = identity["python_bytecode_cache"]
    identity_path = CONFIG_PATH.parent.joinpath(
        *GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH.split("/")
    )
    gateway_root = identity_path.parent
    try:
        identity_raw = _worker_stable_bytes(identity_path)
        parsed_identity = json.loads(identity_raw.decode("utf-8-sig"))
        source_sha256, source_files = _gateway_source_identity(gateway_root)
        local_app_data = Path(os.environ["LOCALAPPDATA"]).resolve(strict=True)
        pycache_prefix = Path(bytecode_cache["prefix_path"]).resolve(strict=True)
        expected_pycache_prefix = (
            local_app_data / "Codex" / "stability" / "pycache" / "gateway"
        ).resolve(strict=True)
        pycache_is_empty = not any(pycache_prefix.iterdir())
        base_root = Path(python_runtime["base_root"]).resolve(strict=True)
        console = Path(python_runtime["console_executable_path"]).resolve(strict=True)
        windowless = Path(python_runtime["windowless_executable_path"]).resolve(
            strict=True
        )
        dependency_lock = Path(python_runtime["dependency_lock_path"]).resolve(
            strict=True
        )
        site_packages = Path(python_runtime["site_packages_path"]).resolve(strict=True)
        expected_dependency_lock = (gateway_root / "uv.lock").resolve(strict=True)
        expected_site_packages = (
            gateway_root / ".venv" / "Lib" / "site-packages"
        ).resolve(strict=True)
        base_runtime_identity = _gateway_runtime_tree_identity(
            base_root, domain=GATEWAY_PYTHON_BASE_RUNTIME_DOMAIN
        )
        site_packages_identity = _gateway_runtime_tree_identity(
            site_packages, domain=GATEWAY_SITE_PACKAGES_DOMAIN
        )
        console_sha256 = hashlib.sha256(_worker_stable_bytes(console)).hexdigest()
        windowless_sha256 = hashlib.sha256(
            _worker_stable_bytes(windowless)
        ).hexdigest()
        dependency_lock_sha256 = hashlib.sha256(
            _worker_stable_bytes(dependency_lock)
        ).hexdigest()
    except (
        OSError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        CapabilityDataError,
    ):
        return False
    return bool(
        isinstance(parsed_identity, dict)
        and parsed_identity == identity
        and hmac.compare_digest(
            hashlib.sha256(identity_raw).hexdigest(),
            str(binding["identity_sha256"]),
        )
        and source_files == identity["source_files"]
        and hmac.compare_digest(source_sha256, str(identity["source_sha256"]))
        and _same_worker_path(console, base_root / "python.exe")
        and _same_worker_path(windowless, base_root / "pythonw.exe")
        and _same_worker_path(pycache_prefix, expected_pycache_prefix)
        and not _worker_is_link_or_reparse(pycache_prefix)
        and pycache_prefix.is_dir()
        and pycache_is_empty
        and _same_worker_path(dependency_lock, expected_dependency_lock)
        and _same_worker_path(site_packages, expected_site_packages)
        and base_runtime_identity["file_count"]
        == python_runtime["base_runtime_file_count"]
        and hmac.compare_digest(
            str(base_runtime_identity["sha256"]),
            str(python_runtime["base_runtime_sha256"]),
        )
        and site_packages_identity["file_count"]
        == python_runtime["site_packages_file_count"]
        and hmac.compare_digest(
            str(site_packages_identity["sha256"]),
            str(python_runtime["site_packages_sha256"]),
        )
        and hmac.compare_digest(
            console_sha256, str(python_runtime["console_executable_sha256"])
        )
        and hmac.compare_digest(
            windowless_sha256,
            str(python_runtime["windowless_executable_sha256"]),
        )
        and hmac.compare_digest(
            dependency_lock_sha256,
            str(python_runtime["dependency_lock_sha256"]),
        )
    )


def _load_worker_runtime_bom(
    path: Path | None = None, *, expected_sha256: str = ""
) -> tuple[dict[str, Any], str]:
    """Load and strictly validate the entire worker-runtime dependency authority."""

    source = path or WORKER_RUNTIME_BOM_PATH
    expected = str(expected_sha256 or "").lower()
    if expected and SHA256_PATTERN.fullmatch(expected) is None:
        raise CapabilityDataError("worker runtime BOM expected digest is invalid")
    if not source.is_file() or source.is_symlink():
        raise CapabilityDataError("worker runtime BOM is missing or linked")
    try:
        before = source.stat()
        raw = source.read_bytes()
        second = source.read_bytes()
        after = source.stat()
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityDataError("worker runtime BOM is unreadable") from exc
    if (
        raw != second
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise CapabilityDataError("worker runtime BOM changed during verification")
    actual = hashlib.sha256(raw).hexdigest()
    if expected and not hmac.compare_digest(actual, expected):
        raise CapabilityDataError("worker runtime BOM digest mismatch")
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "gateway_runtime",
        "runtimes",
    }:
        raise CapabilityDataError("worker runtime BOM root is invalid")
    if not _gateway_runtime_binding_valid(value.get("gateway_runtime")):
        raise CapabilityDataError("worker runtime BOM gateway binding is invalid")
    runtimes = value.get("runtimes")
    if value.get("schema_version") != WORKER_RUNTIME_BOM_SCHEMA or not isinstance(
        runtimes, dict
    ) or set(runtimes) != REQUIRED_WORKER_RUNTIME_SERVER_IDS:
        raise CapabilityDataError("worker runtime BOM schema is invalid")
    required = {
        "config_server_id",
        "identity_relative_path",
        "identity_sha256",
        "command_sha256",
        "python_execution_closure",
        "server_config_sha256",
        "release_id",
        "route_schema_version",
        "route_registry_schema_version",
    }
    for server_id, binding in runtimes.items():
        closure = (
            binding.get("python_execution_closure")
            if isinstance(binding, dict)
            else None
        )
        if (
            not isinstance(server_id, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", server_id) is None
            or not isinstance(binding, dict)
            or set(binding) != required
            or binding.get("config_server_id") != server_id
            or binding.get("route_schema_version") != "3.0"
            or binding.get("route_registry_schema_version")
            != ROUTE_REGISTRY_SCHEMA_VERSION
            or SHA256_PATTERN.fullmatch(str(binding.get("identity_sha256") or ""))
            is None
            or SHA256_PATTERN.fullmatch(str(binding.get("command_sha256") or ""))
            is None
            or not isinstance(closure, dict)
            or set(closure) != PYTHON_EXECUTION_CLOSURE_KEYS
            or closure.get("schema_version")
            != PYTHON_EXECUTION_CLOSURE_SCHEMA
            or closure.get("import_package")
            != WORKER_IMPORT_PACKAGES.get(server_id)
            or closure.get("include_system_site_packages") is not False
            or closure.get("isolated_mode") is not True
            or closure.get("user_site_enabled") is not False
            or closure.get("dont_write_bytecode") is not True
            or closure.get("pycache_prefix_empty") is not True
            or closure.get("forbidden_environment_variables")
            != PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
            or closure.get("child_environment_policy_id")
            != WORKER_CHILD_ENVIRONMENT_POLICY_ID
            or SEMVER_PATTERN.fullmatch(
                str(closure.get("base_interpreter_version") or "")
            )
            is None
            or any(
                SHA256_PATTERN.fullmatch(str(closure.get(key) or "")) is None
                for key in (
                    "base_interpreter_sha256",
                    "base_runtime_tree_sha256",
                    "editable_pth_sha256",
                    "site_packages_tree_sha256",
                    "installed_distributions_sha256",
                    "pth_files_sha256",
                    "pth_imports_sha256",
                    "pyvenv_config_sha256",
                    "venv_python_sha256",
                )
            )
            or any(
                isinstance(closure.get(key), bool)
                or not isinstance(closure.get(key), int)
                or closure[key] < 1
                for key in (
                    "base_runtime_tree_file_count",
                    "site_packages_tree_file_count",
                    "installed_distributions_count",
                    "pth_files_count",
                    "pth_imports_count",
                )
            )
            or any(
                not isinstance(closure.get(key), str)
                or not Path(closure[key]).is_absolute()
                for key in (
                    "base_interpreter_path",
                    "base_runtime_tree_path",
                    "editable_pth_path",
                    "editable_source_root",
                    "import_origin",
                    "pyvenv_config_path",
                    "pycache_prefix_path",
                    "site_packages_path",
                    "venv_python_path",
                )
            )
            or SHA256_PATTERN.fullmatch(
                str(binding.get("server_config_sha256") or "")
            )
            is None
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}",
                str(binding.get("release_id") or ""),
            )
            is None
        ):
            raise CapabilityDataError("worker runtime BOM binding is invalid")
        relative_identity = str(binding.get("identity_relative_path") or "")
        relative_path = Path(relative_identity)
        if (
            relative_identity != "runtime-identity.json"
            or len(relative_identity) > 160
            or relative_path.is_absolute()
            or re.match(r"^[A-Za-z]:", relative_identity)
            or any(part == ".." for part in relative_path.parts)
        ):
            raise CapabilityDataError("worker runtime BOM identity path is invalid")
    return value, actual


def _gateway_managed_upstream_configured(
    server_id: str,
    *,
    expected_bom_sha256: str = "",
    verify_current_bytes: bool = False,
    gateway_runtime_verified: bool = False,
    require_process_generation: bool = True,
    gateway_process_verified: bool = False,
) -> bool:
    """Verify deployed route binding; rescan bytes only at audit/execution boundaries."""

    try:
        with CONFIG_PATH.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    try:
        projection, resolved_root = _worker_server_projection(config, server_id)
    except CapabilityDataError:
        return False
    binding = _worker_runtime_binding(
        server_id, expected_bom_sha256=expected_bom_sha256
    )
    if binding is None:
        return False
    if not binding:
        return True
    try:
        bom, _ = _load_worker_runtime_bom(
            expected_sha256=str(expected_bom_sha256 or "").lower()
        )
    except CapabilityDataError:
        return False
    gateway_binding = bom.get("gateway_runtime")
    if (
        not isinstance(gateway_binding, dict)
        or not _gateway_runtime_binding_deployed(config, gateway_binding)
        or (
            verify_current_bytes
            and not gateway_runtime_verified
            and not _gateway_runtime_binding_current(config, gateway_binding)
        )
    ):
        return False
    if (
        require_process_generation
        and not gateway_process_verified
        and not _gateway_startup_receipt_current(
            config, bom, str(expected_bom_sha256 or "").lower(), gateway_binding, {server_id}
        )
    ):
        return False
    if not hmac.compare_digest(
        _worker_projection_sha256(projection),
        str(binding.get("server_config_sha256") or ""),
    ):
        return False
    try:
        command_sha256 = hashlib.sha256(
            _worker_stable_bytes(Path(projection["command"]))
        ).hexdigest()
    except CapabilityDataError:
        return False
    if not hmac.compare_digest(
        command_sha256, str(binding.get("command_sha256") or "")
    ):
        return False
    relative_identity = str(binding.get("identity_relative_path") or "")
    if relative_identity != "runtime-identity.json":
        return False
    try:
        identity_path = (resolved_root / relative_identity).resolve(strict=True)
    except OSError:
        return False
    if (
        not identity_path.is_relative_to(resolved_root)
        or not identity_path.is_file()
        or identity_path.is_symlink()
    ):
        return False
    try:
        before = identity_path.stat()
        identity_raw = identity_path.read_bytes()
        identity_second = identity_path.read_bytes()
        after = identity_path.stat()
        identity = json.loads(identity_raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if (
        identity_raw != identity_second
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or not isinstance(identity, dict)
    ):
        return False
    try:
        compatibility = (
            _validate_worker_family_identity(
                server_id, identity, resolved_root, projection
            )
            if verify_current_bytes
            else _validate_worker_family_identity_document(server_id, identity)
        )
    except CapabilityDataError:
        return False
    return bool(
        hmac.compare_digest(
            hashlib.sha256(identity_raw).hexdigest(),
            str(binding["identity_sha256"]),
        )
        and isinstance(identity, dict)
        and identity.get("release_id") == binding["release_id"]
        and identity.get("python_execution_closure")
        == binding["python_execution_closure"]
        and compatibility.get("route_schema_version") == "3.0"
        and compatibility.get("route_registry_schema_version")
        == ROUTE_REGISTRY_SCHEMA_VERSION
    )


def worker_runtime_identity_status(manifest: dict[str, Any]) -> dict[str, Any]:
    """Report BOM validity and each configured worker identity independently."""

    expected = str(
        (manifest.get("source_hashes") or {}).get(WORKER_RUNTIME_BOM_SOURCE_HASH_KEY)
        or ""
    ).lower()
    result: dict[str, Any] = {
        "bom_status": "unavailable",
        "bom_sha256": expected,
        "gateway": {},
        "components": {},
    }
    try:
        bom, actual = _load_worker_runtime_bom(expected_sha256=expected)
    except CapabilityDataError:
        return result
    result["bom_status"] = "current"
    result["bom_sha256"] = actual
    try:
        with CONFIG_PATH.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        config = {}
    gateway_binding = bom["gateway_runtime"]
    gateway_current = _gateway_runtime_binding_current(config, gateway_binding)
    process_current = gateway_current and _gateway_startup_receipt_current(
        config,
        bom,
        actual,
        gateway_binding,
        REQUIRED_WORKER_RUNTIME_SERVER_IDS,
    )
    result["gateway"] = {
        "release_id": gateway_binding["runtime_identity"]["release_id"],
        "identity_sha256": gateway_binding["identity_sha256"],
        "identity_binding_status": "current" if gateway_current else "unavailable",
        "process_generation_status": (
            "current" if process_current else "restart_required"
            if gateway_current else "unavailable"
        ),
    }
    reverse_families = {
        server_id: family for family, server_id in WORKER_FAMILY_SERVER_IDS.items()
    }
    for server_id, binding in sorted(bom["runtimes"].items()):
        current = gateway_current and _gateway_managed_upstream_configured(
            server_id,
            expected_bom_sha256=actual,
            verify_current_bytes=True,
            gateway_runtime_verified=True,
            require_process_generation=False,
        )
        result["components"][server_id] = {
            "worker_family": reverse_families.get(server_id, ""),
            "release_id": binding["release_id"],
            "identity_sha256": binding["identity_sha256"],
            "identity_binding_status": "current" if current else "unavailable",
            "admission_status": (
                "current" if current and process_current else "restart_required"
                if current else "unavailable"
            ),
        }
    return result


_HOOK_EVENT_KEY_LABELS = {
    "UserPromptSubmit": "user_prompt_submit",
    "SessionStart": "session_start",
}
_HOOK_EVENTS_WITHOUT_MATCHERS = {"UserPromptSubmit", "Stop"}
_DEFAULT_HOOK_TIMEOUT_SECONDS = 600
_DEFAULT_HOOK_OUTPUT_TOKEN_LIMIT = 2_500


def _command_hook_trust_hash(
    event_name: str,
    group: dict[str, Any],
    hook: dict[str, Any],
) -> str:
    """Reproduce Codex's normalized command-hook trust fingerprint."""

    event_label = _HOOK_EVENT_KEY_LABELS.get(event_name)
    if event_label is None:
        raise CapabilityDataError(f"unsupported hook event for trust hashing: {event_name}")
    if hook.get("type") != "command":
        raise CapabilityDataError("router hook handler must have type=command")

    command = hook.get("command")
    command_windows = hook.get("commandWindows")
    if not isinstance(command, str):
        raise CapabilityDataError("router hook command must be a string")
    if command_windows is not None and not isinstance(command_windows, str):
        raise CapabilityDataError("router hook commandWindows must be a string")
    selected_command = command_windows if os.name == "nt" and command_windows is not None else command
    if not selected_command.strip():
        raise CapabilityDataError("router hook command must not be empty")

    timeout = hook.get("timeout", _DEFAULT_HOOK_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 0:
        raise CapabilityDataError("router hook timeout must be a non-negative integer")
    timeout = max(timeout, 1)

    asynchronous = hook.get("async", False)
    if not isinstance(asynchronous, bool):
        raise CapabilityDataError("router hook async must be a Boolean")
    if asynchronous:
        raise CapabilityDataError(f"async {event_name} hooks are not executable")

    normalized_handler: dict[str, Any] = {
        "type": "command",
        "command": selected_command,
        "timeout": timeout,
        "async": asynchronous,
    }
    if "statusMessage" in hook:
        status_message = hook.get("statusMessage")
        if status_message is not None and not isinstance(status_message, str):
            raise CapabilityDataError("router hook statusMessage must be a string")
        if status_message is not None:
            normalized_handler["statusMessage"] = status_message
    if "additionalContextLimit" in hook:
        context_limit = hook.get("additionalContextLimit")
        if (
            isinstance(context_limit, bool)
            or not isinstance(context_limit, int)
            or context_limit < 0
        ):
            raise CapabilityDataError(
                "router hook additionalContextLimit must be a non-negative integer"
            )
        if context_limit != _DEFAULT_HOOK_OUTPUT_TOKEN_LIMIT:
            normalized_handler["additionalContextLimit"] = context_limit

    identity: dict[str, Any] = {
        "event_name": event_label,
        "hooks": [normalized_handler],
    }
    matcher = group.get("matcher")
    if matcher is not None and not isinstance(matcher, str):
        raise CapabilityDataError("router hook matcher must be a string")
    if event_name not in _HOOK_EVENTS_WITHOUT_MATCHERS and matcher is not None:
        identity["matcher"] = matcher
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def hook_carrier_status(
    *,
    config_path: Path | None = None,
    hooks_path: Path | None = None,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    """Report hook dispatch separately from canonical CLI router authority."""

    home = (codex_home or CODEX_HOME).resolve(strict=False)
    selected_config = config_path or CONFIG_PATH
    selected_hooks = hooks_path or (home / "hooks.json")
    unavailable = {
        "status": "unavailable",
        "feature_enabled": False,
        "hooks_json_status": "unavailable",
        "trust_state_status": "unavailable",
        "user_prompt_router": "unavailable",
        "session_start_recovery": "unavailable",
    }
    try:
        config = tomllib.loads(selected_config.read_text(encoding="utf-8-sig"))
        hook_config = json.loads(selected_hooks.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, json.JSONDecodeError):
        return unavailable
    if not isinstance(config, dict) or not isinstance(hook_config, dict):
        return unavailable
    feature_enabled = (
        isinstance(config.get("features"), dict)
        and config["features"].get("hooks") is True
    )
    hooks_root = hook_config.get("hooks")
    if not isinstance(hooks_root, dict):
        return {**unavailable, "feature_enabled": feature_enabled}
    hook_section = config.get("hooks")
    state = hook_section.get("state", {}) if isinstance(hook_section, dict) else {}
    if not isinstance(state, dict):
        state = {}

    required = {
        "UserPromptSubmit": (
            "user_prompt_submit",
            "user_prompt_skill_router.py",
            "user_prompt_router",
        ),
        "SessionStart": (
            "session_start",
            "capability_index_session_start.py",
            "session_start_recovery",
        ),
    }
    components: dict[str, str] = {}
    trust_current = True
    hooks_valid = True
    resolved_hooks = selected_hooks.resolve(strict=False)
    for event_name, (state_event, script_name, output_name) in required.items():
        event_valid = True
        matches: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
        groups = hooks_root.get(event_name)
        if not isinstance(groups, list):
            groups = []
            event_valid = False
        for group_index, group in enumerate(groups):
            commands = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(commands, list):
                event_valid = False
                continue
            for hook_index, hook in enumerate(commands):
                if not isinstance(hook, dict):
                    event_valid = False
                    continue
                command = " ".join(
                    str(hook.get(field) or "")
                    for field in ("command", "commandWindows")
                )
                if script_name.casefold() not in command.casefold():
                    continue
                script_path = (home / "hooks" / script_name).resolve(strict=False)
                if (
                    not script_path.is_file()
                    or str(script_path).casefold() not in command.casefold()
                ):
                    event_valid = False
                    continue
                matches.append((group_index, hook_index, group, hook))
        if len(matches) != 1:
            event_valid = False
            hooks_valid = False
            trust_current = False
            components[output_name] = "unavailable"
            continue

        group_index, hook_index, group, hook = matches[0]
        trust_key = f"{resolved_hooks}:{state_event}:{group_index}:{hook_index}"
        trust = state.get(trust_key)
        enabled = trust.get("enabled", True) if isinstance(trust, dict) else True
        if not isinstance(enabled, bool):
            enabled = False
            trust_current = False
        try:
            expected_digest = _command_hook_trust_hash(event_name, group, hook)
        except CapabilityDataError:
            event_valid = False
            expected_digest = None
        digest = trust.get("trusted_hash") if isinstance(trust, dict) else None
        exact_trust = (
            enabled is True
            and isinstance(digest, str)
            and expected_digest is not None
            and hmac.compare_digest(digest, expected_digest)
        )
        if not exact_trust:
            trust_current = False
        hooks_valid = hooks_valid and event_valid
        components[output_name] = (
            "current" if event_valid and exact_trust else "unavailable"
        )
    status = (
        "current"
        if feature_enabled and hooks_valid and trust_current
        else "unavailable"
    )
    return {
        "status": status,
        "feature_enabled": feature_enabled,
        "hooks_json_status": "current" if hooks_valid else "unavailable",
        "trust_state_status": "current" if trust_current else "unavailable",
        **components,
    }


def _load_live_config_inventory() -> dict[str, Any] | None:
    try:
        with CONFIG_PATH.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return config if isinstance(config, dict) else None


def _config_value_at_path(
    config: dict[str, Any], path: Iterable[str]
) -> tuple[bool, object]:
    current: object = config
    for segment in path:
        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _live_dependency_probe_status(
    dependency_id: str,
    *,
    control: dict[str, Any],
    probes: object,
    execution_request_id: object,
) -> tuple[bool, str]:
    """Validate one request-bound live call result without trusting config alone."""

    requirement = control.get("probe_requirement")
    requirement = requirement if isinstance(requirement, dict) else {}
    if (
        requirement.get("kind") != "live_call"
        or requirement.get("target") != dependency_id
        or requirement.get("success_status") != "callable"
    ):
        return False, f"probe:{dependency_id}:policy_invalid"
    if not isinstance(execution_request_id, str) or not EXECUTION_REQUEST_ID_PATTERN.fullmatch(
        execution_request_id
    ):
        return False, f"probe:{dependency_id}:request_unbound"
    if not isinstance(probes, dict):
        return False, f"probe:{dependency_id}:missing"
    raw_probe = probes.get(dependency_id)
    if not isinstance(raw_probe, dict):
        return False, f"probe:{dependency_id}:missing"
    if (
        raw_probe.get("kind") != requirement["kind"]
        or raw_probe.get("target") != requirement["target"]
        or raw_probe.get("request_id") != execution_request_id
    ):
        return False, f"probe:{dependency_id}:invalid"
    status = str(raw_probe.get("status") or "").strip()
    if status == requirement["success_status"]:
        return True, ""
    if status in {"auth_failed", "tool_failed", "target_failed"}:
        return False, f"probe:{dependency_id}:{status}"
    return False, f"probe:{dependency_id}:invalid"


_LIVE_DEPENDENCY_ALIASES = {
    "app:supabase": r"(?:live\s+)?supabase\s+(?:app|connector|project|provider)",
    "app:neon": r"(?:live\s+)?neon(?:\s+postgres)?\s+(?:app|connector|project|provider)",
    "mcp:codex-security": (
        r"(?:live\s+)?(?:codex[- ]security|codex\s+security|security)\s+"
        r"(?:mcp|server|connector|plugin)"
    ),
}


def _live_dependency_explicitly_excluded(prompt: str, dependency_id: str) -> bool:
    """Honor an instruction that forbids live access despite a callable probe."""

    text = _normalized_unquoted_prompt(prompt)
    generic_patterns = (
        r"\b(?:static(?:ally)?(?:\s+only)?|static[- ]only|offline(?:\s+only)?)\b",
        r"\bwithout\s+(?:(?:using|calling|accessing)\s+)?(?:the\s+)?live\b",
        r"\b(?:do\s+not|don'?t|dont|never)\s+"
        r"(?:access|call|use|connect\s+to)\s+(?:the\s+)?live\b",
        r"\b(?:no|without)\s+live\s+(?:provider|project|connector|app|mcp|tool)\s+access\b",
    )
    if any(re.search(pattern, text) for pattern in generic_patterns):
        return True
    alias = _LIVE_DEPENDENCY_ALIASES.get(dependency_id)
    if not alias:
        return False
    return bool(
        re.search(
            rf"\b(?:do\s+not|don'?t|dont|never|without)\s+"
            rf"(?:(?:access|call|use|connect\s+to|using|calling|accessing)\s+)?"
            rf"(?:the\s+)?(?:{alias})\b",
            text,
        )
        or re.search(
            rf"\b(?:{alias})\b[^.!?;]{{0,40}}\b"
            rf"(?:disabled|excluded|not\s+(?:allowed|requested|used))\b",
            text,
        )
    )


def _unavailable_live_dependencies(
    requirements: Iterable[str],
    *,
    manifest: dict[str, Any],
    policy: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_alias: dict[str, list[dict[str, Any]]],
    config: dict[str, Any] | None,
    probes: object,
    execution_request_id: object,
    prompt: str = "",
) -> list[str]:
    """Return dependencies lacking inventory, config, or a bound live probe."""

    required = [str(item).strip() for item in requirements if str(item).strip()]
    if not required:
        return []
    unavailable: list[str] = []
    manifest_ready = (
        isinstance(manifest, dict)
        and normalize(manifest.get("freshness_status")) in FRESH_STATES
        and isinstance(manifest.get("entries"), list)
        and manifest.get("source_hashes_verified") is True
    )
    if not manifest_ready:
        unavailable.append("inventory:active-capabilities")
    if config is None:
        unavailable.append("inventory:config")
    controls = policy.get("live_dependency_controls")
    controls = controls if isinstance(controls, dict) else {}
    for dependency_id in required:
        if _live_dependency_explicitly_excluded(prompt, dependency_id):
            unavailable.extend(
                [dependency_id, f"prompt:{dependency_id}:explicitly_excluded"]
            )
            continue
        control = controls.get(dependency_id)
        available = False
        configured = False
        probe_reason = ""
        if (
            manifest_ready
            and isinstance(config, dict)
            and isinstance(control, dict)
        ):
            present_entry = next(
                (
                    entry
                    for reference in control.get("manifest_any", [])
                    if (
                        (entry := _resolve_reference(reference, by_id, by_alias))
                        is not None
                        and not _is_suppressed(entry, policy)
                        and _entry_hash_current(entry)
                    )
                ),
                None,
            )
            found_value, current_value = _config_value_at_path(
                config, control.get("config_path", [])
            )
            found_container, container = _config_value_at_path(
                config, control.get("config_path", [])[:-1]
            )
            gateway_managed = bool(
                found_container
                and isinstance(container, dict)
                and container.get("gateway_managed") is True
                and present_entry
                and (
                    normalize(present_entry.get("state"))
                    == "active-gateway-managed"
                    or "gateway_managed" in present_entry.get("families", [])
                )
            )
            expected_value = control.get("expected_value")
            direct_value_matches = (
                type(current_value) is type(expected_value)
                and current_value == expected_value
            )
            available = (
                present_entry is not None
                and found_value
                and (
                    direct_value_matches
                    or gateway_managed
                )
            )
            configured = available
            if configured:
                available, probe_reason = _live_dependency_probe_status(
                    dependency_id,
                    control=control,
                    probes=probes,
                    execution_request_id=execution_request_id,
                )
        if not available:
            unavailable.append(dependency_id)
            if configured and probe_reason:
                unavailable.append(probe_reason)
    return list(dict.fromkeys(unavailable))


def _entry_usable(
    entry: dict[str, Any] | None,
    *,
    prompt: str,
    policy: dict[str, Any],
    rule: dict[str, Any],
) -> bool:
    return bool(
        entry
        and not is_state_artifact(entry)
        and not _is_suppressed(entry, policy)
        and not _is_superpowers(entry)
        and not _forbidden_capability(entry, rule)
        and not _entry_explicitly_excluded(prompt, entry, policy)
        and _entry_hash_current(entry)
    )


def _equivalent_fallback_semantics_valid(
    fallback: dict[str, Any],
    *,
    requested_entry: dict[str, Any] | None,
    fallback_entry: dict[str, Any] | None,
    by_id: dict[str, dict[str, Any]],
    by_alias: dict[str, list[dict[str, Any]]],
) -> bool:
    """Bind an equivalent declaration to the requested or allowlisted entry."""

    if fallback.get("equivalence") != "equivalent":
        return True
    if requested_entry is None or fallback_entry is None:
        return False
    allowed_ids = {str(requested_entry.get("id") or "")}
    for reference in fallback.get("equivalent_capabilities", []):
        allowed = _resolve_reference(str(reference), by_id, by_alias)
        if allowed is not None:
            allowed_ids.add(str(allowed.get("id") or ""))
    emitted_id = str(fallback_entry.get("id") or "")
    return bool(emitted_id) and emitted_id in allowed_ids


def _entry_explicitly_excluded(
    prompt: str, entry: dict[str, Any] | None, policy: dict[str, Any]
) -> bool:
    """Honor explicit user negation for a selected named capability."""

    if not entry:
        return False
    targets: list[str] = []
    for raw_value in (entry.get("id"), entry.get("name")):
        value = str(raw_value or "").strip()
        if not value:
            continue
        without_kind = value.removeprefix("skill:")
        candidates = [value, without_kind]
        if ":" in without_kind:
            candidates.append(without_kind.rsplit(":", 1)[-1])
        for candidate in candidates:
            targets.append(candidate)
            targets.append(re.sub(r"[-_]+", " ", candidate))
    declared = policy.get("capability_aliases", {})
    if isinstance(declared, dict):
        for reference, aliases in declared.items():
            if not _target_matches(entry, str(reference)):
                continue
            targets.extend(str(alias).strip() for alias in aliases)
    return _prompt_negates_any(prompt, dict.fromkeys(targets))


def _resolve_supports(
    references: Iterable[str],
    *,
    prompt: str,
    primary: dict[str, Any] | None,
    max_supports: int,
    policy: dict[str, Any],
    rule: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_alias: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if not primary:
        return []
    supports: list[dict[str, Any]] = []
    seen = {primary.get("id")}
    for reference in references:
        support = _resolve_reference(reference, by_id, by_alias)
        if not support or support.get("id") in seen:
            continue
        if (
            is_state_artifact(support)
            or _is_suppressed(support, policy)
            or _forbidden_capability(support, rule)
            or _entry_explicitly_excluded(prompt, support, policy)
            or not _entry_hash_current(support)
        ):
            continue
        if not _tactical_support_allowed(support, primary, policy, by_id, by_alias):
            continue
        supports.append(support)
        seen.add(support.get("id"))
        if len(supports) >= max_supports:
            break
    return supports


def _public_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    return {
        "id": str(entry.get("id") or ""),
        "kind": str(entry.get("kind") or ""),
        "name": str(entry.get("name") or ""),
        "state": str(entry.get("state") or ""),
        "status": str(entry.get("status") or entry.get("state") or ""),
        "provider": str(entry.get("provider") or ""),
        "version": str(entry.get("version") or ""),
        "source_path": str(entry.get("source_path") or ""),
        "sha256": str(entry.get("sha256") or ""),
        "families": list(entry.get("families", [])),
        "description": str(entry.get("description") or ""),
    }


def _decision_digest(decision: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in decision.items()
        if key not in {"decision_digest", "decision_id"}
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_route_json(decision: dict[str, Any]) -> str:
    """Serialize a full route for exact cross-process receipt comparison."""

    return json.dumps(
        decision,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def normalize_task_text(value: object) -> str:
    """Canonicalize bounded task text without changing its internal content."""

    text = "" if value is None else str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def compute_task_text_sha256(task_text: object) -> str:
    """Hash UTF-8 bytes of the documented bounded-task normalization."""

    normalized = normalize_task_text(task_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonical_task_input_json(task_input: dict[str, Any]) -> str:
    """Serialize the complete task input without normalizing JSON values."""

    if not isinstance(task_input, dict):
        raise CapabilityDataError("task_input must be a JSON object")
    try:
        return json.dumps(
            task_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityDataError(f"task_input is not canonical JSON data: {exc}") from exc


def compute_task_input_sha256(task_input: dict[str, Any]) -> str:
    """Hash the canonical UTF-8 JSON bytes of the full task input object."""

    canonical = canonical_task_input_json(task_input)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def route_task_input_matches(
    decision: dict[str, Any], task_input: dict[str, Any]
) -> bool:
    """Verify that an exact full task input is bound to a route decision."""

    expected = str(decision.get("task_input_sha256") or "").lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        return False
    try:
        actual = compute_task_input_sha256(task_input)
    except CapabilityDataError:
        return False
    return hmac.compare_digest(expected, actual)


def route_task_text_matches(decision: dict[str, Any], task_text: object) -> bool:
    """Verify that executed bounded task text is bound to a route decision."""

    expected = str(decision.get("task_text_sha256") or "").lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        return False
    return hmac.compare_digest(expected, compute_task_text_sha256(task_text))


def _validate_route_schema(decision: dict[str, Any]) -> None:
    """Validate the full decision against the canonical route schema."""

    if not isinstance(decision, dict):
        raise CapabilityDataError("route decision must be an object")
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError, ValidationError
    except ImportError as exc:
        raise CapabilityDataError("jsonschema is required for route issuance") from exc
    try:
        schema = json.loads(ROUTE_DECISION_SCHEMA_PATH.read_text(encoding="utf-8-sig"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(decision)
    except OSError as exc:
        raise CapabilityDataError(
            f"cannot read route-decision schema: {ROUTE_DECISION_SCHEMA_PATH}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CapabilityDataError(
            f"route-decision schema is not valid JSON: {ROUTE_DECISION_SCHEMA_PATH}"
        ) from exc
    except (SchemaError, ValidationError) as exc:
        raise CapabilityDataError(f"route decision schema validation failed: {exc.message}") from exc


def validate_route_decision(decision: dict[str, Any]) -> None:
    """Require schema validity and a self-consistent decision digest and ID."""

    _validate_route_schema(decision)
    expected = _decision_digest(decision)
    digest = str(decision.get("decision_digest") or "")
    decision_id = str(decision.get("decision_id") or "")
    if not hmac.compare_digest(digest, expected) or not hmac.compare_digest(
        decision_id, expected
    ):
        raise CapabilityDataError("route decision digest or decision_id mismatch")


_ROUTE_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS route_decisions (
    decision_id TEXT PRIMARY KEY
        CHECK(length(decision_id) = 64 AND decision_id NOT GLOB '*[^0-9a-f]*'),
    decision_digest TEXT NOT NULL
        CHECK(length(decision_digest) = 64 AND decision_digest NOT GLOB '*[^0-9a-f]*'),
    task_text_sha256 TEXT NOT NULL
        CHECK(length(task_text_sha256) = 64 AND task_text_sha256 NOT GLOB '*[^0-9a-f]*'),
    task_input_sha256 TEXT NOT NULL
        CHECK(length(task_input_sha256) = 64 AND task_input_sha256 NOT GLOB '*[^0-9a-f]*'),
    route_json TEXT NOT NULL,
    route_json_sha256 TEXT NOT NULL
        CHECK(length(route_json_sha256) = 64 AND route_json_sha256 NOT GLOB '*[^0-9a-f]*'),
    schema_version TEXT NOT NULL,
    manifest_snapshot TEXT NOT NULL,
    decision_snapshot TEXT NOT NULL,
    manifest_authority_sha256 TEXT NOT NULL
        CHECK(length(manifest_authority_sha256) = 64 AND manifest_authority_sha256 NOT GLOB '*[^0-9a-f]*'),
    policy_authority_sha256 TEXT NOT NULL
        CHECK(length(policy_authority_sha256) = 64 AND policy_authority_sha256 NOT GLOB '*[^0-9a-f]*'),
    issued_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK(expires_at > issued_at)
)
"""
_ROUTE_REGISTRY_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_route_decisions_expires_at "
    "ON route_decisions(expires_at)"
)


def _registry_schema_signature(
    connection: sqlite3.Connection,
) -> tuple[tuple[object, ...], tuple[tuple[object, ...], ...]]:
    """Return the exact table, constraint, index, and trigger shape for v3."""

    table_xinfo = tuple(
        tuple(row)
        for row in connection.execute("PRAGMA table_xinfo(route_decisions)").fetchall()
    )
    objects = tuple(
        tuple(row)
        for row in connection.execute(
            """
            SELECT type, name, tbl_name, COALESCE(sql, '')
            FROM sqlite_schema
            WHERE name = 'route_decisions' OR tbl_name = 'route_decisions'
            ORDER BY type, name
            """
        ).fetchall()
    )
    return table_xinfo, objects


def _expected_registry_schema_signature(
) -> tuple[tuple[object, ...], tuple[tuple[object, ...], ...]]:
    """Build the canonical v3 signature from the same DDL used for issuance."""

    expected = sqlite3.connect(":memory:")
    try:
        expected.execute(_ROUTE_REGISTRY_DDL)
        expected.execute(_ROUTE_REGISTRY_INDEX_DDL)
        return _registry_schema_signature(expected)
    finally:
        expected.close()


_EXPECTED_ROUTE_REGISTRY_SCHEMA_SIGNATURE = _expected_registry_schema_signature()


def _registry_schema_is_exact(connection: sqlite3.Connection) -> bool:
    """Reject same-column counterfeits that omit v3 constraints or indexes."""

    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        return bool(
            version == ROUTE_REGISTRY_SCHEMA_VERSION
            and _registry_schema_signature(connection)
            == _EXPECTED_ROUTE_REGISTRY_SCHEMA_SIGNATURE
        )
    except (TypeError, ValueError, sqlite3.Error):
        return False


def _drop_obsolete_registry_schema(connection: sqlite3.Connection) -> None:
    """Remove the route-owned table or view and its named index."""

    row = connection.execute(
        "SELECT type FROM sqlite_schema WHERE name = 'route_decisions'"
    ).fetchone()
    object_type = str(row[0]) if row else ""
    if object_type == "view":
        connection.execute("DROP VIEW route_decisions")
    elif object_type == "table":
        connection.execute("DROP TABLE route_decisions")
    elif object_type == "index":
        connection.execute("DROP INDEX route_decisions")
    elif object_type == "trigger":
        connection.execute("DROP TRIGGER route_decisions")
    connection.execute("DROP INDEX IF EXISTS ix_route_decisions_expires_at")


def _ensure_registry_schema_v3(connection: sqlite3.Connection) -> None:
    """Atomically replace any obsolete receipt schema with the exact v3 table."""

    if not _registry_schema_is_exact(connection):
        _drop_obsolete_registry_schema(connection)
    connection.execute(_ROUTE_REGISTRY_DDL)
    connection.execute(_ROUTE_REGISTRY_INDEX_DDL)
    connection.execute(f"PRAGMA user_version = {ROUTE_REGISTRY_SCHEMA_VERSION}")
    if not _registry_schema_is_exact(connection):
        raise RouteRegistryError("route registry schema does not match canonical v3 DDL")


def _registry_path(value: str | Path | None = None) -> Path:
    return Path(value) if value is not None else ROUTE_DECISION_REGISTRY_PATH


def _issue_route_decision(
    decision: dict[str, Any],
    *,
    registry_path: str | Path | None = None,
    issued_at: int | None = None,
    max_records: int = MAX_REGISTERED_ROUTES,
) -> dict[str, Any]:
    """Transactionally register one schema-valid canonical route without task text."""

    validate_route_decision(decision)
    issuance = decision.get("issuance")
    if not isinstance(issuance, dict) or issuance.get("status") != "registered":
        raise CapabilityDataError("only a route marked registered can be issued")
    now = int(time.time()) if issued_at is None else int(issued_at)
    retention = max(1, min(int(max_records), MAX_REGISTERED_ROUTES))
    expires_at = now + DEFAULT_ROUTE_TTL_SECONDS
    route_json = canonical_route_json(decision)
    route_json_sha256 = hashlib.sha256(route_json.encode("utf-8")).hexdigest()
    path = _registry_path(registry_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RouteRegistryError(f"cannot create route registry directory: {path.parent}") from exc

    last_error: Exception | None = None
    for attempt in range(6):
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                path,
                timeout=10.0,
                isolation_level=None,
            )
            connection.execute("PRAGMA busy_timeout = 10000")
            journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
            if journal_mode.lower() != "wal":
                raise RouteRegistryError(
                    f"route registry did not enter WAL mode: {journal_mode}"
                )
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE")
            _ensure_registry_schema_v3(connection)
            connection.execute(
                "DELETE FROM route_decisions WHERE expires_at < ?",
                (now - EXPIRED_ROUTE_AUDIT_RETENTION_SECONDS,),
            )
            existing = connection.execute(
                "SELECT 1 FROM route_decisions WHERE decision_id = ?",
                (decision["decision_id"],),
            ).fetchone()
            active_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM route_decisions WHERE expires_at >= ?",
                    (now,),
                ).fetchone()[0]
            )
            if existing is None and active_count >= retention:
                raise RouteRegistryError(
                    "route registry active issuance capacity reached; "
                    "no unexpired route was removed"
                )
            connection.execute(
                """
                INSERT INTO route_decisions (
                    decision_id,
                    decision_digest,
                    task_text_sha256,
                    task_input_sha256,
                    route_json,
                    route_json_sha256,
                    schema_version,
                    manifest_snapshot,
                    decision_snapshot,
                    manifest_authority_sha256,
                    policy_authority_sha256,
                    issued_at,
                    expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    decision_digest = excluded.decision_digest,
                    task_text_sha256 = excluded.task_text_sha256,
                    task_input_sha256 = excluded.task_input_sha256,
                    route_json = excluded.route_json,
                    route_json_sha256 = excluded.route_json_sha256,
                    schema_version = excluded.schema_version,
                    manifest_snapshot = excluded.manifest_snapshot,
                    decision_snapshot = excluded.decision_snapshot,
                    manifest_authority_sha256 = excluded.manifest_authority_sha256,
                    policy_authority_sha256 = excluded.policy_authority_sha256,
                    issued_at = excluded.issued_at,
                    expires_at = excluded.expires_at
                """,
                (
                    decision["decision_id"],
                    decision["decision_digest"],
                    decision["task_text_sha256"],
                    decision["task_input_sha256"],
                    route_json,
                    route_json_sha256,
                    decision["schema_version"],
                    decision["manifest_snapshot"],
                    decision["decision_snapshot"],
                    decision["manifest_authority_sha256"],
                    decision["policy_authority_sha256"],
                    now,
                    expires_at,
                ),
            )
            connection.execute(
                """
                DELETE FROM route_decisions
                WHERE decision_id IN (
                    SELECT decision_id
                    FROM route_decisions
                    WHERE expires_at < ?
                    ORDER BY expires_at DESC, issued_at DESC, decision_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (now, retention),
            )
            connection.execute("COMMIT")
            return {
                "valid": True,
                "status": "registered",
                "decision_id": decision["decision_id"],
                "decision_digest": decision["decision_digest"],
                "task_text_sha256": decision["task_text_sha256"],
                "task_input_sha256": decision["task_input_sha256"],
                "execution_disposition": json.loads(
                    canonical_task_input_json(decision["execution_disposition"])
                ),
                "execution_request_id": decision["execution_request_id"],
                "issued_at": now,
                "expires_at": expires_at,
                "registry_path": str(path),
            }
        except (OSError, sqlite3.Error, RouteRegistryError) as exc:
            last_error = exc
            if connection is not None:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            locked = isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()
            if locked and attempt < 5:
                time.sleep(0.025 * (attempt + 1))
                continue
            break
        finally:
            if connection is not None:
                connection.close()
    raise RouteRegistryError(f"cannot issue route registry receipt: {path}: {last_error}")


def _route_capability_ids(decision: dict[str, Any]) -> set[str]:
    identifiers: set[str] = set()

    def add_entry(value: object) -> None:
        if isinstance(value, dict):
            identifier = value.get("id")
            if isinstance(identifier, str) and identifier:
                identifiers.add(identifier)

    add_entry(decision.get("primary"))
    for item in decision.get("supports", []):
        add_entry(item)
    skills = decision.get("skills")
    if isinstance(skills, dict):
        add_entry(skills.get("primary"))
        for item in skills.get("supports", []):
            add_entry(item)
    for fallback in decision.get("capability_fallbacks", []):
        if not isinstance(fallback, dict):
            continue
        for key in ("requested_capability", "chosen_fallback", "selected_capability"):
            identifier = fallback.get(key)
            if isinstance(identifier, str) and identifier:
                identifiers.add(identifier)
    return identifiers


def _selected_route_skill_hashes_current(decision: dict[str, Any]) -> bool:
    """Recheck every projected skill byte source before a new worker admission."""

    disposition = _normalize_execution_disposition(
        decision.get("execution_disposition")
    )
    if not disposition or disposition["mode"] != "worker_support":
        return True
    skills = decision.get("skills")
    if not isinstance(skills, dict):
        return True
    selected: list[dict[str, Any]] = []
    primary = skills.get("primary")
    if isinstance(primary, dict):
        selected.append(primary)
    supports = skills.get("supports")
    if isinstance(supports, list):
        selected.extend(item for item in supports if isinstance(item, dict))
    for entry in selected:
        source = str(entry.get("source_path") or "").strip()
        is_absolute = bool(
            Path(source).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", source)
        )
        if not is_absolute or not _entry_hash_current(entry):
            return False
    return True


def _route_worker_identity_current(
    decision: dict[str, Any], current_manifest: dict[str, Any]
) -> bool:
    disposition = _normalize_execution_disposition(
        decision.get("execution_disposition")
    )
    if not disposition or disposition["mode"] != "worker_support":
        return True
    families = disposition["eligible_worker_families"]
    if len(families) != 1:
        return False
    family = families[0]
    server_id = WORKER_FAMILY_SERVER_IDS.get(family)
    if server_id is None:
        return family == "terra"
    if current_manifest.get("worker_runtime_bom_status") != "current":
        return False
    expected_bom_sha256 = str(
        (current_manifest.get("source_hashes") or {}).get(
            WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
        )
        or ""
    ).lower()
    return _gateway_managed_upstream_configured(
        server_id, expected_bom_sha256=expected_bom_sha256
    )


def verify_registered_route(
    decision: dict[str, Any],
    *,
    registry_path: str | Path | None = None,
    now: int | None = None,
    manifest_path: str | Path | None = None,
    policy_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read-only verification of one exact, current Catalogue Router issuance."""

    decision_id = str(decision.get("decision_id") or "") if isinstance(decision, dict) else ""
    base = {
        "valid": False,
        "status": "schema_invalid",
        "decision_id": decision_id,
        "decision_digest": str(decision.get("decision_digest") or "")
        if isinstance(decision, dict)
        else "",
        "task_text_sha256": str(decision.get("task_text_sha256") or "")
        if isinstance(decision, dict)
        else "",
        "task_input_sha256": str(decision.get("task_input_sha256") or "")
        if isinstance(decision, dict)
        else "",
        "execution_disposition": (
            _normalize_execution_disposition(decision.get("execution_disposition"))
            if isinstance(decision, dict)
            else None
        ),
        "execution_request_id": (
            decision.get("execution_request_id") if isinstance(decision, dict) else None
        ),
        "issued_at": None,
        "expires_at": None,
        "registry_path": str(_registry_path(registry_path)),
    }
    try:
        _validate_route_schema(decision)
        canonical = canonical_route_json(decision)
    except (CapabilityDataError, TypeError, ValueError):
        return base

    expected_digest = _decision_digest(decision)
    if not hmac.compare_digest(str(decision.get("decision_digest") or ""), expected_digest) or not hmac.compare_digest(
        str(decision.get("decision_id") or ""), expected_digest
    ):
        base["status"] = "digest_mismatch"
        return base
    issuance = decision.get("issuance")
    if not isinstance(issuance, dict) or issuance.get("status") != "registered":
        base["status"] = "issuance_failed"
        return base

    try:
        current_manifest = load_active_capabilities(
            Path(manifest_path) if manifest_path is not None else ACTIVE_CAPABILITIES_PATH
        )
        current_policy = load_routing_policy(
            Path(policy_path) if policy_path is not None else ROUTING_POLICY_PATH
        )
    except (CapabilityDataError, OSError, RuntimeError, ValueError):
        base["status"] = "authority_unavailable"
        return base
    manifest_authority_sha256 = _authority_sha256(current_manifest)
    policy_authority_sha256 = _authority_sha256(current_policy)
    if not manifest_authority_sha256 or not policy_authority_sha256:
        base["status"] = "authority_unavailable"
        return base
    if not hmac.compare_digest(
        str(decision.get("manifest_authority_sha256") or ""),
        manifest_authority_sha256,
    ):
        base["status"] = "manifest_mismatch"
        return base
    if not hmac.compare_digest(
        str(decision.get("policy_authority_sha256") or ""),
        policy_authority_sha256,
    ):
        base["status"] = "policy_mismatch"
        return base
    if (
        normalize(current_manifest.get("freshness_status")) not in FRESH_STATES
        or current_manifest.get("source_hashes_verified") is not True
    ):
        base["status"] = "authority_unavailable"
        return base
    dynamic_authority = current_manifest.get("dynamic_authority")
    dynamic_authority = dynamic_authority if isinstance(dynamic_authority, dict) else {}
    quarantined_ids = {
        str(item)
        for item in dynamic_authority.get("quarantined_capability_ids", [])
        if str(item)
    }
    if _route_capability_ids(decision) & quarantined_ids:
        base["status"] = "capability_quarantined"
        return base
    if not _selected_route_skill_hashes_current(decision):
        base["status"] = "capability_quarantined"
        return base
    if not _route_worker_identity_current(decision, current_manifest):
        base["status"] = "capability_quarantined"
        return base

    path = _registry_path(registry_path)
    if not path.is_file():
        base["status"] = "registry_missing"
        return base
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.execute("PRAGMA query_only = ON")
        if not _registry_schema_is_exact(connection):
            base["status"] = "registry_schema_mismatch"
            return base
        row = connection.execute(
            """
            SELECT
                decision_digest,
                task_text_sha256,
                task_input_sha256,
                route_json,
                route_json_sha256,
                schema_version,
                manifest_snapshot,
                decision_snapshot,
                manifest_authority_sha256,
                policy_authority_sha256,
                issued_at,
                expires_at
            FROM route_decisions
            WHERE decision_id = ?
            """,
            (decision["decision_id"],),
        ).fetchone()
    except (OSError, sqlite3.Error):
        base["status"] = "registry_error"
        return base
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        base["status"] = "not_found"
        return base

    (
        stored_digest,
        stored_task_hash,
        stored_task_input_hash,
        stored_route_json,
        stored_route_json_sha256,
        stored_schema_version,
        stored_manifest_snapshot,
        stored_decision_snapshot,
        stored_manifest_authority_sha256,
        stored_policy_authority_sha256,
        issued_at,
        expires_at,
    ) = row
    try:
        issued_at_value = int(issued_at)
        expires_at_value = int(expires_at)
        current = int(time.time()) if now is None else int(now)
    except (TypeError, ValueError, OverflowError):
        base["status"] = "registry_error"
        return base
    base["issued_at"] = issued_at_value
    base["expires_at"] = expires_at_value
    if (
        expires_at_value - issued_at_value != DEFAULT_ROUTE_TTL_SECONDS
        or not issued_at_value <= current <= expires_at_value
    ):
        base["status"] = "expired"
        return base

    stored_route_hash = hashlib.sha256(str(stored_route_json).encode("utf-8")).hexdigest()
    exact_match = all(
        (
            hmac.compare_digest(str(stored_digest), decision["decision_digest"]),
            hmac.compare_digest(str(stored_task_hash), decision["task_text_sha256"]),
            hmac.compare_digest(
                str(stored_task_input_hash), decision["task_input_sha256"]
            ),
            hmac.compare_digest(str(stored_route_json_sha256), stored_route_hash),
            hmac.compare_digest(str(stored_route_json_sha256), hashlib.sha256(canonical.encode("utf-8")).hexdigest()),
            str(stored_route_json) == canonical,
            str(stored_schema_version) == str(decision["schema_version"]),
            str(stored_manifest_snapshot) == str(decision["manifest_snapshot"]),
            str(stored_decision_snapshot) == str(decision["decision_snapshot"]),
            hmac.compare_digest(
                str(stored_manifest_authority_sha256),
                decision["manifest_authority_sha256"],
            ),
            hmac.compare_digest(
                str(stored_policy_authority_sha256),
                decision["policy_authority_sha256"],
            ),
        )
    )
    if not exact_match:
        base["status"] = "route_mismatch"
        return base
    base["valid"] = True
    base["status"] = "registered"
    return base


def _task_fingerprint(prompt: str, classification: dict[str, Any] | None) -> str:
    canonical_prompt = normalize_task_text(prompt)
    payload = {
        "prompt": canonical_prompt,
        "classification": classification if isinstance(classification, dict) else {},
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prepare_task_input(
    prompt: str,
    task_text: str | None,
    task_input: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, str, bool]:
    """Return canonical input, mode, bounded text, and instruction agreement."""

    if task_input is None:
        bounded_text = prompt if task_text is None else task_text
        normalized = normalize_task_text(bounded_text)
        return (
            {"instruction": normalized},
            "conservative_instruction_only",
            normalized,
            True,
        )
    canonical = canonical_task_input_json(task_input)
    exact_input = json.loads(canonical)
    instruction = exact_input.get("instruction")
    instruction_text = instruction if isinstance(instruction, str) else ""
    bounded_text = instruction_text if task_text is None else task_text
    normalized_instruction = normalize_task_text(instruction_text)
    agrees = bool(normalized_instruction) and (
        normalize_task_text(prompt) == normalized_instruction
        and normalize_task_text(bounded_text) == normalized_instruction
    )
    return exact_input, "complete", bounded_text, agrees


def _task_input_requirements_complete(
    task_input: dict[str, Any], requirements: list[str]
) -> bool:
    instruction = task_input.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        return False
    for requirement in requirements:
        if requirement == "instruction":
            continue
        if requirement == "execution_disposition":
            if _normalize_execution_disposition(task_input.get(requirement)) is None:
                return False
            continue
        if requirement == "execution_request_id":
            value = task_input.get(requirement)
            if not isinstance(value, str) or not EXECUTION_REQUEST_ID_PATTERN.fullmatch(
                value
            ):
                return False
            continue
        if requirement in {"query", "extraction_prompt_description"}:
            value = task_input.get(requirement)
            if not isinstance(value, str) or not value.strip():
                return False
            continue
        if requirement == "extraction_examples":
            examples = task_input.get(requirement)
            if not isinstance(examples, list) or not examples:
                return False
            continue
        if requirement == "exactly_one_of:literal_text|extraction_source_path":
            literal = task_input.get("literal_text")
            source_path = task_input.get("extraction_source_path")
            has_literal = isinstance(literal, str) and bool(literal)
            has_source = isinstance(source_path, str) and bool(source_path.strip())
            if has_literal == has_source:
                return False
            continue
        return False
    return True


def _fail_closed_local_execution(local_execution: dict[str, Any]) -> dict[str, Any]:
    """Preserve classification evidence while removing every executable directive."""

    failed = dict(local_execution)
    failed.update(
        {
            "admitted": False,
            "recipe_id": None,
            "local_stack_purpose": None,
            "worker_roles": [],
            "source_need": "none",
            "requested_source_scopes": [],
            "exact_evidence": False,
            "task_input_requirements": [],
            "memory": {
                "mode": "none",
                "scope": None,
                "capture_when": "durable_task_outcome",
            },
        }
    )
    return failed


def route_execution_ready(
    decision: dict[str, Any],
    *,
    task_text: object | None = None,
    task_input: dict[str, Any] | None = None,
) -> bool:
    """Authorize execution only against the canonical current authorities and registry."""

    return _route_execution_ready_with_runtime(
        decision,
        task_text=task_text,
        task_input=task_input,
    )


def _route_execution_ready_with_runtime(
    decision: dict[str, Any],
    *,
    task_text: object | None,
    task_input: dict[str, Any] | None,
    registry_path: str | Path | None = None,
    now: int | None = None,
    manifest_path: str | Path | None = None,
    policy_path: str | Path | None = None,
) -> bool:
    """Internal verifier with path/time overrides for isolated contract tests."""

    try:
        validate_route_decision(decision)
    except CapabilityDataError:
        return False
    issuance = decision.get("issuance")
    if not isinstance(issuance, dict):
        return False
    requested = issuance.get("worker_execution_requested") is True
    if not requested:
        return issuance.get("status") == "registered"
    if task_text is None or task_input is None:
        return False
    support_workers = decision.get("support_workers")
    support_workers = support_workers if isinstance(support_workers, list) else []
    local_execution = decision.get("local_execution")
    local_execution = local_execution if isinstance(local_execution, dict) else {}
    has_local_worker = any(
        isinstance(worker, dict)
        and worker.get("execution_owner") == "local_agent_stack"
        for worker in support_workers
    )
    local_admitted = local_execution.get("admitted") is True
    runnable = bool(support_workers) or local_admitted
    structurally_ready = bool(
        runnable
        and (not has_local_worker or local_admitted)
        and decision.get("task_input_mode") == "complete"
        and issuance.get("status") == "registered"
        and issuance.get("failure_code") is None
        and SHA256_PATTERN.fullmatch(
            str(decision.get("manifest_authority_sha256") or "")
        )
        is not None
        and SHA256_PATTERN.fullmatch(
            str(decision.get("policy_authority_sha256") or "")
        )
        is not None
    )
    if not structurally_ready:
        return False
    if not route_task_text_matches(decision, task_text) or not route_task_input_matches(
        decision, task_input
    ):
        return False
    receipt = verify_registered_route(
        decision,
        registry_path=registry_path,
        now=now,
        manifest_path=manifest_path,
        policy_path=policy_path,
    )
    return receipt.get("valid") is True and receipt.get("status") == "registered"


def _execution_profile_for_rule(
    rule: dict[str, Any] | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    profiles = policy.get("execution_profiles")
    if not isinstance(profiles, dict) or not profiles:
        return _normalize_execution_profile(DEFAULT_EXECUTION_PROFILE)
    profile_id = str((rule or {}).get("execution_profile") or "").strip()
    if not profile_id:
        profile_id = str(policy.get("default_execution_profile") or "")
    return _normalize_execution_profile(profiles.get(profile_id))


def _worker_family(worker: dict[str, Any]) -> str | None:
    owner = str(worker.get("execution_owner") or "")
    role = str(worker.get("role") or "")
    if owner == "local_agent_stack":
        return "local_agent_stack"
    if owner == "codex_child" and role == "read_heavy":
        return "terra"
    if owner == "codex_child" and role == "independent_challenger":
        return "antigravity"
    return None


def _worker_rule_attempted(
    prompt_lower: str,
    classification: dict[str, Any] | None,
    rules: Iterable[dict[str, Any]],
) -> bool:
    flags = _classification_flags(classification)
    return any(_worker_rule_matches(rule, prompt_lower, flags) for rule in rules)


def _select_support_workers(
    prompt_lower: str,
    classification: dict[str, Any] | None,
    eligible_worker_families: set[str],
    policy: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_alias: dict[str, list[dict[str, Any]]],
    worker_runtime_bom_sha256: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    flags = _classification_flags(classification)
    excluded_roles, exclusion_reasons = _worker_exclusions(prompt_lower, flags)
    max_workers = max(
        0,
        min(
            int(policy.get("max_worker_supports", DEFAULT_MAX_WORKER_SUPPORTS)),
            ABSOLUTE_MAX_WORKER_SUPPORTS,
        ),
    )
    workers: list[dict[str, Any]] = []
    reasons: list[str] = list(exclusion_reasons)
    validated_roles = {
        family: _validated_worker_roles(classification, family)
        for family in eligible_worker_families
    }
    if any(roles is None for roles in validated_roles.values()):
        reasons.append("WORKER_TASK_GATE_TUPLE_INVALID")
        return [], reasons
    seen: set[tuple[str, str, str]] = set()
    eligible_count = 0
    for rule in policy.get("worker_rules", []):
        configured_worker = rule.get("worker", {})
        family = _worker_family(configured_worker)
        if family not in eligible_worker_families:
            continue
        allowed_roles = validated_roles.get(family)
        if allowed_roles is None or str(configured_worker.get("role") or "") not in allowed_roles:
            continue
        # Prompt text may disambiguate an already eligible rule, but it can
        # never create Task Gate eligibility by itself.
        if not _rule_classification_matches(rule, flags):
            continue
        if not _worker_rule_matches(rule, prompt_lower, flags):
            continue
        owner = str(configured_worker.get("execution_owner") or "")
        role = str(configured_worker.get("role") or "")
        if role in excluded_roles:
            continue
        if not _worker_capability_available(
            rule,
            by_id,
            by_alias,
            policy,
            worker_runtime_bom_sha256,
        ):
            if role == "independent_challenger":
                reason = "ANTIGRAVITY_SUPPORT_UNAVAILABLE"
            elif owner == "local_agent_stack":
                reason = "LOCAL_SUPPORT_UNAVAILABLE"
            else:
                reason = "WORKER_SUPPORT_UNAVAILABLE"
            if reason not in reasons:
                reasons.append(reason)
            continue
        eligible_count += 1
        worker = dict(rule.get("worker", {}))
        key = (
            str(worker.get("execution_owner") or ""),
            str(worker.get("role") or ""),
            str(worker.get("model") or ""),
        )
        if key in seen:
            continue
        owner = key[0]
        role = key[1]
        if role == "independent_challenger":
            required_flag = "antigravity_support_required"
        elif role == "read_heavy":
            required_flag = "terra_support_required"
        elif owner == "local_agent_stack":
            required_flag = "local_support_required"
        else:
            required_flag = None
        if required_flag and required_flag in flags:
            worker["required"] = True
        worker["rule_id"] = str(rule.get("id") or "")
        worker["reason_code"] = str(rule.get("reason_code") or "EXACT_SUPPORT_MATCH")
        seen.add(key)
        if len(workers) >= max_workers:
            continue
        workers.append(worker)
        if worker["reason_code"] not in reasons:
            reasons.append(worker["reason_code"])
        if rule.get("exclusive"):
            break
    if eligible_count > max_workers:
        reasons.append("WORKER_SUPPORT_LIMIT_APPLIED")
    worker_families = {_worker_family(worker) for worker in workers}
    if len(worker_families) > 1:
        workers = []
        reasons.append("WORKER_FAMILY_CONFLICT_RETURNED_TO_CODEX")
    return workers, reasons


_PROJECT_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _normalize_project_root(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        raw = str(Path(raw).expanduser().resolve(strict=True))
    except OSError:
        pass
    return raw.replace("/", "\\").rstrip("\\").lower()


def _load_project_scope_map(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load deployment-owned project roots and retrieval scopes.

    The public repository intentionally ships no user or company paths. A live
    deployment may supply an explicit map through CODEX_PROJECT_SCOPE_MAP_PATH.
    Missing or malformed maps fail closed to the generic project only.
    """

    source = path or PROJECT_SCOPE_MAP_PATH
    default = {
        "generic": {
            "roots": [],
            "source_scopes": [],
            "memory_scope": None,
        }
    }
    if not source.is_file():
        return default
    try:
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default
    projects = raw.get("projects") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != "1.0"
        or not isinstance(projects, dict)
    ):
        return default
    normalized: dict[str, dict[str, Any]] = {}
    for raw_id, raw_config in projects.items():
        project_id = normalize(raw_id).replace("-", "_")
        if not _PROJECT_IDENTIFIER.fullmatch(project_id) or not isinstance(raw_config, dict):
            return default
        roots = raw_config.get("roots", [])
        scopes = raw_config.get("source_scopes", [])
        memory_scope = raw_config.get("memory_scope")
        if (
            not isinstance(roots, list)
            or any(not isinstance(item, str) or not item.strip() for item in roots)
            or not isinstance(scopes, list)
            or any(
                not isinstance(item, str)
                or not _PROJECT_IDENTIFIER.fullmatch(normalize(item).replace("-", "_"))
                for item in scopes
            )
            or (
                memory_scope is not None
                and (
                    not isinstance(memory_scope, str)
                    or not _PROJECT_IDENTIFIER.fullmatch(
                        normalize(memory_scope).replace("-", "_")
                    )
                )
            )
        ):
            return default
        if any(
            not Path(item).expanduser().is_absolute()
            or not Path(item).expanduser().is_dir()
            for item in roots
        ):
            return default
        normalized_roots = [_normalize_project_root(item) for item in roots]
        if any(not item for item in normalized_roots) or len(normalized_roots) != len(
            set(normalized_roots)
        ):
            return default
        normalized[project_id] = {
            "roots": normalized_roots,
            "source_scopes": [normalize(item).replace("-", "_") for item in scopes],
            "memory_scope": (
                normalize(memory_scope).replace("-", "_")
                if isinstance(memory_scope, str)
                else None
            ),
        }
    generic = normalized.setdefault(
        "generic", {"roots": [], "source_scopes": [], "memory_scope": None}
    )
    if generic != {"roots": [], "source_scopes": [], "memory_scope": None}:
        return default
    claimed_roots = [
        (project_id, root)
        for project_id, config in normalized.items()
        for root in config["roots"]
    ]
    for index, (project_id, root) in enumerate(claimed_roots):
        for other_project_id, other_root in claimed_roots[index + 1 :]:
            if project_id == other_project_id:
                continue
            if root == other_root:
                return default
    return normalized


PROJECT_SCOPE_MAP = _load_project_scope_map()
PROJECT_SOURCE_SCOPES = {
    project_id: list(config["source_scopes"])
    for project_id, config in PROJECT_SCOPE_MAP.items()
}
PROJECT_MEMORY_SCOPES = {
    project_id: config["memory_scope"]
    for project_id, config in PROJECT_SCOPE_MAP.items()
    if config["memory_scope"] is not None
}
PROJECT_ROOTS = tuple(
    sorted(
        (
            (project_id, root)
            for project_id, config in PROJECT_SCOPE_MAP.items()
            for root in config["roots"]
        ),
        key=lambda item: (-len(item[1]), item[0], item[1]),
    )
)


def _project_from_cwd(raw_cwd: object) -> str:
    normalized_cwd = _normalize_project_root(raw_cwd)
    if not normalized_cwd:
        return "generic"
    for project_id, root in PROJECT_ROOTS:
        if normalized_cwd == root or normalized_cwd.startswith(root + "\\"):
            return project_id
    return "generic"


def _structured_project(
    classification: dict[str, Any] | None,
) -> tuple[str, bool, str | None]:
    """Resolve exact project evidence and reject explicit project/cwd conflicts."""

    if not isinstance(classification, dict):
        return "generic", True, None
    explicit_value = next(
        (
            classification.get(key)
            for key in ("project_id", "project", "project_identity")
            if classification.get(key) is not None
        ),
        None,
    )
    explicit = normalize(explicit_value).replace("-", "_") if explicit_value is not None else ""
    if explicit and explicit not in PROJECT_SOURCE_SCOPES:
        return "generic", False, "PROJECT_CWD_CONFLICT"
    raw_cwd = next(
        (
            classification.get(key)
            for key in ("cwd", "workspace_root", "project_root")
            if classification.get(key)
        ),
        "",
    )
    cwd_project = _project_from_cwd(raw_cwd)
    if explicit:
        if raw_cwd and cwd_project != explicit:
            return explicit, False, "PROJECT_CWD_CONFLICT"
        return explicit, True, None
    return cwd_project, True, None


def _structured_memory_scope(project_id: str) -> str | None:
    return PROJECT_MEMORY_SCOPES.get(project_id)


def _structured_source_scopes(
    classification: dict[str, Any] | None,
    project_id: str,
) -> tuple[list[str], bool, str | None]:
    allowed = PROJECT_SOURCE_SCOPES[project_id]
    source_need = normalize((classification or {}).get("source_need")).replace("-", "_")
    explicit_scopes = _explicit_requested_source_scopes(classification)
    if explicit_scopes is None:
        if source_need in {"index", "both"}:
            return [], False, "SOURCE_SCOPE_UNAUTHORIZED"
        return [], True, None
    requested = explicit_scopes
    if not requested:
        if source_need in {"index", "both"}:
            return [], False, "SOURCE_SCOPE_UNAUTHORIZED"
        return [], True, None
    if source_need not in {"index", "both"}:
        return [], False, "SOURCE_SCOPE_UNAUTHORIZED"
    if project_id == "generic":
        return [], False, "SOURCE_SCOPE_UNAUTHORIZED"
    if any(item not in allowed for item in requested):
        return [], False, "SOURCE_SCOPE_UNAUTHORIZED"
    return requested, True, None


def _classified_value(
    classification: dict[str, Any] | None,
    key: str,
    allowed: set[str],
    default: str,
) -> str:
    value = normalize((classification or {}).get(key)).replace("-", "_")
    return value if value in allowed else default


def _derive_local_execution(
    support_workers: list[dict[str, Any]],
    classification: dict[str, Any] | None,
    task_input: dict[str, Any],
    prompt_lower: str,
    policy: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_alias: dict[str, list[dict[str, Any]]],
    worker_runtime_bom_sha256: str = "",
) -> tuple[dict[str, Any], list[str]]:
    local_workers = [
        worker
        for worker in support_workers
        if worker.get("execution_owner") == "local_agent_stack"
    ]
    roles = tuple(str(worker.get("role") or "") for worker in local_workers)
    recipes = {
        ("fast",): ("fast_transform", "bounded_classification_or_transformation", "transform", {"none", "memory", "index", "both"}, "low"),
        ("fast", "critic"): ("fast_then_critic", "complex_multi_source_synthesis", "synthesize", {"none", "memory", "index", "both"}, "high"),
        ("coding", "critic"): ("coding_then_critic", "focused_coding_assistance", "implement", {"none", "index"}, "medium"),
        ("critic",): ("critic", "explicit_challenge", "review", {"none", "memory", "index", "both"}, "medium"),
    }
    selected = recipes.get(roles)
    selected_rule: dict[str, Any] | None = None
    unavailable_rule = False
    tuple_invalid = False
    if not selected and not local_workers:
        local_rules = list(policy.get("local_execution_rules", []))
        validated_recipe = _validated_local_operation_recipe(classification, task_input)
        if validated_recipe == "retrieval_bundle":
            bundle_dependencies: dict[str, dict[str, Any]] = {}
            for candidate in local_rules:
                recipe = str(candidate.get("recipe_id") or "")
                if recipe not in {"memory_recall", "source_lookup"}:
                    continue
                if not _worker_capability_available(
                    candidate,
                    by_id,
                    by_alias,
                    policy,
                    worker_runtime_bom_sha256,
                ):
                    unavailable_rule = True
                    continue
                bundle_dependencies[recipe] = candidate
            if set(bundle_dependencies) == {"memory_recall", "source_lookup"}:
                selected_rule = {
                    "id": "local-retrieval-bundle-structured",
                    "recipe_id": "retrieval_bundle",
                    "local_stack_purpose": "retrieval_bundle",
                    "task_type": "research",
                    "source_need": "both",
                    "exact_evidence": False,
                    "reason_code": "LOCAL_RETRIEVAL_BUNDLE_STRUCTURED",
                }
        elif validated_recipe is not None:
            selected_rule = next(
                (
                    candidate
                    for candidate in local_rules
                    if str(candidate.get("recipe_id") or "") == validated_recipe
                ),
                None,
            )
            if selected_rule is not None and not _worker_capability_available(
                selected_rule,
                by_id,
                by_alias,
                policy,
                worker_runtime_bom_sha256,
            ):
                unavailable_rule = True
                selected_rule = None
        elif _worker_rule_attempted(prompt_lower, classification, local_rules):
            tuple_invalid = True

    project_id, project_valid, project_reason = _structured_project(classification)
    memory_scope = _structured_memory_scope(project_id)
    source_scopes, source_scope_valid, source_scope_reason = _structured_source_scopes(
        classification, project_id
    )
    if selected:
        recipe_id, purpose, _default_task_type, allowed_source_needs, _default_complexity = selected
        raw_source_need = normalize((classification or {}).get("source_need")).replace("-", "_")
        requested_source_need = (
            raw_source_need if raw_source_need in allowed_source_needs else "none"
        )
        task_type = normalize((classification or {}).get("task_type")).replace("-", "_")
        complexity = normalize((classification or {}).get("complexity")).replace("-", "_")
        exact_evidence = bool((classification or {}).get("exact_evidence", False))
    elif selected_rule:
        recipe_id = selected_rule["recipe_id"]
        purpose = selected_rule["local_stack_purpose"]
        requested_source_need = normalize(
            (classification or {}).get("source_need")
        ).replace("-", "_")
        task_type = normalize((classification or {}).get("task_type")).replace("-", "_")
        complexity = normalize((classification or {}).get("complexity")).replace("-", "_")
        exact_evidence = bool((classification or {}).get("exact_evidence") is True)
        selected = (recipe_id, purpose, task_type, {requested_source_need}, complexity)
    else:
        recipe_id = None
        purpose = None
        task_type = _classified_value(
            classification,
            "task_type",
            {"answer", "transform", "recall", "research", "synthesize", "implement", "review", "extract", "status"},
            "answer",
        )
        complexity = _classified_value(
            classification, "complexity", {"low", "medium", "high"}, "medium"
        )
        requested_source_need = "none"
        exact_evidence = False

    scope_valid = requested_source_need not in {"memory", "both"} or memory_scope is not None
    sources_valid = requested_source_need not in {"index", "both"} or bool(source_scopes)
    evidence_valid = recipe_id != "literal_extraction" or exact_evidence
    admitted = bool(selected) and all(
        (
            project_valid,
            source_scope_valid,
            scope_valid,
            sources_valid,
            evidence_valid,
        )
    )
    raw_memory = (classification or {}).get("memory")
    nested_mode = raw_memory.get("mode") if isinstance(raw_memory, dict) else raw_memory
    requested_mode = normalize(
        (classification or {}).get("memory_mode") or nested_mode
    ).replace("-", "_")
    if admitted and requested_source_need in {"memory", "both"} and memory_scope:
        mode = requested_mode
    else:
        mode = "none"
    if admitted and requested_source_need in {"memory", "both"} and mode not in {
        "recall",
        "recall_and_capture",
    }:
        admitted = False
        tuple_invalid = True
        mode = "none"
    if mode == "none":
        memory_scope = None
    if admitted and recipe_id == "literal_extraction":
        task_input_requirements = [
            "instruction",
            "execution_disposition",
            "execution_request_id",
            "extraction_prompt_description",
            "extraction_examples",
            "exactly_one_of:literal_text|extraction_source_path",
        ]
    elif admitted and recipe_id in {"memory_recall", "source_lookup", "retrieval_bundle"}:
        task_input_requirements = [
            "instruction",
            "execution_disposition",
            "execution_request_id",
            "query",
        ]
    elif admitted:
        task_input_requirements = [
            "instruction",
            "execution_disposition",
            "execution_request_id",
        ]
    else:
        task_input_requirements = []
    directive = {
        "admitted": admitted,
        "recipe_id": recipe_id if admitted else None,
        "local_stack_purpose": purpose if admitted else None,
        "worker_roles": list(roles) if admitted else [],
        "project_id": project_id,
        "task_type": task_type,
        "source_need": requested_source_need if admitted else "none",
        "requested_source_scopes": source_scopes if admitted and requested_source_need in {"index", "both"} else [],
        "complexity": complexity,
        "exact_evidence": exact_evidence if admitted else False,
        "task_input_requirements": task_input_requirements,
        "memory": {
            "mode": mode,
            "scope": memory_scope,
            "capture_when": "durable_task_outcome",
        },
    }
    if admitted:
        expected_scopes = _explicit_requested_source_scopes(classification) or []
        expected_persistence = _normalized_classification_field(
            classification, "persistence_intent"
        )
        directive_matches = all(
            (
                directive["project_id"]
                == _normalized_classification_field(classification, "project_id"),
                directive["task_type"]
                == _normalized_classification_field(classification, "task_type"),
                directive["complexity"]
                == _normalized_classification_field(classification, "complexity"),
                directive["local_stack_purpose"]
                == _normalized_classification_field(
                    classification, "local_stack_purpose"
                ),
                directive["source_need"]
                == _normalized_classification_field(classification, "source_need"),
                directive["requested_source_scopes"] == expected_scopes,
                directive["memory"]["mode"]
                == _normalized_classification_field(classification, "memory_mode"),
                expected_persistence
                == (
                    "requested"
                    if directive["memory"]["mode"] == "recall_and_capture"
                    else "none"
                ),
            )
        )
        if not directive_matches:
            directive = _fail_closed_local_execution(directive)
            admitted = False
            tuple_invalid = True
    reasons: list[str] = []
    if project_reason:
        reasons.append(project_reason)
    if source_scope_reason:
        reasons.append(source_scope_reason)
    if local_workers and not selected:
        reasons.append("LOCAL_RECIPE_UNRESOLVED")
    elif tuple_invalid:
        reasons.append("LOCAL_TASK_GATE_TUPLE_INVALID")
    elif unavailable_rule:
        reasons.append("LOCAL_SUPPORT_UNAVAILABLE")
    elif selected and not admitted:
        reasons.append(
            "LOCAL_EXACT_EVIDENCE_REQUIRED"
            if not evidence_valid
            else "LOCAL_EXECUTION_SCOPE_UNAVAILABLE"
        )
    elif admitted:
        if recipe_id is None:
            raise CapabilityDataError("admitted local execution has no recipe")
        reasons.append(
            str(selected_rule.get("reason_code"))
            if selected_rule
            else f"LOCAL_RECIPE_{recipe_id.upper()}"
        )
        reasons.append("MEMORY_SCOPE_MAPPED" if memory_scope else "MEMORY_SCOPE_NONE")
    return directive, reasons


def _build_decision(
    *,
    rule: dict[str, Any] | None,
    primary: dict[str, Any] | None,
    supports: list[dict[str, Any]],
    support_workers: list[dict[str, Any]],
    local_execution: dict[str, Any],
    execution_disposition: dict[str, Any],
    execution_request_id: str | None,
    task_text_sha256: str,
    task_input_sha256: str,
    task_input_mode: str,
    task_fingerprint: str,
    issuance_status: str,
    worker_execution_requested: bool,
    issuance_failure_code: str | None,
    reason_codes: list[str],
    capability_fallbacks: list[dict[str, Any]],
    manifest: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    profile = _execution_profile_for_rule(rule, policy)
    public_primary = _public_entry(primary)
    public_supports = [item for item in (_public_entry(entry) for entry in supports) if item]
    def skill_ref(item: dict[str, Any] | None) -> dict[str, str] | None:
        if (
            not item
            or normalize(item.get("kind")) != "skill"
            or not str(item.get("source_path") or "").lower().endswith("skill.md")
            or not re.fullmatch(r"[A-Fa-f0-9]{64}", str(item.get("sha256") or ""))
        ):
            return None
        return {
            "id": item["id"],
            "source_path": item["source_path"],
            "sha256": item["sha256"].lower(),
        }

    primary_skill_ref = skill_ref(public_primary)
    support_skill_refs = [
        ref for ref in (skill_ref(item) for item in public_supports) if ref
    ][:2]
    local_execution = dict(local_execution)
    local_execution.update(
        {
            "mutation_authorized": False,
            "persistence_intent": (
                "requested"
                if local_execution.get("memory", {}).get("mode") == "recall_and_capture"
                else "none"
            ),
            "local_stack_role": "support",
            "skill_refs": {
                "primary": primary_skill_ref,
                "supports": support_skill_refs,
            },
        }
    )
    unique_reasons = list(dict.fromkeys(reason_codes or ["CODEX_SOL_DEFAULT"]))
    decision = {
        "schema_version": "3.0",
        "rule_id": str((rule or {}).get("id") or ""),
        "scenario": str((rule or {}).get("scenario") or "Conservative Codex default"),
        "execution_owner": profile["execution_owner"],
        "model": profile["model"],
        "reasoning_effort": profile["reasoning_effort"],
        "support_workers": support_workers,
        "local_execution": local_execution,
        "execution_disposition": execution_disposition,
        "execution_request_id": execution_request_id,
        "skills": {
            "primary": public_primary,
            "supports": public_supports,
        },
        "capability_fallbacks": capability_fallbacks,
        "reason_codes": unique_reasons,
        "deadline_seconds": profile["deadline_seconds"],
        "fallback": profile["fallback"],
        "requires": list((rule or {}).get("requires", [])),
        "authority_limit": str(
            (rule or {}).get("authority_limit")
            or "Advisory only. The latest user request and controlling instructions remain authoritative."
        ),
        "evidence_ids": list((rule or {}).get("evidence_ids", [])),
        "manifest_snapshot": str(manifest.get("snapshot_id") or ""),
        "decision_snapshot": str(policy.get("decision_snapshot") or ""),
        "manifest_authority_sha256": _authority_sha256(manifest),
        "policy_authority_sha256": _authority_sha256(policy),
        "task_text_sha256": task_text_sha256,
        "task_input_sha256": task_input_sha256,
        "task_input_mode": task_input_mode,
        "issuance": {
            "status": issuance_status,
            "registry_schema_version": ROUTE_REGISTRY_SCHEMA_VERSION,
            "worker_execution_requested": worker_execution_requested,
            "failure_code": issuance_failure_code,
        },
        "task_fingerprint": task_fingerprint,
        "decision_id": "",
        "decision_digest": "",
        "primary": public_primary,
        "supports": public_supports,
    }
    digest = _decision_digest(decision)
    decision["decision_id"] = digest
    decision["decision_digest"] = digest
    validate_route_decision(decision)
    return decision


def conservative_default_decision(
    reason_code: str = "ROUTER_FAIL_OPEN",
    *,
    prompt: str = "",
    classification: dict[str, Any] | None = None,
    task_text: str | None = None,
    task_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "snapshot_id": "",
        "freshness_status": "missing",
        "source_hashes_verified": False,
        "authority_sha256": "",
        "entries": [],
    }
    policy = {
        "decision_snapshot": "",
        "authority_sha256": "",
        "default_execution_profile": "codex-sol-default",
        "execution_profiles": {"codex-sol-default": DEFAULT_EXECUTION_PROFILE},
    }
    exact_input, input_mode, bounded_text, instruction_agrees = _prepare_task_input(
        prompt, task_text, task_input
    )
    reasons = ["CODEX_SOL_DEFAULT", reason_code]
    failure_code = None
    if input_mode == "complete" and not instruction_agrees:
        input_mode = "conservative_instruction_only"
        failure_code = "TASK_INPUT_INSTRUCTION_MISMATCH"
        reasons.append(failure_code)
    local_execution = {
        "admitted": False,
        "recipe_id": None,
        "local_stack_purpose": None,
        "worker_roles": [],
        "project_id": "generic",
        "task_type": "answer",
        "source_need": "none",
        "requested_source_scopes": [],
        "complexity": "medium",
        "exact_evidence": False,
        "task_input_requirements": [],
        "memory": {
            "mode": "none",
            "scope": None,
            "capture_when": "durable_task_outcome",
        },
    }
    build_args = {
        "rule": None,
        "primary": None,
        "supports": [],
        "support_workers": [],
        "local_execution": local_execution,
        "execution_disposition": dict(CODEX_ONLY_EXECUTION_DISPOSITION),
        "execution_request_id": None,
        "task_text_sha256": compute_task_text_sha256(bounded_text),
        "task_input_sha256": compute_task_input_sha256(exact_input),
        "task_input_mode": input_mode,
        "task_fingerprint": _task_fingerprint(prompt, classification),
        "worker_execution_requested": False,
        "reason_codes": reasons,
        "capability_fallbacks": [],
        "manifest": manifest,
        "policy": policy,
    }
    del failure_code
    build_args["reason_codes"] = [*reasons, "AUTHORITY_UNAVAILABLE"]
    return _build_decision(
        **build_args,
        issuance_status="failed",
        issuance_failure_code="AUTHORITY_UNAVAILABLE",
    )


def _canonical_authority_payload(value: dict[str, Any]) -> str:
    """Serialize the routed authority content without its provenance wrapper."""

    if not isinstance(value, dict):
        raise CapabilityDataError("authority input must be an object")
    payload = {
        key: item
        for key, item in value.items()
        if key not in {"source", "authority_sha256"}
    }
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityDataError(f"authority input is not canonical JSON data: {exc}") from exc


def _rebind_supplied_authority(
    supplied: dict[str, Any] | None,
    *,
    canonical_path: Path,
    loader: Callable[[Path | None], dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    """Reload provenance-bearing authority and reject mutable injected wrappers."""

    if supplied is None:
        return loader(canonical_path)
    if not isinstance(supplied, dict):
        raise CapabilityDataError(f"{label} authority must be an object")

    has_provenance = "source" in supplied or "authority_sha256" in supplied
    if not has_provenance:
        return json.loads(_canonical_authority_payload(supplied))

    source = str(supplied.get("source") or "").strip()
    supplied_hash = str(supplied.get("authority_sha256") or "").lower()
    if not source or not SHA256_PATTERN.fullmatch(supplied_hash):
        raise CapabilityDataError(f"{label} authority provenance is incomplete")
    try:
        source_path = Path(source).resolve(strict=True)
        expected_path = canonical_path.resolve(strict=True)
    except OSError as exc:
        raise CapabilityDataError(f"{label} authority source is unavailable") from exc
    if source_path != expected_path:
        raise CapabilityDataError(f"{label} authority source is not canonical")

    current = loader(canonical_path)
    current_hash = _authority_sha256(current)
    if not current_hash or not hmac.compare_digest(supplied_hash, current_hash):
        raise CapabilityDataError(f"{label} authority changed after it was loaded")
    if not hmac.compare_digest(
        _canonical_authority_payload(supplied).encode("utf-8"),
        _canonical_authority_payload(current).encode("utf-8"),
    ):
        raise CapabilityDataError(f"{label} authority was mutated after it was loaded")
    return current


def _route_authority_issuable(
    manifest: dict[str, Any], policy: dict[str, Any]
) -> bool:
    """Require fresh manifest evidence and both exact authority hashes before write."""

    return bool(
        normalize(manifest.get("freshness_status")) in FRESH_STATES
        and manifest.get("source_hashes_verified") is True
        and SHA256_PATTERN.fullmatch(_authority_sha256(manifest))
        and SHA256_PATTERN.fullmatch(_authority_sha256(policy))
    )


def _synthetic_authority_input(value: dict[str, Any]) -> bool:
    """Identify test-only authority objects that have no live source provenance."""

    return "source" not in value and "authority_sha256" not in value


def resolve_route(
    prompt: str,
    manifest: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    classification: dict[str, Any] | None = None,
    task_text: str | None = None,
    task_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one unified advisory decision from the canonical Catalogue Router."""

    exact_input, input_mode, bounded_text, instruction_agrees = _prepare_task_input(
        prompt, task_text, task_input
    )
    manifest = _rebind_supplied_authority(
        manifest,
        canonical_path=ACTIVE_CAPABILITIES_PATH,
        loader=load_active_capabilities,
        label="active capability manifest",
    )
    policy = _rebind_supplied_authority(
        policy,
        canonical_path=ROUTING_POLICY_PATH,
        loader=load_routing_policy,
        label="routing policy",
    )
    entries = manifest.get("entries") if isinstance(manifest, dict) else []
    rules = policy.get("rules") if isinstance(policy, dict) else []
    entries = entries if isinstance(entries, list) else []
    rules = rules if isinstance(rules, list) else []
    worker_runtime_bom_sha256 = str(
        ((manifest.get("source_hashes") or {}) if isinstance(manifest, dict) else {}).get(
            WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
        )
        or ""
    ).lower()
    by_id, by_alias = _build_lookup(entries)
    prompt_lower = prompt.lower()
    max_supports = max(
        0,
        min(int(policy.get("max_supports", DEFAULT_MAX_SUPPORTS)), ABSOLUTE_MAX_SUPPORTS),
    )

    selected_rule: dict[str, Any] | None = None
    selected_primary: dict[str, Any] | None = None
    selected_supports: list[dict[str, Any]] = []
    capability_fallbacks: list[dict[str, Any]] = []
    capability_fallback_reasons: list[str] = []
    live_config_loaded = False
    live_config: dict[str, Any] | None = None
    live_dependency_probes = (
        exact_input.get("live_dependency_probes") if instruction_agrees else None
    )
    probe_execution_request_id = (
        exact_input.get("execution_request_id") if instruction_agrees else None
    )
    split_security_surfaces = _security_surfaces_requiring_split(prompt)
    route_guard_reasons: list[str] = []
    if split_security_surfaces:
        route_guard_reasons.extend([
            "SECURITY_SURFACES_REQUIRE_SPLIT_TASK",
            "SECURITY_SURFACES_"
            + "_".join(surface.upper() for surface in split_security_surfaces),
        ])
    split_tracker_destinations = _tracker_destinations_requiring_split(prompt)
    if split_tracker_destinations:
        route_guard_reasons.extend([
            "TRACKER_DESTINATIONS_REQUIRE_SPLIT_TASK",
            "TRACKER_DESTINATIONS_"
            + "_".join(
                destination.upper() for destination in split_tracker_destinations
            ),
        ])
    split_security_phases = _security_phases_requiring_split(prompt)
    if split_security_phases:
        route_guard_reasons.extend([
            "SECURITY_PHASES_REQUIRE_SPLIT_TASK",
            "SECURITY_PHASES_"
            + "_".join(phase.upper() for phase in split_security_phases),
        ])
    for rule in rules:
        if route_guard_reasons:
            break
        if not _rule_matches_prompt(rule, prompt_lower, policy):
            continue
        if not _rule_requirements_met(rule, prompt_lower, by_id, by_alias, policy):
            continue
        requested_reference = str(rule.get("primary") or "").strip()
        primary = _resolve_reference(requested_reference, by_id, by_alias)
        primary_usable = _entry_usable(
            primary, prompt=prompt, policy=policy, rule=rule
        )
        required_dependencies = list(rule.get("requires_live_dependencies", []))
        unavailable_dependencies: list[str] = []
        if required_dependencies:
            if not live_config_loaded:
                live_config = _load_live_config_inventory()
                live_config_loaded = True
            unavailable_dependencies = _unavailable_live_dependencies(
                required_dependencies,
                manifest=manifest,
                policy=policy,
                by_id=by_id,
                by_alias=by_alias,
                config=live_config,
                probes=live_dependency_probes,
                execution_request_id=probe_execution_request_id,
                prompt=prompt,
            )
            if not primary_usable:
                unavailable_dependencies.append(f"capability:{requested_reference}")
            unavailable_dependencies = list(dict.fromkeys(unavailable_dependencies))

        if required_dependencies and unavailable_dependencies:
            dependency_fallback = _normalize_dependency_fallback(
                rule.get("dependency_fallback"),
                requested_reference,
            )
            if dependency_fallback is None:
                selected_rule = rule
                capability_fallback_reasons.extend(
                    [
                        "CAPABILITY_DEPENDENCY_FALLBACK",
                        "CAPABILITY_FALLBACK_SEMANTIC_INVALID",
                        "CAPABILITY_FALLBACK_UNAVAILABLE_RETURNED_TO_CODEX",
                    ]
                )
                break
            fallback_reference = str(
                dependency_fallback.get("selected_capability") or ""
            ).strip()
            fallback_entry = (
                _resolve_reference(fallback_reference, by_id, by_alias)
                if fallback_reference
                else None
            )
            fallback_usable = (
                _entry_usable(
                    fallback_entry, prompt=prompt, policy=policy, rule=rule
                )
                if fallback_reference
                else True
            )
            equivalent_semantics_valid = _equivalent_fallback_semantics_valid(
                dependency_fallback,
                requested_entry=primary,
                fallback_entry=fallback_entry,
                by_id=by_id,
                by_alias=by_alias,
            )
            if not equivalent_semantics_valid:
                selected_rule = rule
                capability_fallback_reasons.extend(
                    [
                        "CAPABILITY_DEPENDENCY_FALLBACK",
                        "CAPABILITY_FALLBACK_SEMANTIC_INVALID",
                        "CAPABILITY_FALLBACK_UNAVAILABLE_RETURNED_TO_CODEX",
                    ]
                )
                break
            if fallback_reference and not fallback_usable:
                unavailable_dependencies.append(f"capability:{fallback_reference}")
            selected_rule = rule
            selected_primary = fallback_entry if fallback_usable else None
            selected_supports = _resolve_supports(
                dependency_fallback.get("supports", []),
                prompt=prompt,
                primary=selected_primary,
                max_supports=max_supports,
                policy=policy,
                rule=rule,
                by_id=by_id,
                by_alias=by_alias,
            )
            actual_selected = (
                str(selected_primary.get("id") or "") if selected_primary else None
            )
            capability_fallbacks.append(
                {
                    "requested_capability": requested_reference,
                    "requested_capability_available": primary_usable,
                    "required_dependencies": required_dependencies,
                    "unavailable_dependencies": list(
                        dict.fromkeys(unavailable_dependencies)
                    ),
                    "chosen_fallback": dependency_fallback["chosen_fallback"],
                    "selected_capability": actual_selected,
                    "equivalence": (
                        dependency_fallback["equivalence"]
                        if fallback_usable
                        else "non_equivalent"
                    ),
                    "bounds": {
                        "max_passes": dependency_fallback["max_passes"],
                        "deadline_seconds": dependency_fallback[
                            "deadline_seconds"
                        ],
                    },
                    "reason_code": dependency_fallback["reason_code"],
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
        worker_runtime_bom_sha256,
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
        worker_runtime_bom_sha256,
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
    elif normalize(manifest.get("freshness_status")) == "degraded":
        reasons.append("CAPABILITY_AUTHORITY_DEGRADED")
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
