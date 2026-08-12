#!/usr/bin/env python3
"""Fail-closed tests for deterministic catalogue manifest recovery."""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "capability-routing" / "reference-runtime"
sys.path.insert(0, str(RUNTIME_ROOT))
import capability_manifest_recovery as recovery
import capability_index_session_start as session_start
sys.path.pop(0)


ALLOWED_CONFIG_PATHS = (
    "/mcp_servers/node_repl/command",
    "/mcp_servers/node_repl/env/NODE_REPL_NODE_MODULE_DIRS",
    "/mcp_servers/node_repl/env/NODE_REPL_NODE_PATH",
    "/mcp_servers/node_repl/env/NODE_REPL_TRUSTED_CODE_PATHS",
    "/mcp_servers/node_repl/env/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
    "/mcp_servers/node_repl/env/BROWSER_USE_CODEX_APP_VERSION",
    "/mcp_servers/node_repl/env/CODEX_CLI_PATH",
    "/shell_environment_policy/set/NODE_REPL_TRUSTED_CODE_PATHS",
    "/shell_environment_policy/set/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
)


def _write_required_sources(codex_home: Path) -> None:
    for path in recovery._required_source_paths(codex_home).values():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("{}\n", encoding="utf-8")


def _row_hashes(version: str, marker: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for plugin in ("browser", "chrome", "computer-use"):
        prefix = f"openai-bundled/{plugin}/{version}"
        rows[f"ROOT\t{prefix}"] = marker * 64
        rows[f"FILE\t{prefix}/.codex-plugin/plugin.json"] = marker * 64
    rows["ROOT\topenai-bundled/chrome/latest"] = marker * 64
    rows[
        "FILE\topenai-bundled/chrome/latest/.codex-plugin/plugin.json"
    ] = marker * 64
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


def _receipt(
    version: str,
    runtime_id: str,
    cli_id: str,
    marker: str,
    *,
    config_overrides: dict[str, str] | None = None,
    row_overrides: dict[str, str] | None = None,
) -> dict:
    leaves = {path: marker * 64 for path in ALLOWED_CONFIG_PATHS}
    leaves["/features/hooks"] = "8" * 64
    if config_overrides:
        leaves.update(config_overrides)
    rows = _row_hashes(version, marker)
    if row_overrides:
        rows.update(row_overrides)
    receipt = {
        "schema_version": recovery.RECEIPT_SCHEMA,
        "config_projection_sha256": marker * 64,
        "config_leaf_hashes": leaves,
        "plugin_cache_inventory_sha256": marker * 64,
        "plugin_cache_row_hashes": rows,
        "plugin_capability_surfaces": _surfaces_for_rows(rows),
        "plugin_package_manifests": {},
        "plugin_package_origins": {},
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


def _managed_origin(
    package: str,
    *,
    manifest_sha256: str,
    package_sha256: str,
    install_receipt_sha256: str,
    remote_plugin_id: str = "plugin_asdk_example_12345678",
) -> dict[str, object]:
    marketplace, plugin_name, plugin_version = package.split("/")
    return {
        "receipt_schema_version": 2,
        "remote_plugin_id": remote_plugin_id,
        "marketplace": marketplace,
        "plugin_name": plugin_name,
        "plugin_version": plugin_version,
        "plugin_manifest_sha256": manifest_sha256,
        "package_sha256": package_sha256,
        "install_receipt_sha256": install_receipt_sha256,
    }


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
    )
    receipt["snapshot_sha256"] = recovery.authority_snapshot_digest(receipt)
    return receipt


class RecoveryClassificationTests(unittest.TestCase):
    def test_exact_coherent_app_update_is_recognized(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = _receipt("26.803.81509", "runtime-new", "cli-new", "B")

        allowed, reason = recovery.classify_app_update(previous, current)

        self.assertTrue(allowed)
        self.assertEqual(reason, "RECOGNIZED_CODEX_DESKTOP_UPDATE")

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

    def test_plugin_only_or_config_only_update_is_denied(self) -> None:
        previous = _receipt("26.803.41515", "runtime-old", "cli-old", "A")
        plugin_only = _receipt("26.803.81509", "runtime-new", "cli-new", "A")
        plugin_only["plugin_cache_row_hashes"] = _row_hashes("26.803.81509", "B")
        plugin_only["plugin_cache_inventory_sha256"] = "B" * 64
        plugin_only["snapshot_sha256"] = recovery.authority_snapshot_digest(plugin_only)
        config_only = _receipt("26.803.81509", "runtime-new", "cli-new", "B")
        config_only["plugin_cache_row_hashes"] = previous["plugin_cache_row_hashes"]
        config_only["plugin_capability_surfaces"] = previous[
            "plugin_capability_surfaces"
        ]
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
            "PLUGIN_COHORT_INCOMPLETE",
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

    def test_self_authored_plugin_receipts_never_authorize_replacement(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")

        allowed, reason = recovery.classify_enabled_plugin_version_replacement(
            previous, current
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "PLUGIN_ORIGIN_UNPROVEN")

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

        self.assertFalse(allowed)
        self.assertEqual(reason, "PLUGIN_ORIGIN_UNPROVEN")

    def test_remote_false_overrides_enabled_legacy_alias(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")
        exact_pointer = "/plugins/gmail@openai-curated-remote/enabled"
        for receipt in (previous, current):
            receipt["config_leaf_hashes"][exact_pointer] = _boolean_leaf_hash(False)
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

    def test_mixed_or_add_only_plugin_cache_delta_is_denied(self) -> None:
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
            "PLUGIN_VERSION_REPLACEMENT_INCOMPLETE",
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
            for name in (
                "capability_index_session_start.py",
                "_common.py",
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

    def test_root_v1_receipt_never_authorizes_changed_package_bytes(self) -> None:
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
            previous["plugin_package_origins"] = {}
            current["plugin_package_origins"] = {}
            previous["snapshot_sha256"] = recovery.authority_snapshot_digest(previous)
            current["snapshot_sha256"] = recovery.authority_snapshot_digest(current)
            allowed, reason = recovery.classify_enabled_plugin_version_replacement(
                previous, current
            )

        self.assertEqual(origins, {})
        self.assertFalse(allowed)
        self.assertEqual(reason, "PLUGIN_ORIGIN_UNPROVEN")

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
                        "config-capability-projection-v1",
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

    def test_enabled_plugin_replacement_cannot_enter_recovery_without_detached_attestation(self) -> None:
        previous = _curated_plugin_receipt("0.1.7", "B")
        current = _curated_plugin_receipt("0.1.8", "C")
        builder = mock.Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "active-capabilities.json"
            manifest_path.write_text(
                json.dumps({"authority_receipt": previous, "entries": []}),
                encoding="utf-8",
            )
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

        self.assertEqual(result["status"], "denied")
        self.assertEqual(result["reason_code"], "PLUGIN_ORIGIN_UNPROVEN")
        builder.assert_not_called()

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
