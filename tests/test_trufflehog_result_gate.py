#!/usr/bin/env python3
"""Behavioral tests for redacted TruffleHog release-result evaluation."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.trufflehog_result_gate import (
    GateError,
    decode_scanner_output,
    evaluate,
    load_history_allowlist,
)


COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
PATH = "tests/test_example.py"
RAW_SENTINEL = "DO_NOT_PRINT_THIS_RAW_SECRET"


def finding_json(*, mode: str, commit: str = COMMIT, verified: bool = True) -> str:
    source = {"file": PATH, "line": 17}
    if mode == "history":
        source["commit"] = commit
    return json.dumps(
        {
            "DetectorName": "ExampleDetector",
            "Raw": RAW_SENTINEL,
            "Verified": verified,
            "SourceMetadata": {
                "Data": {"Git" if mode == "history" else "Filesystem": source}
            },
        }
    )


def allowlist_document(*, commit: str = COMMIT, verified: bool = True) -> dict:
    return {
        "schema_version": 1,
        "scope": "immutable-git-history-only",
        "entries": [
            {
                "detector": "ExampleDetector",
                "path": PATH,
                "commit": commit,
                "expected_verified": verified,
                "raw_sha256": hashlib.sha256(
                    RAW_SENTINEL.encode("utf-8")
                ).hexdigest(),
                "reason": (
                    "Synthetic historical test fixture with no credential value "
                    "and an exact immutable occurrence."
                ),
            }
        ],
    }


class TruffleHogResultGateTests(unittest.TestCase):
    def write_allowlist(self, document: dict) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "allowlist.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return temporary, path

    def test_current_finding_fails_without_exposing_raw_value(self) -> None:
        result = evaluate([finding_json(mode="current")], mode="current")
        self.assertFalse(result["ok"])
        self.assertNotIn(RAW_SENTINEL, json.dumps(result))

    def test_history_exact_allowlisted_occurrence_passes(self) -> None:
        temporary, path = self.write_allowlist(allowlist_document())
        self.addCleanup(temporary.cleanup)
        allowlist = load_history_allowlist(path)
        result = evaluate(
            [finding_json(mode="history")],
            mode="history",
            allowlist=allowlist,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["allowlisted_count"], 1)
        self.assertNotIn(RAW_SENTINEL, json.dumps(result))

    def test_wrong_commit_is_unreviewed_and_leaves_exception_unused(self) -> None:
        temporary, path = self.write_allowlist(allowlist_document())
        self.addCleanup(temporary.cleanup)
        result = evaluate(
            [finding_json(mode="history", commit=OTHER_COMMIT)],
            mode="history",
            allowlist=load_history_allowlist(path),
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("unreviewed" in item for item in result["errors"]))
        self.assertTrue(any("not observed" in item for item in result["errors"]))

    def test_nonempty_allowlist_cannot_silently_match_no_findings(self) -> None:
        temporary, path = self.write_allowlist(allowlist_document())
        self.addCleanup(temporary.cleanup)
        result = evaluate([], mode="history", allowlist=load_history_allowlist(path))
        self.assertFalse(result["ok"])
        self.assertTrue(any("not observed" in item for item in result["errors"]))

    def test_malformed_scanner_output_fails_closed(self) -> None:
        result = evaluate(["not-json"], mode="current")
        self.assertFalse(result["ok"])
        self.assertIn("not JSON", result["errors"][0])

    def test_powershell_51_bom_prefix_is_removed_once(self) -> None:
        finding = finding_json(mode="current")
        scanner_bytes = b"\xef\xbb\xbf" + finding.encode("utf-8") + b"\r\n"
        result = evaluate(
            decode_scanner_output(stream=io.BytesIO(scanner_bytes)),
            mode="current",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["finding_count"], 1)
        self.assertFalse(any("not JSON" in item for item in result["errors"]))
        self.assertNotIn(RAW_SENTINEL, json.dumps(result))

    def test_powershell_51_bom_only_input_is_empty(self) -> None:
        result = evaluate(
            decode_scanner_output(stream=io.BytesIO(b"\xef\xbb\xbf")),
            mode="current",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["finding_count"], 0)

    def test_duplicate_or_broad_allowlist_entries_are_rejected(self) -> None:
        duplicate = allowlist_document()
        duplicate["entries"].append(dict(duplicate["entries"][0]))
        temporary, path = self.write_allowlist(duplicate)
        self.addCleanup(temporary.cleanup)
        with self.assertRaises(GateError):
            load_history_allowlist(path)

        broad = allowlist_document()
        broad["entries"][0]["path"] = "tests/**"
        temporary_broad, broad_path = self.write_allowlist(broad)
        self.addCleanup(temporary_broad.cleanup)
        with self.assertRaises(GateError):
            load_history_allowlist(broad_path)

    def test_cli_failure_never_prints_raw_value(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(REPO_ROOT / "scripts" / "trufflehog_result_gate.py"),
                "--mode",
                "current",
            ],
            input=finding_json(mode="current") + "\n",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn(RAW_SENTINEL, completed.stdout)
        self.assertNotIn(RAW_SENTINEL, completed.stderr)


if __name__ == "__main__":
    unittest.main()
