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

    def issue(self, grant: dict | None = None, *, expected_revision: int | None = None) -> dict:
        with mock.patch.object(
            engine, "proposal_broker_source_pins", return_value=self.source_pins
        ):
            return self.store.issue_proposal_action_grant(
                self.case_id,
                grant=grant or self.grant_request(),
                request_id=request_id(),
                expected_revision=(
                    self.revision if expected_revision is None else expected_revision
                ),
            )

    def grant(self) -> dict:
        return self.case["runtime"]["action_grants"][self.grant_id]

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
                    self.issue(grant)
                self.assertEqual(self.revision, before_revision)
                self.assertEqual(self.case["runtime"]["action_grants"], {})
        grant = self.grant_request()
        grant["evidence_mode"] = "nested_sandbox_v1"
        grant["authority"]["evidence_mode"] = "nested_sandbox_v1"
        grant["authority_sha256"] = engine.canonical_json_sha256(grant["authority"])
        with self.assertRaises(engine.AuthorizationError):
            self.issue(grant)

    def test_v2_rejects_incomplete_broker_dacl_without_issuance(self) -> None:
        grant = self.grant_request()
        grant["preissue_dacl_evidence"]["rules"].pop()
        grant["preissue_dacl_evidence_sha256"] = engine.canonical_json_sha256(
            grant["preissue_dacl_evidence"]
        )
        before_revision = self.revision
        with self.assertRaises(engine.ValidationError):
            self.issue(grant)
        self.assertEqual(self.revision, before_revision)
        self.assertEqual(self.case["runtime"]["action_grants"], {})
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

        self.assertEqual(self.revision, before_revision)
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
                        self.issue(grant)
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
