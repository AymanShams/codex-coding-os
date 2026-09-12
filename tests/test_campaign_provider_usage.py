"""Synthetic native usage reports exercise accounting without model calls."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from scripts.agent.campaign_engine import (
    Actor, ActorRole, AuthorityError, CampaignSpec, CampaignStore,
    EventType, canonical_json_digest,
)
from scripts.agent.campaign_engine import host
from tests.test_campaign_store import StoreFixture, make_spec
from tests.test_campaign_runtime_components import _FakeTransport


def usage(thread_id="native-parent", *, input_tokens=100, output_tokens=20, turn_id="turn-1"):
    counts = {
        "inputTokens": input_tokens,
        "cachedInputTokens": input_tokens // 2,
        "outputTokens": output_tokens,
        "reasoningOutputTokens": output_tokens // 2,
        "totalTokens": input_tokens + output_tokens,
    }
    return {"threadId": thread_id, "turnId": turn_id,
            "tokenUsage": {"total": counts, "last": dict(counts)}}


class ProviderUsageTests(StoreFixture):
    def setUp(self):
        super().setUp()
        self.snapshot = self.admit(self.create_running())

    def bind(self, actor_id="parent", role=ActorRole.PARENT, *, bind=True):
        current = self.store.get_snapshot(self.snapshot.spec.campaign_id)
        actor = Actor(actor_id, current.spec.campaign_id, "node-1", role,
                      "UNBOUND", current.authority_epoch, False)
        lease = self.store.acquire_lease(
            current.spec.campaign_id, "node-1", actor_id, actor,
            lease_id=f"lease-{actor_id}", request_id=f"acquire-{actor_id}",
            expected_revision=current.revision, authority_epoch=current.authority_epoch,
            cancellation_epoch=current.cancellation_epoch,
        )
        if bind:
            identity = {
                "thread_id": f"native-{actor_id}", "cwd": current.spec.worktree,
                "source_digest": "a" * 64, "sandbox_type": "read-only",
                "writable_roots": [], "mediated_write_scope": [],
                "dynamic_tool_digest": "b" * 64, "native_write_mode": "denied",
                "role": role.value, "lease_digest": "c" * 64,
                "thread_created_idle": True,
            }
            self.store.bind_actor(
                lease.lease_id, actor_id, role, identity["thread_id"],
                canonical_json_digest(identity), current.authority_epoch,
                current.cancellation_epoch, lease.fencing_epoch, f"bind-{actor_id}",
                native_identity=identity,
            )
        return actor, lease

    def summary(self):
        return self.store.usage_summary(self.snapshot.spec.campaign_id)

    def test_missing_is_unavailable_and_cumulative_reports_are_counted_once(self):
        self.assertIsNone(self.summary()["totals"])
        self.bind()
        self.bind("reviewer", ActorRole.REVIEWER)
        self.assertEqual(self.summary()["status"], "unavailable")
        first = usage()
        initial = self.store.record_native_usage("parent", first)
        self.assertEqual(initial, self.store.record_native_usage("parent", first))
        self.store.record_native_usage("parent", usage(input_tokens=200, output_tokens=40))
        # A late replay of a smaller cumulative counter must not replace or add.
        self.store.record_native_usage("parent", usage(input_tokens=50, output_tokens=10))
        partial = self.summary()
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["totals"]["total_tokens"], 240)
        self.assertEqual(partial["coverage"]["unavailable_actor_ids"], ["reviewer"])
        self.store.record_native_usage("reviewer", usage("native-reviewer"))
        result = self.summary()
        self.assertEqual(result["totals"], {
            "input_tokens": 300, "cached_input_tokens": 150,
            "cache_write_input_tokens": 0, "output_tokens": 60,
            "reasoning_output_tokens": 30, "total_tokens": 360,
        })
        self.assertEqual(result["coverage"]["measured_actor_count"], 2)
        self.assertEqual(result["coverage"]["unbound_parent_usage"], "unavailable")
        self.assertFalse(result["coverage"]["complete_provider_spend"])

    def test_concurrent_duplicates_preserve_one_snapshot_in_existing_schema(self):
        self.bind()
        with ThreadPoolExecutor(max_workers=4) as executor:
            reports = list(executor.map(
                lambda _: self.store.record_native_usage("parent", usage()), range(12)
            ))
        self.assertTrue(all(item == reports[0] for item in reports))
        self.assertEqual(self.summary()["actors"][0]["observed_report_count"], 1)
        reopened = CampaignStore(self.database)
        self.assertEqual(reopened.integrity_check()["schema_version"], 1)
        self.assertEqual(reopened.usage_summary(self.snapshot.spec.campaign_id), self.summary())

    def test_failed_and_restarted_actors_retain_spend_without_state_changes(self):
        self.bind()
        self.store.record_native_usage("parent", usage())
        failed = self.store.apply_event(self.event(
            self.store.get_snapshot(self.snapshot.spec.campaign_id), EventType.FAIL_NODE,
            node_id="node-1", payload={"reason": "fixture failure"},
        ))[0]
        self.store.record_native_usage("parent", usage(input_tokens=200, output_tokens=40))
        self.assertEqual(self.store.get_snapshot(failed.spec.campaign_id), failed)
        reopened = CampaignStore(self.database)
        reopened.recover_after_restart()
        stored = reopened.record_native_usage("parent", usage(input_tokens=300, output_tokens=60))
        self.assertEqual(stored["recorded_campaign_state"], "FAILED")
        self.assertEqual(reopened.get_snapshot(failed.spec.campaign_id), failed)
        self.assertEqual(reopened.usage_summary(failed.spec.campaign_id)["totals"]["total_tokens"], 360)

    def test_cancelled_actor_can_report_spend_without_restoring_authority(self):
        self.bind()
        self.store.record_native_usage("parent", usage())
        cancelled = self.store.cancel_campaign(self.snapshot.spec.campaign_id, request_id="stop")
        reopened = CampaignStore(self.database)
        reopened.recover_after_restart()
        stored = reopened.record_native_usage("parent", usage(input_tokens=300, output_tokens=60))
        self.assertEqual(stored["recorded_campaign_state"], "CANCELLED")
        self.assertEqual(reopened.get_snapshot(cancelled.spec.campaign_id), cancelled)
        self.assertEqual(reopened.usage_summary(cancelled.spec.campaign_id)["totals"]["total_tokens"], 360)

    def test_unbound_or_foreign_threads_cannot_supply_campaign_usage(self):
        self.bind(bind=False)
        with self.assertRaises(AuthorityError):
            self.store.record_native_usage("parent", usage())
        self.bind("reviewer", ActorRole.REVIEWER)
        with self.assertRaises(AuthorityError):
            self.store.record_native_usage("reviewer", usage())
        with self.assertRaises(AuthorityError):
            self.store.record_native_usage("external-parent", usage())
        self.assertIsNone(self.summary()["totals"])

    def test_invalid_and_synthetic_context_counts_remain_unavailable(self):
        self.bind()
        for mutate in (
            lambda value: value["tokenUsage"]["total"].update(inputTokens=True),
            lambda value: value["tokenUsage"]["total"].update(outputTokens=-1),
            lambda value: value["tokenUsage"]["total"].update(cachedInputTokens=1000),
            lambda value: value["tokenUsage"]["total"].update(totalTokens=200000),
            lambda value: value["tokenUsage"].pop("last"),
        ):
            report = usage()
            mutate(report)
            self.assertEqual(self.store.record_native_usage("parent", report)["status"], "unavailable")
        self.assertIsNone(self.summary()["totals"])
        self.store.record_native_usage("parent", usage(input_tokens=0, output_tokens=0))
        self.assertEqual(self.summary()["totals"]["total_tokens"], 0)
        self.assertEqual(self.summary()["status"], "partial")

    def test_incomparable_counters_do_not_create_synthetic_component_totals(self):
        self.bind()
        self.store.record_native_usage("parent", usage(input_tokens=100, output_tokens=20))
        self.store.record_native_usage("parent", usage(input_tokens=90, output_tokens=40))
        result = self.summary()
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["actors"][0]["counter_discontinuity"])
        self.assertEqual(result["totals"]["total_tokens"], 130)
        self.assertEqual(result["totals"]["input_tokens"], 90)

    def test_reader_persists_before_queue_consumption_and_stream_failure(self):
        self.bind()
        transport = host.AppServerTransport(
            sys.executable, cwd=self.temporary.name,
            usage_handler=lambda params: self.store.record_native_usage("parent", params),
        )
        notifications = [
            {"method": "thread/tokenUsage/updated", "params": usage()},
            {"method": "turn/completed", "params": {
                "threadId": "native-parent", "turn": {"id": "turn-1", "status": "failed"},
            }},
        ]
        transport.process = SimpleNamespace(
            stdout=io.StringIO("".join(json.dumps(item) + "\n" for item in notifications)),
            poll=lambda: 1,
        )
        transport._read_loop()
        self.assertEqual(self.summary()["totals"]["total_tokens"], 120)
        self.assertEqual(transport.events, [])
        self.assertEqual(transport.wait_turn("native-parent", "turn-1")["status"], "failed")
        with self.assertRaises(host.HostProtocolError):
            transport._pump(0.001)
        self.assertEqual(CampaignStore(self.database).usage_summary(
            self.snapshot.spec.campaign_id)["totals"]["total_tokens"], 120)

    def test_capture_write_failure_is_not_silently_accepted(self):
        def fail(_):
            raise OSError("fixture disk failure")
        transport = host.AppServerTransport(sys.executable, cwd=self.temporary.name, usage_handler=fail)
        transport.process = SimpleNamespace(stdout=io.StringIO(json.dumps({
            "method": "thread/tokenUsage/updated", "params": usage(),
        }) + "\n"))
        transport._read_loop()
        with self.assertRaisesRegex(host.HostProtocolError, "durably recorded"):
            transport._pump(0.001)


class NativeUsageBindingTests(StoreFixture):
    def test_only_started_bound_thread_and_known_turn_are_recorded(self):
        root = Path(self.temporary.name).resolve()
        lease = host.ActorLease.issue(
            lease_id="lease", request_id="request", campaign_id="campaign",
            node_id="node", actor_id="parent", role="PARENT", worktree=str(root),
            allowed_paths=(), authority_epoch=1, cancellation_epoch=0,
            fencing_epoch=1, candidate_head="a" * 40,
        )
        transport = _FakeTransport()
        reports = []
        native = host.NativeCodexHost(
            transport_factory=lambda *a, **k: transport,
            usage_recorder=lambda actor_id, params: reports.append((actor_id, params)),
        )
        native.create_idle_actor(lease, bind_authority=lambda *args: None)
        transport.usage_handler(usage("native-thread-1"))
        self.assertEqual(reports, [])
        native.start_actor_turn(lease.lease_id, "fixture")
        transport.usage_handler(usage("native-thread-1", turn_id="foreign-turn"))
        transport.usage_handler(usage("external-parent"))
        self.assertEqual(reports, [])
        native.interrupt(lease.lease_id)
        transport.usage_handler(usage("native-thread-1"))
        self.assertEqual(reports, [("parent", usage("native-thread-1"))])

    def test_early_usage_is_bound_and_survives_abandoned_turn(self):
        root = Path(self.temporary.name).resolve()
        spec = make_spec("native-usage-binding").to_dict()
        spec.pop("specification_digest", None)
        spec.update(git_root=str(root), worktree=str(root))
        spec["required_validation_commands"][0]["working_directory"] = str(root)
        snapshot = self.store.create_campaign(CampaignSpec.from_dict(spec), request_id="create")
        snapshot = self.store.apply_event(self.event(snapshot, EventType.APPROVE,
            payload={"specification_digest": snapshot.spec.specification_digest}))[0]
        snapshot = self.store.apply_event(self.event(snapshot, EventType.START))[0]
        snapshot = self.admit(snapshot)
        provisional = Actor("parent", snapshot.spec.campaign_id, "node-1",
                            ActorRole.PARENT, "UNBOUND", snapshot.authority_epoch, False)
        stored_lease = self.store.acquire_lease(snapshot.spec.campaign_id, "node-1", "parent", provisional,
            lease_id="lease-parent", request_id="acquire", expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch, cancellation_epoch=snapshot.cancellation_epoch)
        lease = host.ActorLease.issue(lease_id=stored_lease.lease_id, request_id="turn-request",
            campaign_id=snapshot.spec.campaign_id, node_id="node-1", actor_id="parent", role="PARENT",
            worktree=str(root), allowed_paths=(), authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch, fencing_epoch=stored_lease.fencing_epoch,
            candidate_head=snapshot.spec.base_sha)

        class AbandonedTransport(_FakeTransport):
            def request(self, method, params=None, timeout=None):
                if method == "turn/start":
                    self.usage_handler(usage("native-thread-1"))
                    raise host.HostProtocolError("abandoned before turn response")
                return super().request(method, params, timeout)

        transport = AbandonedTransport()
        native = host.NativeCodexHost(transport_factory=lambda *a, **k: transport,
                                      usage_recorder=self.store.record_native_usage)
        def bind(bound, identity):
            self.store.bind_actor(bound.lease_id, bound.actor_id, bound.role, identity["thread_id"],
                canonical_json_digest(identity), bound.authority_epoch, bound.cancellation_epoch,
                bound.fencing_epoch, "bind", native_identity=identity)
        native.create_idle_actor(lease, bind_authority=bind)
        transport.usage_handler(usage("unrelated-parent"))
        self.assertIsNone(self.store.usage_summary(snapshot.spec.campaign_id)["totals"])
        with self.assertRaisesRegex(host.HostProtocolError, "abandoned"):
            native.start_actor_turn(lease.lease_id, "synthetic fixture")
        self.assertTrue(transport.closed)
        result = self.store.usage_summary(snapshot.spec.campaign_id)
        self.assertEqual(result["totals"]["total_tokens"], 120)
        self.assertEqual(result["actors"][0]["role"], "PARENT")
