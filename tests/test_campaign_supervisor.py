#!/usr/bin/env python3
"""Deterministic integration tests for the campaign supervisor."""

from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts.agent.campaign_engine.ed25519 import verify as verify_ed25519
from scripts.agent.campaign_engine.effects import ExternalEffectDriver
from scripts.agent.campaign_engine.evidence import (
    HeadRaceError,
    HostedEvidenceError,
    publication_preflight,
)
from scripts.agent.campaign_engine.host import (
    FakeHost,
    HostProtocolError,
    LateResultError,
)
from scripts.agent.campaign_engine.model import (
    ActorRole,
    AuthorityError,
    BudgetToken,
    CampaignSpec,
    CampaignState,
    EffectState,
    EvidenceKind,
    Event,
    EventType,
    NodeState,
    RevisionConflict,
    canonical_json,
    canonical_json_digest,
)
from scripts.agent.campaign_engine.store import CampaignStore
from scripts.agent.campaign_engine.supervisor import (
    AttemptAlreadyReserved,
    DeterministicSupervisor,
    SupervisorDeadlineExceeded,
    SupervisorError,
)
from tests.ed25519_test_helper import (
    forge_mixed_order_signature,
    public_key_from_seed,
    sign,
    verifies_without_subgroup_checks,
)


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
AUTHORIZATION_PRIVATE_SEED = b"\x01" * 32
AUTHORIZATION_PUBLIC_KEY_BASE64 = base64.b64encode(
    public_key_from_seed(AUTHORIZATION_PRIVATE_SEED)
).decode("ascii")


class StrictEd25519VerifierTests(unittest.TestCase):
    RFC_PUBLIC_KEY_1 = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a"
        "0ee172f3daa62325af021a68f707511a"
    )
    RFC_SIGNATURE_1 = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a"
        "84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46b"
        "d25bf5f0595bbe24655141438e7a100b"
    )
    RFC_PUBLIC_KEY_2 = bytes.fromhex(
        "3d4017c3e843895a92b70aa74d1b7ebc"
        "9c982ccf2ec4968cc0cd55f12af4660c"
    )
    RFC_SIGNATURE_2 = bytes.fromhex(
        "92a009a9f0d4cab8720e820b5f642540"
        "a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c"
        "387b2eaeb4302aeeb00d291612bb0c00"
    )
    GROUP_ORDER = 2**252 + 27742317777372353535851937790883648493
    FIELD_PRIME = 2**255 - 19

    def test_accepts_rfc_8032_vectors_and_rejects_mutations(self) -> None:
        self.assertTrue(
            verify_ed25519(self.RFC_PUBLIC_KEY_1, self.RFC_SIGNATURE_1, b"")
        )
        self.assertTrue(
            verify_ed25519(self.RFC_PUBLIC_KEY_2, self.RFC_SIGNATURE_2, b"\x72")
        )

        changed_signature = bytearray(self.RFC_SIGNATURE_1)
        changed_signature[0] ^= 1
        self.assertFalse(
            verify_ed25519(self.RFC_PUBLIC_KEY_1, bytes(changed_signature), b"")
        )
        self.assertFalse(
            verify_ed25519(self.RFC_PUBLIC_KEY_2, self.RFC_SIGNATURE_2, b"\x73")
        )

    def test_rejects_malformed_and_noncanonical_encodings(self) -> None:
        self.assertFalse(
            verify_ed25519(self.RFC_PUBLIC_KEY_1[:-1], self.RFC_SIGNATURE_1, b"")
        )
        self.assertFalse(
            verify_ed25519(self.RFC_PUBLIC_KEY_1, self.RFC_SIGNATURE_1[:-1], b"")
        )

        noncanonical_y = self.FIELD_PRIME.to_bytes(32, "little")
        self.assertFalse(verify_ed25519(noncanonical_y, self.RFC_SIGNATURE_1, b""))
        self.assertFalse(
            verify_ed25519(
                self.RFC_PUBLIC_KEY_1,
                noncanonical_y + self.RFC_SIGNATURE_1[32:],
                b"",
            )
        )

        invalid_curve_point = (2).to_bytes(32, "little")
        self.assertFalse(
            verify_ed25519(invalid_curve_point, self.RFC_SIGNATURE_1, b"")
        )
        impossible_x_sign = b"\x01" + b"\x00" * 30 + b"\x80"
        self.assertFalse(
            verify_ed25519(impossible_x_sign, self.RFC_SIGNATURE_1, b"")
        )

    def test_rejects_noncanonical_scalar(self) -> None:
        noncanonical = self.RFC_SIGNATURE_1[:32] + self.GROUP_ORDER.to_bytes(
            32, "little"
        )
        self.assertFalse(verify_ed25519(self.RFC_PUBLIC_KEY_1, noncanonical, b""))

    def test_rejects_small_order_and_mixed_order_forged_keys(self) -> None:
        identity_key = b"\x01" + b"\x00" * 31
        base_point = bytes.fromhex("58" + "66" * 31)
        identity_forgery = base_point + (1).to_bytes(32, "little")
        self.assertTrue(
            verifies_without_subgroup_checks(identity_key, identity_forgery, b"forged")
        )
        self.assertFalse(verify_ed25519(identity_key, identity_forgery, b"forged"))

        order_two_key = (self.FIELD_PRIME - 1).to_bytes(32, "little")
        self.assertFalse(verify_ed25519(order_two_key, self.RFC_SIGNATURE_1, b""))
        small_order_r = identity_key + self.RFC_SIGNATURE_1[32:]
        self.assertFalse(verify_ed25519(self.RFC_PUBLIC_KEY_1, small_order_r, b""))

        mixed_key, mixed_signature = forge_mixed_order_signature(
            b"\x03" * 32, b"mixed-order forgery"
        )
        self.assertTrue(
            verifies_without_subgroup_checks(
                mixed_key, mixed_signature, b"mixed-order forgery"
            )
        )
        self.assertFalse(
            verify_ed25519(mixed_key, mixed_signature, b"mixed-order forgery")
        )


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


class FakePublicationBackend:
    """Confirm effects only after recording one deterministic execution."""

    def __init__(self) -> None:
        self.executions: list[tuple[str, dict[str, object]]] = []
        self.queries: list[tuple[str, dict[str, object]]] = []
        self.confirmed: set[tuple[str, str]] = set()

    @staticmethod
    def _key(kind: str, payload: dict[str, object]) -> tuple[str, str]:
        return kind, str(payload["candidate_head"])

    def execute(self, kind: str, payload) -> dict[str, object]:
        copied = dict(payload)
        self.executions.append((kind, copied))
        self.confirmed.add(self._key(kind, copied))
        return {"executed": True, "kind": kind}

    def query(self, kind: str, payload) -> dict[str, object]:
        copied = dict(payload)
        self.queries.append((kind, copied))
        return {
            "confirmed": self._key(kind, copied) in self.confirmed,
            "kind": kind,
        }


class FailSecondActorHost(FakeHost):
    """Inject a deterministic failure after one cohort member starts."""

    def __init__(self) -> None:
        super().__init__()
        self.create_attempts: list[str] = []

    def create_idle_actor(
        self,
        lease,
        *,
        bind_authority,
        authorize_action=None,
        ephemeral=False,
    ):
        self.create_attempts.append(lease.lease_id)
        if len(self.create_attempts) == 2:
            raise RuntimeError("injected second actor creation failure")
        return super().create_idle_actor(
            lease,
            bind_authority=bind_authority,
            authorize_action=authorize_action,
            ephemeral=ephemeral,
        )


class TimeoutOnceHost(FakeHost):
    def __init__(self) -> None:
        super().__init__()
        self.collection_attempts = 0

    def collect_terminal_receipt(self, lease_id, *, current_epochs, timeout=None):
        self.collection_attempts += 1
        if self.collection_attempts == 1:
            raise HostProtocolError("injected transport timeout")
        return super().collect_terminal_receipt(
            lease_id, current_epochs=current_epochs, timeout=timeout
        )


class SupervisorFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "tests@example.invalid")
        git(self.repo, "config", "user.name", "Campaign Supervisor Tests")
        git(
            self.repo,
            "remote",
            "add",
            "origin",
            "https://example.invalid/acme/campaign.git",
        )
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.txt").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base_sha = git(self.repo, "rev-parse", "HEAD").casefold()
        self.store = CampaignStore(self.root / "state" / "campaigns.sqlite3")
        self.addCleanup(self.store.close)
        self.sequence = 0

    def make_spec(
        self,
        campaign_id: str,
        *,
        mode: str = "MANUAL",
        publication_automated: bool = False,
        budget_limit: int = 20,
        budget_overrides: dict[BudgetToken, int] | None = None,
        required_hosted_checks: tuple[str, ...] = (),
        campaign_deadline: str = "2026-08-06T00:00:00Z",
        node_deadline: str = "2026-08-05T00:00:00Z",
    ) -> CampaignSpec:
        overrides = budget_overrides or {}
        repo_path = str(self.repo.resolve(strict=True))
        budgets = [
            {"token": token.value, "limit": overrides.get(token, budget_limit)}
            for token in BudgetToken
        ]
        return CampaignSpec.from_dict(
            {
                "campaign_id": campaign_id,
                "specification_revision": 1,
                "authority_epoch": 7,
                "cancellation_epoch": 2,
                "mode": mode,
                "objective": "exercise the deterministic supervisor",
                "objective_kind": "CONTROL_RUNTIME",
                "repository_remote": "https://example.invalid/acme/campaign.git",
                "git_root": repo_path,
                "worktree": repo_path,
                "branch": "main",
                "base_sha": self.base_sha,
                "allowed_paths": ["src/**"],
                "nodes": [
                    {
                        "node_id": "node-1",
                        "objective": "change the approved file",
                        "allowed_paths": ["src/**"],
                        "validation_command_ids": ["unit"],
                        "deadline_utc": node_deadline,
                    }
                ],
                "required_validation_commands": [
                    {
                        "command_id": "unit",
                        "executable": sys.executable,
                        "arguments": ["-B", "-c", "print('validated')"],
                        "working_directory": repo_path,
                        "environment_allowlist": ["PATH", "SYSTEMROOT"],
                        "timeout_seconds": 30,
                        "output_limit_bytes": 100_000,
                        "expected_worktree_condition": "CLEAN",
                        "required_exit_code": 0,
                    }
                ],
                "required_review_cohort": ["reviewer-a", "reviewer-b"],
                "publication_authority": {
                    "authorized_by": "test-owner",
                    "human_authorization": {
                        "algorithm": "ED25519",
                        "public_key_base64": AUTHORIZATION_PUBLIC_KEY_BASE64,
                    },
                    "automated": publication_automated,
                    "allowed_effects": [
                        "PUSH",
                        "CREATE_PULL_REQUEST",
                        "MERGE",
                    ],
                    "required_effects": [
                        "PUSH",
                        "CREATE_PULL_REQUEST",
                        "MERGE",
                    ],
                    "required_hosted_checks": list(required_hosted_checks),
                },
                "attempt_budgets": budgets,
                "stop_conditions": ["STOP", "budget exhausted", "deadline exceeded"],
                "installed_source_commit": "b" * 40,
                "installed_bundle_digest": "c" * 64,
                "install_transaction": "install-test",
                "protocol_version": "ccos-campaign-v1",
                "schema_compatibility": "campaign-store-v1",
                "host_capability_probe_version": "native-bind-before-turn-scoped-tools-v3",
                "autonomous_rank": sum(item["limit"] for item in budgets),
                "deadline_utc": campaign_deadline,
            }
        )

    def event(
        self,
        snapshot,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict | None = None,
    ) -> Event:
        self.sequence += 1
        return Event(
            event_id=f"fixture-event-{self.sequence}",
            campaign_id=snapshot.spec.campaign_id,
            event_type=event_type,
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
            node_id=node_id,
            payload=payload or {},
        )

    @staticmethod
    def authorization_receipt(snapshot, node_id: str, event_kind: str, receipt_id: str):
        node = snapshot.node(node_id)
        receipt = {
            "receipt_id": receipt_id,
            "authorized_by": "test-owner",
            "signature_algorithm": "ED25519",
            "event_kind": event_kind,
            "campaign_id": snapshot.spec.campaign_id,
            "specification_digest": snapshot.spec.specification_digest,
            "specification_revision": snapshot.spec.specification_revision,
            "store_revision": snapshot.revision,
            "authority_epoch": snapshot.authority_epoch,
            "cancellation_epoch": snapshot.cancellation_epoch,
            "node_id": node_id,
            "candidate_head": node.candidate_head,
            "frozen_blocker_ids": [
                finding.finding_id for finding in node.findings if finding.blocking
            ],
        }
        receipt["signature_base64"] = base64.b64encode(
            sign(
                AUTHORIZATION_PRIVATE_SEED,
                canonical_json(receipt).encode("utf-8")
            )
        ).decode("ascii")
        return receipt

    def create_approved(self, spec: CampaignSpec):
        snapshot = self.store.create_campaign(spec)
        return self.store.apply_event(
            self.event(
                snapshot,
                EventType.APPROVE,
                payload={"specification_digest": spec.specification_digest},
            )
        )[0]

    def create_manual_candidate(self, campaign_id: str = "campaign"):
        spec = self.make_spec(campaign_id, mode="MANUAL")
        snapshot = self.create_approved(spec)
        candidate_payload = {
            "candidate_head": self.base_sha,
            "candidate_tree": git(self.repo, "rev-parse", "HEAD^{tree}").casefold(),
            "candidate_diff_digest": "d" * 64,
            "candidate_node_diff_digest": "d" * 64,
        }
        for event_type, payload in (
            (EventType.START, {}),
            (EventType.ADMIT_NODE, {"start_head": self.base_sha}),
            (EventType.START_IMPLEMENTATION, {}),
            (EventType.IMPLEMENTATION_COMPLETED, {}),
            (EventType.VALIDATION_PASSED, candidate_payload),
        ):
            snapshot = self.store.apply_event(
                self.event(
                    snapshot,
                    event_type,
                    node_id=None if event_type is EventType.START else "node-1",
                    payload=payload,
                )
            )[0]
        return spec, snapshot

    def create_manual_closure(self, campaign_id: str = "campaign"):
        spec, snapshot = self.create_manual_candidate(campaign_id)
        blocker = {
            "finding_id": "F-1",
            "title": "repair the frozen blocker",
            "blocking": True,
            "details": {"candidate_head": self.base_sha},
        }
        events = (
            (
                EventType.START_REVIEW,
                {
                    "review_id": f"review-{campaign_id}",
                    "review_cohort": list(spec.required_review_cohort),
                },
            ),
            (EventType.FREEZE_FINDINGS, {"findings": [blocker]}),
            (EventType.START_REPAIR, {}),
            (EventType.REPAIR_COMPLETED, {}),
            (
                EventType.REVALIDATION_PASSED,
                {
                    "candidate_head": self.base_sha,
                    "candidate_tree": git(
                        self.repo, "rev-parse", "HEAD^{tree}"
                    ).casefold(),
                    "candidate_diff_digest": "e" * 64,
                    "candidate_node_diff_digest": "e" * 64,
                },
            ),
        )
        for event_type, payload in events[:2]:
            snapshot = self.store.apply_event(
                self.event(
                    snapshot,
                    event_type,
                    node_id="node-1",
                    payload=payload,
                )
            )[0]
        receipt = self.authorization_receipt(
            snapshot, "node-1", "repair_authorized", f"repair-auth-{campaign_id}"
        )
        receipt_digest = canonical_json_digest(receipt)
        event = self.event(
            snapshot,
            EventType.AUTHORIZE_REPAIR,
            node_id="node-1",
            payload={
                "finding_ids": ["F-1"],
                "authorization_receipt_id": receipt["receipt_id"],
                "authorization_receipt_digest": receipt_digest,
            },
        )
        snapshot = self.store.apply_human_authorized_event(event, receipt)[0]
        for event_type, payload in events[2:]:
            snapshot = self.store.apply_event(
                self.event(snapshot, event_type, node_id="node-1", payload=payload)
            )[0]
        return spec, snapshot

    def commit_text(self, text: str, message: str) -> str:
        (self.repo / "src" / "app.txt").write_text(text, encoding="utf-8")
        git(self.repo, "add", "src/app.txt")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD").casefold()

    @staticmethod
    def budget_consumed(snapshot) -> dict[BudgetToken, int]:
        return {balance.token: balance.consumed for balance in snapshot.budgets}

    def dispatch_implementer(
        self,
        supervisor: DeterministicSupervisor,
        campaign_id: str = "campaign",
    ):
        self.assertEqual(supervisor.step(campaign_id).action, "CAMPAIGN_STARTED")
        self.assertEqual(supervisor.step(campaign_id).action, "NODE_ADMITTED")
        decision = supervisor.step(campaign_id)
        self.assertEqual(decision.action, "IMPLEMENTER_DISPATCHED")
        return str(decision.details["lease_id"])

    @staticmethod
    def hosted_check_payload(
        candidate_head: str,
        *,
        status: str,
        conclusion: str | None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol_version": "ccos-hosted-checks-v1",
            "repository": "acme/campaign",
            "candidate_head": candidate_head,
            "checks": [
                {
                    "id": 1,
                    "name": "product-quality",
                    "head_sha": candidate_head,
                    "status": status,
                    "conclusion": conclusion,
                    "details_url": "https://example.invalid/checks/1",
                    "completed_at": (
                        "2026-08-04T12:00:00Z" if status == "completed" else None
                    ),
                }
            ],
        }
        payload["evidence_sha256"] = canonical_json_digest(payload)
        return payload

    def advance_to_merge_preflight(
        self,
        supervisor: DeterministicSupervisor,
        host: FakeHost,
        backend: FakePublicationBackend,
        *,
        campaign_id: str = "campaign",
    ) -> str:
        implementer = self.dispatch_implementer(supervisor, campaign_id)
        host.write_file(
            implementer,
            "src/app.txt",
            f"candidate for {campaign_id}\n".encode(),
        )
        git(self.repo, "add", "src/app.txt")
        git(self.repo, "commit", "-q", "-m", f"candidate {campaign_id}")
        candidate_head = git(self.repo, "rev-parse", "HEAD").casefold()
        host.set_result(implementer, {"status": "completed", "action_count": 1})
        supervisor.complete_worker(implementer)
        self.assertEqual(supervisor.step(campaign_id).action, "CANDIDATE_FROZEN")
        spec = self.store.get_snapshot(campaign_id).spec
        receipts, findings = self.collect_passing_review(
            supervisor, host, spec, campaign_id=campaign_id
        )
        self.assertEqual(
            supervisor.freeze_review(
                campaign_id,
                "node-1",
                receipts=receipts,
                findings=findings,
            ).action,
            "FINDINGS_FROZEN",
        )
        self.assertEqual(supervisor.step(campaign_id).action, "READY_TO_PUBLISH")
        for expected_kind in ("PUSH", "CREATE_PULL_REQUEST"):
            prepared = supervisor.step(campaign_id)
            self.assertEqual(prepared.action, "PUBLICATION_PREPARED")
            self.assertEqual(prepared.details["effect_kind"], expected_kind)
            self.assertEqual(supervisor.step(campaign_id).action, "PUBLICATION_CONFIRMED")
        self.assertEqual(
            [kind for kind, _ in backend.executions],
            ["PUSH", "CREATE_PULL_REQUEST"],
        )
        return candidate_head

    def collect_passing_review(
        self,
        supervisor: DeterministicSupervisor,
        host: FakeHost,
        spec: CampaignSpec,
        *,
        campaign_id: str = "campaign",
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        review = supervisor.begin_review(campaign_id, "node-1")
        candidate_head = str(
            self.store.get_snapshot(campaign_id).node("node-1").candidate_head
        )
        leases = list(review.details["leases"])
        for reviewer_id, lease_id in zip(spec.required_review_cohort, leases):
            host.set_result(
                str(lease_id),
                {
                    "status": "completed",
                    "reviewer_id": reviewer_id,
                    "candidate_head": candidate_head,
                    "findings": [],
                    "verdict": "PASS",
                },
            )
        return supervisor.collect_review_cohort(leases)


class CampaignSupervisorLifecycleTests(SupervisorFixture):
    def test_two_disjoint_nodes_validate_from_immutable_node_start_and_publish(self) -> None:
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "guide.txt").write_text("base guide\n", encoding="utf-8")
        git(self.repo, "add", "docs/guide.txt")
        git(self.repo, "commit", "-q", "-m", "add guide base")
        self.base_sha = git(self.repo, "rev-parse", "HEAD").casefold()

        raw = self.make_spec(
            "campaign", mode="AUTOMATED", publication_automated=True
        ).to_dict()
        raw.pop("specification_digest", None)
        raw["base_sha"] = self.base_sha
        raw["allowed_paths"] = ["src/**", "docs/**"]
        raw["nodes"] = [
            {
                "node_id": "source",
                "objective": "change source",
                "allowed_paths": ["src/**"],
                "validation_command_ids": ["unit"],
            },
            {
                "node_id": "docs",
                "objective": "change docs after source publication",
                "dependencies": ["source"],
                "allowed_paths": ["docs/**"],
                "validation_command_ids": ["unit"],
            },
        ]
        raw["publication_authority"]["allowed_effects"] = ["PUSH"]
        raw["publication_authority"]["required_effects"] = ["PUSH"]
        spec = CampaignSpec.from_dict(raw)
        self.create_approved(spec)
        host = FakeHost()
        backend = FakePublicationBackend()
        supervisor = DeterministicSupervisor(
            self.store,
            host=host,
            effect_driver=ExternalEffectDriver(self.store, backend),
            now=lambda: NOW,
        )

        self.assertEqual(supervisor.step("campaign").action, "CAMPAIGN_STARTED")
        first_admission = supervisor.step("campaign")
        self.assertEqual(first_admission.node_id, "source")
        self.assertEqual(
            self.store.get_snapshot("campaign").node("source").start_head,
            self.base_sha,
        )

        def implement_review_publish(
            node_id: str, relative_path: str, content: bytes, message: str
        ) -> str:
            dispatched = supervisor.step("campaign")
            self.assertEqual(dispatched.action, "IMPLEMENTER_DISPATCHED")
            lease_id = str(dispatched.details["lease_id"])
            host.write_file(lease_id, relative_path, content)
            git(self.repo, "add", relative_path)
            git(self.repo, "commit", "-q", "-m", message)
            head = git(self.repo, "rev-parse", "HEAD").casefold()
            host.set_result(lease_id, {"status": "completed", "action_count": 1})
            supervisor.complete_worker(lease_id)
            self.assertEqual(supervisor.step("campaign").action, "CANDIDATE_FROZEN")
            review = supervisor.step("campaign")
            self.assertEqual(review.action, "REVIEW_DISPATCHED")
            leases = list(review.details["leases"])
            for reviewer_id, reviewer_lease in zip(spec.required_review_cohort, leases):
                host.set_result(
                    str(reviewer_lease),
                    {
                        "status": "completed",
                        "reviewer_id": reviewer_id,
                        "candidate_head": head,
                        "findings": [],
                        "verdict": "PASS",
                    },
                )
            receipts, findings = supervisor.collect_review_cohort(leases)
            self.assertEqual(
                supervisor.freeze_review(
                    "campaign", node_id, receipts=receipts, findings=findings
                ).action,
                "FINDINGS_FROZEN",
            )
            self.assertEqual(supervisor.step("campaign").action, "READY_TO_PUBLISH")
            self.assertEqual(supervisor.step("campaign").action, "PUBLICATION_PREPARED")
            self.assertEqual(supervisor.step("campaign").action, "PUBLICATION_CONFIRMED")
            return head

        first_head = implement_review_publish(
            "source", "src/app.txt", b"source candidate\n", "source candidate"
        )
        second_admission = supervisor.step("campaign")
        self.assertEqual(second_admission.action, "NODE_ADMITTED")
        self.assertEqual(second_admission.node_id, "docs")
        restarted = self.store.get_snapshot("campaign")
        self.assertEqual(restarted.node("docs").start_head, first_head)

        second_head = implement_review_publish(
            "docs", "docs/guide.txt", b"docs candidate\n", "docs candidate"
        )
        terminal = supervisor.step("campaign")
        self.assertEqual(terminal.campaign_state, CampaignState.COMPLETED.value)
        node = self.store.get_snapshot("campaign").node("docs")
        self.assertEqual(node.candidate_head, second_head)
        self.assertNotEqual(node.candidate_node_diff_digest, node.candidate_diff_digest)
        with closing(sqlite3.connect(self.store.path)) as connection:
            payloads = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM evidence WHERE campaign_id=? "
                    "AND node_id=? AND kind='REPOSITORY'",
                    ("campaign", "docs"),
                )
            ]
        self.assertEqual(len(payloads), 2)
        self.assertEqual(
            {payload["base_sha"] for payload in payloads},
            {self.base_sha, first_head},
        )
        local = next(payload for payload in payloads if payload["base_sha"] == first_head)
        self.assertEqual(
            [entry.split("\t")[-1] for entry in local["changed_entries"]],
            ["docs/guide.txt"],
        )

    def test_human_receipts_are_exact_one_use_and_concurrent_safe(self) -> None:
        spec, snapshot = self.create_manual_candidate("campaign")
        blocker = {"finding_id": "F-1", "title": "repair", "blocking": True}
        for event_type, payload in (
            (
                EventType.START_REVIEW,
                {
                    "review_id": "human-auth-review",
                    "review_cohort": list(spec.required_review_cohort),
                },
            ),
            (EventType.FREEZE_FINDINGS, {"findings": [blocker]}),
        ):
            snapshot = self.store.apply_event(
                self.event(snapshot, event_type, node_id="node-1", payload=payload)
            )[0]
        receipt = self.authorization_receipt(
            snapshot, "node-1", "repair_authorized", "one-use-repair"
        )
        barrier = threading.Barrier(3)
        outcomes: list[str] = []
        outcome_lock = threading.Lock()

        def authorize() -> None:
            supervisor = DeterministicSupervisor(
                self.store, host=FakeHost(), now=lambda: NOW
            )
            barrier.wait()
            try:
                supervisor.authorize_repair(
                    "campaign", "node-1", authorization_receipt=receipt
                )
                outcome = "success"
            except (AuthorityError, RevisionConflict):
                outcome = "denied"
            with outcome_lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=authorize) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), ["denied", "success"])
        current = self.store.get_snapshot("campaign")
        self.assertEqual(current.node("node-1").state, NodeState.REPAIR_AUTHORIZED)
        with closing(sqlite3.connect(self.store.path)) as connection:
            consumed = connection.execute(
                "SELECT COUNT(*) FROM operations WHERE request_id=? "
                "AND kind='HUMAN_AUTHORIZATION:AUTHORIZE_REPAIR' "
                "AND status='CONFIRMED'",
                (receipt["receipt_id"],),
            ).fetchone()[0]
        self.assertEqual(consumed, 1)

    def test_publication_authorization_binds_candidate_and_survives_reload(self) -> None:
        spec, snapshot = self.create_manual_candidate("publication-auth")
        for event_type, payload in (
            (
                EventType.START_REVIEW,
                {
                    "review_id": "publication-auth-review",
                    "review_cohort": list(spec.required_review_cohort),
                },
            ),
            (EventType.FREEZE_FINDINGS, {"findings": []}),
            (EventType.MARK_READY_TO_PUBLISH, {}),
        ):
            snapshot = self.store.apply_event(
                self.event(snapshot, event_type, node_id="node-1", payload=payload)
            )[0]
        receipt = self.authorization_receipt(
            snapshot,
            "node-1",
            "publication_authorized",
            "one-use-publication",
        )
        encoded_signature = receipt["signature_base64"]
        base64_alphabet = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        )
        final_value_index = base64_alphabet.index(encoded_signature[-3])
        self.assertEqual(final_value_index % 16, 0)
        noncanonical_encoding = dict(receipt)
        noncanonical_encoding["signature_base64"] = (
            encoded_signature[:-3]
            + base64_alphabet[final_value_index + 1]
            + "=="
        )
        self.assertEqual(
            base64.b64decode(
                noncanonical_encoding["signature_base64"], validate=True
            ),
            base64.b64decode(encoded_signature, validate=True),
        )
        forged = dict(receipt)
        forged.pop("signature_base64")
        forged["signature_base64"] = base64.b64encode(
            sign(b"\x02" * 32, canonical_json(forged).encode("utf-8"))
        ).decode("ascii")
        supervisor = DeterministicSupervisor(
            self.store, host=FakeHost(), now=lambda: NOW
        )
        with self.assertRaisesRegex(AuthorityError, "encoding is invalid"):
            supervisor.authorize_publication(
                "publication-auth",
                "node-1",
                authorization_receipt=noncanonical_encoding,
            )
        with self.assertRaisesRegex(AuthorityError, "signature is invalid"):
            supervisor.authorize_publication(
                "publication-auth", "node-1", authorization_receipt=forged
            )
        decision = supervisor.authorize_publication(
            "publication-auth", "node-1", authorization_receipt=receipt
        )
        self.assertEqual(decision.action, "PUBLICATION_AUTHORIZED")
        reloaded = self.store.get_snapshot("publication-auth").node("node-1")
        self.assertEqual(
            reloaded.publication_authorization_receipt_id, "one-use-publication"
        )
        with self.assertRaises(AuthorityError):
            supervisor.authorize_publication(
                "publication-auth", "node-1", authorization_receipt=receipt
            )

    def test_full_clean_lifecycle_uses_fake_host_and_ordered_publication(self) -> None:
        spec = self.make_spec(
            "campaign", mode="AUTOMATED", publication_automated=True
        )
        initial_rank = self.create_approved(spec).autonomous_rank_remaining
        host = FakeHost()
        backend = FakePublicationBackend()
        preflight_budget_observations: list[int] = []

        def tracked_preflight(*args, **kwargs):
            preflight_budget_observations.append(
                self.budget_consumed(self.store.get_snapshot("campaign"))[
                    BudgetToken.VALIDATION_EXECUTION
                ]
            )
            return publication_preflight(*args, **kwargs)

        supervisor = DeterministicSupervisor(
            self.store,
            host=host,
            effect_driver=ExternalEffectDriver(self.store, backend),
            publication_preflight_runner=tracked_preflight,
            now=lambda: NOW,
        )

        implementer = self.dispatch_implementer(supervisor)
        self.assertEqual(host.bindings[implementer].lease.role, "IMPLEMENTER")
        self.assertEqual(host.bindings[implementer].sandbox_type, "read-only")
        self.assertIn(implementer, host.action_authorities)
        host.write_file(implementer, "src/app.txt", b"candidate\n")
        git(self.repo, "add", "src/app.txt")
        git(self.repo, "commit", "-q", "-m", "candidate")
        candidate_head = git(self.repo, "rev-parse", "HEAD").casefold()
        host.set_result(implementer, {"status": "completed", "action_count": 1})
        supervisor.complete_worker(implementer)

        frozen = supervisor.step("campaign")
        self.assertEqual(frozen.action, "CANDIDATE_FROZEN")
        self.assertEqual(
            self.store.get_snapshot("campaign").node("node-1").candidate_head,
            candidate_head,
        )
        review = supervisor.step("campaign")
        self.assertEqual(review.action, "REVIEW_DISPATCHED")
        leases = list(review.details["leases"])
        self.assertEqual(len(leases), 2)
        for reviewer_id, lease_id in zip(spec.required_review_cohort, leases):
            binding = host.bindings[str(lease_id)]
            self.assertEqual(binding.lease.role, "REVIEWER")
            self.assertEqual(binding.lease.candidate_head, candidate_head)
            self.assertEqual(binding.sandbox_type, "read-only")
            with self.assertRaisesRegex(PermissionError, "read-only"):
                host.write_file(str(lease_id), "src/reviewer.txt", b"denied")
            host.set_result(
                str(lease_id),
                {
                    "status": "completed",
                    "reviewer_id": reviewer_id,
                    "candidate_head": candidate_head,
                    "findings": [],
                    "verdict": "PASS",
                },
            )
        receipts, findings = supervisor.collect_review_cohort(leases)
        freeze = supervisor.freeze_review(
            "campaign", "node-1", receipts=receipts, findings=findings
        )
        self.assertEqual(freeze.action, "FINDINGS_FROZEN")
        self.assertIsNone(freeze.wait_event)
        self.assertEqual(supervisor.step("campaign").action, "READY_TO_PUBLISH")

        observed: list[str] = []
        for expected_kind in ("PUSH", "CREATE_PULL_REQUEST", "MERGE"):
            prepared = supervisor.step("campaign")
            self.assertEqual(prepared.action, "PUBLICATION_PREPARED")
            self.assertEqual(prepared.details["effect_kind"], expected_kind)
            confirmed = supervisor.step("campaign")
            self.assertEqual(confirmed.action, "PUBLICATION_CONFIRMED")
            observed.append(expected_kind)
        self.assertEqual(observed, ["PUSH", "CREATE_PULL_REQUEST", "MERGE"])
        terminal = supervisor.step("campaign")
        self.assertEqual(terminal.action, "TERMINAL")
        self.assertEqual(terminal.campaign_state, CampaignState.COMPLETED.value)
        self.assertEqual([kind for kind, _ in backend.executions], observed)
        self.assertEqual(preflight_budget_observations, [2, 3, 4])
        self.assertTrue(
            all(payload["candidate_head"] == candidate_head for _, payload in backend.executions)
        )
        outbox = self.store.list_outbox(campaign_id="campaign")
        self.assertEqual([item["kind"] for item in outbox], observed)
        self.assertTrue(all(item["state"] == EffectState.CONFIRMED.value for item in outbox))
        snapshot = self.store.get_snapshot("campaign")
        expected_consumption = {
            BudgetToken.CHILD_CREATION: 3,
            BudgetToken.CHILD_START: 3,
            BudgetToken.VALIDATION_EXECUTION: 4,
            BudgetToken.REVIEW_DISPATCH: 1,
            BudgetToken.PUSH: 1,
            BudgetToken.PULL_REQUEST_CREATION: 1,
            BudgetToken.MERGE: 1,
        }
        consumed = self.budget_consumed(snapshot)
        for token in BudgetToken:
            self.assertEqual(
                consumed[token], expected_consumption.get(token, 0), token.value
            )
        expected_receipts = sum(expected_consumption.values())
        self.assertEqual(
            snapshot.autonomous_rank_remaining, initial_rank - expected_receipts
        )
        self.assertEqual(len(snapshot.budget_receipts), expected_receipts)
        self.assertEqual(self.store.list_active_leases("campaign"), [])

    def test_one_repair_revalidates_and_binds_closure_to_repaired_head(self) -> None:
        spec = self.make_spec("campaign", mode="MANUAL")
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)

        implementer = self.dispatch_implementer(supervisor)
        host.write_file(implementer, "src/app.txt", b"candidate with defect\n")
        git(self.repo, "add", "src/app.txt")
        git(self.repo, "commit", "-q", "-m", "candidate")
        candidate_head = git(self.repo, "rev-parse", "HEAD").casefold()
        host.set_result(implementer, {"status": "completed"})
        supervisor.complete_worker(implementer)
        self.assertEqual(supervisor.step("campaign").action, "CANDIDATE_FROZEN")
        review = supervisor.step("campaign")

        wrong_cohort = [
            {"reviewer_id": "reviewer-a", "candidate_head": candidate_head}
        ]
        with self.assertRaisesRegex(SupervisorError, "exact frozen cohort"):
            supervisor.freeze_review(
                "campaign", "node-1", receipts=wrong_cohort, findings=[]
            )
        wrong_head = [
            {"reviewer_id": reviewer, "candidate_head": self.base_sha}
            for reviewer in spec.required_review_cohort
        ]
        with self.assertRaisesRegex(SupervisorError, "another candidate head"):
            supervisor.freeze_review(
                "campaign", "node-1", receipts=wrong_head, findings=[]
            )

        blocker = {
            "finding_id": "F-1",
            "title": "repair the deterministic defect",
            "blocking": True,
            "details": {"candidate_head": candidate_head},
        }
        leases = list(review.details["leases"])
        for index, (reviewer_id, lease_id) in enumerate(
            zip(spec.required_review_cohort, leases)
        ):
            host.set_result(
                str(lease_id),
                {
                    "status": "completed",
                    "reviewer_id": reviewer_id,
                    "candidate_head": candidate_head,
                    "findings": [blocker] if index == 0 else [],
                    "verdict": "BLOCK" if index == 0 else "PASS",
                },
            )
        receipts, findings = supervisor.collect_review_cohort(leases)
        frozen = supervisor.freeze_review(
            "campaign", "node-1", receipts=receipts, findings=findings
        )
        self.assertEqual(frozen.wait_event, "repair_authorized")
        self.assertEqual(frozen.details["blocking_finding_ids"], ["F-1"])
        repair_receipt = self.authorization_receipt(
            self.store.get_snapshot("campaign"),
            "node-1",
            "repair_authorized",
            "repair-auth-campaign",
        )
        self.assertEqual(
            supervisor.authorize_repair(
                "campaign", "node-1", authorization_receipt=repair_receipt
            ).action,
            "REPAIR_AUTHORIZED",
        )

        repair = supervisor.step("campaign")
        self.assertEqual(repair.action, "REPAIRER_DISPATCHED")
        repair_lease = str(repair.details["lease_id"])
        self.assertEqual(host.bindings[repair_lease].lease.candidate_head, candidate_head)
        host.write_file(repair_lease, "src/app.txt", b"repaired candidate\n")
        git(self.repo, "add", "src/app.txt")
        git(self.repo, "commit", "-q", "-m", "repair")
        repaired_head = git(self.repo, "rev-parse", "HEAD").casefold()
        self.assertNotEqual(repaired_head, candidate_head)
        host.set_result(repair_lease, {"status": "completed"})
        supervisor.complete_worker(repair_lease)
        revalidated = supervisor.step("campaign")
        self.assertEqual(revalidated.action, "CLOSURE_READY")
        node = self.store.get_snapshot("campaign").node("node-1")
        self.assertEqual(node.state, NodeState.CLOSURE)
        self.assertEqual(node.candidate_head, repaired_head)

        closure = supervisor.step("campaign")
        self.assertEqual(closure.action, "CLOSURE_DISPATCHED")
        closure_leases = list(closure.details["leases"])
        for reviewer_id, lease_id in zip(spec.required_review_cohort, closure_leases):
            binding = host.bindings[str(lease_id)]
            self.assertEqual(binding.lease.role, "CLOSURE_REVIEWER")
            self.assertEqual(binding.lease.candidate_head, repaired_head)
            self.assertEqual(binding.sandbox_type, "read-only")
            with self.assertRaisesRegex(PermissionError, "read-only"):
                host.write_file(str(lease_id), "src/closure.txt", b"denied")
            host.set_result(
                str(lease_id),
                {
                    "status": "completed",
                    "reviewer_id": reviewer_id,
                    "candidate_head": repaired_head,
                    "resolved_finding_ids": ["F-1"],
                    "findings": [],
                    "verdict": "PASS",
                },
            )
        closure_receipts, closure_findings = supervisor.collect_review_cohort(
            closure_leases
        )
        self.assertTrue(
            all(item["resolved_finding_ids"] == ["F-1"] for item in closure_receipts)
        )
        completed = supervisor.complete_closure(
            "campaign",
            "node-1",
            resolved_finding_ids=["F-1"],
            findings=closure_findings,
            receipts=closure_receipts,
        )
        self.assertEqual(completed.action, "CLOSURE_PASSED")
        self.assertEqual(
            self.store.get_snapshot("campaign").node("node-1").candidate_head,
            repaired_head,
        )
        waiting = supervisor.step("campaign")
        self.assertEqual(waiting.action, "YIELD")
        self.assertEqual(waiting.wait_event, "publication_authorized")
        self.assertEqual(self.store.get_snapshot("campaign").budget_receipts, ())


