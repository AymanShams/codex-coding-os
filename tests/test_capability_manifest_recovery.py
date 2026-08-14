#!/usr/bin/env python3
"""Fail-closed tests for deterministic catalogue manifest recovery."""

from __future__ import annotations

import copy
import contextlib
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "capability-routing" / "reference-runtime"
sys.path.insert(0, str(RUNTIME_ROOT))
import capability_manifest_recovery as recovery
import capability_index_session_start as session_start
sys.path.pop(0)


ALLOWED_CONFIG_PATHS = (
    "/mcp_servers/node_repl/runtime/command",
    "/mcp_servers/node_repl/runtime/env/NODE_REPL_NODE_MODULE_DIRS",
    "/mcp_servers/node_repl/runtime/env/NODE_REPL_NODE_PATH",
    "/mcp_servers/node_repl/runtime/env/NODE_REPL_TRUSTED_CODE_PATHS",
    "/mcp_servers/node_repl/runtime/env/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
    "/mcp_servers/node_repl/runtime/env/BROWSER_USE_CODEX_APP_VERSION",
    "/mcp_servers/node_repl/runtime/env/CODEX_CLI_PATH",
    "/shell_environment_policy/set/NODE_REPL_TRUSTED_CODE_PATHS",
    "/shell_environment_policy/set/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
)


def _config_surface_for_pointer(pointer: str) -> dict[str, object]:
    if pointer.startswith("/plugins/") and pointer.endswith("/enabled"):
        plugin_key = pointer.split("/", 3)[2]
        plugin_name = plugin_key.split("@", 1)[0]
        return {
            "change_class": "availability_toggle",
            "control_kind": "plugin",
            "control_key": plugin_key,
            "enabled": True,
            "capability_ids": [f"plugin:{plugin_name}"],
            "required_capability_ids": [f"plugin:{plugin_name}"],
        }
    return {
        "change_class": "runtime_identity",
        "control_kind": "app_runtime",
        "control_key": "node_repl",
        "capability_ids": [
            "mcp:node_repl",
            "plugin:browser",
            "plugin:chrome",
            "plugin:computer-use",
        ],
        "required_capability_ids": ["mcp:node_repl"],
    }


def _write_required_sources(codex_home: Path) -> None:
    for path in recovery._required_source_paths(codex_home).values():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("{}\n", encoding="utf-8")


def _row_hashes(
    version: str,
    marker: str,
    *,
    sites_version: str = "0.1.34",
    visualize_version: str = "1.0.20",
) -> dict[str, str]:
    rows: dict[str, str] = {}
    for plugin in ("browser", "chrome", "computer-use"):
        prefix = f"openai-bundled/{plugin}/{version}"
        rows[f"ROOT\t{prefix}"] = marker * 64
        rows[f"FILE\t{prefix}/.codex-plugin/plugin.json"] = marker * 64
    rows["ROOT\topenai-bundled/chrome/latest"] = marker * 64
    rows[
        "FILE\topenai-bundled/chrome/latest/.codex-plugin/plugin.json"
    ] = marker * 64
    for plugin, plugin_version in (
        ("sites", sites_version),
        ("visualize", visualize_version),
    ):
        prefix = f"openai-bundled/{plugin}/{plugin_version}"
        package_marker = hashlib.sha256(
            f"{plugin}:{plugin_version}".encode("utf-8")
        ).hexdigest().upper()
        rows[f"ROOT\t{prefix}"] = package_marker
        rows[f"FILE\t{prefix}/.codex-plugin/plugin.json"] = package_marker
    rows["ROOT\topenai-curated-remote/github/1.0.0"] = "9" * 64
    return rows


def _surfaces_for_rows(rows: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    surfaces: dict[str, list[dict[str, str]]] = {}
    for key in rows:
        if not key.startswith("ROOT\t"):
            continue
        package = key.split("\t", 1)[1]
        parts = package.replace("\\", "/").strip("/").split("/")
        plugin_name = parts[1]
        surfaces[package] = [
            {"id": f"plugin:{plugin_name}", "kind": "plugin"}
        ]
    return surfaces


def _synthetic_primary_runtime_origin(
    version: str = "26.800.1", marker: str = "6"
) -> dict[str, object]:
    packages: dict[str, object] = {}
    for plugin_name in sorted(recovery.PRIMARY_RUNTIME_REQUIRED_PACKAGES):
        authority_files = {".codex-plugin/plugin.json": marker * 64}
        authority_sha256 = recovery._sha256_text(
            recovery._canonical_json(authority_files)
        )
        packages[plugin_name] = {
            "plugin_name": plugin_name,
            "plugin_version": version,
            "bundle_package": (
                "plugins/openai-primary-runtime/plugins/" + plugin_name
            ),
            "cache_package": (
                f"openai-primary-runtime/{plugin_name}/{version}"
            ),
            "authority_file_hashes": authority_files,
            "bundle_authority_sha256": authority_sha256,
            "cache_authority_sha256": authority_sha256,
        }
    return {
        "schema_version": recovery.PRIMARY_RUNTIME_BUNDLE_SCHEMA,
        "bundle_format_version": 2,
        "bundle_version": version,
        "runtime_json_sha256": marker * 64,
        "authority_projection_sha256": recovery._sha256_text(
            recovery._canonical_json(packages)
        ),
        "packages": packages,
    }


def _synthetic_bundled_marketplace_origin(
    app_version: str,
    marker: str,
    *,
    sites_version: str = "0.1.34",
    visualize_version: str = "1.0.20",
    bundle_id: str | None = None,
) -> dict[str, object]:
    versions = {
        "browser": app_version,
        "chrome": app_version,
        "computer-use": app_version,
        "latex": "0.2.5",
        "sites": sites_version,
        "visualize": visualize_version,
    }
    packages: dict[str, object] = {}
    for plugin_name, plugin_version in sorted(versions.items()):
        authority_marker = (
            marker * 64
            if plugin_name in recovery.BUNDLED_MARKETPLACE_REQUIRED_ACTIVE_PACKAGES
            else hashlib.sha256(
                f"{plugin_name}:{plugin_version}".encode("utf-8")
            ).hexdigest().upper()
        )
        authority_files = {".codex-plugin/plugin.json": authority_marker}
        authority_sha256 = recovery._sha256_text(
            recovery._canonical_json(authority_files)
        )
        if plugin_name == "latex":
            cache_packages: list[str] = []
        else:
            cache_packages = [
                f"openai-bundled/{plugin_name}/{plugin_version}"
            ]
            if plugin_name == "chrome":
                cache_packages.append("openai-bundled/chrome/latest")
        cache_packages.sort()
        packages[plugin_name] = {
            "plugin_name": plugin_name,
            "plugin_version": plugin_version,
            "bundle_package": f"plugins/{plugin_name}",
            "cache_packages": cache_packages,
            "authority_file_hashes": authority_files,
            "bundle_authority_sha256": authority_sha256,
            "cache_authority_sha256s": {
                package: authority_sha256 for package in cache_packages
            },
        }
    result = {
        "schema_version": recovery.BUNDLED_MARKETPLACE_SCHEMA,
        "materialization_version": 1,
        "app_version": app_version,
        "bundle_id": bundle_id
        or str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{app_version}:{marker}")),
        "materialization_key_sha256": marker * 64,
        "marketplace_projection_sha256": "5" * 64,
        "packages": packages,
    }
    result["authority_projection_sha256"] = recovery._sha256_text(
        recovery._canonical_json(recovery._bundled_marketplace_projection(result))
    )
    return result


def _receipt(
    version: str,
    runtime_id: str,
    cli_id: str,
    marker: str,
    *,
    config_overrides: dict[str, str] | None = None,
    row_overrides: dict[str, str] | None = None,
    sites_version: str = "0.1.34",
    visualize_version: str = "1.0.20",
    bundle_id: str | None = None,
) -> dict:
    leaves = {path: marker * 64 for path in ALLOWED_CONFIG_PATHS}
    leaves["/features/js_repl"] = "8" * 64
    if config_overrides:
        leaves.update(config_overrides)
    rows = _row_hashes(
        version,
        marker,
        sites_version=sites_version,
        visualize_version=visualize_version,
    )
    primary_version = "26.800.1"
    primary_packages = {
        plugin_name: f"openai-primary-runtime/{plugin_name}/{primary_version}"
        for plugin_name in sorted(recovery.PRIMARY_RUNTIME_REQUIRED_PACKAGES)
    }
    for primary_package in primary_packages.values():
        rows[f"ROOT\t{primary_package}"] = "6" * 64
        rows[f"FILE\t{primary_package}/.codex-plugin/plugin.json"] = "6" * 64
    if row_overrides:
        rows.update(row_overrides)
    bundled_origin = _synthetic_bundled_marketplace_origin(
        version,
        marker,
        sites_version=sites_version,
        visualize_version=visualize_version,
        bundle_id=bundle_id,
    )
    bundled_manifests = {
        cache_package: {
            "name": package["plugin_name"],
            "version": package["plugin_version"],
            "manifest_sha256": package["authority_file_hashes"][
                ".codex-plugin/plugin.json"
            ],
        }
        for package in bundled_origin["packages"].values()
        for cache_package in package["cache_packages"]
    }
    receipt = {
        "schema_version": recovery.RECEIPT_SCHEMA,
        "config_projection_sha256": marker * 64,
        "config_leaf_hashes": leaves,
        "config_capability_surfaces": {
            pointer: _config_surface_for_pointer(pointer)
            for pointer in sorted(leaves)
        },
        "plugin_cache_inventory_sha256": marker * 64,
        "plugin_cache_row_hashes": rows,
        "plugin_capability_surfaces": _surfaces_for_rows(rows),
        "plugin_package_manifests": {
            package: {
                "name": plugin_name,
                "version": primary_version,
                "manifest_sha256": "6" * 64,
            }
            for plugin_name, package in primary_packages.items()
        }
        | bundled_manifests,
        "plugin_package_origins": {},
        "remote_plugin_catalog_entries": {},
        "bundled_marketplace_origin": bundled_origin,
        "primary_runtime_bundle_origin": _synthetic_primary_runtime_origin(),
        "required_source_hashes": {
            key: "7" * 64 for key in sorted(recovery.REQUIRED_SOURCE_HASH_KEYS)
        },
        "app_identity": {
            "app_version": version,
            "runtime_id": runtime_id,
            "cli_id": cli_id,
            "trusted_browser_client_sha256": marker * 64,
            "browser_client_sha256": marker * 64,
            "runtime_executable_sha256": marker * 64,
            "cli_executable_sha256": marker * 64,
            "coherent": True,
        },
    }
    receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)
    return receipt


def _boolean_leaf_hash(value: bool) -> str:
    return recovery._sha256_text(
        recovery._canonical_json({"type": "boolean", "value": value})
    )


def _worker_bom_receipts() -> tuple[dict, dict]:
    previous = _receipt("26.810.41047", "runtime-current", "cli-current", "B")
    current = copy.deepcopy(previous)
    current["required_source_hashes"][
        recovery.index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
    ] = "8" * 64
    current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)
    return previous, current


def _set_toggle_control(
    receipt: dict,
    *,
    pointer: str,
    control_kind: str,
    control_key: str,
    enabled: bool,
    capability_ids: list[str],
    required_ids: list[str] | None = None,
) -> None:
    receipt["config_leaf_hashes"][pointer] = _boolean_leaf_hash(enabled)
    receipt["config_capability_surfaces"][pointer] = {
        "change_class": "availability_toggle",
        "control_kind": control_kind,
        "control_key": control_key,
        "enabled": enabled,
        "capability_ids": sorted(capability_ids),
        "required_capability_ids": sorted(required_ids or capability_ids),
    }
    receipt["config_projection_sha256"] = "C" * 64 if enabled else "D" * 64
    receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)


def _managed_origin(
    package: str,
    *,
    manifest_sha256: str,
    package_sha256: str,
    install_receipt_sha256: str,
    remote_plugin_id: str = "plugin_asdk_example_12345678",
    receipt_schema_version: int = 2,
    attestation_mode: str = "version-package-v2",
) -> dict[str, object]:
    marketplace, plugin_name, plugin_version = package.split("/")
    return {
        "attestation_mode": attestation_mode,
        "receipt_schema_version": receipt_schema_version,
        "remote_plugin_id": remote_plugin_id,
        "marketplace": marketplace,
        "plugin_name": plugin_name,
        "plugin_version": plugin_version,
        "plugin_manifest_sha256": manifest_sha256,
        "package_sha256": package_sha256,
        "install_receipt_sha256": install_receipt_sha256,
    }


def _synthetic_catalog_binding(
    package: str,
    *,
    remote_plugin_id: str,
    apps: dict[str, str] | None = None,
    local_apps: dict[str, str] | None = None,
    skill_names: list[str] | None = None,
    fetched_at: str = "2026-08-14T12:00:00Z",
    installation_policy: str = "AVAILABLE",
) -> dict[str, object]:
    _, plugin_name, plugin_version = package.split("/")
    app_map = dict(sorted((apps or {}).items()))
    local_app_map = dict(sorted((local_apps or app_map).items()))
    binding: dict[str, object] = {
        "schema_version": recovery.REMOTE_PLUGIN_CATALOG_ENTRY_SCHEMA,
        "remote_plugin_id": remote_plugin_id,
        "plugin_name": plugin_name,
        "plugin_version": plugin_version,
        "status": "AVAILABLE",
        "installation_policy": installation_policy,
        "apps": app_map,
        "local_apps": local_app_map,
        "skill_names": sorted(skill_names or []),
        "fetched_at": fetched_at,
    }
    binding["entry_projection_sha256"] = recovery._sha256_text(
        recovery._canonical_json(recovery._catalog_entry_projection(binding))
    )
    return binding


def _write_version_specific_install_receipt(
    package: Path,
    *,
    overrides: dict[str, object] | None = None,
) -> Path:
    cache_root = package.parents[2]
    marketplace, plugin_name, plugin_version = package.relative_to(cache_root).parts
    manifest_path = package / ".codex-plugin" / "plugin.json"
    payload: dict[str, object] = {
        "schema_version": 2,
        "remote_plugin_id": "plugins~Plugin_neon_12345678",
        "marketplace": marketplace,
        "plugin_name": plugin_name,
        "plugin_version": plugin_version,
        "plugin_manifest_sha256": recovery._sha256_file(manifest_path),
        "package_sha256": recovery._plugin_package_sha256(package, cache_root),
    }
    if overrides:
        payload.update(overrides)
    receipt_path = package / recovery.REMOTE_PLUGIN_INSTALL_RECEIPT
    receipt_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return receipt_path


def _curated_plugin_receipt(
    plugin_version: str,
    marker: str,
    *,
    plugin_name: str = "gmail",
    enabled: bool = True,
    extra_skill: str | None = None,
    config_marketplace: str = "openai-curated",
    remote_plugin_id: str = "plugin_asdk_example_12345678",
    origin_digest: str | None = None,
    receipt_schema_version: int = 2,
) -> dict:
    receipt = _receipt("26.803.81509", "runtime-stable", "cli-stable", "A")
    config_key = f"/plugins/{plugin_name}@{config_marketplace}/enabled"
    receipt["config_leaf_hashes"][config_key] = _boolean_leaf_hash(enabled)
    package = f"openai-curated-remote/{plugin_name}/{plugin_version}"
    rows = {
        f"ROOT\t{package}": marker * 64,
        f"FILE\t{package}/.codex-plugin/plugin.json": marker * 64,
        f"FILE\t{package}/.app.json": marker * 64,
        f"FILE\t{package}/skills/{plugin_name}/skill.md": marker * 64,
    }
    surfaces = [
        {"id": f"plugin:{plugin_name}", "kind": "plugin"},
        {"id": f"skill:{plugin_name}:{plugin_name}", "kind": "skill"},
        {"id": f"tool-family:app:{plugin_name}", "kind": "tool-family"},
    ]
    if extra_skill:
        rows[f"FILE\t{package}/skills/{extra_skill}/skill.md"] = marker * 64
        surfaces.append(
            {"id": f"skill:{plugin_name}:{extra_skill}", "kind": "skill"}
        )
    receipt["plugin_cache_row_hashes"].update(rows)
    receipt["plugin_cache_inventory_sha256"] = marker * 64
    receipt["plugin_capability_surfaces"][package] = sorted(
        surfaces, key=lambda item: (item["kind"], item["id"])
    )
    receipt["config_capability_surfaces"][config_key] = {
        "change_class": "availability_toggle",
        "control_kind": "plugin",
        "control_key": f"{plugin_name}@{config_marketplace}",
        "enabled": enabled,
        "capability_ids": sorted(item["id"] for item in surfaces),
        "required_capability_ids": [f"plugin:{plugin_name}"],
    }
    receipt["plugin_package_manifests"][package] = {
        "name": plugin_name,
        "version": plugin_version,
        "manifest_sha256": marker * 64,
    }
    receipt["plugin_package_origins"][package] = _managed_origin(
        package,
        manifest_sha256=marker * 64,
        package_sha256=marker * 64,
        install_receipt_sha256=origin_digest or marker * 64,
        remote_plugin_id=remote_plugin_id,
        receipt_schema_version=receipt_schema_version,
        attestation_mode=(
            "plugin-root-v1"
            if receipt_schema_version == 1
            else "version-package-v2"
        ),
    )
    if receipt_schema_version == 1:
        app_id = f"asdk_app_{'1' * 32}"
        receipt["remote_plugin_catalog_entries"][package] = (
            _synthetic_catalog_binding(
                package,
                remote_plugin_id=remote_plugin_id,
                apps={plugin_name: app_id},
                skill_names=[plugin_name],
            )
        )
    receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)
    return receipt


