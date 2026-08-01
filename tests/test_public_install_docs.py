"""Regression checks for public install and session-helper documentation."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


class PublicInstallDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.getting_started = (cls.repo_root / "docs" / "getting-started.md").read_text(encoding="utf-8")
        cls.readme = (cls.repo_root / "README.md").read_text(encoding="utf-8")
        cls.publishing_checklist = (cls.repo_root / "docs" / "publishing-checklist.md").read_text(
            encoding="utf-8"
        )
        cls.public_docs = f"{cls.readme}\n{cls.getting_started}"

    @classmethod
    def readme_section(cls, heading: str, next_heading: str) -> str:
        start = cls.readme.index(heading)
        end = cls.readme.index(next_heading, start)
        return cls.readme[start:end]

    def test_getting_started_uses_the_bundled_session_helper(self) -> None:
        helper = self.repo_root / ".agents" / "skills" / "project-session-continuity" / "scripts" / "session_continuity.py"
        documented = ".agents/skills/project-session-continuity/scripts/session_continuity.py"

        self.assertTrue(helper.is_file(), f"missing documented helper: {helper}")
        self.assertIn(f"python {documented} start --profile auto --start-new", self.getting_started)
        self.assertIn(f"python {documented} summary --profile auto --json", self.getting_started)
        self.assertNotIn("python scripts/agent/session_continuity.py", self.getting_started)

    def test_public_archive_and_source_commands_match_the_installer_contract(self) -> None:
        powershell_bundle_expression = (
            "$ExpectedBundleSha256 = (Get-Content -Raw -LiteralPath "
            ".\\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256"
        )
        powershell_archive_command = ".\\scripts\\install.ps1 -ExpectedBundleSha256 $ExpectedBundleSha256 -ArchiveMode"
        shell_archive_command = (
            './scripts/install.sh --expected-bundle-sha256 "$expected_bundle_sha256" --archive-mode'
        )

        self.assertIn(powershell_bundle_expression, self.public_docs)
        self.assertIn(powershell_archive_command, self.public_docs)
        self.assertIn("expected_bundle_sha256=", self.public_docs)
        self.assertIn(shell_archive_command, self.public_docs)
        self.assertIn("$ExpectedSourceCommit = git rev-parse HEAD", self.readme)
        self.assertIn("-ExpectedSourceCommit $ExpectedSourceCommit", self.readme)
        source_shell_contract = (
            'python_cmd="$(command -v python3 || command -v python)"\n'
            'expected_bundle_sha256="$("$python_cmd" -c \'import json; '
            'print(json.load(open("install-bundle.manifest.json", encoding="utf-8"))["aggregate_sha256"])\')"\n'
            'expected_source_commit="$(git rev-parse HEAD)"\n'
            './scripts/install.sh --expected-bundle-sha256 "$expected_bundle_sha256" '
            '--expected-source-commit "$expected_source_commit"'
        )
        self.assertIn(source_shell_contract, self.readme)
        self.assertNotIn("-InstallGlobalAgents", self.public_docs)
        self.assertNotIn("--install-global-agents", self.public_docs)
        self.assertIn("separately authorized operation", self.public_docs)
        self.assertIn("Archive mode rejects it", self.public_docs)

        install_ps1 = (self.repo_root / "scripts" / "install.ps1").read_text(encoding="utf-8")
        install_sh = (self.repo_root / "scripts" / "install.sh").read_text(encoding="utf-8")
        transaction_engine = (self.repo_root / "scripts" / "install_transaction.py").read_text(encoding="utf-8")
        self.assertIn("[Parameter(Mandatory = $true)][string]$ExpectedBundleSha256", install_ps1)
        self.assertIn('"--expected-bundle-sha256", $ExpectedBundleSha256', install_ps1)
        self.assertIn('[[ -n "$expected_bundle" ]] || { echo "--expected-bundle-sha256 is required"', install_sh)
        self.assertIn("if options.archive_mode:", transaction_engine)
        self.assertIn('raise AuthorityError("archive mode cannot synchronize universal policy")', transaction_engine)

    def test_windows_source_checkout_sets_process_execution_policy(self) -> None:
        section = self.readme_section("### Windows source checkout", "### macOS or Linux archive")

        self.assertIn(
            "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass",
            section,
        )

    def test_posix_source_checkout_marks_installers_executable(self) -> None:
        section = self.readme_section("### macOS or Linux source checkout", "### Confirm the normal install")

        self.assertIn(
            "chmod +x ./scripts/install.sh ./scripts/uninstall.sh",
            section,
        )

    def test_smoke_commands_use_cross_platform_path_separators(self) -> None:
        self.assertIn("python tests/workflow-gates-smoke.py", self.readme)
        self.assertIn("python tests/worktree-lanes-smoke.py", self.readme)
        self.assertNotIn(r"python tests\workflow-gates-smoke.py", self.readme)
        self.assertNotIn(r"python tests\worktree-lanes-smoke.py", self.readme)

    def test_current_status_uses_live_authorities(self) -> None:
        section = self.readme_section("## Current source status", "## Contents")
        self.assertIn("https://github.com/AymanShams/codex-coding-os/releases", section)
        self.assertIn("https://github.com/AymanShams/codex-coding-os/pulls", section)
        self.assertIn("pack.manifest.json", section)
        self.assertIn("install-bundle.manifest.json", section)
        self.assertNotRegex(section, r"(?i)latest published[^\n]*`v\d+\.\d+\.\d+`")
        self.assertNotRegex(section, r"(?i)through pull request \d+")

    def test_package_inventory_contains_no_numeric_snapshot(self) -> None:
        section = self.readme_section("## Package inventory", "## Installation")
        mutable_count_row = re.compile(
            r"(?im)^\|\s*(?:Tracked files|Bundled skills|Required manifest paths|Support items|Templates|"
            r"Documentation files under `docs/`|Files under `scripts/`|Test files under `tests/`|"
            r"Install bundle entries)\s*\|\s*\d"
        )
        self.assertIsNone(mutable_count_row.search(section))
        mutable_count_prose = re.compile(
            r"(?i)\b(?:\d[\d,]*|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
            r"\s*(?:-\s*)?(?:bundled\s+)?(?:skills?|capabilit(?:y|ies))\b"
        )
        self.assertIsNone(mutable_count_prose.search(section))
        self.assertIn("git ls-files", section)
        self.assertIn("pack.manifest.json", section)
        self.assertIn("install-bundle.manifest.json", section)

    def test_publishing_checklist_requires_readme_drift_checks(self) -> None:
        self.assertIn("tests\\test_documentation_contracts.py", self.publishing_checklist)
        self.assertIn("instead of hardcoding a latest release tag", self.publishing_checklist)
        self.assertIn("Open GitHub Releases directly", self.publishing_checklist)

    def test_legacy_overlap_migration_is_explicit_in_docs_and_powershell_adapters(self) -> None:
        install_ps1 = (self.repo_root / "scripts" / "install.ps1").read_text(encoding="utf-8")
        uninstall_ps1 = (self.repo_root / "scripts" / "uninstall.ps1").read_text(encoding="utf-8")
        transaction_engine = (self.repo_root / "scripts" / "install_transaction.py").read_text(encoding="utf-8")

        self.assertIn("-LegacyOverlapMigration", self.getting_started)
        self.assertIn("-UniversalBundleId", self.getting_started)
        self.assertIn("does not treat nested files as proven owned", self.getting_started)
        self.assertIn("when a recorded v2 skill is no longer bundled", self.getting_started)
        self.assertIn("[switch]$LegacyOverlapMigration", install_ps1)
        self.assertIn('"--legacy-overlap-migration"', install_ps1)
        self.assertIn("[string]$UniversalBundleId", install_ps1)
        self.assertIn('"--universal-bundle-id", $UniversalBundleId', install_ps1)
        self.assertIn("[switch]$LegacyOverlapMigration", uninstall_ps1)
        self.assertIn('"--legacy-overlap-migration"', uninstall_ps1)
        self.assertIn('install_parser.add_argument("--legacy-overlap-migration", action="store_true")', transaction_engine)
        self.assertIn('uninstall_parser.add_argument("--legacy-overlap-migration", action="store_true")', transaction_engine)
        self.assertIn('install_parser.add_argument("--universal-bundle-id", default=UNIVERSAL_BUNDLE_ID)', transaction_engine)
        self.assertIn("def _validate_legacy_v2_skill_descendants(", transaction_engine)


if __name__ == "__main__":
    unittest.main()