class CampaignSupervisorControlTests(SupervisorFixture):
    def test_review_prompt_requests_the_schema_the_finding_model_accepts(self) -> None:
        _, snapshot = self.create_manual_candidate()
        for role in (ActorRole.REVIEWER, ActorRole.CLOSURE_REVIEWER):
            with self.subTest(role=role.value):
                prompt = DeterministicSupervisor._worker_prompt(
                    snapshot, "node-1", role, "reviewer-a"
                )
                for field in ("finding_id", "title", "blocking", "details"):
                    self.assertIn(field, prompt)
                self.assertNotIn(
                    "finding_id, severity, blocking, summary, and evidence", prompt
                )

    def test_fabricated_review_receipts_cannot_freeze_findings(self) -> None:
        spec, _ = self.create_manual_candidate()
        supervisor = DeterministicSupervisor(
            self.store, host=FakeHost(), now=lambda: NOW
        )
        review = supervisor.begin_review("campaign", "node-1")
        fabricated = [
            {
                "reviewer_id": reviewer_id,
                "candidate_head": self.base_sha,
                "receipt_digest": "f" * 64,
                "verdict": "PASS",
            }
            for reviewer_id in spec.required_review_cohort
        ]
        with self.assertRaisesRegex(SupervisorError, "receipt|evidence"):
            supervisor.freeze_review(
                "campaign", "node-1", receipts=fabricated, findings=[]
            )
        self.assertEqual(
            self.store.get_snapshot("campaign").node("node-1").state,
            NodeState.CHECKS_AND_REVIEW,
        )
        self.assertEqual(
            {lease.lease_id for lease in self.store.list_active_leases("campaign")},
            set(review.details["leases"]),
        )

    def test_review_freeze_requires_completed_native_terminal_attestation(self) -> None:
        spec, _ = self.create_manual_candidate()
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        receipts, findings = self.collect_passing_review(supervisor, host, spec)
        first = receipts[0]
        evidence = self.store.find_evidence_by_digest(
            "campaign",
            "node-1",
            str(first["receipt_digest"]),
            kind=EvidenceKind.REVIEW,
            candidate_head=str(first["candidate_head"]),
        )
        self.assertEqual(len(evidence), 1)
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute(
                "DELETE FROM operations WHERE request_id=?",
                (f"attest:{evidence[0].evidence_id}",),
            )
            connection.commit()

        with self.assertRaisesRegex(SupervisorError, "native terminal attestation"):
            supervisor.freeze_review(
                "campaign", "node-1", receipts=receipts, findings=findings
            )
        self.assertEqual(
            self.store.get_snapshot("campaign").node("node-1").state,
            NodeState.CHECKS_AND_REVIEW,
        )

    def test_review_freeze_requires_actor_principal_binding(self) -> None:
        spec, _ = self.create_manual_candidate()
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        receipts, findings = self.collect_passing_review(supervisor, host, spec)
        first = receipts[0]
        evidence = self.store.find_evidence_by_digest(
            "campaign",
            "node-1",
            str(first["receipt_digest"]),
            kind=EvidenceKind.REVIEW,
            candidate_head=str(first["candidate_head"]),
        )
        self.assertEqual(len(evidence), 1)
        actor_id = str(evidence[0].payload["actor_id"])
        with closing(sqlite3.connect(self.store.path)) as connection:
            row = connection.execute(
                "SELECT actor_json FROM actors WHERE actor_id=?", (actor_id,)
            ).fetchone()
            self.assertIsNotNone(row)
            actor = json.loads(str(row[0]))
            actor["principal_id"] = "forged-reviewer"
            connection.execute(
                "UPDATE actors SET actor_json=? WHERE actor_id=?",
                (json.dumps(actor, sort_keys=True, separators=(",", ":")), actor_id),
            )
            connection.commit()

        with self.assertRaisesRegex(SupervisorError, "actor authority"):
            supervisor.freeze_review(
                "campaign", "node-1", receipts=receipts, findings=findings
            )
        self.assertEqual(
            self.store.get_snapshot("campaign").node("node-1").state,
            NodeState.CHECKS_AND_REVIEW,
        )

    def test_required_hosted_checks_consume_one_wakeup_then_yield_pending(self) -> None:
        spec = self.make_spec(
            "campaign",
            mode="AUTOMATED",
            publication_automated=True,
            required_hosted_checks=("product-quality",),
        )
        self.create_approved(spec)
        host = FakeHost()
        backend = FakePublicationBackend()
        wakeups: list[str] = []

        def pending_checks(_root, *, repository, candidate_head):
            wakeups.append(candidate_head)
            return self.hosted_check_payload(
                candidate_head, status="in_progress", conclusion=None
            )

        supervisor = DeterministicSupervisor(
            self.store,
            host=host,
            effect_driver=ExternalEffectDriver(self.store, backend),
            hosted_check_collector=pending_checks,
            now=lambda: NOW,
        )
        self.advance_to_merge_preflight(supervisor, host, backend)

        decision = supervisor.step("campaign")

        self.assertEqual(decision.action, "HOSTED_CHECKS_PENDING")
        self.assertEqual(decision.wait_event, "hosted_checks")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(
            self.budget_consumed(snapshot)[BudgetToken.HOSTED_CHECK_WAKEUP], 1
        )
        self.assertNotIn(
            "MERGE",
            [item["kind"] for item in self.store.list_outbox(campaign_id="campaign")],
        )
        repeated = supervisor.step("campaign")
        self.assertEqual(repeated.action, "HOSTED_CHECKS_PENDING")
        self.assertEqual(len(wakeups), 1)
        self.assertEqual(
            self.budget_consumed(self.store.get_snapshot("campaign"))[
                BudgetToken.HOSTED_CHECK_WAKEUP
            ],
            1,
        )

        named = supervisor.start_publication(
            "campaign", "node-1", hosted_wakeup_id="hosted-event-1"
        )
        self.assertEqual(named.action, "HOSTED_CHECKS_PENDING")
        self.assertEqual(len(wakeups), 2)
        self.assertEqual(
            self.budget_consumed(self.store.get_snapshot("campaign"))[
                BudgetToken.HOSTED_CHECK_WAKEUP
            ],
            2,
        )
        replayed = supervisor.start_publication(
            "campaign", "node-1", hosted_wakeup_id="hosted-event-1"
        )
        self.assertEqual(replayed.action, "HOSTED_CHECKS_PENDING")
        self.assertEqual(len(wakeups), 2)
        self.assertEqual(
            self.budget_consumed(self.store.get_snapshot("campaign"))[
                BudgetToken.HOSTED_CHECK_WAKEUP
            ],
            2,
        )

    def test_required_hosted_checks_consume_one_wakeup_then_yield_unavailable(self) -> None:
        spec = self.make_spec(
            "campaign",
            mode="AUTOMATED",
            publication_automated=True,
            required_hosted_checks=("product-quality",),
        )
        self.create_approved(spec)
        host = FakeHost()
        backend = FakePublicationBackend()

        def unavailable(*_args, **_kwargs):
            raise HostedEvidenceError("hosted transport unavailable")

        supervisor = DeterministicSupervisor(
            self.store,
            host=host,
            effect_driver=ExternalEffectDriver(self.store, backend),
            hosted_check_collector=unavailable,
            now=lambda: NOW,
        )
        self.advance_to_merge_preflight(supervisor, host, backend)

        decision = supervisor.step("campaign")

        self.assertEqual(decision.action, "HOSTED_CHECKS_UNAVAILABLE")
        self.assertEqual(decision.wait_event, "hosted_checks")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(
            self.budget_consumed(snapshot)[BudgetToken.HOSTED_CHECK_WAKEUP], 1
        )
        self.assertNotIn(
            "MERGE",
            [item["kind"] for item in self.store.list_outbox(campaign_id="campaign")],
        )

    def test_completed_hosted_check_failure_fails_exact_node_with_evidence(self) -> None:
        spec = self.make_spec(
            "campaign",
            mode="AUTOMATED",
            publication_automated=True,
            required_hosted_checks=("product-quality",),
        )
        self.create_approved(spec)
        host = FakeHost()
        backend = FakePublicationBackend()
        collected: dict[str, object] = {}

        def failing(_root, *, repository, candidate_head):
            del repository
            collected.update(
                self.hosted_check_payload(
                    candidate_head, status="completed", conclusion="failure"
                )
            )
            return dict(collected)

        supervisor = DeterministicSupervisor(
            self.store,
            host=host,
            effect_driver=ExternalEffectDriver(self.store, backend),
            hosted_check_collector=failing,
            now=lambda: NOW,
        )
        candidate_head = self.advance_to_merge_preflight(supervisor, host, backend)

        decision = supervisor.step("campaign")

        self.assertEqual(decision.action, "PUBLICATION_PREFLIGHT_FAILED")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(snapshot.node("node-1").state, NodeState.FAILED_EXACT_NODE)
        self.assertEqual(
            len(
                self.store.find_evidence_by_digest(
                    "campaign",
                    "node-1",
                    str(collected["evidence_sha256"]),
                    kind=EvidenceKind.HOSTED_CHECK,
                    candidate_head=candidate_head,
                )
            ),
            1,
        )
        self.assertNotIn("MERGE", [kind for kind, _ in backend.executions])

    def test_hosted_check_head_race_fails_exact_node(self) -> None:
        spec = self.make_spec(
            "campaign",
            mode="AUTOMATED",
            publication_automated=True,
            required_hosted_checks=("product-quality",),
        )
        self.create_approved(spec)
        host = FakeHost()
        backend = FakePublicationBackend()

        def head_race(*_args, **_kwargs):
            raise HeadRaceError("hosted evidence belongs to another head")

        supervisor = DeterministicSupervisor(
            self.store,
            host=host,
            effect_driver=ExternalEffectDriver(self.store, backend),
            hosted_check_collector=head_race,
            now=lambda: NOW,
        )
        self.advance_to_merge_preflight(supervisor, host, backend)

        decision = supervisor.step("campaign")

        self.assertEqual(decision.action, "PUBLICATION_PREFLIGHT_FAILED")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(snapshot.node("node-1").state, NodeState.FAILED_EXACT_NODE)
        self.assertNotIn("MERGE", [kind for kind, _ in backend.executions])

    def test_passing_hosted_checks_are_persisted_before_merge_execution(self) -> None:
        spec = self.make_spec(
            "campaign",
            mode="AUTOMATED",
            publication_automated=True,
            required_hosted_checks=("product-quality",),
        )
        self.create_approved(spec)
        host = FakeHost()
        backend = FakePublicationBackend()
        collected: dict[str, object] = {}

        def passing(_root, *, repository, candidate_head):
            del repository
            collected.update(
                self.hosted_check_payload(
                    candidate_head, status="completed", conclusion="success"
                )
            )
            return dict(collected)

        supervisor = DeterministicSupervisor(
            self.store,
            host=host,
            effect_driver=ExternalEffectDriver(self.store, backend),
            hosted_check_collector=passing,
            now=lambda: NOW,
        )
        candidate_head = self.advance_to_merge_preflight(supervisor, host, backend)

        decision = supervisor.step("campaign")

        self.assertEqual(decision.action, "PUBLICATION_PREPARED")
        self.assertEqual(decision.details["effect_kind"], "MERGE")
        self.assertEqual(
            len(
                self.store.find_evidence_by_digest(
                    "campaign",
                    "node-1",
                    str(collected["evidence_sha256"]),
                    kind=EvidenceKind.HOSTED_CHECK,
                    candidate_head=candidate_head,
                )
            ),
            1,
        )
        merge = self.store.get_effect(str(decision.details["operation_id"]))
        self.assertEqual(merge["state"], EffectState.PREPARED.value)
        self.assertNotIn("MERGE", [kind for kind, _ in backend.executions])

    def test_closure_cannot_pass_before_exact_cohort_is_dispatched_and_collected(self) -> None:
        self.create_manual_closure()
        supervisor = DeterministicSupervisor(
            self.store, host=FakeHost(), now=lambda: NOW
        )
        with self.assertRaisesRegex(SupervisorError, "closure|cohort|receipt"):
            supervisor.complete_closure(
                "campaign",
                "node-1",
                resolved_finding_ids=["F-1"],
                findings=[],
            )
        self.assertEqual(
            self.store.get_snapshot("campaign").node("node-1").state,
            NodeState.CLOSURE,
        )
        self.assertEqual(self.store.list_active_leases("campaign"), [])

    def test_partial_review_dispatch_cleans_leases_and_fails_exact_node(self) -> None:
        self.create_manual_candidate()
        host = FailSecondActorHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        with self.assertRaisesRegex(RuntimeError, "second actor creation"):
            supervisor.begin_review("campaign", "node-1")
        self.assertEqual(len(host.create_attempts), 2)
        self.assertTrue(set(host.create_attempts).issubset(host.interrupted))
        self.assertEqual(self.store.list_active_leases("campaign"), [])
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(snapshot.node("node-1").state, NodeState.FAILED_EXACT_NODE)
        self.assertIn("cohort", snapshot.failure_reason.casefold())
        with self.assertRaisesRegex(SupervisorError, "unknown in-process"):
            supervisor.complete_worker(host.create_attempts[0])

    def test_partial_review_budget_exhaustion_cleans_leases_and_fails_exact_node(self) -> None:
        spec = self.make_spec(
            "campaign",
            mode="AUTOMATED",
            budget_overrides={BudgetToken.CHILD_CREATION: 2},
        )
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)

        implementer = self.dispatch_implementer(supervisor)
        host.write_file(implementer, "src/app.txt", b"candidate\n")
        git(self.repo, "add", "src/app.txt")
        git(self.repo, "commit", "-q", "-m", "candidate")
        host.set_result(implementer, {"status": "completed"})
        supervisor.complete_worker(implementer)
        self.assertEqual(supervisor.step("campaign").action, "CANDIDATE_FROZEN")

        exhausted = supervisor.step("campaign")
        self.assertEqual(exhausted.action, "BUDGET_EXHAUSTED")

        reviewer_leases = [
            lease_id
            for lease_id, binding in host.bindings.items()
            if binding.lease.role == ActorRole.REVIEWER.value
        ]
        self.assertEqual(len(reviewer_leases), 1)
        self.assertIn(reviewer_leases[0], host.interrupted)
        self.assertEqual(self.store.list_active_leases("campaign"), [])
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(snapshot.node("node-1").state, NodeState.FAILED_EXACT_NODE)
        self.assertIn("budget exhausted", snapshot.failure_reason.casefold())
        self.assertEqual(
            self.budget_consumed(snapshot)[BudgetToken.REJECTED_ATTEMPT], 1
        )
        with self.assertRaisesRegex(SupervisorError, "unknown in-process"):
            supervisor.complete_worker(reviewer_leases[0])

    def test_validation_git_failure_releases_lease_and_fails_exact_node(self) -> None:
        spec = self.make_spec("campaign", mode="MANUAL")
        snapshot = self.create_approved(spec)
        for event_type in (
            EventType.START,
            EventType.ADMIT_NODE,
            EventType.START_IMPLEMENTATION,
            EventType.IMPLEMENTATION_COMPLETED,
        ):
            snapshot = self.store.apply_event(
                self.event(
                    snapshot,
                    event_type,
                    node_id=None if event_type is EventType.START else "node-1",
                    payload=(
                        {"start_head": self.base_sha}
                        if event_type is EventType.ADMIT_NODE
                        else None
                    ),
                )
            )[0]
        supervisor = DeterministicSupervisor(
            self.store, host=FakeHost(), now=lambda: NOW
        )
        failure = subprocess.CalledProcessError(
            128, ("git", "rev-parse", "HEAD")
        )
        with patch(
            "scripts.agent.campaign_engine.supervisor.subprocess.run",
            side_effect=failure,
        ):
            decision = supervisor.validate_node("campaign", "node-1")
        self.assertEqual(decision.action, "VALIDATION_FAILED")
        self.assertEqual(self.store.list_active_leases("campaign"), [])
        failed = self.store.get_snapshot("campaign")
        self.assertEqual(failed.state, CampaignState.FAILED)
        self.assertEqual(failed.node("node-1").state, NodeState.FAILED_EXACT_NODE)

    def test_nonzero_validation_receipt_is_persisted_before_exact_node_failure(self) -> None:
        raw_spec = self.make_spec("campaign", mode="MANUAL").to_dict()
        raw_spec.pop("specification_digest", None)
        raw_spec["required_validation_commands"][0]["arguments"] = [
            "-B",
            "-c",
            "print('assertions passed'); raise SystemExit(7)",
        ]
        spec = CampaignSpec.from_dict(raw_spec)
        snapshot = self.create_approved(spec)
        for event_type in (
            EventType.START,
            EventType.ADMIT_NODE,
            EventType.START_IMPLEMENTATION,
            EventType.IMPLEMENTATION_COMPLETED,
        ):
            snapshot = self.store.apply_event(
                self.event(
                    snapshot,
                    event_type,
                    node_id=None if event_type is EventType.START else "node-1",
                    payload=(
                        {"start_head": self.base_sha}
                        if event_type is EventType.ADMIT_NODE
                        else None
                    ),
                )
            )[0]
        decision = DeterministicSupervisor(
            self.store, host=FakeHost(), now=lambda: NOW
        ).validate_node("campaign", "node-1")
        self.assertEqual(decision.action, "VALIDATION_FAILED")
        with closing(sqlite3.connect(self.store.path)) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM evidence WHERE campaign_id=? AND kind='VALIDATION'",
                ("campaign",),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        retained = json.loads(rows[0][0])
        self.assertFalse(retained["passed"])
        self.assertEqual(retained["exit_code"], 7)

    def test_out_of_scope_validation_fails_exact_node_and_releases_lease(self) -> None:
        spec = self.make_spec("campaign", mode="MANUAL")
        snapshot = self.create_approved(spec)
        for event_type in (
            EventType.START,
            EventType.ADMIT_NODE,
            EventType.START_IMPLEMENTATION,
            EventType.IMPLEMENTATION_COMPLETED,
        ):
            snapshot = self.store.apply_event(
                self.event(
                    snapshot,
                    event_type,
                    node_id=None if event_type is EventType.START else "node-1",
                    payload=(
                        {"start_head": self.base_sha}
                        if event_type is EventType.ADMIT_NODE
                        else None
                    ),
                )
            )[0]
        (self.repo / "outside.txt").write_text("outside scope\n", encoding="utf-8")
        git(self.repo, "add", "outside.txt")
        git(self.repo, "commit", "-q", "-m", "outside scope")
        supervisor = DeterministicSupervisor(
            self.store, host=FakeHost(), now=lambda: NOW
        )
        decision = supervisor.validate_node("campaign", "node-1")
        self.assertEqual(decision.action, "VALIDATION_FAILED")
        self.assertEqual(self.store.list_active_leases("campaign"), [])
        failed = self.store.get_snapshot("campaign")
        self.assertEqual(failed.state, CampaignState.FAILED)
        self.assertIn("outside", failed.failure_reason.casefold())

    def test_deadline_with_active_worker_interrupts_and_fences_late_result(self) -> None:
        clock = [NOW]
        spec = self.make_spec(
            "campaign",
            mode="MANUAL",
            campaign_deadline="2026-08-06T00:00:00Z",
            node_deadline="2026-08-04T12:30:00Z",
        )
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(
            self.store, host=host, now=lambda: clock[0]
        )
        lease_id = self.dispatch_implementer(supervisor)
        host.set_result(lease_id, {"status": "completed"})
        clock[0] = datetime(2026, 8, 4, 12, 30, 1, tzinfo=timezone.utc)
        expired = supervisor.step("campaign")
        self.assertEqual(expired.action, "DEADLINE_EXCEEDED")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(snapshot.node("node-1").state, NodeState.FAILED_EXACT_NODE)
        self.assertIn(lease_id, host.interrupted)
        self.assertEqual(self.store.list_active_leases("campaign"), [])
        with self.assertRaisesRegex(LateResultError, "stale"):
            host.collect_terminal_receipt(
                lease_id, current_epochs=self.store.current_epochs
            )
        with self.assertRaisesRegex(SupervisorError, "unknown in-process"):
            supervisor.complete_worker(lease_id)

    def test_fake_host_write_callback_rejects_cancelled_and_stale_leases_before_write(
        self,
    ) -> None:
        host = FakeHost()
        supervisor = DeterministicSupervisor(
            self.store, host=host, now=lambda: NOW
        )
        original = (self.repo / "src" / "app.txt").read_bytes()

        stale_spec = self.make_spec("campaign", mode="MANUAL")
        self.create_approved(stale_spec)
        stale_lease = self.dispatch_implementer(supervisor)
        self.assertIn(stale_lease, host.action_authorities)
        current = self.store.get_snapshot(stale_spec.campaign_id)
        waiting = self.store.apply_event(
            self.event(current, EventType.WAIT_HUMAN)
        )[0]
        self.store.apply_event(
            self.event(
                waiting,
                EventType.ADVANCE_AUTHORITY,
                payload={"new_authority_epoch": waiting.authority_epoch + 1},
            )
        )
        with self.assertRaisesRegex(AuthorityError, "epochs are stale"):
            host.write_file(stale_lease, "src/app.txt", b"stale write\n")
        self.assertEqual((self.repo / "src" / "app.txt").read_bytes(), original)

        current = self.store.get_snapshot(stale_spec.campaign_id)
        self.store.cancel_campaign(
            stale_spec.campaign_id,
            request_id="cancel-before-mediated-write",
            expected_revision=current.revision,
            authority_epoch=current.authority_epoch,
            cancellation_epoch=current.cancellation_epoch,
        )
        with self.assertRaisesRegex(AuthorityError, "terminal"):
            host.write_file(stale_lease, "src/app.txt", b"cancelled write\n")
        self.assertEqual((self.repo / "src" / "app.txt").read_bytes(), original)

    def test_stop_interrupts_worker_and_denies_its_late_result(self) -> None:
        spec = self.make_spec("campaign", mode="AUTOMATED")
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease_id = self.dispatch_implementer(supervisor)
        host.set_result(lease_id, {"status": "completed"})

        stopped = supervisor.cancel("campaign", reason="STOP")
        self.assertEqual(stopped.action, "CANCELLED")
        self.assertEqual(stopped.campaign_state, CampaignState.CANCELLED.value)
        self.assertIn(lease_id, host.interrupted)
        self.assertEqual(self.store.list_active_leases("campaign"), [])
        with self.assertRaisesRegex(LateResultError, "stale"):
            host.collect_terminal_receipt(
                lease_id, current_epochs=self.store.current_epochs
            )
        with self.assertRaisesRegex(SupervisorError, "unknown in-process"):
            supervisor.complete_worker(lease_id)
        self.assertEqual(supervisor.step("campaign").action, "TERMINAL")
        self.assertEqual(self.store.telemetry_counts("campaign")["stopped"], 1)

    def test_restart_invalidates_orphaned_worker_and_fails_only_exact_node(self) -> None:
        spec = self.make_spec("campaign", mode="MANUAL")
        self.create_approved(spec)
        original_host = FakeHost()
        original = DeterministicSupervisor(
            self.store, host=original_host, now=lambda: NOW
        )
        self.dispatch_implementer(original)

        restarted = DeterministicSupervisor(
            self.store, host=FakeHost(), now=lambda: NOW
        )
        recovered = restarted.recover()
        self.assertEqual(recovered["orphaned_actor_identities"], 1)
        self.assertEqual(recovered["invalidated_leases"], 1)
        self.assertEqual(
            recovered["failed_nodes"],
            [{"campaign_id": "campaign", "node_id": "node-1"}],
        )
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(snapshot.node("node-1").state, NodeState.FAILED_EXACT_NODE)
        self.assertIn("orphaned", snapshot.failure_reason)
        repeated = restarted.recover()
        self.assertEqual(repeated["orphaned_actor_identities"], 0)
        self.assertEqual(repeated["invalidated_leases"], 0)
        self.assertEqual(repeated["failed_nodes"], [])

    def test_restart_queries_executing_publication_without_reexecution(self) -> None:
        spec = self.make_spec(
            "campaign",
            mode="MANUAL",
            publication_automated=True,
        )
        snapshot = self.create_approved(spec)
        for event_type, payload in (
            (EventType.START, {}),
            (EventType.ADMIT_NODE, {"start_head": self.base_sha}),
            (EventType.START_IMPLEMENTATION, {}),
            (EventType.IMPLEMENTATION_COMPLETED, {}),
            (
                EventType.VALIDATION_PASSED,
                {
                    "candidate_head": self.base_sha,
                    "candidate_tree": git(self.repo, "rev-parse", "HEAD^{tree}"),
                    "candidate_diff_digest": "d" * 64,
                    "candidate_node_diff_digest": "d" * 64,
                },
            ),
            (
                EventType.START_REVIEW,
                {
                    "review_id": "restart-review",
                    "review_cohort": list(spec.required_review_cohort),
                },
            ),
            (EventType.FREEZE_FINDINGS, {"findings": []}),
            (EventType.MARK_READY_TO_PUBLISH, {}),
        ):
            node_id = None if event_type is EventType.START else "node-1"
            snapshot = self.store.apply_event(
                self.event(snapshot, event_type, node_id=node_id, payload=payload)
            )[0]
        backend = FakePublicationBackend()
        driver = ExternalEffectDriver(self.store, backend)
        supervisor = DeterministicSupervisor(
            self.store, host=FakeHost(), effect_driver=driver, now=lambda: NOW
        )
        prepared = supervisor.start_publication("campaign", "node-1")
        operation_id = str(prepared.details["operation_id"])
        self.store.update_effect(
            operation_id,
            expected_state=EffectState.PREPARED.value,
            state=EffectState.EXECUTING.value,
        )
        backend.confirmed.add(("PUSH", self.base_sha))

        restarted = DeterministicSupervisor(
            self.store,
            host=FakeHost(),
            effect_driver=driver,
            now=lambda: NOW,
        )
        recovered = restarted.recover()
        self.assertEqual(recovered["ambiguous_effects"], 1)
        self.assertEqual(len(recovered["reconciled"]), 1)
        self.assertEqual(recovered["reconciled"][0]["state"], EffectState.CONFIRMED.value)
        self.assertEqual(backend.executions, [])
        self.assertEqual(self.store.get_effect(operation_id)["state"], EffectState.CONFIRMED.value)
        no_op = restarted.reconcile(operation_id)
        self.assertEqual(no_op["state"], EffectState.CONFIRMED.value)
        self.assertEqual(
            self.budget_consumed(self.store.get_snapshot("campaign"))[
                BudgetToken.NO_OP_ATTEMPT
            ],
            0,
        )
        advanced = restarted.step("campaign")
        self.assertEqual(advanced.action, "PUBLICATION_CONFIRMED")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.node("node-1").completed_publication_effects, ("PUSH",))
        self.assertEqual(snapshot.node("node-1").state, NodeState.READY_TO_PUBLISH)
        next_effect = restarted.step("campaign")
        self.assertEqual(next_effect.action, "PUBLICATION_PREPARED")
        self.assertEqual(next_effect.details["effect_kind"], "CREATE_PULL_REQUEST")
        self.assertEqual(backend.executions, [])

    def test_campaign_and_node_deadlines_fail_deterministically(self) -> None:
        expired_draft = self.make_spec(
            "expired-draft",
            campaign_deadline="2026-08-04T11:59:59Z",
            node_deadline="2026-08-05T00:00:00Z",
        )
        self.store.create_campaign(expired_draft)
        supervisor = DeterministicSupervisor(self.store, host=FakeHost(), now=lambda: NOW)
        with self.assertRaisesRegex(SupervisorDeadlineExceeded, "draft campaign"):
            supervisor.step("expired-draft")
        supervisor.cancel("expired-draft", reason="deadline-test-complete")

        expired_node = self.make_spec(
            "expired-node",
            campaign_deadline="2026-08-06T00:00:00Z",
            node_deadline="2026-08-04T11:59:59Z",
        )
        self.create_approved(expired_node)
        self.assertEqual(supervisor.step("expired-node").action, "CAMPAIGN_STARTED")
        self.assertEqual(supervisor.step("expired-node").action, "NODE_ADMITTED")
        decision = supervisor.step("expired-node")
        self.assertEqual(decision.action, "DEADLINE_EXCEEDED")
        snapshot = self.store.get_snapshot("expired-node")
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(snapshot.node("node-1").state, NodeState.FAILED_EXACT_NODE)

    def test_automated_dispatch_consumes_budgets_before_starting_host_turn(self) -> None:
        spec = self.make_spec("campaign", mode="AUTOMATED", budget_limit=3)
        approved = self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease_id = self.dispatch_implementer(supervisor)
        snapshot = self.store.get_snapshot("campaign")
        consumed = self.budget_consumed(snapshot)
        self.assertEqual(consumed[BudgetToken.CHILD_CREATION], 1)
        self.assertEqual(consumed[BudgetToken.CHILD_START], 1)
        self.assertEqual(snapshot.autonomous_rank_remaining, approved.autonomous_rank_remaining - 2)
        self.assertEqual(len(snapshot.budget_receipts), 2)
        self.assertEqual(len(host.turns), 1)
        self.assertIn(lease_id, host.bindings)

    def test_concurrent_dispatch_callers_cannot_share_one_budget_reservation(self) -> None:
        spec = self.make_spec("campaign", mode="AUTOMATED", budget_limit=3)
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        self.assertEqual(supervisor.step("campaign").action, "CAMPAIGN_STARTED")
        self.assertEqual(supervisor.step("campaign").action, "NODE_ADMITTED")
        barrier = threading.Barrier(3)
        actions: list[str] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def dispatch() -> None:
            barrier.wait()
            try:
                decision = supervisor.step("campaign")
                with lock:
                    actions.append(decision.action)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=dispatch) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(actions, ["IMPLEMENTER_DISPATCHED"])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AttemptAlreadyReserved)
        self.assertEqual(len(host.bindings), 1)
        self.assertEqual(len(host.turns), 1)
        consumed = self.budget_consumed(self.store.get_snapshot("campaign"))
        self.assertEqual(consumed[BudgetToken.CHILD_CREATION], 1)
        self.assertEqual(consumed[BudgetToken.CHILD_START], 1)

    def test_concurrent_validation_callers_cannot_share_one_executor_token(self) -> None:
        spec = self.make_spec("campaign", mode="AUTOMATED", budget_limit=3)
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease_id = self.dispatch_implementer(supervisor)
        host.write_file(lease_id, "src/app.txt", b"candidate\n")
        git(self.repo, "add", "src/app.txt")
        git(self.repo, "commit", "-q", "-m", "candidate")
        host.set_result(lease_id, {"status": "completed"})
        supervisor.complete_worker(lease_id)

        import scripts.agent.campaign_engine.supervisor as supervisor_module

        real_executor = supervisor_module.execute_trusted_command
        entered = threading.Event()
        release = threading.Event()
        decisions: list[str] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def blocked_executor(command):
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("validation concurrency barrier timed out")
            return real_executor(command)

        def validate() -> None:
            try:
                decision = supervisor.validate_node("campaign", "node-1")
                with lock:
                    decisions.append(decision.action)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        with patch.object(
            supervisor_module,
            "execute_trusted_command",
            side_effect=blocked_executor,
        ) as executor:
            first = threading.Thread(target=validate)
            first.start()
            self.assertTrue(entered.wait(timeout=10))
            second = threading.Thread(target=validate)
            second.start()
            second.join(timeout=10)
            release.set()
            first.join(timeout=10)

        self.assertEqual(decisions, ["CANDIDATE_FROZEN"])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AttemptAlreadyReserved)
        self.assertEqual(executor.call_count, 1)
        consumed = self.budget_consumed(self.store.get_snapshot("campaign"))
        self.assertEqual(consumed[BudgetToken.VALIDATION_EXECUTION], 1)

    def test_one_durable_transport_retry_consumes_token_before_collection(self) -> None:
        spec = self.make_spec("campaign", mode="AUTOMATED", budget_limit=3)
        self.create_approved(spec)
        host = TimeoutOnceHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease_id = self.dispatch_implementer(supervisor)
        host.set_result(lease_id, {"status": "completed"})

        with self.assertRaisesRegex(HostProtocolError, "transport timeout"):
            supervisor.complete_worker(lease_id)
        receipt = supervisor.complete_worker(lease_id, transport_retry=True)

        self.assertEqual(receipt.lease_id, lease_id)
        self.assertEqual(host.collection_attempts, 2)
        consumed = self.budget_consumed(self.store.get_snapshot("campaign"))
        self.assertEqual(consumed[BudgetToken.TRANSPORT_RETRY], 1)

    def test_transport_failure_cannot_be_collected_again_without_reserved_retry(self) -> None:
        spec = self.make_spec("campaign", mode="AUTOMATED", budget_limit=3)
        self.create_approved(spec)
        host = TimeoutOnceHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease_id = self.dispatch_implementer(supervisor)
        host.set_result(lease_id, {"status": "completed"})

        with self.assertRaisesRegex(HostProtocolError, "transport timeout"):
            supervisor.complete_worker(lease_id)
        with self.assertRaisesRegex(SupervisorError, "requires the one explicit"):
            supervisor.complete_worker(lease_id)
        with self.assertRaisesRegex(SupervisorError, "already recorded"):
            supervisor.complete_worker(lease_id)

        self.assertEqual(host.collection_attempts, 1)
        consumed = self.budget_consumed(self.store.get_snapshot("campaign"))
        self.assertEqual(consumed[BudgetToken.REJECTED_ATTEMPT], 1)
        self.assertEqual(consumed[BudgetToken.TRANSPORT_RETRY], 0)

    def test_crash_after_retry_reservation_cannot_repeat_retry_or_token(self) -> None:
        spec = self.make_spec("campaign", mode="AUTOMATED", budget_limit=3)
        self.create_approved(spec)
        host = TimeoutOnceHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease_id = self.dispatch_implementer(supervisor)
        host.set_result(lease_id, {"status": "completed"})

        with self.assertRaisesRegex(HostProtocolError, "transport timeout"):
            supervisor.complete_worker(lease_id)
        with patch.object(
            self.store,
            "record_runtime_operation",
            side_effect=RuntimeError("injected crash after durable reservation"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected crash"):
                supervisor.complete_worker(lease_id, transport_retry=True)
        with self.assertRaisesRegex(SupervisorError, "already consumed"):
            supervisor.complete_worker(lease_id, transport_retry=True)

        self.assertEqual(host.collection_attempts, 1)
        consumed = self.budget_consumed(self.store.get_snapshot("campaign"))
        self.assertEqual(consumed[BudgetToken.TRANSPORT_RETRY], 1)

    def test_terminal_effect_no_op_is_budgeted_once_without_external_query(self) -> None:
        spec = self.make_spec(
            "campaign", mode="AUTOMATED", publication_automated=True
        )
        self.create_approved(spec)
        host = FakeHost()
        backend = FakePublicationBackend()
        driver = ExternalEffectDriver(self.store, backend)
        supervisor = DeterministicSupervisor(
            self.store,
            host=host,
            effect_driver=driver,
            now=lambda: NOW,
        )
        self.advance_to_merge_preflight(supervisor, host, backend)
        prepared = supervisor.start_publication("campaign", "node-1")
        operation_id = str(prepared.details["operation_id"])
        confirmed = driver.run(operation_id)
        self.assertEqual(confirmed["state"], EffectState.CONFIRMED.value)

        before_queries = len(backend.queries)
        no_op = supervisor.reconcile(operation_id)
        self.assertEqual(no_op["state"], EffectState.CONFIRMED.value)
        with self.assertRaisesRegex(SupervisorError, "no-op already recorded"):
            supervisor.reconcile(operation_id)

        self.assertEqual(len(backend.queries), before_queries)
        consumed = self.budget_consumed(self.store.get_snapshot("campaign"))
        self.assertEqual(consumed[BudgetToken.NO_OP_ATTEMPT], 1)

    def test_ambiguous_publication_yields_without_polling_or_budget_spend(self) -> None:
        spec = self.make_spec(
            "campaign", mode="AUTOMATED", publication_automated=True
        )
        self.create_approved(spec)
        host = FakeHost()
        backend = FakePublicationBackend()
        driver = ExternalEffectDriver(self.store, backend)
        supervisor = DeterministicSupervisor(
            self.store,
            host=host,
            effect_driver=driver,
            now=lambda: NOW,
        )
        self.advance_to_merge_preflight(supervisor, host, backend)
        prepared = supervisor.start_publication("campaign", "node-1")
        operation_id = str(prepared.details["operation_id"])
        self.store.update_effect(
            operation_id,
            expected_state=EffectState.PREPARED.value,
            state=EffectState.EXECUTING.value,
        )
        self.store.update_effect(
            operation_id,
            expected_state=EffectState.EXECUTING.value,
            state=EffectState.AMBIGUOUS.value,
        )
        queries_before_wait = len(backend.queries)

        first = supervisor.step("campaign")
        second = supervisor.step("campaign")

        self.assertEqual(first.action, "PUBLICATION_AMBIGUOUS")
        self.assertEqual(first.wait_event, "reconcile")
        self.assertEqual(second.to_dict(), first.to_dict())
        self.assertEqual(len(backend.queries), queries_before_wait)
        consumed = self.budget_consumed(self.store.get_snapshot("campaign"))
        self.assertEqual(consumed[BudgetToken.RECONCILIATION], 0)

    def test_unproven_transport_retry_is_rejected_after_spending_rejected_token(self) -> None:
        spec = self.make_spec("campaign", mode="AUTOMATED", budget_limit=3)
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease_id = self.dispatch_implementer(supervisor)

        with self.assertRaisesRegex(SupervisorError, "requires one prior failure"):
            supervisor.complete_worker(lease_id, transport_retry=True)

        consumed = self.budget_consumed(self.store.get_snapshot("campaign"))
        self.assertEqual(consumed[BudgetToken.REJECTED_ATTEMPT], 1)
        self.assertEqual(consumed[BudgetToken.TRANSPORT_RETRY], 0)

    def test_exhausted_automated_budget_denies_dispatch_before_host_effect(self) -> None:
        spec = self.make_spec(
            "campaign",
            mode="AUTOMATED",
            budget_limit=3,
            budget_overrides={BudgetToken.CHILD_CREATION: 0},
        )
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        self.assertEqual(supervisor.step("campaign").action, "CAMPAIGN_STARTED")
        self.assertEqual(supervisor.step("campaign").action, "NODE_ADMITTED")
        decision = supervisor.step("campaign")
        self.assertEqual(decision.action, "BUDGET_EXHAUSTED")
        self.assertEqual(host.bindings, {})
        self.assertEqual(host.turns, [])
        self.assertEqual(self.store.list_active_leases("campaign"), [])
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(snapshot.node("node-1").state, NodeState.FAILED_EXACT_NODE)
        consumed = self.budget_consumed(snapshot)
        self.assertEqual(consumed[BudgetToken.CHILD_CREATION], 0)
        self.assertEqual(consumed[BudgetToken.REJECTED_ATTEMPT], 1)
        repeated = supervisor.step("campaign")
        self.assertEqual(repeated.action, "TERMINAL")
        self.assertEqual(
            self.budget_consumed(self.store.get_snapshot("campaign"))[
                BudgetToken.REJECTED_ATTEMPT
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
