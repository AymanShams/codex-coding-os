#!/usr/bin/env python3
"""Synthetic enforcement tests for the mandatory anti-loop runtime hook."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
AGENT_SCRIPTS = ROOT / "scripts" / "agent"
HOOK_PATH = ROOT / "hooks" / "anti-loop-runtime" / "anti_loop_runtime.py"
PINNED_PYTHON = Path(sys.executable).resolve(strict=True)
if str(AGENT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(AGENT_SCRIPTS))

import case_state
TEST_DIRECTORY = str(Path(__file__).resolve().parent)
if TEST_DIRECTORY not in sys.path:
    sys.path.insert(0, TEST_DIRECTORY)
from runtime_actor_test_support import bind_controller_actor


def load_hook():
    spec = importlib.util.spec_from_file_location("anti_loop_runtime_under_test", HOOK_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {HOOK_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = load_hook()
REPOSITORY = "https://github.com/example/anti-loop-hook"
THREAD_ID = "01900000-0000-7000-8000-000000000801"


def run_git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


class AntiLoopRuntimeHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos anti loop hook ")
        self.root = Path(self.temp.name).resolve(strict=True)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        run_git(self.repo, "init", "-q")
        run_git(self.repo, "config", "user.email", "hook@example.invalid")
        run_git(self.repo, "config", "user.name", "Hook Test")
        run_git(self.repo, "remote", "add", "origin", REPOSITORY)
        scope_body = {
            "protocol_version": case_state.ANTI_LOOP_SUPPORT_SCOPE_PROTOCOL_VERSION,
            "schema_version": 1,
            "support_only_patterns": [
                "AGENTS.md",
                "CLAUDE.md",
                ".codex/active-slice*.json",
                "docs/delivery/current-state*.md",
                "docs/delivery/handoffs/**",
                "scripts/agent/**",
            ],
        }
        scope = {
            **scope_body,
            "record_sha256": case_state.canonical_json_sha256(scope_body),
        }
        (self.repo / ".codex").mkdir()
        (self.repo / ".codex" / "anti-loop-support-scope.json").write_text(
            json.dumps(scope, sort_keys=True) + "\n", encoding="utf-8"
        )
        (self.repo / "src").mkdir()
        (self.repo / "src" / "product.py").write_text("value = 1\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("old\n", encoding="utf-8")
        (self.repo / "scripts" / "agent").mkdir(parents=True)
        (self.repo / "scripts" / "agent" / "session_continuity.py").write_text(
            (
                "from case_state_adapter import record_anti_loop_event\n"
                "# SUPPORT_MUTATION\n"
                "# repository_handoff_write\n"
            ),
            encoding="utf-8",
        )
        (self.repo / "scripts" / "agent" / "review_worktree.py").write_text(
            "# committed review worktree helper\n",
            encoding="utf-8",
        )
        (self.repo / "scripts" / "agent" / "pr-body.mjs").write_text(
            "// committed PR body helper\n",
            encoding="utf-8",
        )
        (self.repo / "scripts" / "agent" / "codex-review-worktree.mjs").write_text(
            "// committed Codex review worktree helper\n",
            encoding="utf-8",
        )
        (self.repo / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "agent:pr-body": "node scripts/agent/pr-body.mjs",
                        "agent:codex-review-worktree": (
                            "node scripts/agent/codex-review-worktree.mjs"
                        ),
                    }
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-q", "-m", "initial product")
        self.head = run_git(self.repo, "rev-parse", "HEAD").casefold()
        self.store = case_state.CaseStore(self.root / "state")
        self.case_id = str(uuid.uuid4())
        self.store.register_case(
            self.case_id,
            objective="bounded hook test",
            request_id=str(uuid.uuid4()),
            expected_store_revision=0,
        )
        for kind, value in (
            ("repo_url", REPOSITORY),
            ("worktree", str(self.repo)),
            ("thread", THREAD_ID),
        ):
            self.store.bind(
                self.case_id,
                kind=kind,
                value=value,
                request_id=str(uuid.uuid4()),
                expected_revision=self.revision,
            )
        bind_controller_actor(
            case_state,
            self.store,
            self.case_id,
            thread_id=THREAD_ID,
            role="parent",
            parent_thread_id=None,
            agent_path="/root/implementer",
            cwd=self.repo,
        )
        self.environ = {"CODEX_THREAD_ID": THREAD_ID}
        self.turn_id = "01900000-0000-7000-8000-000000000802"
        self.tool_sequence = 0

    def tearDown(self) -> None:
        self.temp.cleanup()

    @property
    def revision(self) -> int:
        return self.store.get_case(self.case_id)["revision"]

    def evaluate(
        self,
        data: dict,
        *,
        environ: dict[str, str] | None = None,
        session_id: str = THREAD_ID,
        cwd: str | None = None,
    ) -> dict:
        payload = dict(data)
        payload.setdefault("session_id", session_id)
        payload.setdefault("cwd", cwd or str(self.repo))
        payload.setdefault("turn_id", self.turn_id)
        phase = payload.get("hook_event_name")
        self.tool_sequence += 1
        tool_id = f"tool-{self.tool_sequence}"
        if phase in {"PreToolUse", "PostToolUse"}:
            payload.setdefault("tool_use_id", tool_id)
        return hook.evaluate(
            payload,
            environ=self.environ if environ is None else environ,
            store=self.store,
        )

    @staticmethod
    def handoff_command(reason: str = "loop") -> str:
        return (
            "python scripts/agent/session_continuity.py handoff "
            f"--topic loop --reason {reason} --next loop --write"
        )

    def record_handoff_support(self) -> dict:
        return self.store.record_anti_loop_event(
            self.case_id,
            event_type="SUPPORT_MUTATION",
            actor_thread_id=THREAD_ID,
            actor_role="parent",
            repository=REPOSITORY,
            worktree=str(self.repo),
            product_head=run_git(self.repo, "rev-parse", "HEAD").casefold(),
            support_action="repository_handoff_write",
            failure_fingerprint=None,
            request_id=str(uuid.uuid4()),
            expected_revision=self.revision,
        )

    @staticmethod
    def disposition_authority(**arguments) -> dict:
        authority = {
            "protocol_version": case_state.ANTI_LOOP_HUMAN_DISPOSITION_PROTOCOL_VERSION,
            "schema_version": 2,
            "authority_id": (
                f"native-user:{arguments['native_thread_id']}:"
                f"{arguments['native_turn_id']}"
            ),
            "case_id": arguments["case_id"],
            "decision": arguments["decision"],
            "product_heads": dict(arguments["product_heads"]),
            "native_thread_id": arguments["native_thread_id"],
            "native_turn_id": arguments["native_turn_id"],
            "rollout_relative_path": (
                f"sessions/2026/07/29/rollout-{arguments['native_thread_id']}.jsonl"
            ),
            "decided_at": "2026-07-29T00:00:00+00:00",
            "message_sha256": "1" * 64,
            "log_prefix_sha256": "2" * 64,
            "evidence_sha256": "3" * 64,
            "native_verification_protocol": (
                case_state.NATIVE_HUMAN_VERIFICATION_PROTOCOL_VERSION
            ),
        }
        authority["authority_sha256"] = case_state.canonical_json_sha256(authority)
        return authority

    def control_broker(self, arguments=None) -> dict:
        command = f'"{PINNED_PYTHON}" "{HOOK_PATH}" control-patch'
        if arguments is not None:
            command += (
                f' --repository-root "{arguments.repository_root}"'
                f' --patch-file "{arguments.patch_file}" --sha256 {arguments.sha256}'
                f' --state-root "{arguments.state_root}" --case-id {arguments.case_id}'
                f' --actor-thread-id {arguments.actor_thread_id} --actor-role {arguments.actor_role}'
                f' --repository {arguments.repository} --product-head {arguments.product_head}'
                f' --support-action "{arguments.support_action}" --request-id {arguments.request_id}'
                f' --expected-revision {arguments.expected_revision}'
            )
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "shell_command",
            "tool_input": {"command": command},
        }

    def control_patch_arguments(self, before: str, after: str, name: str):
        patch = (
            "diff --git a/AGENTS.md b/AGENTS.md\n"
            "--- a/AGENTS.md\n"
            "+++ b/AGENTS.md\n"
            "@@ -1 +1 @@\n"
            f"-{before}\n"
            f"+{after}\n"
        ).encode("utf-8")
        path = self.root / f"{name}.patch"
        path.write_bytes(patch)
        return hook.argparse.Namespace(
            repository_root=str(self.repo),
            patch_file=str(path),
            sha256=hashlib.sha256(patch).hexdigest(),
            state_root=str(self.root / "state"),
            case_id=self.case_id,
            actor_thread_id=THREAD_ID,
            actor_role="parent",
            repository=REPOSITORY,
            product_head=self.head,
            support_action=f"guarded control patch {name}",
            request_id=str(uuid.uuid4()),
            expected_revision=self.revision,
        )

    def support_chain_arguments(
        self,
        parent_event: dict,
        *,
        support_action: str = "create another handoff",
    ):
        return hook.argparse.Namespace(
            repository_root=str(self.repo),
            state_root=str(self.root / "state"),
            case_id=self.case_id,
            actor_thread_id=THREAD_ID,
            actor_role="parent",
            repository=REPOSITORY,
            product_head=self.head,
            parent_event_id=parent_event["event_id"],
            support_action=support_action,
            request_id=str(uuid.uuid4()),
            expected_revision=self.revision,
        )

    @staticmethod
    def support_chain_command(arguments) -> str:
        return (
            "python hooks/anti-loop-runtime/anti_loop_runtime.py "
            "support-chain-proposed "
            f'--repository-root "{arguments.repository_root}" '
            f'--state-root "{arguments.state_root}" '
            f"--case-id {arguments.case_id} "
            f"--actor-thread-id {arguments.actor_thread_id} "
            f"--actor-role {arguments.actor_role} "
            f"--repository {arguments.repository} "
            f"--product-head {arguments.product_head} "
            f"--parent-event-id {arguments.parent_event_id} "
            f'--support-action "{arguments.support_action}" '
            f"--request-id {arguments.request_id} "
            f"--expected-revision {arguments.expected_revision}"
        )

    def seed_product_head(self, head: str | None = None) -> None:
        self.store.record_anti_loop_event(
            self.case_id,
            event_type="PRODUCT_HEAD_ADVANCED",
            actor_thread_id=THREAD_ID,
            actor_role="parent",
            repository=REPOSITORY,
            worktree=str(self.repo),
            product_head=head or self.head,
            support_action=None,
            failure_fingerprint=None,
            request_id=str(uuid.uuid4()),
            expected_revision=self.revision,
        )

    @staticmethod
    def control_patch() -> dict:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: AGENTS.md\n@@\n-old\n+new\n*** End Patch"
            },
        }

    @staticmethod
    def product_patch() -> dict:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: src/product.py\n@@\n-old\n+new\n*** End Patch"
            },
        }

    @staticmethod
    def support_failure() -> dict:
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": "shell_command",
            "tool_input": {
                "command": "python scripts/agent/session_continuity.py validate"
            },
            "tool_response": {"exit_code": 1, "stderr": "same timeout"},
        }

    def test_real_guarded_control_patch_applies_once_and_second_is_denied_before_mutation(self) -> None:
        first_arguments = self.control_patch_arguments("old", "first", "first")
        first = self.control_broker(first_arguments)
        first_revision = self.revision
        allowed = self.evaluate(first)
        self.assertEqual(allowed["reason_code"], "GUARDED_CONTROL_BROKER_ALLOWED")
        applied = hook.apply_control_patch(first_arguments)
        self.assertEqual(applied["status"], "APPLIED")
        self.assertEqual(self.revision, first_revision + 1)
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), "first\n")
        second_arguments = self.control_patch_arguments("first", "second", "second")
        second = self.control_broker(second_arguments)
        second_revision = self.revision
        self.assertEqual(
            self.evaluate(second)["reason_code"], "GUARDED_CONTROL_BROKER_ALLOWED"
        )
        with self.assertRaisesRegex(hook.HookError, "ANTI_LOOP_LATCH_ACTIVE"):
            hook.apply_control_patch(second_arguments)
        self.assertEqual(self.revision, second_revision + 1)
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), "first\n")

    def test_bootstrap_and_control_broker_require_exact_pinned_complete_commands(self) -> None:
        bootstrap = (
            f'"{PINNED_PYTHON}" "{case_state.__file__}" '
            f'--state-root "{self.store.state_root}" --json bind '
            f"--case-id {self.case_id} --kind thread "
            f"--value 01900000-0000-7000-8000-000000000899 "
            f"--request-id {uuid.uuid4()} --expected-revision {self.revision}"
        )
        exact = self.evaluate(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "shell_command",
                "tool_input": {"command": bootstrap},
            }
        )
        self.assertEqual(exact["reason_code"], "CASE_BOOTSTRAP_ALLOWED")
        for command in (
            bootstrap + " --unexpected bypass",
            bootstrap.replace(f'"{PINNED_PYTHON}"', "python", 1),
            f'"{PINNED_PYTHON}" "{HOOK_PATH}" control-patch',
            self.control_broker(self.control_patch_arguments("old", "x", "x"))[
                "tool_input"
            ]["command"]
            + " --unexpected bypass",
            (
                f'"{PINNED_PYTHON}" "{self.root / "evil" / "scripts" / "agent" / "case_state.py"}" '
                "register --case-id counterfeit"
            ),
            (
                f'"{PINNED_PYTHON}" "{self.root / "evil" / "anti_loop_runtime.py"}" '
                "control-patch"
            ),
        ):
            with self.subTest(command=command):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "shell_command",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(result["ccos_decision"], "DENY")
        self.record_handoff_support()
        self.record_handoff_support()
        latched = self.evaluate(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "shell_command",
                "tool_input": {"command": bootstrap},
            }
        )
        self.assertEqual(latched["reason_code"], "ANTI_LOOP_LATCH_ACTIVE")

    def test_all_mutating_mcp_target_shapes_protect_support_paths(self) -> None:
        payloads = (
            ("mcp__filesystem__write_file", {"path": "docs/delivery/current-state.md"}),
            ("mcp__filesystem__edit", {"file_path": "docs/delivery/current-state.md"}),
            ("mcp__filesystem__move", {"target": "docs/delivery/current-state.md"}),
            (
                "mcp__filesystem__copy",
                {"request": {"destination": "docs/delivery/current-state.md"}},
            ),
        )
        for tool_name, tool_input in payloads:
            with self.subTest(tool_name=tool_name, tool_input=tool_input):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                    }
                )
                self.assertEqual(result["ccos_decision"], "DENY")
                self.assertEqual(result["reason_code"], "GUARDED_COMMAND_REQUIRED")

    def test_native_top_level_child_identity_is_accepted_pre_and_post_only(self) -> None:
        child = "01900000-0000-7000-8000-000000000803"
        self.store.bind(
            self.case_id,
            kind="thread",
            value=child,
            request_id=str(uuid.uuid4()),
            expected_revision=self.revision,
        )
        bind_controller_actor(
            case_state,
            self.store,
            self.case_id,
            thread_id=child,
            role="review_child",
            parent_thread_id=THREAD_ID,
            agent_path="/root/reviewer",
            cwd=self.repo,
        )
        for phase in ("PreToolUse", "PostToolUse"):
            payload = self.product_patch()
            payload.update(
                {
                    "hook_event_name": phase,
                    "session_id": THREAD_ID,
                    "agent_id": child,
                    "agent_type": "review_child",
                }
            )
            with self.subTest(phase=phase):
                self.assertEqual(self.evaluate(payload)["ccos_decision"], "ALLOW")
        invalid = (
            {"agent_id": child},
            {"agent_type": "review_child"},
            {
                "agent_id": child,
                "agent_type": "review_child",
                "subagent": {"agent_id": child, "agent_type": "review_child"},
            },
            {"subagent": {"agent_id": child, "agent_type": "review_child"}},
        )
        for identity in invalid:
            with self.subTest(identity=identity):
                payload = self.product_patch()
                payload.update(identity)
                result = self.evaluate(payload)
                self.assertEqual(result["ccos_decision"], "DENY")
                self.assertEqual(result["reason_code"], "HOOK_ACTOR_IDENTITY_INVALID")

    def test_apply_patch_move_validates_both_directions_traversal_and_duplicates(self) -> None:
        moves = (
            (
                "src/product.py",
                "docs/delivery/current-state.md",
            ),
            (
                "docs/delivery/current-state.md",
                "src/product.py",
            ),
        )
        for source, destination in moves:
            with self.subTest(source=source, destination=destination):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "apply_patch",
                        "tool_input": {
                            "command": (
                                "*** Begin Patch\n"
                                f"*** Update File: {source}\n"
                                f"*** Move to: {destination}\n"
                                "@@\n-old\n+new\n"
                                "*** End Patch"
                            )
                        },
                    }
                )
                self.assertEqual(result["ccos_decision"], "DENY")
                self.assertEqual(result["reason_code"], "CONTROL_BROKER_REQUIRED")
        for patch in (
            (
                "*** Begin Patch\n*** Update File: src/product.py\n"
                "*** Move to: ../outside.py\n*** End Patch"
            ),
            (
                "*** Begin Patch\n*** Update File: src/product.py\n"
                "*** Move to: SRC/product.py\n*** End Patch"
            ),
            "*** Begin Patch\n*** Move to: AGENTS.md\n*** End Patch",
        ):
            with self.subTest(patch=patch):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "apply_patch",
                        "tool_input": {"command": patch},
                    }
                )
                self.assertEqual(result["ccos_decision"], "DENY")
                self.assertEqual(result["reason_code"], "TOOL_TARGET_INVALID")

    def test_hook_git_ignores_forged_and_missing_path(self) -> None:
        forged = self.root / "forged"
        forged.mkdir()
        for name in ("git", "git.exe"):
            (forged / name).write_text("forged\n", encoding="utf-8")
        for path_value in (str(forged), ""):
            with self.subTest(path_value=path_value):
                with mock.patch.dict(os.environ, {"PATH": path_value}, clear=False):
                    self.assertEqual(
                        hook._git(str(self.repo), "rev-parse", "HEAD").casefold(),
                        self.head,
                    )

    def test_control_patch_event_and_apply_share_one_store_lock(self) -> None:
        arguments = self.control_patch_arguments("old", "atomic", "atomic")
        original_run = hook.subprocess.run
        inside_apply = threading.Event()
        release_apply = threading.Event()
        contender_started = threading.Event()
        contender_finished = threading.Event()
        outcomes: dict[str, object] = {}

        def controlled_run(command, *args, **kwargs):
            if "apply" in command and "--check" in command:
                inside_apply.set()
                if not release_apply.wait(10):
                    raise AssertionError("timed out waiting to release guarded apply")
            return original_run(command, *args, **kwargs)

        def apply_worker() -> None:
            try:
                outcomes["apply"] = hook.apply_control_patch(arguments)
            except Exception as exc:
                outcomes["apply_error"] = exc

        def contender() -> None:
            contender_started.set()
            try:
                self.store.record_anti_loop_event(
                    self.case_id,
                    event_type="SUPPORT_MUTATION",
                    actor_thread_id=THREAD_ID,
                    actor_role="parent",
                    repository=REPOSITORY,
                    worktree=str(self.repo),
                    product_head=self.head,
                    support_action="concurrent support write",
                    failure_fingerprint=None,
                    request_id=str(uuid.uuid4()),
                    expected_revision=arguments.expected_revision,
                )
            except Exception as exc:
                outcomes["contender_error"] = exc
            finally:
                contender_finished.set()

        with mock.patch.object(hook.subprocess, "run", side_effect=controlled_run):
            apply_thread = threading.Thread(target=apply_worker)
            apply_thread.start()
            self.assertTrue(inside_apply.wait(10))
            contender_thread = threading.Thread(target=contender)
            contender_thread.start()
            self.assertTrue(contender_started.wait(10))
            time.sleep(0.1)
            self.assertFalse(contender_finished.is_set())
            self.assertEqual(
                (self.repo / "AGENTS.md").read_text(encoding="utf-8"), "old\n"
            )
            release_apply.set()
            apply_thread.join(10)
            contender_thread.join(10)
        self.assertFalse(apply_thread.is_alive())
        self.assertFalse(contender_thread.is_alive())
        self.assertNotIn("apply_error", outcomes)
        self.assertEqual(outcomes["apply"]["status"], "APPLIED")
        self.assertIsInstance(outcomes.get("contender_error"), case_state.RevisionConflict)
        self.assertEqual(
            (self.repo / "AGENTS.md").read_text(encoding="utf-8"), "atomic\n"
        )

    def test_failed_atomic_control_patch_replay_never_claims_applied(self) -> None:
        arguments = self.control_patch_arguments("not-the-live-line", "changed", "bad")
        revision = self.revision
        with self.assertRaisesRegex(hook.HookError, "guarded git apply failed"):
            hook.apply_control_patch(arguments)
        self.assertEqual(self.revision, revision + 1)
        self.assertEqual(
            (self.repo / "AGENTS.md").read_text(encoding="utf-8"), "old\n"
        )
        with self.assertRaisesRegex(
            hook.HookError, "did not complete its one-use atomic apply"
        ):
            hook.apply_control_patch(arguments)
        self.assertEqual(self.revision, revision + 1)
        self.assertEqual(
            (self.repo / "AGENTS.md").read_text(encoding="utf-8"), "old\n"
        )

    def test_support_chain_reporter_latches_from_exact_immediately_prior_event(self) -> None:
        parent = self.record_handoff_support()
        arguments = self.support_chain_arguments(parent)
        result = hook.report_support_chain(arguments)
        self.assertEqual(result["status"], "LATCHED")
        self.assertEqual(result["parent_event_id"], parent["event_id"])
        self.assertEqual(
            result["trigger_reason"],
            "SUPPORT_ACTION_PROPOSED_ANOTHER_SUPPORT_ACTION",
        )
        case = self.store.get_case(self.case_id)
        self.assertEqual(case["state"], "ANTI_LOOP_LOCKED")
        self.assertEqual(case["anti_loop_latch"]["status"], "LATCHED")

    def test_hook_support_chain_signal_requires_exact_parent_event_evidence(self) -> None:
        parent = self.record_handoff_support()
        arguments = self.support_chain_arguments(parent)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "shell_command",
            "tool_input": {"command": self.support_chain_command(arguments)},
        }
        result = self.evaluate(payload)
        self.assertEqual(result["ccos_decision"], "DENY")
        self.assertEqual(result["reason_code"], "ANTI_LOOP_LATCH_ACTIVE")
        self.assertEqual(
            result["event"]["trigger_reason"],
            "SUPPORT_ACTION_PROPOSED_ANOTHER_SUPPORT_ACTION",
        )

    def test_hook_support_chain_signal_rejects_forged_parent_event(self) -> None:
        parent = self.record_handoff_support()
        arguments = self.support_chain_arguments(parent)
        arguments.parent_event_id = str(uuid.uuid4())
        revision = self.revision
        result = self.evaluate(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "shell_command",
                "tool_input": {"command": self.support_chain_command(arguments)},
            }
        )
        self.assertEqual(result["ccos_decision"], "DENY")
        self.assertEqual(result["reason_code"], "UNTRUSTED_SUPPORT_HELPER")
        self.assertEqual(self.revision, revision)
        self.assertEqual(
            self.store.get_case(self.case_id)["anti_loop_latch"]["status"], "CLEAR"
        )

    def test_review_worktree_create_is_hook_owned_and_second_write_latches(self) -> None:
        command = (
            "python scripts/agent/review_worktree.py create "
            f"--case-id {self.case_id} --ref {self.head}"
        )
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "shell_command",
            "tool_input": {"command": command},
        }
        first = self.evaluate(payload)
        self.assertEqual(first["ccos_decision"], "ALLOW")
        self.assertEqual(first["event"]["event_type"], "SUPPORT_MUTATION")
        self.assertEqual(
            first["event"]["support_action"],
            "repository_review_worktree_create",
        )
        second = self.evaluate(payload)
        self.assertEqual(second["ccos_decision"], "DENY")
        self.assertEqual(second["reason_code"], "ANTI_LOOP_LATCH_ACTIVE")
        self.assertEqual(
            second["event"]["trigger_reason"],
            "SECOND_SUPPORT_MUTATION_WITHOUT_PRODUCT_HEAD_ADVANCE",
        )
        revision = self.revision
        third = self.evaluate(payload)
        self.assertEqual(third["reason_code"], "ANTI_LOOP_LATCH_ACTIVE")
        self.assertEqual(self.revision, revision)

    def test_pr_body_output_is_hook_owned_and_second_write_latches(self) -> None:
        command = (
            "node scripts/agent/pr-body.mjs --risk material "
            "--out .codex/pr-body.md"
        )
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "shell_command",
            "tool_input": {"command": command},
        }
        first = self.evaluate(payload)
        self.assertEqual(first["ccos_decision"], "ALLOW")
        self.assertEqual(first["event"]["event_type"], "SUPPORT_MUTATION")
        self.assertEqual(first["event"]["support_action"], "repository_pr_body_write")
        second = self.evaluate(payload)
        self.assertEqual(second["ccos_decision"], "DENY")
        self.assertEqual(second["reason_code"], "ANTI_LOOP_LATCH_ACTIVE")
        self.assertEqual(
            second["event"]["trigger_reason"],
            "SECOND_SUPPORT_MUTATION_WITHOUT_PRODUCT_HEAD_ADVANCE",
        )
        revision = self.revision
        third = self.evaluate(payload)
        self.assertEqual(third["reason_code"], "ANTI_LOOP_LATCH_ACTIVE")
        self.assertEqual(self.revision, revision)

    def test_package_review_worktree_create_is_hook_owned(self) -> None:
        command = (
            "corepack pnpm run agent:codex-review-worktree -- "
            f"--case-id {self.case_id} --actor-thread-id {THREAD_ID} "
            f"--request-id {uuid.uuid4()} --expected-revision {self.revision} "
            f"--product-event-id {uuid.uuid4()} --path ../review-worktree"
        )
        result = self.evaluate(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "shell_command",
                "tool_input": {"command": command},
            }
        )
        self.assertEqual(result["ccos_decision"], "ALLOW")
        self.assertEqual(result["event"]["event_type"], "SUPPORT_MUTATION")
        self.assertEqual(
            result["event"]["support_action"],
            "repository_review_worktree_create",
        )

    def test_package_pr_body_output_is_hook_owned(self) -> None:
        command = (
            "corepack pnpm run agent:pr-body -- --risk material "
            f"--case-id {self.case_id} --out .codex/pr-body.md"
        )
        result = self.evaluate(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "shell_command",
                "tool_input": {"command": command},
            }
        )
        self.assertEqual(result["ccos_decision"], "ALLOW")
        self.assertEqual(result["event"]["event_type"], "SUPPORT_MUTATION")
        self.assertEqual(result["event"]["support_action"], "repository_pr_body_write")

    def test_read_only_review_and_pr_body_forms_do_not_consume_support_budget(self) -> None:
        commands = (
            "python scripts/agent/review_worktree.py status",
            "python scripts/agent/review_worktree.py list",
            (
                "python scripts/agent/review_worktree.py create "
                f"--case-id {self.case_id} --ref {self.head} --dry-run"
            ),
            "node scripts/agent/pr-body.mjs --validate",
            (
                "corepack pnpm run agent:codex-review-worktree -- "
                f"--case-id {self.case_id} --print-only"
            ),
        )
        revision = self.revision
        for command in commands:
            with self.subTest(command=command):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "shell_command",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(result["ccos_decision"], "ALLOW")
                self.assertEqual(result["reason_code"], "NO_ANTI_LOOP_EVENT")
        self.assertEqual(self.revision, revision)

    def test_identical_second_support_failure_latches_from_post_result(self) -> None:
        self.seed_product_head()
        first = self.support_failure()
        first["tool_response"] = {
            "stderr": "2026-07-29T10:00:00Z timeout\nelapsed 12ms\nC:/temp/run-a/check.log",
            "exit_code": 1,
            "duration_ms": 12,
        }
        self.assertEqual(self.evaluate(first)["ccos_decision"], "ALLOW")
        second = self.support_failure()
        second["tool_response"] = {
            "duration_ms": 991,
            "exit_code": 1,
            "stderr": "C:/temp/run-b/check.log\nelapsed 991ms\n2026-07-29T10:05:00Z timeout",
        }
        result = self.evaluate(second)
        self.assertEqual(result["ccos_decision"], "DENY")
        self.assertEqual(result["reason_code"], "ANTI_LOOP_LATCH_ACTIVE")
        self.assertEqual(
            result["event"]["trigger_reason"], "REPEATED_SUPPORT_FAILURE_FINGERPRINT"
        )

    def test_verified_product_head_with_product_diff_resets_both_counters(self) -> None:
        self.seed_product_head()
        self.store.record_anti_loop_event(
            self.case_id,
            event_type="SUPPORT_MUTATION",
            actor_thread_id=THREAD_ID,
            actor_role="parent",
            repository=REPOSITORY,
            worktree=str(self.repo),
            product_head=self.head,
            support_action="refresh handoff",
            failure_fingerprint=None,
            request_id=str(uuid.uuid4()),
            expected_revision=self.revision,
        )
        self.store.record_anti_loop_event(
            self.case_id,
            event_type="SUPPORT_FAILURE",
            actor_thread_id=THREAD_ID,
            actor_role="parent",
            repository=REPOSITORY,
            worktree=str(self.repo),
            product_head=self.head,
            support_action=None,
            failure_fingerprint="sha256:" + "1" * 64,
            request_id=str(uuid.uuid4()),
            expected_revision=self.revision,
        )
        (self.repo / "src" / "product.py").write_text("value = 2\n", encoding="utf-8")
        run_git(self.repo, "add", "src/product.py")
        run_git(self.repo, "commit", "-q", "-m", "product advance")
        new_head = run_git(self.repo, "rev-parse", "HEAD").casefold()
        data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "shell_command",
            "tool_input": {"command": "git commit -m 'product advance'"},
            "tool_response": {"exit_code": 0},
        }
        result = self.evaluate(data)
        self.assertEqual(result["ccos_decision"], "ALLOW")
        latch = result["event"]["anti_loop_latch"]
        self.assertEqual(latch["consecutive_support_mutations"], 0)
        self.assertEqual(latch["failure_fingerprint_repetitions"], 0)

    def test_first_handoff_is_allowed_and_second_support_write_latches(self) -> None:
        data = {
            "hook_event_name": "PreToolUse",
            "tool_name": "shell_command",
            "tool_input": {"command": self.handoff_command()},
        }
        revision = self.revision
        first = self.evaluate(data)
        self.assertEqual(first["ccos_decision"], "ALLOW")
        self.assertEqual(first["reason_code"], "SELF_REPORTING_SUPPORT_HELPER_ALLOWED")
        self.assertIsNone(first["event"])
        self.assertEqual(self.revision, revision)
        first_event = self.record_handoff_support()
        self.assertEqual(first_event["anti_loop_latch"]["status"], "CLEAR")
        second_pre = self.evaluate(data)
        self.assertEqual(
            second_pre["reason_code"], "SELF_REPORTING_SUPPORT_HELPER_ALLOWED"
        )
        result = self.record_handoff_support()
        self.assertEqual(result["anti_loop_latch"]["status"], "LATCHED")
        self.assertEqual(
            result["trigger_reason"],
            "SECOND_SUPPORT_MUTATION_WITHOUT_PRODUCT_HEAD_ADVANCE",
        )

    def test_self_reporting_handoff_rejects_chains_redirects_and_interpolation(self) -> None:
        base = self.handoff_command()
        rejected = (
            base + "; Write-Output bypass",
            base + " > handoff.log",
            base + " --",
            base + " --write",
            base + " --unknown value",
            (
                "python scripts/agent/session_continuity.py handoff "
                '--topic loop --reason "$(Set-Content AGENTS.md bypass)" '
                "--next loop --write"
            ),
            (
                "python scripts/agent/session_continuity.py handoff "
                '--topic loop --reason "literal '
                + chr(96)
                + "Set-Content AGENTS.md bypass"
                + chr(96)
                + '" --next loop --write'
            ),
        )
        for command in rejected:
            with self.subTest(command=command):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "shell_command",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(result["ccos_decision"], "DENY")
        literal = self.evaluate(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "shell_command",
                "tool_input": {
                    "command": (
                        "python scripts/agent/session_continuity.py handoff "
                        "--topic loop --reason 'literal $(not executed)' "
                        "--next loop --write"
                    )
                },
            }
        )
        self.assertEqual(
            literal["reason_code"], "SELF_REPORTING_SUPPORT_HELPER_ALLOWED"
        )

    def test_self_reporting_handoff_rejects_topic_traversal_without_event(self) -> None:
        command = (
            "python scripts/agent/session_continuity.py handoff "
            "--topic x/../../../apps/api/src/new-file "
            "--reason loop --next loop --write"
        )
        revision = self.revision
        events = dict(self.store.get_case(self.case_id)["events"])
        result = self.evaluate(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "shell_command",
                "tool_input": {"command": command},
            }
        )
        self.assertEqual(result["ccos_decision"], "DENY")
        self.assertEqual(result["reason_code"], "GUARDED_COMMAND_REQUIRED")
        case = self.store.get_case(self.case_id)
        self.assertEqual(case["revision"], revision)
        self.assertEqual(case["events"], events)

    def test_hook_event_replay_uses_native_tool_identity_once(self) -> None:
        payload = self.support_failure()
        payload["turn_id"] = self.turn_id
        payload["tool_use_id"] = "native-tool-one"
        first = self.evaluate(payload)
        self.assertEqual(first["ccos_decision"], "ALLOW")
        revision = self.revision
        replay = self.evaluate(payload)
        self.assertEqual(replay["ccos_decision"], "ALLOW")
        self.assertTrue(replay["event"]["idempotent"])
        self.assertEqual(self.revision, revision)
        distinct = dict(payload)
        distinct["tool_use_id"] = "native-tool-two"
        result = self.evaluate(distinct)
        self.assertEqual(result["ccos_decision"], "DENY")
        self.assertEqual(
            result["event"]["trigger_reason"],
            "REPEATED_SUPPORT_FAILURE_FINGERPRINT",
        )

    def test_latch_denies_all_mutations_but_preserves_exact_read_only_evidence(self) -> None:
        self.record_handoff_support()
        self.record_handoff_support()
        mutations = (
            ("shell_command", {"command": "git push origin feature"}),
            ("shell_command", {"command": "gh pr create --base main"}),
            (
                "shell_command",
                {"command": "node --test scripts/agent/tests/case-state.test.mjs"},
            ),
            (
                "shell_command",
                {"command": "corepack pnpm run agent:state-check"},
            ),
            (
                "shell_command",
                {"command": "python scripts/agent/review_worktree.py create"},
            ),
            (
                "shell_command",
                {"command": "node scripts/agent/pr-body.mjs --out body.md"},
            ),
            ("write_file", {"path": "src/product.py", "content": "changed"}),
            ("mcp__example__write", {"path": "src/product.py", "value": "changed"}),
        )
        for tool_name, tool_input in mutations:
            with self.subTest(tool_name=tool_name, tool_input=tool_input):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                    }
                )
                self.assertEqual(result["ccos_decision"], "DENY")
                self.assertEqual(result["reason_code"], "ANTI_LOOP_LATCH_ACTIVE")
        for command in (
            "git status",
            "python scripts/agent/session_continuity.py validate",
        ):
            with self.subTest(read_only=command):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "shell_command",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(result["ccos_decision"], "ALLOW")

    def test_forged_state_root_environment_is_ignored(self) -> None:
        class EmptyStore:
            def list_cases(self):
                return []

        payload = self.product_patch()
        payload.update(
            {
                "session_id": "unbound-native-thread",
                "turn_id": self.turn_id,
                "tool_use_id": "native-tool-forged-root",
                "cwd": str(self.repo),
            }
        )
        with mock.patch.object(
            hook.case_state, "CaseStore", return_value=EmptyStore()
        ) as constructor:
            result = hook.evaluate(
                payload,
                environ={
                    "HOME": str(self.root / "fake-home"),
                    "USERPROFILE": str(self.root / "fake-profile"),
                    "CCOS_CASE_STATE_ROOT": str(self.root / "fake-state"),
                },
            )
        constructor.assert_called_once_with()
        self.assertEqual(result["ccos_decision"], "ALLOW")

    def test_latched_case_allows_only_exact_canonical_human_disposition(self) -> None:
        self.record_handoff_support()
        self.record_handoff_support()
        self.store.human_disposition_verifier = self.disposition_authority
        native_thread_id = "01900000-0000-7000-8000-000000000811"
        native_turn_id = "01900000-0000-7000-8000-000000000812"
        request_id = str(uuid.uuid4())
        state_root = self.root / "state"
        command = (
            f'"{sys.executable}" "{case_state.__file__}" '
            f'--state-root "{state_root}" --json anti-loop-stop-case '
            f"--case-id {self.case_id} --native-thread-id {native_thread_id} "
            f"--native-turn-id {native_turn_id} --request-id {request_id} "
            f"--expected-revision {self.revision}"
        )
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "shell_command",
            "tool_input": {"command": command},
        }
        with mock.patch.object(
            hook.case_state, "default_state_root", return_value=state_root
        ):
            allowed = self.evaluate(payload)
        self.assertEqual(
            allowed["reason_code"], "ANTI_LOOP_HUMAN_DISPOSITION_ALLOWED"
        )
        disposed = self.store.anti_loop_stop_case(
            self.case_id,
            native_thread_id=native_thread_id,
            native_turn_id=native_turn_id,
            request_id=request_id,
            expected_revision=self.revision,
        )
        self.assertEqual(disposed["anti_loop_latch"]["status"], "DISPOSED")
        self.assertEqual(disposed["state"], "CASE_LOCKED")

    def test_native_stdout_contains_only_official_pre_and_post_fields(self) -> None:
        cases = (
            (
                "PreToolUse",
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "ANTI_LOOP_LATCH_ACTIVE: blocked",
                    }
                },
            ),
            (
                "PostToolUse",
                {
                    "decision": "block",
                    "reason": "ANTI_LOOP_LATCH_ACTIVE: blocked",
                },
            ),
        )
        for phase, expected in cases:
            with self.subTest(phase=phase):
                stdin = io.StringIO(json.dumps({"hook_event_name": phase}))
                stdout = io.StringIO()
                stderr = io.StringIO()
                internal = hook._result(
                    phase=phase,
                    decision="DENY",
                    reason_code="ANTI_LOOP_LATCH_ACTIVE",
                    reason="blocked",
                )
                with (
                    mock.patch.object(hook.sys, "stdin", stdin),
                    mock.patch.object(hook.sys, "stdout", stdout),
                    mock.patch.object(hook.sys, "stderr", stderr),
                    mock.patch.object(hook, "evaluate", return_value=internal),
                ):
                    exit_code = hook.main([])
                self.assertEqual(exit_code, 0)
                self.assertEqual(json.loads(stdout.getvalue()), expected)
                self.assertEqual(stderr.getvalue(), "")

    def test_native_allow_is_silent_and_malformed_input_fails_closed(self) -> None:
        allow = hook._result(
            phase="PreToolUse",
            decision="ALLOW",
            reason_code="NO_ANTI_LOOP_EVENT",
            reason="allowed",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(hook.sys, "stdin", io.StringIO("{}")),
            mock.patch.object(hook.sys, "stdout", stdout),
            mock.patch.object(hook.sys, "stderr", stderr),
            mock.patch.object(hook, "evaluate", return_value=allow),
        ):
            self.assertEqual(hook.main([]), 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(hook.sys, "stdin", io.StringIO("{")),
            mock.patch.object(hook.sys, "stdout", stdout),
            mock.patch.object(hook.sys, "stderr", stderr),
        ):
            self.assertEqual(hook.main([]), 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ANTI_LOOP_HOOK_FAILURE", stderr.getvalue())

    def test_active_latch_denies_unmarked_product_mutation_globally_for_exact_case(self) -> None:
        self.record_handoff_support()
        self.record_handoff_support()
        denied = self.evaluate(self.product_patch())
        self.assertEqual(denied["ccos_decision"], "DENY")
        self.assertEqual(denied["reason_code"], "ANTI_LOOP_LATCH_ACTIVE")

    def test_unmarked_control_bypasses_are_denied_but_normal_product_patch_is_allowed(self) -> None:
        denied_patch = self.evaluate(self.control_patch())
        self.assertEqual(denied_patch["reason_code"], "CONTROL_BROKER_REQUIRED")
        denied_commands = (
            "pwsh -File scripts/install.ps1",
            "python scripts/agent/session_continuity.py handoff",
        )
        for command in denied_commands:
            with self.subTest(command=command):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "shell_command",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(result["ccos_decision"], "DENY")
                self.assertEqual(result["reason_code"], "GUARDED_COMMAND_REQUIRED")
        allowed_commands = (
            "git worktree add ../other feature",
            "git switch -c feature",
            "git push origin feature",
            "gh pr create --base main",
            "python -m unittest tests.test_example",
            "Set-Content src/product.py value",
        )
        for command in allowed_commands:
            with self.subTest(command=command):
                result = self.evaluate(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "shell_command",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(result["ccos_decision"], "ALLOW")
        self.assertEqual(self.evaluate(self.product_patch())["ccos_decision"], "ALLOW")
        product_write = self.evaluate(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "write_file",
                "tool_input": {"path": "src/product.py", "content": "value = 2"},
            }
        )
        self.assertEqual(product_write["ccos_decision"], "ALLOW")

    def test_command_prose_cannot_spoof_carrier_or_control_classification(self) -> None:
        result = self.evaluate(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "shell_command",
                "tool_input": {
                    "command": "Write-Output 'git worktree add and ccos_anti_loop PRODUCT_HEAD_ADVANCED'"
                },
            }
        )
        self.assertEqual(result["ccos_decision"], "ALLOW")
        self.assertEqual(result["reason_code"], "NO_ANTI_LOOP_EVENT")

    def test_unchanged_and_support_only_product_heads_are_denied(self) -> None:
        self.seed_product_head()
        unchanged = {
            "hook_event_name": "PostToolUse",
            "tool_name": "shell_command",
            "tool_input": {"command": "git commit -m unchanged"},
            "tool_response": {"exit_code": 0},
        }
        self.assertEqual(self.evaluate(unchanged)["reason_code"], "ANTI_LOOP_EVENT_REJECTED")

        (self.repo / "docs" / "delivery").mkdir(parents=True)
        (self.repo / "docs" / "delivery" / "current-state.md").write_text(
            "support only\n", encoding="utf-8"
        )
        run_git(self.repo, "add", "docs/delivery/current-state.md")
        run_git(self.repo, "commit", "-q", "-m", "support only")
        support_only = {
            "hook_event_name": "PostToolUse",
            "tool_name": "shell_command",
            "tool_input": {"command": "git commit -m 'support only'"},
            "tool_response": {"exit_code": 0},
        }
        denied = self.evaluate(support_only)
        self.assertEqual(denied["ccos_decision"], "DENY")
        self.assertIn("outside the prior support-only scope", denied["reason"])

    def test_coding_os_hook_commit_counts_as_product_when_not_support_only(self) -> None:
        self.seed_product_head()
        hook_path = self.repo / "hooks" / "anti-loop-runtime" / "anti_loop_runtime.py"
        hook_path.parent.mkdir(parents=True)
        hook_path.write_text("print('Coding OS product change')\n", encoding="utf-8")
        run_git(self.repo, "add", "hooks/anti-loop-runtime/anti_loop_runtime.py")
        run_git(self.repo, "commit", "-q", "-m", "Coding OS hook product")
        result = self.evaluate(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "shell_command",
                "tool_input": {"command": "git commit -m 'Coding OS hook product'"},
                "tool_response": {"exit_code": 0},
            }
        )
        self.assertEqual(result["ccos_decision"], "ALLOW")
        self.assertEqual(result["event"]["event_type"], "PRODUCT_HEAD_ADVANCED")

    def test_case_resolution_is_exact_case_scoped_and_unbound_behavior_is_bounded(self) -> None:
        unbound_product = self.evaluate(
            self.product_patch(), environ={}, session_id="unbound-thread", cwd=str(self.root / "other")
        )
        self.assertEqual(unbound_product["ccos_decision"], "ALLOW")
        unbound_control = self.evaluate(
            self.control_patch(), environ={}, session_id="unbound-thread", cwd=str(self.root / "other")
        )
        self.assertEqual(unbound_control["ccos_decision"], "ALLOW")

        self.record_handoff_support()
        self.record_handoff_support()
        other_repo = "https://github.com/example/unrelated"
        unrelated = self.root / "unrelated-repository"
        unrelated.mkdir()
        run_git(unrelated, "init", "-q")
        run_git(unrelated, "config", "user.email", "unrelated@example.invalid")
        run_git(unrelated, "config", "user.name", "Unrelated")
        run_git(unrelated, "remote", "add", "origin", other_repo)
        (unrelated / "src").mkdir()
        (unrelated / "src" / "product.py").write_text("unrelated\n", encoding="utf-8")
        run_git(unrelated, "add", ".")
        run_git(unrelated, "commit", "-q", "-m", "unrelated")
        unrelated_result = self.evaluate(self.product_patch(), cwd=str(unrelated))
        self.assertEqual(unrelated_result["ccos_decision"], "ALLOW")
        self.assertEqual(unrelated_result["reason_code"], "UNRELATED_OR_UNBOUND_PRODUCT_SCOPE")
        ambiguous = self.evaluate(self.product_patch(), cwd=str(self.root / "other"))
        self.assertEqual(ambiguous["ccos_decision"], "DENY")
        self.assertEqual(ambiguous["reason_code"], "CASE_RESOLUTION_AMBIGUOUS")

    def test_ambiguous_thread_resolution_denies_mutation(self) -> None:
        case = self.store.get_case(self.case_id)

        class AmbiguousStore:
            def list_cases(self_nonlocal):
                return [case, json.loads(json.dumps(case))]

        payload = self.product_patch()
        payload.update({"session_id": THREAD_ID, "cwd": str(self.repo)})
        result = hook.evaluate(payload, environ=self.environ, store=AmbiguousStore())
        self.assertEqual(result["ccos_decision"], "DENY")
        self.assertEqual(result["reason_code"], "CASE_RESOLUTION_AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