def _primary_runtime_receipt(
    bundle_version: str,
    marker: str,
    *,
    extra_skill: str | None = None,
) -> dict:
    receipt = _receipt("26.803.81509", "runtime-stable", "cli-stable", "A")
    old_packages = {
        package
        for package in receipt["plugin_capability_surfaces"]
        if package.startswith("openai-primary-runtime/")
    }
    receipt["plugin_cache_row_hashes"] = {
        key: value
        for key, value in receipt["plugin_cache_row_hashes"].items()
        if not any(
            key.split("\t", 1)[-1] == package
            or key.split("\t", 1)[-1].startswith(package + "/")
            for package in old_packages
        )
    }
    for package in old_packages:
        receipt["plugin_capability_surfaces"].pop(package, None)
        receipt["plugin_package_manifests"].pop(package, None)

    packages: dict[str, object] = {}
    for plugin_name in sorted(recovery.PRIMARY_RUNTIME_REQUIRED_PACKAGES):
        package = f"openai-primary-runtime/{plugin_name}/{bundle_version}"
        authority_files = {".codex-plugin/plugin.json": marker * 64}
        surface = [{"id": f"plugin:{plugin_name}", "kind": "plugin"}]
        if extra_skill and plugin_name == "documents":
            authority_files[f"skills/{extra_skill}/skill.md"] = marker * 64
            surface.append(
                {
                    "id": f"skill:documents:{extra_skill}",
                    "kind": "skill",
                }
            )
        receipt["plugin_cache_row_hashes"][f"ROOT\t{package}"] = marker * 64
        for relative in authority_files:
            receipt["plugin_cache_row_hashes"][
                f"FILE\t{package}/{relative}"
            ] = marker * 64
        receipt["plugin_capability_surfaces"][package] = sorted(
            surface, key=lambda item: (item["kind"], item["id"])
        )
        receipt["plugin_package_manifests"][package] = {
            "name": plugin_name,
            "version": bundle_version,
            "manifest_sha256": marker * 64,
        }
        authority_sha256 = recovery._sha256_text(
            recovery._canonical_json(authority_files)
        )
        packages[plugin_name] = {
            "plugin_name": plugin_name,
            "plugin_version": bundle_version,
            "bundle_package": (
                "plugins/openai-primary-runtime/plugins/" + plugin_name
            ),
            "cache_package": package,
            "authority_file_hashes": authority_files,
            "bundle_authority_sha256": authority_sha256,
            "cache_authority_sha256": authority_sha256,
        }
    receipt["primary_runtime_bundle_origin"] = {
        "schema_version": recovery.PRIMARY_RUNTIME_BUNDLE_SCHEMA,
        "bundle_format_version": 2,
        "bundle_version": bundle_version,
        "runtime_json_sha256": marker * 64,
        "authority_projection_sha256": recovery._sha256_text(
            recovery._canonical_json(packages)
        ),
        "packages": packages,
    }
    receipt["plugin_cache_inventory_sha256"] = marker * 64
    receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)
    return receipt


def _compound_app_primary_runtime_receipt(
    app_version: str,
    runtime_id: str,
    cli_id: str,
    app_marker: str,
    bundle_version: str,
    bundle_marker: str,
    *,
    sites_version: str = "0.1.34",
    visualize_version: str = "1.0.20",
) -> dict:
    app_receipt = _receipt(
        app_version,
        runtime_id,
        cli_id,
        app_marker,
        sites_version=sites_version,
        visualize_version=visualize_version,
    )
    receipt = _primary_runtime_receipt(bundle_version, bundle_marker)
    for field in (
        "config_projection_sha256",
        "config_leaf_hashes",
        "config_capability_surfaces",
        "app_identity",
        "bundled_marketplace_origin",
        "remote_plugin_catalog_entries",
    ):
        receipt[field] = copy.deepcopy(app_receipt[field])

    def primary_package(package: str) -> bool:
        return package.startswith("openai-primary-runtime/")

    primary_rows = {
        key: value
        for key, value in receipt["plugin_cache_row_hashes"].items()
        if primary_package(key.split("\t", 1)[-1])
    }
    receipt["plugin_cache_row_hashes"] = {
        key: value
        for key, value in app_receipt["plugin_cache_row_hashes"].items()
        if not primary_package(key.split("\t", 1)[-1])
    }
    receipt["plugin_cache_row_hashes"].update(primary_rows)
    for field in ("plugin_capability_surfaces", "plugin_package_manifests"):
        primary_values = {
            package: value
            for package, value in receipt[field].items()
            if primary_package(package)
        }
        receipt[field] = {
            package: copy.deepcopy(value)
            for package, value in app_receipt[field].items()
            if not primary_package(package)
        }
        receipt[field].update(primary_values)
    receipt["plugin_package_origins"] = copy.deepcopy(
        app_receipt["plugin_package_origins"]
    )
    receipt["plugin_cache_inventory_sha256"] = bundle_marker * 64
    receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)
    return receipt


