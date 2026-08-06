"""Public installation contract checks for the single campaign engine runtime."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.agent.campaign_engine import admission
from scripts.agent.campaign_engine.model import CampaignSpec


class PublicInstallDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = REPO_ROOT
        cls.getting_started = (cls.repo_root / "docs" / "getting-started.md").read_text(
            encoding="utf-8"
        )
        cls.readme = (cls.repo_root / "README.md").read_text(encoding="utf-8")
        cls.publishing_checklist = (
            cls.repo_root / "docs" / "publishing-checklist.md"
        ).read_text(encoding="utf-8")
        cls.retirement_contract = (
            cls.repo_root / "docs" / "case-state-contract.md"
        ).read_text(encoding="utf-8")
        cls.codex_rules = (cls.repo_root / "docs" / "codex-rules.md").read_text(
            encoding="utf-8"
        )
        cls.hooks_doc = (
            cls.repo_root / "docs" / "codex-plugins-mcps-hooks.md"
        ).read_text(encoding="utf-8")
        cls.claude_template = (
            cls.repo_root / "templates" / "CLAUDE.md"
        ).read_text(encoding="utf-8")
        cls.capability_catalogue = (
            cls.repo_root
            / ".agents"
            / "skills"
            / "catalogue-router"
            / "references"
            / "capability-catalogue.md"
        ).read_text(encoding="utf-8")
        cls.historical_adr = (
            cls.repo_root
            / "docs"
            / "architecture"
            / "adr"
            / "0001-brokered-runtime-action-boundary.md"
        ).read_text(encoding="utf-8")
        cls.historical_adr_2 = (
            cls.repo_root
            / "docs"
            / "architecture"
            / "adr"
            / "0002-artifact-authorized-one-shot-action.md"
        ).read_text(encoding="utf-8")
        cls.campaign_example = (
            cls.repo_root / "templates" / "campaign.example.json"
        ).read_text(encoding="utf-8")
        cls.pack_manifest = json.loads(
            (cls.repo_root / "pack.manifest.json").read_text(encoding="utf-8")
        )
        cls.trufflehog_allowlist = json.loads(
            (
                cls.repo_root
                / "scripts"
                / "release-safety-trufflehog-allowlist.json"
            ).read_text(encoding="utf-8")
        )
        cls.release_safety_script = (
            cls.repo_root / "scripts" / "release-safety-scan.ps1"
        ).read_text(encoding="utf-8")
        cls.validation_workflow = (
            cls.repo_root / ".github" / "workflows" / "validate.yml"
        ).read_text(encoding="utf-8")
        cls.public_docs = f"{cls.readme}\n{cls.getting_started}"
        cls.install_ps1 = (cls.repo_root / "scripts" / "install.ps1").read_text(
            encoding="utf-8"
        )
        cls.install_sh = (cls.repo_root / "scripts" / "install.sh").read_text(
            encoding="utf-8"
        )
        cls.transaction = (
            cls.repo_root / "scripts" / "install_transaction.py"
        ).read_text(encoding="utf-8")

    def test_installer_requires_bundle_and_exact_source_commit(self) -> None:
        normalized = " ".join(self.public_docs.lower().split())
        self.assertIn("exact clean tagged git checkout", normalized)
        self.assertIn(
            "[Parameter(Mandatory = $true)][string]$ExpectedSourceCommit",
            self.install_ps1,
        )
        self.assertIn('[[ -n "$expected_commit" ]] ||', self.install_sh)
        self.assertIn('install_parser.add_argument("--expected-source-commit", required=True)', self.transaction)

    def test_public_docs_define_the_complete_runtime_pin(self) -> None:
        for field in (
            "source commit",
            "bundle digest",
            "install transaction",
            "protocol version",
            "schema compatibility",
            "host capability probe version",
        ):
            self.assertIn(field, self.public_docs)

    def test_each_public_install_doc_explains_policy_tri_state(self) -> None:
        for label, document in (
            ("README.md", self.readme),
            ("docs/getting-started.md", self.getting_started),
        ):
            with self.subTest(document=label):
                normalized = " ".join(document.split())
                self.assertIn("tri-state", normalized)
                self.assertIn("Omitting both policy action flags", normalized)
                self.assertIn("preserves a previously managed", normalized)
                self.assertIn("-RemoveUniversalPolicy", normalized)
                self.assertIn("--remove-universal-policy", normalized)
                self.assertIn("-InstallUniversalPolicy", normalized)
                self.assertIn("--install-universal-policy", normalized)
                self.assertIn("policy authority source and reference", normalized)

    def test_public_installers_reject_noncanonical_codex_home(self) -> None:
        self.assertIn("account profile's `.codex` directory", self.public_docs)
        self.assertIn(
            "CodexHome must equal the canonical operating-system account-profile path",
            self.install_ps1,
        )
        self.assertIn(
            "GetFolderPath([Environment+SpecialFolder]::UserProfile)",
            self.install_ps1,
        )
        self.assertIn("pwd.getpwuid(os.getuid()).pw_dir", self.install_sh)
        self.assertIn(
            "--codex-home must equal the canonical operating-system account-profile path",
            self.install_sh,
        )
        self.assertIn(
            "SkillsRoot must equal the canonical CodexHome skills path",
            self.install_ps1,
        )
        self.assertIn(
            "--skills-root must equal the canonical Codex home skills path",
            self.install_sh,
        )
        self.assertIn("any other skills root is rejected", self.public_docs)

    def test_public_docs_point_to_external_campaign_state_and_campaign_hook(self) -> None:
        self.assertIn("coding-os-state", self.public_docs)
        self.assertIn("campaigns.sqlite3", self.public_docs)
        self.assertIn("campaign hook", self.public_docs)
        self.assertIn("campaign_engine/cli.py", self.public_docs.replace("\\", "/"))
        for retired in (
            "case_state.py action-check",
            "case_state.py transition",
            "case_runtime_supervisor.py",
            "activate_anti_loop.py",
            "SHIP_PRODUCT_WITH_CONTROL_QUARANTINED",
        ):
            self.assertNotIn(retired, self.public_docs)

    def test_public_docs_publish_the_current_campaign_commands_only(self) -> None:
        for command in (
            "doctor",
            "admit --spec",
            "approve --campaign-id",
            "run --campaign-id",
            "status --campaign-id",
            "cancel --campaign-id",
            "reconcile --operation-id",
            "legacy inspect --source",
        ):
            self.assertIn(command, self.public_docs)
        for retired in ("case_state.py show", "case_state.py action-check", "case_state.py transition"):
            self.assertNotIn(retired, self.public_docs)

    def test_public_docs_state_campaign_authority_and_keep_old_adapter_flags_out(self) -> None:
        self.assertIn("publication authority", self.public_docs)
        self.assertIn("do not authorize or block work", self.public_docs)
        for adapter in (self.install_ps1, self.install_sh, self.transaction):
            self.assertNotIn("--authority-case-id", adapter)
            self.assertNotIn("--case-state-engine", adapter)
            self.assertNotIn("--authority-actor-thread-id", adapter)

    def test_legacy_archive_is_read_only_evidence(self) -> None:
        self.assertIn("legacy inspect", self.public_docs.lower())
        self.assertIn("LEGACY_ARCHIVED_UNRESOLVED", self.public_docs)
        self.assertIn("cannot create a campaign", self.public_docs)

    def test_public_docs_name_the_six_repository_adapter_signals(self) -> None:
        for signal in (
            "product-quality",
            "product-tests",
            "product-acceptance",
            "requested-documentation",
            "coding-os-adapter",
            "pr-metadata",
        ):
            self.assertIn(signal, self.readme)

    def test_publishing_checklist_requires_pack_validation_and_rebuild(self) -> None:
        self.assertIn("validate-pack.ps1", self.publishing_checklist)
        self.assertIn("install_transaction.py --json build-bundle", self.publishing_checklist)
        self.assertIn("package.ps1", self.publishing_checklist)
        self.assertIn("install-uninstall-smoke.ps1", self.publishing_checklist)

    def test_public_docs_use_real_packaging_and_installer_help_commands(self) -> None:
        self.assertNotIn("scripts/package.sh", self.public_docs)
        self.assertNotIn("install.ps1 -Help", self.public_docs)
        self.assertIn("Get-Help .\\scripts\\install.ps1 -Full", self.readme)

    def test_universal_rules_and_campaign_hook_use_transactional_installation(self) -> None:
        self.assertIn("-InstallUniversalPolicy", self.codex_rules)
        self.assertIn("preserves unrelated rules bytes", self.codex_rules)
        self.assertNotIn(
            'Copy-Item -LiteralPath ".\\.codex\\rules\\default.rules" -Destination "$HOME',
            self.codex_rules,
        )
        self.assertIn("campaign hook is installed transactionally", self.hooks_doc)
        self.assertIn("enforce decisions delegated to the", self.hooks_doc)
        self.assertNotIn("Hooks are not enabled by default", self.hooks_doc)
        self.assertIn("if (Test-Path -LiteralPath $Target)", self.codex_rules)
        self.assertNotIn("-Destination $Target -Force", self.codex_rules)

    def test_campaign_client_template_uses_valid_global_argument_order(self) -> None:
        self.assertIn('cli.py" --json status --repository-root .', self.claude_template)
        self.assertNotIn("status --repository-root . --json", self.claude_template)

    def test_release_docs_explain_the_retirement_reason_and_boundary(self) -> None:
        for phrase in (
            "What was retired",
            "Why it was retired",
            "Permanent compatibility boundary",
            "not a compatibility engine",
            "LEGACY_ARCHIVED_UNRESOLVED",
        ):
            self.assertIn(phrase, self.retirement_contract)
        self.assertNotIn("manifest blocks coding", self.capability_catalogue)
        self.assertNotIn("decisions in this ADR remain current", self.historical_adr)
        self.assertIn("deterministic `LEGACY_ENGINE_RETIRED` denial stub", self.historical_adr)
        self.assertIn("non-operative in version 1.0", self.historical_adr_2)
        self.assertIn("no old grant can recover", self.historical_adr_2)

    def test_release_checklist_binds_tag_assets_and_retirement_proof(self) -> None:
        for phrase in (
            "annotated `vX.Y.Z` tag",
            "codex-coding-os-vX.Y.Z.zip",
            "LEGACY_ENGINE_RETIRED",
            "Why retired:",
            "Bundle digest:",
        ):
            self.assertIn(phrase, self.publishing_checklist)
        for phrase in (
            ".\\scripts\\package.ps1 -OutputPath $ReleaseAsset",
            "Get-FileHash -Algorithm SHA256",
            "[IO.File]::WriteAllText($SidecarPath",
            "Release ZIP sidecar digest mismatch",
        ):
            self.assertIn(phrase, self.publishing_checklist)

    def test_ci_runs_the_redacted_scanner_gate_tests(self) -> None:
        self.assertIn(
            "python -B -m unittest tests.test_trufflehog_result_gate -v",
            self.validation_workflow,
        )

    def test_manifest_declares_the_legacy_retirement_contract(self) -> None:
        self.assertEqual(
            self.pack_manifest["source_of_truth"]["legacy_retirement_contract"],
            "docs/case-state-contract.md",
        )
        self.assertIn(
            "docs/case-state-contract.md",
            self.pack_manifest["required_files"],
        )

    def test_tagged_checkout_and_archive_install_paths_are_distinct(self) -> None:
        self.assertIn("tagged Git checkout", self.public_docs)
        self.assertIn("-ArchiveMode", self.public_docs)
        self.assertIn("release ZIP has no `.git` directory", self.getting_started)
        self.assertIn("Archive mode preserves universal policy", self.getting_started)
        self.assertIn("cannot install or remove it", self.getting_started)

    def test_upgrade_archival_is_conditional_and_refreshes_capabilities(self) -> None:
        self.assertIn("Test-Path -LiteralPath $LegacyRoot -PathType Container", self.getting_started)
        self.assertIn("RefreshCapabilityIndex = $true", self.getting_started)
        self.assertNotIn("-ArchiveLegacyState `", self.getting_started)

    def test_campaign_example_is_complete_and_model_valid(self) -> None:
        raw = json.loads(self.campaign_example)
        spec = CampaignSpec.from_dict(raw)
        spec.verify_digest()
        self.assertEqual(spec.protocol_version, "ccos-campaign-v1")
        self.assertEqual(len(spec.required_review_cohort), 2)
        self.assertEqual(
            {item.token.value for item in spec.attempt_budgets},
            {
                "CHILD_CREATION",
                "CHILD_START",
                "VALIDATION_EXECUTION",
                "REVIEW_DISPATCH",
                "REPAIR_DISPATCH",
                "CLOSURE_DISPATCH",
                "HOSTED_CHECK_WAKEUP",
                "TRANSPORT_RETRY",
                "RECONCILIATION",
                "PUSH",
                "PULL_REQUEST_CREATION",
                "COMMENT",
                "MERGE",
                "REJECTED_ATTEMPT",
                "NO_OP_ATTEMPT",
            },
        )
        self.assertEqual(
            raw["publication_authority"]["human_authorization"]["algorithm"],
            "ED25519",
        )
        self.assertNotEqual(
            raw["publication_authority"]["human_authorization"][
                "public_key_base64"
            ],
            "iojj3XQJ8ZX9UtstPLpdcspnCb8dlBIb83SIAbQPb1w=",
        )

    def test_campaign_example_satisfies_the_public_admission_contract(self) -> None:
        raw = json.loads(self.campaign_example)
        exact_root = str(self.repo_root.resolve())
        raw["git_root"] = exact_root
        raw["worktree"] = exact_root
        for command in raw["required_validation_commands"]:
            command["working_directory"] = exact_root

        repository_evidence = mock.Mock()
        repository_evidence.to_dict.return_value = {"head_sha": raw["base_sha"]}
        runtime_evidence = mock.Mock()
        runtime_evidence.to_dict.return_value = {
            "source_commit": raw["installed_source_commit"]
        }
        with (
            mock.patch.object(
                admission,
                "resolve_repository",
                return_value=repository_evidence,
            ),
            mock.patch.object(
                admission,
                "verify_installed_runtime",
                return_value=runtime_evidence,
            ),
        ):
            evidence = admission.admit_campaign_spec(
                raw,
                installed_root=self.repo_root,
            )

        self.assertEqual(
            evidence["human_authorization_verifier"]["algorithm"],
            "ED25519",
        )
        self.assertRegex(
            evidence["human_authorization_verifier"]["public_key_sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_capability_catalogue_matches_bundled_skill_manifest(self) -> None:
        section = self.capability_catalogue.split("## Bundled Full Local Skills", 1)[1]
        section = section.split("## External Reference Repositories", 1)[0]
        listed = set(re.findall(r"^- `([^`]+)`$", section, re.MULTILINE))
        declared = {item["name"] for item in self.pack_manifest["bundled_skills"]}
        self.assertEqual(listed, declared)

    def test_history_scanner_allowlist_is_exact_and_auditable(self) -> None:
        self.assertEqual(self.trufflehog_allowlist["schema_version"], 1)
        self.assertEqual(
            self.trufflehog_allowlist["scope"], "immutable-git-history-only"
        )
        keys: set[tuple[str, str, str, bool, str]] = set()
        for entry in self.trufflehog_allowlist["entries"]:
            key = (
                entry["detector"],
                entry["path"],
                entry["commit"],
                entry["expected_verified"],
                entry["raw_sha256"],
            )
            self.assertNotIn(key, keys)
            keys.add(key)
            self.assertRegex(entry["path"], r"^tests/[0-9A-Za-z._/-]+$")
            self.assertNotIn("..", entry["path"])
            self.assertNotIn("*", entry["path"])
            self.assertRegex(entry["commit"], r"^[0-9a-f]{40}$")
            self.assertIsInstance(entry["expected_verified"], bool)
            self.assertRegex(entry["raw_sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(len(entry["reason"]), 40)
        for marker in (
            "trufflehog_result_gate.py",
            "--mode current",
            "--mode history",
            "--branch=$CandidateHead",
            "--concurrency=1",
            "--candidate-head $CandidateHead",
            "--fail-on-scan-errors",
            "exact local candidate ancestry",
            "history scan requires a clean committed candidate working tree",
        ):
            self.assertIn(marker, self.release_safety_script)
        self.assertNotIn("history scan target: origin remote", self.release_safety_script)
        self.assertNotIn("Write-Output $TruffleHogOutput", self.release_safety_script)
        runtime_files = set(self.pack_manifest["installation"]["runtime_files"])
        self.assertIn("scripts/trufflehog_result_gate.py", runtime_files)
        self.assertIn(
            "scripts/release-safety-trufflehog-allowlist.json",
            runtime_files,
        )

    def test_release_install_receipt_fields_are_checked_after_install(self) -> None:
        self.assertIn("install-manifest.json", self.publishing_checklist)
        self.assertIn("runtime_installations", self.publishing_checklist)
        self.assertNotIn("package `runtime_pin`", self.publishing_checklist)


if __name__ == "__main__":
    unittest.main()
