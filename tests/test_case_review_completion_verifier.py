#!/usr/bin/env python3
"""Focused tests for native Codex reviewer completion evidence."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AGENT_SCRIPTS = ROOT / "scripts" / "agent"
if str(AGENT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(AGENT_SCRIPTS))

import case_review_completion_verifier as verifier


CASE_ID = "123e4567-e89b-42d3-a456-426614174000"
CHILD_ID = "01900000-0000-7000-8000-000000000101"
PARENT_ID = "01900000-0000-7000-8000-000000000102"
LEGACY_TURN_ID = "01900000-0000-7000-8000-000000000103"
V2_TURN_ID = "01900000-0000-7000-8000-000000000104"
REPO = "https://github.com/example/project"
HEAD = "a" * 40
SCOPE = "exact frozen review scope"
SNAPSHOT = {
    "contract": "ccos-git-snapshot-v1",
    "head": HEAD,
    "sha256": "b" * 64,
}


def epoch(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def record(timestamp: str, record_type: str, payload: dict) -> dict:
    return {"timestamp": timestamp, "type": record_type, "payload": payload}


class NativeVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos native verifier ")
        self.base = Path(self.temp.name)
        self.codex_home = self.base / ".codex"
        self.state_root = self.codex_home / "case-state"
        self.sessions = self.codex_home / "sessions" / "2026" / "01" / "02"
        self.state_root.mkdir(parents=True)
        self.sessions.mkdir(parents=True)
        self.assignment = {
            "reviewer_id": "reviewer-one",
            "reviewer_role": "review_child",
            "thread_id": "/root/reviewer_one",
            "native_thread_id": CHILD_ID,
            "native_parent_thread_id": PARENT_ID,
            "agent_path": "/root/reviewer_one",
            "repository": REPO,
            "reviewed_head": HEAD,
            "snapshot": SNAPSHOT,
            "scope": SCOPE,
            "scope_sha256": hashlib.sha256(SCOPE.encode("utf-8")).hexdigest(),
            "required": True,
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def payload(self, *, completion_state: str = "COMPLETED") -> dict:
        return {
            "protocol_version": verifier.REVIEW_COMPLETION_PROTOCOL_VERSION,
            "schema_version": 2,
            "case_id": CASE_ID,
            "cohort_id": "cohort-one",
            "reviewer_id": self.assignment["reviewer_id"],
            "reviewer_role": "review_child",
            "thread_id": self.assignment["thread_id"],
            "native_thread_id": CHILD_ID,
            "native_parent_thread_id": PARENT_ID,
            "agent_path": self.assignment["agent_path"],
            "repository": REPO,
            "reviewed_head": HEAD,
            "snapshot": SNAPSHOT,
            "scope": SCOPE,
            "scope_sha256": self.assignment["scope_sha256"],
            "completion_state": completion_state,
            "findings": [],
            "finding_ids": [],
        }

    def session_meta(self, **overrides) -> dict:
        payload = {
            "id": CHILD_ID,
            "parent_thread_id": PARENT_ID,
            "session_id": PARENT_ID,
            "agent_path": self.assignment["agent_path"],
            "source": {
                "subagent": {
                    "thread_spawn": {
                        "parent_thread_id": PARENT_ID,
                        "depth": 1,
                        "agent_path": self.assignment["agent_path"],
                    }
                }
            },
        }
        payload.update(overrides)
        return record("2026-01-02T00:00:00.000Z", "session_meta", payload)

    @staticmethod
    def turn(
        turn_id: str,
        *,
        start: str,
        complete: str,
        message: str,
        context_between: bool = True,
        complete_started_at: int | None = None,
    ) -> list[dict]:
        started_at = epoch(start)
        completed_at = epoch(complete)
        started = record(
            start.replace("Z", ".100Z"),
            "event_msg",
            {"type": "task_started", "turn_id": turn_id, "started_at": started_at},
        )
        context = record(
            start.replace("Z", ".200Z"),
            "turn_context",
            {"turn_id": turn_id, "cwd": "C:\\fixture"},
        )
        completed = record(
            complete.replace("Z", ".100Z"),
            "event_msg",
            {
                "type": "task_complete",
                "turn_id": turn_id,
                "last_agent_message": message,
                "started_at": (
                    started_at if complete_started_at is None else complete_started_at
                ),
                "completed_at": completed_at,
            },
        )
        return [started, context, completed] if context_between else [context, started, completed]

    def write_rollout(
        self,
        *,
        meta: dict | None = None,
        include_legacy: bool = False,
        include_v2: bool = True,
        context_between: bool = True,
        complete_started_at: int | None = None,
        suffix: str = "",
    ) -> tuple[Path, list[dict]]:
        records = [meta or self.session_meta()]
        if include_legacy:
            records.extend(
                self.turn(
                    LEGACY_TURN_ID,
                    start="2026-01-02T00:00:00Z",
                    complete="2026-01-02T00:00:01Z",
                    message="legacy reviewer final",
                )
            )
        if include_v2:
            message = json.dumps(
                self.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            records.extend(
                self.turn(
                    V2_TURN_ID,
                    start="2026-01-02T00:00:02Z",
                    complete="2026-01-02T00:00:03Z",
                    message=message,
                    context_between=context_between,
                    complete_started_at=complete_started_at,
                )
            )
        if suffix:
            records.append(
                record(
                    "2026-01-02T00:00:04.000Z",
                    "response_item",
                    {"type": "agent_message", "text": suffix},
                )
            )
        path = self.sessions / f"rollout-test-{CHILD_ID}.jsonl"
        path.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in records),
            encoding="utf-8",
            newline="",
        )
        return path, records

    def verify(self, **kwargs) -> dict:
        with mock.patch.object(verifier, "_require_supported_acl_platform"):
            with mock.patch.object(verifier, "_assert_acl_chain_readonly"):
                return verifier.verify_review_completion(
                    case_id=CASE_ID,
                    cohort_id="cohort-one",
                    cohort_declared_at="2026-01-01T00:00:00Z",
                    assignment=self.assignment,
                    state_root=self.state_root,
                    **kwargs,
                )

    def test_native_v2_completion_derives_turn_times_and_prefix_evidence(self) -> None:
        path, records = self.write_rollout(suffix="later non-v2 activity")
        result = self.verify()
        self.assertEqual(result["completed_turn_id"], V2_TURN_ID)
        self.assertEqual(result["started_at"], "2026-01-02T00:00:02Z")
        self.assertEqual(result["completed_at"], "2026-01-02T00:00:03Z")
        raw_lines = path.read_bytes().splitlines(keepends=True)
        complete_index = next(
            index
            for index, item in enumerate(records)
            if item.get("type") == "event_msg"
            and item["payload"].get("type") == "task_complete"
            and item["payload"].get("turn_id") == V2_TURN_ID
        )
        self.assertEqual(
            result["native_verification"]["log_prefix_sha256"],
            hashlib.sha256(b"".join(raw_lines[: complete_index + 1])).hexdigest(),
        )

    def test_unsupported_platform_fails_before_rollout_discovery(self) -> None:
        with mock.patch.object(verifier.os, "name", "posix"):
            with mock.patch.object(verifier, "_discover_rollout") as discover:
                with self.assertRaisesRegex(
                    verifier.NativeReviewVerificationError,
                    "authenticated Windows ACL evidence",
                ):
                    verifier.verify_review_completion(
                        case_id=CASE_ID,
                        cohort_id="cohort-one",
                        cohort_declared_at="2026-01-01T00:00:00Z",
                        assignment=self.assignment,
                        state_root=self.state_root,
                    )
                discover.assert_not_called()

    def test_legacy_attestation_anchors_original_turn_and_same_rollout(self) -> None:
        self.write_rollout(include_legacy=True)
        legacy_assignment = {
            key: value
            for key, value in self.assignment.items()
            if key not in {"native_thread_id", "native_parent_thread_id", "agent_path"}
        }
        with mock.patch.object(verifier, "_require_supported_acl_platform"):
            with mock.patch.object(verifier, "_assert_acl_chain_readonly"):
                result = verifier.verify_review_completion(
                    case_id=CASE_ID,
                    cohort_id="cohort-one",
                    cohort_declared_at="2026-01-01T00:00:00Z",
                    assignment=legacy_assignment,
                    state_root=self.state_root,
                    expected_findings=[],
                    expected_completion_state="COMPLETED",
                    legacy_completed_turn_id=LEGACY_TURN_ID,
                )
        self.assertEqual(
            result["native_verification"]["legacy_completed_turn_id"],
            LEGACY_TURN_ID,
        )
        self.assertEqual(result["native_verification"]["mode"], "legacy_attestation")

    def test_wrong_parent_depth_or_agent_path_is_rejected(self) -> None:
        for mutation, message in (
            ({"parent_thread_id": "01900000-0000-7000-8000-000000000199"}, "parent"),
            ({"agent_path": "/root/other"}, "agent_path"),
        ):
            with self.subTest(mutation=mutation):
                meta = self.session_meta(**mutation)
                self.write_rollout(meta=meta)
                with self.assertRaisesRegex(verifier.NativeReviewVerificationError, message):
                    self.verify()
        meta = self.session_meta()
        meta["payload"]["source"]["subagent"]["thread_spawn"]["depth"] = 2
        self.write_rollout(meta=meta)
        with self.assertRaisesRegex(verifier.NativeReviewVerificationError, "depth"):
            self.verify()

    def test_incomplete_duplicate_and_misordered_native_evidence_is_rejected(self) -> None:
        self.write_rollout(include_v2=False)
        with self.assertRaisesRegex(verifier.NativeReviewVerificationError, "exactly one raw"):
            self.verify()
        self.write_rollout(context_between=False)
        with self.assertRaisesRegex(verifier.NativeReviewVerificationError, "ordered"):
            self.verify()
        self.write_rollout(complete_started_at=epoch("2026-01-02T00:00:01Z"))
        with self.assertRaisesRegex(verifier.NativeReviewVerificationError, "started_at differs"):
            self.verify()

    def test_completion_before_cohort_declaration_is_rejected(self) -> None:
        self.write_rollout()
        with mock.patch.object(verifier, "_require_supported_acl_platform"):
            with mock.patch.object(verifier, "_assert_acl_chain_readonly"):
                with self.assertRaisesRegex(
                    verifier.NativeReviewVerificationError, "predates"
                ):
                    verifier.verify_review_completion(
                        case_id=CASE_ID,
                        cohort_id="cohort-one",
                        cohort_declared_at="2026-01-03T00:00:00Z",
                        assignment=self.assignment,
                        state_root=self.state_root,
                    )

    def test_duplicate_session_metadata_id_is_rejected(self) -> None:
        path, _ = self.write_rollout()
        duplicate = path.with_name(f"rollout-duplicate-{CHILD_ID}.jsonl")
        duplicate.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(
            verifier.NativeReviewVerificationError, "one matching filename"
        ):
            self.verify()

    def test_truncated_and_linked_rollouts_are_rejected(self) -> None:
        path, _ = self.write_rollout()
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        with self.assertRaisesRegex(verifier.NativeReviewVerificationError, "truncated"):
            self.verify()
        path.unlink()
        target, _ = self.write_rollout()
        real = target.with_name("real-rollout.jsonl")
        target.replace(real)
        try:
            target.symlink_to(real)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaisesRegex(
            verifier.NativeReviewVerificationError, "link or reparse"
        ):
            self.verify()

    @unittest.skipUnless(os.name == "nt", "Windows ACL contract")
    def test_acl_writer_allowlist_rejects_everyone_and_owner_mismatch(self) -> None:
        path_one = self.base / "path one"
        path_two = self.base / "path two"
        path_one.mkdir()
        path_two.mkdir()

        def acl_result(*, other_sid: str, owner_two: str = "S-1-5-21-1"):
            records = [
                {
                    "Path": str(path_one.resolve()),
                    "OwnerSid": "S-1-5-21-1",
                    "Entries": [
                        {"Sid": other_sid, "Type": "Allow", "Mask": 0x0002}
                    ],
                },
                {
                    "Path": str(path_two.resolve()),
                    "OwnerSid": owner_two,
                    "Entries": [],
                },
            ]
            return subprocess_result(stdout=json.dumps(records))

        for dangerous_sid in ("S-1-1-0", "S-1-5-32-545"):
            with self.subTest(dangerous_sid=dangerous_sid):
                with mock.patch.object(
                    verifier.subprocess,
                    "run",
                    return_value=acl_result(other_sid=dangerous_sid),
                ):
                    with self.assertRaisesRegex(
                        verifier.NativeReviewVerificationError, "dangerous writer"
                    ):
                        verifier._assert_acl_writer_allowlist([path_one, path_two])
        with mock.patch.object(
            verifier.subprocess,
            "run",
            return_value=acl_result(
                other_sid="S-1-5-18", owner_two="S-1-5-21-2"
            ),
        ):
            with self.assertRaisesRegex(
                verifier.NativeReviewVerificationError, "ownership differs"
            ):
                verifier._assert_acl_writer_allowlist([path_one, path_two])

    @unittest.skipUnless(os.name == "nt", "Windows ACL contract")
    def test_acl_subprocess_uses_env_json_and_ignores_hostile_psmodulepath(self) -> None:
        path_one = self.base / "path with spaces"
        path_two = self.base / "root path"
        path_one.mkdir()
        path_two.mkdir()
        owner = "S-1-5-21-1"
        records = [
            {"Path": str(path_one.resolve()), "OwnerSid": owner, "Entries": []},
            {"Path": str(path_two.resolve()), "OwnerSid": owner, "Entries": []},
        ]
        captured: dict = {}

        def fake_run(arguments, **kwargs):
            captured["arguments"] = arguments
            captured["env"] = kwargs["env"]
            return subprocess_result(stdout=json.dumps(records))

        with mock.patch.dict(os.environ, {"PSModulePath": "C:\\hostile"}):
            with mock.patch.object(verifier.subprocess, "run", side_effect=fake_run):
                verifier._assert_acl_writer_allowlist([path_one, path_two])
        self.assertNotIn(str(path_one), captured["arguments"])
        script = captured["arguments"][-1]
        self.assertIn("ConvertFrom-Json", script)
        self.assertIn("ForEach-Object", script)
        self.assertNotEqual(captured["env"]["PSModulePath"], "C:\\hostile")
        self.assertEqual(
            json.loads(captured["env"]["CCOS_ACL_PATHS_JSON"]),
            [str(path_one), str(path_two)],
        )


def subprocess_result(*, stdout: str):
    return type(
        "Completed",
        (),
        {"returncode": 0, "stdout": stdout, "stderr": ""},
    )()


if __name__ == "__main__":
    unittest.main()