def _write_remote_catalog_fixture(
    codex_home: Path,
    *,
    plugin_name: str = "neon-postgres",
    plugin_version: str = "1.1.0",
    remote_plugin_id: str = "plugins~Plugin_neon_12345678",
    catalog_remote_plugin_id: str | None = None,
    catalog_version: str | None = None,
    catalog_app_id: str | None = None,
    local_app_id: str | None = None,
    fetched_at: str = "2026-08-14T12:00:00Z",
) -> tuple[str, dict[str, dict[str, object]], Path]:
    package = f"openai-curated-remote/{plugin_name}/{plugin_version}"
    package_root = codex_home / "plugins" / "cache" / Path(package)
    package_root.mkdir(parents=True, exist_ok=True)
    default_app_id = f"asdk_app_{'1' * 32}"
    (package_root / ".app.json").write_text(
        json.dumps(
            {
                "apps": {
                    plugin_name: {
                        "id": local_app_id or default_app_id,
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    catalog_path = (
        codex_home
        / recovery.REMOTE_PLUGIN_CATALOG_RELATIVE
        / "current.json"
    )
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fetched_at": fetched_at,
                "plugins": [
                    {
                        "id": catalog_remote_plugin_id or remote_plugin_id,
                        "name": plugin_name,
                        "status": "AVAILABLE",
                        "installation_policy": "AVAILABLE",
                        "release": {
                            "version": catalog_version or plugin_version,
                            "app_ids": [catalog_app_id or default_app_id],
                            "app_manifest": {
                                "apps": {
                                    plugin_name: {
                                        "id": catalog_app_id or default_app_id,
                                    }
                                }
                            },
                            "skills": [{"name": plugin_name}],
                        },
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    origins = {
        package: _managed_origin(
            package,
            manifest_sha256="A" * 64,
            package_sha256="B" * 64,
            install_receipt_sha256="C" * 64,
            remote_plugin_id=remote_plugin_id,
            receipt_schema_version=1,
            attestation_mode="plugin-root-v1",
        )
    }
    return package, origins, catalog_path


class RecoveryClassificationTests(unittest.TestCase):
    def test_worker_runtime_bom_update_requires_one_isolated_hash_delta(self) -> None:
        previous, current = _worker_bom_receipts()
        self.assertEqual(
            recovery.classify_worker_runtime_bom_update(previous, current),
            (True, "RECOGNIZED_WORKER_RUNTIME_BOM_UPDATE"),
        )
        mixed = copy.deepcopy(current)
        mixed["required_source_hashes"]["capability_index.py"] = "9" * 64
        mixed["snapshot_sha256"] = recovery.authority_snapshot_digest(mixed)
        self.assertEqual(
            recovery.classify_worker_runtime_bom_update(previous, mixed)[1],
            "WORKER_RUNTIME_BOM_DELTA_NOT_ISOLATED",
        )
        promoter_only = copy.deepcopy(previous)
        promoter_only["required_source_hashes"][
            recovery.index.WORKER_RUNTIME_BOM_PROMOTER_SOURCE_HASH_KEY
        ] = "9" * 64
        promoter_only["snapshot_sha256"] = recovery.authority_snapshot_digest(
            promoter_only
        )
        self.assertEqual(
            recovery.classify_worker_runtime_bom_update(previous, promoter_only)[1],
            "WORKER_RUNTIME_BOM_DELTA_NOT_ISOLATED",
        )
        promoter_and_bom = copy.deepcopy(current)
        promoter_and_bom["required_source_hashes"][
            recovery.index.WORKER_RUNTIME_BOM_PROMOTER_SOURCE_HASH_KEY
        ] = "9" * 64
        promoter_and_bom["snapshot_sha256"] = recovery.authority_snapshot_digest(
            promoter_and_bom
        )
        self.assertEqual(
            recovery.classify_worker_runtime_bom_update(
                previous, promoter_and_bom
            )[1],
            "WORKER_RUNTIME_BOM_DELTA_NOT_ISOLATED",
        )
        unrelated = copy.deepcopy(current)
        unrelated["app_identity"]["cli_id"] = "unrelated-cli"
        unrelated["snapshot_sha256"] = recovery.authority_snapshot_digest(unrelated)
        self.assertEqual(
            recovery.classify_worker_runtime_bom_update(previous, unrelated)[1],
            "WORKER_RUNTIME_BOM_HAS_UNRELATED_AUTHORITY_DELTA",
        )

    def test_exact_coherent_app_update_is_recognized(self) -> None:
        previous = _receipt(
            "26.803.41515",
            "runtime-old",
            "cli-old",
            "A",
            sites_version="0.1.34",
            visualize_version="1.0.20",
        )
        current = _receipt(
            "26.810.41047",
            "runtime-new",
            "cli-new",
            "B",
            sites_version="0.1.37",
            visualize_version="1.0.21",
        )

        allowed, reason = recovery.classify_app_update(previous, current)

        self.assertTrue(allowed)
        self.assertEqual(reason, "RECOGNIZED_CODEX_DESKTOP_UPDATE")

    def test_app_update_requires_new_shipped_bundle_identity(self) -> None:
        shared_bundle_id = "144477fa-b5ce-4ee9-8e77-dd8e42a42d12"
        previous = _receipt(
            "26.803.41515",
            "runtime-old",
            "cli-old",
            "A",
            bundle_id=shared_bundle_id,
        )
        current = _receipt(
            "26.810.41047",
            "runtime-new",
            "cli-new",
            "B",
            bundle_id=shared_bundle_id,
        )

        self.assertEqual(
            recovery.classify_app_update(previous, current),
            (False, "APP_BUNDLE_ID_DID_NOT_ADVANCE"),
        )

    def test_exact_primary_runtime_bundle_update_is_recognized(self) -> None:
        previous = _primary_runtime_receipt("26.805.1", "B")
        current = _primary_runtime_receipt("26.812.1", "C")

        allowed, reason = recovery.classify_primary_runtime_bundle_update(
            previous, current
        )

        self.assertTrue(recovery._valid_receipt(previous))
        self.assertTrue(recovery._valid_receipt(current))
        self.assertTrue(allowed)
        self.assertEqual(reason, "RECOGNIZED_PRIMARY_RUNTIME_BUNDLE_UPDATE")
        self.assertEqual(
            recovery.classify_authority_update(previous, current),
            (True, "RECOGNIZED_PRIMARY_RUNTIME_BUNDLE_UPDATE"),
        )

    def test_primary_runtime_bundle_update_requires_strictly_newer_version(self) -> None:
        previous = _primary_runtime_receipt("26.812.1", "B")
        downgrade = _primary_runtime_receipt("26.805.1", "C")
        same_version = _primary_runtime_receipt("26.812.1", "C")

        self.assertEqual(
            recovery.classify_primary_runtime_bundle_update(previous, downgrade),
            (False, "PRIMARY_RUNTIME_BUNDLE_VERSION_NOT_NEWER"),
        )
        self.assertEqual(
            recovery.classify_primary_runtime_bundle_update(previous, same_version),
            (False, "PRIMARY_RUNTIME_BUNDLE_VERSION_NOT_NEWER"),
        )

    def test_primary_runtime_bundle_update_denies_surface_or_unrelated_delta(self) -> None:
        previous = _primary_runtime_receipt("26.805.1", "B")
        expanded = _primary_runtime_receipt(
            "26.812.1", "C", extra_skill="new-skill"
        )
        unrelated = _primary_runtime_receipt("26.812.1", "C")
        unrelated["plugin_cache_row_hashes"][
            "ROOT\topenai-curated-remote/github/1.0.0"
        ] = "D" * 64
        unrelated["snapshot_sha256"] = recovery.authority_snapshot_digest(
            unrelated
        )

        self.assertEqual(
            recovery.classify_primary_runtime_bundle_update(previous, expanded)[1],
            "PRIMARY_RUNTIME_CAPABILITY_SURFACE_CHANGED",
        )
        self.assertEqual(
            recovery.classify_primary_runtime_bundle_update(previous, unrelated)[1],
            "UNRELATED_PRIMARY_RUNTIME_CACHE_DELTA",
        )

    def test_primary_runtime_bundle_update_denies_mixed_authority_changes(self) -> None:
        previous = _primary_runtime_receipt("26.805.1", "B")
        config_changed = _primary_runtime_receipt("26.812.1", "C")
        config_changed["config_projection_sha256"] = "D" * 64
        config_changed["snapshot_sha256"] = recovery.authority_snapshot_digest(
            config_changed
        )
        source_changed = _primary_runtime_receipt("26.812.1", "C")
        source_changed["required_source_hashes"]["capability_index.py"] = "D" * 64
        source_changed["snapshot_sha256"] = recovery.authority_snapshot_digest(
            source_changed
        )
        app_changed = _primary_runtime_receipt("26.812.1", "C")
        app_changed["app_identity"]["runtime_id"] = "runtime-changed"
        app_changed["snapshot_sha256"] = recovery.authority_snapshot_digest(
            app_changed
        )

        self.assertEqual(
            recovery.classify_primary_runtime_bundle_update(
                previous, config_changed
            )[1],
            "PRIMARY_RUNTIME_CONFIG_CHANGED",
        )
        self.assertEqual(
            recovery.classify_primary_runtime_bundle_update(
                previous, source_changed
            )[1],
            "UNRECOGNIZED_SOURCE_DELTA",
        )
        self.assertEqual(
            recovery.classify_primary_runtime_bundle_update(previous, app_changed)[1],
            "PRIMARY_RUNTIME_APP_IDENTITY_CHANGED",
        )

    def test_primary_runtime_bundle_origin_rejects_unmatched_cache_bytes(self) -> None:
        current = _primary_runtime_receipt("26.812.1", "C")
        current["primary_runtime_bundle_origin"]["packages"]["documents"][
            "cache_authority_sha256"
        ] = "D" * 64
        current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)

        self.assertFalse(recovery._valid_receipt(current))

    def test_compound_app_primary_runtime_update_is_recognized_atomically(self) -> None:
        previous = _compound_app_primary_runtime_receipt(
            "26.803.41515",
            "runtime-old",
            "cli-old",
            "A",
            "26.805.1",
            "B",
            sites_version="0.1.34",
            visualize_version="1.0.20",
        )
        current = _compound_app_primary_runtime_receipt(
            "26.810.41047",
            "runtime-new",
            "cli-new",
            "C",
            "26.812.1",
            "D",
            sites_version="0.1.37",
            visualize_version="1.0.21",
        )

        allowed, reason = recovery.classify_compound_app_primary_runtime_update(
            previous, current
        )
        app_only, primary_only, composed = (
            recovery._compound_app_primary_runtime_receipts(previous, current)
        )

        self.assertTrue(recovery._valid_receipt(previous))
        self.assertTrue(recovery._valid_receipt(current))
        self.assertEqual(
            recovery.classify_app_update(previous, app_only),
            (True, "RECOGNIZED_CODEX_DESKTOP_UPDATE"),
        )
        self.assertEqual(
            recovery.classify_primary_runtime_bundle_update(
                previous, primary_only
            ),
            (True, "RECOGNIZED_PRIMARY_RUNTIME_BUNDLE_UPDATE"),
        )
        self.assertEqual(composed, current)
        self.assertTrue(allowed)
        self.assertEqual(
            reason, "RECOGNIZED_COMPOUND_APP_PRIMARY_RUNTIME_UPDATE"
        )
        self.assertEqual(
            recovery.classify_authority_update(previous, current),
            (True, "RECOGNIZED_COMPOUND_APP_PRIMARY_RUNTIME_UPDATE"),
        )

    def test_compound_app_primary_runtime_update_denies_unrelated_cache_change(self) -> None:
        previous = _compound_app_primary_runtime_receipt(
            "26.803.41515",
            "runtime-old",
            "cli-old",
            "A",
            "26.805.1",
            "B",
        )
        current = _compound_app_primary_runtime_receipt(
            "26.810.41047",
            "runtime-new",
            "cli-new",
            "C",
            "26.812.1",
            "D",
        )
        current["plugin_cache_row_hashes"][
            "ROOT\topenai-curated-remote/github/1.0.0"
        ] = "E" * 64
        current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)

        self.assertEqual(
            recovery.classify_compound_app_primary_runtime_update(
                previous, current
            ),
            (False, "COMPOUND_UPDATE_HAS_UNRELATED_AUTHORITY_DELTA"),
        )
        self.assertEqual(
            recovery.classify_authority_update(previous, current),
            (False, "COMPOUND_UPDATE_HAS_UNRELATED_AUTHORITY_DELTA"),
        )

    def test_primary_runtime_origin_uses_only_canonical_user_cache_root(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"CODEX_PRIMARY_RUNTIME_ROOT": r"C:\attacker-controlled"},
        ):
            resolved = recovery._primary_runtime_root()

        self.assertEqual(
            resolved,
            Path.home()
            / ".cache"
            / "codex-runtimes"
            / "codex-primary-runtime",
        )

    def test_primary_runtime_origin_requires_exact_bundle_cache_authority_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            codex_home = root / ".codex"
            runtime_root = root / "codex-primary-runtime"
            version = "26.812.1"
            (runtime_root / "runtime.json").parent.mkdir(parents=True)
            (runtime_root / "runtime.json").write_text(
                json.dumps(
                    {
                        "bundleFormatVersion": 2,
                        "bundleVersion": version,
                        "bundledPlugins": ["plugins/openai-primary-runtime"],
                    }
                ),
                encoding="utf-8",
            )
            cache_skill: Path | None = None
            for plugin_name in sorted(
                recovery.PRIMARY_RUNTIME_REQUIRED_PACKAGES
            ):
                bundle_package = (
                    runtime_root
                    / "plugins"
                    / "openai-primary-runtime"
                    / "plugins"
                    / plugin_name
                )
                cache_package = (
                    codex_home
                    / "plugins"
                    / "cache"
                    / "openai-primary-runtime"
                    / plugin_name
                    / version
                )
                manifest = json.dumps(
                    {"name": plugin_name, "version": version},
                    sort_keys=True,
                )
                for package in (bundle_package, cache_package):
                    manifest_path = package / ".codex-plugin" / "plugin.json"
                    manifest_path.parent.mkdir(parents=True)
                    manifest_path.write_text(manifest, encoding="utf-8")
                    skill_path = package / "skills" / plugin_name / "SKILL.md"
                    skill_path.parent.mkdir(parents=True)
                    skill_path.write_text(
                        f"---\nname: {plugin_name}\n---\n",
                        encoding="utf-8",
                    )
                    if package == cache_package and plugin_name == "documents":
                        cache_skill = skill_path

            origin = recovery._primary_runtime_bundle_origin(
                codex_home, runtime_root=runtime_root
            )

            self.assertEqual(origin["bundle_version"], version)
            self.assertEqual(
                set(origin["packages"]),
                recovery.PRIMARY_RUNTIME_REQUIRED_PACKAGES,
            )
            assert cache_skill is not None
            cache_skill.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(
                RuntimeError, "bundle and cache authority content differ"
            ):
                recovery._primary_runtime_bundle_origin(
                    codex_home, runtime_root=runtime_root
                )

    @unittest.skipUnless(
        (
            Path.home()
            / ".cache"
            / "codex-runtimes"
            / "codex-primary-runtime"
            / "runtime.json"
        ).is_file()
        and (Path.home() / ".codex" / "plugins" / "cache").is_dir(),
        "canonical Codex primary-runtime bundle is unavailable",
    )
    def test_current_primary_runtime_layout_is_byte_coherent(self) -> None:
        origin = recovery._primary_runtime_bundle_origin(
            Path.home() / ".codex"
        )

        self.assertEqual(origin["schema_version"], recovery.PRIMARY_RUNTIME_BUNDLE_SCHEMA)
        self.assertEqual(
            set(origin["packages"]), recovery.PRIMARY_RUNTIME_REQUIRED_PACKAGES
        )
        for package in origin["packages"].values():
            self.assertEqual(
                package["bundle_authority_sha256"],
                package["cache_authority_sha256"],
            )

    @unittest.skipUnless(
        (
            Path.home()
            / ".codex"
            / recovery.BUNDLED_MARKETPLACE_RELATIVE
            / ".materialization-key"
        ).is_file()
        and (Path.home() / ".codex" / "plugins" / "cache" / "openai-bundled").is_dir(),
        "materialized bundled marketplace is unavailable",
    )
    def test_current_bundled_marketplace_closes_full_active_cohort(self) -> None:
        origin = recovery._bundled_marketplace_origin(Path.home() / ".codex")

        self.assertEqual(
            set(origin["packages"]),
            {"browser", "chrome", "computer-use", "latex", "sites", "visualize"},
        )
        self.assertEqual(origin["packages"]["latex"]["cache_packages"], [])
        self.assertEqual(
            {
                package.split("/")[1]
                for item in origin["packages"].values()
                for package in item["cache_packages"]
            },
            {"browser", "chrome", "computer-use", "sites", "visualize"},
        )

    def test_unknown_config_leaf_change_is_denied(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt(
            "26.803.81509",
            "runtime-new",
            "cli-new",
            "B",
            config_overrides={"/features/hooks": "7" * 64},
        )

        allowed, reason = recovery.classify_app_update(previous, current)

        self.assertFalse(allowed)
        self.assertEqual(reason, "UNRECOGNIZED_CONFIG_DELTA")

    def test_exact_disable_toggle_is_scoped(self) -> None:
        previous = _receipt("26.803.81509", "runtime", "cli", "A")
        current = copy.deepcopy(previous)
        _set_toggle_control(
            previous,
            pointer="/plugins/vercel@openai-curated/enabled",
            control_kind="plugin",
            control_key="vercel@openai-curated",
            enabled=True,
            capability_ids=["plugin:vercel", "skill:vercel:vercel"],
            required_ids=["plugin:vercel"],
        )
        _set_toggle_control(
            current,
            pointer="/plugins/vercel@openai-curated/enabled",
            control_kind="plugin",
            control_key="vercel@openai-curated",
            enabled=False,
            capability_ids=["plugin:vercel", "skill:vercel:vercel"],
            required_ids=["plugin:vercel"],
        )
        plan, reason = recovery._scoped_config_update_plan(previous, current)

        self.assertEqual(reason, "RECOGNIZED_SCOPED_CONFIG_UPDATE")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(
            set(plan["required_disabled_capability_ids"]),
            {"plugin:vercel", "skill:vercel:vercel"},
        )
        self.assertEqual(plan["required_enabled_capability_ids"], [])

    def test_local_skill_enablement_requires_rebaseline_but_disable_is_scoped(self) -> None:
        skill_pointer = "/skills/c:\\users\\test\\skill.md/enabled"
        skill_id = "skill:sample"
        skill_disabled = _receipt("26.803.81509", "runtime", "cli", "A")
        skill_default_enabled = copy.deepcopy(skill_disabled)
        _set_toggle_control(
            skill_disabled,
            pointer=skill_pointer,
            control_kind="skill",
            control_key="c:\\users\\test\\skill.md",
            enabled=False,
            capability_ids=[skill_id],
        )
        skill_default_enabled["config_projection_sha256"] = "E" * 64
        skill_default_enabled["snapshot_sha256"] = recovery.authority_snapshot_digest(
            skill_default_enabled
        )

        plan, reason = recovery._scoped_config_update_plan(
            skill_disabled, skill_default_enabled
        )
        self.assertIsNone(plan)
        self.assertEqual(
            reason, "LOCAL_SKILL_ENABLEMENT_REQUIRES_OPERATOR_REBASELINE"
        )

        plan, reason = recovery._scoped_config_update_plan(
            skill_default_enabled, skill_disabled
        )
        self.assertEqual(reason, "RECOGNIZED_SCOPED_CONFIG_UPDATE")
        assert plan is not None
        self.assertEqual(plan["required_disabled_capability_ids"], [skill_id])

        mcp_absent = _receipt("26.803.81509", "runtime", "cli", "A")
        mcp_added = copy.deepcopy(mcp_absent)
        _set_toggle_control(
            mcp_added,
            pointer="/mcp_servers/n8n/enabled",
            control_kind="mcp",
            control_key="n8n",
            enabled=True,
            capability_ids=["mcp:n8n"],
        )
        plan, reason = recovery._scoped_config_update_plan(mcp_absent, mcp_added)
        self.assertIsNone(plan)
        self.assertEqual(
            reason, "CONFIG_ENABLEMENT_REQUIRES_OPERATOR_REBASELINE"
        )

    def test_local_skill_content_change_cannot_ride_false_to_true(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = Path(temp_dir) / "sample" / "SKILL.md"
            skill_path.parent.mkdir()
            skill_path.write_text("baseline\n", encoding="utf-8")
            previous = _receipt("26.803.81509", "runtime", "cli", "A")
            current = copy.deepcopy(previous)
            pointer = "/skills/" + str(skill_path).replace("/", "\\").lower() + "/enabled"
            _set_toggle_control(
                previous,
                pointer=pointer,
                control_kind="skill",
                control_key=str(skill_path),
                enabled=False,
                capability_ids=["skill:sample"],
            )
            _set_toggle_control(
                current,
                pointer=pointer,
                control_kind="skill",
                control_key=str(skill_path),
                enabled=True,
                capability_ids=["skill:sample"],
            )
            skill_path.write_text("changed while enabled\n", encoding="utf-8")

            plan, reason = recovery._scoped_config_update_plan(previous, current)

        self.assertIsNone(plan)
        self.assertEqual(
            reason, "LOCAL_SKILL_ENABLEMENT_REQUIRES_OPERATOR_REBASELINE"
        )

    def test_runtime_or_mixed_unknown_config_delta_requires_operator(self) -> None:
        previous = _receipt("26.803.81509", "runtime", "cli", "A")
        current = copy.deepcopy(previous)
        _set_toggle_control(
            previous,
            pointer="/mcp_servers/n8n/enabled",
            control_kind="mcp",
            control_key="n8n",
            enabled=False,
            capability_ids=["mcp:n8n"],
        )
        _set_toggle_control(
            current,
            pointer="/mcp_servers/n8n/enabled",
            control_kind="mcp",
            control_key="n8n",
            enabled=True,
            capability_ids=["mcp:n8n"],
        )
        runtime_pointer = "/mcp_servers/n8n/runtime/command"
        current["config_leaf_hashes"][runtime_pointer] = "F" * 64
        current["config_capability_surfaces"][runtime_pointer] = {
            "change_class": "runtime_identity",
            "control_kind": "mcp_runtime",
            "control_key": "n8n",
            "capability_ids": ["mcp:n8n"],
            "required_capability_ids": ["mcp:n8n"],
        }
        current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)

        allowed, reason = recovery.classify_scoped_config_update(previous, current)

        self.assertFalse(allowed)
        self.assertEqual(
            reason, "CONFIG_RUNTIME_IDENTITY_REQUIRES_OPERATOR_REBASELINE"
        )

    def test_config_candidate_never_admits_new_metadata_for_enablement(self) -> None:
        plan = {
            "affected_capability_ids": ["mcp:n8n"],
            "expected_enabled_surface_ids": ["mcp:n8n"],
            "expected_disabled_surface_ids": [],
            "required_enabled_capability_ids": ["mcp:n8n"],
            "required_disabled_capability_ids": [],
        }
        baseline = {"entries": []}
        valid = {
            "entries": [
                {"id": "mcp:n8n", "kind": "mcp", "state": "active"}
            ]
        }
        unrelated = {
            "entries": [
                {"id": "mcp:n8n", "kind": "mcp", "state": "active"},
                {"id": "skill:other", "kind": "skill", "state": "active"},
            ]
        }
        valid["entries"][0].update(
            {
                "provider": "malicious",
                "families": ["unreviewed"],
                "source_path": "unbound.py",
                "sha256": "f" * 64,
                "version": "unreviewed",
            }
        )
        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(baseline, valid, plan)
        )
        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(
                baseline, unrelated, plan
            )
        )

    def test_config_candidate_must_account_for_entire_enabled_plugin_surface(self) -> None:
        plan = {
            "affected_capability_ids": [
                "plugin:sample",
                "skill:sample:a",
                "skill:sample:b",
            ],
            "expected_enabled_surface_ids": [
                "plugin:sample",
                "skill:sample:a",
                "skill:sample:b",
            ],
            "expected_disabled_surface_ids": [],
            "required_enabled_capability_ids": ["plugin:sample"],
            "required_disabled_capability_ids": [],
        }
        baseline = {"entries": []}
        partial = {
            "entries": [
                {"id": "plugin:sample", "kind": "plugin", "state": "active"}
            ]
        }
        swapped_partial = {
            "entries": [
                {"id": "plugin:sample", "kind": "plugin", "state": "active"},
                {"id": "skill:sample:b", "kind": "skill", "state": "active"},
            ]
        }
        accounted = {
            "entries": [
                {"id": "plugin:sample", "kind": "plugin", "state": "active"},
                {"id": "skill:sample:b", "kind": "skill", "state": "active"},
            ],
            "suppressed_capabilities": [
                {
                    "id": "skill:sample:a",
                    "reason_code": "HARD_DEPENDENCY_DISABLED_OR_UNCALLABLE",
                    "missing_capability": "mcp:sample",
                    "fallback_capabilities": [],
                }
            ],
        }

        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(
                baseline, partial, plan
            )
        )
        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(
                baseline, swapped_partial, plan
            )
        )
        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(
                baseline, accounted, plan
            )
        )

    def test_config_candidate_rejects_arbitrary_suppression_reason(self) -> None:
        plan = {
            "affected_capability_ids": ["plugin:sample", "skill:sample:a"],
            "expected_enabled_surface_ids": ["plugin:sample", "skill:sample:a"],
            "expected_disabled_surface_ids": [],
            "required_enabled_capability_ids": ["plugin:sample"],
            "required_disabled_capability_ids": [],
        }
        candidate = {
            "entries": [
                {"id": "plugin:sample", "kind": "plugin", "state": "active"}
            ],
            "suppressed_capabilities": [
                {"id": "skill:sample:a", "reason_code": "ARBITRARY"}
            ],
        }
        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(
                {"entries": []}, candidate, plan
            )
        )

    def test_config_candidate_rejects_unrelated_suppression_addition(self) -> None:
        plan = {
            "affected_capability_ids": ["mcp:n8n"],
            "expected_enabled_surface_ids": ["mcp:n8n"],
            "expected_disabled_surface_ids": [],
            "required_enabled_capability_ids": ["mcp:n8n"],
            "required_disabled_capability_ids": [],
        }
        candidate = {
            "entries": [{"id": "mcp:n8n", "kind": "mcp", "state": "active"}],
            "suppressed_capabilities": [
                {
                    "id": "mcp:other",
                    "reason_code": "LIVE_MCP_TRANSPORT_UNRESOLVABLE",
                    "missing_capability": None,
                    "fallback_capabilities": [],
                }
            ],
        }
        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(
                {"entries": []}, candidate, plan
            )
        )

    def test_config_candidate_rejects_active_and_suppressed_duplicate(self) -> None:
        plan = {
            "affected_capability_ids": ["plugin:sample", "skill:sample:a"],
            "expected_enabled_surface_ids": ["plugin:sample", "skill:sample:a"],
            "expected_disabled_surface_ids": [],
            "required_enabled_capability_ids": ["plugin:sample"],
            "required_disabled_capability_ids": [],
        }
        candidate = {
            "entries": [
                {"id": "plugin:sample", "kind": "plugin", "state": "active"},
                {"id": "skill:sample:a", "kind": "skill", "state": "active"},
            ],
            "suppressed_capabilities": [
                {
                    "id": "skill:sample:a",
                    "reason_code": "HARD_DEPENDENCY_DISABLED_OR_UNCALLABLE",
                    "missing_capability": "mcp:sample",
                    "fallback_capabilities": [],
                }
            ],
        }
        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(
                {"entries": []}, candidate, plan
            )
        )

    def test_config_candidate_preserves_unaffected_suppression_exactly(self) -> None:
        suppression = {
            "id": "mcp:other",
            "reason_code": "LIVE_MCP_TRANSPORT_UNRESOLVABLE",
            "missing_capability": None,
            "fallback_capabilities": [],
        }
        plan = {
            "affected_capability_ids": ["mcp:n8n"],
            "expected_enabled_surface_ids": ["mcp:n8n"],
            "expected_disabled_surface_ids": [],
            "required_enabled_capability_ids": ["mcp:n8n"],
            "required_disabled_capability_ids": [],
        }
        baseline = {"entries": [], "suppressed_capabilities": [suppression]}
        candidate = {
            "entries": [{"id": "mcp:n8n", "kind": "mcp", "state": "active"}],
            "suppressed_capabilities": [],
        }
        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(
                baseline, candidate, plan
            )
        )

    def test_config_disable_rejects_affected_common_evidence_mutation(self) -> None:
        plan = {
            "affected_capability_ids": ["mcp:n8n", "skill:optional"],
            "expected_enabled_surface_ids": [],
            "expected_disabled_surface_ids": ["mcp:n8n"],
            "required_enabled_capability_ids": [],
            "required_disabled_capability_ids": ["mcp:n8n"],
        }
        baseline = {
            "entries": [
                {"id": "mcp:n8n", "kind": "mcp", "state": "active"},
                {
                    "id": "skill:optional",
                    "kind": "skill",
                    "state": "active",
                    "source_path": "stable.md",
                    "sha256": "a" * 64,
                    "version": "1.0.0",
                },
            ]
        }
        candidate = copy.deepcopy(baseline)
        candidate["entries"] = [candidate["entries"][1]]
        candidate["entries"][0]["source_path"] = "changed.md"
        candidate["entries"][0]["sha256"] = "b" * 64

        self.assertFalse(
            recovery._config_candidate_surface_matches_plan(
                baseline, candidate, plan
            )
        )

    def test_plugin_only_or_config_only_update_is_denied(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        plugin_only = _receipt("26.803.81509", "runtime-new", "cli-new", "B")
        for field in (
            "config_projection_sha256",
            "config_leaf_hashes",
            "config_capability_surfaces",
        ):
            plugin_only[field] = copy.deepcopy(previous[field])
        plugin_only["snapshot_sha256"] = recovery.authority_snapshot_digest(plugin_only)
        config_only = _receipt("26.803.81509", "runtime-new", "cli-new", "B")
        old_app_packages = recovery._bundled_cache_packages(
            previous["bundled_marketplace_origin"]
        )
        new_app_packages = recovery._bundled_cache_packages(
            config_only["bundled_marketplace_origin"]
        )
        recovery._replace_receipt_package_authority(
            config_only,
            previous,
            old_app_packages | new_app_packages,
        )
        config_only["bundled_marketplace_origin"] = copy.deepcopy(
            previous["bundled_marketplace_origin"]
        )
        config_only["plugin_cache_inventory_sha256"] = previous[
            "plugin_cache_inventory_sha256"
        ]
        config_only["snapshot_sha256"] = recovery.authority_snapshot_digest(config_only)

        self.assertEqual(
            recovery.classify_app_update(previous, plugin_only)[1],
            "APP_CONFIG_COHORT_INCOMPLETE",
        )
        self.assertEqual(
            recovery.classify_app_update(previous, config_only)[1],
            "INVALID_CURRENT_AUTHORITY_RECEIPT",
        )

    def test_unrelated_in_place_plugin_mutation_is_denied(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt(
            "26.803.81509",
            "runtime-new",
            "cli-new",
            "B",
            row_overrides={"ROOT\topenai-curated-remote/github/1.0.0": "7" * 64},
        )

        allowed, reason = recovery.classify_app_update(previous, current)

        self.assertFalse(allowed)
        self.assertEqual(reason, "UNRECOGNIZED_PLUGIN_DELTA")

    def test_app_update_rejects_unrelated_v2_plugin_authority_deltas(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt("26.803.81509", "runtime-new", "cli-new", "B")
        package = "openai-curated-remote/github/1.0.0"

        cases: list[tuple[str, tuple[dict, dict], str]] = []
        origin_previous = copy.deepcopy(previous)
        origin_current = copy.deepcopy(current)
        for receipt in (origin_previous, origin_current):
            receipt["plugin_package_manifests"][package] = {
                "name": "github",
                "version": "1.0.0",
                "manifest_sha256": "3" * 64,
            }
        origin_previous["plugin_package_origins"][package] = _managed_origin(
            package,
            manifest_sha256="3" * 64,
            package_sha256="4" * 64,
            install_receipt_sha256="1" * 64,
            remote_plugin_id="plugins~Plugin_abcdefgh",
        )
        origin_current["plugin_package_origins"][package] = _managed_origin(
            package,
            manifest_sha256="3" * 64,
            package_sha256="4" * 64,
            install_receipt_sha256="2" * 64,
            remote_plugin_id="plugins~Plugin_ijklmnop",
        )
        cases.append(
            ("origin", (origin_previous, origin_current), "UNRELATED_PLUGIN_ORIGIN_DELTA")
        )

        manifest_previous = copy.deepcopy(previous)
        manifest_current = copy.deepcopy(current)
        manifest_previous["plugin_package_manifests"][package] = {
            "name": "github",
            "version": "1.0.0",
            "manifest_sha256": "1" * 64,
        }
        manifest_current["plugin_package_manifests"][package] = {
            "name": "github",
            "version": "1.0.0",
            "manifest_sha256": "2" * 64,
        }
        cases.append(
            (
                "manifest",
                (manifest_previous, manifest_current),
                "UNRELATED_PLUGIN_AUTHORITY_DELTA",
            )
        )

        surface_previous = copy.deepcopy(previous)
        surface_current = copy.deepcopy(current)
        surface_current["plugin_capability_surfaces"][package].append(
            {"id": "skill:github:changed", "kind": "skill"}
        )
        cases.append(
            (
                "surface",
                (surface_previous, surface_current),
                "UNRELATED_PLUGIN_CAPABILITY_DELTA",
            )
        )

        for name, pair, expected_reason in cases:
            with self.subTest(name=name):
                candidate_previous, candidate_current = pair
                for receipt in (candidate_previous, candidate_current):
                    receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(
                        receipt
                    )
                allowed, reason = recovery.classify_app_update(
                    candidate_previous, candidate_current
                )
                self.assertFalse(allowed)
                self.assertEqual(reason, expected_reason)

    def test_malformed_or_self_inconsistent_receipt_is_denied(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt("26.803.81509", "runtime-new", "cli-new", "B")
        current["snapshot_sha256"] = "0" * 64

        allowed, reason = recovery.classify_app_update(previous, current)

        self.assertFalse(allowed)
        self.assertEqual(reason, "INVALID_CURRENT_AUTHORITY_RECEIPT")

    def test_exact_version_bound_plugin_replacement_is_recognized(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")

        allowed, reason = recovery.classify_enabled_plugin_version_replacement(
            previous, current
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "RECOGNIZED_ENABLED_PLUGIN_VERSION_REPLACEMENT")

    def test_exact_remote_marketplace_enablement_is_preferred(self) -> None:
        previous = _curated_plugin_receipt(
            "1.0.0",
            "B",
            plugin_name="neon-postgres",
            config_marketplace="openai-curated-remote",
        )
        current = _curated_plugin_receipt(
            "1.1.0",
            "C",
            plugin_name="neon-postgres",
            config_marketplace="openai-curated-remote",
        )

        allowed, reason = recovery.classify_enabled_plugin_version_replacement(
            previous, current
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "RECOGNIZED_ENABLED_PLUGIN_VERSION_REPLACEMENT")

    def test_real_additive_curated_cache_update_is_recognized(self) -> None:
        receipt_digest = "9" * 64
        previous = _curated_plugin_receipt(
            "0.1.18",
            "B",
            plugin_name="codex-security",
            config_marketplace="openai-curated-remote",
            origin_digest=receipt_digest,
            receipt_schema_version=1,
        )
        current = _curated_plugin_receipt(
            "0.1.19",
            "C",
            plugin_name="codex-security",
            config_marketplace="openai-curated-remote",
            origin_digest=receipt_digest,
            receipt_schema_version=1,
        )
        old_package = "openai-curated-remote/codex-security/0.1.18"
        new_package = "openai-curated-remote/codex-security/0.1.19"
        current["plugin_capability_surfaces"][old_package] = copy.deepcopy(
            previous["plugin_capability_surfaces"][old_package]
        )
        current["plugin_package_manifests"][old_package] = copy.deepcopy(
            previous["plugin_package_manifests"][old_package]
        )
        current["plugin_package_origins"][old_package] = copy.deepcopy(
            previous["plugin_package_origins"][old_package]
        )
        current["remote_plugin_catalog_entries"][old_package] = copy.deepcopy(
            previous["remote_plugin_catalog_entries"][old_package]
        )
        for key, value in previous["plugin_cache_row_hashes"].items():
            if old_package in key:
                current["plugin_cache_row_hashes"][key] = value
        previous["snapshot_sha256"] = recovery.authority_snapshot_digest(previous)
        current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)

        allowed, reason = recovery.classify_enabled_plugin_version_replacement(
            previous, current
        )

        self.assertTrue(recovery._valid_receipt(previous))
        self.assertTrue(recovery._valid_receipt(current))
        self.assertTrue(allowed)
        self.assertEqual(reason, "RECOGNIZED_ENABLED_PLUGIN_VERSION_REPLACEMENT")

    def test_remote_false_overrides_enabled_legacy_alias(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")
        exact_pointer = "/plugins/gmail@openai-curated-remote/enabled"
        for receipt in (previous, current):
            receipt["config_leaf_hashes"][exact_pointer] = _boolean_leaf_hash(False)
            legacy = receipt["config_capability_surfaces"][
                "/plugins/gmail@openai-curated/enabled"
            ]
            receipt["config_capability_surfaces"][exact_pointer] = {
                **legacy,
                "control_key": "gmail@openai-curated-remote",
                "enabled": False,
            }
            receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)

        allowed, reason = recovery.classify_enabled_plugin_version_replacement(
            previous, current
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "PLUGIN_NOT_ENABLED")

    def test_plugin_replacement_requires_proven_version_specific_origin(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")
        unproven = copy.deepcopy(current)
        unproven["plugin_package_origins"] = {}
        unproven["snapshot_sha256"] = recovery.authority_snapshot_digest(unproven)
        changed = _curated_plugin_receipt(
            "0.1.8", "D", remote_plugin_id="plugin_asdk_other_12345678"
        )

        self.assertEqual(
            recovery.classify_enabled_plugin_version_replacement(
                previous, unproven
            )[1],
            "PLUGIN_ORIGIN_UNPROVEN",
        )
        self.assertEqual(
            recovery.classify_enabled_plugin_version_replacement(
                previous, changed
            )[1],
            "PLUGIN_ORIGIN_CHANGED",
        )

    def test_plugin_replacement_requires_directory_bound_manifest_identity(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")
        package = "openai-curated-remote/gmail/0.1.8"
        current["plugin_package_manifests"][package]["name"] = "other-plugin"
        current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)

        allowed, reason = recovery.classify_enabled_plugin_version_replacement(
            previous, current
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "INVALID_CURRENT_AUTHORITY_RECEIPT")

    def test_receipt_v1_and_missing_required_source_hashes_are_rejected(self) -> None:
        receipt_v1 = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        receipt_v1["schema_version"] = "capability-authority-receipt-v1"
        receipt_v1["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt_v1)
        missing_sources = _receipt(
            "26.803.41515", "runtime-old", "cli-old", "A"
        )
        del missing_sources["required_source_hashes"]
        missing_sources["snapshot_sha256"] = recovery.authority_snapshot_digest(
            missing_sources
        )
        empty_sources = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        empty_sources["required_source_hashes"] = {}
        empty_sources["snapshot_sha256"] = recovery.authority_snapshot_digest(
            empty_sources
        )
        partial_sources = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        partial_sources["required_source_hashes"].pop("_hook_io.py")
        partial_sources["snapshot_sha256"] = recovery.authority_snapshot_digest(
            partial_sources
        )

        self.assertFalse(recovery._valid_receipt(receipt_v1))
        self.assertFalse(recovery._valid_receipt(missing_sources))
        self.assertFalse(recovery._valid_receipt(empty_sources))
        self.assertFalse(recovery._valid_receipt(partial_sources))

    def test_plugin_replacement_requires_strictly_newer_parseable_version(self) -> None:
        previous = _curated_plugin_receipt("0.1.8", "B")
        downgrade = _curated_plugin_receipt("0.1.7", "C")
        unparseable = _curated_plugin_receipt("latest", "D")
        invalid_prerelease = _curated_plugin_receipt("0.1.9-01", "E")

        self.assertEqual(
            recovery.classify_enabled_plugin_version_replacement(
                previous, downgrade
            )[1],
            "PLUGIN_VERSION_NOT_NEWER",
        )
        self.assertEqual(
            recovery.classify_enabled_plugin_version_replacement(
                previous, unparseable
            )[1],
            "PLUGIN_VERSION_UNPARSEABLE",
        )
        self.assertEqual(
            recovery.classify_enabled_plugin_version_replacement(
                previous, invalid_prerelease
            )[1],
            "PLUGIN_VERSION_UNPARSEABLE",
        )

    def test_disabled_or_capability_expanding_plugin_replacement_is_denied(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        disabled = _curated_plugin_receipt("0.1.8", "C", enabled=False)
        expanded = _curated_plugin_receipt(
            "0.1.8", "D", extra_skill="gmail-inbox-triage"
        )

        self.assertEqual(
            recovery.classify_enabled_plugin_version_replacement(
                previous, disabled
            )[1],
            "PLUGIN_CONFIG_CHANGED",
        )
        self.assertEqual(
            recovery.classify_enabled_plugin_version_replacement(
                previous, expanded
            )[1],
            "PLUGIN_CAPABILITY_SURFACE_CHANGED",
        )

    def test_mixed_or_unbound_add_only_plugin_cache_delta_is_denied(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")
        current["plugin_cache_row_hashes"][
            "ROOT\topenai-curated-remote/github/1.0.0"
        ] = "7" * 64
        current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)
        add_only = copy.deepcopy(previous)
        added = _curated_plugin_receipt("0.1.8", "C")
        add_only["plugin_cache_row_hashes"].update(
            {
                key: value
                for key, value in added["plugin_cache_row_hashes"].items()
                if "/gmail/0.1.8" in key
            }
        )
        add_only["plugin_capability_surfaces"].update(
            added["plugin_capability_surfaces"]
        )
        add_only["plugin_cache_inventory_sha256"] = "D" * 64
        add_only["snapshot_sha256"] = recovery.authority_snapshot_digest(add_only)

        self.assertEqual(
            recovery.classify_enabled_plugin_version_replacement(
                previous, current
            )[1],
            "UNRELATED_PLUGIN_CACHE_DELTA",
        )
        self.assertEqual(
            recovery.classify_enabled_plugin_version_replacement(
                previous, add_only
            )[1],
            "PLUGIN_MANIFEST_IDENTITY_UNPROVEN",
        )

    def test_plugin_receipt_rejects_cache_rows_without_matching_surface(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")
        old_package = "openai-curated-remote/gmail/0.1.7"
        old_rows = {
            key: value
            for key, value in previous["plugin_cache_row_hashes"].items()
            if old_package in key
        }

        exact_mixed = copy.deepcopy(current)
        exact_mixed["plugin_cache_row_hashes"].update(old_rows)
        exact_mixed["snapshot_sha256"] = recovery.authority_snapshot_digest(
            exact_mixed
        )
        member_only = copy.deepcopy(current)
        member_key = next(
            key for key in old_rows if key.startswith("FILE\t")
        )
        member_only["plugin_cache_row_hashes"][member_key] = old_rows[member_key]
        member_only["snapshot_sha256"] = recovery.authority_snapshot_digest(
            member_only
        )

        for candidate in (exact_mixed, member_only):
            with self.subTest(rows=len(candidate["plugin_cache_row_hashes"])):
                allowed, reason = (
                    recovery.classify_enabled_plugin_version_replacement(
                        previous, candidate
                    )
                )
                self.assertFalse(allowed)
                self.assertEqual(reason, "INVALID_CURRENT_AUTHORITY_RECEIPT")


class StableReadTests(unittest.TestCase):
    def test_two_different_reads_fail_closed(self) -> None:
        first = _receipt("26.803.81509", "runtime-new", "cli-new", "B")
        second = dict(first)
        second["snapshot_sha256"] = "0" * 64
        reads = iter((first, second))

        stable, reason = recovery.stable_two_read(
            lambda: next(reads), settle_seconds=0, sleeper=lambda _: None
        )

        self.assertIsNone(stable)
        self.assertEqual(reason, "AUTHORITY_SNAPSHOT_UNSTABLE")

    def test_quiet_poll_eventually_admits(self) -> None:
        checks = iter((False, False, True))
        elapsed = [0.0]

        admitted = recovery.wait_for_quiet(
            lambda: next(checks),
            timeout_seconds=10,
            poll_seconds=2,
            clock=lambda: elapsed[0],
            sleeper=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        )

        self.assertTrue(admitted)
        self.assertEqual(elapsed[0], 4.0)

    def test_quiet_poll_timeout_denies(self) -> None:
        elapsed = [0.0]

        admitted = recovery.wait_for_quiet(
            lambda: False,
            timeout_seconds=5,
            poll_seconds=2,
            clock=lambda: elapsed[0],
            sleeper=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        )

        self.assertFalse(admitted)
        self.assertGreaterEqual(elapsed[0], 5.0)

    def test_quiet_check_includes_remote_catalog_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            config = codex_home / "config.toml"
            plugin_file = codex_home / "plugins" / "cache" / "plugin.json"
            catalog_file = (
                codex_home
                / recovery.REMOTE_PLUGIN_CATALOG_RELATIVE
                / "catalog.json"
            )
            for path in (config, plugin_file, catalog_file):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            now_ns = 1_800_000_000_000_000_000
            quiet_ns = now_ns - 20_000_000_000
            for path in (config, plugin_file):
                os.utime(path, ns=(quiet_ns, quiet_ns))
            os.utime(catalog_file, ns=(now_ns, now_ns))

            with mock.patch.object(recovery.time, "time_ns", return_value=now_ns):
                self.assertFalse(recovery.authority_is_quiet(codex_home))
                os.utime(catalog_file, ns=(quiet_ns, quiet_ns))
                self.assertTrue(recovery.authority_is_quiet(codex_home))


class RemoteCatalogBindingTests(unittest.TestCase):
    def test_schema1_origin_binds_fresh_catalog_entry_and_app_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            package, origins, _ = _write_remote_catalog_fixture(codex_home)

            bindings = recovery._remote_plugin_catalog_entries(
                codex_home,
                origins,
                now=dt.datetime(2026, 8, 14, 13, tzinfo=dt.timezone.utc),
            )

        self.assertEqual(set(bindings), {package})
        self.assertEqual(bindings[package]["plugin_version"], "1.1.0")
        self.assertEqual(bindings[package]["apps"], bindings[package]["local_apps"])

    def test_schema1_catalog_rejects_mismatched_id_version_or_app_surface(self) -> None:
        cases = (
            {"catalog_remote_plugin_id": "plugins~Plugin_other_12345678"},
            {"catalog_version": "1.2.0"},
            {"catalog_app_id": f"asdk_app_{'2' * 32}"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as temp_dir:
                codex_home = Path(temp_dir)
                _, origins, _ = _write_remote_catalog_fixture(
                    codex_home, **overrides
                )
                with self.assertRaises(RuntimeError):
                    recovery._remote_plugin_catalog_entries(
                        codex_home,
                        origins,
                        now=dt.datetime(
                            2026, 8, 14, 13, tzinfo=dt.timezone.utc
                        ),
                    )

    def test_catalog_fetched_at_is_audit_only_but_must_be_fresh(self) -> None:
        previous = _curated_plugin_receipt(
            "1.0.0", "B", receipt_schema_version=1
        )
        current = copy.deepcopy(previous)
        package = next(iter(current["remote_plugin_catalog_entries"]))
        current["remote_plugin_catalog_entries"][package][
            "fetched_at"
        ] = "2026-08-14T13:00:00Z"
        current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)
        self.assertEqual(previous["snapshot_sha256"], current["snapshot_sha256"])

        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            _, origins, _ = _write_remote_catalog_fixture(
                codex_home, fetched_at="2026-08-10T12:00:00Z"
            )
            with self.assertRaisesRegex(RuntimeError, "stale"):
                recovery._remote_plugin_catalog_entries(
                    codex_home,
                    origins,
                    now=dt.datetime(2026, 8, 14, 13, tzinfo=dt.timezone.utc),
                )

    def test_catalog_changed_between_reads_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            _, origins, catalog_path = _write_remote_catalog_fixture(codex_home)
            original = Path.read_bytes
            first = catalog_path.read_bytes()
            changed = first.replace(b'"status": "AVAILABLE"', b'"status": "UNAVAILABLE"')
            reads = iter((first, changed))

            def unstable(path: Path) -> bytes:
                if path == catalog_path:
                    return next(reads)
                return original(path)

            with mock.patch.object(Path, "read_bytes", unstable):
                with self.assertRaisesRegex(RuntimeError, "changed during capture"):
                    recovery._remote_plugin_catalog_entries(
                        codex_home,
                        origins,
                        now=dt.datetime(
                            2026, 8, 14, 13, tzinfo=dt.timezone.utc
                        ),
                    )


class RequiredSourceBindingTests(unittest.TestCase):
    def test_project_scope_map_is_bound_and_map_only_drift_changes_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            _write_required_sources(codex_home)
            map_path = codex_home / "capability-routing" / "project-scope-map.json"
            map_path.parent.mkdir(parents=True, exist_ok=True)
            map_path.write_text('{"schema_version":"1.0","projects":{}}\n', encoding="utf-8")
            before = recovery._required_source_hashes(codex_home)
            map_path.write_text('{"schema_version":"1.0","projects":{"generic":{}}}\n', encoding="utf-8")
            after = recovery._required_source_hashes(codex_home)

        self.assertIn("project-scope-map.json", before)
        self.assertNotEqual(
            before["project-scope-map.json"], after["project-scope-map.json"]
        )

    def test_routing_contract_schemas_are_bound_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            _write_required_sources(codex_home)
            routing_dir = codex_home / "capability-routing"
            routing_dir.mkdir(parents=True, exist_ok=True)
            for name in (
                "routing-policy.schema.json",
                "active-capabilities.schema.json",
                "project-scope-map.schema.json",
            ):
                (routing_dir / name).write_text("{}\n", encoding="utf-8")
            hashes = recovery._required_source_hashes(codex_home)

        self.assertTrue(
            {
                "routing-policy.schema.json",
                "active-capabilities.schema.json",
                "project-scope-map.schema.json",
            }.issubset(hashes)
        )

    def test_missing_session_entrypoint_or_hook_helpers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            _write_required_sources(codex_home)
            self.assertTrue(
                {
                    "hooks.json",
                    "AGENTS.md",
                    "task-routing-gate.md",
                    "_common.py",
                    "ensure-node-dependencies.ps1",
                }.isdisjoint(recovery._required_source_paths(codex_home))
            )
            for name in (
                "capability_index_session_start.py",
                "routing_policy_validation.py",
                "_hook_io.py",
            ):
                path = recovery._required_source_paths(codex_home)[name]
                path.unlink()
                with self.subTest(name=name):
                    with self.assertRaisesRegex(RuntimeError, name):
                        recovery._required_source_hashes(codex_home)
                path.write_text("{}\n", encoding="utf-8")


class PluginCapabilitySurfaceTests(unittest.TestCase):
    def test_cached_plugin_surface_derives_plugin_skill_app_and_mcp_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            package = (
                codex_home
                / "plugins"
                / "cache"
                / "openai-curated-remote"
                / "example"
                / "1.2.3"
            )
            (package / ".codex-plugin").mkdir(parents=True)
            (package / ".codex-plugin" / "plugin.json").write_text(
                '{"name":"example","version":"1.2.3"}\n', encoding="utf-8"
            )
            (package / "skills" / "folder-name").mkdir(parents=True)
            (package / "skills" / "folder-name" / "SKILL.md").write_text(
                "---\nname: analyze\n---\n", encoding="utf-8"
            )
            (package / "skills" / "presentation-folder").mkdir(parents=True)
            (package / "skills" / "presentation-folder" / "SKILL.md").write_text(
                "---\nname: Presentations\n---\n", encoding="utf-8"
            )
            (package / ".app.json").write_text(
                '{"apps":{"example-app":{"id":"connector"}}}\n', encoding="utf-8"
            )
            (package / ".mcp.json").write_text(
                '{"mcpServers":{"example-mcp":{"command":"node"}}}\n',
                encoding="utf-8",
            )

            surfaces = recovery._plugin_capability_surfaces(codex_home)

        self.assertEqual(
            surfaces["openai-curated-remote/example/1.2.3"],
            [
                {"id": "mcp:example-mcp", "kind": "mcp"},
                {"id": "plugin:example", "kind": "plugin"},
                {"id": "skill:example:Presentations", "kind": "skill"},
                {"id": "skill:example:analyze", "kind": "skill"},
                {"id": "tool-family:app:example-app", "kind": "tool-family"},
            ],
        )

    def test_stable_root_v1_receipt_authorizes_only_exact_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            plugin_root = (
                codex_home
                / "plugins"
                / "cache"
                / "openai-curated-remote"
                / "neon-postgres"
            )
            root_receipt = plugin_root / recovery.REMOTE_PLUGIN_INSTALL_RECEIPT
            root_receipt.parent.mkdir(parents=True)
            root_receipt.write_text(
                '{"schema_version":1,"remote_plugin_id":"plugins~Plugin_neon_12345678"}\n',
                encoding="utf-8",
            )
            packages: set[str] = set()
            for version, content in (("1.0.0", "trusted"), ("1.1.0", "changed")):
                package = plugin_root / version
                (package / ".codex-plugin").mkdir(parents=True)
                (package / ".codex-plugin" / "plugin.json").write_text(
                    json.dumps({"name": "neon-postgres", "version": version}) + "\n",
                    encoding="utf-8",
                )
                (package / "payload.txt").write_text(content, encoding="utf-8")
                packages.add(f"openai-curated-remote/neon-postgres/{version}")

            origins = recovery._plugin_package_origins(codex_home, packages)
            previous = _curated_plugin_receipt(
                "1.0.0", "B", plugin_name="neon-postgres"
            )
            current = _curated_plugin_receipt(
                "1.1.0", "C", plugin_name="neon-postgres"
            )
            old_package = "openai-curated-remote/neon-postgres/1.0.0"
            new_package = "openai-curated-remote/neon-postgres/1.1.0"
            previous["plugin_package_origins"] = {
                old_package: origins[old_package]
            }
            current["plugin_package_origins"] = {
                new_package: origins[new_package]
            }
            previous["plugin_package_manifests"][old_package][
                "manifest_sha256"
            ] = origins[old_package]["plugin_manifest_sha256"]
            current["plugin_package_manifests"][new_package][
                "manifest_sha256"
            ] = origins[new_package]["plugin_manifest_sha256"]
            for receipt, package in (
                (previous, old_package),
                (current, new_package),
            ):
                receipt["remote_plugin_catalog_entries"] = {
                    package: _synthetic_catalog_binding(
                        package,
                        remote_plugin_id=origins[package]["remote_plugin_id"],
                        apps={
                            "neon-postgres": f"asdk_app_{'1' * 32}"
                        },
                        skill_names=["neon-postgres"],
                    )
                }
            previous["snapshot_sha256"] = recovery.authority_snapshot_digest(previous)
            current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)
            allowed, reason = recovery.classify_enabled_plugin_version_replacement(
                previous, current
            )

        self.assertEqual(set(origins), {old_package, new_package})
        self.assertEqual(origins[old_package]["receipt_schema_version"], 1)
        self.assertEqual(origins[old_package]["attestation_mode"], "plugin-root-v1")
        self.assertEqual(
            origins[old_package]["install_receipt_sha256"],
            origins[new_package]["install_receipt_sha256"],
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "RECOGNIZED_ENABLED_PLUGIN_VERSION_REPLACEMENT")

    def test_version_specific_v2_managed_origin_binds_exact_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            package = (
                codex_home
                / "plugins"
                / "cache"
                / "openai-curated-remote"
                / "neon-postgres"
                / "1.0.0"
            )
            (package / ".codex-plugin").mkdir(parents=True)
            manifest_path = package / ".codex-plugin" / "plugin.json"
            manifest_path.write_text(
                '{"name":"neon-postgres","version":"1.0.0"}\n',
                encoding="utf-8",
            )
            (package / "payload.txt").write_text("package bytes", encoding="utf-8")
            install_receipt = _write_version_specific_install_receipt(package)
            install_receipt_sha256 = recovery._sha256_file(install_receipt)
            package_key = "openai-curated-remote/neon-postgres/1.0.0"

            manifests = recovery._plugin_package_manifests(codex_home, {package_key})
            origins = recovery._plugin_package_origins(codex_home, {package_key})
            manifest_sha256 = recovery._sha256_file(manifest_path)
            package_sha256 = recovery._plugin_package_sha256(
                package, package.parents[2]
            )

        self.assertEqual(manifests[package_key]["name"], "neon-postgres")
        self.assertEqual(manifests[package_key]["version"], "1.0.0")
        self.assertEqual(origins[package_key]["receipt_schema_version"], 2)
        self.assertEqual(
            origins[package_key]["attestation_mode"], "version-package-v2"
        )
        self.assertEqual(origins[package_key]["marketplace"], "openai-curated-remote")
        self.assertEqual(origins[package_key]["plugin_name"], "neon-postgres")
        self.assertEqual(origins[package_key]["plugin_version"], "1.0.0")
        self.assertEqual(origins[package_key]["remote_plugin_id"], "plugins~Plugin_neon_12345678")
        self.assertEqual(
            origins[package_key]["plugin_manifest_sha256"], manifest_sha256
        )
        self.assertEqual(origins[package_key]["package_sha256"], package_sha256)
        self.assertEqual(origins[package_key]["install_receipt_sha256"], install_receipt_sha256)

    def test_version_specific_receipt_mismatches_and_later_byte_changes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            package = (
                codex_home
                / "plugins"
                / "cache"
                / "openai-curated-remote"
                / "neon-postgres"
                / "1.0.0"
            )
            (package / ".codex-plugin").mkdir(parents=True)
            (package / ".codex-plugin" / "plugin.json").write_text(
                '{"name":"neon-postgres","version":"1.0.0"}\n',
                encoding="utf-8",
            )
            payload_path = package / "payload.txt"
            payload_path.write_text("trusted", encoding="utf-8")
            package_key = "openai-curated-remote/neon-postgres/1.0.0"
            mismatches: dict[str, object] = {
                "marketplace": "other-marketplace",
                "plugin_name": "other-plugin",
                "plugin_version": "9.9.9",
                "plugin_manifest_sha256": "0" * 64,
                "package_sha256": "1" * 64,
            }

            for field, value in mismatches.items():
                with self.subTest(field=field):
                    _write_version_specific_install_receipt(
                        package, overrides={field: value}
                    )
                    self.assertEqual(
                        recovery._plugin_package_origins(codex_home, {package_key}),
                        {},
                    )

            _write_version_specific_install_receipt(package)
            self.assertIn(
                package_key,
                recovery._plugin_package_origins(codex_home, {package_key}),
            )
            payload_path.write_text("mutated after install", encoding="utf-8")
            origins_after_mutation = recovery._plugin_package_origins(
                codex_home, {package_key}
            )

        self.assertEqual(origins_after_mutation, {})

    def test_remote_manifest_identity_must_match_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            package = (
                codex_home
                / "plugins"
                / "cache"
                / "openai-curated-remote"
                / "neon-postgres"
                / "1.0.0"
            )
            (package / ".codex-plugin").mkdir(parents=True)
            (package / ".codex-plugin" / "plugin.json").write_text(
                '{"name":"wrong-name","version":"1.0.0"}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "does not match"):
                recovery._plugin_package_manifests(
                    codex_home,
                    {"openai-curated-remote/neon-postgres/1.0.0"},
                )

    def test_malformed_capability_manifest_fails_surface_capture_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            package = (
                codex_home
                / "plugins"
                / "cache"
                / "openai-curated-remote"
                / "example"
                / "1.2.3"
            )
            (package / ".codex-plugin").mkdir(parents=True)
            (package / ".codex-plugin" / "plugin.json").write_text(
                '{"name":"example","version":"1.2.3"}\n', encoding="utf-8"
            )
            (package / ".app.json").write_text("[]\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "not an object"):
                recovery._plugin_capability_surfaces(codex_home)

    def test_tombstones_and_plugin_install_staging_are_not_authority_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir)
            cache_root = codex_home / "plugins" / "cache"
            empty_tombstone = (
                cache_root
                / "openai-curated-remote"
                / "codex-security"
                / "0.1.17"
            )
            empty_tombstone.mkdir(parents=True)
            active = empty_tombstone.parent / "0.1.18"
            (active / ".codex-plugin").mkdir(parents=True)
            (active / ".codex-plugin" / "plugin.json").write_text(
                '{"name":"codex-security","version":"0.1.18"}\n',
                encoding="utf-8",
            )
            staging = (
                cache_root
                / "openai-curated"
                / "plugin-install-IVNdUg"
                / "cloudflare"
            )
            (staging / ".codex-plugin").mkdir(parents=True)
            (staging / ".codex-plugin" / "plugin.json").write_text(
                '{"name":"cloudflare","version":"0.1.2"}\n', encoding="utf-8"
            )

            surfaces = recovery._plugin_capability_surfaces(codex_home)
            rows = (
                "ROOT\topenai-curated-remote/codex-security/0.1.17\t0\t1",
                "ROOT\topenai-curated-remote/codex-security/0.1.18\t0\t2",
                "FILE\topenai-curated-remote/codex-security/0.1.18/.codex-plugin/plugin.json\t1\t2",
                "ROOT\topenai-curated/plugin-install-ivndug/cloudflare\t0\t3",
            )
            filtered = recovery._filter_plugin_cache_inventory_rows(
                rows, set(surfaces)
            )

        self.assertEqual(
            set(surfaces),
            {"openai-curated-remote/codex-security/0.1.18"},
        )
        self.assertEqual(
            filtered,
            (
                "FILE\topenai-curated-remote/codex-security/0.1.18/.codex-plugin/plugin.json\t1\t2",
                "ROOT\topenai-curated-remote/codex-security/0.1.18\t0\t2",
            ),
        )


class RecoveryAdmissionTests(unittest.TestCase):
    def _run_app_recovery_with_patches(
        self,
        temp_dir: str,
        *,
        load_side_effect: object,
        terminal_writer: object | None = None,
        unlink_side_effect: object | None = None,
    ) -> tuple[dict, Path]:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt("26.810.41047", "runtime-new", "cli-new", "B")
        entries = [{"id": "plugin:browser", "kind": "plugin", "state": "active"}]
        manifest_path = Path(temp_dir) / "active-capabilities.json"
        manifest_path.write_text(
            json.dumps({"authority_receipt": previous, "entries": entries}),
            encoding="utf-8",
        )

        def builder(_: str, target: Path) -> Path:
            candidate = target.with_suffix(".candidate.json")
            candidate.write_text(
                json.dumps({"authority_receipt": current, "entries": entries}),
                encoding="utf-8",
            )
            return candidate

        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            mock.patch.object(
                recovery.index,
                "load_active_capabilities",
                side_effect=load_side_effect,
            )
        )
        stack.enter_context(
            mock.patch.object(
                recovery,
                "promote_manifest_generation",
                return_value={
                    "generation_id": "a" * 64,
                    "compatibility_copy_status": "current",
                },
            )
        )
        if terminal_writer is not None:
            stack.enter_context(
                mock.patch.object(
                    recovery,
                    "write_authority_transaction_terminal",
                    side_effect=terminal_writer,
                )
            )
        if unlink_side_effect is not None:
            stack.enter_context(
                mock.patch.object(Path, "unlink", side_effect=unlink_side_effect)
            )
        result = recovery.attempt_recovery(
            manifest_path=manifest_path,
            current_state={
                "freshness_status": "stale",
                "source_hashes_verified": False,
                "source_hash_mismatches": [
                    recovery.config_fingerprint.SOURCE_HASH_KEY,
                    "plugin-cache-inventory",
                ],
            },
            snapshot_reader=lambda: copy.deepcopy(current),
            builder_runner=builder,
            inventory_preparer=lambda _: None,
            mutex_factory=recovery.null_mutex,
            quiet_checker=lambda: True,
            settle_seconds=0,
            sleeper=lambda _: None,
        )
        stack.close()
        return result, manifest_path

    def test_installed_manifest_load_failure_after_pointer_stays_committed(self) -> None:
        fresh = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "entries": [
                {"id": "plugin:browser", "kind": "plugin", "state": "active"}
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            result, manifest_path = self._run_app_recovery_with_patches(
                temp_dir,
                load_side_effect=[fresh, RuntimeError("injected installed load failure")],
            )
            terminal_path = next(
                (manifest_path.parent / "authority-transactions").glob("*.terminal.json")
            )
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "rebuilt", result)
        self.assertEqual(result["reason_code"], "COMMITTED_POSTVALIDATION_EXCEPTION")
        self.assertEqual(terminal["status"], "committed")
        self.assertEqual(terminal["generation_id"], "a" * 64)

    def test_terminal_write_failure_after_pointer_reports_repair_pending(self) -> None:
        fresh = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "entries": [
                {"id": "plugin:browser", "kind": "plugin", "state": "active"}
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            result, _ = self._run_app_recovery_with_patches(
                temp_dir,
                load_side_effect=[fresh, fresh],
                terminal_writer=OSError("injected terminal write failure"),
            )

        self.assertEqual(result["status"], "rebuilt", result)
        self.assertEqual(
            result["reason_code"], "AUTHORITY_TRANSACTION_TERMINAL_REPAIR_PENDING"
        )
        self.assertTrue(result["repair_required"])
        self.assertEqual(result["generation_id"], "a" * 64)

    def test_persistent_candidate_unlink_failure_cannot_cross_commit_boundary(self) -> None:
        fresh = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "entries": [
                {"id": "plugin:browser", "kind": "plugin", "state": "active"}
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            result, manifest_path = self._run_app_recovery_with_patches(
                temp_dir,
                load_side_effect=[fresh, fresh],
                unlink_side_effect=OSError("persistent cleanup failure"),
            )
            terminal_path = next(
                (manifest_path.parent / "authority-transactions").glob("*.terminal.json")
            )
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "rebuilt", result)
        self.assertEqual(
            result["reason_code"], "RECOGNIZED_CODEX_DESKTOP_UPDATE"
        )
        self.assertEqual(terminal["status"], "committed")

    def test_worker_postvalidation_failure_after_pointer_stays_committed(self) -> None:
        previous, current = _worker_bom_receipts()
        entries = [{"id": "skill:baseline", "kind": "skill", "state": "active"}]
        fresh = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "entries": entries,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps({"authority_receipt": previous, "entries": entries}),
                encoding="utf-8",
            )

            def builder(_: str, target: Path) -> Path:
                candidate = target.with_suffix(".candidate.json")
                candidate.write_text(
                    json.dumps({"authority_receipt": current, "entries": entries}),
                    encoding="utf-8",
                )
                return candidate

            with (
                mock.patch.object(
                    recovery,
                    "_worker_bom_promotion_receipt",
                    return_value=({"receipt_sha256": "a" * 64}, "CURRENT"),
                ),
                mock.patch.object(
                    recovery.index,
                    "load_active_capabilities",
                    side_effect=[fresh, fresh],
                ),
                mock.patch.object(
                    recovery.index,
                    "worker_runtime_identity_status",
                    return_value={"bom_status": "unavailable", "components": {}},
                ),
                mock.patch.object(
                    recovery,
                    "promote_manifest_generation",
                    return_value={
                        "generation_id": "b" * 64,
                        "compatibility_copy_status": "current",
                    },
                ),
            ):
                result = recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    codex_home=Path(temp_dir),
                    current_state={
                        "freshness_status": "degraded",
                        "source_hashes_verified": False,
                        "source_hash_mismatches": [
                            recovery.index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
                        ],
                    },
                    snapshot_reader=lambda: copy.deepcopy(current),
                    builder_runner=builder,
                    inventory_preparer=lambda _: None,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )
            terminal_path = next(
                (manifest_path.parent / "authority-transactions").glob("*.terminal.json")
            )
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "rebuilt", result)
        self.assertEqual(
            result["reason_code"],
            "COMMITTED_WORKER_RUNTIME_POSTVALIDATION_DEGRADED",
        )
        self.assertEqual(terminal["status"], "committed")
        self.assertEqual(terminal["generation_id"], "b" * 64)

    def test_stale_catalog_inventory_refresh_precedes_first_full_capture(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt("26.810.41047", "runtime-new", "cli-new", "B")
        entries = [{"id": "plugin:browser", "kind": "plugin", "state": "active"}]
        events: list[str] = []
        refreshed = False

        def preparer(_: dict) -> None:
            nonlocal refreshed
            events.append("refresh")
            refreshed = True

        def reader() -> dict:
            events.append("read")
            if not refreshed:
                raise RuntimeError("remote plugin catalog is stale")
            return current

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps({"authority_receipt": previous, "entries": entries}),
                encoding="utf-8",
            )

            def builder(expected: str, target: Path) -> Path:
                candidate = target.with_suffix(".candidate.json")
                candidate.write_text(
                    json.dumps({"authority_receipt": current, "entries": entries}),
                    encoding="utf-8",
                )
                return candidate

            with (
                mock.patch.object(
                    recovery.index,
                    "load_active_capabilities",
                    return_value={
                        "freshness_status": "fresh",
                        "source_hashes_verified": True,
                        "entries": entries,
                    },
                ),
                mock.patch.object(
                    recovery,
                    "promote_manifest_generation",
                    return_value={"generation_id": "a" * 64},
                ),
            ):
                result = recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    current_state={
                        "freshness_status": "stale",
                        "source_hashes_verified": False,
                        "source_hash_mismatches": [
                            recovery.config_fingerprint.SOURCE_HASH_KEY,
                            "plugin-cache-inventory",
                        ],
                    },
                    snapshot_reader=reader,
                    builder_runner=builder,
                    inventory_preparer=preparer,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )

        self.assertEqual(events[0], "refresh")
        self.assertEqual(result["status"], "rebuilt")

    def test_receipt_bound_worker_bom_update_rebuilds_one_generation(self) -> None:
        previous, current = _worker_bom_receipts()
        entries = [{"id": "skill:baseline", "kind": "skill", "state": "active"}]
        inventory = mock.Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps({"authority_receipt": previous, "entries": entries}),
                encoding="utf-8",
            )

            def builder(expected: str, target: Path) -> Path:
                candidate = target.with_suffix(".candidate.json")
                candidate.write_text(
                    json.dumps({"authority_receipt": current, "entries": entries}),
                    encoding="utf-8",
                )
                return candidate

            with (
                mock.patch.object(
                    recovery,
                    "_worker_bom_promotion_receipt",
                    return_value=({"receipt_sha256": "a" * 64}, "CURRENT"),
                ) as verifier,
                mock.patch.object(
                    recovery.index,
                    "load_active_capabilities",
                    return_value={
                        "freshness_status": "fresh",
                        "source_hashes_verified": True,
                        "entries": entries,
                    },
                ),
                mock.patch.object(
                    recovery.index,
                    "worker_runtime_identity_status",
                    return_value={
                        "bom_status": "current",
                        "gateway": {
                            "identity_binding_status": "current",
                            "process_generation_status": "current",
                        },
                        "components": {
                            server_id: {"identity_binding_status": "current"}
                            for server_id in recovery.index.REQUIRED_WORKER_RUNTIME_SERVER_IDS
                        },
                    },
                ),
                mock.patch.object(
                    recovery,
                    "promote_manifest_generation",
                    return_value={"generation_id": "a" * 64},
                ),
            ):
                result = recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    codex_home=Path(temp_dir),
                    current_state={
                        "freshness_status": "degraded",
                        "source_hashes_verified": False,
                        "source_hash_mismatches": [
                            recovery.index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
                        ],
                    },
                    snapshot_reader=lambda: current,
                    builder_runner=builder,
                    inventory_preparer=inventory,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )

        self.assertEqual(result["status"], "rebuilt")
        self.assertEqual(
            result["reason_code"], "RECOGNIZED_WORKER_RUNTIME_BOM_UPDATE"
        )
        self.assertGreaterEqual(verifier.call_count, 2)
        inventory.assert_called_once_with(previous)

    def test_worker_bom_commit_reports_gateway_restart_required(self) -> None:
        previous, current = _worker_bom_receipts()
        entries = [{"id": "skill:baseline", "kind": "skill", "state": "active"}]
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps({"authority_receipt": previous, "entries": entries}),
                encoding="utf-8",
            )

            def builder(_: str, target: Path) -> Path:
                candidate = target.with_suffix(".candidate.json")
                candidate.write_text(
                    json.dumps({"authority_receipt": current, "entries": entries}),
                    encoding="utf-8",
                )
                return candidate

            worker_status = {
                "bom_status": "current",
                "gateway": {
                    "identity_binding_status": "current",
                    "process_generation_status": "restart_required",
                },
                "components": {
                    server_id: {"identity_binding_status": "current"}
                    for server_id in recovery.index.REQUIRED_WORKER_RUNTIME_SERVER_IDS
                },
            }
            with (
                mock.patch.object(
                    recovery,
                    "_worker_bom_promotion_receipt",
                    return_value=({"receipt_sha256": "a" * 64}, "CURRENT"),
                ),
                mock.patch.object(
                    recovery.index,
                    "load_active_capabilities",
                    return_value={
                        "freshness_status": "fresh",
                        "source_hashes_verified": True,
                        "entries": entries,
                    },
                ),
                mock.patch.object(
                    recovery.index,
                    "worker_runtime_identity_status",
                    return_value=worker_status,
                ),
                mock.patch.object(
                    recovery,
                    "promote_manifest_generation",
                    return_value={"generation_id": "a" * 64},
                ),
            ):
                result = recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    codex_home=Path(temp_dir),
                    current_state={
                        "freshness_status": "degraded",
                        "source_hashes_verified": False,
                        "source_hash_mismatches": [
                            recovery.index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
                        ],
                    },
                    snapshot_reader=lambda: current,
                    builder_runner=builder,
                    inventory_preparer=lambda _: None,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )

        self.assertEqual(result["status"], "rebuilt")
        self.assertEqual(
            result["reason_code"], "COMMITTED_WORKER_RUNTIME_RESTART_REQUIRED"
        )
        self.assertTrue(result["restart_required"])
        self.assertTrue(result["repair_required"])

    def test_worker_bom_missing_or_corrupt_receipt_never_calls_builder(self) -> None:
        previous, current = _worker_bom_receipts()
        for reason in (
            "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_MISSING",
            "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_CORRUPT",
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp_dir:
                manifest_path = Path(temp_dir) / "active-capabilities.json"
                manifest_path.write_text(
                    json.dumps({"authority_receipt": previous, "entries": [{}]}),
                    encoding="utf-8",
                )
                builder = mock.Mock()
                with mock.patch.object(
                    recovery,
                    "_worker_bom_promotion_receipt",
                    return_value=(None, reason),
                ):
                    result = recovery.attempt_recovery(
                        manifest_path=manifest_path,
                        codex_home=Path(temp_dir),
                        current_state={
                            "freshness_status": "degraded",
                            "source_hashes_verified": False,
                            "source_hash_mismatches": [
                                recovery.index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
                            ],
                        },
                        snapshot_reader=lambda: current,
                        builder_runner=builder,
                        inventory_preparer=lambda _: None,
                        mutex_factory=recovery.null_mutex,
                        quiet_checker=lambda: True,
                        settle_seconds=0,
                        sleeper=lambda _: None,
                    )
                self.assertEqual(result["reason_code"], reason)
                builder.assert_not_called()

    def test_worker_bom_builder_failure_does_not_promote(self) -> None:
        previous, current = _worker_bom_receipts()
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps({"authority_receipt": previous, "entries": [{}]}),
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    recovery,
                    "_worker_bom_promotion_receipt",
                    return_value=({"receipt_sha256": "a" * 64}, "CURRENT"),
                ),
                mock.patch.object(
                    recovery,
                    "promote_manifest_generation",
                ) as promote,
            ):
                result = recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    codex_home=Path(temp_dir),
                    current_state={
                        "freshness_status": "degraded",
                        "source_hashes_verified": False,
                        "source_hash_mismatches": [
                            recovery.index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
                        ],
                    },
                    snapshot_reader=lambda: current,
                    builder_runner=lambda *_: (_ for _ in ()).throw(
                        RuntimeError("builder failed")
                    ),
                    inventory_preparer=lambda _: None,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )
            self.assertEqual(result["reason_code"], "RECOVERY_BUILDER_FAILED")
            promote.assert_not_called()

    def test_worker_bom_promoter_receipt_binds_old_current_and_two_workers(self) -> None:
        previous, current = _worker_bom_receipts()
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            routing = home / "capability-routing"
            routing.mkdir()
            target = routing / "worker-runtime-bom.json"
            target.write_bytes(b'{"synthetic":true}\n')
            schema = routing / "worker-runtime-bom.schema.json"
            schema.write_bytes(b'{"schema":true}\n')
            promoter = routing / "promote_worker_runtime_bom.py"
            promoter.write_bytes(b"# exact synthetic promoter\n")
            (home / "config.toml").write_text(
                "[mcp_servers.codex-stability-gateway]\n"
                "url='http://127.0.0.1:8765/mcp'\n",
                encoding="utf-8",
            )
            old_sha = previous["required_source_hashes"][
                recovery.index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
            ].lower()
            current_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            current["required_source_hashes"][
                recovery.index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY
            ] = current_sha.upper()
            promoter_sha = hashlib.sha256(promoter.read_bytes()).hexdigest()
            for authority in (previous, current):
                authority["required_source_hashes"][
                    recovery.index.WORKER_RUNTIME_BOM_PROMOTER_SOURCE_HASH_KEY
                ] = promoter_sha.upper()
                authority["snapshot_sha256"] = recovery.authority_snapshot_digest(
                    authority
                )
            current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)
            projection = {"command": "fixture"}
            config_projection = {
                server_id: projection
                for server_id in sorted(
                    recovery.index.REQUIRED_WORKER_RUNTIME_SERVER_IDS
                )
            }
            config_projection[recovery.index.GATEWAY_CONFIG_SERVER_ID] = {
                "url": recovery.index.GATEWAY_CONFIG_URL
            }
            config_sha = recovery.index._worker_projection_sha256(config_projection)
            schema_sha = hashlib.sha256(schema.read_bytes()).hexdigest()
            transaction_id = "worker-bom-tx"
            transaction = (
                routing
                / recovery.WORKER_BOM_PROMOTER_STATE_DIRECTORY
                / "transactions"
                / transaction_id
            )
            transaction.mkdir(parents=True)
            journal = {
                "candidate_sha256": current_sha,
                "config_sha256": config_sha,
                "error": None,
                "expected_target_sha256": old_sha,
                "phase": "completed",
                "promoter_sha256": promoter_sha,
                "schema_sha256": schema_sha,
                "schema_version": recovery.WORKER_BOM_PROMOTION_JOURNAL_SCHEMA,
                "target": str(target.resolve()),
                "transaction_id": transaction_id,
                "updated_at": "2026-08-14T12:00:00Z",
            }
            journal_raw = recovery._canonical_json(journal).encode("utf-8") + b"\n"
            (transaction / "journal.json").write_bytes(journal_raw)
            receipt = {
                "candidate_sha256": current_sha,
                "completed_at": journal["updated_at"],
                "config_sha256": config_sha,
                "error": None,
                "expected_target_sha256": old_sha,
                "journal_sha256": hashlib.sha256(journal_raw).hexdigest(),
                "outcome": "completed",
                "promoter_sha256": promoter_sha,
                "schema_sha256": schema_sha,
                "schema_version": recovery.WORKER_BOM_PROMOTION_RECEIPT_SCHEMA,
                "target": str(target.resolve()),
                "transaction_id": transaction_id,
            }
            receipt["receipt_sha256"] = hashlib.sha256(
                recovery.WORKER_BOM_PROMOTION_RECEIPT_DOMAIN
                + recovery._canonical_json(receipt).encode("utf-8")
                + b"\n"
            ).hexdigest()
            receipt_path = transaction / "receipt.json"
            receipt_path.write_bytes(
                recovery._canonical_json(receipt).encode("utf-8") + b"\n"
            )
            exact_bom = {
                "runtimes": {
                    server_id: {}
                    for server_id in recovery.index.REQUIRED_WORKER_RUNTIME_SERVER_IDS
                }
            }
            with (
                mock.patch.object(
                    recovery.index,
                    "_load_worker_runtime_bom",
                    return_value=(exact_bom, current_sha),
                ),
                mock.patch.object(
                    recovery.index,
                    "_worker_server_projection",
                    return_value=(projection, home),
                ),
                mock.patch.object(
                    recovery.index,
                    "_gateway_managed_upstream_configured",
                    return_value=True,
                ),
            ):
                verified, reason = recovery._worker_bom_promotion_receipt(
                    home, previous, current
                )
                self.assertEqual(verified, receipt)
                self.assertEqual(
                    reason, "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_CURRENT"
                )
                promoter.write_bytes(b"# changed promoter bytes\n")
                self.assertEqual(
                    recovery._worker_bom_promotion_receipt(
                        home, previous, current
                    )[1],
                    "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_MISMATCH",
                )
                promoter.write_bytes(b"# exact synthetic promoter\n")
                omitted = {"runtimes": {"local-agent-stack": {}}}
                with mock.patch.object(
                    recovery.index,
                    "_load_worker_runtime_bom",
                    return_value=(omitted, current_sha),
                ):
                    self.assertEqual(
                        recovery._worker_bom_promotion_receipt(
                            home, previous, current
                        )[1],
                        "WORKER_RUNTIME_BOM_CURRENT_CLOSURE_INVALID",
                    )
                corrupt = dict(receipt)
                corrupt["receipt_sha256"] = "0" * 64
                receipt_path.write_bytes(
                    recovery._canonical_json(corrupt).encode("utf-8") + b"\n"
                )
                self.assertEqual(
                    recovery._worker_bom_promotion_receipt(
                        home, previous, current
                    )[1],
                    "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_CORRUPT",
                )

    def test_receipt_binds_exact_codex_cli_binary_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local_app_data = Path(temp_dir)
            cli_path = (
                local_app_data
                / "OpenAI"
                / "Codex"
                / "bin"
                / "cli-new"
                / "codex.exe"
            )
            cli_path.parent.mkdir(parents=True)
            cli_path.write_bytes(b"exact-codex-cli")
            receipt = _receipt("26.803.81509", "runtime-new", "cli-new", "B")
            receipt["app_identity"]["cli_executable_sha256"] = recovery._sha256_file(
                cli_path
            )
            receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)

            resolved = recovery.resolve_receipt_codex_exe(
                receipt, local_app_data=local_app_data
            )

            self.assertEqual(resolved, cli_path.resolve())
            receipt["app_identity"]["cli_executable_sha256"] = "0" * 64
            receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)
            with self.assertRaisesRegex(RuntimeError, "hash"):
                recovery.resolve_receipt_codex_exe(
                    receipt, local_app_data=local_app_data
                )

    def test_inventory_preflight_uses_receipt_cli_for_both_live_lists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local_app_data = Path(temp_dir)
            cli_path = (
                local_app_data
                / "OpenAI"
                / "Codex"
                / "bin"
                / "cli-new"
                / "codex.exe"
            )
            cli_path.parent.mkdir(parents=True)
            cli_path.write_bytes(b"exact-codex-cli")
            receipt = _receipt("26.803.81509", "runtime-new", "cli-new", "B")
            receipt["app_identity"]["cli_executable_sha256"] = recovery._sha256_file(
                cli_path
            )
            receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)
            calls: list[list[str]] = []

            recovery.prepare_live_inventories(
                receipt,
                local_app_data=local_app_data,
                command_runner=lambda command, timeout: calls.append(command) or "[]",
            )

            self.assertEqual(
                calls,
                [
                    [str(cli_path.resolve()), "mcp", "list", "--json"],
                    [str(cli_path.resolve()), "plugin", "list", "--json"],
                ],
            )

    def test_missing_baseline_never_calls_builder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps({"schema_version": "1.1", "entries": []}),
                encoding="utf-8",
            )
            builder = mock.Mock()

            result = recovery.attempt_recovery(
                manifest_path=manifest_path,
                current_state={
                    "freshness_status": "stale",
                    "source_hash_mismatches": [
                        recovery.config_fingerprint.SOURCE_HASH_KEY,
                        "plugin-cache-inventory",
                    ],
                },
                snapshot_reader=lambda: _receipt(
                    "26.803.81509", "runtime-new", "cli-new", "B"
                ),
                builder_runner=builder,
                mutex_factory=recovery.null_mutex,
                settle_seconds=0,
                sleeper=lambda _: None,
            )

        self.assertEqual(result["reason_code"], "AUTHORITY_BASELINE_MISSING")
        builder.assert_not_called()

    def test_mutex_contention_never_calls_builder(self) -> None:
        builder = mock.Mock()

        result = recovery.attempt_recovery(
            manifest_path=Path("unused.json"),
            current_state={"freshness_status": "stale"},
            snapshot_reader=mock.Mock(),
            builder_runner=builder,
            mutex_factory=lambda: recovery.StaticMutex(acquired=False),
        )

        self.assertEqual(result["reason_code"], "RECOVERY_MUTEX_BUSY")
        builder.assert_not_called()

    def test_updater_bound_plugin_replacement_enters_recovery_and_promotes(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")
        def builder(expected: str, target: Path) -> Path:
            self.assertEqual(expected, current["snapshot_sha256"])
            candidate = target.with_suffix(".candidate.json")
            candidate.write_text(
                json.dumps({"authority_receipt": current, "entries": [{}]}),
                encoding="utf-8",
            )
            return candidate
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps({"authority_receipt": previous, "entries": []}),
                encoding="utf-8",
            )
            fresh_state = {
                "freshness_status": "fresh",
                "source_hashes_verified": True,
                "entries": [{}],
            }
            with (
                mock.patch.object(
                    recovery.index, "load_active_capabilities", return_value=fresh_state
                ),
                mock.patch.object(
                    recovery,
                    "promote_manifest_generation",
                    return_value={"generation_id": "d" * 64},
                ),
            ):
                result = recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    current_state={
                        "freshness_status": "stale",
                        "source_hashes_verified": False,
                        "source_hash_mismatches": ["plugin-cache-inventory"],
                    },
                    snapshot_reader=lambda: current,
                    builder_runner=builder,
                    inventory_preparer=lambda _: None,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )

        self.assertEqual(result["status"], "rebuilt")
        self.assertEqual(
            result["reason_code"], "RECOGNIZED_ENABLED_PLUGIN_VERSION_REPLACEMENT"
        )

    def test_app_candidate_cannot_drop_an_unchanged_active_capability(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt("26.810.41047", "runtime-new", "cli-new", "B")
        baseline_entries = [
            {"id": "plugin:browser", "kind": "plugin", "state": "active"},
            {"id": "plugin:sites", "kind": "plugin", "state": "active"},
        ]
        candidate_entries = baseline_entries[:1]
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps(
                    {"authority_receipt": previous, "entries": baseline_entries}
                ),
                encoding="utf-8",
            )

            def builder(expected: str, target: Path) -> Path:
                candidate = target.with_suffix(".candidate.json")
                candidate.write_text(
                    json.dumps(
                        {
                            "authority_receipt": current,
                            "entries": candidate_entries,
                        }
                    ),
                    encoding="utf-8",
                )
                return candidate

            with mock.patch.object(
                recovery.index,
                "load_active_capabilities",
                return_value={
                    "freshness_status": "fresh",
                    "source_hashes_verified": True,
                    "entries": candidate_entries,
                },
            ):
                result = recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    current_state={
                        "freshness_status": "stale",
                        "source_hashes_verified": False,
                        "source_hash_mismatches": [
                            recovery.config_fingerprint.SOURCE_HASH_KEY,
                            "plugin-cache-inventory",
                        ],
                    },
                    snapshot_reader=lambda: current,
                    builder_runner=builder,
                    inventory_preparer=lambda _: None,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )

        self.assertEqual(result["status"], "denied")
        self.assertEqual(
            result["reason_code"],
            "RECOVERY_CANDIDATE_ACTIVE_SURFACE_MISMATCH",
        )

    def test_dated_inventory_families_cannot_ride_an_app_update(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt("26.810.41047", "runtime-new", "cli-new", "B")
        baseline_entries = [
            {
                "id": "plugin:browser",
                "kind": "plugin",
                "name": "browser",
                "state": "active-live",
                "provider": "openai-bundled",
                "version": "26.803.41515",
                "source_path": "old/plugin.json",
                "sha256": "a" * 64,
                "families": ["plugin", "browser"],
                "description": "Browser control",
            }
        ]
        candidate_entries = copy.deepcopy(baseline_entries)
        candidate_entries[0].update(
            {
                "version": "26.810.41047",
                "source_path": "new/plugin.json",
                "sha256": "b" * 64,
                "families": ["plugin", "unreviewed-routing-family"],
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps(
                    {"authority_receipt": previous, "entries": baseline_entries}
                ),
                encoding="utf-8",
            )

            def builder(expected: str, target: Path) -> Path:
                candidate = target.with_suffix(".candidate.json")
                candidate.write_text(
                    json.dumps(
                        {"authority_receipt": current, "entries": candidate_entries}
                    ),
                    encoding="utf-8",
                )
                return candidate

            with mock.patch.object(
                recovery.index,
                "load_active_capabilities",
                return_value={
                    "freshness_status": "fresh",
                    "source_hashes_verified": True,
                    "entries": candidate_entries,
                },
            ):
                result = recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    current_state={
                        "freshness_status": "stale",
                        "source_hashes_verified": False,
                        "source_hash_mismatches": [
                            recovery.config_fingerprint.SOURCE_HASH_KEY,
                            "plugin-cache-inventory",
                        ],
                    },
                    snapshot_reader=lambda: current,
                    builder_runner=builder,
                    inventory_preparer=lambda _: None,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )

        self.assertEqual(result["status"], "denied")
        self.assertEqual(
            result["reason_code"],
            "RECOVERY_CANDIDATE_SEMANTIC_SURFACE_MISMATCH",
        )

    def test_primary_runtime_and_compound_updates_use_explicit_atomic_lanes(
        self,
    ) -> None:
        isolated_previous = _primary_runtime_receipt("26.805.1", "B")
        isolated_current = _primary_runtime_receipt("26.812.1", "C")
        compound_previous = _compound_app_primary_runtime_receipt(
            "26.803.41515",
            "runtime-old",
            "cli-old",
            "A",
            "26.805.1",
            "B",
        )
        compound_current = _compound_app_primary_runtime_receipt(
            "26.810.41047",
            "runtime-new",
            "cli-new",
            "C",
            "26.812.1",
            "D",
        )
        cases = (
            (
                isolated_previous,
                isolated_current,
                ["plugin-cache-inventory"],
                "primary_runtime_bundle_update",
                "RECOGNIZED_PRIMARY_RUNTIME_BUNDLE_UPDATE",
            ),
            (
                compound_previous,
                compound_current,
                [
                    recovery.config_fingerprint.SOURCE_HASH_KEY,
                    "plugin-cache-inventory",
                ],
                "compound_app_primary_runtime_update",
                "RECOGNIZED_COMPOUND_APP_PRIMARY_RUNTIME_UPDATE",
            ),
        )
        entries = [
            {
                "id": f"plugin:{plugin_name}",
                "kind": "plugin",
                "state": "active",
            }
            for plugin_name in sorted(
                recovery.PRIMARY_RUNTIME_REQUIRED_PACKAGES
            )
        ]
        for previous, current, mismatches, lane, reason in cases:
            with self.subTest(lane=lane), tempfile.TemporaryDirectory() as temp_dir:
                manifest_path = Path(temp_dir) / "active-capabilities.json"
                manifest_path.write_text(
                    json.dumps(
                        {"authority_receipt": previous, "entries": entries}
                    ),
                    encoding="utf-8",
                )

                def builder(expected: str, target: Path) -> Path:
                    self.assertEqual(expected, current["snapshot_sha256"])
                    candidate = target.with_suffix(".candidate.json")
                    candidate.write_text(
                        json.dumps(
                            {"authority_receipt": current, "entries": entries}
                        ),
                        encoding="utf-8",
                    )
                    return candidate

                fresh_state = {
                    "freshness_status": "fresh",
                    "source_hashes_verified": True,
                    "entries": entries,
                }
                with (
                    mock.patch.object(
                        recovery.index,
                        "load_active_capabilities",
                        return_value=fresh_state,
                    ),
                    mock.patch.object(
                        recovery,
                        "promote_manifest_generation",
                        return_value={"generation_id": "e" * 64},
                    ),
                ):
                    result = recovery.attempt_recovery(
                        manifest_path=manifest_path,
                        current_state={
                            "freshness_status": "stale",
                            "source_hashes_verified": False,
                            "source_hash_mismatches": mismatches,
                        },
                        snapshot_reader=lambda: current,
                        builder_runner=builder,
                        inventory_preparer=lambda _: None,
                        mutex_factory=recovery.null_mutex,
                        quiet_checker=lambda: True,
                        settle_seconds=0,
                        sleeper=lambda _: None,
                    )

                begin_paths = list(
                    (manifest_path.parent / "authority-transactions").glob(
                        "*.begin.json"
                    )
                )
                self.assertEqual(result["status"], "rebuilt")
                self.assertEqual(result["reason_code"], reason)
                self.assertEqual(len(begin_paths), 1)
                begin = json.loads(begin_paths[0].read_text(encoding="utf-8"))
                self.assertEqual(begin["lane"], lane)

    def test_postbuild_authority_requires_another_stable_two_read(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt("26.803.81509", "runtime-new", "cli-new", "B")
        changed = copy.deepcopy(current)
        changed["plugin_cache_row_hashes"][
            "FILE\topenai-curated-remote/gmail/0.1.8/.app.json"
        ] = "D" * 64
        changed["snapshot_sha256"] = recovery.authority_snapshot_digest(changed)
        reads = iter((current, current, current, current, current, changed))
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps({"authority_receipt": previous, "entries": []}),
                encoding="utf-8",
            )

            def build_candidate(expected: str, target: Path) -> Path:
                self.assertEqual(expected, current["snapshot_sha256"])
                candidate = target.with_suffix(".candidate.json")
                candidate.write_text(
                    json.dumps({"authority_receipt": current, "entries": [{}]}),
                    encoding="utf-8",
                )
                return candidate

            fresh_state = {
                "freshness_status": "fresh",
                "source_hashes_verified": True,
                "source_hash_mismatches": [],
                "entries": [{}],
            }
            with mock.patch.object(
                recovery.index, "load_active_capabilities", return_value=fresh_state
            ):
                result = recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    current_state={
                        "freshness_status": "stale",
                        "source_hashes_verified": False,
                        "source_hash_mismatches": ["plugin-cache-inventory"],
                    },
                    snapshot_reader=lambda: next(reads),
                    builder_runner=build_candidate,
                    inventory_preparer=lambda _: None,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )

        self.assertEqual(result["status"], "denied")
        self.assertEqual(result["reason_code"], "AUTHORITY_SNAPSHOT_UNSTABLE")


