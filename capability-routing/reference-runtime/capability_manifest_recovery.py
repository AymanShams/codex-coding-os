#!/usr/bin/env python3
"""Deterministic, fail-closed recovery for the canonical capability manifest."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any, Callable

import capability_config_fingerprint as config_fingerprint
import capability_index as index


RECEIPT_SCHEMA = "capability-authority-receipt-v2"
SESSION_START_RECEIPT_SCHEMA = "capability-session-start-recovery-v1"
SESSION_START_RECEIPT_LIMIT = 24
CURATED_PLUGIN_CACHE_MARKETPLACE = "openai-curated-remote"
CURATED_PLUGIN_CONFIG_MARKETPLACE_ALIASES = (
    CURATED_PLUGIN_CACHE_MARKETPLACE,
    "openai-curated",
)
REMOTE_PLUGIN_INSTALL_RECEIPT = ".codex-remote-plugin-install.json"
PLUGIN_INSTALL_STAGING_PREFIX = "plugin-install-"
MUTEX_NAME = r"Local\OpenAI.Codex.CapabilityManifestRecovery.v1"
RECOVERABLE_MISMATCHES = frozenset(
    {config_fingerprint.SOURCE_HASH_KEY, "plugin-cache-inventory"}
)
ALLOWED_CONFIG_LEAF_PATHS = frozenset(
    {
        "/mcp_servers/node_repl/command",
        "/mcp_servers/node_repl/env/NODE_REPL_NODE_MODULE_DIRS",
        "/mcp_servers/node_repl/env/NODE_REPL_NODE_PATH",
        "/mcp_servers/node_repl/env/NODE_REPL_TRUSTED_CODE_PATHS",
        "/mcp_servers/node_repl/env/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
        "/mcp_servers/node_repl/env/BROWSER_USE_CODEX_APP_VERSION",
        "/mcp_servers/node_repl/env/CODEX_CLI_PATH",
        "/shell_environment_policy/set/NODE_REPL_TRUSTED_CODE_PATHS",
        "/shell_environment_policy/set/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
    }
)
REQUIRED_APP_CONFIG_PATHS = frozenset(
    path for path in ALLOWED_CONFIG_LEAF_PATHS if path.startswith("/mcp_servers/")
)
APP_PLUGIN_NAMES = ("browser", "chrome", "computer-use")
SHA256_PATTERN = re.compile(r"^[A-Fa-f0-9]{64}$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
REQUIRED_SOURCE_HASH_KEYS = frozenset(
    {
        "hooks.json",
        "AGENTS.md",
        "task-routing-gate.md",
        "catalogue-router.SKILL.md",
        "capability_index.py",
        "capability_config_fingerprint.py",
        "capability_index_cli.py",
        "user_prompt_skill_router.py",
        "capability_index_session_start.py",
        "_common.py",
        "_hook_io.py",
        "query-catalogue.ps1",
        "routing-policy.yaml",
        "routing-policy.schema.json",
        "active-capabilities.schema.json",
        "project-scope-map.json",
        "project-scope-map.schema.json",
        "route-decision.schema.json",
        "ensure-node-dependencies.ps1",
        "capability_manifest_recovery.py",
        "capability-manifest-builder.ps1",
        "authority-receipt.schema.json",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    try:
        return index._sha256_file(path) if path.is_file() else ""
    except OSError:
        return ""


def authority_snapshot_digest(receipt: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in receipt.items()
        if key not in {"snapshot_sha256", "newest_authority_mtime_ns"}
    }
    return _sha256_text(_canonical_json(payload))


def _valid_hash_map(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str)
        and isinstance(digest, str)
        and SHA256_PATTERN.fullmatch(digest) is not None
        for key, digest in value.items()
    )


def _valid_plugin_capability_surfaces(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for package, capabilities in value.items():
        if not isinstance(package, str) or not package:
            return False
        if not isinstance(capabilities, list) or not capabilities:
            return False
        seen: set[tuple[str, str]] = set()
        for capability in capabilities:
            if not isinstance(capability, dict) or set(capability) != {"id", "kind"}:
                return False
            identifier = capability.get("id")
            kind = capability.get("kind")
            if (
                not isinstance(identifier, str)
                or not identifier
                or kind not in {"plugin", "skill", "tool-family", "mcp"}
                or (kind, identifier) in seen
            ):
                return False
            seen.add((kind, identifier))
    return True


def _valid_plugin_package_manifests(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for package, identity in value.items():
        if _normalized_plugin_package(package) is None or not isinstance(identity, dict):
            return False
        if set(identity) != {"name", "version", "manifest_sha256"}:
            return False
        if not isinstance(identity.get("name"), str) or not identity["name"]:
            return False
        if not isinstance(identity.get("version"), str) or not identity["version"]:
            return False
        if not isinstance(identity.get("manifest_sha256"), str) or not SHA256_PATTERN.fullmatch(
            identity["manifest_sha256"]
        ):
            return False
    return True


def _valid_plugin_package_origins(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for package, origin in value.items():
        normalized = _normalized_plugin_package(package)
        if normalized is None or not normalized.startswith(
            CURATED_PLUGIN_CACHE_MARKETPLACE + "/"
        ):
            return False
        if not isinstance(origin, dict) or set(origin) != {
            "remote_plugin_id",
            "install_receipt_sha256",
        }:
            return False
        remote_plugin_id = origin.get("remote_plugin_id")
        if (
            not isinstance(remote_plugin_id, str)
            or not re.fullmatch(r"[-A-Za-z0-9._:~]{8,256}", remote_plugin_id)
        ):
            return False
        digest = origin.get("install_receipt_sha256")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            return False
    return True


def _normalized_plugin_package(value: str) -> str | None:
    normalized = value.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if (
        len(parts) != 3
        or any(not part or part in {".", ".."} for part in parts)
    ):
        return None
    return normalized.casefold()


def _plugin_cache_rows_match_surfaces(
    rows: dict[str, str], surfaces: dict[str, list[dict[str, str]]]
) -> bool:
    """Prove every cache row belongs to exactly one declared package surface."""

    roots: set[str] = set()
    parsed_rows: list[tuple[str, str]] = []
    for key in rows:
        try:
            kind, relative = key.split("\t", 1)
        except ValueError:
            return False
        if kind not in {"ROOT", "FILE"}:
            return False
        normalized_relative = relative.replace("\\", "/").strip("/").casefold()
        if not normalized_relative:
            return False
        parsed_rows.append((kind, normalized_relative))
        if kind == "ROOT":
            package = _normalized_plugin_package(relative)
            if package is None or package in roots:
                return False
            roots.add(package)

    normalized_surfaces: set[str] = set()
    for package in surfaces:
        normalized = _normalized_plugin_package(package)
        if normalized is None or normalized in normalized_surfaces:
            return False
        normalized_surfaces.add(normalized)
    if roots != normalized_surfaces:
        return False

    for kind, relative in parsed_rows:
        owners = {
            package
            for package in roots
            if relative == package or relative.startswith(package + "/")
        }
        if len(owners) != 1:
            return False
        if kind == "ROOT" and relative not in roots:
            return False
    return True


def _valid_receipt(receipt: Any) -> bool:
    if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_SCHEMA:
        return False
    for name in ("config_projection_sha256", "plugin_cache_inventory_sha256"):
        if not isinstance(receipt.get(name), str) or not SHA256_PATTERN.fullmatch(
            receipt[name]
        ):
            return False
    if not _valid_hash_map(receipt.get("config_leaf_hashes")):
        return False
    if not _valid_hash_map(receipt.get("plugin_cache_row_hashes")):
        return False
    if not _valid_plugin_capability_surfaces(
        receipt.get("plugin_capability_surfaces")
    ):
        return False
    if not _valid_plugin_package_manifests(
        receipt.get("plugin_package_manifests")
    ):
        return False
    if not _valid_plugin_package_origins(receipt.get("plugin_package_origins")):
        return False
    surface_packages = set(receipt["plugin_capability_surfaces"])
    if not set(receipt["plugin_package_manifests"]).issubset(surface_packages):
        return False
    if not set(receipt["plugin_package_origins"]).issubset(surface_packages):
        return False
    if not _plugin_cache_rows_match_surfaces(
        receipt["plugin_cache_row_hashes"],
        receipt["plugin_capability_surfaces"],
    ):
        return False
    required_sources = receipt.get("required_source_hashes")
    if (
        not _valid_hash_map(required_sources)
        or set(required_sources) != REQUIRED_SOURCE_HASH_KEYS
    ):
        return False
    identity = receipt.get("app_identity")
    if not isinstance(identity, dict) or identity.get("coherent") is not True:
        return False
    for name in ("app_version", "runtime_id", "cli_id"):
        if not isinstance(identity.get(name), str) or not identity[name]:
            return False
    for name in (
        "trusted_browser_client_sha256",
        "browser_client_sha256",
        "runtime_executable_sha256",
        "cli_executable_sha256",
    ):
        if not isinstance(identity.get(name), str) or not SHA256_PATTERN.fullmatch(
            identity[name]
        ):
            return False
    supplied = receipt.get("snapshot_sha256")
    return (
        isinstance(supplied, str)
        and SHA256_PATTERN.fullmatch(supplied) is not None
        and supplied.upper() == authority_snapshot_digest(receipt)
    )


def _changed_hash_keys(previous: dict[str, str], current: dict[str, str]) -> set[str]:
    return {
        key
        for key in set(previous) | set(current)
        if previous.get(key) != current.get(key)
    }


def _plugin_delta_allowed(key: str, old_version: str, new_version: str) -> bool:
    try:
        kind, relative = key.split("\t", 1)
    except ValueError:
        return False
    if kind not in {"ROOT", "FILE"}:
        return False
    relative = relative.lower()
    allowed_prefixes = {
        f"openai-bundled/{plugin}/{version}".lower()
        for plugin in APP_PLUGIN_NAMES
        for version in (old_version, new_version)
    }
    allowed_prefixes.add("openai-bundled/chrome/latest")
    return any(
        relative == prefix or relative.startswith(prefix + "/")
        for prefix in allowed_prefixes
    )


def _app_package_authority_delta_allowed(
    package: str, old_version: str, new_version: str
) -> bool:
    normalized = _normalized_plugin_package(package)
    if normalized is None:
        return False
    marketplace, plugin, version = normalized.split("/")
    if marketplace != "openai-bundled" or plugin not in APP_PLUGIN_NAMES:
        return False
    return version in {old_version.casefold(), new_version.casefold()} or (
        plugin == "chrome" and version == "latest"
    )


def classify_app_update(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    """Accept only the exact coherent Codex Desktop app-update cohort."""

    if not _valid_receipt(previous):
        return False, "INVALID_BASELINE_AUTHORITY_RECEIPT"
    if not _valid_receipt(current):
        return False, "INVALID_CURRENT_AUTHORITY_RECEIPT"

    previous_sources = previous.get("required_source_hashes")
    current_sources = current.get("required_source_hashes")
    if previous_sources is not None or current_sources is not None:
        if previous_sources != current_sources:
            return False, "UNRECOGNIZED_SOURCE_DELTA"

    previous_identity = previous["app_identity"]
    current_identity = current["app_identity"]
    old_version = previous_identity["app_version"]
    new_version = current_identity["app_version"]
    if (
        old_version == new_version
        or previous_identity["runtime_id"] == current_identity["runtime_id"]
        or previous_identity["cli_id"] == current_identity["cli_id"]
    ):
        return False, "APP_IDENTITY_DID_NOT_ADVANCE"
    if (
        current_identity["trusted_browser_client_sha256"].upper()
        != current_identity["browser_client_sha256"].upper()
    ):
        return False, "APP_IDENTITY_INCOHERENT"

    if previous["plugin_package_origins"] != current["plugin_package_origins"]:
        return False, "UNRELATED_PLUGIN_ORIGIN_DELTA"
    for field, reason in (
        ("plugin_package_manifests", "UNRELATED_PLUGIN_AUTHORITY_DELTA"),
        ("plugin_capability_surfaces", "UNRELATED_PLUGIN_CAPABILITY_DELTA"),
    ):
        previous_values = previous[field]
        current_values = current[field]
        for package in set(previous_values) | set(current_values):
            if _app_package_authority_delta_allowed(
                package, old_version, new_version
            ):
                continue
            if previous_values.get(package) != current_values.get(package):
                return False, reason

    config_delta = _changed_hash_keys(
        previous["config_leaf_hashes"], current["config_leaf_hashes"]
    )
    if not config_delta:
        return False, "APP_CONFIG_COHORT_INCOMPLETE"
    if not config_delta.issubset(ALLOWED_CONFIG_LEAF_PATHS):
        return False, "UNRECOGNIZED_CONFIG_DELTA"
    if not REQUIRED_APP_CONFIG_PATHS.issubset(config_delta):
        return False, "APP_CONFIG_COHORT_INCOMPLETE"

    plugin_delta = _changed_hash_keys(
        previous["plugin_cache_row_hashes"], current["plugin_cache_row_hashes"]
    )
    if not plugin_delta:
        return False, "PLUGIN_COHORT_INCOMPLETE"
    if any(
        not _plugin_delta_allowed(key, old_version, new_version)
        for key in plugin_delta
    ):
        return False, "UNRECOGNIZED_PLUGIN_DELTA"
    for plugin in APP_PLUGIN_NAMES:
        new_root = f"ROOT\topenai-bundled/{plugin}/{new_version}".lower()
        current_keys = {key.lower() for key in current["plugin_cache_row_hashes"]}
        if new_root not in current_keys:
            return False, "PLUGIN_COHORT_INCOMPLETE"
    return True, "RECOGNIZED_CODEX_DESKTOP_UPDATE"


def _semver_key(value: str) -> tuple[int, int, int, tuple[tuple[int, Any], ...]] | None:
    match = SEMVER_PATTERN.fullmatch(value)
    if not match:
        return None
    prerelease = match.group(4)
    if prerelease is None:
        prerelease_key: tuple[tuple[int, Any], ...] = ((2, ""),)
    else:
        parts: list[tuple[int, Any]] = []
        for item in prerelease.split("."):
            if item.isdigit() and len(item) > 1 and item.startswith("0"):
                return None
            parts.append((0, int(item)) if item.isdigit() else (1, item.casefold()))
        prerelease_key = tuple(parts)
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease_key


def _plugin_package_parts(package: str) -> tuple[str, str] | None:
    parts = package.replace("\\", "/").strip("/").split("/")
    if len(parts) != 3 or parts[0].casefold() != CURATED_PLUGIN_CACHE_MARKETPLACE:
        return None
    plugin_name, version = parts[1:]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", plugin_name) or not version:
        return None
    return plugin_name.casefold(), version


def _package_row_suffixes(rows: dict[str, str], package: str) -> set[str]:
    prefix = package.casefold() + "/"
    result: set[str] = set()
    for key in rows:
        try:
            kind, relative = key.split("\t", 1)
        except ValueError:
            continue
        relative_folded = relative.replace("\\", "/").casefold()
        if relative_folded == package.casefold():
            result.add(f"{kind.upper()}\t.")
        elif relative_folded.startswith(prefix):
            result.add(f"{kind.upper()}\t{relative_folded[len(prefix):]}")
    return result


def _plugin_is_enabled(config_leaf_hashes: dict[str, str], plugin_name: str) -> bool:
    """Use the exact remote marketplace control, then one legacy alias."""

    expected = _sha256_text(_canonical_json({"type": "boolean", "value": True}))
    for marketplace in CURATED_PLUGIN_CONFIG_MARKETPLACE_ALIASES:
        pointer = f"/plugins/{plugin_name}@{marketplace}/enabled"
        supplied = config_leaf_hashes.get(pointer)
        if supplied is not None:
            return supplied == expected
    return False


def classify_enabled_plugin_version_replacement(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    """Accept one coherent, surface-preserving enabled curated-plugin upgrade."""

    if not _valid_receipt(previous):
        return False, "INVALID_BASELINE_AUTHORITY_RECEIPT"
    if not _valid_receipt(current):
        return False, "INVALID_CURRENT_AUTHORITY_RECEIPT"
    if previous.get("required_source_hashes") != current.get("required_source_hashes"):
        return False, "UNRECOGNIZED_SOURCE_DELTA"
    if (
        previous.get("config_projection_sha256")
        != current.get("config_projection_sha256")
        or previous.get("config_leaf_hashes") != current.get("config_leaf_hashes")
    ):
        return False, "PLUGIN_CONFIG_CHANGED"
    if previous.get("app_identity") != current.get("app_identity"):
        return False, "PLUGIN_APP_IDENTITY_CHANGED"

    previous_surfaces = previous["plugin_capability_surfaces"]
    current_surfaces = current["plugin_capability_surfaces"]
    removed_packages = sorted(set(previous_surfaces) - set(current_surfaces))
    added_packages = sorted(set(current_surfaces) - set(previous_surfaces))
    if len(removed_packages) != 1 or len(added_packages) != 1:
        return False, "PLUGIN_VERSION_REPLACEMENT_INCOMPLETE"
    old_package, new_package = removed_packages[0], added_packages[0]
    old_parts = _plugin_package_parts(old_package)
    new_parts = _plugin_package_parts(new_package)
    if old_parts is None or new_parts is None or old_parts[0] != new_parts[0]:
        return False, "PLUGIN_IDENTITY_CHANGED"
    plugin_name = old_parts[0]
    old_version = _semver_key(old_parts[1])
    new_version = _semver_key(new_parts[1])
    if old_version is None or new_version is None:
        return False, "PLUGIN_VERSION_UNPARSEABLE"
    if new_version <= old_version:
        return False, "PLUGIN_VERSION_NOT_NEWER"

    if not _plugin_is_enabled(previous["config_leaf_hashes"], plugin_name):
        return False, "PLUGIN_NOT_ENABLED"

    previous_manifests = previous["plugin_package_manifests"]
    current_manifests = current["plugin_package_manifests"]
    old_manifest = previous_manifests.get(old_package)
    new_manifest = current_manifests.get(new_package)
    if old_manifest is None or new_manifest is None:
        return False, "PLUGIN_MANIFEST_IDENTITY_UNPROVEN"
    if (
        old_manifest["name"].casefold() != plugin_name
        or new_manifest["name"].casefold() != plugin_name
        or old_manifest["version"] != old_parts[1]
        or new_manifest["version"] != new_parts[1]
    ):
        return False, "PLUGIN_MANIFEST_IDENTITY_MISMATCH"

    previous_origins = previous["plugin_package_origins"]
    current_origins = current["plugin_package_origins"]
    old_origin = previous_origins.get(old_package)
    new_origin = current_origins.get(new_package)
    if old_origin is None or new_origin is None:
        return False, "PLUGIN_ORIGIN_UNPROVEN"
    if old_origin != new_origin:
        return False, "PLUGIN_ORIGIN_CHANGED"

    old_surface = {
        (item["kind"], item["id"]) for item in previous_surfaces[old_package]
    }
    new_surface = {
        (item["kind"], item["id"]) for item in current_surfaces[new_package]
    }
    if old_surface != new_surface or (
        "plugin", f"plugin:{old_manifest['name']}"
    ) not in old_surface:
        return False, "PLUGIN_CAPABILITY_SURFACE_CHANGED"
    for package in set(previous_surfaces) & set(current_surfaces):
        if previous_surfaces[package] != current_surfaces[package]:
            return False, "UNRELATED_PLUGIN_CAPABILITY_DELTA"
        if previous_manifests.get(package) != current_manifests.get(package):
            return False, "UNRELATED_PLUGIN_AUTHORITY_DELTA"
        if previous_origins.get(package) != current_origins.get(package):
            return False, "UNRELATED_PLUGIN_AUTHORITY_DELTA"

    previous_rows = previous["plugin_cache_row_hashes"]
    current_rows = current["plugin_cache_row_hashes"]
    removed_rows = set(previous_rows) - set(current_rows)
    added_rows = set(current_rows) - set(previous_rows)
    changed_rows = {
        key
        for key in set(previous_rows) & set(current_rows)
        if previous_rows[key] != current_rows[key]
    }
    old_prefix = old_package.casefold()
    new_prefix = new_package.casefold()

    def belongs(key: str, package_prefix: str) -> bool:
        try:
            _, relative = key.split("\t", 1)
        except ValueError:
            return False
        value = relative.replace("\\", "/").casefold()
        return value == package_prefix or value.startswith(package_prefix + "/")

    previous_old_rows = {
        key for key in previous_rows if belongs(key, old_prefix)
    }
    current_new_rows = {
        key for key in current_rows if belongs(key, new_prefix)
    }

    if (
        changed_rows
        or not removed_rows
        or not added_rows
        or any(belongs(key, old_prefix) for key in current_rows)
        or any(belongs(key, new_prefix) for key in previous_rows)
        or removed_rows != previous_old_rows
        or added_rows != current_new_rows
        or any(not belongs(key, old_prefix) for key in removed_rows)
        or any(not belongs(key, new_prefix) for key in added_rows)
    ):
        return False, "UNRELATED_PLUGIN_CACHE_DELTA"
    old_suffixes = _package_row_suffixes(previous_rows, old_package)
    new_suffixes = _package_row_suffixes(current_rows, new_package)
    required_suffixes = {"ROOT\t.", "FILE\t.codex-plugin/plugin.json"}
    if old_suffixes != new_suffixes or not required_suffixes.issubset(old_suffixes):
        return False, "PLUGIN_AUTHORITY_SHAPE_CHANGED"
    return True, "RECOGNIZED_ENABLED_PLUGIN_VERSION_REPLACEMENT"


def classify_authority_update(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    app_allowed, app_reason = classify_app_update(previous, current)
    if app_allowed:
        return True, app_reason
    plugin_allowed, plugin_reason = classify_enabled_plugin_version_replacement(
        previous, current
    )
    if plugin_allowed:
        return True, plugin_reason
    if app_reason == "APP_IDENTITY_DID_NOT_ADVANCE":
        return False, plugin_reason
    return False, app_reason


def _component_after(path_value: str, marker: str) -> str:
    parts = Path(path_value).parts
    for position, part in enumerate(parts[:-1]):
        if part.casefold() == marker.casefold():
            return parts[position + 1]
    return ""


def _nested_string(data: dict[str, Any], *path: str) -> str:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current if isinstance(current, str) else ""


def _read_plugin_version(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    value = data.get("version") if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


def _app_identity(codex_home: Path, config_path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return {"coherent": False}
    command = _nested_string(data, "mcp_servers", "node_repl", "command")
    env = (
        data.get("mcp_servers", {}).get("node_repl", {}).get("env", {})
        if isinstance(data.get("mcp_servers"), dict)
        else {}
    )
    if not isinstance(env, dict):
        return {"coherent": False}
    runtime_id = _component_after(command, "cua_node")
    app_version = env.get("BROWSER_USE_CODEX_APP_VERSION", "")
    cli_path_text = env.get("CODEX_CLI_PATH", "")
    cli_id = _component_after(cli_path_text, "bin")
    trusted_hash = env.get("NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S", "")
    runtime_path = Path(command) if command else Path()
    cli_path = Path(cli_path_text) if cli_path_text else Path()
    browser_root = codex_home / "plugins" / "cache" / "openai-bundled" / "browser"
    browser_client = browser_root / str(app_version) / "scripts" / "browser-client.mjs"
    browser_hash = _sha256_file(browser_client)
    runtime_values = (
        command,
        str(env.get("NODE_REPL_NODE_MODULE_DIRS", "")),
        str(env.get("NODE_REPL_NODE_PATH", "")),
        str(env.get("NODE_REPL_TRUSTED_CODE_PATHS", "")),
    )
    plugin_versions = {
        plugin: _read_plugin_version(
            codex_home
            / "plugins"
            / "cache"
            / "openai-bundled"
            / plugin
            / str(app_version)
            / ".codex-plugin"
            / "plugin.json"
        )
        for plugin in APP_PLUGIN_NAMES
    }
    latest_version = _read_plugin_version(
        codex_home
        / "plugins"
        / "cache"
        / "openai-bundled"
        / "chrome"
        / "latest"
        / ".codex-plugin"
        / "plugin.json"
    )
    coherent = bool(
        app_version
        and runtime_id
        and cli_id
        and all(runtime_id.casefold() in value.casefold() for value in runtime_values)
        and runtime_path.is_file()
        and cli_path.is_file()
        and SHA256_PATTERN.fullmatch(str(trusted_hash))
        and browser_hash
        and browser_hash.upper() == str(trusted_hash).upper()
        and all(version == app_version for version in plugin_versions.values())
        and latest_version == app_version
    )
    return {
        "app_version": str(app_version),
        "runtime_id": runtime_id,
        "cli_id": cli_id,
        "trusted_browser_client_sha256": str(trusted_hash).upper(),
        "browser_client_sha256": browser_hash,
        "runtime_executable_sha256": _sha256_file(runtime_path),
        "cli_executable_sha256": _sha256_file(cli_path),
        "coherent": coherent,
    }


def _required_source_paths(codex_home: Path) -> dict[str, Path]:
    return {
        "hooks.json": codex_home / "hooks.json",
        "AGENTS.md": codex_home / "AGENTS.md",
        "task-routing-gate.md": codex_home / "docs" / "context" / "task-routing-gate.md",
        "catalogue-router.SKILL.md": codex_home
        / "skills"
        / "catalogue-router"
        / "SKILL.md",
        "capability_index.py": codex_home / "hooks" / "capability_index.py",
        "capability_config_fingerprint.py": codex_home
        / "hooks"
        / "capability_config_fingerprint.py",
        "capability_index_cli.py": codex_home / "hooks" / "capability_index_cli.py",
        "user_prompt_skill_router.py": codex_home
        / "hooks"
        / "user_prompt_skill_router.py",
        "capability_index_session_start.py": codex_home
        / "hooks"
        / "capability_index_session_start.py",
        "_common.py": codex_home / "hooks" / "_common.py",
        "_hook_io.py": codex_home / "hooks" / "_hook_io.py",
        "query-catalogue.ps1": codex_home
        / "skills"
        / "catalogue-router"
        / "scripts"
        / "query-catalogue.ps1",
        "routing-policy.yaml": codex_home / "capability-routing" / "routing-policy.yaml",
        "routing-policy.schema.json": codex_home
        / "capability-routing"
        / "routing-policy.schema.json",
        "active-capabilities.schema.json": codex_home
        / "capability-routing"
        / "active-capabilities.schema.json",
        "project-scope-map.json": codex_home
        / "capability-routing"
        / "project-scope-map.json",
        "project-scope-map.schema.json": codex_home
        / "capability-routing"
        / "project-scope-map.schema.json",
        "route-decision.schema.json": codex_home
        / "capability-routing"
        / "route-decision.schema.json",
        "ensure-node-dependencies.ps1": codex_home
        / "tools"
        / "dependency-readiness"
        / "ensure-node-dependencies.ps1",
        "capability_manifest_recovery.py": codex_home
        / "hooks"
        / "capability_manifest_recovery.py",
        "capability-manifest-builder.ps1": codex_home
        / "capability-routing"
        / "builder"
        / "build_canonical_capability_manifest.ps1",
        "authority-receipt.schema.json": codex_home
        / "capability-routing"
        / "authority-receipt.schema.json",
    }


def _required_source_hashes(codex_home: Path) -> dict[str, str]:
    paths = _required_source_paths(codex_home)
    if set(paths) != REQUIRED_SOURCE_HASH_KEYS:
        raise RuntimeError("required recovery source contract is inconsistent")
    hashes: dict[str, str] = {}
    for name, path in paths.items():
        digest = _sha256_file(path)
        if not digest:
            raise RuntimeError(f"required recovery source is unavailable: {name}")
        hashes[name] = digest
    return hashes


def _plugin_row_hashes(rows: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        parts = row.split("\t", 3)
        if len(parts) != 4 or parts[0] not in {"ROOT", "FILE"}:
            raise ValueError("plugin cache authority row is malformed")
        result[f"{parts[0]}\t{parts[1]}"] = _sha256_text(row)
    return dict(sorted(result.items()))


def _capability_safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-.")
    if not normalized:
        raise ValueError("plugin capability name is empty after normalization")
    return normalized


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"plugin authority JSON is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"plugin authority JSON is not an object: {path.name}")
    return value


def _effective_skill_name(skill_file: Path, fallback_name: str) -> str:
    """Match the builder's bounded frontmatter name extraction."""

    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()[:80]
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("plugin skill metadata is unreadable") from exc
    name = fallback_name
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            match = re.fullmatch(r"name:\s*(.+?)\s*", line)
            if match:
                name = match.group(1).strip().strip('"').strip("'")
    if not name:
        raise ValueError("plugin skill name is empty")
    return name


