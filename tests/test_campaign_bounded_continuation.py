"""Executable continuation, closure, and confirmed-result recovery journeys."""

from contextlib import closing
from dataclasses import replace
import hashlib
import json
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

from scripts.agent.campaign_engine.evidence import ValidationFailure, execute_trusted_command
from scripts.agent.campaign_engine.effects import ExternalEffectDriver
from scripts.agent.campaign_engine.host import FakeHost
from scripts.agent.campaign_engine.model import (
    BudgetToken, CampaignSpec, CampaignState, EventType, NodeState,
)
from scripts.agent.campaign_engine.supervisor import DeterministicSupervisor, SupervisorError
from tests.test_campaign_supervisor import NOW, SupervisorFixture, git


ORDER_SUMMARY_BASE_SOURCE = """import json
import sys


def summarize(orders):
    total = sum(order["amount"] for order in orders)
    return {"total": total}


if __name__ == "__main__":
    print(json.dumps(summarize(json.load(sys.stdin)["orders"]), sort_keys=True))
"""
ORDER_SUMMARY_COUNT_DEFECT_SOURCE = ORDER_SUMMARY_BASE_SOURCE.replace(
    'return {"total": total}',
    'return {"order_count": sum(order["amount"] > 0 for order in orders), "total": total}',
)
ORDER_SUMMARY_SOURCE = ORDER_SUMMARY_BASE_SOURCE.replace(
    'return {"total": total}', 'return {"order_count": len(orders), "total": total}',
)
ORDER_SUMMARY_REQUIREMENT = (
    "Add order_count to the order summary JSON output while preserving total.\n"
    "Read an orders array from JSON on standard input, with each amount expressed in integer cents.\n"
    "Count every supplied entry, including zero-value entries and refunds with negative amounts.\n"
    "Return the sum of all amounts as total. An empty array returns order_count 0 and total 0.\n"
)
ORDER_SUMMARY_ACCEPTANCE_BODY = """import json
import subprocess
import sys
cases = (
    ({"orders": [{"amount": 1250}, {"amount": 750}]}, {"order_count": 2, "total": 2000}),
    ({"orders": [{"amount": 1250}, {"amount": 0}, {"amount": -250}]}, {"order_count": 3, "total": 1000}),
    ({"orders": []}, {"order_count": 0, "total": 0}),
)
for request, expected in cases:
    result = subprocess.run(
        [sys.executable, "-B", "src/order_summary.py"], input=json.dumps(request),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False,
    )
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(json.loads(result.stdout), expected)
"""


