#!/usr/bin/env python3
"""Executable incident replays and cross-layer campaign conformance tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.agent.campaign_engine.effects import ExternalEffectDriver
from scripts.agent.campaign_engine.evidence import (
    TrustedCommand,
    ValidationFailure,
    execute_trusted_command,
)
from scripts.agent.campaign_engine.host import FakeHost
from scripts.agent.campaign_engine.model import (
    Actor,
    ActorRole,
    AuthorityError,
    BudgetError,
    BudgetToken,
    CampaignMode,
    CampaignSpec,
    CampaignState,
    CancellationError,
    EffectState,
    Event,
    EventType,
    NodeState,
    TransitionError,
    canonical_json_digest,
)
from scripts.agent.campaign_engine.reducer import reduce as canonical_reduce
from scripts.agent.campaign_engine.store import CampaignStore
from scripts.agent.campaign_engine.supervisor import (
    DeterministicSupervisor,
    SupervisorError,
)


INCIDENT_ROOT = Path(__file__).resolve().parent / "fixtures" / "incidents"
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
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


class QueryOnlyBackend:
    """Expose whether reconciliation attempted a second mutation."""

    def __init__(self, *, mutation_calls: int, confirmed: bool) -> None:
        self.mutation_calls = mutation_calls
        self.confirmed = confirmed
        self.query_calls = 0

    def execute(self, _kind: str, _payload: object) -> dict[str, object]:
        self.mutation_calls += 1
        raise AssertionError("an ambiguous external effect was blindly repeated")

    def query(self, _kind: str, _payload: object) -> dict[str, object]:
        self.query_calls += 1
        return {"confirmed": self.confirmed, "queried": True}


class CampaignIncidentConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = CampaignStore(self.root / "state" / "campaigns.sqlite3")
        self.addCleanup(self.store.close)
        self.sequence = 0

    def new_repository(self, label: str) -> tuple[Path, str]:
        root = self.root / label
        root.mkdir()
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "campaign-tests@example.invalid")
        git(root, "config", "user.name", "Campaign Incident Tests")
        git(
            root,
            "remote",
            "add",
            "origin",
            f"https://example.invalid/acme/{label}.git",
        )
        (root / "src").mkdir()
        (root / "src" / "app.txt").write_text("base\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", "base")
        return root, git(root, "rev-parse", "HEAD").casefold()

    def make_spec(
        self,
        campaign_id: str,
        repository: Path,
        base_sha: str,
        *,
        mode: str = "MANUAL",
        publication_automated: bool = False,
        budget_overrides: dict[BudgetToken, int] | None = None,
    ) -> CampaignSpec:
        limits = {token: 2 for token in BudgetToken}
        limits.update(budget_overrides or {})
        root = str(repository.resolve(strict=True))
        remote = git(repository, "remote", "get-url", "origin")
        return CampaignSpec.from_dict(
            {
                "campaign_id": campaign_id,
                "specification_revision": 1,
                "authority_epoch": 7,
                "cancellation_epoch": 0,
                "mode": mode,
                "objective": "replay one indexed campaign-engine incident",
                "objective_kind": "CONTROL_RUNTIME",
                "repository_remote": remote,
                "git_root": root,
                "worktree": root,
                "branch": "main",
                "base_sha": base_sha,
                "allowed_paths": ["src/**"],
                "nodes": [
                    {
                        "node_id": "node-1",
                        "objective": "exercise the exact incident invariant",
                        "allowed_paths": ["src/**"],
                        "validation_command_ids": ["unit"],
                        "deadline_utc": "2099-01-01T00:00:00Z",
                    }
                ],
                "required_validation_commands": [
                    {
                        "command_id": "unit",
                        "executable": sys.executable,
                        "arguments": ["-B", "-c", "print('validated')"],
                        "working_directory": root,
                        "environment_allowlist": ["PATH"],
                        "timeout_seconds": 30,
                        "output_limit_bytes": 100_000,
                        "expected_worktree_condition": "CLEAN",
                        "required_exit_code": 0,
                    }
                ],
                "required_review_cohort": ["reviewer-a", "reviewer-b"],
                "publication_authority": {
                    "authorized_by": "incident-test-owner",
                    "automated": publication_automated,
                    "allowed_effects": ["PUSH"],
                    "required_effects": ["PUSH"],
                },
                "attempt_budgets": [
                    {"token": token.value, "limit": limits[token]}
                    for token in BudgetToken
                ],
                "stop_conditions": ["STOP", "budget exhausted"],
                "installed_source_commit": "b" * 40,
                "installed_bundle_digest": "c" * 64,
                "install_transaction": "0123456789abcdef0123456789abcdef",
                "protocol_version": "ccos-campaign-v1",
                "schema_compatibility": "campaign-store-v1",
                "host_capability_probe_version": "native-bind-before-turn-scoped-tools-v3",
                "autonomous_rank": sum(limits.values()),
                "deadline_utc": "2099-01-02T00:00:00Z",
            }
        )

    def event(
        self,
        snapshot,
        event_type: EventType,
        *,
        node_id: str | None = None,
        actor_id: str | None = None,
        fencing_epoch: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> Event:
        self.sequence += 1
        return Event(
            event_id=f"incident-event-{self.sequence}",
            campaign_id=snapshot.spec.campaign_id,
            event_type=event_type,
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
            node_id=node_id,
            actor_id=actor_id,
            fencing_epoch=fencing_epoch,
            payload=payload or {},
        )

    def apply(self, snapshot, event_type: EventType, **kwargs):
        return self.store.apply_event(self.event(snapshot, event_type, **kwargs))[0]

    def create_approved(
        self,
        campaign_id: str,
        repository: Path,
        base_sha: str,
        **spec_options,
    ):
        spec = self.make_spec(campaign_id, repository, base_sha, **spec_options)
        snapshot = self.store.create_campaign(spec)
        snapshot = self.apply(
            snapshot,
            EventType.APPROVE,
            payload={"specification_digest": spec.specification_digest},
        )
        return spec, snapshot

    def create_candidate(
        self,
        campaign_id: str,
        repository: Path,
        base_sha: str,
        candidate_head: str,
        *,
        publication_automated: bool = False,
    ):
        spec, snapshot = self.create_approved(
            campaign_id,
            repository,
            base_sha,
            mode="MANUAL",
            publication_automated=publication_automated,
        )
        snapshot = self.apply(snapshot, EventType.START)
        snapshot = self.apply(
            snapshot,
            EventType.ADMIT_NODE,
            node_id="node-1",
            payload={"start_head": base_sha},
        )
        for event_type in (
            EventType.START_IMPLEMENTATION,
            EventType.IMPLEMENTATION_COMPLETED,
        ):
            snapshot = self.apply(
                snapshot,
                event_type,
                node_id="node-1",
            )
        snapshot = self.apply(
            snapshot,
            EventType.VALIDATION_PASSED,
            node_id="node-1",
            payload={
                "candidate_head": candidate_head,
                "candidate_tree": "d" * 40,
                "candidate_diff_digest": "e" * 64,
                "candidate_node_diff_digest": "e" * 64,
            },
        )
        return spec, snapshot

    def freeze_clean_review(self, spec: CampaignSpec, snapshot):
        snapshot = self.apply(
            snapshot,
            EventType.START_REVIEW,
            node_id="node-1",
            payload={
                "review_id": f"review-{spec.campaign_id}",
                "review_cohort": list(spec.required_review_cohort),
            },
        )
        return self.apply(
            snapshot,
            EventType.FREEZE_FINDINGS,
            node_id="node-1",
            payload={"findings": []},
        )

    @staticmethod
    def read_fixture(relative: str) -> dict[str, object]:
        return json.loads((INCIDENT_ROOT / relative).read_text(encoding="utf-8"))

    def replay_actor_binding(self, fixture: dict[str, object]) -> dict[str, object]:
        inputs = dict(fixture["input"])
        campaign_id = f"actor-{fixture['case']}"
        repository, base_sha = self.new_repository(campaign_id)
        _, snapshot = self.create_approved(campaign_id, repository, base_sha)
        snapshot = self.apply(snapshot, EventType.START)
        snapshot = self.apply(
            snapshot,
            EventType.ADMIT_NODE,
            node_id="node-1",
            payload={"start_head": base_sha},
        )
        role = ActorRole(str(inputs["caller_role"]))
        self.assertTrue(inputs["turn_started"])
        actor = Actor(
            actor_id=f"{campaign_id}-actor",
            campaign_id=campaign_id,
            node_id="node-1",
            role=role,
            native_thread_id="UNBOUND",
            authority_epoch=snapshot.authority_epoch,
            can_write=True,
        )
        lease = self.store.acquire_lease(
            campaign_id,
            "node-1",
            f"write:{repository}",
            actor,
            lease_id=f"{campaign_id}-lease",
            request_id=f"{campaign_id}-acquire",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        snapshot = self.store.get_snapshot(campaign_id)
        if inputs["native_thread_bound"]:
            thread_id = f"{campaign_id}-native-thread"
            native_identity = {
                "thread_id": thread_id,
                "host_pid": 4242,
                "host_process_identity": {
                    "pid": 4242,
                    "creation_token": "fixture-incident",
                    "executable": "C:/fake/codex.exe",
                },
                "cwd": str(repository.resolve(strict=True)),
                "source_digest": "a" * 64,
                "sandbox_type": "read-only",
                "writable_roots": [],
                "mediated_write_scope": ["src/**"],
                "dynamic_tool_digest": "9" * 64,
                "native_write_mode": "scoped-dynamic-tools",
                "role": role.value,
                "lease_digest": "f" * 64,
                "thread_created_idle": True,
            }
            self.store.bind_actor(
                lease.lease_id,
                actor.actor_id,
                role,
                thread_id,
                canonical_json_digest(native_identity),
                snapshot.authority_epoch,
                snapshot.cancellation_epoch,
                lease.fencing_epoch,
                f"{campaign_id}-bind",
                native_identity=native_identity,
                host_pid=4242,
            )
        try:
            proof = self.store.verify_actor_action(
                campaign_id,
                actor_id=actor.actor_id,
                lease_id=lease.lease_id,
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
                fencing_epoch=lease.fencing_epoch,
                repository_root=str(repository),
                action="WRITE",
                path="src/app.txt",
            )
        except AuthorityError as exc:
            self.assertIn("natively bound", str(exc))
            return {"allowed": False, "reason": "native_actor_unbound"}
        self.assertEqual(proof["role"], role.value)
        self.assertEqual(proof["native_thread_id"], f"{campaign_id}-native-thread")
        return {"allowed": True, "reason": "exact_native_binding"}

    def replay_support_budget(self, fixture: dict[str, object]) -> dict[str, object]:
        inputs = dict(fixture["input"])
        token = BudgetToken(str(inputs["attempt"]))
        remaining = int(inputs["budget_remaining"])
        campaign_id = f"budget-{fixture['case']}"
        repository, base_sha = self.new_repository(campaign_id)
        _, snapshot = self.create_approved(
            campaign_id,
            repository,
            base_sha,
            mode="AUTOMATED",
            budget_overrides={token: remaining},
        )
        snapshot = self.apply(snapshot, EventType.START)
        try:
            snapshot = self.store.consume_budget(
                campaign_id,
                token,
                request_id=f"{campaign_id}-attempt",
                expected_revision=snapshot.revision,
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
            )
        except BudgetError as exc:
            self.assertIn("exhausted", str(exc))
            self.assertEqual(
                self.store.telemetry_counts(campaign_id)["loop_prevented"], 1
            )
            return {"allowed": False, "reason": "budget_exhausted"}
        balance = next(item for item in snapshot.budgets if item.token is token)
        self.assertEqual(balance.consumed, 1)
        return {"allowed": True, "remaining": balance.remaining}

    def replay_stale_review_head(self, fixture: dict[str, object]) -> dict[str, object]:
        inputs = dict(fixture["input"])
        candidate_head = str(inputs["candidate_head"])
        reviewed_head = str(inputs["reviewed_head"])
        campaign_id = f"review-{fixture['case']}"
        repository, base_sha = self.new_repository(campaign_id)
        spec, _ = self.create_candidate(
            campaign_id,
            repository,
            base_sha,
            candidate_head,
        )
        host = FakeHost()
        supervisor = DeterministicSupervisor(
            self.store, host=host, now=lambda: NOW
        )
        dispatched = supervisor.begin_review(campaign_id, "node-1")
        leases = [str(item) for item in dispatched.details["leases"]]
        for reviewer_id, lease_id in zip(spec.required_review_cohort, leases):
            host.set_result(
                lease_id,
                {
                    "status": "completed",
                    "reviewer_id": reviewer_id,
                    "candidate_head": reviewed_head,
                    "findings": [],
                    "verdict": "PASS",
                },
            )
        review_allowed = True
        try:
            receipts, findings = supervisor.collect_review_cohort(leases)
            supervisor.freeze_review(
                campaign_id, "node-1", receipts=receipts, findings=findings
            )
        except SupervisorError as exc:
            review_allowed = False
            self.assertIn("candidate head", str(exc))
        if candidate_head != reviewed_head:
            self.assertFalse(review_allowed)
            publication_id = f"{campaign_id}-publication"
            publication_spec, publication = self.create_candidate(
                publication_id,
                repository,
                base_sha,
                candidate_head,
            )
            publication = self.freeze_clean_review(publication_spec, publication)
            publication = self.apply(
                publication,
                EventType.MARK_READY_TO_PUBLISH,
                node_id="node-1",
            )
            with self.assertRaisesRegex(AuthorityError, "candidate head"):
                self.store.verify_publication_authority(
                    publication_id,
                    "PUSH",
                    authority_epoch=publication.authority_epoch,
                    cancellation_epoch=publication.cancellation_epoch,
                    node_id="node-1",
                    candidate_head=reviewed_head,
                )
            return {"allowed": False, "reason": "head_race"}
        self.assertTrue(review_allowed)
        snapshot = self.store.get_snapshot(campaign_id)
        with self.assertRaisesRegex(AuthorityError, "publication-ready"):
            self.store.verify_publication_authority(
                campaign_id,
                "PUSH",
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
                node_id="node-1",
                candidate_head=reviewed_head,
            )
        snapshot = self.apply(
            snapshot, EventType.MARK_READY_TO_PUBLISH, node_id="node-1"
        )
        authority = self.store.verify_publication_authority(
            campaign_id,
            "PUSH",
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
            node_id="node-1",
            candidate_head=reviewed_head,
        )
        self.assertTrue(authority["authorized"])
        return {"allowed": True}

    def replay_volatile_state(self, fixture: dict[str, object]) -> dict[str, object]:
        inputs = dict(fixture["input"])
        campaign_id = f"volatile-{fixture['case']}"
        repository, base_sha = self.new_repository(campaign_id)
        mirror = repository / "docs" / "delivery" / "current-state.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_text(str(inputs["repository_state"]), encoding="utf-8")
        _, snapshot = self.create_approved(campaign_id, repository, base_sha)
        external_state = str(inputs["external_campaign"])
        supervisor = DeterministicSupervisor(
            self.store, host=FakeHost(), now=lambda: NOW
        )
        if external_state == CampaignState.RUNNING.value:
            snapshot = self.apply(snapshot, EventType.START)
            decision = supervisor.step(campaign_id)
            self.assertEqual(decision.action, "NODE_ADMITTED")
            self.assertEqual(
                self.store.get_snapshot(campaign_id).state, CampaignState.RUNNING
            )
            return {
                "authority": "external_campaign",
                "repository_state_ignored": True,
            }
        if external_state == CampaignState.CANCELLED.value:
            self.store.cancel_campaign(campaign_id, request_id=f"{campaign_id}-stop")
            decision = supervisor.step(campaign_id)
            self.assertEqual(decision.action, "TERMINAL")
            self.assertEqual(decision.campaign_state, CampaignState.CANCELLED.value)
            return {"authority": "external_campaign", "allowed": False}
        self.fail(f"unsupported external fixture state: {external_state}")

    def replay_validation_exit(self, fixture: dict[str, object]) -> dict[str, object]:
        inputs = dict(fixture["input"])
        campaign_id = f"validation-{fixture['case']}"
        repository, head = self.new_repository(campaign_id)
        self.assertEqual(inputs["assertions"], "passing")
        command = TrustedCommand(
            executable=sys.executable,
            arguments=(
                "-B",
                "-c",
                f"print('assertions passing'); raise SystemExit({int(inputs['exit_code'])})",
            ),
            working_directory=str(repository),
            environment_allowlist=(
                ("PATH", "SYSTEMROOT") if os.name == "nt" else ("PATH",)
            ),
            environment={},
            timeout_seconds=30,
            output_limit_bytes=100_000,
            candidate_head=head,
            expected_working_tree="CLEAN",
            required_exit_code=int(inputs["required_exit_code"]),
        )
        try:
            evidence = execute_trusted_command(command)
        except ValidationFailure as exc:
            self.assertIn("exit", str(exc))
            return {"passed": False, "reason": "exit_code_mismatch"}
        self.assertEqual(evidence.exit_code, int(inputs["exit_code"]))
        return {"passed": evidence.passed}

    def replay_uncertain_effect(self, fixture: dict[str, object]) -> dict[str, object]:
        inputs = dict(fixture["input"])
        self.assertEqual(inputs["state"], EffectState.AMBIGUOUS.value)
        campaign_id = f"effect-{fixture['case']}"
        repository, base_sha = self.new_repository(campaign_id)
        spec, snapshot = self.create_candidate(
            campaign_id,
            repository,
            base_sha,
            base_sha,
            publication_automated=True,
        )
        snapshot = self.freeze_clean_review(spec, snapshot)
        snapshot = self.apply(
            snapshot, EventType.MARK_READY_TO_PUBLISH, node_id="node-1"
        )
        operation_id = f"{campaign_id}-push"
        snapshot = self.apply(
            snapshot,
            EventType.START_PUBLISH,
            node_id="node-1",
            payload={
                "effect_kind": "PUSH",
                "operation_id": operation_id,
                "effect_payload": {
                    "root": str(repository),
                    "candidate_head": base_sha,
                },
            },
        )
        self.store.update_effect(
            operation_id,
            EffectState.EXECUTING,
            expected_state=EffectState.PREPARED,
        )
        self.store.update_effect(
            operation_id,
            EffectState.AMBIGUOUS,
            expected_state=EffectState.EXECUTING,
            result={"error": "historical response was uncertain"},
        )
        backend = QueryOnlyBackend(
            mutation_calls=int(inputs["mutation_calls"]),
            confirmed=inputs["query_result"] == "confirmed",
        )
        result = ExternalEffectDriver(self.store, backend).reconcile(operation_id)
        self.assertEqual(backend.query_calls, 1)
        actual = {
            "state": result["state"],
            "mutation_calls": backend.mutation_calls,
        }
        if result["state"] == EffectState.AMBIGUOUS.value:
            actual["action"] = "query_only"
        return actual

    def test_every_indexed_incident_has_executable_historical_and_opposite_replays(
        self,
    ) -> None:
        index = self.read_fixture("index.json")
        replayers = {
            "actor-unbound": self.replay_actor_binding,
            "support-loop": self.replay_support_budget,
            "stale-review-head": self.replay_stale_review_head,
            "volatile-state-gate": self.replay_volatile_state,
            "validation-exit-mismatch": self.replay_validation_exit,
            "uncertain-external-effect": self.replay_uncertain_effect,
        }
        incidents = list(index["incidents"])
        self.assertEqual(
            {str(item["incident_id"]) for item in incidents}, set(replayers)
        )
        replayed: set[tuple[str, str]] = set()
        for incident in incidents:
            incident_id = str(incident["incident_id"])
            for fixture_key in ("historical_fixture", "opposite_fixture"):
                fixture = self.read_fixture(str(incident[fixture_key]))
                with self.subTest(incident=incident_id, case=fixture["case"]):
                    self.assertEqual(fixture["incident_id"], incident_id)
                    actual = replayers[incident_id](fixture)
                    for key, expected in dict(fixture["expected"]).items():
                        self.assertIn(key, actual)
                        self.assertEqual(actual[key], expected)
                    replayed.add((incident_id, str(fixture["case"])))
        self.assertEqual(len(replayed), len(incidents) * 2)

    def test_supervisor_and_store_transitions_conform_to_the_canonical_reducer(
        self,
    ) -> None:
        repository, base_sha = self.new_repository("cross-layer-conformance")
        spec, approved = self.create_approved(
            "cross-layer-conformance",
            repository,
            base_sha,
            mode=CampaignMode.AUTOMATED.value,
        )
        host = FakeHost()
        supervisor = DeterministicSupervisor(
            self.store, host=host, now=lambda: NOW
        )
        reductions: list[tuple[EventType, object, tuple[object, ...]]] = []

        def checked_reduce(snapshot, event):
            next_snapshot, intents = canonical_reduce(snapshot, event)
            reductions.append((event.event_type, next_snapshot, intents))
            return next_snapshot, intents

        with patch(
            "scripts.agent.campaign_engine.store.reduce", side_effect=checked_reduce
        ):
            self.assertEqual(supervisor.step(spec.campaign_id).action, "CAMPAIGN_STARTED")
            self.assertEqual(self.store.get_snapshot(spec.campaign_id), reductions[-1][1])
            self.assertEqual(supervisor.step(spec.campaign_id).action, "NODE_ADMITTED")
            self.assertEqual(self.store.get_snapshot(spec.campaign_id), reductions[-1][1])
            dispatched = supervisor.step(spec.campaign_id)
            self.assertEqual(dispatched.action, "IMPLEMENTER_DISPATCHED")
            self.assertEqual(self.store.get_snapshot(spec.campaign_id), reductions[-1][1])
            lease_id = str(dispatched.details["lease_id"])
            host.set_result(lease_id, {"status": "completed"})
            supervisor.complete_worker(lease_id)
            self.assertEqual(self.store.get_snapshot(spec.campaign_id), reductions[-1][1])

        observed = [event_type for event_type, _, _ in reductions]
        for required in (
            EventType.START,
            EventType.ADMIT_NODE,
            EventType.CONSUME_BUDGET,
            EventType.ACQUIRE_LEASE,
            EventType.START_IMPLEMENTATION,
            EventType.IMPLEMENTATION_COMPLETED,
            EventType.RELEASE_LEASE,
        ):
            self.assertIn(required, observed)
        self.assertEqual(
            self.store.get_snapshot(spec.campaign_id).node("node-1").state,
            NodeState.VALIDATING,
        )
        self.assertEqual(approved.spec.specification_digest, spec.specification_digest)

    def test_state_loops_cannot_regenerate_work_or_resume_after_stop(self) -> None:
        repository, base_sha = self.new_repository("state-loop-denial")
        spec, snapshot = self.create_approved(
            "state-loop-denial", repository, base_sha, mode="MANUAL"
        )
        snapshot = self.apply(snapshot, EventType.START)
        snapshot = self.apply(
            snapshot,
            EventType.ADMIT_NODE,
            node_id="node-1",
            payload={"start_head": base_sha},
        )
        for event_type in (
            EventType.START_IMPLEMENTATION,
            EventType.IMPLEMENTATION_COMPLETED,
            EventType.REQUEST_VALIDATION_CORRECTION,
            EventType.IMPLEMENTATION_COMPLETED,
        ):
            snapshot = self.apply(
                snapshot,
                event_type,
                node_id="node-1",
            )
        before = snapshot
        with self.assertRaisesRegex(TransitionError, "already been used"):
            self.store.apply_event(
                self.event(
                    snapshot,
                    EventType.REQUEST_VALIDATION_CORRECTION,
                    node_id="node-1",
                )
            )
        after_rejection = self.store.get_snapshot(spec.campaign_id)
        self.assertEqual(after_rejection, before)
        self.assertEqual(after_rejection.node("node-1").validation_corrections, 1)
        self.assertEqual(after_rejection.node("node-1").implementation_attempts, 1)
        self.assertEqual(after_rejection.spec.specification_digest, spec.specification_digest)
        self.assertEqual(self.store.telemetry_counts(spec.campaign_id)["loop_prevented"], 1)
        self.assertEqual(len(self.store.list_campaigns(str(repository))), 1)

        cancelled = self.store.cancel_campaign(
            spec.campaign_id, request_id="state-loop-stop", reason="STOP"
        )
        self.assertEqual(cancelled.state, CampaignState.CANCELLED)
        with self.assertRaises(CancellationError):
            self.store.apply_event(
                self.event(cancelled, EventType.RESUME)
            )
        restarted = DeterministicSupervisor(
            self.store, host=FakeHost(), now=lambda: NOW
        )
        decision = restarted.step(spec.campaign_id)
        self.assertEqual(decision.action, "TERMINAL")
        self.assertEqual(decision.campaign_state, CampaignState.CANCELLED.value)
        self.assertEqual(
            self.store.get_snapshot(spec.campaign_id).cancellation_epoch,
            cancelled.cancellation_epoch,
        )


if __name__ == "__main__":
    unittest.main()