def _filter_plugin_cache_inventory_rows(
    rows: tuple[str, ...], packages: set[str]
) -> tuple[str, ...]:
    """Keep only manifest-backed packages represented by the bounded surface scan."""

    normalized_packages = {
        normalized
        for package in packages
        if (normalized := _normalized_plugin_package(package)) is not None
    }
    filtered: list[str] = []
    for row in rows:
        parts = row.split("\t", 3)
        if len(parts) != 4 or parts[0] not in {"ROOT", "FILE"}:
            raise ValueError("plugin cache authority row is malformed")
        relative_parts = parts[1].replace("\\", "/").strip("/").split("/")
        if len(relative_parts) < 3:
            raise ValueError("plugin cache authority row has no package root")
        package = "/".join(relative_parts[:3]).casefold()
        if package in normalized_packages:
            filtered.append(row)
    return tuple(sorted(filtered))


def _plugin_package_manifests(
    codex_home: Path, packages: set[str]
) -> dict[str, dict[str, str]]:
    cache_root = codex_home / "plugins" / "cache"
    result: dict[str, dict[str, str]] = {}
    for package in sorted(packages):
        manifest_path = cache_root / Path(package) / ".codex-plugin" / "plugin.json"
        if not manifest_path.is_file():
            continue
        manifest = _read_json_object(manifest_path)
        name = manifest.get("name")
        version = manifest.get("version")
        digest = _sha256_file(manifest_path)
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise RuntimeError("plugin manifest identity is incomplete")
        if not digest:
            raise RuntimeError("plugin manifest digest is unavailable")
        remote_parts = _plugin_package_parts(package)
        if remote_parts is not None and (
            name.casefold() != remote_parts[0] or version != remote_parts[1]
        ):
            raise RuntimeError("remote plugin manifest identity does not match its cache directory")
        result[package] = {
            "name": name,
            "version": version,
            "manifest_sha256": digest,
        }
    return result


