"""Check paired evaluation input and its existing documentation boundary.

These tests execute the documentation validator, not a language model. Agent
behavior must be reported separately after the paired evaluation is performed.
"""

from __future__ import annotations

import json
import unittest

from tests.test_project_artifact_instances import ROOT, VALIDATOR, fresh_manifest


class FounderEntryFixtureTests(unittest.TestCase):
    def test_each_pair_changes_the_business_consequence(self) -> None:
        fixture = json.loads((ROOT / "tests/fixtures/founder-entry-pairs.json").read_text(encoding="utf-8"))
        ids = []
        for pair in fixture["pairs"]:
            self.assertTrue(pair["shared_sources"])
            self.assertEqual(2, len(pair["cases"]))
            self.assertEqual({0, 1}, {case["expected"]["founder_questions"] for case in pair["cases"]})
            for case in pair["cases"]:
                ids.append(case["id"])
                self.assertTrue(case["request"])
                self.assertTrue(case["expected"]["action"])
                self.assertTrue(case["expected"]["must_preserve"])
                self.assertTrue(case["expected"]["must_avoid"])
        self.assertEqual(len(ids), len(set(ids)))

    def documentation_errors(self, *, unresolved_business_choice: bool, source_approval: bool) -> list[str]:
        manifest = fresh_manifest()
        manifest["schema_version"] = "1.0"
        manifest.pop("artifact_instances")
        for phase in VALIDATOR.PHASES[:3]:
            manifest["phases"][phase]["status"] = "completed"
        manifest["phases"]["3_controlled_docs"]["status"] = "in_progress"
        manifest["approvals"]["material_decisions"] = source_approval
        manifest["open_material_decisions"] = ["Approve new subscription spending"] if unresolved_business_choice else []
        return VALIDATOR.validate(manifest, pack_manifest=None, project_root=ROOT)

    def test_reused_source_approval_allows_document_drafting(self) -> None:
        self.assertEqual([], self.documentation_errors(unresolved_business_choice=False, source_approval=True))

    def test_unresolved_spending_still_blocks_dependent_documents(self) -> None:
        errors = self.documentation_errors(unresolved_business_choice=True, source_approval=True)
        self.assertTrue(any("material decisions remain open" in error for error in errors), errors)

    def test_derived_technical_choice_does_not_fabricate_document_approval(self) -> None:
        errors = self.documentation_errors(unresolved_business_choice=False, source_approval=False)
        self.assertTrue(any("without material_decisions approval" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
