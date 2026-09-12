"""Product meaning and saved validation inputs remain binding through acceptance."""

import os
from unittest.mock import patch

from scripts.agent.campaign_engine.admission import SourceDriftError, verify_acceptance_sources
from scripts.agent.campaign_engine.model import CampaignState
from tests import test_campaign_bounded_continuation as bounded
from tests.test_campaign_supervisor import SupervisorFixture, git


class AcceptanceSourcesTests(SupervisorFixture):
    prepare = bounded.BoundedContinuationTests.prepare

    def test_weakened_test_cannot_accept_unchanged_wrong_product(self):
        supervisor, host = self.prepare()
        (self.repo / "test_acceptance.py").write_text(
            "import unittest\nclass Acceptance(unittest.TestCase):\n    def test_contract(self): pass\n",
            encoding="utf-8",
        )
        git(self.repo, "add", "test_acceptance.py")
        git(self.repo, "commit", "-q", "-m", "weaken implementation test")
        result = supervisor.step("campaign")
        self.assertEqual(result.action, "ACCEPTANCE_SOURCE_CHANGED")
        self.assertEqual(self.store.get_snapshot("campaign").state, CampaignState.FAILED)
        self.assertEqual((self.repo / "src/app.txt").read_text().strip(), "incorrect")
        self.assertEqual(len(host.turns), 1)
        self.assertEqual(self.store.list_outbox(campaign_id="campaign"), [])

    def test_unrelated_source_keeps_declared_acceptance_identity(self):
        self.prepare()
        spec = self.store.get_snapshot("campaign").spec.to_dict()
        before = verify_acceptance_sources(spec, node_id="node-1")
        (self.repo / "unrelated-notes.md").write_text("A separate future idea.\n", encoding="utf-8")
        self.assertEqual(verify_acceptance_sources(spec, node_id="node-1"), before)

    def test_missing_requirement_is_drift_not_silent_empty_acceptance(self):
        supervisor, _ = self.prepare()
        (self.repo / "requirements.md").unlink()
        result = supervisor.step("campaign")
        self.assertEqual(result.action, "ACCEPTANCE_SOURCE_CHANGED")
        self.assertIn("requirements.md", result.details["reason"])

    def test_changed_requirement_after_validation_rejects_review_without_new_dispatch(self):
        supervisor, host = self.prepare()
        self.commit_text("accepted\n", "fulfill requested behavior")
        self.assertEqual(supervisor.step("campaign").action, "CANDIDATE_FROZEN")
        before = self.store.get_snapshot("campaign")
        (self.repo / "requirements.md").write_text("A materially different result.\n", encoding="utf-8")
        with self.assertRaises(SourceDriftError):
            supervisor.begin_review("campaign", "node-1")
        self.assertEqual(self.store.get_snapshot("campaign").revision, before.revision)
        self.assertEqual(len(host.turns), 1)

    def test_changed_validation_environment_rejects_evidence_reuse_without_spending(self):
        supervisor, _ = self.prepare()
        self.commit_text("accepted\n", "fulfill requested behavior")
        self.assertEqual(supervisor.step("campaign").action, "CANDIDATE_FROZEN")
        before = self.store.get_snapshot("campaign")
        with patch.dict(os.environ, {"PATH": os.environ["PATH"] + os.pathsep + "changed-tool-path"}):
            with self.assertRaisesRegex(SourceDriftError, "command inputs changed"):
                supervisor.begin_review("campaign", "node-1")
        self.assertEqual(self.store.get_snapshot("campaign").budget_receipts, before.budget_receipts)
        self.assertEqual(self.store.get_snapshot("campaign").revision, before.revision)
