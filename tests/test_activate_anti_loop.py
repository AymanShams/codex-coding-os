from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "activate_anti_loop.py"
SPEC = importlib.util.spec_from_file_location("activate_anti_loop_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
activation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = activation
SPEC.loader.exec_module(activation)


THREAD_ID = "019faced-a7a0-7523-a3c3-4f2e36e4c249"
SOURCE_COMMIT = "a" * 40
BUNDLE_SHA256 = "b" * 64


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ActivationFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "codex home"
        self.home.mkdir()
        self.source = root / "source"
        self.source.mkdir()
        self.python = root / "runtime" / Path(sys.executable).name
        self.python.parent.mkdir()
        shutil.copy2(Path(sys.executable).resolve(strict=True), self.python)
        self.python.chmod(self.python.stat().st_mode | stat.S_IXUSR)
        self.python = self.python.resolve(strict=True)
        self.installed = self.home / "coding-os"
        hook = self.installed / "hooks" / "anti-loop-runtime" / "anti_loop_runtime.py"
        hook.parent.mkdir(parents=True)
        hook.write_text("print('installed')\n", encoding="utf-8")
        self.agents = self.home / "AGENTS.md"
        self.hooks = self.home / "hooks.json"
        self.config = self.home / "config.toml"
        self.store = self.home / "case-state" / "case-state.json"
        self.agents.write_text("global anti-loop policy\n", encoding="utf-8")
        self.hooks.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "^Bash$",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python existing.py",
                                        "commandWindows": "python.exe existing.py",
                                        "timeout": 10,
                                    }
                                ],
                            }
                        ],
                        "PostToolUse": [],
                        "Stop": [{"matcher": ".*", "hooks": []}],
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.config.write_text(
            "[features]\nmemories = true\njs_repl = false\n\n"
            "[sandbox_workspace_write]\nnetwork_access = true\n",
            encoding="utf-8",
        )
        self.store.parent.mkdir()
        self.store.write_text(
            json.dumps({"schema_version": 2, "revision": 278, "cases": []}) + "\n",
            encoding="utf-8",
        )
        self.original = {
            "agents": self.agents.read_bytes(),
            "hooks": self.hooks.read_bytes(),
            "config": self.config.read_bytes(),
            "store": self.store.read_bytes(),
        }

    def identity_patches(self):
        source_identity = {
            "root": self.source,
            "commit": SOURCE_COMMIT,
            "repository": "https://github.com/example/codex-coding-os.git",
            "bundle_sha256": BUNDLE_SHA256,
            "manifest_sha256": "c" * 64,
            "entries": {},
        }
        installed_identity = {
            "root": self.installed,
            "manifest_sha256": "d" * 64,
        }
        return (
            mock.patch.object(activation, "_source_identity", return_value=source_identity),
            mock.patch.object(activation, "_installed_identity", return_value=installed_identity),
        )

    def arguments(self, mode: str, *, current_hashes: bool = False):
        paths = {
            "agents": self.agents,
            "hooks": self.hooks,
            "config": self.config,
            "store": self.store,
        }
        hashes = {name: sha(path) for name, path in paths.items()}
        return {
            "mode": mode,
            "source_root": self.source,
            "codex_home": self.home,
            "expected_source_commit": SOURCE_COMMIT,
            "expected_bundle_sha256": BUNDLE_SHA256,
            "expected_agents_sha256": hashes["agents"],
            "expected_hooks_sha256": hashes["hooks"],
            "expected_config_sha256": hashes["config"],
            "expected_store_sha256": hashes["store"],
            "python_windows": self.python,
            "expected_python_sha256": sha(self.python),
            "authority_thread_id": THREAD_ID,
            "authority_request_id": str(uuid.uuid4()),
            "authority_reference": "user approved universal anti-loop activation",
        }

    def call(self, mode: str, *, writer=activation._atomic_write):
        source_patch, installed_patch = self.identity_patches()
        with (
            mock.patch.dict(os.environ, {"CODEX_THREAD_ID": THREAD_ID}, clear=False),
            source_patch,
            installed_patch,
        ):
            return activation.activate(**self.arguments(mode), writer=writer)


class UniversalActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ccos anti-loop activation ")
        self.fixture = ActivationFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_is_read_only_and_preserves_existing_hook_order(self) -> None:
        result = self.fixture.call("plan")

        self.assertEqual(result["status"], "PLANNED")
        self.assertFalse(result["idempotent"])
        self.assertEqual(self.fixture.hooks.read_bytes(), self.fixture.original["hooks"])
        self.assertEqual(self.fixture.config.read_bytes(), self.fixture.original["config"])
        self.assertEqual(self.fixture.agents.read_bytes(), self.fixture.original["agents"])
        self.assertEqual(self.fixture.store.read_bytes(), self.fixture.original["store"])
        self.assertFalse(
            (self.fixture.home / ".coding-os-install" / "anti-loop-activation.json").exists()
        )

    def test_apply_is_transactional_then_verify_and_reapply_are_idempotent(self) -> None:
        result = self.fixture.call("apply")

        self.assertEqual(result["status"], "ACTIVE")
        self.assertFalse(result["idempotent"])
        document = json.loads(self.fixture.hooks.read_text(encoding="utf-8"))
        pre_groups = document["hooks"]["PreToolUse"]
        post_groups = document["hooks"]["PostToolUse"]
        self.assertEqual(pre_groups[0]["matcher"], "^Bash$")
        self.assertEqual(pre_groups[1]["matcher"], ".*")
        self.assertEqual(post_groups[-1]["matcher"], ".*")
        self.assertEqual(document["hooks"]["Stop"], [{"matcher": ".*", "hooks": []}])
        runtime_hook = (
            self.fixture.installed
            / "hooks"
            / "anti-loop-runtime"
            / "anti_loop_runtime.py"
        ).resolve(strict=True)
        handler = pre_groups[1]["hooks"][0]
        for command_name in ("command", "commandWindows"):
            command = handler[command_name]
            self.assertNotIn("$HOME", command)
            self.assertIn(f'"{self.fixture.python}"', command)
            self.assertIn(f'"{runtime_hook}"', command)
        config = tomllib.loads(self.fixture.config.read_text(encoding="utf-8"))
        self.assertIs(config["features"]["hooks"], True)
        self.assertIs(config["features"]["memories"], True)
        self.assertIs(config["features"]["js_repl"], False)
        self.assertEqual(self.fixture.agents.read_bytes(), self.fixture.original["agents"])
        self.assertEqual(self.fixture.store.read_bytes(), self.fixture.original["store"])

        record_path = self.fixture.home / ".coding-os-install" / "anti-loop-activation.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "ACTIVE")
        self.assertEqual(
            record["python"],
            {"path": str(self.fixture.python), "sha256": sha(self.fixture.python)},
        )
        self.assertEqual(record["record_sha256"], activation._record_digest(record))
        record_before = record_path.read_bytes()

        verified = self.fixture.call("verify")
        reapplied = self.fixture.call("apply")
        self.assertTrue(verified["idempotent"])
        self.assertTrue(reapplied["idempotent"])
        self.assertEqual(record_path.read_bytes(), record_before)

    def test_apply_rejects_hash_drift_before_mutation(self) -> None:
        args = self.fixture.arguments("apply")
        self.fixture.store.write_text('{"changed":true}\n', encoding="utf-8")
        source_patch, installed_patch = self.fixture.identity_patches()

        with (
            mock.patch.dict(os.environ, {"CODEX_THREAD_ID": THREAD_ID}, clear=False),
            source_patch,
            installed_patch,
            self.assertRaisesRegex(activation.ActivationError, "store changed"),
        ):
            activation.activate(**args)

        self.assertEqual(self.fixture.hooks.read_bytes(), self.fixture.original["hooks"])
        self.assertEqual(self.fixture.config.read_bytes(), self.fixture.original["config"])

    def test_partial_record_write_rolls_back_hooks_config_and_record(self) -> None:
        calls = 0

        def failing_writer(path: Path, content: bytes) -> None:
            nonlocal calls
            calls += 1
            activation._atomic_write(path, content)
            if calls == 3:
                raise OSError("injected record failure")

        with self.assertRaisesRegex(activation.ActivationError, "injected record failure"):
            self.fixture.call("apply", writer=failing_writer)

        self.assertEqual(self.fixture.hooks.read_bytes(), self.fixture.original["hooks"])
        self.assertEqual(self.fixture.config.read_bytes(), self.fixture.original["config"])
        self.assertEqual(self.fixture.agents.read_bytes(), self.fixture.original["agents"])
        self.assertEqual(self.fixture.store.read_bytes(), self.fixture.original["store"])
        self.assertFalse(
            (self.fixture.home / ".coding-os-install" / "anti-loop-activation.json").exists()
        )

    def test_verify_fails_closed_when_record_is_missing_or_tampered(self) -> None:
        self.fixture.call("apply")
        record_path = self.fixture.home / ".coding-os-install" / "anti-loop-activation.json"
        record_path.unlink()

        with self.assertRaisesRegex(activation.ActivationError, "record is absent"):
            self.fixture.call("verify")

        hooks_before = self.fixture.hooks.read_bytes()
        config_before = self.fixture.config.read_bytes()
        recovered = self.fixture.call("apply")
        self.assertFalse(recovered["idempotent"])
        self.assertTrue(recovered["reconciled"])
        self.assertEqual(self.fixture.hooks.read_bytes(), hooks_before)
        self.assertEqual(self.fixture.config.read_bytes(), config_before)
        self.fixture.call("verify")

        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["source"]["commit"] = "f" * 40
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(activation.ActivationError, "invalid or belongs"):
            self.fixture.call("verify")

    def test_exact_live_surfaces_reconcile_record_for_new_installed_source(self) -> None:
        self.fixture.call("apply")
        hooks_before = self.fixture.hooks.read_bytes()
        config_before = self.fixture.config.read_bytes()
        new_commit = "e" * 40
        new_bundle = "f" * 64
        source_identity = {
            "root": self.fixture.source,
            "commit": new_commit,
            "repository": "https://github.com/example/codex-coding-os.git",
            "bundle_sha256": new_bundle,
            "manifest_sha256": "1" * 64,
            "entries": {},
        }
        installed_identity = {
            "root": self.fixture.installed,
            "manifest_sha256": "2" * 64,
        }
        arguments = self.fixture.arguments("apply")
        arguments["expected_source_commit"] = new_commit
        arguments["expected_bundle_sha256"] = new_bundle
        with (
            mock.patch.dict(os.environ, {"CODEX_THREAD_ID": THREAD_ID}, clear=False),
            mock.patch.object(
                activation, "_source_identity", return_value=source_identity
            ),
            mock.patch.object(
                activation, "_installed_identity", return_value=installed_identity
            ),
        ):
            reconciled = activation.activate(**arguments)
        self.assertFalse(reconciled["idempotent"])
        self.assertTrue(reconciled["reconciled"])
        self.assertEqual(self.fixture.hooks.read_bytes(), hooks_before)
        self.assertEqual(self.fixture.config.read_bytes(), config_before)
        record_path = (
            self.fixture.home
            / ".coding-os-install"
            / "anti-loop-activation.json"
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["source"]["commit"], new_commit)
        self.assertEqual(record["source"]["bundle_sha256"], new_bundle)
        self.assertEqual(record["record_sha256"], activation._record_digest(record))

    def test_conflicting_runtime_hook_is_rejected_without_replacement(self) -> None:
        document = json.loads(self.fixture.hooks.read_text(encoding="utf-8"))
        document["hooks"]["PreToolUse"].append(
            {
                "matcher": "^Bash$",
                "hooks": [
                    {
                        "type": "command",
                        "command": "python anti_loop_runtime.py",
                        "commandWindows": "python.exe wrong/anti_loop_runtime.py",
                    }
                ],
            }
        )
        self.fixture.hooks.write_text(json.dumps(document) + "\n", encoding="utf-8")
        before = self.fixture.hooks.read_bytes()

        with self.assertRaisesRegex(activation.ActivationError, "conflicting or duplicate"):
            self.fixture.call("apply")

        self.assertEqual(self.fixture.hooks.read_bytes(), before)


    def test_python_link_identity_and_digest_drift_fail_closed(self) -> None:
        args = self.fixture.arguments("plan")
        source_patch, installed_patch = self.fixture.identity_patches()
        real_link_check = activation._is_link_or_reparse

        def forged_link(path: Path) -> bool:
            if path == self.fixture.python:
                return True
            return real_link_check(path)

        with (
            mock.patch.dict(os.environ, {"CODEX_THREAD_ID": THREAD_ID}, clear=False),
            source_patch,
            installed_patch,
            mock.patch.object(
                activation, "_is_link_or_reparse", side_effect=forged_link
            ),
            self.assertRaisesRegex(
                activation.ActivationError, "symbolic link or reparse point"
            ),
        ):
            activation.activate(**args)

        hardlink = self.fixture.root / "runtime" / f"hardlink-{self.fixture.python.name}"
        os.link(self.fixture.python, hardlink)
        try:
            args = self.fixture.arguments("plan")
            args["python_windows"] = hardlink
            source_patch, installed_patch = self.fixture.identity_patches()
            with (
                mock.patch.dict(
                    os.environ,
                    {"CODEX_THREAD_ID": THREAD_ID},
                    clear=False,
                ),
                source_patch,
                installed_patch,
                self.assertRaisesRegex(
                    activation.ActivationError, "hardlinked executable"
                ),
            ):
                activation.activate(**args)
        finally:
            hardlink.unlink()

        args = self.fixture.arguments("plan")
        args["expected_python_sha256"] = "0" * 64
        source_patch, installed_patch = self.fixture.identity_patches()
        with (
            mock.patch.dict(os.environ, {"CODEX_THREAD_ID": THREAD_ID}, clear=False),
            source_patch,
            installed_patch,
            self.assertRaisesRegex(
                activation.ActivationError, "ExpectedPythonSha256"
            ),
        ):
            activation.activate(**args)


class FixedGitResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ccos fixed git ")
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.fixed_git = self.root / ("git.exe" if os.name == "nt" else "git")
        self.fixed_git.write_bytes(b"fixed git")
        self.fixed_git.chmod(self.fixed_git.stat().st_mode | stat.S_IXUSR)
        self.forged_bin = self.root / "forged-bin"
        self.forged_bin.mkdir()
        forged_git = self.forged_bin / self.fixed_git.name
        forged_git.write_bytes(b"forged git")
        forged_git.chmod(forged_git.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run_with_environment(self, environment: dict[str, str]) -> mock.Mock:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="verified\n",
            stderr="",
        )
        run = mock.Mock(return_value=completed)
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.object(
                activation,
                "_fixed_git_candidates",
                return_value=(self.fixed_git,),
            ),
            mock.patch.object(activation.subprocess, "run", run),
        ):
            self.assertEqual(activation._run_git(self.repo, "status"), "verified")
        return run

    def test_forged_path_is_ignored_for_fixed_direct_git(self) -> None:
        run = self._run_with_environment(
            {
                "PATH": str(self.forged_bin),
                "GIT_DIR": str(self.forged_bin),
                "GIT_CONFIG_GLOBAL": str(self.forged_bin / "forged.config"),
            }
        )
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(Path(command[0]), self.fixed_git.resolve(strict=True))
        self.assertNotEqual(Path(command[0]).parent, self.forged_bin)
        self.assertEqual(environment["PATH"], str(self.fixed_git.resolve().parent))
        self.assertNotIn("GIT_DIR", environment)
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)

    def test_fully_missing_path_still_uses_fixed_direct_git(self) -> None:
        run = self._run_with_environment({})
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(Path(command[0]), self.fixed_git.resolve(strict=True))
        self.assertEqual(environment["PATH"], str(self.fixed_git.resolve().parent))

    def test_forged_path_cannot_replace_a_missing_fixed_git(self) -> None:
        missing = self.root / "missing" / self.fixed_git.name
        with (
            mock.patch.dict(
                os.environ,
                {"PATH": str(self.forged_bin)},
                clear=True,
            ),
            mock.patch.object(
                activation,
                "_fixed_git_candidates",
                return_value=(missing,),
            ),
            self.assertRaisesRegex(
                activation.ActivationError, "ambient PATH is not trusted"
            ),
        ):
            activation._resolve_git_executable()


if __name__ == "__main__":
    unittest.main()
