#!/usr/bin/env python3
"""Tests for the pinned real-TLC campaign model runner."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import run_campaign_tlc as runner


class CampaignTlcRunnerTests(unittest.TestCase):
    def test_official_release_is_version_and_digest_pinned(self) -> None:
        self.assertEqual(runner.TLA2TOOLS_VERSION, "1.7.4")
        self.assertEqual(
            runner.TLA2TOOLS_URL,
            "https://github.com/tlaplus/tlaplus/releases/download/"
            "v1.7.4/tla2tools.jar",
        )
        self.assertEqual(
            runner.TLA2TOOLS_SHA256,
            "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88",
        )

    def test_jar_verification_rejects_any_other_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tla2tools.jar"
            path.write_bytes(b"pinned-test-jar")
            expected = hashlib.sha256(b"pinned-test-jar").hexdigest()
            self.assertEqual(runner.sha256_file(path), expected)
            with self.assertRaisesRegex(runner.TlcRunnerError, "digest mismatch"):
                runner.verify_jar(path)

    def _run_with_result(self, returncode: int, output: bytes) -> tuple[mock.Mock, str]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        jar = Path(temporary.name) / "tla2tools.jar"
        jar.write_bytes(b"fixture")

        def fake_run(command, **kwargs):
            kwargs["stdout"].write(output)
            return subprocess.CompletedProcess(command, returncode)

        patched = mock.Mock(side_effect=fake_run)
        with mock.patch.object(
            runner, "verify_jar", return_value=runner.TLA2TOOLS_SHA256
        ), mock.patch.object(runner.subprocess, "run", patched):
            result = runner.run_tlc(
                jar,
                java_executable="java-fixture",
                timeout_seconds=30,
            )
        return patched, result

    def test_runner_executes_exact_bounded_campaign_model(self) -> None:
        patched, result = self._run_with_result(
            0,
            f"{runner.TLC_SUCCESS_MARKER}\n".encode(),
        )
        command = patched.call_args.args[0]
        self.assertEqual(command[0], "java-fixture")
        self.assertEqual(command[3:7], ("tlc2.TLC", "-workers", "1", "-metadir"))
        self.assertEqual(command[-3:], ("-config", "Campaign.cfg", "Campaign.tla"))
        self.assertEqual(patched.call_args.kwargs["timeout"], 30)
        self.assertEqual(
            patched.call_args.kwargs["cwd"],
            Path(runner.__file__).resolve().parents[1] / "formal",
        )
        self.assertIn(runner.TLC_SUCCESS_MARKER, result)

    def test_nonzero_exit_is_failure_even_if_success_text_exists(self) -> None:
        with self.assertRaisesRegex(runner.TlcRunnerError, "exited with code 1"):
            self._run_with_result(1, runner.TLC_SUCCESS_MARKER.encode())

    def test_missing_success_marker_fails_closed(self) -> None:
        with self.assertRaisesRegex(runner.TlcRunnerError, "success marker"):
            self._run_with_result(0, b"TLC finished without a proof receipt")


if __name__ == "__main__":
    unittest.main()