def _plugin_package_origins(
    codex_home: Path, packages: set[str]
) -> dict[str, dict[str, str]]:
    """Bind remote curated packages to the managed-install receipt when proven."""

    cache_root = codex_home / "plugins" / "cache"
    result: dict[str, dict[str, str]] = {}
    for package in sorted(packages):
        normalized = _normalized_plugin_package(package)
        if normalized is None or not normalized.startswith(
            CURATED_PLUGIN_CACHE_MARKETPLACE + "/"
        ):
            continue
        parts = normalized.split("/")
        receipt_path = cache_root / parts[0] / parts[1] / REMOTE_PLUGIN_INSTALL_RECEIPT
        if not receipt_path.is_file():
            continue
        try:
            receipt = _read_json_object(receipt_path)
        except RuntimeError:
            continue
        remote_plugin_id = receipt.get("remote_plugin_id")
        digest = _sha256_file(receipt_path)
        if (
            receipt.get("schema_version") != 1
            or not isinstance(remote_plugin_id, str)
            or not re.fullmatch(r"[-A-Za-z0-9._:~]{8,256}", remote_plugin_id)
            or not digest
        ):
            continue
        result[package] = {
            "remote_plugin_id": remote_plugin_id,
            "install_receipt_sha256": digest,
        }
    return result


