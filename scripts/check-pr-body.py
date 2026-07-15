#!/usr/bin/env python3
"""Validate pull request body control metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


REQUIRED_HEADING_GROUPS = [
    ("## Summary",),
    ("## Scope And Authority", "## Source And Scope"),
    ("## Decision Record",),
    ("## Scope-Creep And Hidden-Dependency Check",),
    ("## Review And Validation", "## AI Review And Agent Coordination"),
]

REQUIRED_PHRASES = [
    "Original requested outcome",
    "Acceptance criteria",
    "Explicit non-goals",
    "Owner | Approver",
    "hidden dependency",
    "No parent-orchestrator or automatic review-fix train was used",
    "Current PR head SHA",
    "Reviewed head SHA",
    "Review source",
    "Current-head review count",
]

AI_REVIEW_OPTIONS = [
    "Codex reviewed this exact PR head",
    "Claude reviewed this diff",
    "AI review intentionally deferred with reason below",
]


def checked_line(body: str, label: str) -> bool:
    escaped_label = re.escape(label)
    return bool(re.search(rf"(?m)^\s*-\s*\[[xX]\]\s+{escaped_label}\s*$", body))


def option_line_present(body: str, label: str) -> bool:
    escaped_label = re.escape(label)
    return bool(re.search(rf"(?m)^\s*-\s*\[[ xX]\]\s+{escaped_label}\s*$", body))


def field_value(body: str, label: str) -> str:
    escaped_label = re.escape(label)
    match = re.search(rf"(?im)^\s*(?:-\s*)?{escaped_label}:[^\S\r\n]*([^\r\n]+)", body)
    return match.group(1).strip() if match else ""


def valid_sha(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", value))


def positive_int(value: str) -> bool:
    return bool(re.fullmatch(r"[1-9][0-9]*", value))


def validate_body(
    body: str,
    *,
    allow_unmarked_ai_review: bool = False,
    expected_current_head: str = "",
) -> list[str]:
    failures: list[str] = []

    for heading_group in REQUIRED_HEADING_GROUPS:
        if not any(heading in body for heading in heading_group):
            failures.append(f"PR body is missing required section: {' or '.join(heading_group)}")

    for phrase in REQUIRED_PHRASES:
        if phrase not in body:
            failures.append(f"PR body is missing required workflow-control phrase: {phrase}")

    if allow_unmarked_ai_review:
        for option in AI_REVIEW_OPTIONS:
            if not option_line_present(body, option):
                failures.append(f"PR template is missing AI review option: {option}")
    elif not any(checked_line(body, option) for option in AI_REVIEW_OPTIONS):
        failures.append("PR must mark Codex review, Claude review, or intentional AI-review deferral.")

    if checked_line(body, "Codex reviewed this exact PR head"):
        current_pr_head = field_value(body, "Current PR head SHA")
        reviewed_head = field_value(body, "Reviewed head SHA")
        review_source = field_value(body, "Review source")
        review_count = field_value(body, "Current-head review count")
        if not valid_sha(current_pr_head):
            failures.append("Codex exact-head review must record a valid Current PR head SHA.")
        elif expected_current_head and current_pr_head.lower() != expected_current_head.lower():
            failures.append("Codex exact-head review Current PR head SHA must match the live pull request head SHA.")
        if not valid_sha(reviewed_head):
            failures.append("Codex exact-head review must record a valid Reviewed head SHA.")
        if valid_sha(current_pr_head) and valid_sha(reviewed_head) and current_pr_head.lower() != reviewed_head.lower():
            failures.append("Codex exact-head review Reviewed head SHA must match Current PR head SHA.")
        if not review_source:
            failures.append("Codex exact-head review must record the review source.")
        if not positive_int(review_count):
            failures.append("Codex exact-head review must record a positive Current-head review count.")

    return failures


def fixture_body(current_head: str, reviewed_head: str, review_source: str, review_count: str) -> str:
    source_value = f" {review_source}" if review_source else ""
    return f"""## Summary

