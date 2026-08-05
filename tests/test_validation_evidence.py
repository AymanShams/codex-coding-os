#!/usr/bin/env python3
"""Tests for the retained, non-executing validation receipt validator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "agent" / "validation_evidence.py"
EXAMPLE = ROOT / "templates" / "validation-evidence.example.json"


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def receipt(*, exit_code: int = 0, passed: bool = True) -> dict[str, object]:
    stdout = "synthetic validation\n"
    stderr = ""
    body: dict[str, object] = {
        "protocol_version": "ccos-validation-execution-v1",
        "executable": "C:/synthetic/python.exe",
        "executable_sha256": "1" * 64,
        "arguments": ["-B", "-c", "print('synthetic validation')"],
        "working_directory": "C:/synthetic/repository",
        "environment_names": ["PATH"],
        "timeout_seconds": 30,
        "output_limit_bytes": 65536,
        "candidate_head": "2" * 40,
        "head_after": "2" * 40,
        "status_before_sha256": "3" * 64,
        "status_after_sha256": "3" * 64,
        "exit_code": exit_code,
        "timed_out": False,
        "output_limited": False,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stdout": stdout,
        "stderr": stderr,
        "required_exit_code": 0,
        "passed": passed,
        "duration_ms": 1,
    }
    body["evidence_sha256"] = hashlib.sha256(canonical_json(body)).hexdigest()
    return body


def run_validator(payload: dict[str, object], expected: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "receipt.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-B", str(VALIDATOR), str(path), "--json"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
    if completed.returncode != expected:
        raise AssertionError(
            f"expected {expected}, got {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    return json.loads(completed.stdout)


class ValidationEvidenceTests(unittest.TestCase):
    def test_synthetic_template_is_one_valid_record_shape(self) -> None:
        payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        result = run_validator(payload, 0)
        self.assertEqual(result, {
            "ok": True,
            "passed": True,
            "protocol_version": "ccos-validation-execution-v1",
        })

    def test_nonzero_exit_can_be_recorded_but_never_credited_as_passing(self) -> None:
        failed = receipt(exit_code=7, passed=False)
        self.assertFalse(run_validator(failed, 0)["passed"])
        dishonest = dict(failed)
        dishonest["passed"] = True
        dishonest["evidence_sha256"] = hashlib.sha256(
            canonical_json({key: value for key, value in dishonest.items() if key != "evidence_sha256"})
        ).hexdigest()
        denied = run_validator(dishonest, 2)
        self.assertFalse(denied["ok"])
        self.assertIn("passed conflicts", denied["message"])

    def test_digest_output_and_head_mutations_are_rejected(self) -> None:
        cases = {
            "record digest": ("evidence_sha256", "0" * 64, "digest"),
            "stdout": ("stdout", "changed", "stdout_sha256"),
            "head": ("head_after", "4" * 40, "head race"),
        }
        for name, (field, value, message) in cases.items():
            with self.subTest(name=name):
                payload = receipt()
                payload[field] = value
                if field != "evidence_sha256":
                    payload["evidence_sha256"] = hashlib.sha256(
                        canonical_json(
                            {
                                key: item
                                for key, item in payload.items()
                                if key != "evidence_sha256"
                            }
                        )
                    ).hexdigest()
                denied = run_validator(payload, 2)
                self.assertIn(message, denied["message"])

    def test_recorded_arguments_are_inert_and_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            marker = Path(raw) / "must-not-exist.txt"
            payload = receipt()
            payload["arguments"] = ["write", str(marker)]
            payload["evidence_sha256"] = hashlib.sha256(
                canonical_json(
                    {
                        key: value
                        for key, value in payload.items()
                        if key != "evidence_sha256"
                    }
                )
            ).hexdigest()
            self.assertTrue(run_validator(payload, 0)["ok"])
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