def _plugin_capability_surfaces(codex_home: Path) -> dict[str, list[dict[str, str]]]:
    """Derive the bounded capability ID/kind surface of every cached package."""

    cache_root = codex_home / "plugins" / "cache"
    try:
        resolved_cache_root = cache_root.resolve(strict=True)
        if not resolved_cache_root.is_dir():
            raise RuntimeError("plugin cache root is unavailable")
        result: dict[str, list[dict[str, str]]] = {}
        for marketplace in sorted(cache_root.iterdir(), key=lambda path: path.name.casefold()):
            if not marketplace.is_dir():
                continue
            for plugin in sorted(marketplace.iterdir(), key=lambda path: path.name.casefold()):
                if not plugin.is_dir():
                    continue
                if plugin.name.casefold().startswith(PLUGIN_INSTALL_STAGING_PREFIX):
                    continue
                for version in sorted(plugin.iterdir(), key=lambda path: path.name.casefold()):
                    if not version.is_dir():
                        continue
                    resolved_version = version.resolve(strict=True)
                    if not resolved_version.is_relative_to(resolved_cache_root):
                        raise RuntimeError("plugin package escapes the configured cache root")
                    manifest_path = version / ".codex-plugin" / "plugin.json"
                    if not manifest_path.is_file():
                        continue
                    resolved_manifest = manifest_path.resolve(strict=True)
                    if not resolved_manifest.is_relative_to(resolved_cache_root):
                        raise RuntimeError("plugin manifest escapes the cache root")
                    manifest = _read_json_object(manifest_path)
                    plugin_name = manifest.get("name")
                    if not isinstance(plugin_name, str) or not plugin_name:
                        raise RuntimeError("plugin manifest identity is incomplete")
                    package = version.relative_to(cache_root).as_posix().casefold()
                    surface: set[tuple[str, str]] = {
                        ("plugin", f"plugin:{plugin_name}")
                    }

                    skills_root = version / "skills"
                    if skills_root.is_dir():
                        resolved_skills = skills_root.resolve(strict=True)
                        if not resolved_skills.is_relative_to(resolved_cache_root):
                            raise RuntimeError("plugin skills root escapes the cache root")
                        for skill in sorted(
                            skills_root.iterdir(), key=lambda path: path.name.casefold()
                        ):
                            skill_file = skill / "SKILL.md"
                            if not skill.is_dir() or not skill_file.is_file():
                                continue
                            resolved_skill = skill_file.resolve(strict=True)
                            if not resolved_skill.is_relative_to(resolved_cache_root):
                                raise RuntimeError("plugin skill escapes the cache root")
                            skill_name = _effective_skill_name(skill_file, skill.name)
                            surface.add(
                                ("skill", f"skill:{plugin_name}:{skill_name}")
                            )

                    app_path = version / ".app.json"
                    if app_path.is_file():
                        resolved_app = app_path.resolve(strict=True)
                        if not resolved_app.is_relative_to(resolved_cache_root):
                            raise RuntimeError("plugin app manifest escapes the cache root")
                        apps = _read_json_object(app_path).get("apps")
                        if not isinstance(apps, dict):
                            raise RuntimeError("plugin app manifest has no apps object")
                        for app_name in apps:
                            if not isinstance(app_name, str):
                                raise RuntimeError("plugin app name is not a string")
                            surface.add(
                                (
                                    "tool-family",
                                    f"tool-family:app:{_capability_safe_name(app_name)}",
                                )
                            )

                    mcp_path = version / ".mcp.json"
                    if mcp_path.is_file():
                        resolved_mcp = mcp_path.resolve(strict=True)
                        if not resolved_mcp.is_relative_to(resolved_cache_root):
                            raise RuntimeError("plugin MCP manifest escapes the cache root")
                        servers = _read_json_object(mcp_path).get("mcpServers")
                        if not isinstance(servers, dict):
                            raise RuntimeError("plugin MCP manifest has no mcpServers object")
                        for server_name in servers:
                            if not isinstance(server_name, str):
                                raise RuntimeError("plugin MCP name is not a string")
                            surface.add(
                                ("mcp", f"mcp:{_capability_safe_name(server_name)}")
                            )

                    result[package] = [
                        {"id": identifier, "kind": kind}
                        for kind, identifier in sorted(surface)
                    ]
        return dict(sorted(result.items()))
    except (OSError, RuntimeError, ValueError):
        raise