class BoundedContinuationTests(SupervisorFixture):
    def prepare(self, *, budget_overrides=None, test_body=None, deliver=False,
                artifact_path="src/app.txt", objective_kind="PRODUCT_CODE",
                base_source="base\n", candidate_source="incorrect\n",
                requirement_text="The result is accepted.\n", expectation="result is accepted"):
        body = test_body or f"self.assertEqual(Path({artifact_path!r}).read_text().strip(), 'accepted')"
        product = self.repo / artifact_path
        product.parent.mkdir(parents=True, exist_ok=True)
        if not product.exists():
            product.write_text(base_source, encoding="utf-8")
        test_source = ("import unittest\nfrom pathlib import Path\n"
                       "class Acceptance(unittest.TestCase):\n"
                       "    def test_contract(self):\n"
                       + "".join("        " + line + "\n" for line in body.splitlines()))
        (self.repo / "test_acceptance.py").write_text(test_source, encoding="utf-8")
        (self.repo / "requirements.md").write_text(requirement_text, encoding="utf-8")
        git(self.repo, "add", "test_acceptance.py", "requirements.md", artifact_path)
        git(self.repo, "commit", "-q", "-m", "independent acceptance expectation")
        self.base_sha = git(self.repo, "rev-parse", "HEAD")
        raw = self.make_spec("campaign", mode="AUTOMATED", budget_overrides=budget_overrides).to_dict()
        raw.pop("specification_digest")
        raw["objective_kind"] = objective_kind
        raw["allowed_paths"] = [artifact_path.partition("/")[0] + "/**"]
        raw["nodes"][0]["allowed_paths"] = list(raw["allowed_paths"])
        if expectation != "result is accepted":
            raw["objective"] = expectation
            raw["nodes"][0]["objective"] = expectation
        if deliver:
            self.delivery = self.root / "delivery.git"
            git(self.root, "init", "--bare", "-q", str(self.delivery))
            git(self.repo, "remote", "set-url", "origin", self.delivery.as_uri())
            raw["repository_remote"] = self.delivery.as_uri()
            raw["publication_authority"]["automated"] = True
            raw["publication_authority"]["allowed_effects"] = ["PUSH"]
            raw["publication_authority"]["required_effects"] = ["PUSH"]
        command = raw["required_validation_commands"][0]
        command["arguments"] = ["-B", "-m", "unittest", "-v", "test_acceptance.Acceptance.test_contract"]
        command["correction_policy"] = {
            "adapter": "unittest", "expectation_id": "accepted",
            "test_id": "test_acceptance.Acceptance.test_contract",
        }
        raw["nodes"][0]["acceptance_scenarios"] = [{
            "scenario_id": "accepted", "expectation": expectation, "validation_command_id": "unit",
            "sources": [{"path": name, "requirement_id": name,
                         "sha256": hashlib.sha256((self.repo / name).read_bytes()).hexdigest()}
                        for name in ("requirements.md", "test_acceptance.py")],
        }]
        self.create_approved(CampaignSpec.from_dict(raw))
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease = self.dispatch_implementer(supervisor)
        product.write_text(candidate_source, encoding="utf-8")
        git(self.repo, "add", artifact_path)
        git(self.repo, "commit", "-q", "-m", "initial candidate")
        host.set_result(lease, {"status": "completed"})
        supervisor.complete_worker(lease)
        return supervisor, host

    def test_product_journey_corrects_validates_reviews_and_delivers_exact_local_git_head(self):
        artifact_path = "src/order_summary.py"
        supervisor, host = self.prepare(
            deliver=True, artifact_path=artifact_path,
            base_source=ORDER_SUMMARY_BASE_SOURCE, candidate_source=ORDER_SUMMARY_COUNT_DEFECT_SOURCE,
            requirement_text=ORDER_SUMMARY_REQUIREMENT, test_body=ORDER_SUMMARY_ACCEPTANCE_BODY,
            expectation="Report every order in order_count while preserving the total in cents",
        )
        sources_before = {name: (self.repo / name).read_bytes()
                          for name in ("requirements.md", "test_acceptance.py")}
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_CORRECTION_READY")
        failed = self.validation_receipts()[0]
        self.assertEqual(failed["exit_code"], 1)
        self.assertIn("FAILED (failures=1)", failed["stderr"])
        self.assertNotIn("ERROR:", failed["stderr"])
        lease = supervisor.step("campaign").details["lease_id"]
        self.expected_delivery_artifact = ORDER_SUMMARY_SOURCE.encode("utf-8")
        (self.repo / artifact_path).write_bytes(self.expected_delivery_artifact)
        git(self.repo, "add", artifact_path)
        git(self.repo, "commit", "-q", "-m", "count all orders while preserving the total")
        head = git(self.repo, "rev-parse", "HEAD")
        host.set_result(lease, {"status": "completed"})
        supervisor.complete_worker(lease)
        self.assertEqual(supervisor.step("campaign").action, "CANDIDATE_FROZEN")
        self.assertEqual({name: (self.repo / name).read_bytes() for name in sources_before}, sources_before)
        self.deliver(supervisor, host, head, artifact_path)
        self.assertEqual(len(self.validation_receipts()), 2)
        self.assertEqual({item["passed"] for item in self.validation_receipts()}, {False, True})
        self.assertEqual(self.store.get_snapshot("campaign").node("node-1").validation_corrections, 1)

    def deliver(self, supervisor, host, head, artifact_path):
        spec = self.store.get_snapshot("campaign").spec
        receipts, findings = self.collect_passing_review(supervisor, host, spec)
        supervisor.freeze_review("campaign", "node-1", receipts=receipts, findings=findings)
        self.assertEqual(supervisor.step("campaign").action, "READY_TO_PUBLISH")
        fixture = self
        class LocalDelivery:
            executions = 0
            def query(self, kind, payload):
                fixture.assertEqual(kind, "PUSH")
                output = git(fixture.repo, "ls-remote", "--heads", "origin", "refs/heads/main")
                return {"confirmed": bool(output) and output.split()[0] == payload["candidate_head"]}
            def execute(self, kind, payload):
                fixture.assertEqual(kind, "PUSH")
                self.executions += 1
                git(fixture.repo, "push", "origin", f"{payload['candidate_head']}:refs/heads/main")
                return {"pushed": payload["candidate_head"]}
        backend = LocalDelivery()
        supervisor.effect_driver = ExternalEffectDriver(self.store, backend)
        prepared = supervisor.step("campaign")
        self.assertEqual(prepared.action, "PUBLICATION_PREPARED")
        operation_id = prepared.details["operation_id"]
        self.assertEqual(supervisor.step("campaign").action, "PUBLICATION_CONFIRMED")
        self.assertEqual(supervisor.step("campaign").campaign_state, "COMPLETED")
        self.assertEqual(git(self.delivery, "rev-parse", "refs/heads/main"), head)
        expected_artifact = getattr(self, "expected_delivery_artifact", None)
        if expected_artifact is None:
            self.assertEqual(git(self.delivery, "show", f"{head}:{artifact_path}"), "accepted")
        else:
            delivered = subprocess.run(
                ["git", "show", f"{head}:{artifact_path}"], cwd=self.delivery,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30,
            )
            self.assertEqual(delivered.stdout, expected_artifact)
        self.assertEqual(self.store.get_effect(operation_id)["state"], "CONFIRMED")
        self.assertEqual(backend.executions, 1)

    def test_documentation_objective_completes_with_verified_document_artifact(self):
        supervisor, host = self.prepare(deliver=True, artifact_path="docs/guide.md",
                                        objective_kind="PRODUCT_DOCUMENTATION")
        (self.repo / "docs" / "guide.md").write_text("accepted\n", encoding="utf-8")
        git(self.repo, "add", "docs/guide.md")
        git(self.repo, "commit", "-q", "-m", "complete approved guide")
        head = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(supervisor.step("campaign").action, "CANDIDATE_FROZEN")
        self.deliver(supervisor, host, head, "docs/guide.md")
        self.assertEqual(self.store.get_snapshot("campaign").node("node-1").validation_corrections, 0)
        self.assertEqual(len(self.validation_receipts()), 1)

    def test_feature_objective_cannot_complete_from_documentation_only_patch(self):
        supervisor, host = self.prepare()
        # Restore the feature artifact to its pre-implementation bytes, leaving
        # only documentation as the node's committed diff.
        (self.repo / "src" / "app.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "src" / "feature-notes.md").write_text("The feature is accepted.\n", encoding="utf-8")
        git(self.repo, "add", "src/app.txt", "src/feature-notes.md")
        git(self.repo, "commit", "-q", "-m", "document feature without implementing it")
        self.assertEqual(git(self.repo, "diff", "--name-only", self.base_sha, "HEAD"), "src/feature-notes.md")
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_CORRECTION_READY")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.node("node-1").state, NodeState.IMPLEMENTING)
        self.assertIsNone(snapshot.node("node-1").candidate_head)
        self.assertEqual(self.store.list_outbox(campaign_id="campaign"), [])
        self.assertEqual(len(host.turns), 1)

    def validation_receipts(self):
        with closing(sqlite3.connect(self.store.path)) as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT payload_json FROM evidence WHERE kind='VALIDATION' ORDER BY evidence_id")]

    def test_approved_failure_corrects_once_with_fresh_lease_and_full_revalidation(self):
        supervisor, host = self.prepare()
        before = self.store.get_snapshot("campaign")
        failed_head = git(self.repo, "rev-parse", "HEAD")
        decision = supervisor.step("campaign")
        self.assertEqual(decision.action, "VALIDATION_CORRECTION_READY")
        corrected = self.store.get_snapshot("campaign")
        self.assertEqual(corrected.node("node-1").validation_corrections, 1)
        self.assertEqual(corrected.node("node-1").implementation_attempts, 1)
        self.assertIsNone(corrected.node("node-1").candidate_head)
        self.assertEqual(self.validation_receipts()[0]["exit_code"], 1)
        # A restart at this durable boundary never replenishes or repeats the first attempt.
        restarted = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        self.assertEqual(restarted.recover()["failed_nodes"], [])
        dispatched = restarted.step("campaign")
        self.assertEqual(dispatched.action, "VALIDATION_CORRECTION_DISPATCHED")
        lease = dispatched.details["lease_id"]
        self.assertEqual(host.bindings[lease].lease.candidate_head, failed_head)
        self.assertEqual(restarted.step("campaign").action, "YIELD")
        self.commit_text("accepted\n", "bounded correction")
        accepted_head = git(self.repo, "rev-parse", "HEAD")
        host.set_result(lease, {"status": "completed"})
        restarted.complete_worker(lease)
        self.assertEqual(restarted.step("campaign").action, "CANDIDATE_FROZEN")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.node("node-1").candidate_head, accepted_head)
        self.assertEqual(snapshot.node("node-1").implementation_attempts, 1)
        self.assertEqual(snapshot.node("node-1").validation_corrections, 1)
        receipts = self.validation_receipts()
        self.assertEqual({item["passed"] for item in receipts}, {False, True})
        self.assertEqual({item["candidate_head"] for item in receipts}, {failed_head, accepted_head})
        consumed = self.budget_consumed(snapshot)
        self.assertEqual(consumed[BudgetToken.VALIDATION_EXECUTION], 2)
        self.assertEqual(consumed[BudgetToken.CHILD_CREATION], 2)
        self.assertEqual(consumed[BudgetToken.CHILD_START], 2)
        self.assertEqual(snapshot.autonomous_rank_remaining, before.autonomous_rank_remaining - 4)

    def test_second_failure_stops_and_retains_both_expenditures(self):
        supervisor, host = self.prepare()
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_CORRECTION_READY")
        lease = supervisor.step("campaign").details["lease_id"]
        self.commit_text("still incorrect\n", "correction still fails")
        host.set_result(lease, {"status": "completed"})
        supervisor.complete_worker(lease)
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_FAILED")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.state, CampaignState.FAILED)
        self.assertEqual(len(self.validation_receipts()), 2)
        self.assertEqual(self.budget_consumed(snapshot)[BudgetToken.VALIDATION_EXECUTION], 2)
        self.assertEqual(supervisor.step("campaign").action, "TERMINAL")
        self.assertEqual(len(host.turns), 2)

    def test_test_error_is_not_an_assertion_correction(self):
        supervisor, host = self.prepare(test_body="raise RuntimeError('host or fixture problem')")
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_FAILED")
        self.assertEqual(self.store.get_snapshot("campaign").node("node-1").validation_corrections, 0)
        self.assertEqual(len(host.turns), 1)

    def test_unknown_exit_is_not_a_correction_even_with_matching_assertion_output(self):
        supervisor, _ = self.prepare()
        def unknown(command):
            try:
                execute_trusted_command(command)
            except ValidationFailure as exc:
                from scripts.agent.campaign_engine.model import canonical_json_digest
                payload = exc.evidence.to_dict()
                payload.pop("evidence_sha256")
                payload["exit_code"] = 7
                evidence = replace(exc.evidence, exit_code=7, evidence_sha256=canonical_json_digest(payload))
                raise ValidationFailure("unknown exit", evidence=evidence)
        with patch("scripts.agent.campaign_engine.supervisor.execute_trusted_command", side_effect=unknown):
            self.assertEqual(supervisor.step("campaign").action, "VALIDATION_FAILED")
        self.assertEqual(self.validation_receipts()[0]["exit_code"], 7)

    def test_no_remaining_validation_budget_denies_correction_before_worker(self):
        supervisor, host = self.prepare(budget_overrides={BudgetToken.VALIDATION_EXECUTION: 1})
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_FAILED")
        self.assertEqual(len(host.turns), 1)
        self.assertEqual(self.store.get_snapshot("campaign").node("node-1").validation_corrections, 0)

    def test_correction_requires_remaining_review_capacity(self):
        supervisor, host = self.prepare(budget_overrides={BudgetToken.REVIEW_DISPATCH: 0})
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_FAILED")
        self.assertEqual(len(host.turns), 1)

    def test_correction_requires_remaining_delivery_capacity(self):
        supervisor, host = self.prepare(budget_overrides={BudgetToken.MERGE: 0})
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_FAILED")
        self.assertEqual(len(host.turns), 1)

    def test_candidate_change_after_correction_admission_stops_without_dispatch(self):
        supervisor, host = self.prepare()
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_CORRECTION_READY")
        self.commit_text("unapproved concurrent head\n", "changed between failure and dispatch")
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_FAILED")
        self.assertEqual(len(host.turns), 1)

    def test_out_of_scope_failed_candidate_cannot_open_correction(self):
        supervisor, host = self.prepare()
        (self.repo / "outside.txt").write_text("outside scope\n", encoding="utf-8")
        git(self.repo, "add", "outside.txt")
        git(self.repo, "commit", "-q", "-m", "out of scope")
        self.assertEqual(supervisor.step("campaign").action, "VALIDATION_FAILED")
        self.assertEqual(len(host.turns), 1)

    def test_corrupt_failed_receipt_stops_without_correction_or_receipt_promotion(self):
        supervisor, host = self.prepare()
        def corrupt(command):
            try:
                execute_trusted_command(command)
            except ValidationFailure as exc:
                raise ValidationFailure("untrusted result", evidence=replace(exc.evidence, stdout="changed"))
        with patch("scripts.agent.campaign_engine.supervisor.execute_trusted_command", side_effect=corrupt):
            self.assertEqual(supervisor.step("campaign").action, "VALIDATION_FAILED")
        self.assertEqual(self.validation_receipts(), [])
        self.assertEqual(self.store.get_snapshot("campaign").node("node-1").validation_corrections, 0)
        self.assertEqual(len(host.turns), 1)

    def test_cancellation_during_failed_validation_never_opens_correction(self):
        supervisor, host = self.prepare()
        def cancel_on_failure(command):
            try:
                execute_trusted_command(command)
            except ValidationFailure:
                supervisor.cancel("campaign")
                raise
        with patch("scripts.agent.campaign_engine.supervisor.execute_trusted_command", side_effect=cancel_on_failure):
            self.assertEqual(supervisor.step("campaign").action, "TERMINAL")
        snapshot = self.store.get_snapshot("campaign")
        self.assertEqual(snapshot.state, CampaignState.CANCELLED)
        self.assertEqual(snapshot.node("node-1").validation_corrections, 0)
        self.assertEqual(self.budget_consumed(snapshot)[BudgetToken.VALIDATION_EXECUTION], 1)
        self.assertEqual(len(host.turns), 1)