class AuthorityGenerationTransactionTests(unittest.TestCase):
    @staticmethod
    def _candidate(
        path: Path,
        *,
        transaction_id: str = "tx-1",
        authority_snapshot_sha256: str = "1" * 64,
    ) -> bytes:
        generation_id = ""
        payload = {
            "schema_version": "1.3",
            "generated_at": "2026-08-14T00:00:00Z",
            "snapshot_id": f"authority-generation:{generation_id}",
            "freshness_status": "fresh",
            "authority_generation": {
                "id": "0" * 64,
                "sequence": 1,
                "previous_id": None,
                "transaction_id": transaction_id,
                "promoted_at": "2026-08-14T00:00:00Z",
                "promotion_reason": "operator_rebaseline",
                "static_authority_sha256": "b" * 64,
                "dynamic_authority_sha256": "c" * 64,
                "config_projection_sha256": "d" * 64,
                "plugin_inventory_sha256": "e" * 64,
                "worker_runtime_bom_sha256": "f" * 64,
                "authority_snapshot_sha256": authority_snapshot_sha256.lower(),
            },
            "source_hashes": {},
            "entries": [],
        }
        generation_id = recovery.index.authority_generation_id(
            payload["authority_generation"]
        )
        payload["authority_generation"]["id"] = generation_id
        payload["snapshot_id"] = f"authority-generation:{generation_id}"
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path.read_bytes()

    def _candidate_for_receipt(
        self,
        path: Path,
        *,
        transaction_id: str,
        receipt: dict,
    ) -> bytes:
        self._candidate(
            path,
            transaction_id=transaction_id,
            authority_snapshot_sha256=receipt["snapshot_sha256"],
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["authority_receipt"] = receipt
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path.read_bytes()

    def _render_operator_review(
        self,
        home: Path,
        *,
        receipt: dict,
        transaction_id: str,
    ) -> tuple[dict, mock.Mock]:
        routing_dir = home / "capability-routing"
        routing_dir.mkdir(parents=True, exist_ok=True)
        builder_output = routing_dir / f"{transaction_id}.builder-output.json"
        current_preflight = mock.Mock()

        def build(expected: str, _target: Path) -> Path:
            self.assertTrue(
                current_preflight.called,
                "current CLI inventory must be prepared before candidate build",
            )
            self.assertEqual(expected, receipt["snapshot_sha256"])
            self._candidate_for_receipt(
                builder_output,
                transaction_id=transaction_id,
                receipt=receipt,
            )
            return builder_output

        builder = mock.Mock(side_effect=build)
        fresh_state = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "entries": [{}],
        }
        with mock.patch.object(
            recovery.index, "load_active_capabilities", return_value=fresh_state
        ):
            result = recovery.render_operator_rebaseline_candidate(
                expected_authority_snapshot_sha256=receipt["snapshot_sha256"],
                transaction_id=transaction_id,
                codex_home=home,
                snapshot_reader=lambda: copy.deepcopy(receipt),
                builder_runner=builder,
                current_inventory_preparer=current_preflight,
                mutex_factory=recovery.null_mutex,
                quiet_checker=lambda: True,
                settle_seconds=0,
                sleeper=lambda _: None,
            )
        self.assertEqual(result["status"], "rendered", result)
        current_preflight.assert_called_once_with()
        return result, builder

    def test_generation_promotion_is_immutable_cas_and_replay_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routing_dir = Path(temp_dir) / "capability-routing"
            routing_dir.mkdir()
            target = routing_dir / "active-capabilities.json"
            target.write_bytes(b"legacy\n")
            candidate = routing_dir / "candidate.json"
            expected_bytes = self._candidate(candidate)

            pointer = recovery.promote_manifest_generation(
                candidate,
                target,
                transaction_id="tx-1",
                expected_previous_generation_id=None,
            )
            generation_path = routing_dir / pointer["manifest_path"]
            first_pointer_bytes = (routing_dir / "current-generation.json").read_bytes()
            replay = recovery.promote_manifest_generation(
                candidate,
                target,
                transaction_id="tx-1",
                expected_previous_generation_id=None,
            )

            self.assertEqual(pointer, replay)
            self.assertEqual(generation_path.read_bytes(), expected_bytes)
            self.assertEqual(target.read_bytes(), expected_bytes)
            self.assertEqual(
                (routing_dir / "current-generation.json").read_bytes(),
                first_pointer_bytes,
            )

            other = routing_dir / "other.json"
            other_payload = json.loads(expected_bytes)
            other_payload["authority_generation"]["id"] = "2" * 64
            other_payload["snapshot_id"] = "authority-generation:" + "2" * 64
            other_payload["authority_generation"]["sequence"] = 2
            other_payload["authority_generation"]["previous_id"] = "f" * 64
            other_id = recovery.index.authority_generation_id(
                other_payload["authority_generation"]
            )
            other_payload["authority_generation"]["id"] = other_id
            other_payload["snapshot_id"] = f"authority-generation:{other_id}"
            other.write_text(json.dumps(other_payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "compare-and-swap"):
                recovery.promote_manifest_generation(
                    other,
                    target,
                    transaction_id="tx-1",
                    expected_previous_generation_id="f" * 64,
                )
            self.assertEqual(target.read_bytes(), expected_bytes)

    def test_pointer_failure_rolls_back_compatibility_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routing_dir = Path(temp_dir) / "capability-routing"
            routing_dir.mkdir()
            target = routing_dir / "active-capabilities.json"
            target.write_bytes(b"previous\n")
            candidate = routing_dir / "candidate.json"
            self._candidate(candidate)
            with mock.patch.object(
                recovery, "_atomic_write_json", side_effect=OSError("injected")
            ):
                with self.assertRaisesRegex(OSError, "injected"):
                    recovery.promote_manifest_generation(
                        candidate,
                        target,
                        transaction_id="tx-1",
                        expected_previous_generation_id=None,
                    )
            self.assertEqual(target.read_bytes(), b"previous\n")
            self.assertFalse((routing_dir / "current-generation.json").exists())

    def test_pointer_commit_survives_compatibility_failure_and_replay_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routing_dir = Path(temp_dir) / "capability-routing"
            routing_dir.mkdir()
            target = routing_dir / "active-capabilities.json"
            target.write_bytes(b"previous\n")
            candidate = routing_dir / "candidate.json"
            expected = self._candidate(candidate, transaction_id="tx-repair")
            with mock.patch.object(
                recovery,
                "_repair_manifest_compatibility_copy",
                side_effect=OSError("injected repair failure"),
            ):
                committed = recovery.promote_manifest_generation(
                    candidate,
                    target,
                    transaction_id="tx-repair",
                    expected_previous_generation_id=None,
                )
            self.assertEqual(
                committed["compatibility_copy_status"], "repair_pending"
            )
            self.assertEqual(target.read_bytes(), b"previous\n")
            self.assertTrue((routing_dir / "current-generation.json").is_file())

            replay = recovery.promote_manifest_generation(
                candidate,
                target,
                transaction_id="tx-repair",
                expected_previous_generation_id=None,
            )
            self.assertEqual(replay["compatibility_copy_status"], "current")
            self.assertEqual(target.read_bytes(), expected)

    def test_transaction_receipts_are_idempotent_and_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routing_dir = Path(temp_dir) / "capability-routing"
            first = recovery.write_authority_transaction_begin(
                routing_dir,
                transaction_id="stable-request",
                lane="operator_rebaseline",
                previous_generation_id=None,
                expected_authority_snapshot_sha256="a" * 64,
                expected_candidate_sha256="c" * 64,
                authorization_id="authorization",
            )
            first_bytes = first.read_bytes()
            replay = recovery.write_authority_transaction_begin(
                routing_dir,
                transaction_id="stable-request",
                lane="operator_rebaseline",
                previous_generation_id=None,
                expected_authority_snapshot_sha256="a" * 64,
                expected_candidate_sha256="c" * 64,
                authorization_id="authorization",
            )
            self.assertEqual(replay.read_bytes(), first_bytes)
            with self.assertRaisesRegex(RuntimeError, "conflicts"):
                recovery.write_authority_transaction_begin(
                    routing_dir,
                    transaction_id="stable-request",
                    lane="operator_rebaseline",
                    previous_generation_id=None,
                    expected_authority_snapshot_sha256="b" * 64,
                    expected_candidate_sha256="c" * 64,
                    authorization_id="authorization",
                )
            with self.assertRaisesRegex(RuntimeError, "conflicts"):
                recovery.write_authority_transaction_begin(
                    routing_dir,
                    transaction_id="stable-request",
                    lane="operator_rebaseline",
                    previous_generation_id=None,
                    expected_authority_snapshot_sha256="a" * 64,
                    expected_candidate_sha256="d" * 64,
                    authorization_id="authorization",
                )

    def test_replay_reconciles_erroneous_terminal_to_committed_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            routing_dir = Path(temp_dir) / "capability-routing"
            routing_dir.mkdir()
            target = routing_dir / "active-capabilities.json"
            receipt = _receipt("26.810.41047", "runtime", "cli", "A")
            candidate = routing_dir / "reviewed-candidate.json"
            candidate_bytes = self._candidate_for_receipt(
                candidate,
                transaction_id="tx-reconcile",
                receipt=receipt,
            )
            candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
            recovery.write_authority_transaction_begin(
                routing_dir,
                transaction_id="tx-reconcile",
                lane="operator_rebaseline",
                previous_generation_id=None,
                expected_authority_snapshot_sha256=receipt["snapshot_sha256"],
                expected_candidate_sha256=candidate_sha256,
                authorization_id="authorization",
            )
            pointer = recovery.promote_manifest_generation(
                candidate,
                target,
                transaction_id="tx-reconcile",
                expected_previous_generation_id=None,
                expected_candidate_sha256=candidate_sha256,
            )
            recovery.write_authority_transaction_terminal(
                routing_dir,
                transaction_id="tx-reconcile",
                lane="operator_rebaseline",
                status="error",
                reason_code="INJECTED_POSTCOMMIT_ERROR",
                generation_id=pointer["generation_id"],
                expected_authority_snapshot_sha256=receipt["snapshot_sha256"],
                expected_candidate_sha256=candidate_sha256,
            )
            with mock.patch.object(
                recovery.index,
                "load_active_capabilities",
                return_value={
                    "freshness_status": "fresh",
                    "source_hashes_verified": True,
                    "generation_pointer_status": "current",
                    "entries": [{}],
                },
            ):
                replay = recovery.replay_operator_transaction(
                    routing_dir,
                    transaction_id="tx-reconcile",
                    authorization_id="authorization",
                    expected_authority_snapshot_sha256=receipt["snapshot_sha256"],
                    expected_candidate_sha256=candidate_sha256,
                )

            self.assertEqual(replay["status"], "rebuilt", replay)
            self.assertEqual(
                replay["reason_code"], "OPERATOR_REBASELINE_COMMIT_RECONCILED"
            )
            self.assertEqual(replay["recorded_terminal_status"], "error")
            self.assertEqual(replay["generation_id"], pointer["generation_id"])

    def test_operator_render_then_apply_promotes_exact_reviewed_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            receipt = _receipt("26.810.41047", "runtime", "cli", "A")
            rendered, render_builder = self._render_operator_review(
                home, receipt=receipt, transaction_id="operator-two-phase"
            )
            candidate = Path(rendered["reviewed_candidate_path"])
            reviewed_bytes = candidate.read_bytes()
            routing_dir = home / "capability-routing"
            self.assertFalse((routing_dir / "authority-transactions").exists())

            fresh_state = {
                "freshness_status": "fresh",
                "source_hashes_verified": True,
                "entries": [{}],
            }
            apply_preflight = mock.Mock()

            def read_current() -> dict:
                self.assertTrue(
                    apply_preflight.called,
                    "current CLI inventory must be prepared before apply capture",
                )
                return copy.deepcopy(receipt)

            with (
                mock.patch.object(
                    recovery.index,
                    "load_active_capabilities",
                    return_value=fresh_state,
                ),
                mock.patch.object(recovery, "run_builder_candidate") as apply_builder,
            ):
                result = recovery.operator_rebaseline(
                    authorization_id="review-authorization",
                    expected_authority_snapshot_sha256=receipt["snapshot_sha256"],
                    expected_candidate_sha256=rendered["candidate_sha256"],
                    reviewed_candidate_path=candidate,
                    transaction_id="operator-two-phase",
                    codex_home=home,
                    snapshot_reader=read_current,
                    current_inventory_preparer=apply_preflight,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )

            self.assertEqual(result["status"], "rebuilt")
            apply_preflight.assert_called_once_with()
            self.assertEqual(result["candidate_sha256"], rendered["candidate_sha256"])
            self.assertEqual(candidate.read_bytes(), reviewed_bytes)
            self.assertEqual(
                (routing_dir / "active-capabilities.json").read_bytes(), reviewed_bytes
            )
            render_builder.assert_called_once()
            apply_builder.assert_not_called()
            begin = json.loads(
                (
                    routing_dir
                    / "authority-transactions/operator-two-phase.begin.json"
                ).read_text(encoding="utf-8")
            )
            terminal = json.loads(
                (
                    routing_dir
                    / "authority-transactions/operator-two-phase.terminal.json"
                ).read_text(encoding="utf-8")
            )
            for artifact in (begin, terminal):
                self.assertEqual(
                    artifact["expected_authority_snapshot_sha256"],
                    receipt["snapshot_sha256"].lower(),
                )
                self.assertEqual(
                    artifact["expected_candidate_sha256"],
                    rendered["candidate_sha256"],
                )
            self.assertEqual(
                terminal["observed_candidate_sha256"], rendered["candidate_sha256"]
            )

    def test_operator_pointer_commit_stays_committed_when_compatibility_repair_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            receipt = _receipt("26.810.41047", "runtime", "cli", "A")
            rendered, _ = self._render_operator_review(
                home, receipt=receipt, transaction_id="operator-repair-pending"
            )
            candidate = Path(rendered["reviewed_candidate_path"])
            routing_dir = home / "capability-routing"
            fresh_state = {
                "freshness_status": "fresh",
                "source_hashes_verified": True,
                "generation_pointer_status": "current",
                "entries": [{}],
            }
            with (
                mock.patch.object(
                    recovery.index,
                    "load_active_capabilities",
                    return_value=fresh_state,
                ),
                mock.patch.object(
                    recovery,
                    "_repair_manifest_compatibility_copy",
                    side_effect=OSError("injected repair failure"),
                ),
            ):
                result = recovery.operator_rebaseline(
                    authorization_id="review-authorization",
                    expected_authority_snapshot_sha256=receipt["snapshot_sha256"],
                    expected_candidate_sha256=rendered["candidate_sha256"],
                    reviewed_candidate_path=candidate,
                    transaction_id="operator-repair-pending",
                    codex_home=home,
                    snapshot_reader=lambda: copy.deepcopy(receipt),
                    current_inventory_preparer=lambda: None,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )
            self.assertEqual(result["status"], "rebuilt", result)
            self.assertEqual(result["compatibility_copy_status"], "repair_pending")
            terminal_path = (
                routing_dir
                / "authority-transactions/operator-repair-pending.terminal.json"
            )
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
            self.assertEqual(terminal["status"], "committed")
            self.assertFalse((routing_dir / "active-capabilities.json").exists())

            with mock.patch.object(
                recovery.index,
                "load_active_capabilities",
                return_value=fresh_state,
            ):
                replay = recovery.operator_rebaseline(
                    authorization_id="review-authorization",
                    expected_authority_snapshot_sha256=receipt["snapshot_sha256"],
                    expected_candidate_sha256=rendered["candidate_sha256"],
                    reviewed_candidate_path=candidate,
                    transaction_id="operator-repair-pending",
                    codex_home=home,
                    mutex_factory=recovery.null_mutex,
                )
            self.assertEqual(replay["status"], "rebuilt", replay)
            self.assertTrue(replay["replayed"])
            self.assertEqual(
                (routing_dir / "active-capabilities.json").read_bytes(),
                candidate.read_bytes(),
            )

    def test_operator_apply_rejects_unrelated_authority_delta_before_begin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            reviewed = _receipt("26.810.41047", "runtime", "cli", "A")
            rendered, _ = self._render_operator_review(
                home, receipt=reviewed, transaction_id="operator-unrelated-delta"
            )
            changed = copy.deepcopy(reviewed)
            changed["required_source_hashes"]["capability_index.py"] = "9" * 64
            changed["snapshot_sha256"] = recovery.authority_snapshot_digest(changed)

            result = recovery.operator_rebaseline(
                authorization_id="review-authorization",
                expected_authority_snapshot_sha256=reviewed["snapshot_sha256"],
                expected_candidate_sha256=rendered["candidate_sha256"],
                reviewed_candidate_path=Path(rendered["reviewed_candidate_path"]),
                transaction_id="operator-unrelated-delta",
                codex_home=home,
                snapshot_reader=lambda: copy.deepcopy(changed),
                current_inventory_preparer=lambda: None,
                mutex_factory=recovery.null_mutex,
                quiet_checker=lambda: True,
                settle_seconds=0,
                sleeper=lambda _: None,
            )

            self.assertEqual(result["status"], "denied")
            self.assertEqual(
                result["reason_code"], "OPERATOR_AUTHORITY_SNAPSHOT_CAS_MISMATCH"
            )
            self.assertFalse(
                (
                    home
                    / "capability-routing/authority-transactions/"
                    "operator-unrelated-delta.begin.json"
                ).exists()
            )

    def test_delayed_review_ignores_only_catalog_fetch_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            reviewed = _curated_plugin_receipt(
                "0.1.8", "C", receipt_schema_version=1
            )
            rendered, _ = self._render_operator_review(
                home, receipt=reviewed, transaction_id="operator-delayed-review"
            )
            current = copy.deepcopy(reviewed)
            package = next(iter(current["remote_plugin_catalog_entries"]))
            current["remote_plugin_catalog_entries"][package]["fetched_at"] = (
                "2026-08-16T12:00:00Z"
            )
            current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)
            self.assertEqual(
                current["snapshot_sha256"], reviewed["snapshot_sha256"]
            )
            self.assertTrue(
                recovery._operator_authority_receipts_equivalent(reviewed, current)
            )

            content_changed = copy.deepcopy(current)
            binding = content_changed["remote_plugin_catalog_entries"][package]
            binding["status"] = "UNAVAILABLE"
            binding["entry_projection_sha256"] = recovery._sha256_text(
                recovery._canonical_json(
                    recovery._catalog_entry_projection(binding)
                )
            )
            content_changed["snapshot_sha256"] = recovery.authority_snapshot_digest(
                content_changed
            )
            self.assertFalse(
                recovery._operator_authority_receipts_equivalent(
                    reviewed, content_changed
                )
            )

            fresh_state = {
                "freshness_status": "fresh",
                "source_hashes_verified": True,
                "entries": [{}],
            }
            with mock.patch.object(
                recovery.index,
                "load_active_capabilities",
                return_value=fresh_state,
            ):
                result = recovery.operator_rebaseline(
                    authorization_id="review-authorization",
                    expected_authority_snapshot_sha256=reviewed["snapshot_sha256"],
                    expected_candidate_sha256=rendered["candidate_sha256"],
                    reviewed_candidate_path=Path(
                        rendered["reviewed_candidate_path"]
                    ),
                    transaction_id="operator-delayed-review",
                    codex_home=home,
                    snapshot_reader=lambda: copy.deepcopy(current),
                    current_inventory_preparer=lambda: None,
                    mutex_factory=recovery.null_mutex,
                    quiet_checker=lambda: True,
                    settle_seconds=0,
                    sleeper=lambda _: None,
                )

            self.assertEqual(result["status"], "rebuilt", result)

    def test_operator_apply_rejects_reviewed_candidate_digest_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            receipt = _receipt("26.810.41047", "runtime", "cli", "A")
            rendered, _ = self._render_operator_review(
                home, receipt=receipt, transaction_id="operator-candidate-cas"
            )
            candidate = Path(rendered["reviewed_candidate_path"])
            candidate.write_bytes(candidate.read_bytes() + b" ")

            result = recovery.operator_rebaseline(
                authorization_id="review-authorization",
                expected_authority_snapshot_sha256=receipt["snapshot_sha256"],
                expected_candidate_sha256=rendered["candidate_sha256"],
                reviewed_candidate_path=candidate,
                transaction_id="operator-candidate-cas",
                codex_home=home,
                snapshot_reader=lambda: copy.deepcopy(receipt),
                current_inventory_preparer=lambda: None,
                mutex_factory=recovery.null_mutex,
                quiet_checker=lambda: True,
                settle_seconds=0,
                sleeper=lambda _: None,
            )

            self.assertEqual(result["status"], "denied")
            self.assertEqual(
                result["reason_code"], "OPERATOR_CANDIDATE_SHA256_CAS_MISMATCH"
            )
            self.assertFalse(
                (
                    home
                    / "capability-routing/authority-transactions/"
                    "operator-candidate-cas.begin.json"
                ).exists()
            )

    def test_operator_rebaseline_replays_committed_terminal_without_builder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            routing_dir = home / "capability-routing"
            target = routing_dir / "active-capabilities.json"
            candidate = routing_dir / "candidate.json"
            routing_dir.mkdir(parents=True, exist_ok=True)
            self._candidate(candidate, transaction_id="operator-replay")
            candidate_payload = json.loads(candidate.read_text(encoding="utf-8"))
            candidate_payload["authority_receipt"] = {
                "snapshot_sha256": "a" * 64
            }
            candidate.write_text(
                json.dumps(candidate_payload) + "\n", encoding="utf-8"
            )
            candidate_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
            recovery.write_authority_transaction_begin(
                routing_dir,
                transaction_id="operator-replay",
                lane="operator_rebaseline",
                previous_generation_id=None,
                expected_authority_snapshot_sha256="a" * 64,
                expected_candidate_sha256=candidate_sha256,
                authorization_id="authorization",
            )
            pointer = recovery.promote_manifest_generation(
                candidate,
                target,
                transaction_id="operator-replay",
                expected_previous_generation_id=None,
                expected_candidate_sha256=candidate_sha256,
            )
            recovery.write_authority_transaction_terminal(
                routing_dir,
                transaction_id="operator-replay",
                lane="operator_rebaseline",
                status="committed",
                reason_code="OPERATOR_REBASELINE_COMMITTED",
                generation_id=pointer["generation_id"],
                expected_authority_snapshot_sha256="a" * 64,
                expected_candidate_sha256=candidate_sha256,
                observed_authority_snapshot_sha256="a" * 64,
                observed_candidate_sha256=candidate_sha256,
            )
            with (
                mock.patch.object(
                    recovery.index,
                    "load_active_capabilities",
                    return_value={
                        "freshness_status": "fresh",
                        "source_hashes_verified": True,
                        "generation_pointer_status": "legacy",
                    },
                ),
                mock.patch.object(recovery, "run_builder_candidate") as builder,
            ):
                result = recovery.operator_rebaseline(
                    authorization_id="authorization",
                    expected_authority_snapshot_sha256="a" * 64,
                    expected_candidate_sha256=candidate_sha256,
                    reviewed_candidate_path=candidate,
                    transaction_id="operator-replay",
                    codex_home=home,
                    mutex_factory=recovery.null_mutex,
                )
            self.assertEqual(result["status"], "rebuilt")
            self.assertTrue(result["replayed"])
            self.assertEqual(result["generation_id"], pointer["generation_id"])
            builder.assert_not_called()