def capture_authority_receipt(codex_home: Path | None = None) -> dict[str, Any]:
    home = (codex_home or index.CODEX_HOME).resolve(strict=True)
    config_path = home / "config.toml"
    authority = config_fingerprint.capability_config_authority(config_path)
    rows = index._plugin_cache_inventory_rows(home)
    if rows is None:
        raise RuntimeError("plugin cache authority inventory is unavailable")
    surfaces = _plugin_capability_surfaces(home)
    packages = set(surfaces)
    rows = _filter_plugin_cache_inventory_rows(rows, packages)
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "config_projection_sha256": authority["sha256"],
        "config_leaf_hashes": authority["projection_leaf_hashes"],
        "plugin_cache_inventory_sha256": _sha256_text("\n".join(rows)),
        "plugin_cache_row_hashes": _plugin_row_hashes(rows),
        "plugin_capability_surfaces": surfaces,
        "plugin_package_manifests": _plugin_package_manifests(home, packages),
        "plugin_package_origins": _plugin_package_origins(home, packages),
        "required_source_hashes": _required_source_hashes(home),
        "app_identity": _app_identity(home, config_path),
    }
    receipt["snapshot_sha256"] = authority_snapshot_digest(receipt)
    if not _valid_receipt(receipt):
        raise RuntimeError("captured authority receipt is invalid")
    return receipt


