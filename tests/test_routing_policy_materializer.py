from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO_ROOT
    / "capability-routing"
    / "deployment"
    / "materialize_routing_policy.py"
)
SPEC = importlib.util.spec_from_file_location("materialize_routing_policy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
materializer = importlib.util.module_from_spec(SPEC)
import sys

sys.modules[SPEC.name] = materializer
SPEC.loader.exec_module(materializer)


BASE_POLICY = REPO_ROOT / "capability-routing" / "routing-policy.yaml"
CURRENT_DELTA_OVERLAY = (
    REPO_ROOT
    / "capability-routing"
    / "routing-policy.deployment-overlay.example.json"
)
REVIEWED_CAPABILITY_CANDIDATE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "routing-policy-reviewed-capability-candidate.json"
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def policy_capability_ids(policy: dict[str, object]) -> set[str]:
    values: set[str] = set(policy["capability_aliases"])
    for control in policy["live_dependency_controls"].values():
        values.update(control["manifest_any"])
    for section in ("local_execution_rules", "worker_rules"):
        for rule in policy[section]:
            values.update(rule["requires_any_capabilities"])
    for rule in policy["rules"]:
        values.add(rule["primary"])
        values.update(rule["supports"])
        values.update(
            value.removeprefix("active:")
            for value in rule["requires"]
            if not value.casefold().startswith("prompt:")
        )
        values.update(
            value.removeprefix("capability:")
            for value in rule["forbids"]
            if not value.casefold().startswith("prompt:")
        )
        fallback = rule.get("dependency_fallback")
        if fallback:
            values.add(fallback["selected_capability"])
            values.update(fallback["supports"])
            values.update(fallback.get("equivalent_capabilities", []))
    for override in policy.get("explicit_overrides", []):
        values.add(override["target"])
        for field in ("requires_primary", "winner"):
            if override.get(field):
                values.add(override[field])
    return values


class MaterializerEnvironment:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.base = root / "policy-base" / "routing-policy.yaml"
        self.overlay = root / "routing-policy.deployment-overlay.json"
        self.capability_manifest = root / "active-capabilities.json"
        self.target = root / "live" / "routing-policy.yaml"
        self.base.parent.mkdir(parents=True)
        self.target.parent.mkdir(parents=True)
        self.base.write_bytes(BASE_POLICY.read_bytes())
        self.overlay.write_bytes(CURRENT_DELTA_OVERLAY.read_bytes())
        (
            root / "routing-policy.schema.json"
        ).write_bytes(
            (REPO_ROOT / "capability-routing" / "routing-policy.schema.json").read_bytes()
        )
        (
            root / "routing-policy-overlay.schema.json"
        ).write_bytes(
            (
                REPO_ROOT
                / "capability-routing"
                / "routing-policy-overlay.schema.json"
            ).read_bytes()
        )
        base = json.loads(self.base.read_text(encoding="utf-8"))
        write_json(
            self.capability_manifest,
            {
                "schema_version": "synthetic-capability-validation-v1",
                "entries": [
                    {"id": identifier, "state": "active-live"}
                    for identifier in sorted(
                        policy_capability_ids(base)
                        | {"skill:healpath-knowledge-layer"}
                    )
                ],
                "suppressed_capabilities": [],
            },
        )

    def render(self) -> dict[str, object]:
        return materializer.materialize_policy(
            self.base,
            self.overlay,
            capability_manifest_path=self.capability_manifest,
        )

    def apply(
        self,
        transaction_id: str,
        *,
        expected_target: str | None = None,
        **overrides: object,
    ) -> dict[str, object]:
        rendered = self.render()
        if expected_target is None:
            expected_target = (
                materializer._sha256_file(self.target)
                if self.target.exists()
                else materializer.MISSING
            )
        values = {
            "base_path": self.base,
            "overlay_path": self.overlay,
            "target_policy": self.target,
            "transaction_id": transaction_id,
            "expected_target_sha256": expected_target,
            "expected_materialized_sha256": rendered["facts"]["materialized_sha256"],
            "expected_materialization_digest": rendered["facts"]["materialization_digest"],
            "capability_manifest_path": self.capability_manifest,
            **overrides,
        }
        return materializer.apply_materialized_policy(
            materializer.PolicyApplyOptions(**values)
        )


class RoutingPolicyOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = MaterializerEnvironment(Path(self.temporary.name))

    def test_overlay_schema_and_current_delta_example_are_valid(self) -> None:
        schema = json.loads(
            (
                REPO_ROOT
                / "capability-routing"
                / "routing-policy-overlay.schema.json"
            ).read_text(encoding="utf-8")
        )
        overlay = json.loads(self.env.overlay.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(overlay)

    def test_missing_jsonschema_fails_closed(self) -> None:
        with mock.patch.dict(sys.modules, {"jsonschema": None}):
            with self.assertRaisesRegex(
                materializer.OverlayError,
                "jsonschema is required",
            ):
                self.env.render()

    def test_readme_commands_include_every_required_materializer_input(self) -> None:
        readme = (
            REPO_ROOT / "capability-routing" / "README.md"
        ).read_text(encoding="utf-8")
        for option in (
            "--capability-manifest",
            "--policy-schema",
            "--overlay-schema",
        ):
            self.assertGreaterEqual(readme.count(option), 2, option)
        for option in (
            "--expected-target-sha256",
            "--expected-materialized-sha256",
            "--expected-materialization-digest",
        ):
            self.assertIn(option, readme)

    def test_current_live_delta_shape_is_represented_exactly(self) -> None:
        base = json.loads(self.env.base.read_text(encoding="utf-8"))
        result = self.env.render()["policy"]
        self.assertEqual(
            result["decision_snapshot"],
            "universal-router-authority-v1.3.0-2026-08-15-reviewed",
        )
        unchanged = copy.deepcopy(result)
        unchanged["decision_snapshot"] = base["decision_snapshot"]

        base_rule = next(item for item in base["rules"] if item["id"] == "deep-critique")
        result_rule = next(item for item in unchanged["rules"] if item["id"] == "deep-critique")
        phrase = "review the healpath code of conduct"
        self.assertNotIn(phrase, base_rule["match_any"])
        self.assertEqual(
            result_rule["match_any"].index(phrase) + 1,
            result_rule["match_any"].index("review the code of conduct"),
        )
        result_rule["match_any"].remove(phrase)

        inserted = next(
            item
            for item in unchanged["explicit_overrides"]
            if item["id"] == "healpath-skill-project-scope"
        )
        self.assertEqual(inserted["target"], "skill:healpath-knowledge-layer")
        insert_index = unchanged["explicit_overrides"].index(inserted)
        self.assertEqual(
            unchanged["explicit_overrides"][insert_index + 1]["id"],
            "document-skills-migration-fallback",
        )
        unchanged["explicit_overrides"].remove(inserted)
        self.assertEqual(unchanged, base)

    def test_repository_base_and_overlay_match_reviewed_identity_candidate(self) -> None:
        candidate = json.loads(
            REVIEWED_CAPABILITY_CANDIDATE.read_text(encoding="utf-8")
        )
        active = {row["id"] for row in candidate["entries"]}
        suppressed = {
            row["id"] for row in candidate["suppressed_capabilities"]
        }
        self.assertTrue(
            {
                "skill:creative-production:intake",
                "skill:creative-production:produce",
                "skill:cloudflare:web-perf",
            }.issubset(active)
        )
        self.assertIn("tool-family:app:atlassian", suppressed)
        self.assertNotIn("tool-family:app:atlassian", active)

        rendered = materializer.materialize_policy(
            self.env.base,
            self.env.overlay,
            capability_manifest_path=REVIEWED_CAPABILITY_CANDIDATE,
        )
        declared = rendered["validation_context"]["declared_capabilities"]
        self.assertTrue(policy_capability_ids(rendered["policy"]).issubset(declared))
        self.assertIn(
            "policy_validator_sha256",
            rendered["facts"],
        )

    def test_new_source_rules_flow_through_without_overlay_reconstruction(self) -> None:
        base = json.loads(self.env.base.read_text(encoding="utf-8"))
        deep_rule = next(item for item in base["rules"] if item["id"] == "deep-critique")
        deep_rule["scenario"] = "Updated source-owned critique scenario"
        base["rules"].append(
            {
                "id": "new-source-rule",
                "scenario": "A later repository rule",
                "match_any": ["new source rule"],
                "match_all": [],
                "primary": "skill:deep-critic",
                "supports": [],
                "requires": [],
                "forbids": [],
                "authority_limit": "Synthetic source update.",
                "evidence_ids": [],
            }
        )
        write_json(self.env.base, base)
        result = self.env.render()["policy"]
        self.assertEqual(
            next(item for item in result["rules"] if item["id"] == "deep-critique")[
                "scenario"
            ],
            "Updated source-owned critique scenario",
        )
        self.assertTrue(any(item["id"] == "new-source-rule" for item in result["rules"]))
        self.assertIn(
            "review the healpath code of conduct",
            next(item for item in result["rules"] if item["id"] == "deep-critique")[
                "match_any"
            ],
        )

    def test_duplicate_overlay_edit_fails_closed(self) -> None:
        overlay = json.loads(self.env.overlay.read_text(encoding="utf-8"))
        overlay["operations"].append(copy.deepcopy(overlay["operations"][1]))
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(materializer.OverlayError, "duplicate overlay edit"):
            self.env.render()

    def test_duplicate_json_members_fail_closed(self) -> None:
        self.env.overlay.write_text(
            '{"schema_version":"catalogue-routing-policy-overlay-v1",'
            '"overlay_id":"one","overlay_id":"two","operations":[]}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(materializer.OverlayError, "duplicate JSON object member"):
            self.env.render()

    def test_overlay_value_already_in_base_fails_closed(self) -> None:
        base = json.loads(self.env.base.read_text(encoding="utf-8"))
        deep_rule = next(item for item in base["rules"] if item["id"] == "deep-critique")
        deep_rule["match_any"].insert(
            deep_rule["match_any"].index("review the code of conduct"),
            "review the healpath code of conduct",
        )
        write_json(self.env.base, base)
        with self.assertRaisesRegex(materializer.OverlayError, "already exists"):
            self.env.render()

    def test_ambiguous_selector_and_invalid_path_fail_closed(self) -> None:
        base = json.loads(self.env.base.read_text(encoding="utf-8"))
        deep_rule = next(item for item in base["rules"] if item["id"] == "deep-critique")
        duplicate_scenario = copy.deepcopy(deep_rule)
        duplicate_scenario["id"] = "deep-critique-same-scenario"
        base["rules"].append(duplicate_scenario)
        write_json(self.env.base, base)
        overlay = json.loads(self.env.overlay.read_text(encoding="utf-8"))
        overlay["operations"][1]["select"] = {
            "key": "scenario",
            "equals": deep_rule["scenario"],
        }
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(materializer.OverlayError, "resolve exactly once"):
            self.env.render()

        self.env.base.write_bytes(BASE_POLICY.read_bytes())
        overlay = json.loads(self.env.overlay.read_text(encoding="utf-8"))
        overlay["operations"][0]["path"] = "/missing"
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(materializer.OverlayError, "does not resolve"):
            self.env.render()

    def test_schema_invalid_base_and_materialized_policy_fail_closed(self) -> None:
        base = json.loads(self.env.base.read_text(encoding="utf-8"))
        base["max_supports"] = {"invalid": True}
        write_json(self.env.base, base)
        with self.assertRaisesRegex(materializer.OverlayError, "base routing policy failed schema"):
            self.env.render()

        self.env.base.write_bytes(BASE_POLICY.read_bytes())
        overlay = {
            "schema_version": materializer.OVERLAY_SCHEMA,
            "overlay_id": "invalid-final-policy-type",
            "operations": [
                {"op": "set", "path": "/max_supports", "value": {"invalid": True}}
            ],
        }
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(
            materializer.OverlayError, "materialized routing policy failed schema"
        ):
            self.env.render()

    def test_unknown_execution_profile_reference_fails_closed(self) -> None:
        overlay = {
            "schema_version": materializer.OVERLAY_SCHEMA,
            "overlay_id": "missing-execution-profile",
            "operations": [
                {
                    "op": "set",
                    "path": "/default_execution_profile",
                    "value": "does-not-exist",
                }
            ],
        }
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(materializer.OverlayError, "not declared"):
            self.env.render()

    def test_unknown_capability_and_fallback_references_fail_closed(self) -> None:
        overlay = {
            "schema_version": materializer.OVERLAY_SCHEMA,
            "overlay_id": "missing-capability-alias-target",
            "operations": [
                {
                    "op": "set",
                    "path": "/capability_aliases",
                    "value": {"skill:not-in-manifest": ["Missing Capability"]},
                }
            ],
        }
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(materializer.OverlayError, "not a declared"):
            self.env.render()

        base = json.loads(self.env.base.read_text(encoding="utf-8"))
        fallback_rule = copy.deepcopy(
            next(rule for rule in base["rules"] if rule.get("dependency_fallback"))
        )
        fallback_rule["id"] = "broken-fallback-capability"
        fallback_rule["dependency_fallback"]["selected_capability"] = (
            "skill:not-in-manifest"
        )
        overlay = {
            "schema_version": materializer.OVERLAY_SCHEMA,
            "overlay_id": "missing-fallback-capability",
            "operations": [
                {
                    "op": "insert_object_unique",
                    "path": "/rules",
                    "unique_key": "id",
                    "value": fallback_rule,
                }
            ],
        }
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(materializer.OverlayError, "fallback selected_capability"):
            self.env.render()

    def test_suppressed_capability_remains_a_valid_declared_identity(self) -> None:
        manifest = json.loads(
            self.env.capability_manifest.read_text(encoding="utf-8")
        )
        identifier = "skill:create-prd"
        manifest["entries"] = [
            row for row in manifest["entries"] if row["id"] != identifier
        ]
        manifest["suppressed_capabilities"].append({"id": identifier})
        write_json(self.env.capability_manifest, manifest)
        rendered = self.env.render()
        context = rendered["validation_context"]
        self.assertNotIn(identifier, context["active_capabilities"])
        self.assertIn(identifier, context["suppressed_capabilities"])
        self.assertIn(identifier, context["declared_capabilities"])

    def test_schema_valid_worker_contract_mismatch_fails_closed(self) -> None:
        base = json.loads(self.env.base.read_text(encoding="utf-8"))
        workers = copy.deepcopy(base["worker_rules"])
        workers[0]["worker"]["model"] = "schema-valid-but-unapproved-model"
        overlay = {
            "schema_version": materializer.OVERLAY_SCHEMA,
            "overlay_id": "invalid-worker-contract",
            "operations": [
                {"op": "set", "path": "/worker_rules", "value": workers}
            ],
        }
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(
            materializer.OverlayError,
            "approved worker contract",
        ):
            self.env.render()

    def test_duplicate_policy_ids_and_normalized_aliases_fail_closed(self) -> None:
        base = json.loads(self.env.base.read_text(encoding="utf-8"))
        worker = copy.deepcopy(base["worker_rules"][0])
        worker["id"] = base["rules"][0]["id"]
        worker["priority"] = 999
        overlay = {
            "schema_version": materializer.OVERLAY_SCHEMA,
            "overlay_id": "duplicate-cross-section-id",
            "operations": [
                {
                    "op": "insert_object_unique",
                    "path": "/worker_rules",
                    "unique_key": "id",
                    "value": worker,
                }
            ],
        }
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(materializer.OverlayError, "duplicate policy id"):
            self.env.render()

        known = sorted(base["capability_aliases"])
        overlay = {
            "schema_version": materializer.OVERLAY_SCHEMA,
            "overlay_id": "duplicate-normalized-alias",
            "operations": [
                {
                    "op": "set",
                    "path": "/capability_aliases",
                    "value": {
                        known[0]: ["Shared Alias"],
                        known[1]: ["shared-alias"],
                    },
                }
            ],
        }
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(materializer.OverlayError, "duplicate normalized"):
            self.env.render()

    def test_policy_contradictions_fail_closed(self) -> None:
        overlay = {
            "schema_version": materializer.OVERLAY_SCHEMA,
            "overlay_id": "contradictory-support-limit",
            "operations": [
                {"op": "set", "path": "/max_supports", "value": 0}
            ],
        }
        write_json(self.env.overlay, overlay)
        with self.assertRaisesRegex(materializer.OverlayError, "more supports"):
            self.env.render()


class RoutingPolicyTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = MaterializerEnvironment(Path(self.temporary.name))

    def test_apply_and_replay_are_exact_and_idempotent(self) -> None:
        self.env.target.write_bytes(b"prior-policy\n")
        prior_sha256 = materializer._sha256_file(self.env.target)
        receipt = self.env.apply("apply-replay", expected_target=prior_sha256)
        self.assertEqual(receipt["outcome"], "applied")
        rendered = self.env.render()
        self.assertEqual(self.env.target.read_bytes(), rendered["policy_bytes"])
        transaction_root = (
            self.env.target.parent
            / materializer.STATE_DIRECTORY
            / "transactions"
            / "apply-replay"
        )
        receipt_path = transaction_root / "receipt.json"
        before = receipt_path.read_bytes()
        replay = self.env.apply("apply-replay", expected_target=prior_sha256)
        self.assertEqual(replay, receipt)
        self.assertEqual(receipt_path.read_bytes(), before)

    def test_compare_and_swap_mismatch_preserves_live_target(self) -> None:
        self.env.target.write_bytes(b"external-policy\n")
        receipt = self.env.apply("cas-mismatch", expected_target="0" * 64)
        self.assertEqual(receipt["outcome"], "precondition_failed")
        self.assertEqual(self.env.target.read_bytes(), b"external-policy\n")
        self.assertEqual(
            self.env.apply("cas-mismatch", expected_target="0" * 64), receipt
        )

    def test_external_drift_after_stage_is_preserved(self) -> None:
        self.env.target.write_bytes(b"prior\n")
        prior_sha256 = materializer._sha256_file(self.env.target)
        original_fault = materializer._fault

        def mutate(name: str, configured: str | None) -> None:
            if name == "after-stage":
                self.env.target.write_bytes(b"external-after-stage\n")
            original_fault(name, configured)

        with mock.patch.object(materializer, "_fault", side_effect=mutate):
            receipt = self.env.apply("cas-stage-drift", expected_target=prior_sha256)
        self.assertEqual(receipt["outcome"], "precondition_failed")
        self.assertEqual(self.env.target.read_bytes(), b"external-after-stage\n")

    def test_external_exact_candidate_before_promotion_is_not_claimed_or_rolled_back(self) -> None:
        self.env.target.write_bytes(b"prior\n")
        prior_sha256 = materializer._sha256_file(self.env.target)
        candidate = self.env.render()["policy_bytes"]
        original_fault = materializer._fault

        def mutate(name: str, configured: str | None) -> None:
            if name == "after-stage":
                self.env.target.write_bytes(candidate)
            original_fault(name, configured)

        with mock.patch.object(materializer, "_fault", side_effect=mutate):
            receipt = self.env.apply("cas-exact-candidate", expected_target=prior_sha256)
        self.assertEqual(receipt["outcome"], "precondition_failed")
        self.assertEqual(self.env.target.read_bytes(), candidate)

    def test_injected_post_promotion_failure_rolls_back_and_replays(self) -> None:
        self.env.target.write_bytes(b"prior-policy\n")
        prior_sha256 = materializer._sha256_file(self.env.target)
        receipt = self.env.apply(
            "rollback-replay",
            expected_target=prior_sha256,
            fault_injection="after-promote",
        )
        self.assertEqual(receipt["outcome"], "rolled_back")
        self.assertEqual(self.env.target.read_bytes(), b"prior-policy\n")
        self.assertEqual(
            self.env.apply(
                "rollback-replay",
                expected_target=prior_sha256,
                fault_injection="after-promote",
            ),
            receipt,
        )

    def test_reviewed_materialization_digest_binds_capability_manifest(self) -> None:
        rendered = self.env.render()
        manifest = json.loads(
            self.env.capability_manifest.read_text(encoding="utf-8")
        )
        manifest["entries"].append(
            {"id": "skill:unused-manifest-change", "state": "active-live"}
        )
        write_json(self.env.capability_manifest, manifest)
        with self.assertRaisesRegex(
            materializer.PolicyMaterializationError,
            "materialization inputs do not match",
        ):
            materializer.apply_materialized_policy(
                materializer.PolicyApplyOptions(
                    base_path=self.env.base,
                    overlay_path=self.env.overlay,
                    target_policy=self.env.target,
                    transaction_id="manifest-binding",
                    expected_target_sha256=materializer.MISSING,
                    expected_materialized_sha256=rendered["facts"][
                        "materialized_sha256"
                    ],
                    expected_materialization_digest=rendered["facts"][
                        "materialization_digest"
                    ],
                    capability_manifest_path=self.env.capability_manifest,
                )
            )
        self.assertFalse(self.env.target.exists())

    def test_pre_receipt_semantic_validation_failure_rolls_back(self) -> None:
        self.env.target.write_bytes(b"prior-policy\n")
        prior_sha256 = materializer._sha256_file(self.env.target)
        rendered = self.env.render()
        options = materializer.PolicyApplyOptions(
            base_path=self.env.base,
            overlay_path=self.env.overlay,
            target_policy=self.env.target,
            transaction_id="pre-receipt-validation",
            expected_target_sha256=prior_sha256,
            expected_materialized_sha256=rendered["facts"]["materialized_sha256"],
            expected_materialization_digest=rendered["facts"][
                "materialization_digest"
            ],
            capability_manifest_path=self.env.capability_manifest,
        )
        original = materializer._validate_policy_semantics
        calls = 0

        def fail_only_at_pre_receipt(
            policy: object,
            active: object,
            declared: object | None = None,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise materializer.OverlayError("synthetic pre-receipt semantic failure")
            original(policy, active, declared)

        with mock.patch.object(
            materializer,
            "_validate_policy_semantics",
            side_effect=fail_only_at_pre_receipt,
        ):
            receipt = materializer.apply_materialized_policy(options)

        self.assertEqual(receipt["outcome"], "rolled_back")
        self.assertEqual(self.env.target.read_bytes(), b"prior-policy\n")


if __name__ == "__main__":
    unittest.main()
