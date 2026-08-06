from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.agent.campaign_engine.effects import ExactFileEffectDriver


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
AGENTS_POLICY = """<!-- BEGIN CODEX CODING OS MANAGED: CAMPAIGN ENGINE POLICY -->
- The installed campaign engine and external SQLite store are the only lifecycle authority.
- Repository state files are informational only and old lifecycle commands are retired.
<!-- END CODEX CODING OS MANAGED: CAMPAIGN ENGINE POLICY -->"""
RULES_LEGACY = 'prefix_rule(pattern=["gh", "pr", "merge"], decision="allow")'
RULES_POLICY = """# BEGIN CODEX CODING OS MANAGED: CAMPAIGN EXTERNAL EFFECTS
prefix_rule(
    pattern = ["gh", "pr", ["create", "merge"]],
    decision = "prompt",
    justification = "Campaign publication uses the transactional outbox.",
)
# END CODEX CODING OS MANAGED: CAMPAIGN EXTERNAL EFFECTS"""
REAL_AGENTS_FIXTURE = REPO_ROOT / "tests/fixtures/install/global-agents-real-layout-lf.md"


def hook_command(name: str) -> dict[str, object]:
    return {
        "type": "command",
        "command": f'python3 -B "$HOME/.codex/hooks/{name}.py"',
        "commandWindows": f'python.exe -B "C:\\synthetic\\hooks\\{name}.py"',
        "timeout": 11,
        "statusMessage": f"Running {name}",
    }