class ClosureAndRecoveryTests(SupervisorFixture):
    def test_cancelled_campaign_remains_terminal_after_its_deadline(self):
        spec = self.make_spec("campaign", campaign_deadline="2026-08-04T11:00:00Z")
        self.create_approved(spec)
        supervisor = DeterministicSupervisor(self.store, host=FakeHost(), now=lambda: NOW)
        supervisor.cancel("campaign")
        before = self.store.get_snapshot("campaign")
        self.assertEqual(supervisor.step("campaign").action, "TERMINAL")
        self.assertEqual(self.store.get_snapshot("campaign"), before)

    def closure_result(self, finding, *, resolved=True, verdict=None):
        spec, _ = self.create_manual_closure()
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        leases = supervisor.step("campaign").details["leases"]
        for index, (reviewer, lease) in enumerate(zip(spec.required_review_cohort, leases)):
            host.set_result(lease, {"status": "completed", "reviewer_id": reviewer,
                "candidate_head": self.base_sha, "resolved_finding_ids": ["F-1"] if resolved else [],
                "findings": [finding] if index == 0 else [],
                "verdict": verdict if index == 0 and verdict else "PASS"})
        receipts, findings = supervisor.collect_review_cohort(leases)
        decision = supervisor.complete_closure("campaign", "node-1", receipts=receipts,
            resolved_finding_ids=["F-1"] if resolved else [], findings=findings)
        return supervisor, decision

    def test_observation_persists_and_closure_does_not_reopen(self):
        observation = {"finding_id": "O-1", "title": "possible wording improvement",
                       "blocking": False, "details": {"observation": "optional wording has no behavior impact"}}
        supervisor, result = self.closure_result(observation)
        self.assertEqual(result.action, "CLOSURE_PASSED")
        before = self.store.get_snapshot("campaign")
        node = before.node("node-1")
        self.assertFalse(node.closure_findings[0].blocking)
        self.assertEqual(node.closure_generations, 1)
        self.assertEqual(supervisor.step("campaign").wait_event, "publication_authorized")
        self.assertEqual(self.store.get_snapshot("campaign").revision, before.revision)

    def test_unresolved_original_blocker_stops_despite_nonblocking_observation(self):
        supervisor, result = self.closure_result({"finding_id": "O-1", "title": "wording",
            "blocking": False, "details": {"observation": "optional"}}, resolved=False)
        self.assertEqual(result.action, "CLOSURE_FAILED")
        self.assertIn("remaining=F-1", self.store.get_snapshot("campaign").failure_reason)
        self.assertEqual(supervisor.step("campaign").action, "TERMINAL")

    def test_new_regression_cannot_be_downgraded_by_observation_flag(self):
        _, result = self.closure_result({"finding_id": "R-1", "title": "new regression",
            "blocking": False, "details": {"observation": "claimed cosmetic",
                "regression": {"reproduction": "run original acceptance", "expected": "success", "actual": "failure"}}}, verdict="BLOCK")
        self.assertEqual(result.action, "CLOSURE_FAILED")
        self.assertTrue(self.store.get_snapshot("campaign").node("node-1").closure_findings[0].blocking)

    def test_confirmed_terminal_survives_crash_before_lifecycle_event_without_new_worker(self):
        spec = self.make_spec("campaign", mode="AUTOMATED")
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease = self.dispatch_implementer(supervisor)
        self.commit_text("candidate\n", "candidate")
        host.set_result(lease, {"status": "completed"})
        with patch.object(supervisor, "_apply", side_effect=RuntimeError("crash after attestation")):
            with self.assertRaisesRegex(RuntimeError, "crash after attestation"):
                supervisor.complete_worker(lease)
        before = self.store.get_snapshot("campaign")
        restarted_host = FakeHost()
        restarted = DeterministicSupervisor(self.store, host=restarted_host, now=lambda: NOW)
        recovery = restarted.recover()
        self.assertEqual(recovery["reattached_terminal_leases"], [lease])
        self.assertEqual(recovery["invalidated_leases"], 0)
        after = self.store.get_snapshot("campaign")
        self.assertEqual(after.node("node-1").state, NodeState.VALIDATING)
        self.assertEqual(after.budget_receipts, before.budget_receipts)
        self.assertEqual(after.cancellation_epoch, before.cancellation_epoch)
        self.assertEqual(after.node("node-1").implementation_attempts, 1)
        self.assertEqual(restarted_host.turns, [])
        self.assertEqual(restarted.recover()["reattached_terminal_leases"], [])
        self.assertEqual(restarted.step("campaign").action, "CANDIDATE_FROZEN")

    def test_readonly_cohort_receipts_reattach_without_new_dispatch(self):
        spec, _ = self.create_manual_candidate()
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        leases = supervisor.begin_review("campaign", "node-1").details["leases"]
        for reviewer, lease in zip(spec.required_review_cohort, leases):
            host.set_result(lease, {"status": "completed", "reviewer_id": reviewer,
                "candidate_head": self.base_sha, "findings": [], "verdict": "PASS"})
            supervisor.complete_worker(lease)
        restarted_host = FakeHost()
        restarted = DeterministicSupervisor(self.store, host=restarted_host, now=lambda: NOW)
        self.assertEqual(restarted.recover()["failed_nodes"], [])
        receipts, findings = restarted.collect_review_cohort(leases)
        self.assertEqual(restarted.freeze_review("campaign", "node-1", receipts=receipts, findings=findings).action,
                         "FINDINGS_FROZEN")
        self.assertEqual(restarted_host.turns, [])

    def test_unattested_terminal_cannot_reattach_or_start_another_writer(self):
        spec = self.make_spec("campaign", mode="AUTOMATED")
        self.create_approved(spec)
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        lease = self.dispatch_implementer(supervisor)
        host.set_result(lease, {"status": "completed"})
        with patch.object(supervisor, "_apply", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                supervisor.complete_worker(lease)
        with closing(sqlite3.connect(self.store.path)) as connection:
            connection.execute("DELETE FROM operations WHERE kind='ATTEST_NATIVE_TERMINAL_RECEIPT'")
            connection.commit()
        before = self.store.get_snapshot("campaign")
        restarted_host = FakeHost()
        restarted = DeterministicSupervisor(self.store, host=restarted_host, now=lambda: NOW)
        recovery = restarted.recover()
        self.assertEqual(recovery["reattached_terminal_leases"], [])
        self.assertEqual(recovery["failed_nodes"], [{"campaign_id": "campaign", "node_id": "node-1"}])
        self.assertEqual(self.store.get_snapshot("campaign").budget_receipts, before.budget_receipts)
        self.assertEqual(restarted_host.turns, [])
        self.assertEqual(restarted.step("campaign").action, "TERMINAL")

    def test_changed_or_contradictory_review_verdict_cannot_accept_candidate(self):
        spec, _ = self.create_manual_candidate()
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        receipts, findings = self.collect_passing_review(supervisor, host, spec)
        receipts[0]["verdict"] = "BLOCK"
        with self.assertRaisesRegex(SupervisorError, "verdict"):
            supervisor.freeze_review("campaign", "node-1", receipts=receipts, findings=findings)
        self.assertEqual(self.store.get_snapshot("campaign").node("node-1").state, NodeState.CHECKS_AND_REVIEW)

    def test_two_reviewers_rewording_one_finding_freeze_one_complete_blocker(self):
        spec, _ = self.create_manual_candidate()
        host = FakeHost()
        supervisor = DeterministicSupervisor(self.store, host=host, now=lambda: NOW)
        leases = supervisor.begin_review("campaign", "node-1").details["leases"]
        for index, (reviewer, lease) in enumerate(zip(spec.required_review_cohort, leases)):
            finding = {"finding_id": "same-defect", "title": ("Wrong output" if index else "Output differs"),
                       "blocking": True, "details": {"invariant": "exact expected result", "evidence": "observed wrong result"}}
            host.set_result(lease, {"status": "completed", "reviewer_id": reviewer,
                "candidate_head": self.base_sha, "findings": [finding], "verdict": "BLOCK"})
        receipts, findings = supervisor.collect_review_cohort(leases)
        self.assertEqual(len(receipts), 2)
        self.assertEqual(len(findings), 2)
        result = supervisor.freeze_review("campaign", "node-1", receipts=receipts, findings=findings)
        self.assertEqual(result.details["blocking_finding_ids"], ["same-defect"])
        frozen = self.store.get_snapshot("campaign").node("node-1").findings
        self.assertEqual(len(frozen), 1)
        self.assertEqual(frozen[0].title, "Output differs")


if __name__ == "__main__":
    unittest.main()
