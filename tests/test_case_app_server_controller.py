#!/usr/bin/env python3
"""Focused tests for the proposal-only App Server controller."""

from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = ROOT / "scripts" / "agent"
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

controller_module = importlib.import_module("case_app_server_controller")
broker = importlib.import_module("case_runtime_broker")

WORKER_SID = "S-1-5-21-100-200-300-1005"
OFFLINE_SID = "S-1-5-21-100-200-300-1004"
GROUP_SID = "S-1-5-21-100-200-300-1003"
BROKER_SID = "S-1-5-21-100-200-300-1001"
CONTROLLER_KEY = bytes(range(32))
BASELINE = "baseline\n"
REPLACEMENT = "authorized replacement\n"


def make_thread(
    thread_id: str,
    cwd: str,
    source: object,
    turns: list[dict] | None = None,
) -> dict:
    return {
        "id": thread_id,
        "cwd": cwd,
        "source": source,
        "turns": list(turns or []),
        "createdAt": 100,
        "updatedAt": 101,
        "cliVersion": "0.146.0-alpha.3.1",
        "modelProvider": "openai",
        "sessionId": f"session-{thread_id}",
        "ephemeral": False,
        "preview": "",
        "status": {"type": "idle"},
    }


class FakeTransport:
    def __init__(
        self,
        spec: dict,
        *,
        bad_proposal: str | None = None,
        implementation_status: str = "completed",
        bad_sender: bool = False,
        bad_source: bool = False,
        child_descendant: bool = False,
        child_extra_item: str | None = None,
        snapshot: dict[str, dict] | None = None,
    ) -> None:
        self.spec = spec
        self.bad_proposal = bad_proposal
        self.implementation_status = implementation_status
        self.bad_sender = bad_sender
        self.bad_source = bad_source
        self.child_descendant = child_descendant
        self.child_extra_item = child_extra_item
        self.events: list[dict] = []
        self.audit: list[dict] = []
        self.requests: list[tuple[str, dict]] = []
        self.notifications: list[tuple[str, dict]] = []
        self.parent_id = "native-parent"
        self.threads = copy.deepcopy(snapshot or {})
        self.child_order: list[str] = [
            thread_id for thread_id in self.threads if thread_id != self.parent_id
        ]
        self.turn_count = 0

    def _proposal_text(self) -> str:
        if self.bad_proposal is not None:
            return self.bad_proposal
        return json.dumps(
            {
                "protocol_version": controller_module.PROPOSAL_PROTOCOL_VERSION,
                "schema_version": 1,
                "completion_state": "COMPLETED",
                "case_id": self.spec["case_id"],
                "operation_id": self.spec["operation_id"],
                "base_head": self.spec["base_head"],
                "target_path": self.spec["target_path"],
                "replacement_text": REPLACEMENT,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def request(self, method: str, params=None, timeout=None):
        del timeout
        params = dict(params or {})
        self.requests.append((method, copy.deepcopy(params)))
        if method == "initialize":
            return {"serverInfo": {"name": "fake", "version": "1"}}
        if method == "mcpServerStatus/list":
            return {"data": [], "nextCursor": None}
        if method == "hooks/list":
            return {"data": [], "nextCursor": None}
        if method == "thread/start":
            parent = make_thread(
                self.parent_id, self.spec["runtime_working_directory"], "appServer"
            )
            self.threads[self.parent_id] = parent
            return {
                "thread": parent,
                "approvalPolicy": "never",
                "sandbox": {"type": "readOnly", "networkAccess": False},
                "cwd": self.spec["runtime_working_directory"],
                "model": self.spec["model"],
                "approvalsReviewer": "user",
                "modelProvider": "openai",
                "reasoningEffort": self.spec["reasoning_effort"],
                "runtimeWorkspaceRoots": [self.spec["runtime_working_directory"]],
                "instructionSources": [],
                "activePermissionProfile": {"id": ":read-only", "extends": None},
            }
        if method == "thread/read":
            return {"thread": copy.deepcopy(self.threads[params["threadId"]])}
        if method == "thread/list":
            parent_id = params["parentThreadId"]
            matching = []
            if self.child_descendant and parent_id == "native-implementation":
                matching.append(
                    make_thread(
                        "native-illegal-descendant",
                        self.spec["runtime_working_directory"],
                        {
                            "subAgent": {
                                "thread_spawn": {
                                    "parent_thread_id": parent_id,
                                    "agent_path": "/root/implementation/illegal",
                                    "depth": 2,
                                }
                            }
                        },
                    )
                )
            for thread_id in self.child_order:
                source = self.threads[thread_id].get("source")
                spawn = source.get("subAgent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
                if spawn.get("parent_thread_id") == parent_id:
                    matching.append(copy.deepcopy(self.threads[thread_id]))
            return {
                "data": matching,
                "nextCursor": None,
            }
        if method == "turn/start":
            self.turn_count += 1
            prompt = params["input"][0]["text"]
            marker = "Spawn exactly one direct child named "
            name = prompt.split(marker, 1)[1].split(".", 1)[0]
            child_id = f"native-{name}"
            parent_turn_id = f"parent-turn-{self.turn_count}"
            agent_path = f"/root/{name}"
            source = {
                "subAgent": {
                    "thread_spawn": {
                        "parent_thread_id": (
                            "wrong-parent" if self.bad_source and name == "implementation"
                            else self.parent_id
                        ),
                        "agent_path": agent_path,
                        "depth": 1,
                    }
                }
            }
            if name == "implementation":
                message = self._proposal_text()
                status = self.implementation_status
            elif name == "incomplete":
                message = json.dumps(
                    {
                        "protocol_version": "ccos-incomplete-child-result-v1",
                        "schema_version": 1,
                        "completion_state": "INCOMPLETE",
                        "case_id": self.spec["case_id"],
                        "proposal": None,
                        "action": None,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
                status = "completed"
            elif name == controller_module.UNKNOWN_CHILD_NAME:
                message = "UNKNOWN_COMPLETE"
                status = "completed"
            else:
                message = f"READ_ONLY_{name.upper()}_COMPLETE"
                status = "completed"
            child_turn = {
                "id": f"{child_id}-turn-1",
                "status": status,
                "items": [{"type": "agentMessage", "text": message}],
            }
            if self.child_extra_item and name == "implementation":
                child_turn["items"].insert(0, {"type": self.child_extra_item})
            self.threads[child_id] = make_thread(
                child_id, self.spec["runtime_working_directory"], source, [child_turn]
            )
            self.child_order.append(child_id)
            collab = {
                "type": "collabAgentToolCall",
                "tool": "spawnAgent",
                "id": f"spawn-{name}",
                "senderThreadId": "wrong-sender" if self.bad_sender and name == "implementation" else self.parent_id,
                "receiverThreadIds": [child_id],
                "status": "completed",
                "agentsStates": {},
            }
            parent_turn = {
                "id": parent_turn_id,
                "status": "completed",
                "items": [collab],
            }
            self.events.extend(
                [
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": self.parent_id,
                            "turnId": parent_turn_id,
                            "item": copy.deepcopy(collab),
                        },
                    },
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": self.parent_id,
                            "turn": copy.deepcopy(parent_turn),
                        },
                    },
                ]
            )
            return {"turn": {"id": parent_turn_id, "status": "inProgress", "items": []}}
        raise AssertionError(method)

    def notify(self, method: str, params=None):
        self.notifications.append((method, dict(params or {})))

    def wait_turn_completed(self, thread_id: str, turn_id: str, timeout=None):
        del timeout
        for event in self.events:
            params = event["params"]
            if (
                event["method"] == "turn/completed"
                and params["threadId"] == thread_id
                and params["turn"]["id"] == turn_id
            ):
                return params["turn"]
        raise AssertionError((thread_id, turn_id))


class ProposalControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos-controller-")
        self.root = Path(self.temp.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.codex_home = self.root / "worker-codex-home"
        self.codex_home.mkdir()
        self.target = self.worktree / "candidate.txt"
        self.target.write_text(BASELINE, encoding="utf-8", newline="")
        self.proposal_root = self.root / "proposal-root"
        self.proposal_root.mkdir()
        self.proposal = self.proposal_root / "proposal.txt"
        self.case_id = str(uuid.uuid4())
        self.spec = {
            "protocol_version": controller_module.CONTROLLER_RUN_PROTOCOL_VERSION,
            "schema_version": 1,
            "case_id": self.case_id,
            "app_server_executable": str(Path(sys.executable).resolve()),
            "expected_app_server_sha256": hashlib.sha256(
                Path(sys.executable).read_bytes()
            ).hexdigest(),
            "expected_app_server_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "worker_codex_home": str(self.codex_home.resolve()),
            "runtime_working_directory": str(self.worktree.resolve()),
            "model": "gpt-test",
            "reasoning_effort": "high",
            "implementation_instruction": "Replace the baseline with the authorized replacement.",
            "instruction_source_pins": [],
            "repository": "https://github.com/example/runtime-boundary",
            "branch": "codex/runtime-boundary",
            "worktree": str(self.worktree.resolve()),
            "base_head": "a" * 40,
            "target_path": "candidate.txt",
            "baseline_sha256": hashlib.sha256(BASELINE.encode()).hexdigest(),
            "proposal_artifact_path": str(self.proposal.resolve()),
            "worker_principal_sid": WORKER_SID,
            "worker_offline_principal_sid": OFFLINE_SID,
            "sandbox_group_principal_sid": GROUP_SID,
            "broker_principal_sid": BROKER_SID,
            "expected_schema_file_count": 347,
            "expected_schema_tree_sha256": "4" * 64,
            "grant_id": "one-action-grant",
            "operation_id": "replace-candidate-once",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_controller(self, transport=None):
        instance = controller_module.ProposalController(
            self.spec,
            clock=lambda: "2026-07-25T00:00:00+00:00",
            nonce_factory=lambda: "fixed-controller-nonce",
        )
        fake = transport or FakeTransport(instance.spec)
        fake.spec = instance.spec
        result = instance.run(fake, CONTROLLER_KEY)
        return instance, fake, result

    def test_normalized_paths_recreate_signed_transport_context(self):
        normalized = controller_module._normalize_run_spec(self.spec)
        executable = Path(normalized["app_server_executable"])
        worker_codex_home = Path(normalized["worker_codex_home"])
        runtime_root = Path(normalized["runtime_working_directory"])
        environment = controller_module.build_app_server_environment(
            worker_codex_home, executable
        )
        _command, sandbox_state = controller_module.build_sandboxed_app_server_command(
            executable,
            cwd=runtime_root,
            worker_codex_home=worker_codex_home,
        )
        self.assertEqual(
            controller_module.canonical_json_sha256(sandbox_state),
            normalized["sandbox_profile_sha256"],
        )
        self.assertEqual(
            controller_module.canonical_json_sha256(environment),
            normalized["app_server_environment_sha256"],
        )
        transport = controller_module.AppServerTransport(
            executable=executable,
            expected_worker_sid=normalized["worker_principal_sid"],
            expected_broker_sid=normalized["broker_principal_sid"],
            expected_app_server_sha256=normalized["expected_app_server_sha256"],
            expected_app_server_version=normalized["expected_app_server_version"],
            expected_sandbox_profile_sha256=normalized["sandbox_profile_sha256"],
            expected_environment_sha256=normalized["app_server_environment_sha256"],
            worker_codex_home=worker_codex_home,
            cwd=runtime_root,
        )
        self.assertEqual(transport.sandbox_state, sandbox_state)

    def test_run_emits_exact_receipt_grant_and_role_bindings_without_mutation(self):
        _instance, fake, draft = self.run_controller()
        self.assertNotIn("controller_receipt", draft)
        live_evidence = {"both_process_trees_closed": True, "test_fixture": True}
        result = controller_module.finalize_controller_bundle(
            draft,
            live_evidence,
            controller_module.canonical_json_sha256(live_evidence),
            CONTROLLER_KEY,
        )
        self.assertEqual(self.target.read_text(encoding="utf-8"), BASELINE)
        self.assertEqual(self.proposal.read_text(encoding="utf-8"), REPLACEMENT)
        grant = result["grant_core"]
        self.assertEqual(grant["actor_thread_id"], "native-implementation")
        self.assertEqual(
            grant["proposal_artifact_path"],
            controller_module.normalize_binding("worktree", str(self.proposal.resolve())),
        )
        self.assertEqual(grant["replacement_sha256"], hashlib.sha256(REPLACEMENT.encode()).hexdigest())
        broker.verify_controller_receipt(
            result["controller_receipt"], grant, self.case_id, CONTROLLER_KEY
        )
        self.assertEqual(
            [entry["controller_assigned_role"] for entry in result["actor_binding_requests"]],
            [
                "parent", "implementer_child", "review_child", "review_child",
                "review_child", "closure_child", "incomplete_child",
            ],
        )
        self.assertNotIn("unknown_child", {
            entry["controller_assigned_role"] for entry in result["actor_binding_requests"]
        })
        self.assertFalse(result["broker_execute_invoked"])
        self.assertEqual(result["controller_receipt"]["schema_version"], 2)
        self.assertEqual(
            result["controller_receipt"]["worker_offline_principal_sid"], OFFLINE_SID
        )
        self.assertEqual(result["grant_core"]["denied_principal_sids"], [
            WORKER_SID, OFFLINE_SID, GROUP_SID,
        ])
        self.assertFalse(result["authorization_evidence"]["parent_permit_request_channel"])
        self.assertEqual(result["authorization_evidence"]["preissued_parent_permits"], 0)
        starts = [params for method, params in fake.requests if method == "turn/start"]
        self.assertEqual(len(starts), 7)
        for params in starts:
            self.assertEqual(params["approvalPolicy"], "never")
            self.assertEqual(params["approvalsReviewer"], "user")
            self.assertNotIn("dynamicTools", params)
            self.assertEqual(params["environments"], [])
            self.assertNotIn("selectedCapabilityRoots", params)
            self.assertEqual(
                params["sandboxPolicy"], {"type": "readOnly", "networkAccess": False}
            )
        thread_start = [params for method, params in fake.requests if method == "thread/start"]
        self.assertEqual(thread_start[0]["approvalPolicy"], "never")
        self.assertEqual(thread_start[0]["dynamicTools"], [])
        initialize = [params for method, params in fake.requests if method == "initialize"]
        self.assertEqual(initialize[0]["capabilities"], {"experimentalApi": True})

    def test_strict_proposal_rejects_markdown_or_wrong_completion(self):
        bad = "```json\n{}\n```"
        with self.assertRaises(controller_module.ControllerAuthorizationError):
            self.run_controller(FakeTransport(self.spec, bad_proposal=bad))
        self.assertFalse(self.proposal.exists())

        wrong = json.dumps(
            {
                "protocol_version": controller_module.PROPOSAL_PROTOCOL_VERSION,
                "schema_version": 1,
                "completion_state": "INCOMPLETE",
                "case_id": self.case_id,
                "operation_id": self.spec["operation_id"],
                "base_head": self.spec["base_head"],
                "target_path": self.spec["target_path"],
                "replacement_text": REPLACEMENT,
            },
            separators=(",", ":"),
        )
        with self.assertRaises(controller_module.ControllerAuthorizationError):
            self.run_controller(FakeTransport(self.spec, bad_proposal=wrong))
        self.assertFalse(self.proposal.exists())

    def test_native_incomplete_implementation_turn_cannot_authorize(self):
        with self.assertRaises(controller_module.ControllerAuthorizationError):
            self.run_controller(
                FakeTransport(self.spec, implementation_status="interrupted")
            )
        self.assertFalse(self.proposal.exists())

    def test_collaboration_sender_and_thread_read_source_are_both_authority_checks(self):
        with self.assertRaises(controller_module.ControllerProtocolError):
            self.run_controller(FakeTransport(self.spec, bad_sender=True))
        with self.assertRaises(controller_module.ControllerProtocolError):
            self.run_controller(FakeTransport(self.spec, bad_source=True))
        self.assertFalse(self.proposal.exists())

    def test_server_request_gate_declines_approvals_and_fails_closed_elsewhere(self):
        for method in controller_module.MUTATION_APPROVAL_METHODS:
            decision, fatal, outcome = controller_module.server_request_decision(method)
            self.assertEqual(decision, {"result": {"decision": "decline"}})
            self.assertFalse(fatal)
            self.assertEqual(outcome, "DECLINED")
            self.assertNotIn("acceptForSession", json.dumps(decision))
        decision, fatal, _ = controller_module.server_request_decision("item/tool/call")
        self.assertFalse(decision["result"]["success"])
        self.assertTrue(fatal)
        decision, fatal, _ = controller_module.server_request_decision(
            "mcpServer/elicitation/request"
        )
        self.assertEqual(decision, {"result": {"action": "decline"}})
        self.assertTrue(fatal)
        decision, fatal, _ = controller_module.server_request_decision("unknown/request")
        self.assertIn("error", decision)
        self.assertTrue(fatal)

    def test_child_collaboration_descendants_and_nonallowlisted_items_are_denied(self):
        with self.assertRaises(controller_module.ControllerAuthorizationError):
            self.run_controller(FakeTransport(self.spec, child_descendant=True))
        with self.assertRaises(controller_module.ControllerAuthorizationError):
            self.run_controller(FakeTransport(self.spec, child_extra_item="webSearch"))
        self.assertFalse(self.proposal.exists())

    def test_launch_environment_is_allowlisted_and_never_contains_controller_key(self):
        inherited = {
            "PATH": "C:\\bin",
            "SYSTEMROOT": "C:\\Windows",
            "GITHUB_TOKEN": "secret",
            "PROD_PASSWORD": "secret",
            controller_module.CONTROLLER_KEY_ENVIRONMENT: base64.b64encode(CONTROLLER_KEY).decode(),
            "UNRELATED": "not inherited",
        }
        environment = controller_module.build_app_server_environment(
            self.codex_home, Path(sys.executable), inherited=inherited
        )
        self.assertNotEqual(environment["PATH"], "C:\\bin")
        self.assertEqual(environment["CODEX_HOME"], str(self.codex_home.resolve()))
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("PROD_PASSWORD", environment)
        self.assertNotIn(controller_module.CONTROLLER_KEY_ENVIRONMENT, environment)
        command = controller_module.build_app_server_command(Path(sys.executable))
        self.assertIn("mcp_servers={}", command)
        self.assertIn('shell_environment_policy.inherit="none"', command)
        self.assertIn("--strict-config", command)
        self.assertEqual(command.count("--enable"), 1)
        self.assertEqual(command.count("--disable"), len(controller_module.APP_SERVER_DISABLED_FEATURES))
        for feature in controller_module.APP_SERVER_DISABLED_FEATURES:
            self.assertIn(feature, command)
        self.assertIn('web_search="disabled"', command)
        self.assertNotIn("thread/shellCommand", controller_module.CLIENT_REQUEST_METHODS)

        sandboxed, state = controller_module.build_sandboxed_app_server_command(
            Path(sys.executable).resolve(),
            cwd=self.worktree.resolve(),
            worker_codex_home=self.codex_home.resolve(),
        )
        self.assertTrue(state["permissionProfile"]["network"]["enabled"])
        self.assertEqual(
            state["permissionProfile"]["file_system"]["write"],
            [str(self.codex_home.resolve())],
        )
        self.assertNotIn("--sandbox-state-disable-network", sandboxed)
        delimiter = sandboxed.index("--")
        self.assertEqual(
            sandboxed[delimiter + 1 :],
            controller_module.build_app_server_command(Path(sys.executable).resolve()),
        )

    def test_suspended_wrapper_is_job_assigned_before_resume_and_assign_failure_kills(self):
        executable = Path(sys.executable).resolve()
        environment = controller_module.build_app_server_environment(
            self.codex_home.resolve(), executable
        )
        _command, state = controller_module.build_sandboxed_app_server_command(
            executable,
            cwd=self.worktree.resolve(),
            worker_codex_home=self.codex_home.resolve(),
        )

        class FakeProcess:
            def __init__(self):
                self.pid = 12345
                self._handle = 67890
                self.stdin = None
                self.stdout = None
                self.killed = False

            def wait(self, timeout=None):
                del timeout
                return 0

            def kill(self):
                self.killed = True

        process = FakeProcess()
        sequence = []

        class FakeJob:
            def assign(self, observed):
                self.observed = observed
                sequence.append("assign")

            def resume(self, observed):
                self.observed = observed
                sequence.append("resume")

            def close(self):
                sequence.append("close")

        transport = controller_module.AppServerTransport(
            executable=executable,
            expected_worker_sid=WORKER_SID,
            expected_broker_sid=BROKER_SID,
            expected_app_server_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            expected_app_server_version=(
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            ),
            expected_sandbox_profile_sha256=controller_module.canonical_json_sha256(state),
            expected_environment_sha256=controller_module.canonical_json_sha256(environment),
            worker_codex_home=self.codex_home.resolve(),
            cwd=self.worktree.resolve(),
        )
        identity = {
            "pid": 54321,
            "sid": WORKER_SID,
            "command_line_sha256": "6" * 64,
            "argv_sha256": transport.app_server_command_sha256,
        }
        with (
            mock.patch.object(controller_module, "windows_identity", return_value=("broker", BROKER_SID)),
            mock.patch.object(controller_module, "_prepare_worker_environment"),
            mock.patch.object(
                controller_module,
                "_worker_environment_evidence",
                return_value={"environment_values_sha256": transport.expected_environment_sha256},
            ),
            mock.patch.object(controller_module, "WindowsKillOnCloseJob", return_value=FakeJob()),
            mock.patch.object(controller_module.subprocess, "Popen", return_value=process) as popen,
            mock.patch.object(controller_module.threading.Thread, "start"),
            mock.patch.object(
                controller_module, "inspect_app_server_process_identity", return_value=identity
            ),
            mock.patch.object(
                controller_module, "inspect_worker_environment_acls", return_value={"fixed": True}
            ),
            mock.patch.object(controller_module, "_windows_process_is_running", return_value=False),
        ):
            transport.start()
            self.assertEqual(sequence[:2], ["assign", "resume"])
            self.assertEqual(
                popen.call_args.kwargs["creationflags"],
                getattr(controller_module.subprocess, "CREATE_SUSPENDED", 0x00000004),
            )
            transport.close()
            self.assertIn("close", sequence)

        failing_process = FakeProcess()

        class FailingJob(FakeJob):
            def assign(self, observed):
                self.observed = observed
                raise controller_module.ControllerProtocolError("assign failed")

        failing = controller_module.AppServerTransport(
            executable=executable,
            expected_worker_sid=WORKER_SID,
            expected_broker_sid=BROKER_SID,
            expected_app_server_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            expected_app_server_version=(
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            ),
            expected_sandbox_profile_sha256=controller_module.canonical_json_sha256(state),
            expected_environment_sha256=controller_module.canonical_json_sha256(environment),
            worker_codex_home=self.codex_home.resolve(),
            cwd=self.worktree.resolve(),
        )
        with (
            mock.patch.object(controller_module, "windows_identity", return_value=("broker", BROKER_SID)),
            mock.patch.object(controller_module, "_prepare_worker_environment"),
            mock.patch.object(
                controller_module,
                "_worker_environment_evidence",
                return_value={"environment_values_sha256": failing.expected_environment_sha256},
            ),
            mock.patch.object(controller_module, "WindowsKillOnCloseJob", return_value=FailingJob()),
            mock.patch.object(controller_module.subprocess, "Popen", return_value=failing_process),
        ):
            with self.assertRaises(controller_module.ControllerProtocolError):
                failing.start()
        self.assertTrue(failing_process.killed)

    def test_restart_reconstructs_all_native_identities_and_detects_drift(self):
        _instance, fake, result = self.run_controller()
        snapshot = copy.deepcopy(fake.threads)
        restarted = FakeTransport(self.spec, snapshot=snapshot)
        evidence = controller_module.verify_restart_continuity(
            restarted, self.spec, result["restart_checkpoint"]
        )
        self.assertTrue(evidence["continuity_verified"])
        self.assertEqual(len(evidence["verified_thread_ids"]), 8)

        drifted = copy.deepcopy(snapshot)
        drifted["native-reviewer_coding_os"]["source"]["subAgent"]["thread_spawn"]["depth"] = 2
        with self.assertRaises(controller_module.ControllerProtocolError):
            controller_module.verify_restart_continuity(
                FakeTransport(self.spec, snapshot=drifted),
                self.spec,
                result["restart_checkpoint"],
            )

    def test_controller_instance_cannot_emit_a_second_grant_bundle(self):
        instance, _fake, result = self.run_controller()
        self.assertTrue(instance.grant_emitted)
        with self.assertRaises(controller_module.ControllerAuthorizationError):
            instance._build_bundle(
                parent_identity=result["restart_checkpoint"]["identities"][0],
                child_records=[],
                proposal_bytes=b"x",
                native_turn_evidence_sha256="1" * 64,
                implementation_turn_id="turn-two",
            )

    def test_cli_has_no_execute_or_arbitrary_mutation_arguments(self):
        parser = controller_module.build_parser()
        actions = parser._subparsers._group_actions[0].choices
        self.assertEqual(set(actions), {"run", "verify-restart"})
        help_text = parser.format_help()
        self.assertNotIn("command-json", help_text)
        self.assertNotIn("replacement-text", help_text)
        self.assertNotIn("broker-execute", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
