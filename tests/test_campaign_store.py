#!/usr/bin/env python3
"""Durability, concurrency, fencing, recovery, and telemetry tests."""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from scripts.agent.campaign_engine import (
    Actor,
    ActorRole,
    AuthorityError,
    BudgetError,
    BudgetToken,
    CampaignSpec,
    CampaignState,
    CampaignStore,
    EffectKind,
    EffectState,
    Event,
    EventType,
    Evidence,
    EvidenceKind,
    LeaseState,
    NodeState,
    RequestConflict,
    RevisionConflict,
    StoreError,
    TransitionError,
    canonical_json_digest,
)
from scripts.agent.campaign_engine.admission import ScopeOverlapError


def make_spec(
    campaign_id: str = "campaign-store",
    *,
    budget_limit: int = 3,
    exhausted_token: BudgetToken | None = None,
) -> CampaignSpec:
    attempt_budgets = [
        {
            "token": token.value,
            "limit": 0 if token is exhausted_token else budget_limit,
        }
        for token in BudgetToken
    ]
    return CampaignSpec.from_dict(
        {
            "campaign_id": campaign_id,
            "specification_revision": 1,
            "authority_epoch": 11,
            "cancellation_epoch": 4,
            "mode": "MANUAL",
            "objective": "replace engine",
            "objective_kind": "CONTROL_RUNTIME",
            "repository_remote": "https://github.com/example/coding-os.git",
            "git_root": "C:/repo",
            "worktree": "C:/worktree",
            "branch": "codex/replacement",
            "base_sha": "a" * 40,
            "allowed_paths": ["scripts/agent/campaign_engine/**"],
            "nodes": [
                {
                    "node_id": "node-1",
                    "objective": "engine core",
                    "allowed_paths": ["scripts/agent/campaign_engine/**"],
                    "validation_command_ids": ["unit"],
                }
            ],
            "required_validation_commands": [
                {
                    "command_id": "unit",
                    "executable": "python",
                    "arguments": ["-m", "unittest"],
                    "working_directory": "C:/worktree",
                    "environment_allowlist": ["PATH"],
                    "timeout_seconds": 60,
                    "output_limit_bytes": 100_000,
                    "expected_worktree_condition": "CLEAN",
                    "required_exit_code": 0,
                }
            ],
            "required_review_cohort": ["reviewer-a", "reviewer-b"],
            "publication_authority": {
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
            "attempt_budgets": attempt_budgets,
            "stop_conditions": ["STOP", "failed exact node"],
            "installed_source_commit": "b" * 40,
            "installed_bundle_digest": "c" * 64,
            "install_transaction": "transaction",
            "protocol_version": "ccos-campaign-v1",
            "schema_compatibility": "campaign-store-v1",
            "host_capability_probe_version": "native-bind-before-turn-scoped-tools-v3",
            "autonomous_rank": sum(item["limit"] for item in attempt_budgets),
        }
    )


class StoreFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "campaigns.sqlite3"
        self.store = CampaignStore(self.database)
        self.sequence = 0

    def event(
        self,
        snapshot,
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
            event_id or f"store-event-{self.sequence}",
            snapshot.spec.campaign_id,
            event_type,
            snapshot.revision,
            snapshot.authority_epoch,
            snapshot.cancellation_epoch,
            node_id=node_id,
            actor_id=actor_id,
            fencing_epoch=fencing_epoch,
            payload=payload or {},
        )

    def create_running(self, campaign_id: str = "campaign-store"):
        snapshot = self.store.create_campaign(
            make_spec(campaign_id), request_id=f"create-{campaign_id}"
        )
        snapshot = self.store.apply_event(
            self.event(
                snapshot,
                EventType.APPROVE,
                payload={"specification_digest": snapshot.spec.specification_digest},
            )
        )[0]
        snapshot = self.store.apply_event(self.event(snapshot, EventType.START))[0]
        return snapshot

    def admit(self, snapshot):
        return self.store.apply_event(
            self.event(
                snapshot,
                EventType.ADMIT_NODE,
                node_id="node-1",
                payload={"start_head": snapshot.spec.base_sha},
            )
        )[0]


class CampaignStoreSchemaTests(StoreFixture):
    def test_required_tables_pragmas_integrity_and_migration_backup(self) -> None:
        proof = self.store.integrity_check()
        self.assertEqual(proof["foreign_keys"], 1)
        self.assertEqual(proof["journal_mode"], "wal")
        self.assertEqual(proof["synchronous"], 2)
        self.assertEqual(proof["schema_version"], 1)
        expected = {
            "campaigns",
            "nodes",
            "dependencies",
            "actors",
            "leases",
            "operations",
            "external_effect_outbox",
            "evidence",
            "reviews",
            "findings",
            "resource_locks",
            "events",
            "runtime_installations",
            "legacy_archives",
        }
        with closing(sqlite3.connect(self.database)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertTrue(expected.issubset(tables))
        self.assertTrue(
            list(self.database.parent.glob("campaigns.sqlite3.pre-migration-v0-to-v1*.bak"))
        )

    def test_existing_version_zero_database_is_backed_up_before_migration(self) -> None:
        other = Path(self.temporary.name) / "old.sqlite3"
        with closing(sqlite3.connect(other)) as connection:
            connection.execute("CREATE TABLE retained(value TEXT NOT NULL)")
            connection.execute("INSERT INTO retained VALUES ('before-migration')")
            connection.commit()
        CampaignStore(other)
        backups = list(other.parent.glob("old.sqlite3.pre-migration-v0-to-v1*.bak"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as connection:
            self.assertEqual(
                connection.execute("SELECT value FROM retained").fetchone()[0],
                "before-migration",
            )

    def test_startup_rejects_foreign_key_corruption(self) -> None:
        corrupted = Path(self.temporary.name) / "foreign-key-corrupt.sqlite3"
        CampaignStore(corrupted)
        with closing(sqlite3.connect(corrupted)) as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                """
                INSERT INTO dependencies(campaign_id, node_id, depends_on_node_id)
                VALUES ('missing-campaign', 'missing-node', 'missing-dependency')
                """
            )
            connection.commit()

        with self.assertRaisesRegex(StoreError, "foreign-key check failed"):
            CampaignStore(corrupted)

    def test_startup_rejects_tampered_migration_digest(self) -> None:
        corrupted = Path(self.temporary.name) / "migration-digest-corrupt.sqlite3"
        CampaignStore(corrupted)
        with closing(sqlite3.connect(corrupted)) as connection:
            connection.execute(
                "UPDATE schema_migrations SET migration_digest=? WHERE version=1",
                ("0" * 64,),
            )
            connection.commit()

        with self.assertRaisesRegex(StoreError, "migration provenance digest mismatch"):
            CampaignStore(corrupted)


class CampaignStoreLifecycleTests(StoreFixture):
    @staticmethod
    def scoped_spec(campaign_id: str, allowed_path: str) -> CampaignSpec:
        value = make_spec(campaign_id).to_dict()
        value.pop("specification_digest", None)
        value["allowed_paths"] = [allowed_path]
        value["nodes"][0]["allowed_paths"] = [allowed_path]
        return CampaignSpec.from_dict(value)

    def test_concurrent_overlapping_campaign_creation_only_one_succeeds(self) -> None:
        specs = (
            self.scoped_spec("overlap-parent", "src/**"),
            self.scoped_spec("overlap-child", "src/api/**"),
        )
        barrier = threading.Barrier(3)
        results: list[str] = []
        lock = threading.Lock()

        def create(spec: CampaignSpec) -> None:
            barrier.wait()
            try:
                self.store.create_campaign(
                    spec, request_id=f"create-{spec.campaign_id}"
                )
                result = "success"
            except ScopeOverlapError:
                result = "overlap"
            with lock:
                results.append(result)

        threads = [threading.Thread(target=create, args=(spec,)) for spec in specs]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(results), ["overlap", "success"])
        self.assertEqual(len(self.store.list_campaigns("C:/repo")), 1)

    def test_concurrent_nonoverlapping_campaign_creation_both_succeed(self) -> None:
        specs = (
            self.scoped_spec("nonoverlap-source", "src/**"),
            self.scoped_spec("nonoverlap-tests", "tests/**"),
        )
        barrier = threading.Barrier(3)
        results: list[str] = []
        lock = threading.Lock()

        def create(spec: CampaignSpec) -> None:
            barrier.wait()
            try:
                self.store.create_campaign(
                    spec, request_id=f"create-{spec.campaign_id}"
                )
                result = "success"
            except BaseException as exc:  # pragma: no cover - assertion reports the type
                result = type(exc).__name__
            with lock:
                results.append(result)

        threads = [threading.Thread(target=create, args=(spec,)) for spec in specs]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(results, ["success", "success"])
        self.assertEqual(len(self.store.list_campaigns("C:/repo")), 2)

    def test_generic_evidence_path_rejects_forged_native_terminal_receipt(self) -> None:
        snapshot = self.create_running("forged-terminal")
        payload = {
            "protocol_version": "ccos-native-terminal-receipt-v1",
            "campaign_id": snapshot.spec.campaign_id,
            "node_id": "node-1",
            "actor_id": "forged-actor",
            "lease_id": "forged-lease",
            "native_thread_id": "forged-thread",
            "native_turn_id": "forged-turn",
            "role": "REVIEWER",
            "authority_epoch": snapshot.authority_epoch,
            "cancellation_epoch": snapshot.cancellation_epoch,
            "fencing_epoch": 1,
            "candidate_head": snapshot.spec.base_sha,
            "result_payload": {
                "reviewer_id": "reviewer-a",
                "candidate_head": snapshot.spec.base_sha,
                "findings": [],
            },
        }
        digest = canonical_json_digest(payload)
        payload["receipt_digest"] = digest
        forged = Evidence(
            evidence_id="forged-native-terminal",
            campaign_id=snapshot.spec.campaign_id,
            node_id="node-1",
            kind=EvidenceKind.REVIEW,
            digest=digest,
            payload=payload,
            candidate_head=snapshot.spec.base_sha,
        )

        with self.assertRaisesRegex(AuthorityError, "attested evidence path"):
            self.store.record_evidence(forged)

    def test_create_get_list_cas_and_request_binding(self) -> None:
        spec = make_spec()
        snapshot = self.store.create_campaign(spec, request_id="create")
        repeated = self.store.create_campaign(spec, request_id="create")
        self.assertEqual(snapshot, repeated)
        self.assertEqual(self.store.get_snapshot(spec.campaign_id), snapshot)
        self.assertEqual(self.store.list_campaigns("C:/repo"), [snapshot])
        self.assertEqual(self.store.list_campaigns("C:/different"), [])
        approve = self.event(
            snapshot,
            EventType.APPROVE,
            payload={"specification_digest": spec.specification_digest},
            event_id="approve",
        )
        approved = self.store.apply_event(approve)[0]
        self.assertEqual(self.store.apply_event(approve)[0], approved)
        changed = Event.from_dict(approve.to_dict())
        changed = Event(
            changed.event_id,
            changed.campaign_id,
            changed.event_type,
            changed.expected_revision,
            changed.authority_epoch,
            changed.cancellation_epoch,
            payload={"specification_digest": "0" * 64},
        )
        with self.assertRaises(RequestConflict):
            self.store.apply_event(changed)

    def test_concurrent_writers_only_one_exact_revision_wins(self) -> None:
        snapshot = self.create_running()
        barrier = threading.Barrier(3)
        results: list[str] = []
        lock = threading.Lock()

        def writer(event_type: EventType, event_id: str) -> None:
            event = self.event(snapshot, event_type, event_id=event_id)
            barrier.wait()
            try:
                self.store.apply_event(event)
                result = "success"
            except RevisionConflict:
                result = "conflict"
            with lock:
                results.append(result)

        threads = [
            threading.Thread(target=writer, args=(EventType.WAIT_EXTERNAL, "concurrent-1")),
            threading.Thread(target=writer, args=(EventType.WAIT_HUMAN, "concurrent-2")),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(results), ["conflict", "success"])
        self.assertEqual(self.store.get_snapshot(snapshot.spec.campaign_id).revision, snapshot.revision + 1)

    def test_budget_consumption_is_durable(self) -> None:
        snapshot = self.create_running()
        after = self.store.consume_budget(
            snapshot.spec.campaign_id,
            BudgetToken.VALIDATION_EXECUTION,
            request_id="validation-budget",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        self.assertEqual(after.autonomous_rank_remaining, snapshot.autonomous_rank_remaining - 1)
        self.assertEqual(self.store.get_snapshot(snapshot.spec.campaign_id), after)

    def test_one_shot_budget_attempt_reservation_is_atomic_across_writers(self) -> None:
        snapshot = self.create_running()
        barrier = threading.Barrier(3)
        results: list[bool] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def reserve() -> None:
            barrier.wait()
            try:
                _, created = self.store.reserve_budget_attempt(
                    snapshot.spec.campaign_id,
                    BudgetToken.TRANSPORT_RETRY,
                    request_id="one-transport-retry",
                    expected_revision=snapshot.revision,
                    authority_epoch=snapshot.authority_epoch,
                    cancellation_epoch=snapshot.cancellation_epoch,
                    node_id="node-1",
                )
                with lock:
                    results.append(created)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False, True])
        current = self.store.get_snapshot(snapshot.spec.campaign_id)
        balance = next(
            item for item in current.budgets
            if item.token is BudgetToken.TRANSPORT_RETRY
        )
        self.assertEqual(balance.consumed, 1)
        self.assertEqual(
            current.autonomous_rank_remaining,
            snapshot.autonomous_rank_remaining - 1,
        )

    def test_parallel_read_only_reviewers_and_writer_exclusion(self) -> None:
        snapshot = self.admit(self.create_running())
        reviewers = [
            Actor(
                f"reviewer-{index}",
                snapshot.spec.campaign_id,
                "node-1",
                ActorRole.REVIEWER,
                "UNBOUND",
                snapshot.authority_epoch,
                False,
            )
            for index in (1, 2)
        ]
        leases = [
            self.store.acquire_lease(
                snapshot.spec.campaign_id,
                "node-1",
                f"review-resource-{index}",
                actor,
                lease_id=f"review-lease-{index}",
                request_id=f"review-lease-request-{index}",
                expected_revision=snapshot.revision,
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
            )
            for index, actor in enumerate(reviewers, 1)
        ]
        self.assertEqual([lease.fencing_epoch for lease in leases], [0, 0])
        self.assertEqual(len(self.store.list_active_leases()), 2)
        writer = Actor(
            "writer",
            snapshot.spec.campaign_id,
            "node-1",
            ActorRole.IMPLEMENTER,
            "UNBOUND",
            snapshot.authority_epoch,
            True,
        )
        with self.assertRaisesRegex(StoreError, "conflicts"):
            self.store.acquire_lease(
                snapshot.spec.campaign_id,
                "node-1",
                "write-resource",
                writer,
                lease_id="writer-lease",
                request_id="writer-request",
                expected_revision=snapshot.revision,
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
            )

    def test_validator_and_supervisor_receive_read_only_leases(self) -> None:
        snapshot = self.admit(self.create_running("read-only-runtime-actors"))
        observed = []
        for index, role in enumerate((ActorRole.VALIDATOR, ActorRole.SUPERVISOR), 1):
            actor = Actor(
                f"read-only-{role.value.casefold()}",
                snapshot.spec.campaign_id,
                "node-1",
                role,
                "UNBOUND",
                snapshot.authority_epoch,
                False,
            )
            lease = self.store.acquire_lease(
                snapshot.spec.campaign_id,
                "node-1",
                f"read-only-runtime-resource-{index}",
                actor,
                lease_id=f"read-only-runtime-lease-{index}",
                request_id=f"read-only-runtime-acquire-{index}",
                expected_revision=snapshot.revision,
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
            )
            if role is ActorRole.VALIDATOR:
                self.assertEqual(lease.fencing_epoch, snapshot.node("node-1").fencing_epoch + 1)
                snapshot = self.store.get_snapshot(snapshot.spec.campaign_id)
                self.assertEqual(snapshot.node("node-1").lease_actor_id, actor.actor_id)
                snapshot = self.store.release_lease(
                    lease.lease_id,
                    request_id="release-trusted-validator",
                    expected_revision=snapshot.revision,
                    authority_epoch=snapshot.authority_epoch,
                    cancellation_epoch=snapshot.cancellation_epoch,
                )
            else:
                self.assertEqual(lease.fencing_epoch, snapshot.node("node-1").fencing_epoch)
            observed.append(lease)
        self.assertEqual(len(observed), 2)
        self.assertEqual(len(self.store.list_active_leases(snapshot.spec.campaign_id)), 1)

    def test_writer_fencing_reacquire_and_stale_release(self) -> None:
        snapshot = self.admit(self.create_running())
        actor = Actor(
            "worker-1",
            snapshot.spec.campaign_id,
            "node-1",
            ActorRole.IMPLEMENTER,
            "UNBOUND",
            snapshot.authority_epoch,
            True,
        )
        lease = self.store.acquire_lease(
            snapshot.spec.campaign_id,
            "node-1",
            "write-resource",
            actor,
            lease_id="lease-1",
            request_id="lease-request-1",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        self.assertEqual(lease.fencing_epoch, 1)
        snapshot = self.store.get_snapshot(snapshot.spec.campaign_id)
        with self.assertRaises(RevisionConflict):
            self.store.release_lease(
                lease.lease_id,
                request_id="stale-release",
                expected_revision=snapshot.revision - 1,
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
            )
        snapshot = self.store.release_lease(
            lease.lease_id,
            request_id="release-1",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        actor2 = Actor(
            "worker-2",
            snapshot.spec.campaign_id,
            "node-1",
            ActorRole.IMPLEMENTER,
            "UNBOUND",
            snapshot.authority_epoch,
            True,
        )
        lease2 = self.store.acquire_lease(
            snapshot.spec.campaign_id,
            "node-1",
            "write-resource",
            actor2,
            lease_id="lease-2",
            request_id="lease-request-2",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        self.assertEqual(lease2.fencing_epoch, 2)

    def test_bind_actor_is_one_use_and_persists_owned_process_identity(self) -> None:
        snapshot = self.admit(self.create_running())
        provisional = Actor(
            "reviewer",
            snapshot.spec.campaign_id,
            "node-1",
            ActorRole.REVIEWER,
            "UNBOUND",
            snapshot.authority_epoch,
            False,
        )
        lease = self.store.acquire_lease(
            snapshot.spec.campaign_id,
            "node-1",
            "review-resource",
            provisional,
            lease_id="review-lease",
            request_id="review-acquire",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        identity = {
            "thread_id": "native-thread",
            "host_pid": 4321,
            "host_process_identity": {
                "pid": 4321,
                "creation_token": "fixture-reviewer",
                "executable": "C:/fake/codex.exe",
            },
            "cwd": "C:/worktree",
            "source_digest": "a" * 64,
            "sandbox_type": "read-only",
            "writable_roots": [],
            "mediated_write_scope": [],
            "dynamic_tool_digest": "e" * 64,
            "native_write_mode": "denied",
            "role": "REVIEWER",
            "lease_digest": "b" * 64,
            "thread_created_idle": True,
        }
        actor = self.store.bind_actor(
            lease.lease_id,
            provisional.actor_id,
            ActorRole.REVIEWER,
            "native-thread",
            canonical_json_digest(identity),
            snapshot.authority_epoch,
            snapshot.cancellation_epoch,
            lease.fencing_epoch,
            "bind-request",
            native_identity=identity,
            host_pid=4321,
        )
        self.assertFalse(actor.can_write)
        self.assertEqual(actor.host_pid, 4321)
        self.assertEqual(self.store.get_actor(actor.actor_id), actor)
        persisted = self.store.list_active_actor_identities(snapshot.spec.campaign_id)
        self.assertEqual(persisted[0]["host_pid"], 4321)
        self.assertEqual(persisted[0]["native_identity"]["writable_roots"], [])
        self.assertEqual(persisted[0]["native_identity"]["mediated_write_scope"], [])
        self.assertEqual(persisted[0]["native_identity"]["native_write_mode"], "denied")
        with self.assertRaisesRegex(AuthorityError, "write denied"):
            self.store.verify_actor_action(
                snapshot.spec.campaign_id,
                actor_id=actor.actor_id,
                lease_id=lease.lease_id,
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
                fencing_epoch=lease.fencing_epoch,
                repository_root="C:/repo",
                action="WRITE",
                path="scripts/agent/campaign_engine/model.py",
            )
        with self.assertRaises(AuthorityError):
            self.store.bind_actor(
                lease.lease_id,
                provisional.actor_id,
                ActorRole.REVIEWER,
                "other-thread",
                "different",
                snapshot.authority_epoch,
                snapshot.cancellation_epoch,
                lease.fencing_epoch,
                "second-bind",
            )

    def test_verify_actor_action_binds_exact_root_scope_epochs_and_fence(self) -> None:
        snapshot = self.admit(self.create_running())
        provisional = Actor(
            "implementer",
            snapshot.spec.campaign_id,
            "node-1",
            ActorRole.IMPLEMENTER,
            "UNBOUND",
            snapshot.authority_epoch,
            True,
        )
        lease = self.store.acquire_lease(
            snapshot.spec.campaign_id,
            "node-1",
            "write-resource",
            provisional,
            lease_id="implementer-lease",
            request_id="implementer-acquire",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        snapshot = self.store.get_snapshot(snapshot.spec.campaign_id)
        legacy_workspace_write_identity = {
            "thread_id": "implementer-thread",
            "host_pid": 2222,
            "host_process_identity": {
                "pid": 2222,
                "creation_token": "fixture-implementer",
                "executable": "C:/fake/codex.exe",
            },
            "cwd": "C:/worktree",
            "source_digest": "c" * 64,
            "sandbox_type": "workspace-write",
            "writable_roots": ["C:/worktree/scripts/agent/campaign_engine"],
            "mediated_write_scope": ["scripts/agent/campaign_engine/**"],
            "dynamic_tool_digest": "e" * 64,
            "native_write_mode": "scoped-dynamic-tools",
            "role": "IMPLEMENTER",
            "lease_digest": "d" * 64,
            "thread_created_idle": True,
        }
        with self.assertRaisesRegex(AuthorityError, "read-only mediated-write"):
            self.store.bind_actor(
                lease.lease_id,
                provisional.actor_id,
                ActorRole.IMPLEMENTER,
                "implementer-thread",
                canonical_json_digest(legacy_workspace_write_identity),
                snapshot.authority_epoch,
                snapshot.cancellation_epoch,
                lease.fencing_epoch,
                "legacy-workspace-write-bind",
                native_identity=legacy_workspace_write_identity,
                host_pid=2222,
            )
        identity = {
            **legacy_workspace_write_identity,
            "sandbox_type": "read-only",
            "writable_roots": [],
        }
        self.store.bind_actor(
            lease.lease_id,
            provisional.actor_id,
            ActorRole.IMPLEMENTER,
            "implementer-thread",
            canonical_json_digest(identity),
            snapshot.authority_epoch,
            snapshot.cancellation_epoch,
            lease.fencing_epoch,
            "implementer-bind",
            native_identity=identity,
            host_pid=2222,
        )
        bound = self.store.get_actor(provisional.actor_id)
        self.assertEqual(bound.native_identity["sandbox_type"], "read-only")
        self.assertEqual(bound.native_identity["writable_roots"], ())
        self.assertEqual(
            bound.native_identity["mediated_write_scope"],
            ("scripts/agent/campaign_engine/**",),
        )
        self.assertEqual(bound.native_identity["native_write_mode"], "scoped-dynamic-tools")
        proof = self.store.verify_actor_action(
            snapshot.spec.campaign_id,
            actor_id=provisional.actor_id,
            lease_id=lease.lease_id,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
            fencing_epoch=lease.fencing_epoch,
            repository_root="C:/repo",
            action="WRITE",
            path="scripts/agent/campaign_engine/model.py",
        )
        self.assertEqual(proof["path"], "scripts/agent/campaign_engine/model.py")
        self.assertEqual(len(proof["authorization_digest"]), 64)
        with self.assertRaises(AuthorityError):
            self.store.verify_actor_action(
                snapshot.spec.campaign_id,
                actor_id=provisional.actor_id,
                lease_id=lease.lease_id,
                authority_epoch=snapshot.authority_epoch,
                cancellation_epoch=snapshot.cancellation_epoch,
                fencing_epoch=lease.fencing_epoch,
                repository_root="C:/repo",
                action="WRITE",
                path="../outside.py",
            )

    def test_cancel_invalidates_leases_prevents_effects_and_preserves_process_identity(self) -> None:
        snapshot = self.admit(self.create_running())
        actor = Actor(
            "worker",
            snapshot.spec.campaign_id,
            "node-1",
            ActorRole.IMPLEMENTER,
            "UNBOUND",
            snapshot.authority_epoch,
            True,
            native_identity={"transport": "codex"},
            host_pid=9876,
        )
        lease = self.store.acquire_lease(
            snapshot.spec.campaign_id,
            "node-1",
            "write-resource",
            actor,
            lease_id="worker-lease",
            request_id="worker-acquire",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        snapshot = self.store.get_snapshot(snapshot.spec.campaign_id)
        self.store.prepare_effect(
            "uncertain-effect",
            snapshot.spec.campaign_id,
            None,
            EffectKind.PUSH,
            {"candidate_head": "d" * 40},
        )
        self.store.update_effect("uncertain-effect", EffectState.EXECUTING)
        cancelled = self.store.cancel_campaign(
            snapshot.spec.campaign_id,
            request_id="STOP",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        self.assertEqual(cancelled.state, CampaignState.CANCELLED)
        self.assertEqual(self.store.get_lease(lease.lease_id).state, LeaseState.INVALIDATED)
        self.assertEqual(self.store.get_effect("uncertain-effect")["state"], "AMBIGUOUS")
        self.assertEqual(
            self.store.list_actor_identities(snapshot.spec.campaign_id)[0]["host_pid"], 9876
        )
        with self.assertRaises(TransitionError):
            self.store.prepare_effect(
                "late-effect",
                snapshot.spec.campaign_id,
                "node-1",
                EffectKind.PUSH,
                {},
            )

    def test_terminal_failure_atomically_invalidates_active_lease_and_resource_lock(self) -> None:
        snapshot = self.admit(self.create_running("terminal-failure-lease"))
        actor = Actor(
            "failing-worker",
            snapshot.spec.campaign_id,
            "node-1",
            ActorRole.IMPLEMENTER,
            "UNBOUND",
            snapshot.authority_epoch,
            True,
        )
        lease = self.store.acquire_lease(
            snapshot.spec.campaign_id,
            "node-1",
            "terminal-failure-resource",
            actor,
            lease_id="terminal-failure-lease",
            request_id="terminal-failure-acquire",
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
        )
        snapshot = self.store.get_snapshot(snapshot.spec.campaign_id)
        failed = self.store.apply_event(
            self.event(
                snapshot,
                EventType.FAIL_NODE,
                node_id="node-1",
                payload={"reason": "worker_terminal_failure"},
            )
        )[0]
        self.assertEqual(failed.state, CampaignState.FAILED)
        self.assertEqual(self.store.get_lease(lease.lease_id).state, LeaseState.INVALIDATED)
        self.assertEqual(self.store.list_active_leases(failed.spec.campaign_id), [])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT lease_id FROM resource_locks WHERE resource_key=?",
                    ("terminal-failure-resource",),
                ).fetchone()[0]
            )


class CampaignStoreEffectAndRecoveryTests(StoreFixture):
    def test_effect_outbox_is_idempotent_and_ambiguous_never_reexecutes(self) -> None:
        snapshot = self.create_running()
        payload = {"candidate_head": "d" * 40, "remote": "origin"}
        first = self.store.prepare_effect(
            "effect-1", snapshot.spec.campaign_id, None, EffectKind.PUSH, payload
        )
        repeated = self.store.prepare_effect(
            "effect-1", snapshot.spec.campaign_id, None, EffectKind.PUSH, payload
        )
        self.assertEqual(first, repeated)
        with self.assertRaises(RequestConflict):
            self.store.prepare_effect(
                "effect-1", snapshot.spec.campaign_id, None, EffectKind.PUSH, {"other": True}
            )
        self.store.update_effect("effect-1", EffectState.EXECUTING)
        self.store.update_effect("effect-1", EffectState.AMBIGUOUS)
        with self.assertRaisesRegex(TransitionError, "forbidden"):
            self.store.update_effect("effect-1", EffectState.EXECUTING)
        final = self.store.update_effect(
            "effect-1", EffectState.CONFIRMED, result={"reconciled": True}
        )
        self.assertEqual(final["state"], "CONFIRMED")

    def test_restart_marks_executing_effect_ambiguous(self) -> None:
        snapshot = self.create_running()
        self.store.prepare_effect(
            "effect-crash", snapshot.spec.campaign_id, None, EffectKind.PUSH, {}
        )
        self.store.update_effect("effect-crash", EffectState.EXECUTING)
        recovered = CampaignStore(self.database).recover_after_restart()
        self.assertEqual(recovered["ambiguous_effects"], 1)
        self.assertEqual(self.store.get_effect("effect-crash")["state"], "AMBIGUOUS")

    def test_reconciliation_evidence_updates_an_ambiguous_record_in_place(self) -> None:
        snapshot = self.create_running()
        self.store.prepare_effect(
            "effect-query", snapshot.spec.campaign_id, None, EffectKind.PUSH, {}
        )
        self.store.update_effect("effect-query", EffectState.EXECUTING)
        self.store.update_effect("effect-query", EffectState.AMBIGUOUS)
        updated = self.store.update_effect(
            "effect-query",
            EffectState.AMBIGUOUS,
            expected_state=EffectState.AMBIGUOUS,
            result={"queried": True, "confirmed": False},
        )
        self.assertEqual(updated["state"], "AMBIGUOUS")
        self.assertEqual(updated["result"], {"queried": True, "confirmed": False})

    def test_forced_termination_rolls_back_uncommitted_transition(self) -> None:
        snapshot = self.create_running()
        script = r"""
import os, sqlite3, sys
database, campaign = sys.argv[1:]
connection = sqlite3.connect(database, isolation_level=None)
connection.execute('PRAGMA journal_mode=WAL')
connection.execute('PRAGMA synchronous=FULL')
connection.execute('BEGIN IMMEDIATE')
connection.execute(
    "INSERT INTO telemetry(telemetry_id, campaign_id, category, payload_json) VALUES ('forced', ?, 'failed', '{}')",
    (campaign,),
)
os._exit(73)
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.database), snapshot.spec.campaign_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 73)
        reopened = CampaignStore(self.database)
        self.assertEqual(reopened.integrity_check()["status"], "ok")
        self.assertEqual(reopened.telemetry_counts(snapshot.spec.campaign_id)["failed"], 0)

    def test_runtime_installation_idempotency_and_drift_rejection(self) -> None:
        pin = {
            "installation_id": "install-1",
            "source_commit": "a" * 40,
            "bundle_digest": "b" * 64,
            "install_transaction": "tx",
            "protocol_version": "ccos-campaign-v1",
            "schema_compatibility": "campaign-store-v1",
            "host_capability_probe_version": "native-bind-before-turn-scoped-tools-v3",
        }
        self.store.record_runtime_installation(pin)
        self.store.record_runtime_installation(pin)
        self.assertEqual(self.store.list_runtime_installations(), [pin])
        changed = dict(pin, source_commit="c" * 40)
        with self.assertRaises(RequestConflict):
            self.store.record_runtime_installation(changed)

    def test_mixed_legacy_archives_remain_read_only_without_outcomes(self) -> None:
        unresolved = self.store.record_legacy_archive(
            "legacy-u",
            "C:/legacy/u.json",
            "a" * 64,
            "RUNNING",
            "LEGACY_ARCHIVED_UNRESOLVED",
            {"events": 3},
        )
        terminal = self.store.record_legacy_archive(
            "legacy-t",
            "C:/legacy/t.json",
            "b" * 64,
            "DONE",
            "LEGACY_ARCHIVED_TERMINAL_EVIDENCE",
            {"receipt": "verified"},
        )
        self.assertIsNone(unresolved["translated_outcome"])
        self.assertIsNone(terminal["translated_outcome"])
        self.assertEqual(len(self.store.list_legacy_archives()), 2)
        with self.assertRaises(StoreError):
            self.store.record_legacy_archive(
                "legacy-bad",
                "C:/legacy/bad.json",
                "c" * 64,
                "DONE",
                "LEGACY_ARCHIVED_TERMINAL_EVIDENCE",
                {},
                translated_outcome="COMPLETED",
            )

    def test_telemetry_counts_completed_failed_stopped_denied_and_loop_prevented(self) -> None:
        completed = self.create_running("telemetry-completed")
        for event_type, payload in (
            (EventType.ADMIT_NODE, {"start_head": completed.spec.base_sha}),
            (EventType.START_IMPLEMENTATION, None),
            (EventType.IMPLEMENTATION_COMPLETED, None),
            (
                EventType.VALIDATION_PASSED,
                {
                    "candidate_head": "d" * 40,
                    "candidate_tree": "e" * 40,
                    "candidate_diff_digest": "f" * 64,
                    "candidate_node_diff_digest": "f" * 64,
                },
            ),
            (
                EventType.START_REVIEW,
                {"review_cohort": ["reviewer-a", "reviewer-b"]},
            ),
            (EventType.FREEZE_FINDINGS, {"findings": []}),
            (EventType.MARK_READY_TO_PUBLISH, None),
        ):
            completed = self.store.apply_event(
                self.event(
                    completed,
                    event_type,
                    node_id="node-1",
                    payload=payload,
                )
            )[0]
        for index, effect_kind in enumerate(
            (EffectKind.PUSH, EffectKind.CREATE_PULL_REQUEST, EffectKind.MERGE), 1
        ):
            operation_id = f"telemetry-publication-{index}"
            completed = self.store.apply_event(
                self.event(
                    completed,
                    EventType.START_PUBLISH,
                    node_id="node-1",
                    payload={
                        "effect_kind": effect_kind.value,
                        "operation_id": operation_id,
                        "effect_payload": {"candidate_head": "d" * 40},
                    },
                )
            )[0]
            completed = self.store.apply_event(
                self.event(
                    completed,
                    EventType.PUBLISH_CONFIRMED,
                    node_id="node-1",
                    payload={
                        "candidate_head": "d" * 40,
                        "operation_id": operation_id,
                    },
                )
            )[0]
        self.assertEqual(completed.state, CampaignState.COMPLETED)

        failed = self.create_running("telemetry-failed")
        failed = self.store.apply_event(
            self.event(
                failed,
                EventType.FAIL_NODE,
                node_id="node-1",
                payload={"reason": "historical_failure_replay"},
            )
        )[0]
        self.assertEqual(failed.state, CampaignState.FAILED)

        running = self.create_running()
        with self.assertRaises(TransitionError):
            self.store.apply_event(
                self.event(running, EventType.START_REPAIR, node_id="node-1", event_id="denied")
            )
        zero_budget_store = CampaignStore(Path(self.temporary.name) / "zero.sqlite3")
        zero = zero_budget_store.create_campaign(
            make_spec(
                "zero",
                budget_limit=1,
                exhausted_token=BudgetToken.NO_OP_ATTEMPT,
            )
        )
        zero = zero_budget_store.apply_event(
            Event(
                "zero-approve",
                "zero",
                EventType.APPROVE,
                zero.revision,
                zero.authority_epoch,
                zero.cancellation_epoch,
                payload={"specification_digest": zero.spec.specification_digest},
            )
        )[0]
        zero = zero_budget_store.apply_event(
            Event(
                "zero-start",
                "zero",
                EventType.START,
                zero.revision,
                zero.authority_epoch,
                zero.cancellation_epoch,
            )
        )[0]
        with self.assertRaises(BudgetError):
            zero_budget_store.consume_budget(
                "zero",
                BudgetToken.NO_OP_ATTEMPT,
                request_id="loop",
                expected_revision=zero.revision,
                authority_epoch=zero.authority_epoch,
                cancellation_epoch=zero.cancellation_epoch,
            )
        self.store.cancel_campaign(running.spec.campaign_id, request_id="stop")
        counts = self.store.telemetry_counts(running.spec.campaign_id)
        self.assertEqual(counts["denied"], 1)
        self.assertEqual(counts["stopped"], 1)
        self.assertEqual(zero_budget_store.telemetry_counts("zero")["loop_prevented"], 1)
        all_counts = self.store.telemetry_counts()
        self.assertEqual(all_counts["completed"], 1)
        self.assertEqual(all_counts["failed"], 1)
        self.assertEqual(set(counts), {"completed", "failed", "stopped", "denied", "loop_prevented"})


if __name__ == "__main__":
    unittest.main()