class OperatorRebaselineCliTests(unittest.TestCase):
    def test_snapshot_cli_refreshes_stale_catalog_before_first_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            receipt = _receipt("26.810.41047", "runtime", "cli", "A")
            catalog_stale = True
            events: list[str] = []

            def prepare(codex_home: Path, *, timeout_seconds: float) -> None:
                nonlocal catalog_stale
                self.assertEqual(codex_home, home)
                self.assertEqual(timeout_seconds, 20.0)
                events.append("prepare-current-cli")
                catalog_stale = False

            def capture(codex_home: Path) -> dict:
                self.assertEqual(codex_home, home)
                self.assertFalse(catalog_stale, "stale catalog reached full capture")
                events.append("capture-authority")
                return receipt

            with (
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "capability_manifest_recovery.py",
                        "--snapshot",
                        "--codex-home",
                        str(home),
                    ],
                ),
                mock.patch.object(
                    recovery, "prepare_current_live_inventories", side_effect=prepare
                ),
                mock.patch.object(
                    recovery, "capture_authority_receipt", side_effect=capture
                ),
                mock.patch("builtins.print"),
            ):
                exit_code = recovery.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(events, ["prepare-current-cli", "capture-authority"])

    def test_cli_dispatches_candidate_render_without_apply_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            rendered = {
                "status": "rendered",
                "candidate_sha256": "b" * 64,
            }
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "capability_manifest_recovery.py",
                        "--render-operator-rebaseline-candidate",
                        "--transaction-id",
                        "review-cli",
                        "--expected-authority-snapshot-sha256",
                        "a" * 64,
                        "--codex-home",
                        str(home),
                    ],
                ),
                mock.patch.object(
                    recovery,
                    "render_operator_rebaseline_candidate",
                    return_value=rendered,
                ) as render,
                mock.patch("builtins.print"),
            ):
                exit_code = recovery.main()

        self.assertEqual(exit_code, 0)
        render.assert_called_once_with(
            expected_authority_snapshot_sha256="a" * 64,
            transaction_id="review-cli",
            codex_home=home,
        )

    def test_cli_dispatches_apply_with_exact_reviewed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            candidate = home / "candidate.json"
            candidate.write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "capability_manifest_recovery.py",
                        "--operator-rebaseline",
                        "--authorization-id",
                        "authorization",
                        "--transaction-id",
                        "apply-cli",
                        "--expected-authority-snapshot-sha256",
                        "a" * 64,
                        "--expected-candidate-sha256",
                        "b" * 64,
                        "--reviewed-candidate",
                        str(candidate),
                        "--codex-home",
                        str(home),
                    ],
                ),
                mock.patch.object(
                    recovery,
                    "operator_rebaseline",
                    return_value={"status": "rebuilt"},
                ) as apply,
                mock.patch("builtins.print"),
            ):
                exit_code = recovery.main()

        self.assertEqual(exit_code, 0)
        apply.assert_called_once_with(
            authorization_id="authorization",
            expected_authority_snapshot_sha256="a" * 64,
            expected_candidate_sha256="b" * 64,
            reviewed_candidate_path=candidate,
            transaction_id="apply-cli",
            codex_home=home,
        )


