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
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = (
    REPO_ROOT
    / "capability-routing"
    / "builder"
    / "build_canonical_capability_manifest.ps1"
)
RUNTIME_ROOT = REPO_ROOT / "capability-routing" / "reference-runtime"
sys.path.insert(0, str(RUNTIME_ROOT))
import capability_manifest_recovery as recovery
sys.path.pop(0)
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
            "skills/catalogue-router/SKILL.md",
            "hooks/capability_index.py",
            "hooks/routing_policy_validation.py",
            "hooks/capability_index_cli.py",
            "hooks/user_prompt_skill_router.py",
            "hooks/capability_index_session_start.py",
            "hooks/_hook_io.py",
            "capability-routing/authority-receipt.schema.json",
            "capability-routing/routing-policy.yaml",
            "capability-routing/routing-policy.schema.json",
            "capability-routing/active-capabilities.schema.json",
            "capability-routing/project-scope-map.json",
            "capability-routing/project-scope-map.schema.json",
            "capability-routing/route-decision.schema.json",
            "capability-routing/worker-runtime-bom.json",
            "capability-routing/worker-runtime-bom.schema.json",
            "capability-routing/promote_worker_runtime_bom.py",
            "skills/catalogue-router/scripts/query-catalogue.ps1",
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
            "projection_schema": "capability-config-v2",
            "source_hash_key": "config-capability-projection-v2",
            "hash_scope": "capability-config-v2",
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
        self.builder_receipt = {
            "schema_version": "capability-authority-receipt-v3",
            "snapshot_sha256": "3" * 64,
            "app_identity": {"coherent": True},
            "config_projection_sha256": "1" * 64,
            "plugin_cache_inventory_sha256": "4" * 64,
            "remote_plugin_catalog_entries": {},
            "bundled_marketplace_origin": {
                "schema_version": "bundled-marketplace-origin-v1",
                "app_version": "26.800.1",
                "authority_projection_sha256": "6" * 64,
            },
            "primary_runtime_bundle_origin": {
                "schema_version": "primary-runtime-bundle-origin-v1",
                "bundle_version": "26.800.1",
                "authority_projection_sha256": "5" * 64,
            },
            "required_source_hashes": {
                "worker-runtime-bom.json": hashlib.sha256(
                    (self.codex_home / "capability-routing" / "worker-runtime-bom.json").read_bytes()
                ).hexdigest().upper()
            },
        }
        self._write(
            self.codex_home / "hooks" / "capability_manifest_recovery.py",
            "import json\nprint(json.dumps(" + repr(self.builder_receipt) + "))\n",
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
        installed_builder = (
            self.codex_home
            / "capability-routing"
            / "builder"
            / "build_canonical_capability_manifest.ps1"
        )
        installed_builder.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BUILDER, installed_builder)
        references = installed_builder.parent / "references"
        references.mkdir()
        for source, name in (
            (self.skills_csv, "universal-skills-2026-07-25.csv"),
            (self.plugins_csv, "universal-plugins-2026-07-25.csv"),
            (
                self.tools_csv,
                "universal-tool-families-and-mcps-2026-07-25.csv",
            ),
        ):
            shutil.copy2(source, references / name)

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
        fake_codex_script = self.root / "fake-codex.py"
        self._write(
            fake_codex_script,
            "import pathlib, sys\n"
            f"mcp = pathlib.Path({str(self.mcp_inventory)!r})\n"
            f"plugins = pathlib.Path({str(self.plugin_inventory)!r})\n"
            "args = sys.argv[1:]\n"
            "path = mcp if args[:2] == ['mcp', 'list'] else plugins if args[:2] == ['plugin', 'list'] else None\n"
            "if path is None:\n"
            "    raise SystemExit(2)\n"
            "print(path.read_text(encoding='utf-8'))\n",
        )
        self.fake_codex = self.root / "fake-codex.cmd"
        self._write(
            self.fake_codex,
            f'@"{sys.executable}" "{fake_codex_script}" %*\n',
        )
        self.builder_receipt["app_identity"].update(
            {
                "cli_id": self.fake_codex.parent.name,
                "cli_executable_sha256": hashlib.sha256(
                    self.fake_codex.read_bytes()
                ).hexdigest().upper(),
            }
        )
        self._write(
            self.codex_home / "hooks" / "capability_manifest_recovery.py",
            "import json\nprint(json.dumps("
            + repr(self.builder_receipt)
            + "))\n",
        )

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

    def _run_builder(
        self, *, promotion_reason: str | None = None
    ) -> tuple[dict[str, object], bytes]:
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
        if promotion_reason is not None:
            command.extend(["-PromotionReason", promotion_reason])
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
        self.assertEqual(manifest["schema_version"], "1.3")
        generation = manifest["authority_generation"]
        self.assertEqual(manifest["snapshot_id"], f"authority-generation:{generation['id']}")
        self.assertEqual(generation["sequence"], 1)
        self.assertIsNone(generation["previous_id"])
        self.assertEqual(generation["promotion_reason"], "operator_rebaseline")
        for key in (
            "id",
            "static_authority_sha256",
            "dynamic_authority_sha256",
            "config_projection_sha256",
            "plugin_inventory_sha256",
            "worker_runtime_bom_sha256",
            "authority_snapshot_sha256",
        ):
            self.assertRegex(generation[key], r"^[a-f0-9]{64}$")
        self.assertEqual(
            generation["worker_runtime_bom_sha256"],
            manifest["source_hashes"]["worker-runtime-bom.json"].lower(),
        )
        self.assertEqual(
            manifest["source_hashes"]["promote_worker_runtime_bom.py"],
            hashlib.sha256(
                (
                    self.codex_home
                    / "capability-routing"
                    / "promote_worker_runtime_bom.py"
                ).read_bytes()
            ).hexdigest().upper(),
        )
        by_id = {entry["id"]: entry for entry in manifest["entries"]}
        for entry in manifest["entries"]:
            self.assertIn(
                entry["hash_scope"],
                {"file-sha256", "text-sha256", "capability-config-v2"},
            )
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

    def test_primary_runtime_promotion_reasons_are_generation_bound(self) -> None:
        for reason in (
            "primary_runtime_bundle_update",
            "compound_app_primary_runtime_update",
            "worker_runtime_bom_update",
        ):
            with self.subTest(reason=reason):
                manifest, _ = self._run_builder(promotion_reason=reason)
                self.assertEqual(
                    manifest["authority_generation"]["promotion_reason"], reason
                )

    def test_operator_candidate_runner_passes_installed_authority_inventories(self) -> None:
        receipt = {
            "app_identity": {
                "cli_id": "synthetic-cli",
                "cli_executable_sha256": "A" * 64,
            }
        }
        with mock.patch.object(
            recovery,
            "resolve_receipt_codex_exe",
            return_value=self.fake_codex,
        ):
            candidate = recovery.run_builder_candidate(
                "3" * 64,
                self.manifest_path,
                authority_receipt=receipt,
                previous_generation_id=None,
                generation_sequence=1,
                authority_transaction_id="operator-build-test",
                promotion_reason="operator_rebaseline",
                codex_home=self.codex_home,
            )
        try:
            manifest = json.loads(candidate.read_text(encoding="utf-8"))
        finally:
            candidate.unlink(missing_ok=True)
        self.assertEqual(
            manifest["authority_generation"]["promotion_reason"],
            "operator_rebaseline",
        )
        self.assertEqual(
            manifest["authority_generation"]["authority_snapshot_sha256"],
            "3" * 64,
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
