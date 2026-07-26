#!/usr/bin/env python3
"""Focused tests for canonical runtime actors and the one-shot action broker."""

from __future__ import annotations

import base64
import ast
from contextlib import contextmanager, ExitStack
from concurrent.futures import ThreadPoolExecutor
import copy
import datetime as dt
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = ROOT / "scripts" / "agent"
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

engine = importlib.import_module("case_state")
broker = importlib.import_module("case_runtime_broker")

WORKER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1101"
BROKER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1102"
OFFLINE_WORKER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1103"
SANDBOX_GROUP_SID = "S-1-5-21-1111111111-2222222222-3333333333-1100"
REPOSITORY = "https://github.com/example/runtime-boundary"
BRANCH = "codex/runtime-boundary"
TARGET_PATH = "candidate.txt"
BASELINE_BYTES = b"baseline\n"
REPLACEMENT_BYTES = b"authorized replacement\n"
CONTROLLER_KEY = bytes(range(32))


def request_id() -> str:
    return str(uuid.uuid4())


class RuntimeFixture(unittest.TestCase):
    broker_sid = BROKER_SID

    def fixture_state_root(self) -> Path:
        return self.root / "state"

    def _live_transport_audit(self, phase: str) -> list[dict]:
        environment_body = {
            "environment_names": sorted(engine.LIVE_CONTROLLER_ENVIRONMENT_NAMES),
            "environment_values_sha256": "4" * 64,
            "mutable_paths_sha256": "a" * 64,
            "path_entries_sha256": "b" * 64,
            "controller_key_exposed": False,
            "secret_like_name_count": 0,
            "mutable_paths_within_worker_root": True,
        }
        environment = {
            **environment_body,
            "evidence_sha256": engine.canonical_json_sha256(environment_body),
        }
        process_instance_sha256 = engine.canonical_json_sha256(
            {"phase": phase, "worker_sid": WORKER_SID}
        )
        methods = (
            engine.LIVE_CONTROLLER_INITIAL_REQUIRED_METHODS
            if phase == "initial"
            else engine.LIVE_CONTROLLER_RESTART_REQUIRED_METHODS
        )
        return [
            {
                "event": "app_server_launch",
                "sandbox_launcher_command_sha256": "c" * 64,
                "app_server_command_sha256": "d" * 64,
                "environment_evidence": environment,
                "controller_key_exposed": False,
                "controller_principal_sid": self.broker_sid,
                "controller_principal_matches_broker": True,
                "mcp_override": "empty",
                "shell_environment_inherit": "none",
            },
            {
                "event": "restricted_app_server_identity",
                "process_instance_sha256": process_instance_sha256,
                "worker_principal_sid": WORKER_SID,
                "principal_distinct_from_broker": True,
                "app_server_sha256": engine.file_sha256(Path(sys.executable)),
                "app_server_version": "0.0.0-test",
                "command_line_sha256": "e" * 64,
                "argv_sha256": "f" * 64,
                "argv_matches_sealed_command": True,
                "worker_environment_acl_evidence": {
                    "record_count": 3,
                    "owner_sid": self.broker_sid,
                    "evidence_sha256": "1" * 64,
                },
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
                "process_instance_sha256": process_instance_sha256,
                "kill_on_job_close": True,
                "descendant_exit_verified": True,
            },
        ]

    def _live_controller_evidence(self) -> dict:
        capability = {
            "client_capabilities": {"experimentalApi": True},
            "mcp_server_count": 0,
            "hook_count": 0,
            "dynamic_tools": [],
        }
        incomplete_body = {
            "thread_id": self.incomplete_thread,
            "turn_id": "incomplete-turn-one",
            "completion_state": "INCOMPLETE",
            "proposal_count": 0,
            "action_count": 0,
            "result_sha256": "8" * 64,
        }
        authority_sha256 = engine.canonical_json_sha256(
            {"revision": self.revision, "target_sha256": hashlib.sha256(BASELINE_BYTES).hexdigest()}
        )
        raw = {
            "protocol_version": engine.LIVE_CONTROLLER_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 1,
            "case_id": self.case_id,
            "initial_run": {},
            "restart_run": {},
            "incomplete_child_evidence": {
                **incomplete_body,
                "evidence_sha256": engine.canonical_json_sha256(incomplete_body),
            },
            "stale_revision_denial": {
                "denial": "RevisionConflict",
                "attempted_revision": self.revision - 1,
                "current_revision": self.revision,
                "authority_state_sha256_before": authority_sha256,
                "authority_state_sha256_after": authority_sha256,
                "state_unchanged": True,
            },
            "all_mutation_surfaces_disabled": True,
            "both_process_trees_closed": True,
        }
        for field, phase in (("initial_run", "initial"), ("restart_run", "restart")):
            audit = self._live_transport_audit(phase)
            raw[field] = {
                "capability_evidence": capability,
                "capability_evidence_sha256": engine.canonical_json_sha256(capability),
                "transport_audit": audit,
                "transport_audit_sha256": engine.canonical_json_sha256(audit),
                "process_tree_closed": True,
                "server_request_count": 0,
                "server_request_policy_status": "UNEXERCISED",
            }
        return engine.normalize_live_controller_evidence(
            raw,
            worker_sid=WORKER_SID,
            broker_sid=self.broker_sid,
            app_server_sha256=engine.file_sha256(Path(sys.executable)),
            app_server_version="0.0.0-test",
            environment_sha256="4" * 64,
            expected_case_id=self.case_id,
        )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos-runtime-")
        self.worker_temp = tempfile.TemporaryDirectory(prefix="ccos-worker-runtime-")
        self.root = Path(self.temp.name).resolve(strict=True)
        self.worker_runtime_root = Path(self.worker_temp.name).resolve(strict=True)
        self.state_root = self.fixture_state_root()
        self.repository_root = self.root / "repository"
        self.repository_root.mkdir()
        subprocess.run(["git", "-C", str(self.repository_root), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repository_root), "checkout", "-q", "-b", BRANCH],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repository_root), "config", "user.email", "runtime@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repository_root), "config", "user.name", "Runtime Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repository_root), "remote", "add", "origin", REPOSITORY],
            check=True,
        )
        self.target = self.repository_root / TARGET_PATH
        self.target.write_bytes(BASELINE_BYTES)
        subprocess.run(["git", "-C", str(self.repository_root), "add", TARGET_PATH], check=True)
        subprocess.run(
            ["git", "-C", str(self.repository_root), "commit", "-q", "-m", "baseline"],
            check=True,
        )
        self.head = subprocess.run(
            ["git", "-C", str(self.repository_root), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        self.proposal_root = self.root / "proposal-root"
        self.proposal_root.mkdir()
        (self.proposal_root / "probe-descendant").mkdir()
        self.proposal = self.proposal_root / "proposal.bin"
        self.proposal.write_bytes(REPLACEMENT_BYTES)
        self.store = engine.CaseStore(self.state_root)
        (self.repository_root / "probe-descendant").mkdir()
        (self.state_root / "probe-descendant").mkdir(parents=True)
        self.case_id = str(uuid.uuid4())
        self.parent_thread = f"parent-{self.case_id}"
        self.implementation_thread = f"implementation-{self.case_id}"
        self.reviewer_thread = f"reviewer-{self.case_id}"
        self.incomplete_thread = f"incomplete-{self.case_id}"
        self.store.register_case(
            self.case_id,
            objective="prove one exact brokered action boundary",
            request_id=request_id(),
            expected_store_revision=0,
        )
        for kind, value, repository in (
            ("repo_url", REPOSITORY, None),
            ("branch", BRANCH, REPOSITORY),
            ("worktree", str(self.repository_root), None),
            ("thread", self.parent_thread, None),
            ("thread", self.implementation_thread, None),
            ("thread", self.reviewer_thread, None),
            ("thread", self.incomplete_thread, None),
        ):
            self.store.bind(
                self.case_id,
                kind=kind,
                value=value,
                repository=repository,
                request_id=request_id(),
                expected_revision=self.revision,
            )
        self.bind_actor(self.parent_thread, "parent", None)
        self.bind_actor(self.implementation_thread, "implementer_child", self.parent_thread)
        self.bind_actor(self.reviewer_thread, "review_child", self.parent_thread)
        self.bind_actor(self.incomplete_thread, "incomplete_child", self.parent_thread)
        self.store.start_implementation(
            self.case_id,
            request_id=request_id(),
            expected_revision=self.revision,
        )
        self.grant_id = "grant-one"
        self.actor_turn_id = "implementation-turn-one"
        self.store.claim_runtime_generation_attempt(
            self.case_id,
            attempt={
                "protocol_version": engine.RUNTIME_GENERATION_ATTEMPT_PROTOCOL_VERSION,
                "schema_version": 1,
                "attempt_id": "controller-grant-one",
                "grant_id": self.grant_id,
                "controller_spec_sha256": "9" * 64,
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )
        self.receipt_body = {
            "protocol_version": broker.CONTROLLER_RECEIPT_PROTOCOL_VERSION,
            "schema_version": 2,
            "case_id": self.case_id,
            "grant_id": self.grant_id,
            "actor_thread_id": self.implementation_thread,
            "actor_turn_id": self.actor_turn_id,
            "action": "implementation",
            "operation_id": "replace-candidate-once",
            "base_head": self.head,
            "target_path": TARGET_PATH,
            "proposal_sha256": hashlib.sha256(REPLACEMENT_BYTES).hexdigest(),
            "proposal_size": len(REPLACEMENT_BYTES),
            "completion_state": "COMPLETED",
            "native_turn_evidence_sha256": "5" * 64,
            "repository": REPOSITORY,
            "branch": BRANCH,
            "worktree": engine.normalize_binding("worktree", str(self.repository_root)),
            "baseline_sha256": hashlib.sha256(BASELINE_BYTES).hexdigest(),
            "worker_runtime_root": engine.normalize_binding(
                "worktree", str(self.worker_runtime_root)
            ),
            "worker_online_principal_sid": WORKER_SID,
            "worker_offline_principal_sid": OFFLINE_WORKER_SID,
            "sandbox_group_principal_sid": SANDBOX_GROUP_SID,
            "broker_principal_sid": self.broker_sid,
            "app_server_sha256": engine.file_sha256(Path(sys.executable)),
            "app_server_executable_path": engine.normalize_binding(
                "worktree", str(Path(sys.executable).resolve())
            ),
            "app_server_version": "0.0.0-test",
            "schema_file_count": 347,
            "schema_tree_sha256": "2" * 64,
            "sandbox_profile_sha256": "3" * 64,
            "app_server_environment_sha256": "4" * 64,
            "issued_at": engine.utc_now(),
            "nonce": "controller-turn-nonce-one",
        }
        self.live_controller_evidence = self._live_controller_evidence()
        self.live_controller_evidence_sha256 = engine.canonical_json_sha256(
            self.live_controller_evidence
        )
        self.receipt_body["live_controller_evidence_sha256"] = (
            self.live_controller_evidence_sha256
        )
        self.receipt = broker.seal_controller_receipt(self.receipt_body, CONTROLLER_KEY)
        self.source_pins = {
            "manifest_path": "install-bundle.manifest.json",
            "manifest_sha256": engine.file_sha256(ROOT / "install-bundle.manifest.json"),
            "files": [
                {
                    "path": "scripts/agent/case_state.py",
                    "sha256": engine.file_sha256(ROOT / "scripts" / "agent" / "case_state.py"),
                    "size": (ROOT / "scripts" / "agent" / "case_state.py").stat().st_size,
                },
                {
                    "path": "scripts/agent/case_runtime_broker.py",
                    "sha256": engine.file_sha256(ROOT / "scripts" / "agent" / "case_runtime_broker.py"),
                    "size": (ROOT / "scripts" / "agent" / "case_runtime_broker.py").stat().st_size,
                },
            ],
        }
        self.environment = mock.patch.dict(
            os.environ,
            {broker.CONTROLLER_KEY_ENVIRONMENT: base64.b64encode(CONTROLLER_KEY).decode("ascii")},
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()
        self.worker_temp.cleanup()

    @property
    def revision(self) -> int:
        return self.store.get_case(self.case_id)["revision"]

    def bind_actor(self, thread_id: str, role: str, parent_thread_id: str | None) -> None:
        self.store.bind_runtime_actor(
            self.case_id,
            actor={
                "protocol_version": engine.RUNTIME_ACTOR_PROTOCOL_VERSION,
                "schema_version": 1,
                "thread_id": thread_id,
                "controller_assigned_role": role,
                "parent_thread_id": parent_thread_id,
                "agent_path": f"/root/{role}/{thread_id}",
                "identity_evidence_sha256": hashlib.sha256(thread_id.encode()).hexdigest(),
                "binding_source": "native_thread_read",
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )

    def protected_root_requests(self) -> list[dict]:
        state_path = engine.normalize_binding("worktree", str(self.state_root))
        source_path = engine.normalize_binding("worktree", str(ROOT))
        target_path = engine.normalize_binding("worktree", str(self.repository_root))
        proposal_path = engine.normalize_binding("worktree", str(self.proposal_root))
        return [
            {
                "root_kind": "target_root",
                "path": target_path,
                "owner_sid": self.broker_sid,
                "parent_path": engine.normalize_binding("worktree", str(self.repository_root.parent)),
                "parent_owner_sid": self.broker_sid,
                "anchor_path": TARGET_PATH,
                "anchor_sha256": hashlib.sha256(BASELINE_BYTES).hexdigest(),
                "nested_probe_parent_path": "probe-descendant",
            },
            {
                "root_kind": "state_root",
                "path": state_path,
                "owner_sid": self.broker_sid,
                "parent_path": engine.normalize_binding("worktree", str(self.state_root.parent)),
                "parent_owner_sid": self.broker_sid,
                "anchor_path": engine.STORE_FILENAME,
                "anchor_sha256": engine.file_sha256(self.store.path),
                "nested_probe_parent_path": "probe-descendant",
            },
            {
                "root_kind": "broker_source_root",
                "path": source_path,
                "owner_sid": self.broker_sid,
                "parent_path": engine.normalize_binding("worktree", str(ROOT.parent)),
                "parent_owner_sid": self.broker_sid,
                "anchor_path": "install-bundle.manifest.json",
                "anchor_sha256": self.source_pins["manifest_sha256"],
                "nested_probe_parent_path": "scripts/agent",
            },
            {
                "root_kind": "proposal_root",
                "path": proposal_path,
                "owner_sid": self.broker_sid,
                "parent_path": engine.normalize_binding("worktree", str(self.proposal_root.parent)),
                "parent_owner_sid": self.broker_sid,
                "anchor_path": self.proposal.name,
                "anchor_sha256": hashlib.sha256(REPLACEMENT_BYTES).hexdigest(),
                "nested_probe_parent_path": "probe-descendant",
            },
        ]

    def principal_probe(self, worker_sid: str, worker_name: str) -> dict:
        challenge = "worker-isolation-challenge-one"
        roots = []
        for item in self.protected_root_requests():
            kind = item["root_kind"]
            canonical_anchor = engine.normalize_binding(
                "worktree",
                str(
                    Path(item["path"])
                    .joinpath(*PurePosixPath(item["anchor_path"]).parts)
                    .resolve(strict=True)
                ),
            )
            probe = (
                f".ccos-worker-{kind.replace('_root', '')}-probe-"
                + hashlib.sha256(challenge.encode()).hexdigest()[:20]
            )
            nested_probe = (
                item["nested_probe_parent_path"]
                + "/.ccos-worker-nested-probe-"
                + hashlib.sha256((challenge + ":" + kind).encode()).hexdigest()[:20]
            )
            acl_nonce = hashlib.sha256(
                (challenge + ":" + kind + ":acl").encode()
            ).hexdigest()
            hard_link = (
                f".ccos-worker-{kind.replace('_root', '')}-hard-link-"
                + hashlib.sha256((challenge + ":" + kind + ":link").encode()).hexdigest()[:20]
            )
            roots.append(
                {
                    "root_kind": kind,
                    "path": item["path"],
                    "owner_sid": self.broker_sid,
                    "parent_path": item["parent_path"],
                    "parent_owner_sid": self.broker_sid,
                    "anchor_path": item["anchor_path"],
                    "anchor_sha256_before": item["anchor_sha256"],
                    "anchor_sha256_after": item["anchor_sha256"],
                    "probe_relative_path": probe,
                    "write_denial_error": "ACCESS_DENIED",
                    "write_denial_native_code": 5,
                    "probe_absent_before": True,
                    "probe_absent_after": True,
                    "nested_probe_parent_path": item["nested_probe_parent_path"],
                    "nested_probe_relative_path": nested_probe,
                    "nested_write_denial_error": "ACCESS_DENIED",
                    "nested_write_denial_native_code": 5,
                    "nested_probe_absent_before": True,
                    "nested_probe_absent_after": True,
                    "overwrite_denial_error": "ACCESS_DENIED",
                    "overwrite_denial_native_code": 5,
                    "replace_capability_denial_error": "ACCESS_DENIED",
                    "replace_capability_denial_native_code": 5,
                    "rename_capability_denial_error": "ACCESS_DENIED",
                    "rename_capability_denial_native_code": 5,
                    "hard_link_relative_path": hard_link,
                    "hard_link_denial_error": "ACCESS_DENIED",
                    "hard_link_denial_native_code": 5,
                    "hard_link_absent_before": True,
                    "hard_link_absent_after": True,
                    "anchor_identity_sha256_before": "7" * 64,
                    "anchor_identity_sha256_after": "7" * 64,
                    "anchor_hardlink_paths_before": [canonical_anchor],
                    "anchor_hardlink_paths_after": [canonical_anchor],
                    "anchor_transport_hardlink_paths_before": [],
                    "anchor_transport_hardlink_paths_after": [],
                    "acl_change_nonce": acl_nonce,
                    "acl_sddl_sha256_before": "6" * 64,
                    "acl_sddl_sha256_after": "6" * 64,
                    "change_permissions_denial_error": "ACCESS_DENIED",
                    "change_permissions_denial_native_code": 5,
                    "take_ownership_denial_error": "ACCESS_DENIED",
                    "take_ownership_denial_native_code": 5,
                    "delete_capability_denial_error": "ACCESS_DENIED",
                    "delete_capability_denial_native_code": 5,
                }
            )
        return {
            "protocol_version": engine.WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION,
            "schema_version": 1,
            "challenge_id": challenge,
            "worker_principal_sid": worker_sid,
            "worker_identity_name": worker_name,
            "worker_group_sids": [SANDBOX_GROUP_SID],
            "protected_roots": roots,
            "head_before": self.head,
            "head_after": self.head,
            "status_sha256_before": engine.EMPTY_SHA256,
            "status_sha256_after": engine.EMPTY_SHA256,
            "observed_at": engine.utc_now(),
        }

    def membership_evidence(self) -> dict:
        return {
            "protocol_version": engine.WINDOWS_GROUP_MEMBERSHIP_PROTOCOL_VERSION,
            "schema_version": 1,
            "sandbox_group_sid": SANDBOX_GROUP_SID,
            "members": [
                {
                    "principal_role": "app_server_host",
                    "principal_sid": WORKER_SID,
                    "group_sids": [SANDBOX_GROUP_SID],
                },
                {
                    "principal_role": "model_sandbox",
                    "principal_sid": OFFLINE_WORKER_SID,
                    "group_sids": [SANDBOX_GROUP_SID],
                },
            ],
            "observed_at": engine.utc_now(),
        }

    def isolation_evidence(self, membership: dict | None = None) -> dict:
        denied = [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID]
        membership = membership or self.membership_evidence()
        membership_sha256 = engine.canonical_json_sha256(membership)
        probes = [
            {
                "principal_role": "app_server_host",
                "principal_sid": WORKER_SID,
                "probe": self.principal_probe(WORKER_SID, "fixture\\online"),
            },
            {
                "principal_role": "model_sandbox",
                "principal_sid": OFFLINE_WORKER_SID,
                "probe": self.principal_probe(OFFLINE_WORKER_SID, "fixture\\offline"),
            },
        ]
        combined = {
            "denied_principal_sids": denied,
            "membership_evidence_sha256": membership_sha256,
            "principal_probes": probes,
        }
        return {
            "protocol_version": engine.WINDOWS_ISOLATION_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 2,
            **combined,
            "combined_probe_sha256": engine.canonical_json_sha256(combined),
        }

    def protected_acl_snapshot(self) -> list[dict]:
        paths = {
            value
            for item in self.protected_root_requests()
            for value in (item["path"], item["parent_path"])
        }
        paths.update(
            engine.normalize_binding("worktree", str(path))
            for path in (
                self.target,
                self.store.path,
                ROOT / "install-bundle.manifest.json",
                self.proposal,
            )
        )
        entries = []
        for index, path in enumerate(
            sorted(
                paths,
                key=lambda item: (len(Path(item).parts), item.casefold()),
                reverse=True,
            )
        ):
            sddl = f"O:SYG:SYD:(A;;FA;;;SY)-fixture-{index}"
            entry = {
                "path": path,
                "owner_sid": "S-1-5-18",
                "sddl": sddl,
                "sddl_sha256": hashlib.sha256(sddl.encode()).hexdigest(),
            }
            entry["entry_sha256"] = engine.canonical_json_sha256(entry)
            entries.append(entry)
        return entries

    def acl_snapshot_entry(self, path: Path, label: str) -> dict:
        sddl = f"O:SYG:SYD:(A;;FA;;;SY)-{label}"
        entry = {
            "path": engine.normalize_binding("worktree", str(path)),
            "owner_sid": "S-1-5-18",
            "sddl": sddl,
            "sddl_sha256": hashlib.sha256(sddl.encode()).hexdigest(),
        }
        entry["entry_sha256"] = engine.canonical_json_sha256(entry)
        return entry

    def grant_request(self, **overrides) -> dict:
        membership = copy.deepcopy(
            overrides.pop("group_membership_evidence", self.membership_evidence())
        )
        grant = {
            "protocol_version": engine.ACTION_GRANT_PROTOCOL_VERSION,
            "schema_version": 1,
            "grant_id": self.grant_id,
            "actor_thread_id": self.implementation_thread,
            "actor_turn_id": self.actor_turn_id,
            "native_turn_evidence_sha256": "5" * 64,
            "controller_receipt_sha256": engine.canonical_json_sha256(self.receipt),
            "operation_id": "replace-candidate-once",
            "action": "implementation",
            "operation": "replace_existing_file_v1",
            "repository": REPOSITORY,
            "branch": BRANCH,
            "worktree": str(self.repository_root),
            "base_head": self.head,
            "target_path": TARGET_PATH,
            "baseline_sha256": hashlib.sha256(BASELINE_BYTES).hexdigest(),
            "replacement_sha256": hashlib.sha256(REPLACEMENT_BYTES).hexdigest(),
            "proposal_artifact_path": str(self.proposal),
            "proposal_size": len(REPLACEMENT_BYTES),
            "worker_runtime_root": str(self.worker_runtime_root),
            "worker_principal_sid": WORKER_SID,
            "model_worker_principal_sid": OFFLINE_WORKER_SID,
            "sandbox_group_principal_sid": SANDBOX_GROUP_SID,
            "denied_principal_sids": [
                WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID
            ],
            "broker_principal_sid": self.broker_sid,
            "app_server_sha256": engine.file_sha256(Path(sys.executable)),
            "app_server_executable_path": engine.normalize_binding(
                "worktree", str(Path(sys.executable).resolve())
            ),
            "app_server_version": "0.0.0-test",
            "schema_file_count": 347,
            "schema_tree_sha256": "2" * 64,
            "sandbox_profile_sha256": "3" * 64,
            "app_server_environment_sha256": "4" * 64,
            "live_controller_evidence": copy.deepcopy(
                self.live_controller_evidence
            ),
            "live_controller_evidence_sha256": self.live_controller_evidence_sha256,
            "group_membership_evidence": membership,
            "isolation_evidence": self.isolation_evidence(membership),
            "expires_at": (
                dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(minutes=5)
            ).isoformat(),
        }
        snapshot = self.protected_acl_snapshot()
        grant["protected_acl_snapshot"] = snapshot
        grant["protected_acl_snapshot_sha256"] = engine.canonical_json_sha256(snapshot)
        dacl_context = {
            **grant,
            "worktree": engine.normalize_binding("worktree", str(self.repository_root)),
            "state_root": engine.normalize_binding("worktree", str(self.state_root)),
            "broker_source_root": engine.normalize_binding("worktree", str(ROOT)),
            "proposal_root": engine.normalize_binding("worktree", str(self.proposal_root)),
            "group_membership_evidence_sha256": engine.canonical_json_sha256(membership),
        }
        preissue_dacl = engine.CaseStore._normalize_dacl_evidence(
            self.dacl_evidence(dacl_context), dacl_context
        )
        grant["preissue_dacl_evidence"] = preissue_dacl
        grant["preissue_dacl_evidence_sha256"] = engine.canonical_json_sha256(
            preissue_dacl
        )
        grant.update(overrides)
        return grant

    def issue(self, grant: dict | None = None) -> dict:
        request = grant or self.grant_request()
        with mock.patch.object(engine, "controller_source_pins", return_value=self.source_pins):
            result = self.store.issue_action_grant(
                self.case_id,
                grant=request,
                request_id=request_id(),
                expected_revision=self.revision,
                allow_legacy_v1=True,
            )
        canonical = self.grant()
        journal = broker.BrokerJournal(self.state_root, self.case_id, self.grant_id)
        run_id = "fixture-preissue"
        journal.append(
            "ACL_SNAPSHOT",
            run_id,
            protected_acl_snapshot=canonical["protected_acl_snapshot"],
            protected_acl_snapshot_sha256=canonical[
                "protected_acl_snapshot_sha256"
            ],
        )
        roots = {
            "target_root": canonical["worktree"],
            "state_root": canonical["state_root"],
            "broker_source_root": canonical["broker_source_root"],
            "proposal_root": canonical["proposal_root"],
        }
        intent = {
            "roots": roots,
            "denied_principal_sids": canonical["denied_principal_sids"],
            "broker_principal_sid": canonical["broker_principal_sid"],
        }
        journal.append(
            "ACL_LOCKDOWN_INTENT",
            run_id,
            protected_acl_snapshot_sha256=canonical[
                "protected_acl_snapshot_sha256"
            ],
            lockdown_intent=intent,
            lockdown_intent_sha256=engine.canonical_json_sha256(intent),
        )
        journal.append(
            "ACL_LOCKDOWN_VERIFIED",
            run_id,
            protected_acl_snapshot_sha256=canonical[
                "protected_acl_snapshot_sha256"
            ],
            preissue_dacl_evidence=canonical["preissue_dacl_evidence"],
            preissue_dacl_evidence_sha256=canonical[
                "preissue_dacl_evidence_sha256"
            ],
        )
        return result

    def grant(self) -> dict:
        return self.store.get_case(self.case_id)["runtime"]["action_grants"][self.grant_id]

    def dacl_evidence(self, grant: dict | None = None) -> dict:
        grant = grant or self.grant()
        rules = []
        for kind in engine.PROTECTED_ROOT_KINDS:
            path = {
                "target_root": grant["worktree"],
                "state_root": grant["state_root"],
                "broker_source_root": grant["broker_source_root"],
                "proposal_root": grant["proposal_root"],
            }[kind]
            for principal_sid in grant["denied_principal_sids"]:
                rules.append(
                    {
                        "root_kind": kind,
                        "principal_sid": principal_sid,
                        "deny_source_sids": sorted(
                            {principal_sid, grant["sandbox_group_principal_sid"]}
                        ),
                        "path": path,
                        "owner_sid": self.broker_sid,
                        "parent_path": engine.normalize_binding("worktree", str(Path(path).parent)),
                        "parent_owner_sid": self.broker_sid,
                        "root_sddl_sha256": "a" * 64,
                        "parent_sddl_sha256": "b" * 64,
                        "access_type": "DENY",
                        "is_inherited": False,
                        "inheritance_flags": ["CONTAINER_INHERIT", "OBJECT_INHERIT"],
                        "propagation_flags": ["NONE"],
                        "rights_mask": engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
                        "inheritable_rights_mask": engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
                        "parent_rights_mask": engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
                    }
                )
        return {
            "protocol_version": engine.WINDOWS_DACL_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 2,
            "denied_principal_sids": grant["denied_principal_sids"],
            "membership_evidence_sha256": grant["group_membership_evidence_sha256"],
            "broker_principal_sid": self.broker_sid,
            "rules": rules,
            "observed_at": engine.utc_now(),
        }

    def trusted_probe(self, grant: dict | None = None) -> dict:
        grant = grant or self.grant()
        isolation = {
            item["root_kind"]: item
            for item in grant["isolation_evidence"]["principal_probes"][0]["probe"]["protected_roots"]
        }
        anchors = {
            "target_root": (TARGET_PATH, hashlib.sha256(BASELINE_BYTES).hexdigest()),
            "state_root": (engine.STORE_FILENAME, engine.file_sha256(self.store.path)),
            "broker_source_root": (
                isolation["broker_source_root"]["anchor_path"],
                isolation["broker_source_root"]["anchor_sha256_after"],
            ),
            "proposal_root": (
                isolation["proposal_root"]["anchor_path"],
                isolation["proposal_root"]["anchor_sha256_after"],
            ),
        }
        paths = {
            "target_root": grant["worktree"],
            "state_root": grant["state_root"],
            "broker_source_root": grant["broker_source_root"],
            "proposal_root": grant["proposal_root"],
        }
        suffix = hashlib.sha256(grant["authorization_nonce"].encode()).hexdigest()[:20]
        roots = []
        for kind in engine.PROTECTED_ROOT_KINDS:
            anchor_path, anchor_sha = anchors[kind]
            roots.append(
                {
                    "root_kind": kind,
                    "path": paths[kind],
                    "anchor_path": anchor_path,
                    "anchor_sha256_before": anchor_sha,
                    "anchor_sha256_after": anchor_sha,
                    "probe_relative_path": f".ccos-broker-{kind.replace('_root', '')}-probe-{suffix}",
                    "probe_content_sha256": "4" * 64,
                    "probe_absent_after": True,
                }
            )
        return {
            "protocol_version": engine.TRUSTED_WRITE_PROBE_PROTOCOL_VERSION,
            "schema_version": 1,
            "broker_principal_sid": self.broker_sid,
            "broker_identity_name": "fixture\\broker",
            "protected_roots": roots,
            "head_before": self.head,
            "head_after": self.head,
            "status_sha256_before": engine.EMPTY_SHA256,
            "status_sha256_after": engine.EMPTY_SHA256,
            "observed_at": engine.utc_now(),
        }

    def claim(self, *, expected_revision: int | None = None) -> dict:
        grant = self.grant()
        return self.store.claim_action_grant(
            self.case_id,
            claim={
                "protocol_version": engine.ACTION_GRANT_CLAIM_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": self.grant_id,
                "controller_receipt_sha256": grant["controller_receipt_sha256"],
                "broker_principal_sid": self.broker_sid,
                "dacl_evidence": self.dacl_evidence(grant),
                "trusted_write_probe": self.trusted_probe(grant),
            },
            request_id=request_id(),
            expected_revision=self.revision if expected_revision is None else expected_revision,
        )

    def broker_patches(self):
        def post_evidence(_store, grant, *, run_id):
            body = {
                "protocol_version": broker.POST_REPLACEMENT_EVIDENCE_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": grant["grant_id"],
                "run_id": run_id,
                "dacl_evidence": self.dacl_evidence(grant),
            }
            return {
                **body,
                "post_replacement_evidence_sha256": engine.canonical_json_sha256(body),
            }

        def restore(journal, *, run_id, snapshot, snapshot_sha256,
                    lockdown_dacl_evidence, restore_reason):
            event = journal.append(
                "ACL_RESTORED",
                run_id,
                protected_acl_snapshot_sha256=snapshot_sha256,
                restore_reason=restore_reason,
                already_original=False,
            )
            return {
                "restored": True,
                "already_restored": False,
                "protected_acl_snapshot_sha256": snapshot_sha256,
                "journal_event_sha256": event["event_sha256"],
            }

        return (
            mock.patch.object(
                broker,
                "windows_identity",
                return_value=("fixture\\broker", self.broker_sid),
            ),
            mock.patch.object(broker, "_verify_source_pins", return_value=None),
            mock.patch.object(
                broker,
                "inspect_protected_dacls",
                side_effect=lambda _roots, _denied, _broker, _membership: self.dacl_evidence(),
            ),
            mock.patch.object(
                broker,
                "_trusted_write_probe",
                side_effect=lambda _grant, _name, _sid, _path: self.trusted_probe(_grant),
            ),
            mock.patch.object(
                broker,
                "_collect_post_replacement_isolation_evidence",
                side_effect=post_evidence,
            ),
            mock.patch.object(
                broker,
                "_restore_acl_snapshot_after_lockdown",
                side_effect=restore,
            ),
            mock.patch.object(
                broker, "_verify_protected_acl_restore", return_value=None
            ),
        )

    @contextmanager
    def patched_broker(self):
        with ExitStack() as stack:
            for patch in self.broker_patches():
                stack.enter_context(patch)
            yield


class RuntimeActorAndGrantTests(RuntimeFixture):
    def test_preissue_generation_abort_is_terminal_revision_bound_and_idempotent(self) -> None:
        before = self.store.get_case(self.case_id)
        limits = copy.deepcopy(before["limits"])
        evidence = {
            "protocol_version": engine.RUNTIME_GENERATION_ABORT_PROTOCOL_VERSION,
            "schema_version": 1,
            "grant_id": self.grant_id,
            "snapshot_event_sha256": "a" * 64,
            "lockdown_intent_event_sha256": "b" * 64,
            "acl_restored_event_sha256": "c" * 64,
            "reason_code": "PREISSUE_GENERATION_ABANDONED",
        }
        req = request_id()
        with self.assertRaises(engine.RevisionConflict):
            self.store.abort_runtime_generation(
                self.case_id,
                evidence=evidence,
                request_id=req,
                expected_revision=self.revision - 1,
            )
        self.assertEqual(self.store.get_case(self.case_id)["state"], "IMPLEMENTING")
        result = self.store.abort_runtime_generation(
            self.case_id,
            evidence=evidence,
            request_id=req,
            expected_revision=self.revision,
        )
        self.assertEqual(result["state"], "CASE_LOCKED")
        locked = self.store.get_case(self.case_id)
        self.assertEqual(locked["limits"], limits)
        self.assertEqual(locked["lock_reason"], "PREISSUE_GENERATION_ABANDONED")
        replay = self.store.abort_runtime_generation(
            self.case_id,
            evidence=evidence,
            request_id=req,
            expected_revision=before["revision"],
        )
        self.assertTrue(replay["idempotent"])
        with self.assertRaises(engine.LimitError):
            self.store.start_implementation(
                self.case_id,
                request_id=request_id(),
                expected_revision=locked["revision"],
            )

    def test_action_check_uses_controller_bound_native_role_when_actor_thread_is_supplied(self) -> None:
        parent_spoof = self.store.check_action(
            self.case_id,
            "implementation",
            actor_role="implementer_child",
            actor_thread_id=self.parent_thread,
            repository=REPOSITORY,
            branch=BRANCH,
            thread=self.parent_thread,
        )
        self.assertFalse(parent_spoof["allowed"])
        self.assertEqual(parent_spoof["reason_codes"], ["ACTOR_ROLE_MISMATCH"])
        self.assertEqual(parent_spoof["controller_bound_actor_role"], "parent")
        unknown = self.store.check_action(
            self.case_id,
            "implementation",
            actor_role="implementer_child",
            actor_thread_id="unknown-native-thread",
            repository=REPOSITORY,
            branch=BRANCH,
            thread=self.parent_thread,
        )
        self.assertFalse(unknown["allowed"])
        self.assertEqual(unknown["reason_codes"], ["RUNTIME_ACTOR_UNBOUND"])
        reviewer = self.store.check_action(
            self.case_id,
            "implementation",
            actor_role="review_child",
            actor_thread_id=self.reviewer_thread,
            repository=REPOSITORY,
            branch=BRANCH,
            thread=self.reviewer_thread,
        )
        self.assertFalse(reviewer["allowed"])
        self.assertEqual(reviewer["reason_codes"], ["ROLE_ACTION_DENIED"])
        mismatched_context = self.store.check_action(
            self.case_id,
            "implementation",
            actor_role="implementer_child",
            actor_thread_id=self.implementation_thread,
            repository=REPOSITORY,
            branch=BRANCH,
            thread=self.reviewer_thread,
        )
        self.assertFalse(mismatched_context["allowed"])
        self.assertEqual(
            mismatched_context["reason_codes"], ["ACTOR_THREAD_CONTEXT_MISMATCH"]
        )
        implementer = self.store.check_action(
            self.case_id,
            "implementation",
            actor_role="implementer_child",
            actor_thread_id=self.implementation_thread,
            repository=REPOSITORY,
            branch=BRANCH,
            thread=self.implementation_thread,
        )
        self.assertTrue(implementer["allowed"])

    def test_parent_reviewer_incomplete_unknown_and_forged_role_cannot_get_grant(self) -> None:
        for thread_id in (
            self.parent_thread,
            self.reviewer_thread,
            self.incomplete_thread,
            "unknown-thread",
        ):
            with self.subTest(thread_id=thread_id):
                with self.assertRaises(engine.AuthorizationError):
                    self.issue(self.grant_request(actor_thread_id=thread_id))
        forged = self.grant_request()
        forged["actor_role"] = "implementer_child"
        with self.assertRaisesRegex(engine.ValidationError, "fixed"):
            self.issue(forged)
        self.assertEqual(self.store.get_case(self.case_id)["runtime"]["action_grants"], {})

    def test_exact_grant_claims_once_and_completion_verifies_one_path(self) -> None:
        issued = self.issue()
        self.assertEqual(issued["status"], "ISSUED")
        claimed = self.claim()
        self.assertEqual(claimed["status"], "CLAIMED")
        self.target.write_bytes(REPLACEMENT_BYTES)
        completed = self.store.complete_action_grant(
            self.case_id,
            completion={
                "protocol_version": engine.ACTION_GRANT_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": self.grant_id,
                "controller_receipt_sha256": self.grant()["controller_receipt_sha256"],
                "broker_principal_sid": BROKER_SID,
                "post_replacement_evidence_sha256": "d" * 64,
                "completed_at": engine.utc_now(),
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )
        self.assertEqual(completed["changed_paths"], [TARGET_PATH])
        self.assertEqual(self.grant()["status"], "COMPLETED")
        with self.assertRaises(engine.LimitError):
            self.store.complete_action_grant(
                self.case_id,
                completion={
                    "protocol_version": engine.ACTION_GRANT_RESULT_PROTOCOL_VERSION,
                    "schema_version": 1,
                    "grant_id": self.grant_id,
                    "controller_receipt_sha256": self.grant()["controller_receipt_sha256"],
                    "broker_principal_sid": BROKER_SID,
                    "post_replacement_evidence_sha256": "d" * 64,
                    "completed_at": engine.utc_now(),
                },
                request_id=request_id(),
                expected_revision=self.revision,
            )

    def test_claim_rejects_missing_write_owner_and_parent_delete_child_denial(self) -> None:
        self.issue()
        dacl = self.dacl_evidence()
        dacl["rules"][0]["rights_mask"] &= ~524288
        with self.assertRaisesRegex(engine.AuthorizationError, "ownership protection"):
            self.store.claim_action_grant(
                self.case_id,
                claim={
                    "protocol_version": engine.ACTION_GRANT_CLAIM_PROTOCOL_VERSION,
                    "schema_version": 1,
                    "grant_id": self.grant_id,
                    "controller_receipt_sha256": self.grant()["controller_receipt_sha256"],
                    "broker_principal_sid": BROKER_SID,
                    "dacl_evidence": dacl,
                    "trusted_write_probe": self.trusted_probe(),
                },
                request_id=request_id(),
                expected_revision=self.revision,
            )
        dacl = self.dacl_evidence()
        dacl["rules"][0]["parent_rights_mask"] &= ~64
        with self.assertRaisesRegex(engine.AuthorizationError, "parent"):
            self.store.claim_action_grant(
                self.case_id,
                claim={
                    "protocol_version": engine.ACTION_GRANT_CLAIM_PROTOCOL_VERSION,
                    "schema_version": 1,
                    "grant_id": self.grant_id,
                    "controller_receipt_sha256": self.grant()["controller_receipt_sha256"],
                    "broker_principal_sid": BROKER_SID,
                    "dacl_evidence": dacl,
                    "trusted_write_probe": self.trusted_probe(),
                },
                request_id=request_id(),
                expected_revision=self.revision,
            )
        dacl = self.dacl_evidence()
        dacl["rules"][0]["inheritable_rights_mask"] = 2
        with self.assertRaisesRegex(engine.AuthorizationError, "recursive DENY"):
            self.store.claim_action_grant(
                self.case_id,
                claim={
                    "protocol_version": engine.ACTION_GRANT_CLAIM_PROTOCOL_VERSION,
                    "schema_version": 1,
                    "grant_id": self.grant_id,
                    "controller_receipt_sha256": self.grant()["controller_receipt_sha256"],
                    "broker_principal_sid": BROKER_SID,
                    "dacl_evidence": dacl,
                    "trusted_write_probe": self.trusted_probe(),
                },
                request_id=request_id(),
                expected_revision=self.revision,
            )
        dacl = self.dacl_evidence()
        dacl["rules"][0]["propagation_flags"] = ["INHERIT_ONLY"]
        with self.assertRaisesRegex(engine.AuthorizationError, "NoPropagate"):
            self.store.claim_action_grant(
                self.case_id,
                claim={
                    "protocol_version": engine.ACTION_GRANT_CLAIM_PROTOCOL_VERSION,
                    "schema_version": 1,
                    "grant_id": self.grant_id,
                    "controller_receipt_sha256": self.grant()["controller_receipt_sha256"],
                    "broker_principal_sid": BROKER_SID,
                    "dacl_evidence": dacl,
                    "trusted_write_probe": self.trusted_probe(),
                },
                request_id=request_id(),
                expected_revision=self.revision,
            )

    def test_worker_evidence_requires_anchor_overwrite_nested_create_and_unchanged_acl(self) -> None:
        membership = self.membership_evidence()
        evidence = self.isolation_evidence(membership)
        roots = evidence["principal_probes"][0]["probe"]["protected_roots"]
        roots[0]["overwrite_denial_error"] = "OVERWRITE_CAPABILITY_GRANTED"
        roots[0]["overwrite_denial_native_code"] = 0
        with self.assertRaisesRegex(engine.AuthorizationError, "overwrite"):
            self.issue(self.grant_request(
                group_membership_evidence=membership, isolation_evidence=evidence
            ))
        membership = self.membership_evidence()
        evidence = self.isolation_evidence(membership)
        roots = evidence["principal_probes"][0]["probe"]["protected_roots"]
        roots[0]["nested_write_denial_error"] = "WRITE_SUCCEEDED"
        roots[0]["nested_write_denial_native_code"] = 0
        with self.assertRaisesRegex(engine.AuthorizationError, "nested-create"):
            self.issue(self.grant_request(
                group_membership_evidence=membership, isolation_evidence=evidence
            ))
        membership = self.membership_evidence()
        evidence = self.isolation_evidence(membership)
        roots = evidence["principal_probes"][0]["probe"]["protected_roots"]
        roots[0]["acl_sddl_sha256_after"] = "a" * 64
        with self.assertRaisesRegex(engine.AuthorizationError, "security descriptor"):
            self.issue(self.grant_request(
                group_membership_evidence=membership, isolation_evidence=evidence
            ))
        membership = self.membership_evidence()
        evidence = self.isolation_evidence(membership)
        roots = evidence["principal_probes"][0]["probe"]["protected_roots"]
        roots[3]["rename_capability_denial_error"] = "RENAME_CAPABILITY_GRANTED"
        roots[3]["rename_capability_denial_native_code"] = 0
        with self.assertRaisesRegex(engine.AuthorizationError, "rename"):
            self.issue(self.grant_request(
                group_membership_evidence=membership, isolation_evidence=evidence
            ))
        membership = self.membership_evidence()
        evidence = self.isolation_evidence(membership)
        roots = evidence["principal_probes"][0]["probe"]["protected_roots"]
        roots[3]["hard_link_absent_after"] = False
        with self.assertRaisesRegex(engine.AuthorizationError, "write probe changed"):
            self.issue(self.grant_request(
                group_membership_evidence=membership, isolation_evidence=evidence
            ))
        membership = self.membership_evidence()
        evidence = self.isolation_evidence(membership)
        roots = evidence["principal_probes"][0]["probe"]["protected_roots"]
        roots[3]["anchor_identity_sha256_after"] = "9" * 64
        with self.assertRaisesRegex(engine.AuthorizationError, "anchor identity"):
            self.issue(self.grant_request(
                group_membership_evidence=membership, isolation_evidence=evidence
            ))

    def test_principal_probe_v1_remains_backward_compatible(self) -> None:
        membership = self.membership_evidence()
        evidence = self.isolation_evidence(membership)
        for envelope in evidence["principal_probes"]:
            probe = envelope["probe"]
            probe["protocol_version"] = engine.WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION_V1
            for root in probe["protected_roots"]:
                for field in (
                    "anchor_hardlink_paths_before",
                    "anchor_hardlink_paths_after",
                    "anchor_transport_hardlink_paths_before",
                    "anchor_transport_hardlink_paths_after",
                ):
                    root.pop(field)
        combined = {
            "denied_principal_sids": evidence["denied_principal_sids"],
            "membership_evidence_sha256": evidence["membership_evidence_sha256"],
            "principal_probes": evidence["principal_probes"],
        }
        evidence["combined_probe_sha256"] = engine.canonical_json_sha256(combined)
        issued = self.issue(self.grant_request(
            group_membership_evidence=membership,
            isolation_evidence=evidence,
        ))
        self.assertEqual(issued["status"], "ISSUED")

    def test_principal_probe_versions_cannot_be_mixed(self) -> None:
        membership = self.membership_evidence()
        evidence = self.isolation_evidence(membership)
        probe = evidence["principal_probes"][0]["probe"]
        probe["protocol_version"] = engine.WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION_V1
        for root in probe["protected_roots"]:
            for field in (
                "anchor_hardlink_paths_before",
                "anchor_hardlink_paths_after",
                "anchor_transport_hardlink_paths_before",
                "anchor_transport_hardlink_paths_after",
            ):
                root.pop(field)
        combined = {
            "denied_principal_sids": evidence["denied_principal_sids"],
            "membership_evidence_sha256": evidence["membership_evidence_sha256"],
            "principal_probes": evidence["principal_probes"],
        }
        evidence["combined_probe_sha256"] = engine.canonical_json_sha256(combined)
        with self.assertRaisesRegex(engine.ValidationError, "one protocol version"):
            self.issue(self.grant_request(
                group_membership_evidence=membership,
                isolation_evidence=evidence,
            ))

    @unittest.skipUnless(os.name == "nt", "Windows transport paths are required")
    def test_principal_probe_v2_accepts_only_exact_transport_path_evidence(self) -> None:
        def refresh_digest(evidence: dict) -> None:
            combined = {
                "denied_principal_sids": evidence["denied_principal_sids"],
                "membership_evidence_sha256": evidence["membership_evidence_sha256"],
                "principal_probes": evidence["principal_probes"],
            }
            evidence["combined_probe_sha256"] = engine.canonical_json_sha256(combined)

        membership = self.membership_evidence()
        evidence = self.isolation_evidence(membership)
        root = evidence["principal_probes"][0]["probe"]["protected_roots"][0]
        canonical = root["anchor_hardlink_paths_before"][0]
        transport = engine.normalize_binding(
            "worktree", f"{Path(canonical).drive}\\Work\\.tmp.driveupload\\12345"
        )
        root["anchor_hardlink_paths_before"].append(transport)
        root["anchor_transport_hardlink_paths_before"].append(transport)
        refresh_digest(evidence)
        issued = self.issue(self.grant_request(
            group_membership_evidence=membership,
            isolation_evidence=evidence,
        ))
        self.assertEqual(issued["status"], "ISSUED")

    @unittest.skipUnless(os.name == "nt", "Windows transport paths are required")
    def test_principal_probe_v2_rejects_nontransport_or_mismatched_paths(self) -> None:
        def refresh_digest(evidence: dict) -> None:
            combined = {
                "denied_principal_sids": evidence["denied_principal_sids"],
                "membership_evidence_sha256": evidence["membership_evidence_sha256"],
                "principal_probes": evidence["principal_probes"],
            }
            evidence["combined_probe_sha256"] = engine.canonical_json_sha256(combined)

        for mode in ("outside", "mismatch", "duplicate"):
            with self.subTest(mode=mode):
                membership = self.membership_evidence()
                evidence = self.isolation_evidence(membership)
                root = evidence["principal_probes"][0]["probe"]["protected_roots"][0]
                canonical = root["anchor_hardlink_paths_before"][0]
                drive = Path(canonical).drive
                transport = engine.normalize_binding(
                    "worktree", f"{drive}\\Work\\.tmp.driveupload\\12345"
                )
                if mode == "outside":
                    transport = engine.normalize_binding(
                        "worktree", f"{drive}\\Temp\\outside-transport"
                    )
                    root["anchor_hardlink_paths_before"].append(transport)
                    root["anchor_transport_hardlink_paths_before"].append(transport)
                elif mode == "mismatch":
                    root["anchor_hardlink_paths_before"].append(transport)
                else:
                    root["anchor_hardlink_paths_before"].append(canonical)
                refresh_digest(evidence)
                with self.assertRaises((engine.AuthorizationError, engine.ValidationError)):
                    self.issue(self.grant_request(
                        group_membership_evidence=membership,
                        isolation_evidence=evidence,
                    ))

    def test_proposal_root_must_be_dedicated_and_nonoverlapping(self) -> None:
        overlapping = self.state_root / "proposal-overlap.bin"
        overlapping.write_bytes(REPLACEMENT_BYTES)
        with self.assertRaisesRegex(engine.AuthorizationError, "dedicated and nonoverlapping"):
            self.issue(self.grant_request(proposal_artifact_path=str(overlapping)))

    def test_hard_linked_proposal_and_target_are_rejected(self) -> None:
        proposal_link = self.root / "proposal-link.bin"
        os.link(self.proposal, proposal_link)
        with self.assertRaisesRegex(engine.AuthorizationError, "exactly one hard link"):
            self.issue()
        proposal_link.unlink()
        target_link = self.root / "target-link.txt"
        os.link(self.target, target_link)
        with self.assertRaisesRegex(engine.AuthorizationError, "exactly one hard link"):
            self.issue()

    def test_reparse_proposal_is_rejected_when_platform_can_create_one(self) -> None:
        alias = self.root / "proposal-alias.bin"
        try:
            alias.symlink_to(self.proposal)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        self.assertTrue(alias.is_symlink(), "created proposal alias is not a symbolic link")
        self.assertTrue(
            engine.path_contains_link_or_reparse(alias),
            "created proposal alias is not visible as a link or reparse point",
        )
        with self.assertRaisesRegex(
            engine.AuthorizationError,
            "proposal artifact path must not traverse a link or reparse point",
        ):
            self.issue(self.grant_request(proposal_artifact_path=str(alias)))

    def test_other_reparse_grant_paths_are_rejected_before_resolution(self) -> None:
        cases = (
            (
                "worktree",
                self.root / "worktree-alias",
                self.repository_root,
                True,
                "grant worktree must not traverse a link or reparse point",
            ),
            (
                "worker_runtime_root",
                self.root / "worker-runtime-alias",
                self.worker_runtime_root,
                True,
                "worker runtime root must not traverse a link or reparse point",
            ),
            (
                "app_server_executable_path",
                self.root / "app-server-alias.exe",
                Path(sys.executable).resolve(strict=True),
                False,
                "App Server executable must not traverse a link or reparse point",
            ),
        )
        for _, alias, target, target_is_directory, _ in cases:
            try:
                alias.symlink_to(target, target_is_directory=target_is_directory)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")
            self.assertTrue(alias.is_symlink(), f"created alias is not a symbolic link: {alias}")
            self.assertTrue(
                engine.path_contains_link_or_reparse(alias),
                f"created alias is not visible as a link or reparse point: {alias}",
            )
        for field, alias, _, _, message in cases:
            with self.subTest(field=field), self.assertRaisesRegex(
                engine.AuthorizationError, message
            ):
                self.issue(self.grant_request(**{field: str(alias)}))

    def test_active_grant_blocks_unrelated_mutations_without_revision_drift(self) -> None:
        self.issue()

        def blocked_mutations() -> list:
            return [
                lambda: self.store.bind(
                    self.case_id,
                    kind="thread",
                    value="post-grant-administrative-thread",
                    request_id=request_id(),
                    expected_revision=self.revision,
                ),
                lambda: self.store.freeze_candidate(
                    self.case_id,
                    heads={REPOSITORY: self.head},
                    snapshots={
                        REPOSITORY: {
                            "contract": engine.SNAPSHOT_CONTRACT,
                            "sha256": "7" * 64,
                            "head": self.head,
                        }
                    },
                    request_id=request_id(),
                    expected_revision=self.revision,
                ),
                lambda: self.store.record_control_failure(
                    self.case_id,
                    category="runtime-boundary",
                    fingerprint="blocked-during-active-grant",
                    description="must not race an exact one-use action",
                    request_id=request_id(),
                    expected_revision=self.revision,
                ),
            ]

        issued_revision = self.revision
        for mutation in blocked_mutations():
            with self.assertRaisesRegex(engine.AuthorizationError, "action grant is ISSUED"):
                mutation()
            self.assertEqual(self.revision, issued_revision)
        with self.assertRaises(engine.RevisionConflict):
            self.claim(expected_revision=issued_revision - 1)
        claimed = self.claim()
        self.assertEqual(claimed["status"], "CLAIMED")
        claimed_revision = self.revision
        for mutation in blocked_mutations():
            with self.assertRaisesRegex(engine.AuthorizationError, "action grant is CLAIMED"):
                mutation()
            self.assertEqual(self.revision, claimed_revision)
        self.target.write_bytes(REPLACEMENT_BYTES)
        completed = self.store.complete_action_grant(
            self.case_id,
            completion={
                "protocol_version": engine.ACTION_GRANT_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": self.grant_id,
                "controller_receipt_sha256": self.grant()["controller_receipt_sha256"],
                "broker_principal_sid": BROKER_SID,
                "post_replacement_evidence_sha256": "d" * 64,
                "completed_at": engine.utc_now(),
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )
        self.assertEqual(completed["status"], "COMPLETED")

    def test_claimed_failure_locks_exact_case(self) -> None:
        self.issue()
        self.claim()
        failed = self.store.fail_action_grant(
            self.case_id,
            failure={
                "protocol_version": engine.ACTION_GRANT_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": self.grant_id,
                "broker_principal_sid": BROKER_SID,
                "failure_stage": "post_claim",
                "failure_code": "FORCED_TEST_FAILURE",
                "failure_evidence_sha256": "8" * 64,
                "observed_at": engine.utc_now(),
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(self.store.get_case(self.case_id)["state"], "CASE_LOCKED")

    def test_unclaimed_supervisor_loss_consumes_grant_and_locks_exact_case(self) -> None:
        self.issue()
        failed = self.store.fail_action_grant(
            self.case_id,
            failure={
                "protocol_version": engine.ACTION_GRANT_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": self.grant_id,
                "broker_principal_sid": BROKER_SID,
                "failure_stage": "supervisor_context",
                "failure_code": "SUPERVISOR_CONTEXT_LOST",
                "failure_evidence_sha256": "9" * 64,
                "observed_at": engine.utc_now(),
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(self.store.get_case(self.case_id)["state"], "CASE_LOCKED")

    def test_noop_replacement_is_rejected_before_grant_issuance(self) -> None:
        self.proposal.write_bytes(BASELINE_BYTES)
        with self.assertRaisesRegex(engine.AuthorizationError, "must differ"):
            self.issue(
                self.grant_request(
                    replacement_sha256=hashlib.sha256(BASELINE_BYTES).hexdigest(),
                    proposal_size=len(BASELINE_BYTES),
                )
            )


class RuntimeBrokerTests(RuntimeFixture):
    def execute_with_patches(self) -> dict:
        with self.patched_broker():
            return broker.execute_grant(
                self.state_root, self.case_id, self.grant_id, self.receipt
            )

    def test_successful_completion_restores_acl_from_sealed_preissue_evidence(
        self,
    ) -> None:
        self.issue()
        grant = copy.deepcopy(self.grant())
        post_dacl = copy.deepcopy(grant["preissue_dacl_evidence"])
        post_dacl["observed_at"] = (
            dt.datetime.fromisoformat(post_dacl["observed_at"])
            + dt.timedelta(seconds=1)
        ).isoformat()
        self.assertNotEqual(post_dacl, grant["preissue_dacl_evidence"])

        def fresh_post_evidence(_store, current_grant, *, run_id):
            body = {
                "protocol_version": broker.POST_REPLACEMENT_EVIDENCE_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": current_grant["grant_id"],
                "run_id": run_id,
                "dacl_evidence": post_dacl,
            }
            return {
                **body,
                "post_replacement_evidence_sha256": engine.canonical_json_sha256(
                    body
                ),
            }

        with self.patched_broker(), mock.patch.object(
            broker,
            "_collect_post_replacement_isolation_evidence",
            side_effect=fresh_post_evidence,
        ), mock.patch.object(
            broker, "_restore_acl_snapshot_after_lockdown", return_value={}
        ) as restore:
            result = broker.execute_grant(
                self.state_root, self.case_id, self.grant_id, self.receipt
            )

        self.assertEqual(result["status"], "COMPLETED")
        restore.assert_called_once()
        restore_evidence = restore.call_args.kwargs["lockdown_dacl_evidence"]
        self.assertEqual(restore_evidence, grant["preissue_dacl_evidence"])
        self.assertNotEqual(restore_evidence, post_dacl)

    def test_broker_executes_exact_replacement_once_and_denies_second_action(self) -> None:
        self.issue()
        result = self.execute_with_patches()
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(self.target.read_bytes(), REPLACEMENT_BYTES)
        with self.assertRaisesRegex(broker.BrokerAuthorizationError, "already completed"):
            self.execute_with_patches()
        records = broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        ).records()
        self.assertEqual(sum(item["event"] == "COMPLETED" for item in records), 1)
        for item in records:
            if item["event"] in {"PRECLAIM", "CLAIMED", "REPLACED", "COMPLETED"}:
                self.assertEqual(item["broker_principal_sid"], BROKER_SID)
                self.assertIsInstance(item["broker_process_id"], int)
                self.assertEqual(item["target_path"], TARGET_PATH)
                self.assertRegex(item["target_sha256_before"], r"^[0-9a-f]{64}$")
                self.assertRegex(item["target_sha256_after"], r"^[0-9a-f]{64}$")
        terminal = next(item for item in records if item["event"] == "COMPLETED")
        self.assertEqual(terminal["changed_path"], TARGET_PATH)
        self.assertEqual(terminal["target_sha256_before"], hashlib.sha256(BASELINE_BYTES).hexdigest())
        self.assertEqual(terminal["target_sha256_after"], hashlib.sha256(REPLACEMENT_BYTES).hexdigest())
        self.assertRegex(terminal["claim_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(terminal["result_sha256"], r"^[0-9a-f]{64}$")

    def test_broker_rejects_idempotent_claim_as_write_authority(self) -> None:
        self.issue()
        original = broker.CaseStore.claim_action_grant

        def idempotent_claim(store, *args, **kwargs):
            result = original(store, *args, **kwargs)
            return {**result, "idempotent": True}

        with self.patched_broker(), mock.patch.object(
            broker.CaseStore, "claim_action_grant", new=idempotent_claim
        ):
            with self.assertRaisesRegex(broker.BrokerAuthorizationError, "not write authority"):
                broker.execute_grant(
                    self.state_root, self.case_id, self.grant_id, self.receipt
                )
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)
        self.assertEqual(self.grant()["status"], "CLAIMED")

    def test_signed_parent_receipt_cannot_override_canonical_implementation_actor(self) -> None:
        self.issue()
        forged_body = {
            **self.receipt_body,
            "actor_thread_id": self.parent_thread,
            "actor_turn_id": "parent-turn-one",
        }
        forged = broker.seal_controller_receipt(forged_body, CONTROLLER_KEY)
        with self.patched_broker():
            failed = broker.execute_grant(self.state_root, self.case_id, self.grant_id, forged)
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)
        self.assertEqual(self.grant()["status"], "FAILED")
        self.assertEqual(self.store.get_case(self.case_id)["state"], "CASE_LOCKED")

    def test_claimed_baseline_recovers_once_from_protected_journal(self) -> None:
        self.issue()
        claim = self.claim()
        journal = broker.BrokerJournal(self.state_root, self.case_id, self.grant_id)
        original_run = "original-broker-run"
        journal.append(
            "PRECLAIM", original_run, claim_request_id="original-claim",
            grant_sha256=self.grant()["grant_sha256"],
            target_sha256=hashlib.sha256(BASELINE_BYTES).hexdigest(),
        )
        journal.append(
            "CLAIMED", original_run, claim_request_id="original-claim",
            claim_sha256=claim["claim_sha256"], recovery_attempted=False,
        )
        result = self.execute_with_patches()
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(self.target.read_bytes(), REPLACEMENT_BYTES)
        self.assertEqual(
            sum(record["event"] == "RECOVERY_STARTED" for record in journal.records()), 1
        )

    def test_two_concurrent_brokers_produce_one_claim_and_one_replacement(self) -> None:
        self.issue()
        counter = 0
        counter_lock = threading.Lock()
        original_replace = broker._atomic_replace

        def counted_replace(*args, **kwargs):
            nonlocal counter
            with counter_lock:
                counter += 1
            return original_replace(*args, **kwargs)

        with self.patched_broker(), mock.patch.object(
            broker, "_atomic_replace", side_effect=counted_replace
        ):
            def run():
                try:
                    return broker.execute_grant(
                        self.state_root, self.case_id, self.grant_id, self.receipt
                    )
                except broker.BrokerAuthorizationError as exc:
                    return exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _index: run(), range(2)))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(counter, 1)
        self.assertEqual(self.target.read_bytes(), REPLACEMENT_BYTES)
        claim_events = [
            event
            for event in self.store.get_case(self.case_id)["events"].values()
            if event["operation"] == "claim_action_grant"
        ]
        self.assertEqual(len(claim_events), 1)
        self.assertFalse(claim_events[0]["result"]["idempotent"])

    def test_completion_failure_rolls_back_baseline_and_locks_case(self) -> None:
        self.issue()
        with self.patched_broker(), mock.patch.object(
            broker, "_complete", side_effect=broker.BrokerPreflightError("forced completion failure")
        ):
            result = broker.execute_grant(
                self.state_root, self.case_id, self.grant_id, self.receipt
            )
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)
        self.assertEqual(engine._git_status_paths(self.repository_root), [])
        case = self.store.get_case(self.case_id)
        self.assertEqual(case["state"], "CASE_LOCKED")
        self.assertTrue(case["runtime"]["action_grants"][self.grant_id]["result"]["baseline_restored"])
        failed_events = [
            item for item in broker.BrokerJournal(
                self.state_root, self.case_id, self.grant_id
            ).records() if item["event"] == "FAILED"
        ]
        self.assertEqual(len(failed_events), 1)
        self.assertEqual(failed_events[0]["broker_principal_sid"], BROKER_SID)
        self.assertRegex(failed_events[0]["result_sha256"], r"^[0-9a-f]{64}$")

    def test_preclaim_acl_configuration_diagnostic_is_persisted_in_journal(
        self,
    ) -> None:
        self.issue()
        grant = self.grant()
        diagnostic = {
            "protocol_version": (
                broker.PROTECTED_DACL_CONFIGURATION_FAILURE_PROTOCOL_VERSION
            ),
            "diagnostic_status": "PARSED",
            "inventory_index": 47,
            "object_count": 2359,
            "path": grant["state_root"] + "/case-state.json",
            "path_sha256": "a" * 64,
            "object_type": "file",
            "scope": "descendant",
            "error_code": "NATIVE_DACL_WRITE_FAILED",
            "native_error_code": 5,
            "message_sha256": "b" * 64,
            "process_exit_code": 85,
            "stderr_sha256": "c" * 64,
        }
        journal = broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )

        with mock.patch.object(
            broker, "_restore_acl_snapshot_after_lockdown", return_value={}
        ):
            result = broker._record_failure(
                self.store,
                self.case_id,
                grant,
                BROKER_SID,
                journal,
                "diagnostic-persistence-run",
                stage="preclaim",
                code="PRECLAIM_VERIFICATION_FAILED",
                evidence={
                    "observed_before": grant["baseline_sha256"],
                    "error_type": "BrokerAclConfigurationError",
                    "error_fingerprint": "d" * 64,
                    "acl_configuration_diagnostic": diagnostic,
                },
            )

        self.assertEqual(result["status"], "FAILED")
        failed = [
            item
            for item in journal.records()
            if item["event"] == "FAILED"
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["acl_configuration_diagnostic"], diagnostic)
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_cli_surface_has_no_arbitrary_command_path_or_content_input(self) -> None:
        parser = broker.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "execute", "--state-root", str(self.state_root), "--case-id", self.case_id,
                    "--grant-id", self.grant_id, "--controller-receipt-json", "{}",
                    "--command", "arbitrary",
                ]
            )


class RecoveryCompositionTests(RuntimeFixture):
    def _runtime_roots(self) -> dict[str, str]:
        return {
            "target_root": engine.normalize_binding(
                "worktree", str(self.repository_root)
            ),
            "state_root": engine.normalize_binding("worktree", str(self.state_root)),
            "broker_source_root": engine.normalize_binding("worktree", str(ROOT)),
            "proposal_root": engine.normalize_binding(
                "worktree", str(self.proposal_root)
            ),
        }

    @contextmanager
    def _recovery_identity_and_acl_patches(self):
        inventory = [
            {"path": item["path"], "object_type": "file", "scope": "descendant"}
            for item in self.protected_acl_snapshot()
        ]
        with mock.patch.object(
            broker,
            "windows_identity",
            return_value=("fixture\\broker", BROKER_SID),
        ), mock.patch.object(
            broker, "_verify_source_pins", return_value=None
        ), mock.patch.object(
            broker, "_verify_protected_acl_restore", return_value=None
        ) as verify_restore, mock.patch.object(
            broker, "_protected_acl_inventory", return_value=inventory
        ), mock.patch.object(
            broker, "_restore_protected_acls", return_value=None
        ):
            yield verify_restore

    def test_recover_pending_preissue_acl_lockdown_then_terminally_aborts_generation(self) -> None:
        roots = self._runtime_roots()
        denied = [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID]
        snapshot = self.protected_acl_snapshot()
        snapshot_sha256 = engine.canonical_json_sha256(snapshot)
        journal = broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )
        run_id = "preissue-crash"
        snapshot_event = journal.append(
            "ACL_SNAPSHOT",
            run_id,
            protected_acl_snapshot=snapshot,
            protected_acl_snapshot_sha256=snapshot_sha256,
        )
        intent = {
            "roots": roots,
            "denied_principal_sids": denied,
            "broker_principal_sid": BROKER_SID,
        }
        intent_event = journal.append(
            "ACL_LOCKDOWN_INTENT",
            run_id,
            protected_acl_snapshot_sha256=snapshot_sha256,
            lockdown_intent=intent,
            lockdown_intent_sha256=engine.canonical_json_sha256(intent),
        )
        inventory = [
            {"path": item["path"], "object_type": "file", "scope": "descendant"}
            for item in snapshot
        ]
        with mock.patch.object(
            broker,
            "_verify_protected_acl_restore",
            side_effect=[broker.BrokerAuthorizationError("lockdown active"), None],
        ), mock.patch.object(
            broker, "_protected_acl_inventory", return_value=inventory
        ), mock.patch.object(broker, "_restore_protected_acls") as restore:
            recovered = broker.recover_pending_preissue_acl_lockdowns(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
                expected_roots=roots,
                expected_denied_principal_sids=denied,
                expected_broker_principal_sid=BROKER_SID,
            )
        self.assertEqual(len(recovered), 1)
        restore.assert_called_once_with(snapshot)
        recovery = recovered[0]
        self.assertEqual(
            recovery["snapshot_event_sha256"], snapshot_event["event_sha256"]
        )
        self.assertEqual(
            recovery["lockdown_intent_event_sha256"], intent_event["event_sha256"]
        )
        before = self.store.get_case(self.case_id)
        limits = copy.deepcopy(before["limits"])
        result = self.store.abort_runtime_generation(
            self.case_id,
            evidence={
                "protocol_version": engine.RUNTIME_GENERATION_ABORT_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": self.grant_id,
                "snapshot_event_sha256": recovery["snapshot_event_sha256"],
                "lockdown_intent_event_sha256": recovery[
                    "lockdown_intent_event_sha256"
                ],
                "acl_restored_event_sha256": recovery[
                    "acl_restored_event_sha256"
                ],
                "reason_code": "PREISSUE_GENERATION_ABANDONED",
            },
            request_id=request_id(),
            expected_revision=before["revision"],
        )
        self.assertEqual(result["state"], "CASE_LOCKED")
        locked = self.store.get_case(self.case_id)
        self.assertEqual(locked["limits"], limits)
        self.assertEqual(locked["runtime"]["action_grants"], {})
        self.assertEqual(locked["lock_reason"], "PREISSUE_GENERATION_ABANDONED")
        self.assertEqual(journal.records()[-1]["event"], "ACL_RESTORED")

    def test_recover_orphaned_issued_grant_fails_locks_and_restores_acl(self) -> None:
        self.issue()
        with self._recovery_identity_and_acl_patches() as verify_restore:
            recovered = broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )
        self.assertEqual(recovered["status"], "FAILED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)
        case = self.store.get_case(self.case_id)
        self.assertEqual(case["state"], "CASE_LOCKED")
        self.assertEqual(self.grant()["status"], "FAILED")
        events = [
            item["event"]
            for item in broker.BrokerJournal(
                self.state_root, self.case_id, self.grant_id
            ).records()
        ]
        self.assertEqual(events[-3:], ["ROLLED_BACK", "FAILED", "ACL_RESTORED"])
        verify_restore.assert_called()

    def test_recover_orphaned_claimed_grant_rolls_back_replacement_and_restores_acl(self) -> None:
        self.issue()
        self.claim()
        self.target.write_bytes(REPLACEMENT_BYTES)
        self.assertEqual(engine._git_status_paths(self.repository_root), [TARGET_PATH])
        with self._recovery_identity_and_acl_patches():
            recovered = broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )
        self.assertEqual(recovered["status"], "FAILED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)
        self.assertEqual(engine._git_status_paths(self.repository_root), [])
        self.assertEqual(self.store.get_case(self.case_id)["state"], "CASE_LOCKED")
        result = self.grant()["result"]
        self.assertTrue(result["baseline_restored"])
        self.assertEqual(result["failure_code"], "SUPERVISOR_CONTEXT_LOST")

    def test_recover_completed_grant_reconstructs_journal_then_restores_acl(self) -> None:
        self.issue()
        self.claim()
        self.target.write_bytes(REPLACEMENT_BYTES)
        grant = self.grant()
        membership = {"fixed": "post-membership"}
        isolation = {"fixed": "post-isolation"}
        dacl = grant["preissue_dacl_evidence"]
        post_body = {
            "protocol_version": broker.POST_REPLACEMENT_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 1,
            "grant_id": self.grant_id,
            "run_id": "completed-crash",
            "target_sha256": grant["replacement_sha256"],
            "status_sha256": "9" * 64,
            "observed_status_paths": grant["allowed_paths"],
            "membership_evidence": membership,
            "membership_evidence_sha256": engine.canonical_json_sha256(membership),
            "isolation_evidence": isolation,
            "isolation_evidence_sha256": engine.canonical_json_sha256(isolation),
            "dacl_evidence": dacl,
            "dacl_evidence_sha256": engine.canonical_json_sha256(dacl),
            "protected_acl_snapshot_sha256": grant[
                "protected_acl_snapshot_sha256"
            ],
            "observed_at": engine.utc_now(),
        }
        post_evidence = {
            **post_body,
            "post_replacement_evidence_sha256": engine.canonical_json_sha256(
                post_body
            ),
        }
        journal = broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )
        journal.append(
            "POST_ISOLATION_VERIFIED",
            "completed-crash",
            post_replacement_evidence=post_evidence,
            post_replacement_evidence_sha256=post_evidence[
                "post_replacement_evidence_sha256"
            ],
            protected_acl_snapshot_sha256=grant[
                "protected_acl_snapshot_sha256"
            ],
        )
        completed = self.store.complete_action_grant(
            self.case_id,
            completion={
                "protocol_version": engine.ACTION_GRANT_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": self.grant_id,
                "controller_receipt_sha256": grant[
                    "controller_receipt_sha256"
                ],
                "broker_principal_sid": BROKER_SID,
                "post_replacement_evidence_sha256": post_evidence[
                    "post_replacement_evidence_sha256"
                ],
                "completed_at": engine.utc_now(),
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertFalse(any(item["event"] == "COMPLETED" for item in journal.records()))
        with self._recovery_identity_and_acl_patches():
            recovered = broker.recover_completed_action_grant_cleanup(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )
        self.assertEqual(recovered["status"], "recovered_completed")
        records = journal.records()
        self.assertEqual(records[-2]["event"], "COMPLETED")
        self.assertTrue(records[-2]["recovered_after_canonical_commit"])
        self.assertEqual(records[-1]["event"], "ACL_RESTORED")
        self.assertEqual(
            sum(item["event"] == "COMPLETED" for item in records), 1
        )

    def test_recover_failed_grant_finishes_acl_cleanup_without_reissuing(self) -> None:
        self.issue()
        self.store.fail_action_grant(
            self.case_id,
            failure={
                "protocol_version": engine.ACTION_GRANT_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": self.grant_id,
                "broker_principal_sid": BROKER_SID,
                "failure_stage": "supervisor_context",
                "failure_code": "SUPERVISOR_CONTEXT_LOST",
                "failure_evidence_sha256": "f" * 64,
                "observed_at": engine.utc_now(),
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )
        with self._recovery_identity_and_acl_patches():
            first = broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )
            second = broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )
        self.assertEqual(first["status"], "failed_stable")
        self.assertTrue(first["acl_restore"]["restored"])
        self.assertEqual(second["status"], "failed_stable")
        self.assertTrue(second["acl_restore"]["already_restored"])
        self.assertEqual(self.grant()["status"], "FAILED")
        records = broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        ).records()
        self.assertEqual(
            sum(item["event"] == "ACL_RESTORED" for item in records), 1
        )


class WindowsCompletedAclRecoveryIntegrationTests(RuntimeFixture):
    def fixture_state_root(self) -> Path:
        desired_length = 180
        segment_length = desired_length - len(str(self.root)) - 1
        self.assertGreater(segment_length, 32)
        self.assertLessEqual(segment_length, 255)
        state_root = self.root / ("s" * segment_length)
        self.assertEqual(len(str(state_root)), desired_length)
        return state_root

    def setUp(self) -> None:
        self.broker_sid = (
            broker.windows_identity()[1] if os.name == "nt" else BROKER_SID
        )
        super().setUp()

    def apply_acl_descriptors_with_test_native_api(
        self, snapshot: list[dict], *, lockdown: bool = False
    ) -> None:
        script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class CcosTestNativeAclApply {
  [DllImport("advapi32.dll", EntryPoint = "SetFileSecurityW", ExactSpelling = true,
    SetLastError = true, CharSet = CharSet.Unicode)]
  public static extern bool SetFileSecurityW(
    string path,
    uint securityInformation,
    byte[] securityDescriptor);
}
'@

function Get-TestNativePath {
  param([string]$Path)
  $full = [System.IO.Path]::GetFullPath($Path)
  if ($full.StartsWith('\\?\', [System.StringComparison]::Ordinal)) {
    return $full
  }
  if ($full.StartsWith('\\', [System.StringComparison]::Ordinal)) {
    return '\\?\UNC\' + $full.Substring(2)
  }
  return '\\?\' + $full
}

$payload = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
$items = @($payload.items | ForEach-Object { $_ })
if ([string]$payload.mode -eq 'lockdown') {
  $broker = [System.Security.Principal.SecurityIdentifier]::new(
    [string]$payload.broker_sid
  )
  $system = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
  $denied = @($payload.denied | ForEach-Object {
    [System.Security.Principal.SecurityIdentifier]::new([string]$_)
  })
  $sandboxGroup = $denied[2]
  $denyRights = [System.Security.AccessControl.FileSystemRights][Int64](
    $payload.deny_mask
  )
}
foreach ($item in $items) {
  $binary = $null
  try {
    [uint32]$securityInformation = 4
    if ([string]$payload.mode -eq 'lockdown') {
      if ([string]$item.object_type -eq 'directory') {
        $acl = [System.Security.AccessControl.DirectorySecurity]::new()
      } else {
        $acl = [System.Security.AccessControl.FileSecurity]::new()
      }
      $acl.SetAccessRuleProtection($true, $false)
      $inheritance = [System.Security.AccessControl.InheritanceFlags]::None
      if ([string]$item.object_type -eq 'directory') {
        $inheritance = [System.Security.AccessControl.InheritanceFlags](
          [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
          [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
        )
      }
      foreach ($principal in @($broker, $system)) {
        [void]$acl.PurgeAccessRules($principal)
        [void]$acl.AddAccessRule(
          [System.Security.AccessControl.FileSystemAccessRule]::new(
            $principal,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
          )
        )
      }
      foreach ($principal in $denied) {
        [void]$acl.PurgeAccessRules($principal)
        [void]$acl.AddAccessRule(
          [System.Security.AccessControl.FileSystemAccessRule]::new(
            $principal,
            $denyRights,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Deny
          )
        )
      }
      [void]$acl.AddAccessRule(
        [System.Security.AccessControl.FileSystemAccessRule]::new(
          $sandboxGroup,
          [System.Security.AccessControl.FileSystemRights]::ReadAndExecute,
          $inheritance,
          [System.Security.AccessControl.PropagationFlags]::None,
          [System.Security.AccessControl.AccessControlType]::Allow
        )
      )
      $binary = $acl.GetSecurityDescriptorBinaryForm()
      $securityInformation = $securityInformation -bor
        [Convert]::ToUInt32('80000000', 16)
    } else {
      $raw = [System.Security.AccessControl.RawSecurityDescriptor]::new(
        [string]$item.sddl
      )
      $binary = New-Object byte[] $raw.BinaryLength
      $raw.GetBinaryForm($binary, 0)
      if (($raw.ControlFlags -band
          [System.Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -ne 0) {
        $securityInformation = $securityInformation -bor
          [Convert]::ToUInt32('80000000', 16)
      } else {
        $securityInformation = $securityInformation -bor
          [Convert]::ToUInt32('20000000', 16)
      }
    }
    if (-not [CcosTestNativeAclApply]::SetFileSecurityW(
        (Get-TestNativePath ([string]$item.path)),
        $securityInformation,
        $binary)) {
      $nativeError = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
      throw "test native ACL application failed with Win32 error $nativeError"
    }
  }
  finally {
    if ($null -ne $binary) {
      [Array]::Clear($binary, 0, $binary.Length)
    }
  }
}
"""
        payload = {
            "mode": "lockdown" if lockdown else "descriptor",
            "broker_sid": self.broker_sid,
            "denied": [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID],
            "deny_mask": engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
            "items": [
                {
                    "path": item["path"],
                    "sddl": item.get("sddl"),
                    "object_type": (
                        "directory" if Path(item["path"]).is_dir() else "file"
                    ),
                }
                for item in sorted(
                    snapshot,
                    key=lambda item: (
                        len(Path(item["path"]).parts),
                        item["path"].casefold(),
                    ),
                )
            ],
        }
        result = broker._run_powershell(
            script, {}, input_bytes=engine.canonical_json_bytes(payload)
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stderr.decode("utf-8", errors="replace"),
        )

    def read_acl_topology(self, paths: list[Path]) -> list[dict]:
        script = r"""
$ErrorActionPreference = 'Stop'
$paths = @(ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd()) | ForEach-Object { $_ })
$result = @()
foreach ($path in $paths) {
  $acl = Get-Acl -LiteralPath ([string]$path)
  $explicitRules = @($acl.Access | Where-Object { -not $_.IsInherited })
  $result += [PSCustomObject]@{
    protected = [bool]$acl.AreAccessRulesProtected
    inherited_rule_count = @($acl.Access | Where-Object { $_.IsInherited }).Count
    explicit_rule_count = $explicitRules.Count
    explicit_rules = @($explicitRules | ForEach-Object {
      [PSCustomObject]@{
        principal_sid = $_.IdentityReference.Translate(
          [System.Security.Principal.SecurityIdentifier]
        ).Value.ToUpperInvariant()
        access_type = $_.AccessControlType.ToString()
        rights_mask = [Int64]$_.FileSystemRights
        inheritance_flags = [Int32]$_.InheritanceFlags
        propagation_flags = [Int32]$_.PropagationFlags
      }
    })
  }
}
@($result) | ConvertTo-Json -Compress -Depth 5
"""
        result = broker._run_powershell(
            script,
            {},
            input_bytes=engine.canonical_json_bytes(
                [engine.normalize_binding("worktree", str(path)) for path in paths]
            ),
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stderr.decode("utf-8", errors="replace"),
        )
        return json.loads(result.stdout.decode("utf-8"))

    @unittest.skipUnless(os.name == "nt", "Windows ACL integration test")
    def test_exact_long_completed_cleanup_restores_once_and_restart_only_verifies(
        self,
    ) -> None:
        journal = broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )
        journal.ensure_file()
        journal.lock_path.touch()
        exact_journal_paths = [
            self.state_root / broker.BROKER_JOURNAL_DIRECTORY,
            journal.directory,
            journal.lock_path,
            journal.path,
        ]
        self.assertEqual(len(str(journal.lock_path)), 302)
        self.assertEqual(len(str(journal.path)), 303)
        original_journal_topology = self.read_acl_topology(exact_journal_paths)
        self.assertTrue(
            all(
                not item["protected"] and item["inherited_rule_count"] > 0
                for item in original_journal_topology
            )
        )
        roots = {
            "target_root": engine.normalize_binding(
                "worktree", str(self.repository_root)
            ),
            "state_root": engine.normalize_binding("worktree", str(self.state_root)),
            "broker_source_root": engine.normalize_binding("worktree", str(ROOT)),
            "proposal_root": engine.normalize_binding(
                "worktree", str(self.proposal_root)
            ),
        }
        snapshot = broker._snapshot_protected_acls(roots)
        grant_request = self.grant_request(
            protected_acl_snapshot=snapshot,
            protected_acl_snapshot_sha256=engine.canonical_json_sha256(snapshot),
        )
        self.issue(grant_request)
        self.claim()
        self.target.write_bytes(REPLACEMENT_BYTES)
        grant = self.grant()
        self.assertEqual(engine._git_status_paths(self.repository_root), [TARGET_PATH])
        journal.append(
            "REPLACED",
            "completed-acl-crash",
            **broker._journal_action_details(
                grant,
                self.broker_sid,
                target_sha256_before=grant["baseline_sha256"],
                target_sha256_after=grant["replacement_sha256"],
                changed_path=grant["target_path"],
                claim_sha256=grant["claim"]["claim_sha256"],
            ),
        )
        membership = {"fixed": "post-membership"}
        isolation = {"fixed": "post-isolation"}
        dacl = copy.deepcopy(grant["preissue_dacl_evidence"])
        dacl["observed_at"] = (
            dt.datetime.fromisoformat(dacl["observed_at"])
            + dt.timedelta(seconds=1)
        ).isoformat()
        self.assertNotEqual(dacl, grant["preissue_dacl_evidence"])
        post_body = {
            "protocol_version": broker.POST_REPLACEMENT_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 1,
            "grant_id": self.grant_id,
            "run_id": "completed-acl-crash",
            "target_sha256": grant["replacement_sha256"],
            "status_sha256": "9" * 64,
            "observed_status_paths": grant["allowed_paths"],
            "membership_evidence": membership,
            "membership_evidence_sha256": engine.canonical_json_sha256(membership),
            "isolation_evidence": isolation,
            "isolation_evidence_sha256": engine.canonical_json_sha256(isolation),
            "dacl_evidence": dacl,
            "dacl_evidence_sha256": engine.canonical_json_sha256(dacl),
            "protected_acl_snapshot_sha256": grant[
                "protected_acl_snapshot_sha256"
            ],
            "observed_at": engine.utc_now(),
        }
        post_evidence = {
            **post_body,
            "post_replacement_evidence_sha256": engine.canonical_json_sha256(
                post_body
            ),
        }
        journal.append(
            "POST_ISOLATION_VERIFIED",
            "completed-acl-crash",
            post_replacement_evidence=post_evidence,
            post_replacement_evidence_sha256=post_evidence[
                "post_replacement_evidence_sha256"
            ],
            protected_acl_snapshot_sha256=grant[
                "protected_acl_snapshot_sha256"
            ],
        )
        self.store.complete_action_grant(
            self.case_id,
            completion={
                "protocol_version": engine.ACTION_GRANT_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "grant_id": self.grant_id,
                "controller_receipt_sha256": grant[
                    "controller_receipt_sha256"
                ],
                "broker_principal_sid": self.broker_sid,
                "post_replacement_evidence_sha256": post_evidence[
                    "post_replacement_evidence_sha256"
                ],
                "completed_at": engine.utc_now(),
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )
        completed_grant = self.grant()
        journal.append(
            "COMPLETED",
            "completed-acl-crash",
            **broker._journal_action_details(
                completed_grant,
                self.broker_sid,
                target_sha256_before=completed_grant["baseline_sha256"],
                target_sha256_after=completed_grant["replacement_sha256"],
                changed_path=completed_grant["target_path"],
                claim_sha256=completed_grant["claim"]["claim_sha256"],
                result_sha256=completed_grant["result"]["result_sha256"],
            ),
        )
        self.assertEqual(journal.records()[-1]["event"], "COMPLETED")
        self.assertFalse(
            any(item["event"] == "ACL_RESTORED" for item in journal.records())
        )

        journal_paths = {
            engine.normalize_binding("worktree", str(path))
            for path in exact_journal_paths
        }
        journal_baseline = [
            item
            for item in completed_grant["protected_acl_snapshot"]
            if item["path"] in journal_paths
        ]
        self.assertEqual(len(journal_baseline), 4)

        revision_before_cleanup = self.revision
        records_before_cleanup = journal.records()
        try:
            self.apply_acl_descriptors_with_test_native_api(
                journal_baseline,
                lockdown=True,
            )
            protected_journal_topology = self.read_acl_topology(
                exact_journal_paths
            )
            self.assertEqual(len(protected_journal_topology), 4)
            for index, item in enumerate(protected_journal_topology):
                inheritance_flags = 3 if index < 2 else 0
                expected_rules = sorted(
                    [
                        (
                            WORKER_SID,
                            "Deny",
                            engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
                            inheritance_flags,
                            0,
                        ),
                        (
                            OFFLINE_WORKER_SID,
                            "Deny",
                            engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
                            inheritance_flags,
                            0,
                        ),
                        (
                            SANDBOX_GROUP_SID,
                            "Deny",
                            engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
                            inheritance_flags,
                            0,
                        ),
                        (
                            "S-1-5-18",
                            "Allow",
                            2032127,
                            inheritance_flags,
                            0,
                        ),
                        (
                            self.broker_sid,
                            "Allow",
                            2032127,
                            inheritance_flags,
                            0,
                        ),
                        (
                            SANDBOX_GROUP_SID,
                            "Allow",
                            1179817,
                            inheritance_flags,
                            0,
                        ),
                    ]
                )
                observed_rules = sorted(
                    (
                        rule["principal_sid"],
                        rule["access_type"],
                        rule["rights_mask"],
                        rule["inheritance_flags"],
                        rule["propagation_flags"],
                    )
                    for rule in item["explicit_rules"]
                )
                self.assertTrue(item["protected"])
                self.assertEqual(item["inherited_rule_count"], 0)
                self.assertEqual(item["explicit_rule_count"], 6)
                self.assertEqual(observed_rules, expected_rules)
            with self.assertRaises(broker.BrokerAuthorizationError):
                broker._verify_protected_acl_restore(
                    completed_grant["protected_acl_snapshot"]
                )
            with mock.patch.object(
                broker, "_verify_source_pins", return_value=None
            ):
                first = broker.recover_completed_action_grant_cleanup(
                    state_root=self.state_root,
                    case_id=self.case_id,
                    grant_id=self.grant_id,
                )
            self.assertEqual(first["status"], "recovered_completed")
            self.assertTrue(first["acl_restore"]["restored"])
            self.assertFalse(first["acl_restore"]["already_restored"])
            records_after_cleanup = journal.records()
            self.assertEqual(
                sum(item["event"] == "ACL_RESTORED" for item in records_after_cleanup),
                1,
            )

            module_name = f"case_runtime_broker_restart_{uuid.uuid4().hex}"
            module_path = SCRIPT_DIRECTORY / "case_runtime_broker.py"
            specification = importlib.util.spec_from_file_location(
                module_name, module_path
            )
            self.assertIsNotNone(specification)
            self.assertIsNotNone(specification.loader)
            restarted_broker = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(restarted_broker)
            with mock.patch.object(
                restarted_broker, "_verify_source_pins", return_value=None
            ):
                second = restarted_broker.recover_completed_action_grant_cleanup(
                    state_root=self.state_root,
                    case_id=self.case_id,
                    grant_id=self.grant_id,
                )
            self.assertEqual(second["status"], "recovered_completed")
            self.assertTrue(second["acl_restore"]["already_restored"])
            self.assertEqual(journal.records(), records_after_cleanup)
        finally:
            self.apply_acl_descriptors_with_test_native_api(journal_baseline)
            broker._verify_protected_acl_restore(journal_baseline)

        self.assertEqual(
            self.read_acl_topology(exact_journal_paths),
            original_journal_topology,
        )

        final_records = journal.records()
        for event in ("REPLACED", "COMPLETED", "ACL_RESTORED"):
            self.assertEqual(sum(item["event"] == event for item in final_records), 1)
        self.assertEqual(
            len(self.store.get_case(self.case_id)["runtime"]["action_grants"]), 1
        )
        self.assertEqual(self.grant()["status"], "COMPLETED")
        self.assertEqual(self.revision, revision_before_cleanup)
        self.assertEqual(self.target.read_bytes(), REPLACEMENT_BYTES)
        self.assertEqual(engine._git_status_paths(self.repository_root), [TARGET_PATH])
        self.assertEqual(
            len(records_before_cleanup) + 1,
            len(final_records),
        )


class BrokerHelperIsolationTests(RuntimeFixture):
    def test_restorable_sddl_drops_only_protected_auto_inherited_marker(self) -> None:
        ace = "(A;;FA;;;SY)"
        self.assertEqual(
            broker._canonical_restorable_sddl(f"O:SYG:SYD:PAI{ace}"),
            f"O:SYG:SYD:P{ace}",
        )
        self.assertEqual(
            broker._canonical_restorable_sddl(f"O:SYG:SYD:AI{ace}"),
            f"O:SYG:SYD:AI{ace}",
        )
        self.assertEqual(
            broker._canonical_restorable_sddl(f"O:SYG:SYD:PARAI{ace}"),
            f"O:SYG:SYD:PAR{ace}",
        )

    def test_acl_reader_preserves_raw_and_canonical_descriptor_hashes(self) -> None:
        path = engine.normalize_binding("worktree", str(self.repository_root))
        raw_sddl = "O:SYG:SYD:PAI(A;;FA;;;SY)"
        canonical_sddl = "O:SYG:SYD:P(A;;FA;;;SY)"
        result = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                [{"path": path, "owner_sid": BROKER_SID, "sddl": raw_sddl}]
            ).encode("utf-8"),
            stderr=b"",
        )
        with mock.patch.object(
            broker, "_protected_acl_paths", return_value=[path]
        ), mock.patch.object(broker, "_run_powershell", return_value=result):
            descriptors = broker._read_protected_acl_descriptors({})
        self.assertEqual(descriptors[0]["raw_sddl"], raw_sddl)
        self.assertEqual(descriptors[0]["canonical_sddl"], canonical_sddl)
        self.assertEqual(
            descriptors[0]["raw_sddl_sha256"],
            hashlib.sha256(raw_sddl.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            descriptors[0]["canonical_sddl_sha256"],
            hashlib.sha256(canonical_sddl.encode("utf-8")).hexdigest(),
        )
        self.assertNotEqual(
            descriptors[0]["raw_sddl_sha256"],
            descriptors[0]["canonical_sddl_sha256"],
        )

    def test_acl_configuration_uses_native_protected_write_and_exact_verification(
        self,
    ) -> None:
        path = engine.normalize_binding("worktree", str(self.repository_root))
        inventory = [
            {"path": path, "object_type": "directory", "scope": "root"}
        ]
        captured: list[str] = []

        def run(script, _environment, *, input_bytes=None):
            captured.append(script)
            if len(captured) == 1:
                return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps({"object_count": 1}).encode("utf-8"),
                stderr=b"",
            )

        with mock.patch.object(
            broker, "_protected_acl_inventory", return_value=inventory
        ), mock.patch.object(broker, "_run_powershell", side_effect=run):
            broker._configure_protected_dacls(
                {},
                [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID],
                BROKER_SID,
            )

        self.assertEqual(len(captured), 2)
        configuration, verification = captured
        self.assertIn("public static class CcosNativeAclConfigure", configuration)
        self.assertIn("function Get-NativeSecurityPath", configuration)
        self.assertIn("(Get-NativeSecurityPath $path)", configuration)
        self.assertIn("[Convert]::ToUInt32('80000000', 16)", configuration)
        self.assertIn("$acl.SetAccessRuleProtection($true, $false)", configuration)
        self.assertNotIn(".SetAccessControl($acl)", configuration)
        self.assertIn("$matching.Count -ne 1", verification)
        self.assertIn("FileSystemRights -ne $required", verification)

    def test_acl_configuration_failure_retains_only_validated_bounded_diagnostic(
        self,
    ) -> None:
        path = engine.normalize_binding("worktree", str(self.repository_root))
        inventory = [
            {"path": path, "object_type": "directory", "scope": "root"}
        ]
        raw_diagnostic = {
            "protocol_version": (
                broker.PROTECTED_DACL_CONFIGURATION_FAILURE_PROTOCOL_VERSION
            ),
            "inventory_index": 0,
            "object_count": 1,
            "path": path,
            "path_sha256": hashlib.sha256(path.encode("utf-8")).hexdigest(),
            "object_type": "directory",
            "scope": "root",
            "error_code": "NATIVE_DACL_WRITE_FAILED",
            "native_error_code": 5,
            "message_sha256": "a" * 64,
        }
        stderr = json.dumps(raw_diagnostic, separators=(",", ":")).encode("utf-8")
        failed = subprocess.CompletedProcess(
            [], 85, stdout=b"", stderr=stderr
        )

        with mock.patch.object(
            broker, "_protected_acl_inventory", return_value=inventory
        ), mock.patch.object(broker, "_run_powershell", return_value=failed):
            with self.assertRaises(broker.BrokerAclConfigurationError) as raised:
                broker._configure_protected_dacls(
                    {},
                    [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID],
                    BROKER_SID,
                )

        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic["diagnostic_status"], "PARSED")
        self.assertEqual(diagnostic["path"], path)
        self.assertEqual(diagnostic["inventory_index"], 0)
        self.assertEqual(diagnostic["native_error_code"], 5)
        self.assertEqual(
            diagnostic["stderr_sha256"], hashlib.sha256(stderr).hexdigest()
        )
        self.assertNotIn(stderr.decode("utf-8"), str(raised.exception))

    def test_acl_configuration_rejects_untrusted_subprocess_diagnostic(self) -> None:
        path = engine.normalize_binding("worktree", str(self.repository_root))
        inventory = [
            {"path": path, "object_type": "directory", "scope": "root"}
        ]
        stderr = b'{"path":"C:/untrusted/escape"}\n'
        failed = subprocess.CompletedProcess([], 85, stdout=b"", stderr=stderr)

        with mock.patch.object(
            broker, "_protected_acl_inventory", return_value=inventory
        ), mock.patch.object(broker, "_run_powershell", return_value=failed):
            with self.assertRaises(broker.BrokerAclConfigurationError) as raised:
                broker._configure_protected_dacls(
                    {},
                    [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID],
                    BROKER_SID,
                )

        self.assertEqual(
            raised.exception.diagnostic["diagnostic_status"], "UNPARSEABLE"
        )
        self.assertNotIn("C:/untrusted/escape", raised.exception.diagnostic.values())

    def test_acl_restoration_failure_retains_only_signed_inventory_diagnostic(
        self,
    ) -> None:
        snapshot = self.protected_acl_snapshot()
        ordered = sorted(
            broker._normalize_acl_snapshot(snapshot),
            key=lambda item: (len(Path(item["path"]).parts), item["path"].casefold()),
        )
        expected = ordered[0]
        raw_diagnostic = {
            "protocol_version": (
                broker.PROTECTED_DACL_RESTORATION_FAILURE_PROTOCOL_VERSION
            ),
            "inventory_index": 0,
            "object_count": len(ordered),
            "path_sha256": hashlib.sha256(
                expected["path"].encode("utf-8")
            ).hexdigest(),
            "signed_entry_sha256": expected["entry_sha256"],
            "error_code": "NATIVE_SECURITY_DESCRIPTOR_WRITE_FAILED",
            "native_error_code": 5,
            "security_information": 0x20000004,
            "message_sha256": "a" * 64,
        }
        stderr = json.dumps(raw_diagnostic, separators=(",", ":")).encode("utf-8")
        failed = subprocess.CompletedProcess(
            [], 86, stdout=b"", stderr=stderr
        )

        with mock.patch.object(broker, "_run_powershell", return_value=failed):
            with self.assertRaises(broker.BrokerAclRestorationError) as raised:
                broker._restore_protected_acls(snapshot)

        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic["diagnostic_status"], "PARSED")
        self.assertEqual(diagnostic["inventory_index"], 0)
        self.assertEqual(diagnostic["path_sha256"], raw_diagnostic["path_sha256"])
        self.assertEqual(diagnostic["signed_entry_sha256"], expected["entry_sha256"])
        self.assertEqual(diagnostic["native_error_code"], 5)
        self.assertEqual(diagnostic["security_information"], 0x20000004)
        self.assertNotIn("path", diagnostic)
        self.assertNotIn("sddl", diagnostic)
        self.assertNotIn(expected["path"], json.dumps(diagnostic, sort_keys=True))
        self.assertNotIn(stderr.decode("utf-8"), str(raised.exception))

        pre_mask_diagnostic = {
            **raw_diagnostic,
            "error_code": "ACL_RESTORATION_EXCEPTION",
            "native_error_code": None,
            "security_information": 0,
        }
        pre_mask_failure = subprocess.CompletedProcess(
            [],
            86,
            stdout=b"",
            stderr=json.dumps(
                pre_mask_diagnostic, separators=(",", ":")
            ).encode("utf-8"),
        )
        with mock.patch.object(
            broker, "_run_powershell", return_value=pre_mask_failure
        ):
            with self.assertRaises(broker.BrokerAclRestorationError) as pre_mask:
                broker._restore_protected_acls(snapshot)
        self.assertEqual(pre_mask.exception.diagnostic["diagnostic_status"], "PARSED")
        self.assertEqual(pre_mask.exception.diagnostic["security_information"], 0)

    def test_acl_restoration_rejects_untrusted_subprocess_diagnostic(self) -> None:
        snapshot = self.protected_acl_snapshot()
        raw_diagnostic = {
            "protocol_version": (
                broker.PROTECTED_DACL_RESTORATION_FAILURE_PROTOCOL_VERSION
            ),
            "inventory_index": 0,
            "object_count": len(snapshot),
            "path_sha256": "f" * 64,
            "signed_entry_sha256": "e" * 64,
            "error_code": "NATIVE_SECURITY_DESCRIPTOR_WRITE_FAILED",
            "native_error_code": 5,
            "security_information": 0x20000004,
            "message_sha256": "d" * 64,
        }
        failed = subprocess.CompletedProcess(
            [],
            86,
            stdout=b"",
            stderr=json.dumps(raw_diagnostic, separators=(",", ":")).encode("utf-8"),
        )

        with mock.patch.object(broker, "_run_powershell", return_value=failed):
            with self.assertRaises(broker.BrokerAclRestorationError) as raised:
                broker._restore_protected_acls(snapshot)

        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic["diagnostic_status"], "UNPARSEABLE")
        self.assertNotIn("path_sha256", diagnostic)
        self.assertNotIn("signed_entry_sha256", diagnostic)

    def test_acl_restoration_rejects_forbidden_security_information(self) -> None:
        snapshot = self.protected_acl_snapshot()
        ordered = sorted(
            broker._normalize_acl_snapshot(snapshot),
            key=lambda item: (len(Path(item["path"]).parts), item["path"].casefold()),
        )
        expected = ordered[0]
        for invalid_mask in (
            0x2000000C,  # SACL bit present.
            0xA0000004,  # Protected and unprotected are both present.
            0x20000000,  # DACL bit is absent.
            0x80000008,  # SACL replaces the required DACL bit.
        ):
            with self.subTest(security_information=invalid_mask):
                raw_diagnostic = {
                    "protocol_version": (
                        broker.PROTECTED_DACL_RESTORATION_FAILURE_PROTOCOL_VERSION
                    ),
                    "inventory_index": 0,
                    "object_count": len(ordered),
                    "path_sha256": hashlib.sha256(
                        expected["path"].encode("utf-8")
                    ).hexdigest(),
                    "signed_entry_sha256": expected["entry_sha256"],
                    "error_code": "NATIVE_SECURITY_DESCRIPTOR_WRITE_FAILED",
                    "native_error_code": 5,
                    "security_information": invalid_mask,
                    "message_sha256": "a" * 64,
                }
                failed = subprocess.CompletedProcess(
                    [],
                    86,
                    stdout=b"",
                    stderr=json.dumps(
                        raw_diagnostic, separators=(",", ":")
                    ).encode("utf-8"),
                )
                with mock.patch.object(
                    broker, "_run_powershell", return_value=failed
                ):
                    with self.assertRaises(
                        broker.BrokerAclRestorationError
                    ) as raised:
                        broker._restore_protected_acls(snapshot)
                self.assertEqual(
                    raised.exception.diagnostic["diagnostic_status"],
                    "UNPARSEABLE",
                )

    def test_acl_restoration_rejects_control_bit_opposite_signed_sddl(self) -> None:
        snapshot = self.protected_acl_snapshot()
        ordered = sorted(
            broker._normalize_acl_snapshot(snapshot),
            key=lambda item: (len(Path(item["path"]).parts), item["path"].casefold()),
        )
        expected = ordered[0]
        self.assertNotIn("D:P", expected["sddl"])
        raw_diagnostic = {
            "protocol_version": (
                broker.PROTECTED_DACL_RESTORATION_FAILURE_PROTOCOL_VERSION
            ),
            "inventory_index": 0,
            "object_count": len(ordered),
            "path_sha256": hashlib.sha256(
                expected["path"].encode("utf-8")
            ).hexdigest(),
            "signed_entry_sha256": expected["entry_sha256"],
            "error_code": "NATIVE_SECURITY_DESCRIPTOR_WRITE_FAILED",
            "native_error_code": 5,
            "security_information": 0x80000004,
            "message_sha256": "a" * 64,
        }
        failed = subprocess.CompletedProcess(
            [],
            86,
            stdout=b"",
            stderr=json.dumps(raw_diagnostic, separators=(",", ":")).encode(
                "utf-8"
            ),
        )
        with mock.patch.object(broker, "_run_powershell", return_value=failed):
            with self.assertRaises(broker.BrokerAclRestorationError) as raised:
                broker._restore_protected_acls(snapshot)
        self.assertEqual(
            raised.exception.diagnostic["diagnostic_status"], "UNPARSEABLE"
        )

    @unittest.skipUnless(os.name == "nt", "Windows ACL integration test")
    def test_acl_restoration_diagnostic_runs_in_selected_windows_powershell(
        self,
    ) -> None:
        missing_path = engine.normalize_binding(
            "worktree", str(self.root / "missing-acl-restoration-target")
        )
        sddl = "O:SYG:SYD:(A;;FA;;;SY)"
        entry = {
            "path": missing_path,
            "owner_sid": "S-1-5-18",
            "sddl": sddl,
            "sddl_sha256": hashlib.sha256(sddl.encode("utf-8")).hexdigest(),
        }
        entry["entry_sha256"] = engine.canonical_json_sha256(entry)

        with self.assertRaises(broker.BrokerAclRestorationError) as raised:
            broker._restore_protected_acls([entry])

        diagnostic = raised.exception.diagnostic
        self.assertEqual(diagnostic["diagnostic_status"], "PARSED")
        self.assertEqual(diagnostic["error_code"], "ACL_RESTORATION_EXCEPTION")
        self.assertIsNone(diagnostic["native_error_code"])
        self.assertEqual(diagnostic["security_information"], 0)
        self.assertEqual(
            diagnostic["path_sha256"],
            hashlib.sha256(missing_path.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(missing_path, json.dumps(diagnostic, sort_keys=True))

    def test_cli_json_exposes_bounded_acl_restoration_diagnostic(self) -> None:
        diagnostic = {
            "protocol_version": (
                broker.PROTECTED_DACL_RESTORATION_FAILURE_PROTOCOL_VERSION
            ),
            "diagnostic_status": "PARSED",
            "inventory_index": 7,
            "object_count": 12,
            "path_sha256": "a" * 64,
            "signed_entry_sha256": "b" * 64,
            "error_code": "NATIVE_SECURITY_DESCRIPTOR_WRITE_FAILED",
            "native_error_code": 5,
            "security_information": 0x80000007,
            "message_sha256": "c" * 64,
            "process_exit_code": 86,
            "stderr_sha256": "d" * 64,
        }
        error = broker.BrokerAclRestorationError(diagnostic)
        with mock.patch.object(
            broker, "execute_grant", side_effect=error
        ), mock.patch("builtins.print") as printed:
            exit_code = broker.main(
                [
                    "--json",
                    "execute",
                    "--state-root",
                    str(self.state_root),
                    "--case-id",
                    self.case_id,
                    "--grant-id",
                    self.grant_id,
                    "--controller-receipt-json",
                    "{}",
                ]
            )

        self.assertEqual(exit_code, 2)
        payload = json.loads(printed.call_args.args[0])
        self.assertEqual(payload["error"], "BrokerAclRestorationError")
        self.assertEqual(payload["acl_restoration_diagnostic"], diagnostic)
        self.assertNotIn("path", payload["acl_restoration_diagnostic"])
        self.assertNotIn("sddl", payload["acl_restoration_diagnostic"])

    def test_acl_restore_applies_parents_before_children(self) -> None:
        captured = {}

        def run(_script, environment, *, input_bytes=None):
            captured.update(environment)
            captured["input_bytes"] = input_bytes
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        with mock.patch.object(broker, "_run_powershell", side_effect=run):
            broker._restore_protected_acls(self.protected_acl_snapshot())
        payload = json.loads(captured["input_bytes"])
        depths = [len(Path(item["path"]).parts) for item in payload]
        self.assertEqual(depths, sorted(depths))

    def test_acl_restore_uses_only_native_security_descriptor_write(self) -> None:
        captured = {}

        def run(script, _environment, *, input_bytes=None):
            captured["script"] = script
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        with mock.patch.object(broker, "_run_powershell", side_effect=run):
            broker._restore_protected_acls(self.protected_acl_snapshot())

        self.assertNotIn(".SetAccessControl", captured["script"])

    def test_acl_restore_native_write_uses_exact_selected_field_mask(self) -> None:
        captured = {}

        def run(script, _environment, *, input_bytes=None):
            captured["script"] = script
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        with mock.patch.object(broker, "_run_powershell", side_effect=run):
            broker._restore_protected_acls(self.protected_acl_snapshot())

        script = captured["script"]
        self.assertIn('EntryPoint = "SetFileSecurityW"', script)
        self.assertIn("[System.BitConverter]::ToString(", script)
        self.assertNotIn("[Convert]::ToHexString", script)
        self.assertIn("[uint32]$securityInformation = 0", script)
        self.assertIn("$securityInformation = [uint32]4", script)
        self.assertIn("[Convert]::ToUInt32('80000000', 16)", script)
        self.assertIn("[uint32]0x20000000", script)
        self.assertIn("$securityInformation -bor [uint32]0x1", script)
        self.assertIn("$securityInformation -bor [uint32]0x2", script)
        self.assertIn("[CcosNativeAclRestore]::SetFileSecurityW(", script)
        self.assertIn(
            "security_information = [uint64]$securityInformation", script
        )
        self.assertNotIn("SACL_SECURITY_INFORMATION", script)
        self.assertNotIn("AccessControlSections]::Audit", script)

    def test_acl_restore_uses_unsigned_protected_dacl_flag(self) -> None:
        captured = {}

        def run(script, _environment, *, input_bytes=None):
            captured["script"] = script
            captured["input_bytes"] = input_bytes
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        with mock.patch.object(broker, "_run_powershell", side_effect=run):
            broker._restore_protected_acls(self.protected_acl_snapshot())
        self.assertIn(
            "[Convert]::ToUInt32('80000000', 16)", captured["script"]
        )
        self.assertNotIn("[uint32]0x80000000", captured["script"])

    def test_acl_restore_passes_extended_length_path_to_native_write(self) -> None:
        captured = {}

        def run(script, _environment, *, input_bytes=None):
            captured["script"] = script
            captured["input_bytes"] = input_bytes
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        with mock.patch.object(broker, "_run_powershell", side_effect=run):
            broker._restore_protected_acls(self.protected_acl_snapshot())
        self.assertIn("function Get-NativeSecurityPath", captured["script"])
        self.assertIn(
            "(Get-NativeSecurityPath ([string]$item.path))", captured["script"]
        )

    def test_acl_restore_retries_from_mixed_original_and_lockdown_descriptors(self) -> None:
        self.issue()
        grant = self.grant()
        current = []
        for item in grant["protected_acl_snapshot"]:
            current.append(
                {
                    "path": item["path"],
                    "owner_sid": item["owner_sid"],
                    "raw_sddl": item["sddl"],
                    "raw_sddl_sha256": item["sddl_sha256"],
                    "canonical_sddl": item["sddl"],
                    "canonical_sddl_sha256": item["sddl_sha256"],
                }
            )
        lockdown_rule = grant["preissue_dacl_evidence"]["rules"][0]
        changed_path = lockdown_rule["path"]
        changed = next(item for item in current if item["path"] == changed_path)
        changed["owner_sid"] = BROKER_SID
        changed["raw_sddl"] = "fixture-lockdown-sddl"
        changed["raw_sddl_sha256"] = lockdown_rule["root_sddl_sha256"]
        changed["canonical_sddl"] = "fixture-canonical-lockdown-sddl"
        changed["canonical_sddl_sha256"] = hashlib.sha256(
            changed["canonical_sddl"].encode("utf-8")
        ).hexdigest()
        journal = broker.BrokerJournal(self.state_root, self.case_id, self.grant_id)
        inventory = [
            {"path": item["path"], "object_type": "file", "scope": "descendant"}
            for item in grant["protected_acl_snapshot"]
        ]
        sealed_paths = [
            self.state_root / engine.ACTION_ARTIFACT_DIRECTORY,
            self.state_root / engine.ACTION_ARTIFACT_DIRECTORY / self.case_id,
            self.state_root / grant["sealed_artifact_path"],
            self.state_root / grant["sealed_baseline_path"],
        ]
        inventory.extend(
            {
                "path": engine.normalize_binding("worktree", str(path)),
                "object_type": "file" if path.suffix == ".bin" else "directory",
                "scope": "descendant",
            }
            for path in sealed_paths
            if engine.normalize_binding("worktree", str(path))
            not in {item["path"] for item in inventory}
        )
        with mock.patch.object(
            broker,
            "_verify_protected_acl_restore",
            side_effect=[broker.BrokerAuthorizationError("mixed"), None],
        ), mock.patch.object(
            broker, "_protected_acl_inventory", return_value=inventory
        ), mock.patch.object(
            broker, "_read_protected_acl_descriptors", return_value=current
        ), mock.patch.object(
            broker, "_restore_protected_acls"
        ) as restore:
            result = broker._restore_acl_snapshot_after_lockdown(
                journal,
                run_id="mixed-restore-run",
                snapshot=grant["protected_acl_snapshot"],
                snapshot_sha256=grant["protected_acl_snapshot_sha256"],
                lockdown_dacl_evidence=grant["preissue_dacl_evidence"],
                restore_reason="startup_recovery",
            )
        self.assertTrue(result["restored"])
        restore.assert_called_once()

    def test_acl_recovery_rejects_signed_descendant_escape_before_restore(self) -> None:
        snapshot = [
            *self.protected_acl_snapshot(),
            self.acl_snapshot_entry(
                self.repository_root.parent / "outside-target-root.txt",
                "signed-escape",
            ),
        ]
        snapshot.sort(
            key=lambda item: (len(Path(item["path"]).parts), item["path"].casefold()),
            reverse=True,
        )
        snapshot_sha256 = engine.canonical_json_sha256(snapshot)
        journal = broker.BrokerJournal(self.state_root, self.case_id, self.grant_id)
        run_id = "signed-escape-recovery"
        journal.append(
            "ACL_SNAPSHOT",
            run_id,
            protected_acl_snapshot=snapshot,
            protected_acl_snapshot_sha256=snapshot_sha256,
        )
        roots = {
            "target_root": engine.normalize_binding("worktree", str(self.repository_root)),
            "state_root": engine.normalize_binding("worktree", str(self.state_root)),
            "broker_source_root": engine.normalize_binding("worktree", str(ROOT)),
            "proposal_root": engine.normalize_binding("worktree", str(self.proposal_root)),
        }
        intent = {
            "roots": roots,
            "denied_principal_sids": [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID],
            "broker_principal_sid": BROKER_SID,
        }
        journal.append(
            "ACL_LOCKDOWN_INTENT",
            run_id,
            protected_acl_snapshot_sha256=snapshot_sha256,
            lockdown_intent=intent,
            lockdown_intent_sha256=engine.canonical_json_sha256(intent),
        )

        with mock.patch.object(broker, "_protected_acl_inventory") as inventory, mock.patch.object(
            broker, "_restore_protected_acls"
        ) as restore:
            with self.assertRaisesRegex(broker.BrokerAuthorizationError, "escape"):
                broker._restore_acl_snapshot_after_lockdown(
                    journal,
                    run_id="signed-escape-attempt",
                    snapshot=snapshot,
                    snapshot_sha256=snapshot_sha256,
                    lockdown_dacl_evidence=None,
                    restore_reason="startup_recovery",
                )
        inventory.assert_not_called()
        restore.assert_not_called()

    def test_acl_recovery_rejects_current_inventory_addition_and_deletion(self) -> None:
        snapshot = self.protected_acl_snapshot()
        snapshot_sha256 = engine.canonical_json_sha256(snapshot)
        journal = broker.BrokerJournal(self.state_root, self.case_id, self.grant_id)
        run_id = "inventory-drift-recovery"
        journal.append(
            "ACL_SNAPSHOT",
            run_id,
            protected_acl_snapshot=snapshot,
            protected_acl_snapshot_sha256=snapshot_sha256,
        )
        roots = {
            "target_root": engine.normalize_binding("worktree", str(self.repository_root)),
            "state_root": engine.normalize_binding("worktree", str(self.state_root)),
            "broker_source_root": engine.normalize_binding("worktree", str(ROOT)),
            "proposal_root": engine.normalize_binding("worktree", str(self.proposal_root)),
        }
        intent = {
            "roots": roots,
            "denied_principal_sids": [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID],
            "broker_principal_sid": BROKER_SID,
        }
        journal.append(
            "ACL_LOCKDOWN_INTENT",
            run_id,
            protected_acl_snapshot_sha256=snapshot_sha256,
            lockdown_intent=intent,
            lockdown_intent_sha256=engine.canonical_json_sha256(intent),
        )
        baseline_inventory = [
            {"path": item["path"], "object_type": "file", "scope": "descendant"}
            for item in snapshot
        ]
        drifted_inventories = {
            "addition": [
                *baseline_inventory,
                {
                    "path": engine.normalize_binding(
                        "worktree", str(self.repository_root / "unexpected.bin")
                    ),
                    "object_type": "file",
                    "scope": "descendant",
                },
            ],
            "deletion": baseline_inventory[1:],
        }

        for drift, current_inventory in drifted_inventories.items():
            with self.subTest(drift=drift), mock.patch.object(
                broker, "_protected_acl_inventory", return_value=current_inventory
            ), mock.patch.object(broker, "_restore_protected_acls") as restore:
                with self.assertRaisesRegex(
                    broker.BrokerAuthorizationError, "current ACL inventory differs"
                ):
                    broker._restore_acl_snapshot_after_lockdown(
                        journal,
                        run_id=f"inventory-{drift}-attempt",
                        snapshot=snapshot,
                        snapshot_sha256=snapshot_sha256,
                        lockdown_dacl_evidence=None,
                        restore_reason="startup_recovery",
                    )
                restore.assert_not_called()

    def test_native_probe_directory_inherits_windows_acl(self) -> None:
        directory = mock.Mock()
        with mock.patch.object(broker.os, "name", "nt"):
            broker._create_native_operation_probe_directory(directory)
        directory.mkdir.assert_called_once_with()

    def test_native_probe_directory_remains_owner_only_on_posix(self) -> None:
        directory = mock.Mock()
        with mock.patch.object(broker.os, "name", "posix"):
            broker._create_native_operation_probe_directory(directory)
        directory.mkdir.assert_called_once_with(mode=0o700)

    @unittest.skipUnless(os.name == "nt", "Windows read-only directory cleanup")
    def test_native_probe_cleanup_clears_windows_readonly_directory(self) -> None:
        directory = self.worker_runtime_root / "readonly-native-probe"
        directory.mkdir()
        directory.chmod(broker.stat.S_IREAD)
        paths = {
            "directory": directory,
            "delete": directory / "delete-anchor.bin",
            "rename_source": directory / "rename-source.bin",
            "rename_destination": directory / "rename-destination.bin",
            "replace_target": directory / "replace-target.bin",
            "replace_source": directory / "replace-source.bin",
            "replace_backup": directory / "replace-backup.bin",
        }

        broker._cleanup_native_operation_anchors(paths)

        self.assertFalse(directory.exists())

    def test_native_probe_cleanup_does_not_retry_other_permission_denials(self) -> None:
        path = mock.Mock()
        path.rmdir.side_effect = PermissionError("denied")
        path.stat.return_value.st_file_attributes = 0
        with mock.patch.object(broker.os, "name", "nt"):
            with self.assertRaises(PermissionError):
                broker._remove_native_operation_probe_path(path, directory=True)
        path.chmod.assert_not_called()
        self.assertEqual(path.rmdir.call_count, 1)

    def test_native_probe_cleanup_rejects_reparse_directory_before_removal(self) -> None:
        directory = self.worker_runtime_root / "reparse-native-probe"
        paths = {
            "directory": directory,
            "delete": directory / "delete-anchor.bin",
            "rename_source": directory / "rename-source.bin",
            "rename_destination": directory / "rename-destination.bin",
            "replace_target": directory / "replace-target.bin",
            "replace_source": directory / "replace-source.bin",
            "replace_backup": directory / "replace-backup.bin",
        }
        with mock.patch.object(
            broker.os.path,
            "lexists",
            side_effect=lambda path: path == directory,
        ), mock.patch.object(
            broker,
            "_acl_object_type",
            side_effect=broker.BrokerAuthorizationError("reparse point"),
        ), mock.patch.object(
            broker, "_remove_native_operation_probe_path"
        ) as remove:
            with self.assertRaisesRegex(broker.BrokerAuthorizationError, "reparse"):
                broker._cleanup_native_operation_anchors(paths)
        remove.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows kernel API probe")
    def test_native_delete_rename_and_replace_calls_run_on_sacrificial_anchors(self) -> None:
        root = self.worker_runtime_root / "native-api-probe"
        nested = root / "nested"
        nested.mkdir(parents=True)
        for operation in ("delete", "rename", "replace"):
            challenge = f"native-{operation}-probe"
            paths = broker._prepare_native_operation_anchors(
                root, "nested", challenge, "target_root"
            )
            try:
                error, code = broker._attempt_denied_file_capability(
                    root, "nested", challenge, "target_root", operation
                )
                self.assertEqual(error, f"{operation.upper()}_CAPABILITY_GRANTED")
                self.assertEqual(code, 0)
            finally:
                broker._cleanup_native_operation_anchors(paths)

    def test_worker_probe_attempts_root_nested_anchor_acl_owner_and_delete_boundaries(self) -> None:
        request = {
            "protocol_version": broker.WORKER_PROBE_REQUEST_PROTOCOL_VERSION,
            "schema_version": 1,
            "challenge_id": "live-worker-boundary-probe",
            "worker_principal_sid": WORKER_SID,
            "sandbox_group_principal_sid": SANDBOX_GROUP_SID,
            "broker_principal_sid": BROKER_SID,
            "protected_roots": self.protected_root_requests(),
            "base_head": self.head,
            "target_path": TARGET_PATH,
            "expected_status_sha256": engine.EMPTY_SHA256,
        }
        writes: list[str] = []
        overwrites: list[Path] = []
        acl_operations: list[tuple[str, str]] = []
        capability_operations: list[tuple[Path, str]] = []
        hard_links: list[tuple[Path, Path, str]] = []

        def denied_write(_root, relative):
            writes.append(relative)
            return "ACCESS_DENIED", 5, True, True

        def denied_overwrite(anchor):
            overwrites.append(anchor)
            return "ACCESS_DENIED", 5

        def denied_acl(_root, operation, _worker, nonce):
            acl_operations.append((operation, nonce))
            return "ACCESS_DENIED", 5

        def denied_capability(root, _nested, _challenge, _kind, operation):
            capability_operations.append((root, operation))
            return "ACCESS_DENIED", 5

        def denied_hard_link(anchor, root, relative):
            hard_links.append((anchor, root, relative))
            return "ACCESS_DENIED", 5, True, True

        with mock.patch.object(
            broker, "windows_identity", return_value=("fixture\\worker", WORKER_SID)
        ), mock.patch.object(
            broker, "windows_group_sids", return_value=[SANDBOX_GROUP_SID]
        ), mock.patch.object(
            broker, "_attempt_denied_write", side_effect=denied_write
        ), mock.patch.object(
            broker, "_attempt_denied_overwrite", side_effect=denied_overwrite
        ), mock.patch.object(
            broker, "_attempt_denied_acl_operation", side_effect=denied_acl
        ), mock.patch.object(
            broker, "_attempt_denied_file_capability", side_effect=denied_capability
        ), mock.patch.object(
            broker, "_attempt_denied_hard_link", side_effect=denied_hard_link
        ), mock.patch.object(
            broker,
            "_probe_regular_file_identity",
            side_effect=lambda path, stop=None: {
                "identity_sha256": "7" * 64,
                "hardlink_paths": [str(path.resolve(strict=True))],
                "transport_hardlink_paths": [],
            },
        ), mock.patch.object(
            broker, "_acl_sddl_sha256", return_value="b" * 64
        ):
            evidence = broker.worker_isolation_probe(request)
        self.assertEqual(len(writes), len(engine.PROTECTED_ROOT_KINDS) * 2)
        self.assertEqual(len(overwrites), len(engine.PROTECTED_ROOT_KINDS))
        self.assertEqual(
            {path.resolve() for path in overwrites},
            {
                (self.repository_root / TARGET_PATH).resolve(),
                (self.state_root / engine.STORE_FILENAME).resolve(),
                (ROOT / "install-bundle.manifest.json").resolve(),
                self.proposal.resolve(),
            },
        )
        self.assertEqual(len(capability_operations), len(engine.PROTECTED_ROOT_KINDS) * 3)
        self.assertEqual(
            {operation for _anchor, operation in capability_operations},
            {"replace", "rename", "delete"},
        )
        self.assertEqual(len(hard_links), len(engine.PROTECTED_ROOT_KINDS))
        self.assertEqual(len(acl_operations), len(engine.PROTECTED_ROOT_KINDS) * 2)
        self.assertEqual({name for name, _nonce in acl_operations}, {"change_permissions", "take_ownership"})
        self.assertTrue(all(item["overwrite_denial_error"] == "ACCESS_DENIED" for item in evidence["protected_roots"]))
        self.assertTrue(all(item["nested_write_denial_error"] == "ACCESS_DENIED" for item in evidence["protected_roots"]))
        self.assertTrue(
            all(
                item["anchor_hardlink_paths_before"]
                == item["anchor_hardlink_paths_after"]
                and len(item["anchor_hardlink_paths_before"]) == 1
                and item["anchor_transport_hardlink_paths_before"] == []
                and item["anchor_transport_hardlink_paths_after"] == []
                for item in evidence["protected_roots"]
            )
        )

    @unittest.skipUnless(os.name == "nt", "Windows hardlink identity is required")
    def test_probe_identity_allows_only_one_exact_transport_hardlink(self) -> None:
        anchor = (self.repository_root / TARGET_PATH).resolve(strict=True)
        canonical = str(anchor)[2:]
        native_identity = (123, 456, 2, anchor.stat().st_size)
        allowed = [canonical, r"\Work\.tmp.driveupload\12345"]
        absolute_allowed = sorted(
            engine.normalize_binding("worktree", str(anchor)[:2] + item)
            for item in allowed
        )
        with mock.patch.object(
            broker, "_windows_regular_file_identity", return_value=native_identity
        ), mock.patch.object(
            broker, "_windows_hardlink_paths", return_value=allowed
        ):
            identity = broker._probe_regular_file_identity(
                anchor, stop=self.repository_root
            )
        self.assertEqual(identity["number_of_links"], 2)
        self.assertEqual(identity["hardlink_paths"], absolute_allowed)
        self.assertEqual(
            identity["transport_hardlink_paths"],
            [
                engine.normalize_binding(
                    "worktree",
                    str(anchor)[:2] + r"\Work\.tmp.driveupload\12345",
                )
            ],
        )
        self.assertEqual(
            identity["identity_sha256"],
            engine.canonical_json_sha256({
                "volume_id": 123,
                "file_id": 456,
                "size": anchor.stat().st_size,
            }),
        )
        with mock.patch.object(
            broker,
            "_windows_regular_file_identity",
            return_value=(123, 456, 1, anchor.stat().st_size),
        ), mock.patch.object(
            broker, "_windows_hardlink_paths", return_value=[canonical]
        ):
            canonical_only = broker._probe_regular_file_identity(
                anchor, stop=self.repository_root
            )
        self.assertEqual(
            canonical_only["hardlink_paths"],
            [engine.normalize_binding("worktree", str(anchor))],
        )
        self.assertEqual(canonical_only["transport_hardlink_paths"], [])

    @unittest.skipUnless(os.name == "nt", "Windows hardlink identity is required")
    def test_probe_identity_rejects_every_other_hardlink_shape(self) -> None:
        anchor = (self.repository_root / TARGET_PATH).resolve(strict=True)
        canonical = str(anchor)[2:]
        cases = (
            (
                (123, 456, 2, anchor.stat().st_size),
                [canonical, r"\Temp\outside-transport"],
            ),
            (
                (123, 456, 3, anchor.stat().st_size),
                [
                    canonical,
                    r"\Work\.tmp.driveupload\12345",
                    r"\Work\.tmp.driveupload\67890",
                ],
            ),
            (
                (123, 456, 2, anchor.stat().st_size),
                [canonical],
            ),
            (
                (123, 456, 1, anchor.stat().st_size),
                [r"\Work\.tmp.driveupload\12345"],
            ),
            (
                (123, 456, 2, anchor.stat().st_size),
                [canonical, canonical],
            ),
        )
        for native_identity, paths in cases:
            with self.subTest(paths=paths), mock.patch.object(
                broker, "_windows_regular_file_identity", return_value=native_identity
            ), mock.patch.object(
                broker, "_windows_hardlink_paths", return_value=paths
            ):
                with self.assertRaisesRegex(
                    broker.BrokerAuthorizationError, "hardlink"
                ):
                    broker._probe_regular_file_identity(
                        anchor, stop=self.repository_root
                    )

    @unittest.skipUnless(os.name == "nt", "Windows hardlink identity is required")
    def test_probe_identity_rejects_unstable_or_zero_native_identity(self) -> None:
        anchor = (self.repository_root / TARGET_PATH).resolve(strict=True)
        canonical = str(anchor)[2:]
        with mock.patch.object(
            broker,
            "_windows_regular_file_identity",
            side_effect=[
                (123, 456, 1, anchor.stat().st_size),
                (123, 789, 1, anchor.stat().st_size),
            ],
        ), mock.patch.object(
            broker, "_windows_hardlink_paths", return_value=[canonical]
        ):
            with self.assertRaisesRegex(
                broker.BrokerAuthorizationError, "identity changed"
            ):
                broker._probe_regular_file_identity(anchor, stop=self.repository_root)
        with mock.patch.object(
            broker,
            "_windows_regular_file_identity",
            return_value=(123, 0, 1, anchor.stat().st_size),
        ), mock.patch.object(
            broker, "_windows_hardlink_paths", return_value=[canonical]
        ):
            with self.assertRaisesRegex(
                broker.BrokerAuthorizationError, "nonzero"
            ):
                broker._probe_regular_file_identity(anchor, stop=self.repository_root)

    def test_hardlink_enumerator_fails_closed_on_timeout_or_malformed_output(self) -> None:
        anchor = (self.repository_root / TARGET_PATH).resolve(strict=True)
        executable = r"C:\Windows\System32\fsutil.exe"
        with mock.patch.object(
            broker, "resolved_executable", return_value=executable
        ), mock.patch.object(
            broker, "safe_subprocess_environment", return_value={}
        ), mock.patch.object(
            broker.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired([executable], 30),
        ):
            with self.assertRaisesRegex(broker.BrokerPreflightError, "timed out"):
                broker._windows_hardlink_paths(anchor)
        malformed = subprocess.CompletedProcess(
            [executable], 0, stdout=b"\xff", stderr=b""
        )
        with mock.patch.object(
            broker, "resolved_executable", return_value=executable
        ), mock.patch.object(
            broker, "safe_subprocess_environment", return_value={}
        ), mock.patch.object(broker.subprocess, "run", return_value=malformed):
            with self.assertRaisesRegex(broker.BrokerPreflightError, "valid UTF-8"):
                broker._windows_hardlink_paths(anchor)

    def test_probe_identity_helper_cannot_reach_action_paths(self) -> None:
        tree = ast.parse(Path(broker.__file__).read_text(encoding="utf-8"))
        calls: dict[str, dict[str, int]] = {}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names: dict[str, int] = {}
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    names[child.func.id] = names.get(child.func.id, 0) + 1
            calls[node.name] = names
        private_callers = {
            name: counts.get("_probe_regular_file_identity", 0)
            for name, counts in calls.items()
            if counts.get("_probe_regular_file_identity", 0)
        }
        self.assertEqual(private_callers, {"worker_isolation_probe": 2})
        for action_function in (
            "_verify_original_proposal",
            "_sealed_path",
            "_repository_target",
            "_atomic_replace",
            "_execute_grant",
        ):
            self.assertGreater(
                calls[action_function].get("regular_file_identity", 0), 0
            )

    def test_worker_probe_fails_closed_for_every_mutation_family(self) -> None:
        request = {
            "protocol_version": broker.WORKER_PROBE_REQUEST_PROTOCOL_VERSION,
            "schema_version": 1,
            "challenge_id": "fail-closed-worker-boundary-probe",
            "worker_principal_sid": WORKER_SID,
            "sandbox_group_principal_sid": SANDBOX_GROUP_SID,
            "broker_principal_sid": BROKER_SID,
            "protected_roots": self.protected_root_requests(),
            "base_head": self.head,
            "target_path": TARGET_PATH,
            "expected_status_sha256": engine.EMPTY_SHA256,
        }

        def nested_write_override():
            calls = 0

            def result(_root, _relative):
                nonlocal calls
                calls += 1
                if calls == 2:
                    return "WRITE_SUCCEEDED", 0, True, True
                return "ACCESS_DENIED", 5, True, True

            return {"_attempt_denied_write": result}

        def capability_override(operation: str):
            def result(_root, _nested, _challenge, _kind, observed):
                if observed == operation:
                    return f"{operation.upper()}_CAPABILITY_GRANTED", 0
                return "ACCESS_DENIED", 5

            return {"_attempt_denied_file_capability": result}

        def acl_override(operation: str):
            def result(_root, observed, _worker, _nonce):
                if observed == operation:
                    return "ACL_OPERATION_SUCCEEDED", 0
                return "ACCESS_DENIED", 5

            return {"_attempt_denied_acl_operation": result}

        cases = [
            (
                "root write",
                lambda: {"_attempt_denied_write": lambda *_: ("WRITE_SUCCEEDED", 0, True, True)},
                "root write capability",
            ),
            ("nested write", nested_write_override, "nested write capability"),
            (
                "overwrite",
                lambda: {"_attempt_denied_overwrite": lambda *_: ("OVERWRITE_SUCCEEDED", 0)},
                "overwrite capability",
            ),
            ("replace", lambda: capability_override("replace"), "replace capability"),
            ("rename", lambda: capability_override("rename"), "rename capability"),
            ("delete", lambda: capability_override("delete"), "delete capability"),
            (
                "hard link",
                lambda: {"_attempt_denied_hard_link": lambda *_: ("HARD_LINK_SUCCEEDED", 0, True, True)},
                "hard-link capability",
            ),
            (
                "permissions",
                lambda: acl_override("change_permissions"),
                "permission-change capability",
            ),
            (
                "ownership",
                lambda: acl_override("take_ownership"),
                "ownership-change capability",
            ),
        ]
        defaults = {
            "_attempt_denied_write": lambda *_: ("ACCESS_DENIED", 5, True, True),
            "_attempt_denied_overwrite": lambda *_: ("ACCESS_DENIED", 5),
            "_attempt_denied_file_capability": lambda *_: ("ACCESS_DENIED", 5),
            "_attempt_denied_hard_link": lambda *_: ("ACCESS_DENIED", 5, True, True),
            "_attempt_denied_acl_operation": lambda *_: ("ACCESS_DENIED", 5),
        }
        for label, override_factory, message in cases:
            with self.subTest(boundary=label), ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    broker, "windows_identity", return_value=("fixture\\worker", WORKER_SID)
                ))
                stack.enter_context(mock.patch.object(
                    broker, "windows_group_sids", return_value=[SANDBOX_GROUP_SID]
                ))
                overrides = override_factory()
                for name, default in defaults.items():
                    stack.enter_context(mock.patch.object(
                        broker, name, side_effect=overrides.get(name, default)
                    ))
                stack.enter_context(mock.patch.object(
                    broker, "_probe_regular_file_identity",
                    side_effect=lambda path, stop=None: {
                        "identity_sha256": "7" * 64,
                        "hardlink_paths": [str(path.resolve(strict=True))],
                        "transport_hardlink_paths": [],
                    },
                ))
                stack.enter_context(mock.patch.object(
                    broker, "_acl_sddl_sha256", return_value="b" * 64
                ))
                with self.assertRaisesRegex(
                    broker.BrokerAuthorizationError, message
                ):
                    broker.worker_isolation_probe(request)

    def test_git_and_powershell_helpers_receive_only_minimal_secret_free_environments(self) -> None:
        sentinels = {
            broker.CONTROLLER_KEY_ENVIRONMENT: "controller-key-must-not-flow",
            "SENTINEL_API_TOKEN": "token-must-not-flow",
            "SENTINEL_PASSWORD": "password-must-not-flow",
        }
        git_call: dict = {}

        def fake_git_run(command, **kwargs):
            git_call.update(command=command, kwargs=kwargs)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with mock.patch.dict(os.environ, sentinels, clear=False), mock.patch.object(
            engine,
            "resolved_executable",
            return_value=str(Path(sys.executable).resolve(strict=True)),
        ), mock.patch.object(engine.subprocess, "run", side_effect=fake_git_run):
            engine._run_git(self.repository_root, "status", "--porcelain=v1")
        git_environment = git_call["kwargs"]["env"]
        for name in sentinels:
            self.assertNotIn(name, git_environment)
        self.assertEqual(git_call["command"][0], str(Path(sys.executable).resolve()))
        self.assertIn(
            f"safe.directory={self.repository_root.resolve()}", git_call["command"]
        )

        powershell_call: dict = {}

        def fake_powershell_run(command, **kwargs):
            powershell_call.update(command=command, kwargs=kwargs)
            return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

        with mock.patch.dict(os.environ, sentinels, clear=False), mock.patch.object(
            broker,
            "_powershell_executable",
            return_value=str(Path(sys.executable).resolve(strict=True)),
        ), mock.patch.object(broker.subprocess, "run", side_effect=fake_powershell_run):
            broker._run_powershell("Write-Output ok", {"CCOS_WORKER_SID": WORKER_SID})
        powershell_environment = powershell_call["kwargs"]["env"]
        for name in sentinels:
            self.assertNotIn(name, powershell_environment)
        self.assertEqual(powershell_call["command"][0], str(Path(sys.executable).resolve()))

    def test_dacl_inspection_rejects_split_or_propagation_escape_denials(self) -> None:
        roots = {
            "target_root": engine.normalize_binding("worktree", str(self.repository_root)),
            "state_root": engine.normalize_binding("worktree", str(self.state_root)),
            "broker_source_root": engine.normalize_binding("worktree", str(ROOT)),
            "proposal_root": engine.normalize_binding("worktree", str(self.proposal_root)),
        }

        def records(inheritable_mask: int, propagation_flags: list[str]) -> list[dict]:
            return [
                {
                    "root_kind": kind,
                    "principal_sid": principal_sid,
                    "deny_source_sids": sorted({principal_sid, SANDBOX_GROUP_SID}),
                    "path": path,
                    "owner_sid": BROKER_SID,
                    "parent_path": engine.normalize_binding("worktree", str(Path(path).parent)),
                    "parent_owner_sid": BROKER_SID,
                    "root_sddl": "synthetic-root-sddl",
                    "parent_sddl": "synthetic-parent-sddl",
                    "rights_mask": engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
                    "inheritable_rights_mask": inheritable_mask,
                    "parent_rights_mask": engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
                    "propagation_flags": propagation_flags,
                }
                for kind, path in roots.items()
                for principal_sid in [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID]
            ]

        split = subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(records(2, ["NONE"])).encode(), stderr=b""
        )
        with mock.patch.object(
            broker, "_verify_protected_object_dacls"
        ), mock.patch.object(broker, "_run_powershell", return_value=split):
            with self.assertRaisesRegex(broker.BrokerAuthorizationError, "full recursive"):
                broker.inspect_protected_dacls(
                    roots,
                    [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID],
                    BROKER_SID,
                    "c" * 64,
                )
        escaped = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                records(
                    engine.WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
                    ["INHERITONLY", "NONE"],
                )
            ).encode(),
            stderr=b"",
        )
        with mock.patch.object(
            broker, "_verify_protected_object_dacls"
        ), mock.patch.object(broker, "_run_powershell", return_value=escaped):
            with self.assertRaisesRegex(broker.BrokerAuthorizationError, "InheritOnly"):
                broker.inspect_protected_dacls(
                    roots,
                    [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID],
                    BROKER_SID,
                    "c" * 64,
                )

    @unittest.skipUnless(os.name == "nt", "Windows ACL integration test")
    def test_acl_operation_probe_succeeds_on_writable_control_root(self) -> None:
        worker_sid = broker.windows_identity()[1]
        with tempfile.TemporaryDirectory(prefix="ccos-acl-probe-control-") as temporary:
            result = broker._attempt_denied_acl_operation(
                Path(temporary),
                "change_permissions",
                worker_sid,
                "a" * 64,
            )
            self.assertEqual(result, ("ACL_OPERATION_SUCCEEDED", 0))

    @unittest.skipUnless(os.name == "nt", "Windows ACL integration test")
    def test_dacl_configuration_works_without_sacl_privilege(self) -> None:
        denied = [
            "S-1-5-21-444444444-555555555-666666666-2101",
            "S-1-5-21-444444444-555555555-666666666-2102",
            "S-1-5-21-444444444-555555555-666666666-2103",
        ]
        broker_sid = broker.windows_identity()[1]
        with tempfile.TemporaryDirectory(prefix="ccos-acl-no-sacl-") as temporary:
            base = Path(temporary)
            roots: dict[str, str] = {}
            for kind in engine.PROTECTED_ROOT_KINDS:
                root = base / f"{kind}-parent" / "root"
                root.mkdir(parents=True)
                nested = root / "existing" / "deep"
                nested.mkdir(parents=True)
                (nested / "existing.bin").write_bytes(kind.encode("ascii"))
                roots[kind] = engine.normalize_binding("worktree", str(root))
            snapshot = broker._snapshot_protected_acls(roots)
            self.assertGreater(
                len(snapshot), len(engine.PROTECTED_ROOT_KINDS) * 2
            )
            broker._configure_protected_dacls(roots, denied, broker_sid)
            evidence = broker.inspect_protected_dacls(
                roots,
                denied,
                broker_sid,
                "c" * 64,
            )
            self.assertEqual(
                len(evidence["rules"]),
                len(engine.PROTECTED_ROOT_KINDS) * len(denied),
            )
            broker._restore_protected_acls(snapshot)
            broker._verify_protected_acl_restore(snapshot)

    @unittest.skipUnless(os.name == "nt", "Windows ACL integration test")
    def test_dacl_refresh_canonicalizes_new_inherited_long_artifacts(self) -> None:
        denied = [
            "S-1-5-21-444444444-555555555-666666666-2151",
            "S-1-5-21-444444444-555555555-666666666-2152",
            "S-1-5-21-444444444-555555555-666666666-2153",
        ]
        broker_sid = broker.windows_identity()[1]
        with tempfile.TemporaryDirectory(
            prefix="ccos-acl-post-issuance-"
        ) as temporary:
            base = Path(temporary)
            roots: dict[str, str] = {}
            root_paths: dict[str, Path] = {}
            for kind in engine.PROTECTED_ROOT_KINDS:
                root = base / f"{kind}-parent" / "root"
                root.mkdir(parents=True)
                (root / "existing.bin").write_bytes(kind.encode("ascii"))
                roots[kind] = engine.normalize_binding("worktree", str(root))
                root_paths[kind] = root

            baseline = broker._snapshot_protected_acls(roots)
            artifact_directories: list[Path] = []
            artifact_files: list[Path] = []
            try:
                broker._configure_protected_dacls(roots, denied, broker_sid)
                artifact_root = (
                    root_paths["state_root"] / "canonical" / "action-artifacts"
                )
                case_directory = artifact_root / str(uuid.uuid4())
                case_directory.mkdir(parents=True)
                artifact_directories = [
                    case_directory,
                    artifact_root,
                    artifact_root.parent,
                ]
                file_name_length = 303 - len(str(case_directory)) - 1
                self.assertGreater(file_name_length, 32)
                self.assertLessEqual(file_name_length, 255)
                for label in ("replacement-", "baseline-"):
                    fill = file_name_length - len(label) - len(".bin")
                    self.assertGreater(fill, 0)
                    artifact = case_directory / (
                        label + ("x" * fill) + ".bin"
                    )
                    artifact.write_bytes(label.encode("ascii"))
                    self.assertEqual(len(str(artifact)), 303)
                    artifact_files.append(artifact)

                broker._configure_protected_dacls(roots, denied, broker_sid)
                first = {
                    item["path"]: item["sddl"]
                    for item in broker._snapshot_protected_acls(roots)
                }
                broker._configure_protected_dacls(roots, denied, broker_sid)
                second = {
                    item["path"]: item["sddl"]
                    for item in broker._snapshot_protected_acls(roots)
                }
                self.assertEqual(second, first)
            finally:
                for artifact in artifact_files:
                    if artifact.exists():
                        artifact.unlink()
                for directory in artifact_directories:
                    if directory.exists():
                        directory.rmdir()
                broker._restore_protected_acls(baseline)
                broker._verify_protected_acl_restore(baseline)

    @unittest.skipUnless(os.name == "nt", "Windows ACL integration test")
    def test_acl_restore_reapplies_protected_dacl_control_flag(self) -> None:
        denied = [
            "S-1-5-21-444444444-555555555-666666666-2201",
            "S-1-5-21-444444444-555555555-666666666-2202",
            "S-1-5-21-444444444-555555555-666666666-2203",
        ]
        broker_sid = broker.windows_identity()[1]
        with tempfile.TemporaryDirectory(prefix="ccos-acl-protected-restore-") as temporary:
            base = Path(temporary)
            roots: dict[str, str] = {}
            for kind in engine.PROTECTED_ROOT_KINDS:
                root = base / f"{kind}-parent" / "root"
                root.mkdir(parents=True)
                (root / "existing.bin").write_bytes(kind.encode("ascii"))
                roots[kind] = engine.normalize_binding("worktree", str(root))
            baseline = broker._snapshot_protected_acls(roots)
            try:
                broker._configure_protected_dacls(roots, denied, broker_sid)
                protected = broker._snapshot_protected_acls(roots)
                self.assertTrue(any("D:P" in item["sddl"] for item in protected))
                broker._restore_protected_acls(baseline)
                broker._verify_protected_acl_restore(baseline)
                broker._restore_protected_acls(protected)
                broker._verify_protected_acl_restore(protected)
            finally:
                broker._restore_protected_acls(baseline)
                broker._verify_protected_acl_restore(baseline)

    @unittest.skipUnless(os.name == "nt", "Windows ACL integration test")
    def test_acl_restore_supports_extended_length_descendant_path(self) -> None:
        denied = [
            "S-1-5-21-444444444-555555555-666666666-2301",
            "S-1-5-21-444444444-555555555-666666666-2302",
            "S-1-5-21-444444444-555555555-666666666-2303",
        ]
        broker_sid = broker.windows_identity()[1]
        with tempfile.TemporaryDirectory(prefix="ccos-acl-long-restore-") as temporary:
            base = Path(temporary)
            roots: dict[str, str] = {}
            root_paths: dict[str, Path] = {}
            for kind in engine.PROTECTED_ROOT_KINDS:
                root = base / f"{kind}-parent" / "root"
                root.mkdir(parents=True)
                (root / "existing.bin").write_bytes(kind.encode("ascii"))
                roots[kind] = engine.normalize_binding("worktree", str(root))
                root_paths[kind] = root

            long_directory = root_paths[engine.PROTECTED_ROOT_KINDS[0]]
            segment_number = 0
            while len(str(long_directory / "restore-target.bin")) <= 270:
                long_directory /= f"segment-{segment_number:02d}-" + ("x" * 24)
                segment_number += 1
            long_directory.mkdir(parents=True)
            long_target = long_directory / "restore-target.bin"
            long_target.write_bytes(b"extended-length ACL restore\n")
            self.assertGreater(len(str(long_target)), 260)

            baseline = broker._snapshot_protected_acls(roots)
            self.assertGreater(
                max(len(item["path"]) for item in baseline),
                260,
            )
            try:
                broker._configure_protected_dacls(roots, denied, broker_sid)
                broker._restore_protected_acls(baseline)
                broker._verify_protected_acl_restore(baseline)
            finally:
                broker._restore_protected_acls(baseline)
                broker._verify_protected_acl_restore(baseline)

if __name__ == "__main__":
    unittest.main(verbosity=2)
