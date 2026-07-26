#!/usr/bin/env python3
"""Focused tests for actorless, exact-proposal action grants."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = ROOT / "scripts" / "agent"
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

engine = importlib.import_module("case_state")
proposal_entrypoint = importlib.import_module("case_proposal_action_broker")
runtime_broker = importlib.import_module("case_runtime_broker")

WORKER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1101"
BROKER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1102"
OFFLINE_WORKER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1103"
SANDBOX_GROUP_SID = "S-1-5-21-1111111111-2222222222-3333333333-1100"
REPOSITORY = "https://github.com/example/proposal-boundary"
BRANCH = "codex/proposal-boundary"
TARGET_PATH = "candidate.txt"
BASELINE_BYTES = b"baseline\n"
REPLACEMENT_BYTES = b"authorized replacement\n"


def request_id() -> str:
    return str(uuid.uuid4())


class ProposalActionGrantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos-proposal-action-")
        self.probe_temp = tempfile.TemporaryDirectory(prefix="ccos-proposal-probe-")
        self.root = Path(self.temp.name).resolve(strict=True)
        self.probe_runtime_root = Path(self.probe_temp.name).resolve(strict=True)
        self.state_root = self.root / "state"
        self.repository_root = self.root / "repository"
        self.repository_root.mkdir()
        subprocess.run(
            ["git", "-C", str(self.repository_root), "init", "-q"], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.repository_root), "checkout", "-q", "-b", BRANCH],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository_root),
                "config",
                "user.email",
                "proposal@example.invalid",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository_root),
                "config",
                "user.name",
                "Proposal Test",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository_root),
                "remote",
                "add",
                "origin",
                REPOSITORY,
            ],
            check=True,
        )
        self.target = self.repository_root / TARGET_PATH
        self.target.write_bytes(BASELINE_BYTES)
        subprocess.run(
            ["git", "-C", str(self.repository_root), "add", TARGET_PATH], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository_root),
                "commit",
                "-q",
                "-m",
                "baseline",
            ],
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
        self.proposal = self.proposal_root / "proposal.bin"
        self.proposal.write_bytes(REPLACEMENT_BYTES)
        for root in (self.repository_root, self.proposal_root):
            (root / "probe-descendant").mkdir()

        self.store = engine.CaseStore(self.state_root)
        (self.state_root / "probe-descendant").mkdir(parents=True)
        self.case_id = str(uuid.uuid4())
        self.grant_id = "proposal-grant-one"
        self.authority_id = "proposal-authority-one"
        self.attempt_secret = hashlib.sha256(
            f"fixture-attempt-secret:{self.case_id}".encode("utf-8")
        ).hexdigest()
        self.store.register_case(
            self.case_id,
            objective="prove one actorless exact-proposal action",
            request_id=request_id(),
            expected_store_revision=0,
        )
        for kind, value, repository in (
            ("repo_url", REPOSITORY, None),
            ("branch", BRANCH, REPOSITORY),
            ("worktree", str(self.repository_root), None),
        ):
            self.store.bind(
                self.case_id,
                kind=kind,
                value=value,
                repository=repository,
                request_id=request_id(),
                expected_revision=self.revision,
            )
        self.store.start_implementation(
            self.case_id,
            request_id=request_id(),
            expected_revision=self.revision,
        )
        self.assertEqual(self.case["runtime"]["actors"], {})
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
                    "sha256": engine.file_sha256(
                        ROOT / "scripts" / "agent" / "case_runtime_broker.py"
                    ),
                    "size": (
                        ROOT / "scripts" / "agent" / "case_runtime_broker.py"
                    ).stat().st_size,
                },
            ],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()
        self.probe_temp.cleanup()

    @property
    def case(self) -> dict:
        return self.store.get_case(self.case_id)

    @property
    def revision(self) -> int:
        return self.case["revision"]

    def membership_evidence(self) -> dict:
        return {
            "protocol_version": engine.WINDOWS_GROUP_MEMBERSHIP_PROTOCOL_VERSION,
            "schema_version": 1,
            "sandbox_group_sid": SANDBOX_GROUP_SID,
            "members": [
                {
                    "principal_role": "proposal_generator",
                    "principal_sid": WORKER_SID,
                    "group_sids": [SANDBOX_GROUP_SID],
                },
                {
                    "principal_role": "offline_sandbox",
                    "principal_sid": OFFLINE_WORKER_SID,
                    "group_sids": [SANDBOX_GROUP_SID],
                },
            ],
            "observed_at": engine.utc_now(),
        }

    def protected_root_requests(self) -> list[dict]:
        state_path = engine.normalize_binding("worktree", str(self.state_root))
        source_path = engine.normalize_binding("worktree", str(ROOT))
        target_path = engine.normalize_binding("worktree", str(self.repository_root))
        proposal_path = engine.normalize_binding("worktree", str(self.proposal_root))
        return [
            {
                "root_kind": "target_root",
                "path": target_path,
                "owner_sid": BROKER_SID,
                "parent_path": engine.normalize_binding(
                    "worktree", str(self.repository_root.parent)
                ),
                "parent_owner_sid": BROKER_SID,
                "anchor_path": TARGET_PATH,
                "anchor_sha256": hashlib.sha256(BASELINE_BYTES).hexdigest(),
                "nested_probe_parent_path": "probe-descendant",
            },
            {
                "root_kind": "state_root",
                "path": state_path,
                "owner_sid": BROKER_SID,
                "parent_path": engine.normalize_binding(
                    "worktree", str(self.state_root.parent)
                ),
                "parent_owner_sid": BROKER_SID,
                "anchor_path": engine.STORE_FILENAME,
                "anchor_sha256": engine.file_sha256(self.store.path),
                "nested_probe_parent_path": "probe-descendant",
            },
            {
                "root_kind": "broker_source_root",
                "path": source_path,
                "owner_sid": BROKER_SID,
                "parent_path": engine.normalize_binding("worktree", str(ROOT.parent)),
                "parent_owner_sid": BROKER_SID,
                "anchor_path": self.source_pins["manifest_path"],
                "anchor_sha256": self.source_pins["manifest_sha256"],
                "nested_probe_parent_path": "scripts/agent",
            },
            {
                "root_kind": "proposal_root",
                "path": proposal_path,
                "owner_sid": BROKER_SID,
                "parent_path": engine.normalize_binding(
                    "worktree", str(self.proposal_root.parent)
                ),
                "parent_owner_sid": BROKER_SID,
                "anchor_path": self.proposal.name,
                "anchor_sha256": hashlib.sha256(REPLACEMENT_BYTES).hexdigest(),
                "nested_probe_parent_path": "probe-descendant",
            },
        ]

    def principal_probe(self, principal_sid: str, identity_name: str) -> dict:
        challenge = "proposal-worker-isolation-challenge"
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
            suffix = hashlib.sha256(challenge.encode()).hexdigest()[:20]
            nested_suffix = hashlib.sha256(
                f"{challenge}:{kind}".encode()
            ).hexdigest()[:20]
            acl_nonce = hashlib.sha256(
                f"{challenge}:{kind}:acl".encode()
            ).hexdigest()
            link_suffix = hashlib.sha256(
                f"{challenge}:{kind}:link".encode()
            ).hexdigest()[:20]
            roots.append(
                {
                    "root_kind": kind,
                    "path": item["path"],
                    "owner_sid": BROKER_SID,
                    "parent_path": item["parent_path"],
                    "parent_owner_sid": BROKER_SID,
                    "anchor_path": item["anchor_path"],
                    "anchor_sha256_before": item["anchor_sha256"],
                    "anchor_sha256_after": item["anchor_sha256"],
                    "probe_relative_path": (
                        f".ccos-worker-{kind.replace('_root', '')}-probe-{suffix}"
                    ),
                    "write_denial_error": "ACCESS_DENIED",
                    "write_denial_native_code": 5,
                    "probe_absent_before": True,
                    "probe_absent_after": True,
                    "nested_probe_parent_path": item["nested_probe_parent_path"],
                    "nested_probe_relative_path": (
                        item["nested_probe_parent_path"]
                        + "/.ccos-worker-nested-probe-"
                        + nested_suffix
                    ),
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
                    "hard_link_relative_path": (
                        f".ccos-worker-{kind.replace('_root', '')}-hard-link-{link_suffix}"
                    ),
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
            "worker_principal_sid": principal_sid,
            "worker_identity_name": identity_name,
            "worker_group_sids": [SANDBOX_GROUP_SID],
            "protected_roots": roots,
            "head_before": self.head,
            "head_after": self.head,
            "status_sha256_before": engine.EMPTY_SHA256,
            "status_sha256_after": engine.EMPTY_SHA256,
            "observed_at": engine.utc_now(),
        }

    def isolation_evidence(self, membership: dict) -> dict:
        denied = [WORKER_SID, OFFLINE_WORKER_SID, SANDBOX_GROUP_SID]
        membership_sha256 = engine.canonical_json_sha256(membership)
        probes = [
            {
                "principal_role": "proposal_generator",
                "principal_sid": WORKER_SID,
                "probe": self.principal_probe(WORKER_SID, "fixture\\online"),
            },
            {
                "principal_role": "offline_sandbox",
                "principal_sid": OFFLINE_WORKER_SID,
                "probe": self.principal_probe(OFFLINE_WORKER_SID, "fixture\\offline"),
            },
        ]
        body = {
            "denied_principal_sids": denied,
            "membership_evidence_sha256": membership_sha256,
            "principal_probes": probes,
        }
        return {
            "protocol_version": engine.WINDOWS_ISOLATION_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 2,
            **body,
            "combined_probe_sha256": engine.canonical_json_sha256(body),
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
            sddl = f"O:SYG:SYD:(A;;FA;;;SY)-proposal-fixture-{index}"
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

    def dacl_evidence(self, grant: dict) -> dict:
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
                        "owner_sid": BROKER_SID,
                        "parent_path": engine.normalize_binding(
                            "worktree", str(Path(path).parent)
                        ),
                        "parent_owner_sid": BROKER_SID,
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
            "protocol_version": engine.PROPOSAL_DACL_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 1,
            "denied_principal_sids": grant["denied_principal_sids"],
            "broker_principal_sid": BROKER_SID,
            "rules": rules,
            "observed_at": engine.utc_now(),
        }

    def trusted_probe(self, grant: dict) -> dict:
        anchors = {
            "target_root": (TARGET_PATH, hashlib.sha256(BASELINE_BYTES).hexdigest()),
            "state_root": (engine.STORE_FILENAME, engine.file_sha256(self.store.path)),
            "broker_source_root": (
                grant["proposal_broker_source_pins"]["manifest_path"],
                grant["proposal_broker_source_pins"]["manifest_sha256"],
            ),
            "proposal_root": (
                Path(grant["proposal_artifact_path"]).name,
                grant["proposal_artifact_sha256"],
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
            anchor_path, anchor_sha256 = anchors[kind]
            roots.append(
                {
                    "root_kind": kind,
                    "path": paths[kind],
                    "anchor_path": anchor_path,
                    "anchor_sha256_before": anchor_sha256,
                    "anchor_sha256_after": anchor_sha256,
                    "probe_relative_path": (
                        f".ccos-broker-{kind.replace('_root', '')}-probe-{suffix}"
                    ),
                    "probe_content_sha256": "4" * 64,
                    "probe_absent_after": True,
                }
            )
        return {
            "protocol_version": engine.TRUSTED_WRITE_PROBE_PROTOCOL_VERSION,
            "schema_version": 1,
            "broker_principal_sid": BROKER_SID,
            "broker_identity_name": "fixture\\broker",
            "protected_roots": roots,
            "head_before": self.head,
            "head_after": self.head,
            "status_sha256_before": engine.EMPTY_SHA256,
            "status_sha256_after": engine.EMPTY_SHA256,
            "observed_at": engine.utc_now(),
        }

    def grant_request(self) -> dict:
        normalized_worktree = engine.normalize_binding(
            "worktree", str(self.repository_root)
        )
        normalized_proposal = engine.normalize_binding(
            "worktree", str(self.proposal)
        )
        normalized_probe_root = engine.normalize_binding(
            "worktree", str(self.probe_runtime_root)
        )
        replacement_sha256 = hashlib.sha256(REPLACEMENT_BYTES).hexdigest()
        baseline_sha256 = hashlib.sha256(BASELINE_BYTES).hexdigest()
        expires_at = (
            dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            + dt.timedelta(minutes=5)
        ).isoformat()
        denied_principals = [
            WORKER_SID,
            OFFLINE_WORKER_SID,
            SANDBOX_GROUP_SID,
        ]
        authority = {
            "protocol_version": "ccos-proposal-action-authority-v1",
            "schema_version": 1,
            "evidence_mode": engine.PROPOSAL_DACL_EVIDENCE_MODE,
            "authority_id": self.authority_id,
            "case_id": self.case_id,
            "expected_case_revision": self.revision,
            "grant_id": self.grant_id,
            "operation_id": "replace-candidate-once",
            "action": "implementation",
            "operation": "replace_existing_file_v1",
            "repository": REPOSITORY,
            "branch": BRANCH,
            "worktree": normalized_worktree,
            "base_head": self.head,
            "target_path": TARGET_PATH,
            "baseline_sha256": baseline_sha256,
            "proposal_artifact_path": normalized_proposal,
            "proposal_artifact_sha256": replacement_sha256,
            "proposal_size": len(REPLACEMENT_BYTES),
            "replacement_sha256": replacement_sha256,
            "probe_runtime_root": normalized_probe_root,
            "broker_principal_sid": BROKER_SID,
            "denied_principal_sids": denied_principals,
            "expires_at": expires_at,
        }
        membership = self.membership_evidence()
        snapshot = self.protected_acl_snapshot()
        grant = {
            "protocol_version": engine.PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION,
            "schema_version": 2,
            "evidence_mode": engine.PROPOSAL_DACL_EVIDENCE_MODE,
            "grant_id": self.grant_id,
            "authority_id": self.authority_id,
            "operation_id": "replace-candidate-once",
            "action": "implementation",
            "operation": "replace_existing_file_v1",
            "repository": REPOSITORY,
            "branch": BRANCH,
            "worktree": normalized_worktree,
            "base_head": self.head,
            "target_path": TARGET_PATH,
            "baseline_sha256": baseline_sha256,
            "proposal_artifact_path": normalized_proposal,
            "proposal_artifact_sha256": replacement_sha256,
            "proposal_size": len(REPLACEMENT_BYTES),
            "replacement_sha256": replacement_sha256,
            "probe_runtime_root": normalized_probe_root,
            "worker_principal_sid": WORKER_SID,
            "model_worker_principal_sid": OFFLINE_WORKER_SID,
            "sandbox_group_principal_sid": SANDBOX_GROUP_SID,
            "denied_principal_sids": denied_principals,
            "broker_principal_sid": BROKER_SID,
            "sandbox_executable_path": engine.normalize_binding(
                "worktree", str(Path(sys.executable).resolve())
            ),
            "sandbox_executable_sha256": engine.file_sha256(Path(sys.executable)),
            "sandbox_executable_version": "0.0.0-test",
            "protected_acl_snapshot": snapshot,
            "protected_acl_snapshot_sha256": engine.canonical_json_sha256(snapshot),
            "preissue_dacl_evidence": {},
            "preissue_dacl_evidence_sha256": "0" * 64,
            "expires_at": expires_at,
            "authority": authority,
            "authority_sha256": engine.canonical_json_sha256(authority),
        }
        context = {
            **grant,
            "state_root": engine.normalize_binding("worktree", str(self.state_root)),
            "broker_source_root": engine.normalize_binding("worktree", str(ROOT)),
            "proposal_root": engine.normalize_binding(
                "worktree", str(self.proposal_root)
            ),
        }
        preissue = engine.CaseStore._normalize_dacl_evidence(
            self.dacl_evidence(context), context
        )
        grant["preissue_dacl_evidence"] = preissue
        grant["preissue_dacl_evidence_sha256"] = engine.canonical_json_sha256(
            preissue
        )
        return grant

    def arm_record(self, grant: dict) -> dict:
        return {
            "protocol_version": engine.PROPOSAL_ACTION_ARM_PROTOCOL_VERSION,
            "schema_version": 1,
            "lease_id": "proposal-lease-one",
            "attempt_id": "proposal-attempt-one",
            "attempt_secret_sha256": hashlib.sha256(
                self.attempt_secret.encode("ascii")
            ).hexdigest(),
            "supervisor_pid": 4242,
            "supervisor_creation_time_100ns": 133700000000000000,
            "supervisor_ready_sha256": "d" * 64,
            "lease_expires_at": grant["expires_at"],
        }

    def arm(
        self, grant: dict | None = None, *, expected_revision: int | None = None
    ) -> dict:
        full_grant = grant or self.grant_request()
        core = {
            field: full_grant[field]
            for field in proposal_entrypoint.GRANT_CORE_FIELDS
        }
        return self.store.arm_proposal_action_grant(
            self.case_id,
            grant=core,
            arm=self.arm_record(full_grant),
            request_id=request_id(),
            expected_revision=(
                self.revision if expected_revision is None else expected_revision
            ),
        )

    def issue(self, grant: dict | None = None, *, expected_revision: int | None = None) -> dict:
        full_grant = grant or self.grant_request()
        arm_result = self.arm(full_grant, expected_revision=expected_revision)
        with mock.patch.object(
            engine, "proposal_broker_source_pins", return_value=self.source_pins
        ):
            return self.store.issue_armed_proposal_action_grant(
                self.case_id,
                grant=full_grant,
                expected_arm_sha256=arm_result["arm_sha256"],
                attempt_secret=self.attempt_secret,
                request_id=request_id(),
                expected_revision=self.revision,
            )

    def write_envelope(
        self,
        grant: dict,
        *,
        expected_revision: int,
        name: str = "grant.json",
    ) -> Path:
        envelope_root = self.state_root / "proposal-envelopes"
        envelope_root.mkdir(exist_ok=True)
        envelope_path = envelope_root / name
        envelope_path.write_text(
            json.dumps(
                {
                    "protocol_version": proposal_entrypoint.ENVELOPE_PROTOCOL_VERSION,
                    "schema_version": 1,
                    "case_id": self.case_id,
                    "expected_case_revision": expected_revision,
                    "request_id": request_id(),
                    "grant": {
                        field: grant[field]
                        for field in proposal_entrypoint.GRANT_CORE_FIELDS
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return envelope_path

    def grant(self) -> dict:
        return self.case["runtime"]["action_grants"][self.grant_id]

    def alternate_source_file(self, name: str) -> Path:
        source_root = self.root / "alternate-source"
        agent_root = source_root / "scripts" / "agent"
        agent_root.mkdir(parents=True, exist_ok=True)
        for source_name in (
            "case_state.py",
            "case_runtime_broker.py",
            "case_proposal_action_broker.py",
        ):
            (agent_root / source_name).write_bytes(
                (SCRIPT_DIRECTORY / source_name).read_bytes()
            )
        (source_root / "install-bundle.manifest.json").write_bytes(
            (ROOT / "install-bundle.manifest.json").read_bytes()
        )
        return agent_root / name

    def claim(self, *, expected_revision: int | None = None) -> dict:
        grant = self.grant()
        return self.store.claim_action_grant(
            self.case_id,
            claim={
                "protocol_version": engine.PROPOSAL_ACTION_CLAIM_PROTOCOL_VERSION,
                "schema_version": 2,
                "grant_id": self.grant_id,
                "authority_sha256": grant["authority_sha256"],
                "broker_principal_sid": BROKER_SID,
                "dacl_evidence": self.dacl_evidence(grant),
                "trusted_write_probe": self.trusted_probe(grant),
            },
            request_id=request_id(),
            expected_revision=(
                self.revision if expected_revision is None else expected_revision
            ),
        )

    def complete_for_cleanup_recovery(
        self,
        *,
        refresh_run_id: str,
        post_run_id: str,
        refresh_overrides: dict | None = None,
    ) -> tuple[runtime_broker.BrokerJournal, dict]:
        self.issue()
        self.claim()
        claimed_grant = self.grant()
        self.target.write_bytes(REPLACEMENT_BYTES)
        normalized_dacl = engine.CaseStore._normalize_dacl_evidence(
            self.dacl_evidence(claimed_grant), claimed_grant
        )
        dacl_rules = {
            name: value
            for name, value in normalized_dacl.items()
            if name != "observed_at"
        }
        refresh_details = {
            "grant_sha256": claimed_grant["grant_sha256"],
            "claim_sha256": claimed_grant["claim"]["claim_sha256"],
            "broker_principal_sid": claimed_grant["broker_principal_sid"],
            "protected_acl_snapshot_sha256": claimed_grant[
                "protected_acl_snapshot_sha256"
            ],
            "refresh_stage": "post_replacement",
            "protected_object_count": 1,
            "protected_object_inventory_sha256": engine.canonical_json_sha256(
                [{"fixture": "protected-object"}]
            ),
            "dacl_rules_sha256": engine.canonical_json_sha256(dacl_rules),
            "target_sha256": claimed_grant["replacement_sha256"],
        }
        refresh_details.update(refresh_overrides or {})
        journal = runtime_broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )
        journal.append(
            "ACL_LOCKDOWN_REFRESHED", refresh_run_id, **refresh_details
        )
        status_raw = runtime_broker._run_git(
            self.repository_root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        )
        post_body = {
            "protocol_version": (
                runtime_broker.POST_REPLACEMENT_EVIDENCE_PROTOCOL_VERSION
            ),
            "schema_version": 2,
            "evidence_mode": engine.PROPOSAL_DACL_EVIDENCE_MODE,
            "grant_id": self.grant_id,
            "run_id": post_run_id,
            "target_sha256": claimed_grant["replacement_sha256"],
            "status_sha256": hashlib.sha256(status_raw).hexdigest(),
            "observed_status_paths": claimed_grant["allowed_paths"],
            "dacl_evidence": normalized_dacl,
            "dacl_evidence_sha256": engine.canonical_json_sha256(
                normalized_dacl
            ),
            "protected_acl_snapshot_sha256": claimed_grant[
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
            post_run_id,
            post_replacement_evidence=post_evidence,
            post_replacement_evidence_sha256=post_evidence[
                "post_replacement_evidence_sha256"
            ],
            protected_acl_snapshot_sha256=claimed_grant[
                "protected_acl_snapshot_sha256"
            ],
            **runtime_broker._journal_action_details(
                claimed_grant,
                BROKER_SID,
                target_sha256_before=claimed_grant["baseline_sha256"],
                target_sha256_after=claimed_grant["replacement_sha256"],
                changed_path=claimed_grant["target_path"],
                claim_sha256=claimed_grant["claim"]["claim_sha256"],
            ),
        )
        completed = self.store.complete_action_grant(
            self.case_id,
            completion={
                "protocol_version": engine.PROPOSAL_ACTION_RESULT_PROTOCOL_VERSION,
                "schema_version": 2,
                "grant_id": self.grant_id,
                "authority_sha256": claimed_grant["authority_sha256"],
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
        return journal, claimed_grant

    def recover_completed_cleanup(self) -> dict:
        restored = {
            "restored": True,
            "already_restored": False,
            "protected_acl_snapshot_sha256": self.grant()[
                "protected_acl_snapshot_sha256"
            ],
            "journal_event_sha256": "f" * 64,
        }
        with (
            mock.patch.object(
                runtime_broker,
                "windows_identity",
                return_value=("fixture\\broker", BROKER_SID),
            ),
            mock.patch.object(runtime_broker, "_verify_source_pins"),
            mock.patch.object(
                runtime_broker,
                "_restore_acl_snapshot_after_lockdown",
                return_value=restored,
            ),
        ):
            return runtime_broker.recover_completed_action_grant_cleanup(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )

    def test_arm_binds_one_exact_prearm_revision_and_blocks_other_mutations(self) -> None:
        grant = self.grant_request()
        authority_revision = self.revision

        armed = self.arm(grant)

        self.assertEqual(armed["status"], "ARMED")
        self.assertEqual(self.revision, authority_revision + 1)
        canonical = self.grant()
        self.assertEqual(canonical["status"], "ARMED")
        self.assertEqual(canonical["arm"]["authority_revision"], authority_revision)
        self.assertEqual(canonical["arm"]["armed_revision"], self.revision)
        self.assertEqual(canonical["arm"]["attempt_id"], "proposal-attempt-one")
        self.assertEqual(
            canonical["arm"]["recovery_roots"],
            {
                "target_root": engine.normalize_binding(
                    "worktree", str(self.repository_root)
                ),
                "state_root": engine.normalize_binding(
                    "worktree", str(self.state_root)
                ),
                "broker_source_root": engine.normalize_binding(
                    "worktree", str(ROOT)
                ),
                "proposal_root": engine.normalize_binding(
                    "worktree", str(self.proposal_root)
                ),
            },
        )
        self.assertEqual(
            canonical["arm"]["recovery_roots_sha256"],
            engine.canonical_json_sha256(canonical["arm"]["recovery_roots"]),
        )
        self.assertEqual(
            canonical["arm"]["attempt_secret_sha256"],
            hashlib.sha256(self.attempt_secret.encode("ascii")).hexdigest(),
        )
        self.assertNotIn(self.attempt_secret, json.dumps(canonical, sort_keys=True))
        with self.assertRaisesRegex(engine.AuthorizationError, "blocked"):
            self.store.start_implementation(
                self.case_id,
                request_id=request_id(),
                expected_revision=self.revision,
            )
        self.assertEqual(self.revision, authority_revision + 1)

    def test_armed_issue_requires_exact_arm_revision_and_plaintext_secret(self) -> None:
        grant = self.grant_request()
        armed = self.arm(grant)
        armed_revision = self.revision
        attempts = (
            ("0" * 64, self.attempt_secret, armed_revision, engine.AuthorizationError),
            (
                armed["arm_sha256"],
                "b" * 64,
                armed_revision,
                engine.AuthorizationError,
            ),
            (
                armed["arm_sha256"],
                self.attempt_secret,
                armed_revision - 1,
                engine.RevisionConflict,
            ),
        )
        with mock.patch.object(
            engine, "proposal_broker_source_pins", return_value=self.source_pins
        ):
            for arm_sha256, secret, revision, error in attempts:
                with self.subTest(
                    arm_sha256=arm_sha256,
                    secret=secret,
                    revision=revision,
                ):
                    with self.assertRaises(error):
                        self.store.issue_armed_proposal_action_grant(
                            self.case_id,
                            grant=grant,
                            expected_arm_sha256=arm_sha256,
                            attempt_secret=secret,
                            request_id=request_id(),
                            expected_revision=revision,
                        )
                    self.assertEqual(self.revision, armed_revision)
                    self.assertEqual(self.grant()["status"], "ARMED")

            issued = self.store.issue_armed_proposal_action_grant(
                self.case_id,
                grant=grant,
                expected_arm_sha256=armed["arm_sha256"],
                attempt_secret=self.attempt_secret,
                request_id=request_id(),
                expected_revision=armed_revision,
            )

        self.assertEqual(issued["status"], "ISSUED")
        self.assertEqual(self.grant()["status"], "ISSUED")
        self.assertEqual(
            self.grant()["execution_nonce_sha256"],
            hashlib.sha256(self.attempt_secret.encode("ascii")).hexdigest(),
        )
        self.assertNotIn(self.attempt_secret, json.dumps(self.case, sort_keys=True))
        self.assertNotIn(
            self.attempt_secret, self.store.path.read_text(encoding="utf-8")
        )

    def test_direct_proposal_issuance_is_disabled(self) -> None:
        initial_case = self.case
        with self.assertRaisesRegex(engine.AuthorizationError, "canonically armed"):
            self.store.issue_proposal_action_grant(
                self.case_id,
                grant=self.grant_request(),
                request_id=request_id(),
                expected_revision=self.revision,
            )
        self.assertEqual(self.case, initial_case)

    def test_arm_cancellation_is_terminal_and_idempotent(self) -> None:
        grant = self.grant_request()
        armed = self.arm(grant)
        armed_revision = self.revision
        cancellation = {
            "protocol_version": engine.PROPOSAL_ACTION_CANCELLATION_PROTOCOL_VERSION,
            "schema_version": 1,
            "reason_code": "SUPERVISOR_CONTEXT_LOST",
            "evidence_sha256": "e" * 64,
            "cancelled_at": engine.utc_now(),
        }
        cancellation_request_id = request_id()

        cancelled = self.store.cancel_armed_proposal_action_grant(
            self.case_id,
            grant_id=self.grant_id,
            expected_arm_sha256=armed["arm_sha256"],
            cancellation=cancellation,
            request_id=cancellation_request_id,
            expected_revision=armed_revision,
        )
        replay = self.store.cancel_armed_proposal_action_grant(
            self.case_id,
            grant_id=self.grant_id,
            expected_arm_sha256=armed["arm_sha256"],
            cancellation=cancellation,
            request_id=cancellation_request_id,
            expected_revision=armed_revision,
        )

        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(self.case["state"], "CASE_LOCKED")
        self.assertEqual(self.grant()["status"], "CANCELLED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_orphaned_arm_recovery_cancels_without_target_mutation(self) -> None:
        self.arm(self.grant_request())
        with mock.patch.object(
            runtime_broker,
            "windows_identity",
            return_value=("fixture\\broker", BROKER_SID),
        ):
            recovered = runtime_broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )
            replay = runtime_broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )

        self.assertEqual(recovered["status"], "CANCELLED")
        self.assertEqual(replay["status"], "cancelled_stable")
        self.assertEqual(self.case["state"], "CASE_LOCKED")
        self.assertEqual(self.grant()["status"], "CANCELLED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_orphaned_arm_recovery_accepts_empty_preissue_journal(self) -> None:
        self.arm(self.grant_request())
        journal = runtime_broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )
        journal.ensure_file()

        with mock.patch.object(
            runtime_broker,
            "windows_identity",
            return_value=("fixture\\broker", BROKER_SID),
        ):
            recovered = runtime_broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )

        self.assertEqual(recovered["status"], "CANCELLED")
        self.assertEqual(recovered["acl_recovery"], [])
        self.assertEqual(journal.records(), [])
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_orphaned_arm_recovery_proves_snapshot_only_preintent_is_original(self) -> None:
        grant = self.grant_request()
        self.arm(grant)
        snapshot = grant["protected_acl_snapshot"]
        journal = runtime_broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )
        snapshot_event = journal.append(
            "ACL_SNAPSHOT",
            "snapshot-only-crash",
            protected_acl_snapshot=snapshot,
            protected_acl_snapshot_sha256=engine.canonical_json_sha256(snapshot),
        )

        with (
            mock.patch.object(
                runtime_broker,
                "windows_identity",
                return_value=("fixture\\broker", BROKER_SID),
            ),
            mock.patch.object(
                runtime_broker, "_verify_protected_acl_restore"
            ) as verify_original,
            mock.patch.object(
                runtime_broker, "_restore_protected_acls"
            ) as restore,
        ):
            recovered = runtime_broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )

        verify_original.assert_called_once_with(snapshot)
        restore.assert_not_called()
        acl_recovery = recovered["acl_recovery"]
        self.assertEqual(len(acl_recovery), 1)
        self.assertEqual(acl_recovery[0]["recovery_state"], "PREINTENT_ORIGINAL")
        self.assertEqual(
            acl_recovery[0]["snapshot_event_sha256"],
            snapshot_event["event_sha256"],
        )
        self.assertIsNone(acl_recovery[0]["lockdown_intent_event_sha256"])
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_orphaned_arm_recovery_restores_postintent_preissue_lockdown(self) -> None:
        grant = self.grant_request()
        self.arm(grant)
        canonical = self.grant()
        snapshot = grant["protected_acl_snapshot"]
        snapshot_sha256 = engine.canonical_json_sha256(snapshot)
        roots = canonical["arm"]["recovery_roots"]
        intent = {
            "roots": roots,
            "denied_principal_sids": canonical["denied_principal_sids"],
            "broker_principal_sid": canonical["broker_principal_sid"],
        }
        journal = runtime_broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )
        journal.append(
            "ACL_SNAPSHOT",
            "postintent-crash",
            protected_acl_snapshot=snapshot,
            protected_acl_snapshot_sha256=snapshot_sha256,
        )
        journal.append(
            "ACL_LOCKDOWN_INTENT",
            "postintent-crash",
            protected_acl_snapshot_sha256=snapshot_sha256,
            lockdown_intent=intent,
            lockdown_intent_sha256=engine.canonical_json_sha256(intent),
        )
        inventory = [
            {"path": item["path"], "object_type": "file", "scope": "descendant"}
            for item in snapshot
        ]

        with (
            mock.patch.object(
                runtime_broker,
                "windows_identity",
                return_value=("fixture\\broker", BROKER_SID),
            ),
            mock.patch.object(
                runtime_broker,
                "_verify_protected_acl_restore",
                side_effect=[
                    runtime_broker.BrokerAuthorizationError("lockdown active"),
                    None,
                ],
            ),
            mock.patch.object(
                runtime_broker,
                "_protected_acl_inventory",
                return_value=inventory,
            ),
            mock.patch.object(runtime_broker, "_restore_protected_acls") as restore,
        ):
            recovered = runtime_broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )

        restore.assert_called_once_with(snapshot)
        self.assertEqual(recovered["status"], "CANCELLED")
        self.assertEqual(len(recovered["acl_recovery"]), 1)
        self.assertTrue(recovered["acl_recovery"][0]["restored"])
        self.assertEqual(journal.records()[-1]["event"], "ACL_RESTORED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_orphaned_arm_recovery_converges_when_issuance_wins_revision_race(self) -> None:
        grant = self.grant_request()
        armed = self.arm(grant)
        armed_revision = self.revision

        def issuance_wins(*_args: object, **_kwargs: object) -> None:
            self.store.issue_armed_proposal_action_grant(
                self.case_id,
                grant=grant,
                expected_arm_sha256=armed["arm_sha256"],
                attempt_secret=self.attempt_secret,
                request_id=request_id(),
                expected_revision=armed_revision,
            )
            raise engine.RevisionConflict("issuance won")

        with (
            mock.patch.object(
                runtime_broker,
                "windows_identity",
                return_value=("fixture\\broker", BROKER_SID),
            ),
            mock.patch.object(
                engine, "proposal_broker_source_pins", return_value=self.source_pins
            ),
            mock.patch.object(
                engine.CaseStore,
                "cancel_armed_proposal_action_grant",
                side_effect=issuance_wins,
            ),
            mock.patch.object(
                runtime_broker,
                "_rollback_and_fail",
                return_value={"status": "FAILED", "race_converged": True},
            ) as rollback,
        ):
            recovered = runtime_broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )

        self.assertTrue(recovered["race_converged"])
        rollback.assert_called_once()
        self.assertEqual(rollback.call_args.args[2]["status"], "ISSUED")
        self.assertEqual(self.grant()["status"], "ISSUED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_envelope_without_canonical_arm_denies_before_secret_or_action(self) -> None:
        grant = self.grant_request()
        revision = self.revision
        envelope = self.write_envelope(grant, expected_revision=revision)
        secret_provider = mock.Mock(return_value=self.attempt_secret)
        with (
            mock.patch.object(proposal_entrypoint, "require_current_broker_principal"),
            mock.patch.object(
                proposal_entrypoint, "collect_proposal_isolation_evidence"
            ) as collect_evidence,
            mock.patch.object(
                proposal_entrypoint, "execute_proposal_grant"
            ) as execute_grant,
        ):
            with self.assertRaisesRegex(
                engine.AuthorizationError, "NOT_CANONICALLY_ARMED"
            ):
                proposal_entrypoint.execute_envelope(
                    self.state_root,
                    envelope,
                    attempt_secret_provider=secret_provider,
                )

        secret_provider.assert_not_called()
        collect_evidence.assert_not_called()
        execute_grant.assert_not_called()
        self.assertEqual(self.revision, revision)
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_armed_envelope_missing_or_wrong_secret_denies_before_action(self) -> None:
        grant = self.grant_request()
        authority_revision = self.revision
        self.arm(grant)
        armed_revision = self.revision
        envelope = self.write_envelope(
            grant, expected_revision=authority_revision
        )
        with (
            mock.patch.object(proposal_entrypoint, "require_current_broker_principal"),
            mock.patch.object(
                proposal_entrypoint, "collect_proposal_isolation_evidence"
            ) as collect_evidence,
            mock.patch.object(
                proposal_entrypoint, "execute_proposal_grant"
            ) as execute_grant,
        ):
            for supplied in (None, "b" * 64):
                with self.subTest(supplied=supplied):
                    with self.assertRaises(engine.AuthorizationError):
                        proposal_entrypoint.execute_envelope(
                            self.state_root,
                            envelope,
                            attempt_secret=supplied,
                        )

        collect_evidence.assert_not_called()
        execute_grant.assert_not_called()
        self.assertEqual(self.revision, armed_revision)
        self.assertEqual(self.grant()["status"], "ARMED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_issue_denies_when_live_source_root_differs_from_canonical_arm(self) -> None:
        grant = self.grant_request()
        armed = self.arm(grant)
        armed_revision = self.revision
        store_before = self.store.path.read_bytes()
        alternate_module = self.alternate_source_file("case_state.py")
        journal_root = self.state_root / runtime_broker.BROKER_JOURNAL_DIRECTORY

        with (
            mock.patch.object(engine, "__file__", str(alternate_module)),
            mock.patch.object(
                engine.CaseStore,
                "_seal_action_artifact_unlocked",
                side_effect=AssertionError("issue must deny before sealing evidence"),
            ) as seal_artifact,
            mock.patch.object(
                engine,
                "proposal_broker_source_pins",
                side_effect=AssertionError("issue must deny before reading source pins"),
            ) as source_pins,
        ):
            with self.assertRaisesRegex(
                engine.AuthorizationError, "recovery roots differ"
            ):
                self.store.issue_armed_proposal_action_grant(
                    self.case_id,
                    grant=grant,
                    expected_arm_sha256=armed["arm_sha256"],
                    attempt_secret=self.attempt_secret,
                    request_id=request_id(),
                    expected_revision=armed_revision,
                )

        seal_artifact.assert_not_called()
        source_pins.assert_not_called()
        self.assertEqual(self.store.path.read_bytes(), store_before)
        self.assertEqual(self.revision, armed_revision)
        self.assertEqual(self.grant()["status"], "ARMED")
        self.assertFalse(journal_root.exists())
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_exact_secret_issues_once_and_fresh_envelope_resume_is_denied(self) -> None:
        grant = self.grant_request()
        authority_revision = self.revision
        self.arm(grant)
        envelope = self.write_envelope(
            grant, expected_revision=authority_revision
        )
        evidence = {
            field: grant[field]
            for field in engine.PROPOSAL_ACTION_GRANT_ISSUANCE_EVIDENCE_FIELDS
        }
        late_secret_provider = mock.Mock(return_value=self.attempt_secret)
        with (
            mock.patch.object(proposal_entrypoint, "require_current_broker_principal"),
            mock.patch.object(
                proposal_entrypoint,
                "collect_proposal_isolation_evidence",
                return_value=evidence,
            ) as collect_evidence,
            mock.patch.object(
                engine, "proposal_broker_source_pins", return_value=self.source_pins
            ),
            mock.patch.object(
                proposal_entrypoint,
                "execute_proposal_grant",
                return_value={"status": "COMPLETED"},
            ) as execute_grant,
        ):
            result = proposal_entrypoint.execute_envelope(
                self.state_root,
                envelope,
                attempt_secret=self.attempt_secret,
            )
            with self.assertRaisesRegex(
                engine.AuthorizationError, "cannot be resumed"
            ):
                proposal_entrypoint.execute_envelope(
                    self.state_root,
                    envelope,
                    attempt_secret_provider=late_secret_provider,
                )

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(self.grant()["status"], "ISSUED")
        collect_evidence.assert_called_once()
        execute_grant.assert_called_once_with(
            self.state_root.resolve(strict=True),
            self.case_id,
            self.grant_id,
            execution_nonce=self.attempt_secret,
        )
        late_secret_provider.assert_not_called()
        self.assertNotIn(self.attempt_secret, json.dumps(result, sort_keys=True))
        self.assertNotIn(self.attempt_secret, json.dumps(self.case, sort_keys=True))
        self.assertNotIn(
            self.attempt_secret, self.store.path.read_text(encoding="utf-8")
        )

    def test_runtime_proposal_execution_requires_nonce_before_journal_creation(self) -> None:
        self.issue()
        token = hashlib.sha256(
            f"{self.case_id}\0{self.grant_id}".encode("utf-8")
        ).hexdigest()
        journal_directory = (
            self.state_root
            / runtime_broker.BROKER_JOURNAL_DIRECTORY
            / self.case_id
        )
        journal_lock = journal_directory / f"{token}.lock"

        with self.assertRaisesRegex(
            runtime_broker.BrokerAuthorizationError, "in-memory execution nonce"
        ):
            runtime_broker.execute_proposal_grant(
                self.state_root, self.case_id, self.grant_id
            )
        with self.assertRaisesRegex(
            runtime_broker.BrokerAuthorizationError, "differs"
        ):
            runtime_broker.execute_proposal_grant(
                self.state_root,
                self.case_id,
                self.grant_id,
                execution_nonce="b" * 64,
            )

        self.assertFalse(journal_lock.exists())
        self.assertFalse(journal_directory.exists())
        self.assertEqual(self.grant()["status"], "ISSUED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_issue_succeeds_with_empty_runtime_actors(self) -> None:
        result = self.issue()
        self.assertEqual(result["status"], "ISSUED")
        self.assertEqual(self.case["runtime"]["actors"], {})
        self.assertEqual(self.grant()["protocol_version"], engine.PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION)
        self.assertEqual(
            self.grant()["evidence_mode"], engine.PROPOSAL_DACL_EVIDENCE_MODE
        )
        self.assertNotIn("group_membership_evidence", self.grant())
        self.assertNotIn("isolation_evidence", self.grant())
        self.assertNotIn("actor_thread_id", result)

    def test_issued_grant_rejects_recovery_root_binding_corruption(self) -> None:
        self.issue()
        data = json.loads(self.store.path.read_text(encoding="utf-8"))
        grant = data["cases"][self.case_id]["runtime"]["action_grants"][
            self.grant_id
        ]
        grant["broker_source_root"] = engine.normalize_binding(
            "worktree", str(self.root / "tampered-source-root")
        )
        grant["grant_sha256"] = engine.canonical_json_sha256(
            {name: value for name, value in grant.items() if name != "grant_sha256"}
        )
        self.store.path.write_text(
            json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            engine.StoreCorruptionError, "recovery roots differ from issued roots"
        ):
            engine.CaseStore(self.state_root).get_case(self.case_id)

    def test_v2_broker_dacl_mode_never_calls_worker_isolation_validators(self) -> None:
        with (
            mock.patch.object(
                engine.CaseStore,
                "_normalize_sandbox_membership_evidence",
                side_effect=AssertionError("proposal v2 must not validate worker membership"),
            ),
            mock.patch.object(
                engine.CaseStore,
                "_normalize_windows_isolation_evidence",
                side_effect=AssertionError("proposal v2 must not validate nested probes"),
            ),
        ):
            result = self.issue()
        self.assertEqual(result["status"], "ISSUED")

    def test_v2_rejects_unknown_mode_and_legacy_probe_fields(self) -> None:
        for field, value in (
            ("group_membership_evidence", self.membership_evidence()),
            ("isolation_evidence", self.isolation_evidence(self.membership_evidence())),
        ):
            with self.subTest(field=field):
                grant = self.grant_request()
                grant[field] = value
                before_revision = self.revision
                with self.assertRaises(engine.ValidationError):
                    self.store._normalize_proposal_action_grant_request(
                        self.case_id, grant
                    )
                self.assertEqual(self.revision, before_revision)
                self.assertEqual(self.case["runtime"]["action_grants"], {})
        grant = self.grant_request()
        grant["evidence_mode"] = "nested_sandbox_v1"
        grant["authority"]["evidence_mode"] = "nested_sandbox_v1"
        grant["authority_sha256"] = engine.canonical_json_sha256(grant["authority"])
        with self.assertRaises(engine.AuthorizationError):
            self.store._normalize_proposal_action_grant_request(self.case_id, grant)

    def test_v2_rejects_incomplete_broker_dacl_without_issuance(self) -> None:
        grant = self.grant_request()
        grant["preissue_dacl_evidence"]["rules"].pop()
        grant["preissue_dacl_evidence_sha256"] = engine.canonical_json_sha256(
            grant["preissue_dacl_evidence"]
        )
        before_revision = self.revision
        with self.assertRaises(engine.ValidationError):
            self.issue(grant)
        self.assertEqual(self.revision, before_revision + 1)
        self.assertEqual(self.grant()["status"], "ARMED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_proposal_snapshot_accepts_bounded_recursive_descendants(self) -> None:
        grant = self.grant_request()
        snapshot = [
            *grant["protected_acl_snapshot"],
            self.acl_snapshot_entry(
                self.repository_root / ".git" / "config",
                "recursive-target-metadata",
            ),
            self.acl_snapshot_entry(
                ROOT / "scripts" / "agent" / "case_state.py",
                "recursive-source",
            ),
        ]
        snapshot.sort(
            key=lambda item: (
                len(Path(item["path"]).parts),
                item["path"].casefold(),
            ),
            reverse=True,
        )
        grant["protected_acl_snapshot"] = snapshot
        grant["protected_acl_snapshot_sha256"] = engine.canonical_json_sha256(
            snapshot
        )

        issued = self.issue(grant)

        self.assertEqual(issued["status"], "ISSUED")
        self.assertEqual(
            {
                item["path"]
                for item in self.grant()["protected_acl_snapshot"]
            },
            {item["path"] for item in snapshot},
        )

    def test_proposal_snapshot_rejects_a_descendant_escape(self) -> None:
        grant = self.grant_request()
        escaped = self.repository_root.parent / "outside-protected-root.txt"
        escaped.write_text("outside\n", encoding="utf-8")
        snapshot = [
            *grant["protected_acl_snapshot"],
            self.acl_snapshot_entry(escaped, "escaped-descendant"),
        ]
        snapshot.sort(
            key=lambda item: (
                len(Path(item["path"]).parts),
                item["path"].casefold(),
            ),
            reverse=True,
        )
        grant["protected_acl_snapshot"] = snapshot
        grant["protected_acl_snapshot_sha256"] = engine.canonical_json_sha256(
            snapshot
        )
        before_revision = self.revision

        with self.assertRaisesRegex(engine.ValidationError, "escapes"):
            self.issue(grant)

        self.assertEqual(self.revision, before_revision + 1)
        self.assertEqual(self.grant()["status"], "ARMED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_proposal_snapshot_rejects_missing_mandatory_path(self) -> None:
        grant = self.grant_request()
        mandatory = self.protected_root_requests()[0]["parent_path"]
        grant["protected_acl_snapshot"] = [
            item
            for item in grant["protected_acl_snapshot"]
            if item["path"] != mandatory
        ]
        grant["protected_acl_snapshot_sha256"] = engine.canonical_json_sha256(
            grant["protected_acl_snapshot"]
        )

        with self.assertRaisesRegex(engine.ValidationError, "omits"):
            self.issue(grant)

    def test_proposal_snapshot_rejects_duplicate_descendant(self) -> None:
        grant = self.grant_request()
        descendant = next(
            item
            for item in grant["protected_acl_snapshot"]
            if item["path"]
            == engine.normalize_binding("worktree", str(self.target))
        )
        grant["protected_acl_snapshot"].append(dict(descendant))
        grant["protected_acl_snapshot_sha256"] = engine.canonical_json_sha256(
            grant["protected_acl_snapshot"]
        )

        with self.assertRaisesRegex(engine.ValidationError, "duplicated"):
            self.issue(grant)

    def test_proposal_snapshot_rejects_more_than_fixed_bound(self) -> None:
        grant = self.grant_request()
        grant["protected_acl_snapshot"] = [
            {}
        ] * (engine.MAX_PROTECTED_ACL_SNAPSHOT_ENTRIES + 1)
        grant["protected_acl_snapshot_sha256"] = engine.canonical_json_sha256(
            grant["protected_acl_snapshot"]
        )

        with self.assertRaisesRegex(engine.ValidationError, "fixed bound"):
            self.issue(grant)

    def test_v2_preissue_collection_never_launches_nested_sandbox(self) -> None:
        grant = self.grant_request()
        self.arm(grant)
        context = {
            **grant,
            "state_root": engine.normalize_binding("worktree", str(self.state_root)),
            "broker_source_root": engine.normalize_binding("worktree", str(ROOT)),
            "proposal_root": engine.normalize_binding(
                "worktree", str(self.proposal_root)
            ),
        }
        dacl = self.dacl_evidence(context)
        version_result = subprocess.CompletedProcess(
            [str(Path(sys.executable).resolve()), "--version"],
            0,
            stdout=b"Python 0.0.0-test\n",
            stderr=b"",
        )
        with (
            mock.patch.object(runtime_broker, "windows_identity", return_value=("fixture\\broker", BROKER_SID)),
            mock.patch.object(runtime_broker.subprocess, "run", return_value=version_result),
            mock.patch.object(
                runtime_broker,
                "_snapshot_protected_acls",
                return_value=self.protected_acl_snapshot(),
            ),
            mock.patch.object(runtime_broker, "_configure_protected_dacls"),
            mock.patch.object(runtime_broker, "inspect_proposal_dacls", return_value=dacl),
            mock.patch.object(
                runtime_broker,
                "_collect_preissue_dual_probes",
                side_effect=AssertionError("proposal v2 must not launch dual probes"),
            ),
            mock.patch.object(
                runtime_broker,
                "_run_fixed_sandbox_probe",
                side_effect=AssertionError("proposal v2 must not launch a sandbox"),
            ),
        ):
            evidence = runtime_broker.collect_proposal_isolation_evidence(
                store=self.store,
                case_id=self.case_id,
                grant_core=grant,
            )
        self.assertNotIn("group_membership_evidence", evidence)
        self.assertNotIn("isolation_evidence", evidence)
        self.assertEqual(evidence["preissue_dacl_evidence"], dacl)
        records = runtime_broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        ).records()
        self.assertEqual(
            [record["event"] for record in records],
            ["ACL_SNAPSHOT", "ACL_LOCKDOWN_INTENT", "ACL_LOCKDOWN_VERIFIED"],
        )

    def test_preissue_collection_denies_cross_source_root_before_side_effects(self) -> None:
        grant = self.grant_request()
        self.arm(grant)
        armed_revision = self.revision
        store_before = self.store.path.read_bytes()
        alternate_module = self.alternate_source_file("case_runtime_broker.py")
        token = hashlib.sha256(
            f"{self.case_id}\0{self.grant_id}".encode("utf-8")
        ).hexdigest()
        journal_path = (
            self.state_root
            / runtime_broker.BROKER_JOURNAL_DIRECTORY
            / self.case_id
            / f"{token}.jsonl"
        )
        version_result = subprocess.CompletedProcess(
            [str(Path(sys.executable).resolve()), "--version"],
            0,
            stdout=b"Python 0.0.0-test\n",
            stderr=b"",
        )

        with (
            mock.patch.object(runtime_broker, "__file__", str(alternate_module)),
            mock.patch.object(
                runtime_broker,
                "windows_identity",
                return_value=("fixture\\broker", BROKER_SID),
            ),
            mock.patch.object(
                runtime_broker.subprocess, "run", return_value=version_result
            ),
            mock.patch.object(
                runtime_broker, "_snapshot_protected_acls"
            ) as snapshot,
            mock.patch.object(
                runtime_broker, "_configure_protected_dacls"
            ) as configure,
            mock.patch.object(
                runtime_broker.BrokerJournal, "ensure_file"
            ) as ensure_file,
        ):
            with self.assertRaisesRegex(
                runtime_broker.BrokerAuthorizationError,
                "roots differ from the canonical arm",
            ):
                runtime_broker.collect_proposal_isolation_evidence(
                    store=self.store,
                    case_id=self.case_id,
                    grant_core=grant,
                )

        ensure_file.assert_not_called()
        snapshot.assert_not_called()
        configure.assert_not_called()
        self.assertFalse(journal_path.exists())
        self.assertEqual(self.store.path.read_bytes(), store_before)
        self.assertEqual(self.revision, armed_revision)
        self.assertEqual(self.grant()["status"], "ARMED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_cancelled_arm_cannot_resume_preissue_collection(self) -> None:
        grant = self.grant_request()
        authority_revision = self.revision
        self.arm(grant)
        armed_revision = self.revision
        envelope = self.write_envelope(
            grant, expected_revision=authority_revision
        )
        token = hashlib.sha256(
            f"{self.case_id}\0{self.grant_id}".encode("utf-8")
        ).hexdigest()
        journal_path = (
            self.state_root
            / runtime_broker.BROKER_JOURNAL_DIRECTORY
            / self.case_id
            / f"{token}.jsonl"
        )
        version_result = subprocess.CompletedProcess(
            [str(Path(sys.executable).resolve()), "--version"],
            0,
            stdout=b"Python 0.0.0-test\n",
            stderr=b"",
        )

        def cancel_then_collect(**kwargs: object) -> dict:
            runtime_broker.recover_orphaned_action_grant(
                state_root=self.state_root,
                case_id=self.case_id,
                grant_id=self.grant_id,
            )
            return runtime_broker.collect_proposal_isolation_evidence(**kwargs)

        with (
            mock.patch.object(proposal_entrypoint, "require_current_broker_principal"),
            mock.patch.object(
                proposal_entrypoint,
                "collect_proposal_isolation_evidence",
                side_effect=cancel_then_collect,
            ),
            mock.patch.object(
                proposal_entrypoint, "execute_proposal_grant"
            ) as execute_grant,
            mock.patch.object(
                runtime_broker,
                "windows_identity",
                return_value=("fixture\\broker", BROKER_SID),
            ),
            mock.patch.object(
                runtime_broker.subprocess, "run", return_value=version_result
            ),
            mock.patch.object(
                runtime_broker, "_snapshot_protected_acls"
            ) as snapshot,
            mock.patch.object(
                runtime_broker, "_configure_protected_dacls"
            ) as configure,
            mock.patch.object(
                runtime_broker.BrokerJournal, "ensure_file"
            ) as ensure_file,
        ):
            with self.assertRaisesRegex(
                runtime_broker.BrokerAuthorizationError,
                "sole canonical ARMED grant",
            ):
                proposal_entrypoint.execute_envelope(
                    self.state_root,
                    envelope,
                    attempt_secret=self.attempt_secret,
                )

        ensure_file.assert_not_called()
        snapshot.assert_not_called()
        configure.assert_not_called()
        execute_grant.assert_not_called()
        self.assertFalse(journal_path.exists())
        self.assertEqual(self.revision, armed_revision + 1)
        self.assertEqual(self.grant()["status"], "CANCELLED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_nonempty_preissue_journal_denies_duplicate_collector(self) -> None:
        grant = self.grant_request()
        self.arm(grant)
        armed_revision = self.revision
        journal = runtime_broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )
        journal.append(
            "ACL_SNAPSHOT",
            "first-collector",
            protected_acl_snapshot=[],
            protected_acl_snapshot_sha256=engine.canonical_json_sha256([]),
        )
        journal_before = journal.path.read_bytes()
        version_result = subprocess.CompletedProcess(
            [str(Path(sys.executable).resolve()), "--version"],
            0,
            stdout=b"Python 0.0.0-test\n",
            stderr=b"",
        )

        with (
            mock.patch.object(
                runtime_broker,
                "windows_identity",
                return_value=("fixture\\broker", BROKER_SID),
            ),
            mock.patch.object(
                runtime_broker.subprocess, "run", return_value=version_result
            ),
            mock.patch.object(
                runtime_broker, "_snapshot_protected_acls"
            ) as snapshot,
            mock.patch.object(
                runtime_broker, "_configure_protected_dacls"
            ) as configure,
            mock.patch.object(
                runtime_broker.BrokerJournal, "ensure_file"
            ) as ensure_file,
        ):
            with self.assertRaisesRegex(
                runtime_broker.BrokerAuthorizationError,
                "empty preissue journal",
            ):
                runtime_broker.collect_proposal_isolation_evidence(
                    store=self.store,
                    case_id=self.case_id,
                    grant_core=grant,
                )

        ensure_file.assert_not_called()
        snapshot.assert_not_called()
        configure.assert_not_called()
        self.assertEqual(journal.path.read_bytes(), journal_before)
        self.assertEqual(self.revision, armed_revision)
        self.assertEqual(self.grant()["status"], "ARMED")
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_wrong_principal_is_rejected_before_runtime_or_action_paths(self) -> None:
        grant = self.grant_request()
        with (
            mock.patch.object(
                runtime_broker,
                "windows_identity",
                return_value=("fixture\\worker", WORKER_SID),
            ),
            mock.patch.object(
                runtime_broker,
                "file_sha256",
                side_effect=AssertionError("executable path must remain untouched"),
            ),
            mock.patch.object(
                runtime_broker.subprocess,
                "run",
                side_effect=AssertionError("version probe must not start"),
            ),
        ):
            with self.assertRaisesRegex(
                runtime_broker.BrokerAuthorizationError,
                "exact broker principal",
            ):
                runtime_broker.collect_proposal_isolation_evidence(
                    store=self.store,
                    case_id=self.case_id,
                    grant_core=grant,
                )

    def test_proposal_entrypoint_rejects_wrong_principal_before_case_store(self) -> None:
        grant = self.grant_request()
        before_revision = self.revision
        before_target_sha256 = hashlib.sha256(self.target.read_bytes()).hexdigest()
        grant_core = {
            field: grant[field] for field in proposal_entrypoint.GRANT_CORE_FIELDS
        }
        envelope_root = self.state_root / "proposal-envelopes"
        envelope_root.mkdir(exist_ok=True)
        envelope_path = envelope_root / "wrong-principal.json"
        envelope_path.write_text(
            json.dumps(
                {
                    "protocol_version": proposal_entrypoint.ENVELOPE_PROTOCOL_VERSION,
                    "schema_version": 1,
                    "case_id": self.case_id,
                    "expected_case_revision": self.revision,
                    "request_id": request_id(),
                    "grant": grant_core,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        with (
            mock.patch.object(
                runtime_broker,
                "windows_identity",
                return_value=("fixture\\worker", WORKER_SID),
            ),
            mock.patch.object(
                proposal_entrypoint,
                "CaseStore",
                side_effect=AssertionError("case store must remain untouched"),
            ),
            mock.patch.object(
                proposal_entrypoint,
                "collect_proposal_isolation_evidence",
            ) as collect_evidence,
            mock.patch.object(
                proposal_entrypoint,
                "execute_proposal_grant",
            ) as execute_grant,
            mock.patch.object(
                proposal_entrypoint,
                "recover_completed_action_grant_cleanup",
            ) as recover_cleanup,
        ):
            with self.assertRaisesRegex(
                runtime_broker.BrokerAuthorizationError,
                "exact broker principal",
            ):
                proposal_entrypoint.execute_envelope(
                    self.state_root,
                    envelope_path,
                )
        collect_evidence.assert_not_called()
        execute_grant.assert_not_called()
        recover_cleanup.assert_not_called()
        self.assertEqual(self.revision, before_revision)
        self.assertEqual(
            hashlib.sha256(self.target.read_bytes()).hexdigest(),
            before_target_sha256,
        )
        self.assertFalse((self.probe_runtime_root / "version-probe").exists())

    def test_v2_post_replacement_rechecks_dacl_without_worker_probe(self) -> None:
        self.issue()
        grant = self.grant()
        self.target.write_bytes(REPLACEMENT_BYTES)
        dacl = self.dacl_evidence(grant)
        with (
            mock.patch.object(runtime_broker, "inspect_proposal_dacls", return_value=dacl),
            mock.patch.object(
                runtime_broker,
                "_run_fixed_sandbox_probe",
                side_effect=AssertionError("proposal v2 must not launch a sandbox"),
            ),
        ):
            evidence = runtime_broker._collect_post_replacement_isolation_evidence(
                self.store,
                grant,
                run_id="proposal-post-dacl-test",
            )
        self.assertEqual(evidence["schema_version"], 2)
        self.assertEqual(
            evidence["evidence_mode"], engine.PROPOSAL_DACL_EVIDENCE_MODE
        )
        self.assertNotIn("membership_evidence", evidence)
        self.assertNotIn("isolation_evidence", evidence)
        self.assertEqual(evidence["dacl_evidence"], dacl)

    def test_proposal_acl_inventory_allows_only_snapshot_and_exact_artifacts(self) -> None:
        self.issue()
        grant = self.grant()
        roots = runtime_broker._protected_roots(grant)
        snapshot_paths = {item["path"] for item in grant["protected_acl_snapshot"]}
        artifact_root = self.state_root / engine.ACTION_ARTIFACT_DIRECTORY
        artifact_directory = artifact_root / self.case_id
        required_paths = {
            engine.normalize_binding("worktree", str(artifact_root)),
            engine.normalize_binding("worktree", str(artifact_directory)),
            engine.normalize_binding(
                "worktree", str(self.state_root / grant["sealed_artifact_path"])
            ),
            engine.normalize_binding(
                "worktree", str(self.state_root / grant["sealed_baseline_path"])
            ),
        }
        inventory = [
            {
                "path": path,
                "object_type": "directory" if Path(path).is_dir() else "file",
                "scope": "descendant",
            }
            for path in sorted(snapshot_paths | required_paths)
        ]
        with mock.patch.object(
            runtime_broker, "_protected_acl_inventory", return_value=inventory
        ):
            accepted = runtime_broker._validate_proposal_acl_inventory(
                grant, roots
            )
        self.assertEqual(accepted, inventory)

        unexpected = [
            *inventory,
            {
                "path": engine.normalize_binding(
                    "worktree", str(self.repository_root / "unexpected.bin")
                ),
                "object_type": "file",
                "scope": "descendant",
            },
        ]
        with mock.patch.object(
            runtime_broker, "_protected_acl_inventory", return_value=unexpected
        ):
            with self.assertRaisesRegex(
                runtime_broker.BrokerAuthorizationError, "inventory differs"
            ):
                runtime_broker._validate_proposal_acl_inventory(grant, roots)

    def test_proposal_acl_refresh_configures_once_then_verifies_exact_replay(self) -> None:
        self.issue()
        grant = self.grant()
        journal = runtime_broker.BrokerJournal(
            self.state_root, self.case_id, self.grant_id
        )
        inventory = [
            {
                "path": grant["worktree"],
                "object_type": "directory",
                "scope": "root",
            }
        ]
        dacl = self.dacl_evidence(grant)
        with (
            mock.patch.object(
                runtime_broker,
                "_validate_proposal_acl_inventory",
                return_value=inventory,
            ),
            mock.patch.object(
                runtime_broker,
                "_protected_acl_inventory",
                return_value=inventory,
            ),
            mock.patch.object(
                runtime_broker, "_configure_protected_dacls"
            ) as configure,
            mock.patch.object(
                runtime_broker, "inspect_proposal_dacls", return_value=dacl
            ),
        ):
            first = runtime_broker._refresh_proposal_acl_lockdown(
                grant,
                journal,
                run_id="proposal-refresh-run",
                stage="post_issuance",
            )
            second = runtime_broker._refresh_proposal_acl_lockdown(
                grant,
                journal,
                run_id="proposal-refresh-replay",
                stage="post_issuance",
            )
        configure.assert_called_once()
        self.assertFalse(first["already_refreshed"])
        self.assertTrue(second["already_refreshed"])
        refreshes = [
            record
            for record in journal.records()
            if record["event"] == "ACL_LOCKDOWN_REFRESHED"
        ]
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(refreshes[0]["refresh_stage"], "post_issuance")
        self.assertEqual(
            refreshes[0]["protected_object_inventory_sha256"],
            engine.canonical_json_sha256(inventory),
        )

    def test_completed_proposal_cleanup_uses_claimed_stage_binding(self) -> None:
        _journal, claimed_grant = self.complete_for_cleanup_recovery(
            refresh_run_id="proposal-completion-run",
            post_run_id="proposal-completion-run",
        )
        self.assertNotEqual(
            claimed_grant["grant_sha256"], self.grant()["grant_sha256"]
        )
        recovered = self.recover_completed_cleanup()
        self.assertEqual(recovered["status"], "recovered_completed")

    def test_completed_proposal_cleanup_allows_split_restart_run_ids(self) -> None:
        self.complete_for_cleanup_recovery(
            refresh_run_id="before-crash",
            post_run_id="after-restart",
        )
        recovered = self.recover_completed_cleanup()
        self.assertEqual(recovered["status"], "recovered_completed")

    def test_completed_proposal_cleanup_rejects_wrong_claimed_grant_digest(self) -> None:
        self.complete_for_cleanup_recovery(
            refresh_run_id="proposal-wrong-grant",
            post_run_id="proposal-wrong-grant",
            refresh_overrides={"grant_sha256": "1" * 64},
        )
        with self.assertRaisesRegex(
            runtime_broker.BrokerAuthorizationError,
            "post-isolation journal evidence is invalid",
        ):
            self.recover_completed_cleanup()

    def test_completed_proposal_cleanup_rejects_wrong_claim_digest(self) -> None:
        self.complete_for_cleanup_recovery(
            refresh_run_id="proposal-wrong-claim",
            post_run_id="proposal-wrong-claim",
            refresh_overrides={"claim_sha256": "2" * 64},
        )
        with self.assertRaisesRegex(
            runtime_broker.BrokerAuthorizationError,
            "post-isolation journal evidence is invalid",
        ):
            self.recover_completed_cleanup()

    def test_actor_thread_and_app_server_fields_are_rejected(self) -> None:
        initial_case = self.case
        forbidden_fields = (
            ("actor_thread_id", "implementation-child"),
            ("actor_turn_id", "implementation-turn"),
            ("app_server_sha256", "a" * 64),
        )
        for location in ("grant", "authority"):
            for field, value in forbidden_fields:
                with self.subTest(location=location, field=field):
                    grant = self.grant_request()
                    if location == "grant":
                        grant[field] = value
                    else:
                        grant["authority"][field] = value
                        grant["authority_sha256"] = engine.canonical_json_sha256(
                            grant["authority"]
                        )
                    with self.assertRaises(engine.ValidationError):
                        self.store._normalize_proposal_action_grant_request(
                            self.case_id, grant
                        )
                    self.assertEqual(self.case, initial_case)

    def test_stale_issue_revision_is_rejected_without_mutation(self) -> None:
        initial_case = self.case
        target_sha256 = engine.file_sha256(self.target)
        with self.assertRaises(engine.RevisionConflict):
            self.issue(expected_revision=self.revision - 1)
        self.assertEqual(self.case, initial_case)
        self.assertEqual(engine.file_sha256(self.target), target_sha256)

    def test_wrong_proposal_hash_is_rejected_without_case_or_target_mutation(self) -> None:
        grant = self.grant_request()
        initial_case = self.case
        target_sha256 = engine.file_sha256(self.target)
        self.proposal.write_bytes(b"x" * len(REPLACEMENT_BYTES))
        with self.assertRaisesRegex(engine.AuthorizationError, "digest"):
            self.issue(grant)
        self.assertEqual(self.case, initial_case)
        self.assertEqual(engine.file_sha256(self.target), target_sha256)

    def test_claim_uses_authority_digest_once_and_replay_is_denied(self) -> None:
        self.issue()
        authority_sha256 = self.grant()["authority_sha256"]
        claimed = self.claim()
        self.assertEqual(claimed["status"], "CLAIMED")
        self.assertEqual(self.grant()["claim"]["authority_sha256"], authority_sha256)
        claimed_case = self.case
        with self.assertRaisesRegex(
            engine.AuthorizationError, "blocked while an action grant is CLAIMED"
        ):
            self.claim()
        self.assertEqual(self.case, claimed_case)
        self.assertEqual(self.target.read_bytes(), BASELINE_BYTES)

    def test_claim_rejects_revision_drift_without_consuming_grant(self) -> None:
        self.issue()
        issued_case = self.case
        with self.assertRaises(engine.RevisionConflict):
            self.claim(expected_revision=self.revision - 1)
        self.assertEqual(self.case, issued_case)
        self.assertEqual(self.grant()["status"], "ISSUED")

    def test_completion_records_no_actor_thread_turn_or_app_server_identity(self) -> None:
        self.issue()
        self.claim()
        self.target.write_bytes(REPLACEMENT_BYTES)
        grant = self.grant()
        completed = self.store.complete_action_grant(
            self.case_id,
            completion={
                "protocol_version": engine.PROPOSAL_ACTION_RESULT_PROTOCOL_VERSION,
                "schema_version": 2,
                "grant_id": self.grant_id,
                "authority_sha256": grant["authority_sha256"],
                "broker_principal_sid": BROKER_SID,
                "post_replacement_evidence_sha256": "d" * 64,
                "completed_at": engine.utc_now(),
            },
            request_id=request_id(),
            expected_revision=self.revision,
        )
        self.assertEqual(completed["status"], "COMPLETED")
        result = self.grant()["result"]
        self.assertEqual(result["authority_sha256"], grant["authority_sha256"])
        for field in (
            "actor_thread_id",
            "actor_turn_id",
            "thread_id",
            "turn_id",
            "app_server_sha256",
            "app_server_executable_path",
            "app_server_version",
        ):
            self.assertNotIn(field, result)
        self.assertEqual(self.case["runtime"]["actors"], {})

    def test_legacy_v1_issuance_is_disabled_by_default(self) -> None:
        initial_case = self.case
        with self.assertRaisesRegex(engine.AuthorizationError, "disabled"):
            self.store.issue_action_grant(
                self.case_id,
                grant={},
                request_id=request_id(),
                expected_revision=self.revision,
            )
        self.assertEqual(self.case, initial_case)

    def test_proposal_broker_cli_exposes_only_state_root_and_envelope(self) -> None:
        parser = proposal_entrypoint.build_parser()
        parsed = parser.parse_args(
            ["--state-root", str(self.state_root), "--envelope", "grant.json"]
        )
        self.assertEqual(parsed.state_root, self.state_root)
        for forbidden in (
            "--target",
            "--replacement",
            "--command",
            "--role",
            "--thread",
            "--controller-receipt-json",
        ):
            with self.subTest(forbidden=forbidden), self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "--state-root",
                        str(self.state_root),
                        "--envelope",
                        "grant.json",
                        forbidden,
                        "arbitrary",
                    ]
                )

    def test_runtime_broker_exposes_narrow_orphan_recovery_cli(self) -> None:
        parser = runtime_broker.build_parser()
        parsed = parser.parse_args(
            [
                "recover-orphaned",
                "--state-root",
                str(self.state_root),
                "--case-id",
                self.case_id,
                "--grant-id",
                self.grant_id,
            ]
        )
        self.assertEqual(parsed.command, "recover-orphaned")
        self.assertEqual(parsed.state_root, self.state_root)
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "recover-orphaned",
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

    def test_proposal_broker_loads_path_typed_envelope_from_cli(self) -> None:
        grant = self.grant_request()
        grant_core = {
            field: grant[field] for field in proposal_entrypoint.GRANT_CORE_FIELDS
        }
        envelope_root = self.state_root / "proposal-envelopes"
        envelope_root.mkdir()
        envelope_path = envelope_root / "grant.json"
        envelope_path.write_text(
            json.dumps(
                {
                    "protocol_version": proposal_entrypoint.ENVELOPE_PROTOCOL_VERSION,
                    "schema_version": 1,
                    "case_id": self.case_id,
                    "expected_case_revision": self.revision,
                    "request_id": request_id(),
                    "grant": grant_core,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        loaded = proposal_entrypoint._load_envelope(
            self.state_root, envelope_path
        )

        self.assertEqual(loaded["case_id"], self.case_id)
        self.assertEqual(loaded["grant"], grant_core)
        self.assertEqual(
            loaded["envelope_path"],
            engine.normalize_binding("worktree", str(envelope_path)),
        )

    def test_proposal_broker_does_not_import_controller_or_supervisor(self) -> None:
        source = (SCRIPT_DIRECTORY / "case_proposal_action_broker.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("case_app_server_controller", source)
        self.assertNotIn("case_runtime_supervisor", source)
        self.assertNotIn("CONTROLLER_KEY_ENVIRONMENT", source)


if __name__ == "__main__":
    unittest.main()
