"""Product meaning and saved validation inputs remain binding through acceptance."""

import os
import hashlib
from unittest.mock import patch

from scripts.agent.campaign_engine.admission import SourceDriftError, verify_acceptance_sources
from scripts.agent.campaign_engine.model import CampaignSpec, CampaignState
from tests import test_campaign_bounded_continuation as bounded
from tests.test_campaign_supervisor import SupervisorFixture, git


class AcceptanceSourcesTests(SupervisorFixture):
    prepare = bounded.BoundedContinuationTests.prepare

    def prepare_selected_dependencies(self):
        files = {"dependencies.lock": "selected-dependency==1\n",
                 ".gitignore": ".runtime/\n",
                 ".runtime/selected_dependency.py": "RESULT = 'accepted'\n",
                 ".runtime/unrelated_dependency.py": "RESULT = 'unrelated'\n"}
        for name, contents in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(contents, encoding="utf-8")
        git(self.repo, "add", "dependencies.lock", ".gitignore")
        git(self.repo, "commit", "-q", "-m", "declare selected dependency inputs")
        original_create = self.create_approved

        def bind_dependencies(spec):
            raw = spec.to_dict()
            raw.pop("specification_digest")
            raw["nodes"][0]["acceptance_scenarios"][0]["sources"].extend(
                {"path": name, "requirement_id": name,
                 "sha256": hashlib.sha256((self.repo / name).read_bytes()).hexdigest()}
                for name in ("dependencies.lock", ".runtime/selected_dependency.py")
            )
            return original_create(CampaignSpec.from_dict(raw))

        with patch.object(self, "create_approved", side_effect=bind_dependencies):
            supervisor, host = self.prepare(candidate_source="accepted\n", test_body=(
                "import sys\n"
                "sys.path.insert(0, str(Path('.runtime').resolve()))\n"
                "from selected_dependency import RESULT\n"
                "self.assertEqual(RESULT, 'accepted')\n"
                "self.assertEqual(Path('src/app.txt').read_text().strip(), RESULT)"
            ))
        self.assertEqual(supervisor.step("campaign").action, "CANDIDATE_FROZEN")
        return supervisor, host

    def assert_selected_dependency_rejects_reuse(self, name, contents):
        supervisor, host = self.prepare_selected_dependencies()
        before = self.store.get_snapshot("campaign")
        (self.repo / name).write_text(contents, encoding="utf-8")
        if name.startswith(".runtime/"):
            self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before.node("node-1").candidate_head)
            self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        with self.assertRaisesRegex(SourceDriftError, name.replace(".", r"\.")):
            supervisor.begin_review("campaign", "node-1")
        self.assertEqual(self.store.get_snapshot("campaign"), before)
        self.assertEqual(len(host.turns), 1)
        self.assertEqual(self.store.list_outbox(campaign_id="campaign"), [])

    def test_selected_lockfile_change_invalidates_passing_acceptance_before_new_work(self):
        self.assert_selected_dependency_rejects_reuse("dependencies.lock", "selected-dependency==2\n")

    def test_selected_imported_module_change_invalidates_passing_acceptance_before_new_work(self):
        self.assert_selected_dependency_rejects_reuse(".runtime/selected_dependency.py", "RESULT = 'wrong'\n")

    def test_unselected_dependency_does_not_invalidate_selected_acceptance_inputs(self):
        supervisor, _ = self.prepare_selected_dependencies()
        before = self.store.get_snapshot("campaign")
        sources = verify_acceptance_sources(before.spec.to_dict(), node_id="node-1")
        (self.repo / ".runtime/unrelated_dependency.py").write_text("RESULT = 'other'\n", encoding="utf-8")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(verify_acceptance_sources(before.spec.to_dict(), node_id="node-1"), sources)
        supervisor._verify_sources(before, "node-1")
        self.assertEqual(self.store.get_snapshot("campaign"), before)

    def test_conflicting_declared_identity_requires_resolution_before_acceptance(self):
        self.prepare()
        raw = self.store.get_snapshot("campaign").spec.to_dict()
        sources = raw["nodes"][0]["acceptance_scenarios"][0]["sources"]
        sources[1]["requirement_id"] = sources[0]["requirement_id"]
        with self.assertRaisesRegex(SourceDriftError, "conflicting source identities"):
            verify_acceptance_sources(raw)

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
