"""Recover an existing observable review without granting another execution."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from scripts.agent.campaign_engine import host as native_host
from scripts.agent.campaign_engine.host import FakeHost, HostProtocolError, LateResultError
from scripts.agent.campaign_engine.model import (
    ActorRole, CampaignSpec, CampaignState, EventType, NodeState,
)
from scripts.agent.campaign_engine.supervisor import DeterministicSupervisor
from scripts.agent.campaign_engine.store import StoreError
from tests.test_campaign_runtime_components import _FakeTransport
from tests.test_campaign_supervisor import NOW, SupervisorFixture, git


class SurvivingReadOnlyHost(FakeHost):
    """Protocol fixture. Native connection/process checks have separate host tests."""

    def __init__(self):
        super().__init__()
        self.identities = {}
        self.unavailable = set()
        self.recovery_calls = []

    def create_idle_actor(self, lease, *, bind_authority, **kwargs):
        def capture(bound, identity):
            self.identities[bound.lease_id] = dict(identity)
            bind_authority(bound, identity)
        return super().create_idle_actor(lease, bind_authority=capture, **kwargs)

    def recover_read_only_actor(self, lease_id, *, native_identity, current_epochs):
        self.recovery_calls.append(lease_id)
        if lease_id in self.unavailable or lease_id in self.interrupted:
            raise HostProtocolError("original connection unavailable")
        binding = self.bindings[lease_id]
        if (binding.lease.role not in {"REVIEWER", "CLOSURE_REVIEWER"}
                or native_identity != self.identities[lease_id]
                or git(self._root(binding), "rev-parse", "HEAD") != binding.lease.candidate_head):
            raise HostProtocolError("original read-only binding changed")
        epochs = current_epochs(binding.lease.campaign_id, binding.lease.node_id)
        if any(epochs[name] != getattr(binding.lease, name)
               for name in ("authority_epoch", "cancellation_epoch", "fencing_epoch")):
            raise LateResultError("original authority changed")
        return binding

    @staticmethod
    def _root(binding):
        from pathlib import Path
        return Path(binding.lease.worktree)


class ReadOnlyRecoveryTests(SupervisorFixture):
    def start_review(self, *, closure=False, automated=False):
        if automated:
            spec = self.make_spec("campaign", mode="AUTOMATED")
            self.create_approved(spec)
        elif closure:
            spec, _ = self.create_manual_closure()
        else:
            spec, _ = self.create_manual_candidate()
        host = SurvivingReadOnlyHost()
        original = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        if automated:
            lease = self.dispatch_implementer(original)
            self.commit_text("accepted\n", "produce actual validated candidate")
            host.set_result(lease, {"status": "completed"})
            original.complete_worker(lease)
            self.assertEqual(original.validate_node("campaign", "node-1").action, "CANDIDATE_FROZEN")
        decision = (original.step("campaign") if closure
                    else original.begin_review("campaign", "node-1"))
        leases = list(decision.details["leases"])
        return spec, host, original, leases

    def test_live_review_keeps_exact_turns_epochs_and_budget_then_collects(self):
        spec, host, _, leases = self.start_review(automated=True)
        before = self.store.get_snapshot("campaign")
        self.assertTrue(before.budget_receipts)
        bindings = dict(host.bindings)
        turns = list(host.turns)
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        result = restarted.recover()
        self.assertEqual(result["reattached_read_only_leases"], sorted(leases))
        self.assertEqual(result["invalidated_leases"], 0)
        self.assertEqual(result["failed_nodes"], [])
        self.assertEqual(self.store.get_snapshot("campaign"), before)
        self.assertEqual(host.bindings, bindings)
        self.assertEqual(host.turns, turns)
        self.assertEqual(host.interrupted, set())
        self.assertEqual(restarted.recover()["reattached_read_only_leases"], sorted(leases))
        self.assertEqual(self.store.get_snapshot("campaign"), before)
        for reviewer, lease_id in zip(spec.required_review_cohort, leases):
            host.set_result(lease_id, {"status": "completed", "reviewer_id": reviewer,
                                      "candidate_head": before.node("node-1").candidate_head,
                                      "findings": [], "verdict": "PASS"})
        receipts, findings = restarted.collect_review_cohort(leases)
        self.assertEqual(restarted.freeze_review("campaign", "node-1", receipts=receipts,
                                                findings=findings).action, "FINDINGS_FROZEN")
        after = self.store.get_snapshot("campaign")
        self.assertEqual(after.budgets, before.budgets)
        self.assertEqual(after.budget_receipts, before.budget_receipts)
        self.assertEqual(after.autonomous_rank_remaining, before.autonomous_rank_remaining)
        self.assertEqual(after.authority_epoch, before.authority_epoch)
        self.assertEqual(after.cancellation_epoch, before.cancellation_epoch)
        self.assertEqual(host.turns, turns)

    def test_live_closure_keeps_its_existing_generation_and_reviewer_identity(self):
        _, host, _, leases = self.start_review(closure=True)
        before = self.store.get_snapshot("campaign")
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        self.assertEqual(restarted.recover()["reattached_read_only_leases"], sorted(leases))
        self.assertEqual(self.store.get_snapshot("campaign"), before)
        self.assertEqual({runtime.role for runtime in restarted._workers.values()},
                         {ActorRole.CLOSURE_REVIEWER})
        self.assertEqual({runtime.reviewer_id for runtime in restarted._workers.values()},
                         set(before.spec.required_review_cohort))
        self.assertEqual(restarted.step("campaign").action, "YIELD")
        self.assertEqual(len(host.turns), len(leases))

    def test_one_lost_cohort_connection_fences_survivor_without_replacement(self):
        _, host, _, leases = self.start_review()
        host.unavailable.add(leases[0])
        before = self.store.get_snapshot("campaign")
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        result = restarted.recover()
        self.assertEqual(result["reattached_read_only_leases"], [])
        self.assertEqual(result["invalidated_leases"], len(leases))
        self.assertEqual(self.store.get_snapshot("campaign").state, CampaignState.FAILED)
        self.assertEqual(self.store.get_snapshot("campaign").budget_receipts, before.budget_receipts)
        self.assertTrue(set(leases).issubset(host.interrupted))
        self.assertEqual(len(host.turns), len(leases))
        self.assertEqual(restarted._workers, {})

    def test_changed_candidate_cannot_reuse_running_review(self):
        _, host, _, leases = self.start_review()
        self.commit_text("changed candidate\n", "change after review start")
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        result = restarted.recover()
        self.assertEqual(result["reattached_read_only_leases"], [])
        self.assertEqual(self.store.get_snapshot("campaign").state, CampaignState.FAILED)
        self.assertEqual(len(host.turns), len(leases))

    def test_changed_binding_cannot_be_substituted_for_original_turn(self):
        _, host, _, leases = self.start_review()
        original = host.bindings[leases[0]]
        host.bindings[leases[0]] = replace(original, native_thread_id="other-thread")
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        self.assertEqual(restarted.recover()["reattached_read_only_leases"], [])
        self.assertEqual(self.store.get_snapshot("campaign").state, CampaignState.FAILED)
        self.assertEqual(len(host.turns), len(leases))

    def test_expired_review_is_fenced_without_resetting_deadline_or_budget(self):
        _, host, _, leases = self.start_review()
        before = self.store.get_snapshot("campaign")
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW + timedelta(days=3))
        self.assertEqual(restarted.recover()["reattached_read_only_leases"], [])
        after = self.store.get_snapshot("campaign")
        self.assertEqual(after.state, CampaignState.FAILED)
        self.assertEqual(after.spec.deadline_utc, before.spec.deadline_utc)
        self.assertEqual(after.budget_receipts, before.budget_receipts)
        self.assertEqual(host.recovery_calls, [])
        self.assertEqual(len(host.turns), len(leases))

    def test_stop_after_reattachment_rejects_late_result(self):
        _, host, _, leases = self.start_review()
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        restarted.recover()
        restarted.cancel("campaign")
        self.assertEqual(restarted.recover()["reattached_read_only_leases"], [])
        with self.assertRaises(LateResultError):
            host.collect_terminal_receipt(leases[0], current_epochs=self.store.current_epochs)
        self.assertEqual(restarted.step("campaign").action, "TERMINAL")
        self.assertEqual(len(host.turns), len(leases))

    def test_existing_writer_is_never_reattached_or_replaced(self):
        self.create_approved(self.make_spec("campaign", mode="AUTOMATED"))
        host = SurvivingReadOnlyHost()
        original = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        self.dispatch_implementer(original)
        before = self.store.get_snapshot("campaign")
        writer_lease = self.store.list_active_leases("campaign")[0].lease_id
        with self.assertRaisesRegex(StoreError, "read-only reviewer"):
            self.store.recover_after_restart(retained_lease_ids=[writer_lease])
        self.assertEqual(self.store.get_snapshot("campaign"), before)
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        self.assertEqual(restarted.recover()["reattached_read_only_leases"], [])
        self.assertEqual(host.recovery_calls, [])
        self.assertEqual(len(host.turns), 1)
        self.assertEqual(self.store.get_snapshot("campaign").budget_receipts, before.budget_receipts)
        self.assertEqual(restarted.step("campaign").action, "TERMINAL")

    def test_store_rejects_partial_unknown_and_duplicate_retention_atomically(self):
        _, _, _, leases = self.start_review()
        before = self.store.get_snapshot("campaign")
        active = self.store.list_active_leases("campaign")
        for retained in ([leases[0]], ["unknown"], [leases[0], "unknown"], [leases[0], leases[0]]):
            with self.subTest(retained=retained), self.assertRaises(StoreError):
                self.store.recover_after_restart(retained_lease_ids=retained)
            self.assertEqual(self.store.get_snapshot("campaign"), before)
            self.assertEqual(self.store.list_active_leases("campaign"), active)

    def test_store_rejects_released_read_only_lease(self):
        spec, host, supervisor, leases = self.start_review()
        host.set_result(leases[0], {"status": "completed", "reviewer_id": spec.required_review_cohort[0],
                                   "candidate_head": self.base_sha, "findings": [], "verdict": "PASS"})
        supervisor.complete_worker(leases[0])
        before = self.store.get_snapshot("campaign")
        active = self.store.list_active_leases("campaign")
        with self.assertRaisesRegex(StoreError, "active bound"):
            self.store.recover_after_restart(retained_lease_ids=leases)
        self.assertEqual(self.store.get_snapshot("campaign"), before)
        self.assertEqual(self.store.list_active_leases("campaign"), active)

    def test_store_rejects_unbound_read_only_lease(self):
        spec, snapshot = self.create_manual_candidate()
        snapshot = self.store.apply_event(self.event(snapshot, EventType.START_REVIEW,
            node_id="node-1", payload={"review_id": "review", "review_cohort": list(spec.required_review_cohort)}))[0]
        supervisor = DeterministicSupervisor(self.store, host=FakeHost(), now=lambda: NOW)
        before, _, lease = supervisor._acquire_actor(snapshot, "node-1", ActorRole.REVIEWER,
                                                     reviewer_id="reviewer-a", native_worker=True)
        with self.assertRaisesRegex(StoreError, "active bound"):
            self.store.recover_after_restart(retained_lease_ids=[lease.lease_id])
        self.assertEqual(self.store.get_snapshot("campaign"), before)
        self.assertEqual(self.store.list_active_leases("campaign"), [lease])

    def test_rejected_attestation_does_not_fall_back_to_live_turn(self):
        spec, host, original, leases = self.start_review()
        host.set_result(leases[0], {"status": "completed", "reviewer_id": spec.required_review_cohort[0],
                                   "candidate_head": self.base_sha, "findings": [], "verdict": "PASS"})
        with patch.object(self.store, "release_lease", side_effect=RuntimeError("lost before release")):
            with self.assertRaisesRegex(RuntimeError, "lost before release"):
                original.complete_worker(leases[0])
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        with patch.object(self.store, "verify_terminal_evidence_attestation",
                          side_effect=RuntimeError("invalid attestation")):
            result = restarted.recover()
        self.assertEqual(result["reattached_read_only_leases"], [])
        self.assertNotIn(leases[0], host.recovery_calls)
        self.assertEqual(self.store.get_snapshot("campaign").state, CampaignState.FAILED)

    def test_existing_graph_selects_ready_independent_node_and_preserves_serial_owner(self):
        raw = self.make_spec("campaign", mode="AUTOMATED").to_dict()
        raw.pop("specification_digest")
        raw["allowed_paths"] = ["src/**", "docs/**", "assets/**"]
        raw["nodes"] = [
            {"node_id": "dependent", "objective": "wait for prerequisite", "dependencies": ["prerequisite"],
             "allowed_paths": ["docs/**"], "validation_command_ids": ["unit"]},
            {"node_id": "independent", "objective": "ready work", "allowed_paths": ["src/**"],
             "validation_command_ids": ["unit"]},
            {"node_id": "prerequisite", "objective": "prerequisite work", "allowed_paths": ["assets/**"],
             "validation_command_ids": ["unit"]},
        ]
        self.create_approved(CampaignSpec.from_dict(raw))
        supervisor = DeterministicSupervisor(self.store, host=FakeHost(), now=lambda: NOW)
        self.assertEqual(supervisor.step("campaign").action, "CAMPAIGN_STARTED")
        self.assertEqual(supervisor.step("campaign").node_id, "independent")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.node("dependent").state, NodeState.PENDING)
        self.assertIsNone(supervisor.select_next_approved_node(snapshot))
        waiting = self.store.apply_event(self.event(snapshot, EventType.WAIT_EXTERNAL))[0]
        self.assertEqual(supervisor.step("campaign").action, "YIELD")
        self.assertEqual(self.store.get_snapshot("campaign"), waiting)


class OrphanProcessIdentityTests(SupervisorFixture):
    """Use the persisted native identity and actual verified-termination guard."""

    def check_process_cleanup(self, *, operation, process_changed):
        self.create_approved(self.make_spec("campaign", mode="AUTOMATED"))
        transport = _FakeTransport()
        transport.process = SimpleNamespace(pid=731, poll=lambda: None)
        original_identity = {"pid": 731, "creation_token": "original", "executable": "codex-test"}
        with patch.object(native_host, "process_identity", return_value=original_identity) as probe:
            native = native_host.NativeCodexHost(transport_factory=lambda *a, **k: transport)
            self.addCleanup(native.close)
            supervisor = DeterministicSupervisor(self.store, host=native, now=lambda: NOW)
            lease_id = self.dispatch_implementer(supervisor)
            persisted = self.store.list_active_actor_identities()[0]
            self.assertEqual(persisted["native_identity"]["host_process_identity"], original_identity)
            self.assertEqual(persisted["lease_id"], lease_id)
            # Simulate loss of the owning supervisor connection. Only the durable
            # native identity is available to the new supervisor's cleanup path.
            recovered = DeterministicSupervisor(self.store, host=FakeHost(), now=lambda: NOW)
            if process_changed:
                probe.return_value = {**original_identity, "creation_token": "replacement"}
            with patch.object(native_host, "terminate_process_tree") as terminate:
                if operation == "cancel":
                    recovered.cancel("campaign")
                    self.assertEqual(self.store.get_snapshot("campaign").state, CampaignState.CANCELLED)
                else:
                    result = recovered.recover()
                    self.assertEqual(result["invalidated_leases"], 1)
                    self.assertEqual(self.store.get_snapshot("campaign").state, CampaignState.FAILED)
                if process_changed:
                    terminate.assert_not_called()
                else:
                    terminate.assert_called_once_with(731)
            self.assertEqual([method for method, _ in transport.calls].count("turn/start"), 1)

    def test_cancel_terminates_the_exact_persisted_native_process(self):
        self.check_process_cleanup(operation="cancel", process_changed=False)

    def test_cancel_does_not_terminate_a_reused_native_pid(self):
        self.check_process_cleanup(operation="cancel", process_changed=True)

    def test_recovery_terminates_the_exact_persisted_native_process(self):
        self.check_process_cleanup(operation="recover", process_changed=False)

    def test_recovery_does_not_terminate_a_reused_native_pid(self):
        self.check_process_cleanup(operation="recover", process_changed=True)


class NativeReadOnlyRecoveryTests(SupervisorFixture):
    """Exercise the actual host hook against controlled connection observations."""

    def setup_native(self, *, role="REVIEWER", started=True):
        transport = _FakeTransport()
        transport.process = SimpleNamespace(pid=731, poll=lambda: None)
        transport.reader = SimpleNamespace(is_alive=lambda: True)
        identity = {"pid": 731, "creation_token": "original", "executable": "codex-test"}
        process_probe = patch.object(native_host, "process_identity", return_value=identity)
        self.process_identity = process_probe.start()
        self.addCleanup(process_probe.stop)
        native = native_host.NativeCodexHost(transport_factory=lambda *a, **k: transport)
        self.addCleanup(native.close)
        lease = native_host.ActorLease.issue(
            lease_id="lease-review", request_id="request-review", campaign_id="campaign",
            node_id="node-1", actor_id="actor", role=role, worktree=str(self.repo),
            allowed_paths=("src/**",) if role == "IMPLEMENTER" else (), authority_epoch=7,
            cancellation_epoch=2, fencing_epoch=4, candidate_head=self.base_sha,
        )
        captured = []
        native.create_idle_actor(lease, bind_authority=lambda _, value: captured.append(dict(value)),
                                 authorize_action=lambda *_: {})
        if started:
            native.start_actor_turn(lease.lease_id, "inspect exact candidate")
        self.epochs = {"authority_epoch": 7, "cancellation_epoch": 2, "fencing_epoch": 4}
        return native, transport, lease, captured[0]

    def recover_native(self, native, lease, identity):
        return native.recover_read_only_actor(lease.lease_id, native_identity=identity,
                                              current_epochs=lambda *_: self.epochs)

    def test_same_verified_native_connection_returns_original_binding_without_protocol_calls(self):
        native, transport, lease, identity = self.setup_native()
        before = list(transport.calls)
        self.assertEqual(native._native_identities[lease.lease_id], identity)
        self.assertIs(self.recover_native(native, lease, identity), native._bindings[lease.lease_id])
        self.assertEqual(transport.calls, before)
        self.assertEqual([method for method, _ in before].count("thread/start"), 1)
        self.assertEqual([method for method, _ in before].count("turn/start"), 1)

    def test_reused_pid_is_not_the_original_native_process(self):
        native, transport, lease, identity = self.setup_native()
        self.process_identity.return_value = {**identity["host_process_identity"], "creation_token": "replacement"}
        before = list(transport.calls)
        with self.assertRaisesRegex(native_host.HostAuthorityError, "verifiably alive"):
            self.recover_native(native, lease, identity)
        self.assertEqual(transport.calls, before)

    def test_missing_dead_or_different_process_and_reader_are_rejected(self):
        native, transport, lease, identity = self.setup_native()
        for attribute, value, message in (
            ("process", None, "verifiably alive"),
            ("process", SimpleNamespace(pid=731, poll=lambda: 1), "verifiably alive"),
            ("process", SimpleNamespace(pid=732, poll=lambda: None), "verifiably alive"),
            ("reader", None, "reader is unavailable"),
            ("reader", SimpleNamespace(is_alive=lambda: False), "reader is unavailable"),
        ):
            with self.subTest(attribute=attribute, message=message), patch.object(transport, attribute, value):
                with self.assertRaisesRegex(native_host.HostAuthorityError, message):
                    self.recover_native(native, lease, identity)

    def test_changed_or_missing_native_identity_is_rejected(self):
        native, _, lease, identity = self.setup_native()
        for changed in ({}, {**identity, "thread_id": "other"}, {**identity, "lease_digest": "0" * 64}):
            with self.subTest(identity=changed), self.assertRaisesRegex(native_host.HostAuthorityError, "identity changed"):
                self.recover_native(native, lease, changed)
        native._native_identities.clear()
        with self.assertRaisesRegex(native_host.HostAuthorityError, "identity changed"):
            self.recover_native(native, lease, identity)

    def test_writer_and_unstarted_or_interrupted_turns_are_rejected(self):
        native, _, lease, identity = self.setup_native(role="IMPLEMENTER")
        with self.assertRaisesRegex(native_host.HostAuthorityError, "read-only connection"):
            self.recover_native(native, lease, identity)
        native, _, lease, identity = self.setup_native(started=False)
        with self.assertRaisesRegex(native_host.HostAuthorityError, "read-only connection"):
            self.recover_native(native, lease, identity)
        native.start_actor_turn(lease.lease_id, "review")
        native.interrupt(lease.lease_id)
        with self.assertRaisesRegex(native_host.HostAuthorityError, "read-only connection"):
            self.recover_native(native, lease, identity)

    def test_epoch_or_candidate_drift_rejects_recovery(self):
        native, _, lease, identity = self.setup_native()
        for name in self.epochs:
            with self.subTest(epoch=name), patch.dict(self.epochs, {name: self.epochs[name] + 1}):
                with self.assertRaises(LateResultError):
                    self.recover_native(native, lease, identity)
        self.commit_text("moved\n", "candidate moved")
        with self.assertRaisesRegex(native_host.HostAuthorityError, "differs from frozen candidate"):
            self.recover_native(native, lease, identity)
