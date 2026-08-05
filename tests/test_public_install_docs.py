"""Public installation contract checks for the single campaign engine runtime."""

from __future__ import annotations

from pathlib import Path
import unittest


class PublicInstallDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.getting_started = (cls.repo_root / "docs" / "getting-started.md").read_text(
            encoding="utf-8"
        )
        cls.readme = (cls.repo_root / "README.md").read_text(encoding="utf-8")
        cls.publishing_checklist = (
            cls.repo_root / "docs" / "publishing-checklist.md"
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
        self.assertIn("exact clean source commit", self.public_docs.lower())
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
        self.assertIn("package.ps1", self.publishing_checklist)
        self.assertIn("install-uninstall-smoke.ps1", self.publishing_checklist)


if __name__ == "__main__":
    unittest.main()
