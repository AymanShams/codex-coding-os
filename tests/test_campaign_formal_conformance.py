#!/usr/bin/env python3
"""Executable conformance checks between Campaign.tla and the pure reducer."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from scripts.agent.campaign_engine import CampaignState, EventType, NodeState
from tests.test_campaign_model_reducer import Events, running_snapshot


ROOT = Path(__file__).resolve().parents[1]
FORMAL_PATH = ROOT / "formal" / "Campaign.tla"
CONFIG_PATH = ROOT / "formal" / "Campaign.cfg"


def definition_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(name)}(?:\([^)]*\))?\s*==(?P<first>[^\n]*)",
        source,
    )
    if match is None:
        raise AssertionError(f"missing TLA+ definition: {name}")
    following = re.search(
        r"(?m)^[A-Za-z][A-Za-z0-9_]*(?:\([^)]*\))?\s*==",
        source[match.end() :],
    )
    end = match.end() + following.start() if following else len(source)
    return match.group("first") + source[match.end() : end]


def string_set(source: str, name: str) -> set[str]:
    return set(re.findall(r'"([^"]+)"', definition_body(source, name)))


def transition_contract(source: str) -> set[tuple[str, str, str]]:
    body = definition_body(source, "CoreReducerNodeTransitions")
    return {
        tuple(match)
        for match in re.findall(
            r'<<\s*"([A-Z_]+)"\s*,\s*"([A-Z_]+)"\s*,\s*"([A-Z_]+)"\s*>>',
            body,
        )
    }


class ReducerRecorder:
    """Record node-state edges emitted by real reducer calls."""

    def __init__(self, events: Events, snapshot) -> None:
        self.events = events
        self.snapshot = snapshot
        self.edges: set[tuple[str, str, str]] = set()

    def apply(self, event_type: EventType, **kwargs) -> None:
        node_id = kwargs.get("node_id")
        before = self.snapshot.node(node_id).state if node_id else None
        self.snapshot, _ = self.events.apply(self.snapshot, event_type, **kwargs)
        after = self.snapshot.node(node_id).state if node_id else None
        if before is not None and after is not before:
            self.edges.add((event_type.value, before.value, after.value))


def candidate_recorder() -> ReducerRecorder:
    events, snapshot = running_snapshot()
    recorder = ReducerRecorder(events, snapshot)
    recorder.apply(
        EventType.ADMIT_NODE,
        node_id="node-1",
        payload={"start_head": snapshot.spec.base_sha},
    )
    recorder.apply(EventType.START_IMPLEMENTATION, node_id="node-1")
    recorder.apply(EventType.IMPLEMENTATION_COMPLETED, node_id="node-1")
    recorder.apply(
        EventType.VALIDATION_PASSED,
        node_id="node-1",
        payload={
            "candidate_head": "d" * 40,
            "candidate_tree": "e" * 40,
            "candidate_diff_digest": "f" * 64,
            "candidate_node_diff_digest": "f" * 64,
        },
    )
    return recorder


def blocking_repair_recorder() -> ReducerRecorder:
    recorder = candidate_recorder()
    recorder.apply(
        EventType.START_REVIEW,
        node_id="node-1",
        payload={"review_cohort": ["reviewer-1", "reviewer-2"]},
    )
    recorder.apply(
        EventType.FREEZE_FINDINGS,
        node_id="node-1",
        payload={
            "findings": [
                {"finding_id": "F-1", "title": "verified blocker", "blocking": True}
            ]
        },
    )
    recorder.apply(
        EventType.AUTHORIZE_REPAIR,
        node_id="node-1",
        payload={
            "finding_ids": ["F-1"],
            "authorization_receipt_id": "repair-auth-1",
            "authorization_receipt_digest": "a" * 64,
        },
    )
    recorder.apply(EventType.START_REPAIR, node_id="node-1")
    recorder.apply(EventType.REPAIR_COMPLETED, node_id="node-1")
    return recorder


class CampaignFormalConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.formal = FORMAL_PATH.read_text(encoding="utf-8")
        cls.config = CONFIG_PATH.read_text(encoding="utf-8")

    def test_formal_state_universes_exactly_match_runtime_enums(self) -> None:
        self.assertEqual(
            {state.value for state in CampaignState},
            string_set(self.formal, "CampaignStates"),
        )
        self.assertEqual(
            {state.value for state in NodeState},
            string_set(self.formal, "NodeStates"),
        )

    def test_real_reducer_executes_every_declared_core_transition(self) -> None:
        observed: set[tuple[str, str, str]] = set()

        clean = candidate_recorder()
        clean.apply(
            EventType.START_REVIEW,
            node_id="node-1",
            payload={"review_cohort": ["reviewer-1", "reviewer-2"]},
        )
        clean.apply(
            EventType.FREEZE_FINDINGS,
            node_id="node-1",
            payload={"findings": []},
        )
        clean.apply(EventType.MARK_READY_TO_PUBLISH, node_id="node-1")
        for index, effect_kind in enumerate(
            ("PUSH", "CREATE_PULL_REQUEST", "MERGE"), start=1
        ):
            operation_id = f"publication-{index}"
            clean.apply(
                EventType.START_PUBLISH,
                node_id="node-1",
                payload={
                    "effect_kind": effect_kind,
                    "operation_id": operation_id,
                    "effect_payload": {"candidate_head": "d" * 40},
                },
            )
            clean.apply(
                EventType.PUBLISH_CONFIRMED,
                node_id="node-1",
                payload={
                    "candidate_head": "d" * 40,
                    "operation_id": operation_id,
                },
            )
        observed.update(clean.edges)

        corrected_events, corrected_snapshot = running_snapshot()
        corrected = ReducerRecorder(corrected_events, corrected_snapshot)
        corrected.apply(
            EventType.ADMIT_NODE,
            node_id="node-1",
            payload={"start_head": corrected_snapshot.spec.base_sha},
        )
        corrected.apply(EventType.START_IMPLEMENTATION, node_id="node-1")
        corrected.apply(EventType.IMPLEMENTATION_COMPLETED, node_id="node-1")
        corrected.apply(EventType.REQUEST_VALIDATION_CORRECTION, node_id="node-1")
        observed.update(corrected.edges)

        repaired = blocking_repair_recorder()
        repaired.apply(
            EventType.REVALIDATION_PASSED,
            node_id="node-1",
            payload={
                "candidate_head": "1" * 40,
                "candidate_tree": "2" * 40,
                "candidate_diff_digest": "3" * 64,
                "candidate_node_diff_digest": "4" * 64,
            },
        )
        repaired.apply(
            EventType.COMPLETE_CLOSURE,
            node_id="node-1",
            payload={"resolved_finding_ids": ["F-1"], "findings": []},
        )
        observed.update(repaired.edges)

        closure_failed = blocking_repair_recorder()
        closure_failed.apply(
            EventType.REVALIDATION_PASSED,
            node_id="node-1",
            payload={
                "candidate_head": "1" * 40,
                "candidate_tree": "2" * 40,
                "candidate_diff_digest": "3" * 64,
                "candidate_node_diff_digest": "4" * 64,
            },
        )
        closure_failed.apply(
            EventType.COMPLETE_CLOSURE,
            node_id="node-1",
            payload={
                "resolved_finding_ids": ["F-1"],
                "findings": [
                    {
                        "finding_id": "NEW-1",
                        "title": "repair regression",
                        "blocking": False,
                    }
                ],
            },
        )
        observed.update(closure_failed.edges)

        validation_events, validation_snapshot = running_snapshot()
        validation_failed = ReducerRecorder(validation_events, validation_snapshot)
        validation_failed.apply(
            EventType.ADMIT_NODE,
            node_id="node-1",
            payload={"start_head": validation_snapshot.spec.base_sha},
        )
        validation_failed.apply(EventType.START_IMPLEMENTATION, node_id="node-1")
        validation_failed.apply(EventType.IMPLEMENTATION_COMPLETED, node_id="node-1")
        validation_failed.apply(
            EventType.VALIDATION_FAILED,
            node_id="node-1",
            payload={"reason": "failed"},
        )
        observed.update(validation_failed.edges)

        revalidation_failed = blocking_repair_recorder()
        revalidation_failed.apply(
            EventType.REVALIDATION_FAILED,
            node_id="node-1",
            payload={"reason": "failed"},
        )
        observed.update(revalidation_failed.edges)

        publish_failed = candidate_recorder()
        publish_failed.apply(
            EventType.START_REVIEW,
            node_id="node-1",
            payload={"review_cohort": ["reviewer-1", "reviewer-2"]},
        )
        publish_failed.apply(
            EventType.FREEZE_FINDINGS,
            node_id="node-1",
            payload={"findings": []},
        )
        publish_failed.apply(EventType.MARK_READY_TO_PUBLISH, node_id="node-1")
        publish_failed.apply(
            EventType.START_PUBLISH,
            node_id="node-1",
            payload={
                "effect_kind": "PUSH",
                "operation_id": "failed-push",
                "effect_payload": {"candidate_head": "d" * 40},
            },
        )
        publish_failed.apply(
            EventType.PUBLISH_FAILED,
            node_id="node-1",
            payload={"reason": "failed"},
        )
        observed.update(publish_failed.edges)

        self.assertEqual(transition_contract(self.formal), observed)

    def test_formal_clean_and_repair_routes_preserve_required_intermediate_states(self) -> None:
        edges = transition_contract(self.formal)
        self.assertIn(
            ("FREEZE_FINDINGS", "CHECKS_AND_REVIEW", "FINDINGS_FROZEN"), edges
        )
        self.assertNotIn(
            ("FREEZE_FINDINGS", "CHECKS_AND_REVIEW", "READY_TO_PUBLISH"), edges
        )
        self.assertIn(
            ("AUTHORIZE_REPAIR", "FINDINGS_FROZEN", "REPAIR_AUTHORIZED"), edges
        )
        self.assertNotIn(
            ("START_REPAIR", "FINDINGS_FROZEN", "REPAIRING"), edges
        )
        self.assertIn(
            ("REVALIDATION_PASSED", "REVALIDATING", "CLOSURE"), edges
        )

    def test_every_leaf_transition_preserves_the_immutable_contract(self) -> None:
        leaf_actions = {
            "Approve",
            "Start",
            "WaitExternal",
            "WaitHuman",
            "Resume",
            "AdvanceAuthority",
            "Admit",
            "Implement",
            "ImplementationDone",
            "ValidationCorrection",
            "ValidationPass",
            "Review",
            "FreezeFindings",
            "MarkReady",
            "AuthorizeRepair",
            "Repair",
            "RepairDone",
            "RevalidationPass",
            "ClosurePass",
            "ClosureFail",
            "StartPublish",
            "PublishConfirmed",
            "FailNode",
            "Cancel",
        }
        for action in sorted(leaf_actions):
            with self.subTest(action=action):
                self.assertIn("KeepContract", definition_body(self.formal, action))

        next_body = definition_body(self.formal, "Next")
        self.assertNotRegex(next_body, r"\b(?:Successor|Replenish|Reset)\b")
        self.assertIn("NoSuccessorGeneration == campaignGeneration = 1", self.formal)

    def test_budgeted_actions_strictly_spend_and_waiting_actions_do_not(self) -> None:
        for action in ("Implement", "Review", "Repair", "RevalidationPass", "StartPublish"):
            with self.subTest(action=action):
                body = definition_body(self.formal, action)
                self.assertIn("SpendBudget", body)
                self.assertNotIn("KeepBudget", body)

        for action in ("WaitExternal", "WaitHuman", "Resume", "AdvanceAuthority"):
            with self.subTest(action=action):
                body = definition_body(self.formal, action)
                self.assertIn("KeepBudget", body)
                self.assertNotIn("SpendBudget", body)

        self.assertIn("rankRemaining' = rankRemaining - 1", definition_body(self.formal, "SpendBudget"))
        self.assertIn("budgetReceipts' = budgetReceipts + 1", definition_body(self.formal, "SpendBudget"))

    def test_config_checks_every_required_invariant_and_temporal_property(self) -> None:
        invariant_section, property_section = self.config.split("PROPERTIES", maxsplit=1)
        configured_invariants = {
            line.strip()
            for line in invariant_section.split("INVARIANTS", maxsplit=1)[1].splitlines()
            if line.strip()
        }
        configured_properties = {
            line.strip() for line in property_section.splitlines() if line.strip()
        }
        self.assertEqual(
            {
                "TypeInvariant",
                "GraphIsFiniteDAG",
                "GraphImmutable",
                "ContractImmutable",
                "NoSuccessorGeneration",
                "BudgetConservation",
                "LifecycleLimits",
                "CampaignTerminalShape",
                "CancelledIsTerminal",
                "TerminalCampaignIsTerminal",
                "WaitingYields",
            },
            configured_invariants,
        )
        self.assertEqual(
            {
                "RankNeverIncreases",
                "RevisionAlwaysAdvances",
                "AutonomousBudgetIsOneWay",
                "LifecycleUseIsOneWay",
                "ApprovedContractNeverChanges",
                "WaitingConsumesNoAutonomousBudget",
            },
            configured_properties,
        )


if __name__ == "__main__":
    unittest.main()
