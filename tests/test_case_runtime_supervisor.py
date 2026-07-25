#!/usr/bin/env python3
"""Focused tests for the trusted runtime lifecycle supervisor."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = ROOT / "scripts" / "agent"
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

controller = importlib.import_module("case_app_server_controller")
supervisor = importlib.import_module("case_runtime_supervisor")
case_state = importlib.import_module("case_state")

ONLINE_SID = "S-1-5-21-100-200-300-1005"
OFFLINE_SID = "S-1-5-21-100-200-300-1004"
GROUP_SID = "S-1-5-21-100-200-300-1003"
BROKER_SID = "S-1-5-21-100-200-300-1001"
BASELINE = b"baseline\n"
REPLACEMENT = b"replacement\n"


class FakeStore:
    def __init__(self, target: Path, *, orphan_status: str | None = None) -> None:
        self.target = target
        self.calls: list[tuple[str, dict]] = []
        grants: dict[str, dict] = {}
        if orphan_status:
            grants["orphan-grant"] = {
                "grant_id": "orphan-grant",
                "status": orphan_status,
                "broker_principal_sid": BROKER_SID,
            }
        self.case = {
            "case_id": "",
            "revision": 20,
            "state": "CASE_LOCKED" if orphan_status == "FAILED" else "IMPLEMENTING",
            "bindings": {"thread": []},
            "runtime": {"actors": {}, "action_grants": grants},
            "runtime_generation_attempt": None,
            "runtime_generation_abort": None,
        }

    def get_case(self, case_id: str) -> dict:
        self.case["case_id"] = case_id
        return copy.deepcopy(self.case)

    def bind(self, case_id: str, **kwargs):
        self.calls.append(("bind", copy.deepcopy(kwargs)))
        if kwargs["expected_revision"] != self.case["revision"]:
            raise case_state.RevisionConflict("stale")
        self.case["bindings"]["thread"].append(kwargs["value"])
        self.case["revision"] += 1
        return {"case_id": case_id, "revision": self.case["revision"], "idempotent": False}

    def bind_runtime_actor(self, case_id: str, **kwargs):
        self.calls.append(("bind_runtime_actor", copy.deepcopy(kwargs)))
        if kwargs["expected_revision"] != self.case["revision"]:
            raise case_state.RevisionConflict("stale")
        actor = copy.deepcopy(kwargs["actor"])
        self.case["runtime"]["actors"][actor["thread_id"]] = actor
        self.case["revision"] += 1
        return {"case_id": case_id, "revision": self.case["revision"], "idempotent": False}

    def claim_runtime_generation_attempt(self, case_id: str, **kwargs):
        self.calls.append(("claim_runtime_generation_attempt", copy.deepcopy(kwargs)))
        if kwargs["expected_revision"] != self.case["revision"]:
            raise case_state.RevisionConflict("stale")
        if self.case.get("runtime_generation_attempt") is not None:
            raise case_state.LimitError("attempt consumed")
        record = {
            **copy.deepcopy(kwargs["attempt"]),
            "status": "CLAIMED",
            "claimed_at": "2026-07-25T12:00:00+00:00",
            "finalized_at": None,
            "abort_reason_code": None,
        }
        record["record_sha256"] = case_state.canonical_json_sha256(record)
        self.case["runtime_generation_attempt"] = record
        self.case["revision"] += 1
        return {"case_id": case_id, "status": "CLAIMED", "idempotent": False}

    def abort_runtime_generation_attempt(self, case_id: str, **kwargs):
        self.calls.append(("abort_runtime_generation_attempt", copy.deepcopy(kwargs)))
        if kwargs["expected_revision"] != self.case["revision"]:
            raise case_state.RevisionConflict("stale")
        attempt = self.case["runtime_generation_attempt"]
        if attempt["status"] != "CLAIMED" or attempt["attempt_id"] != kwargs["attempt_id"]:
            raise case_state.AuthorizationError("attempt mismatch")
        attempt["status"] = "ABORTED"
        attempt["finalized_at"] = "2026-07-25T12:00:00+00:00"
        attempt["abort_reason_code"] = "CONTROLLER_GENERATION_ABANDONED"
        attempt["record_sha256"] = case_state.canonical_json_sha256(
            {name: value for name, value in attempt.items() if name != "record_sha256"}
        )
        self.case["state"] = "CASE_LOCKED"
        self.case["revision"] += 1
        return {"case_id": case_id, "status": "ABORTED", "idempotent": False}

    def issue_action_grant(self, case_id: str, **kwargs):
        self.calls.append(("issue_action_grant", copy.deepcopy(kwargs)))
        if kwargs["expected_revision"] != self.case["revision"]:
            raise case_state.RevisionConflict("stale")
        grant = copy.deepcopy(kwargs["grant"])
        actor = self.case["runtime"]["actors"].get(grant["actor_thread_id"])
        if not actor or actor["controller_assigned_role"] != "implementer_child":
            raise case_state.AuthorizationError("actor is not implementer")
        if self.case["runtime"]["action_grants"]:
            raise case_state.LimitError("one grant already exists")
        attempt = self.case.get("runtime_generation_attempt")
        if not attempt or attempt["status"] != "CLAIMED" or attempt["grant_id"] != grant["grant_id"]:
            raise case_state.AuthorizationError("generation attempt missing")
        grant["status"] = "ISSUED"
        self.case["runtime"]["action_grants"][grant["grant_id"]] = grant
        attempt["status"] = "GRANT_ISSUED"
        attempt["finalized_at"] = "2026-07-25T12:00:00+00:00"
        attempt["record_sha256"] = case_state.canonical_json_sha256(
            {name: value for name, value in attempt.items() if name != "record_sha256"}
        )
        self.case["revision"] += 1
        return {
            "case_id": case_id,
            "revision": self.case["revision"],
            "grant_id": grant["grant_id"],
            "idempotent": False,
        }

    def fail_action_grant(self, case_id: str, **kwargs):
        self.calls.append(("fail_action_grant", copy.deepcopy(kwargs)))
        grant = self.case["runtime"]["action_grants"][kwargs["failure"]["grant_id"]]
        grant["status"] = "FAILED"
        self.case["state"] = "CASE_LOCKED"
        self.case["revision"] += 1
        return {"case_id": case_id, "state": "CASE_LOCKED", "idempotent": False}

    def check_action(self, _case_id: str, _action: str, **kwargs):
        actor = self.case["runtime"]["actors"].get(kwargs.get("actor_thread_id"))
        if not actor:
            return {"allowed": False, "reason_codes": ["RUNTIME_ACTOR_UNBOUND"]}
        bound_role = actor["controller_assigned_role"]
        if bound_role != kwargs.get("actor_role"):
            return {"allowed": False, "reason_codes": ["ACTOR_ROLE_MISMATCH"]}
        return {
            "allowed": bound_role == "implementer_child",
            "reason_codes": [
                "ACTION_ALLOWED" if bound_role == "implementer_child" else "ROLE_ACTION_DENIED"
            ],
        }

    def abort_runtime_generation(self, case_id: str, **kwargs):
        self.calls.append(("abort_runtime_generation", copy.deepcopy(kwargs)))
        if kwargs["expected_revision"] != self.case["revision"]:
            raise case_state.RevisionConflict("stale")
        evidence = copy.deepcopy(kwargs["evidence"])
        record = {**evidence, "aborted_at": "2026-07-25T12:00:00+00:00"}
        record["record_sha256"] = case_state.canonical_json_sha256(record)
        self.case["runtime_generation_abort"] = record
        self.case["state"] = "CASE_LOCKED"
        self.case["revision"] += 1
        return {"case_id": case_id, "state": "CASE_LOCKED", "idempotent": False}


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ccos-supervisor-")
        self.worker_temporary = tempfile.TemporaryDirectory(prefix="ccos-supervisor-worker-")
        self.root = Path(self.temporary.name)
        self.state_root = self.root / "state"
        self.state_root.mkdir()
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.worker_home = Path(self.worker_temporary.name) / "worker-home"
        self.worker_home.mkdir()
        self.proposal_root = self.root / "proposal-root"
        self.proposal_root.mkdir()
        self.target = self.worktree / "candidate.txt"
        self.target.write_bytes(BASELINE)
        self.proposal = self.proposal_root / "proposal.txt"
        self.case_id = str(uuid.uuid4())
        executable = Path(sys.executable).resolve()
        self.controller_spec = {
            "protocol_version": controller.CONTROLLER_RUN_PROTOCOL_VERSION,
            "schema_version": 1,
            "case_id": self.case_id,
            "app_server_executable": str(executable),
            "expected_app_server_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "expected_app_server_version": (
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            ),
            "worker_codex_home": str(self.worker_home),
            "runtime_working_directory": str(self.worktree),
            "model": "gpt-test",
            "reasoning_effort": "high",
            "implementation_instruction": "Return the fixed replacement.",
            "instruction_source_pins": [],
            "repository": "https://github.com/example/runtime-supervisor",
            "branch": "codex/runtime-supervisor",
            "worktree": str(self.worktree),
            "base_head": "a" * 40,
            "target_path": "candidate.txt",
            "baseline_sha256": hashlib.sha256(BASELINE).hexdigest(),
            "proposal_artifact_path": str(self.proposal),
            "worker_principal_sid": ONLINE_SID,
            "worker_offline_principal_sid": OFFLINE_SID,
            "sandbox_group_principal_sid": GROUP_SID,
            "broker_principal_sid": BROKER_SID,
            "expected_schema_file_count": 347,
            "expected_schema_tree_sha256": "1" * 64,
            "grant_id": "one-action-grant",
            "operation_id": "replace-candidate-once",
        }
        self.spec = {
            "protocol_version": supervisor.SUPERVISOR_RUN_PROTOCOL_VERSION,
            "schema_version": 1,
            "state_root": str(self.state_root),
            "controller_spec": self.controller_spec,
            "worker_offline_principal_sid": OFFLINE_SID,
            "sandbox_users_group_sid": GROUP_SID,
            "expected_schema_file_count": 347,
            "expected_schema_tree_sha256": "1" * 64,
        }
        self.store = FakeStore(self.target)
        self.key_reference: bytearray | None = None
        self.broker_key_nonzero: list[bool] = []
        self.acl_restore_calls: list[dict] = []
        self.bundle = self._bundle()

    def tearDown(self) -> None:
        self.temporary.cleanup()
        self.worker_temporary.cleanup()

    def _identity(self, name: str, parent: str | None) -> dict:
        return {
            "thread_id": f"native-{name}",
            "parent_thread_id": parent,
            "agent_path": "/root" if parent is None else f"/root/{name}",
            "identity_evidence_sha256": hashlib.sha256(name.encode()).hexdigest(),
        }

    def _transport_audit(self, phase: str, run_spec: dict) -> list[dict]:
        environment_body = {
            "environment_names": sorted(case_state.LIVE_CONTROLLER_ENVIRONMENT_NAMES),
            "environment_values_sha256": run_spec["app_server_environment_sha256"],
            "mutable_paths_sha256": "a" * 64,
            "path_entries_sha256": "b" * 64,
            "controller_key_exposed": False,
            "secret_like_name_count": 0,
            "mutable_paths_within_worker_root": True,
        }
        environment = {
            **environment_body,
            "evidence_sha256": case_state.canonical_json_sha256(environment_body),
        }
        process_digest = case_state.canonical_json_sha256({"phase": phase})
        methods = (
            case_state.LIVE_CONTROLLER_INITIAL_REQUIRED_METHODS
            if phase == "initial"
            else case_state.LIVE_CONTROLLER_RESTART_REQUIRED_METHODS
        )
        return [
            {
                "event": "app_server_launch",
                "sandbox_launcher_command_sha256": "c" * 64,
                "app_server_command_sha256": "d" * 64,
                "environment_evidence": environment,
                "controller_key_exposed": False,
                "controller_principal_sid": BROKER_SID,
                "controller_principal_matches_broker": True,
                "mcp_override": "empty",
                "shell_environment_inherit": "none",
            },
            {
                "event": "restricted_app_server_identity",
                "process_instance_sha256": process_digest,
                "worker_principal_sid": ONLINE_SID,
                "principal_distinct_from_broker": True,
                "app_server_sha256": run_spec["expected_app_server_sha256"],
                "app_server_version": run_spec["expected_app_server_version"],
                "command_line_sha256": "e" * 64,
                "argv_sha256": "f" * 64,
                "argv_matches_sealed_command": True,
                "worker_environment_acl_evidence": {"evidence_sha256": "1" * 64},
                "kill_on_job_close": True,
            },
            *[
                {
                    "event": "client_request",
                    "method": method,
                    "allowlisted": True,
                    "shell_command_requested": False,
                }
                for method in sorted(methods)
            ],
            {
                "event": "app_server_process_tree_closed",
                "process_instance_sha256": process_digest,
                "kill_on_job_close": True,
                "descendant_exit_verified": True,
            },
        ]

    def _bundle(self) -> dict:
        parent = self._identity("parent", None)
        plan = [
            ("implementation", "implementer_child"),
            ("reviewer_coding_os", "review_child"),
            ("reviewer_leheta", "review_child"),
            ("reviewer_healpath", "review_child"),
            ("closure", "closure_child"),
            ("incomplete", "incomplete_child"),
            ("unrecognized_probe", "unknown_child"),
        ]
        children = [(self._identity(name, parent["thread_id"]), role) for name, role in plan]
        actors = []
        for identity, role in [(parent, "parent"), *children[:-1]]:
            actors.append(
                {
                    "protocol_version": case_state.RUNTIME_ACTOR_PROTOCOL_VERSION,
                    "schema_version": 1,
                    **identity,
                    "controller_assigned_role": role,
                    "binding_source": "native_thread_read",
                }
            )
        implementation = children[0][0]
        grant_core = {
            "protocol_version": case_state.ACTION_GRANT_PROTOCOL_VERSION,
            "schema_version": 1,
            "grant_id": "one-action-grant",
            "actor_thread_id": implementation["thread_id"],
            "actor_turn_id": "implementation-turn",
            "native_turn_evidence_sha256": "2" * 64,
            "operation_id": "replace-candidate-once",
            "action": "implementation",
            "operation": "replace_existing_file_v1",
            "repository": "https://github.com/example/runtime-supervisor",
            "branch": "codex/runtime-supervisor",
            "worktree": str(self.worktree.resolve()),
            "base_head": "a" * 40,
            "target_path": "candidate.txt",
            "baseline_sha256": hashlib.sha256(BASELINE).hexdigest(),
            "replacement_sha256": hashlib.sha256(REPLACEMENT).hexdigest(),
            "proposal_artifact_path": str(self.proposal.resolve()),
            "proposal_size": len(REPLACEMENT),
            "worker_principal_sid": ONLINE_SID,
            "model_worker_principal_sid": OFFLINE_SID,
            "sandbox_group_principal_sid": GROUP_SID,
            "denied_principal_sids": [ONLINE_SID, OFFLINE_SID, GROUP_SID],
            "broker_principal_sid": BROKER_SID,
            "app_server_sha256": self.controller_spec["expected_app_server_sha256"],
            "app_server_version": self.controller_spec["expected_app_server_version"],
            "app_server_executable_path": self.controller_spec["app_server_executable"],
            "worker_runtime_root": self.controller_spec["worker_codex_home"],
            "schema_file_count": 347,
            "schema_tree_sha256": "1" * 64,
            "sandbox_profile_sha256": "4" * 64,
            "app_server_environment_sha256": "5" * 64,
        }
        denied = [
            {"thread_id": parent["thread_id"], "controller_assigned_role": "parent"},
            *[
                {"thread_id": identity["thread_id"], "controller_assigned_role": role}
                for identity, role in children[1:]
            ],
        ]
        receipt_body = {
            "protocol_version": controller.CONTROLLER_RECEIPT_PROTOCOL_VERSION,
            "schema_version": 2,
            "case_id": self.case_id,
            "grant_id": grant_core["grant_id"],
            "actor_thread_id": grant_core["actor_thread_id"],
            "actor_turn_id": grant_core["actor_turn_id"],
            "action": "implementation",
            "operation_id": grant_core["operation_id"],
            "base_head": grant_core["base_head"],
            "target_path": grant_core["target_path"],
            "proposal_sha256": grant_core["replacement_sha256"],
            "proposal_size": grant_core["proposal_size"],
            "completion_state": "COMPLETED",
            "native_turn_evidence_sha256": grant_core["native_turn_evidence_sha256"],
            "repository": grant_core["repository"],
            "branch": grant_core["branch"],
            "worktree": grant_core["worktree"],
            "baseline_sha256": grant_core["baseline_sha256"],
            "worker_runtime_root": grant_core["worker_runtime_root"],
            "worker_online_principal_sid": ONLINE_SID,
            "worker_offline_principal_sid": OFFLINE_SID,
            "sandbox_group_principal_sid": GROUP_SID,
            "broker_principal_sid": BROKER_SID,
            "app_server_sha256": grant_core["app_server_sha256"],
            "app_server_version": grant_core["app_server_version"],
            "app_server_executable_path": grant_core["app_server_executable_path"],
            "schema_file_count": grant_core["schema_file_count"],
            "schema_tree_sha256": grant_core["schema_tree_sha256"],
            "sandbox_profile_sha256": grant_core["sandbox_profile_sha256"],
            "app_server_environment_sha256": grant_core["app_server_environment_sha256"],
            "issued_at": "2026-07-25T12:00:00+00:00",
            "nonce": "test-receipt-nonce",
        }
        incomplete_body = {
            "thread_id": children[5][0]["thread_id"],
            "turn_id": "incomplete-turn",
            "completion_state": "INCOMPLETE",
            "proposal_count": 0,
            "action_count": 0,
            "result_sha256": "8" * 64,
        }
        return {
            "case_id": self.case_id,
            "proposal_artifact": {
                "path": str(self.proposal.resolve()),
                "sha256": hashlib.sha256(REPLACEMENT).hexdigest(),
                "size": len(REPLACEMENT),
                "nlink": 1,
            },
            "controller_receipt_body": receipt_body,
            "grant_core": grant_core,
            "incomplete_child_evidence": {
                **incomplete_body,
                "evidence_sha256": case_state.canonical_json_sha256(incomplete_body),
            },
            "actor_binding_requests": actors,
            "restart_checkpoint": {
                "identities": [parent, *[identity for identity, _role in children]],
            },
            "authorization_evidence": {
                "nonimplementation_identities_for_supervisor_probe": denied,
            },
        }

    def dependencies(self, *, controller_should_run: bool = True, startup_store=None):
        race_lock = threading.Lock()
        race_success_consumed = False
        capability = {
            "client_capabilities": {"experimentalApi": True},
            "mcp_server_count": 0,
            "hook_count": 0,
            "dynamic_tools": [],
        }

        def run_controller(_spec, key):
            if not controller_should_run:
                raise AssertionError("controller must not run")
            self.key_reference = key
            self.proposal.write_bytes(REPLACEMENT)
            bundle = copy.deepcopy(self.bundle)
            bundle["capability_evidence"] = copy.deepcopy(capability)
            bundle["transport_audit"] = self._transport_audit("initial", _spec)
            return bundle

        def restart(_spec, checkpoint):
            return {
                "continuity_verified": True,
                "verified_thread_ids": sorted(
                    item["thread_id"] for item in checkpoint["identities"]
                ),
                "capability_evidence": copy.deepcopy(capability),
                "transport_audit": self._transport_audit("restart", _spec),
            }

        def isolation(_store, spec, _bundle):
            protected_acl_snapshot = [
                {
                    "path": str(self.worktree.resolve()),
                    "owner_sid": BROKER_SID,
                    "sddl": "O:BAG:BAD:(A;;FA;;;BA)",
                    "sddl_sha256": "6" * 64,
                    "entry_sha256": "7" * 64,
                }
            ]
            preissue_dacl_evidence = {"verified": True, "protocol_version": "test"}
            return {
                "model_worker_principal_sid": spec["worker_offline_principal_sid"],
                "sandbox_group_principal_sid": spec["sandbox_users_group_sid"],
                "denied_principal_sids": [ONLINE_SID, OFFLINE_SID, GROUP_SID],
                "group_membership_evidence": {"verified": True},
                "isolation_evidence": {"all_mutations_denied": True},
                "protected_acl_snapshot": protected_acl_snapshot,
                "protected_acl_snapshot_sha256": case_state.canonical_json_sha256(
                    protected_acl_snapshot
                ),
                "preissue_dacl_evidence": preissue_dacl_evidence,
                "preissue_dacl_evidence_sha256": case_state.canonical_json_sha256(
                    preissue_dacl_evidence
                ),
            }

        def run_broker(_state_root, _case_id, grant_id, _receipt, key):
            nonlocal race_success_consumed
            self.broker_key_nonzero.append(any(key))
            with race_lock:
                if not race_success_consumed:
                    race_success_consumed = True
                    self.target.write_bytes(REPLACEMENT)
                    self.store.case["runtime"]["action_grants"][grant_id]["status"] = "COMPLETED"
                    self.store.case["revision"] += 2
                    return {"ok": True, "result": {"status": "COMPLETED"}}
            return {"ok": False, "error": "BrokerAuthorizationError"}

        def restore_acl(
            _state_root, _case_id, grant_id, snapshot, snapshot_sha256,
            preissue_dacl_evidence, restore_reason,
        ):
            self.acl_restore_calls.append(
                {
                    "grant_id": grant_id,
                    "snapshot": copy.deepcopy(snapshot),
                    "snapshot_sha256": snapshot_sha256,
                    "preissue_dacl_evidence": copy.deepcopy(preissue_dacl_evidence),
                    "restore_reason": restore_reason,
                }
            )
            return {
                "restored": True,
                "already_restored": True,
                "protected_acl_snapshot_sha256": snapshot_sha256,
            }

        def recover_orphan(_state_root, case_id, grant_id):
            active_store = startup_store or self.store
            grant = active_store.case["runtime"]["action_grants"][grant_id]
            if grant["status"] == "FAILED":
                return {"status": "failed_stable", "acl_restore": {"restored": True}}
            grant["status"] = "FAILED"
            active_store.case["state"] = "CASE_LOCKED"
            active_store.case["revision"] += 1
            active_store.calls.append(("recover_orphaned_action_grant", {"grant_id": grant_id}))
            return {"status": "FAILED", "case_id": case_id}

        return supervisor.SupervisorDependencies(
            controller_runner=run_controller,
            restart_runner=restart,
            isolation_collector=isolation,
            broker_runner=run_broker,
            schema_checker=lambda _spec: {"compatible": True, "file_count": 347},
            final_verifier=lambda grant: {
                "head": grant["base_head"],
                "status_paths": [grant["target_path"]],
                "target_sha256": grant["replacement_sha256"],
            },
            completed_recovery_verifier=lambda _root, _case, grant, _spec: {
                "grant_id": grant["grant_id"], "verified": True,
            },
            acl_restore_runner=restore_acl,
            pending_acl_recovery=lambda _root, _case_id, _spec: [],
            orphan_grant_recovery=recover_orphan,
            identity_resolver=lambda: ("synthetic-broker", BROKER_SID),
            clock=lambda: "2026-07-25T12:00:00+00:00",
            nonce_factory=(lambda values=iter(range(1000)): f"nonce-{next(values)}"),
        )

    def test_full_sequence_binds_eight_threads_denies_seven_and_executes_once(self):
        instance = supervisor.RuntimeSupervisor(
            self.spec, store=self.store, dependencies=self.dependencies()
        )
        result = instance.run()
        self.assertEqual(result["accepted_action_count"], 1)
        self.assertEqual(result["binding_count"], 14)
        self.assertEqual(len(result["canonical_denial_evidence"]), 7)
        self.assertEqual(self.target.read_bytes(), REPLACEMENT)
        self.assertEqual(self.broker_key_nonzero, [True, True, True])
        self.assertTrue(instance.key_zeroized)
        self.assertEqual(len(self.acl_restore_calls), 1)
        self.assertEqual(
            self.acl_restore_calls[0]["restore_reason"], "supervisor_finally"
        )
        self.assertTrue(result["acl_restore_evidence"]["restored"])
        self.assertIsNotNone(self.key_reference)
        self.assertFalse(any(self.key_reference or b""))
        issue_calls = [item for item in self.store.calls if item[0] == "issue_action_grant"]
        self.assertEqual(len(issue_calls), 10)
        real = issue_calls[-2][1]
        self.assertIn("isolation_evidence", real["grant"])
        self.assertIn("protected_acl_snapshot", real["grant"])
        self.assertIn("preissue_dacl_evidence", real["grant"])
        self.assertIn("expires_at", real["grant"])
        self.assertNotIn("expected_revision", self.bundle["grant_core"])

    def test_controller_failure_aborts_claim_and_replay_never_regenerates(self):
        calls = 0

        def fail_controller(_spec, _key):
            nonlocal calls
            calls += 1
            raise RuntimeError("synthetic controller failure")

        dependencies = replace(
            self.dependencies(), controller_runner=fail_controller
        )
        with self.assertRaisesRegex(RuntimeError, "synthetic controller failure"):
            supervisor.RuntimeSupervisor(
                self.spec, store=self.store, dependencies=dependencies
            ).run()
        self.assertEqual(calls, 1)
        self.assertEqual(self.store.case["state"], "CASE_LOCKED")
        self.assertEqual(
            self.store.case["runtime_generation_attempt"]["status"], "ABORTED"
        )
        self.assertEqual(
            sum(call[0] == "claim_runtime_generation_attempt" for call in self.store.calls),
            1,
        )
        self.assertEqual(
            sum(call[0] == "abort_runtime_generation_attempt" for call in self.store.calls),
            1,
        )
        replay = supervisor.RuntimeSupervisor(
            self.spec,
            store=self.store,
            dependencies=self.dependencies(
                controller_should_run=False, startup_store=self.store
            ),
        ).run()
        self.assertTrue(replay["recovered_generation_attempt_abort"])
        self.assertFalse(replay["controller_started"])
        self.assertEqual(calls, 1)

    def test_crash_left_claim_is_aborted_before_schema_or_controller(self):
        attempt = {
            "protocol_version": case_state.RUNTIME_GENERATION_ATTEMPT_PROTOCOL_VERSION,
            "schema_version": 1,
            "attempt_id": f"controller-{self.controller_spec['grant_id']}",
            "grant_id": self.controller_spec["grant_id"],
            "controller_spec_sha256": "9" * 64,
            "status": "CLAIMED",
            "claimed_at": "2026-07-25T12:00:00+00:00",
            "finalized_at": None,
            "abort_reason_code": None,
        }
        attempt["record_sha256"] = case_state.canonical_json_sha256(attempt)
        self.store.case["runtime_generation_attempt"] = attempt

        def forbidden_schema(_spec):
            raise AssertionError("schema must not run after a crash-left claim")

        dependencies = replace(
            self.dependencies(controller_should_run=False, startup_store=self.store),
            schema_checker=forbidden_schema,
        )
        result = supervisor.RuntimeSupervisor(
            self.spec, store=self.store, dependencies=dependencies
        ).run()
        self.assertTrue(result["recovered_generation_attempt_abort"])
        self.assertFalse(result["controller_started"])
        self.assertEqual(self.store.case["state"], "CASE_LOCKED")
        self.assertEqual(
            self.store.case["runtime_generation_attempt"]["status"], "ABORTED"
        )

    def test_orphan_is_failed_and_locked_before_controller_or_schema(self):
        store = FakeStore(self.target, orphan_status="ISSUED")
        instance = supervisor.RuntimeSupervisor(
            self.spec,
            store=store,
            dependencies=self.dependencies(
                controller_should_run=False, startup_store=store
            ),
        )
        with self.assertRaises(supervisor.SupervisorAuthorizationError):
            instance.run()
        self.assertEqual(store.case["state"], "CASE_LOCKED")
        self.assertTrue(
            any(call[0] == "recover_orphaned_action_grant" for call in store.calls)
        )

    def test_pending_preissue_recovery_locks_generation_without_regeneration(self):
        store = FakeStore(self.target)
        recovery = {
            "restored": True,
            "grant_id": self.controller_spec["grant_id"],
            "snapshot_event_sha256": "a" * 64,
            "lockdown_intent_event_sha256": "b" * 64,
            "acl_restored_event_sha256": "c" * 64,
        }
        dependencies = replace(
            self.dependencies(controller_should_run=False, startup_store=store),
            pending_acl_recovery=lambda _root, _case_id, _spec: [copy.deepcopy(recovery)],
        )
        result = supervisor.RuntimeSupervisor(
            self.spec, store=store, dependencies=dependencies
        ).run()
        self.assertTrue(result["recovered_preissue_generation_abort"])
        self.assertEqual(store.case["state"], "CASE_LOCKED")
        self.assertEqual(
            sum(call[0] == "abort_runtime_generation" for call in store.calls), 1
        )
        replay = supervisor.RuntimeSupervisor(
            self.spec, store=store, dependencies=dependencies
        ).run()
        self.assertTrue(replay["recovered_preissue_generation_abort"])
        self.assertEqual(
            sum(call[0] == "abort_runtime_generation" for call in store.calls), 1
        )

    def test_nonimplementing_no_grant_case_never_starts_schema_or_controller(self):
        store = FakeStore(self.target)
        store.case["state"] = "CONTROL_FAILURE"

        def forbidden_schema(_spec):
            raise AssertionError("schema checker must not run")

        dependencies = replace(
            self.dependencies(controller_should_run=False, startup_store=store),
            schema_checker=forbidden_schema,
            pending_acl_recovery=lambda *_args: (_ for _ in ()).throw(
                AssertionError("preissue recovery must not run")
            ),
        )
        with self.assertRaisesRegex(
            supervisor.SupervisorAuthorizationError,
            "requires canonical state IMPLEMENTING",
        ):
            supervisor.RuntimeSupervisor(
                self.spec, store=store, dependencies=dependencies
            ).run()
        self.assertEqual(store.calls, [])

    def test_failed_and_completed_startup_states_never_regenerate(self):
        failed_store = FakeStore(self.target, orphan_status="FAILED")
        failed = supervisor.RuntimeSupervisor(
            self.spec,
            store=failed_store,
            dependencies=self.dependencies(
                controller_should_run=False, startup_store=failed_store
            ),
        ).run()
        self.assertTrue(failed["recovered_terminal_failure"])
        self.assertFalse(failed["controller_started"])

        self.target.write_bytes(REPLACEMENT)
        completed_store = FakeStore(self.target, orphan_status="COMPLETED")
        completed = supervisor.RuntimeSupervisor(
            self.spec,
            store=completed_store,
            dependencies=self.dependencies(
                controller_should_run=False, startup_store=completed_store
            ),
        ).run()
        self.assertTrue(completed["recovered_completed"])
        self.assertFalse(completed["controller_started"])

    def test_schema_digest_is_recursive_exact_and_rejects_extra_file(self):
        schema = self.root / "schema"
        (schema / "v2").mkdir(parents=True)
        aggregate = json.dumps(
            {"tokens": [token.strip('"') for token in supervisor.REQUIRED_SCHEMA_TOKENS]}
        )
        (schema / "ClientRequest.json").write_text("{}", encoding="utf-8")
        (schema / "ServerRequest.json").write_text("{}", encoding="utf-8")
        (schema / "codex_app_server_protocol.v2.schemas.json").write_text(
            aggregate, encoding="utf-8"
        )
        (schema / "PermissionsRequestApprovalResponse.json").write_text(
            json.dumps(
                {
                    "type": "object",
                    "required": ["permissions"],
                    "properties": {
                        "permissions": {}, "scope": {}, "strictAutoReview": {},
                    },
                }
            ),
            encoding="utf-8",
        )
        for relative, properties in supervisor.CONTROLLER_SCHEMA_PROPERTIES.items():
            required = []
            if relative.endswith("TurnStartParams.json"):
                required = ["input", "threadId"]
            elif relative.endswith("ThreadReadParams.json"):
                required = ["threadId"]
            (schema / relative).write_text(
                json.dumps(
                    {
                        "type": "object",
                        "required": required,
                        "properties": {name: {} for name in properties},
                    }
                ),
                encoding="utf-8",
            )
        (schema / "v2" / "ThreadStartResponse.json").write_text(
            json.dumps(
                {
                    "type": "object",
                    "required": [
                        "approvalPolicy", "approvalsReviewer", "cwd", "model",
                        "modelProvider", "sandbox", "thread",
                    ],
                    "properties": {
                        name: {} for name in {
                            "approvalPolicy", "approvalsReviewer", "cwd", "model",
                            "modelProvider", "sandbox", "thread", "activePermissionProfile",
                            "instructionSources", "reasoningEffort", "runtimeWorkspaceRoots",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        digest, count, record_bytes = supervisor._schema_tree_digest(schema)
        evidence = supervisor.verify_generated_schema_directory(
            schema, expected_file_count=count, expected_tree_sha256=digest
        )
        self.assertEqual(evidence["file_count"], 9)
        self.assertEqual(evidence["record_bytes"], record_bytes)
        (schema / "v2" / "Unexpected.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(supervisor.SupervisorAuthorizationError):
            supervisor.verify_generated_schema_directory(
                schema, expected_file_count=count, expected_tree_sha256=digest
            )

    def test_controller_contract_has_no_preproposal_issue_fields(self):
        normalized = supervisor.normalize_supervisor_spec(self.spec)
        for name in (
            "isolation_evidence", "expires_at", "expected_case_revision", "issue_request_id",
        ):
            self.assertNotIn(name, normalized["controller_spec"])

    def test_preexisting_worker_auth_is_verified_unchanged_and_retained(self):
        auth_path = self.worker_home / "auth.json"
        auth_bytes = b'{"opaque":"acceptance-credential"}\n'
        auth_path.write_bytes(auth_bytes)
        before_hash = hashlib.sha256(auth_bytes).hexdigest()

        instance = supervisor.RuntimeSupervisor(
            self.spec, store=self.store, dependencies=self.dependencies()
        )
        result = instance.run()

        self.assertTrue(auth_path.is_file())
        self.assertEqual(auth_path.read_bytes(), auth_bytes)
        evidence = result["auth_artifact_evidence"]
        self.assertEqual(evidence["ownership"], "pre_existing_configured_worker_auth")
        self.assertEqual(evidence["sha256_before"], before_hash)
        self.assertEqual(evidence["sha256_after"], before_hash)
        self.assertTrue(evidence["unchanged"])
        self.assertTrue(evidence["retained"])
        self.assertTrue(evidence["preservation_verified"])
        self.assertFalse(evidence["content_logged"])

    def test_worker_auth_identity_or_content_change_is_rejected_without_deletion(self):
        auth_path = self.worker_home / "auth.json"
        auth_path.write_bytes(b'{"opaque":"original"}\n')
        snapshot = supervisor._capture_worker_auth(self.controller_spec)
        auth_path.write_bytes(b'{"opaque":"changed"}\n')

        with self.assertRaises(supervisor.SupervisorAuthorizationError):
            supervisor._cleanup_worker_auth(snapshot)
        self.assertTrue(auth_path.is_file())

    def test_absent_worker_auth_must_remain_absent(self):
        auth_path = self.worker_home / "auth.json"
        self.assertFalse(auth_path.exists())
        snapshot = supervisor._capture_worker_auth(self.controller_spec)
        auth_path.write_bytes(b'{"opaque":"unexpected"}\n')

        with self.assertRaisesRegex(
            supervisor.SupervisorAuthorizationError, "appeared"
        ):
            supervisor._cleanup_worker_auth(snapshot)
        self.assertTrue(auth_path.is_file())

    def test_cli_exposes_only_fixed_run_from_spec_file(self):
        parser = supervisor.build_parser()
        actions = parser._subparsers._group_actions[0].choices
        self.assertEqual(set(actions), {"run"})
        help_text = parser.format_help()
        self.assertNotIn("replacement-text", help_text)
        self.assertNotIn("command-json", help_text)
        self.assertNotIn("role", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
