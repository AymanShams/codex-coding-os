#!/usr/bin/env python3
"""Focused model and exhaustive reducer tests for the campaign engine."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import ast
import inspect
import random
import unittest

from scripts.agent.campaign_engine import (
    Actor,
    ActorRole,
    AuthorityError,
    BudgetError,
    BudgetToken,
    CampaignMode,
    CampaignSnapshot,
    CampaignSpec,
    CampaignState,
    CancellationError,
    EffectKind,
    Event,
    EventType,
    FencingError,
    ModelValidationError,
    NodeState,
    RevisionConflict,
    TransitionError,
    canonical_json_digest,
    reduce,
)
import scripts.agent.campaign_engine.reducer as reducer_module


def spec_dict(
    *,
    campaign_id: str = "campaign-1",
    mode: str = "MANUAL",
    nodes: list[dict] | None = None,
    budget_limit: int = 4,
    rank: int | None = None,
) -> dict:
    nodes = nodes or [
        {
            "node_id": "node-1",
            "objective": "replace engine core",
            "allowed_paths": ["scripts/agent/campaign_engine/**"],
            "validation_command_ids": ["unit"],
            "deadline_utc": "2026-08-05T00:00:00Z",
        }
    ]
    return {
        "campaign_id": campaign_id,
        "specification_revision": 1,
        "authority_epoch": 7,
        "cancellation_epoch": 2,
        "mode": mode,
        "objective": "complete Coding OS replacement",
        "objective_kind": "CONTROL_RUNTIME",
        "repository_remote": "https://github.com/example/coding-os.git",
        "git_root": "C:/repo",
        "worktree": "C:/worktree",
        "branch": "codex/replacement",
        "base_sha": "a" * 40,
        "allowed_paths": ["scripts/agent/campaign_engine/**"],
        "nodes": nodes,
        "required_validation_commands": [
            {
                "command_id": "unit",
                "executable": "python",
                "arguments": ["-B", "-m", "unittest"],
                "working_directory": "C:/worktree",
                "environment_allowlist": ["PATH", "SYSTEMROOT"],
                "timeout_seconds": 120,
                "output_limit_bytes": 1_000_000,
                "expected_worktree_condition": "CLEAN",
                "required_exit_code": 0,
            }
        ],
        "required_review_cohort": ["reviewer-1", "reviewer-2"],
        "publication_authority": {
            "authorized_by": "user",
            "human_authorization": {
                "algorithm": "ED25519",
                "public_key_base64": "iojj3XQJ8ZX9UtstPLpdcspnCb8dlBIb83SIAbQPb1w=",
            },
            "automated": True,
            "allowed_effects": [
                "PUSH",
                "CREATE_PULL_REQUEST",
                "UPSERT_COMMENT",
                "MERGE",
                "EXACT_FILE_REPLACE",
            ],
            "required_effects": ["PUSH", "CREATE_PULL_REQUEST", "MERGE"],
        },
        "attempt_budgets": [
            {"token": token.value, "limit": budget_limit} for token in BudgetToken
        ],
        "stop_conditions": ["budget exhausted", "deadline exceeded", "STOP"],
        "installed_source_commit": "b" * 40,
        "installed_bundle_digest": "c" * 64,
        "install_transaction": "install-transaction-1",
        "protocol_version": "ccos-campaign-v1",
        "schema_compatibility": "campaign-store-v1",
        "host_capability_probe_version": "native-bind-before-turn-scoped-tools-v3",
        "autonomous_rank": (
            len(BudgetToken) * budget_limit if rank is None else rank
        ),
        "deadline_utc": "2026-08-06T00:00:00Z",
    }


def make_spec(**kwargs) -> CampaignSpec:
    return CampaignSpec.from_dict(spec_dict(**kwargs))


class Events:
    def __init__(self) -> None:
        self.sequence = 0

    def make(
        self,
        snapshot: CampaignSnapshot,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict | None = None,
        actor_id: str | None = None,
        fencing_epoch: int | None = None,
        event_id: str | None = None,
    ) -> Event:
        self.sequence += 1
        return Event(
            event_id=event_id or f"event-{self.sequence}",
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

    def apply(self, snapshot: CampaignSnapshot, event_type: EventType, **kwargs):
        return reduce(snapshot, self.make(snapshot, event_type, **kwargs))


def running_snapshot(*, mode: str = "MANUAL", budget_limit: int = 4):
    events = Events()
    snapshot = CampaignSnapshot.initial(
        make_spec(mode=mode, budget_limit=budget_limit)
    )
    snapshot, _ = events.apply(
        snapshot,
        EventType.APPROVE,
        payload={"specification_digest": snapshot.spec.specification_digest},
    )
    snapshot, _ = events.apply(snapshot, EventType.START)
    return events, snapshot


def candidate_snapshot():
    events, snapshot = running_snapshot()
    snapshot, _ = events.apply(
        snapshot,
        EventType.ADMIT_NODE,
        node_id="node-1",
        payload={"start_head": snapshot.spec.base_sha},
    )
    snapshot, _ = events.apply(
        snapshot, EventType.START_IMPLEMENTATION, node_id="node-1"
    )
    snapshot, _ = events.apply(
        snapshot, EventType.IMPLEMENTATION_COMPLETED, node_id="node-1"
    )
    snapshot, _ = events.apply(
        snapshot,
        EventType.VALIDATION_PASSED,
        node_id="node-1",
        payload={
            "candidate_head": "d" * 40,
            "candidate_tree": "e" * 40,
            "candidate_diff_digest": "f" * 64,
            "candidate_node_diff_digest": "f" * 64,
        },
    )
    return events, snapshot


class CampaignModelTests(unittest.TestCase):
    def test_parent_reviewer_validator_and_supervisor_actors_are_write_denied(self) -> None:
        for role in (
            ActorRole.PARENT,
            ActorRole.REVIEWER,
            ActorRole.CLOSURE_REVIEWER,
            ActorRole.VALIDATOR,
            ActorRole.SUPERVISOR,
        ):
            with self.subTest(role=role.value), self.assertRaisesRegex(
                ModelValidationError, "write denied"
            ):
                Actor(
                    actor_id=f"actor-{role.value.casefold()}",
                    campaign_id="campaign-1",
                    node_id="node-1",
                    role=role,
                    native_thread_id="UNBOUND",
                    authority_epoch=1,
                    can_write=True,
                )

    def test_spec_digest_is_stable_complete_and_immutable(self) -> None:
        raw = spec_dict()
        reordered = {key: raw[key] for key in reversed(tuple(raw))}
        one = CampaignSpec.from_dict(raw)
        two = CampaignSpec.from_dict(reordered)
        self.assertEqual(one.specification_digest, two.specification_digest)
        self.assertEqual(one.compute_digest(), one.specification_digest)
        self.assertEqual(one.deadline_utc, "2026-08-06T00:00:00Z")
        self.assertEqual(one.nodes[0].deadline_utc, "2026-08-05T00:00:00Z")
        with self.assertRaises(FrozenInstanceError):
            one.objective = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            one.publication_authority["authorized_by"] = "other"  # type: ignore[index]
        changed = one.to_dict()
        changed["objective"] = "changed"
        changed["specification_digest"] = one.specification_digest
        with self.assertRaises(ModelValidationError):
            CampaignSpec.from_dict(changed)

    def test_digest_covers_runtime_pin_and_deadlines(self) -> None:
        base = make_spec()
        for field, value in (
            ("installed_source_commit", "1" * 40),
            ("installed_bundle_digest", "2" * 64),
            ("install_transaction", "different"),
            ("protocol_version", "different"),
            ("schema_compatibility", "different"),
            ("host_capability_probe_version", "different"),
            ("deadline_utc", "2026-08-07T00:00:00Z"),
        ):
            raw = base.to_dict()
            raw.pop("specification_digest")
            raw[field] = value
            self.assertNotEqual(base.specification_digest, CampaignSpec.from_dict(raw).specification_digest)

    def test_graph_rejects_cycles_unknown_dependencies_and_scope_escape(self) -> None:
        cycle = [
            {
                "node_id": "a",
                "objective": "a",
                "dependencies": ["b"],
                "allowed_paths": ["scripts/agent/campaign_engine/**"],
                "validation_command_ids": ["unit"],
            },
            {
                "node_id": "b",
                "objective": "b",
                "dependencies": ["a"],
                "allowed_paths": ["scripts/agent/campaign_engine/**"],
                "validation_command_ids": ["unit"],
            },
        ]
        with self.assertRaisesRegex(ModelValidationError, "acyclic"):
            make_spec(nodes=cycle)
        unknown = [
            {
                "node_id": "a",
                "objective": "a",
                "dependencies": ["missing"],
                "allowed_paths": ["scripts/agent/campaign_engine/**"],
                "validation_command_ids": ["unit"],
            }
        ]
        with self.assertRaisesRegex(ModelValidationError, "unknown dependencies"):
            make_spec(nodes=unknown)
        escaped = [
            {
                "node_id": "a",
                "objective": "a",
                "allowed_paths": ["outside/**"],
                "validation_command_ids": ["unit"],
            }
        ]
        with self.assertRaisesRegex(ModelValidationError, "exceed campaign scope"):
            make_spec(nodes=escaped)

    def test_publication_contract_is_finite_and_ordered(self) -> None:
        raw = spec_dict()
        raw["publication_authority"]["required_effects"] = ["MERGE", "MERGE"]
        with self.assertRaises(ModelValidationError):
            CampaignSpec.from_dict(raw)

    def test_every_node_requires_an_executable_exact_worktree_validation(self) -> None:
        raw = spec_dict()
        raw["nodes"][0]["validation_command_ids"] = []
        with self.assertRaisesRegex(ModelValidationError, "at least one validation"):
            CampaignSpec.from_dict(raw)

        raw = spec_dict()
        raw["required_validation_commands"][0][
            "expected_worktree_condition"
        ] = "CANDIDATE_HEAD"
        with self.assertRaisesRegex(ModelValidationError, "CLEAN or EXACT_STATUS"):
            CampaignSpec.from_dict(raw)

        raw = spec_dict()
        raw["required_validation_commands"][0][
            "expected_worktree_condition"
        ] = "EXACT_STATUS"
        with self.assertRaisesRegex(ModelValidationError, "expected_status_sha256"):
            CampaignSpec.from_dict(raw)

        raw = spec_dict()
        raw["required_validation_commands"][0]["working_directory"] = "C:/other"
        with self.assertRaisesRegex(ModelValidationError, "exact campaign worktree"):
            CampaignSpec.from_dict(raw)
        raw = spec_dict()
        raw["publication_authority"]["required_effects"] = ["DELETE_REPOSITORY"]
        with self.assertRaises(ModelValidationError):
            CampaignSpec.from_dict(raw)

    def test_autonomous_rank_cannot_exceed_total_durable_budget_capacity(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "exceeds declared durable"):
            make_spec(budget_limit=1, rank=len(BudgetToken) + 1)


class CampaignReducerTests(unittest.TestCase):
    def test_clean_lifecycle_requires_exact_publication_sequence(self) -> None:
        events, snapshot = candidate_snapshot()
        snapshot, _ = events.apply(
            snapshot,
            EventType.START_REVIEW,
            node_id="node-1",
            payload={"review_cohort": ["reviewer-1", "reviewer-2"]},
        )
        snapshot, _ = events.apply(
            snapshot,
            EventType.FREEZE_FINDINGS,
            node_id="node-1",
            payload={"findings": []},
        )
        snapshot, _ = events.apply(
            snapshot, EventType.MARK_READY_TO_PUBLISH, node_id="node-1"
        )
        with self.assertRaisesRegex(TransitionError, "out of order"):
            events.apply(
                snapshot,
                EventType.START_PUBLISH,
                node_id="node-1",
                payload={
                    "effect_kind": "MERGE",
                    "operation_id": "merge-too-early",
                    "effect_payload": {"candidate_head": "d" * 40},
                },
            )
        for index, kind in enumerate(
            (EffectKind.PUSH, EffectKind.CREATE_PULL_REQUEST, EffectKind.MERGE), 1
        ):
            operation_id = f"publication-{index}"
            snapshot, effects = events.apply(
                snapshot,
                EventType.START_PUBLISH,
                node_id="node-1",
                payload={
                    "effect_kind": kind.value,
                    "operation_id": operation_id,
                    "effect_payload": {"candidate_head": "d" * 40},
                },
            )
            self.assertEqual([effect.operation_id for effect in effects], [operation_id])
            snapshot, _ = events.apply(
                snapshot,
                EventType.PUBLISH_CONFIRMED,
                node_id="node-1",
                payload={"candidate_head": "d" * 40, "operation_id": operation_id},
            )
            expected = CampaignState.COMPLETED if index == 3 else CampaignState.RUNNING
            self.assertEqual(snapshot.state, expected)
        self.assertEqual(snapshot.node("node-1").state, NodeState.DONE)
        self.assertEqual(
            snapshot.node("node-1").completed_publication_effects,
            ("PUSH", "CREATE_PULL_REQUEST", "MERGE"),
        )

    def test_duplicate_publication_identity_and_confirmation_are_rejected(self) -> None:
        events, snapshot = candidate_snapshot()
        snapshot, _ = events.apply(
            snapshot,
            EventType.START_REVIEW,
            node_id="node-1",
            payload={"review_cohort": ["reviewer-1", "reviewer-2"]},
        )
        snapshot, _ = events.apply(
            snapshot, EventType.FREEZE_FINDINGS, node_id="node-1", payload={"findings": []}
        )
        snapshot, _ = events.apply(snapshot, EventType.MARK_READY_TO_PUBLISH, node_id="node-1")
        snapshot, _ = events.apply(
            snapshot,
            EventType.START_PUBLISH,
            node_id="node-1",
            payload={
                "effect_kind": "PUSH",
                "operation_id": "same",
                "effect_payload": {"candidate_head": "d" * 40},
            },
        )
        with self.assertRaisesRegex(TransitionError, "confirmation operation"):
            events.apply(
                snapshot,
                EventType.PUBLISH_CONFIRMED,
                node_id="node-1",
                payload={"candidate_head": "d" * 40, "operation_id": "different"},
            )
        snapshot, _ = events.apply(
            snapshot,
            EventType.PUBLISH_CONFIRMED,
            node_id="node-1",
            payload={"candidate_head": "d" * 40, "operation_id": "same"},
        )
        with self.assertRaisesRegex(TransitionError, "out of order"):
            events.apply(
                snapshot,
                EventType.START_PUBLISH,
                node_id="node-1",
                payload={
                    "effect_kind": "PUSH",
                    "operation_id": "same",
                    "effect_payload": {"candidate_head": "d" * 40},
                },
            )

    def test_one_validation_correction_only(self) -> None:
        events, snapshot = running_snapshot()
        snapshot, _ = events.apply(
            snapshot,
            EventType.ADMIT_NODE,
            node_id="node-1",
            payload={"start_head": snapshot.spec.base_sha},
        )
        snapshot, _ = events.apply(snapshot, EventType.START_IMPLEMENTATION, node_id="node-1")
        snapshot, _ = events.apply(snapshot, EventType.IMPLEMENTATION_COMPLETED, node_id="node-1")
        snapshot, _ = events.apply(
            snapshot, EventType.REQUEST_VALIDATION_CORRECTION, node_id="node-1"
        )
        snapshot, _ = events.apply(snapshot, EventType.IMPLEMENTATION_COMPLETED, node_id="node-1")
        with self.assertRaisesRegex(TransitionError, "already been used"):
            events.apply(
                snapshot, EventType.REQUEST_VALIDATION_CORRECTION, node_id="node-1"
            )

    def _repair_closure(self, *, closure_findings: list[dict]):
        events, snapshot = candidate_snapshot()
        snapshot, _ = events.apply(
            snapshot,
            EventType.START_REVIEW,
            node_id="node-1",
            payload={"review_cohort": ["reviewer-1", "reviewer-2"]},
        )
        snapshot, _ = events.apply(
            snapshot,
            EventType.FREEZE_FINDINGS,
            node_id="node-1",
            payload={
                "findings": [
                    {"finding_id": "F-1", "title": "verified defect", "blocking": True}
                ]
            },
        )
        snapshot, _ = events.apply(
            snapshot,
            EventType.AUTHORIZE_REPAIR,
            node_id="node-1",
            payload={
                "finding_ids": ["F-1"],
                "authorization_receipt_id": "repair-auth-1",
                "authorization_receipt_digest": "a" * 64,
            },
        )
        snapshot, _ = events.apply(snapshot, EventType.START_REPAIR, node_id="node-1")
        snapshot, _ = events.apply(snapshot, EventType.REPAIR_COMPLETED, node_id="node-1")
        with self.assertRaisesRegex(TransitionError, "identity must be complete"):
            events.apply(snapshot, EventType.REVALIDATION_PASSED, node_id="node-1")
        with self.assertRaisesRegex(TransitionError, "trusted REVALIDATION_PASSED"):
            events.apply(
                snapshot,
                EventType.START_CLOSURE,
                node_id="node-1",
                payload={
                    "candidate_head": "1" * 40,
                    "candidate_tree": "2" * 40,
                    "candidate_diff_digest": "3" * 64,
                    "candidate_node_diff_digest": "4" * 64,
                },
            )
        snapshot, _ = events.apply(
            snapshot,
            EventType.REVALIDATION_PASSED,
            node_id="node-1",
            payload={
                "candidate_head": "1" * 40,
                "candidate_tree": "2" * 40,
                "candidate_diff_digest": "3" * 64,
                "candidate_node_diff_digest": "4" * 64,
            },
        )
        return events.apply(
            snapshot,
            EventType.COMPLETE_CLOSURE,
            node_id="node-1",
            payload={"resolved_finding_ids": ["F-1"], "findings": closure_findings},
        )[0]

    def test_one_combined_repair_can_close_cleanly(self) -> None:
        snapshot = self._repair_closure(closure_findings=[])
        node = snapshot.node("node-1")
        self.assertEqual(node.state, NodeState.READY_TO_PUBLISH)
        self.assertEqual(node.repair_attempts, 1)
        self.assertEqual(node.review_generations, 1)
        self.assertEqual(node.closure_generations, 1)
        self.assertEqual(node.candidate_head, "1" * 40)
        self.assertEqual(node.candidate_tree, "2" * 40)
        self.assertEqual(node.candidate_diff_digest, "3" * 64)
        self.assertEqual([finding.finding_id for finding in node.findings], ["F-1"])
        events = Events()
        with self.assertRaisesRegex(TransitionError, "frozen candidate head"):
            events.apply(
                snapshot,
                EventType.START_PUBLISH,
                node_id="node-1",
                payload={
                    "effect_kind": "PUSH",
                    "operation_id": "repair-publication-old-head",
                    "effect_payload": {"candidate_head": "d" * 40},
                },
            )
        publishing, effects = events.apply(
            snapshot,
            EventType.START_PUBLISH,
            node_id="node-1",
            payload={
                "effect_kind": "PUSH",
                "operation_id": "repair-publication-new-head",
                "effect_payload": {"candidate_head": "1" * 40},
            },
        )
        self.assertEqual(publishing.node("node-1").candidate_head, "1" * 40)
        self.assertEqual(effects[0].payload["candidate_head"], "1" * 40)

    def test_repair_introduced_defect_fails_exact_node(self) -> None:
        snapshot = self._repair_closure(
            closure_findings=[
                {"finding_id": "NEW-1", "title": "repair regression", "blocking": False}
            ]
        )
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(snapshot.node("node-1").state, NodeState.FAILED_EXACT_NODE)
        self.assertIn("new=NEW-1", snapshot.failure_reason or "")

    def test_exact_revision_authority_cancellation_and_fencing(self) -> None:
        events, snapshot = running_snapshot(mode="AUTOMATED")
        with self.assertRaises(RevisionConflict):
            reduce(
                snapshot,
                replace(events.make(snapshot, EventType.ADMIT_NODE, node_id="node-1"), expected_revision=0),
            )
        with self.assertRaises(AuthorityError):
            reduce(
                snapshot,
                replace(events.make(snapshot, EventType.ADMIT_NODE, node_id="node-1"), authority_epoch=8),
            )
        with self.assertRaises(CancellationError):
            reduce(
                snapshot,
                replace(events.make(snapshot, EventType.ADMIT_NODE, node_id="node-1"), cancellation_epoch=3),
            )
        snapshot, _ = events.apply(
            snapshot,
            EventType.ADMIT_NODE,
            node_id="node-1",
            payload={"start_head": snapshot.spec.base_sha},
        )
        snapshot, _ = events.apply(
            snapshot,
            EventType.ACQUIRE_LEASE,
            node_id="node-1",
            actor_id="worker-1",
            fencing_epoch=1,
        )
        snapshot, effects = events.apply(
            snapshot,
            EventType.START_IMPLEMENTATION,
            node_id="node-1",
            actor_id="worker-1",
            fencing_epoch=1,
            payload={"budget_token": "CHILD_START", "operation_id": "dispatch-1"},
        )
        self.assertEqual(len(effects), 1)
        with self.assertRaises(FencingError):
            events.apply(
                snapshot,
                EventType.IMPLEMENTATION_COMPLETED,
                node_id="node-1",
                actor_id="worker-1",
                fencing_epoch=2,
            )

    def test_budget_and_rank_strictly_decrease_and_never_replenish(self) -> None:
        events, snapshot = running_snapshot(mode="AUTOMATED", budget_limit=1)
        rank = snapshot.autonomous_rank_remaining
        snapshot, _ = events.apply(
            snapshot, EventType.CONSUME_BUDGET, payload={"token": "NO_OP_ATTEMPT"}
        )
        self.assertEqual(snapshot.autonomous_rank_remaining, rank - 1)
        balance = next(item for item in snapshot.budgets if item.token is BudgetToken.NO_OP_ATTEMPT)
        self.assertEqual(balance.remaining, 0)
        with self.assertRaises(BudgetError):
            events.apply(
                snapshot, EventType.CONSUME_BUDGET, payload={"token": "NO_OP_ATTEMPT"}
            )

    def test_stop_increments_epoch_rejects_late_results_and_emits_internal_intents(self) -> None:
        events, snapshot = running_snapshot()
        previous_epoch = snapshot.cancellation_epoch
        snapshot, effects = events.apply(
            snapshot, EventType.CANCEL, payload={"reason": "STOP"}
        )
        self.assertEqual(snapshot.state, CampaignState.CANCELLED)
        self.assertEqual(snapshot.cancellation_epoch, previous_epoch + 1)
        self.assertEqual({effect.kind for effect in effects}, {EffectKind.INTERRUPT_WORKER, EffectKind.RECONCILE_EFFECT})
        self.assertTrue(all(not effect.external for effect in effects))
        late = Event(
            "late",
            snapshot.spec.campaign_id,
            EventType.FAIL_NODE,
            snapshot.revision,
            snapshot.authority_epoch,
            previous_epoch,
            node_id="node-1",
        )
        with self.assertRaises(CancellationError):
            reduce(snapshot, late)

    def test_reducer_has_no_ambient_io_imports(self) -> None:
        tree = ast.parse(inspect.getsource(reducer_module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(
            {"os", "pathlib", "subprocess", "socket", "time", "uuid"}.isdisjoint(imported)
        )

    def test_exhaustive_event_exploration_preserves_global_invariants(self) -> None:
        events, running = running_snapshot()
        reachable: list[CampaignSnapshot] = [CampaignSnapshot.initial(make_spec()), running]
        admitted, _ = events.apply(
            running,
            EventType.ADMIT_NODE,
            node_id="node-1",
            payload={"start_head": running.spec.base_sha},
        )
        reachable.append(admitted)
        implementing, _ = events.apply(admitted, EventType.START_IMPLEMENTATION, node_id="node-1")
        reachable.append(implementing)
        validating, _ = events.apply(implementing, EventType.IMPLEMENTATION_COMPLETED, node_id="node-1")
        reachable.append(validating)
        generic_payload = {
            EventType.APPROVE: {"specification_digest": running.spec.specification_digest},
            EventType.CONSUME_BUDGET: {"token": "NO_OP_ATTEMPT"},
            EventType.VALIDATION_PASSED: {
                "candidate_head": "d" * 40,
                "candidate_tree": "e" * 40,
                "candidate_diff_digest": "f" * 64,
                "candidate_node_diff_digest": "f" * 64,
            },
            EventType.START_REVIEW: {"review_cohort": ["reviewer-1", "reviewer-2"]},
            EventType.FREEZE_FINDINGS: {"findings": []},
            EventType.START_PUBLISH: {
                "effect_kind": "PUSH",
                "operation_id": "publish",
                "effect_payload": {"candidate_head": "d" * 40},
            },
            EventType.PUBLISH_CONFIRMED: {
                "candidate_head": "d" * 40,
                "operation_id": "publish",
            },
        }
        accepted = 0
        for index, snapshot in enumerate(reachable):
            for event_type in EventType:
                event = Event(
                    event_id=f"explore-{index}-{event_type.value}",
                    campaign_id=snapshot.spec.campaign_id,
                    event_type=event_type,
                    expected_revision=snapshot.revision,
                    authority_epoch=snapshot.authority_epoch,
                    cancellation_epoch=snapshot.cancellation_epoch,
                    node_id="node-1",
                    payload=generic_payload.get(event_type, {}),
                )
                try:
                    after, _ = reduce(snapshot, event)
                except (TransitionError, BudgetError, FencingError, CancellationError):
                    continue
                accepted += 1
                self.assertEqual(after.revision, snapshot.revision + 1)
                self.assertEqual(after.spec.specification_digest, snapshot.spec.specification_digest)
                self.assertLessEqual(after.autonomous_rank_remaining, snapshot.autonomous_rank_remaining)
                node = after.node("node-1")
                self.assertLessEqual(node.implementation_attempts, 1)
                self.assertLessEqual(node.validation_corrections, 1)
                self.assertLessEqual(node.review_generations, 1)
                self.assertLessEqual(node.repair_attempts, 1)
                self.assertLessEqual(node.closure_generations, 1)
        self.assertGreater(accepted, 10)

    def test_property_style_random_epoch_and_revision_mutations_never_apply(self) -> None:
        randomizer = random.Random(731)
        _, snapshot = running_snapshot()
        baseline = canonical_json_digest(snapshot.to_dict())
        for index in range(100):
            field = randomizer.choice(
                ("expected_revision", "authority_epoch", "cancellation_epoch")
            )
            event = Event(
                f"property-{index}",
                snapshot.spec.campaign_id,
                EventType.WAIT_EXTERNAL,
                snapshot.revision,
                snapshot.authority_epoch,
                snapshot.cancellation_epoch,
            )
            event = replace(event, **{field: getattr(event, field) + randomizer.randint(1, 5)})
            with self.assertRaises((RevisionConflict, AuthorityError, CancellationError)):
                reduce(snapshot, event)
            self.assertEqual(canonical_json_digest(snapshot.to_dict()), baseline)


if __name__ == "__main__":
    unittest.main()