def _bounded_recovery_state(state: dict[str, Any] | None) -> dict[str, Any]:
    value = state if isinstance(state, dict) else {}
    mismatches = value.get("source_hash_mismatches")
    mismatch_values = mismatches if isinstance(mismatches, list) else []
    return {
        "freshness_status": str(value.get("freshness_status") or "unknown")[:64],
        "source_hashes_verified": value.get("source_hashes_verified") is True,
        "source_hash_mismatches": [str(item)[:160] for item in mismatch_values[:32]],
        "entry_count": len(value.get("entries", []))
        if isinstance(value.get("entries"), list)
        else 0,
    }


def _bounded_hash(value: str) -> str:
    return value.upper() if SHA256_PATTERN.fullmatch(str(value or "")) else ""


def write_session_start_recovery_receipt(
    result: dict[str, Any],
    *,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
    before_manifest_sha256: str,
    after_manifest_sha256: str,
    receipt_dir: Path | None = None,
    max_receipts: int = SESSION_START_RECEIPT_LIMIT,
    event_id: str | None = None,
    recorded_at: dt.datetime | None = None,
) -> Path:
    """Atomically persist one bounded SessionStart recovery outcome."""

    directory = receipt_dir or (
        index.CODEX_HOME / "capability-routing" / "recovery-receipts"
    )
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = recorded_at or dt.datetime.now(dt.timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    timestamp = timestamp.astimezone(dt.timezone.utc)
    identifier = re.sub(r"[^A-Za-z0-9._-]+", "-", event_id or uuid.uuid4().hex)
    identifier = identifier.strip("-.")[:96] or uuid.uuid4().hex
    stamp = timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
    destination = directory / f"session-start-recovery-{stamp}-{identifier}.json"
    bounded_result = result if isinstance(result, dict) else {}
    payload: dict[str, Any] = {
        "schema_version": SESSION_START_RECEIPT_SCHEMA,
        "recorded_at_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "event_id": identifier,
        "status": str(bounded_result.get("status") or "error")[:32],
        "reason_code": str(
            bounded_result.get("reason_code") or "RECOVERY_RESULT_INVALID"
        )[:128],
        "before_manifest_sha256": _bounded_hash(before_manifest_sha256),
        "after_manifest_sha256": _bounded_hash(after_manifest_sha256),
        "before": _bounded_recovery_state(before_state),
        "after": _bounded_recovery_state(after_state),
    }
    for key, limit in (("snapshot_sha256", 64), ("error_type", 128)):
        value = bounded_result.get(key)
        if isinstance(value, str) and value:
            payload[key] = value[:limit]

    temporary = directory / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    retained = max(1, min(int(max_receipts), 256))
    receipts = sorted(directory.glob("session-start-recovery-*.json"))
    for expired in receipts[:-retained]:
        if expired != destination:
            expired.unlink(missing_ok=True)
    return destination


def stable_two_read(
    reader: Callable[[], dict[str, Any]],
    *,
    settle_seconds: float = 2.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any] | None, str]:
    first = reader()
    sleeper(settle_seconds)
    second = reader()
    if _canonical_json(first) != _canonical_json(second):
        return None, "AUTHORITY_SNAPSHOT_UNSTABLE"
    if not _valid_receipt(second):
        return None, "INVALID_CURRENT_AUTHORITY_RECEIPT"
    return second, "AUTHORITY_SNAPSHOT_STABLE"


