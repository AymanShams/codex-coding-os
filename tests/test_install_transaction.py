from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "install_transaction.py"
SPEC = importlib.util.spec_from_file_location("install_transaction", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
it = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = it
SPEC.loader.exec_module(it)


AGENTS_LEGACY = (
    "  - Manual Session And Case Isolation Policy: parent-orchestrator mode and automatic session, review, "
    "and review-fix trains are disabled. A human may deliberately start one bounded implementation or review "
    "session, but no session may automatically spawn, authorize, or chain another session."
)
AGENTS_POLICY = """<!-- BEGIN CODEX CODING OS MANAGED: AUTOMATION-PRESERVING CASE POLICY -->
  - Automation-Preserving Case Orchestration Policy: Parent orchestration and child agents are enabled only inside an explicitly approved run envelope bound to one canonical case ID. The parent is administrative only. A case permits one implementation generation, one frozen-head review cohort, at most one explicitly authorized combined repair, and one closure check. A new chat, branch, worktree, pull request, commit counter, or child session cannot reset the case. The case-state engine is the lifecycle authority and prose files are mirrors. A stop or red lock is case-scoped and unrelated work remains available.
<!-- END CODEX CODING OS MANAGED: AUTOMATION-PRESERVING CASE POLICY -->"""
RULES_LEGACY = 'prefix_rule(pattern=["gh", "pr", "merge"], decision="allow")'
RULES_POLICY = """# BEGIN CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY
prefix_rule(
    pattern = ["gh", "pr", "merge"],
    decision = "prompt",
    justification = "Pull request merge requires explicit authority for the exact repository, pull request, and reviewed head.",
)
# END CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="")


class SyntheticEnvironment:
    def __init__(self, root: Path, *, git_source: bool = False) -> None:
        self.root = root
        self.source = root / "source"
        self.skills = root / "skills"
        self.codex = root / "codex-home"
        self.state = root / "case-state"
        self.case_engine = root / "fake_case_engine.py"
        self.case_id = str(uuid.uuid4())
        self.repository = "https://example.invalid/synthetic/coding-os"
        self.source.mkdir(parents=True)
        write_text(self.source / ".agents/skills/alpha/SKILL.md", "---\nname: alpha\ndescription: synthetic\n---\n")
        write_text(self.source / "payload/doc.txt", "payload-v1\n")
        write_text(self.source / "scripts/install_transaction.py", "# synthetic runtime\n")
        write_text(
            self.source / "scripts/fake_refresh.py",
            "import os, sys\nsys.exit(int(os.environ.get('CCOS_SYNTHETIC_REFRESH_EXIT', '0')))\n",
        )
        write_text(self.source / "universal/AGENTS.automation-case-policy.md", AGENTS_POLICY + "\n")
        write_text(self.source / "universal/rules/gh-pr-merge-authority.rules", RULES_POLICY + "\n")
        self.pack = {
            "version": "0.9.0",
            "package_name": "codex-coding-os",
            "support_items": [
                "payload",
                "scripts/install_transaction.py",
                "scripts/fake_refresh.py",
                "universal",
                "pack.manifest.json",
                "install-bundle.manifest.json",
            ],
            "bundled_skills": [
                {"name": "alpha", "category": "synthetic", "required": True, "source": "local"}
            ],
            "installation": {
                "transaction_protocol": "ccos-install-transaction-v1",
                "bundle_protocol": "CCOS-INSTALL-BUNDLE-v1",
                "bundle_manifest": "install-bundle.manifest.json",
                "managed_skill_root": ".agents/skills",
                "runtime_files": ["scripts/install_transaction.py", "scripts/fake_refresh.py"],
                "universal_policy_sources": {
                    "global_agents": "universal/AGENTS.automation-case-policy.md",
                    "default_rules": "universal/rules/gh-pr-merge-authority.rules",
                },
                "capability_refresh_cli": "scripts/fake_refresh.py",
                "external_skills_staged": False,
            },
        }
        write_text(self.source / "pack.manifest.json", json.dumps(self.pack, indent=2) + "\n")
        self.bundle = it.build_bundle_manifest(self.source)
        self.bundle_hash = self.bundle["aggregate_sha256"]
        self.commit: str | None = None
        if git_source:
            run_git(self.source, "init", "-q")
            run_git(self.source, "config", "user.email", "synthetic@example.invalid")
            run_git(self.source, "config", "user.name", "Synthetic Test")
            run_git(self.source, "remote", "add", "origin", self.repository + ".git")
            run_git(self.source, "add", ".")
            run_git(self.source, "commit", "-q", "-m", "synthetic bundle")
            self.commit = run_git(self.source, "rev-parse", "HEAD")
            self._write_case_authority(allowed=False)

    def _write_case_authority(
        self,
        *,
        allowed: bool,
        approved_head: str | None = None,
        universal_bundle: str = "automation-preserving-case-state-recovery-v1",
        state: str = "CLOSED_SUCCESS",
    ) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        action = {
            "protocol_version": "ccos-case-action-v1",
            "schema_version": 2,
            "case_id": self.case_id,
            "state": state,
            "action": "universal_sync",
            "actor_role": "publication_child",
            "repository": self.repository,
            "head": approved_head or self.commit,
            "context": {
                "actor_role": "publication_child",
                "repository": self.repository,
                "branch": None,
                "worktree": None,
                "pr": None,
                "thread": None,
                "universal_bundle": universal_bundle,
                "head": approved_head or self.commit,
            },
            "limits": {},
            "allowed": allowed,
            "reason_codes": ["SEPARATE_AUTHORITY_REQUIRED"] if not allowed else [],
            "reason": "universal_sync requires separately recorded external authority",
            "separate_authority_required": not allowed,
            "blocked_case_id": None,
        }
        show = {"case_id": self.case_id, "state": state}
        write_text(self.state / "authority.json", json.dumps(action))
        write_text(self.state / "show.json", json.dumps(show))
        write_text(
            self.case_engine,
            """import json, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(args[args.index('--state-root') + 1])
if 'action-check' in args:
    (root / 'last-action-check.json').write_text(json.dumps(args), encoding='utf-8')
name = 'show.json' if 'show' in args else 'authority.json'
print(json.dumps(json.loads((root / name).read_text(encoding='utf-8'))))
""",
        )

    def archive_options(self, **overrides: object) -> object:
        values = dict(
            source_root=self.source,
            skills_root=self.skills,
            codex_home=self.codex,
            expected_bundle_sha256=self.bundle_hash,
            archive_mode=True,
        )
        values.update(overrides)
        return it.InstallOptions(**values)

    def policy_options(self, **overrides: object) -> object:
        assert self.commit is not None
        values = dict(
            source_root=self.source,
            skills_root=self.skills,
            codex_home=self.codex,
            expected_bundle_sha256=self.bundle_hash,
            expected_source_commit=self.commit,
            install_universal_policy=True,
            authority_case_id=self.case_id,
            authority_source="preauthorized-run-envelope",
            authority_reference="synthetic-approved-run",
            case_state_engine=self.case_engine,
            case_state_root=self.state,
        )
        values.update(overrides)
        return it.InstallOptions(**values)

    def prepare_legacy_policy(self) -> tuple[bytes, bytes]:
        agents = b"alpha\r\n" + AGENTS_LEGACY.encode() + b"\npost\r\n"
        rules = b"rule-before\n" + RULES_LEGACY.encode() + b"\r\nrule-after\n"
        self.codex.mkdir(parents=True, exist_ok=True)
        (self.codex / "rules").mkdir(parents=True, exist_ok=True)
        (self.codex / "AGENTS.md").write_bytes(agents)
        (self.codex / "rules/default.rules").write_bytes(rules)
        return agents, rules

    def prepare_legacy_overlap_v2(
        self,
        *,
        package: str = "codex-coding-os-starter",
        manifest_overrides: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
        skills = self.codex / "skills"
        support = self.codex / "coding-os-starter"
        write_text(skills / "alpha/SKILL.md", "legacy-alpha\n")
        write_text(support / "legacy-support.txt", "legacy-support\n")
        manifest: dict[str, object] = {
            "manifest_version": 2,
            "package": package,
            "skills_root": str(skills),
            "codex_home": str(self.codex),
            "support_root": str(support),
            "skills": [{"name": "alpha", "path": str(skills / "alpha")}],
        }
        if manifest_overrides:
            manifest.update(manifest_overrides)
        write_text(support / "install-manifest.json", json.dumps(manifest, indent=2) + "\n")
        return skills, support

    def legacy_overlap_options(self, **overrides: object) -> object:
        values = dict(skills_root=self.codex / "skills", legacy_overlap_migration=True)
        values.update(overrides)
        return self.archive_options(**values)


class BundleContractTests(unittest.TestCase):
    def test_transaction_protocol_module_exists(self) -> None:
        self.assertEqual(it.TRANSACTION_PROTOCOL, "ccos-install-transaction-v1")

    def test_bundle_manifest_uses_relative_paths_and_exact_aggregate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            manifest = json.loads((env.source / "install-bundle.manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["protocol"], "CCOS-INSTALL-BUNDLE-v1")
            self.assertNotIn("install-bundle.manifest.json", [entry["path"] for entry in manifest["entries"]])
            self.assertTrue(all(not Path(entry["path"]).is_absolute() for entry in manifest["entries"]))
            prefix = b"CCOS-INSTALL-BUNDLE-v1\0"
            digest = hashlib.sha256()
            digest.update(prefix)
            for entry in sorted(manifest["entries"], key=lambda item: item["path"].encode("utf-8")):
                digest.update(entry["path"].encode("utf-8"))
                digest.update(b"\0")
                digest.update(str(entry["size"]).encode("ascii"))
                digest.update(b"\0")
                digest.update(bytes.fromhex(entry["sha256"]))
            self.assertEqual(manifest["aggregate_sha256"], digest.hexdigest())
            verified = it.verify_bundle(env.source, env.bundle_hash)
            self.assertEqual(verified.aggregate_sha256, env.bundle_hash)

    def test_bundle_rejects_tampering_traversal_and_case_collisions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            (env.source / "payload/doc.txt").write_text("tampered", encoding="utf-8")
            with self.assertRaises(it.BundleError):
                it.verify_bundle(env.source, env.bundle_hash)

        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            manifest_path = env.source / "install-bundle.manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["entries"][0]["path"] = "../escape"
            write_text(manifest_path, json.dumps(manifest))
            with self.assertRaises(it.BundleError):
                it.verify_bundle(env.source)

        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            manifest_path = env.source / "install-bundle.manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            duplicate = dict(manifest["entries"][0])
            duplicate["path"] = duplicate["path"].swapcase()
            manifest["entries"].append(duplicate)
            write_text(manifest_path, json.dumps(manifest))
            with self.assertRaises(it.BundleError):
                it.verify_bundle(env.source)

    def test_bundle_rejects_links_and_undeclared_managed_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            link = env.source / "payload/link.txt"
            try:
                link.symlink_to(env.source / "payload/doc.txt")
            except OSError:
                self.skipTest("symlink creation is unavailable")
            with self.assertRaises(it.BundleError):
                it.build_bundle_manifest(env.source)

        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            write_text(env.source / ".agents/skills/alpha/undeclared.txt", "late")
            with self.assertRaises(it.BundleError):
                it.verify_bundle(env.source, env.bundle_hash)

    def test_bundle_ignores_ignored_runtime_artifacts_and_rejects_untracked_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            write_text(env.source / ".gitignore", "payload/*.runtime\n")
            run_git(env.source, "add", ".gitignore")
            run_git(env.source, "commit", "-q", "-m", "ignore local runtime artifacts")
            write_text(env.source / "scripts/__pycache__/install_transaction.cpython-312.pyc", "cache")
            write_text(env.source / "payload/local.runtime", "ignored")

            verified = it.verify_bundle(env.source, env.bundle_hash)
            self.assertEqual(verified.aggregate_sha256, env.bundle_hash)
            self.assertFalse(any("__pycache__" in entry["path"] for entry in verified.entries))
            self.assertFalse(any(entry["path"] == "payload/local.runtime" for entry in verified.entries))

            write_text(env.source / "payload/untracked.txt", "unexpected")
            with self.assertRaisesRegex(it.BundleError, "untracked pack-owned paths"):
                it.build_bundle_manifest(env.source)
            with self.assertRaises(it.BundleError):
                it.verify_bundle(env.source, env.bundle_hash)


class PolicyMigrationTests(unittest.TestCase):
    def test_first_migrations_preserve_all_outside_bytes_and_mixed_newlines(self) -> None:
        agents = b"pre\r\n" + AGENTS_LEGACY.encode() + b"\npost\rmore"
        migrated = it.migrate_agents_bytes(agents, (AGENTS_POLICY + "\n").encode())
        self.assertEqual(migrated, b"pre\r\n" + AGENTS_POLICY.encode() + b"\npost\rmore")
        rules = b"before\n" + RULES_LEGACY.encode() + b"\r\nafter\n"
        migrated_rules = it.migrate_rules_bytes(rules, (RULES_POLICY + "\n").encode())
        self.assertEqual(migrated_rules, b"before\n" + RULES_POLICY.encode() + b"\r\nafter\n")

    def test_later_migration_replaces_exact_marker_block_only(self) -> None:
        old_agents = AGENTS_POLICY.replace("one closure check", "one old closure check").encode()
        existing = b"pre\n" + old_agents + b"\r\npost"
        updated = it.migrate_agents_bytes(existing, AGENTS_POLICY.encode())
        self.assertEqual(updated, b"pre\n" + AGENTS_POLICY.encode() + b"\r\npost")
        old_rules = RULES_POLICY.replace('decision = "prompt"', 'decision = "forbidden"').encode()
        rules = b"x\r\n" + old_rules + b"\ny"
        self.assertEqual(
            it.migrate_rules_bytes(rules, RULES_POLICY.encode()),
            b"x\r\n" + RULES_POLICY.encode() + b"\ny",
        )

    def test_missing_duplicate_partial_and_invalid_utf8_fail_closed(self) -> None:
        bad_values = [
            b"no legacy or marker",
            (AGENTS_LEGACY + "\n" + AGENTS_LEGACY).encode(),
            b"<!-- BEGIN CODEX CODING OS MANAGED: AUTOMATION-PRESERVING CASE POLICY -->\npartial",
            b"\xff" + AGENTS_LEGACY.encode(),
        ]
        for value in bad_values:
            with self.subTest(value=value[:20]), self.assertRaises(it.PolicyMigrationError):
                it.migrate_agents_bytes(value, AGENTS_POLICY.encode())
        rules_bad = [
            b"no merge rule",
            (RULES_LEGACY + "\n" + RULES_LEGACY).encode(),
            b"# END CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY",
        ]
        for value in rules_bad:
            with self.subTest(value=value[:20]), self.assertRaises(it.PolicyMigrationError):
                it.migrate_rules_bytes(value, RULES_POLICY.encode())

    def test_uninstall_removes_only_exact_blocks(self) -> None:
        agents = b"pre\n" + AGENTS_POLICY.encode() + b"\r\npost"
        self.assertEqual(it.remove_agents_policy_bytes(agents), b"pre\n\r\npost")
        rules = b"pre\r\n" + RULES_POLICY.encode() + b"\npost"
        self.assertEqual(it.remove_rules_policy_bytes(rules), b"pre\r\n\npost")


class RepositoryNormalizationTests(unittest.TestCase):
    def test_normalizes_https_userinfo_and_scp_repository_forms(self) -> None:
        expected = "https://github.com/aymanshams/codex-coding-os"
        cases = [
            "https://github.com/AymanShams/Codex-Coding-OS.git",
            "https://AymanShams@github.com/AymanShams/codex-coding-os.git",
            "HTTPS://AymanShams@GitHub.Com:443//AymanShams///Codex-Coding-OS.git/",
            "https://github.com/%41ymanShams/%43odex-Coding-OS.git",
            "git@GitHub.com:AymanShams/Codex-Coding-OS.git",
        ]
        for remote in cases:
            with self.subTest(remote=remote):
                self.assertEqual(it._normalize_repository(remote), expected)

    def test_rejects_malformed_or_unsupported_repository_forms(self) -> None:
        invalid = [
            "https://github.com/AymanShams",
            "https://github.com/AymanShams/../codex-coding-os",
            "https://github.com/AymanShams%2F..%2Fcodex-coding-os",
            "https://github.com/AymanShams/codex-coding-os?ref=main",
            "https://github.com:not-a-port/AymanShams/codex-coding-os",
            "http://github.com/AymanShams/codex-coding-os.git",
            "https://AymanShams:password@github.com/AymanShams/codex-coding-os.git",
            "https://AymanShams:@github.com/AymanShams/codex-coding-os.git",
            "https://github.com:22/AymanShams/codex-coding-os.git",
            "https://github.com:80/AymanShams/codex-coding-os.git",
            "https://github.com:444/AymanShams/codex-coding-os.git",
            "https://github.com:/AymanShams/codex-coding-os.git",
            "https://[::1/AymanShams/codex-coding-os.git",
            "ssh://git@github.com/AymanShams/codex-coding-os.git",
            "git@bad host:AymanShams/codex-coding-os.git",
            "git@github.com",
        ]
        for remote in invalid:
            with self.subTest(remote=remote), self.assertRaises(it.SourceVerificationError):
                it._normalize_repository(remote)


class InstallTransactionTests(unittest.TestCase):
    def test_fresh_archive_install_writes_v3_provenance_and_preserves_unmanaged_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            env.codex.mkdir(parents=True)
            env.skills.mkdir(parents=True)
            write_text(env.codex / "config.toml", "keep-config\n")
            write_text(env.codex / "case-state/case.json", "keep-case\n")
            write_text(env.codex / "plugins/plugin.txt", "keep-plugin\n")
            write_text(env.skills / "unmanaged/SKILL.md", "keep-unmanaged\n")
            before = {path: sha(path) for path in [
                env.codex / "config.toml",
                env.codex / "case-state/case.json",
                env.codex / "plugins/plugin.txt",
                env.skills / "unmanaged/SKILL.md",
            ]}
            result = it.install(env.archive_options())
            self.assertEqual(result["status"], "committed")
            manifest_path = env.codex / "coding-os/install-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["manifest_version"], 3)
            self.assertEqual(manifest["transaction_protocol"], it.TRANSACTION_PROTOCOL)
            self.assertEqual(manifest["package"]["bundle_sha256"], env.bundle_hash)
            self.assertEqual(manifest["source"]["kind"], "archive")
            self.assertFalse(manifest["preserved_paths"]["case_state"]["managed"])
            current = json.loads((env.codex / ".coding-os-install/current.json").read_text(encoding="utf-8"))
            self.assertEqual(current["status"], "committed")
            self.assertEqual(current["install_manifest_sha256"], sha(manifest_path))
            self.assertTrue((env.skills / "alpha/SKILL.md").is_file())
            self.assertTrue((env.codex / "coding-os/payload/doc.txt").is_file())
            self.assertEqual(before, {path: sha(path) for path in before})

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            first = it.install(env.archive_options())
            current_path = env.codex / ".coding-os-install/current.json"
            before = current_path.read_bytes()
            second = it.install(env.archive_options())
            self.assertEqual(first["status"], "committed")
            self.assertEqual(second["status"], "already_committed")
            self.assertEqual(current_path.read_bytes(), before)

    def test_unowned_skill_collision_fails_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            write_text(env.skills / "alpha/owner.txt", "not ours")
            with self.assertRaises(it.OwnershipError):
                it.install(env.archive_options())
            self.assertEqual((env.skills / "alpha/owner.txt").read_text(), "not ours")
            self.assertFalse((env.codex / "coding-os").exists())

    def test_case_insensitive_unowned_collision_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            write_text(env.skills / "ALPHA/owner.txt", "not ours")
            with self.assertRaises(it.OwnershipError):
                it.install(env.archive_options())

    def test_v2_upgrade_fails_closed_for_a_no_longer_bundled_managed_skill(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            write_text(env.skills / "obsolete/SKILL.md", "obsolete")
            write_text(env.skills / "obsolete/local-notes.md", "keep-local-notes")
            support = env.codex / "coding-os"
            support.mkdir(parents=True)
            v2 = {
                "package": "codex-coding-os",
                "skills_root": str(env.skills),
                "support_root": str(support),
                "skills": [{"name": "obsolete", "path": str(env.skills / "obsolete")}],
            }
            write_text(support / "install-manifest.json", json.dumps(v2))
            preserved = {
                env.skills / "obsolete/SKILL.md": sha(env.skills / "obsolete/SKILL.md"),
                env.skills / "obsolete/local-notes.md": sha(env.skills / "obsolete/local-notes.md"),
            }

            with self.assertRaisesRegex(it.OwnershipError, "no longer bundled"):
                it.install(env.archive_options())

            self.assertEqual(preserved, {path: sha(path) for path in preserved})
            self.assertFalse((env.codex / ".coding-os-install/current.json").exists())

    def test_policy_sync_requires_clean_exact_git_source_bundle_and_case_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            with self.assertRaises(it.SourceVerificationError):
                it.install(env.policy_options(expected_source_commit="0" * 40))
            with self.assertRaises(it.BundleError):
                it.install(env.policy_options(expected_bundle_sha256="0" * 64))
            write_text(env.source / "untracked.txt", "dirty")
            with self.assertRaises(it.SourceVerificationError):
                it.install(env.policy_options())
            (env.source / "untracked.txt").unlink()
            env._write_case_authority(allowed=True)
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options())

    def test_policy_sync_migrates_only_agents_and_rules_and_records_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            original_agents, original_rules = env.prepare_legacy_policy()
            config = env.codex / "config.toml"
            write_text(config, "never-write\n")
            config_hash = sha(config)
            result = it.install(env.policy_options())
            self.assertEqual(result["status"], "committed")
            agents = (env.codex / "AGENTS.md").read_bytes()
            rules = (env.codex / "rules/default.rules").read_bytes()
            self.assertEqual(agents, original_agents.replace(AGENTS_LEGACY.encode(), AGENTS_POLICY.encode()))
            self.assertEqual(rules, original_rules.replace(RULES_LEGACY.encode(), RULES_POLICY.encode()))
            self.assertEqual(sha(config), config_hash)
            manifest = json.loads((env.codex / "coding-os/install-manifest.json").read_text())
            self.assertEqual(manifest["authority"]["case_id"], env.case_id)
            self.assertEqual(manifest["authority"]["approved_head"], env.commit)
            self.assertEqual(manifest["authority"]["source"], "preauthorized-run-envelope")
            self.assertEqual(manifest["authority"]["reference"], "synthetic-approved-run")
            self.assertEqual(manifest["authority"]["boundary_reason"], "SEPARATE_AUTHORITY_REQUIRED")
            self.assertEqual(manifest["source"]["git_commit"], env.commit)
            self.assertTrue(manifest["source"]["working_tree_clean"])

    def test_policy_sync_matches_canonical_authority_for_credentialed_mixed_case_origin(self) -> None:
        bundle_id = "automation-preserving-case-state-recovery-v1-remote-normalization"
        canonical_repository = "https://github.com/aymanshams/codex-coding-os"
        credentialed_origin = "https://AymanShams@github.com/AymanShams/codex-coding-os.git"
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            run_git(env.source, "remote", "set-url", "origin", credentialed_origin)
            env.repository = canonical_repository
            env._write_case_authority(allowed=False, universal_bundle=bundle_id)

            result = it.install(env.policy_options(universal_bundle_id=bundle_id))

            self.assertEqual(result["status"], "committed")
            request = json.loads((env.state / "last-action-check.json").read_text(encoding="utf-8"))
            self.assertEqual(request[request.index("--repository") + 1], canonical_repository)
            manifest = json.loads((env.codex / "coding-os/install-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source"]["repository"], canonical_repository)

    def test_policy_sync_missing_migration_target_fails_before_live_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.codex.mkdir(parents=True)
            write_text(env.codex / "AGENTS.md", "no legacy")
            write_text(env.codex / "rules/default.rules", RULES_LEGACY)
            with self.assertRaises(it.PolicyMigrationError):
                it.install(env.policy_options())
            self.assertEqual((env.codex / "AGENTS.md").read_text(), "no legacy")
            self.assertFalse((env.codex / "coding-os").exists())

    def test_policy_sync_requires_explicit_external_authority_reference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options(authority_source=None))
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options(authority_reference=""))

    def test_capability_refresh_failure_rolls_back_all_live_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            it.install(env.archive_options())
            old_skill = sha(env.skills / "alpha/SKILL.md")
            old_support = (env.codex / "coding-os/payload/doc.txt").read_bytes()
            write_text(env.source / "payload/doc.txt", "payload-v2\n")
            env.bundle = it.build_bundle_manifest(env.source)
            env.bundle_hash = env.bundle["aggregate_sha256"]
            with mock.patch.dict(os.environ, {"CCOS_SYNTHETIC_REFRESH_EXIT": "7"}):
                with self.assertRaises(it.TransactionError):
                    it.install(env.archive_options(refresh_capability_index=True))
            self.assertEqual(sha(env.skills / "alpha/SKILL.md"), old_skill)
            self.assertEqual((env.codex / "coding-os/payload/doc.txt").read_bytes(), old_support)

    def test_faults_before_pointer_roll_back_and_pointer_fault_retains_new_bundle(self) -> None:
        precommit_phases = [
            "LOCK_ACQUIRED",
            "PREFLIGHT_VERIFIED",
            "SOURCE_VERIFIED",
            "STAGE_VERIFIED",
            "PROMOTION_PREPARED",
            "PROMOTING",
            "LIVE_TARGETS_VERIFIED",
            "PROMOTION:first",
            "PROMOTION:middle",
            "PROMOTION:last",
        ]
        for phase in precommit_phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
                env = SyntheticEnvironment(Path(raw))
                it.install(env.archive_options())
                before_current = (env.codex / ".coding-os-install/current.json").read_bytes()
                before_payload = (env.codex / "coding-os/payload/doc.txt").read_bytes()
                write_text(env.source / "payload/doc.txt", "payload-v2\n")
                env.bundle = it.build_bundle_manifest(env.source)
                env.bundle_hash = env.bundle["aggregate_sha256"]
                fault_env = {
                    "CCOS_INSTALL_TEST_MODE": "1",
                    "CCOS_INSTALL_TEST_FAIL_AFTER": phase,
                }
                with mock.patch.dict(os.environ, fault_env, clear=False):
                    with self.assertRaises(it.InjectedFailure):
                        it.install(env.archive_options())
                self.assertEqual((env.codex / ".coding-os-install/current.json").read_bytes(), before_current)
                self.assertEqual((env.codex / "coding-os/payload/doc.txt").read_bytes(), before_payload)

        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            with mock.patch.dict(
                os.environ,
                {"CCOS_INSTALL_TEST_MODE": "1", "CCOS_INSTALL_TEST_FAIL_AFTER": "CURRENT_POINTER_COMMITTED"},
                clear=False,
            ):
                result = it.install(env.archive_options())
            self.assertEqual(result["status"], "committed_recovered")
            self.assertTrue((env.codex / "coding-os/payload/doc.txt").is_file())

    def test_fault_injection_is_rejected_without_both_test_mode_and_temp_roots(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            with mock.patch.dict(os.environ, {"CCOS_INSTALL_TEST_FAIL_AFTER": "SOURCE_VERIFIED"}, clear=False):
                with self.assertRaises(it.TransactionError):
                    it.install(env.archive_options())
        outside = REPO_ROOT / "must-not-be-created"
        env_vars = {"CCOS_INSTALL_TEST_MODE": "1", "CCOS_INSTALL_TEST_FAIL_AFTER": "SOURCE_VERIFIED"}
        with mock.patch.dict(os.environ, env_vars, clear=False):
            with self.assertRaises(it.TransactionError):
                it.install(
                    it.InstallOptions(
                        source_root=REPO_ROOT,
                        skills_root=outside / "skills",
                        codex_home=outside / "codex",
                        expected_bundle_sha256="0" * 64,
                        archive_mode=True,
                    )
                )
        self.assertFalse(outside.exists())

    def test_exclusive_lock_blocks_a_concurrent_transaction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            state_root = env.codex / ".coding-os-install"
            with it.exclusive_install_lock(state_root, "held", "install"):
                with self.assertRaises(it.LockError):
                    it.install(env.archive_options())

    def test_dry_run_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            result = it.install(env.archive_options(dry_run=True))
            self.assertEqual(result["status"], "dry_run")
            self.assertFalse(env.skills.exists())
            self.assertFalse(env.codex.exists())

    def test_policy_sync_uses_the_supplied_bundle_id_and_refuses_preclosure(self) -> None:
        bundle_id = "automation-preserving-case-state-recovery-v1-legacy-layout-migration"
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            env._write_case_authority(allowed=False, universal_bundle=bundle_id)
            result = it.install(env.policy_options(universal_bundle_id=bundle_id))
            self.assertEqual(result["status"], "committed")
            request = json.loads((env.state / "last-action-check.json").read_text(encoding="utf-8"))
            self.assertEqual(request[request.index("--universal-bundle") + 1], bundle_id)
            manifest = json.loads((env.codex / "coding-os/install-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["authority"]["universal_bundle"], bundle_id)
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options(universal_bundle_id="unsafe bundle"))
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options(universal_bundle_id=""))

        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            env._write_case_authority(allowed=False, universal_bundle=bundle_id, state="IMPLEMENTING")
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options(universal_bundle_id=bundle_id))

    def test_cli_exposes_legacy_overlap_and_universal_bundle_inputs(self) -> None:
        parser = it.build_parser()
        install_args = parser.parse_args(
            [
                "install",
                "--source-root",
                "source",
                "--skills-root",
                "skills",
                "--codex-home",
                "codex",
                "--expected-bundle-sha256",
                "0" * 64,
                "--universal-bundle-id",
                "automation-preserving-case-state-recovery-v1-legacy-layout-migration",
                "--legacy-overlap-migration",
            ]
        )
        self.assertTrue(install_args.legacy_overlap_migration)
        self.assertEqual(
            install_args.universal_bundle_id,
            "automation-preserving-case-state-recovery-v1-legacy-layout-migration",
        )
        uninstall_args = parser.parse_args(
            [
                "uninstall",
                "--skills-root",
                "skills",
                "--codex-home",
                "codex",
                "--legacy-overlap-migration",
            ]
        )
        self.assertTrue(uninstall_args.legacy_overlap_migration)


class LegacyOverlapMigrationTests(unittest.TestCase):
    def test_default_and_nonexact_legacy_overlap_layouts_are_denied(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills, _ = env.prepare_legacy_overlap_v2()
            with self.assertRaises(it.TransactionError):
                it.install(env.archive_options(skills_root=skills))
            for unsafe_root in (env.codex, skills / "nested", env.root):
                with self.subTest(unsafe_root=unsafe_root), self.assertRaises(it.TransactionError):
                    it.install(
                        env.archive_options(
                            skills_root=unsafe_root,
                            legacy_overlap_migration=True,
                        )
                    )

    def test_legacy_overlap_requires_an_exact_owned_v2_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            with self.assertRaises(it.OwnershipError):
                it.install(env.legacy_overlap_options())

        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            env.prepare_legacy_overlap_v2(manifest_overrides={"codex_home": str(env.root / "wrong-codex")})
            with self.assertRaises(it.OwnershipError):
                it.install(env.legacy_overlap_options())

    def test_legacy_overlap_accepts_the_known_v2_text_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills, support = env.prepare_legacy_overlap_v2()
            (support / "install-manifest.json").unlink()
            write_text(
                support / "install-manifest.txt",
                "\n".join(
                    [
                        "ManifestVersion=2",
                        "Package=codex-coding-os-starter",
                        f"SkillsRoot={skills}",
                        f"CodexHome={env.codex}",
                        f"SupportRoot={support}",
                        f"SkillPath={skills / 'alpha'}",
                    ]
                )
                + "\n",
            )
            self.assertEqual(it.install(env.legacy_overlap_options())["status"], "committed")

    def test_legacy_overlap_migration_preserves_nonmanaged_paths_and_records_v3_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills, legacy_support = env.prepare_legacy_overlap_v2()
            write_text(skills / "user-owned/SKILL.md", "keep-user-skill\n")
            write_text(env.codex / "config.toml", "keep-config\n")
            write_text(env.root / ".agents/skills/unmanaged/SKILL.md", "keep-agents-skill\n")
            preserved = {
                skills / "user-owned/SKILL.md": sha(skills / "user-owned/SKILL.md"),
                env.codex / "config.toml": sha(env.codex / "config.toml"),
                env.root / ".agents/skills/unmanaged/SKILL.md": sha(env.root / ".agents/skills/unmanaged/SKILL.md"),
                legacy_support / "legacy-support.txt": sha(legacy_support / "legacy-support.txt"),
            }

            result = it.install(env.legacy_overlap_options())
            self.assertEqual(result["status"], "committed")
            manifest = json.loads((env.codex / "coding-os/install-manifest.json").read_text(encoding="utf-8"))
            marker = manifest["legacy_overlap_migration"]
            self.assertEqual(marker["layout"], it.LEGACY_OVERLAP_LAYOUT)
            self.assertEqual(marker["source_manifest_version"], 2)
            self.assertEqual(Path(marker["skills_root"]).resolve(strict=False), skills.resolve(strict=False))
            self.assertEqual(
                Path(manifest["targets"]["skills_root"]).resolve(strict=False),
                skills.resolve(strict=False),
            )
            self.assertIn("name: alpha", (skills / "alpha/SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(preserved, {path: sha(path) for path in preserved})
            self.assertFalse((skills / ".coding-os-stage").exists())
            self.assertFalse((skills / ".coding-os-rollback").exists())
            self.assertFalse((env.codex / ".coding-os-stage").exists())
            self.assertFalse((env.codex / ".coding-os-rollback").exists())
            journals = list((env.codex / ".coding-os-install/transactions").glob("*/journal.json"))
            self.assertEqual(len(journals), 1)
            journal = json.loads(journals[0].read_text(encoding="utf-8"))
            for value in [*journal["stage_roots"], *journal["rollback_roots"]]:
                self.assertFalse(it._path_is_within(Path(value), skills))
                self.assertFalse(it._path_is_within(Path(value), env.codex))
            self.assertFalse(Path(journal["transaction_workspace"]).exists())

    def test_legacy_overlap_rejects_an_unrecorded_nested_descendant_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills, _ = env.prepare_legacy_overlap_v2()
            nested = skills / "alpha/local-notes.md"
            empty_nested = skills / "alpha/z-user-cache"
            write_text(nested, "user-owned notes\n")
            empty_nested.mkdir()
            preserved = {
                skills / "alpha/SKILL.md": sha(skills / "alpha/SKILL.md"),
                nested: sha(nested),
            }

            with self.assertRaisesRegex(it.OwnershipError, "unrecorded descendant.*alpha.*local-notes\\.md"):
                it.install(env.legacy_overlap_options())

            self.assertEqual(preserved, {path: sha(path) for path in preserved})
            self.assertTrue(empty_nested.is_dir())
            self.assertFalse((env.codex / "coding-os").exists())
            self.assertFalse((env.codex / ".coding-os-install/current.json").exists())

    def test_legacy_overlap_detects_unowned_skill_conflicts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills, _ = env.prepare_legacy_overlap_v2(
                manifest_overrides={
                    "skills": [{"name": "obsolete", "path": str(env.codex / "skills/obsolete")}]
                }
            )
            write_text(skills / "obsolete/SKILL.md", "legacy-obsolete\n")
            write_text(skills / "alpha/user-file.txt", "not-managed\n")
            before = sha(skills / "alpha/user-file.txt")
            with self.assertRaises(it.OwnershipError):
                it.install(env.legacy_overlap_options())
            self.assertEqual(sha(skills / "alpha/user-file.txt"), before)
            self.assertFalse((env.codex / "coding-os").exists())

    def test_legacy_overlap_fault_rolls_back_without_in_root_staging(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills, legacy_support = env.prepare_legacy_overlap_v2()
            write_text(skills / "user-owned/SKILL.md", "keep\n")
            write_text(env.codex / "config.toml", "keep-config\n")
            preserved = {
                skills / "alpha/SKILL.md": sha(skills / "alpha/SKILL.md"),
                skills / "user-owned/SKILL.md": sha(skills / "user-owned/SKILL.md"),
                env.codex / "config.toml": sha(env.codex / "config.toml"),
                legacy_support / "legacy-support.txt": sha(legacy_support / "legacy-support.txt"),
            }
            fault_env = {
                "CCOS_INSTALL_TEST_MODE": "1",
                "CCOS_INSTALL_TEST_FAIL_AFTER": "PROMOTION:middle",
            }
            with mock.patch.dict(os.environ, fault_env, clear=False), self.assertRaises(it.InjectedFailure):
                it.install(env.legacy_overlap_options())
            self.assertEqual(preserved, {path: sha(path) for path in preserved})
            self.assertFalse((env.codex / "coding-os").exists())
            self.assertFalse((skills / ".coding-os-stage").exists())
            self.assertFalse((skills / ".coding-os-rollback").exists())
            self.assertFalse((env.codex / ".coding-os-stage").exists())
            self.assertFalse((env.codex / ".coding-os-rollback").exists())

    def test_follow_up_overlap_install_and_uninstall_require_the_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills, _ = env.prepare_legacy_overlap_v2()
            write_text(skills / "user-owned/SKILL.md", "keep\n")
            write_text(env.codex / "config.toml", "keep-config\n")
            preserved = {
                skills / "user-owned/SKILL.md": sha(skills / "user-owned/SKILL.md"),
                env.codex / "config.toml": sha(env.codex / "config.toml"),
            }
            self.assertEqual(it.install(env.legacy_overlap_options())["status"], "committed")
            with self.assertRaises(it.TransactionError):
                it.install(env.archive_options(skills_root=skills))
            with self.assertRaises(it.TransactionError):
                it.uninstall(it.UninstallOptions(skills_root=skills, codex_home=env.codex))
            self.assertEqual(it.install(env.legacy_overlap_options())["status"], "already_committed")
            result = it.uninstall(
                it.UninstallOptions(
                    skills_root=skills,
                    codex_home=env.codex,
                    legacy_overlap_migration=True,
                )
            )
            self.assertEqual(result["status"], "uninstalled")
            self.assertFalse((skills / "alpha").exists())
            self.assertEqual(preserved, {path: sha(path) for path in preserved})


class UninstallTransactionTests(unittest.TestCase):
    def test_policy_install_then_opt_out_reinstall_removes_only_managed_policy_blocks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            original_agents, original_rules = env.prepare_legacy_policy()
            write_text(env.codex / "config.toml", "config\n")
            write_text(env.codex / "case-state/data.json", "case\n")
            write_text(env.codex / "plugins/data.txt", "plugin\n")
            write_text(env.skills / "unmanaged/SKILL.md", "unmanaged\n")
            preserved = {
                env.codex / "config.toml": sha(env.codex / "config.toml"),
                env.codex / "case-state/data.json": sha(env.codex / "case-state/data.json"),
                env.codex / "plugins/data.txt": sha(env.codex / "plugins/data.txt"),
                env.skills / "unmanaged/SKILL.md": sha(env.skills / "unmanaged/SKILL.md"),
            }

            it.install(env.policy_options())
            agents_path = env.codex / "AGENTS.md"
            rules_path = env.codex / "rules/default.rules"
            agents_path.write_bytes(agents_path.read_bytes() + b"user-agents-setting\n")
            rules_path.write_bytes(rules_path.read_bytes() + b"user-rule-setting\n")

            opt_out = env.policy_options(
                install_universal_policy=False,
                authority_case_id=None,
                authority_source=None,
                authority_reference=None,
                case_state_engine=None,
                case_state_root=None,
            )
            result = it.install(opt_out)
            self.assertEqual(result["status"], "committed")
            expected_agents = original_agents.replace(AGENTS_LEGACY.encode(), b"") + b"user-agents-setting\n"
            expected_rules = original_rules.replace(RULES_LEGACY.encode(), b"") + b"user-rule-setting\n"
            self.assertEqual(agents_path.read_bytes(), expected_agents)
            self.assertEqual(rules_path.read_bytes(), expected_rules)
            manifest = json.loads((env.codex / "coding-os/install-manifest.json").read_text())
            self.assertFalse(manifest["targets"]["global_agents"]["managed"])
            self.assertFalse(manifest["targets"]["default_rules"]["managed"])
            self.assertEqual(preserved, {path: sha(path) for path in preserved})

            uninstall = it.uninstall(it.UninstallOptions(skills_root=env.skills, codex_home=env.codex))
            self.assertEqual(uninstall["status"], "uninstalled")
            self.assertFalse((env.skills / "alpha").exists())
            self.assertFalse((env.codex / "coding-os").exists())
            self.assertEqual(agents_path.read_bytes(), expected_agents)
            self.assertEqual(rules_path.read_bytes(), expected_rules)
            self.assertEqual(preserved, {path: sha(path) for path in preserved})

    def test_uninstall_removes_recorded_targets_and_markers_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            original_agents, original_rules = env.prepare_legacy_policy()
            write_text(env.codex / "config.toml", "config")
            write_text(env.codex / "case-state/data.json", "case")
            write_text(env.codex / "plugins/data.txt", "plugin")
            write_text(env.skills / "unmanaged/SKILL.md", "unmanaged")
            preserved = {
                env.codex / "config.toml": sha(env.codex / "config.toml"),
                env.codex / "case-state/data.json": sha(env.codex / "case-state/data.json"),
                env.codex / "plugins/data.txt": sha(env.codex / "plugins/data.txt"),
                env.skills / "unmanaged/SKILL.md": sha(env.skills / "unmanaged/SKILL.md"),
            }
            it.install(env.policy_options())
            result = it.uninstall(it.UninstallOptions(skills_root=env.skills, codex_home=env.codex))
            self.assertEqual(result["status"], "uninstalled")
            self.assertFalse((env.skills / "alpha").exists())
            self.assertFalse((env.codex / "coding-os").exists())
            self.assertEqual(preserved, {path: sha(path) for path in preserved})
            self.assertEqual(
                (env.codex / "AGENTS.md").read_bytes(),
                original_agents.replace(AGENTS_LEGACY.encode(), b""),
            )
            self.assertEqual(
                (env.codex / "rules/default.rules").read_bytes(),
                original_rules.replace(RULES_LEGACY.encode(), b""),
            )
            current = json.loads((env.codex / ".coding-os-install/current.json").read_text())
            self.assertEqual(current["status"], "uninstalled")

    def test_uninstall_fails_closed_on_missing_manifest_or_path_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            root = Path(raw)
            skills = root / "skills"
            codex = root / "codex"
            write_text(codex / ".coding-os-install/current.json", json.dumps({"status": "committed"}))
            with self.assertRaises(it.TransactionError):
                it.uninstall(it.UninstallOptions(skills_root=skills, codex_home=codex))

        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            it.install(env.archive_options())
            manifest_path = env.codex / "coding-os/install-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["targets"]["managed_skills"][0]["path"] = str(Path(raw).parent / "escape")
            write_text(manifest_path, json.dumps(manifest))
            current_path = env.codex / ".coding-os-install/current.json"
            current = json.loads(current_path.read_text())
            current["install_manifest_sha256"] = sha(manifest_path)
            write_text(current_path, json.dumps(current))
            with self.assertRaises(it.TransactionError):
                it.uninstall(it.UninstallOptions(skills_root=env.skills, codex_home=env.codex))

    def test_uninstall_fault_rolls_back_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            it.install(env.archive_options())
            before_current = (env.codex / ".coding-os-install/current.json").read_bytes()
            with mock.patch.dict(
                os.environ,
                {"CCOS_INSTALL_TEST_MODE": "1", "CCOS_INSTALL_TEST_FAIL_AFTER": "PROMOTION:middle"},
                clear=False,
            ):
                with self.assertRaises(it.InjectedFailure):
                    it.uninstall(it.UninstallOptions(skills_root=env.skills, codex_home=env.codex))
            self.assertTrue((env.skills / "alpha/SKILL.md").is_file())
            self.assertTrue((env.codex / "coding-os/install-manifest.json").is_file())
            self.assertEqual((env.codex / ".coding-os-install/current.json").read_bytes(), before_current)


if __name__ == "__main__":
    unittest.main(verbosity=2)