class SessionStartReceiptTests(unittest.TestCase):
    def test_receipt_write_is_atomic_bounded_and_rotated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            receipt_dir = Path(temp_dir)
            unrelated = receipt_dir / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            paths = []
            for position in range(5):
                paths.append(
                    recovery.write_session_start_recovery_receipt(
                        {
                            "status": "noop",
                            "reason_code": "MANIFEST_ALREADY_FRESH",
                            "unbounded": "x" * 100_000,
                        },
                        before_state={
                            "freshness_status": "fresh",
                            "source_hashes_verified": True,
                            "source_hash_mismatches": [],
                        },
                        after_state={
                            "freshness_status": "fresh",
                            "source_hashes_verified": True,
                            "source_hash_mismatches": [],
                        },
                        before_manifest_sha256="A" * 64,
                        after_manifest_sha256="A" * 64,
                        receipt_dir=receipt_dir,
                        max_receipts=3,
                        event_id=f"event-{position}",
                        recorded_at=dt.datetime(
                            2026, 8, 12, 8, 0, position, tzinfo=dt.timezone.utc
                        ),
                    )
                )

            receipts = sorted(receipt_dir.glob("session-start-recovery-*.json"))
            payload = json.loads(paths[-1].read_text(encoding="utf-8"))
            unrelated_preserved = unrelated.read_text(encoding="utf-8") == "keep"
            temporary_files = [path for path in receipt_dir.glob("*") if ".tmp" in path.name]

        self.assertEqual(len(receipts), 3)
        self.assertTrue(unrelated_preserved)
        self.assertFalse(temporary_files)
        self.assertNotIn("unbounded", payload)
        self.assertLess(len(json.dumps(payload)), 4096)
        self.assertEqual(payload["reason_code"], "MANIFEST_ALREADY_FRESH")

    def test_session_start_reloads_state_inside_mutex_after_prelock_change(self) -> None:
        before = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "source_hash_mismatches": [],
        }
        changed_under_mutex = {
            "freshness_status": "stale",
            "source_hashes_verified": False,
            "source_hash_mismatches": ["unexpected-authority-change"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text("{}\n", encoding="utf-8")
            receipt_dir = Path(temp_dir) / "receipts"

            def attempt_with_locked_reload(**kwargs):
                return recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    mutex_factory=recovery.null_mutex,
                    **kwargs,
                )

            with (
                mock.patch.object(session_start, "ACTIVE_CAPABILITIES_PATH", manifest_path),
                mock.patch.object(
                    session_start, "load_active_capabilities", return_value=before
                ),
                mock.patch.object(
                    recovery.index,
                    "load_active_capabilities",
                    return_value=changed_under_mutex,
                ) as locked_load,
                mock.patch.object(
                    session_start,
                    "attempt_recovery",
                    side_effect=attempt_with_locked_reload,
                ) as recovery_attempt,
                mock.patch.object(
                    session_start, "ensure_index", return_value=changed_under_mutex
                ),
                mock.patch.object(session_start, "load_routing_policy", return_value={}),
            ):
                receipt_path = session_start.run_session_start(receipt_dir=receipt_dir)
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))

        recovery_attempt.assert_called_once_with()
        locked_load.assert_called_once_with(manifest_path)
        self.assertEqual(payload["status"], "denied")
        self.assertEqual(payload["reason_code"], "UNRECOVERABLE_SOURCE_DRIFT")
        self.assertNotEqual(payload["reason_code"], "MANIFEST_ALREADY_FRESH")

    def test_session_start_preserves_no_change_fresh_noop(self) -> None:
        fresh = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "source_hash_mismatches": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text("{}\n", encoding="utf-8")
            receipt_dir = Path(temp_dir) / "receipts"

            def attempt_with_locked_reload(**kwargs):
                return recovery.attempt_recovery(
                    manifest_path=manifest_path,
                    mutex_factory=recovery.null_mutex,
                    **kwargs,
                )

            with (
                mock.patch.object(session_start, "ACTIVE_CAPABILITIES_PATH", manifest_path),
                mock.patch.object(
                    session_start, "load_active_capabilities", return_value=fresh
                ),
                mock.patch.object(
                    recovery.index, "load_active_capabilities", return_value=fresh
                ) as locked_load,
                mock.patch.object(
                    session_start,
                    "attempt_recovery",
                    side_effect=attempt_with_locked_reload,
                ) as recovery_attempt,
                mock.patch.object(session_start, "ensure_index", return_value=fresh),
                mock.patch.object(session_start, "load_routing_policy", return_value={}),
            ):
                receipt_path = session_start.run_session_start(receipt_dir=receipt_dir)
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))

        recovery_attempt.assert_called_once_with()
        locked_load.assert_called_once_with(manifest_path)
        self.assertEqual(payload["status"], "noop")
        self.assertEqual(payload["reason_code"], "MANIFEST_ALREADY_FRESH")

    def test_session_start_records_recovery_result_and_before_after_state(self) -> None:
        before = {
            "freshness_status": "stale",
            "source_hashes_verified": False,
            "source_hash_mismatches": ["plugin-cache-inventory"],
        }
        after = {
            "freshness_status": "fresh",
            "source_hashes_verified": True,
            "source_hash_mismatches": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text("before", encoding="utf-8")
            receipt_dir = Path(temp_dir) / "receipts"
            with (
                mock.patch.object(session_start, "ACTIVE_CAPABILITIES_PATH", manifest_path),
                mock.patch.object(
                    session_start, "load_active_capabilities", return_value=before
                ),
                mock.patch.object(
                    session_start,
                    "attempt_recovery",
                    return_value={
                        "status": "rebuilt",
                        "reason_code": "RECOGNIZED_ENABLED_PLUGIN_VERSION_REPLACEMENT",
                        "snapshot_sha256": "B" * 64,
                    },
                ),
                mock.patch.object(session_start, "ensure_index", return_value=after),
                mock.patch.object(session_start, "load_routing_policy", return_value={}),
            ):
                receipt_path = session_start.run_session_start(receipt_dir=receipt_dir)
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "rebuilt")
        self.assertEqual(payload["before"]["freshness_status"], "stale")
        self.assertEqual(payload["after"]["freshness_status"], "fresh")


if __name__ == "__main__":
    unittest.main()
