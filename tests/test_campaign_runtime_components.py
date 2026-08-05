from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "scripts" / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from campaign_engine import admission, effects, evidence, host, legacy  # noqa: E402


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def make_repo(root: Path) -> str:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "tests@example.invalid")
    git(root, "config", "user.name", "Campaign Tests")
    git(root, "remote", "add", "origin", "https://token@example.invalid/acme/repo.git")
    (root / "src").mkdir()
    (root / "src" / "one.txt").write_text("one\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    return git(root, "rev-parse", "HEAD")


class AdmissionTests(unittest.TestCase):
    def test_exact_root_remote_and_manual_automated_modes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            head = make_repo(root)
            result = admission.resolve_repository(
                git_root=root,
                worktree=root,
                expected_remote="https://example.invalid/acme/repo",
                expected_branch="main",
                expected_base_sha=head,
                mode="AUTOMATED",
            )
            self.assertTrue(result.clean)
            self.assertNotIn("token", result.normalized_remote)
            clone = Path(raw) / "clone"
            subprocess.run(("git", "clone", "-q", str(root), str(clone)), check=True)
            with self.assertRaises(admission.AdmissionError):
                admission.resolve_repository(
                    git_root=root,
                    worktree=clone,
                    expected_remote="https://example.invalid/acme/repo",
                    expected_branch="main",
                    expected_base_sha=head,
                    mode="AUTOMATED",
                )
            (root / "dirty.txt").write_text("dirty", encoding="utf-8")
            with self.assertRaisesRegex(admission.AdmissionError, "clean"):
                admission.resolve_repository(
                    git_root=root,
                    worktree=root,
                    expected_remote="https://example.invalid/acme/repo",
                    expected_branch="main",
                    expected_base_sha=head,
                    mode="AUTOMATED",
                )
            manual = admission.resolve_repository(
                git_root=root,
                worktree=root,
                expected_remote="https://example.invalid/acme/repo",
                expected_branch="main",
                expected_base_sha=head,
                mode="MANUAL",
            )
            self.assertFalse(manual.clean)

    def test_scope_overlap_and_path_escape(self) -> None:
        self.assertTrue(admission.scopes_overlap(["src/**"], ["src/api/**"]))
        self.assertFalse(admission.scopes_overlap(["src/**"], ["tests/**"]))
        with self.assertRaises(admission.AdmissionError):
            admission.normalize_allowed_path("../outside")
        with self.assertRaises(admission.ScopeOverlapError):
            admission.assert_no_scope_overlap(
                ["src/**"], [{"campaign_id": "other", "allowed_paths": ["src/api/**"]}]
            )

    def test_installed_split_bundle_and_complete_pin(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            support = home / "coding-os"
            skills = home / "skills"
            support.mkdir()
            (support / "scripts").mkdir()
            (support / "scripts" / "runner.py").write_text("# runner\n", encoding="utf-8")
            (skills / "sample").mkdir(parents=True)
            (skills / "sample" / "SKILL.md").write_text("sample\n", encoding="utf-8")
            entries = []
            for relative, path in (
                ("scripts/runner.py", support / "scripts" / "runner.py"),
                (".agents/skills/sample/SKILL.md", skills / "sample" / "SKILL.md"),
            ):
                entries.append(
                    {
                        "path": relative,
                        "size": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            aggregate = admission._aggregate_entries(entries)
            bundle = {
                "protocol": admission.BUNDLE_PROTOCOL,
                "package": {"name": "codex-coding-os", "version": "test"},
                "aggregate_sha256": aggregate,
                "entries": entries,
            }
            bundle_path = support / "install-bundle.manifest.json"
            bundle_path.write_text(json.dumps(bundle, sort_keys=True), encoding="utf-8")
            source_commit = "a" * 40
            transaction = "install-1"
            manifest = {
                "package": {"bundle_sha256": aggregate},
                "source": {
                    "git_commit": source_commit,
                    "bundle_manifest_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
                },
                "transaction": {"id": transaction},
                "targets": {"support_root": str(support), "skills_root": str(skills)},
                "runtime_pin": {
                    "source_commit": source_commit,
                    "bundle_digest": aggregate,
                    "install_transaction": transaction,
                    "protocol_version": admission.RUNTIME_PROTOCOL_VERSION,
                    "schema_compatibility": admission.SCHEMA_COMPATIBILITY,
                    "host_capability_probe_version": admission.HOST_CAPABILITY_PROBE_VERSION,
                },
            }
            (support / "install-manifest.json").write_text(
                json.dumps(manifest, sort_keys=True), encoding="utf-8"
            )
            result = admission.verify_installed_runtime(
                support,
                expected_source_commit=source_commit,
                expected_bundle_digest=aggregate,
                expected_install_transaction=transaction,
            )
            self.assertEqual(result.verified_file_count, 2)
            redirected_support = home / "redirected-support"
            redirected_support.mkdir()
            manifest["targets"]["support_root"] = str(redirected_support)
            (support / "install-manifest.json").write_text(
                json.dumps(manifest, sort_keys=True), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                admission.RuntimePinError, "support_root differs"
            ):
                admission.verify_installed_runtime(
                    support,
                    expected_source_commit=source_commit,
                    expected_bundle_digest=aggregate,
                )
            manifest["targets"]["support_root"] = str(support)
            redirected_skills = home / "redirected-skills"
            redirected_skills.mkdir()
            manifest["targets"]["skills_root"] = str(redirected_skills)
            (support / "install-manifest.json").write_text(
                json.dumps(manifest, sort_keys=True), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                admission.RuntimePinError, "skills_root differs"
            ):
                admission.verify_installed_runtime(
                    support,
                    expected_source_commit=source_commit,
                    expected_bundle_digest=aggregate,
                )
            manifest["targets"] = {
                "support_root": str(support),
                "skills_root": str(skills),
            }
            manifest["runtime_pin"]["schema_compatibility"] = "wrong"
            (support / "install-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(admission.RuntimePinError):
                admission.verify_installed_runtime(
                    support,
                    expected_source_commit=source_commit,
                    expected_bundle_digest=aggregate,
                )


class _FakeTransport:
    def __init__(self, *args, **kwargs) -> None:
        self.calls = []
        self.started = False
        self.closed = False
        self.dynamic_tool_handler = kwargs.get("dynamic_tool_handler")

    def start(self) -> None:
        self.started = True

    def notify(self, method, params=None) -> None:
        self.calls.append((method, params))

    def request(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        if method == "initialize":
            return {"userAgent": "fake"}
        if method == "thread/start":
            return {
                "thread": {
                    "id": "native-thread-1",
                    "cwd": params["cwd"],
                    "source": {},
                    "status": {"type": "idle"},
                    "turns": [],
                },
                "approvalPolicy": "never",
                "cwd": params["cwd"],
                "sandbox": {"type": "readOnly" if params["sandbox"] == "read-only" else "workspaceWrite"},
            }
        if method == "thread/read":
            return {
                "thread": {
                    "id": "native-thread-1",
                    "cwd": str(params),
                    "source": {"native": True},
                    "turns": [],
                }
            }
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    def wait_turn(self, thread_id, turn_id, timeout=None):
        return {"id": turn_id, "status": "completed", "items": []}

    def close(self):
        self.closed = True


class _EarlyToolTransport(_FakeTransport):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.early_tool_result = None

    def request(self, method, params=None, timeout=None):
        if method == "turn/start":
            self.calls.append((method, params))
            self.early_tool_result = self.dynamic_tool_handler(
                {
                    "threadId": "native-thread-1",
                    "turnId": "turn-1",
                    "tool": "campaign_apply_patch",
                    "arguments": {
                        "patch": (
                            "diff --git a/src/one.txt b/src/one.txt\n"
                            "--- a/src/one.txt\n"
                            "+++ b/src/one.txt\n"
                            "@@ -1 +1 @@\n"
                            "-one\n"
                            "+early\n"
                        )
                    },
                }
            )
            return {"turn": {"id": "turn-1"}}
        return super().request(method, params, timeout)


class _ProbeTransport(_FakeTransport):
    def __init__(self, ordinal: int, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.thread_id = f"probe-native-thread-{ordinal}"
        self.turn_id = f"probe-native-turn-{ordinal}"
        self.items = []

    def request(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        if method == "initialize":
            return {"userAgent": "deterministic-probe-transport"}
        if method == "thread/start":
            return {
                "thread": {
                    "id": self.thread_id,
                    "cwd": params["cwd"],
                    "source": {"probe": True},
                    "status": {"type": "idle"},
                    "turns": [],
                },
                "approvalPolicy": "never",
                "cwd": params["cwd"],
                "sandbox": {"type": "readOnly"},
            }
        if method == "turn/start":
            prompt = params["input"][0]["text"]
            if "PROBE_IMPLEMENTER" in prompt:
                read_result = self.dynamic_tool_handler(
                    {
                        "threadId": self.thread_id,
                        "turnId": self.turn_id,
                        "tool": "campaign_read_file",
                        "arguments": {"path": "probe.txt"},
                    }
                )
                self.assert_probe_read(read_result)
                patch_result = self.dynamic_tool_handler(
                    {
                        "threadId": self.thread_id,
                        "turnId": self.turn_id,
                        "tool": "campaign_apply_patch",
                        "arguments": {
                            "patch": (
                                "diff --git a/probe.txt b/probe.txt\n"
                                "--- a/probe.txt\n"
                                "+++ b/probe.txt\n"
                                "@@ -1 +1 @@\n"
                                "-PROBE_OLD\n"
                                "+PROBE_NEW\n"
                            )
                        },
                    }
                )
                commit_result = self.dynamic_tool_handler(
                    {
                        "threadId": self.thread_id,
                        "turnId": self.turn_id,
                        "tool": "campaign_commit",
                        "arguments": {"message": "native probe commit"},
                    }
                )
                self.items = [
                    {
                        "type": "dynamicToolCall",
                        "tool": "campaign_apply_patch",
                        "status": "completed",
                        "output": json.dumps(patch_result),
                    },
                    {
                        "type": "dynamicToolCall",
                        "tool": "campaign_commit",
                        "status": "completed",
                        "output": json.dumps(commit_result),
                    },
                    {"type": "agentMessage", "text": '{"probe":"implemented"}'},
                ]
            elif (
                "PROBE_PARENT_DENIAL" in prompt
                or "PROBE_REVIEWER_DENIAL" in prompt
            ):
                actor = "parent" if "PROBE_PARENT_DENIAL" in prompt else "reviewer"
                try:
                    self.dynamic_tool_handler(
                        {
                            "threadId": self.thread_id,
                            "turnId": self.turn_id,
                            "tool": "campaign_apply_patch",
                            "arguments": {
                                "patch": (
                                    "diff --git a/probe.txt b/probe.txt\n"
                                    "--- a/probe.txt\n"
                                    "+++ b/probe.txt\n"
                                    "@@ -1 +1 @@\n"
                                    "-PROBE_NEW\n"
                                    "+PROBE_DENIED\n"
                                )
                            },
                        }
                    )
                except host.HostAuthorityError as exc:
                    denial = str(exc)
                else:
                    raise AssertionError(f"{actor} write canary was not denied")
                self.items = [
                    {
                        "type": "dynamicToolCall",
                        "tool": "campaign_apply_patch",
                        "status": "failed",
                        "output": denial,
                    },
                    {"type": "agentMessage", "text": '{"probe":"denied"}'},
                ]
            else:
                raise AssertionError("probe prompt marker is missing")
            return {"turn": {"id": self.turn_id}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    @staticmethod
    def assert_probe_read(result) -> None:
        if result.get("text") != "PROBE_OLD":
            raise AssertionError(f"unexpected probe read result: {result}")

    def wait_turn(self, thread_id, turn_id, timeout=None):
        if thread_id != self.thread_id or turn_id != self.turn_id:
            raise AssertionError("probe turn identity mismatch")
        return {
            "id": turn_id,
            "status": "completed",
            "items": list(self.items),
        }


class _ProbeTransportFactory:
    def __init__(self) -> None:
        self.transports = []

    def __call__(self, *args, **kwargs):
        transport = _ProbeTransport(len(self.transports) + 1, *args, **kwargs)
        self.transports.append(transport)
        return transport


class HostTests(unittest.TestCase):
    def lease(
        self,
        root: Path,
        role: str = "IMPLEMENTER",
        *,
        candidate_head: str = "a" * 40,
    ) -> host.ActorLease:
        return host.ActorLease.issue(
            lease_id=f"lease-{role.lower()}",
            request_id=f"request-{role.lower()}",
            campaign_id="campaign-1",
            node_id="node-1",
            actor_id=f"actor-{role.lower()}",
            role=role,
            worktree=str(root),
            allowed_paths=("src/**",) if role in {"IMPLEMENTER", "REPAIRER"} else (),
            authority_epoch=3,
            cancellation_epoch=0,
            fencing_epoch=9,
            candidate_head=candidate_head,
        )

    def test_process_identity_mismatch_refuses_termination(self) -> None:
        expected = {
            "pid": 731,
            "creation_token": "original-process",
            "executable": str(Path("C:/tools/codex.exe")),
        }
        reused_pid = {
            "pid": 731,
            "creation_token": "replacement-process",
            "executable": str(Path("C:/tools/codex.exe")),
        }

        with patch.object(host, "process_identity", return_value=reused_pid), patch.object(
            host, "terminate_process_tree"
        ) as terminate:
            self.assertFalse(host.terminate_verified_process_tree(731, expected))

        terminate.assert_not_called()

    def test_native_thread_is_bound_while_idle_before_turn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "src").mkdir()
            transport = _FakeTransport()
            native = host.NativeCodexHost(transport_factory=lambda *a, **k: transport)
            observed = []
            actions = []

            def bind(lease, identity):
                observed.append((lease, dict(identity), list(transport.calls)))

            def authorize(lease, action, path):
                actions.append((lease.lease_id, action, path))
                return {"authorized": True}

            binding = native.create_idle_actor(
                self.lease(root),
                bind_authority=bind,
                authorize_action=authorize,
            )
            self.assertTrue(binding.bound_before_turn)
            identity = observed[0][1]
            self.assertEqual(binding.sandbox_type, "read-only")
            self.assertEqual(identity["sandbox_type"], "read-only")
            self.assertEqual(identity["writable_roots"], [])
            self.assertEqual(identity["mediated_write_scope"], ["src/**"])
            self.assertEqual(identity["native_write_mode"], "scoped-dynamic-tools")
            self.assertEqual(len(identity["dynamic_tool_digest"]), 64)
            self.assertNotIn("turn/start", [method for method, _ in observed[0][2]])
            thread_start = next(
                params for method, params in transport.calls if method == "thread/start"
            )
            self.assertEqual(thread_start["sandbox"], "read-only")
            self.assertEqual(
                {item["name"] for item in thread_start["dynamicTools"]},
                {
                    "campaign_list_files",
                    "campaign_read_file",
                    "campaign_search",
                    "campaign_git_status",
                    "campaign_git_diff",
                    "campaign_apply_patch",
                    "campaign_commit",
                },
            )
            self.assertEqual(
                identity["dynamic_tool_digest"],
                host._digest(thread_start["dynamicTools"]),
            )
            native.start_actor_turn(binding.lease.lease_id, "implement")
            self.assertIn("turn/start", [method for method, _ in transport.calls])
            self.assertEqual(actions, [])
            with self.assertRaises(host.HostAuthorityError):
                native.start_actor_turn(binding.lease.lease_id, "again")

    def test_early_dynamic_tool_binds_exact_turn_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            make_repo(root)
            transport = _EarlyToolTransport()
            native = host.NativeCodexHost(
                transport_factory=lambda *a, **k: transport
            )
            binding = native.create_idle_actor(
                self.lease(root),
                bind_authority=lambda *_: None,
                authorize_action=lambda *_: {"authorized": True},
            )
            started = native.start_actor_turn(binding.lease.lease_id, "implement")
            self.assertEqual(started.turn_id, "turn-1")
            self.assertTrue(started.lease_consumed)
            self.assertEqual(
                transport.early_tool_result,
                {"applied": True, "paths": ["src/one.txt"]},
            )
            self.assertEqual(
                (root / "src" / "one.txt").read_text(encoding="utf-8"),
                "early\n",
            )
            native.close()

    def test_reviewer_rejects_head_change_before_first_turn(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            frozen_head = make_repo(root)
            transport = _FakeTransport()
            native = host.NativeCodexHost(
                transport_factory=lambda *a, **k: transport
            )
            lease = self.lease(
                root, "REVIEWER", candidate_head=frozen_head
            )
            native.create_idle_actor(lease, bind_authority=lambda *_: None)
            (root / "src" / "one.txt").write_text("new head\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "candidate moved")
            with self.assertRaisesRegex(
                host.HostAuthorityError, "differs from frozen candidate"
            ):
                native.start_actor_turn(lease.lease_id, "review")
            native.close()

    def test_reviewer_rejects_head_change_before_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            frozen_head = make_repo(root)
            transport = _FakeTransport()
            native = host.NativeCodexHost(
                transport_factory=lambda *a, **k: transport
            )
            lease = self.lease(
                root, "REVIEWER", candidate_head=frozen_head
            )
            binding = native.create_idle_actor(
                lease, bind_authority=lambda *_: None
            )
            identity = native._review_candidate_identities[lease.lease_id]
            self.assertEqual(identity["head"], frozen_head)
            self.assertEqual(len(identity["tree"]), 40)
            native.start_actor_turn(binding.lease.lease_id, "review")
            (root / "src" / "one.txt").write_text("late head\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "candidate moved late")
            with self.assertRaisesRegex(
                host.HostAuthorityError, "differs from frozen candidate"
            ):
                native.collect_terminal_receipt(
                    lease.lease_id,
                    current_epochs=lambda *_: {
                        "authority_epoch": 3,
                        "cancellation_epoch": 0,
                        "fencing_epoch": 9,
                    },
                )
            native.close()

    def test_cancel_during_patch_rolls_back_before_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            make_repo(root)
            transport = _FakeTransport()
            native = host.NativeCodexHost(
                transport_factory=lambda *a, **k: transport
            )
            cancelled = [False]

            def authorize(_lease, _action, _path):
                if cancelled[0]:
                    raise host.HostAuthorityError("campaign was cancelled")
                return {"authorized": True}

            binding = native.create_idle_actor(
                self.lease(root),
                bind_authority=lambda *_: None,
                authorize_action=authorize,
            )
            binding = native.start_actor_turn(binding.lease.lease_id, "implement")
            patch_text = (
                "diff --git a/src/one.txt b/src/one.txt\n"
                "--- a/src/one.txt\n"
                "+++ b/src/one.txt\n"
                "@@ -1 +1 @@\n"
                "-one\n"
                "+cancelled\n"
            )
            original_git = host._git_read

            def racing_git(root_arg, *arguments, input_text=None):
                result = original_git(
                    root_arg, *arguments, input_text=input_text
                )
                if (
                    arguments
                    and arguments[0] == "apply"
                    and "--check" not in arguments
                    and "--reverse" not in arguments
                ):
                    cancelled[0] = True
                return result

            with patch.object(host, "_git_read", side_effect=racing_git):
                with self.assertRaisesRegex(
                    host.HostAuthorityError, "cancelled"
                ):
                    transport.dynamic_tool_handler(
                        {
                            "threadId": binding.native_thread_id,
                            "turnId": binding.turn_id,
                            "tool": "campaign_apply_patch",
                            "arguments": {"patch": patch_text},
                        }
                    )
            self.assertEqual(
                (root / "src" / "one.txt").read_text(encoding="utf-8"),
                "one\n",
            )
            self.assertEqual(git(root, "status", "--porcelain"), "")
            native.close()

    def test_cancel_during_commit_rolls_back_ref_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            old_head = make_repo(root)
            (root / "src" / "one.txt").write_text("worker bytes\n", encoding="utf-8")
            transport = _FakeTransport()
            native = host.NativeCodexHost(
                transport_factory=lambda *a, **k: transport
            )
            cancelled = [False]

            def authorize(_lease, _action, _path):
                if cancelled[0]:
                    raise host.HostAuthorityError("campaign was cancelled")
                return {"authorized": True}

            binding = native.create_idle_actor(
                self.lease(root),
                bind_authority=lambda *_: None,
                authorize_action=authorize,
            )
            binding = native.start_actor_turn(binding.lease.lease_id, "commit")
            original_git = host._git_read
            update_seen = [False]

            def racing_git(root_arg, *arguments, input_text=None):
                result = original_git(
                    root_arg, *arguments, input_text=input_text
                )
                update_index = (
                    arguments.index("update-ref")
                    if "update-ref" in arguments
                    else -1
                )
                if (
                    update_index >= 0
                    and len(arguments) > update_index + 1
                    and arguments[update_index + 1] == "HEAD"
                    and not update_seen[0]
                ):
                    update_seen[0] = True
                    cancelled[0] = True
                return result

            with patch.object(host, "_git_read", side_effect=racing_git):
                with self.assertRaisesRegex(
                    host.HostAuthorityError, "cancelled"
                ):
                    transport.dynamic_tool_handler(
                        {
                            "threadId": binding.native_thread_id,
                            "turnId": binding.turn_id,
                            "tool": "campaign_commit",
                            "arguments": {"message": "must not commit"},
                        }
                    )
            self.assertTrue(update_seen[0])
            self.assertEqual(git(root, "rev-parse", "HEAD"), old_head)
            self.assertEqual(git(root, "diff", "--cached", "--name-only"), "")
            self.assertEqual(
                (root / "src" / "one.txt").read_text(encoding="utf-8"),
                "worker bytes\n",
            )
            native.close()

    def test_canonical_deletion_patch_is_scoped_and_committed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            make_repo(root)
            transport = _FakeTransport()
            native = host.NativeCodexHost(
                transport_factory=lambda *a, **k: transport
            )
            binding = native.create_idle_actor(
                self.lease(root),
                bind_authority=lambda *_: None,
                authorize_action=lambda *_: {"authorized": True},
            )
            binding = native.start_actor_turn(binding.lease.lease_id, "delete")
            result = transport.dynamic_tool_handler(
                {
                    "threadId": binding.native_thread_id,
                    "turnId": binding.turn_id,
                    "tool": "campaign_apply_patch",
                    "arguments": {
                        "patch": (
                            "diff --git a/src/one.txt b/src/one.txt\n"
                            "deleted file mode 100644\n"
                            "--- a/src/one.txt\n"
                            "+++ /dev/null\n"
                            "@@ -1 +0,0 @@\n"
                            "-one\n"
                        )
                    },
                }
            )
            self.assertEqual(result["paths"], ["src/one.txt"])
            self.assertFalse((root / "src" / "one.txt").exists())
            committed = transport.dynamic_tool_handler(
                {
                    "threadId": binding.native_thread_id,
                    "turnId": binding.turn_id,
                    "tool": "campaign_commit",
                    "arguments": {"message": "delete scoped file"},
                }
            )
            self.assertTrue(committed["committed"])
            self.assertEqual(
                git(root, "diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD"),
                "D\tsrc/one.txt",
            )
            self.assertEqual(git(root, "status", "--porcelain"), "")
            native.close()

    def test_removed_hunk_line_starting_with_file_header_marker_is_product_text(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            make_repo(root)
            (root / "src" / "one.txt").write_text("-- old value\n", encoding="utf-8")
            git(root, "add", "src/one.txt")
            git(root, "commit", "-q", "-m", "dash-prefixed source")
            transport = _FakeTransport()
            native = host.NativeCodexHost(
                transport_factory=lambda *a, **k: transport
            )
            binding = native.create_idle_actor(
                self.lease(root),
                bind_authority=lambda *_: None,
                authorize_action=lambda *_: {"authorized": True},
            )
            binding = native.start_actor_turn(binding.lease.lease_id, "patch")
            patch_text = (
                "diff --git a/src/one.txt b/src/one.txt\n"
                "--- a/src/one.txt\n"
                "+++ b/src/one.txt\n"
                "@@ -1 +1 @@\n"
                "--- old value\n"
                "+new value\n"
            )

            self.assertEqual(host._patch_paths(patch_text), ("src/one.txt",))
            result = transport.dynamic_tool_handler(
                {
                    "threadId": binding.native_thread_id,
                    "turnId": binding.turn_id,
                    "tool": "campaign_apply_patch",
                    "arguments": {"patch": patch_text},
                }
            )

            self.assertEqual(result["paths"], ["src/one.txt"])
            self.assertEqual(
                (root / "src" / "one.txt").read_text(encoding="utf-8"),
                "new value\n",
            )
            native.close()

    def test_capability_probe_executes_write_and_denial_canaries_disposably(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            requested_root = Path(raw) / "product"
            requested_root.mkdir()
            sentinel = requested_root / "sentinel.txt"
            sentinel.write_text("untouched\n", encoding="utf-8")
            factory = _ProbeTransportFactory()
            result = host.probe_native_host_capability(
                executable=sys.executable,
                cwd=requested_root,
                transport_factory=factory,
                turn_timeout_seconds=5,
            )
            self.assertTrue(result["implementer_turn_started"])
            self.assertTrue(result["scoped_dynamic_tools"])
            self.assertTrue(result["read_only_denial_proven"])
            self.assertEqual(result["read_only_denial_attempts"], 2)
            self.assertEqual(
                result["denial_canary_write_tool_names"], ["campaign_apply_patch"]
            )
            self.assertNotEqual(result["base_head"], result["candidate_head"])
            self.assertEqual(result["parent_role"], "PARENT")
            self.assertTrue(result["parent_idle_before_turn"])
            self.assertTrue(result["parent_bound_before_turn"])
            self.assertTrue(result["parent_turn_started"])
            self.assertTrue(result["parent_turn_completed"])
            self.assertEqual(result["parent_write_attempts"], 1)
            self.assertTrue(result["parent_write_denied"])
            self.assertEqual(result["parent_head"], result["candidate_head"])
            self.assertEqual(result["parent_tree"], result["candidate_tree"])
            self.assertEqual(result["parent_status"], result["candidate_status"])
            self.assertEqual(result["parent_content"], result["candidate_content"])
            self.assertEqual(result["reviewer_role"], "REVIEWER")
            self.assertTrue(result["reviewer_idle_before_turn"])
            self.assertTrue(result["reviewer_bound_before_turn"])
            self.assertTrue(result["reviewer_turn_completed"])
            self.assertEqual(result["reviewer_write_attempts"], 1)
            self.assertTrue(result["reviewer_write_denied"])
            self.assertEqual(result["reviewer_head"], result["candidate_head"])
            self.assertEqual(result["reviewer_candidate_tree"], result["candidate_tree"])
            self.assertEqual(result["reviewer_tree"], result["candidate_tree"])
            self.assertEqual(result["reviewer_status"], result["candidate_status"])
            self.assertEqual(result["reviewer_content"], result["candidate_content"])
            self.assertTrue(result["actors_bound_only_to_disposable_repository"])
            self.assertFalse(result["requested_root_used_as_actor_worktree"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched\n")
            self.assertEqual(len(factory.transports), 3)
            for transport in factory.transports[1:]:
                thread_start = next(
                    params
                    for method, params in transport.calls
                    if method == "thread/start"
                )
                names = {item["name"] for item in thread_start["dynamicTools"]}
                self.assertIn("campaign_apply_patch", names)
                self.assertNotIn("campaign_commit", names)

    def test_recorded_live_capability_evidence_is_exact_and_complete(self) -> None:
        proof = json.loads(
            (ROOT / "tests" / "fixtures" / "native-host-capability-live-proof.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(proof["execution"], "live-codex-app-server")
        self.assertEqual(
            proof["probe_version"], admission.HOST_CAPABILITY_PROBE_VERSION
        )
        self.assertTrue(proof["idle_before_turn"])
        self.assertTrue(proof["bind_before_turn"])
        self.assertTrue(proof["implementer_turn_started"])
        self.assertTrue(proof["implementer_turn_completed"])
        self.assertGreaterEqual(proof["mediated_action_counts"]["APPLY_PATCH"], 2)
        self.assertGreaterEqual(proof["mediated_action_counts"]["COMMIT"], 4)
        self.assertEqual(proof["read_only_denial_attempts"], 2)
        self.assertTrue(proof["read_only_denial_proven"])
        self.assertEqual(
            proof["denial_canary_write_tool_names"], ["campaign_apply_patch"]
        )
        self.assertNotEqual(proof["base_head"], proof["candidate_head"])
        self.assertEqual(proof["parent_role"], "PARENT")
        self.assertTrue(proof["parent_idle_before_turn"])
        self.assertTrue(proof["parent_bound_before_turn"])
        self.assertTrue(proof["parent_turn_started"])
        self.assertTrue(proof["parent_turn_completed"])
        self.assertEqual(proof["parent_write_attempts"], 1)
        self.assertTrue(proof["parent_write_denied"])
        self.assertEqual(proof["candidate_head"], proof["parent_head"])
        self.assertEqual(proof["candidate_tree"], proof["parent_tree"])
        self.assertEqual(proof["candidate_status"], proof["parent_status"])
        self.assertEqual(proof["candidate_content"], proof["parent_content"])
        self.assertEqual(proof["reviewer_role"], "REVIEWER")
        self.assertTrue(proof["reviewer_idle_before_turn"])
        self.assertTrue(proof["reviewer_bound_before_turn"])
        self.assertTrue(proof["reviewer_turn_completed"])
        self.assertEqual(proof["candidate_head"], proof["reviewer_head"])
        self.assertEqual(proof["candidate_tree"], proof["reviewer_tree"])
        self.assertEqual(proof["candidate_status"], "CLEAN")
        self.assertEqual(proof["reviewer_status"], "CLEAN")
        self.assertEqual(proof["candidate_content"], "PROBE_NEW\n")
        self.assertEqual(proof["reviewer_content"], "PROBE_NEW\n")
        self.assertGreaterEqual(proof["reviewer_write_attempts"], 1)
        self.assertTrue(proof["reviewer_write_denied"])
        self.assertTrue(proof["reviewer_turn_started"])
        self.assertTrue(proof["disposable_repository"])
        self.assertTrue(proof["actors_bound_only_to_disposable_repository"])
        self.assertFalse(proof["requested_root_used_as_actor_worktree"])
        self.assertFalse(proof["product_state_mutated"])
        for key in (
            "base_head",
            "candidate_head",
            "candidate_tree",
            "parent_head",
            "parent_tree",
            "reviewer_head",
            "reviewer_tree",
        ):
            self.assertRegex(proof[key], r"^[0-9a-f]{40}$")
        for key in (
            "host_executable_sha256",
            "dynamic_tool_digest",
            "denial_canary_dynamic_tool_digest",
            "implementer_receipt_digest",
            "parent_receipt_digest",
            "reviewer_receipt_digest",
            "evidence_digest",
        ):
            self.assertRegex(proof[key], r"^[0-9a-f]{64}$")
        digest_payload = {
            key: value
            for key, value in proof.items()
            if key
            not in {
                "schema_version",
                "captured_at",
                "execution",
                "elapsed_seconds",
                "evidence_digest",
            }
        }
        self.assertEqual(proof["evidence_digest"], host._digest(digest_payload))

    def test_campaign_commit_disables_repository_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            make_repo(root)
            hook_path = root / ".git" / "hooks" / "post-commit"
            hook_path.write_text(
                "#!/bin/sh\nprintf compromised > outside.txt\n",
                encoding="utf-8",
                newline="\n",
            )
            hook_path.chmod(0o755)
            reference_hook = root / ".git" / "hooks" / "reference-transaction"
            reference_hook.write_text(
                "#!/bin/sh\nprintf compromised > outside-reference.txt\n",
                encoding="utf-8",
                newline="\n",
            )
            reference_hook.chmod(0o755)
            (root / "src" / "one.txt").write_text("changed\n", encoding="utf-8")
            transport = _FakeTransport()
            native = host.NativeCodexHost(
                transport_factory=lambda *a, **k: transport
            )
            binding = native.create_idle_actor(
                self.lease(root),
                bind_authority=lambda *_: None,
                authorize_action=lambda *_: {"authorized": True},
            )
            started = native.start_actor_turn(binding.lease.lease_id, "commit")
            result = transport.dynamic_tool_handler(
                {
                    "threadId": binding.native_thread_id,
                    "turnId": started.turn_id,
                    "tool": "campaign_commit",
                    "arguments": {"message": "scoped change"},
                }
            )
            self.assertTrue(result["committed"])
            self.assertFalse((root / "outside.txt").exists())
            self.assertFalse((root / "outside-reference.txt").exists())
            self.assertEqual(git(root, "status", "--porcelain"), "")
            native.close()

    def test_native_read_only_roles_have_no_write_tools_or_mediated_scope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            head = make_repo(root)
            for role in ("PARENT", "REVIEWER", "CLOSURE_REVIEWER"):
                with self.subTest(role=role):
                    transport = _FakeTransport()
                    native = host.NativeCodexHost(
                        transport_factory=lambda *a, _transport=transport, **k: _transport
                    )
                    identities = []
                    binding = native.create_idle_actor(
                        self.lease(root, role, candidate_head=head),
                        bind_authority=lambda _lease, identity: identities.append(
                            dict(identity)
                        ),
                    )
                    identity = identities[0]
                    self.assertEqual(binding.sandbox_type, "read-only")
                    self.assertEqual(identity["writable_roots"], [])
                    self.assertEqual(identity["mediated_write_scope"], [])
                    self.assertEqual(identity["native_write_mode"], "denied")
                    self.assertEqual(len(identity["dynamic_tool_digest"]), 64)
                    thread_start = next(
                        params
                        for method, params in transport.calls
                        if method == "thread/start"
                    )
                    names = {item["name"] for item in thread_start["dynamicTools"]}
                    self.assertNotIn("campaign_apply_patch", names)
                    self.assertNotIn("campaign_commit", names)
                    self.assertEqual(
                        identity["dynamic_tool_digest"],
                        host._digest(thread_start["dynamicTools"]),
                    )
                    started = native.start_actor_turn(binding.lease.lease_id, "review")
                    with self.assertRaisesRegex(
                        host.HostAuthorityError, "no write tool"
                    ):
                        transport.dynamic_tool_handler(
                            {
                                "threadId": binding.native_thread_id,
                                "turnId": started.turn_id,
                                "tool": "campaign_apply_patch",
                                "arguments": {
                                    "patch": (
                                        "diff --git a/src/one.txt b/src/one.txt\n"
                                        "--- a/src/one.txt\n"
                                        "+++ b/src/one.txt\n"
                                        "@@ -1 +1 @@\n-old\n+new\n"
                                    )
                                },
                            }
                        )
                    native.close()

    def test_native_dynamic_patch_requires_live_authority_and_checks_scope_first(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            make_repo(root)
            transport = _FakeTransport()
            native = host.NativeCodexHost(transport_factory=lambda *a, **k: transport)
            approved = [False]
            actions = []

            def authorize(lease, action, path):
                actions.append((lease.lease_id, action, path))
                if not approved[0]:
                    raise host.HostAuthorityError("action authority denied the write")
                return {"authorized": True}

            binding = native.create_idle_actor(
                self.lease(root),
                bind_authority=lambda *_: None,
                authorize_action=authorize,
            )
            binding = native.start_actor_turn(binding.lease.lease_id, "implement")
            allowed_patch = (
                "diff --git a/src/one.txt b/src/one.txt\n"
                "--- a/src/one.txt\n"
                "+++ b/src/one.txt\n"
                "@@ -1 +1 @@\n"
                "-one\n"
                "+two\n"
            )
            call = {
                "threadId": binding.native_thread_id,
                "turnId": binding.turn_id,
                "tool": "campaign_apply_patch",
                "arguments": {"patch": allowed_patch},
            }
            with self.assertRaisesRegex(host.HostAuthorityError, "denied"):
                transport.dynamic_tool_handler(call)
            self.assertEqual((root / "src" / "one.txt").read_text(encoding="utf-8"), "one\n")
            approved[0] = True
            result = transport.dynamic_tool_handler(call)
            self.assertEqual(result, {"applied": True, "paths": ["src/one.txt"]})
            self.assertEqual((root / "src" / "one.txt").read_text(encoding="utf-8"), "two\n")
            self.assertEqual(
                actions,
                [
                    (binding.lease.lease_id, "APPLY_PATCH", "src/one.txt"),
                    (binding.lease.lease_id, "APPLY_PATCH", "src/one.txt"),
                    (binding.lease.lease_id, "APPLY_PATCH", "src/one.txt"),
                ],
            )

            forbidden_patch = (
                "diff --git a/outside.txt b/outside.txt\n"
                "--- a/outside.txt\n"
                "+++ b/outside.txt\n"
                "@@ -0,0 +1 @@\n"
                "+outside\n"
            )
            with self.assertRaisesRegex(host.HostScopeError, "outside"):
                transport.dynamic_tool_handler(
                    {**call, "arguments": {"patch": forbidden_patch}}
                )
            self.assertFalse((root / "outside.txt").exists())
            self.assertEqual(len(actions), 3)

    def test_fake_host_requires_and_invokes_live_write_callback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "src").mkdir()
            implementer = self.lease(root)
            with self.assertRaisesRegex(host.HostAuthorityError, "action-authority"):
                host.FakeHost().create_idle_actor(
                    implementer, bind_authority=lambda *_: None
                )
            fake = host.FakeHost()
            actions = []

            def authorize(lease, action, path):
                actions.append((lease.lease_id, action, path))
                return {"authorized": True}

            identities = []
            fake.create_idle_actor(
                implementer,
                bind_authority=lambda _lease, identity: identities.append(
                    dict(identity)
                ),
                authorize_action=authorize,
            )
            fake.start_actor_turn(implementer.lease_id, "work")
            self.assertEqual(identities[0]["sandbox_type"], "read-only")
            self.assertEqual(identities[0]["writable_roots"], [])
            self.assertEqual(identities[0]["mediated_write_scope"], ["src/**"])
            self.assertEqual(
                identities[0]["dynamic_tool_digest"],
                host._digest(host._dynamic_tool_specs(True)),
            )
            fake.write_file(implementer.lease_id, "src/ok.txt", b"ok")
            self.assertEqual(
                actions,
                [(implementer.lease_id, "WRITE_FILE", "src/ok.txt")],
            )
            with self.assertRaises(host.HostScopeError):
                fake.write_file(implementer.lease_id, "outside.txt", b"no")
            self.assertEqual(len(actions), 1)
            for role in ("PARENT", "REVIEWER"):
                lease = self.lease(root, role)
                fake.create_idle_actor(lease, bind_authority=lambda *_: None)
                fake.start_actor_turn(lease.lease_id, "read")
                with self.assertRaises(PermissionError):
                    fake.write_file(lease.lease_id, "src/no.txt", b"no")

    def test_late_result_rejected_by_cancellation_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "src").mkdir()
            fake = host.FakeHost()
            lease = self.lease(root)
            fake.create_idle_actor(
                lease,
                bind_authority=lambda *_: None,
                authorize_action=lambda *_: {"authorized": True},
            )
            fake.start_actor_turn(lease.lease_id, "work")
            with self.assertRaises(host.LateResultError):
                fake.collect_terminal_receipt(
                    lease.lease_id,
                    current_epochs=lambda *_: {
                        "authority_epoch": 3,
                        "cancellation_epoch": 1,
                        "fencing_epoch": 9,
                    },
                )


class EvidenceTests(unittest.TestCase):
    def command(self, root: Path, head: str, code: str) -> evidence.TrustedCommand:
        return evidence.TrustedCommand(
            executable=sys.executable,
            arguments=("-c", code),
            working_directory=str(root),
            environment_allowlist=("PATH", "SYSTEMROOT") if os.name == "nt" else ("PATH",),
            environment={},
            timeout_seconds=10,
            output_limit_bytes=4096,
            candidate_head=head,
            expected_working_tree="CLEAN",
            required_exit_code=0,
        )

    def test_trusted_command_requires_real_zero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            head = make_repo(root)
            passed = evidence.execute_trusted_command(self.command(root, head, "print('passed')"))
            self.assertTrue(passed.passed)
            with self.assertRaises(evidence.ValidationFailure) as caught:
                evidence.execute_trusted_command(
                    self.command(root, head, "print('all assertions passed'); raise SystemExit(7)")
                )
            self.assertIsNotNone(caught.exception.evidence)
            self.assertFalse(caught.exception.evidence.passed)
            self.assertEqual(caught.exception.evidence.exit_code, 7)

    def test_head_race_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            head = make_repo(root)
            script = (
                "from pathlib import Path; import subprocess; "
                "Path('new.txt').write_text('new'); "
                "subprocess.run(['git','add','new.txt'],check=True); "
                "subprocess.run(['git','commit','-q','-m','race'],check=True)"
            )
            with self.assertRaises(evidence.HeadRaceError):
                evidence.execute_trusted_command(self.command(root, head, script))

    def test_exact_diff_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            base = make_repo(root)
            (root / "src" / "one.txt").write_text("two\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "candidate")
            head = git(root, "rev-parse", "HEAD")
            result = evidence.exact_repository_evidence(root, base_sha=base, candidate_head=head)
            self.assertEqual(result["head_sha"], head)
            self.assertGreater(result["diff_size"], 0)


class _MemoryEffectStore:
    def __init__(self) -> None:
        self.records = {}

    def prepare_effect(self, **kwargs):
        operation = kwargs["operation_id"]
        record = self.records.get(operation)
        candidate = {**kwargs, "state": "PREPARED", "result": None, "error": None}
        if record:
            if record["payload_digest"] != kwargs["payload_digest"]:
                raise effects.EffectConflict("payload conflict")
            return record
        self.records[operation] = candidate
        return candidate

    def get_effect(self, operation_id):
        return self.records[operation_id]

    def update_effect(self, operation_id, *, expected_state, state, result=None, error=None):
        current = self.records[operation_id]
        if current["state"] != expected_state:
            raise effects.EffectConflict("CAS")
        current = {**current, "state": state, "result": result, "error": error}
        self.records[operation_id] = current
        return current


class _FakeEffectBackend:
    def __init__(self, ambiguous=False) -> None:
        self.executions = 0
        self.confirmed = False
        self.ambiguous = ambiguous

    def execute(self, kind, payload):
        self.executions += 1
        self.confirmed = True
        if self.ambiguous:
            raise effects.AmbiguousMutation("lost response")
        return {"executed": True}

    def query(self, kind, payload):
        return {"confirmed": self.confirmed}


class EffectTests(unittest.TestCase):
    def intent(self):
        return effects.EffectIntent.create(
            operation_id="op-1",
            campaign_id="campaign-1",
            node_id="node-1",
            kind="PUSH",
            payload={"root": "x", "head": "a" * 40},
        )

    def test_ambiguous_mutation_is_queried_not_repeated(self) -> None:
        store = _MemoryEffectStore()
        backend = _FakeEffectBackend(ambiguous=True)
        driver = effects.ExternalEffectDriver(store, backend)
        driver.prepare(self.intent())
        result = driver.run("op-1")
        self.assertEqual(result["state"], "CONFIRMED")
        self.assertEqual(backend.executions, 1)
        driver.run("op-1")
        self.assertEqual(backend.executions, 1)

    def test_http_502_after_mutation_start_is_reconciled_without_repeat(self) -> None:
        class StartedFailureBackend(effects.GitHubBackend):
            def __init__(self) -> None:
                super().__init__(
                    gh_executable="provider-does-not-run",
                    git_executable="provider-does-not-run",
                )
                self.executions = 0
                self.queries = 0

            def execute(self, kind, payload):
                del kind, payload
                self.executions += 1
                return {
                    "output": self._run(
                        ("provider", "mutate"), cwd=Path.cwd(), mutation=True
                    )
                }

            def query(self, kind, payload):
                del kind, payload
                self.queries += 1
                return {"confirmed": self.queries > 1}

        store = _MemoryEffectStore()
        backend = StartedFailureBackend()
        driver = effects.ExternalEffectDriver(store, backend)
        driver.prepare(self.intent())
        completed = subprocess.CompletedProcess(
            args=["provider"], returncode=1, stdout="", stderr="HTTP 502 Bad Gateway"
        )
        with patch.object(subprocess, "run", return_value=completed):
            result = driver.run("op-1")
        self.assertEqual(result["state"], "CONFIRMED")
        self.assertEqual(backend.executions, 1)
        self.assertEqual(backend.queries, 2)
        driver.run("op-1")
        self.assertEqual(backend.executions, 1)

    def test_query_confirmed_operation_never_repeats_the_mutation(self) -> None:
        store = _MemoryEffectStore()
        backend = _FakeEffectBackend()
        backend.confirmed = True
        driver = effects.ExternalEffectDriver(store, backend)
        driver.prepare(self.intent())
        result = driver.run("op-1")
        self.assertEqual(result["state"], "CONFIRMED")
        self.assertEqual(backend.executions, 0)

    def test_request_identity_cannot_change_payload(self) -> None:
        store = _MemoryEffectStore()
        driver = effects.ExternalEffectDriver(store, _FakeEffectBackend())
        driver.prepare(self.intent())
        changed = effects.EffectIntent.create(
            operation_id="op-1",
            campaign_id="campaign-1",
            node_id="node-1",
            kind="PUSH",
            payload={"root": "different", "head": "b" * 40},
        )
        with self.assertRaises(effects.EffectConflict):
            driver.prepare(changed)

    def test_exact_file_replacement_verifies_baseline_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target.txt"
            target.write_bytes(b"before")
            driver = effects.ExactFileEffectDriver(root / "journals")
            before = hashlib.sha256(b"before").hexdigest()
            after = hashlib.sha256(b"after").hexdigest()
            result = driver.replace(
                operation_id="replace-1",
                target=target,
                expected_baseline_sha256=before,
                replacement=b"after",
                expected_replacement_sha256=after,
            )
            self.assertEqual(result["state"], "CONFIRMED")
            replay = driver.replace(
                operation_id="replace-1",
                target=target,
                expected_baseline_sha256=before,
                replacement=b"after",
                expected_replacement_sha256=after,
            )
            self.assertTrue(replay.get("replayed") or replay["state"] == "CONFIRMED")
            self.assertEqual(target.read_bytes(), b"after")


class _ScriptedGitHubBackend(effects.GitHubBackend):
    def __init__(
        self,
        pull_requests,
        *,
        pull_request_view=None,
        comment_pages=None,
    ) -> None:
        super().__init__(gh_executable="gh-does-not-run", git_executable="git")
        self.pull_requests = pull_requests
        self.pull_request_view = pull_request_view
        self.comment_pages = dict(comment_pages or {})
        self.mutations = []
        self.queries = []

    def _assert_repository_binding(self, root, payload):
        del root, payload
        return "https://example.invalid/acme/repo.git", "example.invalid/acme/repo"

    def _gh_json(self, root, args):
        del root
        self.queries.append(tuple(args))
        if tuple(args[:2]) == ("pr", "list"):
            return self.pull_requests
        if tuple(args[:2]) == ("pr", "view") and self.pull_request_view is not None:
            return self.pull_request_view
        if tuple(args[:1]) == ("api",):
            page = int(str(args[1]).rsplit("page=", 1)[1])
            return self.comment_pages.get(page, [])
        raise AssertionError(args)

    def _run(self, argv, *, cwd, timeout=120, mutation):
        del cwd, timeout
        if not mutation:
            raise AssertionError("unexpected non-mutation command")
        self.mutations.append(tuple(argv))
        return "merged"


class GitHubBackendTests(unittest.TestCase):
    def payload(self, root: Path) -> dict[str, object]:
        return {
            "root": str(root),
            "head": "a" * 40,
            "head_branch": "codex/exact-head",
            "base": "main",
            "repository_remote": "https://example.invalid/acme/repo.git",
        }

    def pull_request(self, number: int) -> dict[str, object]:
        return {
            "number": number,
            "url": f"https://example.invalid/pr/{number}",
            "state": "OPEN",
            "headRefName": "codex/exact-head",
            "headRefOid": "a" * 40,
            "baseRefName": "main",
            "mergeCommit": None,
        }

    def test_duplicate_exact_head_pull_requests_fail_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            backend = _ScriptedGitHubBackend(
                [self.pull_request(11), self.pull_request(12)]
            )
            with self.assertRaisesRegex(effects.EffectConflict, "duplicate"):
                backend.query("CREATE_PULL_REQUEST", self.payload(root))
            self.assertEqual(backend.mutations, [])

    def test_merge_resolves_the_exact_head_pull_request_without_dynamic_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            backend = _ScriptedGitHubBackend([self.pull_request(17)])
            result = backend.execute("MERGE", self.payload(root))
            self.assertTrue(result["merged"])
            self.assertIn("17", backend.mutations[0])
            self.assertIn("--repo", backend.mutations[0])
            self.assertIn("example.invalid/acme/repo", backend.mutations[0])
            self.assertIn("--match-head-commit", backend.mutations[0])
            self.assertIn("a" * 40, backend.mutations[0])

    def test_execute_rechecks_exact_remote_before_any_github_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            git(root, "init", "-q", "-b", "main")
            git(root, "remote", "add", "origin", "https://github.com/acme/repo.git")
            payload = self.payload(root)
            payload["repository_remote"] = "https://github.com/acme/different.git"
            backend = effects.GitHubBackend(
                gh_executable="gh-must-not-run", git_executable="git"
            )
            with self.assertRaisesRegex(effects.EffectConflict, "remote changed"):
                backend.execute("CREATE_PULL_REQUEST", payload)

    def test_explicit_comment_pr_is_still_bound_to_exact_candidate_head(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            wrong_head = self.pull_request(97)
            wrong_head["headRefOid"] = "b" * 40
            backend = _ScriptedGitHubBackend([], pull_request_view=wrong_head)
            payload = self.payload(root)
            payload.update(
                {
                    "pull_request": 97,
                    "marker": "<!-- exact-operation -->",
                    "body": "<!-- exact-operation -->\ncomplete",
                }
            )
            with self.assertRaisesRegex(effects.EffectConflict, "candidate head"):
                backend.query("UPSERT_COMMENT", payload)
            self.assertTrue(any(query[:2] == ("pr", "view") for query in backend.queries))
            self.assertFalse(any(query[:1] == ("api",) for query in backend.queries))

    def test_comment_requires_one_nonempty_marker_embedded_once_in_body(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            backend = _ScriptedGitHubBackend(
                [], pull_request_view=self.pull_request(97)
            )
            base = self.payload(root)
            base["pull_request"] = 97
            invalid = (
                {"marker": "", "body": "complete"},
                {"marker": "<!-- operation -->", "body": "complete"},
                {
                    "marker": "<!-- operation -->",
                    "body": "<!-- operation -->\n<!-- operation -->",
                },
            )
            for fields in invalid:
                with self.subTest(fields=fields), self.assertRaisesRegex(
                    effects.EffectError, "marker"
                ):
                    backend.query("UPSERT_COMMENT", {**base, **fields})

    def test_comment_reconciliation_paginates_beyond_first_hundred(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            marker = "<!-- exact-operation -->"
            body = f"{marker}\ncomplete"
            first_page = [
                {"id": index, "body": f"unrelated-{index}"}
                for index in range(1, 101)
            ]
            backend = _ScriptedGitHubBackend(
                [],
                pull_request_view=self.pull_request(97),
                comment_pages={
                    1: first_page,
                    2: [{"id": 101, "html_url": "comment-101", "body": body}],
                },
            )
            payload = {
                **self.payload(root),
                "pull_request": 97,
                "marker": marker,
                "body": body,
            }

            result = backend.query("UPSERT_COMMENT", payload)

            self.assertTrue(result["confirmed"])
            self.assertEqual(result["comment"]["id"], 101)
            api_queries = [query for query in backend.queries if query[:1] == ("api",)]
            self.assertEqual(len(api_queries), 2)
            self.assertIn("page=2", api_queries[-1][1])

    def test_duplicate_comment_markers_across_pages_deny_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            marker = "<!-- exact-operation -->"
            body = f"{marker}\ncomplete"
            first_page = [
                {"id": 1, "html_url": "comment-1", "body": body},
                *[
                    {"id": index, "body": f"unrelated-{index}"}
                    for index in range(2, 101)
                ],
            ]
            backend = _ScriptedGitHubBackend(
                [],
                pull_request_view=self.pull_request(97),
                comment_pages={
                    1: first_page,
                    2: [{"id": 101, "html_url": "comment-101", "body": body}],
                },
            )
            payload = {
                **self.payload(root),
                "pull_request": 97,
                "marker": marker,
                "body": body,
            }

            with self.assertRaisesRegex(effects.EffectConflict, "multiple marker"):
                backend.execute("UPSERT_COMMENT", payload)
            self.assertEqual(backend.mutations, [])

    def test_any_started_nonzero_mutation_is_ambiguous_including_http_502(self) -> None:
        backend = effects.GitHubBackend(
            gh_executable="gh-does-not-run", git_executable="git-does-not-run"
        )
        for stderr in ("HTTP 502 Bad Gateway", "provider rejected request unexpectedly"):
            with self.subTest(stderr=stderr):
                completed = subprocess.CompletedProcess(
                    args=["provider"], returncode=1, stdout="", stderr=stderr
                )
                with patch.object(subprocess, "run", return_value=completed):
                    with self.assertRaises(effects.AmbiguousMutation):
                        backend._run(
                            ("provider", "mutate"),
                            cwd=Path.cwd(),
                            mutation=True,
                        )


class LegacyTests(unittest.TestCase):
    def test_archive_preserves_each_case_and_never_translates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "case-state"
            state = root / "coding-os-state"
            source.mkdir()
            document = {
                "revision": 4,
                "cases": {
                    "open-case": {"case_id": "open-case", "revision": 2, "state": "IMPLEMENTING", "events": {}},
                    "closed-case": {"case_id": "closed-case", "revision": 3, "state": "CLOSED_SUCCESS", "events": {"one": {}}},
                },
            }
            (source / "case-state.json").write_text(json.dumps(document), encoding="utf-8")
            result = legacy.archive_legacy_root(source, state_root=state)
            self.assertTrue(result.verified)
            self.assertEqual(result.unresolved_count, 1)
            verification = legacy.verify_legacy_archive(result.archive_root)
            self.assertEqual(verification["case_count"], 2)
            inspected = legacy.inspect_legacy_case(source, "open-case")
            self.assertEqual(inspected["classification"], legacy.UNRESOLVED)
            self.assertIsNone(inspected["translated_outcome"])

    def test_archive_uses_digest_filenames_for_untrusted_case_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy"
            state = root / "state"
            source.mkdir()
            case_id = "../outside"
            (source / "case-state.json").write_text(
                json.dumps(
                    {
                        "revision": 1,
                        "cases": {
                            case_id: {
                                "case_id": case_id,
                                "state": "RUNNING",
                                "events": {},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = legacy.archive_legacy_root(source, state_root=state)
            manifest = json.loads(
                (Path(result.archive_root) / "archive-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            archived = manifest["cases"][0]["archive_record"]
            self.assertRegex(archived, r"^cases/case-[0-9a-f]{64}\.json$")
            self.assertTrue((Path(result.archive_root) / archived).is_file())
            self.assertFalse((state / "legacy-archives" / "outside.json").exists())

    def test_archive_verifier_rejects_noncanonical_manifest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "archive"
            archive.mkdir()
            manifest = {
                "protocol_version": legacy.ARCHIVE_PROTOCOL,
                "source_digest": "unused",
                "files": [
                    {
                        "path": "../escape",
                        "size": 0,
                        "sha256": hashlib.sha256(b"").hexdigest(),
                        "mtime_ns": 0,
                    }
                ],
                "cases": [],
            }
            manifest["manifest_digest"] = hashlib.sha256(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            (archive / "archive-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(legacy.LegacyError, "canonical relative path"):
                legacy.verify_legacy_archive(archive)


if __name__ == "__main__":
    unittest.main()