def legacy_lifecycle_hook_command() -> dict[str, object]:
    return {
        "type": "command",
        "command": (
            'python3 -B "$HOME/.codex/coding-os/hooks/anti-loop-runtime/'
            'anti_loop_runtime.py"'
        ),
        "commandWindows": (
            'python.exe -B "C:\\synthetic\\coding-os\\hooks\\anti-loop-runtime\\'
            'anti_loop_runtime.py"'
        ),
        "timeout": 30,
        "statusMessage": "Enforcing mandatory anti-loop latch",
    }


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
        root = root.resolve(strict=True)
        self.root = root
        self.source = root / "source"
        self.skills = root / "skills"
        self.codex = root / "codex-home"
        self.legacy_state = self.codex / "case-state"
        self.state_db = self.codex / "coding-os-state" / "campaigns.sqlite3"
        self.campaign_id = "campaign-synthetic"
        self.node_id = "install-runtime"
        self.authority_epoch = 3
        self.cancellation_epoch = 0
        self.repository = "https://example.invalid/synthetic/coding-os"
        self.source.mkdir(parents=True)
        write_text(self.source / ".agents/skills/alpha/SKILL.md", "---\nname: alpha\ndescription: synthetic\n---\n")
        write_text(self.source / "payload/doc.txt", "payload-v1\n")
        write_text(self.source / "scripts/install_transaction.py", "# synthetic runtime\n")
        write_text(self.source / "scripts/agent/campaign_engine/__init__.py", "# synthetic package\n")
        write_text(
            self.source / "scripts/agent/campaign_engine/effects.py",
            """import hashlib, json, os, pathlib, tempfile

class ExactFileEffectDriver:
    def __init__(self, journal_root):
        self.journal_root = pathlib.Path(journal_root)
        self.journal_root.mkdir(parents=True, exist_ok=True)

    def replace(self, *, operation_id, target, expected_baseline_sha256, replacement, expected_replacement_sha256):
        path = pathlib.Path(target).resolve(strict=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_baseline_sha256:
            raise RuntimeError("synthetic exact-file baseline mismatch")
        if hashlib.sha256(replacement).hexdigest() != expected_replacement_sha256:
            raise RuntimeError("synthetic exact-file replacement mismatch")
        descriptor, temporary = tempfile.mkstemp(prefix=".synthetic-effect-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_replacement_sha256:
            raise RuntimeError("synthetic exact-file post-write mismatch")
        receipt = {
            "protocol_version": "ccos-exact-file-effect-v1",
            "operation_id": operation_id,
            "target": str(path),
            "baseline_sha256": expected_baseline_sha256,
            "replacement_sha256": expected_replacement_sha256,
            "state": "CONFIRMED",
            "replayed": False,
        }
        (self.journal_root / (operation_id.replace(":", "-") + ".json")).write_text(
            json.dumps(receipt, sort_keys=True), encoding="utf-8"
        )
        return receipt
""",
        )
        write_text(
            self.source / "scripts/agent/campaign_engine/store.py",
            """import json, pathlib, sqlite3
from contextlib import closing

class CampaignStore:
    def __init__(self, path, timeout_seconds=10.0):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("CREATE TABLE IF NOT EXISTS runtime_installations (installation_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS legacy_archives (archive_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.commit()

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def integrity_check(self):
        with closing(self._connect()) as db:
            return {
                "status": "ok",
                "foreign_keys": db.execute("PRAGMA foreign_keys").fetchone()[0],
                "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "synchronous": db.execute("PRAGMA synchronous").fetchone()[0],
                "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
            }

    def record_runtime_installation(self, installation):
        raw = json.dumps(installation, sort_keys=True, separators=(",", ":"))
        with closing(self._connect()) as db:
            row = db.execute("SELECT payload FROM runtime_installations WHERE installation_id=?", (installation["installation_id"],)).fetchone()
            if row is not None and row[0] != raw:
                raise RuntimeError("runtime installation identity drift")
            db.execute("INSERT OR IGNORE INTO runtime_installations VALUES (?, ?)", (installation["installation_id"], raw))
            db.commit()

    def verify_publication_authority(self, campaign_id, effect_kind, *, authority_epoch, cancellation_epoch, node_id=None, candidate_head=None):
        record = json.loads(self.path.with_name("authority.json").read_text(encoding="utf-8"))
        expected = {
            "campaign_id": campaign_id,
            "effect_kind": str(effect_kind),
            "authority_epoch": authority_epoch,
            "cancellation_epoch": cancellation_epoch,
            "node_id": node_id,
            "candidate_head": candidate_head,
            "authorized": True,
        }
        if record != expected:
            raise RuntimeError("publication authority tuple mismatch")
        return record

    def record_legacy_archive(self, *, archive_id, source_path, digest, last_state, classification, evidence):
        payload = {
            "archive_id": archive_id,
            "source_path": source_path,
            "digest": digest,
            "last_state": last_state,
            "classification": classification,
            "evidence": evidence,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with closing(self._connect()) as db:
            row = db.execute("SELECT payload FROM legacy_archives WHERE archive_id=?", (archive_id,)).fetchone()
            if row is not None and row[0] != raw:
                raise RuntimeError("legacy archive identity drift")
            db.execute("INSERT OR IGNORE INTO legacy_archives VALUES (?, ?)", (archive_id, raw))
            db.commit()
        return payload
""",
        )
        write_text(
            self.source / "scripts/agent/campaign_engine/legacy.py",
            """import hashlib, json, pathlib, shutil
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class Result:
    archive_id: str
    archive_root: str
    source_digest: str
    replayed: bool
    def to_dict(self):
        return asdict(self)

def inspect_legacy_root(root):
    source = pathlib.Path(root).resolve()
    state_file = source / "case-state.json"
    if state_file.is_file():
        json.loads(state_file.read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    return {"source_root": str(source), "source_digest": digest.hexdigest()}

def archive_legacy_root(root, *, state_root, store=None):
    source = pathlib.Path(root).resolve()
    value = inspect_legacy_root(source)["source_digest"]
    destination = pathlib.Path(state_root) / "legacy-archives" / ("legacy-" + value[:24])
    replayed = destination.exists()
    if not replayed:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        (destination / "archive-manifest.json").write_text(json.dumps({"source_digest": value}, sort_keys=True), encoding="utf-8")
        if store is not None:
            store.record_legacy_archive(
                archive_id="legacy-" + value[:24],
                source_path=str(source),
                digest=value,
                last_state="UNKNOWN",
                classification="LEGACY_ARCHIVED_UNRESOLVED",
                evidence={"translated_outcome": None},
            )
    return Result("legacy-" + value[:24], str(destination), value, replayed)
""",
        )
        write_text(
            self.source / "scripts/fake_refresh.py",
            "import os, sys\nsys.exit(int(os.environ.get('CCOS_SYNTHETIC_REFRESH_EXIT', '0')))\n",
        )
        write_text(self.source / "hooks/campaign-engine/campaign_hook.py", "# synthetic campaign hook\n")
        write_text(self.source / "universal/AGENTS.automation-case-policy.md", AGENTS_POLICY + "\n")
        write_text(self.source / "universal/rules/gh-pr-merge-authority.rules", RULES_POLICY + "\n")
        self.pack = {
            "version": "1.0.0",
            "package_name": "codex-coding-os",
            "support_items": [
                "payload",
                "scripts/install_transaction.py",
                "scripts/agent/campaign_engine",
                "scripts/fake_refresh.py",
                "hooks/campaign-engine",
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
                "runtime_files": [
                    "scripts/install_transaction.py",
                    "scripts/agent/campaign_engine",
                    "scripts/fake_refresh.py",
                    "hooks/campaign-engine",
                ],
                "campaign_hook": {
                    "source": "hooks/campaign-engine",
                    "target": "hooks/campaign-engine",
                },
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

    def write_campaign_authority(self, **overrides: object) -> None:
        assert self.commit is not None
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, object] = {
            "campaign_id": self.campaign_id,
            "effect_kind": "EXACT_FILE_REPLACE",
            "authority_epoch": self.authority_epoch,
            "cancellation_epoch": self.cancellation_epoch,
            "node_id": self.node_id,
            "candidate_head": self.commit,
            "authorized": True,
        }
        record.update(overrides)
        write_text(self.state_db.with_name("authority.json"), json.dumps(record))

    def archive_options(self, **overrides: object) -> object:
        values = dict(
            source_root=self.source,
            skills_root=self.skills,
            codex_home=self.codex,
            expected_bundle_sha256=self.bundle_hash,
            expected_source_commit=self.commit or ("a" * 40),
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
            policy_authority_source="explicit-user-approval",
            policy_authority_reference="synthetic-user-approval",
        )
        values.update(overrides)
        return it.InstallOptions(**values)

    def campaign_policy_options(self, **overrides: object) -> object:
        self.write_campaign_authority()
        values = dict(
            policy_authority_source="campaign-publication-authority",
            policy_authority_reference="synthetic-campaign-publication",
            publication_campaign_id=self.campaign_id,
            publication_node_id=self.node_id,
            publication_authority_epoch=self.authority_epoch,
            publication_cancellation_epoch=self.cancellation_epoch,
        )
        values.update(overrides)
        return self.policy_options(**values)

    def prepare_legacy_policy(self) -> tuple[bytes, bytes]:
        agents = b"alpha\r\n" + AGENTS_LEGACY.encode() + b"\npost\r\n"
        rules = b"rule-before\n" + RULES_LEGACY.encode() + b"\r\nrule-after\n"
        self.codex.mkdir(parents=True, exist_ok=True)
        (self.codex / "rules").mkdir(parents=True, exist_ok=True)
        (self.codex / "AGENTS.md").write_bytes(agents)
        (self.codex / "rules/default.rules").write_bytes(rules)
        return agents, rules

    def prepare_real_layout_policy(self) -> tuple[bytes, bytes]:
        agents = REAL_AGENTS_FIXTURE.read_bytes()
        rules = b'prefix_rule(pattern=["git", "status"], decision="allow")\r\n'
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

    def test_bundle_requires_the_single_campaign_hook_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            manifest_path = env.source / "pack.manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["installation"].pop("campaign_hook")
            write_text(manifest_path, json.dumps(manifest))
            with self.assertRaisesRegex(it.BundleError, "campaign_hook"):
                it.build_bundle_manifest(env.source)

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

    def test_real_installed_agents_layout_migrates_exact_sections_for_lf_and_crlf(self) -> None:
        fixture = REAL_AGENTS_FIXTURE.read_bytes()
        lifecycle_start = it.AGENTS_LEGACY_BLOCK_START.encode()
        lifecycle_sentinel = it.AGENTS_LEGACY_BLOCK_SENTINEL.encode()
        routing_start = it.AGENTS_LEGACY_ROUTING_START.encode()
        routing_sentinel = it.AGENTS_LEGACY_ROUTING_SENTINEL.encode()
        lifecycle = fixture[fixture.index(lifecycle_start) : fixture.index(lifecycle_sentinel)]
        routing = fixture[fixture.index(routing_start) : fixture.index(routing_sentinel)]
        self.assertEqual(len(lifecycle), it.AGENTS_LEGACY_BLOCK_NORMALIZED_SIZE)
        self.assertEqual(
            hashlib.sha256(lifecycle).hexdigest(),
            it.AGENTS_LEGACY_BLOCK_NORMALIZED_SHA256,
        )
        self.assertEqual(len(routing), it.AGENTS_LEGACY_ROUTING_NORMALIZED_SIZE)
        self.assertEqual(
            hashlib.sha256(routing).hexdigest(),
            it.AGENTS_LEGACY_ROUTING_NORMALIZED_SHA256,
        )

        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                existing = fixture if newline == b"\n" else fixture.replace(b"\n", b"\r\n")
                raw_lifecycle = existing[existing.index(lifecycle_start) : existing.index(lifecycle_sentinel)]
                raw_routing = existing[existing.index(routing_start) : existing.index(routing_sentinel)]
                lifecycle_newline = b"\r\n" if raw_lifecycle.endswith(b"\r\n") else b"\n"
                expected = existing.replace(
                    raw_lifecycle,
                    AGENTS_POLICY.encode() + lifecycle_newline,
                    1,
                )
                expected = expected.replace(
                    it.AGENTS_LEGACY_AUTHORITY_LINE.encode(),
                    it.AGENTS_CAMPAIGN_AUTHORITY_LINE.encode(),
                    1,
                )
                expected = expected.replace(
                    raw_routing,
                    it.AGENTS_CAMPAIGN_ROUTING_POINTER.encode(),
                    1,
                )

                migrated = it.migrate_agents_bytes(existing, (AGENTS_POLICY + "\n").encode())

                self.assertEqual(migrated, expected)
                self.assertEqual(migrated.count(it.AGENTS_START.encode()), 1)
                self.assertIn(
                    AGENTS_POLICY.encode() + lifecycle_newline + lifecycle_sentinel,
                    migrated,
                )
                self.assertIn(lifecycle_sentinel, migrated)
                self.assertIn(routing_sentinel, migrated)
                self.assertNotIn(lifecycle_start, migrated)
                self.assertNotIn(it.AGENTS_LEGACY_AUTHORITY_LINE.encode(), migrated)
                self.assertNotIn(b"codex-coding-os-master", migrated)
                self.assertNotIn(b"workflow manifest", migrated)
                self.assertNotIn(b"session_continuity.py start", migrated)

    def test_real_installed_agents_layout_rejects_altered_partial_and_duplicate_sections(self) -> None:
        fixture = REAL_AGENTS_FIXTURE.read_bytes()
        cases = {
            "altered lifecycle": fixture.replace(
                b"support-failure fingerprint", b"support failure fingerprint", 1
            ),
            "partial lifecycle": fixture.replace(
                it.AGENTS_LEGACY_BLOCK_SENTINEL.encode(), b"partial sentinel", 1
            ),
            "duplicate lifecycle": fixture.replace(
                it.AGENTS_LEGACY_BLOCK_SENTINEL.encode(),
                it.AGENTS_LEGACY_BLOCK_START.encode() + b"\n" + it.AGENTS_LEGACY_BLOCK_SENTINEL.encode(),
                1,
            ),
            "altered routing": fixture.replace(b"workflow manifest first", b"workflow manifest now", 1),
            "partial routing": fixture.replace(
                it.AGENTS_LEGACY_ROUTING_SENTINEL.encode(), b"## Partial sentinel", 1
            ),
            "duplicate routing": fixture.replace(
                it.AGENTS_LEGACY_ROUTING_SENTINEL.encode(),
                it.AGENTS_LEGACY_ROUTING_START.encode()
                + b"\n"
                + it.AGENTS_LEGACY_ROUTING_SENTINEL.encode(),
                1,
            ),
            "altered authority": fixture.replace(
                b"managed case-policy block.", b"managed old-policy block.", 1
            ),
            "duplicate authority": fixture.replace(
                it.AGENTS_LEGACY_AUTHORITY_LINE.encode(),
                it.AGENTS_LEGACY_AUTHORITY_LINE.encode()
                + b"\n"
                + it.AGENTS_LEGACY_AUTHORITY_LINE.encode(),
                1,
            ),
        }
        for label, existing in cases.items():
            with self.subTest(label=label), self.assertRaises(it.PolicyMigrationError):
                it.migrate_agents_bytes(existing, AGENTS_POLICY.encode())

    def test_markerless_campaign_layout_restores_policy_for_lf_and_crlf(self) -> None:
        markerless = (
            "intro\n"
            + it.AGENTS_CAMPAIGN_AUTHORITY_LINE
            + "\nother\n"
            + it.AGENTS_CAMPAIGN_ROUTING_POINTER
            + it.AGENTS_LEGACY_ROUTING_SENTINEL
            + "\nend\n"
        ).encode()
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                existing = markerless if newline == b"\n" else markerless.replace(b"\n", b"\r\n")
                routing_start = existing.index(it.AGENTS_LEGACY_ROUTING_START.encode())
                migrated = it.migrate_agents_bytes(existing, AGENTS_POLICY.encode())
                expected = (
                    existing[:routing_start]
                    + AGENTS_POLICY.encode()
                    + newline
                    + existing[routing_start:]
                )
                self.assertEqual(migrated, expected)
                self.assertEqual(migrated.count(it.AGENTS_START.encode()), 1)

    def test_markerless_campaign_layout_rejects_modified_routing(self) -> None:
        existing = (
            it.AGENTS_CAMPAIGN_AUTHORITY_LINE
            + "\n"
            + it.AGENTS_CAMPAIGN_ROUTING_POINTER.replace(
                "explicit user instructions", "caller instructions"
            )
            + it.AGENTS_LEGACY_ROUTING_SENTINEL
            + "\n"
        ).encode()
        with self.assertRaisesRegex(it.PolicyMigrationError, "approved layout"):
            it.migrate_agents_bytes(existing, AGENTS_POLICY.encode())

    def test_rules_migration_removes_only_exact_installed_case_state_allows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            codex_home = Path(raw) / "codex-home"
            retired = str(
                (codex_home / "coding-os/scripts/agent/case_state.py").resolve(
                    strict=False
                )
            )
            other = str(
                (codex_home / "other/scripts/agent/case_state.py").resolve(
                    strict=False
                )
            )

            def rule(command: list[str], decision: str = "allow") -> bytes:
                return (
                    "prefix_rule(pattern="
                    + json.dumps(command, separators=(",", ":"))
                    + f', decision="{decision}")\r\n'
                ).encode()

            retained = (
                rule(["python", other, "--help"])
                + rule(["py", retired, "--help"])
                + rule(["python", retired, "--help"], "prompt")
                + b'prefix_rule(pattern=["git", "status"], decision="allow")\n'
            )
            existing = (
                rule(["python", retired, "--help"])
                + retained
                + rule(
                    [
                        "corepack",
                        "pnpm",
                        "run",
                        "agent:case-state",
                        "--",
                        "--help",
                    ]
                )
                + rule(["python", retired, "--json", "show"])
            )
            migrated = it.migrate_rules_bytes(
                existing, RULES_POLICY.encode(), codex_home
            )
            self.assertEqual(migrated, retained + RULES_POLICY.encode())

    def test_markerless_rules_append_once_and_preserve_lf_and_crlf_bytes(self) -> None:
        values = (
            b'prefix_rule(pattern=["git", "status"], decision="allow")\n',
            b'prefix_rule(pattern=["git", "status"], decision="allow")\r\n',
            b'prefix_rule(pattern=["git", "status"], decision="allow")',
            b"",
        )
        for existing in values:
            with self.subTest(existing=existing):
                separator = b"" if not existing or existing.endswith((b"\n", b"\r")) else b"\n"
                migrated = it.migrate_rules_bytes(existing, (RULES_POLICY + "\n").encode())
                self.assertEqual(migrated, existing + separator + RULES_POLICY.encode())
                self.assertTrue(migrated.startswith(existing))
                self.assertEqual(migrated.count(it.RULES_START.encode()), 1)

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
            (RULES_LEGACY + "\n" + RULES_LEGACY).encode(),
            b"# END CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY",
            b"# BEGIN CODEX CODING OS MANAGED: CAMPAIGN EXTERNAL EFFECTS\npartial",
            (RULES_POLICY + "\n" + RULES_POLICY).encode(),
            (
                RULES_POLICY
                + "\n# BEGIN CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY\n"
                + RULES_LEGACY
                + "\n# END CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY"
            ).encode(),
        ]
        for value in rules_bad:
            with self.subTest(value=value[:20]), self.assertRaises(it.PolicyMigrationError):
                it.migrate_rules_bytes(value, RULES_POLICY.encode())

    def test_uninstall_removes_only_exact_blocks_case(self) -> None:
        agents = b"pre\n" + AGENTS_POLICY.encode() + b"\r\npost"
        self.assertEqual(it.remove_agents_policy_bytes(agents), b"pre\n\r\npost")
        rules = b"pre\r\n" + RULES_POLICY.encode() + b"\npost"
        self.assertEqual(it.remove_rules_policy_bytes(rules), b"pre\r\n\npost")