- Changed files: control helpers
- Original requested outcome: verify exact-head review metadata
- Acceptance criteria: parse line-bounded fields
- Explicit non-goals: no product implementation

## Scope And Authority

- [x] This PR maps to the original requested outcome and acceptance criteria.
- [x] Explicit non-goals stayed out of the diff.

## Decision Record

| Decision | Alternatives rejected | Reason | Owner | Approver | Revisit trigger | Evidence test | Status | Authority source |
|---|---|---|---|---|---|---|---|---|
| Line-bounded parser | Cross-line capture | Avoid hidden dependency on PR prose | Ayman | Ayman | parser drift | self-test | Approved | control-state contract |

## Scope-Creep And Hidden-Dependency Check

- [x] No hidden dependency was added.
- [x] No parent-orchestrator or automatic review-fix train was used.

## Review And Validation

- [x] Codex reviewed this exact PR head
  - Current PR head SHA: {current_head}
  - Reviewed head SHA: {reviewed_head}
  - Review source:{source_value}
  - Current-head review count: {review_count}
- [ ] Claude reviewed this diff
- [ ] AI review intentionally deferred with reason below
- [x] Required validation commands were run or explicitly reported as not run.
"""


def run_self_test(template_path: Path | None = None) -> None:
    head = "a" * 40
    valid_failures = validate_body(
        fixture_body(head, head, "https://example.invalid/review", "1"),
        expected_current_head=head,
    )
    if valid_failures:
        raise AssertionError(f"valid PR body fixture failed: {'; '.join(valid_failures)}")

    blank_source_failures = validate_body(fixture_body(head, head, "", "1"))
    if not any("review source" in failure.lower() for failure in blank_source_failures):
        raise AssertionError("blank Review source fixture did not fail closed")

    short_sha_failures = validate_body(fixture_body(head, head[:10], "https://example.invalid/review", "1"))
    if not any("Reviewed head SHA" in failure for failure in short_sha_failures):
        raise AssertionError("abbreviated Reviewed head SHA fixture did not fail closed")

    stale_head_failures = validate_body(
        fixture_body(head, head, "https://example.invalid/review", "1"),
        expected_current_head="b" * 40,
    )
    if not any("live pull request head SHA" in failure for failure in stale_head_failures):
        raise AssertionError("stale Current PR head SHA fixture did not fail closed")

    if template_path is not None:
        template_failures = validate_body(template_path.read_text(encoding="utf-8"), allow_unmarked_ai_review=True)
        if template_failures:
            raise AssertionError(f"PR template failed template-mode validation: {'; '.join(template_failures)}")


def read_github_event_body(event_path: Path, *, enforce_draft: bool) -> tuple[str, str, str | None]:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        return "", "", "pr-body check skipped because this is not a pull_request event."
    if pull_request.get("draft") is True and not enforce_draft:
        return "", "", "pr-body check skipped because this pull request is still a draft."
    head = pull_request.get("head")
    head_sha = str(head.get("sha") or "") if isinstance(head, dict) else ""
    return str(pull_request.get("body") or ""), head_sha, None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body-file")
    parser.add_argument("--allow-unmarked-ai-review", action="store_true")
    parser.add_argument("--enforce-draft", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    template_path = repo_root / ".github" / "pull_request_template.md"

    if args.self_test:
        run_self_test(template_path)
        print("pr-body check self-test passed")
        return 0

    source = ""
    expected_current_head = ""
    if args.body_file:
        source = args.body_file
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif os.environ.get("GITHUB_EVENT_PATH"):
        source = "GitHub pull request event"
        body, expected_current_head, skipped = read_github_event_body(
            Path(os.environ["GITHUB_EVENT_PATH"]),
            enforce_draft=args.enforce_draft,
        )
        if skipped:
            print(skipped)
            return 0
    else:
        print("pr-body check skipped outside GitHub Actions.")
        return 0

    failures = validate_body(
        body,
        allow_unmarked_ai_review=args.allow_unmarked_ai_review,
        expected_current_head=expected_current_head,
    )
    if failures:
        print(f"pr-body check failed for {source}", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"pr-body check passed for {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
