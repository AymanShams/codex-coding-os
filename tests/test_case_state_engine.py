#!/usr/bin/env python3
"""Focused tests for the canonical automation case-state engine."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent" / "case_state.py"


def load_engine():
    spec = importlib.util.spec_from_file_location("case_state_under_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


engine = load_engine()


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
REPO = "https://github.com/example/project"


def request() -> str:
    return str(uuid.uuid4())


def protocol_digest(entries: list[tuple[str, bytes]]) -> str:
    """Independent implementation of the documented snapshot byte stream."""
    import unicodedata

    normalized = [
        (unicodedata.normalize("NFC", path.replace("\\", "/")), content)
        for path, content in entries
    ]
    normalized.sort(key=lambda item: item[0].encode("utf-8"))
    stream = bytearray(b"CCOS-CASE-SNAPSHOT\0")
    version = b"ccos-snapshot-v1"
    stream.extend(len(version).to_bytes(8, "big"))
    stream.extend(version)
    stream.extend(len(normalized).to_bytes(8, "big"))
    for path, content in normalized:
        encoded_path = path.encode("utf-8")
        stream.extend(len(encoded_path).to_bytes(8, "big"))
        stream.extend(encoded_path)
        stream.extend(len(content).to_bytes(8, "big"))
        stream.extend(content)
    return hashlib.sha256(stream).hexdigest()


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ccos-case-state-")
        self.root = Path(self.temp.name)
        self.store = engine.CaseStore(self.root)
        self.case_id = str(uuid.uuid4())
        self.case = self.store.register_case(
            self.case_id,
            objective="bounded review and repair",
            request_id=request(),
            expected_store_revision=0,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @property
    def revision(self) -> int:
        return self.store.get_case(self.case_id)["revision"]

    def mutate(self, method: str, *args, **kwargs):
        kwargs.setdefault("request_id", request())
        kwargs.setdefault("expected_revision", self.revision)
        return getattr(self.store, method)(self.case_id, *args, **kwargs)

    def freeze_candidate(self, head: str = SHA_A) -> dict:
        self.mutate("start_implementation")
        return self.mutate(
            "freeze_candidate",
            heads={REPO: head},
            snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "1" * 64}},
        )

    def begin_review(self) -> None:
        self.freeze_candidate()
        self.mutate("start_review")

    def add_blocker(self, finding_id: str = "F-001", reviewed_sha: str = SHA_A) -> dict:
        return self.mutate(
            "add_finding",
            finding={
                "id": finding_id,
                "candidate": "candidate-1",
                "repo": REPO,
                "reviewed_sha": reviewed_sha,
                "source": "independent-reviewer-1",
                "description": "Concrete blocker",
                "classification": "CURRENT_BLOCKER",
            },
        )

    def authorize_and_complete_repair(self) -> None:
        self.begin_review()
        self.add_blocker()
        self.mutate("freeze_findings")
        self.mutate(
            "authorize_repair",
            finding_ids=["F-001"],
            authority={
                "authority_id": "AUTH-001",
                "source": "explicit-user-approval",
                "authorized_by": "human-owner",
                "scope": "combined repair of F-001",
            },
        )
        self.mutate(
            "complete_repair",
            heads={REPO: SHA_B},
            snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "2" * 64}},
            addressed_ids=["F-001"],
        )

    def enter_closure_check(self) -> None:
        self.authorize_and_complete_repair()
        self.mutate("start_closure_preflight")
        self.mutate(
            "verify_closure_preflight",
            review_heads={REPO: SHA_A},
            repaired_heads={REPO: SHA_B},
            authorized_ids=["F-001"],
            snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "2" * 64}},
        )


class IdentityAndBindingTests(StoreCase):
    def test_default_state_root_is_outside_managed_install_tree(self) -> None:
        expected = Path.home() / ".codex" / "case-state"
        self.assertEqual(engine.default_state_root(), expected)
        self.assertFalse(engine.path_is_within(expected, ROOT))
        with self.assertRaisesRegex(engine.ValidationError, "managed Coding OS tree"):
            engine.CaseStore(ROOT / ".case-state-must-not-live-here")

    def test_case_uuid_must_be_canonical_lowercase(self) -> None:
        uppercase = self.case_id.upper()
        with self.assertRaisesRegex(engine.ValidationError, "lowercase canonical UUID"):
            self.store.register_case(
                uppercase,
                objective="bad identity",
                request_id=request(),
                expected_store_revision=self.store.store_revision(),
            )

    def test_registry_normalizes_identifiers_and_prevents_rebinding(self) -> None:
        self.mutate("bind", kind="repo_url", value="git@GitHub.com:Example/Project.git")
        self.assertEqual(
            self.store.resolve_binding("repo_url", "https://github.com/example/project/"),
            self.case_id,
        )
        other = str(uuid.uuid4())
        self.store.register_case(
            other,
            objective="unrelated",
            request_id=request(),
            expected_store_revision=self.store.store_revision(),
        )
        with self.assertRaisesRegex(engine.ConflictError, "already bound"):
            self.store.bind(
                other,
                kind="repo_url",
                value="https://github.com/EXAMPLE/PROJECT.git",
                request_id=request(),
                expected_revision=1,
            )

    def test_all_binding_kinds_resolve_to_same_case_without_creating_a_case(self) -> None:
        identifiers = {
            "branch": "refs/heads/codex/Case-One",
            "worktree": str(self.root / "worktree"),
            "pr": "GitHub.com/Example/Project#17",
            "thread": "019F4CE9-6FEA-7F52-ACB4-FF4914D9A37D",
            "universal_bundle": " Automation-Preserving-v1 ",
        }
        for kind, value in identifiers.items():
            self.mutate("bind", kind=kind, value=value)
            self.assertEqual(self.store.resolve_binding(kind, value), self.case_id)
        before = len(self.store.list_cases())
        self.assertIsNone(self.store.resolve_binding("branch", "new-chat-new-branch"))
        self.assertEqual(len(self.store.list_cases()), before)

    def test_request_ids_are_idempotent_and_payload_bound(self) -> None:
        req = request()
        first = self.store.bind(
            self.case_id,
            kind="thread",
            value="thread-1",
            request_id=req,
            expected_revision=self.revision,
        )
        second = self.store.bind(
            self.case_id,
            kind="thread",
            value="thread-1",
            request_id=req,
            expected_revision=1,
        )
        self.assertEqual(first["revision"], second["revision"])
        self.assertTrue(second["idempotent"])
        with self.assertRaisesRegex(engine.ConflictError, "different operation payload"):
            self.store.bind(
                self.case_id,
                kind="thread",
                value="thread-2",
                request_id=req,
                expected_revision=self.revision,
            )

    def test_stale_revision_fails_without_mutation(self) -> None:
        self.mutate("bind", kind="thread", value="thread-1")
        before = self.store.get_case(self.case_id)
        with self.assertRaisesRegex(engine.RevisionConflict, "expected revision"):
            self.store.bind(
                self.case_id,
                kind="branch",
                value="branch-2",
                request_id=request(),
                expected_revision=1,
            )
        self.assertEqual(self.store.get_case(self.case_id), before)


class FiniteLifecycleTests(StoreCase):
    def test_clean_candidate_uses_one_review_and_closes_without_repair(self) -> None:
        self.begin_review()
        self.mutate(
            "add_finding",
            finding={
                "id": "N-001",
                "candidate": "candidate-1",
                "repo": REPO,
                "reviewed_sha": SHA_A,
                "source": "reviewer",
                "description": "Optional improvement",
                "classification": "NON_BLOCKING",
            },
        )
        self.mutate("freeze_findings")
        closed = self.mutate("close_without_blockers")
        self.assertEqual(closed["state"], "CLOSED_SUCCESS")
        case = self.store.get_case(self.case_id)
        self.assertEqual(case["limits"]["implementation_generations"], 1)
        self.assertEqual(case["limits"]["review_cohorts"], 1)
        self.assertEqual(case["limits"]["combined_repairs"], 0)
        self.assertEqual(case["limits"]["closure_checks"], 0)

    def test_redesign_required_locks_case_instead_of_publishing(self) -> None:
        self.begin_review()
        self.mutate(
            "add_finding",
            finding={
                "id": "R-001",
                "candidate": "candidate-1",
                "repo": REPO,
                "reviewed_sha": SHA_A,
                "source": "reviewer",
                "description": "The bounded candidate requires a materially new design",
                "classification": "REDESIGN_REQUIRED",
            },
        )
        self.mutate("freeze_findings")
        result = self.mutate("close_without_blockers")
        self.assertEqual(result["state"], "CASE_LOCKED")
        self.assertIn("redesign", result["lock_reason"])

    def test_only_documented_substantive_transitions_are_allowed(self) -> None:
        with self.assertRaisesRegex(engine.TransitionError, "REGISTERED"):
            self.mutate("start_review")
        self.mutate("start_implementation")
        with self.assertRaisesRegex(engine.LimitError, "implementation generation"):
            self.mutate("start_implementation")
        with self.assertRaisesRegex(engine.TransitionError, "IMPLEMENTING"):
            self.mutate("freeze_findings")

    def test_full_repair_path_has_one_combined_repair_and_one_closure(self) -> None:
        self.enter_closure_check()
        result = self.mutate("complete_closure_check", resolutions={"F-001": "RESOLVED"})
        self.assertEqual(result["state"], "CLOSED_SUCCESS")
        case = self.store.get_case(self.case_id)
        self.assertEqual(case["limits"]["combined_repairs"], 1)
        self.assertEqual(case["limits"]["closure_checks"], 1)
        with self.assertRaisesRegex(engine.TransitionError, "CLOSED_SUCCESS"):
            self.mutate("complete_closure_check", resolutions={"F-001": "RESOLVED"})

    def test_remaining_blocker_or_regression_locks_only_case(self) -> None:
        self.enter_closure_check()
        result = self.mutate("complete_closure_check", resolutions={"F-001": "REMAINING"})
        self.assertEqual(result["state"], "CASE_LOCKED")
        self.assertIn("remaining blocker", result["lock_reason"])

    def test_candidate_head_can_advance_only_in_authorized_combined_repair(self) -> None:
        self.freeze_candidate()
        locked = self.mutate("observe_heads", heads={REPO: SHA_C})
        self.assertEqual(locked["state"], "CASE_LOCKED")
        self.assertIn("unexpected head drift", locked["lock_reason"])


class FindingAndRepairTests(StoreCase):
    def test_stale_finding_is_preserved_but_cannot_authorize_repair(self) -> None:
        self.begin_review()
        result = self.add_blocker(reviewed_sha=SHA_C)
        self.assertEqual(result["finding"]["classification"], "INVALID_OR_STALE")
        self.assertFalse(result["finding"]["authorizing"])
        self.mutate("freeze_findings")
        with self.assertRaisesRegex(engine.AuthorizationError, "no frozen CURRENT_BLOCKER"):
            self.mutate(
                "authorize_repair",
                finding_ids=["F-001"],
                authority={"authority_id": "A", "source": "user", "authorized_by": "owner", "scope": "F-001"},
            )

    def test_findings_freeze_once_and_late_findings_are_non_authorizing(self) -> None:
        self.begin_review()
        self.add_blocker()
        self.mutate("freeze_findings")
        late = self.mutate(
            "add_finding",
            finding={
                "id": "F-LATE",
                "candidate": "candidate-1",
                "repo": REPO,
                "reviewed_sha": SHA_A,
                "source": "late-reviewer",
                "description": "Arrived after frozen batch",
                "classification": "CURRENT_BLOCKER",
            },
        )
        self.assertTrue(late["late"])
        self.assertFalse(late["finding"]["authorizing"])
        with self.assertRaisesRegex(engine.TransitionError, "FINDINGS_FROZEN"):
            self.mutate("freeze_findings")

    def test_repair_authority_must_cover_exact_frozen_blocker_set(self) -> None:
        self.begin_review()
        self.add_blocker("F-001")
        self.add_blocker("F-002")
        self.mutate("freeze_findings")
        authority = {"authority_id": "AUTH", "source": "user", "authorized_by": "owner", "scope": "combined"}
        with self.assertRaisesRegex(engine.AuthorizationError, "exact frozen CURRENT_BLOCKER set"):
            self.mutate("authorize_repair", finding_ids=["F-001"], authority=authority)
        self.mutate("authorize_repair", finding_ids=["F-001", "F-002"], authority=authority)
        with self.assertRaisesRegex(engine.TransitionError, "REPAIR_AUTHORIZED"):
            self.mutate("authorize_repair", finding_ids=["F-001", "F-002"], authority=authority)

    def test_repair_head_advances_once_and_requires_all_authorized_ids(self) -> None:
        self.begin_review()
        self.add_blocker()
        self.mutate("freeze_findings")
        self.mutate(
            "authorize_repair",
            finding_ids=["F-001"],
            authority={"authority_id": "AUTH", "source": "user", "authorized_by": "owner", "scope": "combined"},
        )
        with self.assertRaisesRegex(engine.AuthorizationError, "exact authorized blocker set"):
            self.mutate(
                "complete_repair",
                heads={REPO: SHA_B},
                snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "2" * 64}},
                addressed_ids=[],
            )
        self.mutate(
            "complete_repair",
            heads={REPO: SHA_B},
            snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "2" * 64}},
            addressed_ids=["F-001"],
        )
        with self.assertRaisesRegex(engine.TransitionError, "REPAIR_COMPLETE"):
            self.mutate(
                "complete_repair",
                heads={REPO: SHA_C},
                snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "3" * 64}},
                addressed_ids=["F-001"],
            )


class ClosureAndControlFailureTests(StoreCase):
    def test_preflight_identity_failure_does_not_consume_closure_check(self) -> None:
        self.authorize_and_complete_repair()
        self.mutate("start_closure_preflight")
        before = self.store.get_case(self.case_id)
        with self.assertRaisesRegex(engine.PreflightError, "review heads"):
            self.mutate(
                "verify_closure_preflight",
                review_heads={REPO: SHA_C},
                repaired_heads={REPO: SHA_B},
                authorized_ids=["F-001"],
                snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "2" * 64}},
            )
        after = self.store.get_case(self.case_id)
        self.assertEqual(after["state"], "CLOSURE_PREFLIGHT")
        self.assertEqual(after["limits"]["closure_checks"], 0)
        self.assertEqual(after, before)

    def test_preflight_verifies_heads_authority_snapshots_and_no_extra_findings(self) -> None:
        self.authorize_and_complete_repair()
        self.mutate("start_closure_preflight")
        with self.assertRaisesRegex(engine.PreflightError, "snapshot contract"):
            self.mutate(
                "verify_closure_preflight",
                review_heads={REPO: SHA_A},
                repaired_heads={REPO: SHA_B},
                authorized_ids=["F-001"],
                snapshots={REPO: {"contract": "ambiguous-v0", "sha256": "2" * 64}},
            )
        result = self.mutate(
            "verify_closure_preflight",
            review_heads={REPO: SHA_A},
            repaired_heads={REPO: SHA_B},
            authorized_ids=["F-001"],
            snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "2" * 64}},
        )
        self.assertEqual(result["state"], "CLOSURE_CHECK")

    def test_closure_cannot_become_a_general_review(self) -> None:
        self.enter_closure_check()
        result = self.mutate(
            "complete_closure_check",
            resolutions={"F-001": "RESOLVED", "NEW-001": "REMAINING"},
        )
        self.assertEqual(result["state"], "CONTROL_FAILURE")
        self.assertEqual(result["failure"]["category"], "closure_protocol")
        self.assertFalse(result["failure"]["product_defect"])

    def test_tool_failure_preserves_state_and_allows_one_identical_retry(self) -> None:
        self.enter_closure_check()
        failure = self.mutate(
            "record_control_failure",
            category="reviewer",
            fingerprint="reviewer-timeout-v1",
            description="reviewer did not return a protocol result",
        )
        self.assertEqual(failure["state"], "CONTROL_FAILURE")
        self.assertEqual(failure["resumable_state"], "CLOSURE_CHECK")
        retried = self.mutate("retry_control_failure", fingerprint="reviewer-timeout-v1")
        self.assertEqual(retried["state"], "CLOSURE_CHECK")
        self.assertEqual(self.store.get_case(self.case_id)["limits"]["operational_retries"], 1)
        failure_again = self.mutate(
            "record_control_failure",
            category="reviewer",
            fingerprint="reviewer-timeout-v1",
            description="same failure after retry",
        )
        self.assertEqual(failure_again["state"], "CASE_LOCKED")

    def test_retry_must_match_identical_failure(self) -> None:
        self.mutate(
            "record_control_failure",
            category="network",
            fingerprint="network-op-1",
            description="temporary network failure",
        )
        with self.assertRaisesRegex(engine.ControlFailureError, "identical fingerprint"):
            self.mutate("retry_control_failure", fingerprint="network-op-2")

    def test_second_control_failure_record_before_retry_cannot_corrupt_resume_state(self) -> None:
        self.mutate(
            "record_control_failure",
            category="network",
            fingerprint="network-op-1",
            description="temporary network failure",
        )
        before = self.store.get_case(self.case_id)
        with self.assertRaisesRegex(engine.ControlFailureError, "already has an active"):
            self.mutate(
                "record_control_failure",
                category="network",
                fingerprint="network-op-1",
                description="duplicate record with another request id",
            )
        self.assertEqual(self.store.get_case(self.case_id), before)

    def test_missing_coding_os_current_state_is_control_failure_not_product_failure(self) -> None:
        self.assertFalse((ROOT / "docs" / "delivery" / "current-state.md").exists())
        result = self.mutate("record_start_helper_preflight", repo_root=ROOT)
        self.assertEqual(result["state"], "CONTROL_FAILURE")
        self.assertEqual(result["failure"]["category"], "start_helper_missing_current_state")
        self.assertFalse(result["failure"]["product_defect"])
        self.assertEqual(result["resumable_state"], "REGISTERED")


class ScopeAndAuthorityTests(StoreCase):
    def test_locked_case_does_not_block_unrelated_case(self) -> None:
        self.freeze_candidate()
        self.mutate("observe_heads", heads={REPO: SHA_C})
        other = str(uuid.uuid4())
        self.store.register_case(
            other,
            objective="unrelated product work",
            request_id=request(),
            expected_store_revision=self.store.store_revision(),
        )
        check = self.store.check_action(other, "product_work", blocked_case_id=self.case_id)
        self.assertTrue(check["allowed"])
        self.assertIn("unrelated", check["reason"])
        universal = self.store.check_action(other, "universal_sync", blocked_case_id=self.case_id)
        self.assertFalse(universal["allowed"])
        self.assertTrue(universal["separate_authority_required"])

    def test_publication_requires_closed_success_and_separate_authorities_stay_separate(self) -> None:
        self.assertFalse(self.store.check_action(self.case_id, "publication")["allowed"])
        self.begin_review()
        self.mutate("freeze_findings")
        self.mutate("close_without_blockers")
        self.assertTrue(self.store.check_action(self.case_id, "publication")["allowed"])
        for action in ("merge", "deployment", "release", "credential_change", "universal_sync"):
            result = self.store.check_action(self.case_id, action)
            self.assertFalse(result["allowed"])
            self.assertTrue(result["separate_authority_required"])


class SnapshotTests(unittest.TestCase):
    def test_hash_is_independent_of_input_enumeration_order(self) -> None:
        entries = [
            ("zeta.txt", b"z"),
            ("A/alpha.txt", b"alpha\r\n"),
            ("unicode/cafe\u0301.txt", "cafe".encode("utf-8")),
        ]
        expected = protocol_digest(entries)
        for seed in range(10):
            shuffled = entries[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(engine.canonical_snapshot_hash_from_entries(shuffled), expected)

    def test_filesystem_hash_excludes_git_and_explicit_state_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ccos-snapshot-") as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "a.txt").write_bytes(b"a")
            (root / ".git").mkdir()
            (root / ".git" / "secret").write_bytes(b"ignored")
            state = root / ".codex" / "case-state"
            state.mkdir(parents=True)
            (state / "case-state.json").write_bytes(b"ignored state")
            expected = protocol_digest([("src/a.txt", b"a")])
            self.assertEqual(engine.canonical_snapshot_hash(root, state_root=state), expected)

    def test_unsafe_or_normalization_colliding_paths_fail(self) -> None:
        with self.assertRaises(engine.SnapshotError):
            engine.canonical_snapshot_hash_from_entries([("../outside", b"x")])
        with self.assertRaisesRegex(engine.SnapshotError, "normalization collision"):
            engine.canonical_snapshot_hash_from_entries(
                [("cafe\u0301.txt", b"a"), ("caf\u00e9.txt", b"b")]
            )


class PersistenceAndCliTests(StoreCase):
    def test_persisted_store_has_schema_version_and_no_partial_temp_file(self) -> None:
        self.mutate("bind", kind="thread", value="thread-atomic")
        path = self.root / engine.STORE_FILENAME
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], engine.SCHEMA_VERSION)
        self.assertIn(self.case_id, data["cases"])
        self.assertEqual(list(self.root.glob(f"{engine.STORE_FILENAME}.*.tmp")), [])

    def test_cli_has_json_and_human_readable_output(self) -> None:
        status_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--state-root",
                str(self.root),
                "--json",
                "store-status",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(status_result.returncode, 0, status_result.stderr)
        status = json.loads(status_result.stdout)
        self.assertEqual(status["store_revision"], self.store.store_revision())
        self.assertEqual(status["case_count"], 1)
        json_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--state-root",
                str(self.root),
                "--json",
                "show",
                "--case-id",
                self.case_id,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        self.assertEqual(json.loads(json_result.stdout)["case_id"], self.case_id)
        human = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--state-root",
                str(self.root),
                "show",
                "--case-id",
                self.case_id,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn(f"Case: {self.case_id}", human.stdout)
        self.assertIn("State: REGISTERED", human.stdout)

    @unittest.skipUnless(os.name in {"nt", "posix"}, "locking is implemented for Windows and POSIX")
    def test_two_writers_with_same_revision_cannot_both_commit(self) -> None:
        command = [
            sys.executable,
            str(SCRIPT),
            "--state-root",
            str(self.root),
            "--json",
            "bind",
            "--case-id",
            self.case_id,
            "--kind",
            "thread",
            "--expected-revision",
            str(self.revision),
        ]
        left = subprocess.Popen(command + ["--value", "thread-left", "--request-id", request()], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        right = subprocess.Popen(command + ["--value", "thread-right", "--request-id", request()], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        left_out, left_err = left.communicate(timeout=20)
        right_out, right_err = right.communicate(timeout=20)
        codes = sorted([left.returncode, right.returncode])
        self.assertEqual(codes, [0, 2], (left_out, left_err, right_out, right_err))
        json.loads((self.root / engine.STORE_FILENAME).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
