"""Regression tests for artifact identity and README freshness contracts."""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate_documentation_contracts.py"
SPEC = importlib.util.spec_from_file_location("validate_documentation_contracts", VALIDATOR_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import documentation validator: {VALIDATOR_PATH}")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def artifact(
    artifact_id: str,
    path: str,
    relationship: str,
    source_status: str,
    **extra: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "artifact_id": artifact_id,
        "family_id": "template.example",
        "artifact_type": "template",
        "path": path,
        "source_status": source_status,
        "relationship": relationship,
        "owner": "fixture-owner",
        "consumers": [f"fixture:{artifact_id}"],
        "trigger": {
            "trigger_id": f"trigger_{artifact_id}",
            "kind": "manual",
        },
        "generation_route": "fixture",
    }
    result.update(extra)
    return result


class DocumentationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="coding-os-doc-contract-")
        self.repo = Path(self.temp.name)
        (self.repo / "canonical.md").write_text("same\n", encoding="utf-8")
        (self.repo / "mirror.md").write_text("same\n", encoding="utf-8")
        (self.repo / "variant.md").write_text("variant\n", encoding="utf-8")
        (self.repo / "README.md").write_text(
            "[GitHub Releases](https://github.com/AymanShams/codex-coding-os/releases)\n"
            "[GitHub pull requests](https://github.com/AymanShams/codex-coding-os/pulls)\n"
            "[GitHub Actions](https://github.com/AymanShams/codex-coding-os/actions)\n"
            "Use pack.manifest.json and install-bundle.manifest.json.\n",
            encoding="utf-8",
        )
        self.manifest = {
            "required_files": ["canonical.md", "mirror.md", "variant.md"],
            "artifact_definitions": [
                artifact("canonical", "canonical.md", "canonical", "authoritative"),
                artifact(
                    "mirror",
                    "mirror.md",
                    "exact_mirror",
                    "projection",
                    canonical_artifact_id="canonical",
                ),
                artifact(
                    "variant",
                    "variant.md",
                    "intentional_variant",
                    "authoritative",
                    canonical_artifact_id="canonical",
                    variant_reason="A distinct consumer requires a standalone form.",
                ),
            ],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def errors(self, manifest: dict[str, object] | None = None) -> list[str]:
        current = manifest if manifest is not None else self.manifest
        (self.repo / "pack.manifest.json").write_text(
            json.dumps(current, indent=2) + "\n", encoding="utf-8"
        )
        return VALIDATOR.validate_repository(self.repo)

    def test_repository_contract_passes(self) -> None:
        self.assertEqual([], self.errors())

    def test_exact_mirror_drift_fails(self) -> None:
        (self.repo / "mirror.md").write_text("drifted\n", encoding="utf-8")
        self.assertTrue(any("exact mirror differs" in error for error in self.errors()))

    def test_intentional_variant_requires_reason_case(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["artifact_definitions"][2].pop("variant_reason")
        self.assertTrue(any("must declare variant_reason" in error for error in self.errors(manifest)))

    def test_artifact_path_must_be_required(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["required_files"].remove("variant.md")
        self.assertTrue(any("not declared in required_files" in error for error in self.errors(manifest)))

    def test_duplicate_artifact_id_fails(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["artifact_definitions"][2]["artifact_id"] = "mirror"
        self.assertTrue(any("duplicate artifact_id" in error for error in self.errors(manifest)))

    def test_trigger_must_be_a_typed_object(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["artifact_definitions"][0]["trigger"] = "legacy_string_trigger"
        self.assertTrue(any("typed trigger object" in error for error in self.errors(manifest)))

    def test_conditional_trigger_rejects_duplicate_facts(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["artifact_definitions"][0]["trigger"] = {
            "trigger_id": "conditional_fixture",
            "kind": "conditional",
            "match": "any",
            "predicates": [
                {"fact": "project.has_surface", "operator": "equals", "value": True},
                {"fact": "project.has_surface", "operator": "equals", "value": False},
            ],
        }
        self.assertTrue(any("duplicate predicate fact" in error for error in self.errors(manifest)))

    def test_reused_trigger_id_must_keep_one_definition(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        trigger_id = manifest["artifact_definitions"][0]["trigger"]["trigger_id"]
        manifest["artifact_definitions"][1]["trigger"] = {
            "trigger_id": trigger_id,
            "kind": "workflow_phase",
            "phase": "5_repo_documentation",
        }
        self.assertTrue(any("conflicting definition" in error for error in self.errors(manifest)))

    def test_missing_source_status_fails(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["artifact_definitions"][1].pop("source_status")
        self.assertTrue(any("missing source_status" in error for error in self.errors(manifest)))

    def test_cross_family_canonical_reference_fails(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["artifact_definitions"][2]["family_id"] = "template.other"
        self.assertTrue(any("must share family_id" in error for error in self.errors(manifest)))

    def test_exact_mirror_owner_mismatch_fails(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["artifact_definitions"][1]["owner"] = "different-owner"
        self.assertTrue(any("must share the canonical owner" in error for error in self.errors(manifest)))

    def test_hardcoded_readme_release_and_inventory_fail(self) -> None:
        with (self.repo / "README.md").open("a", encoding="utf-8") as handle:
            handle.write("| Latest published GitHub release | `v9.9.9` |\n")
            handle.write("| Tracked files | 999 |\n")
        errors = self.errors()
        self.assertTrue(any("hardcoded latest release tag" in error for error in errors))
        self.assertTrue(any("hardcodes mutable inventory count" in error for error in errors))

    def test_hardcoded_readme_inventory_prose_fails(self) -> None:
        base_readme = (self.repo / "README.md").read_text(encoding="utf-8")
        for phrase in ("Five bundled capabilities", "The exact 45-skill list"):
            with self.subTest(phrase=phrase):
                (self.repo / "README.md").write_text(
                    base_readme + f"\n## Package inventory\n\n{phrase}\n",
                    encoding="utf-8",
                )
                self.assertTrue(
                    any("inventory count in package inventory prose" in error for error in self.errors())
                )

    def test_live_repository_contract_passes(self) -> None:
        self.assertEqual([], VALIDATOR.validate_repository(ROOT))

    def test_campaign_contract_documents_the_executable_and_formal_boundaries(self) -> None:
        campaign_contract = (ROOT / "docs" / "campaign-engine.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("scripts/agent/campaign_engine/reducer.py", campaign_contract)
        self.assertIn("formal/Campaign.tla", campaign_contract)
        self.assertIn("LEGACY_ENGINE_RETIRED", campaign_contract)
        self.assertIn("Repository state files, handoffs, comments, branch names", readme)

    def test_all_audited_family_members_are_registered(self) -> None:
        manifest = json.loads((ROOT / "pack.manifest.json").read_text(encoding="utf-8"))
        registered = {
            definition["path"]: definition["relationship"]
            for definition in manifest["artifact_definitions"]
        }
        expected = {
            ".agents/skills/technical-docs-pack/references/repo-docs-template.md": "canonical",
            "templates/repo-docs-template.md": "exact_mirror",
            ".agents/skills/new-project-documentation-system/assets/project-brief-template.md": "canonical",
            "templates/project-brief.md": "exact_mirror",
            ".agents/skills/new-project-documentation-system/assets/AGENTS.md": "canonical",
            "templates/repo-AGENTS.md": "intentional_variant",
            ".agents/skills/new-project-documentation-system/assets/scoped-AGENTS.md": "canonical",
            "templates/scoped-AGENTS.md": "intentional_variant",
            ".agents/skills/new-project-documentation-system/assets/CLAUDE.md": "canonical",
            "templates/CLAUDE.md": "intentional_variant",
            ".agents/skills/new-project-documentation-system/assets/history-handoff-template.md": "canonical",
            "templates/handoff-note.md": "intentional_variant",
            ".agents/skills/technical-docs-pack/references/content-guidelines-template.md": "canonical",
            ".agents/skills/technical-docs-pack/references/search-documentation-template.md": "canonical",
            ".agents/skills/technical-docs-pack/references/module-contract-template.md": "canonical",
        }
        for path, relationship in expected.items():
            self.assertEqual(relationship, registered.get(path), path)


if __name__ == "__main__":
    unittest.main()
