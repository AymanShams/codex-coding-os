"""Exercise manual metadata and independently bound campaign metadata."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_pr_body", ROOT / "scripts/check-pr-body.py")
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

HEAD = "a" * 40


class PullRequestMetadataTests(unittest.TestCase):
    def assert_failure(self, body: str, fragment: str, **kwargs: str) -> None:
        failures = CHECKER.validate_body(body, **kwargs)
        self.assertTrue(any(fragment in failure for failure in failures), failures)

    def test_manual_request_passes_without_invented_campaign_identity(self) -> None:
        body = CHECKER.manual_fixture_body(HEAD)
        self.assertEqual([], CHECKER.validate_body(body, expected_current_head=HEAD))
        self.assertNotIn("Campaign ID:", body)

    def test_campaign_evidence_passes_with_independent_identity(self) -> None:
        self.assertEqual([], CHECKER.validate_body(
            CHECKER.fixture_body(HEAD),
            expected_work_mode="CAMPAIGN",
            expected_campaign_id="campaign-1",
            expected_specification_digest="b" * 64,
            expected_current_head=HEAD,
            expected_base_sha=HEAD,
        ))

    def test_campaign_can_describe_authorized_file_replacement_without_publishing_it(self):
        body = CHECKER.fixture_body(HEAD).replace(
            "- Allowed effects: PUSH, CREATE_PULL_REQUEST, MERGE",
            "- Allowed effects: PUSH, CREATE_PULL_REQUEST, MERGE, EXACT_FILE_REPLACE",
        )
        self.assertIn("EXACT_FILE_REPLACE", body)
        self.assertEqual([], CHECKER.validate_body(body, expected_work_mode="CAMPAIGN"))

    def test_manual_label_cannot_bypass_independent_campaign_binding(self) -> None:
        body = CHECKER.manual_fixture_body(HEAD)
        for independent in (
            {"expected_work_mode": "CAMPAIGN"},
            {"expected_campaign_id": "campaign-1"},
            {"expected_specification_digest": "b" * 64},
        ):
            with self.subTest(binding=independent):
                self.assertTrue(CHECKER.validate_body(body, **independent))

    def test_relabeling_campaign_body_is_rejected_even_without_external_binding(self) -> None:
        body = CHECKER.fixture_body(HEAD).replace("Work mode: CAMPAIGN", "Work mode: MANUAL")
        self.assert_failure(body, "MANUAL metadata must omit campaign field")

    def test_campaign_id_and_digest_cannot_be_swapped(self) -> None:
        for binding in (
            {"expected_campaign_id": "campaign-2"},
            {"expected_specification_digest": "c" * 64},
        ):
            with self.subTest(binding=binding):
                self.assert_failure(CHECKER.fixture_body(HEAD), "admitted campaign", **binding)

    def test_manual_and_campaign_require_exact_current_head(self) -> None:
        for fixture in (CHECKER.fixture_body, CHECKER.manual_fixture_body):
            with self.subTest(fixture=fixture.__name__):
                self.assert_failure(fixture(HEAD), "live pull request head", expected_current_head="b" * 40)
                self.assert_failure(fixture("short"), "full 40-character Git SHA")
                self.assert_failure(fixture(HEAD), "supplied base SHA", expected_base_sha="c" * 40)

    def test_every_campaign_field_remains_mandatory(self) -> None:
        body = CHECKER.fixture_body(HEAD)
        for label in CHECKER.CAMPAIGN_FIELDS:
            with self.subTest(field=label):
                trimmed = "\n".join(line for line in body.splitlines() if not line.startswith(f"- {label}:"))
                self.assert_failure(trimmed, f"required field: {label}")

    def test_every_manual_authority_and_scope_field_is_required(self) -> None:
        body = CHECKER.manual_fixture_body(HEAD)
        for label in CHECKER.COMMON_FIELDS + CHECKER.MANUAL_FIELDS:
            with self.subTest(field=label):
                trimmed = "\n".join(line for line in body.splitlines() if not line.startswith(f"- {label}:"))
                self.assert_failure(trimmed, f"required field: {label}")

    def test_invalid_mode_and_duplicate_fields_fail(self) -> None:
        body = CHECKER.manual_fixture_body(HEAD)
        self.assert_failure(body.replace("Work mode: MANUAL", "Work mode: automatic"), "exactly MANUAL or CAMPAIGN")
        self.assert_failure(body + "\n- Work mode: CAMPAIGN\n", "duplicate field: Work mode")
        self.assert_failure(body, "Expected work mode is unsupported", expected_work_mode="manual")

    def test_blank_manual_validation_and_absent_table_fail(self) -> None:
        body = CHECKER.manual_fixture_body(HEAD)
        self.assert_failure(body.replace("local command output", ""), "Validation row must record")
        self.assert_failure(body.replace("| metadata tests |", "metadata tests"), "at least one relevant check")

    def test_campaign_still_requires_all_six_validation_rows(self) -> None:
        body = CHECKER.fixture_body(HEAD)
        for check in CHECKER.VALIDATION_CHECKS:
            with self.subTest(check=check):
                trimmed = "\n".join(line for line in body.splitlines() if not line.startswith(f"| {check} |"))
                self.assert_failure(trimmed, f"validation row: {check}")

    def test_effects_and_operation_identifiers_are_checked(self) -> None:
        self.assert_failure(CHECKER.manual_fixture_body(HEAD).replace("PUSH, CREATE_PULL_REQUEST", "RELEASE"), "unsupported effect")
        self.assert_failure(CHECKER.fixture_body(HEAD).replace("push-1, pr-1, merge-1", "not an identifier"), "stable operation identifiers")

    def test_github_event_uses_live_head_not_the_body_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.json"
            path.write_text(json.dumps({"pull_request": {
                "draft": False,
                "head": {"sha": "c" * 40},
                "body": CHECKER.manual_fixture_body(HEAD),
            }}), encoding="utf-8")
            body, current_head, skipped = CHECKER.read_github_event_body(path, enforce_draft=False)
            self.assertIsNone(skipped)
            self.assert_failure(body, "live pull request head", expected_current_head=current_head)

    def test_template_and_self_test_are_valid(self) -> None:
        CHECKER.run_self_test(ROOT / ".github/pull_request_template.md")

    def test_bound_cli_call_without_body_fails_instead_of_skipping(self) -> None:
        with mock.patch.dict(CHECKER.os.environ, {}, clear=True):
            with mock.patch("sys.stderr"):
                self.assertEqual(1, CHECKER.main(["--expected-work-mode", "CAMPAIGN"]))


if __name__ == "__main__":
    unittest.main()
