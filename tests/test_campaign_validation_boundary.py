"""Controlled process-boundary cases using disposable product and engine state."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from scripts.agent.campaign_engine import evidence
from tests.test_campaign_runtime_components import git, make_repo


def retain_boundary_failure(root, receipt, started_at, failed_at):
    """Capture a failed first launch in CI, without running the command again."""
    destination = os.environ.get("CCOS_BOUNDARY_DIAGNOSTICS")
    if os.environ.get("GITHUB_ACTIONS") != "true" or not destination:
        return None
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "started_at_utc": started_at, "failed_at_utc": failed_at,
        "cwd_supplied": str(root), "cwd_resolved": str(root.resolve(strict=True)),
        "receipt": receipt.to_dict(), "native_logs": [], "directories": [],
        "access_observed": "after failed launch, before fixture cleanup",
    }
    if os.name == "nt":
        log_root = Path(os.environ["USERPROFILE"]) / ".codex" / ".sandbox"
        # Only native text logs, never account configuration or credential files.
        for path in sorted(log_root.glob("*.log"))[:8]:
            try:
                with path.open("rb") as handle:
                    data = handle.read(1024 * 1024)
                report["native_logs"].append({
                    "path": str(path), "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                    "bytes_captured": len(data), "truncated": path.stat().st_size > len(data),
                    "text": data.decode("utf-8", errors="replace"),
                })
            except OSError as exc:
                report["native_logs"].append({"path": str(path), "error": str(exc)})
    for path in (root, *root.parents)[:8]:
        item = {"path": str(path), "observed_at_utc": datetime.now(timezone.utc).isoformat()}
        try:
            stat = path.stat()
            item.update(resolved=str(path.resolve(strict=True)), device=stat.st_dev,
                        inode=stat.st_ino, mode=stat.st_mode,
                        file_attributes=getattr(stat, "st_file_attributes", None),
                        reparse_tag=getattr(path.lstat(), "st_reparse_tag", None))
            if os.name == "nt":
                access = subprocess.run(["icacls.exe", str(path)], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    errors="replace", timeout=5, check=False)
                item.update(icacls_exit=access.returncode, icacls_stdout=access.stdout[:16384],
                            icacls_stderr=access.stderr[:4096])
        except (OSError, subprocess.SubprocessError) as exc:
            item["error"] = str(exc)
        report["directories"].append(item)
    path = output / "first-boundary-failure.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


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
            read_code = "from pathlib import Path; assert Path('src/one.txt').read_text().strip() == 'one'"
            if os.name == "nt":
                read_code += "; import os"
                for name in ("USERPROFILE", "TEMP", "TMP"):
                    read_code += f"; assert os.environ[{name!r}] == {os.environ[name]!r}"
            approved = self.command(root, head, read_code)
            # No skip: a supported validation environment must provision its boundary.
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                passed = evidence.execute_trusted_command(approved)
            except evidence.ValidationFailure as exc:
                failed_at = datetime.now(timezone.utc).isoformat()
                try:
                    retained = retain_boundary_failure(root, exc.evidence, started_at, failed_at)
                    diagnostic = f"\nDiagnostic: {retained}" if retained else ""
                except Exception as diagnostic_error:
                    diagnostic = f"\nDiagnostic capture failed: {diagnostic_error}"
                self.fail("Required validation boundary could not execute an approved read: "
                          + exc.evidence.stderr[:2000] + diagnostic)
            self.assertTrue(passed.passed)
            self.assertEqual(passed.execution_boundary, "READ_ONLY")
            self.assertEqual(passed.boundary_executable_sha256,
                             hashlib.sha256(Path(passed.boundary_executable).read_bytes()).hexdigest())
            self.assertEqual(evidence.verify_command_evidence(approved, passed), passed)
            if os.name == "nt":
                for name in ("USERPROFILE", "TEMP", "TMP"):
                    self.assertIn(name, passed.environment_names)
                self.assertNotIn("Refusing to create helper binaries under temporary dir", passed.stderr)
                self.assertNotIn("Failed to set cwd to temp dir", passed.stderr)
                for name in ("TEMP", "TMP"):
                    with self.subTest(changed_host_directory=name), patch.dict(os.environ, {name: str(base)}):
                        with patch.object(evidence.subprocess, "Popen", side_effect=AssertionError("must not rerun")):
                            with self.assertRaisesRegex(evidence.EvidenceError, "environment_sha256"):
                                evidence.verify_command_evidence(approved, passed)
                self.assertEqual(evidence.verify_command_evidence(approved, passed), passed)
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
            self.assertEqual(caught.exception.evidence.environment_sha256, passed.environment_sha256)
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
            for name in ("USERPROFILE", "TEMP", "TMP"):
                self.assertNotIn(name, receipt.environment_names)

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
    def test_read_only_cannot_redirect_host_bootstrap_directories(self):
        for name in ("USERPROFILE", "TEMP", "TMP"):
            with self.subTest(name=name):
                command = replace(self.command, execution_boundary="READ_ONLY",
                                  environment_allowlist=(*self.command.environment_allowlist, name),
                                  environment={name: str(self.base)})
                with self.assertRaisesRegex(evidence.EvidenceError, "cannot override the host " + name):
                    evidence.execute_trusted_command(command)


class RetainedBoundaryDiagnosticTests(unittest.TestCase):
    def test_failure_capture_is_inert_outside_explicit_ci(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "CCOS_BOUNDARY_DIAGNOSTICS": "unused"}):
            with patch.object(Path, "mkdir", side_effect=AssertionError("must not write")):
                self.assertIsNone(retain_boundary_failure(Path.cwd(), None, "start", "failure"))

    def test_failure_capture_retains_identity_and_bounded_logs_without_native_launch(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            root = base / "product"
            root.mkdir()
            log_root = base / ".codex" / ".sandbox"
            log_root.mkdir(parents=True)
            (log_root / "sandbox.2026-09-12.log").write_bytes(b"x" * (1024 * 1024 + 1))
            (log_root / "not-a-log.json").write_text("must not collect", encoding="utf-8")
            receipt = SimpleNamespace(to_dict=lambda: {"passed": False, "exit_code": 1})
            access = SimpleNamespace(returncode=0, stdout="read access fixture", stderr="")
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "USERPROFILE": str(base),
                                         "CCOS_BOUNDARY_DIAGNOSTICS": str(base / "output")}):
                with patch.object(subprocess, "run", return_value=access) as commands:
                    output = retain_boundary_failure(root, receipt, "start", "failure")
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["cwd_resolved"], str(root.resolve()))
            self.assertEqual(result["receipt"], receipt.to_dict())
            self.assertEqual(result["failed_at_utc"], "failure")
            self.assertTrue(result["directories"])
            self.assertTrue(all(call.args[0][0] == "icacls.exe" for call in commands.call_args_list))
            if os.name == "nt":
                self.assertEqual(len(result["native_logs"]), 1)
                self.assertTrue(result["native_logs"][0]["truncated"])
                self.assertEqual(result["native_logs"][0]["bytes_captured"], 1024 * 1024)
            self.assertNotIn("must not collect", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
