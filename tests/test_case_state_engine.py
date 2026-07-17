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
OTHER_REPO = "https://github.com/example/other-project"
SYNTHETIC_THREAD = "123e4567-e89b-42d3-a456-426614174000"


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

    def test_repository_association_is_normalized_and_nonexclusive(self) -> None:
        self.mutate("bind", kind="repo_url", value="git@GitHub.com:Example/Project.git")
        other = str(uuid.uuid4())
        self.store.register_case(
            other,
            objective="unrelated",
            request_id=request(),
            expected_store_revision=self.store.store_revision(),
        )
        self.store.bind(
            other,
            kind="repo_url",
            value="https://github.com/EXAMPLE/PROJECT.git",
            request_id=request(),
            expected_revision=1,
        )
        self.assertEqual(
            self.store.resolve_bindings("repo_url", "https://github.com/example/project/"),
            sorted([self.case_id, other]),
        )
        with self.assertRaisesRegex(engine.ConflictError, "multiple cases"):
            self.store.resolve_binding("repo_url", REPO)

    def test_same_exclusive_binding_cannot_be_rebound(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="refs/heads/codex/case-one", repository=REPO)
        self.mutate("bind", kind="pr", value=f"{REPO}#17")
        self.mutate("bind", kind="worktree", value=str(self.root / "shared-worktree"))
        self.mutate("bind", kind="thread", value=SYNTHETIC_THREAD)
        self.mutate("bind", kind="universal_bundle", value="synthetic-bundle-v1")
        other = str(uuid.uuid4())
        self.store.register_case(
            other,
            objective="replacement",
            request_id=request(),
            expected_store_revision=self.store.store_revision(),
        )
        self.store.bind(
            other,
            kind="repo_url",
            value=REPO,
            request_id=request(),
            expected_revision=1,
        )
        for kind, value, repository in (
            ("branch", "codex/case-one", REPO),
            ("pr", f"{REPO}#17", None),
            ("worktree", str(self.root / "shared-worktree"), None),
            ("thread", SYNTHETIC_THREAD, None),
            ("universal_bundle", "synthetic-bundle-v1", None),
        ):
            with self.subTest(kind=kind), self.assertRaisesRegex(engine.ConflictError, "already bound"):
                self.store.bind(
                    other,
                    kind=kind,
                    value=value,
                    repository=repository,
                    request_id=request(),
                    expected_revision=2,
                )

    def test_identical_branch_names_in_different_repositories_do_not_conflict(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="codex/bounded", repository=REPO)
        other = str(uuid.uuid4())
        self.store.register_case(
            other,
            objective="other repository work",
            request_id=request(),
            expected_store_revision=self.store.store_revision(),
        )
        self.store.bind(
            other,
            kind="repo_url",
            value=OTHER_REPO,
            request_id=request(),
            expected_revision=1,
        )
        result = self.store.bind(
            other,
            kind="branch",
            value="refs/heads/codex/bounded",
            repository=OTHER_REPO,
            request_id=request(),
            expected_revision=2,
        )
        self.assertEqual(result["binding"]["repository"], OTHER_REPO)
        self.assertEqual(
            self.store.resolve_binding("branch", "codex/bounded", repository=REPO),
            self.case_id,
        )
        self.assertEqual(
            self.store.resolve_binding("branch", "codex/bounded", repository=OTHER_REPO),
            other,
        )

    def test_different_branches_in_the_same_repository_do_not_conflict(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="codex/first", repository=REPO)
        other = str(uuid.uuid4())
        self.store.register_case(
            other,
            objective="second branch",
            request_id=request(),
            expected_store_revision=self.store.store_revision(),
        )
        self.store.bind(
            other,
            kind="repo_url",
            value=REPO,
            request_id=request(),
            expected_revision=1,
        )
        result = self.store.bind(
            other,
            kind="branch",
            value="codex/second",
            repository=REPO,
            request_id=request(),
            expected_revision=2,
        )
        self.assertEqual(result["binding"]["value"], "codex/second")

    def test_all_binding_kinds_resolve_to_same_case_without_creating_a_case(self) -> None:
        identifiers = {
            "worktree": str(self.root / "worktree"),
            "pr": "GitHub.com/Example/Project#17",
            "thread": SYNTHETIC_THREAD,
            "universal_bundle": " Automation-Preserving-v1 ",
        }
        for kind, value in identifiers.items():
            self.mutate("bind", kind=kind, value=value)
            self.assertEqual(self.store.resolve_binding(kind, value), self.case_id)
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="refs/heads/codex/Case-One", repository=REPO)
        self.assertEqual(
            self.store.resolve_binding("branch", "codex/Case-One", repository=REPO),
            self.case_id,
        )
        before = len(self.store.list_cases())
        self.assertIsNone(
            self.store.resolve_binding("branch", "new-chat-new-branch", repository=REPO)
        )
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
                repository=REPO,
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
    def bind_execution_scope(
        self,
        case_id: str,
        *,
        repository: str,
        branch: str,
        expected_revision: int,
    ) -> int:
        self.store.bind(
            case_id,
            kind="repo_url",
            value=repository,
            request_id=request(),
            expected_revision=expected_revision,
        )
        self.store.bind(
            case_id,
            kind="branch",
            value=branch,
            repository=repository,
            request_id=request(),
            expected_revision=expected_revision + 1,
        )
        return expected_revision + 2

    def action_context(
        self,
        *,
        actor_role: str = "implementer_child",
        repository: str = REPO,
        branch: str = "codex/unrelated",
        head: str | None = None,
    ) -> dict[str, str]:
        context = {
            "actor_role": actor_role,
            "repository": repository,
            "branch": branch,
        }
        if head is not None:
            context["head"] = head
        return context

    def test_locked_case_does_not_block_unrelated_case_in_same_repository(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="codex/locked", repository=REPO)
        self.freeze_candidate()
        self.mutate("observe_heads", heads={REPO: SHA_C})
        other = str(uuid.uuid4())
        self.store.register_case(
            other,
            objective="unrelated product work",
            request_id=request(),
            expected_store_revision=self.store.store_revision(),
        )
        self.bind_execution_scope(
            other,
            repository=REPO,
            branch="codex/unrelated",
            expected_revision=1,
        )
        check = self.store.check_action(
            other,
            "product_work",
            blocked_case_id=self.case_id,
            **self.action_context(),
        )
        self.assertTrue(check["allowed"])
        self.assertIn("unrelated", check["reason"])
        self.assertEqual(check["reason_codes"], ["UNRELATED_CASE_ALLOWED"])
        universal = self.store.check_action(
            other,
            "universal_sync",
            blocked_case_id=self.case_id,
            **self.action_context(actor_role="parent"),
        )
        self.assertFalse(universal["allowed"])
        self.assertTrue(universal["separate_authority_required"])

    def test_locked_case_overlap_blocks_replacement_scope_without_mutation(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="codex/locked", repository=REPO)
        self.mutate("bind", kind="worktree", value=str(self.root / "locked-worktree"))
        self.mutate("bind", kind="pr", value=f"{REPO}#17")
        self.mutate("bind", kind="thread", value=SYNTHETIC_THREAD)
        self.mutate("bind", kind="universal_bundle", value="synthetic-bundle-v1")
        self.freeze_candidate()
        self.mutate("observe_heads", heads={REPO: SHA_C})
        other = str(uuid.uuid4())
        self.store.register_case(
            other,
            objective="attempted replacement",
            request_id=request(),
            expected_store_revision=self.store.store_revision(),
        )
        self.store.bind(
            other,
            kind="repo_url",
            value=REPO,
            request_id=request(),
            expected_revision=1,
        )
        before = (self.root / engine.STORE_FILENAME).read_bytes()
        contexts = (
            {"branch": "codex/locked"},
            {"worktree": str(self.root / "locked-worktree")},
            {"pr": f"{REPO}#17"},
            {"thread": SYNTHETIC_THREAD},
            {"universal_bundle": "synthetic-bundle-v1"},
            {"branch": "codex/new", "head": SHA_A},
            {"branch": "codex/new", "head": SHA_C},
        )
        for extra in contexts:
            with self.subTest(extra=extra):
                result = self.store.check_action(
                    other,
                    "product_work",
                    actor_role="implementer_child",
                    repository=REPO,
                    blocked_case_id=self.case_id,
                    **extra,
                )
                self.assertFalse(result["allowed"])
                self.assertIn("LOCKED_CASE_SCOPE_OVERLAP", result["reason_codes"])
        self.assertEqual((self.root / engine.STORE_FILENAME).read_bytes(), before)

    def test_same_head_in_a_different_repository_is_unrelated(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="codex/locked", repository=REPO)
        self.freeze_candidate()
        self.mutate("observe_heads", heads={REPO: SHA_C})
        other = str(uuid.uuid4())
        self.store.register_case(
            other,
            objective="same hash text in another repository",
            request_id=request(),
            expected_store_revision=self.store.store_revision(),
        )
        self.bind_execution_scope(
            other,
            repository=OTHER_REPO,
            branch="codex/unrelated",
            expected_revision=1,
        )
        result = self.store.check_action(
            other,
            "product_work",
            actor_role="implementer_child",
            repository=OTHER_REPO,
            branch="codex/unrelated",
            head=SHA_C,
            blocked_case_id=self.case_id,
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason_codes"], ["UNRELATED_CASE_ALLOWED"])

    def test_role_state_repository_and_head_contract_is_canonical(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="codex/reviewed", repository=REPO)
        administration = self.store.check_action(
            self.case_id,
            "case_administration",
            actor_role="parent",
        )
        self.assertTrue(administration["allowed"])
        implement = self.store.check_action(
            self.case_id,
            "implementation",
            **self.action_context(branch="codex/reviewed"),
        )
        self.assertTrue(implement["allowed"])
        parent_implementation = self.store.check_action(
            self.case_id,
            "implementation",
            **self.action_context(actor_role="parent", branch="codex/reviewed"),
        )
        self.assertFalse(parent_implementation["allowed"])
        self.assertIn("ROLE_ACTION_DENIED", parent_implementation["reason_codes"])
        wrong_binding = self.store.check_action(
            self.case_id,
            "implementation",
            **self.action_context(branch="codex/not-owned"),
        )
        self.assertFalse(wrong_binding["allowed"])
        self.assertIn("CASE_BINDING_MISMATCH", wrong_binding["reason_codes"])

        self.begin_review()
        correct = self.store.check_action(
            self.case_id,
            "review_collection",
            **self.action_context(
                actor_role="review_child",
                branch="codex/reviewed",
                head=SHA_A,
            ),
        )
        self.assertTrue(correct["allowed"])
        for actor_role in ("parent", "implementer_child", "fix_child", "publication_child"):
            with self.subTest(actor_role=actor_role):
                denied = self.store.check_action(
                    self.case_id,
                    "review_collection",
                    **self.action_context(
                        actor_role=actor_role,
                        branch="codex/reviewed",
                        head=SHA_A,
                    ),
                )
                self.assertFalse(denied["allowed"])
                self.assertIn("ROLE_ACTION_DENIED", denied["reason_codes"])
        wrong_head = self.store.check_action(
            self.case_id,
            "review_collection",
            **self.action_context(
                actor_role="review_child",
                branch="codex/reviewed",
                head=SHA_C,
            ),
        )
        self.assertFalse(wrong_head["allowed"])
        self.assertIn("HEAD_DRIFT", wrong_head["reason_codes"])
        wrong_repo = self.store.check_action(
            self.case_id,
            "review_collection",
            **self.action_context(
                actor_role="review_child",
                repository=OTHER_REPO,
                branch="codex/reviewed",
                head=SHA_A,
            ),
        )
        self.assertFalse(wrong_repo["allowed"])
        self.assertIn("REPOSITORY_MISMATCH", wrong_repo["reason_codes"])

        for required in (
            "protocol_version",
            "schema_version",
            "case_id",
            "state",
            "allowed",
            "reason",
            "reason_codes",
            "repository",
            "head",
            "limits",
            "actor_role",
            "context",
        ):
            self.assertIn(required, correct)
        self.assertEqual(correct["protocol_version"], engine.ACTION_PROTOCOL_VERSION)
        self.assertEqual(correct["schema_version"], engine.SCHEMA_VERSION)
        self.assertEqual(correct["repository"], REPO)
        self.assertEqual(correct["head"], SHA_A)
        self.assertEqual(correct["actor_role"], "review_child")

    def test_fix_and_publication_roles_are_separate(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="codex/bounded", repository=REPO)
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
        fix = self.store.check_action(
            self.case_id,
            "repair",
            **self.action_context(actor_role="fix_child", branch="codex/bounded", head=SHA_A),
        )
        self.assertTrue(fix["allowed"])
        parent_fix = self.store.check_action(
            self.case_id,
            "repair",
            **self.action_context(actor_role="parent", branch="codex/bounded", head=SHA_A),
        )
        self.assertFalse(parent_fix["allowed"])
        self.mutate(
            "complete_repair",
            heads={REPO: SHA_B},
            snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "2" * 64}},
            addressed_ids=["F-001"],
        )
        self.mutate("start_closure_preflight")
        self.mutate(
            "verify_closure_preflight",
            review_heads={REPO: SHA_A},
            repaired_heads={REPO: SHA_B},
            authorized_ids=["F-001"],
            snapshots={REPO: {"contract": engine.SNAPSHOT_CONTRACT, "sha256": "2" * 64}},
        )
        closure = self.store.check_action(
            self.case_id,
            "closure_check",
            **self.action_context(actor_role="review_child", branch="codex/bounded", head=SHA_B),
        )
        self.assertTrue(closure["allowed"])
        denied_closure = self.store.check_action(
            self.case_id,
            "closure_check",
            **self.action_context(actor_role="fix_child", branch="codex/bounded", head=SHA_B),
        )
        self.assertFalse(denied_closure["allowed"])
        self.assertIn("ROLE_ACTION_DENIED", denied_closure["reason_codes"])
        self.mutate("complete_closure_check", resolutions={"F-001": "RESOLVED"})
        publication = self.store.check_action(
            self.case_id,
            "publication",
            **self.action_context(
                actor_role="publication_child",
                branch="codex/bounded",
                head=SHA_B,
            ),
        )
        self.assertTrue(publication["allowed"])
        parent_publication = self.store.check_action(
            self.case_id,
            "publication",
            **self.action_context(actor_role="parent", branch="codex/bounded", head=SHA_B),
        )
        self.assertFalse(parent_publication["allowed"])

    def test_publication_requires_closed_success_and_separate_authorities_stay_separate(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="codex/publish", repository=REPO)
        context = self.action_context(
            actor_role="publication_child",
            branch="codex/publish",
            head=SHA_A,
        )
        self.assertFalse(self.store.check_action(self.case_id, "publication", **context)["allowed"])
        self.begin_review()
        self.mutate("freeze_findings")
        self.mutate("close_without_blockers")
        self.assertTrue(self.store.check_action(self.case_id, "publication", **context)["allowed"])
        for action in ("merge", "deployment", "release", "credential_change", "universal_sync"):
            result = self.store.check_action(
                self.case_id,
                action,
                **self.action_context(actor_role="parent", branch="codex/publish", head=SHA_A),
            )
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

    def test_cli_action_check_emits_the_canonical_context_contract(self) -> None:
        self.mutate("bind", kind="repo_url", value=REPO)
        self.mutate("bind", kind="branch", value="refs/heads/codex/cli", repository=REPO)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--state-root",
                str(self.root),
                "--json",
                "action-check",
                "--case-id",
                self.case_id,
                "--action",
                "implementation",
                "--actor-role",
                "implementer_child",
                "--repository",
                "git@GitHub.com:Example/Project.git",
                "--branch",
                "refs/heads/codex/cli",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["protocol_version"], engine.ACTION_PROTOCOL_VERSION)
        self.assertEqual(payload["repository"], REPO)
        self.assertEqual(payload["context"]["branch"], "codex/cli")
        self.assertEqual(payload["reason_codes"], ["ACTION_ALLOWED"])

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
