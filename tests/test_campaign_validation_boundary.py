"""Controlled process-boundary cases using disposable product and engine state."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.agent.campaign_engine import evidence
from tests.test_campaign_runtime_components import git, make_repo


class ValidationBoundaryTests(unittest.TestCase):
    def command(self, root, head, code):
        return evidence.TrustedCommand(
            executable=sys.executable, arguments=("-B", "-c", code),
            working_directory=str(root), environment_allowlist=("PATH", "SYSTEMROOT"),
            environment={}, timeout_seconds=20, output_limit_bytes=10000,
            candidate_head=head, expected_working_tree="CLEAN", execution_boundary="READ_ONLY",
        )

    def test_required_boundary_never_falls_back_when_launcher_is_missing(self):
        with patch.object(evidence, "_resolve_executable", side_effect=evidence.EvidenceError("missing")):
            with self.assertRaises(evidence.EvidenceError):
                evidence.validation_boundary_command(Path(sys.executable), (), Path.cwd(), "READ_ONLY")

    def test_unknown_boundary_is_rejected(self):
        with self.assertRaises(evidence.EvidenceError):
            evidence.validation_boundary_command(Path(sys.executable), (), Path.cwd(), "invented")

    def test_supported_boundary_reads_product_and_denies_engine_state_write(self):
        with tempfile.TemporaryDirectory(prefix="ccos-boundary-") as raw:
            base = Path(raw)
            root = base / "product"
            head = make_repo(root)
            marker = base / "engine-owned-state.txt"
            marker.write_text("retained", encoding="utf-8")
            approved = self.command(root, head, "from pathlib import Path; assert Path('src/one.txt').read_text().strip() == 'one'")
            # No skip: a supported validation environment must provision its boundary.
            try:
                passed = evidence.execute_trusted_command(approved)
            except evidence.ValidationFailure as exc:
                self.fail("Required validation boundary could not execute an approved read: "
                          + exc.evidence.stderr[:2000])
            self.assertTrue(passed.passed)
            self.assertEqual(passed.execution_boundary, "READ_ONLY")
            self.assertEqual(passed.boundary_executable_sha256,
                             hashlib.sha256(Path(passed.boundary_executable).read_bytes()).hexdigest())
            self.assertEqual(evidence.verify_command_evidence(approved, passed), passed)
            if os.name == "nt":
                self.assertIn("USERPROFILE", passed.environment_names)
            launcher = Path(passed.boundary_executable)
            original_read = Path.read_bytes
            def changed_launcher(path):
                return b"changed launcher fixture" if path == launcher else original_read(path)
            with patch.object(Path, "read_bytes", changed_launcher):
                with self.assertRaisesRegex(evidence.EvidenceError, "boundary_executable_sha256"):
                    evidence.verify_command_evidence(approved, passed)
            change = f"from pathlib import Path; Path({str(marker)!r}).write_text('changed')"
            with self.assertRaises(evidence.ValidationFailure) as caught:
                evidence.execute_trusted_command(replace(approved, arguments=("-B", "-c", change)))
            self.assertEqual(marker.read_text(encoding="utf-8"), "retained")
            self.assertFalse(caught.exception.evidence.passed)
            self.assertEqual(git(root, "rev-parse", "HEAD"), head)
            self.assertEqual(git(root, "status", "--porcelain"), "")


class SavedValidationInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ccos-validation-inputs-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "product"
        head = make_repo(self.root)
        self.command = evidence.TrustedCommand(
            executable=sys.executable, arguments=("-B", "-c", "print('passed')"),
            working_directory=str(self.root), environment_allowlist=("PATH", "SYSTEMROOT", "CI"),
            environment={"CI": "fixture-value"}, timeout_seconds=20, output_limit_bytes=10000,
            candidate_head=head, expected_working_tree="CLEAN",
        )

    def test_saved_receipt_verifies_without_running_and_omits_environment_values(self):
        receipt = evidence.execute_trusted_command(self.command)
        persisted = json.loads(json.dumps(receipt.to_dict()))
        self.assertNotIn("fixture-value", json.dumps(persisted))
        self.assertIn("CI", persisted["environment_names"])
        self.assertEqual(len(persisted["environment_sha256"]), 64)
        with patch.object(evidence.subprocess, "Popen", side_effect=AssertionError("must not rerun")):
            verified = evidence.verify_command_evidence(self.command.to_dict(), persisted)
        self.assertEqual(verified.evidence_sha256, receipt.evidence_sha256)
        if os.name == "nt":
            self.assertNotIn("USERPROFILE", receipt.environment_names)

    def test_changed_environment_or_command_cannot_reuse_saved_validation(self):
        receipt = evidence.execute_trusted_command(self.command)
        with self.assertRaisesRegex(evidence.EvidenceError, "environment_sha256"):
            evidence.verify_command_evidence(replace(self.command, environment={"CI": "changed"}), receipt)
        with self.assertRaisesRegex(evidence.EvidenceError, "arguments"):
            evidence.verify_command_evidence(replace(self.command, arguments=("-c", "print('different')")), receipt)
        self.assertEqual(evidence.verify_command_evidence(self.command, receipt), receipt)
        inherited = replace(self.command, environment={})
        with patch.dict(os.environ, {"CI": "before"}):
            receipt = evidence.execute_trusted_command(inherited)
            self.assertEqual(evidence.verify_command_evidence(inherited, receipt), receipt)
        with patch.dict(os.environ, {"CI": "after"}):
            with self.assertRaisesRegex(evidence.EvidenceError, "environment_sha256"):
                evidence.verify_command_evidence(inherited, receipt)

    def test_changed_executable_bytes_cannot_reuse_saved_validation(self):
        # Use a disposable self-contained platform command, never alter the
        # installed interpreter or a real account executable.
        source = shutil.which("cmd.exe" if os.name == "nt" else "true")
        self.assertIsNotNone(source)
        executable = self.base / ("validator.exe" if os.name == "nt" else "validator")
        shutil.copy(source, executable)
        command = replace(self.command, executable=str(executable),
                          arguments=("/d", "/c", "exit", "0") if os.name == "nt" else ())
        receipt = evidence.execute_trusted_command(command)
        self.assertEqual(evidence.verify_command_evidence(command, receipt), receipt)
        executable.write_bytes(b"changed executable fixture")
        with self.assertRaisesRegex(evidence.EvidenceError, "executable_sha256"):
            evidence.verify_command_evidence(command, receipt)

    def test_unavailable_environment_binding_and_corrupt_receipt_are_rejected(self):
        receipt = evidence.execute_trusted_command(self.command)
        with self.assertRaisesRegex(evidence.EvidenceError, "digest"):
            evidence.verify_command_evidence(self.command, replace(receipt, stdout="changed"))
        legacy = receipt.to_dict()
        legacy["environment_sha256"] = None
        legacy.pop("evidence_sha256")
        legacy["evidence_sha256"] = evidence._sha256(evidence._canonical_json(legacy))
        with self.assertRaisesRegex(evidence.EvidenceError, "environment_sha256"):
            evidence.verify_command_evidence(self.command, legacy)

    @unittest.skipUnless(os.name == "nt", "Windows installed sandbox bootstrap")
    def test_read_only_cannot_redirect_host_profile(self):
        command = replace(self.command, execution_boundary="READ_ONLY",
                          environment_allowlist=(*self.command.environment_allowlist, "USERPROFILE"),
                          environment={"USERPROFILE": str(self.base)})
        with self.assertRaisesRegex(evidence.EvidenceError, "cannot override"):
            evidence.execute_trusted_command(command)


if __name__ == "__main__":
    unittest.main()
