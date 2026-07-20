"""Regression checks for public install and session-helper documentation."""

from __future__ import annotations

from pathlib import Path
import unittest


class PublicInstallDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.getting_started = (cls.repo_root / "docs" / "getting-started.md").read_text(encoding="utf-8")
        cls.readme = (cls.repo_root / "README.md").read_text(encoding="utf-8")
        cls.public_docs = f"{cls.readme}\n{cls.getting_started}"

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


if __name__ == "__main__":
    unittest.main()
