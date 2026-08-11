#!/usr/bin/env python3
"""Public-safe fail-closed tests for deterministic manifest recovery."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "capability-routing" / "reference-runtime"
_IMPORT_TEMP = tempfile.TemporaryDirectory(prefix="ccos-recovery-import-")
_IMPORT_ROOT = Path(_IMPORT_TEMP.name)
_ENV = {
    "CODEX_HOME": str(_IMPORT_ROOT),
    "CODEX_CAPABILITY_ROUTING_DIR": str(_IMPORT_ROOT / "routing"),
    "CODEX_ACTIVE_CAPABILITIES_PATH": str(_IMPORT_ROOT / "routing" / "active.json"),
    "CODEX_ROUTING_POLICY_PATH": str(_IMPORT_ROOT / "routing" / "policy.json"),
    "CODEX_CONFIG_PATH": str(_IMPORT_ROOT / "config.toml"),
    "CODEX_ROUTE_DECISION_SCHEMA_PATH": str(_IMPORT_ROOT / "routing" / "schema.json"),
    "CODEX_ROUTE_DECISION_REGISTRY_PATH": str(_IMPORT_ROOT / "routing" / "routes.sqlite3"),
    "CODEX_PROJECT_SCOPE_MAP_PATH": str(_IMPORT_ROOT / "routing" / "projects.json"),
}
_OLD_ENV = {name: os.environ.get(name) for name in _ENV}
os.environ.update(_ENV)
sys.path.insert(0, str(RUNTIME_ROOT))
import capability_manifest_recovery as recovery  # noqa: E402

for _name, _value in _OLD_ENV.items():
    if _value is None:
        os.environ.pop(_name, None)
    else:
        os.environ[_name] = _value


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


def row_hashes(version: str, marker: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for plugin in ("browser", "chrome", "computer-use"):
        prefix = f"openai-bundled/{plugin}/{version}"
        rows[f"ROOT\t{prefix}"] = marker * 64
        rows[f"FILE\t{prefix}/.codex-plugin/plugin.json"] = marker * 64
    rows["ROOT\topenai-bundled/chrome/latest"] = marker * 64
    rows["FILE\topenai-bundled/chrome/latest/.codex-plugin/plugin.json"] = marker * 64
    rows["ROOT\topenai-curated-remote/github/1.0.0"] = "9" * 64
    return rows


def receipt(
    version: str,
    runtime_id: str,
    cli_id: str,
    marker: str,
    *,
    config_overrides: dict[str, str] | None = None,
    row_overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    leaves = {path: marker * 64 for path in ALLOWED_CONFIG_PATHS}
    leaves["/features/hooks"] = "8" * 64
    if config_overrides:
        leaves.update(config_overrides)
    rows = row_hashes(version, marker)
    if row_overrides:
        rows.update(row_overrides)
    value: dict[str, object] = {
        "schema_version": recovery.RECEIPT_SCHEMA,
        "config_projection_sha256": marker * 64,
        "config_leaf_hashes": leaves,
        "plugin_cache_inventory_sha256": marker * 64,
        "plugin_cache_row_hashes": rows,
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
    value["snapshot_sha256"] = recovery.authority_snapshot_digest(value)
    return value


class ManifestRecoveryTests(unittest.TestCase):
    def test_exact_coherent_config_and_plugin_update_is_recognized(self) -> None:
        previous = receipt("26.803.41515", "runtime-old", "cli-old", "A")
        current = receipt("26.803.81509", "runtime-new", "cli-new", "B")

        allowed, reason = recovery.classify_app_update(previous, current)

        self.assertTrue(allowed)
        self.assertEqual(reason, "RECOGNIZED_CODEX_DESKTOP_UPDATE")

    def test_unknown_config_leaf_and_unrelated_plugin_delta_are_denied(self) -> None:
        previous = receipt("26.803.41515", "runtime-old", "cli-old", "A")
        config_delta = receipt(
            "26.803.81509",
            "runtime-new",
            "cli-new",
            "B",
            config_overrides={"/features/hooks": "7" * 64},
        )
        plugin_delta = receipt(
            "26.803.81509",
            "runtime-new",
            "cli-new",
            "B",
            row_overrides={"ROOT\topenai-curated-remote/github/1.0.0": "7" * 64},
        )

        self.assertEqual(
            recovery.classify_app_update(previous, config_delta),
            (False, "UNRECOGNIZED_CONFIG_DELTA"),
        )
        self.assertEqual(
            recovery.classify_app_update(previous, plugin_delta),
            (False, "UNRECOGNIZED_PLUGIN_DELTA"),
        )

    def test_plugin_only_and_config_only_cohorts_are_denied(self) -> None:
        previous = receipt("26.803.41515", "runtime-old", "cli-old", "A")
        plugin_only = receipt("26.803.81509", "runtime-new", "cli-new", "A")
        plugin_only["plugin_cache_row_hashes"] = row_hashes("26.803.81509", "B")
        plugin_only["plugin_cache_inventory_sha256"] = "B" * 64
        plugin_only["snapshot_sha256"] = recovery.authority_snapshot_digest(plugin_only)
        config_only = receipt("26.803.81509", "runtime-new", "cli-new", "B")
        config_only["plugin_cache_row_hashes"] = previous["plugin_cache_row_hashes"]
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

    def test_two_different_authority_reads_fail_closed(self) -> None:
        first = receipt("26.803.81509", "runtime-new", "cli-new", "B")
        second = dict(first)
        second["snapshot_sha256"] = "0" * 64
        reads = iter((first, second))

        stable, reason = recovery.stable_two_read(
            lambda: next(reads), settle_seconds=0, sleeper=lambda _: None
        )

        self.assertIsNone(stable)
        self.assertEqual(reason, "AUTHORITY_SNAPSHOT_UNSTABLE")


if __name__ == "__main__":
    unittest.main()
