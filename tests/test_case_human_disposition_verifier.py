#!/usr/bin/env python3
"""Focused tests for protected native anti-loop human dispositions."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AGENT_SCRIPTS = ROOT / "scripts" / "agent"
if str(AGENT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(AGENT_SCRIPTS))

import case_human_disposition_verifier as verifier


CASE_ID = "123e4567-e89b-42d3-a456-426614174000"
THREAD_ID = "01900000-0000-7000-8000-000000000201"
OTHER_THREAD_ID = "01900000-0000-7000-8000-000000000202"
TURN_ID = "01900000-0000-7000-8000-000000000203"
REPOSITORY = "https://github.com/example/project"
HEAD = "a" * 40


def record(timestamp: str, record_type: str, payload: dict) -> dict:
    return {"timestamp": timestamp, "type": record_type, "payload": payload}


class NativeHumanDispositionVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos human disposition ")
        self.base = Path(self.temp.name)
        self.codex_home = self.base / ".codex"
        self.state_root = self.codex_home / "case-state"
        self.sessions = self.codex_home / "sessions" / "2026" / "07" / "29"
        self.state_root.mkdir(parents=True)
        self.sessions.mkdir(parents=True)
        self.path = self.sessions / f"rollout-2026-07-29T00-00-00-{THREAD_ID}.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def session_meta(*, session_id: str = THREAD_ID, marker: str) -> dict:
        return record(
            "2026-07-29T00:00:00Z",
            "session_meta",
            {
                "id": THREAD_ID,
                "session_id": session_id,
                "agent_path": "/root",
                "full_history_marker": marker,
            },
        )

    @staticmethod
    def disposition_message() -> dict:
        payload = {
            "protocol_version": verifier.HUMAN_DISPOSITION_PROTOCOL_VERSION,
            "schema_version": 1,
            "case_id": CASE_ID,
            "decision": "STOP_CASE",
            "product_heads": {REPOSITORY: HEAD},
        }
        return record(
            "2026-07-29T00:00:01Z",
            "response_item",
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": json.dumps(payload)}],
                "internal_chat_message_metadata_passthrough": {"turn_id": TURN_ID},
            },
        )

    def write_rollout(self, records: list[dict]) -> None:
        raw = b"".join(
            json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
            for item in records
        )
        self.path.write_bytes(raw)

    def verify(self) -> dict:
        with mock.patch.object(verifier, "_assert_acl_chain_readonly", return_value=None):
            return verifier.verify_human_disposition(
                case_id=CASE_ID,
                decision="STOP_CASE",
                product_heads={REPOSITORY: HEAD},
                native_thread_id=THREAD_ID,
                native_turn_id=TURN_ID,
                state_root=self.state_root,
            )

    def test_full_history_accepts_repeated_consistent_session_meta_records(self) -> None:
        self.write_rollout(
            [
                self.session_meta(marker="initial"),
                record(
                    "2026-07-29T00:00:00Z",
                    "turn_context",
                    {"turn_id": TURN_ID, "full_history": True},
                ),
                self.session_meta(marker="replayed-full-history"),
                self.disposition_message(),
            ]
        )

        authority = self.verify()

        self.assertEqual(authority["native_thread_id"], THREAD_ID)
        self.assertEqual(authority["native_turn_id"], TURN_ID)
        self.assertEqual(authority["decision"], "STOP_CASE")
        self.assertEqual(authority["product_heads"], {REPOSITORY: HEAD})

    def test_full_history_rejects_conflicting_session_identity(self) -> None:
        self.write_rollout(
            [
                self.session_meta(marker="initial"),
                self.session_meta(
                    session_id=OTHER_THREAD_ID,
                    marker="conflicting-replayed-full-history",
                ),
                self.disposition_message(),
            ]
        )

        with self.assertRaisesRegex(
            verifier.NativeHumanDispositionVerificationError,
            "absent or conflicting session identities",
        ):
            self.verify()


if __name__ == "__main__":
    unittest.main()