def authority_is_quiet(codex_home: Path, quiet_seconds: float = 15.0) -> bool:
    paths = [codex_home / "config.toml"]
    cache_root = codex_home / "plugins" / "cache"
    if cache_root.is_dir():
        paths.extend(cache_root.rglob("*"))
    newest = 0
    try:
        for path in paths:
            newest = max(newest, path.stat().st_mtime_ns)
    except OSError:
        return False
    return time.time_ns() - newest >= int(quiet_seconds * 1_000_000_000)


def wait_for_quiet(
    checker: Callable[[], bool],
    *,
    timeout_seconds: float = 45.0,
    poll_seconds: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = clock() + max(0.0, timeout_seconds)
    while True:
        if checker():
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleeper(min(max(0.01, poll_seconds), remaining))


class StaticMutex:
    def __init__(self, *, acquired: bool, abandoned: bool = False) -> None:
        self.acquired = acquired
        self.abandoned = abandoned

    def __enter__(self) -> "StaticMutex":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None


def null_mutex() -> StaticMutex:
    return StaticMutex(acquired=True)


class WindowsNamedMutex:
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080
    WAIT_TIMEOUT = 0x00000102

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self.handle: int | None = None
        self.acquired = False
        self.abandoned = False
        self._owned = False

    def __enter__(self) -> "WindowsNamedMutex":
        if os.name != "nt":
            return self
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return self
        self.handle = int(handle)
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == self.WAIT_OBJECT_0:
            self.acquired = True
            self._owned = True
        elif result == self.WAIT_ABANDONED:
            self.abandoned = True
            self._owned = True
        elif result != self.WAIT_TIMEOUT:
            self.abandoned = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None or os.name != "nt":
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            if self._owned:
                kernel32.ReleaseMutex(ctypes.c_void_p(self.handle))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None
        return None


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def resolve_receipt_codex_exe(
    receipt: dict[str, Any], *, local_app_data: Path | None = None
) -> Path:
    if not _valid_receipt(receipt):
        raise RuntimeError("authority receipt is invalid")
    base = local_app_data or Path(os.environ.get("LOCALAPPDATA", ""))
    if not str(base):
        raise RuntimeError("LOCALAPPDATA is unavailable")
    cli_id = receipt["app_identity"]["cli_id"]
    bin_root = (base / "OpenAI" / "Codex" / "bin").resolve(strict=True)
    candidate = (bin_root / cli_id / "codex.exe").resolve(strict=True)
    if not candidate.is_relative_to(bin_root) or not candidate.is_file():
        raise RuntimeError("receipt-bound Codex CLI path is invalid")
    expected = receipt["app_identity"]["cli_executable_sha256"].upper()
    if _sha256_file(candidate) != expected:
        raise RuntimeError("receipt-bound Codex CLI hash does not match")
    return candidate


def _run_json_command(command: list[str], timeout_seconds: float) -> str:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        raise TimeoutError("live capability inventory timed out") from exc
    if process.returncode != 0:
        raise RuntimeError(
            f"live capability inventory failed ({process.returncode}): {stderr.strip()}"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("live capability inventory returned invalid JSON") from exc
    if not isinstance(payload, (list, dict)):
        raise RuntimeError("live capability inventory returned an invalid JSON root")
    return stdout


def prepare_live_inventories(
    receipt: dict[str, Any],
    *,
    local_app_data: Path | None = None,
    timeout_seconds: float = 20.0,
    command_runner: Callable[[list[str], float], str] = _run_json_command,
) -> None:
    codex_exe = resolve_receipt_codex_exe(
        receipt, local_app_data=local_app_data
    )
    for arguments in (("mcp", "list", "--json"), ("plugin", "list", "--json")):
        raw = command_runner([str(codex_exe), *arguments], timeout_seconds)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("live capability inventory returned invalid JSON") from exc
        if not isinstance(payload, (list, dict)):
            raise RuntimeError("live capability inventory returned an invalid JSON root")


def run_builder_candidate(
    expected_snapshot_sha256: str,
    manifest_path: Path,
    *,
    authority_receipt: dict[str, Any],
    codex_home: Path | None = None,
    timeout_seconds: float = 100.0,
) -> Path:
    home = (codex_home or index.CODEX_HOME).resolve(strict=True)
    builder = (
        home
        / "capability-routing"
        / "builder"
        / "build_canonical_capability_manifest.ps1"
    )
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell or not builder.is_file():
        raise RuntimeError("stable capability manifest builder is unavailable")
    codex_exe = resolve_receipt_codex_exe(authority_receipt)
    candidate = manifest_path.with_name(
        f".{manifest_path.name}.recovery-{os.getpid()}-{uuid.uuid4().hex}.json"
    )
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(builder),
        "-CodexHome",
        str(home),
        "-CodexExe",
        str(codex_exe),
        "-ManifestPath",
        str(candidate),
        "-ExpectedAuthoritySnapshotSha256",
        expected_snapshot_sha256,
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        candidate.unlink(missing_ok=True)
        raise TimeoutError("capability manifest builder timed out") from exc
    if process.returncode != 0:
        candidate.unlink(missing_ok=True)
        raise RuntimeError(
            f"capability manifest builder failed ({process.returncode}): {stderr.strip()}"
        )
    if not candidate.is_file() or not stdout.strip():
        candidate.unlink(missing_ok=True)
        raise RuntimeError("capability manifest builder returned no candidate receipt")
    return candidate


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def attempt_recovery(
    *,
    manifest_path: Path | None = None,
    codex_home: Path | None = None,
    current_state: dict[str, Any] | None = None,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
    builder_runner: Callable[[str, Path], Path] | None = None,
    inventory_preparer: Callable[[dict[str, Any]], None] | None = None,
    mutex_factory: Callable[[], Any] | None = None,
    quiet_checker: Callable[[], bool] | None = None,
    quiet_timeout_seconds: float = 45.0,
    quiet_poll_seconds: float = 2.0,
    prebuild_timeout_seconds: float = 60.0,
    settle_seconds: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    home = (codex_home or index.CODEX_HOME).resolve(strict=False)
    target = manifest_path or (home / "capability-routing" / "active-capabilities.json")
    factory = mutex_factory or WindowsNamedMutex
    with factory() as mutex:
        if getattr(mutex, "abandoned", False):
            return {"status": "denied", "reason_code": "RECOVERY_MUTEX_ABANDONED"}
        if not getattr(mutex, "acquired", False):
            return {"status": "noop", "reason_code": "RECOVERY_MUTEX_BUSY"}
        state = current_state or index.load_active_capabilities(target)
        if state.get("freshness_status") == "fresh" and state.get(
            "source_hashes_verified"
        ) is True:
            return {"status": "noop", "reason_code": "MANIFEST_ALREADY_FRESH"}
        mismatches = {str(item) for item in state.get("source_hash_mismatches", [])}
        if not mismatches or not mismatches.issubset(RECOVERABLE_MISMATCHES):
            return {"status": "denied", "reason_code": "UNRECOVERABLE_SOURCE_DRIFT"}
        manifest = _read_manifest(target)
        baseline = manifest.get("authority_receipt")
        if not isinstance(baseline, dict):
            return {"status": "denied", "reason_code": "AUTHORITY_BASELINE_MISSING"}
        if not _valid_receipt(baseline):
            return {"status": "denied", "reason_code": "AUTHORITY_BASELINE_INVALID"}
        deadline = clock() + max(0.0, prebuild_timeout_seconds)

        def remaining(limit: float) -> float:
            return max(0.0, min(limit, deadline - clock()))

        quiet = quiet_checker or (lambda: authority_is_quiet(home))
        if not wait_for_quiet(
            quiet,
            timeout_seconds=remaining(quiet_timeout_seconds),
            poll_seconds=quiet_poll_seconds,
            clock=clock,
            sleeper=sleeper,
        ):
            return {"status": "denied", "reason_code": "AUTHORITY_NOT_QUIET"}
        reader = snapshot_reader or (lambda: capture_authority_receipt(home))
        preliminary, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if preliminary is None:
            return {"status": "denied", "reason_code": stable_reason}
        recognized, reason = classify_authority_update(baseline, preliminary)
        if not recognized:
            return {"status": "denied", "reason_code": reason}
        preparer = inventory_preparer or (
            lambda receipt: prepare_live_inventories(
                receipt, timeout_seconds=max(1.0, remaining(20.0))
            )
        )
        try:
            preparer(preliminary)
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
            return {
                "status": "error",
                "reason_code": "RECOVERY_INVENTORY_PREFLIGHT_FAILED",
                "error_type": type(exc).__name__,
            }
        if not wait_for_quiet(
            quiet,
            timeout_seconds=remaining(quiet_timeout_seconds),
            poll_seconds=quiet_poll_seconds,
            clock=clock,
            sleeper=sleeper,
        ):
            return {"status": "denied", "reason_code": "AUTHORITY_NOT_QUIET"}
        current, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if current is None:
            return {"status": "denied", "reason_code": stable_reason}
        recognized, reason = classify_authority_update(baseline, current)
        if not recognized:
            return {"status": "denied", "reason_code": reason}
        runner = builder_runner or (
            lambda expected, path: run_builder_candidate(
                expected,
                path,
                authority_receipt=current,
                codex_home=home,
            )
        )
        candidate: Path | None = None
        try:
            candidate = runner(current["snapshot_sha256"], target)
            final_snapshot, final_reason = stable_two_read(
                reader, settle_seconds=settle_seconds, sleeper=sleeper
            )
            if final_snapshot is None:
                return {"status": "denied", "reason_code": final_reason}
            if (
                final_snapshot["snapshot_sha256"] != current["snapshot_sha256"]
            ):
                return {
                    "status": "denied",
                    "reason_code": "AUTHORITY_CHANGED_AFTER_BUILD",
                }
            candidate_manifest = _read_manifest(candidate)
            if candidate_manifest.get("authority_receipt") != current:
                return {
                    "status": "denied",
                    "reason_code": "BUILDER_RECEIPT_MISMATCH",
                }
            candidate_state = index.load_active_capabilities(candidate)
            if (
                candidate_state.get("freshness_status") != "fresh"
                or candidate_state.get("source_hashes_verified") is not True
                or not candidate_state.get("entries")
            ):
                return {
                    "status": "denied",
                    "reason_code": "BUILDER_CANDIDATE_INVALID",
                }
            os.replace(candidate, target)
            candidate = None
            installed_state = index.load_active_capabilities(target)
            if (
                installed_state.get("freshness_status") != "fresh"
                or installed_state.get("source_hashes_verified") is not True
            ):
                return {
                    "status": "error",
                    "reason_code": "INSTALLED_MANIFEST_VALIDATION_FAILED",
                }
            return {
                "status": "rebuilt",
                "reason_code": reason,
                "snapshot_sha256": current["snapshot_sha256"],
            }
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
            return {
                "status": "error",
                "reason_code": "RECOVERY_BUILDER_FAILED",
                "error_type": type(exc).__name__,
            }
        finally:
            if candidate is not None:
                candidate.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--codex-home", type=Path, default=index.CODEX_HOME)
    args = parser.parse_args()
    if not args.snapshot:
        parser.error("--snapshot is required")
    receipt = capture_authority_receipt(args.codex_home)
    print(_canonical_json(receipt))
    return 0 if _valid_receipt(receipt) else 2


if __name__ == "__main__":
    raise SystemExit(main())
