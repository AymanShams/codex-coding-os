#!/usr/bin/env python3
"""Executable synthetic tests for capability manifest plugin root selection."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = (
    REPO_ROOT
    / "capability-routing"
    / "builder"
    / "build_canonical_capability_manifest.ps1"
)
CODEX_SECURITY_SKILLS = (
    "attack-path-analysis",
    "deep-security-scan",
    "define-security-policy",
    "finding-discovery",
    "fix-finding",
    "propose-security-hardening",
    "security-diff-scan",
    "security-scan",
    "threat-model",
    "track-findings",
    "triage-finding",
    "validation",
    "vulnerability-writeup",
)


class CapabilityManifestBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pwsh = shutil.which("pwsh")
        if not self.pwsh:
            self.skipTest("PowerShell 7 is required for executable builder tests")
        self.temp = tempfile.TemporaryDirectory(prefix="ccos-builder-")
        self.root = Path(self.temp.name)
        self.codex_home = self.root / "codex-home"
        self.manifest_path = self.root / "active-capabilities.json"
        self.executable_root = self.root / "resolved-mcp" / "codex-security"
        self.remote_root = (
            self.codex_home
            / "plugins"
            / "cache"
            / "openai-curated-remote"
            / "codex-security"
            / "0.1.18"
        )
        self._build_fixture()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, text: str = "fixture\n") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_plugin(self, root: Path, version: str, skills: tuple[str, ...]) -> None:
        self._write(
            root / ".codex-plugin" / "plugin.json",
            json.dumps(
                {
                    "name": "codex-security",
                    "version": version,
                    "description": "Synthetic Codex Security plugin",
                }
            )
            + "\n",
        )
        self._write(
            root / ".mcp.json",
            json.dumps(
                {
                    "mcpServers": {
                        "codex-security": {
                            "command": sys.executable,
                            "args": [],
                        }
                    }
                }
            )
            + "\n",
        )
        for name in skills:
            self._write(
                root / "skills" / name / "SKILL.md",
                f"---\nname: {name}\ndescription: Synthetic {name} skill\n---\n",
            )

    def _build_fixture(self) -> None:
        static_files = (
            "hooks.json",
            "AGENTS.md",
            "docs/context/task-routing-gate.md",
            "skills/catalogue-router/SKILL.md",
            "hooks/capability_index.py",
            "hooks/capability_index_cli.py",
            "hooks/user_prompt_skill_router.py",
            "hooks/capability_index_session_start.py",
            "hooks/_common.py",
            "hooks/_hook_io.py",
            "capability-routing/authority-receipt.schema.json",
            "capability-routing/routing-policy.yaml",
            "capability-routing/routing-policy.schema.json",
            "capability-routing/active-capabilities.schema.json",
            "capability-routing/project-scope-map.json",
            "capability-routing/project-scope-map.schema.json",
            "capability-routing/route-decision.schema.json",
            "skills/catalogue-router/scripts/query-catalogue.ps1",
            "tools/dependency-readiness/ensure-node-dependencies.ps1",
            "tools/dependency-readiness/README.md",
        )
        for relative in static_files:
            content = "{}\n" if relative.endswith((".json", ".yaml")) else "fixture\n"
            self._write(self.codex_home / relative, content)
        self._write(self.codex_home / "config.toml", "[features]\nhooks = true\n")
        self._write(
            self.codex_home / "skills" / "baseline" / "SKILL.md",
            "---\nname: baseline\ndescription: Baseline local skill\n---\n",
        )

        fingerprint = {
            "projection_schema": "capability-config-v1",
            "source_hash_key": "config-capability-projection-v1",
            "hash_scope": "capability-config-v1",
            "sha256": "1" * 64,
            "raw_sha256": "2" * 64,
            "gateway_managed_mcp_names": [],
            "explicitly_disabled_mcp_names": [],
            "disabled_skill_paths": [],
        }
        self._write(
            self.codex_home / "hooks" / "capability_config_fingerprint.py",
            "import json\nprint(json.dumps(" + repr(fingerprint) + "))\n",
        )
        receipt = {
            "schema_version": "capability-authority-receipt-v2",
            "snapshot_sha256": "3" * 64,
            "app_identity": {"coherent": True},
            "plugin_cache_inventory_sha256": "4" * 64,
        }
        self._write(
            self.codex_home / "hooks" / "capability_manifest_recovery.py",
            "import json\nprint(json.dumps(" + repr(receipt) + "))\n",
        )

        self.skills_csv = self.root / "skills.csv"
        self.plugins_csv = self.root / "plugins.csv"
        self.tools_csv = self.root / "tools.csv"
        self._write(
            self.skills_csv,
            "exposed_name,capability_family,description,provenance\n",
        )
        self._write(
            self.plugins_csv,
            "plugin,display_name,description,router_scope\n",
        )
        self._write(
            self.tools_csv,
            "capability_id,family,surface_type,activation_profile,representative_purpose\n",
        )

        self._write_plugin(
            self.executable_root, "0.1.17", CODEX_SECURITY_SKILLS[:10]
        )
        self._write_plugin(self.remote_root, "0.1.18", CODEX_SECURITY_SKILLS)
        self.mcp_inventory = self.root / "mcp-inventory.json"
        self.plugin_inventory = self.root / "plugin-inventory.json"
        self._write(
            self.mcp_inventory,
            json.dumps(
                [
                    {
                        "name": "codex-security",
                        "enabled": True,
                        "transport": {
                            "type": "stdio",
                            "command": sys.executable,
                            "args": [],
                            "cwd": str(self.executable_root),
                            "url": "",
                        },
                    }
                ]
            )
            + "\n",
        )
        self._write_plugin_inventory(enabled=True)

    def _write_plugin_inventory(self, *, enabled: bool) -> None:
        self._write(
            self.plugin_inventory,
            json.dumps(
                {
                    "installed": [
                        {
                            "pluginId": "codex-security@openai-curated-remote",
                            "name": "codex-security",
                            "marketplaceName": "openai-curated-remote",
                            "version": "0.1.17",
                            "installed": True,
                            "enabled": enabled,
                            "source": {"path": str(self.executable_root)},
                        }
                    ]
                }
            )
            + "\n",
        )

    def _run_builder(self) -> tuple[dict[str, object], bytes]:
        command = [
            self.pwsh,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(BUILDER),
            "-CodexHome",
            str(self.codex_home),
            "-SkillsCsvPath",
            str(self.skills_csv),
            "-PluginsCsvPath",
            str(self.plugins_csv),
            "-ToolsCsvPath",
            str(self.tools_csv),
            "-ManifestPath",
            str(self.manifest_path),
            "-McpInventoryJsonPath",
            str(self.mcp_inventory),
            "-PluginInventoryJsonPath",
            str(self.plugin_inventory),
            "-RouterPythonExe",
            sys.executable,
            "-ConfigFingerprintModulePath",
            str(self.codex_home / "hooks" / "capability_config_fingerprint.py"),
            "-GeneratedAt",
            "2026-08-13T00:00:00Z",
        ]
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"builder failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return json.loads(self.manifest_path.read_text(encoding="utf-8")), self.manifest_path.read_bytes()

    def test_resolved_executable_and_prompt_skill_roots_are_separate(self) -> None:
        manifest, first_bytes = self._run_builder()
        by_id = {entry["id"]: entry for entry in manifest["entries"]}
        plugin = by_id["plugin:codex-security"]
        self.assertTrue(
            Path(plugin["source_path"]).samefile(
                self.executable_root / ".codex-plugin" / "plugin.json"
            )
        )
        self.assertEqual(plugin["activation_basis"], "passive-resolved-mcp-package-root")
        self.assertEqual(plugin["components"]["skills"]["count"], 13)
        self.assertEqual(plugin["components"]["mcp_servers"][0]["state"], "active")
        for name in CODEX_SECURITY_SKILLS:
            entry = by_id[f"skill:codex-security:{name}"]
            self.assertTrue(
                Path(entry["source_path"]).samefile(
                    self.remote_root / "skills" / name / "SKILL.md"
                )
            )
        self.assertIn("mcp:codex-security", by_id)

        _, second_bytes = self._run_builder()
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(
            hashlib.sha256(first_bytes).hexdigest(),
            hashlib.sha256(second_bytes).hexdigest(),
        )

    def test_disabled_plugin_suppresses_executable_and_prompt_surfaces(self) -> None:
        self._write_plugin_inventory(enabled=False)
        manifest, _ = self._run_builder()
        identifiers = {entry["id"] for entry in manifest["entries"]}
        self.assertNotIn("plugin:codex-security", identifiers)
        self.assertFalse(
            any(identifier.startswith("skill:codex-security:") for identifier in identifiers)
        )

    def test_ambiguous_remote_skill_root_fails_closed_to_executable_root(self) -> None:
        second_remote = self.remote_root.parent / "0.1.19"
        self._write_plugin(second_remote, "0.1.19", CODEX_SECURITY_SKILLS)
        manifest, _ = self._run_builder()
        by_id = {entry["id"]: entry for entry in manifest["entries"]}
        plugin = by_id["plugin:codex-security"]
        self.assertEqual(plugin["components"]["skills"]["count"], 10)
        self.assertNotIn(
            "skill:codex-security:vulnerability-writeup",
            by_id,
        )
        for name in CODEX_SECURITY_SKILLS[:10]:
            entry = by_id[f"skill:codex-security:{name}"]
            self.assertTrue(
                Path(entry["source_path"]).samefile(
                    self.executable_root / "skills" / name / "SKILL.md"
                )
            )

    def test_ambiguous_remote_without_executable_authority_is_suppressed(self) -> None:
        second_remote = self.remote_root.parent / "0.1.19"
        self._write_plugin(second_remote, "0.1.19", CODEX_SECURITY_SKILLS)
        self._write(self.mcp_inventory, "[]\n")
        inventory = json.loads(self.plugin_inventory.read_text(encoding="utf-8"))
        inventory["installed"][0]["source"]["path"] = str(self.root / "missing")
        self._write(self.plugin_inventory, json.dumps(inventory) + "\n")

        manifest, _ = self._run_builder()
        identifiers = {entry["id"] for entry in manifest["entries"]}
        self.assertNotIn("plugin:codex-security", identifiers)
        self.assertFalse(
            any(identifier.startswith("skill:codex-security:") for identifier in identifiers)
        )
        self.assertIn(
            {
                "id": "plugin:codex-security",
                "reason_code": "PASSIVE_PLUGIN_VERSION_AMBIGUOUS",
            },
            [
                {
                    "id": item["id"],
                    "reason_code": item["reason_code"],
                }
                for item in manifest["suppressed_capabilities"]
            ],
        )


if __name__ == "__main__":
    unittest.main()