class OwnedPathDeletionTests(unittest.TestCase):
    def test_windows_readonly_callback_retries_only_owned_readonly_access_denied(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            target = Path(raw) / "owned.txt"
            target.write_text("owned", encoding="utf-8")
            error = PermissionError(13, "Access denied", str(target))
            error.winerror = 5
            info = mock.Mock(st_mode=stat.S_IREAD, st_file_attributes=it.FILE_ATTRIBUTE_READONLY)
            retried: list[str] = []

            def unlink(raw_path: str) -> None:
                retried.append(raw_path)

            with (
                mock.patch.object(it, "WINDOWS_PLATFORM", True),
                mock.patch.object(it, "_is_link_or_reparse", return_value=False),
                mock.patch.object(Path, "stat", return_value=info),
                mock.patch.object(it.os, "chmod") as chmod,
            ):
                it._retry_windows_readonly_remove(unlink, str(target), (PermissionError, error, None))
            chmod.assert_called_once_with(target, stat.S_IREAD | stat.S_IWRITE)
            self.assertEqual(retried, [str(target)])

    def test_windows_readonly_callback_rejects_nonremove_writable_or_link_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            target = Path(raw) / "owned.txt"
            target.write_text("owned", encoding="utf-8")
            error = PermissionError(13, "Access denied", str(target))
            error.winerror = 5
            writable = mock.Mock(st_mode=stat.S_IREAD | stat.S_IWRITE, st_file_attributes=0)
            retried: list[str] = []

            def unlink(raw_path: str) -> None:
                retried.append(raw_path)

            with (
                mock.patch.object(it, "WINDOWS_PLATFORM", True),
                mock.patch.object(it, "_is_link_or_reparse", return_value=False),
                mock.patch.object(Path, "stat", return_value=writable),
                mock.patch.object(it.os, "chmod") as chmod,
                self.assertRaises(PermissionError),
            ):
                it._retry_windows_readonly_remove(unlink, str(target), (PermissionError, error, None))
            chmod.assert_not_called()
            self.assertEqual(retried, [])
            readonly = mock.Mock(st_mode=stat.S_IREAD, st_file_attributes=it.FILE_ATTRIBUTE_READONLY)

            def scandir(raw_path: str) -> None:
                retried.append(raw_path)

            with (
                mock.patch.object(it, "WINDOWS_PLATFORM", True),
                mock.patch.object(it, "_is_link_or_reparse", return_value=False),
                mock.patch.object(Path, "stat", return_value=readonly),
                mock.patch.object(it.os, "chmod") as chmod,
                self.assertRaises(PermissionError),
            ):
                it._retry_windows_readonly_remove(scandir, str(target), (PermissionError, error, None))
            chmod.assert_not_called()
            self.assertEqual(retried, [])
            with (
                mock.patch.object(it, "WINDOWS_PLATFORM", True),
                mock.patch.object(it, "_is_link_or_reparse", return_value=True),
                mock.patch.object(it.os, "chmod") as chmod,
                self.assertRaises(it.RecoveryError),
            ):
                it._retry_windows_readonly_remove(unlink, str(target), (PermissionError, error, None))
            chmod.assert_not_called()
            self.assertEqual(retried, [])

    @unittest.skipUnless(os.name == "nt", "Windows read-only attributes are platform-specific")
    def test_remove_owned_path_removes_windows_readonly_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            owned = Path(raw) / "owned"
            readonly_file = owned / "readonly.txt"
            write_text(readonly_file, "owned\n")
            os.chmod(readonly_file, stat.S_IREAD)
            os.chmod(owned, stat.S_IREAD)
            try:
                it._remove_owned_path(owned)
            finally:
                if owned.exists():
                    os.chmod(owned, stat.S_IREAD | stat.S_IWRITE)
                if readonly_file.exists():
                    os.chmod(readonly_file, stat.S_IREAD | stat.S_IWRITE)
                if owned.exists():
                    shutil.rmtree(owned, ignore_errors=True)
            self.assertFalse(owned.exists())


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
            "https://AymanShams:" + "password" + "@github.com/AymanShams/codex-coding-os.git",
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
    def test_verified_runtime_import_isolated_without_bytecode_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            module_prefixes = (
                "scripts",
                "scripts.agent",
                "scripts.agent.campaign_engine",
            )
            before_modules = {
                name: module
                for name, module in sys.modules.items()
                if name in module_prefixes
                or name.startswith("scripts.agent.campaign_engine.")
            }
            before_bytecode_setting = sys.dont_write_bytecode

            with it._campaign_runtime_modules(
                env.source, include_effects=True
            ) as runtime_modules:
                self.assertTrue(sys.dont_write_bytecode)
                for module in runtime_modules:
                    self.assertTrue(
                        it._path_is_within(
                            Path(module.__file__).resolve(strict=True), env.source
                        )
                    )

            self.assertEqual(sys.dont_write_bytecode, before_bytecode_setting)
            after_modules = {
                name: module
                for name, module in sys.modules.items()
                if name in module_prefixes
                or name.startswith("scripts.agent.campaign_engine.")
            }
            self.assertEqual(set(after_modules), set(before_modules))
            for name, module in before_modules.items():
                self.assertIs(after_modules[name], module)
            self.assertFalse(
                any(path.name == "__pycache__" for path in env.source.rglob("*"))
            )
            self.assertFalse(any(env.source.rglob("*.pyc")))

    def test_managed_file_replacement_journal_records_confirmed_exact_effect(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            root = Path(raw).resolve(strict=True)
            skills = root / "skills"
            codex_home = root / "codex-home"
            live = codex_home / "AGENTS.md"
            staged = codex_home / "staging" / "AGENTS.md"
            rollback = codex_home / "rollback" / "AGENTS.md"
            write_text(live, "managed baseline\n")
            write_text(staged, "managed replacement\n")
            target = {
                "target_id": "policy:global-agents",
                "live_path": str(live),
                "staged_path": str(staged),
                "rollback_path": str(rollback),
                "prior_state": "present",
                "prior_sha256": sha(live),
                "new_sha256": sha(staged),
                "step": "planned",
            }
            journal_path = codex_home / "transactions" / "journal.json"
            transaction_id = "synthetic-exact-file-journal-test"
            journal = it.Journal(
                journal_path,
                {
                    "transaction_id": transaction_id,
                    "targets": [target],
                },
            )
            journal.save()

            it._promote_targets(
                journal,
                skills,
                codex_home,
                exact_file_driver=ExactFileEffectDriver(
                    codex_home / "transactions" / "exact-file-effects"
                ),
            )

            persisted = json.loads(journal_path.read_text(encoding="utf-8"))
            receipt = persisted["targets"][0]["exact_file_effect"]
            self.assertEqual(
                receipt["protocol_version"], "ccos-exact-file-effect-v1"
            )
            self.assertEqual(
                receipt["operation_id"], f"install:{transaction_id}:0"
            )
            self.assertEqual(receipt["state"], "CONFIRMED")
            self.assertEqual(receipt["baseline_sha256"], target["prior_sha256"])
            self.assertEqual(receipt["replacement_sha256"], target["new_sha256"])
            self.assertEqual(live.read_text(encoding="utf-8"), "managed replacement\n")

    def test_runtime_pin_requires_every_exact_field_and_provenance_match(self) -> None:
        valid = {
            "source_commit": "1" * 40,
            "bundle_digest": "2" * 64,
            "install_transaction": "3" * 32,
            "protocol_version": "ccos-campaign-v1",
            "schema_compatibility": "campaign-store-v1",
            "host_capability_probe_version": "native-bind-before-turn-scoped-tools-v3",
        }
        self.assertEqual(
            it._validate_runtime_pin(
                valid,
                source_commit="1" * 40,
                bundle_digest="2" * 64,
                install_transaction="3" * 32,
            ),
            valid,
        )
        for field in it.RUNTIME_PIN_FIELDS:
            with self.subTest(missing=field), self.assertRaises(it.TransactionError):
                it._validate_runtime_pin({key: value for key, value in valid.items() if key != field})
        invalid = {
            "source_commit": "0" * 40,
            "bundle_digest": "0" * 64,
            "install_transaction": "0" * 32,
            "protocol_version": "ccos-campaign-v0",
            "schema_compatibility": "campaign-store-v0",
            "host_capability_probe_version": "caller-role-v0",
        }
        for field, value in invalid.items():
            with self.subTest(invalid=field), self.assertRaises(it.TransactionError):
                it._validate_runtime_pin(
                    {**valid, field: value},
                    source_commit="1" * 40,
                    bundle_digest="2" * 64,
                    install_transaction="3" * 32,
                )

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
            self.assertFalse(manifest["preserved_paths"]["legacy_state"]["managed"])
            self.assertFalse(manifest["preserved_paths"]["campaign_state"]["managed"])
            current = json.loads((env.codex / ".coding-os-install/current.json").read_text(encoding="utf-8"))
            self.assertEqual(current["status"], "committed")
            self.assertEqual(current["install_manifest_sha256"], sha(manifest_path))
            self.assertTrue((env.skills / "alpha/SKILL.md").is_file())
            self.assertTrue((env.codex / "coding-os/payload/doc.txt").is_file())
            self.assertTrue((env.codex / "hooks/campaign-engine/campaign_hook.py").is_file())
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
            for runtime_root in (env.source, env.codex / "coding-os"):
                self.assertFalse(
                    any(path.name == "__pycache__" for path in runtime_root.rglob("*"))
                )
                self.assertFalse(any(runtime_root.rglob("*.pyc")))

    def test_hooks_migration_retires_only_legacy_and_records_exact_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            hooks_path = env.codex / "hooks.json"
            original = {
                "userSetting": {"keep": True},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": ".*",
                            "hooks": [
                                hook_command("user_pre"),
                                legacy_lifecycle_hook_command(),
                            ],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": ".*",
                            "hooks": [legacy_lifecycle_hook_command()],
                        }
                    ],
                    "Stop": [{"hooks": [hook_command("user_stop")]}],
                },
            }
            original_bytes = it._json_bytes(original)
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_bytes(original_bytes)

            result = it.install(env.archive_options())

            installed = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(installed["userSetting"], original["userSetting"])
            self.assertEqual(installed["hooks"]["Stop"], original["hooks"]["Stop"])
            self.assertEqual(
                installed["hooks"]["PreToolUse"][0]["hooks"],
                [hook_command("user_pre")],
            )
            self.assertNotIn("anti-loop-runtime", hooks_path.read_text(encoding="utf-8"))
            locations = it._campaign_hook_group_locations(installed)
            self.assertEqual(locations, [(it.CAMPAIGN_HOOK_EVENT, 1)])
            owned_group = installed["hooks"][it.CAMPAIGN_HOOK_EVENT][1]
            self.assertEqual(owned_group["matcher"], it.CAMPAIGN_HOOK_MATCHER)

            manifest = json.loads(
                (env.codex / "coding-os/install-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            record = manifest["targets"]["hooks_configuration"]
            self.assertEqual(record["ownership_protocol"], it.HOOKS_CONFIGURATION_PROTOCOL)
            self.assertEqual(record["preinstall_sha256"], hashlib.sha256(original_bytes).hexdigest())
            self.assertEqual(record["installed_sha256"], sha(hooks_path))
            self.assertEqual(len(record["retired_legacy_entries"]), 2)
            self.assertEqual(
                record["owned_entries"][0]["digest"],
                it._canonical_json_digest(owned_group),
            )

            journal = json.loads(
                (
                    env.codex
                    / ".coding-os-install/transactions"
                    / result["transaction_id"]
                    / "journal.json"
                ).read_text(encoding="utf-8")
            )
            target = next(
                item
                for item in journal["targets"]
                if item["target_id"] == "hooks_configuration"
            )
            self.assertEqual(target["prior_sha256"], hashlib.sha256(original_bytes).hexdigest())
            self.assertEqual(target["new_sha256"], sha(hooks_path))
            self.assertEqual(target["exact_file_effect"]["state"], "CONFIRMED")
            retained = Path(target["retained_backup_path"])
            self.assertEqual(retained.read_bytes(), original_bytes)

    def test_hooks_reinstall_preserves_unrelated_additions_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            first = it.install(env.archive_options())
            hooks_path = env.codex / "hooks.json"
            document = json.loads(hooks_path.read_text(encoding="utf-8"))
            document["hooks"]["Stop"] = [{"hooks": [hook_command("added_later")]}]
            hooks_path.write_bytes(it._json_bytes(document))
            before = hooks_path.read_bytes()

            second = it.install(env.archive_options())

            self.assertEqual(first["status"], "committed")
            self.assertEqual(second["status"], "already_committed")
            self.assertEqual(hooks_path.read_bytes(), before)
            self.assertEqual(
                json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]["Stop"],
                document["hooks"]["Stop"],
            )

    def test_hooks_install_failure_restores_original_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            hooks_path = env.codex / "hooks.json"
            original = it._json_bytes(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": ".*",
                                "hooks": [
                                    hook_command("keep"),
                                    legacy_lifecycle_hook_command(),
                                ],
                            }
                        ]
                    }
                }
            )
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_bytes(original)
            with mock.patch.dict(
                os.environ,
                {
                    "CCOS_INSTALL_TEST_MODE": "1",
                    "CCOS_INSTALL_TEST_FAIL_AFTER": "PROMOTION:last",
                },
                clear=False,
            ):
                with self.assertRaises(it.InjectedFailure):
                    it.install(env.archive_options())
            self.assertEqual(hooks_path.read_bytes(), original)
            self.assertFalse((env.codex / "coding-os").exists())

    def test_tampered_owned_campaign_hook_blocks_reinstall(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            it.install(env.archive_options())
            hooks_path = env.codex / "hooks.json"
            document = json.loads(hooks_path.read_text(encoding="utf-8"))
            event, index = it._campaign_hook_group_locations(document)[0]
            document["hooks"][event][index]["matcher"] = ".*"
            hooks_path.write_bytes(it._json_bytes(document))
            before = hooks_path.read_bytes()

            with self.assertRaisesRegex(it.OwnershipError, "changed or duplicated"):
                it.install(env.archive_options())

            self.assertEqual(hooks_path.read_bytes(), before)

    def test_requested_legacy_archive_is_read_only_and_survives_uninstall(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            write_text(env.legacy_state / "case-state.json", '{"cases": {}}\n')
            before = sha(env.legacy_state / "case-state.json")

            result = it.install(env.archive_options(archive_legacy_state=True))

            self.assertEqual(result["status"], "committed")
            self.assertEqual(sha(env.legacy_state / "case-state.json"), before)
            archives = list((env.codex / "coding-os-state/legacy-archives").glob("legacy-*"))
            self.assertEqual(len(archives), 1)
            self.assertTrue((archives[0] / "archive-manifest.json").is_file())
            with closing(sqlite3.connect(env.state_db)) as database:
                self.assertEqual(
                    database.execute("SELECT COUNT(*) FROM legacy_archives").fetchone()[0],
                    1,
                )

            it.uninstall(it.UninstallOptions(skills_root=env.skills, codex_home=env.codex))
            self.assertEqual(sha(env.legacy_state / "case-state.json"), before)
            self.assertTrue(env.state_db.is_file())
            self.assertTrue(archives[0].is_dir())

    def test_invalid_legacy_archive_fails_before_live_install_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            write_text(env.legacy_state / "case-state.json", "{invalid-json")

            with self.assertRaisesRegex(it.TransactionError, "legacy archive preflight"):
                it.install(env.archive_options(archive_legacy_state=True))

            self.assertFalse((env.codex / "coding-os").exists())
            self.assertFalse((env.codex / "coding-os-state").exists())

    def test_install_and_uninstall_use_target_local_workspaces_across_filesystems(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))

            def synthetic_device(path: Path) -> int:
                resolved = Path(path).resolve(strict=False)
                if it._path_is_within(resolved, env.skills):
                    return 2
                if it._path_is_within(resolved, env.codex):
                    return 3
                return 1

            with mock.patch.object(it, "_device_id", side_effect=synthetic_device):
                self.assertEqual(it.install(env.archive_options())["status"], "committed")
                self.assertEqual(
                    it.uninstall(it.UninstallOptions(skills_root=env.skills, codex_home=env.codex))["status"],
                    "uninstalled",
                )

            journals = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (env.codex / ".coding-os-install/transactions").glob("*/journal.json")
            ]
            self.assertEqual({journal["operation"] for journal in journals}, {"install", "uninstall"})
            for journal in journals:
                workspaces = {
                    role: Path(value) for role, value in journal["transaction_workspaces"].items()
                }
                self.assertTrue(it._path_is_within(workspaces["skills"], env.skills))
                self.assertTrue(it._path_is_within(workspaces["codex_home"], env.codex))
                for target in journal["targets"]:
                    live = Path(target["live_path"])
                    rollback = Path(target["rollback_path"])
                    role = "skills" if target["target_id"].startswith("skill:") else "codex_home"
                    self.assertTrue(it._path_is_within(rollback, workspaces[role]))
                    self.assertEqual(synthetic_device(rollback), synthetic_device(live.parent))
                    if target["staged_path"] is not None:
                        staged = Path(target["staged_path"])
                        self.assertTrue(it._path_is_within(staged, workspaces[role]))
                        self.assertEqual(synthetic_device(staged), synthetic_device(live.parent))
                self.assertTrue(all(not workspace.exists() for workspace in workspaces.values()))

    def test_unowned_skill_collision_fails_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            write_text(env.skills / "alpha/owner.txt", "not ours")
            with self.assertRaises(it.OwnershipError):
                it.install(env.archive_options())
            self.assertEqual((env.skills / "alpha/owner.txt").read_text(), "not ours")
            self.assertFalse((env.codex / "coding-os").exists())

    def test_unowned_campaign_hook_collision_fails_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            hook = env.codex / "hooks/campaign-engine/user-hook.py"
            write_text(hook, "# user owned\n")
            before = sha(hook)
            with self.assertRaises(it.OwnershipError):
                it.install(env.archive_options())
            self.assertEqual(sha(hook), before)
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

    def test_policy_sync_requires_clean_exact_git_source_and_explicit_authority(self) -> None:
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
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options(policy_authority_source=None))
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options(policy_authority_reference=""))

    def test_policy_sync_migrates_only_managed_files_and_records_runtime_pin(self) -> None:
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
            self.assertEqual(manifest["authority"]["source"], "explicit-user-approval")
            self.assertEqual(manifest["authority"]["reference"], "synthetic-user-approval")
            self.assertIsNone(manifest["authority"]["campaign"])
            pin = manifest["runtime_pin"]
            self.assertEqual(set(pin), set(it.RUNTIME_PIN_FIELDS))
            self.assertEqual(pin["source_commit"], env.commit)
            self.assertEqual(pin["bundle_digest"], env.bundle_hash)
            self.assertEqual(pin["install_transaction"], result["transaction_id"])
            self.assertEqual(pin["protocol_version"], "ccos-campaign-v1")
            self.assertEqual(pin["schema_compatibility"], "campaign-store-v1")
            self.assertEqual(
                pin["host_capability_probe_version"],
                "native-bind-before-turn-scoped-tools-v3",
            )
            self.assertEqual(manifest["source"]["git_commit"], pin["source_commit"])
            self.assertEqual(manifest["package"]["bundle_sha256"], pin["bundle_digest"])
            self.assertTrue(env.state_db.is_file())
            self.assertTrue((env.codex / "hooks/campaign-engine/campaign_hook.py").is_file())
            self.assertEqual(
                manifest["targets"]["campaign_hook"]["sha256"],
                it._tree_hash(env.codex / "hooks/campaign-engine"),
            )
            it._verify_split_payload_layout(manifest, env.skills, env.codex / "coding-os")

    def test_policy_sync_migrates_real_layout_and_appends_markerless_rules(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            original_agents, original_rules = env.prepare_real_layout_policy()

            result = it.install(env.policy_options())

            self.assertEqual(result["status"], "committed")
            agents = (env.codex / "AGENTS.md").read_bytes()
            rules = (env.codex / "rules/default.rules").read_bytes()
            self.assertTrue(agents.startswith(b"# Global Codex Rules\npreserved-prefix\n"))
            self.assertTrue(agents.endswith(b"## Universal generic workspace\npreserved-suffix\n"))
            self.assertEqual(agents.count(it.AGENTS_START.encode()), 1)
            self.assertNotIn(it.AGENTS_LEGACY_BLOCK_START.encode(), agents)
            self.assertNotIn(it.AGENTS_LEGACY_AUTHORITY_LINE.encode(), agents)
            self.assertNotIn(b"codex-coding-os-master", agents)
            self.assertEqual(rules, original_rules + RULES_POLICY.encode())
            self.assertEqual(rules[: len(original_rules)], original_rules)
            self.assertNotEqual(agents, original_agents)

    def test_policy_sync_rejects_altered_real_layout_before_live_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            original_agents, original_rules = env.prepare_real_layout_policy()
            altered_agents = original_agents.replace(
                b"support-failure fingerprint", b"support failure fingerprint", 1
            )
            (env.codex / "AGENTS.md").write_bytes(altered_agents)

            with self.assertRaisesRegex(it.PolicyMigrationError, "digest"):
                it.install(env.policy_options())

            self.assertEqual((env.codex / "AGENTS.md").read_bytes(), altered_agents)
            self.assertEqual((env.codex / "rules/default.rules").read_bytes(), original_rules)
            self.assertFalse((env.codex / ".coding-os-install/current.json").exists())

    def test_campaign_publication_authority_binds_epochs_node_and_candidate_head(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            result = it.install(env.campaign_policy_options())
            self.assertEqual(result["status"], "committed")
            manifest = json.loads((env.codex / "coding-os/install-manifest.json").read_text())
            campaign = manifest["authority"]["campaign"]
            self.assertEqual(campaign["campaign_id"], env.campaign_id)
            self.assertEqual(campaign["node_id"], env.node_id)
            self.assertEqual(campaign["candidate_head"], env.commit)
            self.assertEqual(campaign["authority_epoch"], env.authority_epoch)
            self.assertEqual(campaign["cancellation_epoch"], env.cancellation_epoch)
            self.assertEqual(campaign["effect_kind"], "EXACT_FILE_REPLACE")

        for override in (
            {"publication_authority_epoch": 4},
            {"publication_cancellation_epoch": 1},
            {"publication_node_id": "other-node"},
        ):
            with self.subTest(override=override), tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
                env = SyntheticEnvironment(Path(raw), git_source=True)
                env.prepare_legacy_policy()
                with self.assertRaises(it.AuthorityError):
                    it.install(env.campaign_policy_options(**override))

        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            options = env.campaign_policy_options()
            env.write_campaign_authority(candidate_head="0" * 40)
            with self.assertRaises(it.AuthorityError):
                it.install(options)

    def test_legacy_authority_entrypoints_are_absent(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for retired in (
            "--authority-case-id",
            "--authority-actor-thread-id",
            "--authority-request-id",
            "--case-state-engine",
            "--case-state-root",
            "action-check",
            "ANTI_LOOP_LATCH",
        ):
            self.assertNotIn(retired, source)
        parser = it.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "install",
                    "--source-root", "source",
                    "--skills-root", "skills",
                    "--codex-home", "codex",
                    "--expected-bundle-sha256", "0" * 64,
                    "--expected-source-commit", "0" * 40,
                    "--case-state-engine", "retired.py",
                ]
            )

    def test_policy_sync_replaces_retired_managed_markers_once(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.codex.mkdir(parents=True)
            write_text(
                env.codex / "AGENTS.md",
                "before\n" + it.RETIRED_AGENTS_START + "\nretired\n" + it.RETIRED_AGENTS_END + "\nafter\n",
            )
            write_text(
                env.codex / "rules/default.rules",
                "before\n" + it.RETIRED_RULES_START + "\nretired\n" + it.RETIRED_RULES_END + "\nafter\n",
            )
            it.install(env.policy_options())
            agents = (env.codex / "AGENTS.md").read_text(encoding="utf-8")
            rules = (env.codex / "rules/default.rules").read_text(encoding="utf-8")
            self.assertEqual(agents.count(it.AGENTS_START), 1)
            self.assertNotIn(it.RETIRED_AGENTS_START, agents)
            self.assertEqual(rules.count(it.RULES_START), 1)
            self.assertNotIn(it.RETIRED_RULES_START, rules)

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

    def test_forced_process_termination_recovers_and_records_one_runtime_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            ready = Path(raw) / "forced-termination-ready.txt"
            command = [
                sys.executable,
                "-B",
                str(MODULE_PATH),
                "--json",
                "install",
                "--source-root",
                str(env.source),
                "--skills-root",
                str(env.skills),
                "--codex-home",
                str(env.codex),
                "--expected-bundle-sha256",
                env.bundle_hash,
                "--expected-source-commit",
                "a" * 40,
                "--archive-mode",
            ]
            process_env = os.environ.copy()
            process_env.update(
                {
                    "CCOS_INSTALL_TEST_MODE": "1",
                    "CCOS_INSTALL_TEST_FAIL_AFTER": "PROMOTION:middle",
                    "CCOS_INSTALL_TEST_PAUSE_AFTER": "1",
                    "CCOS_INSTALL_TEST_READY_FILE": str(ready),
                }
            )
            process_env.pop("CCOS_INSTALL_TEST_HARD_CRASH", None)
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=process_env,
            )
            deadline = time.monotonic() + 10
            while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            if not ready.is_file():
                stdout, stderr = process.communicate(timeout=2)
                self.fail(f"installer did not reach forced-termination point: {stdout} {stderr}")
            process.kill()
            process.communicate(timeout=5)
            self.assertNotEqual(process.returncode, 0)

            result = it.install(env.archive_options())

            self.assertEqual(result["status"], "committed")
            journals = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (env.codex / ".coding-os-install/transactions").glob("*/journal.json")
            ]
            self.assertTrue(any(item.get("outcome") == "rolled_back" for item in journals))
            with closing(sqlite3.connect(env.state_db)) as database:
                count = database.execute("SELECT COUNT(*) FROM runtime_installations").fetchone()[0]
            self.assertEqual(count, 1)

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
                        expected_source_commit="0" * 40,
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

    def test_policy_sync_uses_the_supplied_campaign_policy_bundle_id(self) -> None:
        bundle_id = "campaign-engine-policy-v1-synthetic"
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            result = it.install(env.policy_options(universal_bundle_id=bundle_id))
            self.assertEqual(result["status"], "committed")
            manifest = json.loads((env.codex / "coding-os/install-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["authority"]["universal_bundle"], bundle_id)
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options(universal_bundle_id="unsafe bundle"))
            with self.assertRaises(it.AuthorityError):
                it.install(env.policy_options(universal_bundle_id=""))

    def test_cli_exposes_campaign_authority_legacy_archive_and_overlap_inputs(self) -> None:
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
                "--expected-source-commit",
                "1" * 40,
                "--universal-bundle-id",
                "campaign-engine-policy-v1-synthetic",
                "--policy-authority-source",
                "campaign-publication-authority",
                "--policy-authority-reference",
                "campaign-receipt",
                "--publication-campaign-id",
                "campaign-1",
                "--publication-node-id",
                "install-runtime",
                "--publication-authority-epoch",
                "4",
                "--publication-cancellation-epoch",
                "0",
                "--archive-legacy-state",
                "--legacy-overlap-migration",
            ]
        )
        self.assertTrue(install_args.legacy_overlap_migration)
        self.assertEqual(
            install_args.universal_bundle_id,
            "campaign-engine-policy-v1-synthetic",
        )
        self.assertEqual(install_args.policy_authority_source, "campaign-publication-authority")
        self.assertEqual(install_args.publication_campaign_id, "campaign-1")
        self.assertEqual(install_args.publication_node_id, "install-runtime")
        self.assertEqual(install_args.publication_authority_epoch, 4)
        self.assertEqual(install_args.publication_cancellation_epoch, 0)
        self.assertTrue(install_args.archive_legacy_state)
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


