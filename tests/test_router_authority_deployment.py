from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO_ROOT
    / "capability-routing"
    / "deployment"
    / "deploy_router_authority.py"
)
SPEC = importlib.util.spec_from_file_location("deploy_router_authority", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
deployment = importlib.util.module_from_spec(SPEC)
import sys

sys.modules[SPEC.name] = deployment
SPEC.loader.exec_module(deployment)


class SyntheticEnvironment:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.source = root / "source"
        self.codex_home = root / "codex-home"
        self.source.mkdir()
        self.codex_home.mkdir()
        self.spec = (
            self.source
            / "capability-routing"
            / "deployment"
            / "router-authority.bundle.json"
        )
        self.spec.parent.mkdir(parents=True)
        self.spec.write_bytes(
            (
                REPO_ROOT
                / "capability-routing"
                / "deployment"
                / "router-authority.bundle.json"
            ).read_bytes()
        )
        for source in deployment.EXPECTED_DEPLOYMENT_MAP:
            path = self.source / Path(*source.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"source:{source}\n".encode("utf-8"))
        for source, precondition in deployment.EXPECTED_LIVE_PRECONDITIONS.items():
            path = self.source / Path(*source.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = (
                REPO_ROOT / Path(*source.split("/"))
            ).read_bytes()
            path.write_bytes(payload)
            target = self.codex_home / Path(*precondition["target"].split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

    def manifest(self) -> dict[str, object]:
        return deployment.build_bundle_manifest(self.source)

    def deploy(self, transaction_id: str, **overrides: object) -> dict[str, object]:
        manifest = self.manifest()
        arguments = {
            "source_root": self.source,
            "codex_home": self.codex_home,
            "transaction_id": transaction_id,
            "expected_bundle_sha256": manifest["bundle_sha256"],
            **overrides,
        }
        return deployment.deploy_router_authority(deployment.DeploymentOptions(**arguments))

    def target(self, relative: str) -> Path:
        return self.codex_home / Path(*relative.split("/"))


class RouterAuthorityDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.env = SyntheticEnvironment(Path(self.temporary.name))

    def test_bundle_manifest_is_deterministic_sorted_and_exact(self) -> None:
        first = self.env.manifest()
        second = self.env.manifest()
        self.assertEqual(first, second)
        targets = [entry["target"] for entry in first["entries"]]
        self.assertEqual(targets, sorted(targets, key=lambda value: value.encode("utf-8")))
        self.assertEqual(set(targets), set(deployment.EXPECTED_DEPLOYMENT_MAP.values()))
        self.assertEqual(first["preconditions"], [])
        self.assertNotIn("hooks/_common.py", targets)
        self.assertNotIn("capability-routing/active-capabilities.json", targets)
        self.assertNotIn("capability-routing/worker-runtime-bom.json", targets)
        self.assertNotIn("capability-routing/routing-policy.yaml", targets)
        self.assertIn("capability-routing/policy-base/routing-policy.yaml", targets)
        self.assertIn("capability-routing/materialize_routing_policy.py", targets)
        self.assertIn("capability-routing/promote_worker_runtime_bom.py", targets)
        self.assertIn("skills/catalogue-router/SKILL.md", targets)
        self.assertIn(
            "skills/catalogue-router/references/capability-catalogue.md", targets
        )
        self.assertIn("skills/catalogue-router/scripts/query-catalogue.ps1", targets)
        self.assertIn("hooks/routing_policy_validation.py", targets)

        changed = next(iter(deployment.EXPECTED_DEPLOYMENT_MAP))
        source = self.env.source / Path(*changed.split("/"))
        source.write_bytes(source.read_bytes() + b"changed\n")
        self.assertNotEqual(first["bundle_sha256"], self.env.manifest()["bundle_sha256"])

    def test_actual_bundle_materializer_loads_deployed_shared_validator(self) -> None:
        codex_home = Path(self.temporary.name) / "actual-codex-home"
        codex_home.mkdir()
        manifest = deployment.build_bundle_manifest(REPO_ROOT)
        receipt = deployment.deploy_router_authority(
            deployment.DeploymentOptions(
                source_root=REPO_ROOT,
                codex_home=codex_home,
                transaction_id="actual-layout-validator",
                expected_bundle_sha256=manifest["bundle_sha256"],
            )
        )
        self.assertEqual(receipt["outcome"], "deployed")

        materializer_path = (
            codex_home / "capability-routing" / "materialize_routing_policy.py"
        )
        module_spec = importlib.util.spec_from_file_location(
            "deployed_materialize_routing_policy",
            materializer_path,
        )
        assert module_spec is not None and module_spec.loader is not None
        deployed_materializer = importlib.util.module_from_spec(module_spec)
        import sys

        sys.modules[module_spec.name] = deployed_materializer
        self.addCleanup(sys.modules.pop, module_spec.name, None)
        module_spec.loader.exec_module(deployed_materializer)
        deployed_validator = deployed_materializer._policy_validation_source_path()
        expected_validator = codex_home / "hooks" / "routing_policy_validation.py"
        self.assertTrue(
            deployed_validator.samefile(expected_validator),
            msg=f"{deployed_validator} does not identify {expected_validator}",
        )

    def test_operator_runbook_uses_deployed_target_and_terminal_order(self) -> None:
        source = "capability-routing/reference-runtime/capability_manifest_recovery.py"
        self.assertEqual(
            deployment.EXPECTED_DEPLOYMENT_MAP[source],
            "hooks/capability_manifest_recovery.py",
        )
        readme = (REPO_ROOT / "capability-routing" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(".codex\\hooks\\capability_manifest_recovery.py", readme)
        self.assertNotIn(
            ".codex\\capability-routing\\capability_manifest_recovery.py",
            readme,
        )
        ordered_markers = (
            "1. Drain durable gateway work, disable and stop the verified scheduled task",
            "2. Apply reviewed universal policy, static-router, worker-source, worker-config, gateway-source",
            "3. Render, review, and apply the exact worker-runtime BOM",
            "4. Build a provisional capability candidate",
            "5. Capture the final authority snapshot",
            "6. Start one verified gateway task",
            "7. Verify manifest, policy, BOM, hook carriers, route registry, gateway task, and worker admission status",
        )
        positions = [readme.index(marker) for marker in ordered_markers]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(
            "The final operator manifest promotion is the terminal authority write.",
            readme,
        )

    def test_successful_transaction_is_exact_durable_and_idempotent(self) -> None:
        first_target = next(iter(deployment.EXPECTED_DEPLOYMENT_MAP.values()))
        first_live = self.env.target(first_target)
        first_live.parent.mkdir(parents=True, exist_ok=True)
        first_live.write_bytes(b"prior\n")

        receipt = self.env.deploy("success-replay")
        self.assertEqual(receipt["outcome"], "deployed")
        transaction_root = (
            self.env.codex_home
            / deployment.STATE_DIRECTORY
            / "transactions"
            / "success-replay"
        )
        receipt_path = transaction_root / "receipt.json"
        receipt_bytes = receipt_path.read_bytes()
        journal = json.loads((transaction_root / "journal.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "COMPLETED")
        self.assertTrue((transaction_root / "bundle-manifest.json").is_file())
        for source, target in deployment.EXPECTED_DEPLOYMENT_MAP.items():
            self.assertEqual(
                self.env.target(target).read_bytes(),
                (self.env.source / Path(*source.split("/"))).read_bytes(),
            )

        replay = self.env.deploy("success-replay")
        self.assertEqual(replay, receipt)
        self.assertEqual(receipt_path.read_bytes(), receipt_bytes)

    def test_injected_mid_promotion_failure_restores_exact_baseline(self) -> None:
        targets = list(deployment.EXPECTED_DEPLOYMENT_MAP.values())
        baselines: dict[str, bytes | None] = {}
        for index, target in enumerate(targets):
            live = self.env.target(target)
            if index % 2 == 0:
                live.parent.mkdir(parents=True, exist_ok=True)
                payload = f"prior:{target}\n".encode("utf-8")
                live.write_bytes(payload)
                baselines[target] = payload
            else:
                baselines[target] = None

        receipt = self.env.deploy(
            "rollback-replay",
            fault_injection="after-promote:3",
        )
        self.assertEqual(receipt["outcome"], "rolled_back")
        for target, prior in baselines.items():
            live = self.env.target(target)
            if prior is None:
                self.assertFalse(live.exists(), target)
            else:
                self.assertEqual(live.read_bytes(), prior, target)

        receipt_path = (
            self.env.codex_home
            / deployment.STATE_DIRECTORY
            / "transactions"
            / "rollback-replay"
            / "receipt.json"
        )
        before = receipt_path.read_bytes()
        replay = self.env.deploy("rollback-replay", fault_injection="after-promote:3")
        self.assertEqual(replay, receipt)
        self.assertEqual(receipt_path.read_bytes(), before)

    def test_interrupted_completed_write_recovers_to_exact_baseline(self) -> None:
        first_target = next(iter(deployment.EXPECTED_DEPLOYMENT_MAP.values()))
        first_live = self.env.target(first_target)
        first_live.parent.mkdir(parents=True, exist_ok=True)
        first_live.write_bytes(b"prior-before-interruption\n")
        self.env.deploy("interrupted")

        transaction_root = (
            self.env.codex_home
            / deployment.STATE_DIRECTORY
            / "transactions"
            / "interrupted"
        )
        (transaction_root / "receipt.json").unlink()
        journal = json.loads((transaction_root / "journal.json").read_text(encoding="utf-8"))
        journal["phase"] = "LIVE_TARGETS_VERIFIED"
        deployment._save_journal(transaction_root, journal)

        recovered = self.env.deploy("interrupted")
        self.assertEqual(recovered["outcome"], "recovered_rolled_back")
        self.assertEqual(first_live.read_bytes(), b"prior-before-interruption\n")
        for target in list(deployment.EXPECTED_DEPLOYMENT_MAP.values())[1:]:
            self.assertFalse(self.env.target(target).exists(), target)
        self.assertEqual(self.env.deploy("interrupted"), recovered)

    def test_terminal_journal_tamper_breaks_replay(self) -> None:
        self.env.deploy("tamper")
        journal_path = (
            self.env.codex_home
            / deployment.STATE_DIRECTORY
            / "transactions"
            / "tamper"
            / "journal.json"
        )
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["phase"] = "TAMPERED"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        with self.assertRaisesRegex(deployment.ReplayError, "durable journal"):
            self.env.deploy("tamper")

    def test_compare_and_swap_preserves_external_drift(self) -> None:
        first_target = sorted(deployment.EXPECTED_DEPLOYMENT_MAP.values())[0]
        live = self.env.target(first_target)
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_bytes(b"baseline\n")
        original_fault = deployment._fault

        def mutate_after_stage(name: str, configured: str | None) -> None:
            if name == "after-stage":
                live.write_bytes(b"external-writer\n")
            original_fault(name, configured)

        with mock.patch.object(deployment, "_fault", side_effect=mutate_after_stage):
            receipt = self.env.deploy("cas-drift")

        self.assertEqual(receipt["outcome"], "precondition_failed")
        self.assertEqual(live.read_bytes(), b"external-writer\n")
        replay = self.env.deploy("cas-drift")
        self.assertEqual(replay, receipt)

    def test_compare_and_swap_preserves_unowned_exact_candidate(self) -> None:
        first_source, first_target = sorted(
            deployment.EXPECTED_DEPLOYMENT_MAP.items(),
            key=lambda item: item[1],
        )[0]
        live = self.env.target(first_target)
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_bytes(b"baseline\n")
        candidate = (
            self.env.source / Path(*first_source.split("/"))
        ).read_bytes()
        original_fault = deployment._fault

        def mutate_after_stage(name: str, configured: str | None) -> None:
            if name == "after-stage":
                live.write_bytes(candidate)
            original_fault(name, configured)

        with mock.patch.object(deployment, "_fault", side_effect=mutate_after_stage):
            receipt = self.env.deploy("cas-exact-candidate")

        self.assertEqual(receipt["outcome"], "precondition_failed")
        self.assertEqual(live.read_bytes(), candidate)
        self.assertEqual(self.env.deploy("cas-exact-candidate"), receipt)

    def test_late_compare_and_swap_drift_rolls_back_owned_targets_only(self) -> None:
        ordered = sorted(deployment.EXPECTED_DEPLOYMENT_MAP.items(), key=lambda item: item[1])
        first_source, first_target = ordered[0]
        _, second_target = ordered[1]
        first_live = self.env.target(first_target)
        second_live = self.env.target(second_target)
        first_live.parent.mkdir(parents=True, exist_ok=True)
        second_live.parent.mkdir(parents=True, exist_ok=True)
        first_live.write_bytes(b"first-baseline\n")
        second_live.write_bytes(b"second-baseline\n")
        original_fault = deployment._fault

        def mutate_after_first_promotion(name: str, configured: str | None) -> None:
            if name == "after-promote:1":
                second_live.write_bytes(b"external-second-target\n")
            original_fault(name, configured)

        with mock.patch.object(deployment, "_fault", side_effect=mutate_after_first_promotion):
            receipt = self.env.deploy("cas-late-drift")

        self.assertEqual(receipt["outcome"], "precondition_failed")
        self.assertEqual(first_live.read_bytes(), b"first-baseline\n")
        self.assertEqual(second_live.read_bytes(), b"external-second-target\n")
        self.assertNotEqual(
            first_live.read_bytes(),
            (self.env.source / Path(*first_source.split("/"))).read_bytes(),
        )
        self.assertEqual(self.env.deploy("cas-late-drift"), receipt)

    def test_retired_router_directory_refuses_deployment_before_target_writes(self) -> None:
        retired = self.env.codex_home / "coding-os" / "hooks" / "capability-router"
        retired.mkdir(parents=True)
        with self.assertRaisesRegex(deployment.DeploymentError, "retired router directory"):
            self.env.deploy("retired-path")
        self.assertFalse((self.env.codex_home / deployment.STATE_DIRECTORY).exists())
        for target in deployment.EXPECTED_DEPLOYMENT_MAP.values():
            self.assertFalse(self.env.target(target).exists())

    def test_every_declared_runtime_state_path_is_denied(self) -> None:
        for target in deployment.RUNTIME_STATE_PATHS:
            with self.subTest(target=target):
                with self.assertRaisesRegex(deployment.BundleError, "runtime state"):
                    deployment._reject_runtime_state_target(target)

    def test_source_bundle_must_match_explicit_expected_digest(self) -> None:
        with self.assertRaisesRegex(deployment.BundleError, "explicitly expected"):
            deployment.deploy_router_authority(
                deployment.DeploymentOptions(
                    source_root=self.env.source,
                    codex_home=self.env.codex_home,
                    transaction_id="wrong-bundle",
                    expected_bundle_sha256="0" * 64,
                )
            )
        self.assertFalse((self.env.codex_home / deployment.STATE_DIRECTORY).exists())

    def test_exclusive_target_lock_fails_closed(self) -> None:
        lock = self.env.codex_home / deployment.STATE_DIRECTORY / "deployment.lock"
        with deployment._ExclusiveLock(lock, 0):
            with self.assertRaises(deployment.LockError):
                with deployment._ExclusiveLock(lock, 0):
                    self.fail("second lock unexpectedly acquired")

    def test_codex_home_must_be_explicit_and_absolute(self) -> None:
        manifest = self.env.manifest()
        with self.assertRaisesRegex(deployment.DeploymentError, "explicit absolute"):
            deployment.deploy_router_authority(
                deployment.DeploymentOptions(
                    source_root=self.env.source,
                    codex_home="relative-codex-home",
                    transaction_id="relative-home",
                    expected_bundle_sha256=manifest["bundle_sha256"],
                )
            )


if __name__ == "__main__":
    unittest.main()