class CanonicalNestedLayoutTests(unittest.TestCase):
    def test_public_install_and_uninstall_wrappers_default_to_codex_home_skills(self) -> None:
        root = Path(__file__).resolve().parents[1]
        install_ps = (root / "scripts/install.ps1").read_text(encoding="utf-8")
        uninstall_ps = (root / "scripts/uninstall.ps1").read_text(encoding="utf-8")
        install_sh = (root / "scripts/install.sh").read_text(encoding="utf-8")
        uninstall_sh = (root / "scripts/uninstall.sh").read_text(encoding="utf-8")
        for source in (install_ps, uninstall_ps):
            self.assertIn('$SkillsRoot = Join-Path $CodexHome "skills"', source)
            self.assertNotIn(".agents\\skills", source)
        self.assertIn('skills_root="${SKILLS_ROOT:-}"', install_sh)
        self.assertIn(
            '[[ -n "$skills_root" ]] || skills_root="$codex_home/skills"',
            install_sh,
        )
        self.assertIn(
            'skills_root="${SKILLS_ROOT:-$codex_home/skills}"',
            uninstall_sh,
        )
        for source in (install_sh, uninstall_sh):
            self.assertNotIn(".agents/skills", source)

    def test_clean_canonical_nested_install_reinstall_and_uninstall_need_no_migration_flag(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills = env.codex / "skills"
            options = env.archive_options(skills_root=skills)

            first = it.install(options)
            self.assertEqual(first["status"], "committed")
            manifest_path = env.codex / "coding-os/install-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                Path(manifest["targets"]["skills_root"]).resolve(strict=False),
                skills.resolve(strict=False),
            )
            self.assertNotIn("legacy_overlap_migration", manifest)
            self.assertEqual(it.install(options)["status"], "already_committed")

            removed = it.uninstall(
                it.UninstallOptions(skills_root=skills, codex_home=env.codex)
            )
            self.assertEqual(removed["status"], "uninstalled")
            self.assertFalse((skills / "alpha").exists())
            self.assertFalse((env.codex / "coding-os").exists())


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
            workspaces = {
                role: Path(value) for role, value in journal["transaction_workspaces"].items()
            }
            self.assertTrue(all(it._path_is_within(path, env.codex) for path in workspaces.values()))
            self.assertTrue(all(not it._path_is_within(path, skills) for path in workspaces.values()))
            for value in [*journal["stage_roots"], *journal["rollback_roots"]]:
                self.assertFalse(it._path_is_within(Path(value), skills))
                self.assertTrue(any(it._path_is_within(Path(value), path) for path in workspaces.values()))
            for target in journal["targets"]:
                live = Path(target["live_path"])
                for workspace in workspaces.values():
                    self.assertFalse(it._path_is_within(workspace, live))
                    self.assertFalse(it._path_is_within(live, workspace))
            self.assertTrue(all(not path.exists() for path in workspaces.values()))

    def test_legacy_overlap_uses_codex_local_workspaces_when_codex_parent_is_another_device(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills, _ = env.prepare_legacy_overlap_v2()

            def synthetic_device(path: Path) -> int:
                resolved = Path(path).resolve(strict=False)
                if it._path_is_within(resolved, skills):
                    return 3
                if it._path_is_within(resolved, env.codex):
                    return 3
                return 1

            with mock.patch.object(it, "_device_id", side_effect=synthetic_device):
                self.assertEqual(it.install(env.legacy_overlap_options())["status"], "committed")

            journal_path = next((env.codex / ".coding-os-install/transactions").glob("*/journal.json"))
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            workspaces = [Path(value) for value in journal["transaction_workspaces"].values()]
            self.assertTrue(all(it._path_is_within(path, env.codex) for path in workspaces))
            self.assertTrue(all(not it._path_is_within(path, skills) for path in workspaces))
            self.assertTrue(all(synthetic_device(path) == 3 for path in workspaces))
            self.assertEqual(synthetic_device(env.codex.parent), 1)

    def test_legacy_overlap_rejects_a_nested_skills_mount_before_live_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            skills, legacy_support = env.prepare_legacy_overlap_v2()
            preserved = {
                skills / "alpha/SKILL.md": sha(skills / "alpha/SKILL.md"),
                legacy_support / "legacy-support.txt": sha(legacy_support / "legacy-support.txt"),
            }

            def synthetic_device(path: Path) -> int:
                resolved = Path(path).resolve(strict=False)
                if it._path_is_within(resolved, skills):
                    return 4
                if it._path_is_within(resolved, env.codex):
                    return 3
                return 1

            with mock.patch.object(it, "_device_id", side_effect=synthetic_device):
                with self.assertRaisesRegex(it.TransactionError, "skills transaction workspace must share"):
                    it.install(env.legacy_overlap_options())

            self.assertEqual(preserved, {path: sha(path) for path in preserved})
            self.assertFalse((env.codex / "coding-os").exists())
            self.assertFalse(any(env.codex.glob(".coding-os-transaction-*")))

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

    def test_migrated_v3_overlap_reinstall_and_uninstall_are_idempotent_without_flag(self) -> None:
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
            self.assertEqual(
                it.install(env.archive_options(skills_root=skills))["status"],
                "already_committed",
            )
            self.assertEqual(it.install(env.legacy_overlap_options())["status"], "already_committed")
            result = it.uninstall(
                it.UninstallOptions(skills_root=skills, codex_home=env.codex)
            )
            self.assertEqual(result["status"], "uninstalled")
            self.assertFalse((skills / "alpha").exists())
            self.assertEqual(preserved, {path: sha(path) for path in preserved})


class UninstallTransactionTests(unittest.TestCase):
    def test_uninstall_removes_only_owned_hook_and_never_restores_legacy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            hooks_path = env.codex / "hooks.json"
            initial = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": ".*",
                            "hooks": [
                                hook_command("user_pre"),
                                legacy_lifecycle_hook_command(),
                            ],
                        }
                    ]
                },
                "userSetting": "keep",
            }
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_bytes(it._json_bytes(initial))
            it.install(env.archive_options())
            live = json.loads(hooks_path.read_text(encoding="utf-8"))
            live["hooks"]["Stop"] = [{"hooks": [hook_command("added_after_install")]}]
            hooks_path.write_bytes(it._json_bytes(live))

            result = it.uninstall(
                it.UninstallOptions(skills_root=env.skills, codex_home=env.codex)
            )

            self.assertEqual(result["status"], "uninstalled")
            remaining = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(remaining["userSetting"], "keep")
            self.assertEqual(
                remaining["hooks"]["PreToolUse"][0]["hooks"],
                [hook_command("user_pre")],
            )
            self.assertEqual(
                remaining["hooks"]["Stop"],
                [{"hooks": [hook_command("added_after_install")]}],
            )
            rendered = hooks_path.read_text(encoding="utf-8")
            self.assertNotIn("anti-loop-runtime", rendered)
            self.assertNotIn("campaign_hook.py", rendered)

    def test_uninstall_removes_created_empty_hooks_configuration(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw))
            it.install(env.archive_options())
            hooks_path = env.codex / "hooks.json"
            self.assertTrue(hooks_path.is_file())

            it.uninstall(
                it.UninstallOptions(skills_root=env.skills, codex_home=env.codex)
            )

            self.assertFalse(hooks_path.exists())

    def test_default_reinstall_preserves_managed_policy_bytes_and_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            it.install(env.policy_options())
            agents_path = env.codex / "AGENTS.md"
            rules_path = env.codex / "rules/default.rules"
            agents_path.write_bytes(agents_path.read_bytes() + b"user-agents-setting\n")
            rules_path.write_bytes(rules_path.read_bytes() + b"user-rule-setting\n")
            expected_agents = agents_path.read_bytes()
            expected_rules = rules_path.read_bytes()
            prior_manifest = json.loads(
                (env.codex / "coding-os/install-manifest.json").read_text(
                    encoding="utf-8"
                )
            )

            write_text(env.source / "payload/doc.txt", "payload-v2\n")
            env.bundle = it.build_bundle_manifest(env.source)
            env.bundle_hash = env.bundle["aggregate_sha256"]
            run_git(env.source, "add", ".")
            run_git(env.source, "commit", "-q", "-m", "synthetic bundle v2")
            env.commit = run_git(env.source, "rev-parse", "HEAD")
            options = env.policy_options(
                install_universal_policy=False,
                policy_authority_source=None,
                policy_authority_reference=None,
            )

            result = it.install(options)

            self.assertEqual(result["status"], "committed")
            self.assertEqual(agents_path.read_bytes(), expected_agents)
            self.assertEqual(rules_path.read_bytes(), expected_rules)
            manifest = json.loads(
                (env.codex / "coding-os/install-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["authority"], prior_manifest["authority"])
            self.assertEqual(
                manifest["targets"]["global_agents"],
                prior_manifest["targets"]["global_agents"],
            )
            self.assertEqual(
                manifest["targets"]["default_rules"],
                prior_manifest["targets"]["default_rules"],
            )

    def _assert_preserve_mode_rejects_policy_link(
        self, blocked_relative_path: str
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_legacy_policy()
            it.install(env.policy_options())
            agents_path = env.codex / "AGENTS.md"
            rules_path = env.codex / "rules/default.rules"
            expected_agents = agents_path.read_bytes()
            expected_rules = rules_path.read_bytes()
            blocked = (env.codex / blocked_relative_path).absolute()
            original_link_check = it._is_link_or_reparse
            original_read_bytes = Path.read_bytes
            policy_paths = {agents_path.absolute(), rules_path.absolute()}

            def simulated_link_or_reparse(path: Path) -> bool:
                return path.absolute() == blocked or original_link_check(path)

            def reject_policy_read_before_link_check(path: Path) -> bytes:
                if path.absolute() in policy_paths:
                    raise AssertionError(
                        f"managed policy was read before link rejection: {path}"
                    )
                return original_read_bytes(path)

            preserve = env.policy_options(
                install_universal_policy=False,
                policy_authority_source=None,
                policy_authority_reference=None,
            )
            with (
                mock.patch.object(
                    it,
                    "_is_link_or_reparse",
                    side_effect=simulated_link_or_reparse,
                ),
                mock.patch.object(
                    Path,
                    "read_bytes",
                    new=reject_policy_read_before_link_check,
                ),
                self.assertRaisesRegex(
                    it.TransactionError,
                    "links and reparse points are not allowed",
                ),
            ):
                it.install(preserve)

            self.assertEqual(agents_path.read_bytes(), expected_agents)
            self.assertEqual(rules_path.read_bytes(), expected_rules)

    def test_preserve_mode_rejects_linked_managed_policy_targets(self) -> None:
        for blocked_relative_path in ("AGENTS.md", "rules/default.rules"):
            with self.subTest(target=blocked_relative_path):
                self._assert_preserve_mode_rejects_policy_link(blocked_relative_path)

    def test_preserve_mode_rejects_policy_parent_reparse_component(self) -> None:
        self._assert_preserve_mode_rejects_policy_link("rules")

    def test_policy_install_then_explicit_removal_removes_only_managed_policy_blocks(self) -> None:
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
                remove_universal_policy=True,
                policy_authority_source=None,
                policy_authority_reference=None,
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

    def test_explicit_policy_reinstall_accepts_exact_markerless_campaign_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-tx-test-") as raw:
            env = SyntheticEnvironment(Path(raw), git_source=True)
            env.prepare_real_layout_policy()
            it.install(env.policy_options())
            it.install(
                env.policy_options(
                    install_universal_policy=False,
                    remove_universal_policy=True,
                    policy_authority_source=None,
                    policy_authority_reference=None,
                )
            )
            agents_path = env.codex / "AGENTS.md"
            markerless = agents_path.read_bytes()
            self.assertNotIn(it.AGENTS_START.encode(), markerless)
            self.assertEqual(
                markerless.count(it.AGENTS_CAMPAIGN_AUTHORITY_LINE.encode()), 1
            )
            self.assertIn(it.AGENTS_CAMPAIGN_ROUTING_POINTER.encode(), markerless)

            result = it.install(env.policy_options())

            self.assertEqual(result["status"], "committed")
            restored = agents_path.read_bytes()
            self.assertEqual(restored.count(it.AGENTS_START.encode()), 1)
            self.assertEqual(
                restored.replace(AGENTS_POLICY.encode() + b"\n", b"", 1),
                markerless,
            )

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
